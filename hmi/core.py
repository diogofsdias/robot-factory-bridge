"""HMI logic: polls every factory through the adapter, runs operator commands and
keeps an event log. No HTTP in here, so it can be tested without a network.

Each factory gets its own polling thread, so one dead factory never slows the others.
"""
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from adapter import Adapter, AdapterError, SafetyError, Status
from ota.state import active_config, read_state
from ros2_bridge.runner import AsyncRunner

# Same four operations as the ROS2 bridge services.
COMMANDS = {
    "start": lambda a: a.start(),
    "stop": lambda a: a.stop(),
    "emergency_stop": lambda a: a.emergency_stop(),
    "reset_estop": lambda a: a.set("estop", False),
}


class Station:
    def __init__(self, adapter: Adapter):
        self.adapter = adapter
        self.name = adapter.config.name
        self.key = self.name.replace("-", "_")
        self.lock = threading.Lock()  # one poll or command at a time per station
        self.connected = False
        self.error: str | None = None
        self.snap: dict[str, Any] | None = None
        self.last_status: str | None = None
        self.version: str | None = None  # active release, when run with a deploy folder
        self.last_ota: dict[str, Any] | None = None
        self.failed_version: str | None = None  # so a bad release is reported once

    @property
    def endpoint(self) -> str:
        c = self.adapter.config.connection
        if "url" in c:
            return str(c["url"])
        if "host" in c:
            return f"{c['host']}:{c.get('port', '')}".rstrip(":")
        return ""

    def public(self) -> dict[str, Any]:
        snap = self.snap or {}
        return {
            "key": self.key,
            "name": self.name,
            "protocol": self.adapter.config.protocol,
            "endpoint": self.endpoint,
            "mapping": {tag: str(addr) for tag, addr in self.adapter.config.tags.items()},
            "connected": self.connected,
            "error": self.error,
            "status": snap["status"].name if self.connected and snap else None,
            "cycle_count": snap.get("cycle_count") if self.connected else None,
            "estop": bool(snap["estop"]) if self.connected and snap else None,
            "start": bool(snap["start"]) if self.connected and snap else None,
            "release": self.version,
            "last_ota": self.last_ota,
        }


