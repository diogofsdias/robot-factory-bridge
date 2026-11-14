# ROS2 bridge

One bridge process per factory. Same code, different config. It publishes the
factory's state as ROS2 topics and offers commands as ROS2 services, so any
ROS2 node (robot stack, logger, dashboard) can use a factory without knowing
its protocol.

```
ROS2 nodes  <-- topics/services -->  factory_bridge  -->  Adapter  -->  OPC UA / Modbus  -->  factory
```

## Interfaces

Namespace is the factory name with `-` replaced by `_` (`factory-a` -> `/factory_a`).

| Name | Type | Direction |
|---|---|---|
| `/<ns>/status` | `std_msgs/String` (IDLE, RUNNING, FAULT, ESTOPPED) | published at 2 Hz |
| `/<ns>/cycle_count` | `std_msgs/Int32` | published at 2 Hz |
| `/<ns>/estop` | `std_msgs/Bool` | published at 2 Hz |
| `/<ns>/start` | `std_srvs/Trigger` | service; refused while e-stop is active |
| `/<ns>/stop` | `std_srvs/Trigger` | service |
| `/<ns>/emergency_stop` | `std_srvs/Trigger` | service; always accepted |
| `/<ns>/reset_estop` | `std_srvs/Trigger` | service |

A refused command is a normal answer (`success: false` plus a message), not an error.

## Design notes

- The adapter is asyncio, ROS2 callbacks are plain functions. `ros2_bridge/runner.py`
  runs the adapter on its own event loop in a background thread; ROS callbacks submit
  work to it and wait for the result.
- Service handling (`execute()`) is ROS-free and unit-tested in `tests/test_bridge_runner.py`.
- The bridge runs with the project venv (not `ros2 run`), because the venv holds
  `asyncua` and `pymodbus`. ROS2 is found through the `source /opt/ros/jazzy/setup.bash`
  line in `~/.bashrc`.
- If the factory is unreachable at startup the bridge exits with a clear message.
  If the link drops while running it logs a throttled warning and keeps trying.

## Run (all in Ubuntu, factory venv active)

```
python sim/opcua_server.py                                       # window 1
python sim/modbus_server.py                                      # window 2
python -m ros2_bridge.factory_bridge --config config/factory_a.yaml   # window 3
python -m ros2_bridge.factory_bridge --config config/factory_b.yaml   # window 4
```

Inspect and control from a plain Ubuntu window (no venv needed):

```
ros2 node list
ros2 topic list
ros2 topic echo /factory_a/status
ros2 service call /factory_a/start std_srvs/srv/Trigger
ros2 service call /factory_a/emergency_stop std_srvs/srv/Trigger
ros2 service call /factory_a/start std_srvs/srv/Trigger     # refused
ros2 service call /factory_a/reset_estop std_srvs/srv/Trigger
```
