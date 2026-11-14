"""Tradability mask for Alpha evaluation.

The mask is deliberately deterministic and conservative: a name is not
tradable on a date when it is suspended or when its close/limit state makes the
required side impossible.

Two limit sources are supported, in priority order:

1. **Exact exchange limits** from ``stk_limit`` (``up_limit`` / ``down_limit``)
   compared with the raw (unadjusted) OHLC.  This is the preferred path and is
   used whenever the gateway supplies the fields.
2. **Board-ratio fallback** when the exact limits are absent: the common 10%
   A-share band (20% for ChiNext/STAR, 30% for BSE) reconstructed from the
   previous adjusted close, with a 0.5% tolerance.

Suspension state comes from ``suspend_d`` (``is_suspended``) when available and
otherwise falls back to ``volume == 0``.  This is a research mask, not an
execution engine.
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

    suspended = context.fields.get("is_suspended")
    if suspended is not None:
        mask &= ~(suspended.reindex_like(close).fillna(0.0).to_numpy() > 0.5)

    if high is not None and low is not None:
        high_values = high.reindex_like(close)
        low_values = low.reindex_like(close)
        ratios = pd.Series([_limit_ratio(code) for code in close.columns], index=close.columns)
        fallback_up = previous * (1.0 + ratios)
        fallback_down = previous * (1.0 - ratios)
        can_buy = ~(
            (high_values >= fallback_up * (1.0 - tolerance))
            & (close >= fallback_up * (1.0 - tolerance))
        )
        can_sell = ~(
            (low_values <= fallback_down * (1.0 + tolerance))
            & (close <= fallback_down * (1.0 + tolerance))
        )

        up_limit = context.fields.get("up_limit")
        down_limit = context.fields.get("down_limit")
        close_raw = context.fields.get("close_raw")
        high_raw = context.fields.get("high_raw")
        low_raw = context.fields.get("low_raw")
        if (
            up_limit is not None
            and down_limit is not None
            and close_raw is not None
            and high_raw is not None
            and low_raw is not None
        ):
            up = up_limit.reindex_like(close)
            down = down_limit.reindex_like(close)
            raw_close = close_raw.reindex_like(close)
            raw_high = high_raw.reindex_like(close)
            raw_low = low_raw.reindex_like(close)
            valid = up.notna() & down.notna() & raw_close.notna() & raw_high.notna() & raw_low.notna()
            exact_buy = ~(
                (raw_high >= up * (1.0 - tolerance))
                & (raw_close >= up * (1.0 - tolerance))
            )
            exact_sell = ~(
                (raw_low <= down * (1.0 + tolerance))
                & (raw_close <= down * (1.0 + tolerance))
            )
            # Use the exact exchange limit where it is available; keep the
            # board-ratio fallback for names the limit table does not cover.
            can_buy = can_buy.mask(valid.to_numpy(), exact_buy)
            can_sell = can_sell.mask(valid.to_numpy(), exact_sell)
        mask &= can_buy.to_numpy() & can_sell.to_numpy()

    mask = mask & previous.notna()
    mask = mask & close.notna()
    return mask.fillna(False).astype(bool)


def blocked_fraction(mask: pd.DataFrame | None) -> float | None:
    if mask is None or mask.empty:
        return None
    return float(1.0 - np.asarray(mask, dtype=bool).mean())
