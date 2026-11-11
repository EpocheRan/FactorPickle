"""Command line entry point: ``python -m factor_lab``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factor_lab.core import config
from factor_lab.evaluation.audit import audit_experiment
from factor_lab.evaluation.brief import build_brief, write_decision
from factor_lab.core.control import ensure_control, load_control, set_state
from factor_lab.reporting.dashboard import render_dashboard
from factor_lab.core.ledger import Ledger
from factor_lab.research.maintenance import clean, inventory
from factor_lab.core.policy import init_policy, load_policy
from factor_lab.runtime.run import run_experiment
from factor_lab.core.schemas import Hypothesis, experiment_dir
from factor_lab.core.store import read_json, write_json_atomic

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

    sub.add_parser("init", help="create gate policy and control file")

    bootstrap_parser = sub.add_parser("bootstrap", help="portable workspace bootstrap")
    bootstrap_parser.add_argument("--research", action="store_true")

    doctor_parser = sub.add_parser("doctor", help="environment preflight")
    doctor_parser.add_argument("--probe-data", action="store_true")

    budget_parser = sub.add_parser("budget", help="LLM/disk budget status")
    budget_parser.add_argument("action", choices=["status"], nargs="?", default="status")

    agents_parser = sub.add_parser("agents", help="agent registry / safe reclaim")
    agents_parser.add_argument("action", choices=["status", "register", "reclaim", "roles"], nargs="?", default="status")
    agents_parser.add_argument("--id", default="")
    agents_parser.add_argument("--role", default="")
    agents_parser.add_argument("--session-dir", default="")
    agents_parser.add_argument("--apply", action="store_true")

    library_parser = sub.add_parser("library", help="versioned Lab factor library")
    library_parser.add_argument("action", choices=["list", "verify"], nargs="?", default="list")
    library_parser.add_argument("--factor", default="")

    build_parser = sub.add_parser("factor-build", help="write a generated factor into the Lab library")
    build_parser.add_argument("--family", required=True)
    build_parser.add_argument("--factor-id", required=True)
    build_parser.add_argument("--code", required=True)
    build_parser.add_argument("--spec", required=True)
    build_parser.add_argument("--tests", default=None)
    build_parser.add_argument("--version", default="v1")
    build_parser.add_argument("--run-tests", action="store_true")

    ml_parser = sub.add_parser("ml", help="non-DL ML engine")
    ml_parser.add_argument("action", choices=["run", "data"], nargs="?", default="run")
    ml_parser.add_argument("--dataset", required=False)
    ml_parser.add_argument("--spec", required=False)
    ml_parser.add_argument("--out", default="output/factor_lab/ml")
    ml_parser.add_argument("--codes", default="")
    ml_parser.add_argument("--start", default="2022-01-04")
    ml_parser.add_argument("--end", default="2026-11-01")

    campaign_parser = sub.add_parser("campaign", help="multi-round campaign")
    campaign_parser.add_argument("action", choices=["start", "status", "run"], nargs="?", default="start")
    campaign_parser.add_argument("--objective", default="研究因子")
    campaign_parser.add_argument("--id", default="")
    campaign_parser.add_argument("--dataset", default="")
    campaign_parser.add_argument("--plan", default="")

    runner_parser = sub.add_parser("runner", help="Runner preflight + experiment with data_interface manifest")
    runner_parser.add_argument("--hypothesis", required=True)
    runner_parser.add_argument("--profile", default="quick")
    runner_parser.add_argument("--robustness", action="store_true")
    runner_parser.add_argument("--no-audit", action="store_true")
    runner_parser.add_argument("--data-bundle", action="store_true", help="(compatibility) use Runner-owned panel")
    runner_parser.add_argument("--legacy-panel", action="store_true", help="use legacy research panel path")
    runner_parser.add_argument("--codes", default="", help="comma-separated codes for Runner data bundle")

    discovery_parser = sub.add_parser("discovery", help="DSH Web Gateway query/ingest")
    discovery_parser.add_argument("action", choices=["queries", "ingest"], nargs="?", default="queries")
    discovery_parser.add_argument("--question", default="研究因子")
    discovery_parser.add_argument("--constraints", default="")
    discovery_parser.add_argument("--result", default="")
    discovery_parser.add_argument("--brief", default=str(config.LAB_ROOT / "discovery" / "discovery_brief.json"))

    parity_parser = sub.add_parser("parity", help="legacy vs Runner panel parity")
    parity_parser.add_argument("--factor-id", default="momentum.ret_21d")
    parity_parser.add_argument("--codes", required=True)
    parity_parser.add_argument("--start", default="2022-01-04")
    parity_parser.add_argument("--end", default="2026-11-01")
    parity_parser.add_argument("--out", default="output/factor_lab/parity_panel.json")

    control_parser = sub.add_parser("control", help="show / pause / resume the lab")
    control_parser.add_argument("action", choices=["show", "pause", "resume", "stop"])
    control_parser.add_argument("--reason", default="")

    run_parser = sub.add_parser("run", help="run one hypothesis")
    run_parser.add_argument("--hypothesis", required=True, help="path to hypothesis JSON")
    run_parser.add_argument("--profile", default="quick")
    run_parser.add_argument("--robustness", action="store_true")
    run_parser.add_argument("--no-audit", action="store_true")

    cycle_parser = sub.add_parser("cycle", help="run one research cycle")
    cycle_parser.add_argument("--limit", type=int, default=4)
    cycle_parser.add_argument("--profile", default="quick")
    cycle_parser.add_argument("--heartbeat-interval", type=int, default=2)
    cycle_parser.add_argument("--no-orthogonality", action="store_true")
    cycle_parser.add_argument("--family", default=None)
    cycle_parser.add_argument("--plan", required=True, help="PathFinder hypotheses JSON")

    directive_parser = sub.add_parser("directive", help="show or initialize research direction")
    directive_parser.add_argument("action", choices=["show", "init"], nargs="?", default="show")

    supervisor_parser = sub.add_parser("supervisor", help="show supervisor state / reclaim agents")
    supervisor_parser.add_argument("action", choices=["status", "reclaim", "reclaim-all"], nargs="?", default="status")
    supervisor_parser.add_argument("--id", default="")
    supervisor_parser.add_argument("--apply", action="store_true")

    audit_parser = sub.add_parser("audit", help="audit one experiment")
    audit_parser.add_argument("--experiment", required=True)

    brief_parser = sub.add_parser("brief", help="build decision brief / decision for one experiment")
    brief_parser.add_argument("--experiment", required=True)

    ortho_parser = sub.add_parser("orthogonality", help="compute exposure correlation for one experiment")
    ortho_parser.add_argument("--experiment", required=True)
    context_parser = sub.add_parser("research-context", help="build/show the structured research context")
    context_parser.add_argument("action", choices=["build", "show"], nargs="?", default="build")
    context_parser.add_argument("--campaign", default="")

    evaluate_parser = sub.add_parser("evaluate", help="derive or finalize one experiment evaluation")
    evaluate_parser.add_argument("--experiment", required=True)
    evaluate_parser.add_argument("--finalize", action="store_true")

    alpha_parser = sub.add_parser("alpha", help="Alpha DSL / benchmark / search / red team")
    alpha_parser.add_argument("action", choices=["validate", "eval", "benchmark", "search", "confirm", "holdout", "trials", "memory", "library", "campaign", "ml", "discovery", "roles", "deploy", "allocate", "cycle"], nargs="?", default="holdout")
    alpha_parser.add_argument("--expression", default="")
    alpha_parser.add_argument("--codes", default=str(config.HYPOTHESES_DIR / "pool200.txt"))
    alpha_parser.add_argument("--start", default="2022-10-01")
    alpha_parser.add_argument("--end", default="2026-11-01")
    alpha_parser.add_argument("--dev-end", default="")
    alpha_parser.add_argument("--holdout-start", default="")
    alpha_parser.add_argument("--holdout-end", default="")
    alpha_parser.add_argument("--warmup", type=int, default=300)
    alpha_parser.add_argument("--horizon", type=int, default=5)
    alpha_parser.add_argument("--cost-bps", type=float, default=20.0)
    alpha_parser.add_argument("--budget", type=int, default=120)
    alpha_parser.add_argument("--include-holdout", action="store_true")
    alpha_parser.add_argument("--save", action="store_true")
    alpha_parser.add_argument("--out", default="")
    alpha_parser.add_argument("--search", default="")
    alpha_parser.add_argument("--seed-file", default="")
    alpha_parser.add_argument("--seed", type=int, default=11)
    alpha_parser.add_argument("--objective", default="")
    alpha_parser.add_argument("--campaign-id", default="")
    alpha_parser.add_argument("--max-rounds", type=int, default=5)
    alpha_parser.add_argument("--campaign-budget", type=float, default=0.0)
    alpha_parser.add_argument("--features", default="close,volume,amount")
    alpha_parser.add_argument("--ml-splits", type=int, default=4)
    alpha_parser.add_argument("--ml-embargo", type=int, default=5)
    alpha_parser.add_argument("--ml-out", default="")
    alpha_parser.add_argument("--discovery-file", default="")
    alpha_parser.add_argument("--round-file", default="")
    alpha_parser.add_argument("--factor-artifact", default="")

    web_mode_parser = sub.add_parser("web-mode", help="show discovery mode and cache state")
    web_mode_parser.add_argument("--mode", choices=["exploit", "explore", "deep"], default="exploit")

    sub.add_parser("dry-run", help="check the end-to-end research scaffold without a new trial")

    sub.add_parser("dashboard", help="render the dashboard from artifacts")

    sub.add_parser("eval-adapter", help="show the active research evaluation adapter")

    ledger_parser = sub.add_parser("ledger", help="ledger utilities")
    ledger_parser.add_argument("action", choices=["verify", "summary"])
    ledger_parser.add_argument("--path", default=None)

    maintenance_parser = sub.add_parser("maintenance", help="inventory / clean output")
    maintenance_parser.add_argument("action", choices=["inventory", "clean"])
    maintenance_parser.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init":
        from factor_lab.core.directive import ensure_directive
        from factor_lab.research.retention import ensure_policy
        from factor_lab.evaluation.supervisor import write_heartbeat
        from factor_lab.core.budget import ensure_policy as ensure_budget

        policy = init_policy()
        ensure_budget()
        control = ensure_control()
        directive = ensure_directive()
        retention = ensure_policy()
        write_heartbeat(interval_sec=120)
        if not config.QUARANTINE_PATH.exists():
            write_json_atomic(
                config.QUARANTINE_PATH,
                {"schema_version": "factor_lab.quarantine.v1", "updated_at": config.now_iso(), "items": []},
            )
        _print({"policy": policy, "control": control, "directive": directive, "retention": retention})
        return 0

    if args.command == "bootstrap":
        from factor_lab.runtime.bootstrap import bootstrap

        _print(bootstrap(research=args.research))
        return 0

    if args.command == "doctor":
        from factor_lab.runtime.bootstrap import doctor

        _print(doctor(probe_data=args.probe_data))
        return 0

    if args.command == "budget":
        from factor_lab.core import budget

        _print(budget.status())
        return 0

    if args.command == "agents":
        from factor_lab.core import agents

        if args.action == "roles":
            from factor_lab.team import load_roles, validate_roles

            _print({"validation": validate_roles(), "roles": load_roles()})
        elif args.action == "register":
            if not args.id:
                raise SystemExit("agents register requires --id")
            _print(agents.register(args.id, args.role or "worker", session_dir=args.session_dir or None).to_dict())
        elif args.action == "reclaim":
            if not args.id:
                raise SystemExit("agents reclaim requires --id")
            _print(agents.reclaim(args.id, apply=args.apply))
        else:
            _print({"agents": [item.to_dict() for item in agents.list_agents()]})
        return 0

    if args.command == "library":
        from factor_lab.runtime.factor_library import LIBRARY

        if args.action == "verify":
            if not args.factor:
                raise SystemExit("library verify requires --factor")
            versions = LIBRARY.list_versions(args.factor)
            _print(
                {
                    "factor": args.factor,
                    "versions": [
                        {
                            "version": item.version,
                            "family": item.family,
                            "code_hash": item.code_hash,
                            "window": item.window,
                            "path": str(item.root),
                        }
                        for item in versions
                    ],
                }
            )
        else:
            rows = []
            for family_dir in sorted(LIBRARY.root.glob("*")):
                if not family_dir.is_dir():
                    continue
                for factor_dir in sorted(family_dir.glob("*")):
                    if not factor_dir.is_dir():
                        continue
                    for version_dir in sorted(factor_dir.glob("v*")):
                        rows.append({"factor_id": factor_dir.name, "family": family_dir.name, "version": version_dir.name})
            _print({"root": str(LIBRARY.root), "factors": rows})
        return 0

    if args.command == "factor-build":
        from factor_lab.runtime.factor_builder import build_from_files

        factor = build_from_files(
            family=args.family,
            factor_id=args.factor_id,
            code_path=args.code,
            spec_path=args.spec,
            tests_path=args.tests,
            version=args.version,
        )
        if args.run_tests:
            from factor_lab.runtime.factor_library import LIBRARY

            test_result = LIBRARY.run_tests(args.family, args.factor_id, args.version)
        else:
            test_result = {"status": "not-run"}
        _print(
            {
                "factor_id": factor.factor_id,
                "family": factor.family,
                "version": factor.version,
                "code_hash": factor.code_hash,
                "path": str(factor.root),
                "tests": test_result,
            }
        )
        return 0

    if args.command == "ml":
        from factor_lab.research.ml_engine import MLExperimentSpec, load_dataset, run_experiment

        import pandas as pd

        if args.action == "data":
            codes = [item.strip() for item in args.codes.split(",") if item.strip()]
            if not codes:
                raise SystemExit("ml data requires --codes")
            from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway

            frame, manifest = load_dataset(DataInterfaceGateway(), codes, args.start, args.end)
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out_dir / "ml_dataset.csv", index=False)
            write_json_atomic(out_dir / "ml_data_manifest.json", manifest)
            _print({"out": str(out_dir), "rows": len(frame), "columns": list(frame.columns)})
            return 0

        if not args.dataset or not args.spec:
            raise SystemExit("ml run requires --dataset and --spec")
        dataset = pd.read_csv(args.dataset)
        spec_payload = read_json(Path(args.spec))
        if not isinstance(spec_payload, dict):
            raise SystemExit(f"invalid ML spec: {args.spec}")
        if "spec" in spec_payload and isinstance(spec_payload["spec"], dict):
            spec_payload = spec_payload["spec"]
        spec = MLExperimentSpec(**spec_payload)
        result = run_experiment(dataset, spec)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_dir / "ml_result.json", result.to_dict(include_predictions=True))
        write_json_atomic(out_dir / "model_card.json", {
            "schema_version": "factor_lab.model_card.v1",
            "model": spec.model,
            "features": spec.features,
            "target": spec.target,
            "splits": spec.n_splits,
            "embargo": spec.embargo,
            "cost_bps": spec.cost_bps,
        })
        _print({"out": str(out_dir), "metrics": result.metrics, "candidate": result.candidate, "reject_reasons": result.reject_reasons})
        return 0

    if args.command == "campaign":
        from factor_lab.research.campaign import create_campaign, load_campaign, run_ml_plan_campaign

        if args.action == "status":
            if not args.id:
                raise SystemExit("campaign status requires --id")
            _print(load_campaign(args.id) or {"error": "not found", "campaign_id": args.id})
        elif args.action == "run":
            if not args.dataset or not args.plan:
                raise SystemExit("campaign run requires --dataset and --plan")
            import pandas as pd

            plans = read_json(Path(args.plan))
            if isinstance(plans, dict):
                plans = plans.get("plans") or plans.get("plan") or []
            if not isinstance(plans, list):
                raise SystemExit("campaign plan must be a JSON list or {\"plans\": [...]}")
            _print(
                run_ml_plan_campaign(
                    pd.read_csv(args.dataset),
                    plans,
                    objective=args.objective,
                    campaign_id=args.id or None,
                )
            )
        else:
            from factor_lab.runtime.bootstrap import doctor

            preflight = doctor()
            if not preflight.get("ok"):
                _print({"status": "blocked", "reason": "doctor", "doctor": preflight})
                return 1
            _print(create_campaign(args.objective))
        return 0

    if args.command == "runner":
        path = Path(args.hypothesis)
        raw = read_json(path)
        if not isinstance(raw, dict):
            raise SystemExit(f"hypothesis not found or invalid: {path}")
        from factor_lab.runtime.runner import FactorRunner

        hypothesis = Hypothesis.from_dict(raw)
        runner = FactorRunner()
        codes = [item.strip() for item in args.codes.split(",") if item.strip()]
        use_data_bundle = not args.legacy_panel
        if use_data_bundle and not codes:
            raise SystemExit("runner data path requires --codes (or use --legacy-panel)")
        _print(
            runner.run(
                hypothesis,
                profile=args.profile,
                robustness=args.robustness,
                auto_audit=not args.no_audit,
                use_data_bundle=use_data_bundle,
                codes=codes or None,
            )
        )
        return 0

    if args.command == "discovery":
        from factor_lab.research.discovery import DiscoveryBrief
        from factor_lab.research.web_gateway import parse_gateway_output, read_brief, seed_queries
        if args.action == "queries":
            brief = DiscoveryBrief(
                research_question=args.question,
                constraints=[item.strip() for item in args.constraints.split(",") if item.strip()],
            )
            _print({"brief": brief.to_dict(), "queries": seed_queries(brief)})
        else:
            if not args.result:
                raise SystemExit("discovery ingest requires --result")
            payload = read_json(Path(args.result))
            if not isinstance(payload, dict):
                raise SystemExit(f"invalid gateway result: {args.result}")
            brief = read_brief(Path(args.brief))
            _print(parse_gateway_output(payload.get("gateway") if isinstance(payload.get("gateway"), dict) else payload, brief))
        return 0

    if args.command == "parity":
        from factor_lab.research.parity import compare_price_panel, write_parity

        codes = [item.strip() for item in args.codes.split(",") if item.strip()]
        if not codes:
            raise SystemExit("parity requires --codes")
        result = compare_price_panel(
            factor_id=args.factor_id,
            codes=codes,
            start=args.start,
            end=args.end,
        )
        write_parity(result, args.out)
        _print(result)
        return 0

    if args.command == "control":
        if args.action == "show":
            _print(load_control())
        else:
            state = {"pause": "paused", "resume": "running", "stop": "stopped"}[args.action]
            _print(set_state(state, reason=args.reason, actor="human"))
        return 0

    if args.command == "cycle":
        from factor_lab.research.cycle import run_cycle

        _print(
            run_cycle(
                limit=args.limit,
                profile=args.profile,
                heartbeat_interval=args.heartbeat_interval,
                orthogonality=not args.no_orthogonality,
                family_id=args.family,
                plan=args.plan,
            )
        )
        return 0

    if args.command == "directive":
        from factor_lab.core.directive import ensure_directive, write_directive, default_directive

        if args.action == "init":
            _print(write_directive(default_directive()))
        else:
            _print(ensure_directive())
        return 0

    if args.command == "supervisor":
        from factor_lab.evaluation.supervisor import read_supervisor
        from factor_lab.core import agents
        from factor_lab.core import budget

        if args.action == "reclaim":
            if not args.id:
                raise SystemExit("supervisor reclaim requires --id")
            _print(agents.reclaim(args.id, apply=args.apply))
            return 0
        if args.action == "reclaim-all":
            _print(agents.reclaim_all(apply=args.apply))
            return 0
        _print(
            {
                "supervisor": read_supervisor(),
                "control": load_control(),
                "heartbeat": read_json(config.HEARTBEAT_PATH),
                "cycle": read_json(config.CYCLE_PATH),
                "agents": [item.to_dict() for item in agents.list_agents()],
                "budget": budget.status(),
            }
        )
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

    if args.command == "orthogonality":
        from factor_lab.evaluation.orthogonality import compute_orthogonality

        _print(compute_orthogonality(_root(args.experiment)))
        return 0

    if args.command == "research-context":
        from factor_lab.research.research_context import build_context

        if args.action == "build":
            _print(build_context(args.campaign or None))
        else:
            target = (config.LAB_ROOT / "campaigns" / args.campaign / "research_context.json") if args.campaign else (config.LAB_ROOT / "research_context.json")
            _print(read_json(target) or {"error": "not built"})
        return 0

    if args.command == "evaluate":
        from factor_lab.evaluation.evaluation import derive, finalize

        target = Path(args.experiment)
        _print(finalize(target) if args.finalize else derive(target))
        return 0

    if args.command == "alpha":
        from factor_lab.alpha import parse_expression
        from factor_lab.alpha.benchmarks import run_benchmark
        from factor_lab.alpha.confirm import confirm_search
        from factor_lab.alpha.data import HoldoutWindow, default_holdout, load_alpha_dataset, load_holdout, save_holdout
        from factor_lab.alpha.discovery import ingest_discovery
        from factor_lab.alpha.allocator import CapitalAllocator
        from factor_lab.alpha.deployability import evaluate_long_only_expression
        from factor_lab.alpha.evaluation import evaluate_expression
        from factor_lab.alpha.campaign import CampaignController
        from factor_lab.alpha.factor_library import library_summary
        from factor_lab.alpha.memory import AlphaMemory
        from factor_lab.alpha.ml_branch import make_ml_plan, run_ml_experiment
        from factor_lab.alpha.pipeline import run_research_cycle
        from factor_lab.alpha.supervisor import Supervisor
        from factor_lab.alpha.roles import role_contracts
        from factor_lab.alpha.search import run_search
        from factor_lab.alpha.trials import summarize_trials
        from factor_lab.alpha.validation import validate

        def _codes(value: str) -> list[str]:
            path = Path(value)
            if path.exists() and path.is_file():
                raw = path.read_text(encoding="utf-8")
            else:
                raw = value
            return sorted({part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()})

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
        if args.action == "allocate":
            _print(CapitalAllocator(AlphaMemory()).allocate())
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
            _print({
                "schema": payload["schema"],
                "campaign_id": payload["campaign_id"],
                "factor_ref": payload["factor_ref"],
                "round": payload["round"],
                "evaluation": payload["evaluation"],
                "edge_card": payload["edge_card"]["edge_id"],
            })
            return 0
        if args.action == "discovery":
            if args.discovery_file:
                payload = read_json(Path(args.discovery_file))
            else:
                from factor_lab.research.discovery import read_map
                payload = read_map()
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
        codes = _codes(args.codes)
        if not codes:
            raise SystemExit("no codes supplied")
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
            plan = make_ml_plan(
                "CLI ML experiment",
                features,
                target_horizon=args.horizon,
                n_splits=args.ml_splits,
                embargo_days=args.ml_embargo,
            )
            result = run_ml_experiment(dataset, plan, out_dir=args.ml_out or str(config.LAB_ROOT / "alpha" / "ml"))
            _print({
                "plan_id": plan.plan_id,
                "features": plan.features,
                "metrics": result["metrics"],
                "cv": result["cv"],
                "path": args.ml_out or str(config.LAB_ROOT / "alpha" / "ml"),
            })
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
                # Benchmark the development window only; the locked holdout is
                # not part of the human-baseline comparison unless requested.
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
                window = bench_window
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
            _print({
                "expression": payload["expression"],
                "passed": payload["passed"],
                "metrics": {key: payload["metrics"].get(key) for key in ("ic_mean", "net_spread", "turnover", "capacity_multiple")},
                "red_team_passed": payload["red_team"].get("passed"),
                "path": payload["path"],
            })
            return 0
        if args.action == "search":
            save_holdout(window)
            dataset = load_alpha_dataset(codes, window.dev_start, window.dev_end, warmup_days=args.warmup)
            seed_expressions = []
            if args.seed_file:
                raw = read_json(Path(args.seed_file))
                if isinstance(raw, list):
                    seed_expressions = [str(item) for item in raw]
                elif isinstance(raw, dict):
                    seed_expressions = [str(item) for item in (raw.get("expressions") or [])]
            payload = run_search(dataset, budget=args.budget, horizon=args.horizon, cost_bps=args.cost_bps, holdout=window, seed=args.seed, seed_expressions=seed_expressions, out_path=args.out or None)
            _print({
                "search_id": payload["search_id"],
                "n_evaluated": payload["n_evaluated"],
                "n_ranked": payload["n_ranked"],
                "fdr": payload["fdr"],
                "top": [{"expression": item["expression"], "score": item["score"], "net": item["metrics"].get("net_spread"), "ic": item["metrics"].get("ic_mean")} for item in payload["top"][:5]],
                "confirmed": len([item for item in payload["confirmed"] if item.get("red_team", {}).get("passed")]),
                "path": args.out or str(config.LAB_ROOT / "alpha" / "search" / f"{payload['search_id']}.json"),
            })
            return 0
        raise SystemExit(f"unknown alpha action: {args.action}")

    if args.command == "web-mode":
        from factor_lab.research.web_gateway import resolve_mode

        _print(resolve_mode(args.mode))
        return 0

    if args.command == "dry-run":
        from factor_lab.evaluation.dry_run import run_dry_run

        _print(run_dry_run())
        return 0

    if args.command == "dashboard":
        _print({"dashboard": str(render_dashboard())})
        return 0

    if args.command == "eval-adapter":
        from factor_lab.runtime.research_adapter import RESEARCH

        _print(
            {
                "source": RESEARCH.source,
                "module": "research.report.runner.generate",
                "downstream": "research.report.metrics / sensitivity / rating",
                "note": "Factor Lab wraps the production research evaluator; factor_engine is not in this path.",
            }
        )
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
