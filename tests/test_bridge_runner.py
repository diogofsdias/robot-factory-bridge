"""Tests for the ROS-free part of the bridge (no rclpy needed)."""
import asyncio
from pathlib import Path

import pytest

from adapter import Adapter
from adapter.config import load_config
from adapter.drivers import Driver
from ros2_bridge.runner import AsyncRunner, execute

CONFIG_DIR = Path(__file__).parent.parent / "config"


class FakeDriver(Driver):
    def __init__(self, values):
        self.values = dict(values)

    async def connect(self): ...
    async def close(self): ...

    async def read(self, address):
        return self.values[address]

    async def write(self, address, value):
        self.values[address] = value


@pytest.fixture
def runner():
    r = AsyncRunner()
    yield r
    r.stop()


def make_adapter(**values):
    config = load_config(CONFIG_DIR / "factory_b.yaml")
    raw = {addr: 0 for addr in config.tags.values()}
    for canonical, value in values.items():
        raw[config.tags[canonical]] = value
    return Adapter(config, FakeDriver(raw))


def test_runner_returns_results(runner):
    async def add():
        await asyncio.sleep(0.01)
        return 1 + 2

    assert runner.call(add()) == 3


def test_runner_propagates_exceptions(runner):
    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        runner.call(boom())


def test_execute_success(runner):
    adapter = make_adapter()
    ok, msg = execute(runner, lambda: adapter.start())
    assert (ok, msg) == (True, "ok")
    assert adapter.driver.values[0] == 1  # start register set


def test_execute_reports_safety_refusal_not_a_crash(runner):
    adapter = make_adapter(estop=1)
    ok, msg = execute(runner, lambda: adapter.start())
    assert ok is False
    assert "e-stop is active" in msg


def test_execute_reports_unexpected_errors(runner):
    async def broken():
        raise ConnectionError("link down")

    ok, msg = execute(runner, lambda: broken())
    assert ok is False
    assert "ConnectionError" in msg and "link down" in msg


def test_snapshot_through_runner(runner):
    adapter = make_adapter(status=1, cycle_count=5)
    snap = runner.call(adapter.snapshot())
    assert snap["status"].name == "RUNNING"
    assert snap["cycle_count"] == 5
