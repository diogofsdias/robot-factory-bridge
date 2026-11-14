"""Week 2 demo: ONE piece of robot-side code drives both factories.

Start both simulators first (sim/opcua_server.py and sim/modbus_server.py), then:
    python demo.py
"""
import asyncio
import logging
from pathlib import Path

from adapter import Adapter, SafetyError

CONFIGS = ["config/factory_a.yaml", "config/factory_b.yaml"]


async def run_station(config_path: str) -> None:
    """The robot-side logic. It never mentions OPC UA or Modbus."""
    async with Adapter.from_config_file(Path(config_path)) as factory:
        name = factory.config.name
        print(f"[{name}] before: {await factory.snapshot()}")

        await factory.start()
        await asyncio.sleep(2.5)
        print(f"[{name}] running: {await factory.snapshot()}")

        await factory.emergency_stop()
        await asyncio.sleep(1.5)
        print(f"[{name}] e-stop:  {await factory.snapshot()}")

        try:
            await factory.start()
        except SafetyError as e:
            print(f"[{name}] refused as expected: {e}")

        # reset so the simulator is clean for the next run
        await factory.set("estop", False)
        await factory.stop()


async def main() -> None:
    for cfg in CONFIGS:
        await run_station(cfg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)  # hide the very chatty protocol libraries
    asyncio.run(main())
