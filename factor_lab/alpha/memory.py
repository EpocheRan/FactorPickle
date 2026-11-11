"""Closed-loop research memory for the Alpha engine.

The memory is append-only:
- ``attempts.jsonl``: every expression that entered an evaluation attempt.
- ``edge_cards.jsonl``: evidence cards produced by search/confirm.
- ``failures.jsonl``: invalid/killed/parked outcomes used to avoid dead ends.
- ``summary.json``: compact snapshot for CLI and proposal context.

The loop is read -> search -> record -> next search.  A memory that is only
written is a log, not memory; ``known_expressions`` and ``context_for_search``
are the read side used by the search engine.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Iterable

from factor_lab.alpha.expression import ExpressionError, parse_expression
from factor_lab.core import config
from factor_lab.core.store import append_jsonl, read_jsonl, write_json_atomic


MEMORY_SCHEMA = "factor_lab.alpha.memory.v1"
EDGE_CARD_SCHEMA = "factor_lab.alpha.edge_card.v1"
VALID_CARD_STATUSES = {
    "research",
    "signal_validated",
    "validated",
    "active",
    "parked",
    "not_deployable",
    "retired",
    "killed",
}
FAILURE_STATUSES = {"invalid", "failed", "rejected", "killed", "parked", "not_deployable"}


def canonical_expression(expression: str) -> str:
    """Canonical form used for cross-run deduplication."""
    try:
        return str(parse_expression(expression))
    except ExpressionError:
        return " ".join(str(expression).split())


def expression_hash(expression: str) -> str:
    return hashlib.sha256(canonical_expression(expression).encode("utf-8")).hexdigest()[:16]


def edge_id_for(expression: str, horizon: int) -> str:
    return f"EDGE-{expression_hash(expression + '|' + str(int(horizon)))}"


def make_edge_card(
    expression: str,
    metrics: dict[str, Any],
    *,
    status: str = "research",
    mechanism: str = "unspecified",
    data_axes: Iterable[str] | None = None,
    horizon: int = 5,
    cost_bps: float = 20.0,
    source: str = "",
    search_id: str = "",
    validation: dict[str, Any] | None = None,
    red_team: dict[str, Any] | None = None,
    evidence: Iterable[str] | None = None,
    kill_criteria: Iterable[str] | None = None,
    factor_ref: dict[str, Any] | None = None,
    research_mode: str = "research",
) -> dict[str, Any]:
    if status not in VALID_CARD_STATUSES:
        raise ValueError(f"invalid edge-card status: {status}")
    if research_mode not in {"research", "explore", "validate", "deploy"}:
        raise ValueError(f"invalid research_mode: {research_mode}")
    canonical = canonical_expression(expression)
    signal = {
        "long_short_ic": metrics.get("ic_mean"),
        "long_short_spread": metrics.get("gross_spread"),
        "long_short_t": metrics.get("gross_t"),
        "long_short_net_spread": metrics.get("net_spread"),
        "long_short_net_t": metrics.get("net_t"),
        "turnover": metrics.get("turnover"),
        "capacity_multiple": metrics.get("capacity_multiple"),
        "n_dates": metrics.get("n_dates"),
    }
    return {
        "schema": EDGE_CARD_SCHEMA,
        "edge_id": edge_id_for(canonical, horizon),
        "factor_ref": dict(factor_ref or {"factor_id": "", "version": "", "code_hash": ""}),
        "discovery_refs": [],
        "expression": str(expression),
        "canonical_expression": canonical,
        "mechanism": mechanism,
        "data_axes": sorted({str(item) for item in (data_axes or [])}),
        "source": source,
        "search_id": search_id,
        "horizon_days": int(horizon),
        "signal_evidence": signal,
        "deployability": {
            "status": "not_measured",
            "long_only_excess_after_tax": None,
            "long_only_te": None,
            "alpha_per_turn": None,
            "cost_bps": float(cost_bps),
        },
        "validation": validation or {},
        "red_team": red_team or {},
        "status": status,
        "research_mode": research_mode,
        "evidence": list(evidence or []),
        "kill_criteria": list(
            kill_criteria
            or [
                "long_short_t < 2",
                "long_short_net_spread <= 0",
                "capacity_multiple < 2",
            ]
        ),
        "created_at": config.now_iso(),
    }


class AlphaMemory:
    """Append-only memory with read APIs used by search/confirm/CLI."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else config.LAB_ROOT / "alpha" / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.attempts_path = self.root / "attempts.jsonl"
        self.edge_cards_path = self.root / "edge_cards.jsonl"
        self.failures_path = self.root / "failures.jsonl"
        self.summary_path = self.root / "summary.json"

    # ------------------------------------------------------------------
    # read side
    def read_attempts(self) -> list[dict[str, Any]]:
        return read_jsonl(self.attempts_path)

    def read_edge_cards(self) -> list[dict[str, Any]]:
        return read_jsonl(self.edge_cards_path)

    def read_failures(self) -> list[dict[str, Any]]:
        return read_jsonl(self.failures_path)

    def known_expressions(self) -> set[str]:
        known: set[str] = set()
        for row in self.read_attempts():
            value = row.get("canonical_expression") or row.get("expression")
            if value:
                known.add(canonical_expression(str(value)))
        return known

    def latest_edge_cards(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for card in self.read_edge_cards():
            edge_id = str(card.get("edge_id") or "")
            if edge_id:
                latest[edge_id] = card
        return list(latest.values())

    def get_edge_card(self, edge_id: str) -> dict[str, Any] | None:
        for card in reversed(self.latest_edge_cards()):
            if card.get("edge_id") == edge_id:
                return card
        return None

    def summary(self) -> dict[str, Any]:
        attempts = self.read_attempts()
        failures = self.read_failures()
        cards = self.latest_edge_cards()
        by_status: dict[str, int] = {}
        for card in cards:
            status = str(card.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "schema": MEMORY_SCHEMA,
            "root": str(self.root),
            "n_attempts": len(attempts),
            "n_failures": len(failures),
            "n_edge_cards": len(cards),
            "edge_cards_by_status": by_status,
            "latest_attempt": attempts[-1] if attempts else None,
            "latest_failure": failures[-1] if failures else None,
            "latest_edge_cards": cards[-5:],
        }

    def context_for_search(self, limit: int = 8) -> dict[str, Any]:
        """Compact read context for PathFinder/FactorBuilder prompts."""
        failures = self.read_failures()
        cards = self.latest_edge_cards()
        recent_failures = failures[-limit:]
        recent_validated = [
            card for card in cards
            if card.get("status") in {"signal_validated", "validated", "active"}
        ][-limit:]
        return {
            "schema": MEMORY_SCHEMA,
            "n_attempts": len(self.read_attempts()),
            "n_known_expressions": len(self.known_expressions()),
            "n_failures": len(failures),
            "n_edge_cards": len(cards),
            "recent_failures": [
                {
                    "expression": row.get("canonical_expression") or row.get("expression"),
                    "status": row.get("status"),
                    "reason": row.get("reason"),
                }
                for row in recent_failures
            ],
            "recent_validated": [
                {
                    "edge_id": card.get("edge_id"),
                    "expression": card.get("expression"),
                    "status": card.get("status"),
                    "long_short_t": (card.get("signal_evidence") or {}).get("long_short_t"),
                }
                for card in recent_validated
            ],
            "policy": "skip expressions already present in attempts.jsonl",
        }

    # ------------------------------------------------------------------
    # write side
    def record_attempt(
        self,
        expression: str,
        *,
        status: str,
        reason: str | None = None,
        metrics: dict[str, Any] | None = None,
        source: str = "",
        generation: int = 0,
        horizon: int = 5,
        cost_bps: float = 20.0,
        search_id: str = "",
        p_value: float | None = None,
    ) -> dict[str, Any]:
        canonical = canonical_expression(expression)
        record = {
            "schema": "factor_lab.alpha.attempt.v1",
            "attempt_id": f"ATT-{uuid.uuid4().hex[:12]}",
            "expression": str(expression),
            "canonical_expression": canonical,
            "expression_hash": expression_hash(canonical),
            "status": str(status),
            "reason": reason,
            "source": source,
            "generation": int(generation),
            "horizon": int(horizon),
            "cost_bps": float(cost_bps),
            "search_id": search_id,
            "p_value": p_value,
            "metrics": metrics or {},
            "created_at": config.now_iso(),
        }
        append_jsonl(self.attempts_path, record)
        if status in FAILURE_STATUSES:
            self.record_failure(
                canonical,
                reason=reason or f"attempt status: {status}",
                status=str(status),
                metrics=metrics,
                source=source,
                horizon=horizon,
            )
        return record

    def record_failure(
        self,
        expression: str,
        *,
        reason: str,
        status: str = "failed",
        metrics: dict[str, Any] | None = None,
        source: str = "",
        horizon: int = 5,
    ) -> dict[str, Any]:
        canonical = canonical_expression(expression)
        record = {
            "schema": "factor_lab.alpha.failure.v1",
            "expression": str(expression),
            "canonical_expression": canonical,
            "expression_hash": expression_hash(canonical),
            "status": str(status),
            "reason": str(reason),
            "source": source,
            "horizon": int(horizon),
            "metrics": metrics or {},
            "created_at": config.now_iso(),
        }
        append_jsonl(self.failures_path, record)
        return record

    def record_edge_card(self, card: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(card, dict):
            raise TypeError("edge card must be a dict")
        if card.get("schema") != EDGE_CARD_SCHEMA:
            raise ValueError("invalid edge card schema")
        if not card.get("edge_id") or not card.get("canonical_expression"):
            raise ValueError("edge card requires edge_id and canonical_expression")
        if card.get("status") not in VALID_CARD_STATUSES:
            raise ValueError(f"invalid edge card status: {card.get('status')}")
        append_jsonl(self.edge_cards_path, card)
        if card.get("status") in {"killed", "parked", "not_deployable"}:
            self.record_failure(
                str(card["canonical_expression"]),
                reason=f"edge card status: {card['status']}",
                status=str(card["status"]),
                source=str(card.get("source") or ""),
                horizon=int(card.get("horizon_days") or 5),
            )
        return card

    def record_discovery(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a Web Gateway discovery map as research memory."""
        record = {
            "schema": "factor_lab.alpha.discovery.v1",
            "discovery_id": f"DISC-{uuid.uuid4().hex[:12]}",
            "payload": payload,
            "created_at": config.now_iso(),
        }
        append_jsonl(self.root / "discoveries.jsonl", record)
        return record

    def finalize(self) -> dict[str, Any]:
        summary = self.summary()
        write_json_atomic(self.summary_path, summary)
        return summary

    def clear(self) -> None:
        """Test/maintenance helper; not used by production flow."""
        for path in (
            self.attempts_path,
            self.edge_cards_path,
            self.failures_path,
            self.summary_path,
            self.root / "discoveries.jsonl",
        ):
            if path.exists():
                path.unlink()
