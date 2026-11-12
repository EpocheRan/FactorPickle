"""Filter-value tests (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .stats import TRADING_DAYS


def _filter_test(period_returns: pd.DataFrame, horizon: int) -> dict:
    """过滤价值检验：剔除 Q1 后组合是否真的变好。

    过滤的严格定义不是「Q1 最差」，而是「剔除 Q1 后夏普/回撤/尾部风险改善」。
    如果 Q1 的绝对收益与全组均值接近、剔除后夏普不升，则过滤只是放弃收益。
    """
    if period_returns is None or period_returns.empty or "Q1" not in period_returns:
        return {}
    cols = list(period_returns.columns)
    q1 = period_returns["Q1"].dropna()
    rest_cols = [c for c in cols if c != "Q1"]
    rest = period_returns[rest_cols].mean(axis=1).dropna()
    all_long = period_returns.mean(axis=1).dropna()
    if len(q1) < 20:
        return {}

    def _perf(series: pd.Series) -> dict:
        nav = (1 + series).cumprod()
        ann = float(nav.iloc[-1] ** (TRADING_DAYS / (len(series) * horizon)) - 1)
        vol = float(series.std() * np.sqrt(TRADING_DAYS / horizon))
        sharpe = float(series.mean() / series.std() * np.sqrt(TRADING_DAYS / horizon)) if series.std() else np.nan
        mdd = float((nav / nav.cummax() - 1).min())
        return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd}

    q1_perf, rest_perf, all_perf = _perf(q1), _perf(rest), _perf(all_long)
    spread = period_returns["Q5"] - period_returns["Q1"] if "Q5" in period_returns else pd.Series(dtype=float)
    spread = spread.dropna()
    se = float(spread.std(ddof=1) / np.sqrt(len(spread))) if len(spread) > 2 else np.nan
    rel = ((1 + q1).cumprod() / (1 + all_long).cumprod()).dropna()
    rel_mdd = float((rel / rel.cummax() - 1).min()) if len(rel) else np.nan
    tail_q1 = float((q1 < -0.03).mean())
    tail_rest = float((rest < -0.03).mean())
    ann_factor = TRADING_DAYS / horizon
    q1_excess = float(q1.mean() - all_long.mean())
    useful = bool(
        rest_perf["sharpe"] > all_perf["sharpe"] + 0.05
        and q1.mean() < all_long.mean() - 0.001
        and tail_q1 > tail_rest + 0.03
    )
    return {
        "q1_mean": float(q1.mean()),
        "rest_mean": float(rest.mean()),
        "all_mean": float(all_long.mean()),
        "q1_ann": float(q1.mean() * ann_factor),
        "rest_ann": float(rest.mean() * ann_factor),
        "all_ann": float(all_long.mean() * ann_factor),
        "q1_cagr": q1_perf["ann"],
        "rest_cagr": rest_perf["ann"],
        "all_cagr": all_perf["ann"],
        "q1_excess": q1_excess,
        "q1_excess_ann": float(q1_excess * ann_factor),
        "q1_sharpe": q1_perf["sharpe"],
        "rest_sharpe": rest_perf["sharpe"],
        "all_sharpe": all_perf["sharpe"],
        "q1_mdd": q1_perf["mdd"],
        "rel_mdd": rel_mdd,
        "tail_q1": tail_q1,
        "tail_rest": tail_rest,
        "spread_mean": float(spread.mean()) if len(spread) else np.nan,
        "spread_se": se,
        "useful": useful,
    }


def _filter_test_side(period_returns: pd.DataFrame, horizon: int, drop_group: str) -> dict:
    """通用单侧过滤检验：剔除指定组后，组合是否改善。

    与 ``_filter_test`` 的区别是组名可配置；用于同时检查 Q1 和 Q5，
    避免只因为 direction=+1 就只测“最弱组”。
    """
    if period_returns is None or period_returns.empty or drop_group not in period_returns:
        return {}
    cols = list(period_returns.columns)
    dropped = period_returns[drop_group].dropna()
    rest_cols = [c for c in cols if c != drop_group]
    rest = period_returns[rest_cols].mean(axis=1).dropna()
    all_long = period_returns.mean(axis=1).dropna()
    if len(dropped) < 20:
        return {}

    def _perf(series: pd.Series) -> dict:
        nav = (1 + series).cumprod()
        ann = float(nav.iloc[-1] ** (TRADING_DAYS / (len(series) * horizon)) - 1)
        vol = float(series.std() * np.sqrt(TRADING_DAYS / horizon))
        sharpe = float(series.mean() / series.std() * np.sqrt(TRADING_DAYS / horizon)) if series.std() else np.nan
        mdd = float((nav / nav.cummax() - 1).min())
        return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd}

    d_perf, r_perf, a_perf = _perf(dropped), _perf(rest), _perf(all_long)
    rel = ((1 + dropped).cumprod() / (1 + all_long).cumprod()).dropna()
    rel_mdd = float((rel / rel.cummax() - 1).min()) if len(rel) else np.nan
    tail_d = float((dropped < -0.03).mean())
    tail_r = float((rest < -0.03).mean())
    diff = (rest - all_long).dropna()
    diff_se = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 2 else np.nan
    diff_t = float(diff.mean() / diff_se) if diff_se and np.isfinite(diff_se) else np.nan
    diff_ci = (
        (float(diff.mean() - 1.96 * diff_se), float(diff.mean() + 1.96 * diff_se))
        if np.isfinite(diff_se) else (np.nan, np.nan)
    )
    # 四项改善里至少 3 项，避免单一阈值（例如尾部差 2.6pp）把明显改善判死。
    improvements = [
        bool(r_perf["sharpe"] > a_perf["sharpe"] + 0.05),
        bool(dropped.mean() < all_long.mean() - 0.001),
        bool(r_perf["mdd"] > a_perf["mdd"]),
        bool(tail_d > tail_r + 0.01),
    ]
    useful = bool(sum(improvements) >= 3)
    ann_factor = TRADING_DAYS / horizon
    return {
        "drop_group": drop_group,
        "dropped_mean": float(dropped.mean()),
        "rest_mean": float(rest.mean()),
        "all_mean": float(all_long.mean()),
        "dropped_ann": float(dropped.mean() * ann_factor),
        "rest_ann": float(rest.mean() * ann_factor),
        "all_ann": float(all_long.mean() * ann_factor),
        "dropped_sharpe": d_perf["sharpe"],
        "rest_sharpe": r_perf["sharpe"],
        "all_sharpe": a_perf["sharpe"],
        "dropped_mdd": d_perf["mdd"],
        "rest_mdd": r_perf["mdd"],
        "all_mdd": a_perf["mdd"],
        "rel_mdd": rel_mdd,
        "tail_dropped": tail_d,
        "tail_rest": tail_r,
        "excess_mean": float(diff.mean()) if len(diff) else np.nan,
        "excess_t": diff_t,
        "excess_ci": diff_ci,
        "improvements": improvements,
        "useful": useful,
    }
