"""Evolutionary / grammar-based Alpha search for Phase 2."""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from factor_lab.alpha.baselines import HUMAN_BASELINES
from factor_lab.alpha.compiler import AlphaContext, compile_expression
from factor_lab.alpha.data import AlphaDataset, HoldoutWindow
from factor_lab.alpha.evaluation import evaluate_factor
from factor_lab.alpha.factor_library import write_factor_artifact
from factor_lab.alpha.memory import AlphaMemory, canonical_expression, make_edge_card
from factor_lab.alpha.red_team import red_team_review
from factor_lab.alpha.trials import append_trial, benjamini_hochberg, p_value_from_t
from factor_lab.alpha.validation import validate
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


@dataclass
class Candidate:
    expression: str
    metrics: dict[str, Any]
    validation: dict[str, Any]
    source: str
    generation: int

    @property
    def score(self) -> float:
        net = float(self.metrics.get("net_spread", -1.0))
        ic = float(self.metrics.get("ic_mean", 0.0))
        turnover = float(self.metrics.get("turnover", 1.0))
        # Net spread dominates; IC is a small tie-breaker; turnover is penalized.
        capacity = self.metrics.get("capacity_multiple")
        cap_penalty = 0.0
        if capacity is not None and float(capacity) < 3.0:
            cap_penalty = 0.002 * (3.0 - float(capacity))
        nodes = int((self.validation.get("complexity") or {}).get("nodes", 0))
        return net + 0.05 * ic - 0.002 * turnover - cap_penalty - 0.00002 * nodes

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "source": self.source,
            "generation": self.generation,
            "score": round(float(self.score), 6),
            "metrics": self.metrics,
            "validation": self.validation,
        }


def _candidate(context: AlphaContext, expression: str, *, horizon: int, cost_bps: float, source: str, generation: int, start: str, end: str) -> Candidate | None:
    try:
        report = validate(expression, known_fields=context.field_names)
        if not report.ok:
            return None
        factor, _ = compile_expression(expression, context)
        factor = factor.loc[(factor.index >= start) & (factor.index <= end)]
        amount = context.fields.get("amount")
        metrics = evaluate_factor(
            factor,
            context.close.loc[factor.index],
            horizon=horizon,
            groups=5,
            cost_bps=cost_bps,
            amount=amount.loc[factor.index] if amount is not None else None,
            open_=context.fields.get("open").loc[factor.index] if context.fields.get("open") is not None else None,
        )
        if int(metrics.get("n_dates", 0)) < 20:
            return None
    except Exception:  # noqa: BLE001 - invalid candidates are skipped
        return None
    return Candidate(
        expression=str(expression),
        metrics=metrics,
        validation=report.to_dict(),
        source=source,
        generation=generation,
    )


def _fields(context: AlphaContext) -> list[str]:
    preferred = [
        "close", "open", "high", "low", "volume", "amount", "turnover",
        "returns", "ret_1d", "log_close", "float_mv", "total_mv",
        "fin_revenue", "fin_net_income", "fin_total_assets", "fin_total_equity",
        "fin_operating_cash_flow", "fin_gross_profit", "fin_roe",
        "dividend_yield", "earnings_yield", "pe_ttm", "pb_mrq",
    ]
    names = [name for name in preferred if name in context.field_names]
    names += [name for name in context.field_names if name not in names]
    return names[:28]


def _ts_ops() -> list[str]:
    return ["ts_mean", "ts_std", "ts_max", "ts_min", "ts_delta", "ts_rank", "ts_zscore", "ts_decay"]


def _random_atom(rng: random.Random, fields: list[str], depth: int = 0) -> str:
    windows = [3, 5, 10, 20, 60]
    if depth <= 0 or rng.random() < 0.35:
        return rng.choice(fields)
    choice = rng.random()
    if choice < 0.45:
        return f"{rng.choice(_ts_ops())}({_random_atom(rng, fields, depth - 1)}, {rng.choice(windows)})"
    if choice < 0.62:
        return f"rank({_random_atom(rng, fields, depth - 1)})"
    if choice < 0.78:
        return f"ts_corr({_random_atom(rng, fields, depth - 1)}, {_random_atom(rng, fields, depth - 1)}, {rng.choice(windows)})"
    if choice < 0.88:
        return f"ts_residual({_random_atom(rng, fields, depth - 1)}, {_random_atom(rng, fields, depth - 1)}, {rng.choice(windows)})"
    op = rng.choice(["+", "-", "*", "/"])
    return f"({_random_atom(rng, fields, depth - 1)} {op} {_random_atom(rng, fields, depth - 1)})"


