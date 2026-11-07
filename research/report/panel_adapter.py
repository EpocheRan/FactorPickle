"""报告取数适配器：把数据层（``research.data.loader``）的结果转成报告要的 ``Panel``。

报告层不再自己写 SQL：本模块只做「PanelData → Panel」的字段映射，
数据获取全部由唯一数据入口完成（分块查询，支持 all_a 全市场）。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from research.data.loader import load_panel as _load_panel_data
from research.data.spec import PanelSpec

from . import panel_cache
from .panel import Panel

logger = logging.getLogger("Research.Report.PanelAdapter")

#: 报告默认池子：全市场（可用 ``--universe`` 覆盖）
DEFAULT_UNIVERSE = "all_a"


def _pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """宽表透视：优先用 pivot（不聚合，C 实现），重复键再退回 pivot_table。

    B 模块的 ``_load_ohlcv`` 已按 (symbol, date) 去重，正常路径不会走到 fallback；
    36 次 pivot_table 每次约 3.9s，换 pivot 后降到亚秒级。
    """
    try:
        return frame.pivot(index="date", columns="symbol", values=column)
    except ValueError:
        return frame.pivot_table(index="date", columns="symbol", values=column, aggfunc="last")


def _extras_wide(panel_data) -> dict:
    """把长表中的两融/资金流列透视成 {列名: 宽表}，供情绪因子使用。"""
    frame = getattr(panel_data, "frame", None)
    if frame is None or frame.empty:
        return {}
    date_col = "date" if "date" in frame.columns else "ts"
    sym_col = "symbol" if "symbol" in frame.columns else "code"
    out = {}
    wanted = ("margin_", "mf_", "float_mv", "total_mv")
    for col in [c for c in frame.columns if c.startswith(wanted)]:
        try:
            out[col] = frame.pivot_table(index=date_col, columns=sym_col,
                                         values=col, aggfunc="last")
        except Exception:  # noqa: BLE001
            continue
    return out


def _market_cap_from_extras(extras: dict, index, columns):
    """从 extras 里的 float_mv 构造市值宽表（单位：元）。"""
    mc = extras.get("float_mv")
    if mc is None:
        return None
    mc = mc.reindex(index=index).reindex(columns=columns)
    if not mc.notna().to_numpy().any():
        return mc
    med = float(mc.stack().median())
    return mc * 10000.0 if med < 1e6 else mc


def _industry_map():
    """东财行业映射（主归属），代码 → 行业名；取数失败返回 None。"""
    try:
        from research.data.loader import _query

        rows = _query(
            "select code, last(board_name) as name from map_stock_board "
            "where is_primary = true group by code"
        )
        if rows is None or rows.empty:
            return None
        return pd.Series(rows["name"].to_numpy(), index=rows["code"].astype(str)).dropna()
    except Exception:  # noqa: BLE001
        return None


def _regime_for(dates: pd.DatetimeIndex):
    """市场状态：以创业板指(399006) 200 日均线 ±3% 划分牛/震荡/熊。

    口径写死在代码里并在报告「数据口径」中标明；取数失败时返回 None（相关小节留空）。
    """
    try:
        from research.data.loader import _query

        rows = _query(
            "select ts, close from ohlcv_1d "
            "where type='idx' and code='399006' order by ts"
        )
        if rows is None or rows.empty:
            return None
        index = pd.to_datetime(rows["ts"], utc=True).dt.tz_convert(None).dt.normalize()
        series = pd.Series(
            pd.to_numeric(rows["close"], errors="coerce").to_numpy(), index=index
        )
        series = series[~series.index.duplicated()].sort_index()
        ma = series.rolling(200, min_periods=120).mean()
        regime = pd.Series("neutral", index=series.index, dtype=object)
        regime[series > ma * 1.03] = "bull"
        regime[series < ma * 0.97] = "bear"
        regime[ma.isna()] = "neutral"
        return regime.reindex(dates).ffill()
    except Exception:  # noqa: BLE001
        return None


def load_panel(
    pool_date: str | None = None,
    pool_size: int | None = None,
    start: str = "2019-01-01",
    end: str | None = None,
    *,
    min_amount: float | None = None,
    max_amount: float | None = None,
    warmup_days: int = 800,
    universe: str | None = None,
    extras: tuple[str, ...] = (),
    use_cache: bool = True,
) -> Panel:
    """带本地缓存的唯一取数入口。"""
    target = universe or DEFAULT_UNIVERSE
    if target == "pool" and pool_date and pool_size:
        target = f"pool:{pool_date}:{int(pool_size)}"
    version = panel_cache.data_version(extras) if use_cache else ""
    cache_key = panel_cache.key(
        universe=target, start=start, end=end, warmup_days=warmup_days,
        extras=extras, min_amount=min_amount, max_amount=max_amount,
        version=version,
    ) if version else ""
    cache_path = panel_cache.CACHE_DIR / f"{cache_key}.pkl" if cache_key else None
    if cache_path is not None and cache_path.exists():
        cached = panel_cache.read(cache_path)
        if isinstance(cached, Panel):
            logger.info("Panel 缓存命中: %s", cache_path)
            return cached
    panel = _build_panel(
        pool_date, pool_size, start, end,
        min_amount=min_amount, max_amount=max_amount, warmup_days=warmup_days,
        universe=universe, extras=extras, target=target,
    )
    if cache_path is not None:
        panel_cache.write(cache_path, panel)
    return panel


def _build_panel(
    pool_date: str | None,
    pool_size: int | None,
    start: str,
    end: str | None,
    *,
    min_amount: float | None,
    max_amount: float | None,
    warmup_days: int,
    universe: str | None,
    extras: tuple[str, ...],
    target: str,
) -> Panel:
    spec = PanelSpec(
        universe=target,
        start=start,
        end=end,
        warmup_days=warmup_days,
        min_names=30,
        extras=extras,
    )
    data = _load_panel_data(spec)
    frame = data.frame
    _extras_map = _extras_wide(data)

    close = _pivot(frame, "hfq_close").sort_index()
    # 保留 warmup 行：长窗因子（如 504 日）必须用预热数据，否则有效样本会晚半年以上。
    # 评估区间裁剪交给 runner（因子算完之后再切）。
    close = close.sort_index()
    columns = close.columns
    open_ = _pivot(frame, "hfq_open").reindex(index=close.index, columns=columns)
    high = _pivot(frame, "hfq_high").reindex(index=close.index, columns=columns) if "hfq_high" in frame else close
    low = _pivot(frame, "hfq_low").reindex(index=close.index, columns=columns) if "hfq_low" in frame else close
    volume = _pivot(frame, "volume").reindex(index=close.index, columns=columns)
    amount = _pivot(frame, "amount").reindex(index=close.index, columns=columns)
    turnover = _pivot(frame, "turnover").reindex(index=close.index, columns=columns)
    close_raw = _pivot(frame, "close").reindex(index=close.index, columns=columns)
    open_raw = _pivot(frame, "open").reindex(index=close.index, columns=columns)
    market_cap = _pivot(frame, "float_mv").reindex(index=close.index, columns=columns) if "float_mv" in frame else None

    benchmark = (1 + close.pct_change(fill_method=None).mean(axis=1)).cumprod()
    benchmark.name = "样本池等权"

    pool_meta = {
        "kind": "universe",
        "selection": target,
        "date": pool_date or start,
        "requested": int(pool_size or len(columns)),
        "candidates": int(len(columns)),
        "board_passed": int(len(columns)),
        "selected": int(len(columns)),
        "rule": f"universe={target}（B 模块按需装配）",
        "boards": "全市场" if target == "all_a" else target,
        "start": str(start),
        "data_start": str(close.index.min().date()),
        "end": str(close.index.max().date()),
        "funnel": [
            {"step": "universe 解析", "n": int(len(data.codes)), "note": target},
            {"step": "区间内有行情", "n": int(len(columns)), "note": "进入报告"},
        ],
        "pit": "index: 为 PIT 成分快照；all_a 为区间内全市场（数据可得性即约束）",
        "survivorship_note": (
            "all_a 含区间内全部有行情的股票，退市股若库存无历史行情仍会缺失。"
        ),
    }
    freshness = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "北京时间 UTC+8",
        "requested_end": str(end or ""),
        "cutoff": str(close.index.max().date()),
        "stale_natural_days": 0,
        "intraday_trimmed": False,
        "partial_last_day": None,
        "last_day_coverage": int(close.notna().sum(axis=1).iloc[-1]),
        "data_version": f"ohlcv_1d:{close.index.max().date()}|symbols:{close.shape[1]}|days:{close.shape[0]}",
        "today": str(pd.Timestamp.today().date()),
    }

    return Panel(
        close=close,
        open=open_,
        industry=_industry_map(),
        market_cap=_market_cap_from_extras(_extras_map, close.index, columns),
        extras=_extras_map,
        regime=_regime_for(close.index),
        symbols=list(columns),
        high=high,
        low=low,
        volume=volume,
        amount=amount,
        turnover=turnover,
        close_raw=close_raw,
        open_raw=open_raw,
        benchmark=benchmark,
        stock_info=None,
        is_st={},
        pool=pool_meta,
        freshness=freshness,
        eval_start=pd.Timestamp(start),
    )


__all__ = ["DEFAULT_UNIVERSE", "load_panel"]
