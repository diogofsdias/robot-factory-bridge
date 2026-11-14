# Simulated factories

Two stations, same job, different protocol and layout. The adapter hides the
difference (see `config/*.yaml`).

| | Factory A | Factory B |
|---|---|---|
| Protocol | OPC UA | Modbus TCP |
| Endpoint | `opc.tcp://localhost:4840/factory/` | `localhost:5020`, unit 1 |
| Addressing | Browse path under Objects, namespace `urn:factory:a` | Holding register numbers |
| start (cmd) | `Station/Start` (bool) | register 0 (0/1) |
| estop (cmd) | `Station/EStop` (bool) | register 1 (0/1) |
| status | `Station/Status` (int) | register 2 |
| cycle count | `Station/CycleCount` (int) | register 3 |
| Status codes | 0 idle, 1 running, 2 fault, 3 estopped | same numbers (mapped via `status_map`) |

## Canonical model (what the robot side sees)

| Tag | Type | Writable |
|---|---|---|
| `start` | bool | yes |
| `estop` | bool | yes |
| `status` | `Status` enum (IDLE, RUNNING, FAULT, ESTOPPED) | no |
| `cycle_count` | int | no |

## Behaviour rules enforced by the adapter

- `start()` is refused with `SafetyError` while the e-stop is active.
- `emergency_stop()` is always accepted and also drops the start command.
- Writing a read-only tag raises `TagError`.
- A config that leaves a canonical tag unmapped fails at load time, not at 3 a.m. on site.
