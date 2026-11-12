"""Campaign-level family ledger and FDR (P1).

The family ledger is a *derived* append-only view over the research memory:

- current attempts are mapped to a campaign/family via campaign rounds and the
  pre-registered family policy;
- team ablation/attribution evaluations are added explicitly (they were
  label-touching evaluations, but historically not recorded in attempts);
- legacy ``trials.jsonl`` is **not** part of the primary denominator (it is
  reported as a sensitivity count only).

FDR uses a pre-registered effective-N estimate.  Raw N is conservative under
positive dependence; using N_eff restores power without moving the family
boundary after seeing results.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable

from factor_lab.alpha.memory import AlphaMemory, canonical_expression
from factor_lab.alpha.trials import p_value_from_t
from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic


FAMILY_POLICY_PATH = config.ALPHA_POLICY_DIR / "families.json"
FAMILIES_DIR = config.LAB_ROOT / "alpha" / "families"
LEDGER_NAME = "ledger.jsonl"
TRIAL_SCHEMA = "factor_lab.alpha.family_trial.v1"
FDR_SCHEMA = "factor_lab.alpha.family_fdr.v1"
LEDGER_SCHEMA = "factor_lab.alpha.family_ledger.v1"


# --------------------------------------------------------------------------
# policy / paths
# --------------------------------------------------------------------------
def load_family_policy(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else FAMILY_POLICY_PATH
    raw = read_json(target)
    if not isinstance(raw, dict) or raw.get("schema") != "factor_lab.alpha.families.v1":
        raise ValueError(f"invalid family policy: {target}")
    families = raw.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("family policy requires families")
    return raw


def family_policy_by_campaign(policy: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for family in policy["families"]:
        for campaign_id in family.get("campaign_ids") or []:
            mapping[str(campaign_id)] = str(family["family_id"])
    return mapping


def family_dir(family_id: str) -> Path:
    path = FAMILIES_DIR / str(family_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path() -> Path:
    path = FAMILIES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path / LEDGER_NAME


# --------------------------------------------------------------------------
# campaign/search mapping
# --------------------------------------------------------------------------
def _campaign_search_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    roots = config.LAB_ROOT / "alpha" / "campaigns"
    if not roots.exists():
        return mapping
    for campaign_dir in sorted(roots.iterdir()):
        if not campaign_dir.is_dir():
            continue
        campaign_id = campaign_dir.name
        campaign = read_json(campaign_dir / "campaign.json")
        if not isinstance(campaign, dict):
            continue
        rounds = campaign_dir / "rounds"
        if not rounds.exists():
            continue
        for round_path in sorted(rounds.glob("round-*.json")):
            payload = read_json(round_path)
            if not isinstance(payload, dict):
                continue
            result = payload.get("research_result") or {}
            search_id = result.get("search_id")
            if not search_id:
                search = result.get("search") or {}
                if isinstance(search, dict):
                    search_id = search.get("search_id")
            if search_id:
                mapping.setdefault(str(search_id), campaign_id)
    return mapping


def _cycle_campaign(search_id: str) -> str | None:
    match = re.match(r"^cycle:(CAMP-[a-f0-9]+)$", str(search_id or "").strip())
    return match.group(1) if match else None


def _classify_attempt(
    row: dict[str, Any],
    *,
    family_by_campaign: dict[str, str],
    search_map: dict[str, str],
    legacy_family: str,
) -> tuple[str | None, str, str]:
    search_id = str(row.get("search_id") or "")
    campaign = _cycle_campaign(search_id)
    trial_type = "cycle" if campaign else "search"
    if campaign is None:
        campaign = search_map.get(search_id)
    family = str(row.get("family_id") or family_by_campaign.get(str(campaign or ""), legacy_family))
    return campaign, family, trial_type


# --------------------------------------------------------------------------
# trial records
# --------------------------------------------------------------------------
def _p_net(metrics: dict[str, Any]) -> float | None:
    try:
        net_t = float(metrics.get("net_t"))
        n_dates = int(metrics.get("n_dates") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(net_t) or n_dates < 3:
        return None
    return float(p_value_from_t(net_t, n_dates))


def _p_pos(metrics: dict[str, Any]) -> float | None:
    """One-sided positive-alpha p (primary discovery statistic).

    Negative-direction trials are not competing discoveries for a positive
    long-short candidate; assigning them p=1 prevents a very negative t-stat
    from inflating the BH rank of the positive candidate.
    """
    two_sided = _p_net(metrics)
    if two_sided is None:
        return None
    try:
        net_t = float(metrics.get("net_t"))
    except (TypeError, ValueError):
        return None
    return float(two_sided / 2.0) if net_t > 0 else 1.0


def _p_ic(metrics: dict[str, Any]) -> float | None:
    try:
        ic_t = float(metrics.get("ic_t"))
        n_dates = int(metrics.get("n_dates") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ic_t) or n_dates < 3:
        return None
    return float(p_value_from_t(ic_t, n_dates))


def _trial_record(
    *,
    expression: str,
    metrics: dict[str, Any],
    campaign_id: str | None,
    family_id: str,
    source: str,
    trial_type: str,
    stage: str = "dev",
    trial_id: str | None = None,
    created_at: str | None = None,
    recosted: bool = False,
    cost_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = canonical_expression(expression)
    return {
        "schema": TRIAL_SCHEMA,
        "trial_id": trial_id or f"TRIAL-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}",
        "campaign_id": campaign_id,
        "family_id": family_id,
        "stage": stage,
        "trial_type": trial_type,
        "expression": expression,
        "canonical_expression": canonical,
        "source": source,
        "metrics": metrics,
        "p_net": _p_net(metrics),
        "p_pos": _p_pos(metrics),
        "p_ic": _p_ic(metrics),
        "n_dates": (metrics or {}).get("n_dates"),
        "net_t": (metrics or {}).get("net_t"),
        "cost_model": cost_model or ((metrics or {}).get("metrics_spec") or {}).get("cost"),
        "recosted": bool(recosted),
        "created_at": created_at or config.now_iso(),
    }


def _team_trials(policy: dict[str, Any], family_by_campaign: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    campaign_root = config.LAB_ROOT / "alpha" / "campaigns"
    team_files = {"ablation.json", "lowprice_attribution.json", "scale_check.json"}
    for campaign_id, family_id in family_by_campaign.items():
        team_dir = campaign_root / campaign_id / "team"
        if not team_dir.exists():
            continue
        for path in sorted(team_dir.glob("*.json")):
            if path.name not in team_files:
                continue
            payload = read_json(path)
            if not isinstance(payload, dict):
                continue
            expressions = payload.get("expressions")
            if isinstance(expressions, dict):
                items = list(expressions.items())
            else:
                # scale_check.json stores expression/metrics at top level
                items = [
                    (name, item)
                    for name, item in payload.items()
                    if isinstance(item, dict) and item.get("expression") and isinstance(item.get("metrics"), dict)
                ]
            for name, item in items:
                if not isinstance(item, dict) or not item.get("expression"):
                    continue
                metrics = item.get("metrics") or {}
                if not isinstance(metrics, dict):
                    continue
                rows.append(
                    _trial_record(
                        expression=str(item["expression"]),
                        metrics=metrics,
                        campaign_id=campaign_id,
                        family_id=family_id,
                        source=f"team:{path.name}:{name}",
                        trial_type="ablation",
                    )
                )
    return rows


# --------------------------------------------------------------------------
# ledger build (P1 + P1.5)
# --------------------------------------------------------------------------
def _default_window() -> tuple[str, str]:
    from factor_lab.alpha.data import load_holdout

    holdout = load_holdout()
    if holdout is not None:
        return str(holdout.dev_start), str(holdout.dev_end)
    return "2022-10-01", "2025-10-31"


def _default_codes() -> list[str]:
    path = config.LAB_ROOT / "hypotheses" / "pool200.txt"
    if not path.exists():
        raise ValueError(f"missing code pool: {path}")
    raw = path.read_text(encoding="utf-8")
    return sorted({part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()})


def _recost_attempts(
    attempts: list[dict[str, Any]],
    *,
    family_by_campaign: dict[str, str],
    search_map: dict[str, str],
    legacy_family: str,
    start: str | None = None,
    end: str | None = None,
    codes: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-evaluate attempts whose cost semantics are not the current per-side 20bps.

    Campaign 548 (fam-max-lottery) predates the current cost model and has no
    ``metrics_spec``; without re-costing it, mixing 80bp/20bps semantics would
    invalidate the family FDR denominator.
    """
    from factor_lab.alpha.data import load_alpha_dataset
    from factor_lab.alpha.evaluation import evaluate_expression
    from factor_lab.alpha.universe import build_pit_universe, load_universe_policy

    start, end = (start or _default_window()[0], end or _default_window()[1])
    code_list = list(codes or _default_codes())
    dataset = load_alpha_dataset(code_list, start, end, warmup_days=300)
    policy = load_universe_policy()
    mask = build_pit_universe(dataset.context, policy)
    wanted = [ts for ts in mask.index if start <= ts.strftime("%Y-%m-%d") <= end]
    mask = mask.loc[wanted]
    recosted = 0
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        row = dict(attempt)
        campaign, family, _trial_type = _classify_attempt(
            row, family_by_campaign=family_by_campaign, search_map=search_map, legacy_family=legacy_family
        )
        cost = ((row.get("metrics") or {}).get("metrics_spec") or {}).get("cost") or {}
        needs_recost = bool(campaign) and (
            not cost
            or float(cost.get("bps") or 0) != 20.0
            or cost.get("bps_meaning") != "per_side"
        )
        if needs_recost:
            expression = str(row.get("expression") or "")
            try:
                metrics = evaluate_expression(
                    expression,
                    dataset.context,
                    horizon=int(row.get("horizon") or 5),
                    groups=5,
                    cost_bps=20.0,
                    start=start,
                    end=end,
                    universe=mask,
                )
                row["metrics"] = metrics
                row["recosted"] = True
                recosted += 1
            except Exception as exc:  # noqa: BLE001
                row["recosted"] = False
                row["recost_error"] = f"{type(exc).__name__}: {exc}"
                errors.append({"attempt_id": row.get("attempt_id"), "expression": expression, "error": row["recost_error"]})
        rows.append(row)
    return rows, {"recosted": recosted, "recost_errors": errors, "window": [start, end], "codes": len(code_list)}


