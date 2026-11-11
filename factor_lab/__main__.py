"""Command line entry point for the Alpha research engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import jsonable, read_json


def _print(payload: Any) -> None:
    print(json.dumps(jsonable(payload), ensure_ascii=False, indent=2))


def _codes(value: str) -> list[str]:
    path = Path(value)
    if path.exists() and path.is_file():
        raw = path.read_text(encoding="utf-8")
    else:
        raw = value
    return sorted({part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()})


def _alpha_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("alpha", help="Alpha DSL / research engine commands")
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "eval",
            "benchmark",
            "search",
            "confirm",
            "holdout",
            "trials",
            "memory",
            "library",
            "campaign",
            "ml",
            "discovery",
            "roles",
            "deploy",
            "allocate",
            "cycle",
            "team",
            "behavior",
        ],
        nargs="?",
        default="holdout",
    )
    parser.add_argument("team_command", nargs="?", default="")
    parser.add_argument("--expression", default="")
    parser.add_argument("--codes", default=str(config.LAB_ROOT / "hypotheses" / "pool200.txt"))
    parser.add_argument("--start", default="2022-10-01")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--dev-end", default="")
    parser.add_argument("--holdout-start", default="")
    parser.add_argument("--holdout-end", default="")
    parser.add_argument("--warmup", type=int, default=300)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--cost-bps", type=float, default=20.0)
    parser.add_argument("--budget", type=int, default=120)
    parser.add_argument("--include-holdout", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--search", default="")
    parser.add_argument("--seed-file", default="")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--objective", default="")
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--campaign-budget", type=float, default=0.0)
    parser.add_argument("--features", default="close,volume,amount")
    parser.add_argument("--ml-splits", type=int, default=4)
    parser.add_argument("--ml-embargo", type=int, default=5)
    parser.add_argument("--ml-out", default="")
    parser.add_argument("--discovery-file", default="")
    parser.add_argument("--round-file", default="")
    parser.add_argument("--factor-artifact", default="")
    parser.add_argument("--team-action", choices=["plan", "verify", "brief"], default="plan")
    parser.add_argument("--behavior-action", choices=["list", "show", "verify"], default="show")
    parser.add_argument("--agent", default="")


def _run_alpha(args: argparse.Namespace) -> int:
    from factor_lab.alpha import parse_expression
    from factor_lab.alpha.allocator import CapitalAllocator
    from factor_lab.alpha.benchmarks import run_benchmark
    from factor_lab.alpha.campaign import CampaignController
    from factor_lab.alpha.confirm import confirm_search
    from factor_lab.alpha.data import HoldoutWindow, default_holdout, load_alpha_dataset, load_holdout, save_holdout
    from factor_lab.alpha.deployability import evaluate_long_only_expression
    from factor_lab.alpha.discovery import ingest_discovery
    from factor_lab.alpha.evaluation import evaluate_expression
    from factor_lab.alpha.factor_library import library_summary
    from factor_lab.alpha.memory import AlphaMemory
    from factor_lab.alpha.ml_branch import make_ml_plan, run_ml_experiment
    from factor_lab.alpha.pipeline import run_research_cycle
    from factor_lab.alpha.roles import role_contracts
    from factor_lab.alpha.search import run_search
    from factor_lab.alpha.supervisor import Supervisor
    from factor_lab.alpha.trials import summarize_trials
    from factor_lab.alpha.validation import validate

    if args.action == "validate":
        if not args.expression:
            raise SystemExit("alpha validate requires --expression")
        _print(validate(parse_expression(args.expression)).to_dict())
        return 0
    if args.action == "holdout":
        window = default_holdout(args.end)
        if args.holdout_start:
            window.holdout_start = args.holdout_start
        if args.holdout_end:
            window.holdout_end = args.holdout_end
        payload = window.to_dict()
        payload["path"] = str(save_holdout(window)) if args.save else None
        _print(payload)
        return 0
    if args.action == "trials":
        _print(summarize_trials())
        return 0
    if args.action == "memory":
        _print(AlphaMemory().summary())
        return 0
    if args.action == "library":
        _print(library_summary())
        return 0
    if args.action == "roles":
        _print(role_contracts())
        return 0
    if args.action == "team":
        from factor_lab.alpha.team import bootstrap_plan, session_brief, validate_roster

        team_command = args.team_command or args.team_action
        if team_command == "verify":
            _print(validate_roster())
        elif team_command == "brief":
            _print(session_brief())
        else:
            _print(bootstrap_plan())
        return 0
    if args.action == "behavior":
        from factor_lab.alpha.team.behavior import list_behaviors, load_behavior, verify_behaviors
        from factor_lab.alpha.team.bootstrap import load_roster

        behavior_command = args.behavior_action
        if behavior_command == "list":
            _print([
                {"agent": row["agent"], "behavior_hash": row["behavior_hash"], "n_evals": row["n_evals"]}
                for row in list_behaviors()
            ])
            return 0
        if behavior_command == "verify":
            roster = load_roster()
            _print(verify_behaviors([role["name"] for role in roster["roles"]]))
            return 0
        if not args.agent:
            raise SystemExit("alpha behavior show requires --agent or use --behavior-action list|verify")
        _print(load_behavior(args.agent))
        return 0
    if args.action == "allocate":
        _print(CapitalAllocator(AlphaMemory()).allocate())
        return 0
    if args.action == "discovery":
        if args.discovery_file:
            payload = read_json(Path(args.discovery_file))
        else:
            payload = read_json(config.LAB_ROOT / "discovery" / "discovery_map.json")
        if not isinstance(payload, dict):
            raise SystemExit("no discovery map; pass --discovery-file or create output/factor_lab/discovery/discovery_map.json")
        _print(ingest_discovery(payload))
        return 0
    if args.action == "campaign":
        controller = CampaignController()
        if args.campaign_id and args.round_file:
            payload = read_json(Path(args.round_file))
            if not isinstance(payload, dict):
                raise SystemExit("round file must contain a JSON object")
            _print(controller.record_round(args.campaign_id, payload))
            return 0
        if args.campaign_id:
            _print(controller.summary(args.campaign_id))
            return 0
        if not args.objective:
            raise SystemExit("alpha campaign requires --objective or --campaign-id")
        _print(controller.create(args.objective, max_rounds=args.max_rounds, budget=args.campaign_budget or None))
        return 0
    if args.action == "cycle":
        if not args.factor_artifact or not args.campaign_id:
            raise SystemExit("alpha cycle requires --factor-artifact and --campaign-id")
        from factor_lab.alpha.runner import load_artifact

        cycle_codes = _codes(args.codes)
        if not cycle_codes:
            raise SystemExit("no codes supplied")
        dataset = load_alpha_dataset(cycle_codes, args.start, args.end, warmup_days=args.warmup)
        payload = run_research_cycle(
            args.campaign_id,
            load_artifact(args.factor_artifact),
            dataset,
            horizon=args.horizon,
            cost_bps=args.cost_bps,
            supervisor=Supervisor(),
            memory=AlphaMemory(),
        )
        _print(
            {
                "schema": payload["schema"],
                "campaign_id": payload["campaign_id"],
                "factor_ref": payload["factor_ref"],
                "round": payload["round"],
                "evaluation": payload["evaluation"],
                "edge_card": payload["edge_card"]["edge_id"],
            }
        )
        return 0

    codes = _codes(args.codes)
    if not codes:
        raise SystemExit("no codes supplied")
    locked = load_holdout()
    if locked is not None and not any([args.dev_end, args.holdout_start, args.holdout_end]):
        window = locked
    else:
        try:
            window = default_holdout(args.end)
        except ValueError:
            window = HoldoutWindow(args.start, args.end, args.end, args.end, locked=False)
    if args.start:
        window.dev_start = args.start
    if args.dev_end:
        window.dev_end = args.dev_end
    if args.holdout_start:
        window.holdout_start = args.holdout_start
    if args.holdout_end:
        window.holdout_end = args.holdout_end

    if args.action == "eval":
        if not args.expression:
            raise SystemExit("alpha eval requires --expression")
        dataset = load_alpha_dataset(codes, args.start, args.end, warmup_days=args.warmup)
        _print(evaluate_expression(args.expression, dataset.context, horizon=args.horizon, cost_bps=args.cost_bps, start=args.start, end=args.end))
        return 0
    if args.action == "deploy":
        if not args.expression:
            raise SystemExit("alpha deploy requires --expression")
        dataset = load_alpha_dataset(codes, args.start, args.end, warmup_days=args.warmup)
        _print(evaluate_long_only_expression(args.expression, dataset.context, horizon=args.horizon, cost_bps=args.cost_bps, start=args.start, end=args.end))
        return 0
    if args.action == "ml":
        dataset = load_alpha_dataset(codes, args.start, args.end, warmup_days=args.warmup)
        features = [item.strip() for item in args.features.split(",") if item.strip()]
        plan = make_ml_plan("CLI ML experiment", features, target_horizon=args.horizon, n_splits=args.ml_splits, embargo_days=args.ml_embargo)
        result = run_ml_experiment(dataset, plan, out_dir=args.ml_out or str(config.LAB_ROOT / "alpha" / "ml"))
        _print({"plan_id": plan.plan_id, "metrics": result["metrics"], "cv": result["cv"], "monitor": result["monitor"], "path": args.ml_out or str(config.LAB_ROOT / "alpha" / "ml")})
        return 0
    if args.action == "benchmark":
        locked = load_holdout()
        if args.holdout_start or args.holdout_end:
            bench_window = HoldoutWindow(
                dev_start=args.start,
                dev_end=args.dev_end or args.end,
                holdout_start=args.holdout_start or args.end,
                holdout_end=args.holdout_end or args.end,
                locked=False,
            )
            data_end = args.end
        elif locked is not None:
            bench_window = locked
            data_end = locked.holdout_end if args.include_holdout else locked.dev_end
        else:
            bench_window = HoldoutWindow(
                dev_start=args.start,
                dev_end=args.dev_end or args.end,
                holdout_start=args.end,
                holdout_end=args.end,
                locked=False,
            )
            data_end = args.end
        dataset = load_alpha_dataset(codes, bench_window.dev_start, data_end, warmup_days=args.warmup)
        payload = run_benchmark(dataset, holdout=bench_window, horizon=args.horizon, cost_bps=args.cost_bps, include_holdout=args.include_holdout, out_path=args.out or None)
        _print({"benchmark_id": payload["benchmark_id"], "summary": payload["summary"], "path": args.out or str(config.LAB_ROOT / "alpha" / "benchmark")})
        return 0
    if args.action == "confirm":
        if not args.search:
            raise SystemExit("alpha confirm requires --search")
        locked = load_holdout()
        if locked is None:
            raise SystemExit("no locked holdout; run alpha holdout --save first")
        dataset = load_alpha_dataset(codes, locked.holdout_start, locked.holdout_end, warmup_days=args.warmup)
        dataset.start = locked.holdout_start
        dataset.end = locked.holdout_end
        payload = confirm_search(args.search, dataset, horizon=args.horizon, cost_bps=args.cost_bps, holdout=locked)
        _print({"expression": payload["expression"], "passed": payload["passed"], "metrics": {key: payload["metrics"].get(key) for key in ("ic_mean", "net_spread", "turnover", "capacity_multiple")}, "long_only": payload.get("long_only"), "red_team_passed": payload["red_team"].get("passed"), "path": payload["path"]})
        return 0
    if args.action == "search":
        dataset = load_alpha_dataset(codes, window.dev_start, window.dev_end, warmup_days=args.warmup)
        seed_expressions = []
        if args.seed_file:
            raw = read_json(Path(args.seed_file))
            if isinstance(raw, list):
                seed_expressions = [str(item) for item in raw]
            elif isinstance(raw, dict):
                seed_expressions = [str(item) for item in (raw.get("expressions") or [])]
        payload = run_search(dataset, budget=args.budget, horizon=args.horizon, cost_bps=args.cost_bps, holdout=window, seed=args.seed, seed_expressions=seed_expressions, out_path=args.out or None)
        _print({"search_id": payload["search_id"], "n_evaluated": payload["n_evaluated"], "n_ranked": payload["n_ranked"], "fdr": payload["fdr"], "top": [{"expression": item["expression"], "score": item["score"], "net": item["metrics"].get("net_spread"), "ic": item["metrics"].get("ic_mean")} for item in payload["top"][:5]], "confirmed": len([item for item in payload["confirmed"] if item.get("red_team", {}).get("passed")]), "path": args.out or str(config.LAB_ROOT / "alpha" / "search" / f"{payload['search_id']}.json")})
        return 0
    raise SystemExit(f"unknown alpha action: {args.action}")


def _run_doctor() -> int:
    from factor_lab.alpha import parse_expression
    from factor_lab.alpha.data import default_holdout, load_holdout
    from factor_lab.alpha.factor_library import library_summary
    from factor_lab.alpha.memory import AlphaMemory

    checks = {}
    try:
        parse_expression("rank(close)")
        checks["alpha_dsl"] = True
    except Exception as exc:  # noqa: BLE001
        checks["alpha_dsl"] = f"failed: {exc}"
    try:
        from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway

        DataInterfaceGateway()
        checks["data_interface"] = True
    except Exception as exc:  # noqa: BLE001
        checks["data_interface"] = f"failed: {exc}"
    checks["lab_root"] = str(config.LAB_ROOT)
    config.LAB_ROOT.mkdir(parents=True, exist_ok=True)
    checks["memory"] = AlphaMemory().summary()
    checks["factor_library"] = library_summary()
    checks["holdout"] = load_holdout().to_dict() if load_holdout() else default_holdout().to_dict()
    ok = checks.get("alpha_dsl") is True and checks.get("data_interface") is True
    _print({"ok": bool(ok), "checks": checks})
    return 0 if ok else 1


def _run_dry_run() -> int:
    from factor_lab.alpha import parse_expression
    from factor_lab.alpha.memory import AlphaMemory
    from factor_lab.alpha.roles import role_contracts

    checks = {
        "alpha_dsl": bool(parse_expression("rank(close)")),
        "memory": AlphaMemory().summary()["schema"],
        "roles": role_contracts()["n_llm_roles"],
        "lab_root": str(config.LAB_ROOT),
    }
    _print({"ok": True, "blockers": [], "checks": checks})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factor_lab")
    sub = parser.add_subparsers(dest="command", required=True)
    _alpha_parser(sub)
    sub.add_parser("doctor", help="check Alpha engine dependencies")
    sub.add_parser("dry-run", help="offline Alpha engine smoke check")
    sub.add_parser("bootstrap", help="create runtime directories")
    sub.add_parser("version", help="show Alpha engine version")
    args = parser.parse_args(argv)

    if args.command == "alpha":
        return _run_alpha(args)
    if args.command == "doctor":
        return _run_doctor()
    if args.command == "dry-run":
        return _run_dry_run()
    if args.command == "bootstrap":
        config.LAB_ROOT.mkdir(parents=True, exist_ok=True)
        for name in ("alpha", "discovery", "factors/generated"):
            (config.LAB_ROOT / name).mkdir(parents=True, exist_ok=True)
        _print({"ok": True, "lab_root": str(config.LAB_ROOT)})
        return 0
    if args.command == "version":
        from factor_lab import __version__

        _print({"version": __version__, "engine": "alpha"})
        return 0
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
