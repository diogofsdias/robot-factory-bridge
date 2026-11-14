"""Modbus TCP driver. Address format: holding register number (int)."""
from typing import Any

from .base import Driver


class ModbusDriver(Driver):
    def __init__(self, host: str, port: int = 502, unit: int = 1, **_ignored):
        self.host, self.port, self.unit = host, port, unit
        self._client = None

    async def connect(self) -> None:
        from pymodbus.client import AsyncModbusTcpClient  # lazy import, see opcua.py

        self._client = AsyncModbusTcpClient(self.host, port=self.port)
        if not await self._client.connect():
            raise ConnectionError(f"Modbus connect failed: {self.host}:{self.port}")

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def read(self, address: int) -> Any:
        rr = await self._client.read_holding_registers(address, count=1, slave=self.unit)
        if rr.isError():
            raise ConnectionError(f"Modbus read error at register {address}: {rr}")
        return rr.registers[0]

    async def write(self, address: int, value: Any) -> None:
        rr = await self._client.write_register(address, int(value), slave=self.unit)
        if rr.isError():
            raise ConnectionError(f"Modbus write error at register {address}: {rr}")