def build_ledger(
    *,
    policy: dict[str, Any] | None = None,
    recost: bool = False,
    start: str | None = None,
    end: str | None = None,
    codes: Iterable[str] | None = None,
    write: bool = True,
    memory: AlphaMemory | None = None,
) -> dict[str, Any]:
    """Build the derived family ledger, family registries and summary."""
    policy = policy or load_family_policy()
    family_by_campaign = family_policy_by_campaign(policy)
    legacy_family = str(policy.get("legacy_family_id") or "legacy-unassigned")
    search_map = _campaign_search_map()
    memory = memory or AlphaMemory()
    attempts = memory.read_attempts()
    existing_recost = {
        str(row.get("trial_id")): row
        for row in load_ledger()
        if row.get("recosted") and row.get("trial_id")
    }
    recost_info: dict[str, Any] = {}
    if recost:
        attempts, recost_info = _recost_attempts(
            attempts,
            family_by_campaign=family_by_campaign,
            search_map=search_map,
            legacy_family=legacy_family,
            start=start,
            end=end,
            codes=codes,
        )

    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        recosted = existing_recost.get(str(attempt.get("attempt_id") or ""))
        if recosted is not None and not attempt.get("recosted"):
            attempt = dict(attempt)
            attempt["metrics"] = recosted.get("metrics") or attempt.get("metrics")
            attempt["recosted"] = True
        campaign, family, trial_type = _classify_attempt(
            attempt, family_by_campaign=family_by_campaign, search_map=search_map, legacy_family=legacy_family
        )
        rows.append(
            _trial_record(
                expression=str(attempt.get("expression") or ""),
                metrics=attempt.get("metrics") or {},
                campaign_id=campaign,
                family_id=family,
                source=str(attempt.get("source") or ""),
                trial_type=trial_type,
                trial_id=str(attempt.get("attempt_id") or ""),
                created_at=attempt.get("created_at"),
                recosted=bool(attempt.get("recosted")),
                cost_model=((attempt.get("metrics") or {}).get("metrics_spec") or {}).get("cost"),
            )
        )
    rows.extend(_team_trials(policy, family_by_campaign))

    by_family: dict[str, int] = {}
    by_campaign: dict[str, int] = {}
    legacy_attempts = 0
    recosted_count = 0
    for row in rows:
        by_family[row["family_id"]] = by_family.get(row["family_id"], 0) + 1
        if row.get("campaign_id"):
            by_campaign[str(row["campaign_id"])] = by_campaign.get(str(row["campaign_id"]), 0) + 1
        if row["family_id"] == legacy_family:
            legacy_attempts += 1
        if row.get("recosted"):
            recosted_count += 1

    trials_file = config.LAB_ROOT / "alpha" / "trials.jsonl"
    legacy_trials_lines = 0
    if trials_file.exists():
        legacy_trials_lines = sum(1 for line in trials_file.read_text(encoding="utf-8").splitlines() if line.strip())

    summary = {
        "schema": LEDGER_SCHEMA,
        "q": policy.get("q", 0.10),
        "n_trials": len(rows),
        "by_family": dict(sorted(by_family.items())),
        "by_campaign": dict(sorted(by_campaign.items())),
        "legacy_attempts": legacy_attempts,
        "legacy_trials_lines": legacy_trials_lines,
        "legacy_in_primary_denominator": False,
        "team_trials": sum(1 for row in rows if str(row.get("trial_type")) == "ablation"),
        "recosted": recosted_count,
        "recost": recost_info,
        "policy": str(FAMILY_POLICY_PATH),
        "ledger": str(ledger_path()),
        "created_at": config.now_iso(),
    }
    summary["ledger_hash"] = hashlib.sha256(
        json.dumps(
            [[row["trial_id"], row["canonical_expression"], row.get("family_id"), row.get("p_net")] for row in rows],
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    if write:
        with ledger_path().open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        for family in [item["family_id"] for item in policy["families"]] + [legacy_family]:
            family_rows = [row for row in rows if row["family_id"] == family]
            write_json_atomic(
                family_dir(family) / "family.json",
                {
                    "schema": "factor_lab.alpha.family.v1",
                    "family_id": family,
                    "research_question": next(
                        (item.get("research_question") for item in policy["families"] if item["family_id"] == family),
                        "legacy / unassigned attempts",
                    ),
                    "pre_registered": bool(
                        next((item.get("pre_registered") for item in policy["families"] if item["family_id"] == family), False)
                    ),
                    "n_trials": len(family_rows),
                    "campaign_ids": sorted({row["campaign_id"] for row in family_rows if row.get("campaign_id")}),
                    "updated_at": config.now_iso(),
                },
            )
        write_json_atomic(FAMILIES_DIR / "summary.json", summary)
    return summary


def load_ledger(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else ledger_path()
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# --------------------------------------------------------------------------
# effective N and FDR
# --------------------------------------------------------------------------
def _tokens(expression: str) -> frozenset[str]:
    return frozenset(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(expression)))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def estimate_n_eff(
    expressions: Iterable[str],
    *,
    threshold: float = 0.7,
    sensitivity: Iterable[float] = (0.5, 0.7, 0.9),
) -> dict[str, Any]:
    """Expression-cluster effective N (deterministic, pre-registered)."""
    unique = sorted({canonical_expression(str(expr)) for expr in expressions if str(expr).strip()})
    features = {expr: _tokens(expr) for expr in unique}
    parent = {expr: expr for expr in unique}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    def clusters(threshold_value: float) -> int:
        for expr in unique:
            parent[expr] = expr
        for i, left in enumerate(unique):
            for right in unique[i + 1 :]:
                if _jaccard(features[left], features[right]) >= threshold_value:
                    union(left, right)
        return len({find(expr) for expr in unique})

    values = {str(th): clusters(float(th)) for th in sensitivity}
    values[str(float(threshold))] = clusters(float(threshold))
    return {
        "method": "expression_jaccard",
        "threshold": float(threshold),
        "n_unique": len(unique),
        "n_eff": values[str(float(threshold))],
        "sensitivity": values,
    }


def _normal_threshold_t(alpha: float) -> float:
    if alpha <= 0:
        return float("inf")
    if alpha >= 1:
        return 0.0
    return float(NormalDist().inv_cdf(1.0 - alpha / 2.0))


def family_fdr(
    family_id: str,
    candidate_expression: str | None = None,
    *,
    q: float | None = None,
    policy: dict[str, Any] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    include_legacy: bool = False,
) -> dict[str, Any]:
    policy = policy or load_family_policy()
    ledger = ledger if ledger is not None else load_ledger()
    q = float(policy.get("q", 0.10) if q is None else q)
    n_eff_policy = policy.get("n_eff") or {}
    threshold = float(n_eff_policy.get("threshold", 0.7))
    sensitivity = n_eff_policy.get("sensitivity_thresholds") or [0.5, 0.7, 0.9]
    family_ids = {item["family_id"] for item in policy["families"]}
    if include_legacy:
        family_ids.add(str(policy.get("legacy_family_id") or "legacy-unassigned"))
    program_mode = family_id in {"__program__", "program-all"}
    rows = [
        row
        for row in ledger
        if (
            (program_mode and row.get("family_id") in family_ids)
            or (not program_mode and row.get("family_id") == family_id)
        )
        and row.get("stage") == "dev"
        and (row.get("p_pos") is not None or row.get("p_net") is not None)
        and math.isfinite(float(row.get("p_pos") if row.get("p_pos") is not None else row.get("p_net")))
    ]
    if not rows:
        raise ValueError(f"family has no FDR trials: {family_id}")
    # one row per canonical expression: keep the one with the largest sample
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        canonical = str(row.get("canonical_expression") or canonical_expression(row.get("expression") or ""))
        previous = unique.get(canonical)
        if previous is None or int(row.get("n_dates") or 0) >= int(previous.get("n_dates") or 0):
            unique[canonical] = row
    for row in unique.values():
        if row.get("p_pos") is None:
            row["p_pos"] = _p_pos(row.get("metrics") or {})
    ordered = sorted(unique.values(), key=lambda row: (float(row["p_pos"]), row["canonical_expression"]))
    if candidate_expression is not None:
        candidate_canonical = canonical_expression(candidate_expression)
        matches = [index for index, row in enumerate(ordered) if row["canonical_expression"] == candidate_canonical]
        if not matches:
            raise ValueError(f"candidate not in family ledger: {candidate_expression}")
        rank = matches[0] + 1
        candidate = ordered[rank - 1]
    else:
        # summary default: best *positive* candidate; a very negative t-stat is
        # not a discovery even though its two-sided p is small.
        positive = [
            row
            for row in ordered
            if (row.get("metrics") or {}).get("net_spread") is not None
            and float((row.get("metrics") or {}).get("net_spread") or 0.0) > 0
        ]
        candidate = min(positive or ordered, key=lambda row: (float(row["p_net"]), row["canonical_expression"]))
        rank = ordered.index(candidate) + 1
    n_eff_info = estimate_n_eff((row["canonical_expression"] for row in ordered), threshold=threshold, sensitivity=sensitivity)
    n_raw = len(ordered)
    n_eff = int(n_eff_info["n_eff"])
    p = float(candidate["p_pos"])
    bh_q = min(1.0, p * n_eff / rank) if n_eff else 1.0
    bh_threshold = q * rank / n_eff if n_eff else 0.0
    passed = bool(n_eff and p <= bh_threshold)
    raw_q = min(1.0, p * n_raw / rank)
    raw_threshold = q * rank / n_raw
    sensitivity_rows = []
    for label, effective in n_eff_info["sensitivity"].items():
        effective = max(1, int(effective))
        threshold_value = q * rank / effective
        sensitivity_rows.append(
            {
                "label": label,
                "n_eff": effective,
                "bh_threshold": threshold_value,
                "threshold_t": _normal_threshold_t(threshold_value),
                "passed": bool(p <= threshold_value),
            }
        )
    ledger_hash = hashlib.sha256(
        json.dumps(
            [[row["canonical_expression"], row.get("p_net")] for row in ordered],
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": FDR_SCHEMA,
        "family_id": family_id,
        "candidate_expression": candidate.get("expression"),
        "candidate_canonical": candidate.get("canonical_expression"),
        "candidate_campaign": candidate.get("campaign_id"),
        "candidate_metrics": {
            key: (candidate.get("metrics") or {}).get(key)
            for key in ("n_dates", "coverage", "ic_mean", "ic_t", "net_spread", "net_t", "turnover")
        },
        "n_trials": len(rows),
        "n_unique": n_raw,
        "n_eff": n_eff,
        "n_eff_method": n_eff_info["method"],
        "n_eff_threshold": n_eff_info["threshold"],
        "rank": rank,
        "p_net": p,
        "p_net_two_sided": candidate.get("p_net"),
        "bh_q": bh_q,
        "bh_threshold": bh_threshold,
        "bh_threshold_t": _normal_threshold_t(bh_threshold),
        "raw_bh_q": raw_q,
        "raw_bh_threshold": raw_threshold,
        "raw_bh_threshold_t": _normal_threshold_t(raw_threshold),
        "q": q,
        "passed": passed,
        "sensitivity": sensitivity_rows,
        "ledger_hash": ledger_hash,
        "created_at": config.now_iso(),
    }


def family_list(policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    policy = policy or load_family_policy()
    ledger = load_ledger()
    rows = []
    for family in policy["families"]:
        family_id = family["family_id"]
        family_rows = [row for row in ledger if row.get("family_id") == family_id]
        rows.append(
            {
                "family_id": family_id,
                "research_question": family.get("research_question"),
                "campaign_ids": family.get("campaign_ids") or [],
                "n_trials": len(family_rows),
                "n_dev_trials": sum(1 for row in family_rows if row.get("stage") == "dev"),
                "pre_registered": bool(family.get("pre_registered")),
            }
        )
    return rows


def ledger_summary() -> dict[str, Any]:
    path = FAMILIES_DIR / "summary.json"
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def reclassify_edge_cards(
    *,
    policy: dict[str, Any] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    memory: AlphaMemory | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Apply family FDR to existing signal_validated/research Edge Cards.

    Historical cards are never mutated: a new card with the same edge_id is
    appended with ``reclassified_from`` and the FDR evidence.  This mirrors the
    append-only memory model and keeps the old evidence auditable.
    """
    from factor_lab.alpha.memory import EDGE_CARD_SCHEMA

    policy = policy or load_family_policy()
    ledger = ledger if ledger is not None else load_ledger()
    memory = memory or AlphaMemory()
    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for card in memory.latest_edge_cards():
        status = str(card.get("status") or "")
        if status not in {"signal_validated", "research"}:
            continue
        expression = str(card.get("expression") or "")
        canonical = str(card.get("canonical_expression") or canonical_expression(expression))
        matches = [row for row in ledger if row.get("canonical_expression") == canonical and row.get("p_net") is not None]
        trial = max(matches, key=lambda row: (int(row.get("n_dates") or 0), str(row.get("created_at") or "")), default=None)
        if trial is None:
            skipped.append({"edge_id": card.get("edge_id"), "reason": "no family trial"})
            continue
        family_id = str(trial.get("family_id") or policy.get("legacy_family_id") or "legacy-unassigned")
        fdr = program_fdr(expression, q=float(policy.get("q", 0.10)), ledger=ledger, include_legacy=True)
        if fdr["passed"]:
            continue
        new_card = dict(card)
        new_card["status"] = "killed"
        new_card["reclassified_from"] = status
        new_card["reclassified_at"] = config.now_iso()
        new_card["reclass_reason"] = (
            f"family FDR failed: q={fdr['q']}, n_eff={fdr['n_eff']}, "
            f"p_net={fdr['p_net']:.6g}, bh_q={fdr['bh_q']:.6g} (threshold {fdr['bh_threshold']:.6g})"
        )
        new_card["family_fdr"] = {
            key: fdr.get(key)
            for key in ("family_id", "denominator", "n_unique", "n_eff", "rank", "p_net", "bh_q", "bh_threshold", "bh_threshold_t", "passed", "sensitivity", "ledger_hash")
        }
        new_card["schema"] = EDGE_CARD_SCHEMA
        if write:
            memory.record_edge_card(new_card)
        changed.append(
            {
                "edge_id": card.get("edge_id"),
                "expression": expression,
                "family_id": family_id,
                "from": status,
                "to": "killed",
                "p_net": fdr["p_net"],
                "bh_q": fdr["bh_q"],
            }
        )
    if write:
        memory.finalize()
    return {"schema": "factor_lab.alpha.card_reclassification.v1", "changed": changed, "skipped": skipped, "n_changed": len(changed)}


def program_fdr(
    candidate_expression: str | None = None,
    *,
    q: float | None = None,
    policy: dict[str, Any] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    include_legacy: bool = False,
) -> dict[str, Any]:
    """Program-wide FDR for freeze/reclassification.

    Freeze uses the joint program denominator (all pre-registered families on
    the same standardized pool/window) so a small mechanism family cannot be
    used to split its way below the multiple-testing threshold.  Legacy 40-code
    attempts are excluded from freeze by default and can be included for
    historical card reclassification sensitivity.
    """
    result = family_fdr(
        "__program__",
        candidate_expression,
        q=q,
        policy=policy,
        ledger=ledger,
        include_legacy=include_legacy,
    )
    result["family_id"] = "program-all"
    result["denominator"] = "program_all_families" + ("+legacy" if include_legacy else "")
    return result
