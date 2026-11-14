"""Glue between ROS2 (callbacks, threads) and the adapter (asyncio).

The adapter is async. ROS2 callbacks are plain functions. So the adapter lives
on its own asyncio loop in a background thread, and ROS callbacks submit work
to it and wait for the result.
"""
import asyncio
import threading
from typing import Any, Callable, Coroutine

from adapter import AdapterError


class AsyncRunner:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="adapter-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro: Coroutine, timeout: float = 5.0) -> Any:
        """Run a coroutine on the adapter loop and return its result (or raise)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)


def execute(runner: AsyncRunner, make_coro: Callable[[], Coroutine]) -> tuple[bool, str]:
    """Run an adapter action and turn the outcome into (success, message).

    This is what every ROS2 service handler needs, so it lives here where it
    can be unit-tested. Adapter errors (e.g. a safety refusal) are expected
    outcomes, not crashes.
    """
    try:
        runner.call(make_coro())
        return True, "ok"
    except AdapterError as e:
        return False, str(e)
    except Exception as e:  # connection lost, timeout, ...
        return False, f"{type(e).__name__}: {e}"