class Hmi:
    def __init__(self, adapters: list[Adapter], runner: AsyncRunner | None = None,
                 poll_interval: float = 0.5, retry_interval: float = 2.0,
                 event_limit: int = 100, demo: bool = False,
                 deploy_root: Path | None = None,
                 adapter_factory: Callable[[Path], Adapter] = Adapter.from_config_file):
        self._own_runner = runner is None
        self.runner = runner or AsyncRunner()
        self.stations = {s.key: s for s in map(Station, adapters)}
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval
        self.demo = demo
        self.deploy_root = Path(deploy_root) if deploy_root else None
        self.adapter_factory = adapter_factory
        if self.deploy_root:
            for st in self.stations.values():
                state = read_state(self.deploy_root, st.name)
                st.version = state["active"] if state else None
                st.last_ota = self._last_ota(state)
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._seq = 0
        self._ev_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- lifecycle -----------------------------------------------------
    def start(self) -> None:
        for st in self.stations.values():
            t = threading.Thread(target=self._loop, args=(st,), name=f"poll-{st.key}", daemon=True)
            t.start()
            self._threads.append(t)

    def close(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        for st in self.stations.values():
            try:
                self.runner.call(st.adapter.__aexit__(None, None, None), timeout=3)
            except Exception:
                pass
        if self._own_runner:
            self.runner.stop()

    def _loop(self, st: Station) -> None:
        while not self._stop.is_set():
            self.poll_station(st)
            self._stop.wait(self.poll_interval if st.connected else self.retry_interval)

    # --- event log -----------------------------------------------------
    def log(self, level: str, station: str, message: str) -> None:
        with self._ev_lock:
            self._seq += 1
            self._events.appendleft({
                "id": self._seq,
                "time": time.strftime("%H:%M:%S"),
                "level": level,  # info | ok | refused | alarm | error
                "station": station,
                "message": message,
            })

    # --- polling -------------------------------------------------------
    @staticmethod
    def _last_ota(state: dict[str, Any] | None) -> dict[str, Any] | None:
        history = (state or {}).get("history") or []
        return history[-1] if history else None

    def _sync_release(self, st: Station) -> None:
        """If the deployer switched this factory to another release, load its config."""
        state = read_state(self.deploy_root, st.name)
        if not state:
            return
        st.last_ota = self._last_ota(state)
        wanted = state.get("active")
        if not wanted or wanted == st.version or wanted == st.failed_version:
            return
        try:
            new = self.adapter_factory(active_config(self.deploy_root, st.name))
        except Exception as e:
            st.failed_version = wanted
            self.log("error", st.name, f"release {wanted} could not be loaded, still on {st.version}: {e}")
            return
        try:
            self.runner.call(st.adapter.driver.close(), timeout=3)
        except Exception:
            pass
        old, st.adapter, st.version, st.failed_version = st.version, new, wanted, None
        st.connected, st.snap, st.last_status, st.error = False, None, None, None
        self.log("info", st.name, f"release {old} -> {wanted} activated")

    def poll_station(self, st: Station) -> None:
        with st.lock:
            if self.deploy_root:
                self._sync_release(st)
            fresh = False
            try:
                if not st.connected:
                    self.runner.call(st.adapter.__aenter__(), timeout=3)
                    fresh = True
                snap = self.runner.call(st.adapter.snapshot(), timeout=3)
            except Exception as e:
                self._mark_lost(st, e)
                return
            if fresh:  # only now is the link really up: connected AND readable
                st.connected = True
                st.error = None
                self.log("info", st.name, f"connected ({st.adapter.config.protocol}, {st.endpoint})")
            st.snap = snap
            status = snap["status"].name
            if st.last_status is not None and status != st.last_status:
                level = "alarm" if snap["status"] in (Status.FAULT, Status.ESTOPPED) else "info"
                self.log(level, st.name, f"status {st.last_status} -> {status}")
            st.last_status = status

    def _mark_lost(self, st: Station, e: Exception) -> None:
        message = f"{type(e).__name__}: {e}"
        if st.error is None:  # log once per outage, not on every retry
            what = "link lost" if st.connected else "cannot connect"
            self.log("error", st.name, f"{what}: {message}")
        st.connected = False
        st.error = message
        st.last_status = None
        try:
            self.runner.call(st.adapter.driver.close(), timeout=2)
        except Exception:
            pass

    # --- commands ------------------------------------------------------
    def command(self, key: str, command: str) -> tuple[bool, str]:
        """Run an operator command. Returns (ok, message). Raises KeyError / ValueError
        for an unknown station or command (a bad request, not a factory problem)."""
        st = self.stations[key]
        if command not in COMMANDS:
            raise ValueError(f"unknown command '{command}'")
        with st.lock:
            if not st.connected:
                msg = f"{command} not sent: {st.name} is not connected"
                self.log("error", st.name, msg)
                return False, msg
            try:
                self.runner.call(COMMANDS[command](st.adapter), timeout=5)
            except SafetyError as e:
                reason = str(e).removeprefix(f"{st.name}: ")  # the station is already shown
                self.log("refused", st.name, reason)
                return False, reason
            except AdapterError as e:
                reason = str(e).removeprefix(f"{st.name}: ")
                self.log("error", st.name, f"{command} failed: {reason}")
                return False, reason
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.log("error", st.name, f"{command} failed: {msg}")
                return False, msg
            self.log("ok", st.name, f"{command} accepted")
        self.poll_station(st)  # show the effect right away
        return True, "ok"

    # --- view ----------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._ev_lock:
            events = list(self._events)
        return {
            "demo": self.demo,
            "stations": [s.public() for s in self.stations.values()],
            "events": events,
        }
