"""因子评级：100 分制评分卡 + 一票否决 + 二维定位。

本版（2026-10 重写）修正四类结构性问题：

1. **方向**：不再按样本内 IC 符号翻转；``report.direction`` 是事先给定的先验，
   负 IC 记 0 分并触发「方向与先验相反」，而不是把因子翻成正 IC 再给高分。
2. **预测力**：改为连续加权（IC 幅度 60% + ICIR 40%），不再用 ``min()`` 封顶，
   避免 90% 的因子挤在 0–12 分。
3. **稳定性**：分年 CI 显著率 40% + 分状态 CI 显著率 30% + 滚动 12 个月 IC 同号率 30%，
   不再只靠 3 个 regime 拿不满 12.5 分。
4. **可交易性**：用毛价差的 95% 置信区间与单期成本比较（上界<成本→触发；
   下界>成本→通过；否则边界），不再用「点估计与成本差 1.96SE」的死区。
"""

from __future__ import annotations

import numpy as np

from . import diagnostics as diag

GRADE_LINES = (("A", 85.0), ("B", 70.0), ("C", 55.0))
#: 唯一权重源：报告里的规则表、雷达图、综合分都用这一份。
WEIGHTS = {"time": 25.0, "space": 20.0, "realization": 30.0,
           "increment": 10.0, "tradability": 15.0}
SCORECARD_VERSION = "v2.4.2-2026-11"

#: 口径变更记录（写入报告，便于跨版本比较时判断差异来源）
SCORECARD_CHANGELOG = [
    "v2.4.2：空间维度改为 0.55×max(ρ,0)+0.25×max(0,IC/0.06)+0.20×顶档特异度；兑现按反向毛价差+|IC|；不可交易改按方向相关口径；否决由 2 条降为 1 条",
    "v2.4.2：新增顶档特异度表与方向复核块；缩尾表改为“因子值缩尾（分组边界未变）”并删除错误的秩 IC 断言；regime 已分类天数 2183→2178",
    "v2.4.1：多头/样本外改用清洗后因子；敏感性表换手改 Top 组；新增剔除 G5 的对称过滤检验；反向净价差按“−毛价差−成本”修正；P1/P99 统一小数→百分比",
    "v2.4.1：清洗口径接入敏感性/长端链，历史报告的长端与样本诊断数字会变化（同数据、同因子、不同评估链）",
    "v2.4：主口径改为全 A 剔北证+有效观测；报告标注清洗标准与覆盖率；归因改为多标签证据分类",
    "v2.4：权重单一源（时间25/空间20/兑现30/增量10/可交易15）；多头年化按持有期折算并修正成本口径",
    "v2.3：IC 与价差背离由一票否决改为折价系数，纳入三维综合分（时间40%/空间35%/兑现25%）",
    "v2.3：修复暖机数据被提前裁掉，长窗因子样本从 2020-09 提前到 2020-01",
    "v2.2：恢复市场状态划分（创业板指 200 日均线 ±3%），时间分含牛/震荡/熊稳定性",
    "v2.2：多空净值个股收益缩尾 ±30%，修复单期异常价差导致的净值跳崖",
]


def _band(value: float, bands: list[tuple[float, float]], default: float) -> float:
    if value != value:
        return default
    for threshold, score in bands:
        if value >= threshold:
            return score
    return default


def _grade(total: float) -> str:
    for label, line in GRADE_LINES:
        if total >= line:
            return label
    return "D"


def _reverse_net(gross: float, cost: float) -> float:
    """反向净价差：成本不随信号翻转，所以是 −毛价差 − 成本。"""
    if not np.isfinite(gross):
        return float("nan")
    return float(-gross - (cost if np.isfinite(cost) else 0.0))


def _clip01(value: float) -> float:
    if not np.isfinite(value):
        return 0.0
    return float(max(0.0, min(1.0, value)))


def _rolling_sign_ratio(report, ic_mean: float, primary_h: int | None = None) -> float:
    """滚动 12 个月 IC 均值与全样本同号的比例（#19）。"""
    series = report.ic.get(primary_h if primary_h is not None else 5)
    if series is None or len(series) < 260 or not np.isfinite(ic_mean):
        return float("nan")
    rolled = series.rolling(252, min_periods=126).mean().dropna()
    if len(rolled) < 12:
        return float("nan")
    if ic_mean >= 0:
        return float((rolled > 0).mean())
    return float((rolled < 0).mean())


