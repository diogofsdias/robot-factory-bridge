"""A release bundle: one factory config plus a manifest with its SHA-256.

In this project a release is a config bundle. The same mechanism (verify, stage,
check, switch, confirm, roll back) applies unchanged to a container image tag.
"""
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from adapter import ConfigError
from adapter.config import load_config

from .state import now, valid_factory, valid_version

MANIFEST = "manifest.json"
CONFIG = "config.yaml"


class BundleError(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    factory: str
    version: str
    sha256: str
    notes: str = ""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pack(config_path: Path, version: str, out_dir: Path, notes: str = "") -> Path:
    """Create <out_dir>/<factory>-<version>/ from a factory YAML."""
    if not valid_version(version):
        raise BundleError(f"bad version {version!r} (expected e.g. v1.2.0)")
    config = load_config(config_path)  # refuse to pack a structurally invalid config
    if not valid_factory(config.name):
        raise BundleError(f"unsafe factory name {config.name!r}")
    dest = Path(out_dir) / f"{config.name}-{version}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config_path, dest / CONFIG)
    manifest = {
        "factory": config.name,
        "version": version,
        "sha256": sha256_of(dest / CONFIG),
        "notes": notes,
        "created": now(),
    }
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def verify(bundle_dir: Path) -> Manifest:
    """Check the bundle is complete and untampered. Raises BundleError."""
    bundle_dir = Path(bundle_dir)
    try:
        raw = json.loads((bundle_dir / MANIFEST).read_text(encoding="utf-8"))
        manifest = Manifest(raw["factory"], raw["version"], raw["sha256"], raw.get("notes", ""))
    except (OSError, ValueError, KeyError) as e:
        raise BundleError(f"{bundle_dir}: missing or unreadable manifest ({e})") from e
    if not valid_factory(manifest.factory) or not valid_version(manifest.version):
        raise BundleError(f"{bundle_dir}: manifest has an unsafe factory or version")
    cfg = bundle_dir / CONFIG
    if not cfg.is_file():
        raise BundleError(f"{bundle_dir}: {CONFIG} is missing")
    if sha256_of(cfg) != manifest.sha256:
        raise BundleError(f"{bundle_dir}: checksum mismatch, bundle was modified or corrupted")
    try:
        config = load_config(cfg)
    except ConfigError as e:
        raise BundleError(f"{bundle_dir}: {e}") from e
    if config.name != manifest.factory:
        raise BundleError(f"{bundle_dir}: config is for '{config.name}', manifest says '{manifest.factory}'")
    return manifest
