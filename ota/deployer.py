"""Release flow: verify -> stage -> pre-flight -> switch -> confirm -> (roll back).

Nothing becomes active until it has passed the pre-flight. After the switch the
running service must confirm the new release; if it does not, the previous
release is restored automatically.
"""
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bundle import CONFIG, MANIFEST, BundleError, Manifest, pack, verify
from .health import HealthResult, check_config
from .state import now, read_state, release_dir, write_state


class OtaError(Exception):
    pass


@dataclass(frozen=True)
class Outcome:
    result: str  # activated | rejected | rolled_back | unchanged
    factory: str
    version: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.result in ("activated", "unchanged")


class Deployer:
    def __init__(self, root: Path, health: Callable[[Path], HealthResult] = check_config,
                 gate: Callable[[str, str], HealthResult] | None = None,
                 log: Callable[[str], None] = lambda msg: None):
        self.root = Path(root)
        self.health = health
        self.gate = gate  # None = no running service to confirm with
        self.log = log

    # --- helpers -------------------------------------------------------
    def _state(self, factory: str) -> dict:
        state = read_state(self.root, factory)
        if state is None:
            raise OtaError(f"{factory} is not initialised in {self.root}. Run: python -m ota init")
        return state

    def _record(self, state: dict, factory: str, version: str, action: str, result: str, detail: str) -> None:
        state.setdefault("history", []).append(
            {"time": now(), "version": version, "action": action, "result": result, "detail": detail})
        write_state(self.root, factory, state)

    def _stage(self, bundle: Path, m: Manifest) -> Path:
        dest = release_dir(self.root, m.factory, m.version)
        if dest.exists():
            if (dest / MANIFEST).is_file() and verify(dest).sha256 != m.sha256:
                raise OtaError(f"{m.version} is already installed with different content; use a new version")
        else:
            dest.mkdir(parents=True)
            shutil.copyfile(Path(bundle) / CONFIG, dest / CONFIG)
            shutil.copyfile(Path(bundle) / MANIFEST, dest / MANIFEST)
        return dest

    # --- operations ----------------------------------------------------
    def init(self, config_path: Path, version: str = "v1.0.0") -> Outcome:
        """Bootstrap a factory from an existing config as its first release."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bundle = pack(config_path, version, Path(tmp))
            m = verify(bundle)
            if read_state(self.root, m.factory) is not None:
                raise OtaError(f"{m.factory} is already initialised")
            self._stage(bundle, m)
        state = {"factory": m.factory, "active": m.version, "previous": None, "history": []}
        self._record(state, m.factory, m.version, "init", "activated", "initial release")
        return Outcome("activated", m.factory, m.version, "initial release")

    def deploy(self, bundle: Path, skip_preflight: bool = False) -> Outcome:
        try:
            m = verify(bundle)
        except BundleError as e:
            raise OtaError(str(e)) from e
        state = self._state(m.factory)
        old = state["active"]
        if m.version == old:
            return Outcome("unchanged", m.factory, m.version, "already the active release")
        self.log(f"bundle {m.factory} {m.version} verified (sha256 ok)")
        staged = self._stage(bundle, m)
        self.log(f"staged as {staged}")

        if skip_preflight:
            self.log("pre-flight SKIPPED (simulating a release that slipped past testing)")
        else:
            self.log("pre-flight: connecting to the factory with the candidate config ...")
            res = self.health(staged / CONFIG)
            if not res.ok:
                self.log(f"pre-flight FAILED: {res.detail}")
                self._record(state, m.factory, m.version, "deploy", "rejected", f"pre-flight failed: {res.detail}")
                return Outcome("rejected", m.factory, m.version, f"pre-flight failed: {res.detail}")
            self.log(f"pre-flight ok: {res.detail}")

        prev_before = state.get("previous")
        state["previous"], state["active"] = old, m.version
        write_state(self.root, m.factory, state)
        self.log(f"switched {m.factory}: {old} -> {m.version}")

        if self.gate is not None:
            self.log("waiting for the running service to confirm the new release ...")
            res = self.gate(m.factory, m.version)
            if not res.ok:
                self.log(f"service gate FAILED: {res.detail}")
                state["active"], state["previous"] = old, prev_before
                self._record(state, m.factory, m.version, "deploy", "rolled_back",
                             f"service gate failed: {res.detail}; restored {old}")
                self.log(f"rolled back to {old}")
                if self.gate is not None:
                    back = self.gate(m.factory, old)
                    self.log(f"service on {old}: {back.detail}")
                return Outcome("rolled_back", m.factory, m.version, f"service gate failed: {res.detail}")
            self.log(f"service gate ok: {res.detail}")

        self._record(state, m.factory, m.version, "deploy", "activated", f"replaced {old}")
        return Outcome("activated", m.factory, m.version, f"replaced {old}")

    def rollback(self, factory: str) -> Outcome:
        state = self._state(factory)
        prev, cur = state.get("previous"), state["active"]
        if not prev:
            raise OtaError(f"{factory}: no previous release to roll back to")
        state["active"], state["previous"] = prev, cur  # swap, so it can be undone
        write_state(self.root, factory, state)
        self.log(f"switched {factory}: {cur} -> {prev}")
        detail = f"manual rollback from {cur}"
        if self.gate is not None:
            res = self.gate(factory, prev)
            self.log(f"service gate: {res.detail}")
            if not res.ok:
                detail += f" (service did not confirm: {res.detail})"
        self._record(state, factory, prev, "rollback", "activated", detail)
        return Outcome("activated", factory, prev, detail)

    def status(self, factory: str) -> dict:
        return self._state(factory)
