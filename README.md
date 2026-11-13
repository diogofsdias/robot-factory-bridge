# Factory Adapter (week 1: environment + simulated factories)

Goal of week 1: two simulated "factories" speaking different protocols with
different tag layouts. In week 2 you build the adapter that hides the difference.

## Windows setup (about 1 hour, one time)

1. **Python 3.11 or newer**: install from python.org, tick "Add python.exe to PATH".
2. **Git**: install Git for Windows, then `git init` in this folder.
3. **VS Code** with the Python extension.
4. **WSL2 + Ubuntu 22.04** (needed from week 3 for ROS2): in an admin PowerShell run
   `wsl --install -d Ubuntu-22.04`, then reboot.
5. **Docker Desktop** with the WSL2 backend enabled (used from week 5, install now).

## Run week 1 (PowerShell, in this folder)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Open three terminals (activate the venv in each):

```powershell
python sim\opcua_server.py      # terminal 1, Factory A, port 4840
python sim\modbus_server.py     # terminal 2, Factory B, port 5020
python sim\smoke_test.py        # terminal 3
```

Expected output of the smoke test: status 1 from both factories, then
"Both factories respond."

If Windows Firewall asks, allow Python on private networks (localhost only is fine).

## Week 1 checklist

- [ ] Both servers run and the smoke test passes
- [ ] Add a third simulated factory (MQTT, e.g. with the `amqtt` broker) with yet another tag layout
- [ ] Commit to Git, push a private GitHub repo
- [ ] Write down each factory's tag layout in `docs/factories.md` (this becomes the YAML mapping in week 2)

## Notes

- `pymodbus` is pinned to 3.8.x because its API changed in later releases.
- Your own ABB/Hitachi code and data must never go into this repo. Everything here is generic.
