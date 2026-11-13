"""Research mandate policy: deployment mode, horizon, universe, AUM, benchmark.

The mandate is repository source-of-truth.  Evaluation code may read it, but
must not write it.  The current mandate is long-only, 20d primary horizon,
PIT liquidity-1000 universe, a pre-registered smallest-30% size screen, an
AUM ceiling of CNY 1,000,000, and an equal-weight eligible-pool benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json


MANDATE_SCHEMA = "factor_lab.alpha.mandate.v1"


@dataclass(frozen=True)
class Mandate:
    schema: str = MANDATE_SCHEMA
    deployment_mode: str = "long_only"
    primary_horizon: int = 20
    robustness_horizons: tuple[int, ...] = (5, 60)
    universe_name: str = "pit_liquidity_1000"
    universe_top_n: int = 1000
    min_history_days: int = 120
    lookback_days: int = 60
    size_screen_name: str = "exclude_smallest_float_mv_30pct"
    size_screen_fraction: float = 0.30
    aum_cny: float = 1_000_000.0
    benchmark_primary: str = "ew_eligible_pool"
    benchmark_secondary: tuple[str, ...] = ("csi_1000",)
    secondary_data_available: bool = False
    long_only_gate: dict[str, Any] = field(default_factory=dict)
    data_requirements: dict[str, Any] = field(default_factory=dict)
    research_mode: str = "replication"
    research_evidence: dict[str, Any] = field(default_factory=dict)
    pool_mode: str = "ex_ante_top1000"
    price_basis: str = "pit_cumulative_adjusted_hfq"
    tradability: str = "partial_ohlcv_mask"
    holdout_v2_start: str = "2026-11-02"

    def __post_init__(self) -> None:
        if self.deployment_mode != "long_only":
            raise ValueError("the current mandate requires deployment_mode=long_only")
        if int(self.primary_horizon) < 1:
            raise ValueError("primary_horizon must be positive")
        if float(self.aum_cny) <= 0:
            raise ValueError("aum_cny must be positive")
        if not (0.0 <= float(self.size_screen_fraction) < 1.0):
            raise ValueError("size_screen_fraction must be in [0, 1)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "deployment_mode": self.deployment_mode,
            "primary_horizon": int(self.primary_horizon),
            "robustness_horizons": [int(item) for item in self.robustness_horizons],
            "universe": {
                "name": self.universe_name,
                "top_n": int(self.universe_top_n),
                "min_history_days": int(self.min_history_days),
                "lookback_days": int(self.lookback_days),
            },
            "size_screen": {
                "name": self.size_screen_name,
                "exclude_smallest_fraction": float(self.size_screen_fraction),
            },
            "aum_cny": float(self.aum_cny),
            "benchmark": {
                "primary": self.benchmark_primary,
                "secondary": list(self.benchmark_secondary),
                "secondary_data_available": bool(self.secondary_data_available),
            },
            "long_only_gate": dict(self.long_only_gate),
            "data_requirements": dict(self.data_requirements),
            "research_mode": self.research_mode,
            "research_evidence": dict(self.research_evidence),
            "pool_mode": self.pool_mode,
            "price_basis": self.price_basis,
            "tradability": self.tradability,
            "holdout_v2_start": self.holdout_v2_start,
        }


def default_mandate_path() -> Path:
    return Path(config.ALPHA_POLICY_DIR) / "mandate.json"


def load_mandate(path: str | Path | None = None) -> Mandate:
    target = Path(path) if path is not None else default_mandate_path()
    raw = read_json(target, default=None)
    if not isinstance(raw, dict):
        return Mandate()
    schema = raw.get("schema")
    if schema is not None and schema != MANDATE_SCHEMA:
        raise ValueError(f"unsupported mandate schema: {schema}")
    universe = raw.get("universe") or {}
    size = raw.get("size_screen") or {}
    benchmark = raw.get("benchmark") or {}
    return Mandate(
        schema=str(raw.get("schema") or MANDATE_SCHEMA),
        deployment_mode=str(raw.get("deployment_mode") or "long_only"),
        primary_horizon=int(raw.get("primary_horizon") or 20),
        robustness_horizons=tuple(int(item) for item in (raw.get("robustness_horizons") or [5, 60])),
        universe_name=str(universe.get("name") or "pit_liquidity_1000"),
        universe_top_n=int(universe.get("top_n") or 1000),
        min_history_days=int(universe.get("min_history_days") or 120),
        lookback_days=int(universe.get("lookback_days") or 60),
        size_screen_name=str(size.get("name") or "exclude_smallest_float_mv_30pct"),
        size_screen_fraction=float(size.get("exclude_smallest_fraction") or 0.30),
        aum_cny=float(raw.get("aum_cny") or 1_000_000.0),
        benchmark_primary=str(benchmark.get("primary") or "ew_eligible_pool"),
        benchmark_secondary=tuple(str(item) for item in (benchmark.get("secondary") or ("csi_1000",))),
        secondary_data_available=bool(benchmark.get("secondary_data_available", False)),
        long_only_gate=dict(raw.get("long_only_gate") or {}),
        data_requirements=dict(raw.get("data_requirements") or {}),
        research_mode=str(raw.get("research_mode") or "replication"),
        research_evidence=dict(raw.get("research_evidence") or {}),
        pool_mode=str(raw.get("pool_mode") or "ex_ante_top1000"),
        price_basis=str(raw.get("price_basis") or "pit_cumulative_adjusted_hfq"),
        tradability=str(raw.get("tradability") or "partial_ohlcv_mask"),
        holdout_v2_start=str(raw.get("holdout_v2_start") or "2026-11-02"),
    )


def data_capability_blockers(mandate: Mandate | None = None) -> list[str]:
    mandate = mandate or load_mandate()
    requirements = mandate.data_requirements or {}
    blockers = []
    for key in ("delisting_adjusted_universe", "capacity_impact_model", "pit_earnings_announcement_dates", "oos_2017_2025"):
        if requirements.get(key) is not True:
            blockers.append(key)
    return blockers
