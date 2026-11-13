"""Single-account capital allocator and deterministic risk controller.

The allocator only consumes latest Edge Cards.  It never promotes a card; it
maps validated/active cards into a baseline-plus-active allocation.  The
default baseline is a broad index; no validated edge means 100% baseline.
"""

from __future__ import annotations

from typing import Any, Iterable

from factor_lab.alpha.memory import AlphaMemory


ALLOCATION_SCHEMA = "factor_lab.alpha.allocation.v1"
VALIDATED = {"validated", "active"}


def _latest_cards(cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for card in cards:
        edge_id = str(card.get("edge_id") or "")
        if edge_id:
            latest[edge_id] = card
    return list(latest.values())


class CapitalAllocator:
    def __init__(self, memory: AlphaMemory | None = None) -> None:
        self.memory = memory or AlphaMemory()

    def allocate(
        self,
        *,
        total_capital: float = 300_000.0,
        baseline: str = "BROAD_INDEX",
        max_active_weight: float = 0.50,
        single_edge_cap: float = 0.20,
        max_edges: int = 5,
    ) -> dict[str, Any]:
        if total_capital <= 0:
            raise ValueError("total_capital must be positive")
        cards = [
            card
            for card in _latest_cards(self.memory.latest_edge_cards())
            if card.get("status") in VALIDATED
        ]
        cards.sort(
            key=lambda card: (
                float((card.get("signal_evidence") or {}).get("long_short_t") or 0.0),
                float((card.get("deployability") or {}).get("long_only_excess_after_tax") or 0.0),
            ),
            reverse=True,
        )
        cards = cards[: max(0, int(max_edges))]
        if not cards:
            return {
                "schema": ALLOCATION_SCHEMA,
                "total_capital": float(total_capital),
                "baseline": baseline,
                "baseline_weight": 1.0,
                "active_weight": 0.0,
                "cash_weight": 0.0,
                "positions": [{"kind": "baseline", "symbol": baseline, "weight": 1.0}],
                "source_edges": [],
                "rule": "no validated edge -> 100% baseline",
            }
        # Staged active budget: one edge <=20%, two <=30%, more <=50%.
        staged = [0.20, 0.30, 0.50]
        cap = staged[min(len(cards) - 1, len(staged) - 1)] if len(cards) <= 3 else 0.50
        active_weight = min(float(max_active_weight), cap, len(cards) * float(single_edge_cap))
        active_weight = max(0.0, active_weight)
        per_edge = active_weight / len(cards)
        baseline_weight = max(0.0, 1.0 - active_weight)
        positions = [
            {"kind": "edge", "edge_id": card.get("edge_id"), "factor_id": (card.get("factor_ref") or {}).get("factor_id"), "weight": per_edge}
            for card in cards
        ]
        positions.insert(0, {"kind": "baseline", "symbol": baseline, "weight": baseline_weight})
        return {
            "schema": ALLOCATION_SCHEMA,
            "total_capital": float(total_capital),
            "baseline": baseline,
            "baseline_weight": baseline_weight,
            "active_weight": active_weight,
            "cash_weight": 0.0,
            "positions": positions,
            "source_edges": [card.get("edge_id") for card in cards],
            "rule": "validated edges only; baseline remains the default",
        }


class RiskController:
    """Deterministic exposure controller; no LLM discretion."""

    def __init__(self, target_vol: float = 0.125, min_exposure: float = 0.30, max_exposure: float = 0.95) -> None:
        self.target_vol = float(target_vol)
        self.min_exposure = float(min_exposure)
        self.max_exposure = float(max_exposure)

    def exposure(self, forecast_vol: float) -> float:
        if forecast_vol <= 0:
            return self.max_exposure
        return float(min(self.max_exposure, max(self.min_exposure, self.target_vol / float(forecast_vol))))

    def drawdown_target(self, drawdown: float) -> float:
        dd = abs(float(drawdown))
        if dd >= 0.20:
            return 0.0
        if dd >= 0.15:
            return 0.08
        if dd >= 0.10:
            return 0.10
        return self.target_vol
