"""Versioned evaluation specs for the shared metrics core.

The core algorithms are extracted verbatim from ``research.report.metrics``
(the calibrated source of truth).  A spec only declares *configuration*
(entry convention, sampling, thresholds, cost model, capacity model), never a
different formula: every product must evaluate the same primitives.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Sampling = Literal["non_overlapping", "daily"]
Entry = Literal["next_open", "same_close"]
TMethod = Literal["plain", "newey_west", "independent", "none"]
CostConvention = Literal["two_leg", "round_trip"]
BpsMeaning = Literal["round_trip", "per_side"]


@dataclass(frozen=True)
class CostModel:
    """Cost model.

    ``two_leg`` follows the calibrated report convention: ``cost_bps`` is the
    single-leg round-trip fee (buy + sell), applied to each leg's turnover.

    ``round_trip`` is the historical Alpha convention: LS portfolio turnover
    times ``2 * cost_bps``.  It is numerically equivalent for equal-weight
    legs but kept as an explicit profile knob.
    """

    bps: float = 20.0
    convention: CostConvention = "two_leg"
    #: What ``bps`` means:
    #: - "round_trip": fee charged per unit of *one-way turnover* (替代买入+卖出的往返
    #:   费率).  ``cost = one_way_turnover × bps``.
    #: - "per_side": fee charged per unit of traded notional on each side
    #:   (买入或卖出各收一次).  ``cost = 2 × one_way_turnover × bps``.
    bps_meaning: BpsMeaning = "round_trip"
    #: Annual short-borrow cost charged on the short leg. 0 keeps historical
    #: research metrics comparable; production/Alpha campaigns may set e.g. 800.
    borrow_bps_per_year: float = 0.0
    #: Explicit China A-share one-way fee schedule. When either is non-zero,
    #: ``_spread_series`` charges (buy_fee_bps + sell_fee_bps) per one-way
    #: turnover instead of the generic ``bps`` convention. Stamp duty belongs
    #: only to the sell side; commission/handling/regulatory/transfer are both.
    buy_fee_bps: float = 0.0
    sell_fee_bps: float = 0.0

    def one_way_cost_multiplier(self) -> float:
        return 1.0 if self.bps_meaning == "round_trip" else 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bps": float(self.bps),
            "convention": self.convention,
            "bps_meaning": self.bps_meaning,
            **({"borrow_bps_per_year": float(self.borrow_bps_per_year)} if float(self.borrow_bps_per_year) != 0.0 else {}),
            **({"buy_fee_bps": float(self.buy_fee_bps), "sell_fee_bps": float(self.sell_fee_bps)} if (float(self.buy_fee_bps) or float(self.sell_fee_bps)) else {}),
        }


@dataclass(frozen=True)
class CapacitySpec:
    """Capacity assumption.  ``formula`` selects the calibrated report formula
    (10% ADV per name, 3-day build, 10th-percentile constraint)."""

    formula: str = "research_adv"
    target_aum: float = 50_000_000.0
    participation: float = 0.10
    build_days: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "target_aum": float(self.target_aum),
            "participation": float(self.participation),
            "build_days": int(self.build_days),
        }


@dataclass(frozen=True)
class MetricsSpec:
    version: str = "1.0"
    entry: Entry = "next_open"
    horizon: int = 5
    sampling: Sampling = "non_overlapping"
    groups: int = 5
    min_ic_names: int = 20
    min_book_names: int = 10
    t_method: TMethod = "plain"
    nw_lag: int | None = None
    winsorize: tuple[float, float] = (0.01, 0.99)
    trim: tuple[float, float] = (0.01, 0.99)
    cost: CostModel = field(default_factory=CostModel)
    capacity: CapacitySpec = field(default_factory=CapacitySpec)
    universe_denominator: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self.version),
            "entry": self.entry,
            "horizon": int(self.horizon),
            "sampling": self.sampling,
            "groups": int(self.groups),
            "min_ic_names": int(self.min_ic_names),
            "min_book_names": int(self.min_book_names),
            "t_method": self.t_method,
            "nw_lag": None if self.nw_lag is None else int(self.nw_lag),
            "winsorize": [float(self.winsorize[0]), float(self.winsorize[1])],
            "trim": [float(self.trim[0]), float(self.trim[1])],
            "cost": self.cost.to_dict(),
            "capacity": self.capacity.to_dict(),
            "universe_denominator": bool(self.universe_denominator),
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def replace(self, **changes: Any) -> "MetricsSpec":
        return replace(self, **changes)


#: Alpha's deterministic gate profile.  Sampling stays non-overlapping (the
#: Alpha research design); every formula below comes from the calibrated core.
ALPHA_GATE_SPEC = MetricsSpec(
    version="alpha-gate-v1",
    entry="next_open",
    horizon=5,
    sampling="non_overlapping",
    groups=5,
    min_ic_names=10,
    min_book_names=10,
    t_method="plain",
    nw_lag=None,
    winsorize=(0.01, 0.99),
    trim=(0.01, 0.99),
    cost=CostModel(bps=20.0, convention="two_leg", bps_meaning="per_side"),
    capacity=CapacitySpec(formula="research_adv", target_aum=50_000_000.0, participation=0.10, build_days=3),
    universe_denominator=True,
)

#: Production factor report profile (daily overlapping IC + NW-t; the report
#: itself calls the primitives directly, this spec is the audit identity).
RESEARCH_SPEC = MetricsSpec(
    version="research-report-v1",
    entry="next_open",
    horizon=5,
    sampling="daily",
    groups=5,
    min_ic_names=20,
    min_book_names=5,
    t_method="newey_west",
    nw_lag=4,
    winsorize=(0.01, 0.99),
    trim=(0.01, 0.99),
    cost=CostModel(bps=20.0, convention="two_leg"),
    capacity=CapacitySpec(formula="research_adv", target_aum=50_000_000.0, participation=0.10, build_days=3),
    universe_denominator=False,
)
