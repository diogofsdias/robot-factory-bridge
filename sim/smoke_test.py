"""Week 1 smoke test: talk to both simulated factories and check they respond.

Run the two servers first (see README), then:  python sim/smoke_test.py
"""
import asyncio

from asyncua import Client
from pymodbus.client import AsyncModbusTcpClient

OPCUA_URL = "opc.tcp://localhost:4840/factory/"
OPCUA_NS = "urn:factory:a"


async def check_opcua() -> None:
    async with Client(OPCUA_URL) as client:
        idx = await client.get_namespace_index(OPCUA_NS)
        start = await client.nodes.objects.get_child([f"{idx}:Station", f"{idx}:Start"])
        status = await client.nodes.objects.get_child([f"{idx}:Station", f"{idx}:Status"])
        await start.write_value(True)
        await asyncio.sleep(2)
        print("Factory A status after start:", await status.read_value(), "(expect 1)")
        await start.write_value(False)


async def check_modbus() -> None:
    client = AsyncModbusTcpClient("localhost", port=5020)
    await client.connect()
    await client.write_register(0, 1, slave=1)  # start
    await asyncio.sleep(2)
    rr = await client.read_holding_registers(2, count=2, slave=1)
    print("Factory B status/cycles after start:", rr.registers, "(expect status 1)")
    await client.write_register(0, 0, slave=1)
    client.close()


async def main() -> None:
    await check_opcua()
    await check_modbus()
    print("Both factories respond. Week 1 core done.")


if __name__ == "__main__":
    asyncio.run(main())