def _mutate(rng: random.Random, expression: str, fields: list[str]) -> str:
    windows = [3, 5, 10, 20, 60]
    choice = rng.random()
    if choice < 0.22:
        return f"rank({expression})"
    if choice < 0.42:
        return f"{rng.choice(_ts_ops())}({expression}, {rng.choice(windows)})"
    if choice < 0.57:
        return f"neg({expression})"
    if choice < 0.72:
        other = _random_atom(rng, fields, 1)
        return f"({expression} - {other})"
    if choice < 0.86:
        other = _random_atom(rng, fields, 1)
        return f"ts_corr({expression}, {other}, {rng.choice(windows)})"
    return f"where(sign(ts_delta(close, 5)), {expression}, neg({expression}))"


def _dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for item in sorted(candidates, key=lambda candidate: candidate.score, reverse=True):
        key = str(item.expression)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def run_search(
    dataset: AlphaDataset,
    *,
    budget: int = 120,
    horizon: int = 5,
    cost_bps: float = 20.0,
    holdout: HoldoutWindow | None = None,
    seed: int = 11,
    seed_expressions: Iterable[str] | None = None,
    out_path: str | None = None,
    memory: AlphaMemory | None = None,
    factor_library_root: str | Path | None = None,
) -> dict[str, Any]:
    """Search only on the development segment; holdout is never touched."""
    if holdout is not None:
        holdout.assert_search_window(dataset.start, dataset.end)
    rng = random.Random(seed)
    context = dataset.context
    fields = _fields(context)
    candidates: list[Candidate] = []
    attempted = 0
    memory = memory or AlphaMemory()
    known = memory.known_expressions()
    seen: set[str] = {canonical_expression(item) for item in known}
    search_id = f"ALPHA-{uuid.uuid4().hex[:8]}"

    def evaluate_candidate(expression: str, *, source: str, generation: int) -> Candidate | None:
        nonlocal attempted
        attempted += 1
        candidate = _candidate(
            context,
            expression,
            horizon=horizon,
            cost_bps=cost_bps,
            source=source,
            generation=generation,
            start=dataset.start,
            end=dataset.end,
        )
        if candidate is None:
            memory.record_attempt(
                expression,
                status="invalid",
                reason="validation/evaluation failed or insufficient sample",
                source=source,
                generation=generation,
                horizon=horizon,
                cost_bps=cost_bps,
                search_id=search_id,
            )
            return None
        p_value = p_value_from_t(
            float(candidate.metrics.get("ic_t", 0.0)),
            int(candidate.metrics.get("n_dates", 0)),
        )
        memory.record_attempt(
            expression,
            status="evaluated",
            metrics=candidate.metrics,
            source=source,
            generation=generation,
            horizon=horizon,
            cost_bps=cost_bps,
            search_id=search_id,
            p_value=p_value,
        )
        return candidate

    # Optional LLM/PathFinder seeds are evaluated under the same trial accounting.
    for expression in list(seed_expressions or []):
        text = str(expression).strip()
        key = canonical_expression(text)
        if not text or key in seen:
            continue
        seen.add(key)
        candidate = evaluate_candidate(text, source="seed", generation=0)
        if candidate is not None:
            candidates.append(candidate)

    # Human baselines are seeds, not the search space.
    for baseline in HUMAN_BASELINES:
        baseline_key = canonical_expression(baseline.expression)
        if baseline_key in seen:
            continue
        seen.add(baseline_key)
        candidate = evaluate_candidate(baseline.expression, source=f"baseline:{baseline.name}", generation=0)
        if candidate is not None:
            candidates.append(candidate)

    # Random / grammar candidates.
    for index in range(max(0, int(budget))):
        expression = _random_atom(rng, fields, depth=rng.choice([1, 2, 2, 3]))
        expression_key = canonical_expression(expression)
        if expression_key in seen:
            continue
        seen.add(expression_key)
        candidate = evaluate_candidate(expression, source="grammar", generation=1)
        if candidate is not None:
            candidates.append(candidate)

    # Evolution from the best candidates.
    for generation in range(2, 5):
        parents = _dedupe(candidates)[: max(8, budget // 8)]
        for parent in parents:
            expression = _mutate(rng, parent.expression, fields)
            expression_key = canonical_expression(expression)
            if expression_key in seen:
                continue
            seen.add(expression_key)
            candidate = evaluate_candidate(expression, source=f"mutation:{parent.expression[:40]}", generation=generation)
            if candidate is not None:
                candidates.append(candidate)

    ranked = _dedupe(candidates)
    for candidate in ranked:
        p_value = p_value_from_t(float(candidate.metrics.get("ic_t", 0.0)), int(candidate.metrics.get("n_dates", 0)))
        append_trial(
            {
                "expression": candidate.expression,
                "stage": "screen",
                "source": candidate.source,
                "generation": candidate.generation,
                "metrics": candidate.metrics,
                "complexity": candidate.validation.get("complexity"),
                "p_value": p_value,
                "horizon": horizon,
                "cost_bps": cost_bps,
            }
        )

    # Confirm only the top development candidates with an adversarial review.
    confirmed = []
    for candidate in ranked[:4]:
        try:
            factor, _ = compile_expression(candidate.expression, context)
            factor = factor.loc[(factor.index >= dataset.start) & (factor.index <= dataset.end)]
            amount = context.fields.get("amount")
            review = red_team_review(
                factor,
                context.close.loc[factor.index],
                candidate.metrics,
                horizon=horizon,
                open_=context.fields.get("open").loc[factor.index] if context.fields.get("open") is not None else None,
                cost_bps=cost_bps,
                permutations=60,
            )
        except Exception as exc:  # noqa: BLE001
            review = {"passed": False, "findings": [{"check": "exception", "detail": str(exc)}], "checks": []}
        candidate_payload = candidate.to_dict()
        candidate_payload["red_team"] = review
        confirmed.append(candidate_payload)

    pvalues = [p_value_from_t(float(item.metrics.get("ic_t", 0.0)), int(item.metrics.get("n_dates", 0))) for item in ranked]
    bh = benjamini_hochberg(pvalues, q=0.1)
    passed_count = sum(1 for item in bh if item["passed"])

    if confirmed or ranked:
        best = dict(confirmed[0] if confirmed else ranked[0].to_dict())
        best_metrics = dict(best.get("metrics") or {})
        review = best.get("red_team") or {}
        card_status = (
            "signal_validated"
            if review.get("passed") and float(best_metrics.get("net_spread", -1.0)) > 0.0
            else "research"
        )
        factor_ref = {"factor_id": "", "version": "", "code_hash": ""}
        try:
            artifact = write_factor_artifact(
                factor_id=f"search_{search_id.lower()}",
                family="alpha_search",
                expression=str(best.get("expression") or ""),
                spec={
                    "source": "alpha_search",
                    "search_id": search_id,
                    "data_contract": {"fields": (best.get("validation") or {}).get("fields") or []},
                    "horizon": horizon,
                    "cost_bps": cost_bps,
                },
                source="alpha_search",
                library_root=factor_library_root,
            )
            factor_ref = {
                "factor_id": artifact.factor_id,
                "version": artifact.version,
                "code_hash": artifact.code_hash,
            }
        except Exception:  # noqa: BLE001 - evidence card remains usable without artifact
            pass
        card = make_edge_card(
            str(best.get("expression") or ""),
            best_metrics,
            status=card_status,
            mechanism=str(best.get("source") or "unspecified"),
            data_axes=(best.get("validation") or {}).get("fields") or [],
            horizon=horizon,
            cost_bps=cost_bps,
            source=str(best.get("source") or ""),
            search_id=search_id,
            validation=best.get("validation") or {},
            red_team=review,
            evidence=[f"search:{search_id}"],
            factor_ref=factor_ref,
            research_mode="explore",
        )
        memory.record_edge_card(card)

    memory.finalize()
    payload = {
        "schema": "factor_lab.alpha.search.v1",
        "created_at": config.now_iso(),
        "search_id": search_id,
        "dataset": dataset.data_manifest,
        "holdout": holdout.to_dict() if holdout else None,
        "budget": budget,
        "horizon": horizon,
        "cost_bps": cost_bps,
        "n_attempted": attempted,
        "n_evaluated": len(candidates),
        "n_ranked": len(ranked),
        "fdr": {"q": 0.1, "n_trials": len(pvalues), "n_attempted": attempted, "passed": passed_count},
        "top": [item.to_dict() for item in ranked[:10]],
        "confirmed": confirmed,
        "confirmed_passed": [item["expression"] for item in confirmed if item.get("red_team", {}).get("passed")],
        "trials_path": str(config.LAB_ROOT / "alpha" / "trials.jsonl"),
        "memory": memory.summary(),
    }
    if out_path is None:
        out_path = str(config.LAB_ROOT / "alpha" / "search" / f"{payload['search_id']}.json")
    write_json_atomic(Path(out_path), payload)
    return payload


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
