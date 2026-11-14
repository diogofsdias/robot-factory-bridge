"""Tests for the release flow and for the HMI picking up a new release. No network."""
import json
from pathlib import Path

import pytest

from adapter import Adapter
from adapter.drivers import Driver
from hmi.core import Hmi
from ota.bundle import CONFIG, MANIFEST, BundleError, pack, verify
from ota.deployer import Deployer, OtaError
from ota.health import HealthResult, check_config, wait_for_service
from ota.state import read_state, valid_version, write_state

CONFIG_DIR = Path(__file__).parent.parent / "config"
FACTORY_B = CONFIG_DIR / "factory_b.yaml"


class FakeDriver(Driver):
    def __init__(self, tags):
        self.values = {addr: 0 for addr in tags.values()}

    async def connect(self): ...
    async def close(self): ...

    async def read(self, address):
        return self.values[address]  # KeyError for an address that does not exist

    async def write(self, address, value):
        self.values[address] = value


def fake_health(path):
    # The fake factory only has the registers of the real one, whatever the candidate config says.
    from adapter.config import load_config
    real = load_config(FACTORY_B).tags
    return check_config(path, driver_factory=lambda cfg: FakeDriver(real), samples=2, interval=0)


def make_bundle(tmp_path, version, mutate=None):
    cfg = FACTORY_B.read_text()
    if mutate:
        cfg = mutate(cfg)
    src = tmp_path / f"src-{version}.yaml"
    src.write_text(cfg)
    return pack(src, version, tmp_path / "bundles")


def bad_address(cfg):
    return cfg.replace("status: 2", "status: 99")  # a register that does not exist


@pytest.fixture
def deploy(tmp_path):
    d = Deployer(tmp_path / "deploy", health=fake_health)
    d.init(FACTORY_B)
    return d


# --- bundles ------------------------------------------------------------

def test_pack_and_verify_round_trip(tmp_path):
    b = make_bundle(tmp_path, "v1.1.0")
    m = verify(b)
    assert (m.factory, m.version) == ("factory-b", "v1.1.0")


def test_tampered_bundle_is_rejected(tmp_path):
    b = make_bundle(tmp_path, "v1.1.0")
    (b / CONFIG).write_text((b / CONFIG).read_text() + "\n# sneaky\n")
    with pytest.raises(BundleError, match="checksum"):
        verify(b)


def test_unsafe_version_and_manifest_are_refused(tmp_path):
    assert not valid_version("../../etc") and valid_version("v1.2.3-rc1")
    b = make_bundle(tmp_path, "v1.1.0")
    m = json.loads((b / MANIFEST).read_text())
    m["version"] = "../evil"
    (b / MANIFEST).write_text(json.dumps(m))
    with pytest.raises(BundleError, match="unsafe"):
        verify(b)


# --- pre-flight ----------------------------------------------------------

def test_preflight_passes_good_config_and_fails_bad_address(tmp_path):
    good = make_bundle(tmp_path, "v1.1.0")
    assert fake_health(good / CONFIG).ok
    bad = make_bundle(tmp_path, "v1.2.0", bad_address)
    res = fake_health(bad / CONFIG)
    assert not res.ok and "99" in res.detail


def test_preflight_reports_unreachable_factory(tmp_path):
    class Down(FakeDriver):
        async def connect(self):
            raise ConnectionError("no route to factory")

    res = check_config(FACTORY_B, driver_factory=lambda c: Down(c.tags))
    assert not res.ok and "no route" in res.detail


# --- deploy flow ---------------------------------------------------------

def test_good_release_becomes_active(deploy, tmp_path):
    out = deploy.deploy(make_bundle(tmp_path, "v1.1.0"))
    assert out.result == "activated"
    s = read_state(deploy.root, "factory-b")
    assert (s["active"], s["previous"]) == ("v1.1.0", "v1.0.0")


def test_failed_preflight_never_switches(deploy, tmp_path):
    out = deploy.deploy(make_bundle(tmp_path, "v1.2.0", bad_address))
    assert out.result == "rejected"
    s = read_state(deploy.root, "factory-b")
    assert s["active"] == "v1.0.0"
    assert s["history"][-1]["result"] == "rejected"


def test_service_gate_failure_rolls_back_automatically(tmp_path):
    seen = []

    def gate(factory, version):
        seen.append(version)
        return HealthResult(version == "v1.0.0", "simulated")  # new release never confirms

    d = Deployer(tmp_path / "deploy", health=fake_health, gate=gate)
    d.init(FACTORY_B)
    out = d.deploy(make_bundle(tmp_path, "v1.2.0"))
    assert out.result == "rolled_back"
    s = read_state(d.root, "factory-b")
    assert s["active"] == "v1.0.0" and s["previous"] is None
    assert seen == ["v1.2.0", "v1.0.0"]  # confirmed the new one, failed, then re-confirmed the old one


