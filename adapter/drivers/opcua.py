"""OPC UA driver. Address format: browse path under Objects, e.g. "Station/Start"."""
from typing import Any

from .base import Driver


class OpcUaDriver(Driver):
    def __init__(self, url: str, namespace: str, **_ignored):
        self.url = url
        self.namespace = namespace
        self._client = None
        self._idx = None
        self._nodes: dict[str, Any] = {}

    async def connect(self) -> None:
        from asyncua import Client  # imported here so tests don't need asyncua

        self._client = Client(self.url)
        await self._client.connect()
        self._idx = await self._client.get_namespace_index(self.namespace)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            self._nodes.clear()

    async def _node(self, address: str):
        if address not in self._nodes:
            path = [f"{self._idx}:{part}" for part in address.split("/")]
            self._nodes[address] = await self._client.nodes.objects.get_child(path)
        return self._nodes[address]

    async def read(self, address: str) -> Any:
        return await (await self._node(address)).read_value()

    async def write(self, address: str, value: Any) -> None:
        await (await self._node(address)).write_value(value)
