"""Command line entry point: ``python -m factor_lab``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .audit import audit_experiment
from .brief import build_brief, write_decision
from .control import ensure_control, load_control, set_state
from .dashboard import render_dashboard
from .ledger import Ledger
from .maintenance import clean, inventory
from .policy import init_policy, load_policy
from .run import run_experiment, run_pilot
from .schemas import Hypothesis, experiment_dir
from .store import read_json, write_json_atomic

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _root(value: str) -> Path:
    path = Path(value)
    if path.exists() and path.is_dir():
        return path
    return experiment_dir(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factor_lab")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the P0 gate policy and control file")

    control_parser = sub.add_parser("control", help="show / pause / resume the lab")
    control_parser.add_argument("action", choices=["show", "pause", "resume", "stop"])
    control_parser.add_argument("--reason", default="")

    run_parser = sub.add_parser("run", help="run one hypothesis")
    run_parser.add_argument("--hypothesis", required=True, help="path to hypothesis JSON")
    run_parser.add_argument("--profile", default="quick")
    run_parser.add_argument("--robustness", action="store_true")
    run_parser.add_argument("--no-audit", action="store_true")

    sub.add_parser("pilot", help="run the amount_bias P0 pilot and dashboard")

    audit_parser = sub.add_parser("audit", help="audit one experiment")
    audit_parser.add_argument("--experiment", required=True)

    brief_parser = sub.add_parser("brief", help="build decision brief / decision for one experiment")
    brief_parser.add_argument("--experiment", required=True)

    sub.add_parser("dashboard", help="render the dashboard from artifacts")

    ledger_parser = sub.add_parser("ledger", help="ledger utilities")
    ledger_parser.add_argument("action", choices=["verify", "summary"])
    ledger_parser.add_argument("--path", default=None)

    maintenance_parser = sub.add_parser("maintenance", help="inventory / clean output")
    maintenance_parser.add_argument("action", choices=["inventory", "clean"])
    maintenance_parser.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init":
        from .examples import pilot_coverage

        policy = init_policy()
        control = ensure_control()
        if not config.DATA_COVERAGE_PATH.exists():
            write_json_atomic(config.DATA_COVERAGE_PATH, pilot_coverage())
        _print({"policy": policy, "control": control})
        return 0

    if args.command == "control":
        if args.action == "show":
            _print(load_control())
        else:
            state = {"pause": "paused", "resume": "running", "stop": "stopped"}[args.action]
            _print(set_state(state, reason=args.reason, actor="human"))
        return 0

    if args.command == "pilot":
        result = run_pilot()
        dashboard = render_dashboard()
        result["dashboard"] = str(dashboard)
        _print(result)
        return 0

    if args.command == "run":
        path = Path(args.hypothesis)
        raw = read_json(path)
        if not isinstance(raw, dict):
            raise SystemExit(f"hypothesis not found or invalid: {path}")
        hypothesis = Hypothesis.from_dict(raw)
        config.ensure_dirs()
        result = run_experiment(
            hypothesis,
            profile=args.profile,
            robustness=args.robustness,
            auto_audit=not args.no_audit,
        )
        _print(result)
        return 0

    if args.command == "audit":
        _print(audit_experiment(_root(args.experiment)))
        return 0

    if args.command == "brief":
        root = _root(args.experiment)
        audit = audit_experiment(root)
        brief = build_brief(root)
        decision = write_decision(root, brief, audit)
        _print({"brief": brief, "decision": decision})
        return 0

    if args.command == "dashboard":
        _print({"dashboard": str(render_dashboard())})
        return 0

    if args.command == "ledger":
        ledger = Ledger(Path(args.path) if args.path else None)
        _print(ledger.verify() if args.action == "verify" else ledger.summary())
        return 0

    if args.command == "maintenance":
        if args.action == "inventory":
            _print({"inventory": str(inventory())})
        else:
            _print({"actions": len(clean(apply=args.apply)), "apply": args.apply})
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
