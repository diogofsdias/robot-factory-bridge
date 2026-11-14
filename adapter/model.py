"""Canonical model: the one vocabulary the robot side speaks, whatever the factory."""
from dataclasses import dataclass
from enum import IntEnum


class Status(IntEnum):
    IDLE = 0
    RUNNING = 1
    FAULT = 2
    ESTOPPED = 3


@dataclass(frozen=True)
class TagSpec:
    kind: type  # bool or int
    writable: bool


# Every factory config must map ALL of these canonical tags.
CANONICAL_TAGS: dict[str, TagSpec] = {
    "start": TagSpec(bool, writable=True),
    "estop": TagSpec(bool, writable=True),
    "status": TagSpec(int, writable=False),
    "cycle_count": TagSpec(int, writable=False),
}
