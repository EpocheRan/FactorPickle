"""Cross-sectional rank IC (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _spearman_ic(factor_row: pd.Series, ret_row: pd.Series) -> float:
    pair = pd.concat([factor_row.rename("f"), ret_row.rename("r")], axis=1).dropna()
    if len(pair) < 30 or pair["f"].nunique() < 5 or pair["r"].nunique() < 5:
        return np.nan
    return float(pair["f"].rank().corr(pair["r"].rank()))


def _rank_ic_frame(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    min_names: int = 20,
    *,
    factor_rank: pd.DataFrame | None = None,
) -> pd.Series:
    """逐日 Spearman 秩相关（全向量化）。

    ``factor_rank`` 可由调用方缓存；同一 signal 对 1/5/10/20 日只排一次。
    """
    common = factor.columns.intersection(fwd.columns)
    if len(common) == 0:
        return pd.Series(dtype="float64")
    a = (
        factor_rank[common].reindex(index=factor.index)
        if factor_rank is not None
        else factor[common].rank(axis=1)
    )
    b = fwd[common].reindex(index=factor.index).rank(axis=1)
    mask = a.notna() & b.notna()
    a = a.where(mask)
    b = b.where(mask)
    ma = a.mean(axis=1)
    mb = b.mean(axis=1)
    da = a.sub(ma, axis=0)
    db = b.sub(mb, axis=0)
    cov = (da * db).mean(axis=1)
    sa = da.pow(2).mean(axis=1).pow(0.5)
    sb = db.pow(2).mean(axis=1).pow(0.5)
    ic = cov / (sa * sb).replace(0, np.nan)
    ic = ic.where(mask.sum(axis=1) >= int(min_names))
    return ic.replace([np.inf, -np.inf], np.nan)
