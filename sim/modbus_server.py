"""Simulated Factory B: a Modbus TCP server (port 5020, unit id 1).

Deliberately a different layout from Factory A, so the adapter has to map
both onto one canonical model.

Holding registers:
  0  start command   (0/1, writable)
  1  estop command   (0/1, writable)
  2  status          (0=idle, 1=running, 2=fault, 3=estopped)
  3  cycle counter   (increments while running)

Written for pymodbus 3.8.x (pinned in requirements.txt). Later pymodbus
versions renamed ModbusSlaveContext -> ModbusDeviceContext.
"""
import asyncio
import logging

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

HOST, PORT = "0.0.0.0", 5020
HR = 3  # function code for holding registers in the datastore API

log = logging.getLogger("factory-b")


async def simulate(context: ModbusServerContext) -> None:
    """Update status and cycle counter from the command registers."""
    count = 0
    while True:
        await asyncio.sleep(1)
        store = context[0]
        start, estop = store.getValues(HR, 0, count=2)
        if estop:
            store.setValues(HR, 2, [3])
        elif start:
            count += 1
            store.setValues(HR, 2, [1])
            store.setValues(HR, 3, [count % 65536])
        else:
            store.setValues(HR, 2, [0])


async def main() -> None:
    block = ModbusSequentialDataBlock(0, [0] * 16)
    store = ModbusSlaveContext(hr=block)
    context = ModbusServerContext(slaves=store, single=True)

    sim_task = asyncio.create_task(simulate(context))
    log.info("Factory B (Modbus TCP) listening on %s:%s", HOST, PORT)
    try:
        await StartAsyncTcpServer(context=context, address=(HOST, PORT))
    finally:
        sim_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
