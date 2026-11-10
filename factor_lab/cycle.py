"""One research cycle: 4 factors, 2 worker processes, then audit."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import config
from .audit import audit_experiment
from .brief import build_brief, write_decision
from .control import ensure_control, is_running, load_control, set_state
from .dashboard import render_dashboard
from .directive import ensure_directive
from .ledger import Ledger
from .orthogonality import compute_batch_orthogonality
from .policy import init_policy
from .retention import enforce_r4, ensure_policy, storage_report
from .run import run_experiment
from .schemas import Hypothesis, OutcomeSpec, experiment_dir, trial_id
from .store import read_json, write_json_atomic
from .supervisor import Heartbeat, lease, write_cycle

WORKERS = 2


def _run_one(payload: dict[str, Any], profile: str) -> dict[str, Any]:
    try:
        hypothesis = Hypothesis.from_dict(payload)
        result = run_experiment(hypothesis, profile=profile, auto_audit=True)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload)


def _aggregate_progress(
    *,
    cycle_id: str,
    status: str,
    items: list[dict[str, Any]],
    started_at: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "factor_lab.batch_progress.v1",
        "cycle_id": cycle_id,
        "status": status,
        "started_at": started_at,
        "updated_at": config.now_iso(),
        "total": len(items),
        "completed": sum(1 for item in items if item.get("status") in {"success", "failed", "skipped"}),
        "running": sum(1 for item in items if item.get("status") == "running"),
        "items": items,
    }
    write_json_atomic(config.LAB_ROOT / "batch_progress.json", payload)
    return payload


def _quarantine(items: list[dict[str, Any]]) -> dict[str, Any]:
    current = read_json(config.QUARANTINE_PATH) or {"schema_version": "factor_lab.quarantine.v1", "items": []}
    existing = list(current.get("items") or [])
    known = {(str(item.get("trial_id")), str(item.get("factor_id"))) for item in existing}
    for item in items:
        key = (str(item.get("trial_id")), str(item.get("factor_id")))
        if key not in known:
            existing.append(item)
            known.add(key)
    current.update({"updated_at": config.now_iso(), "items": existing})
    write_json_atomic(config.QUARANTINE_PATH, current)
    return current


def run_cycle(
    *,
    limit: int = 4,
    profile: str = "quick",
    heartbeat_interval: int = 2,
    orthogonality: bool = True,
    family_id: str | None = None,
    plan: str | Path | None = None,
) -> dict[str, Any]:
    config.ensure_dirs()
    init_policy()
    ensure_control()
    ensure_directive()
    ensure_policy()
    if not config.QUARANTINE_PATH.exists():
        write_json_atomic(
            config.QUARANTINE_PATH,
            {"schema_version": "factor_lab.quarantine.v1", "updated_at": config.now_iso(), "items": []},
        )
    if not load_control().get("state") == "running":
        return {"status": "blocked", "reason": "control is not running"}

    r4 = enforce_r4()
    storage = storage_report()
    if r4.get("breaches"):
        write_cycle(cycle_id="blocked-r4", status="blocked", error=f"R4: {r4['breaches']}")
        return {"status": "blocked", "reason": "R4", "storage": storage}

    from .budget import check_cycle

    budget_check = check_cycle()
    if not budget_check.get("ok"):
        write_cycle(cycle_id="blocked-budget", status="blocked", error=f"budget: {budget_check['reasons']}")
        return {"status": "blocked", "reason": "budget", "budget": budget_check}

    if not plan:
        return {
            "status": "empty",
            "reason": "cycle requires a PathFinder plan; no hardcoded idea catalog is available",
        }
    raw = read_json(Path(plan))
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = raw.get("hypotheses") or raw.get("plan") or []
        if isinstance(rows, dict):
            rows = [rows]
    else:
        rows = []
    hypotheses = [Hypothesis.from_dict(row) for row in rows[: max(1, int(limit))]]
    if family_id:
        for hypothesis in hypotheses:
            hypothesis.family_id = str(family_id)
    family_id = str(family_id or (hypotheses[0].family_id if hypotheses else "") or f"F-{config.today_tag()}")
    if not hypotheses:
        return {"status": "empty", "reason": "plan contains no hypotheses"}

    cycle_id = f"C-{config.today_tag()}-{uuid.uuid4().hex[:6]}"
    started_at = config.now_iso()
    progress_dir = config.PROGRESS_DIR / cycle_id
    progress_dir.mkdir(parents=True, exist_ok=True)
    items = [
        {
            "index": index,
            "factor_id": hypothesis.factor_id,
            "trial_id": trial_id(hypothesis.hypothesis_id, hypothesis.outcomes[0]),
            "status": "queued",
            "exp_id": None,
            "error": None,
        }
        for index, hypothesis in enumerate(hypotheses)
    ]
    _aggregate_progress(cycle_id=cycle_id, status="running", items=items, started_at=started_at)

    heartbeat = Heartbeat(heartbeat_interval)
    heartbeat.start()
    write_cycle(
        cycle_id=cycle_id,
        status="running",
        started_at=started_at,
        scheduled_at=started_at,
        finished_at=None,
        worker_count=WORKERS,
        family_id=family_id,
        factor_ids=[item["factor_id"] for item in items],
        successes=0,
        failures=0,
        skipped=0,
    )

    successes: list[Path] = []
    quarantine_items: list[dict[str, Any]] = []
    try:
        with lease(cycle_id, ttl_sec=3600) as acquired:
            if not acquired:
                write_cycle(cycle_id=cycle_id, status="skipped", reason="lease held")
                return {"status": "skipped", "reason": "lease held", "cycle_id": cycle_id}

            payloads = []
            for hypothesis in hypotheses:
                path = config.HYPOTHESES_DIR / f"{hypothesis.hypothesis_id}.json"
                write_json_atomic(path, hypothesis.to_dict())
                payloads.append((hypothesis, path, hypothesis.to_dict()))

            pending = list(payloads)
            active: dict[concurrent.futures.Future, int] = {}
            with concurrent.futures.ProcessPoolExecutor(max_workers=WORKERS) as pool:
                while pending or active:
                    while pending and len(active) < WORKERS and is_running():
                        hypothesis, path, payload = pending.pop(0)
                        index = hypotheses.index(hypothesis)
                        items[index]["status"] = "running"
                        _write_progress(progress_dir / f"worker-{index}.json", items[index])
                        _aggregate_progress(cycle_id=cycle_id, status="running", items=items, started_at=started_at)
                        active[pool.submit(_run_one, payload, profile)] = index

                    if not active:
                        break
                    done, _ = concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        index = active.pop(future)
                        item = items[index]
                        try:
                            outcome = future.result()
                        except Exception as exc:  # noqa: BLE001
                            outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                        if outcome.get("ok"):
                            result = outcome["result"]
                            item.update({"status": "success", "exp_id": result.get("exp_id")})
                            successes.append(Path(result["experiment_dir"]))
                        else:
                            item.update({"status": "failed", "error": outcome.get("error")})
                            quarantine_items.append(
                                {
                                    "trial_id": item["trial_id"],
                                    "factor_id": item["factor_id"],
                                    "reason": outcome.get("error"),
                                    "retry_count": 0,
                                    "counted_in_n": True,
                                    "status": "quarantined",
                                    "cycle_id": cycle_id,
                                }
                            )
                        _write_progress(progress_dir / f"worker-{index}.json", item)
                        _aggregate_progress(cycle_id=cycle_id, status="running", items=items, started_at=started_at)

            skipped: list[dict[str, Any]] = []
            for hypothesis, _path, _payload in pending:
                index = next((i for i, item in enumerate(items) if item["factor_id"] == hypothesis.factor_id), None)
                if index is None:
                    continue
                items[index].update({"status": "skipped", "error": "control not running before start"})
                skipped.append(dict(items[index]))
                _write_progress(progress_dir / f"worker-{index}.json", items[index])
            _aggregate_progress(cycle_id=cycle_id, status="running", items=items, started_at=started_at)

            if quarantine_items:
                _quarantine(quarantine_items)

            orthogonality_results: list[dict[str, Any]] = []
            if orthogonality and successes:
                try:
                    orthogonality_results = compute_batch_orthogonality(successes)
                except Exception as exc:  # noqa: BLE001
                    quarantine_items.append(
                        {
                            "trial_id": None,
                            "factor_id": "__orthogonality__",
                            "reason": f"{type(exc).__name__}: {exc}",
                            "retry_count": 0,
                            "counted_in_n": False,
                            "status": "degraded",
                            "cycle_id": cycle_id,
                        }
                    )
            for root in successes:
                audit = audit_experiment(root)
                brief = build_brief(root)
                write_decision(root, brief, audit)

            final_status = "completed" if not quarantine_items and not skipped else "degraded"
            write_cycle(
                cycle_id=cycle_id,
                status=final_status,
                started_at=started_at,
                finished_at=config.now_iso(),
                worker_count=WORKERS,
                family_id=family_id,
                factor_ids=[item["factor_id"] for item in items],
                successes=len(successes),
                failures=len(quarantine_items),
                skipped=len(skipped),
            )
            _aggregate_progress(cycle_id=cycle_id, status=final_status, items=items, started_at=started_at)
            dashboard = render_dashboard()
            return {
                "status": final_status,
                "cycle_id": cycle_id,
                "family_id": family_id,
                "successes": len(successes),
                "failures": quarantine_items,
                "skipped": skipped,
                "orthogonality": len(orthogonality_results),
                "dashboard": str(dashboard),
                "storage": storage,
                "experiments": [root.name for root in successes],
            }
    except Exception as exc:  # noqa: BLE001
        write_cycle(cycle_id=cycle_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        _aggregate_progress(cycle_id=cycle_id, status="failed", items=items, started_at=started_at)
        raise
    finally:
        heartbeat.stop()
        Ledger().write_summary()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factor_lab.cycle")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--profile", default="quick")
    parser.add_argument("--heartbeat-interval", type=int, default=2)
    parser.add_argument("--no-orthogonality", action="store_true")
    parser.add_argument("--family", default=None)
    parser.add_argument("--plan", required=True, help="PathFinder hypotheses JSON")
    args = parser.parse_args(argv)
    result = run_cycle(
        limit=args.limit,
        profile=args.profile,
        heartbeat_interval=args.heartbeat_interval,
        orthogonality=not args.no_orthogonality,
        family_id=args.family,
        plan=args.plan,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
