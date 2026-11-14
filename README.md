# Factory Adapter

One adapter that connects a robot stack to **any factory's control system**, so a
new customer means a new config file, not a new custom build.

Two simulated factories speak different industrial protocols with different tag
layouts (OPC UA and Modbus TCP). A single adapter maps both onto one canonical
model, enforces a safety rule in the middle, and a ROS2 bridge exposes the result
to the robot side.

```mermaid
flowchart TB
    R\\\["Robot side<br/>(ROS2 nodes, AI stack)"] -->|"topics + services"| B\\\["ros2\\\_bridge<br/>one process per factory"]
    B --> A\\\["Adapter<br/>canonical model + safety rules"]
    A -->|"factory\\\_a.yaml"| D1\\\["OPC UA driver"]
    A -->|"factory\\\_b.yaml"| D2\\\["Modbus TCP driver"]
    D1 --> F1\\\["Factory A<br/>(simulated PLC)"]
    D2 --> F2\\\["Factory B<br/>(simulated PLC)"]
```

## Why this design

* **Protocols are plug-ins.** Drivers only move raw values. A new protocol is one
class and one line in the registry (`adapter/drivers/`).
* **Factories are configuration.** A YAML file maps each canonical tag
(`start`, `estop`, `status`, `cycle\\\_count`) to that factory's native address, and
can translate its status codes. A config that leaves a tag unmapped fails at
load time, not on site.
* **Safety lives in one place.** `start()` is refused while the e-stop is active;
`emergency\\\_stop()` is always accepted and also drops the start command. Every
factory and every interface (Python, ROS2) gets the same behaviour.
* **Refusals are answers, not crashes.** Over ROS2 a refused command returns
`success: false` with a reason.

## Layout

```
adapter/          canonical model, config loader, drivers (opcua, modbus), safety rules
config/           one YAML per factory
sim/              simulated factories (OPC UA server, Modbus server) + smoke test
ros2\\\_bridge/      ROS2 node: state as topics, commands as services
tests/            24 tests (adapter logic, config validation, bridge helpers)
docs/             factory layouts, ROS2 interface notes
demo.py           the same robot-side code driving both factories
```

## Quick start (Windows or Linux, no ROS needed)

Tested on Python 3.12 (Ubuntu) and 3.13 (Windows). Very new Python versions may lack
prebuilt wheels for some dependencies.

```
python -m venv .venv
.venv\\\\Scripts\\\\Activate.ps1          # Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                    # 24 passed
```

Run the two simulated factories in two terminals, then the demo in a third:

```
python sim/opcua\\\_server.py
python sim/modbus\\\_server.py
python demo.py
```

`demo.py` runs one function against both factories: start, run, emergency stop,
refused restart, reset.

## ROS2 bridge (Ubuntu 24.04, ROS2 Jazzy)

Developed on WSL2. Details in [docs/ros2\_bridge.md](docs/ros2_bridge.md).

```
python sim/opcua\\\_server.py                                            # terminal 1
python sim/modbus\\\_server.py                                           # terminal 2
python -m ros2\\\_bridge.factory\\\_bridge --config config/factory\\\_a.yaml   # terminal 3
python -m ros2\\\_bridge.factory\\\_bridge --config config/factory\\\_b.yaml   # terminal 4
```

Then from any ROS2 shell:

```
ros2 topic echo /factory\\\_a/status
ros2 service call /factory\\\_a/start std\\\_srvs/srv/Trigger
ros2 service call /factory\\\_a/emergency\\\_stop std\\\_srvs/srv/Trigger
ros2 service call /factory\\\_a/start std\\\_srvs/srv/Trigger    # refused: e-stop is active
ros2 service call /factory\\\_a/reset\\\_estop std\\\_srvs/srv/Trigger
```

The same commands work for `/factory\\\_b/...`, which runs over Modbus.

## Testing

The adapter and the bridge helpers are tested against an in-memory fake driver, so the
suite needs no network, simulators or ROS2. The real OPC UA and Modbus drivers and the
ROS2 node are exercised end to end with the simulators (see above).

## Status and roadmap

Done: canonical model, OPC UA and Modbus drivers, YAML-driven factories, safety rules,
simulators, ROS2 bridge, tests.

Planned:

* C++ `ros2\\\_control` hardware interface for a simulated actuator
* MQTT as a third protocol and factory
* Docker packaging, health checks and a simulated over-the-air update with rollback
* Fault injection and a diagnostic tool that classifies field issues
* Safety note mapping the interlock to ISO 13849 concepts

## Notes

* `pymodbus` is pinned to 3.8.x because its API changed in later releases.
* Everything here is generic and simulated.

