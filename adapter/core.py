"""The adapter: one interface for every factory.

The robot side only ever calls get / set / status / snapshot with canonical
names. Which protocol or address sits behind them is decided by the config.
"""
import logging
from typing import Any

from .config import FactoryConfig, load_config
from .drivers import REGISTRY, Driver
from .errors import ConfigError, SafetyError, TagError
from .model import CANONICAL_TAGS, Status

log = logging.getLogger("adapter")


class Adapter:
    def __init__(self, config: FactoryConfig, driver: Driver):
        self.config = config
        self.driver = driver

    @classmethod
    def from_config_file(cls, path) -> "Adapter":
        config = load_config(path)
        driver_cls = REGISTRY.get(config.protocol)
        if driver_cls is None:
            known = ", ".join(sorted(REGISTRY))
            raise ConfigError(f"Unknown protocol '{config.protocol}' (known: {known})")
        return cls(config, driver_cls(**config.connection))

    async def __aenter__(self) -> "Adapter":
        await self.driver.connect()
        log.info("connected to %s (%s)", self.config.name, self.config.protocol)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.driver.close()

    # --- reading -------------------------------------------------------
    async def get(self, tag: str) -> Any:
        spec = self._spec(tag)
        raw = await self.driver.read(self.config.tags[tag])
        return spec.kind(raw)

    async def status(self) -> Status:
        raw = int(await self.get("status"))
        if self.config.status_map:
            if raw not in self.config.status_map:
                raise TagError(f"{self.config.name}: unmapped raw status {raw}")
            return self.config.status_map[raw]
        return Status(raw)

    async def snapshot(self) -> dict[str, Any]:
        """Read every canonical tag. Handy for logs and diagnostics."""
        out: dict[str, Any] = {}
        for tag in CANONICAL_TAGS:
            out[tag] = await self.status() if tag == "status" else await self.get(tag)
        return out

    # --- writing -------------------------------------------------------
    async def set(self, tag: str, value: Any) -> None:
        spec = self._spec(tag)
        if not spec.writable:
            raise TagError(f"Tag '{tag}' is read-only")
        await self.driver.write(self.config.tags[tag], spec.kind(value))

    async def start(self) -> None:
        """Start the robot cycle. Refused while the e-stop is active."""
        if await self.get("estop"):
            raise SafetyError(f"{self.config.name}: start refused, e-stop is active")
        await self.set("start", True)

    async def stop(self) -> None:
        await self.set("start", False)

    async def emergency_stop(self) -> None:
        """Always allowed, and always drops the start command too."""
        await self.set("estop", True)
        await self.set("start", False)

    # --- helpers -------------------------------------------------------
    @staticmethod
    def _spec(tag: str):
        try:
            return CANONICAL_TAGS[tag]
        except KeyError:
            raise TagError(f"Unknown canonical tag '{tag}'") from None
