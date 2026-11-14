"""The two gates a release must pass.

1. Pre-flight (check_config): the candidate config loads, connects to the factory, and
   every canonical tag reads with valid values. Optionally a functional test that starts
   the cell and sees it run. That one moves the factory, so it is opt-in (test cell only).
2. Service gate (wait_for_service): after the switch, the RUNNING service itself reports
   the new release and a stable connection to its factory.
"""
import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from adapter import Adapter, AdapterError, ConfigError, Status
from adapter.config import load_config
from adapter.drivers import REGISTRY, Driver


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    detail: str


def check_config(config_path: Path, *, driver_factory: Callable[..., Driver] | None = None,
                 samples: int = 3, interval: float = 0.5, functional: bool = False,
                 timeout: float = 5.0) -> HealthResult:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        return HealthResult(False, f"config invalid: {e}")
    if driver_factory is not None:
        driver = driver_factory(config)
    elif config.protocol in REGISTRY:
        driver = REGISTRY[config.protocol](**config.connection)
    else:
        return HealthResult(False, f"unknown protocol '{config.protocol}'")
    adapter = Adapter(config, driver)
    try:
        return asyncio.run(_check(adapter, samples, interval, functional, timeout))
    except Exception as e:  # connection refused, bad address, ...
        return HealthResult(False, f"{type(e).__name__}: {e}")


async def _check(adapter: Adapter, samples: int, interval: float, functional: bool,
                 timeout: float) -> HealthResult:
    try:
        await asyncio.wait_for(adapter.__aenter__(), timeout)
        snap = {}
        for i in range(samples):
            snap = await asyncio.wait_for(adapter.snapshot(), timeout)
            if i < samples - 1:
                await asyncio.sleep(interval)
        detail = f"connected, {samples} clean reads, status {snap['status'].name}"
        if functional:
            result = await _functional(adapter, snap, timeout)
            if not result.ok:
                return result
            detail += "; " + result.detail
        return HealthResult(True, detail)
    except AdapterError as e:
        return HealthResult(False, str(e))
    finally:
        try:
            await adapter.__aexit__(None, None, None)
        except Exception:
            pass


async def _functional(adapter: Adapter, snap: dict, timeout: float) -> HealthResult:
    if snap["estop"]:
        return HealthResult(True, "functional test skipped (e-stop active)")
    start_count = snap["cycle_count"]
    await adapter.start()
    try:
        deadline = time.monotonic() + max(timeout, 4.0)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            now = await adapter.snapshot()
            if now["status"] == Status.RUNNING and now["cycle_count"] != start_count:
                return HealthResult(True, "functional test passed (ran, cycle count advanced)")
        return HealthResult(False, "functional test failed: did not reach RUNNING with advancing cycle count")
    finally:
        await adapter.stop()


def _http_state(url: str) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=3) as r:
        return json.loads(r.read())


def wait_for_service(url: str, factory: str, version: str, *, timeout: float = 20.0,
                     stable_for: float = 3.0, interval: float = 0.5,
                     fetch: Callable[[str], dict] = _http_state,
                     clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], None] = time.sleep) -> HealthResult:
    """Wait until the running service reports `version` for `factory` and stays connected."""
    key = factory.replace("-", "_")
    deadline = clock() + timeout
    stable_since = None
    last = "no answer from the service yet"
    while clock() < deadline:
        try:
            station = next((s for s in fetch(url)["stations"] if s["key"] == key), None)
        except Exception as e:
            station, last = None, f"service not reachable: {type(e).__name__}: {e}"
        else:
            if station is None:
                last = f"service does not list '{factory}'"
            else:
                last = f"service reports release {station.get('release')}, connected={station['connected']}"
                if station["connected"] and station.get("release") == version:
                    stable_since = stable_since if stable_since is not None else clock()
                    if clock() - stable_since >= stable_for:
                        return HealthResult(True, f"service running {version}, stable for {stable_for:g}s")
                    sleep(interval)
                    continue
                if station.get("error"):
                    last += f" ({station['error']})"
        stable_since = None
        sleep(interval)
    return HealthResult(False, f"service gate timed out: {last}")
