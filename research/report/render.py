"""把 :class:`FactorReport` 渲染成教科书式 HTML（内联 SVG，可打印）。"""

from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd

from . import charts
from . import diagnostics as diag

CSS = """
:root{
  --ink:#1a1f28; --muted:#6b7280; --faint:#9aa3ae; --rule:#d9dee4;
  --navy:#1f3a5f; --burgundy:#8c2f39; --teal:#2c6e6a; --gold:#a8842c;
  --paper:#ffffff; --wash:#f6f7f9;
}
*{box-sizing:border-box}
body{margin:0;background:#eceef1;color:var(--ink);
  font-family:"Source Han Sans SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  font-size:14px;line-height:1.72;-webkit-font-smoothing:antialiased;}
.page{max-width:1120px;margin:32px auto;background:var(--paper);
  padding:64px 72px 72px;box-shadow:0 2px 24px rgba(20,30,45,.08);}
h1,h2,h3,.serif{font-family:Georgia,"Source Han Serif SC","SimSun",serif;font-weight:600;}
.eyebrow{font-size:11px;letter-spacing:.22em;color:var(--faint);text-transform:uppercase;}
.masthead{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:28px;}
.masthead h1{font-size:34px;margin:6px 0 6px;letter-spacing:-.01em;}
.masthead .sub{color:var(--muted);font-size:13px;}
.badge{text-align:center;min-width:120px;}
.badge .grade{font-family:Georgia,serif;font-size:56px;line-height:1;color:var(--burgundy);}
.badge .score{font-size:12px;color:var(--muted);margin-top:6px;letter-spacing:.08em;}
.abstract{border-left:3px solid var(--navy);background:var(--wash);
  padding:16px 20px;margin:0 0 18px;font-size:14.5px;}
.abstract b{color:var(--navy);}
.banner{border-left:3px solid var(--burgundy);background:#fbf3f4;
  padding:10px 14px;margin:10px 0 18px;font-size:12.5px;color:#5b2b31;}
.banner b{color:var(--burgundy);}
.lead{border:1px solid var(--rule);border-left:3px solid var(--navy);background:#f8fafc;
  padding:10px 14px;margin:10px 0 16px;font-size:13px;color:#26303d;}
.lead b{color:var(--navy);}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:0 0 30px;}
.kpi{border:1px solid var(--rule);padding:14px 16px;}
.kpi .label{font-size:11px;color:var(--muted);letter-spacing:.08em;}
.kpi .value{font-family:Georgia,serif;font-size:28px;margin-top:4px;}
.kpi .sub{font-size:11.5px;color:var(--faint);margin-top:2px;}
h2{font-size:20px;margin:44px 0 6px;padding-bottom:8px;border-bottom:1px solid var(--rule);}
h2 .no{color:var(--faint);font-size:13px;margin-right:10px;letter-spacing:.1em;}
h3{font-size:15px;margin:26px 0 8px;color:var(--navy);}
p{margin:8px 0;}
.figure{margin:18px 0 26px;}
.figure .cap{font-size:12px;color:var(--muted);margin-top:8px;}
.figure .cap b{color:var(--ink);font-weight:600;margin-right:8px;}
.figure .note{font-size:11.5px;color:var(--faint);margin-top:2px;}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 6px;
  font-variant-numeric:tabular-nums;}
.block{min-width:0;}
table.kv td:nth-child(2),table.kv th:nth-child(2){text-align:left;}
th,td{padding:7px 8px;border-bottom:1px solid var(--rule);text-align:right;}
th:first-child,td:first-child{text-align:left;}
thead th{font-weight:600;color:var(--muted);font-size:11.5px;letter-spacing:.06em;
  border-bottom:1.5px solid var(--ink);}
tbody tr:last-child td{border-bottom:1.5px solid var(--ink);}
.pos{color:var(--burgundy);} .neg{color:var(--teal);}
.pass{color:var(--teal);} .fail{color:var(--burgundy);} .border{color:var(--gold);font-weight:600;}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:28px;align-items:start;}
.grid3{display:grid;grid-template-columns:1.1fr .9fr;gap:32px;align-items:center;}
.verdict{border:1px solid var(--rule);padding:18px 20px;background:var(--wash);}
.verdict h3{margin-top:0;}
.verdict ul{margin:6px 0 0 18px;padding:0;}
.verdict li{margin:4px 0;}
.warn{border-left:3px solid var(--gold);background:#fdf8ec;padding:10px 14px;
  font-size:12.5px;color:#6b5a20;margin:12px 0;}
.small{font-size:11.5px;color:var(--muted);}
.footer{margin-top:46px;padding-top:14px;border-top:1px solid var(--rule);
  font-size:11px;color:var(--faint);display:flex;justify-content:space-between;}
.tag{display:inline-block;font-size:10.5px;letter-spacing:.08em;color:var(--muted);
  border:1px solid var(--rule);padding:1px 7px;margin-right:6px;}
@media print{
  body{background:#fff;font-size:11.5px}
  .page{box-shadow:none;margin:0;padding:0 0 12mm;max-width:none}
  h2{page-break-after:avoid}
  .figure,table{page-break-before:auto;page-break-inside:avoid}
}
"""


def _formula_text(d: dict) -> str:
    """把 registry 元数据转成可读公式；已知因子给中文表达式。"""
    import re as _re

    fid = str(d.get("id") or d.get("factor_id") or d.get("name") or "")
    raw = str(d.get("formula") or "")
    m = _re.search(r"turnover_std_bias_(\d+)_(\d+)", fid)
    if m:
        short, long_ = m.group(1), m.group(2)
        return f"换手率波动偏离 = MA{short}(换手率) / MA{long_}(换手率)（短窗 {short} 日 / 长窗 {long_} 日）"
    mapped = {
        "momentum.rsrs": "RSRS 阻力支撑相对强度：对过去 N 日 high~low 做 OLS 回归得斜率 β，再按斜率与波动标准化",
        "risk.vol_21d": "21 日收益率标准差（年化前）",
        "risk.beta_250d_csi300": "250 日滚动 beta = cov(个股日收益, 沪深300 日收益) / var(沪深300 日收益)",
        "size.market_cap": "对数流通市值 = ln(流通市值)",
    }
    if fid in mapped:
        return mapped[fid]
    if raw and not raw.startswith("见 registry"):
        return raw
    op = d.get("op") or fid
    on = d.get("on") or "—"
    win = d.get("window")
    tail = f"；窗口 {win} 日" if win else ""
    return f"按 {on} 计算算子 {op}{tail}（详情见 factors 注册表）"


def _esc(value) -> str:
    return html.escape(str(value))


def _cls(value: float) -> str:
    if value != value:
        return ""
    return "pos" if value > 0 else ("neg" if value < 0 else "")


def _pct(value: float, digits: int = 2) -> str:
    if value != value:
        return "—"
    return f"{value * 100:+.{digits}f}%"


def _num(value: float, digits: int = 3) -> str:
    if value != value:
        return "—"
    return f"{value:.{digits}f}"


def _ci(lo: float, hi: float, digits: int = 3) -> str:
    if not np.isfinite(lo) or not np.isfinite(hi):
        return "—"
    return f"[{lo:+.{digits}f}, {hi:+.{digits}f}]"


