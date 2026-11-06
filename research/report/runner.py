"""因子报告编排：取数 → 计算 → 评级 → 渲染。

设计约束
--------
- **不引入绘图第三方库**：图全部是内联 SVG，保证打印/PDF 无损；
- **口径固定**：T+1 开盘买入、双边费率、逐日截面统计；
- **可复现**：``run_id`` 记录数据版本与参数，重跑结果一致；
- **时间可核验**：生成时间统一北京时间，并单列行情截止日与滞后天数。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from data_interface.common.timeutil import now_cn

from . import definitions, diagnostics as diag, metrics, rating as rating_mod, render
from .db import query as _query  # noqa: F401 - 兼容旧调用点
from .panel import flow_factor_panel, Panel, financial_factor_panel, load_panel, peer_panels, price_factor_panel

logger = logging.getLogger("Research.Report")


# ---------------------------------------------------------------- 因子库（内联计算）
def compute_factor(factor_id: str, close: pd.DataFrame) -> pd.DataFrame:
    """旧版简化因子计算（momN / momentum.ret@N / volatility.Nd），保留兼容。"""
    key = str(factor_id).strip()
    rets = close.pct_change(fill_method=None)

    if key.startswith("mom") and key[3:].isdigit():
        n = int(key[3:])
        return close / close.shift(n) - 1
    if key.startswith("momentum.ret@"):
        n = int(key.split("@")[1])
        return close / close.shift(n) - 1
    if key.startswith("reversal.short@"):
        n = int(key.split("@")[1])
        return -(close / close.shift(n) - 1)
    if key.startswith("volatility.") and key.endswith("d"):
        n = int(key.split(".")[1][:-1])
        return rets.rolling(n).std()
    raise ValueError(f"未知因子: {factor_id}")



def _universe_label(panel: Panel, pool_size: int, pool_date: str) -> str:
    """展示用池子标签：从 panel.pool 的真实口径生成，不再写死点位池。"""
    pool = panel.pool or {}
    selection = str(pool.get("selection", "")).strip()
    boards = str(pool.get("boards", "")).strip()
    n = len(panel.symbols)
    if selection == "all_a" or selection.startswith("all_a"):
        return f"全市场（all_a，{n:,} 只）"
    if selection.startswith("index:"):
        return f"{selection} PIT 成分（{n:,} 只）"
    if boards:
        return f"{boards}（{n:,} 只）"
    return f"{selection or '样本池'}（{n:,} 只）"


def _industry_frame(industry, close):
    """把「代码→行业」映射广播成与面板同形状的 DataFrame（供中性化与分层使用）。"""
    if industry is None or not isinstance(industry, pd.Series):
        return industry
    cols = close.columns
    mapping = industry.reindex(cols).to_numpy()
    data = np.tile(mapping, (len(close), 1))
    return pd.DataFrame(data, index=close.index, columns=cols)


def _slice_to_eval(panel, factor, start: str):
    """因子算完后裁到评估区间：面板与因子同时从 start 开始。"""
    start_ts = pd.Timestamp(start).normalize()
    idx = panel.close.index[panel.close.index >= start_ts]
    if len(idx) == 0:
        return panel, factor
    sliced = {}
    for name, value in vars(panel).items():
        if isinstance(value, pd.DataFrame):
            sliced[name] = value.reindex(index=idx)
        else:
            sliced[name] = value
    return Panel(**sliced), factor.reindex(index=idx)


def _resolve_panel(factor_id: str, pool_date: str, pool_size: int, start: str,
                   end: str | None, extras: tuple[str, ...] = ()) -> tuple[Panel, pd.DataFrame]:
    panel = load_panel(pool_date, pool_size, start, end, extras=extras)
    # 同类因子必须在「未切片的全样本面板」上算：切片后的 Panel 只剩评估期的宽表，
    # 长表重建后行情字段会缺失，导致对照组全空、增量性退化成只看规模。
    _peers = None
    try:
        _peers = peer_panels(factor_id, panel)
    except Exception:  # noqa: BLE001 - 对照组失败不应阻断报告
        _peers = None
    category = factor_id.split(".", 1)[0]
    if category in ("quality", "value"):
        frame = financial_factor_panel(factor_id, panel.close, str(panel.close.index.min().date()), str(panel.data_cutoff.date()))
        if frame.empty:
            raise RuntimeError(f"{factor_id} 无物化数据（先跑 backfill_factors.py）")
        return _attach_peers(_slice_to_eval(panel, frame, start), _peers)
    if category == "sentiment":
        return _attach_peers(_slice_to_eval(panel, flow_factor_panel(factor_id, panel), start), _peers)
    return _attach_peers(_slice_to_eval(panel, price_factor_panel(factor_id, panel), start), _peers)



def _attach_peers(pair, peers):
    """把切片前算好的同类因子挂到返回的面板上（切片会丢行情字段）。"""
    panel, factor = pair
    try:
        panel._peers = peers
    except Exception:  # noqa: BLE001
        pass
    return panel, factor


def _pool_symbols(panel: Panel, pool_date: str, pool_size: int) -> tuple[str, ...]:
    """按基准日成交额取前 N；代码由 B 模块按日查询（超集只覆盖评估期，不能用它取历史池）。"""
    from research.eval.data import _pool_codes

    return tuple(_pool_codes(pool_date, int(pool_size)))


def _slice_panel(panel: Panel, symbols: Sequence[str], start: str) -> Panel:
    """从全样本面板切出子池：裁列 + 裁行，并重算该子池的等权基准。"""
    keep = [symbol for symbol in symbols if symbol in panel.close.columns]
    if not keep:
        raise ValueError("子池为空")
    start_ts = pd.Timestamp(start).normalize()

    def cut(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        if not isinstance(frame, pd.DataFrame):
            return frame  # industry 等 Series 字段不参与切片
        sliced = frame.reindex(columns=keep).loc[lambda item: item.index >= start_ts]
        return sliced

    close = cut(panel.close)
    benchmark = (1 + close.pct_change(fill_method=None).mean(axis=1)).cumprod()
    benchmark.name = "样本池等权"
    pool = dict(panel.pool)
    pool.update(
        {
            "selected": len(keep),
            "candidates": len(keep),
            "board_passed": len(keep),
        }
    )
    return Panel(
        close=close,
        open=cut(panel.open),
        market_cap=cut(panel.market_cap),
        industry=cut(panel.industry),
        regime=panel.regime,
        symbols=keep,
        regime_alt=panel.regime_alt,
        high=cut(panel.high),
        low=cut(panel.low),
        volume=cut(panel.volume),
        amount=cut(panel.amount),
        turnover=cut(panel.turnover),
        close_raw=cut(panel.close_raw),
        open_raw=cut(panel.open_raw),
        benchmark=benchmark,
        stock_info=panel.stock_info,
        is_st=panel.is_st,
        pool=pool,
        freshness=panel.freshness,
        eval_start=pd.Timestamp(start),
    )


def _pool_robustness(
    factor_id: str,
    *,
    pool_size: int,
    pool_dates: tuple[tuple[str, str], ...],
    start: str,
    end: str | None,
    direction: int,
    baseline,
    superset: Panel | None = None,
) -> pd.DataFrame:
    """换基准日/起点重构样本池，检验 IC/价差是否还是同一个答案。

    有 ``superset``（全样本）时只在内存里切片，不再重复回表取数。
    """
    rows = [
        {
            "pool_date": baseline.universe,
            "start": str(baseline.start.date()),
            "n": baseline.n_symbols,
            "ic": float(baseline.ic[5].mean()) if 5 in baseline.ic else np.nan,
            "t_indep": float(baseline.ic_stats.loc[5, "t_indep"]) if 5 in baseline.ic_stats.index else np.nan,
            "spread": float(baseline.quantile_returns.get("Q5", np.nan) - baseline.quantile_returns.get("Q1", np.nan)),
            "mono": baseline.monotonicity,
        }
    ]
    for pool_date, start_date in pool_dates:
        try:
            if superset is not None:
                codes = _pool_symbols(superset, pool_date, pool_size)
                panel = _slice_panel(superset, codes, max(start, start_date))
            else:  # pragma: no cover - 兼容未提供超集的老调用
                panel = load_panel(pool_date, pool_size, max(start, start_date), end)
            factor = price_factor_panel(factor_id, panel)
            report = metrics.build_report(
                factor, panel.close, panel.open,
                factor_id=factor_id, category=factor_id.split(".", 1)[0],
                direction=direction, regime=panel.regime,
                market_cap=panel.market_cap, industry=_industry_frame(panel.industry, panel.close),
                full=False,
            )
            rows.append(
                {
                    "pool_date": f"点位池 {pool_size}（{pool_date} 成交额）",
                    "start": str(report.start.date()),
                    "n": report.n_symbols,
                    "ic": float(report.ic[5].mean()) if 5 in report.ic else np.nan,
                    "t_indep": float(report.ic_stats.loc[5, "t_indep"]) if 5 in report.ic_stats.index else np.nan,
                    "spread": float(report.quantile_returns.get("Q5", np.nan) - report.quantile_returns.get("Q1", np.nan)),
                    "mono": report.monotonicity,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("样本池稳健性 %s 失败：%s", pool_date, exc)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 生成报告
def generate(
    factor_id: str,
    *,
    pool_date: str = "2017-11-01",
    pool_size: int = 200,
    start: str = "2019-01-01",
    end: str | None = None,
    universe: str | None = None,
    category: str | None = None,
    direction: int | None = None,
    out_dir: str | Path = "output/reports/factor",
    with_peers: bool = True,
    robustness: bool = True,
    run_id: str | None = None,
    verify_repro: bool = True,
    panel: Panel | None = None,
    neutralize: bool = False,
    horizon: int = 5,
    groups: int = 5,
    eval_horizon: int | None = None,
) -> dict:
    # 批量场景由调用方预加载面板，避免每个因子重复取数
    if panel is None:
        _extras = (("margin_detail", "moneyflow_daily") if factor_id.startswith("sentiment.") else ()) + ("fin_valuation_daily",)
        panel, factor = _resolve_panel(factor_id, pool_date, pool_size, start, end,
                                       extras=_extras)
    else:
        category = factor_id.split(".", 1)[0]
        factor = (
            financial_factor_panel(factor_id, panel.close,
                                   str(panel.close.index.min().date()),
                                   str(panel.data_cutoff.date()))
            if category in ("quality", "value")
            else (
                flow_factor_panel(factor_id, panel)
                if category == "sentiment"
                else price_factor_panel(factor_id, panel)
            )
        )
    definition = definitions.describe(factor_id)
    if direction is None:
        direction = definition.direction
    peers = getattr(panel, "_peers", None) if with_peers else None
    if peers is None and with_peers:
        try:
            peers = peer_panels(factor_id, panel)
        except Exception:  # noqa: BLE001
            peers = None

    report = metrics.build_report(
        factor, panel.close, panel.open,
        factor_id=factor_id, category=category or definition.category,
        direction=direction,
        direction_source=definition.direction_source if direction == definition.direction else "manual",
        universe=universe or _universe_label(panel, pool_size, pool_date),
        regime=panel.regime, regime_alt=panel.regime_alt,
        market_cap=panel.market_cap, industry=panel.industry,
        amount=panel.amount, benchmark=panel.benchmark, peers=peers,
        definition=definition.as_dict(), pool=panel.pool, freshness=panel.freshness,
        is_st=panel.is_st,
        neutralize=neutralize,
        primary_horizon=horizon,
        groups=groups,
    )
    if robustness:
        report.pool_robustness = _pool_robustness(
            factor_id,
            pool_size=pool_size,
            pool_dates=(
                ("2019-01-02", "2019-01-02"),
                ("2021-01-04", "2021-01-04"),
                ("2023-01-03", "2023-01-03"),
            ),
            start=start,
            end=end,
            direction=direction,
            baseline=report,
            superset=panel,
        )
    if verify_repro:
        # 复现检查已在 metrics.build_report 内部用同一因子对象与参数完成
        report.attribution = diag.attribute_failure(report)
    try:
        report.extra = metrics.long_only_stats(factor, panel, direction, horizon)
    except Exception:  # noqa: BLE001 - 附加层失败不影响主报告正文
        report.extra = None
    score = rating_mod.rate(report)

    run_hash = hashlib.sha1(
        f"{factor_id}|{start}|{panel.data_cutoff.date()}|{pool_size}|{len(panel.close)}".encode()
    ).hexdigest()[:8]
    meta = {
        "generated_at": f"{panel.freshness.get('generated_at', now_cn().strftime('%Y-%m-%d %H:%M:%S'))}"
                        f"（{panel.freshness.get('timezone', 'UTC+8')}）",
        "run_id": run_id or f"{now_cn().strftime('%Y%m%dT%H%M')}_{run_hash}",
        "data_version": f"ohlcv_1d:{panel.data_cutoff.date()}",
    }

    html = render.render(report, score, meta)
    base = Path(out_dir)
    target = base if len(base.name) == 10 and base.name[4] == "-" and base.name[7] == "-" else (
        base / now_cn().date().isoformat()
    )
    target.mkdir(parents=True, exist_ok=True)
    safe = factor_id.replace(".", "_").replace("@", "_")
    html_path = target / f"{safe}.html"
    html_path.write_text(html, encoding="utf-8")

    payload = build_payload(report, score, meta, definition.as_dict(), panel)
    json_path = target / f"{safe}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    logger.info("报告已生成: %s（评级 %s / %s 分）", html_path, score["grade"], score["score"])
    return {"html": html_path, "json": json_path, "rating": score, "report": report, "meta": meta}


def build_payload(report, score: dict, meta: dict, definition: dict, panel) -> dict:
    """HTML 与 JSON 必须同源：同一个 ``run_id``、同一组报告对象。"""
    return {
        "run_id": meta["run_id"],
        "generated_at": meta.get("generated_at", ""),
        "data_version": meta.get("data_version", ""),
        "factor": report.factor_id,
        "category": report.category,
        "family": diag.family_of(report.factor_id),
        "direction": report.direction,
        "direction_source": report.direction_source,
        "definition": definition,
        "rating": {k: v for k, v in score.items() if k not in ("items", "rules")},
        "attribution": score.get("attribution") or report.attribution,
        "diagnostics": [c.as_dict() for c in (report.diagnostics or [])],
        "checks_status": diag.overall_status(report.diagnostics or []),
        "ic_stats": report.ic_stats.round(6).to_dict(orient="index"),
        "ic_half": report.ic_half,
        "ic_by_year": report.ic_by_year.round(6).to_dict() if report.ic_by_year is not None else {},
        "ic_neutral": report.ic_neutral.round(6).to_dict(orient="index") if report.ic_neutral is not None else {},
        "monotonicity": report.monotonicity,
        "monotonicity_net": report.monotonicity_net,
        "quantile_returns": report.quantile_returns,
        "quantile_returns_gross": report.quantile_returns_gross,
        "spread_gross": report.spread_gross,
        "spread_net": report.spread_net,
        "cost_mean": report.cost_mean,
        "sigma_5d": report.sigma_5d,
        "transmission": report.transmission,
        "perf": report.perf,
        "long_short": report.long_short,
        "annual_returns": report.annual_returns.round(6).to_dict() if report.annual_returns is not None else {},
        "tradeability": report.tradeability,
        "sell_side": report.sell_side,
        "capacity": report.capacity,
        "incrementality": {k: v for k, v in report.incrementality.items() if k not in ("peers", "residual")},
        "warnings": report.warnings,
        "pool": report.pool,
        "freshness": report.freshness,
        "sample": {
            "start": str(report.start.date()),
            "end": str(report.end.date()),
            "n_symbols": report.n_symbols,
            "n_dates": report.n_dates,
            "coverage": report.coverage,
            "universe": report.universe,
        },
    }


__all__ = ["Panel", "build_payload", "compute_factor", "generate", "load_panel"]
