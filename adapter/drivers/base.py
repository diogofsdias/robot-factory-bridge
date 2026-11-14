from abc import ABC, abstractmethod
from typing import Any


class Driver(ABC):
    """Protocol-specific part. Knows how to move raw values, nothing about meaning.

    `address` is whatever the protocol uses (an OPC UA browse path, a Modbus
    register number). It comes from the factory's YAML config.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def read(self, address: Any) -> Any: ...

    @abstractmethod
    async def write(self, address: Any, value: Any) -> None: ...
