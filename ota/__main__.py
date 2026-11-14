"""Command line for the release flow.

    python -m ota init                        # first release of every factory in config/
    python -m ota pack --config config/factory_b.yaml --version v1.1.0
    python -m ota deploy releases/factory-b-v1.1.0 --service http://localhost:8080
    python -m ota status
    python -m ota rollback factory-b --service http://localhost:8080
"""
import argparse
import sys
from pathlib import Path

from adapter import AdapterError

from .bundle import BundleError, pack
from .deployer import Deployer, OtaError
from .health import check_config, wait_for_service
from .state import list_factories

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIGS = [ROOT / "config" / "factory_a.yaml", ROOT / "config" / "factory_b.yaml"]


def build_deployer(args) -> Deployer:
    driver_factory = None
    if getattr(args, "demo", False):
        from adapter.config import load_config
        from hmi.simdriver import SimDriver, SimPlant
        from .state import active_config

        def driver_factory(cfg):
            # the demo factory has the registers of the ACTIVE release; the candidate must fit them
            base = load_config(active_config(args.root, cfg.name)).tags
            return SimDriver(SimPlant(base, tick=0.2), owns_plant=True)

    def health(path):
        return check_config(path, driver_factory=driver_factory, functional=getattr(args, "functional", False))

    gate = None
    if getattr(args, "service", None):
        gate = lambda factory, version: wait_for_service(  # noqa: E731
            args.service, factory, version, timeout=args.gate_timeout)
    return Deployer(args.root, health=health, gate=gate, log=lambda m: print("  " + m))


def cmd_init(args) -> int:
    d = Deployer(args.root)
    for cfg in args.config or DEFAULT_CONFIGS:
        out = d.init(Path(cfg), args.version)
        print(f"{out.factory}: initial release {out.version} installed and active")
    return 0


def cmd_pack(args) -> int:
    dest = pack(Path(args.config), args.version, Path(args.out), args.notes)
    print(f"bundle written to {dest}")
    return 0


def cmd_deploy(args) -> int:
    d = build_deployer(args)
    print(f"Deploying {args.bundle}")
    out = d.deploy(Path(args.bundle), skip_preflight=args.skip_preflight)
    print(f"RESULT: {out.result.upper()} {out.factory} {out.version}: {out.detail}")
    return 0 if out.ok else 1


def cmd_rollback(args) -> int:
    out = build_deployer(args).rollback(args.factory)
    print(f"RESULT: {out.factory} now on {out.version} ({out.detail})")
    return 0


def cmd_status(args) -> int:
    d = Deployer(args.root)
    names = list_factories(args.root)
    if not names:
        print(f"nothing deployed in {args.root}. Run: python -m ota init")
        return 1
    for name in names:
        s = d.status(name)
        print(f"{name}: active {s['active']}, previous {s.get('previous') or '-'}")
        for h in s.get("history", [])[-5:]:
            print(f"    {h['time']}  {h['version']:<14} {h['action']:<9} {h['result']:<12} {h['detail']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ota", description="Release flow for factory configs")
    ap.add_argument("--root", default="deploy", help="deploy folder (default: deploy)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="install the first release of each factory")
    p.add_argument("--config", action="append")
    p.add_argument("--version", default="v1.0.0")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("pack", help="build a release bundle from a factory YAML")
    p.add_argument("--config", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--out", default="releases")
    p.add_argument("--notes", default="")
    p.set_defaults(fn=cmd_pack)

    def add_gate_args(p):
        p.add_argument("--service", help="URL of the running HMI, e.g. http://localhost:8080")
        p.add_argument("--gate-timeout", type=float, default=20.0)
        p.add_argument("--demo", action="store_true", help="pre-flight against in-memory factories")

    p = sub.add_parser("deploy", help="verify, check, switch and confirm a release")
    p.add_argument("bundle")
    p.add_argument("--functional", action="store_true", help="also start the cell during pre-flight (test cell only)")
    p.add_argument("--skip-preflight", action="store_true", help="demo: simulate a release that bypassed testing")
    add_gate_args(p)
    p.set_defaults(fn=cmd_deploy)

    p = sub.add_parser("rollback", help="go back to the previous release")
    p.add_argument("factory")
    add_gate_args(p)
    p.set_defaults(fn=cmd_rollback)

    p = sub.add_parser("status", help="show active release and history")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (OtaError, BundleError, AdapterError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
