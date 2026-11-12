"""Tradability mask for Alpha evaluation.

The mask is deliberately deterministic and conservative: a name is not
tradable on a date when it is suspended (zero volume) or when its close/limit
state makes the required side impossible. Board/ST-specific 5%/20% limits are
not available in the current context, so the mask uses the common 10% A-share
band and treats any close within 0.5% of the limit as non-tradable. This is a
research approximation, not an execution engine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext


def _limit_ratio(code: str) -> float:
    code = str(code)
    if code.startswith(("30", "68")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def build_tradability_mask(context: AlphaContext, *, tolerance: float = 0.005) -> pd.DataFrame:
    close = context.close
    high = context.fields.get("high")
    low = context.fields.get("low")
    volume = context.fields.get("volume")
    previous = close.shift(1)
    mask = pd.DataFrame(True, index=close.index, columns=close.columns)
    if volume is not None:
        mask &= volume.reindex_like(close).fillna(0.0).to_numpy() > 0
    if high is not None and low is not None:
        ratios = pd.Series([ _limit_ratio(code) for code in close.columns ], index=close.columns)
        limit_up = previous * (1.0 + ratios)
        limit_down = previous * (1.0 - ratios)
        high_values = high.reindex_like(close)
        low_values = low.reindex_like(close)
        can_buy = ~((high_values >= limit_up * (1.0 - tolerance)) & (close >= limit_up * (1.0 - tolerance)))
        can_sell = ~((low_values <= limit_down * (1.0 + tolerance)) & (close <= limit_down * (1.0 + tolerance)))
        mask &= can_buy.to_numpy() & can_sell.to_numpy()
    mask = mask & previous.notna()
    mask = mask & close.notna()
    return mask.fillna(False).astype(bool)


def blocked_fraction(mask: pd.DataFrame | None) -> float | None:
    if mask is None or mask.empty:
        return None
    return float(1.0 - np.asarray(mask, dtype=bool).mean())