def _time_points(series: pd.Series):
    series = series.dropna()
    points = [(float(i), float(v)) for i, v in enumerate(series.to_numpy())]
    n = len(series)
    ticks = 6 if n > 24 else max(2, n // 12)
    labels, step = [], max(1, n // max(1, ticks - 1))
    for i in range(0, n, step):
        labels.append(pd.Timestamp(series.index[i]).strftime("%Y-%m"))
    return points, labels


def _downsample(series: pd.Series, limit: int = 420) -> pd.Series:
    clean = series.dropna()
    if len(clean) <= limit:
        return clean
    idx = np.unique(np.linspace(0, len(clean) - 1, limit).astype(int))
    return clean.iloc[idx]


def _xlabels(index, n: int = 6) -> list[str]:
    if len(index) == 0:
        return []
    idx = np.unique(np.linspace(0, len(index) - 1, min(n, len(index))).astype(int))
    return [pd.Timestamp(index[i]).strftime("%Y-%m") for i in idx]


def _figure(no: str, title: str, svg: str, note: str = "") -> str:
    note_html = f'<div class="note">{note}</div>' if note else ""
    return (
        f'<div class="figure">{svg}'
        f'<div class="cap"><b>图 {no}</b>{_esc(title)}</div>{note_html}</div>'
    )


def _table(headers, rows, note: str = "") -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="{cell[1]}">{cell[0]}</td>' if isinstance(cell, tuple)
                         else f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""
    cls = "kv" if len(headers) == 2 else ""
    return (
        f'<div class="block"><table class="{cls}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>{note_html}</div>"
    )


def _kpi(label: str, value: str, sub: str) -> str:
    return (f'<div class="kpi"><div class="label">{_esc(label)}</div>'
            f'<div class="value">{_esc(value)}</div><div class="sub">{_esc(sub)}</div></div>')


def _fmt_factor(value: float, unit: str) -> str:
    if value != value:
        return "—"
    if unit == "元":
        return f"{value / 1e8:,.2f} 亿元"
    if unit == "%":
        return f"{value:.2f}%"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.4g}"


def _direction_banner(report) -> str:
    """唯一方向措辞：因子原值不取负，评估信号 = 原值 × direction。"""
    d = report.definition or {}
    direction = int(report.direction)
    head = d.get("direction_text") or (
        "反向（评估时 ×−1；因子值越小，信号越强）" if direction < 0
        else "正向（评估时 ×+1；因子值越大，信号越强）"
    )
    source = report.direction_source or d.get("direction_source", "registry")
    source_text = {
        "registry": "registry 先验",
        "family-prior": "家族先验",
        "manual": "人工指定",
        "train": "独立训练窗口",
    }.get(str(source), str(source))
    q1 = "最高" if direction < 0 else "最低"
    q5 = "最低" if direction < 0 else "最高"
    return (
        f'<div class="banner"><b>方向口径（必读）：</b>因子原值不取负，评估信号 = 原值 × direction'
        f'（{direction:+d}，来源：{source_text}）。{head}。'
        f"G1 = 信号最弱（本因子：因子值{q1}），G5 = 信号最强（因子值{q5}）。"
        "若 IC 为负，说明方向与先验相反，本报告<b>不翻转</b>，而是记为「方向错」并给出 D 级归因。"
        "</div>"
    )


def render(report, rating: dict, meta: dict) -> str:
    stats = report.ic_stats
    primary = 5 if 5 in stats.index else (stats.index[0] if len(stats) else 5)
    row = stats.loc[primary] if primary in stats.index else None

    ic_mean = float(row["mean"]) if row is not None else float("nan")
    ic_ir = float(row["ir"]) if row is not None else float("nan")
    ic_t = float(row["t"]) if row is not None else float("nan")
    t_indep = float(row["t_indep"]) if row is not None else float("nan")
    ic_win = float(row["win"]) if row is not None else float("nan")
    ic_lo = float(row["ci_lo"]) if row is not None else float("nan")
    ic_hi = float(row["ci_hi"]) if row is not None else float("nan")
    ls = report.long_short
    d = report.definition
    unit = d.get("unit", "")

    # ---------------- 摘要
    gross = report.spread_gross or {}
    net = report.spread_net or {}
    summary = (
        f"<b>{_esc(report.factor_id)}</b> 在 {_esc(report.universe)} 样本上，"
        f"{primary} 日持有期 IC 均值 <b>{_num(ic_mean)}</b>"
        f"（95% CI {_ci(ic_lo, ic_hi)}，ICIR <b>{_num(ic_ir, 2)}</b>，"
        f"NW t = {_num(ic_t, 1)}，独立样本 t = {_num(t_indep, 1)}）；"
        f"分层单调性 {_num(report.monotonicity, 2)}，"
        f"G5−G1 毛价差 {_pct(gross.get('mean', float('nan')), 2)}/期、"
        f"净价差 {_pct(net.get('mean', float('nan')), 2)}/期"
        f"（单期成本 {_pct(report.cost_mean, 2)}，净价差 t={_num(net.get('t', float('nan')), 1)}）；"
        "净价差 = 毛价差 − 多空两条腿成本；各分组净收益是「多头口径」（每组自扣自身成本），"
        "两组相减不等于多空净价差；"
        f"多空组合几何年化 {_pct(ls.get('ann', float('nan')), 1)}、夏普 {_num(ls.get('sharpe', float('nan')), 2)}、"
        f"最大回撤 {_pct(ls.get('mdd', float('nan')), 1)}。"
        f"时间维度 {rating['time_score']:.0f}/100、空间维度 {rating['space_score']:.0f}/100，"
        f"定位于「<b>{rating['quadrant']}</b>」，评级 <b>{rating['grade']}</b>。"
    )

    kpis = "".join(
        [
            _kpi(f"Rank IC（h={primary}）", _num(ic_mean, 3), f"95% CI {_ci(ic_lo, ic_hi, 3)}"),
            _kpi("ICIR", _num(ic_ir, 2), f"NW t={_num(ic_t, 1)} · 独立 t={_num(t_indep, 1)}"),
            _kpi("多空夏普", _num(ls.get("sharpe", float("nan")), 2),
                 f"年化 {_pct(ls.get('ann', float('nan')), 1)} · MDD {_pct(ls.get('mdd', float('nan')), 1)}"),
            _kpi("覆盖率", f"{report.coverage:.1%}", f"{report.n_symbols} 只 · {report.n_dates} 日"),
        ]
    )

    # ---------------- 图 2-1：逐日 IC 与滚动均值分面板（各自纵轴）
    rolling_fig = ""
    if report.rolling_ic is not None and report.rolling_ic.notna().sum() > 10:
        points, labels = _time_points(report.rolling_ic)
        roll = report.rolling_ic.dropna()
        raw = report.ic.get(primary)
        raw_points = []
        limit = float("nan")
        if raw is not None:
            clean = raw.dropna()
            if len(clean) > 20:
                limit = max(3 * float(clean.std()), 0.10)
                clean = clean.clip(-limit, limit)
            raw_points, _ = _time_points(clean)
        frame_top = charts.ChartFrame(width=980, height=170, top=14, bottom=26, left=64, right=24)
        frame_bottom = charts.ChartFrame(width=980, height=190, top=14, bottom=42, left=64, right=24)
        top_svg = charts.line_chart(
            [charts.Series("逐日 IC", raw_points, color=charts.FAINT, width=0.8, opacity=0.55)],
            frame=frame_top,
            band=(-0.05, 0.05),
            y_formatter=lambda v: f"{v:+.2f}",
            annotate_last=False,
            caption=f"逐日 IC（截断 ±{limit:.2f}）",
        ) if raw_points else ""
        bottom_svg = charts.line_chart(
            [charts.Series("60日滚动均值", points, color=charts.NAVY, width=2.2)],
            frame=frame_bottom,
            band=(-0.05, 0.05),
            x_labels=labels,
            y_formatter=lambda v: f"{v:+.2f}",
            annotate_last=False,
            caption="60 日滚动均值",
        )
        svg = top_svg + bottom_svg
        rolling_fig = _figure(
            "2-1", f"IC 时序（上）与 60 日滚动均值（下，独立纵轴，h={primary}）", svg,
            f"注：上下两图各自纵轴，不再共用刻度；灰线为逐日 Rank IC（截断 ±{limit:.2f}），"
            f"藏青线为 60 日滚动均值（实际范围 {roll.min():+.3f} ~ {roll.max():+.3f}）。"
            "阴影为 ±0.05 弱信号带。滚动均值长期贴近 0 轴说明预测力薄弱；"
            "逐日线的大起大落是单日噪声，不应与滚动均值混读。",
        )

    # ---------------- 图 2-2：分年 IC（带 95% CI）
    yearly_fig = ""
    if report.ic_by_year is not None and len(report.ic_by_year) > 1:
        years = [str(y) for y in report.ic_by_year.index]
        values = list(report.ic_by_year.to_numpy())
        errors = []
        if report.ic_by_year_ci is not None:
            for year in report.ic_by_year.index:
                rec = report.ic_by_year_ci.loc[year]
                errors.append((rec["ci_hi"] - rec["ci_lo"]) / 2 if np.isfinite(rec["ci_hi"]) else 0.0)
        else:
            errors = [0.0] * len(values)
        svg = charts.bar_chart(
            years, values,
            width=980, height=250,
            formatter=lambda v: f"{v:+.3f}",
            errors=errors,
            caption="",
        )
        yearly_fig = _figure("2-2", "分年度 IC（误差棒为 95% 块自助置信区间，已按调整后信号）", svg,
                             "注：柱体为当年 IC 均值；误差棒跨 0 说明该年信号与 0 无法区分。"
                             "显著年份少于 3 年、或显著年份方向不一致时，触发「证据不足」否决；"
                             "不再按同号率降档。")

    # ---------------- 图 2-3：IC 衰减（图注由数据生成）
    decay_fig = ""
    if report.decay is not None and len(report.decay) > 1:
        points = [(float(h), float(v)) for h, v in report.decay.items()]
        svg = charts.line_chart(
            [charts.Series("IC", points, color=charts.BURGUNDY, width=2.4)],
            frame=charts.ChartFrame(width=980, height=230, bottom=44),
            y_formatter=lambda v: f"{v:+.3f}",
            x_labels=[f"h={int(h)}" for h in report.decay.index],
            caption="",
        )
        decay_fig = _figure("2-3", "IC 衰减曲线", svg, f"注：{_esc(report.decay_note)}")

    # ---------------- 图 3-1：分组平均收益（带 95% CI 与 y 轴）
    nav_fig = ""
    if report.quantile_returns:
        labels = [str(k).replace("Q", "G") for k in report.quantile_returns]
        values = [v * 100 for v in report.quantile_returns.values()]
        errors = []
        for k in report.quantile_ci:
            lo, hi = report.quantile_ci.get(k, (np.nan, np.nan))
            errors.append((hi - lo) / 2 * 100 if np.isfinite(hi) else 0.0)
        svg = charts.bar_chart(
            labels, values, width=980, height=280,
            formatter=lambda v: f"{v:+.2f}%",
            errors=errors,
            y_label="平均每期收益（%）",
            caption="",
        )
        q1 = "最高" if report.direction < 0 else "最低"
        q5 = "最低" if report.direction < 0 else "最高"
        nav_fig = _figure(
            "3-1", f"五分组平均持有期收益（h={primary}，T+1 开盘买入，已扣双边 20bp）", svg,
            f"注：分组按<b>评估信号</b>（原值×direction）从弱到强排列；本因子 G1=因子值{q1}、G5=因子值{q5}。"
            "误差棒为每期收益均值的 95% 置信区间。用「每期收益均值」而非复利终值衡量分层，"
            "避免波动拖累污染单调性；图中为已扣费的净收益，毛收益见下表。",
        )

    # ---------------- 表：分层收益
    qrows = []
    gross_means = report.quantile_returns_gross or {}
    net_means = report.quantile_returns or {}
    for col, value in net_means.items():
        qrows.append([
            str(col).replace("Q", "G"),
            (f"{gross_means.get(col, float('nan')) * 100:+.2f}%", _cls(gross_means.get(col, np.nan))),
            (f"{value * 100:+.2f}%", _cls(value)),
        ])
    qrows.append([
        "G5−G1（期均值）",
        (f"{(report.spread_gross or {}).get('mean', float('nan')) * 100:+.2f}%",
         _cls((report.spread_gross or {}).get("mean", np.nan))),
        (f"{(report.spread_net or {}).get('mean', float('nan')) * 100:+.2f}%",
         _cls((report.spread_net or {}).get("mean", np.nan))),
    ])
    qrows.append(["单期成本（G5+G1 换手×20bp）", "—", f"{report.cost_mean * 100:.2f}%"])
    qrows.append(["单调性 ρ（毛收益，有符号）", f"{report.monotonicity:.2f}", f"（净收益 ρ={report.monotonicity_net:.2f}）"])
    qrows.append(["Top 组换手（每期 5 日）", "—", f"{report.turnover:.1%}"])
    qrows.append(["年化换手（×242/5）", "—", f"{report.turnover * 242 / 5:.0%}"])
    quant_table = _table(["分组（评估信号由弱到强）", "毛收益/期", "净收益/期"], qrows,
                         "注：非重叠换仓，每期 5 个交易日；净收益已扣除 20bp/条腿（≈单边 10bp，多空合计 40bp）。"
                         "单调性用<b>毛收益</b>，避免各组换手差异把方向带偏；净收益与成本单列。")

    # ---------------- 图 3-2：多空净值 + 回撤
    perf_fig = ""
    if report.ls_nav is not None and len(report.ls_nav) > 20:
        ls_nav = _downsample(report.ls_nav)
        lines = [("多空 G5−G1", ls_nav.to_numpy(), charts.BURGUNDY)]
        dd = _downsample(report.drawdown).to_numpy() if report.drawdown is not None else []
        svg = charts.nav_chart(lines, dd, _xlabels(ls_nav.index), width=980, height=420)
        perf_fig = _figure(
            "3-2", "多空组合净值与回撤（5 日非重叠，T+1 开盘调仓，成本按换手扣除）", svg,
            "注：上图为 5 日非重叠多空净值（起点 1.0），下图为回撤；与分层表同一策略，"
            "G5 为调整后信号最强组、G1 为最弱组。",
        )

    # ---------------- 表：绩效与分年收益
    perf_rows = [
        ["年化收益（几何）", (_pct(report.perf.get("ann", np.nan), 1), _cls(report.perf.get("ann", np.nan)))],
        ["年化波动", f"{report.perf.get('vol', float('nan')):.1%}"],
        ["夏普", f"{report.perf.get('sharpe', float('nan')):.2f}"],
        ["最大回撤", (_pct(report.perf.get("mdd", np.nan), 1), _cls(report.perf.get("mdd", np.nan)))],
        ["Calmar", f"{report.perf.get('calmar', float('nan')):.2f}"],
        ["期胜率", f"{report.perf.get('win', float('nan')):.0%}"],
        ["样本期数（5 日）", f"{report.perf.get('n_days', 0)}"],
        ["G5−G1 毛价差/期", _pct((report.spread_gross or {}).get("mean", np.nan))],
        ["单期成本", _pct(report.cost_mean)],
        ["G5−G1 净价差/期", _pct((report.spread_net or {}).get("mean", np.nan))],
        ["净价差 t 值", f"{(report.spread_net or {}).get('t', float('nan')):.1f}"],
    ]
    spread_per = float(report.long_short.get(
        "ls_spread_mean",
        report.quantile_returns.get("Q5", np.nan) - report.quantile_returns.get("Q1", np.nan),
    ))
    arith_ann = spread_per * 242 / 5
    vol_ = report.perf.get("vol", float("nan"))
    drag = 0.5 * vol_ ** 2 if np.isfinite(vol_) else float("nan")
    table_spread = float((report.spread_net or {}).get("mean", float("nan")))
    perf_table = _table(["多空组合（G5−G1，5 日非重叠）", "数值"], perf_rows,
                        f"注：表内 G5−G1 净价差 {table_spread*100:.2f}%/期；多空两条腿各付一次往返 20bp。"
                        f"算术年化 ≈ {arith_ann*100:.1f}%（{spread_per*100:.2f}%/期 × 242/5），"
                        f"扣波动拖累 {drag*100:.1f}pp 后为 {arith_ann*100-drag*100:.1f}%，"
                        f"几何复利 {report.perf.get('ann', float('nan'))*100:.1f}%；"
                        "差额来自算术均值与几何复利，不是两套口径。未计融券费用与冲击成本。")
    annual_rows = []
    annual_corr = float("nan")
    if report.annual_ic_return is not None and not report.annual_ic_return.empty:
        frame = report.annual_ic_return
        annual_corr = float(frame["ic"].corr(frame["ret"]))
        for year, rec in frame.iterrows():
            annual_rows.append([
                str(year),
                (f"{rec['ic']:+.3f}", _cls(rec["ic"])),
                (_pct(rec["ret"], 1), _cls(rec["ret"])),
            ])
    elif report.annual_returns is not None:
        for year, value in report.annual_returns.items():
            annual_rows.append([str(year), "—", (_pct(value, 1), _cls(value))])
    annual_table = _table(["年份", "年度 IC", "多空收益"], annual_rows or [["—", "—", "—"]],
                          "注：IC 是逐日秩相关，收益是分组复利；少数极端期会主导年度收益，"
                          f"二者不同号不必然是计算错误。年度 IC 与收益的相关系数 {annual_corr:+.2f}；"
                          "若明显背离，应检查该年是否有单期极端收益或成分股结构变化，"
                          "不能只看年度 IC 判断盈亏。")

    # ---------------- 中性化 IC
    neutral_rows = []
    if report.ic_neutral is not None and not report.ic_neutral.empty:
        for name, rec in report.ic_neutral.iterrows():
            neutral_rows.append([
                name,
                (f"{rec['mean']:+.3f}", _cls(rec["mean"])),
                f"{rec['std']:.3f}" if np.isfinite(rec.get("std", np.nan)) else "—",
                f"{rec['ir']:.2f}" if np.isfinite(rec["ir"]) else "—",
                f"{rec['t']:.1f}" if np.isfinite(rec["t"]) else "—",
                f"{int(rec['n'])}",
            ])
    neutral_table = _table(
        ["口径", "IC 均值", "IC 标准差", "ICIR", "NW t", "样本日"],
        neutral_rows or [["—", "—", "—", "—", "—", "—"]],
        "注：中性化为逐日横截面回归残差——市值用 log 流通市值；行业用申万/东财行业映射"
        "（当前分类，非历史逐期）。残差 IC 仍为 Spearman 秩相关。行业中性后 ICIR 可能高于原始"
        "（去掉行业均值同时去掉了部分波动），因此以 IC 均值与双中性口径为主。",
    )

    # ---------------- 表：分市值 / 分行业
    size_rows = []
    if report.ic_by_size is not None:
        for k, v in report.ic_by_size.items():
            size_rows.append([k, (f"{v:+.3f}", _cls(v))])
    fam = diag.family_of(report.factor_id)
    if fam in ("size", "risk.log_price"):
        size_note = (
            "注：本因子本身就是规模/价格水平，本表是「因子与市值的关系」检验，"
            "不是规模暴露诊断；同号说明因子与规模高度共线，增量性表才是独立性的证据。"
        )
    else:
        size_note = (
            "注：按当日流通市值截面三分位划分。三组同号只说明方向一致；"
            "若小市值组显著高于大市值组，因子带强规模暴露，需结合增量性表判断是否独立。"
        )
    industry_rows = []
    if report.ic_by_industry is not None:
        top = report.ic_by_industry
        if len(top) > 12:
            top = pd.concat([top.head(6), top.tail(6)])
        for k, v in top.items():
            industry_rows.append([str(k), (f"{v:+.3f}", _cls(v))])
    industry_table = _table(["行业（按 IC 排序，展示样本≥8 只的行业）", f"IC（h={primary}）"],
                            industry_rows or [["—", "—"]],
                            "注：行业分类为当前映射，存在前视；仅用于识别行业暴露，不作为中性化结论。")

    # ---------------- 增量性
    inc_rows = []
    if report.peers is not None:
        for name, corr in report.peers.items():
            inc_rows.append([name, (f"{corr:+.2f}", _cls(corr))])
    inc = report.incrementality or {}
    ratio = float(inc.get("effective_ratio", inc.get("ratio", np.nan)))
    max_corr = float(inc.get("max_corr", np.nan))
    inc_summary = (
        f"最高同类相关 {max_corr:.2f}；"
        f"剔除同类后的残差 IC {inc.get('ic_resid', float('nan')):+.3f}"
        f"（原始 {inc.get('ic_raw', float('nan')):+.3f}）；"
        f"{diag.retention_note(ratio, max_corr, float(inc.get('ic_raw', float('nan'))))}"
        f" 原始增量性 {inc.get('score', float('nan')):.1f}/15（折算 {inc.get('score', float('nan')) * 4 / 3:.1f}/20）。"
    )
    inc_table = _table(["对照因子", "横截面秩相关（均值）"], inc_rows or [["—", "—"]],
                       "注：相关性为逐日横截面 Spearman 的均值（每 15 个交易日抽样一次）；"
                       "残差 IC 为把本因子对全部对照因子做截面回归后的残差 IC。")
    inc_note = ""
    if inc and (inc.get("max_corr", 0) >= 0.70 or abs(inc.get("ratio", 1.0) or 1.0) < 0.30):
        inc_note = (
            "<p class=\"small\"><b>解读：</b>该因子与同类/规模因子的相关性偏高，"
            "剔除后残差 IC 明显缩水，说明其大部分信息是已知风格暴露，而非独立 alpha；"
            "多因子合成时不应按独立因子给权重。</p>"
        )

    # ---------------- 过滤价值检验
    ft = report.filter_test or {}
    filter_block = ""
    if ft:
        verdict = "过滤有效" if ft.get("useful") else "过滤不成立"
        all_pos = bool(report.quantile_returns) and all(v > 0 for v in report.quantile_returns.values())
        beta_note = (
            "五组绝对收益全为正，主要是样本池 beta；" if all_pos else
            "分组绝对收益有正有负，不能把某组的正收益当成 alpha；"
        )
        filter_table = _table(
            ["检验项", "G1（拟剔除）", "G2–G5 等权", "全组等权"],
            [
                ["每期收益", _pct(ft.get("q1_mean", np.nan)), _pct(ft.get("rest_mean", np.nan)), _pct(ft.get("all_mean", np.nan))],
                ["折年收益（算术，×242/5）", _pct(ft.get("q1_ann", np.nan), 1), _pct(ft.get("rest_ann", np.nan), 1), _pct(ft.get("all_ann", np.nan), 1)],
                ["相对全组（每期）", _pct(ft.get("q1_excess", np.nan)), _pct(ft.get("rest_mean", np.nan) - ft.get("all_mean", np.nan)), "—"],
                ["相对全组（折年）", _pct(ft.get("q1_excess_ann", np.nan), 1), _pct((ft.get("rest_mean", np.nan) - ft.get("all_mean", np.nan)) * 242 / primary, 1), "—"],
                ["夏普", f"{ft.get('q1_sharpe', np.nan):.2f}", f"{ft.get('rest_sharpe', np.nan):.2f}", f"{ft.get('all_sharpe', np.nan):.2f}"],
                ["最大回撤", _pct(ft.get("q1_mdd", np.nan), 1), "—", "—"],
                ["相对全组回撤", _pct(ft.get("rel_mdd", np.nan), 1), "—", "—"],
                ["尾部亏损频率（<-3%/期）", f"{ft.get('tail_q1', np.nan):.1%}", f"{ft.get('tail_rest', np.nan):.1%}", "—"],
            ],
            f"注：{beta_note}过滤要看 Q1 相对全组的增量，而不是绝对收益。"
            "过滤的严格定义是「剔除 Q1 后组合夏普/回撤/尾部风险改善」。",
        )
        filter_block = (
            f"<h3>过滤价值检验：剔除 Q1 后组合会更好吗？</h3>{filter_table}"
            f"<p class=\"small\"><b>结论：{verdict}。</b>"
            f"Q1 每期收益 {ft.get('q1_mean', float('nan'))*100:+.2f}%（折年 {ft.get('q1_ann', float('nan'))*100:+.1f}%），"
            f"相对全组 {ft.get('q1_excess', float('nan'))*100:+.2f}%/期"
            f"（{ft.get('q1_excess_ann', float('nan'))*100:+.1f}%/年）；"
            f"剔除后夏普 {ft.get('all_sharpe', float('nan')):.2f}→{ft.get('rest_sharpe', float('nan')):.2f}、"
            f"每期收益 {ft.get('all_mean', float('nan'))*100:+.2f}%→{ft.get('rest_mean', float('nan'))*100:+.2f}%。"
            + (
                "改善幅度达到预设门槛，可作为负面剔除条件。"
                if ft.get("useful") else
                "改善幅度未达门槛，不构成过滤理由。"
            )
            + "若组合已暴露于同族因子（见增量性表），本因子的净贡献应以残差 IC 为准。</p>"
        )

    # ---------------- 可交易性
    tr = report.tradeability or {}
    trade_rows = [
        ["分母：有效信号（因子值非空且有下一交易日）", f"{tr.get('denominator', 0):,}"],
        ["分子：停牌不可买", f"{tr.get('suspended', 0):,}"],
        ["分子：开盘一字涨停买不进", f"{tr.get('limit_up', 0):,}"],
        ["不可成交合计", f"{tr.get('blocked', 0):,}"],
        ["不可成交占比", (f"{tr.get('pct', float('nan')):.2%}", "")],
    ]
    trade_table = _table(["指标", "数值"], trade_rows,
                         (tr.get("explain", "") +
                          "分母仅统计有因子值且有下一交易日的信号，口径与可执行交易一致。"
                          f"新口径只统计有因子值且有下一交易日的信号，{tr.get('pct', float('nan')):.2%} "
                          "才是可执行的不可成交比例。"))

    # ---------------- 卖出端
    ss = report.sell_side or {}
    sell_rows = [
        ["分母：可买入信号", f"{ss.get('denominator', 0):,}"],
        ["T+h 收盘跌停/停牌（无法按计划卖出）", f"{ss.get('blocked_at_target', 0):,}（{ss.get('pct_blocked', float('nan')):.2%}）"],
        ["顺延卖出笔数", f"{ss.get('delayed', 0):,}（{ss.get('pct_delayed', float('nan')):.2%}）"],
        ["平均 / 最大顺延（交易日）", f"{ss.get('avg_delay', 0):.2f} / {ss.get('max_delay', 0):.0f}"],
        ["G5−G1 每期收益：计划卖出", _pct(ss.get("spread_planned", np.nan), 2)],
        ["G5−G1 每期收益：顺延卖出", _pct(ss.get("spread_delayed", np.nan), 2)],
        ["影响", (_pct(ss.get("spread_delta", np.nan), 2), _cls(ss.get("spread_delta", np.nan)))],
    ]
    sell_table = _table(["卖出端可执行性", "数值"], sell_rows,
                        "注：跌停价由前收盘×（1−板块幅度）推出；跌停收盘或停牌视为无法卖出，"
                        "顺延到下一个可卖日（最多 10 个交易日）。")

    # ---------------- 成本敏感性
    cost_rows = []
    if report.cost_table is not None:
        for label, rec in report.cost_table.iterrows():
            cost_rows.append([
                label,
                (_pct(rec["ann"], 1), _cls(rec["ann"])),
                f"{rec['sharpe']:.2f}" if np.isfinite(rec["sharpe"]) else "—",
                _pct(rec["mdd"], 1),
            ])
    cost_table = _table(["往返费率/条腿", "年化收益", "夏普", "最大回撤"], cost_rows or [["—", "—", "—", "—"]],
                        "注：费率按换手率摊在调仓日，不是无条件全额扣除；20bp 为单腿往返（≈单边10bp），"
                        "多空两条腿合计 40bp，年化拖累约 1.4pp；未含融券费用。")

    # ---------------- 容量
    cap = report.capacity or {}
    cap_rows = [
        ["估算容量（中位数）", f"{cap.get('aum_est', float('nan')) / 1e8:,.1f} 亿元"],
        ["估算区间（历史调仓日）", f"{cap.get('aum_est_min', float('nan')) / 1e8:,.1f} ~ "
                                  f"{cap.get('aum_est_max', float('nan')) / 1e8:,.1f} 亿元"],
        ["Top 组最新 20 日均额（中位）", f"{cap.get('median_adv', float('nan')) / 1e8:,.2f} 亿元"],
        ["最小成分股 20 日均额", f"{cap.get('min_adv', float('nan')) / 1e8:,.2f} 亿元"],
        ["假设", cap.get("assumption", "—")],
    ]
    cap_table = _table(["容量估算", "数值"], cap_rows,
                       "注：容量是量级估计，不是承诺；实际受冲击成本、涨跌停与融券约束影响。")

    # ---------------- 评级规则
    rules_rows = [
        ["时间维度", "30", "分年 CI 显著率 40% + 分状态显著率 30% + 滚动 12m 同号率 30%"],
        ["空间维度", "20", "毛收益单调性 ρ 与 IC 正向强度合成；ρ<0 记 0"],
        ["兑现维度", "20", "实测净价差 / (IC×2.5σ)，比值 0.8 以上记满分（折价系数）"],
        ["增量维度", "15", "与同类因子的残差 IC 保留率；越独立分越高"],
        ["可交易维度", "15", "覆盖率 40% + 换手便利 30% + 净价差 t 30%"],
    ]
    rules_table = _table(["分项", "满分", "评分规则"], rules_rows,
                         "等级线：A ≥ 85，B ≥ 70，C ≥ 55，D < 55。一票否决只把 A/B 封顶到 C，不改变原始分。"
                         "「不可交易」用毛价差 95% CI 与单期成本比较：上界<成本→触发，下界>成本→通过，否则边界。"
                         "预测力与稳定性已合维，避免同一弱点被重复扣分；分数用于族内相对排序，不跨族比较。")
    veto_rows = []
    for item in rating.get("veto_table", []):
        status = item.get("status") or ("触发" if item["triggered"] else "通过")
        cls = "fail" if status == "触发" else ("pass" if status == "通过" else "border")
        veto_rows.append([item["rule"], item["threshold"], item["actual"], (status, cls)])
    veto_table = _table(["一票否决项", "阈值", "本因子实际值", "结果"], veto_rows)

    # ---------------- 结构化自检（每条结论都可被复核）
    diag_rows = []
    for check in report.diagnostics or []:
        status = {"pass": "通过", "warn": "提示", "fail": "失败", "skip": "跳过"}.get(check.status, check.status)
        cls = "pass" if check.status == "pass" else ("fail" if check.status == "fail" else "border")
        diag_rows.append([check.title, (status, cls), check.actual, check.expected, check.note])
    diag_table = _table(
        ["断言", "结果", "本因子实际值", "通过条件", "说明"],
        diag_rows or [["—", "—", "—", "—", "—"]],
        "注：断言只用于定位「算错 / 方向错 / 传导断裂」，不替代评级。"
        "ρ 与 G5−G1 的硬边界只在 |ρ|≥0.8 时成立；中间区间只报警，因为非单调排列可以出现 ρ>0 但 Q5<Q1。",
    )

    # ---------------- 附录：持有期明细
    detail_rows = []
    for h in stats.index:
        r = stats.loc[h]
        detail_rows.append([
            f"{int(h)} 日",
            (f"{r['mean']:+.3f}", _cls(r["mean"])),
            f"{r['ir']:.2f}" if np.isfinite(r["ir"]) else "—",
            f"{r['t']:.1f}" if np.isfinite(r["t"]) else "—",
            f"{r['t_indep']:.1f}" if np.isfinite(r["t_indep"]) else "—",
            f"{int(r['n_indep'])}",
            _ci(r["ci_lo"], r["ci_hi"], 3),
            f"{r['win']:.0%}",
            f"{int(r['n'])}",
        ])
    detail_table = _table(
        ["持有期", "IC 均值", "ICIR", "NW t", "独立 t", "独立样本数", "95% CI", "IC 胜率", "样本日"],
        detail_rows,
        "注：NW 滞后阶数取 h−1；独立 t 为每 h 个交易日取一个观测的朴素 t；"
        "ICIR 年化系数 √(242/h)。重叠样本看 NW-t，保守判断看独立 t。",
    )

    # ---------------- 因子定义与样本表
    def_rows = [
        ["公式", _formula_text(d)],
        ["数据源 / 字段", d.get("source", "—")],
        ["单位", unit or "—"],
        ["变换", d.get("transform", "—")],
        ["窗口", f"{d.get('window', '—')} 个交易日"],
        ["方向", d.get("direction_text", "—")],
        ["经济含义", d.get("economic", "—")],
        ["备注", d.get("notes", "—") or "—"],
        ["因子分布（全样本）", (
            f"中位数 {_fmt_factor(report.factor_stats.get('median', np.nan), unit)}；"
            f"P1 {_fmt_factor(report.factor_stats.get('p01', np.nan), unit)}；"
            f"P99 {_fmt_factor(report.factor_stats.get('p99', np.nan), unit)}；"
            f"偏度 {_num(report.factor_stats.get('skew', np.nan), 2)}"
        )],
    ]
    def_table = _table(["项目", "说明"], def_rows)
    def_table += (
        '<p class="small"><b>分组口径：</b>G1–G5 = 按评估信号等分的五组'
        '（G1 信号最弱、G5 最强，多空 = G5 减 G1）；'
        'S1–S5 = 按当日流通市值五等分（S1 最小、S5 最大）；'
        'L1–L3 = 按当日成交额分层（L1 后 40%、L2 中 20%、L3 前 40%）。'
        "三类编号互不相同，避免把「信号分位」与「市值分位」混淆。</p>"
    )

    pool = report.pool or {}
    pool_rows = [
        ["入池基准日",
         "不适用（全市场口径）" if pool.get("kind") == "universe" else pool.get("date", "—")],
        ["构造规则", pool.get("rule", "—")],
        ["板块范围", pool.get("boards", "—")],
        ["基准日候选 / 最终入池", f"{pool.get('candidates', 0):,} / {pool.get('selected', 0):,}"],
        ["上市日晚于样本起点", f"{pool.get('listed_after_start', 0):,} 只"],
        ["池内已退市", f"{pool.get('delisted_in_pool', 0):,} 只"],
        ["区间末端无数据（>10 日）", f"{pool.get('stale_symbols', 0):,} 只"],
        ["库内已退市股（基准日前上市）", f"{pool.get('delisted_universe', 0):,} 只，"
                                          f"其中当日有行情 {pool.get('delisted_with_price', 0):,} 只，"
                                          f"缺失 {pool.get('delisted_missing', 0):,} 只"],
    ]
    if pool.get("kind") == "financial":
        pool_rows = [r for r in pool_rows if not str(r[0]).startswith(("池内已退市", "库内已退市", "区间末端无数据", "上市日晚于"))]
    pool_table = _table(["样本池", "取值"], pool_rows, pool.get("survivorship_note", ""))

    # 样本池叙事必须按 pool config 条件渲染，不能把「成交额选池」复制到所有因子
    kind = str(pool.get("kind", "price"))
    selection = str(pool.get("selection", "amount"))
    pool_date = pool.get("date", "—")
    pool_size = pool.get("requested", report.n_symbols)
    fam_for_pool = diag.family_of(report.factor_id)
    amount_family = fam_for_pool in (
        "liquidity.amount_ma", "liquidity.absret_amount", "liquidity.turnover_ma",
    )
    if kind == "financial":
        pool_narrative = (
            f"<b>选样方式（财务池）：</b>样本按财务历史覆盖度选取（基准日 {pool_date}），"
            f"不是全 A 池；覆盖度与规模、上市年限高度相关，因此存在规模倾斜。"
            f"当前有效样本 {report.n_symbols} 只、{report.n_dates} 个交易日"
            f"（约 {report.n_dates / 242:.1f} 年）；窗口 &lt;5 年时否决门槛已放宽为「≥2 个显著年」。"
            "<b>PIT：</b>报表按公告日对齐（as_of=available_at），额外 lag="
            f"{d.get('lag', 0)} 个交易日；重述只保留最近一次入库值，历史重述未逐期回溯，"
            "因此结论应读作「已披露信息下的上限」。"
        )
    elif amount_family:
        pool_narrative = (
            f"<b>选样内生性（仅对本族成立）：</b>样本池按 {pool_date} 成交额降序取前 {pool_size} 只，"
            "而本因子的分子就是成交额/换手——这是「用因子的分子定义样本池，再在同一样本池里检验因子」。"
            f"因此所有结论都是条件结论：<b>在 {pool_date} 最活跃的 {report.n_symbols} 只股票中，"
            "再按活跃度排序</b>，不能外推为全市场流动性溢价。下方用不同基准日重取样池做稳健性检验。"
        )
    elif kind == "universe":
        pool_narrative = (
            f"<b>选样方式：</b>样本池为 <b>{selection}</b>（{pool.get('rule', '')}），"
            f"共 {report.n_symbols:,} 只、{report.n_dates} 个交易日；"
            "按区间内可交易标的计算，不使用固定基准日选池，因此不存在「用因子分子选池」的内生性。"
            "<b>残余偏差：</b>库内无历史行情的退市股仍会缺失，结论需按此折价。"
        )
    else:
        pool_narrative = (
            f"<b>选样方式：</b>样本池按 {pool_date} 成交额取前 {pool_size} 只，属于流动性/规模倾斜的股票池；"
            "这是<b>固定基准日池</b>，不是 point-in-time 全 A 池。"
            "对本因子不存在「用因子分子选池」的直接内生性，但结论仍只在该池内成立。"
        )
    funnel = pool.get("funnel") or [
        {"step": "基准日成交额排序候选", "n": int(pool.get("candidates", 0) or 0), "note": "含 2 倍缓冲"},
        {"step": "板块过滤（沪深主板/创业板/科创板）", "n": int(pool.get("board_passed", 0) or report.n_symbols), "note": "剔除北交所"},
        {"step": "最终入池且有行情", "n": int(report.n_symbols), "note": "报告样本"},
    ]
    funnel_table = _table(
        ["选样漏斗", "数量", "说明"],
        [[str(step.get("step", "—")), f"{int(step.get('n', 0)):,}", str(step.get("note", ""))] for step in funnel],
        "注：每一级都给出数量，避免「规则写 300 只、实际入池 120 只」却没有交代。",
    )

    robust_block = ""
    if report.pool_robustness is not None and not report.pool_robustness.empty:
        rob_rows = []
        for _, rec in report.pool_robustness.iterrows():
            rob_rows.append([
                str(rec["pool_date"]),
                str(rec["start"]),
                f"{int(rec['n'])}",
                (f"{rec['ic']:+.3f}", _cls(rec["ic"])),
                f"{rec['t_indep']:.1f}" if np.isfinite(rec["t_indep"]) else "—",
                (f"{rec['spread']*100:+.2f}%", _cls(rec["spread"])),
                f"{rec['mono']:.2f}",
            ])
        robust_table = _table(
            ["池基准日", "起点", "股票数", "IC(h=5)", "独立 t", "G5−G1/期", "ρ"],
            rob_rows,
            "注：首行为当前样本池（全市场）基准；其余行为换基准日重建的点位池对照。若 IC/价差符号或量级不一致，说明结论是"
            "「特定池子」的条件结论，评级本身不稳。",
        )
        robust_block = f"<h3>样本池稳健性：换基准日是否还是同一个答案</h3>{robust_table}"
        rics = report.pool_robustness["ic"].dropna()
        if len(rics) >= 2:
            robust_block += (
                f"<p class=\"small\"><b>结论：</b>{len(rics)} 个池子的 IC 为 "
                + " / ".join(f"{v:+.3f}" for v in rics)
                + f"，量级相差约 {rics.max() / max(abs(rics.min()), 1e-9):.1f} 倍；"
                + ("符号不一致，结论不可外推。" if (rics > 0).any() and (rics < 0).any()
                   else "方向一致但量级差异大，只能作为条件结论，不能据此给全市场流动性溢价定价。")
                + "</p>"
            )

    fresh = report.freshness or {}
    stale = fresh.get("stale_natural_days", 0)
    fresh_note = (
        f"{fresh.get('cutoff', '—')} 收盘；生成 {fresh.get('generated_at', '—')}"
        f"（{fresh.get('timezone', '北京时间 UTC+8')}）"
    )
    if stale and stale > 0:
        fresh_note += f"；距生成日 {stale} 个自然日，当日行情尚未入库"
    if fresh.get("intraday_trimmed"):
        fresh_note += "；生成时当日未收盘，已回退到上一完整交易日"
    partial = fresh.get("partial_last_day")
    if partial:
        fresh_note += (
            f"；{partial.get('date')} 仅 {partial.get('covered')} 只有行情"
            f"（前一日 {partial.get('previous')} 只），已整日剔除"
        )
    elif fresh.get("last_day_coverage"):
        missing = report.n_symbols - int(fresh.get("last_day_coverage", 0))
        fresh_note += (
            f"；末日覆盖 {fresh.get('last_day_coverage')}/{report.n_symbols} 只"
            + (f"（{missing} 只停牌/无成交）" if missing > 0 else "")
        )
    fresh_note += "。"

    # 因子 ID / 样本池 / 样本区间 / 评估方向 已在页首 EVALUATION PROFILE 与上方
    # 「因子定义与样本」中出现，此处不再重复，只保留运行元数据。
    meta_rows = [
        ["类别 / 家族", f"{report.category} / {diag.family_of(report.factor_id)}（{diag.family_label(diag.family_of(report.factor_id))}）"],
        ["方向来源", str(report.direction_source)],
        ["标的数 / 交易日", f"{report.n_symbols} / {report.n_dates}"],
        ["覆盖率", f"{report.coverage:.1%}"],
        ["成交口径", "T 日收盘出信号，T+1 开盘买入，T+h 收盘卖出"],
        ["成本假设", "单边 10bp / 往返 20bp（每条腿）；多空合计 40bp，按换手率扣除"],
        ["PIT / 财务口径", d.get("pit") or "行情类因子：无财务披露时点问题"],
        ["评分卡版本", str(rating.get("scorecard_version", "—"))],
        ["本次口径变更", "；".join(rating.get("scorecard_changelog") or []) or "—"],
        ["生成时间", meta.get("generated_at", "")],
        ["数据截止 / 口径", fresh_note],
        ["run_id", meta.get("run_id", "")],
    ]
    meta_table = _table(["运行信息", "取值"], meta_rows)


    _rev = rating.get("reverse_eval") or {}
    if _rev:
        rev_note = (f"若把信号取反：IC {_rev['ic']:+.4f}、独立 t {_rev['t']:+.1f}、"
                    f"单调性 {_rev['mono']:+.2f}、净价差 {_rev['spread_net']*100:+.2f}%/期"
                    + ("　→ <b>方向待复核</b>（反向显著）" if _rev.get("suspect")
                       else "　→ 反向也不成立"))
    else:
        rev_note = "—"
    _real = rating.get("realization_score", float("nan"))
    _advice = []
    if rating["grade"] in ("A", "B"):
        _advice.append(f"可进多因子合成，建议权重 {10 if rating['grade'] == 'B' else 15}%~"
                       f"{15 if rating['grade'] == 'B' else 25}%。")
    elif rating["grade"] == "C":
        _advice.append("仅作为组合的辅助成分或过滤条件，不建议单独承担仓位。")
    else:
        _advice.append("归档，不建议使用；如要复用需重新设计信号或换样本。")
    if np.isfinite(_real):
        if _real >= 80:
            _advice.append(f"兑现率 {_real:.0f}/100：IC 能基本兑现为分组收益。")
        elif _real >= 40:
            _advice.append(f"兑现率 {_real:.0f}/100：约 {_real:.0f}% 的 IC 兑现为价差，"
                           "其余被成本与尾部稀释。")
        else:
            _advice.append(f"兑现率 {_real:.0f}/100：大部分 IC 未兑现，实际收益远低于统计显著性暗示的水平。")
    if np.isfinite(report.turnover):
        _advice.append(f"每期换手 {report.turnover:.0%}，"
                       + ("适合 5–10 日调仓。" if report.turnover <= 0.5 else
                          "建议 20 日以上调仓以摊薄成本。"))
    _dims = [
        ("时间", rating.get("time_score", 0.0), 30.0),
        ("空间", rating.get("space_score", 0.0), 20.0),
        ("兑现", rating.get("realization_score", 0.0), 20.0),
        ("增量", rating.get("incremental_score", 0.0), 15.0),
        ("可交易", rating.get("tradability_score", 0.0), 15.0),
    ]
    _vals_w = [float(v or 0.0) / 100.0 * w for _, v, w in _dims]
    _labels_w = [f"{name} {val:.0f}/{w:.0f}" for (name, _, w), val in zip(_dims, _vals_w)]
    _radar = charts.radar_chart(
        _labels_w, _vals_w, maxes=[w for _, _, w in _dims], show_value=False,
        caption=f"五维评分（加权分，合计 {sum(_vals_w):.1f}/100；评级 {rating['grade']}）",
    )
    radar_block = _radar
    advice_block = (
        "<h3>使用建议</h3><div class=\"block\"><ul>"
        + "".join(f"<li>{_esc(item)}</li>" for item in _advice)
        + "</ul></div>"
    )

    warn_html = ""
    if report.warnings:
        warn_html = '<div class="warn"><b>诊断提示</b><br>' + "<br>".join(
            _esc(w) for w in report.warnings) + "</div>"

    regime_extra = ""
    if report.regime_stats is not None and "牛市−熊市" in report.regime_stats.index:
        diff = report.regime_stats.loc["牛市−熊市"]
        p_value = diff.get("p", float("nan"))
        regime_extra = (
            f"牛市与熊市的 IC 差为 {diff['mean']:+.3f}"
            f"（95% CI {_ci(diff['ci_lo'], diff['ci_hi'], 3)}，双尾 p={p_value:.3f}）。"
        )
        if np.isfinite(report.regime_corr):
            p_corr = report.regime_corr_p
            regime_extra += (
                f" 分年 IC 与牛市天数占比的相关系数 r={report.regime_corr:+.2f}"
                f"（n={len(report.ic_by_year.dropna()) if report.ic_by_year is not None else 0}，"
                f"Fisher z 近似 p={p_corr:.2f}"
                + ("，不显著" if np.isfinite(p_corr) and p_corr > 0.05 else "")
                + "），说明状态差异与年份结构有关，不能把牛市 IC 直接外推到全样本。"
            )

    regime_alt_block = ""
    if report.regime_stats_alt is not None and not report.regime_stats_alt.empty:
        alt_rows = []
        for label in ("牛市", "震荡", "熊市"):
            a = report.regime_stats.loc[label] if report.regime_stats is not None and label in report.regime_stats.index else None
            b = report.regime_stats_alt.loc[label] if label in report.regime_stats_alt.index else None
            alt_rows.append([
                label,
                f"{a['mean']:+.3f}（{int(a['n'])}）" if a is not None else "—",
                f"{b['mean']:+.3f}（{int(b['n'])}）" if b is not None else "—",
            ])
        alt_table = _table(
            ["市场状态", "创业板指 IC（天数）", "沪深300 IC（天数）"],
            alt_rows,
            "注：000300 已回补至 2019-01-02（与创业板指重叠区间）；"
            "两套代理给出不同符号/量级时，说明 regime 结论依赖温度计，不应据此做择时。",
        )
        regime_alt_block = f"<h3>regime 代理敏感性</h3>{alt_table}"


    strat_block = ""
    _strat = getattr(report, "strat_ic", None)
    if _strat is not None and len(_strat):
        _rows = [
            [str(rec["stratum"]), f"{rec['ic']:+.3f}" if np.isfinite(rec["ic"]) else "—",
             f"{rec['t']:.1f}" if np.isfinite(rec["t"]) else "—", f"{int(rec['n_days'])}"]
            for _, rec in _strat.iterrows()
        ]
        def _mini(mask, title, caption):
            sub = _strat[mask]
            if sub.empty:
                return ""
            return (f"<h4>{title}</h4>"
                    + charts.bar_chart([str(r["stratum"]) for _, r in sub.iterrows()],
                                       [float(r["ic"]) for _, r in sub.iterrows()],
                                       height=200, formatter=lambda v: f"{v:+.3f}",
                                       caption=caption))

        strat_block = (
            "<h3>分层 IC</h3>"
            + _mini(_strat["stratum"].astype(str).str.startswith("流动性"),
                    "流动性分层", "L1–L3 按日均成交额三分位；各层内部单独计算秩相关。")
            + _mini(_strat["stratum"].astype(str).str.startswith("市值"),
                    "市值分层", "S1（最小）–S5（最大）按流通市值五等分，与上文市值三分组表二选一。")
        )


    _hs = getattr(report, "horizon_spreads", None)
    horizon_block = ""
    if _hs is not None and len(_hs):
        _rows = [
            [f"{int(rec['horizon'])} 日", str(rec["variant"]),
             (f"{rec['gross']*100:+.2f}%", _cls(rec["gross"])),
             (f"{rec['net']*100:+.2f}%", _cls(rec["net"])),
             f"{rec['turnover']:.0%}" if np.isfinite(rec["turnover"]) else "—"]
            for _, rec in _hs.iterrows()
        ]
        _labels = [f"{int(rec['horizon'])}日{'' if rec['variant'] == '原始' else '·中性'}"
                   for _, rec in _hs.iterrows()]
        _vals = [float(rec["gross"]) * 100 for _, rec in _hs.iterrows()]
        horizon_block = (
            "<h3>多持有期对比：5 / 10 / 20 日</h3>"
            + charts.bar_chart(_labels, _vals, height=220,
                               formatter=lambda v: f"{v:+.2f}%",
                               caption="毛价差/期（未扣多空两条腿成本）；「·中性」为 10 日持有期做市值中性化后的对照，"
                                       "两者差即「押注市值」的贡献。")
        )

    neutral_block = ""
    if report.ic_neutral is not None and not report.ic_neutral.empty:
        neutral_block = f"<h3>市值 / 行业中性化后的 IC</h3>{neutral_table}"
    # 行业暴露表单独一整行（原与市值三分组并排；市值表已合并进分层 IC）
    size_block = industry_table if industry_rows else ""

    inc_block = ""
    if inc_rows:
        inc_block = f"<h3>增量性：与同类因子有多像</h3><p>{_esc(inc_summary)}</p>{inc_table}{inc_note}"
    trade_block = f"<h3>可成交性（买入端）</h3>{trade_table}"
    sell_block = f"<h3>可成交性（卖出端：跌停/停牌顺延）</h3>{sell_table}" if report.sell_side else ""
    if report.cost_table is not None and report.capacity:
        cost_block = f"<h3>成本敏感性与容量</h3><div class=\"grid2\">{cost_table}{cap_table}</div>"
    elif report.cost_table is not None:
        cost_block = f"<h3>成本敏感性</h3>{cost_table}"
    elif report.capacity:
        cost_block = f"<h3>容量</h3>{cap_table}"
    else:
        cost_block = ""

    # ---------------- 评级卡
    score_svg = charts.score_bars(rating["items"], width=980, caption="")
    verdict_items = "".join(f"<li>{_esc(v)}</li>" for v in rating["vetoes"]) or "<li>无</li>"
    quad_svg = charts.quadrant(
        rating["space_score"], rating["time_score"],
        x_label="空间区分度", y_label="时间稳定性",
        threshold_x=60, threshold_y=60,
        caption="中线=60；x=空间区分度，y=时间稳定性",
    )
    cap_note = (
        f"原始分 {rating['score']:.1f} 对应 {rating['raw_grade']} 级；"
        f"触发 {len(rating['vetoes'])} 条一票否决，封顶为 {rating['grade']} 级。"
        if rating.get("capped") else
        (
            f"原始分 {rating['score']:.1f} 对应 {rating['raw_grade']} 级；"
            f"触发 {len(rating['vetoes'])} 条否决，但原始等级为 {rating['raw_grade']}，"
            "封顶规则不改变结果。"
            if rating.get("vetoes") else
            f"原始分 {rating['score']:.1f}，无否决项，评级 {rating['grade']}。"
        )
    )

    sig_years = []
    if report.ic_by_year_ci is not None:
        for year, rec in report.ic_by_year_ci.iterrows():
            if np.isfinite(rec["ci_lo"]) and (rec["ci_lo"] > 0 or rec["ci_hi"] < 0):
                sig_years.append((int(year), rec["mean"]))
    dual_ic = float("nan")
    if report.ic_neutral is not None and "双中性" in report.ic_neutral.index:
        dual_ic = float(report.ic_neutral.loc["双中性", "mean"])
    # 衰减结论由数据生成，禁止写死「h=20 最强」
    decay = report.decay.dropna() if report.decay is not None else pd.Series(dtype="float")
    if len(decay):
        peak_h = int(decay.idxmax())
        peak_val = float(decay.max())
        h5 = float(decay.get(5, float("nan")))
        if peak_h == int(decay.index.max()) and peak_val > abs(h5) * 1.2:
            decay_sentence = f"IC 在最长持有期 h={peak_h} 最高（{peak_val:+.3f}），未见衰减。"
        elif abs(peak_val) < 0.005:
            decay_sentence = "各持有期 IC 均接近 0，衰减曲线无经济含义。"
        else:
            decay_sentence = f"IC 在 h={peak_h} 见顶（{peak_val:+.3f}），h=5 为 {h5:+.3f}。"
    else:
        decay_sentence = "持有期样本不足，无法判断衰减。"
    half = report.ic_half or {}
    half_sentence = ""
    if half:
        half_sentence = (
            f"前/后半段 IC {half.get('first', float('nan')):+.3f} → {half.get('second', float('nan')):+.3f}"
            + ("（同号）" if half.get("same_sign") else "（<b>变号</b>）")
            + "；"
        )
    lead_02 = (
        f"<b>本节结论：</b>IC {ic_mean:+.3f}（95% CI {_ci(ic_lo, ic_hi, 3)}，独立 t {t_indep:.1f}）；"
        f"{len(report.ic_by_year) if report.ic_by_year is not None else 0} 年中仅 {len(sig_years)} 年 CI 不含 0"
        + ("、且方向相反" if len({1 if v > 0 else -1 for _, v in sig_years}) > 1 else "")
        + "；" + half_sentence + decay_sentence
    )
    peer_name = ""
    if report.peers is not None and len(report.peers):
        peer_name = str(report.peers.abs().idxmax())
    lead_03 = (
        f"<b>本节结论：</b>G5−G1 毛价差 {float((report.spread_gross or {}).get('mean', np.nan))*100:+.2f}%/期、"
        f"净价差 {float((report.spread_net or {}).get('mean', np.nan))*100:+.2f}%/期"
        f"（成本 {report.cost_mean*100:.2f}%，净价差 t={float((report.spread_net or {}).get('t', np.nan)):.1f}）；"
        f"最大回撤 {report.perf.get('mdd', float('nan'))*100:.1f}%；"
        f"双中性 IC {dual_ic:+.3f}；"
        + (f"最高同类相关来自 {peer_name}（{inc.get('max_corr', float('nan')):.2f}）；" if peer_name else "")
        + f"过滤价值{'成立' if ft.get('useful') else '不成立'}；"
        f"失效归因：{rating.get('attribution', {}).get('label', '—')}。"
    )

    sig_note = ""
    if sig_years:
        sig_note = (
            "显著年份只有 "
            + "、".join(f"{y}（{v:+.3f}）" for y, v in sig_years)
            + "；" + ("两者方向相反，说明不是稳定同向，而是风格切换。" if len({1 if v > 0 else -1 for _, v in sig_years}) > 1
                     else "方向一致。")
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>因子研究报告 · {_esc(report.factor_id)}</title>
<style>{CSS}</style></head>
<body><div class="page">

<div class="masthead">
  <div>
    <div class="eyebrow">Factor Research Note · 因子研究报告</div>
    <h1>{_esc(report.factor_id)}</h1>
    <div class="sub">{_esc(report.category)} · {_esc(report.universe)} ·
      {report.start.strftime('%Y-%m')} – {report.end.strftime('%Y-%m')} ·
      IC 口径 h={primary} · T+1 开盘成交</div>
  </div>
  <div class="badge">
    <div class="grade">{_esc(rating['grade'])}</div>
    <div class="score">{rating['score']:.0f} / 100</div>
  </div>
</div>

<div class="abstract">{summary}</div>
{_direction_banner(report)}
<div class="banner"><b>局部评级（必读）：</b>本报告只在「{_esc(report.universe)}」内有效，
评级与分数<b>禁止跨家族比较</b>；
的等级线不能直接对比。家族内相对位置见 library 汇总。</div>
<div class="kpis">{kpis}</div>

<h2><span class="no">01</span>因子定义与样本</h2>
<p>本报告先回答「因子是什么」，再评估<b>预测力</b>（时间维度）与<b>区分度</b>（空间维度），
并对可交易性、容量与增量性做独立检验。所有收益均按「T 日收盘出信号、T+1 开盘买入」的
可执行价格计算，不使用收盘价成交假设。</p>
{def_table}
<p class="small"><b>数据口径：</b>价格使用后复权；末日覆盖不足前一日 80% 时整日回退；
regime 基准用日线完整的创业板指（399006）；可成交占比的分母为「有因子值且有下一交易日」
的信号。</p>
<h3>样本池构造与幸存者偏差</h3>
{pool_table}
<p class="small">{pool_narrative}</p>
<h3>选样漏斗</h3>
{funnel_table}
{robust_block}
{meta_table}

<h2><span class="no">02</span>时间维度：因子何时有效</h2>
<div class="lead">{lead_02}</div>
<p>时间维度回答三个问题：<b>信号是否持续存在</b>（IC 均值、置信区间与两类 t 值）、
<b>是否跨周期稳定</b>（分年、分市场状态）、<b>衰减速度多快</b>（持有期曲线）。</p>
{rolling_fig}
{yearly_fig}
{decay_fig}
<h3>分市场状态 IC（含置信区间）</h3>
{_table(["市场状态", "天数", "IC（h=%d）" % primary, "95% CI", "NW t"],
        [[k, f"{int(r['n'])}", (f"{r['mean']:+.3f}", _cls(r['mean'])), _ci(r['ci_lo'], r['ci_hi'], 3),
          f"{r['t']:.1f}" if np.isfinite(r['t']) else "—"]
         for k, r in (report.regime_stats.iterrows() if report.regime_stats is not None else [])]
        or [["—", "—", "—", "—", "—"]],
        "注：牛/熊/震荡由创业板指（399006）的 20 日动量与 MA20 位置划分；CI 为 5 日块自助 95% 区间。"
        "「牛市−熊市」行为两者均值差的 2000 次自助分布，p 值为双尾。")}
<p class="small">{_esc(regime_extra)}</p>
<p class="small"><b>口径：</b>状态差异必须用 CI 判断，p &gt; 0.05 时不应声称「因子只在牛市有效」。
若 regime 基准在样本早期缺日，状态标签会集中在后段，把「年份效应」伪装成「状态效应」；
本报告用日线完整的创业板指（399006）覆盖全样本。</p>
<p class="small">熊市与牛市的 IC 差异必须和分年度结果一起读：若牛市样本集中在少数年份，
状态差异可能只是年份效应。下表把两者对齐。</p>
{_table(["年份", "IC", "95% CI", "样本日", "牛市占比", "熊市占比"],
        [[str(y),
          (f"{rec['mean']:+.3f}", _cls(rec['mean'])),
          _ci(rec['ci_lo'], rec['ci_hi'], 3),
          f"{int(rec['n'])}",
          f"{report.regime_share.loc[y].get('bull', 0):.0%}" if report.regime_share is not None and y in report.regime_share.index else "—",
          f"{report.regime_share.loc[y].get('bear', 0):.0%}" if report.regime_share is not None and y in report.regime_share.index else "—"]
         for y, rec in (report.ic_by_year_ci.iterrows() if report.ic_by_year_ci is not None else [])]
        or [["—", "—", "—", "—", "—", "—"]],
        "注：牛市占比=该年交易日中处于牛市状态的比例；用于判断「状态差异」是否被年份结构混淆。")}
<p class="small">{_esc(sig_note)}</p>
{regime_alt_block}

<h2><span class="no">03</span>空间维度：因子在谁身上有效</h2>
<div class="lead">{lead_03}</div>
<p>空间维度检验横截面区分能力：分层是否单调、多空是否有经济意义、
收益是否只是市值/行业暴露，以及扣费、卖出端与容量之后的可行性。</p>
{nav_fig}
{quant_table}
{filter_block}
<h3>多空净值、回撤与分年收益</h3>
{perf_fig}
<div class="grid2">{perf_table}{annual_table}</div>
{horizon_block}{strat_block}{neutral_block}
{size_block}
{inc_block}
{trade_block}
{sell_block}
{cost_block}

<h2><span class="no">04</span>评级与定位</h2>
<div class="verdict">
    <h3>结论：{_esc(rating['grade'])} 级 · {_esc(rating['quadrant'])}</h3>
    <p>{_esc(rating['usage'])}</p>
    <p class="small">评级看综合分与否决，象限看时间×空间；两者不一致时以评级为准，
    象限只说明短板在哪。若评级为 D，归档，不建议单独使用或做过滤。</p>
    <p><b>失效归因：</b>{_esc(rating.get('attribution', {}).get('label', '—'))}
    —— {_esc(rating.get('attribution', {}).get('reason', '—'))}</p>
    <p><b>评级说明：</b>{_esc(cap_note)}</p>
    <p><b>反向评估：</b>{_esc(rev_note)}</p>
    <p><b>一票否决项：</b></p>
    <ul>{verdict_items}</ul>
    <p><b>关键判断：</b></p>
    <ul>{''.join(f'<li>{_esc(n)}</li>' for n in rating['notes'])}</ul>
</div>
<div class="grid2">
  <div>{radar_block}</div>
  <div>
    {quad_svg}
    <p class="small">时间维度 = 100×(0.4×年份 CI 显著率 + 0.3×状态 CI 显著率 + 0.3×滚动 12m IC 同号率)；
    空间维度 = 100×clip(0.7×max(ρ,0) + 0.3×min(1,IC/0.06), 0, 1)。x=空间、y=时间，中线=60；
    右上核心、右下择时、左上过滤、左下淘汰。</p>
    <div class="cap" style="font-size:12px;color:var(--muted);margin-top:6px;">
      <b>图 4-1</b>空间区分度 vs 时间稳定性。
    </div>
  </div>
</div>
{advice_block}
<h3>评级规则（可复核）</h3>
{rules_table}
{veto_table}
<h3>结构化自检（断言）</h3>
{diag_table}
{warn_html}

<h3>重估条件（什么情况下应推翻本结论）</h3>
<ul class="small">
  <li><b>样本池：</b>改用 point-in-time 全 A 池（含退市股）重跑后，IC 与 G5−G1 仍同号且量级不减半；
      否则本结论只适用于「{_esc(report.universe)}」。</li>
  <li><b>中性化：</b>市值+行业双中性后 IC 仍 ≥ 0.015、独立 t ≥ 2，且残差 IC 保留 ≥ 30%；
      否则本因子主要是风格暴露。</li>
  <li><b>价差：</b>G5−G1 毛价差的独立 t（非重叠期）≥ 2 且净价差 ≥ 单期成本；
      否则即使 IC 显著，也不足以覆盖交易成本。</li>
</ul>

<h2><span class="no">05</span>附录：指标明细与方法</h2>
<div class="lead"><b>本节结论：</b>持有期明细同时给 NW-t 与独立 t；所有统计逐日计算、不做全样本池化；
已知局限见文末。</div>
{detail_table}
<p style="font-size:12px;color:var(--muted)">
<b>方法说明</b>：IC 为逐日横截面 Spearman 秩相关；NW t 经 Newey-West 调整（lag = h−1），
独立 t 为每 h 个交易日取一个观测；分层组合等权、非重叠换仓、T+1 开盘买入，
成本按换手率 × 单腿往返 20bp（多空合计 40bp）扣除；市场状态由创业板指（399006）的 20 日动量与 MA20 联合判定；
中性化用逐日横截面回归残差。方向由先验给定，<b>不在本样本内翻转</b>；ρ 与价差用期均值，年化只作展示。
</p>
<p style="font-size:12px;color:var(--muted)">
<b>已知局限</b>：① {("样本按财务覆盖度选取、仅 " + f"{report.n_dates / 242:.1f}" + " 年，财务重述未逐期回溯；") if kind == "financial" else (("样本池为 " + str(selection) + "（不使用固定基准日选池），库内无历史行情的退市股仍缺失；") if kind == "universe" else ("样本池固定于 " + str(pool_date) + "，退市股缺失带来幸存者偏差；"))}
② 多空组合假设可融券卖空，A 股实际融券成本与券源未计入；
③ 行业分类为当前映射，中性化存在前视；
④ ST 状态只能取当前值，历史涨跌停幅度可能低估；
⑤ 容量为量级估计，未建模冲击成本曲线；
⑥ 数据截止日之后的信息未进入报告，实盘使用前需滚动更新。
</p>

<div class="footer">
  <div><span class="tag">RUN</span>{_esc(meta.get('run_id', ''))}
       <span class="tag">DATA</span>{_esc(meta.get('data_version', ''))}</div>
  <div>本报告为因子研究用途，不构成投资建议。</div>
</div>

</div></body></html>"""
    # 空因子/数据不足时，各表直接用 f-string 会留下 +nan% / nan；
    # 统一在出口清洗，保证「数据不足」报告仍然可读。
    html = html.replace("nan%", "—")
    html = re.sub(r"\+?nan\b", "—", html)
    return html