def rate(report, primary_h: int | None = None) -> dict:
    stats = report.ic_stats
    if primary_h is None:
        primary_h = 5 if 5 in stats.index else (stats.index[0] if len(stats) else None)
    elif primary_h not in stats.index:
        primary_h = stats.index[0] if len(stats) else None
    ic_mean = float(stats.loc[primary_h, "mean"]) if primary_h is not None else float("nan")
    icir = float(stats.loc[primary_h, "ir"]) if primary_h is not None else float("nan")
    nw_t = float(stats.loc[primary_h, "t"]) if primary_h is not None else float("nan")
    t_indep = float(stats.loc[primary_h, "t_indep"]) if primary_h is not None else float("nan")
    ic_win = float(stats.loc[primary_h, "win"]) if primary_h is not None else float("nan")
    n_years = int(len(report.ic_by_year.dropna())) if report.ic_by_year is not None else 0

    # ---- 预测力 30：连续加权，不再 min() 封顶
    ic_pos = max(0.0, ic_mean) if np.isfinite(ic_mean) else 0.0
    icir_pos = max(0.0, icir) if np.isfinite(icir) else 0.0
    power = 30.0 * (0.6 * _clip01(ic_pos / 0.05) + 0.4 * _clip01(icir_pos / 0.50))

    # ---- 时间稳定性 25：分年 40 + 分状态 30 + 滚动 12m 同号 30
    year_ratio = float("nan")
    if report.ic_by_year is not None and len(report.ic_by_year) >= 2:
        signs = np.sign(report.ic_by_year.dropna().to_numpy())
        year_ratio = float(np.mean(signs > 0)) if ic_mean >= 0 else float(np.mean(signs < 0))
    year_ci_ratio = float("nan")
    sig_years: list[tuple[int, float]] = []
    if report.ic_by_year_ci is not None and len(report.ic_by_year_ci) >= 2:
        frame = report.ic_by_year_ci.dropna(subset=["ci_lo", "ci_hi"])
        if len(frame):
            mask = (frame["ci_lo"] > 0) | (frame["ci_hi"] < 0)
            year_ci_ratio = float(mask.mean())
            sig_years = [(int(y), float(rec["mean"])) for y, rec in frame[mask].iterrows()]
    regime_ratio = float("nan")
    if report.ic_by_regime is not None and len(report.ic_by_regime.dropna()) >= 2:
        vals = report.ic_by_regime.dropna().to_numpy()
        regime_ratio = float(np.mean(vals > 0)) if ic_mean >= 0 else float(np.mean(vals < 0))
    regime_ci_ratio = float("nan")
    if report.regime_stats is not None and len(report.regime_stats.dropna(subset=["ci_lo", "ci_hi"])) >= 2:
        frame = report.regime_stats.drop(index="牛市−熊市", errors="ignore").dropna(subset=["ci_lo", "ci_hi"])
        if len(frame):
            regime_ci_ratio = float(((frame["ci_lo"] > 0) | (frame["ci_hi"] < 0)).mean())
    rolling_ratio = _rolling_sign_ratio(report, ic_mean, primary_h)
    prior_sign_ratio = float("nan")
    if report.ic_by_year is not None and len(report.ic_by_year.dropna()):
        # 分析信号已经乘过 direction；先验期望的 IC 符号因此恒为 +1。
        _ys = np.sign(report.ic_by_year.dropna().to_numpy())
        prior_sign_ratio = float(np.mean(_ys > 0))

    year_base = year_ci_ratio if np.isfinite(year_ci_ratio) else (year_ratio if np.isfinite(year_ratio) else 0.5)
    regime_base = regime_ci_ratio if np.isfinite(regime_ci_ratio) else (regime_ratio if np.isfinite(regime_ratio) else 0.5)
    rolling_base = rolling_ratio if np.isfinite(rolling_ratio) else year_base
    stability = 25.0 * (0.4 * year_base + 0.3 * regime_base + 0.3 * rolling_base)

    # ---- 空间单调性 15（用毛收益的 ρ；负方向/不单调不给分）
    mono = float(report.monotonicity) if np.isfinite(report.monotonicity) else 0.0
    monotonicity = _band(
        mono,
        [(0.80, 15.0), (0.60, 10.0), (0.40, 6.0), (0.00, 3.0)],
        0.0,
    )
    if ic_mean < 0:
        monotonicity = 0.0

    # ---- 可交易性 20：覆盖率 6 + 换手 6 + 净价差 t 8
    coverage_points = _band(report.coverage, [(0.85, 6.0), (0.70, 3.5)], 1.0)
    turnover_points = _band(
        -(report.turnover if np.isfinite(report.turnover) else 1.0),
        [(-0.30, 6.0), (-0.50, 3.5)],
        1.0,
    )
    net_t = float((report.spread_net or {}).get("t", np.nan))
    spread_points = _band(net_t if np.isfinite(net_t) else -1.0, [(2.0, 8.0), (1.0, 5.0), (0.0, 3.0)], 0.0)
    trade_score = coverage_points + turnover_points + spread_points

    # ---- 增量性 20：残差保留率 14 + 分散度 6（原 15 分制 ×4/3）
    inc = report.incrementality or {}
    if inc.get("score") is not None:
        incremental = float(inc["score"]) * (20.0 / 15.0)
    else:
        incremental = 10.0
    ratio = float(inc.get("effective_ratio", inc.get("ratio", np.nan)))
    max_corr = float(inc.get("max_corr", np.nan))
    incremental_note = (
        diag.retention_note(ratio, max_corr, float(inc.get("ic_raw", np.nan)))
        if inc
        else "未接入同类因子相关性，暂给中性分 10/20（非实测）"
    )

    # ---- 一票否决
    n_sig = len(sig_years)
    sig_desc = "、".join(f"{year}{'−' if mean < 0 else '+'}" for year, mean in sig_years) or "无"
    direction_mixed = len({1 if mean > 0 else -1 for _, mean in sig_years}) > 1
    financial = str(report.category) in ("quality", "value", "growth", "valuation")
    ic_threshold = 0.010 if financial else 0.015
    min_sig_years = 2 if (financial and n_years < 5) else 3
    insufficient = bool(
        (not np.isfinite(ic_mean)) or abs(ic_mean) < ic_threshold
        or n_sig < min_sig_years or direction_mixed
    )

    trade_check = diag.tradeability_interval(report.spread_gross or {}, report.cost_mean)
    if trade_check.status == "fail":
        trade_status = "触发"
    elif trade_check.status == "warn":
        trade_status = "边界（区间跨成本）"
    elif trade_check.status == "pass":
        trade_status = "通过"
    else:
        trade_status = "—"
    t_dir = t_indep if np.isfinite(t_indep) else nw_t
    direction_bad = bool(
        np.isfinite(ic_mean) and ic_mean < -0.010
        and ((not np.isfinite(t_dir)) or t_dir < -2)
    )
    # 方向错时，价差否决按反向口径判，避免与「方向错」重复罚同一个负号。
    trade_dependency = "registered"
    if direction_bad:
        _gs = report.spread_gross or {}
        _rg = {
            "mean": -float(_gs.get("mean", np.nan)),
            "ci_lo": -float(_gs.get("ci_hi", np.nan)),
            "ci_hi": -float(_gs.get("ci_lo", np.nan)),
        }
        trade_check = diag.tradeability_interval(_rg, report.cost_mean)
        trade_dependency = "reverse"
        if trade_check.status == "fail":
            trade_status = "触发（反向口径）"
        elif trade_check.status == "warn":
            trade_status = "边界（方向相关，按反向）"
        elif trade_check.status == "pass":
            trade_status = "通过（反向口径）"
        else:
            trade_status = "—"
        # 自检表与否决表必须同源；否则会出现“否决表按反向、自检按正向”的分裂。
        report.diagnostics = [
            c for c in (report.diagnostics or []) if c.check_id != "tradeability"
        ] + [trade_check]
        report.warnings = [
            w for w in (report.warnings or [])
            if not str(w).startswith(("自检失败[tradeability]", "自检提示[tradeability]"))
        ]
        _wprefix = "自检失败" if trade_check.status == "fail" else "自检提示"
        report.warnings.append(f"{_wprefix}[tradeability]：{trade_check.actual}")
    # IC 隐含价差：等权五分组的两端密度约 2.5σ，σ 取 5 日截面收益标准差
    _sigma = float(getattr(report, "sigma_5d", getattr(report, "cross_section_std", float("nan"))))
    _gross = float((report.spread_gross or {}).get("mean", float("nan")))
    _expected = ic_mean * diag.QUINTILE_SPREAD_MULTIPLIER * _sigma if np.isfinite(_sigma) else float("nan")
    _abs_expected = abs(_expected) if np.isfinite(_expected) else float("nan")
    _ratio = _gross / _expected if np.isfinite(_expected) and abs(_expected) > 1e-12 else float("nan")
    ic_spread_ratio = float(_ratio)
    ic_spread_triggered = bool(np.isfinite(_ratio) and not (0.5 <= _ratio <= 1.5))
    ic_spread_actual = (
        f"实测 {_gross * 100:+.2f}% / 隐含 {_expected * 100:+.2f}%（比值 {_ratio:.2f}）"
        if np.isfinite(_ratio) else "—"
    )
    direction_review = {}
    if direction_bad and abs(ic_mean) >= 0.02 and abs(t_dir) >= 3.0:
        _rvg = -_gross if np.isfinite(_gross) else float("nan")
        _rvn = _reverse_net(_gross, float(report.cost_mean))
        _rev_tail = float(
            (getattr(report, "tail_specificity", {}) or {}).get("score", 0.0) or 0.0
        )
        _rev_space = 100.0 * _clip01(
            0.55 * max(-mono, 0.0)
            + 0.25 * min(1.0, abs(ic_mean) / 0.06)
            + 0.20 * _rev_tail
        )
        direction_review = {
            "label": "方向复核候选",
            "reverse_ic": round(abs(ic_mean), 4),
            "reverse_t": round(abs(t_dir), 2),
            "reverse_gross": round(_rvg, 5) if np.isfinite(_rvg) else None,
            "reverse_net": round(_rvn, 5) if np.isfinite(_rvn) else None,
            "reverse_space_score": round(_rev_space, 1),
            "note": (
                "作为反向信号有显著信息；但即使把 direction 改成 -1，"
                f"空间分也只有约 {_rev_space:.0f}/100（G1–G4 平坦，只有顶档有信息），"
                "本因子更合适的用途可能是剔除 G5 的顶档过滤，而不是反向多空。"
                "若要采用，必须先在独立训练窗口校准 direction=-1、写入 registry，"
                "再用新方向重跑，不能在本样本内翻转。"
            ),
        }
    # 跨样本符号稳定性：主口径与相邻 universe 同量级反号 → 证据不足，直接 D。
    sign_flip = {}
    _sens = list(getattr(report, "sensitivity", []) or [])
    _primary = next((r for r in _sens if r.get("universe") == "EX_BSE_VALID"), None)
    if _primary and np.isfinite(float(_primary.get("ic", np.nan))):
        _pic = abs(float(_primary["ic"]))
        for _rec in _sens:
            _u = str(_rec.get("universe", ""))
            if _u == "EX_BSE_VALID" or "RAW" in _u:
                continue
            _ic = float(_rec.get("ic", np.nan))
            if not np.isfinite(_ic) or _ic == 0:
                continue
            _ratio_u = abs(_ic) / _pic if _pic > 1e-12 else float("inf")
            if np.sign(_ic) != np.sign(float(_primary["ic"])) and 0.8 <= _ratio_u <= 1.2:
                sign_flip = {
                    "universe": _u,
                    "primary_ic": float(_primary["ic"]),
                    "other_ic": _ic,
                    "ratio": _ratio_u,
                }
                break
    incremental_gate = bool(
        inc and (
            (np.isfinite(ratio) and ratio < 0.30)
            or (np.isfinite(float(inc.get("ic_resid", np.nan)))
                and float(inc.get("ic_resid")) <= 0)
            or float(inc.get("score", 0) or 0) <= 0
        )
    )
    _ex = getattr(report, "extra", None) or {}
    _ew_t = ((_ex.get("ew") or {}) or {}).get("t", float("nan"))
    _style_t = ((_ex.get("style") or {}) or {}).get("t", float("nan"))
    long_only_bad = bool(
        np.isfinite(_ew_t) and np.isfinite(_style_t)
        and float(_ew_t) < -2.0 and float(_style_t) < -2.0
    )

    veto_rules = [
        {
            "rule": "证据不足",
            "threshold": f"|IC|<{ic_threshold:.3f}，或显著年份<{min_sig_years}，或显著年份方向不一致",
            "actual": f"IC {ic_mean:+.3f}；显著年份 {n_sig} 个（{sig_desc}）；样本 {n_years} 年",
            "triggered": insufficient,
            "status": "触发" if insufficient else "通过",
        },
        {
            "rule": "方向与先验相反",
            "threshold": "signal = 原值 × direction；IC(信号)<−0.010 且独立 t<−2 才判反向（不自动翻转）",
            "actual": f"IC {ic_mean:+.3f}，独立 t {t_dir:+.1f}",
            "triggered": direction_bad,
            "status": "触发" if direction_bad else "通过",
        },
        {
            "rule": "覆盖率不足",
            "threshold": "< 70%",
            "actual": f"{report.coverage:.1%}",
            "triggered": bool(report.coverage < 0.70),
            "status": "触发" if report.coverage < 0.70 else "通过",
        },
        {
            "rule": "分层反向",
            "threshold": "单调性 ρ < 0",
            "actual": f"{mono:.2f}",
            "triggered": bool(mono < 0),
            "status": "触发" if mono < 0 else "通过",
        },
        {
            "rule": "不可交易",
            "threshold": "毛价差 95% 上界 < 单期成本 → 触发；下界 > 成本 → 通过；否则边界",
            "actual": trade_check.actual,
            "triggered": trade_check.status == "fail",
            "status": trade_status,
        },
        {
            "rule": "换手过高（每期）",
            "threshold": "> 80%",
            "actual": f"{report.turnover:.0%}" if np.isfinite(report.turnover) else "—",
            "triggered": bool(np.isfinite(report.turnover) and report.turnover > 0.80),
            "status": "触发" if np.isfinite(report.turnover) and report.turnover > 0.80 else "通过",
        },
        {
            "rule": "跨样本符号翻转",
            "threshold": "主口径与相邻 universe 的 |IC| 比值 0.8~1.2 且符号相反 → 证据不足",
            "actual": (
                f"EX_BSE_VALID IC {sign_flip.get('primary_ic', float('nan')):+.4f}"
                f" vs {sign_flip.get('universe', '—')} IC {sign_flip.get('other_ic', float('nan')):+.4f}"
                f"（|比值|={sign_flip.get('ratio', float('nan')):.2f}）"
                if sign_flip else "未触发"
            ),
            "triggered": bool(sign_flip),
            "status": "触发" if sign_flip else "通过",
        },
        {
            "rule": "增量闸门（不进合成）",
            "threshold": "残差 IC 保留 <30% 或残差 IC≤0 → 不可按独立因子给权重",
            "actual": (
                f"保留 {ratio:.0%}；残差 IC {float(inc.get('ic_resid', float('nan'))):+.3f}"
                if inc and np.isfinite(ratio) else "未接入同类因子"
            ),
            "triggered": incremental_gate,
            "status": "触发（不进合成）" if incremental_gate else "通过",
        },
        {
            "rule": "多头端双负（实盘不可用）",
            "threshold": "全 A 等权与同风格基准的 t 均 < −2",
            "actual": f"全 A t={_ew_t:.2f}；同风格 t={_style_t:.2f}",
            "triggered": long_only_bad,
            "status": "触发" if long_only_bad else "通过",
        },
    ]
    gates = [v for v in veto_rules if v["triggered"] and "闸门" in str(v["rule"])]
    vetoes = [
        f"{v['rule']}：{v['actual']}（阈值 {v['threshold']}）"
        for v in veto_rules if v["triggered"] and "闸门" not in str(v["rule"])
    ]

    # 价差闸门（先算，供定级与兑现两处使用）
    _net_mean = float((report.spread_net or {}).get("mean", np.nan))
    _cost_mean = float(report.cost_mean) if np.isfinite(report.cost_mean) else float("nan")
    under_water = bool(np.isfinite(_net_mean) and _net_mean <= 0)
    cannot_pay = bool(np.isfinite(_net_mean) and np.isfinite(_cost_mean)
                      and _net_mean < _cost_mean * 0.2)

    # ---- 二维定位
    time_score = 100.0 * (0.4 * year_base + 0.3 * regime_base + 0.3 * rolling_base)
    _tail = getattr(report, "tail_specificity", {}) or {}
    _tail_strength = float(_tail.get("score", 0.0) or 0.0)
    # 单调性已经奖励过的因子，不再用顶档特异度重复加分。
    _tail_space = _tail_strength * max(0.0, 1.0 - abs(mono))
    if isinstance(_tail, dict):
        _tail = dict(_tail)
        _tail["space_term"] = round(_tail_space, 4)
    space_score = 100.0 * _clip01(
        0.55 * max(mono, 0.0)
        + 0.25 * min(1.0, ic_pos / 0.06)
        + 0.20 * _tail_space
    )

    # ---- 反向评估：若把信号取反，指标会全部反号（秩相关与分组收益的对偶性）
    # 不重算，直接由解析关系得到；仅用于标记「方向可能反了」的因子。
    _rev_ic = -ic_mean
    _rev_t = -t_indep
    _rev_mono = -mono
    # 成本不随信号翻转：反向净价差 = −毛价差 − 成本，不是 −(毛价差−成本)。
    _gross_mean = float((report.spread_gross or {}).get("mean", np.nan))
    _cost = float(report.cost_mean) if np.isfinite(report.cost_mean) else 0.0
    _rev_gross = -_gross_mean if np.isfinite(_gross_mean) else float("nan")
    _rev_net = _reverse_net(_gross_mean, _cost)
    _gross_ci_lo = float((report.spread_gross or {}).get("ci_lo", np.nan))
    _gross_ci_hi = float((report.spread_gross or {}).get("ci_hi", np.nan))
    _rev_gross_ci = (
        (-_gross_ci_hi, -_gross_ci_lo)
        if np.isfinite(_gross_ci_lo) and np.isfinite(_gross_ci_hi) else (np.nan, np.nan)
    )
    _rev_net_ci = (
        (_rev_gross_ci[0] - _cost, _rev_gross_ci[1] - _cost)
        if np.isfinite(_rev_gross_ci[0]) and np.isfinite(_rev_gross_ci[1])
        else (np.nan, np.nan)
    )
    # 融券成本：按 8%/年、5 日持有期折算；仅用于判断反向多空是否还有剩余。
    _borrow = 0.08 * 5.0 / 242.0
    direction_suspect = bool(
        np.isfinite(_rev_t) and _rev_t >= 3.0 and _rev_mono >= 0.8
    )
    reverse_eval = {
        "ic": round(_rev_ic, 4),
        "t": round(_rev_t, 2),
        "mono": round(_rev_mono, 3),
        "spread_gross": round(_rev_gross, 5),
        "spread_net": round(_rev_net, 5),
        "gross_ci": (round(_rev_gross_ci[0], 5), round(_rev_gross_ci[1], 5)),
        "net_ci": (round(_rev_net_ci[0], 5), round(_rev_net_ci[1], 5)),
        "borrow_cost": round(_borrow, 5),
        "net_after_borrow": round(_rev_net - _borrow, 5)
        if np.isfinite(_rev_net) else None,
        "suspect": direction_suspect,
    }

    # ---- 第三维：兑现率（IC 隐含价差 vs 实测净价差）
    # 不再一票否决，改为折价系数：ratio≥0.8 记满分，线性折算，最低 0。
    _ratio = float(ic_spread_ratio) if np.isfinite(ic_spread_ratio) else float("nan")
    # 兑现：方向错时按反向毛价差 + |IC| 计算，避免与「方向错」重复扣分。
    realization_basis = "registered"
    if direction_bad and np.isfinite(_gross) and np.isfinite(_abs_expected) and _abs_expected > 1e-12:
        _real_ratio = (-_gross) / _abs_expected
        realization = 100.0 * _clip01(_real_ratio / 0.8)
        realization_basis = "reverse"
    else:
        realization = 0.0 if (under_water or cannot_pay) else (
            100.0 * _clip01(_ratio / 0.8) if np.isfinite(_ratio) else 50.0)
    transmission_note = (
        f"实测毛价差 / IC 隐含价差 = {_ratio:.2f}（≥0.8 视为完全兑现）"
        if np.isfinite(_ratio) else "传导比不可计算（IC 或截面波动缺失）"
    )

    # 综合分：时间 40% + 空间 35% + 兑现 25%
    # 第四维：增量性（与同类因子的区分度），原 0-20 分制归一到 0-100
    incremental_score = 100.0 * min(1.0, max(0.0, incremental) / 20.0)

    # 第五维：可交易性 = 覆盖率 40% + 换手便利 30% + 净价差 t 30%
    _cov = float(getattr(report, "coverage", 0.0) or 0.0)
    _to = float(report.turnover) if np.isfinite(report.turnover) else 1.0
    _net_t = float((report.spread_net or {}).get("t", 0.0) or 0.0)
    _cov_term = 0.4 * min(1.0, _cov)
    _turn_term = 0.3 * max(0.0, 1.0 - _to / 0.80)
    _spread_term = 0.3 * min(1.0, max(0.0, _net_t) / 2.0)
    tradability_score = 100.0 * _clip01(_cov_term + _turn_term + _spread_term)

    # 唯一权重源：时间 25 + 空间 20 + 兑现 30 + 增量 10 + 可交易 15
    total = (
        WEIGHTS["time"] / 100.0 * time_score
        + WEIGHTS["space"] / 100.0 * space_score
        + WEIGHTS["realization"] / 100.0 * realization
        + WEIGHTS["increment"] / 100.0 * incremental_score
        + WEIGHTS["tradability"] / 100.0 * tradability_score
    )
    raw_grade = _grade(total)
    items = [
        ("时间", round(WEIGHTS["time"] * time_score / 100.0, 1), WEIGHTS["time"]),
        ("空间", round(WEIGHTS["space"] * space_score / 100.0, 1), WEIGHTS["space"]),
        ("兑现", round(WEIGHTS["realization"] * realization / 100.0, 1), WEIGHTS["realization"]),
        ("增量", round(WEIGHTS["increment"] * incremental_score / 100.0, 1), WEIGHTS["increment"]),
        ("可交易", round(WEIGHTS["tradability"] * tradability_score / 100.0, 1), WEIGHTS["tradability"]),
    ]
    # 同风格超额不显著 → 不评 A（避免"评级 A 但没有真超额"）
    _ex = getattr(report, "extra", None) or {}
    _style_t = ((_ex.get("style") or {})).get("t")
    if (_style_t is not None and _style_t == _style_t and _style_t < 1.5
            and raw_grade == "A"):
        raw_grade = "B"

    # 综合分重算后重新定级，其余否决项仍然封顶
    grade = raw_grade
    capped = False
    if vetoes and grade in ("A", "B"):
        grade = "C"
        capped = True
    _weight = {"A": "15%~25%", "B": "10%~15%", "C": "5%~8%"}.get(grade, "0（归档）")
    if time_score >= 60 and space_score >= 60:
        quadrant = ("核心因子", f"可直接进多因子合成，建议权重 {_weight}")
    elif time_score < 60 and space_score >= 60:
        quadrant = ("择时因子", f"空间区分度强但状态依赖，只在特定 regime 启用（权重 {_weight}）")
    elif time_score >= 60 and space_score < 60:
        quadrant = ("过滤因子", f"适合做负面剔除/风控条件，不做打分（权重 {_weight}）")
    else:
        quadrant = ("淘汰", "时间与空间均不达标，归档并记录失效原因")
    if grade == "D":
        quadrant = ("淘汰/归档", "D 级归档，不建议单独使用或做过滤")
    _opposite = getattr(report, "filter_test_opposite", None) or {}
    review_candidate = bool(direction_review and _opposite.get("useful"))
    if review_candidate:
        quadrant = (
            "方向复核/过滤候选",
            "按当前 +1 先验归档；反向显著，且剔除 G5 的对称过滤检验有改善，"
            "需独立窗口重设方向后重跑，不能直接当“无信息”处理",
        )
    hard_d = bool(sign_flip or long_only_bad)
    if sign_flip:
        grade = "D"
        capped = True
        quadrant = (
            "淘汰/归档：跨样本符号翻转",
            "主口径与相邻 universe 同量级反号，因子方向不是稳定属性，证据不足，不能发布。",
        )
    elif long_only_bad:
        grade = "D"
        capped = True
        quadrant = (
            "淘汰/归档：多头端双负",
            "全 A 等权与同风格基准的多头超额均显著为负，实盘多头端不可用。",
        )
    elif incremental_gate and grade != "D":
        quadrant = (
            "观察/不可合成",
            "增量保留不足 30%，不能按独立因子给权重；最多作为风险过滤候选。",
        )

    filter_note = ""
    if report.filter_test and not report.filter_test.get("useful", False):
        filter_note = (
            "过滤价值未通过：G1 绝对收益为正且与全组接近，剔除后夏普改善不足 0.05；"
            "不能把「过滤」作为使用理由。"
        )
    if long_only_bad and not sign_flip:
        report.attribution = {
            "label": "多头端双负",
            "reason": f"全 A 等权 t={_ew_t:.2f}；同风格 t={_style_t:.2f}",
        }
    if sign_flip:
        report.attribution = {
            "label": "跨样本符号翻转",
            "reason": (
                f"EX_BSE_VALID IC {sign_flip['primary_ic']:+.4f} vs "
                f"{sign_flip['universe']} IC {sign_flip['other_ic']:+.4f}"
                f"（|比值|={sign_flip['ratio']:.2f}）"
            ),
        }
    attribution = (
        report.attribution if grade == "D"
        else {"label": "—", "reason": "非 D 级，不适用失效归因"}
    )
    notes = [incremental_note]
    if filter_note:
        notes.append(filter_note)
    if sign_flip:
        notes.append(
            f"跨样本符号翻转：EX_BSE_VALID IC {sign_flip['primary_ic']:+.4f}，"
            f"{sign_flip['universe']} IC {sign_flip['other_ic']:+.4f}，"
            f"|比值|={sign_flip['ratio']:.2f}；方向不是稳定属性，证据不足，直接 D。"
        )
    if incremental_gate:
        notes.append(
            f"增量闸门：残差 IC 保留 {ratio:.0%}；增量不足，不能按独立因子给权重。"
        )
    if long_only_bad:
        notes.append(
            f"多头端双负：全 A 等权 t={_ew_t:.2f}、同风格 t={_style_t:.2f}；"
            "实盘多头端不可用，D 不依赖符号翻转是否成立。"
        )
    if review_candidate:
        _rn = direction_review.get("reverse_net")
        notes.append(
            "方向复核候选：反向 IC {:.3f}、t≈{:.1f}，反向净价差 {}；"
            "剔除 G5 后夏普 {:.2f}→{:.2f}。这只是“换方向/换过滤口径”的证据，"
            "不改变当前 +1 先验下的 D；若要采用，先在独立训练窗口校准 direction=-1，"
            "写入 registry 后重跑。".format(
                direction_review.get("reverse_ic", float("nan")),
                direction_review.get("reverse_t", float("nan")),
                "—" if _rn is None else f"{_rn*100:+.2f}%/期",
                _opposite.get("all_sharpe", float("nan")),
                _opposite.get("rest_sharpe", float("nan")),
            )
        )
    if grade == "D":
        if review_candidate:
            notes.append(
                f"D 级失效归因：{attribution.get('label')}（{attribution.get('reason')}）；"
                "这是按 registry +1 先验的条件结论，不等于“因子无信息”。"
                "方向复核与剔除 G5 的对称过滤证据见上；采用前需独立窗口验证。"
            )
        else:
            notes.append(
                f"D 级失效归因：{attribution.get('label')}（{attribution.get('reason')}）；"
                "归档，不建议单独使用或做过滤。"
            )
    if direction_bad:
        notes.append(
            "方向与先验相反：本报告**没有**按样本内 IC 翻转。若要用反向信号，"
            "需在独立训练窗口重新校准并写入 registry，再以新方向重跑本报告。"
        )

    return {
        "score": round(total, 1),
        "grade": grade,
        "raw_grade": raw_grade,
        "capped": capped,
        "items": items,
        "weights": dict(WEIGHTS),
        "vetoes": vetoes,
        "gates": gates,
        "ic_spread_ratio": ic_spread_ratio,
        "veto_table": veto_rules,
        "grade_lines": GRADE_LINES,
        "time_score": round(time_score, 1),
        "prior_sign_ratio": round(prior_sign_ratio, 4) if np.isfinite(prior_sign_ratio) else None,
        "space_score": round(space_score, 1),
        "space_components": {
            "monotonicity": round(max(mono, 0.0), 3),
            "ic_strength": round(min(1.0, ic_pos / 0.06), 3),
            "tail_specificity": round(_tail_space, 3),
        },
        "tail_specificity": _tail,
        "realization_score": round(realization, 1),
        "realization_basis": realization_basis,
        "trade_dependency": trade_dependency,
        "edge_gate": {"under_water": under_water, "cannot_pay": cannot_pay,
                      "net_mean": round(_net_mean, 5) if np.isfinite(_net_mean) else None,
                      "cost_mean": round(_cost_mean, 5) if np.isfinite(_cost_mean) else None},
        "incremental_score": round(incremental_score, 1),
        "reverse_eval": reverse_eval,
        "direction_review": direction_review,
        "review_candidate": review_candidate,
        "sign_flip": sign_flip,
        "incremental_gate": incremental_gate,
        "long_only_bad": long_only_bad,
        "hard_d": hard_d,
        "tradability_score": round(tradability_score, 1),
        "tradability_components": {
            "coverage": {"raw": _cov, "weight": 0.4, "term": _cov_term,
                         "points": round(_cov_term * WEIGHTS["tradability"], 2)},
            "turnover": {"raw": _to, "weight": 0.3, "term": _turn_term,
                         "points": round(_turn_term * WEIGHTS["tradability"], 2)},
            "spread_t": {"raw": _net_t, "weight": 0.3, "term": _spread_term,
                         "points": round(_spread_term * WEIGHTS["tradability"], 2)},
            "total_points": round(tradability_score / 100.0 * WEIGHTS["tradability"], 2),
        },
        "transmission_note": transmission_note,
        "quadrant": quadrant[0],
        "usage": quadrant[1],
        "attribution": attribution,
        "scorecard_version": SCORECARD_VERSION,
        "rating_horizon": primary_h,
        "scorecard_changelog": list(SCORECARD_CHANGELOG),
        "detail": {
            "icir": icir,
            "nw_t": nw_t,
            "t_indep": t_indep,
            "ic_win": ic_win,
            "ic_mean": ic_mean,
            "n_years": n_years,
            "n_sig_years": n_sig,
            "power": round(power, 2),
            "stability": round(stability, 2),
            "monotonicity": mono,
            "spread_gross_mean": (report.spread_gross or {}).get("mean", np.nan),
            "spread_net_mean": (report.spread_net or {}).get("mean", np.nan),
            "cost_mean": report.cost_mean,
        },
        "meta": {
            "icir": icir,
            "ic_mean": ic_mean,
            "ic_pos": ic_pos,
            "year_ratio": year_ratio,
            "year_ci_ratio": year_ci_ratio,
            "regime_ratio": regime_ratio,
            "regime_ci_ratio": regime_ci_ratio,
            "rolling_sign_ratio": rolling_ratio,
            "monotonicity": mono,
            "coverage": report.coverage,
            "turnover": report.turnover,
            "spread_net_t": net_t,
            "incremental": incremental,
            "n_years": n_years,
            "min_sig_years": min_sig_years,
        },
        "notes": notes,
    }
