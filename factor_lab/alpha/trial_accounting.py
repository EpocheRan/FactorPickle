"""Trial-accounting reconciliation and run-level multiple-testing audit.

The append-only event sink records search/dev/ML/holdout events; the family
ledger is the denominator used by FDR.  They are not the same population.  This
module makes the difference explicit and computes a run-level Bonferroni
threshold from the effective expression count, not from a campaign count.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import NormalDist
from typing import Any

from factor_lab.alpha.fdr import estimate_n_eff, load_ledger
from factor_lab.alpha.memory import canonical_expression
from factor_lab.core import config


TRIAL_ACCOUNTING_SCHEMA = "factor_lab.alpha.trial_accounting_audit.v1"
LEDGER_SNAPSHOT_SCHEMA = "factor_lab.alpha.ledger_snapshot.v1"


def ledger_snapshot_path() -> Path:
    return Path(config.LAB_ROOT) / "alpha" / "families" / "ledger_snapshot.json"


def ledger_snapshot_hash(payload: dict[str, Any] | None = None) -> str | None:
    if payload is None:
        path = ledger_snapshot_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return digest


def freeze_ledger_snapshot(*, ledger: list[dict[str, Any]] | None = None, refresh: bool = False) -> dict[str, Any]:
    """Freeze the family ledger once; later evaluations must use this snapshot.

    A refresh is explicit and changes the snapshot hash, so a report/evaluation
    can never silently move the FDR denominator.
    """
    path = ledger_snapshot_path()
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    rows = ledger if ledger is not None else load_ledger()
    payload = {
        "schema": LEDGER_SNAPSHOT_SCHEMA,
        "created_at": config.now_iso(),
        "n_trials": len(rows),
        "ledger_sha256": hashlib.sha256(
            "\n".join(json.dumps(row, sort_keys=True, default=str) for row in rows).encode("utf-8")
        ).hexdigest(),
        "trials": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _event_rows(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else Path(config.LAB_ROOT) / "alpha" / "trial_events.jsonl"
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _event_expression(row: dict[str, Any]) -> str:
    return canonical_expression(str(row.get("canonical_expression") or row.get("expression") or ""))


def trial_accounting_audit(
    *,
    trial_path: str | Path | None = None,
    ledger: list[dict[str, Any]] | None = None,
    q: float = 0.05,
) -> dict[str, Any]:
    events = _event_rows(trial_path)
    ledger = ledger if ledger is not None else load_ledger()
    ledger_expressions = {
        canonical_expression(str(row.get("canonical_expression") or row.get("expression") or ""))
        for row in ledger
        if str(row.get("expression") or row.get("canonical_expression") or "").strip()
    }
    ledger_expressions.discard("")
    event_expressions = {_event_expression(row) for row in events}
    event_expressions.discard("")
    unmapped_events = [row for row in events if _event_expression(row) not in ledger_expressions]
    unmapped_expressions = sorted({expr for expr in event_expressions if expr not in ledger_expressions})
    n_eff = estimate_n_eff(event_expressions) if event_expressions else {"n_unique": 0, "n_eff": 0, "sensitivity": {}}
    n_eff_value = max(1, int(n_eff.get("n_eff") or n_eff.get("n_unique") or 1))
    alpha_eff = float(q) / n_eff_value
    bonferroni_t = float(NormalDist().inv_cdf(1.0 - alpha_eff / 2.0)) if 0 < alpha_eff < 1 else float("inf")
    kinds = Counter(str(row.get("kind") or row.get("action") or row.get("event") or row.get("source") or "unknown") for row in events)
    holdout_rows = []
    try:
        from factor_lab.alpha.governance import holdout_uses

        holdout_rows = holdout_uses()
    except Exception:  # noqa: BLE001
        holdout_rows = []
    ml_trials = []
    for path in Path(config.LAB_ROOT).glob("alpha/campaigns/*/team/ml_admission.json"):
        try:
            ml_trials.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    horizons = Counter(str(row.get("horizon")) for row in events if row.get("horizon") is not None)
    ledger_horizons = Counter(str(row.get("horizon")) for row in ledger if row.get("horizon") is not None)
    return {
        "schema": TRIAL_ACCOUNTING_SCHEMA,
        "created_at": config.now_iso(),
        "n_trial_events": len(events),
        "n_unique_event_expressions": len(event_expressions),
        "n_family_ledger": len(ledger),
        "n_unique_ledger_expressions": len(ledger_expressions),
        "n_mapped_event_expressions": len(event_expressions & ledger_expressions),
        "n_unmapped_event_expressions": len(unmapped_expressions),
        "n_unmapped_events": len(unmapped_events),
        "unmapped_expressions": unmapped_expressions[:50],
        "event_kinds": dict(kinds.most_common()),
        "event_horizons": dict(horizons),
        "ledger_horizons": dict(ledger_horizons),
        "single_sink": str(Path(config.LAB_ROOT) / "alpha" / "trial_events.jsonl"),
        "ledger_snapshot_hash": ledger_snapshot_hash(),
        "n_holdout_uses": len(holdout_rows),
        "n_ml_trials_outside_primary": len(ml_trials),
        "population_policy": "primary=family ledger snapshot; holdout and ML admissions are reported but remain outside the primary denominator until migrated",
        "p_label": "p_net/p_net_two_sided are two-sided; p_pos/p_net_one_sided_positive are one-sided positive",
        "n_eff": n_eff,
        "run_level_bonferroni": {
            "q": float(q),
            "n_eff": n_eff_value,
            "per_test_alpha": alpha_eff,
            "two_sided_t_threshold": bonferroni_t,
        },
        "note": "Event sink and family ledger are different populations; unmapped events are reported, never silently relabeled.",
    }


def write_trial_accounting_audit(path: str | Path | None = None) -> dict[str, Any]:
    payload = trial_accounting_audit()
    target = Path(path) if path is not None else Path(config.LAB_ROOT).parents[0] / "deliverables" / "trial_accounting_audit.json"
    # The default report belongs under output/deliverables, not inside the
    # disposable factor-lab cache; callers can override.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["path"] = str(target)
    return payload
