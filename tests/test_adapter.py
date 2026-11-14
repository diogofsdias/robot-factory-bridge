import asyncio
from pathlib import Path

import pytest

from adapter import Adapter, ConfigError, SafetyError, Status, TagError
from adapter.config import load_config
from adapter.drivers import Driver

CONFIG_DIR = Path(__file__).parent.parent / "config"


class FakeDriver(Driver):
    """In-memory stand-in for a protocol. Records what was written."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def read(self, address):
        return self.values[address]

    async def write(self, address, value):
        self.writes.append((address, value))
        self.values[address] = value


def run(coro):
    return asyncio.run(coro)


def make_adapter(config_name="factory_b.yaml", **values):
    config = load_config(CONFIG_DIR / config_name)
    defaults = {addr: 0 for addr in config.tags.values()}
    for canonical, value in values.items():
        defaults[config.tags[canonical]] = value
    return Adapter(config, FakeDriver(defaults))


# --- the key idea: same calls, different layouts ---------------------------

def test_same_code_works_for_both_factory_layouts():
    async def scenario(adapter):
        await adapter.start()
        return await adapter.get("start")

    for name in ("factory_a.yaml", "factory_b.yaml"):
        assert run(scenario(make_adapter(name))) is True


def test_adapter_writes_to_the_address_from_the_config():
    adapter_b = make_adapter("factory_b.yaml")
    adapter_a = make_adapter("factory_a.yaml")
    run(adapter_b.start())
    run(adapter_a.start())
    assert adapter_b.driver.writes == [(0, True)]
    assert adapter_a.driver.writes == [("Station/Start", True)]


# --- reading ----------------------------------------------------------------

def test_status_is_returned_as_canonical_enum():
    adapter = make_adapter(status=1)
    assert run(adapter.status()) is Status.RUNNING


def test_status_map_translates_raw_codes():
    config = load_config(CONFIG_DIR / "factory_b.yaml")
    config.status_map = {10: Status.RUNNING, 0: Status.IDLE}
    adapter = Adapter(config, FakeDriver({a: 0 for a in config.tags.values()}))
    adapter.driver.values[config.tags["status"]] = 10
    assert run(adapter.status()) is Status.RUNNING


def test_unmapped_raw_status_raises():
    config = load_config(CONFIG_DIR / "factory_b.yaml")
    config.status_map = {0: Status.IDLE}
    adapter = Adapter(config, FakeDriver({a: 0 for a in config.tags.values()}))
    adapter.driver.values[config.tags["status"]] = 99
    with pytest.raises(TagError):
        run(adapter.status())


def test_snapshot_has_every_canonical_tag():
    snap = run(make_adapter(cycle_count=7).snapshot())
    assert set(snap) == {"start", "estop", "status", "cycle_count"}
    assert snap["cycle_count"] == 7


def test_bool_tags_are_coerced_from_modbus_style_ints():
    assert run(make_adapter(estop=1).get("estop")) is True


# --- writing and safety -----------------------------------------------------

def test_unknown_tag_rejected():
    with pytest.raises(TagError):
        run(make_adapter().get("speed"))


def test_read_only_tag_cannot_be_written():
    with pytest.raises(TagError):
        run(make_adapter().set("status", 1))


def test_start_refused_while_estop_active():
    adapter = make_adapter(estop=1)
    with pytest.raises(SafetyError):
        run(adapter.start())
    assert adapter.driver.writes == []  # nothing was sent to the factory


def test_emergency_stop_sets_estop_and_drops_start():
    adapter = make_adapter(start=1)
    run(adapter.emergency_stop())
    assert (1, True) in adapter.driver.writes  # estop register
    assert adapter.driver.values[0] == 0       # start register cleared


# --- lifecycle --------------------------------------------------------------

def test_context_manager_connects_and_closes():
    adapter = make_adapter()

    async def scenario():
        async with adapter:
            assert adapter.driver.connected
        assert not adapter.driver.connected

    run(scenario())


# --- config validation ------------------------------------------------------

def test_real_configs_load():
    assert load_config(CONFIG_DIR / "factory_a.yaml").protocol == "opcua"
    assert load_config(CONFIG_DIR / "factory_b.yaml").protocol == "modbus"


def test_from_config_file_picks_the_right_driver():
    a = Adapter.from_config_file(CONFIG_DIR / "factory_a.yaml")
    b = Adapter.from_config_file(CONFIG_DIR / "factory_b.yaml")
    assert type(a.driver).__name__ == "OpcUaDriver"
    assert type(b.driver).__name__ == "ModbusDriver"


def write_cfg(tmp_path, text):
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_tag_mapping_is_a_config_error(tmp_path):
    p = write_cfg(tmp_path, "name: x\nprotocol: modbus\nconnection: {host: h}\n"
                            "tags: {start: 0, estop: 1, status: 2}\n")
    with pytest.raises(ConfigError, match="cycle_count"):
        load_config(p)


def test_unknown_canonical_tag_is_a_config_error(tmp_path):
    p = write_cfg(tmp_path, "name: x\nprotocol: modbus\nconnection: {host: h}\n"
                            "tags: {start: 0, estop: 1, status: 2, cycle_count: 3, speed: 4}\n")
    with pytest.raises(ConfigError, match="speed"):
        load_config(p)


def test_unknown_protocol_is_a_config_error(tmp_path):
    p = write_cfg(tmp_path, "name: x\nprotocol: carrier-pigeon\nconnection: {}\n"
                            "tags: {start: 0, estop: 1, status: 2, cycle_count: 3}\n")
    with pytest.raises(ConfigError, match="carrier-pigeon"):
        Adapter.from_config_file(p)


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
