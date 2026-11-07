"""因子研究报告核心指标的单元测试（合成数据，不连数据库）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.report import data_quality
from research.report import metrics
from research.report import diagnostics as diag


def _panel(n_dates: int = 60, n_cols: int = 40, seed: int = 0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    cols = [f"{i:06d}" for i in range(n_cols)]
    close = pd.DataFrame(
        10 * np.cumprod(1 + rng.normal(0, 0.01, size=(n_dates, n_cols)), axis=0),
        index=dates, columns=cols,
    )
    open_ = close * (1 + rng.normal(0, 0.002, size=(n_dates, n_cols)))
    factor = pd.DataFrame(rng.normal(size=(n_dates, n_cols)), index=dates, columns=cols)
    return dates, cols, close, open_, factor


def test_independent_t_reduces_sample():
    series = pd.Series(np.random.default_rng(1).normal(0.02, 0.1, size=500))
    t_all = metrics.newey_west_t(series, lag=4)
    t_ind, n_ind = metrics.independent_t(series, step=5)
    assert n_ind == 100
    assert np.isfinite(t_ind)
    assert abs(t_ind) < abs(t_all) * 1.5


def test_tradeability_counts_suspension_and_limit_up():
    dates, cols, close, open_, factor = _panel(n_dates=40, n_cols=40)
    # t=10 的 signal，t+1 停牌
    open_.iloc[11, 0] = np.nan
    # t=20 的 signal，t+1 一字涨停（10 → 11）
    close.iloc[20, 1] = 10.0
    open_.iloc[21, 1] = 11.0
    out = metrics._tradeability(factor, close, open_, horizon=5, is_st={})
    assert out["denominator"] == 39 * 40
    assert out["suspended"] == 1
    assert out["limit_up"] == 1
    assert out["blocked"] == 2
    assert out["old_denominator"] == 40 * 40
    assert out["old_pct"] > out["pct"]


def test_delayed_exit_extends_on_limit_down():
    dates = pd.bdate_range("2024-01-02", periods=20)
    cols = ["000001"]
    close = pd.DataFrame(10.0, index=dates, columns=cols)
    open_ = pd.DataFrame(10.0, index=dates, columns=cols)
    close.iloc[10, 0] = 9.0  # 前收 10，跌停 9.0
    close.iloc[11, 0] = 9.2
    ret, delay, blocked = metrics._delayed_exit(close, open_, 5)
    assert bool(blocked.iloc[5, 0])
    assert delay.iloc[5, 0] == 1
    assert np.isclose(ret.iloc[5, 0], 9.2 / 10.0 - 1)


def test_neutralize_removes_size_and_industry():
    rng = np.random.default_rng(3)
    n_dates, n_cols = 120, 120
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    cols = [f"{i:06d}" for i in range(n_cols)]
    cap = pd.DataFrame(
        np.exp(rng.normal(10, 1, size=(n_dates, n_cols))), index=dates, columns=cols
    )
    industry = pd.DataFrame(
        [[f"ind{i % 6}" for i in range(n_cols)]] * n_dates, index=dates, columns=cols
    )
    factor = pd.DataFrame(
        2.0 * np.log(cap.to_numpy())
        + np.array([int(c) % 6 for c in cols])[None, :] * 3.0
        + rng.normal(0, 0.5, size=(n_dates, n_cols)),
        index=dates, columns=cols,
    )
    resid = metrics.neutralize_panel(factor, cap, industry, mode="both")
    # 残差与 log 市值的截面相关应显著下降
    raw_corr = np.nanmean([
        factor.iloc[i].rank().corr(np.log(cap.iloc[i]).rank()) for i in range(n_dates)
    ])
    res_corr = np.nanmean([
        resid.iloc[i].rank().corr(np.log(cap.iloc[i]).rank()) for i in range(n_dates)
    ])
    assert abs(res_corr) < abs(raw_corr) * 0.5
    # 行业内残差均值接近 0
    last = industry.iloc[-1]
    means = resid.iloc[-1].groupby(last.to_numpy()).mean().abs().max()
    assert means < 1e-8


def test_performance_mdd_and_annual():
    nav = pd.Series(
        [1.0, 1.1, 0.99, 1.2],
        index=pd.to_datetime(["2024-01-02", "2024-06-03", "2024-09-02", "2024-12-02"]),
    )
    perf = metrics._performance(nav)
    assert np.isclose(perf["mdd"], 0.99 / 1.1 - 1)
    annual = metrics._annual_returns(nav)
    assert np.isclose(annual.iloc[0], 0.2)


def test_filter_test_positive_q1_is_not_useful():
    rng = np.random.default_rng(5)
    n = 120
    base = rng.normal(0.002, 0.01, n)
    periods = pd.DataFrame(
        {
            "Q1": base + 0.0002,
            "Q2": base + 0.0004,
            "Q3": base + 0.0005,
            "Q4": base + 0.0006,
            "Q5": base + 0.0008,
        }
    )
    out = metrics._filter_test(periods, 5)
    assert out["q1_mean"] > 0
    assert out["spread_mean"] > 0
    assert not out["useful"]


def test_rank_ic_cached_factor_rank_matches():
    dates, cols, close, open_, factor = _panel(n_dates=80, n_cols=40)
    fwd = close.shift(-5) / open_.shift(-1) - 1
    plain = metrics._rank_ic_frame(factor, fwd)
    cached = metrics._rank_ic_frame(factor, fwd, factor_rank=factor.rank(axis=1))
    pd.testing.assert_series_equal(plain, cached)


def test_quantile_result_unpack_and_cost_reuse():
    dates, cols, close, open_, factor = _panel(n_dates=100, n_cols=40)
    q = metrics._quantile_nav(factor, close, open_, groups=5, horizon=5)
    nav, turnover_df, net, gross, daily, ls_cost, turnover_mean = q
    assert nav.shape[0] == len(dates)
    assert turnover_df.shape == gross.shape == net.shape
    assert q[2] is net
    reused = metrics._cost_sensitivity(
        factor, close, open_, groups=5, horizon=5, tradeable=None, quantile=q
    )
    recomputed = metrics._cost_sensitivity(
        factor, close, open_, groups=5, horizon=5, tradeable=None
    )
    pd.testing.assert_frame_equal(reused, recomputed)


def test_sections_skip_cross_section_extras():
    dates, cols, close, open_, factor = _panel(n_dates=160, n_cols=80)
    cap = pd.DataFrame(1e9, index=dates, columns=cols)
    industry = pd.DataFrame(
        [[f"ind{i % 8}" for i in range(len(cols))]] * len(dates), index=dates, columns=cols
    )
    report = metrics.build_report(
        factor,
        close,
        open_,
        factor_id="momentum.test",
        direction=1,
        market_cap=cap,
        industry=industry,
        sections=("definition", "timing", "rating"),
    )
    assert report.tradeability == {}
    assert report.sell_side == {}
    assert report.capacity == {}
    assert report.horizon_spreads is None
    assert report.ic_neutral is None
    assert report.ic_by_size is None
    assert report.ic_by_industry is None
    # 评级需要的核心价差仍在，不能因为省略展示章节而丢掉
    assert report.spread_net


def test_build_report_smoke_and_direction_banner_data():
    dates, cols, close, open_, factor = _panel(n_dates=160, n_cols=80)
    cap = pd.DataFrame(1e9, index=dates, columns=cols)
    industry = pd.DataFrame(
        [[f"ind{i % 8}" for i in range(len(cols))]] * len(dates), index=dates, columns=cols
    )
    regime = pd.Series(
        np.where(np.arange(len(dates)) % 40 < 20, "bull", "bear"), index=dates
    )
    fwd = close.shift(-5) / open_.shift(-1) - 1
    factor = -fwd  # 设计方向为负：取负后恰好预测未来收益
    report = metrics.build_report(
        factor, close, open_, factor_id="liquidity.test", category="liquidity",
        direction=-1, regime=regime, market_cap=cap, industry=industry,
        definition={"unit": "元"}, full=False,
    )
    assert report.direction == -1
    assert report.raw_direction == -1
    assert not report.flipped
    assert set(["t_indep", "n_indep", "ci_lo"]).issubset(report.ic_stats.columns)
    assert report.tradeability["denominator"] > 0


def test_no_in_sample_direction_flip():
    """方向由先验决定；IC 为负时不得翻转，否则等于样本内择向。"""
    dates, cols, close, open_, factor = _panel(n_dates=180, n_cols=60)
    fwd = close.shift(-5) / open_.shift(-1) - 1
    report = metrics.build_report(
        -fwd, close, open_, factor_id="momentum.test", category="momentum",
        direction=1, full=False,
    )
    assert report.direction == 1
    assert report.flipped is False
    assert report.ic[5].mean() < 0
    # 方向断言应触发 fail，而不是把 IC 翻成正数
    direction_check = {c.check_id: c for c in report.diagnostics}["direction"]
    assert direction_check.status == "fail"


def test_rho_spread_hard_boundary_only_at_extreme():
    assert diag.rho_spread_identity(0.9, -0.01).status == "fail"
    assert diag.rho_spread_identity(-0.9, 0.01).status == "fail"
    # 中间区间允许非单调排列，只报警不判失败
    assert diag.rho_spread_identity(0.2, -0.01).status == "warn"
    assert diag.rho_spread_identity(0.9, 0.01).status == "pass"


def test_tradeability_interval_has_no_dead_zone():
    cost = 0.002
    below = {"mean": 0.0005, "ci_lo": -0.0005, "ci_hi": 0.0015}
    above = {"mean": 0.0035, "ci_lo": 0.0025, "ci_hi": 0.0045}
    straddle = {"mean": 0.0020, "ci_lo": 0.0005, "ci_hi": 0.0035}
    assert diag.tradeability_interval(below, cost).status == "fail"
    assert diag.tradeability_interval(above, cost).status == "pass"
    assert diag.tradeability_interval(straddle, cost).status == "warn"


def test_negative_retention_has_plain_wording():
    text = diag.retention_note(-0.15, 0.99)
    assert "残差为负" in text
    assert "0 分" in text
    assert "-15%" not in text


def test_data_quality_cleaning_marks_zero_no_data():
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2024-01-02", periods=80)
    cols = [f"{i:06d}" for i in range(40)]
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, size=(len(dates), len(cols))), axis=0),
        index=dates, columns=cols,
    )
    factor = pd.DataFrame(
        rng.uniform(0.01, 1.0, size=(len(dates), len(cols))), index=dates, columns=cols
    )
    # 前 20 只股票在前 30 天无行情，因子被填 0
    close.iloc[:30, :20] = np.nan
    factor.iloc[:30, :20] = 0.0
    amount = pd.DataFrame(1e8, index=dates, columns=cols)
    q = data_quality.analyze(
        factor, close, close, low=close, amount=amount,
        standard=data_quality.CleaningStandard(min_history=20), horizon=5,
    )
    assert q.summary["triggered"]
    assert q.summary["zero"]["share"] > 0.15
    assert q.summary["zero"]["no_data_share"] > 0.10
    assert not bool(q.mask.iloc[0, 0])
    assert bool(q.mask.iloc[-2, 0])


def test_data_quality_positive_factor_not_triggered():
    rng = np.random.default_rng(12)
    dates = pd.bdate_range("2024-01-02", periods=60)
    cols = [f"{i:06d}" for i in range(30)]
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, size=(len(dates), len(cols))), axis=0),
        index=dates, columns=cols,
    )
    factor = pd.DataFrame(
        rng.uniform(0.01, 1.0, size=(len(dates), len(cols))), index=dates, columns=cols
    )
    q = data_quality.analyze(
        factor, close, close, low=close,
        standard=data_quality.CleaningStandard(min_history=20), horizon=5,
    )
    assert not q.summary["triggered"]


def test_rating_weights_are_single_source():
    from research.report import rating
    assert abs(sum(rating.WEIGHTS.values()) - 100.0) < 1e-9
    assert rating._grade(85.0) == "A"
    assert rating._grade(70.0) == "B"
    assert rating._grade(55.0) == "C"
    assert rating._grade(54.9) == "D"


def test_reverse_net_keeps_cost_positive():
    from research.report import rating
    # 原始毛价差 -0.30%，成本 0.16%；反向毛 +0.30%，反向净 +0.14%
    assert np.isclose(rating._reverse_net(-0.0030, 0.0016), 0.0014)
    assert np.isclose(rating._reverse_net(0.0014, 0.0017), -0.0031)


def test_decay_note_uses_absolute_ic_for_negative_signal():
    decay = pd.Series({1: -0.016, 5: -0.045, 10: -0.056, 20: -0.066})
    text = metrics._decay_note(decay)
    assert "增强" in text or "最大" in text
    assert "0.066" in text
    assert "见顶" not in text


def test_rho_zero_is_not_treated_as_positive():
    assert diag.rho_spread_identity(0.0, -0.003).status == "warn"
    assert diag.rho_spread_identity(0.0, 0.003).status == "warn"


def test_filter_test_side_can_drop_q5():
    rng = np.random.default_rng(21)
    base = rng.normal(0.001, 0.01, 120)
    periods = pd.DataFrame(
        {"Q1": base + 0.001, "Q2": base + 0.001, "Q3": base + 0.001,
         "Q4": base + 0.001, "Q5": base - 0.003}
    )
    out = metrics._filter_test_side(periods, 5, "Q5")
    assert out["drop_group"] == "Q5"
    assert out["dropped_mean"] < out["rest_mean"]
    assert np.isfinite(out["rest_sharpe"])


def test_regime_diff_ci_ignores_nan_ic_days():
    idx = pd.bdate_range("2024-01-02", periods=200)
    rng = np.random.default_rng(7)
    ic = pd.Series(rng.normal(-0.04, 0.05, len(idx)), index=idx)
    ic.iloc[::7] = np.nan
    regime = pd.Series(
        np.where(np.arange(len(idx)) % 2 == 0, "bull", "bear"), index=idx
    )
    table = metrics._regime_table(ic, regime)
    row = table.loc["牛市−熊市"]
    assert np.isfinite(row["ci_lo"]) and np.isfinite(row["ci_hi"])


def test_winsorize_frame_clips_cross_section_tails():
    dates = pd.bdate_range("2024-01-02", periods=3)
    frame = pd.DataFrame([[0.0, 1.0, 2.0, 3.0, 100.0]] * 3,
                         index=dates, columns=list("abcde"))
    out = metrics._winsorize_frame(frame, 0.01, 0.99)
    assert out.iloc[0, -1] <= 100.0
    assert out.shape == frame.shape


def test_data_quality_symbol_funnel_is_complete():
    rng = np.random.default_rng(31)
    dates = pd.bdate_range("2024-01-02", periods=40)
    cols = [f"{i:06d}" for i in range(20)]
    close = pd.DataFrame(1.0, index=dates, columns=cols)
    factor = pd.DataFrame(rng.uniform(0.1, 1.0, size=(len(dates), len(cols))),
                          index=dates, columns=cols)
    q = data_quality.analyze(
        factor, close, close, low=close,
        standard=data_quality.CleaningStandard(min_history=5), horizon=5,
    )
    steps = [r["step"] for r in q.summary["symbol_funnel"]]
    assert steps == ["universe 解析", "剔除北证", "参与排序"]
    assert q.summary["symbol_funnel"][-1]["count"] <= q.summary["symbol_funnel"][0]["count"]


def test_build_report_has_tail_specificity():
    dates, cols, close, open_, factor = _panel(n_dates=120, n_cols=60, seed=9)
    # 只让最高分位断崖，制造单尾结构
    factor = factor.copy()
    factor.iloc[:, :12] = factor.iloc[:, :12] + 5.0
    report = metrics.build_report(
        factor, close, open_, factor_id="test.tail", category="momentum",
        direction=1, full=False,
    )
    assert report.tail_specificity
    assert 0.0 <= report.tail_specificity["ratio"] <= 1.0


def test_quality_audit_row_is_generic():
    from types import SimpleNamespace
    from research.report import quality_audit
    report = SimpleNamespace(
        factor_id="x.test",
        data_quality={"triggered": True, "zero": {"share": 0.2, "no_data_share": 0.15},
                      "clean": {"g1": 0.0, "g5": 0.002, "spread_net": 0.0001,
                                "spread_net_t": 0.5}},
        incrementality={"max_corr": 0.6, "ic_resid": -0.01, "effective_ratio": 0.0},
    )
    row = quality_audit.row_from_report(report, {"grade": "D", "score": 33.0,
                                                "attribution": {"label": "完全冗余"}})
    assert row["factor_id"] == "x.test"
    assert row["triggered"] is True
    assert row["attribution"] == "完全冗余"


def test_ls_stats_uses_period_frequency():
    a = np.array([0.01, 0.02, 0.03, 0.04])
    b = np.array([0.005, 0.01, 0.0, 0.02])
    m = metrics._ls_stats(a, b, periods_per_year=48.0)
    y = metrics._ls_stats(a, b, periods_per_year=12.0)
    assert np.isclose(m["excess"], y["excess"] * 4.0)
    assert np.isclose(m["te"], y["te"] * 2.0)


def test_direction_error_gets_attribution():
    dates, cols, close, open_, factor = _panel(n_dates=220, n_cols=60)
    fwd = close.shift(-5) / open_.shift(-1) - 1
    report = metrics.build_report(
        -fwd, close, open_, factor_id="quality.test", category="quality",
        direction=1, full=False,
    )
    assert report.attribution["label"] == "方向错"
