"""因子研究报告的指标计算层。

口径约定（与回测一致）
---------------------
- **可交易口径**：T 日收盘出信号，**T+1 开盘价买入**，持有 h 日后收盘卖出；
  即 ``fwd_h = close[t+h] / open[t+1] - 1``。绝不用 close-to-close，
  否则反转/涨停类因子会被系统性高估。
- **IC**：逐日横截面 Spearman（秩相关），不做全样本统计。
- **t 值**：Newey-West 调整（lag = h-1）处理重叠样本；同时给出**非重叠**
  子样本 t 值（每 h 个交易日取一个观测），避免 NW 调整在小样本下过度自信。
- **中性化**：横截面回归剔除对数市值与行业哑变量后的残差 IC。
- **净值**：分组等权、非重叠调仓、T+1 开盘买入；成本按换手率×双边费率扣除。

本模块只做报告编排与产品层逻辑；指标算法统一来自 ``factor_metrics``
（由本模块的既有实现抽取而来，保持 research 校准口径不变）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from factor_metrics import (
    RESEARCH_SPEC,
    TRADING_DAYS,
    QuantileResult,
    forward_returns,
    newey_west_t,
    independent_t,
    block_bootstrap_ci,
    _corr_p,
    _zscore,
    _rank_ic_frame,
    _spearman_ic,
    _winsorize,
    _winsorize_frame,
    _winsor_group_returns,
    board_limit_pct,
    _limit_matrix,
    tradeable_mask,
    _delayed_exit,
    _quantile_nav,
    neutralize_panel,
    neutralized_ic,
    neutralize_factor,
    _as_industry_frame,
    _stratified_ic,
    _performance,
    _period_performance,
    _annual_returns,
    _annual_ic_return,
    _ls_stats,
    _regime_table,
    _decay_note,
    _capacity,
    _filter_test,
    _filter_test_side,
    _incremental,
    _tradeability,
    _sell_side_summary,
    _cost_sensitivity,
)


from . import diagnostics as diag
from .data_quality import CleaningStandard, analyze as analyze_quality

logger = logging.getLogger("Research.Report.Metrics")


_LS_COST = 0.002

_LS_STARTS = ("2022-01-01", "2023-01-01", "2024-01-01", "2025-01-01")

_LS_INDEX = ((5e10, "000300"), (1e10, "000905"), (0.0, "000852"))


@dataclass
class FactorReport:
    factor_id: str
    category: str
    direction: int
    universe: str
    start: pd.Timestamp
    end: pd.Timestamp
    n_symbols: int
    n_dates: int
    coverage: float
    horizons: tuple[int, ...]

    ic: dict[int, pd.Series] = field(default_factory=dict)
    ic_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling_ic: pd.Series | None = None
    ic_by_year: pd.Series | None = None
    ic_by_year_ci: pd.DataFrame | None = None
    ic_by_month: pd.Series | None = None
    ic_by_month_ci: pd.DataFrame | None = None
    ic_by_regime: pd.Series | None = None
    regime_stats: pd.DataFrame | None = None
    regime_stats_alt: pd.DataFrame | None = None
    regime_share: pd.DataFrame | None = None
    regime_corr: float = float("nan")
    regime_corr_p: float = float("nan")
    decay: pd.Series | None = None
    decay_note: str = ""

    nav: pd.DataFrame | None = None
    period_returns: pd.DataFrame | None = None
    period_returns_gross: pd.DataFrame | None = None
    period_returns_net: pd.DataFrame | None = None
    daily_returns: pd.DataFrame | None = None
    winsor: dict = field(default_factory=dict)
    return_winsor: dict = field(default_factory=dict)
    tail_specificity: dict = field(default_factory=dict)
    quantile_returns: dict[str, float] = field(default_factory=dict)
    quantile_returns_gross: dict[str, float] = field(default_factory=dict)
    quantile_ci: dict[str, tuple[float, float]] = field(default_factory=dict)
    monotonicity: float = float("nan")
    monotonicity_net: float = float("nan")
    monotonicity_p: float = float("nan")
    spread_gross: dict = field(default_factory=dict)
    spread_net: dict = field(default_factory=dict)
    cost_mean: float = float("nan")
    sigma_5d: float = float("nan")
    transmission: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    direction_source: str = "registry"
    ic_half: dict = field(default_factory=dict)
    reproduction: dict = field(default_factory=dict)
    long_short: dict[str, float] = field(default_factory=dict)
    ls_nav: pd.Series | None = None
    ls_daily: pd.Series | None = None
    drawdown: pd.Series | None = None
    perf: dict = field(default_factory=dict)
    annual_returns: pd.Series | None = None
    benchmark_nav: pd.Series | None = None
    turnover: float = float("nan")
    turnover_by_group: pd.Series | None = None

    filter_test: dict = field(default_factory=dict)
    filter_test_opposite: dict = field(default_factory=dict)
    strat_ic: object | None = None
    size_ranges: dict | None = None
    horizon_spreads: object | None = None
    annual_ic_return: pd.DataFrame | None = None
    metrics_engine: dict[str, str] = field(default_factory=dict)

    ic_neutral: pd.DataFrame | None = None
    ic_by_size: pd.Series | None = None
    ic_by_industry: pd.Series | None = None
    industry_neutral_ic: float = float("nan")
    autocorr: float = float("nan")

    limit_up: dict = field(default_factory=dict)
    tradeability: dict = field(default_factory=dict)
    sell_side: dict = field(default_factory=dict)
    cost_table: pd.DataFrame | None = None
    capacity: dict = field(default_factory=dict)
    incrementality: dict = field(default_factory=dict)
    peers: pd.Series | None = None

    definition: dict = field(default_factory=dict)
    factor_stats: dict = field(default_factory=dict)
    data_quality: dict = field(default_factory=dict)
    pool: dict = field(default_factory=dict)
    pool_robustness: pd.DataFrame | None = None
    sensitivity: list[dict] = field(default_factory=list)
    freshness: dict = field(default_factory=dict)
    raw_direction: int = 1
    flipped: bool = False
    rating: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics_spec: dict = field(default_factory=dict)


def build_report(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    factor_id: str,
    category: str = "momentum",
    direction: int = 1,
    universe: str = "csi800",
    horizons: tuple[int, ...] = (1, 5, 10, 20, 60),
    primary_horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    regime: pd.Series | None = None,
    regime_alt: pd.Series | None = None,
    market_cap: pd.DataFrame | None = None,
    industry: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    low: pd.DataFrame | None = None,
    cleaning: CleaningStandard | None = None,
    benchmark: pd.Series | None = None,
    peers: dict[str, pd.DataFrame] | None = None,
    definition: dict | None = None,
    pool: dict | None = None,
    neutralize: bool = False,
    freshness: dict | None = None,
    is_st: dict[str, bool] | None = None,
    exclude_limit_up: bool = False,
    direction_source: str = "registry",
    full: bool = True,
    sections: tuple[str, ...] | None = None,
) -> FactorReport:
    sections = tuple(sections or ("definition", "timing", "cross_section", "rating", "appendix"))
    need_cross_extra = "cross_section" in sections
    need_rating = "rating" in sections
    need_cross_core = need_cross_extra or need_rating
    factor = factor.sort_index()
    # 因子面板可能比行情短（预热期被裁掉）。统一裁到因子日期，避免
    # 可成交分母、成本、容量把预热期算进去，也避免 fwd 跨到评估起点之前。
    if not factor.index.equals(close.index):
        close = close.reindex(factor.index)
        open_ = open_.reindex(factor.index)
    # 有效起点：横截面至少 20%（且不少于 30 只）有因子值。财务 value 因子
    # 2023 才有数据，但面板从 2019 开始；不裁会让 report.start/n_dates/覆盖率
    # 都错报成 2019 起、44% 覆盖，并误触发覆盖率否决。
    counts = factor.notna().sum(axis=1)
    min_names = max(30, int(0.2 * factor.shape[1]))
    usable = counts[counts >= min_names]
    if len(usable):
        effective_start = usable.index.min()
        factor = factor.loc[factor.index >= effective_start]
        close = close.loc[close.index >= effective_start]
        open_ = open_.loc[open_.index >= effective_start]
    fwd = forward_returns(close, open_, horizons)
    mask = tradeable_mask(close, open_, is_st=is_st)

    report = FactorReport(
        factor_id=factor_id,
        category=category,
        direction=direction,
        universe=universe,
        start=factor.index.min(),
        end=factor.index.max(),
        n_symbols=int(factor.shape[1]),
        n_dates=int(factor.shape[0]),
        coverage=float(factor.notna().sum().sum() / max(1, factor.size)),
        horizons=horizons,
        definition=definition or {},
        pool=pool or {},
        freshness=freshness or {},
        raw_direction=direction,
    )
    from .metrics_backend import engine_metadata

    report.metrics_engine = engine_metadata(factor.shape)
    report.metrics_spec = {
        **RESEARCH_SPEC.to_dict(),
        "config_hash": RESEARCH_SPEC.config_hash(),
    }

    quality = None
    if cleaning is not None:
        quality = analyze_quality(
            factor, close, open_, low=low, amount=amount,
            standard=cleaning, horizon=primary_horizon,
        )
        report.data_quality = quality.summary
        report.n_symbols = int(quality.summary["universe_symbols"])
        report.coverage = float(
            quality.summary["coverage"].get("valid_signal_ratio",
                                            quality.summary["coverage"]["valid_signal"])
        )
        report.universe = f"{universe} · {cleaning.label}"

    # ---- 方向：唯一入口。
    # 因子值本身不取负；分析信号 = raw × direction。**不做样本内翻转**：
    # 在同一样本上按 IC 符号选方向，等价于用未来信息择向，会把随机因子的 IC
    # 系统性抬成正数（旧版 4/4 强因子 IC>0、ρ 与价差打架，根因就在这里）。
    if direction not in (-1, 1):
        raise ValueError(f"direction 必须是 +1/-1，收到 {direction!r}")
    industry = _as_industry_frame(industry, factor)
    analysis_factor = factor * int(direction)
    if quality is not None:
        analysis_factor = analysis_factor.where(quality.mask)
    if neutralize:
        analysis_factor = neutralize_factor(analysis_factor, market_cap)
        report.neutralized = True
    report.direction = int(direction)
    report.raw_direction = int(direction)
    report.direction_source = direction_source
    report.flipped = False
    factor_rank = analysis_factor.rank(axis=1)

    valid_values = factor.to_numpy(dtype="float64")
    finite = valid_values[np.isfinite(valid_values)]
    if len(finite):
        report.factor_stats = {
            "median": float(np.median(finite)),
            "p01": float(np.quantile(finite, 0.01)),
            "p99": float(np.quantile(finite, 0.99)),
            "skew": float(pd.Series(finite).skew()),
            "min": float(finite.min()),
            "max": float(finite.max()),
            "missing": float(1 - len(finite) / valid_values.size),
            "n": int(len(finite)),
        }
    # 可成交过滤：t+1 开盘一字涨停/停牌的样本不可买入，IC 与分层都必须剔除
    fwd_raw = dict(fwd)
    if exclude_limit_up:
        fwd = {h: v.where(mask) for h, v in fwd.items()}
    report.limit_up = {
        "applied": bool(exclude_limit_up),
        "excluded_ratio": float((~mask).to_numpy().mean()),
        "ic_raw": float(_rank_ic_frame(analysis_factor, fwd_raw[primary_horizon],
                                       factor_rank=factor_rank).mean()),
        "ic_masked": float(_rank_ic_frame(analysis_factor, fwd[primary_horizon],
                                          factor_rank=factor_rank).mean()),
    }

    # 横截面收益离散度：IC 传导到 Q5−Q1 的分母（报告里不再写死 7%）
    sigma_series = fwd[primary_horizon].std(axis=1, skipna=True)
    sigma_series = sigma_series[fwd[primary_horizon].notna().sum(axis=1) >= 30]
    report.sigma_5d = float(sigma_series.mean()) if len(sigma_series) else float("nan")

    rows = []
    for h in horizons:
        series = _rank_ic_frame(analysis_factor, fwd[h], factor_rank=factor_rank)
        report.ic[h] = series
        if series.empty:
            continue
        icir = series.mean() / (series.std() or np.nan) * np.sqrt(TRADING_DAYS / h)
        t_indep, n_indep = independent_t(series, step=h)
        lo, hi = block_bootstrap_ci(series, block=max(5, h))
        rows.append(
            {
                "h": h,
                "mean": series.mean(),
                "std": series.std(),
                "ir": icir,
                "t": newey_west_t(series, lag=max(1, h - 1)),
                "t_indep": t_indep,
                "n_indep": n_indep,
                "ci_lo": lo,
                "ci_hi": hi,
                "win": float((series > 0).mean()),
                "n": int(len(series)),
            }
        )
    report.ic_stats = pd.DataFrame(rows).set_index("h") if rows else pd.DataFrame()

    primary = report.ic.get(primary_horizon, pd.Series(dtype="float64"))
    if not primary.empty:
        window = max(20, min(60, len(primary) // 6 or 20))
        report.rolling_ic = primary.rolling(window, min_periods=max(5, window // 3)).mean()
        report.ic_by_year = primary.groupby(primary.index.year).mean()
        report.ic_by_month = primary.groupby(primary.index.month).mean()
        half = len(primary) // 2
        if half >= 20:
            first, second = primary.iloc[:half], primary.iloc[half:]
            report.ic_half = {
                "first": float(first.mean()),
                "second": float(second.mean()),
                "first_n": int(len(first)),
                "second_n": int(len(second)),
                "same_sign": bool(np.sign(first.mean()) == np.sign(second.mean())),
                "first_end": str(first.index.max().date()),
                "second_start": str(second.index.min().date()),
            }
        if regime is not None:
            aligned = regime.reindex(primary.index).dropna()
            if not aligned.empty:
                report.ic_by_regime = primary.reindex(aligned.index).groupby(aligned).mean()
                report.regime_stats = _regime_table(primary, regime)
                if regime_alt is not None:
                    alt = regime_alt.reindex(primary.index).dropna()
                    if not alt.empty:
                        report.regime_stats_alt = _regime_table(primary, regime_alt)
                tmp = pd.DataFrame({"ic": primary, "regime": regime.reindex(primary.index)}).dropna()
                if not tmp.empty:
                    report.regime_share = (
                        tmp.groupby([tmp.index.year, "regime"]).size().unstack(fill_value=0)
                    )
                    report.regime_share = report.regime_share.div(
                        report.regime_share.sum(axis=1).replace(0, np.nan), axis=0
                    )
                    if "bull" in report.regime_share.columns and report.ic_by_year is not None:
                        merged = pd.concat(
                            [report.ic_by_year.rename("ic"), report.regime_share["bull"].rename("bull")],
                            axis=1,
                        ).dropna()
                        if len(merged) >= 4 and merged["bull"].nunique() > 1:
                            report.regime_corr = float(merged["ic"].corr(merged["bull"]))
                            report.regime_corr_p = _corr_p(report.regime_corr, len(merged))
        report.decay = pd.Series({h: s.mean() for h, s in report.ic.items()})
        report.decay_note = _decay_note(report.decay)

        year_rows = {}
        for year, group in primary.groupby(primary.index.year):
            lo, hi = block_bootstrap_ci(group, block=5)
            year_rows[int(year)] = {
                "mean": float(group.mean()),
                "n": int(len(group)),
                "ci_lo": lo,
                "ci_hi": hi,
            }
        report.ic_by_year_ci = pd.DataFrame(year_rows).T

        month_rows = {}
        for month, group in primary.groupby(primary.index.month):
            lo, hi = block_bootstrap_ci(group, block=5)
            month_rows[int(month)] = {
                "mean": float(group.mean()),
                "n": int(len(group)),
                "ci_lo": lo,
                "ci_hi": hi,
            }
        report.ic_by_month_ci = pd.DataFrame(month_rows).T

    quantile = _quantile_nav(
        analysis_factor, close, open_, groups=groups, horizon=primary_horizon, cost_bps=cost_bps,
        tradeable=mask if exclude_limit_up else None,
    )
    nav, turnover_df, period_returns, period_gross, daily, ls_cost, turnover_mean = quantile
    try:
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("ignore", RuntimeWarning)
            _ret5 = close.pct_change(5, fill_method=None)
            _stds = np.nanstd(_ret5.to_numpy(dtype="float64"), axis=1)
        _stds = _stds[np.isfinite(_stds) & (_stds > 0)]
        report.cross_section_std = float(_stds.mean()) if _stds.size else float("nan")
    except Exception:  # noqa: BLE001
        report.cross_section_std = float("nan")
    # 分层 IC：流动性 / 市值 / 行业，全部在已加载宽表上切片
    try:
        _strata: dict[str, pd.DataFrame] = {}
        if amount is not None:
            _ar = amount.reindex(index=analysis_factor.index, columns=analysis_factor.columns).rank(
                axis=1, pct=True)
            _strata["流动性 L1 后40%"] = _ar <= 0.4
            _strata["流动性 L2 中20%"] = (_ar > 0.4) & (_ar <= 0.6)
            _strata["流动性 L3 前40%"] = _ar > 0.6
        if market_cap is not None:
            _mr = market_cap.reindex(index=analysis_factor.index, columns=analysis_factor.columns).rank(
                axis=1, pct=True)
            _ranges: dict[str, tuple[float, float]] = {}
            _mc_yi = market_cap.reindex(index=analysis_factor.index,
                                        columns=analysis_factor.columns) / 1e8
            for i, label in enumerate(("市值 S1 最小", "市值 S2", "市值 S3", "市值 S4", "市值 S5 最大")):
                lo, hi = i / 5, (i + 1) / 5
                _mask = (_mr > lo) & (_mr <= hi) if i else (_mr <= hi)
                _strata[label] = _mask
                _vals = _mc_yi.where(_mask).stack().dropna()
                if len(_vals) > 100:
                    _ranges[label] = (float(_vals.quantile(0.05)), float(_vals.quantile(0.95)))
            report.size_ranges = _ranges
        if industry is not None:
            _map = industry
            if isinstance(industry, pd.DataFrame) and "symbol" in industry.columns:
                _map = industry.set_index("symbol")["industry"]
            elif isinstance(industry, pd.DataFrame):
                _map = industry.iloc[-1]
            if isinstance(_map, pd.Series):
                # 按成分股数量从多到少取前 12 个行业（原来按 unique 顺序取，
                # 前 12 个可能都是小行业，导致全部被 30 只的门槛过滤掉）
                _counts = _map.reindex(analysis_factor.columns).dropna().value_counts()
                for name in _counts[_counts >= 30].index[:12]:
                    cols = [c for c in analysis_factor.columns if _map.get(c) == name]
                    if len(cols) >= 30:
                        _strata[f"行业 {name}"] = pd.DataFrame(
                            False, index=analysis_factor.index, columns=analysis_factor.columns)
                        _strata[f"行业 {name}"][cols] = True
        if _strata:
            report.strat_ic = _stratified_ic(analysis_factor, fwd[primary_horizon], _strata)
    except Exception:  # noqa: BLE001
        report.strat_ic = None
    report.quantile_factor = analysis_factor
    # 复现自检必须复用同一组参数，否则比较的是两次不同设定，永远"不一致"
    report.quantile_args = {
        "groups": groups,
        "horizon": primary_horizon,
        "cost_bps": cost_bps,
        "tradeable": mask if exclude_limit_up else None,
    }
    report.nav = nav
    report.daily_returns = daily
    report.period_returns = period_returns
    report.period_returns_gross = period_gross
    report.turnover_by_group = turnover_mean
    report.period_returns_net = period_returns
    if not period_returns.empty and not period_gross.empty:
        # 分层优劣用「每期平均收益」，不用复利终值（避免波动拖累污染单调性）。
        # 单调性用**毛收益**：净收益被各组换手差异污染，会把方向判断带偏。
        means_net = period_returns.mean()
        means_gross = period_gross.mean()
        report.quantile_returns = {str(k): float(v) for k, v in means_net.items()}
        report.quantile_returns_gross = {str(k): float(v) for k, v in means_gross.items()}
        report.quantile_ci = {}
        for col in period_returns.columns:
            series = period_returns[col].dropna()
            if len(series) >= 10:
                se = series.std(ddof=1) / np.sqrt(len(series))
                report.quantile_ci[col] = (float(series.mean() - 1.96 * se), float(series.mean() + 1.96 * se))
        if len(means_gross) >= 3:
            report.monotonicity, report.monotonicity_p = diag.group_monotonicity(means_gross)
            report.monotonicity_net, _ = diag.group_monotonicity(means_net)

        q_high, q_low = f"Q{groups}", "Q1"
        # 顶档/底档特异度：给“单尾断崖”一个独立于 Spearman 的表达。
        try:
            _mid_labels = [f"Q{i}" for i in range(2, max(2, groups))]
            _mid = float(means_gross.reindex(_mid_labels).median())
            _upper = float(means_gross[q_high] - _mid)
            _lower = float(means_gross[q_low] - _mid)
            _denom = abs(_upper) + abs(_lower)
            _ratio = max(abs(_upper), abs(_lower)) / _denom if _denom > 1e-12 else 0.0
            _upper_diff = (period_gross[q_high] - period_gross[_mid_labels].mean(axis=1)).dropna()
            _lower_diff = (period_gross[q_low] - period_gross[[c for c in period_gross.columns if c != q_low]].mean(axis=1)).dropna()
            def _t_of(series: pd.Series) -> float:
                if len(series) < 3 or series.std(ddof=1) == 0:
                    return float("nan")
                return float(series.mean() / (series.std(ddof=1) / np.sqrt(len(series))))
            _tu, _tl = _t_of(_upper_diff), _t_of(_lower_diff)
            _tt = max(abs(_tu), abs(_tl))
            report.tail_specificity = {
                "ratio": float(_ratio),
                "upper": _upper,
                "lower": _lower,
                "mid": _mid,
                "upper_t": _tu,
                "lower_t": _tl,
                "t": _tt if np.isfinite(_tt) else 0.0,
                "score": float(np.clip(_ratio * np.clip(abs(_tt) / 2.0, 0, 1), 0, 1))
                if np.isfinite(_tt) else 0.0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("顶档特异度计算失败: %s", exc)
            report.tail_specificity = {}
        gross_spread = (period_gross[q_high] - period_gross[q_low]).dropna()
        cost_series = (
            (turnover_df.get(q_high, pd.Series(dtype="float"))
             + turnover_df.get(q_low, pd.Series(dtype="float"))) * cost_bps / 10000.0
        ).dropna()
        # 多空净价差 = 毛价差 − 两条腿的换手成本。不能用 Q5_net − Q1_net：
        # 两条腿成本相同会互相抵消，等于漏算空头腿成本。
        net_spread = (gross_spread - cost_series).dropna()
        report.spread_gross = diag.spread_stats(gross_spread)
        report.spread_net = diag.spread_stats(net_spread)
        report.cost_mean = float(cost_series.mean()) if len(cost_series) else float("nan")

        # 去极值敏感性：1%/99% 横截面缩尾，回答“G5 断崖是不是极端值造成”
        try:
            _wf = _winsorize_frame(analysis_factor, 0.01, 0.99)
            _wq = _quantile_nav(
                _wf, close, open_, groups=groups, horizon=primary_horizon,
                cost_bps=cost_bps,
            )
            _, _wto, _wpnet, _wpgross, _, _, _ = _wq
            _wg = _wpgross.mean()
            _wn = _wpnet.mean()
            _wcost = (
                (_wto.get(q_high, pd.Series(dtype="float64"))
                 + _wto.get(q_low, pd.Series(dtype="float64")))
                * cost_bps / 10000.0
            ).dropna()
            _wls = (_wpgross[q_high] - _wpgross[q_low] - _wcost).dropna()
            report.winsor = {
                "g1": float(_wg.get(q_low, np.nan)),
                "g5": float(_wg.get(q_high, np.nan)),
                "spread_gross": float(_wg.get(q_high, np.nan) - _wg.get(q_low, np.nan)),
                "spread_net": float(_wls.mean()) if len(_wls) else np.nan,
                "spread_net_t": float(newey_west_t(_wls, lag=max(1, primary_horizon - 1)))
                if len(_wls) > 2 else np.nan,
                "n_periods": int(len(_wpgross)),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("去极值敏感性计算失败: %s", exc)
            report.winsor = {}

        try:
            report.return_winsor = _winsor_group_returns(
                analysis_factor, fwd[primary_horizon],
                groups=groups, horizon=primary_horizon,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("收益缩尾敏感性计算失败: %s", exc)
            report.return_winsor = {}

        # 多持有期对比（5/10/20 日）＋ 10 日市值中性化版本
        _hs_rows = []
        _results = {}
        for _h in (5, 10, 20, 60):
            try:
                if _h == primary_horizon:
                    _g, _n = period_gross, period_returns
                    _to = float(turnover_df.mean().mean()) if len(turnover_df) else float("nan")
                else:
                    _nav_h, _to_h, _pr_h, _pg_h, _daily_h, _lsc_h, _tmean = _quantile_nav(
                        analysis_factor, close, open_, groups=groups, horizon=_h,
                        cost_bps=cost_bps,
                        tradeable=mask if exclude_limit_up else None,
                    )
                    _g, _n = _pg_h, _pr_h
                    _to = float(_to_h.mean().mean()) if len(_to_h) else float("nan")
                _hi, _lo = f"Q{groups}", "Q1"
                _gs = float((_g[_hi] - _g[_lo]).mean())
                _ns = float((_n[_hi] - _n[_lo]).mean())
                _hs_rows.append({
                    "horizon": _h, "variant": "原始",
                    "gross": _gs, "net": _ns, "turnover": _to,
                })
                _results[_h] = (_gs, _ns)
            except Exception as exc:  # noqa: BLE001
                logger.warning("持有期 %s 计算失败: %s", _h, exc)

        # 10 日 + 市值中性化
        try:
            _neu = neutralize_factor(analysis_factor, market_cap)
            _nav_n, _to_n, _pr_n, _pg_n, _d_n, _l_n, _tm_n = _quantile_nav(
                _neu, close, open_, groups=groups, horizon=10, cost_bps=cost_bps,
                tradeable=mask if exclude_limit_up else None,
            )
            _hi, _lo = f"Q{groups}", "Q1"
            _hs_rows.append({
                "horizon": 10, "variant": "市值中性",
                "gross": float((_pg_n[_hi] - _pg_n[_lo]).mean()),
                "net": float((_pr_n[_hi] - _pr_n[_lo]).mean()),
                "turnover": float(_to_n.mean().mean()) if len(_to_n) else float("nan"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("10 日中性化对比失败: %s", exc)

        if need_cross_extra:
            report.horizon_spreads = pd.DataFrame(_hs_rows)
        if need_cross_core:
            report.filter_test = _filter_test(period_returns, primary_horizon)
            report.filter_test_opposite = _filter_test_side(
                period_returns, primary_horizon, f"Q{groups}"
            )
        if len(net_spread) > 10:
            ls_nav = (1 + net_spread).cumprod()
            report.ls_nav = ls_nav
            report.ls_daily = net_spread
            report.drawdown = ls_nav / ls_nav.cummax() - 1
            report.perf = _period_performance(net_spread, primary_horizon)
            report.annual_returns = _annual_returns(ls_nav)
            if need_cross_extra:
                report.annual_ic_return = _annual_ic_return(report.ic_by_year, report.annual_returns)
            report.long_short = {
                "total": report.perf.get("total", np.nan),
                "ann": report.perf.get("ann", np.nan),
                "sharpe": report.perf.get("sharpe", np.nan),
                "mdd": report.perf.get("mdd", np.nan),
                "calmar": report.perf.get("calmar", np.nan),
                "win": report.perf.get("win", np.nan),
                "geo_period": report.perf.get("ann", np.nan),
                "ls_spread_mean": float(net_spread.mean()),
                "gross_spread_mean": report.spread_gross["mean"],
                "cost_mean": report.cost_mean,
                "net_spread_mean": report.spread_net["mean"],
                "spread_t": report.spread_net["t"],
                "spread_gross_t": report.spread_gross["t"],
            }
            if report.filter_test:
                report.filter_test["spread_mean"] = float(net_spread.mean())
                report.filter_test["spread_se"] = report.spread_net["se"]
                report.filter_test["spread_gross_mean"] = report.spread_gross["mean"]
                report.filter_test["spread_gross_se"] = report.spread_gross["se"]
    report.turnover = (
        float(turnover_mean.get(f"Q{groups}", turnover_mean.mean()))
        if not turnover_mean.empty else float("nan")
    )
    if quality is not None:
        report.data_quality["clean"] = {
            "g1": float(report.quantile_returns_gross.get("Q1", np.nan)),
            "g5": float(report.quantile_returns_gross.get(f"Q{groups}", np.nan)),
            "spread_gross": float((report.spread_gross or {}).get("mean", np.nan)),
            "spread_net": float((report.spread_net or {}).get("mean", np.nan)),
            "spread_net_t": float((report.spread_net or {}).get("t", np.nan)),
            "monotonicity": float(report.monotonicity),
            "turnover": float(report.turnover),
            "n_periods": int(len(report.period_returns)) if report.period_returns is not None else 0,
        }
    if benchmark is not None and not benchmark.empty:
        bench = benchmark.reindex(close.index).ffill().dropna()
        if not bench.empty:
            report.benchmark_nav = bench / bench.iloc[0]

    if need_cross_extra and (market_cap is not None or industry is not None):
        report.ic_neutral = neutralized_ic(
            analysis_factor, fwd[primary_horizon], market_cap, industry
        )
        if industry is not None:
            ind = report.ic_neutral.loc["行业中性", "mean"] if "行业中性" in report.ic_neutral.index else np.nan
            report.industry_neutral_ic = float(ind)

    if need_cross_extra and market_cap is not None:
        ic_by_size = {}
        caps = market_cap.reindex(index=factor.index, columns=factor.columns)
        for label, lo, hi in (("小市值", 0.0, 1 / 3), ("中市值", 1 / 3, 2 / 3), ("大市值", 2 / 3, 1.0)):
            rank = caps.rank(axis=1, pct=True)
            masked = analysis_factor.where((rank >= lo) & (rank <= hi))
            series = _rank_ic_frame(masked, fwd[primary_horizon])
            if len(series) > 20:
                ic_by_size[label] = float(series.mean())
        report.ic_by_size = pd.Series(ic_by_size) if ic_by_size else None

    if need_cross_extra and industry is not None:
        industry = industry.reindex(index=factor.index, columns=factor.columns)
        ic_by_ind = {}
        for name in pd.unique(industry.iloc[-1].dropna()):
            if str(name) in ("未知", "未分类"):
                continue
            mask_ind = industry == name
            masked = analysis_factor.where(mask_ind)
            series = _rank_ic_frame(masked, fwd[primary_horizon], min_names=8)
            if len(series) > 20:
                ic_by_ind[str(name)] = float(series.mean())
        if ic_by_ind:
            report.ic_by_industry = pd.Series(ic_by_ind).sort_values(ascending=False)

    if need_cross_extra:
        report.tradeability = _tradeability(
            analysis_factor, close, open_, horizon=primary_horizon, is_st=is_st
        )
    if full and need_cross_extra:
        report.sell_side = _sell_side_summary(
            analysis_factor, close, open_, fwd[primary_horizon], horizon=primary_horizon, groups=groups, is_st=is_st
        )
        report.cost_table = _cost_sensitivity(
            analysis_factor, close, open_, groups=groups, horizon=primary_horizon,
            tradeable=mask if exclude_limit_up else None, quantile=quantile,
        )
        report.capacity = _capacity(amount, analysis_factor, primary_horizon, groups) if amount is not None else {}
    if full and need_cross_core:
        inc, corr = _incremental(analysis_factor, fwd[primary_horizon], peers)
        report.incrementality = inc
        report.peers = corr

    if report.ic_by_regime is not None and len(report.ic_by_regime) > 1:
        if report.ic_by_regime.min() < 0 < report.ic_by_regime.max():
            report.warnings.append(
                "因子在部分市场状态下 IC 为负（"
                + "、".join(f"{k}:{v:+.3f}" for k, v in report.ic_by_regime.items())
                + "），存在状态依赖"
            )
    if report.coverage < 0.7:
        report.warnings.append(f"覆盖率仅 {report.coverage:.0%}（< 70%），横截面代表性不足")
    if report.turnover == report.turnover and report.turnover > 0.6:
        report.warnings.append(f"Top 组换手 {report.turnover:.0%} 偏高，成本敏感")
    if report.perf.get("mdd", 0) < -0.40:
        report.warnings.append(
            f"多空最大回撤 {report.perf['mdd']:.1%}，波动极大；需按回撤预算决定是否可用"
        )

    # ---- 结构化自检：结论必须能被程序复核，而不是只写在正文里 ----
    ic5 = float(report.ic_stats.loc[primary_horizon, "mean"]) if primary_horizon in report.ic_stats.index else float("nan")
    t5 = float(report.ic_stats.loc[primary_horizon, "t_indep"]) if primary_horizon in report.ic_stats.index else float("nan")
    if not np.isfinite(t5):
        t5 = float(report.ic_stats.loc[primary_horizon, "t"]) if primary_horizon in report.ic_stats.index else float("nan")
    gross = report.spread_gross or {}
    report.transmission = diag.ic_spread_transmission(ic5, report.sigma_5d, gross.get("mean", np.nan)).as_dict()
    checks = [
        diag.rho_spread_identity(report.monotonicity, gross.get("mean", np.nan), groups),
        diag.ic_spread_transmission(ic5, report.sigma_5d, gross.get("mean", np.nan)),
        diag.tradeability_interval(gross, report.cost_mean),
        diag.direction_prior(ic5, t5, report.direction),
    ]
    _repro = getattr(report, "_repro_check", None)
    report.diagnostics = list(checks) + ([_repro] if _repro is not None else [])
    report.attribution = diag.attribute_failure(report)
    for check in checks:
        if check.status == "fail":
            report.warnings.append(f"自检失败[{check.check_id}]：{check.actual}")
        elif check.status == "warn":
            report.warnings.append(f"自检提示[{check.check_id}]：{check.actual}")
    # 标题/样本区间按“第一个有有效 IC 的交易日”收口，避免 252 日因子把 2017 写成样本期。
    _ic5 = (report.ic or {}).get(5)
    if _ic5 is not None and len(_ic5):
        _first_ic = _ic5.first_valid_index()
        if _first_ic is not None:
            report.start = max(report.start, _first_ic)
    return report


def _norm_code(c) -> str:
    c = str(c).strip()
    if c.endswith(".0"):
        c = c[:-2]
    return c.zfill(6) if c.isdigit() else c


def _index_series(panel, code):
    try:
        from research.data.loader import _query

        df = _query(f"select ts, close from ohlcv_1d where code='{code}' and type='idx' order by ts")
        if df is None or df.empty:
            return None
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        return df.set_index("ts")["close"].astype("float64").reindex(panel.close.index).ffill()
    except Exception:  # noqa: BLE001
        return None


def _industry_lookup(panel):
    try:
        from research.data.loader import _query

        df = _query(
            "select code, last(industry) as name from stock_basic_info "
            "where industry is not null group by code")
        if df is not None and not df.empty:
            return {_norm_code(c): str(n) for c, n in zip(df["code"], df["name"])}
    except Exception:  # noqa: BLE001
        pass
    imap = panel.industry
    if isinstance(imap, pd.DataFrame):
        imap = imap.iloc[-1]
    if imap is None:
        return {}
    return {_norm_code(k): str(v) for k, v in imap.items()}


def _ls_periods(factor, panel, direction, horizon, groups):
    sig = (factor * int(direction)).reindex(index=panel.close.index, columns=panel.close.columns)
    close, open_ = panel.close, panel.open
    out = []
    for i in range(0, len(close) - horizon - 2, horizon):
        entry, exit_ = open_.iloc[i + 1], close.iloc[i + horizon]
        ret = (exit_ / entry - 1.0).replace([np.inf, -np.inf], np.nan)
        s = sig.iloc[i]
        ok = s.notna() & ret.notna()
        if ok.sum() < 200:
            continue
        q = pd.qcut(s[ok].rank(method="first"), groups, labels=False)
        top = ret[ok][q == groups - 1].index
        out.append({"i": i, "top": top, "ret": ret[ok],
                    "r_top": float(ret[top].mean()) - _LS_COST, "date": close.index[i]})
    return out


def long_only_stats(factor, panel, direction, horizon=20, groups=5) -> dict:
    per = _ls_periods(factor, panel, direction, horizon, groups)
    if len(per) < 12:
        return {}

    cap = panel.market_cap
    amount = panel.amount
    ind_map = _industry_lookup(panel)

    # 每期持仓的市值中位数 → 决定指数基准
    med_caps = []
    for p in per:
        if cap is not None:
            v = cap.iloc[p["i"]][p["top"]]
            med_caps.append(float(np.nanmedian(v)) if len(v) else np.nan)
    med_cap = float(np.nanmedian(med_caps)) if med_caps else np.nan
    idx_code = next(c for th, c in _LS_INDEX if med_cap > th) if med_cap == med_cap else "000905"
    idx = _index_series(panel, idx_code)

    r_top, r_ew, r_idx, r_style = [], [], [], []
    for p in per:
        r_top.append(p["r_top"])
        r_ew.append(float(p["ret"].mean()))
        i = p["i"]
        if idx is not None and np.isfinite(idx.iloc[i + horizon]) and np.isfinite(idx.iloc[i + 1]):
            r_idx.append(float(idx.iloc[i + horizon] / idx.iloc[i + 1] - 1))
        else:
            r_idx.append(float(p["ret"].mean()))
        # 风格基准：按持仓的市值分桶比例，从全市场同桶里取等权收益
        if cap is not None:
            allv = cap.iloc[i].reindex(p["ret"].index)
            topv = allv.reindex(p["top"])
            bins = pd.cut(topv, [-np.inf, 5e9, 1e10, 5e10, np.inf])
            allbins = pd.cut(allv, [-np.inf, 5e9, 1e10, 5e10, np.inf])
            w = bins.value_counts(normalize=True)
            val, wsum = 0.0, 0.0
            for b, wt in w.items():
                sub = p["ret"][allbins == b]
                if len(sub) > 20:
                    val += float(sub.mean()) * float(wt)
                    wsum += float(wt)
            r_style.append(val / wsum if wsum > 0 else float(p["ret"].mean()))
        else:
            r_style.append(float(p["ret"].mean()))

    dates = [p["date"] for p in per]
    ppy = TRADING_DAYS / max(1, int(horizon))
    res = {
        "periods": len(per),
        "holdings": int(np.median([len(p["top"]) for p in per])),
        "index_code": idx_code,
        "median_cap": med_cap,
        "ew": _ls_stats(r_top, r_ew, periods_per_year=ppy),
        "index": _ls_stats(r_top, r_idx, periods_per_year=ppy),
        "style": _ls_stats(r_top, r_style, periods_per_year=ppy),
        "capacity": float(np.nanmedian([float(amount.iloc[p["i"]][p["top"]].median()) for p in per])) * 0.05 * int(np.median([len(p["top"]) for p in per]))
        if amount is not None else np.nan,
        "oos": [],
    }

    for st in _LS_STARTS:
        mask = [d >= pd.Timestamp(st) for d in dates]
        if sum(mask) < 12:
            continue
        sub = lambda arr: [v for v, m in zip(arr, mask) if m]
        s = _ls_stats(sub(r_top), sub(r_ew), periods_per_year=ppy)
        s["start"] = st
        res["oos"].append(s)

    # 持仓分布
    cnt: dict[str, int] = {}
    for p in per:
        for c in p["top"]:
            cnt[c] = cnt.get(c, 0) + 1
    total = sum(cnt.values()) or 1
    inds: dict[str, int] = {}
    for c, n in cnt.items():
        inds[ind_map.get(_norm_code(c), "未映射")] = inds.get(ind_map.get(_norm_code(c), "未映射"), 0) + n
    res["industry"] = sorted(((k, v / total) for k, v in inds.items()), key=lambda x: -x[1])[:8]
    res["unmapped"] = inds.get("未映射", 0) / total

    if cap is not None:
        last = cap.iloc[-1]
        buckets = {"小盘<100亿": 0, "中盘100-500亿": 0, "大盘>500亿": 0, "未知": 0}
        for c, n in cnt.items():
            v = last.get(c, np.nan)
            if v != v:
                buckets["未知"] += n
            elif v > 5e10:
                buckets["大盘>500亿"] += n
            elif v > 1e10:
                buckets["中盘100-500亿"] += n
            else:
                buckets["小盘<100亿"] += n
        res["size"] = [(k, v / total) for k, v in buckets.items() if v]
    else:
        res["size"] = []
    return res
