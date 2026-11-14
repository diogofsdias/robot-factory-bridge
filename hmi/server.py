"""Browser HMI server. Standard library only, no extra packages.

    python -m hmi.server                 # real factories (start the simulators first)
    python -m hmi.server --demo          # in-memory factories, nothing else needed

Then open http://localhost:8080
"""
import argparse
import json
import sys
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from adapter import Adapter
from hmi.core import Hmi
from hmi.simdriver import SimDriver, SimPlant
from ota.state import active_config, list_factories

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "static" / "index.html"
DEFAULT_CONFIGS = [ROOT / "config" / "factory_a.yaml", ROOT / "config" / "factory_b.yaml"]


def make_handler(hmi: Hmi):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(200, hmi.state())
            elif urlsplit(self.path).path == "/api/health":
                stations = {s["key"]: s["connected"] for s in hmi.state()["stations"]}
                degraded = not all(stations.values())
                strict = "strict=1" in urlsplit(self.path).query
                self._json(503 if (degraded and strict) else 200,
                           {"status": "degraded" if degraded else "ok", "stations": stations})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            # JSON only: a browser page on another site cannot send this without a
            # CORS preflight, which we never answer. Blocks drive-by commands.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "Content-Type must be application/json"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                station = str(body["station"])
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "bad request"})

            if self.path == "/api/command":
                try:
                    ok, message = hmi.command(station, str(body.get("command", "")))
                except KeyError:
                    return self._json(404, {"error": f"unknown station '{station}'"})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"ok": ok, "message": message})

            if self.path == "/api/demo" and hmi.demo:
                st = hmi.stations.get(station)
                if st is None:
                    return self._json(404, {"error": f"unknown station '{station}'"})
                st.adapter.driver.online = bool(body.get("online", True))
                return self._json(200, {"ok": True})

            self._json(404, {"error": "not found"})

    return Handler


def make_adapter(path, demo: bool, plants: dict | None = None) -> Adapter:
    adapter = Adapter.from_config_file(path)
    if demo:
        # one plant per factory name: a new release talks to the same simulated factory
        plants = plants if plants is not None else {}
        plant = plants.setdefault(adapter.config.name, SimPlant(adapter.config.tags))
        adapter.driver = SimDriver(plant)
    return adapter


def build_hmi(config_paths, demo: bool, deploy_root=None) -> Hmi:
    if deploy_root:
        names = list_factories(deploy_root)
        if not names:
            raise SystemExit(f"nothing deployed in {deploy_root}. Run first: python -m ota init")
        config_paths = [active_config(deploy_root, n) for n in names]
    plants: dict = {}
    adapters = [make_adapter(p, demo, plants) for p in config_paths]
    return Hmi(adapters, demo=demo, deploy_root=deploy_root,
               adapter_factory=lambda p: make_adapter(p, demo, plants))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Browser HMI for the factory adapter")
    ap.add_argument("--config", action="append", help="factory YAML (repeatable); default: both factories")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1", help="default is local only")
    ap.add_argument("--demo", action="store_true", help="use in-memory simulated factories")
    ap.add_argument("--deploy", help="deploy folder from 'python -m ota init'; enables releases and live reload")
    args = ap.parse_args(argv)

    hmi = build_hmi(args.config or DEFAULT_CONFIGS, args.demo, args.deploy)
    hmi.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(hmi))
    mode = "demo (in-memory factories)" if args.demo else "live factories"
    print(f"HMI running with {mode}: http://localhost:{args.port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        hmi.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
