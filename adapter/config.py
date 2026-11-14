"""Load and validate a factory YAML config."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .model import CANONICAL_TAGS, Status


@dataclass
class FactoryConfig:
    name: str
    protocol: str
    connection: dict[str, Any]
    tags: dict[str, Any]  # canonical tag -> protocol address
    status_map: dict[int, Status] = field(default_factory=dict)  # raw -> canonical


def load_config(path: str | Path) -> FactoryConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"Cannot read config {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    for key in ("name", "protocol", "connection", "tags"):
        if key not in raw:
            raise ConfigError(f"{path}: missing required key '{key}'")

    tags = raw["tags"]
    if not isinstance(tags, dict):
        raise ConfigError(f"{path}: 'tags' must be a mapping")

    missing = sorted(set(CANONICAL_TAGS) - set(tags))
    if missing:
        raise ConfigError(f"{path}: tags not mapped: {', '.join(missing)}")
    unknown = sorted(set(tags) - set(CANONICAL_TAGS))
    if unknown:
        raise ConfigError(f"{path}: unknown canonical tags: {', '.join(unknown)}")

    status_map: dict[int, Status] = {}
    for rawval, name in (raw.get("status_map") or {}).items():
        try:
            status_map[int(rawval)] = Status[str(name).upper()]
        except (KeyError, ValueError) as e:
            raise ConfigError(f"{path}: bad status_map entry {rawval!r}: {name!r}") from e

    return FactoryConfig(
        name=str(raw["name"]),
        protocol=str(raw["protocol"]),
        connection=dict(raw["connection"]),
        tags=dict(tags),
        status_map=status_map,
    )
