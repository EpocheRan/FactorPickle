"""Point-in-time (PIT) universe for Alpha research.

Eligibility at a rebalance date ``t`` is derived **only from data observable at
``t``**:

- a valid, positive close at ``t`` (a halted/suspended/delisted name has no bar);
- at least ``min_history_days`` valid closes in the trailing window (a newly
  listed name becomes eligible only after it has enough history);
- a positive volume at ``t`` when the panel exposes a volume matrix.

No future information is used.  Delisting is not forecast: a name that stops
trading simply drops out of the universe after its last bar and is never
forward-filled.  Likewise, "next open" tradability is not part of membership;
the evaluator already drops dates whose forward entry/return is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.core import config
from factor_lab.core.store import read_json

UNIVERSE_SCHEMA = "factor_lab.alpha.universe.v1"
UNIVERSE_POLICY_SCHEMA = "factor_lab.alpha.universe_policy.v1"


@dataclass(frozen=True)
class UniversePolicy:
    name: str = "pit_v1"
    enabled: bool = True
    min_history_days: int = 60
    min_close: float = 0.0
    require_positive_volume: bool = True
    top_n: int = 0
    lookback_days: int = 60

    def __post_init__(self) -> None:
        if int(self.min_history_days) < 1:
            raise ValueError("min_history_days must be >= 1")
        if int(self.top_n) < 0:
            raise ValueError("top_n must be >= 0")
        if int(self.lookback_days) < 1:
            raise ValueError("lookback_days must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": UNIVERSE_POLICY_SCHEMA,
            "name": str(self.name),
            "enabled": bool(self.enabled),
            "min_history_days": int(self.min_history_days),
            "min_close": float(self.min_close),
            "require_positive_volume": bool(self.require_positive_volume),
            "top_n": int(self.top_n),
            "lookback_days": int(self.lookback_days),
        }


def default_policy_path() -> Path:
    return config.ALPHA_POLICY_DIR / "universe.json"


def load_universe_policy(path: str | Path | None = None) -> UniversePolicy:
    """Load the repo-owned universe policy; missing file falls back to defaults."""
    target = Path(path) if path is not None else default_policy_path()
    raw = read_json(target, default=None)
    if not isinstance(raw, dict):
        return UniversePolicy()
    schema = raw.get("schema")
    if schema is not None and schema != UNIVERSE_POLICY_SCHEMA:
        raise ValueError(f"unsupported universe policy schema: {schema}")
    return UniversePolicy(
        name=str(raw.get("name") or "pit_v1"),
        enabled=bool(raw.get("enabled", True)),
        min_history_days=int(raw.get("min_history_days", 60)),
        min_close=float(raw.get("min_close", 0.0)),
        require_positive_volume=bool(raw.get("require_positive_volume", True)),
        top_n=int(raw.get("top_n", 0)),
        lookback_days=int(raw.get("lookback_days", 60)),
    )


def build_pit_universe(
    context: AlphaContext,
    policy: UniversePolicy | None = None,
    *,
    dates: list[str] | None = None,
) -> pd.DataFrame:
    """Build a ``date x symbol`` boolean eligibility mask from trailing data only."""
    policy = policy or UniversePolicy()
    close = context.close
    columns = list(close.columns)
    index = close.index

    valid = close.notna() & (close.astype(float) > float(policy.min_close))
    history = (
        valid.rolling(int(policy.min_history_days), min_periods=int(policy.min_history_days))
        .sum()
        .ge(int(policy.min_history_days))
    )
    mask = valid & history

    volume = context.fields.get("volume")
    if policy.require_positive_volume and volume is not None:
        volume = volume.reindex(index=index, columns=columns).astype(float)
        mask = mask & (volume.fillna(0.0) > 0.0)

    if int(policy.top_n) > 0:
        amount = context.fields.get("amount")
        if amount is not None:
            liquidity = amount.reindex(index=index, columns=columns).astype(float)
            # Trailing average amount is observed by close t; no future data.
            liquidity = liquidity.where(liquidity > 0).rolling(
                int(policy.lookback_days), min_periods=max(10, int(policy.lookback_days) // 3)
            ).mean()
            ranks = liquidity.where(mask).rank(axis=1, ascending=False, method="first")
            mask = mask & ranks.le(int(policy.top_n))
        else:
            raise ValueError("top_n universe policy requires an amount field")
    mask = mask.fillna(False).astype(bool)
    if dates is not None:
        wanted = [date for date in dates if date in mask.index]
        mask = mask.loc[wanted]
    return mask


def universe_summary(
    mask: pd.DataFrame | None,
    policy: UniversePolicy | None = None,
) -> dict[str, Any] | None:
    """Compact, auditable summary of a PIT universe mask."""
    if mask is None:
        return None
    sizes = mask.sum(axis=1).astype(int)
    cells = int(mask.to_numpy().sum())
    panel_cells = int(mask.shape[0] * mask.shape[1])
    return {
        "schema": UNIVERSE_SCHEMA,
        "policy": policy.to_dict() if policy is not None else None,
        "n_dates": int(mask.shape[0]),
        "n_symbols": int(mask.shape[1]),
        "avg_size": round(float(sizes.mean()), 4) if len(sizes) else 0.0,
        "min_size": int(sizes.min()) if len(sizes) else 0,
        "max_size": int(sizes.max()) if len(sizes) else 0,
        "eligible_cells": cells,
        "eligible_fraction": round(cells / max(1, panel_cells), 6),
        "first_date": str(mask.index.min().date()) if len(mask.index) else None,
        "last_date": str(mask.index.max().date()) if len(mask.index) else None,
    }