def test_skip_preflight_simulates_a_release_that_slipped_through(tmp_path):
    d = Deployer(tmp_path / "deploy", health=lambda p: pytest.fail("must not run"),
                 gate=lambda f, v: HealthResult(v != "v1.2.0", "service lost the factory"))
    d.init(FACTORY_B)
    out = d.deploy(make_bundle(tmp_path, "v1.2.0", bad_address), skip_preflight=True)
    assert out.result == "rolled_back"


def test_same_version_is_a_no_op(deploy, tmp_path):
    assert deploy.deploy(make_bundle(tmp_path, "v1.0.0")).result == "unchanged"


def test_same_version_with_different_content_is_refused(deploy, tmp_path):
    deploy.deploy(make_bundle(tmp_path, "v1.1.0"))
    other = make_bundle(tmp_path, "v1.1.0", lambda c: c + "\n# changed\n")
    deploy.deploy(make_bundle(tmp_path, "v1.2.0"))
    with pytest.raises(OtaError, match="different content"):
        deploy.deploy(other)


def test_manual_rollback_swaps_and_can_be_undone(deploy, tmp_path):
    deploy.deploy(make_bundle(tmp_path, "v1.1.0"))
    deploy.rollback("factory-b")
    s = read_state(deploy.root, "factory-b")
    assert (s["active"], s["previous"]) == ("v1.0.0", "v1.1.0")
    deploy.rollback("factory-b")
    assert read_state(deploy.root, "factory-b")["active"] == "v1.1.0"


def test_rollback_without_previous_and_uninitialised(deploy, tmp_path):
    with pytest.raises(OtaError, match="no previous"):
        deploy.rollback("factory-b")
    with pytest.raises(OtaError, match="not initialised"):
        Deployer(tmp_path / "empty").deploy(make_bundle(tmp_path, "v1.1.0"))


# --- service gate --------------------------------------------------------

def test_wait_for_service_needs_a_stable_connection():
    t = [0.0]
    answers = iter([
        {"stations": [{"key": "factory_b", "release": "v1.1.0", "connected": False, "error": "boom"}]},
        {"stations": [{"key": "factory_b", "release": "v1.1.0", "connected": True}]},
    ])
    last = {"stations": [{"key": "factory_b", "release": "v1.1.0", "connected": True}]}

    def fetch(url):
        return next(answers, last)

    res = wait_for_service("http://x", "factory-b", "v1.1.0", timeout=30, stable_for=2, interval=0.5,
                           fetch=fetch, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert res.ok


def test_wait_for_service_times_out_on_wrong_release_or_unreachable():
    t = [0.0]
    sleep = lambda s: t.__setitem__(0, t[0] + s)  # noqa: E731
    old = {"stations": [{"key": "factory_b", "release": "v1.0.0", "connected": True}]}
    res = wait_for_service("http://x", "factory-b", "v1.1.0", timeout=3, fetch=lambda u: old,
                           clock=lambda: t[0], sleep=sleep)
    assert not res.ok and "v1.0.0" in res.detail

    def down(url):
        raise ConnectionRefusedError("nothing listening")

    res = wait_for_service("http://x", "factory-b", "v1.1.0", timeout=3, fetch=down,
                           clock=lambda: t[0], sleep=sleep)
    assert not res.ok and "not reachable" in res.detail


# --- the HMI follows the deploy state ------------------------------------

def test_hmi_hot_swaps_to_the_new_release_and_reports_it(deploy, tmp_path):
    from adapter.config import load_config
    from ota.state import active_config

    def factory(path):
        cfg = load_config(path)
        return Adapter(cfg, FakeDriver(cfg.tags))

    hmi = Hmi([factory(active_config(deploy.root, "factory-b"))], deploy_root=deploy.root, adapter_factory=factory)
    try:
        st = hmi.stations["factory_b"]
        hmi.poll_station(st)
        assert hmi.state()["stations"][0]["release"] == "v1.0.0"

        deploy.deploy(make_bundle(tmp_path, "v1.1.0"))
        hmi.poll_station(st)
        s = hmi.state()["stations"][0]
        assert s["release"] == "v1.1.0" and s["connected"] and s["last_ota"]["result"] == "activated"
        assert any("v1.0.0 -> v1.1.0 activated" in e["message"] for e in hmi.state()["events"])
    finally:
        hmi.close()


def test_hmi_reports_a_release_it_cannot_load_once_and_keeps_running(deploy, tmp_path):
    from adapter.config import load_config
    from ota.state import active_config

    def factory(path):
        cfg = load_config(path)
        if "v1.3.0" in str(path):
            raise RuntimeError("corrupt")
        return Adapter(cfg, FakeDriver(cfg.tags))

    hmi = Hmi([factory(active_config(deploy.root, "factory-b"))], deploy_root=deploy.root, adapter_factory=factory)
    try:
        st = hmi.stations["factory_b"]
        hmi.poll_station(st)
        deploy.deploy(make_bundle(tmp_path, "v1.3.0"))
        for _ in range(3):
            hmi.poll_station(st)
        s = hmi.state()
        assert s["stations"][0]["release"] == "v1.0.0" and s["stations"][0]["connected"]
        assert sum("could not be loaded" in e["message"] for e in s["events"]) == 1
    finally:
        hmi.close()
