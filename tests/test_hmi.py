"""Tests for the HMI logic and its HTTP layer. No network to factories, no ROS2."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from adapter import Adapter
from adapter.config import load_config
from adapter.drivers import Driver
from hmi.core import Hmi
from hmi.server import make_handler
from hmi.simdriver import SimDriver, SimPlant

CONFIG_DIR = Path(__file__).parent.parent / "config"


class FakeDriver(Driver):
    def __init__(self, tags):
        self.values = {addr: 0 for addr in tags.values()}
        self.fail_connect = False
        self.fail_read = False

    async def connect(self):
        if self.fail_connect:
            raise ConnectionError("refused")

    async def close(self):
        pass

    async def read(self, address):
        if self.fail_read:
            raise ConnectionError("link down")
        return self.values[address]

    async def write(self, address, value):
        self.values[address] = value


def make_adapter(name):
    config = load_config(CONFIG_DIR / name)
    return Adapter(config, FakeDriver(config.tags))


@pytest.fixture
def hmi():
    h = Hmi([make_adapter("factory_a.yaml"), make_adapter("factory_b.yaml")])
    yield h
    h.close()


def poll_all(h):
    for st in h.stations.values():
        h.poll_station(st)


def drv(h, key):
    return h.stations[key].adapter.driver


def addr(h, key, tag):
    return h.stations[key].adapter.config.tags[tag]


def test_state_has_both_factories_with_their_native_mappings(hmi):
    poll_all(hmi)
    s = {st["key"]: st for st in hmi.state()["stations"]}
    assert set(s) == {"factory_a", "factory_b"}
    assert s["factory_a"]["protocol"] == "opcua" and s["factory_b"]["protocol"] == "modbus"
    assert s["factory_a"]["mapping"]["start"] == "Station/Start"
    assert s["factory_b"]["mapping"]["start"] == "0"
    assert s["factory_a"]["connected"] and s["factory_a"]["status"] == "IDLE"


def test_start_is_accepted_and_logged(hmi):
    poll_all(hmi)
    ok, msg = hmi.command("factory_b", "start")
    assert (ok, msg) == (True, "ok")
    assert drv(hmi, "factory_b").values[addr(hmi, "factory_b", "start")] == 1
    assert hmi.state()["events"][0]["level"] == "ok"


def test_start_refused_during_estop_is_a_logged_answer_not_a_crash(hmi):
    poll_all(hmi)
    hmi.command("factory_a", "emergency_stop")
    ok, msg = hmi.command("factory_a", "start")
    assert ok is False and "e-stop is active" in msg
    last = hmi.state()["events"][0]
    assert last["level"] == "refused" and last["station"] == "factory-a"


def test_status_changes_are_logged_and_alarms_flagged(hmi):
    poll_all(hmi)
    drv(hmi, "factory_b").values[addr(hmi, "factory_b", "status")] = 1
    poll_all(hmi)
    drv(hmi, "factory_b").values[addr(hmi, "factory_b", "status")] = 3
    poll_all(hmi)
    events = hmi.state()["events"]
    assert events[0]["level"] == "alarm" and "RUNNING -> ESTOPPED" in events[0]["message"]
    assert "IDLE -> RUNNING" in events[1]["message"] and events[1]["level"] == "info"


def test_unreachable_factory_is_reported_once_and_recovers(hmi):
    drv(hmi, "factory_a").fail_connect = True
    for _ in range(3):
        poll_all(hmi)
    st = {s["key"]: s for s in hmi.state()["stations"]}
    assert st["factory_a"]["connected"] is False and "refused" in st["factory_a"]["error"]
    assert st["factory_b"]["connected"] is True  # the other factory is unaffected
    errors = [e for e in hmi.state()["events"] if e["level"] == "error"]
    assert len(errors) == 1  # logged once per outage, not on every retry

    drv(hmi, "factory_a").fail_connect = False
    poll_all(hmi)
    assert {s["key"]: s for s in hmi.state()["stations"]}["factory_a"]["connected"] is True


def test_link_lost_while_running(hmi):
    poll_all(hmi)
    drv(hmi, "factory_b").fail_read = True
    poll_all(hmi)
    s = {x["key"]: x for x in hmi.state()["stations"]}["factory_b"]
    assert s["connected"] is False and s["status"] is None
    assert "link lost" in hmi.state()["events"][0]["message"]


def test_command_while_disconnected_is_refused_cleanly(hmi):
    drv(hmi, "factory_a").fail_connect = True
    poll_all(hmi)
    ok, msg = hmi.command("factory_a", "start")
    assert ok is False and "not connected" in msg


def test_bad_station_and_command(hmi):
    with pytest.raises(KeyError):
        hmi.command("nope", "start")
    with pytest.raises(ValueError):
        hmi.command("factory_a", "self_destruct")


def test_sim_driver_behaves_like_the_simulators():
    config = load_config(CONFIG_DIR / "factory_b.yaml")
    adapter = Adapter(config, SimDriver(SimPlant(config.tags, tick=0.01)))
    hmi = Hmi([adapter])
    try:
        hmi.poll_station(hmi.stations["factory_b"])
        assert hmi.command("factory_b", "start")[0]
        import time
        time.sleep(0.1)
        hmi.poll_station(hmi.stations["factory_b"])
        s = hmi.state()["stations"][0]
        assert s["status"] == "RUNNING" and s["cycle_count"] > 0
        hmi.command("factory_b", "emergency_stop")
        time.sleep(0.05)
        hmi.poll_station(hmi.stations["factory_b"])
        assert hmi.state()["stations"][0]["status"] == "ESTOPPED"
        assert hmi.command("factory_b", "start")[0] is False
    finally:
        hmi.close()


# --- HTTP layer -------------------------------------------------------

@pytest.fixture
def http(hmi):
    poll_all(hmi)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hmi))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(url, body=None, ctype="application/json"):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": ctype} if body is not None else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_serves_page_and_state(http):
    code, body = call(http + "/")
    assert code == 200 and b"Robot Factory Bridge" in body
    code, body = call(http + "/api/state")
    assert code == 200 and len(json.loads(body)["stations"]) == 2


def test_http_command_round_trip_and_refusal(http):
    assert json.loads(call(http + "/api/command", {"station": "factory_a", "command": "emergency_stop"})[1])["ok"]
    code, body = call(http + "/api/command", {"station": "factory_a", "command": "start"})
    assert code == 200  # a refusal is a normal answer
    assert json.loads(body)["ok"] is False


def test_http_rejects_bad_requests(http):
    assert call(http + "/api/command", {"station": "x", "command": "start"})[0] == 404
    assert call(http + "/api/command", {"station": "factory_a", "command": "boom"})[0] == 400
    assert call(http + "/api/command", {"nothing": 1})[0] == 400
    assert call(http + "/api/command", {"station": "factory_a", "command": "start"}, ctype="text/plain")[0] == 415
    assert call(http + "/nope")[0] == 404
    assert call(http + "/api/demo", {"station": "factory_a", "online": False})[0] == 404  # demo-only


def test_connects_but_cannot_read_is_one_outage_not_a_flapping_log(hmi):
    drv(hmi, "factory_a").fail_read = True  # TCP connect works, every read fails
    for _ in range(4):
        hmi.poll_station(hmi.stations["factory_a"])
    msgs = [e["message"] for e in hmi.state()["events"] if e["station"] == "factory-a"]
    assert len(msgs) == 1 and msgs[0].startswith("cannot connect")
