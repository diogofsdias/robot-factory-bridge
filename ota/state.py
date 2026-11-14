"""On-disk layout and the small state file that says which release is active.

    deploy/<factory>/state.json
    deploy/<factory>/releases/<version>/config.yaml  (+ manifest.json)

The state file is replaced atomically, so a reader (the HMI) sees either the old
or the new content, never a half-written file.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"
HISTORY_LIMIT = 50
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_VERSION = re.compile(r"^v\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")


def valid_factory(name: str) -> bool:
    return bool(_SAFE_NAME.match(name))


def valid_version(version: str) -> bool:
    return bool(_VERSION.match(version))


def factory_dir(root: Path, factory: str) -> Path:
    if not valid_factory(factory):
        raise ValueError(f"unsafe factory name: {factory!r}")
    return Path(root) / factory


def release_dir(root: Path, factory: str, version: str) -> Path:
    if not valid_version(version):
        raise ValueError(f"bad version {version!r} (expected e.g. v1.2.0)")
    return factory_dir(root, factory) / "releases" / version


def read_state(root: Path, factory: str) -> dict[str, Any] | None:
    path = factory_dir(root, factory) / STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(root: Path, factory: str, state: dict[str, Any]) -> None:
    d = factory_dir(root, factory)
    d.mkdir(parents=True, exist_ok=True)
    state["history"] = state.get("history", [])[-HISTORY_LIMIT:]
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, d / STATE_FILE)  # atomic on Windows and Linux
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def active_config(root: Path, factory: str) -> Path | None:
    state = read_state(root, factory)
    if not state or not state.get("active"):
        return None
    return release_dir(root, factory, state["active"]) / "config.yaml"


def list_factories(root: Path) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / STATE_FILE).is_file())


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
