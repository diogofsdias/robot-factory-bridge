# Running everything in containers

Three containers from one image: `opcua-sim` and `modbus-sim` (the two simulated
factories, real OPC UA and Modbus servers) and `hmi` (the browser HMI with the release flow).
The HMI reaches the factories by container name, using `config/docker/`.

## Install Docker (Windows)

Docker Desktop with the WSL2 backend (you already have WSL2). It may ask for a restart.
Start Docker Desktop and wait until it says "running", then check:

```
docker --version
docker compose version
```

## Run

From the project folder:

```
docker compose up --build
```

The first build takes a few minutes. Open http://localhost:8080. Both factory cards should be
online and show `Release v1.0.0`. The first start creates the releases in a Docker volume
(`deploy-data`), so the active release and history survive restarts.

Stop with Ctrl+C, then `docker compose down`. Add `-v` to also delete the volume.

## Check it

```
docker compose ps                      # hmi should become "healthy"
docker compose logs hmi                # first start shows the ota init lines
curl http://localhost:8080/api/health  # {"status": "ok", ...}   (PowerShell: curl.exe)
```

## Roll out releases inside the containers

These run the same flow as in `docs/ota.md`, against the real OPC UA and Modbus servers
(no `--demo`). The bundles in `releases/docker/` use the container host names.

```
docker compose exec hmi python -m ota --root deploy deploy releases/docker/factory-b-v1.1.0 --service http://localhost:8080
docker compose exec hmi python -m ota --root deploy deploy releases/docker/factory-b-v1.2.0 --service http://localhost:8080
docker compose exec hmi python -m ota --root deploy deploy releases/docker/factory-b-v1.2.0 --service http://localhost:8080 --skip-preflight --gate-timeout 8
docker compose exec hmi python -m ota --root deploy status
```

1. **v1.1.0** passes pre-flight and the card switches to `Release v1.1.0`.
2. **v1.2.0** (status register 99 does not exist) is **rejected** by pre-flight: a real Modbus read
   of a register that is not there fails.
3. The same release with `--skip-preflight` is switched on, factory B goes COMMS LOST, the service
   gate times out and the deployer **rolls back** to v1.1.0.

Add `--functional` to the first command to also start the cell during pre-flight and check
that it runs (the simulators are a test cell, so that is safe here).

## Show that a container failure is survivable

```
docker compose stop modbus-sim     # factory B card: COMMS LOST, one event in the log
docker compose start modbus-sim    # it recovers by itself, no HMI restart
```

## If it does not start

| Symptom | Likely cause |
|---|---|
| `docker: command not found` | Docker Desktop is not installed or not started. |
| Factory A stays COMMS LOST, B is fine | OPC UA host name. Check `docker compose logs hmi opcua-sim`. |
| Factory B stays COMMS LOST, A is fine | `docker compose logs modbus-sim`. The pymodbus version is pinned to 3.8.x. |
| Port 8080 already in use | Stop the local `python -m hmi.server`, or change the left side of `127.0.0.1:8080:8080`. |
| Build fails on `pip install` | Network or proxy. Check Docker Desktop's network settings. |
| `ota` says "not initialised" | The volume is empty: `docker compose restart hmi` runs the first-start init. |

## What this is, honestly

A working multi-container setup: separate processes, service names, a health check, a
persistent volume and a rollout flow. It is not an orchestrated fleet: there is no registry, no
image-tag releases and no Kubernetes. In this project a release is a config bundle. The
same verify / pre-flight / switch / confirm / roll back flow would apply to image tags.
