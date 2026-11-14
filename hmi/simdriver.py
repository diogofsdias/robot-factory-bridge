"""In-memory factories for the HMI's --demo mode and for the OTA pre-flight demo.

SimPlant is the factory: a fixed set of addresses (taken from the first config seen)
that behaves like the two simulators in sim/ (estop wins, start counts cycles).
SimDriver is a connection to it. Several drivers can talk to one plant, and a release
that points at an address the plant does not have fails exactly like it would on a
real factory.
"""
import asyncio
from typing import Any

from adapter.drivers import Driver


class SimPlant:
    def __init__(self, tags: dict[str, Any], tick: float = 1.0):
        self.tags = dict(tags)  # canonical tag -> address, fixed for the life of the plant
        self.values: dict[Any, int] = {addr: 0 for addr in tags.values()}
        self.tick = tick
        self.online = True  # flip to False to simulate a lost link
        self._task: asyncio.Task | None = None
        self._count = 0

    def ensure_running(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._simulate())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _simulate(self) -> None:
        a = self.tags
        while True:
            await asyncio.sleep(self.tick)
            if self.values[a["estop"]]:
                self.values[a["status"]] = 3
            elif self.values[a["start"]]:
                self._count += 1
                self.values[a["status"]] = 1
                self.values[a["cycle_count"]] = self._count % 65536
            else:
                self.values[a["status"]] = 0


class SimDriver(Driver):
    def __init__(self, plant: SimPlant, owns_plant: bool = False):
        self.plant = plant
        self.owns_plant = owns_plant  # stop the plant when this driver closes (pre-flight use)

    @property
    def online(self) -> bool:
        return self.plant.online

    @online.setter
    def online(self, value: bool) -> None:
        self.plant.online = value

    def _check(self) -> None:
        if not self.plant.online:
            raise ConnectionError("link down (simulated)")

    async def connect(self) -> None:
        self._check()
        self.plant.ensure_running()

    async def close(self) -> None:
        if self.owns_plant:
            await self.plant.stop()

    def _slot(self, address: Any) -> Any:
        if address not in self.plant.values:
            raise ConnectionError(f"read error at address {address!r}: no such register on this factory")
        return address

    async def read(self, address: Any) -> Any:
        self._check()
        return self.plant.values[self._slot(address)]

    async def write(self, address: Any, value: Any) -> None:
        self._check()
        self.plant.values[self._slot(address)] = int(value)
