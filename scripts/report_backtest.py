"""从 ``output/reports/backtest/<日期>/<run_id>/`` 生成单页 HTML 回测报告。

用法::

    python scripts/report_backtest.py --latest
    python scripts/report_backtest.py --run-id 7e873d43ee277cb7
    python scripts/report_backtest.py --latest --out output/reports/backtest/report.html

只读 CSV/JSON，不重跑回测；没有 matplotlib 依赖。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def _latest_run(base: Path) -> Path | None:
    if not base.exists():
        return None
    runs = {
        item.parent
        for pattern in ("metrics.json", "report_data.json")
        for item in base.rglob(pattern)
    }
    return max(runs, key=lambda item: item.stat().st_mtime) if runs else None


def _fmt(value, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    return f"{number:,.{digits}f}"


def _pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _svg(equity: pd.DataFrame, width: int = 920, height: int = 280) -> str:
    if equity.empty or "equity" not in equity.columns:
        return "<p class='muted'>无净值数据</p>"
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date", "equity"]).sort_values("date")
    if frame.empty:
        return "<p class='muted'>无净值数据</p>"
    values = frame["equity"].astype(float).to_numpy()
    lo, hi = float(values.min()), float(values.max())
    span = hi - lo or 1.0
    pad = 34
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    points = []
    for index, value in enumerate(values):
        x = pad + inner_w * index / max(1, len(values) - 1)
        y = pad + inner_h * (1.0 - (value - lo) / span)
        points.append(f"{x:.1f},{y:.1f}")
    line = " ".join(points)
    area = f"{pad},{height - pad} " + line + f" {width - pad},{height - pad}"
    peak = frame["equity"].cummax()
    dd = frame["equity"] / peak - 1.0
    dd_min = float(dd.min())
    start = frame["date"].iloc[0].date()
    end = frame["date"].iloc[-1].date()
    return f"""
    <svg viewBox="0 0 {width} {height}" class="chart" role="img">
      <defs>
        <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#d4a24c" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#d4a24c" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <polygon points="{area}" fill="url(#fill)"/>
      <polyline points="{line}" fill="none" stroke="#d4a24c" stroke-width="2"/>
      <text x="{pad}" y="18" class="axis">净值 {hi:,.0f}</text>
      <text x="{pad}" y="{height - 10}" class="axis">{start}</text>
      <text x="{width - pad}" y="{height - 10}" text-anchor="end" class="axis">{end}</text>
      <text x="{width - pad}" y="18" text-anchor="end" class="axis">最大回撤 {dd_min*100:.2f}%</text>
    </svg>
    """


def _monthly_table(equity: pd.DataFrame) -> str:
    if equity.empty:
        return "<p class='muted'>无月度数据</p>"
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    monthly = frame["equity"].resample("ME").last().pct_change().dropna() * 100.0
    if monthly.empty:
        return "<p class='muted'>无月度数据</p>"
    table = monthly.to_frame("ret")
    table["year"] = table.index.year
    table["month"] = table.index.month
    pivot = table.pivot_table(index="year", columns="month", values="ret", aggfunc="last")
    header = "".join(f"<th>{m:02d}</th>" for m in range(1, 13))
    rows = []
    for year, row in pivot.iterrows():
        cells = []
        for month in range(1, 13):
            value = row.get(month)
            if value is None or value != value:
                cells.append("<td class='muted'>—</td>")
            else:
                cls = "pos" if value > 0 else "neg" if value < 0 else ""
                cells.append(f"<td class='{cls}'>{value:+.2f}</td>")
        rows.append(f"<tr><th>{year}</th>{''.join(cells)}</tr>")
    return f"<table class='grid'><thead><tr><th>年</th>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _orders_block(orders: pd.DataFrame) -> str:
    if orders.empty:
        return "<p class='muted'>无成交</p>"
    frame = orders.copy()
    frame["type"] = frame.get("type", "").astype(str)
    buys = int((frame["type"] == "buy").sum())
    sells = int((frame["type"] == "sell").sum())
    fees = pd.to_numeric(frame.get("fees", 0.0), errors="coerce").fillna(0.0).sum()
    symbols = frame.get("symbol", pd.Series(dtype=str)).astype(str).value_counts().head(8)
    chips = "".join(
        f"<span class='chip'>{html.escape(str(code))} × {int(count)}</span>"
        for code, count in symbols.items()
    )
    return (
        f"<div class='kpis small'><div><b>{buys}</b><span>买入笔数</span></div>"
        f"<div><b>{sells}</b><span>卖出笔数</span></div>"
        f"<div><b>{fees:,.2f}</b><span>总费用</span></div></div>"
        f"<p class='muted'>成交最活跃：</p><div>{chips}</div>"
    )


def render(run_dir: Path) -> str:
    metrics_payload = _read_json(run_dir / "metrics.json")
    metrics = metrics_payload.get("metrics") or {}
    meta = metrics_payload.get("meta") or {}
    equity = _read_csv(run_dir / "equity.csv")
    orders = _read_csv(run_dir / "orders.csv")
    selections = _read_csv(run_dir / "selections.csv")

    title = html.escape(str(metrics_payload.get("run_id") or run_dir.name))
    kpis = [
        ("年化收益", _pct(metrics.get("annualized_return_pct"))),
        ("累计收益", _pct(metrics.get("total_return_pct"))),
        ("最大回撤", _pct(metrics.get("max_drawdown_pct"))),
        ("Sharpe", _fmt(metrics.get("sharpe"))),
        ("年化波动", _pct(metrics.get("volatility_pct"))),
        ("期末权益", _fmt(metrics.get("final_equity"), 0)),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='v'>{value}</div><div class='k'>{label}</div></div>"
        for label, value in kpis
    )
    meta_rows = [
        ("run_id", title),
        ("engine", meta.get("engine", "—")),
        ("freq", meta.get("freq", "—")),
        ("bar_frequency", meta.get("bar_frequency", "—")),
        ("benchmark", meta.get("benchmark") or "—"),
        ("benchmark_used", meta.get("benchmark_used", "—")),
        ("universe_size", meta.get("universe_size", "—")),
        ("rejected", meta.get("rejected", "—")),
    ]
    meta_html = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in meta_rows
    )
    last_picks = ""
    if not selections.empty and "date" in selections.columns:
        last_date = pd.to_datetime(selections["date"], errors="coerce").max()
        picks = selections[pd.to_datetime(selections["date"], errors="coerce") == last_date]
        if not picks.empty:
            rows = "".join(
                f"<tr><td>{html.escape(str(row.get('symbol', '')))}</td>"
                f"<td>{_fmt(row.get('score'))}</td><td>{_fmt(row.get('rank'))}</td>"
                f"<td>{_fmt(row.get('weight'), 4)}</td></tr>"
                for _, row in picks.head(20).iterrows()
            )
            last_picks = (
                f"<h3>最新持仓（{last_date.date() if hasattr(last_date, 'date') else last_date}）</h3>"
                "<table class='grid'><thead><tr><th>代码</th><th>score</th><th>rank</th><th>weight</th>"
                f"</tr></thead><tbody>{rows}</tbody></table>"
            )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>回测报告 · {title}</title>
<style>
:root{{--bg:#0d1117;--panel:#151b23;--line:#222b36;--ink:#e6edf3;--muted:#8b949e;
--pos:#3fb950;--neg:#f85149;--acc:#d4a24c}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:32px 22px 70px}}
h1{{font-size:22px;margin:0 0 4px}} h3{{font-size:14px;color:var(--muted);
font-weight:500;letter-spacing:.6px;margin:28px 0 10px;text-transform:uppercase}}
.sub{{color:var(--muted);font-size:12.5px}}
.kpis{{display:flex;flex-wrap:wrap;gap:26px;margin:22px 0;padding:16px 0;
border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}
.kpi .v{{font-size:21px;font-weight:600}} .kpi .k{{font-size:11.5px;color:var(--muted);margin-top:2px}}
.kpis.small{{gap:34px}} .kpis.small b{{font-size:16px;display:block}}
.kpis.small span{{font-size:11.5px;color:var(--muted)}}
.chart{{width:100%;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px}}
.axis{{fill:var(--muted);font-size:11px}}
table.grid{{width:100%;border-collapse:collapse;font-size:12.5px}}
table.grid th,table.grid td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}}
table.grid th:first-child,table.grid td:first-child{{text-align:left}}
table.grid th{{color:var(--muted);font-weight:500;background:#11161d}}
.pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .muted{{color:var(--muted)}}
.chip{{display:inline-block;border:1px solid var(--line);border-radius:999px;
padding:2px 9px;margin:3px 5px 0 0;color:var(--muted);font-size:11.5px}}
.meta{{width:auto;min-width:420px}} .meta th{{text-align:left;width:150px}}
</style></head><body><div class="wrap">
<h1>回测报告</h1><div class="sub">{title} · engine {html.escape(str(meta.get('engine','—')))}</div>
<div class="kpis">{kpi_html}</div>
<h3>净值与回撤</h3>
{_svg(equity)}
<h3>月度收益（%）</h3>
{_monthly_table(equity)}
<h3>成交</h3>
{_orders_block(orders)}
{last_picks}
<h3>运行信息</h3>
<table class="grid meta"><tbody>{meta_html}</tbody></table>
<p class="muted" style="margin-top:26px">由 scripts/report_backtest.py 从
{html.escape(str(run_dir))} 生成；指标与净值来自回测输出，不重跑。</p>
</div></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从回测输出生成 HTML 报告")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    base = ROOT / "output" / "reports" / "backtest"
    legacy = ROOT / "output" / "backtest"
    if not base.exists():
        base = legacy
    if args.run_id:
        matches = list(base.rglob(args.run_id)) if base.exists() else []
        if not matches and legacy.exists():
            matches = list(legacy.rglob(args.run_id))
        run_dir = matches[0] if matches else None
    else:
        run_dir = _latest_run(base) if args.latest else None
    if run_dir is None or not run_dir.exists():
        print("没有找到回测输出；先跑 python -m backtest run ... --out console,csv")
        return 1
    data_path = run_dir / "report_data.json"
    if data_path.exists():
        from backtest.reporting.render import render_report

        data = json.loads(data_path.read_text(encoding="utf-8"))
        target = Path(args.out) if args.out else run_dir / "report.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_report(data), encoding="utf-8")
        print(f"report: {target}")
        return 0
    html_text = render(run_dir)
    target = Path(args.out) if args.out else run_dir / "report.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    print(f"report: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
