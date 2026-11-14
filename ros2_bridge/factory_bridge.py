"""ROS2 bridge: exposes ONE factory (through the adapter) to the ROS2 world.

Run one bridge per factory. Same code for both; only the config differs.

    python -m ros2_bridge.factory_bridge --config config/factory_a.yaml

Interfaces (namespace = factory name with '-' replaced by '_'):
  topics    /<ns>/status        std_msgs/String   IDLE | RUNNING | FAULT | ESTOPPED
            /<ns>/cycle_count   std_msgs/Int32
            /<ns>/estop         std_msgs/Bool
  services  /<ns>/start            std_srvs/Trigger
            /<ns>/stop             std_srvs/Trigger
            /<ns>/emergency_stop   std_srvs/Trigger
            /<ns>/reset_estop      std_srvs/Trigger
"""
import argparse
import logging
import sys

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import Trigger

from adapter import Adapter
from ros2_bridge.runner import AsyncRunner, execute


class FactoryBridge(Node):
    def __init__(self, adapter: Adapter, runner: AsyncRunner, publish_hz: float = 2.0):
        namespace = adapter.config.name.replace("-", "_")
        super().__init__("bridge", namespace=namespace)
        self.adapter = adapter
        self.runner = runner

        self.pub_status = self.create_publisher(String, "status", 10)
        self.pub_cycles = self.create_publisher(Int32, "cycle_count", 10)
        self.pub_estop = self.create_publisher(Bool, "estop", 10)
        self.create_timer(1.0 / publish_hz, self._publish_state)

        self._add_trigger("start", lambda: self.adapter.start())
        self._add_trigger("stop", lambda: self.adapter.stop())
        self._add_trigger("emergency_stop", lambda: self.adapter.emergency_stop())
        self._add_trigger("reset_estop", lambda: self.adapter.set("estop", False))

        self.get_logger().info(
            f"bridging {adapter.config.name} ({adapter.config.protocol}) as /{namespace}"
        )

    def _publish_state(self) -> None:
        try:
            snap = self.runner.call(self.adapter.snapshot())
        except Exception as e:
            self.get_logger().warning(
                f"cannot read factory: {type(e).__name__}: {e}", throttle_duration_sec=5.0
            )
            return
        self.pub_status.publish(String(data=snap["status"].name))
        self.pub_cycles.publish(Int32(data=int(snap["cycle_count"])))
        self.pub_estop.publish(Bool(data=bool(snap["estop"])))

    def _add_trigger(self, name: str, make_coro) -> None:
        def handler(request, response):
            response.success, response.message = execute(self.runner, make_coro)
            # Two separate log lines on purpose: rclpy raises if one line of code
            # is used with two different severities.
            if response.success:
                self.get_logger().info(f"{name}: ok")
            else:
                self.get_logger().warning(f"{name}: {response.message}")
            return response

        self.create_service(Trigger, name, handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ROS2 bridge for one factory")
    parser.add_argument("--config", required=True, help="factory YAML, e.g. config/factory_a.yaml")
    parser.add_argument("--hz", type=float, default=2.0, help="state publish rate")
    args, ros_args = parser.parse_known_args(argv)

    logging.basicConfig(level=logging.WARNING)  # keep the OPC UA / Modbus libraries quiet

    rclpy.init(args=[sys.argv[0]] + ros_args)
    runner = AsyncRunner()
    adapter = Adapter.from_config_file(args.config)
    try:
        runner.call(adapter.__aenter__(), timeout=10)
    except Exception as e:
        print(
            f"Cannot connect to {adapter.config.name}: {type(e).__name__}: {e}\n"
            f"Is the simulator running?",
            file=sys.stderr,
        )
        runner.stop()
        rclpy.try_shutdown()
        return 1

    node = FactoryBridge(adapter, runner, args.hz)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            runner.call(adapter.__aexit__(None, None, None), timeout=5)
        except Exception:
            pass
        runner.stop()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())