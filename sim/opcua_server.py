"""Simulated Factory A: an OPC UA server (port 4840).

Tag layout (namespace urn:factory:a, under object "Station"):
  Start        bool   writable  - command: start the robot cycle
  EStop        bool   writable  - command: emergency stop
  Status       int    read      - 0=idle, 1=running, 2=fault, 3=estopped
  CycleCount   int    read      - increments while running
"""
import asyncio
import logging

from asyncua import Server

ENDPOINT = "opc.tcp://0.0.0.0:4840/factory/"
NAMESPACE = "urn:factory:a"

log = logging.getLogger("factory-a")


async def main() -> None:
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    idx = await server.register_namespace(NAMESPACE)

    station = await server.nodes.objects.add_object(idx, "Station")
    start = await station.add_variable(idx, "Start", False)
    estop = await station.add_variable(idx, "EStop", False)
    status = await station.add_variable(idx, "Status", 0)
    cycles = await station.add_variable(idx, "CycleCount", 0)
    await start.set_writable()
    await estop.set_writable()

    async with server:
        log.info("Factory A (OPC UA) listening on %s", ENDPOINT)
        count = 0
        while True:
            await asyncio.sleep(1)
            if await estop.read_value():
                await status.write_value(3)
            elif await start.read_value():
                await status.write_value(1)
                count += 1
                await cycles.write_value(count)
            else:
                await status.write_value(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
