"""backfill 阶段实现。

每个阶段只做三件事：取数（api / data_interface 服务）、写入（write / 服务仓储）、
报进度。不写 SQL（用 backfill.sql）、不管切片（write 负责）、不管 NaN（write 负责）。

P0 基础资料阶段（``p0`` 及 8 个单数据集阶段）直接复用
``acquisition.datasets.AShareSyncService`` 的 sync_* 方法：游标、pending 重试
与任务状态都在那里，回补与增量走同一条代码路径。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

from data_interface.basic.models import P0_DATASETS
from data_interface.financial.fields import (
    VALUATION_COMPANION_FIELDS,
    VALUATION_TARGET_FIELDS,
)

from . import PhaseResult, Scope, gap_report, max_ts, phase, sql, trading_days, write

logger = logging.getLogger("acquisition.backfill.phases")

INDEX_CODES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000016.SH": "上证50",
    "000001.SH": "上证指数",
    "399006.SZ": "创业板指",
}


@phase("index_weight", tables=("index_weight",))
def index_weight(api, scope: Scope) -> PhaseResult:
    """按半年块补指数成分；续跑判据是 max(ts)，不是行数。"""
    if scope.dry_run:
        return PhaseResult(
            phase="index_weight", rows=0,
            gaps=[gap_report("index_weight", scope.start, scope.end)],
        )
    total = 0
    started = pd.Timestamp(scope.end) - pd.Timedelta(days=45)
    for index_code, name in INDEX_CODES.items():
        latest = max_ts("index_weight", where=f"index_code='{index_code}'")
        if latest is not None and latest >= started:
            logger.info("  %s(%s) 已覆盖到 %s，跳过", name, index_code, latest.date())
            continue
        chunks = _half_year_chunks(scope.start, scope.end)
        if latest is not None:
            chunks = [(a, b) for a, b in chunks if pd.Timestamp(b) > latest]
        written = 0
        for start, end in chunks:
            frame = api.call("index_weight", index_code=index_code,
                             start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            if frame is None or frame.empty:
                continue
            rows = []
            for r in frame.itertuples(index=False):
                weight = pd.to_numeric(getattr(r, "weight", None), errors="coerce")
                if pd.isna(weight):
                    continue
                rows.append((pd.Timestamp(str(r.trade_date)).to_pydatetime(),
                             index_code, str(r.con_code)[:6], float(weight)))
            written += write("index_weight", ["ts", "index_code", "code", "weight"], rows,
                             chunk=scope.chunk)
        total += written
        logger.info("  %s(%s): %s 行", name, index_code, f"{written:,}")
    return PhaseResult(phase="index_weight", rows=total)


@phase("valuation", tables=("fin_valuation_daily",))
def valuation(api, scope: Scope) -> PhaseResult:
    """按交易日补估值日表（sync_valuation 只向前增量，不能回补）。

    写入前把 Tushare 的原始单位换成 canonical 单位（万股 -> 股、万元 -> 元、
    百分数 -> 小数），与 ``normalize_daily_basic`` 保持一致；否则该阶段再次
    运行时会把 2017-2022 的 10,000x / 100x 单位缺陷重新写回表里。
    """
    if scope.dry_run:
        return PhaseResult(
            phase="valuation", rows=0,
            gaps=[gap_report("fin_valuation_daily", scope.start, scope.end)],
        )
    cols = ["ts", "code", "close", "total_share", "market_cap", "float_market_cap",
            "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield",
            "source", "formula_version", "ingested_at"]
    days = trading_days(scope.start, scope.end)
    logger.info("valuation 交易日 %s 天（%s ~ %s）", len(days), scope.start, scope.end)
    total = 0
    now = pd.Timestamp.now().to_pydatetime()
    for index, day in enumerate(days, 1):
        frame = api.call(
            "daily_basic", trade_date=day.replace("-", ""),
            fields="ts_code,trade_date,close,total_share,total_mv,circ_mv,"
                   "pe_ttm,pb,ps_ttm,dv_ttm",
        )
        if frame is None or frame.empty:
            continue
        ts_val = pd.Timestamp(day).to_pydatetime()
        rows = [
            (ts_val, str(r.ts_code)[:6], r.close,
             _canonical(r.total_share, 10_000.0), _canonical(r.total_mv, 10_000.0),
             _canonical(r.circ_mv, 10_000.0),
             r.pe_ttm, r.pb, r.ps_ttm, _canonical(r.dv_ttm, 0.01),
             "tushare.daily_basic", "backfill.v1", now)
            for r in frame.itertuples(index=False)
        ]
        total += write("fin_valuation_daily", cols, rows, chunk=scope.chunk)
        if index % 50 == 0 or index == len(days):
            logger.info("  [%s/%s] %s 累计 %s 行", index, len(days), day, f"{total:,}")
    return PhaseResult(phase="valuation", rows=total)


def _canonical(value, scale: float):
    """Tushare 原始值 -> canonical 单位；缺失返回 None（不让 NaN 进 SQL）。"""
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return float(number) * scale


VALUATION_DERIVED_FIELDS = list(VALUATION_TARGET_FIELDS) + list(VALUATION_COMPANION_FIELDS)
#: 逐月续跑状态（只记录已完成月份，不写任何业务数据）。
VALUATION_DERIVED_STATE = "output/factor_lab/backfill/valuation_derived_progress.json"
#: 最近一次重算的完整报告（逐月行数 + 前后覆盖）。
VALUATION_DERIVED_REPORT = "output/factor_lab/backfill/valuation_derived_report.json"


@phase("valuation_derived", tables=("fin_valuation_daily",))
def valuation_derived(api, scope: Scope) -> PhaseResult:  # noqa: ARG001 - 纯本地重算
    """原地重算 ``fin_valuation_daily`` 的全部派生列（零 Tushare 请求）。

    公式与 ``FinancialMetricsCalculator`` 完全一致，只额外修复 2017-2022 行的
    ×10,000 / ÷100 单位缺陷。写入只做 ``INSERT``，靠 ``(ts, code, source)``
    DEDUP 覆盖同键行，行数不变；按自然月切片、按月记录进度，可随时续跑。
    """
    from data_interface.financial.service import FinancialDataService

    months = _month_chunks(scope.start, scope.end)
    state_path = Path(VALUATION_DERIVED_STATE)
    state = {} if scope.fresh else _load_valuation_state(state_path, scope)
    completed = set(state.get("months") or [])
    if scope.fresh and state_path.exists():
        logger.info("valuation_derived --fresh：忽略并重置 %s", state_path)
        completed = set()
        _save_valuation_state(state_path, scope, completed, {})
    todo = [start for start, _ in months if start[:7] not in completed]
    logger.info(
        "valuation_derived %s 个月（%s ~ %s），已完成 %s，本次处理 %s%s",
        len(months), scope.start, scope.end, len(completed), len(todo),
        "（dry-run）" if scope.dry_run else "",
    )
    if scope.dry_run:
        service = FinancialDataService()
        # 覆盖报告用同一个仓储实例（不再新开一个连接），只读一条聚合 SQL。
        coverage = {
            "table": "fin_valuation_daily(derived)",
            **service.repository.valuation_field_coverage(
                scope.start, scope.end, VALUATION_DERIVED_FIELDS
            ),
        }
        # dry-run 仍然完整跑一遍计算，只是不写库：报告是预测，不是实际写入。
        result = service.rebuild_valuation_derived(
            scope.start, scope.end, codes=list(scope.codes) or None,
            chunk=scope.chunk, dry_run=True, skip_months=completed,
        )
        return PhaseResult(
            phase="valuation_derived", rows=0,
            gaps=[gap_report("fin_valuation_daily", scope.start, scope.end), coverage],
            details=result,
        )

    service = FinancialDataService()

    def _record(detail: dict) -> None:
        completed.add(detail["month"])
        _save_valuation_state(state_path, scope, completed, detail)

    result = service.rebuild_valuation_derived(
        scope.start, scope.end, codes=list(scope.codes) or None,
        chunk=scope.chunk, dry_run=False, skip_months=completed,
        on_month=_record,
    )
    report_path = Path(VALUATION_DERIVED_REPORT)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    totals = result["totals"]
    logger.info(
        "valuation_derived 完成：读 %s 行，写 %s 行，%s 秒",
        f"{totals['rows_read']:,}", f"{totals['rows_written']:,}", result["seconds"],
    )
    for field, stat in result["measured"]["fields"].items():
        logger.info("  %-22s 覆盖 %6.2f%%（%s 行非空）",
                    field, stat["coverage"] * 100, f"{stat['nonnull']:,}")
    return PhaseResult(
        phase="valuation_derived",
        rows=int(totals["rows_written"]),
        details=result,
        gaps=[gap_report("fin_valuation_daily", scope.start, scope.end)],
    )


def _load_valuation_state(path: Path, scope: Scope) -> dict:
    """读取续跑状态；区间/代码集不同则视为全新运行。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if (
        payload.get("start") != scope.start
        or payload.get("end") != scope.end
        or tuple(payload.get("codes") or ()) != tuple(scope.codes)
    ):
        logger.info("valuation_derived 状态与本次区间不同，忽略 %s", path)
        return {}
    return payload


def _save_valuation_state(path: Path, scope: Scope, completed: set[str], detail: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "factor_lab.valuation_derived_state.v1",
        "start": scope.start,
        "end": scope.end,
        "codes": list(scope.codes),
        "months": sorted(completed),
        "updated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "last": {
            "month": detail.get("month"),
            "rows_read": detail.get("rows_read"),
            "rows_written": detail.get("rows_written"),
            "seconds": detail.get("seconds"),
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)



def _half_year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """按半年切块（与旧实现一致）。"""
    begin = pd.Timestamp(start).normalize()
    finish = pd.Timestamp(end).normalize()
    chunks: list[tuple[str, str]] = []
    cursor = pd.Timestamp(year=begin.year, month=1, day=1)
    while cursor <= finish:
        half_end = cursor + pd.DateOffset(months=6) - pd.Timedelta(days=1)
        lo = max(cursor, begin)
        hi = min(half_end, finish)
        if lo <= hi:
            chunks.append((str(lo.date()), str(hi.date())))
        cursor = cursor + pd.DateOffset(months=6)
    return chunks


@phase("daily", tables=("ohlcv_1d",))
def daily(api, scope: Scope) -> PhaseResult:
    """按交易日补日线：先查每日已有行数，不足 min_codes 才拉。"""
    if scope.dry_run:
        return PhaseResult(
            phase="daily", rows=0,
            gaps=[gap_report("ohlcv_1d", scope.start, scope.end, where="type='stock'")],
        )
    from data_interface.market.fetcher import MarketDataFetcher

    days = trading_days(scope.start, scope.end)
    counts = _existing_counts(days)
    todo = [d for d in days if counts.get(d, 0) < scope.min_codes]
    logger.info("daily 目标 %s 天，已达标 %s 天，待补 %s 天（阈值 %s 只）",
                len(days), len(days) - len(todo), len(todo), scope.min_codes)
    total = 0
    fetcher = MarketDataFetcher()
    try:
        for index, day in enumerate(todo, 1):
            compact = day.replace("-", "")
            frame = api.call("daily", trade_date=compact)
            if frame is None or frame.empty:
                continue
            adj = api.call("adj_factor", trade_date=compact)
            basic = api.call("daily_basic", trade_date=compact,
                             fields="ts_code,trade_date,turnover_rate,float_share")
            adj_map = dict(zip(adj["ts_code"], adj["adj_factor"])) if adj is not None and not adj.empty else {}
            basic_map = dict(zip(basic["ts_code"], basic["turnover_rate"])) if basic is not None and not basic.empty else {}
            out = pd.DataFrame({
                "ts": pd.to_datetime(frame["trade_date"], format="%Y%m%d"),
                "open": pd.to_numeric(frame["open"], errors="coerce"),
                "high": pd.to_numeric(frame["high"], errors="coerce"),
                "low": pd.to_numeric(frame["low"], errors="coerce"),
                "close": pd.to_numeric(frame["close"], errors="coerce"),
                "volume": pd.to_numeric(frame["vol"], errors="coerce") * 100,
                "amount": pd.to_numeric(frame["amount"], errors="coerce") * 1000,
                "turnover": frame["ts_code"].map(basic_map),
                "adj_factor": frame["ts_code"].map(adj_map),
                "code": frame["ts_code"].str.slice(0, 6),
            })
            out = out.dropna(subset=["open", "close"])
            if out.empty:
                continue
            try:
                total += fetcher.update_ohlc(out, "d")
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 写入失败: %s", day, str(exc)[:100])
            if index % 20 == 0 or index == len(todo):
                logger.info("  [%s/%s] %s 累计 %s 行", index, len(todo), day, f"{total:,}")
    finally:
        fetcher.close()
    return PhaseResult(phase="daily", rows=total)


def _existing_counts(days: list[str]) -> dict[str, int]:
    if not days:
        return {}
    start, end = min(days), max(days)
    rows = sql(
        "select ts, count() from ohlcv_1d "
        f"where type <> 'idx' and ts between '{start}' and '{end}' group by ts",
        timeout=300,
    )
    return {str(r[0])[:10]: int(r[1]) for r in rows}


@phase("delisted", tables=("ohlcv_1d",))
def delisted(api, scope: Scope) -> PhaseResult:
    """补退市股历史行情（消除幸存者偏差）；按代码拉全历史。"""
    if scope.dry_run:
        return PhaseResult(
            phase="delisted", rows=0,
            gaps=[gap_report("ohlcv_1d", scope.start, scope.end, where="type='stock'")],
        )
    from data_interface.market.fetcher import MarketDataFetcher

    basic = api.call("stock_basic", list_status="D",
                     fields="ts_code,symbol,name,list_date,delist_date")
    if basic is None or basic.empty:
        return PhaseResult(phase="delisted", rows=0)
    logger.info("delisted 退市股 %s 只", len(basic))
    total = 0
    fetcher = MarketDataFetcher()
    try:
        for index, row in enumerate(basic.itertuples(index=False), 1):
            start = str(row.list_date or "19910101")
            end = str(row.delist_date or "20261231")
            frame = api.call("daily", ts_code=row.ts_code, start_date=start, end_date=end)
            if frame is None or frame.empty:
                continue
            adj = api.call("adj_factor", ts_code=row.ts_code, start_date=start, end_date=end)
            adj_map = dict(zip(adj["trade_date"], adj["adj_factor"])) if adj is not None and not adj.empty else {}
            out = pd.DataFrame({
                "ts": pd.to_datetime(frame["trade_date"], format="%Y%m%d"),
                "open": pd.to_numeric(frame["open"], errors="coerce"),
                "high": pd.to_numeric(frame["high"], errors="coerce"),
                "low": pd.to_numeric(frame["low"], errors="coerce"),
                "close": pd.to_numeric(frame["close"], errors="coerce"),
                "volume": pd.to_numeric(frame["vol"], errors="coerce") * 100,
                "amount": pd.to_numeric(frame["amount"], errors="coerce") * 1000,
                "turnover": pd.NA,
                "adj_factor": frame["trade_date"].map(adj_map),
                "code": str(row.symbol),
            }).dropna(subset=["open", "close"])
            if out.empty:
                continue
            try:
                total += fetcher.update_ohlc(out, "d")
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 写入失败: %s", row.ts_code, str(exc)[:100])
            if index % 20 == 0 or index == len(basic):
                logger.info("  [%s/%s] 累计 %s 行", index, len(basic), f"{total:,}")
    finally:
        fetcher.close()
    return PhaseResult(phase="delisted", rows=total)


STRUCTURE_COLUMNS: dict[str, list[str]] = {
    "top_list": ["ts", "code", "name", "close", "pct_chg", "turnover_rate", "amount",
                 "l_sell", "l_buy", "l_amount", "net_amount", "reason"],
    "margin_detail": ["ts", "code", "rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl"],
    "block_trade": ["ts", "code", "price", "vol", "amount", "buyer", "seller"],
    "share_float": ["ts", "code", "float_date", "float_share", "float_ratio",
                    "holder_name", "share_type"],
    "repurchase": ["ts", "code", "end_date", "proc", "exp_date", "vol", "amount",
                   "high_limit", "low_limit"],
}


@phase("structure", tables=("top_list", "margin_detail", "block_trade"))
def structure(api, scope: Scope) -> PhaseResult:
    """补结构类数据：龙虎榜 / 两融 / 大宗（按日）+ 解禁 / 回购（按区间）。"""
    if scope.dry_run:
        return PhaseResult(
            phase="structure", rows=0,
            gaps=[gap_report(tb, scope.start, scope.end) for tb in
                  ("top_list", "margin_detail", "block_trade", "share_float", "repurchase")],
        )
    days = trading_days(scope.start, scope.end)
    logger.info("structure 交易日 %s 天（%s ~ %s）", len(days), scope.start, scope.end)
    total = 0

    def _push(table: str, rows: list[tuple]) -> int:
        return write(table, STRUCTURE_COLUMNS[table], rows, chunk=scope.chunk)

    for index, day in enumerate(days, 1):
        compact = day.replace("-", "")
        ts_val = pd.Timestamp(day).to_pydatetime()

        top = api.call("top_list", trade_date=compact)
        if top is not None and not top.empty:
            total += _push("top_list", [
                (ts_val, str(r.ts_code)[:6], r.name, r.close, r.pct_change,
                 r.turnover_rate, r.amount, r.l_sell, r.l_buy, r.l_amount,
                 r.net_amount, r.reason)
                for r in top.itertuples(index=False)])

        margin = api.call("margin_detail", trade_date=compact)
        if margin is not None and not margin.empty:
            total += _push("margin_detail", [
                (ts_val, str(r.ts_code)[:6], r.rzye, r.rqye, r.rzmre, r.rqyl,
                 r.rzche, r.rqchl)
                for r in margin.itertuples(index=False)])

        block = api.call("block_trade", trade_date=compact)
        if block is not None and not block.empty:
            total += _push("block_trade", [
                (ts_val, str(r.ts_code)[:6], r.price, r.vol, r.amount, r.buyer, r.seller)
                for r in block.itertuples(index=False)])

        if index % 20 == 0 or index == len(days):
            logger.info("  [%s/%s] %s 累计 %s 行", index, len(days), day, f"{total:,}")

    for name, fn in ():  # 已移交 periodic 阶段
        frame = api.call(fn, start_date=scope.start.replace("-", ""),
                         end_date=scope.end.replace("-", ""))
        if frame is None or frame.empty:
            logger.warning("  %s 无数据", name)
            continue
        rows = []
        for r in frame.itertuples(index=False):
            base_ts = pd.Timestamp(str(r.ann_date)).to_pydatetime()
            if name == "share_float":
                rows.append((base_ts, str(r.ts_code)[:6],
                             pd.Timestamp(str(r.float_date)).to_pydatetime(),
                             r.float_share, r.float_ratio, r.holder_name, r.share_type))
            else:
                rows.append((base_ts, str(r.ts_code)[:6],
                             pd.Timestamp(str(r.end_date)).to_pydatetime() if r.end_date else None,
                             r.proc,
                             pd.Timestamp(str(r.exp_date)).to_pydatetime() if r.exp_date else None,
                             r.vol, r.amount,
                             getattr(r, "high_limit", None), getattr(r, "low_limit", None)))
        written = _push(name, rows)
        total += written
        logger.info("  %s: %s 行", name, f"{written:,}")
    return PhaseResult(phase="structure", rows=total)


PERIODIC_TABLES = {
    "share_float": ["ts", "code", "float_date", "float_share", "float_ratio",
                    "holder_name", "share_type"],
    "repurchase": ["ts", "code", "end_date", "proc", "exp_date", "vol", "amount",
                   "high_limit", "low_limit"],
    "stk_holdernumber": ["ts", "code", "end_date", "holder_num"],
}


@phase("periodic", tables=("share_float", "repurchase", "stk_holdernumber"))
def periodic(api, scope: Scope) -> PhaseResult:
    """按月分页补解禁 / 回购 / 股东户数（绕开单次返回上限）。"""
    if scope.dry_run:
        return PhaseResult(
            phase="periodic", rows=0,
            gaps=[gap_report(tb, scope.start, scope.end) for tb in PERIODIC_TABLES],
        )
    months = _month_chunks(scope.start, scope.end)
    logger.info("periodic %s 个月（%s ~ %s）", len(months), scope.start, scope.end)
    total = 0
    for index, (start, end) in enumerate(months, 1):
        s, e = start.replace("-", ""), end.replace("-", "")

        frame = api.call("share_float", start_date=s, end_date=e)
        if frame is not None and not frame.empty:
            total += write("share_float", PERIODIC_TABLES["share_float"], [
                (pd.Timestamp(str(r.ann_date)).to_pydatetime(), str(r.ts_code)[:6],
                 pd.Timestamp(str(r.float_date)).to_pydatetime(), r.float_share,
                 r.float_ratio, r.holder_name, r.share_type)
                for r in frame.itertuples(index=False)], chunk=scope.chunk)

        frame = api.call("repurchase", start_date=s, end_date=e)
        if frame is not None and not frame.empty:
            total += write("repurchase", PERIODIC_TABLES["repurchase"], [
                (pd.Timestamp(str(r.ann_date)).to_pydatetime(), str(r.ts_code)[:6],
                 pd.Timestamp(str(r.end_date)).to_pydatetime() if r.end_date else None,
                 r.proc,
                 pd.Timestamp(str(r.exp_date)).to_pydatetime() if r.exp_date else None,
                 r.vol, r.amount, getattr(r, "high_limit", None),
                 getattr(r, "low_limit", None))
                for r in frame.itertuples(index=False)], chunk=scope.chunk)

        frame = api.call("stk_holdernumber", start_date=s, end_date=e)
        if frame is not None and not frame.empty:
            total += write("stk_holdernumber", PERIODIC_TABLES["stk_holdernumber"], [
                (pd.Timestamp(str(r.ann_date)).to_pydatetime(), str(r.ts_code)[:6],
                 pd.Timestamp(str(r.end_date)).to_pydatetime() if r.end_date else None,
                 r.holder_num)
                for r in frame.itertuples(index=False)], chunk=scope.chunk)

        if index % 6 == 0 or index == len(months):
            logger.info("  [%s/%s] %s 累计 %s 行", index, len(months), start, f"{total:,}")
    return PhaseResult(phase="periodic", rows=total)


@phase("financial", tables=("fin_income_statement", "fin_balance_sheet",
                            "fin_cash_flow", "fin_indicator"))
def financial(api, scope: Scope) -> PhaseResult:
    """财务四表：先查覆盖，已完整则跳过（避免重复拉取空转）。"""
    report = [gap_report(tb, scope.start, scope.end, ts_col="period_end")
              for tb in ("fin_income_statement", "fin_balance_sheet",
                          "fin_cash_flow", "fin_indicator")]
    if scope.dry_run:
        return PhaseResult(phase="financial", rows=0, gaps=report)
    years = sorted({y for gap in report for y in gap["years"]})
    if years:
        logger.info("financial 已覆盖年份 %s ~ %s，判定为已完整，跳过", years[0], years[-1])
        return PhaseResult(phase="financial", rows=0, skipped=True, gaps=report)
    raise RuntimeError("财务数据缺失且自动回填未实现；请用旧脚本 --phase p3-financial 逐代码补")


def _month_chunks(start: str, end: str) -> list[tuple[str, str]]:
    begin = pd.Timestamp(start).normalize()
    finish = pd.Timestamp(end).normalize()
    chunks: list[tuple[str, str]] = []
    cursor = begin.replace(day=1)
    while cursor <= finish:
        nxt = cursor + pd.DateOffset(months=1)
        lo = max(cursor, begin)
        hi = min(nxt - pd.Timedelta(days=1), finish)
        if lo <= hi:
            chunks.append((str(lo.date()), str(hi.date())))
        cursor = nxt
    return chunks


@phase("moneyflow", tables=("moneyflow_daily",))
def moneyflow(api, scope: Scope) -> PhaseResult:
    """按交易日回补 moneyflow（复用 data_interface 的 MoneyflowService）。"""
    if scope.dry_run:
        return PhaseResult(phase="moneyflow", rows=0,
                           gaps=[gap_report("moneyflow_daily", scope.start, scope.end)])
    from data_interface.moneyflow.service import MoneyflowService

    days = trading_days(scope.start, scope.end)
    existing = {
        str(r[0])[:10]
        for r in sql(
            "select ts from moneyflow_daily "
            f"where ts between '{scope.start}' and '{scope.end}' group by ts"
        )
    }
    todo = [d for d in days if d not in existing]
    logger.info("moneyflow 目标 %s 天，已有 %s 天，待补 %s 天",
                len(days), len(existing), len(todo))
    service = MoneyflowService()
    total, failed = 0, 0
    for index, day in enumerate(todo, 1):
        try:
            result = service.sync(day, day, max_trade_days=1)
            total += int(result.get("rows_written") or 0)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("%s 回补失败: %s", day, str(exc)[:100])
        if index % 20 == 0 or index == len(todo):
            logger.info("  [%s/%s] %s 累计 %s 行，失败 %s 天",
                        index, len(todo), day, f"{total:,}", failed)
    return PhaseResult(phase="moneyflow", rows=total)


@phase("concept", tables=("concept_board_dim", "concept_board_daily"))
def concept(api, scope: Scope) -> PhaseResult:
    """概念板块：维度快照写入 + 逐板块日线回补（复用 ConceptService）。"""
    if scope.dry_run:
        return PhaseResult(
            phase="concept", rows=0,
            gaps=[gap_report("concept_board_dim", scope.start, scope.end),
                  gap_report("concept_board_daily", scope.start, scope.end)],
        )
    from data_interface.concept.models import BOARD_TYPE_CONCEPT
    from data_interface.concept.service import ConceptService
    from data_interface.common.timeutil import now_cn

    RECONSTRUCTED = "akshare.ths_index.reconstructed"
    service = ConceptService()
    boards = service.boards()
    if not boards:
        logger.warning("concept: 库内无板块元数据，先跑 concept_meta")
        return PhaseResult(phase="concept", rows=0)

    now = now_cn()
    frames = []
    for snapshot in (scope.start, scope.end):
        frames.append(pd.DataFrame([
            {
                "ts": pd.Timestamp(snapshot),
                "board_code": b.board_code,
                "name": b.name,
                "board_type": b.board_type or BOARD_TYPE_CONCEPT,
                "source": RECONSTRUCTED,
                "source_code": b.source_code or b.board_code,
                "status": "active",
                "updated_at": now,
            }
            for b in boards
        ]))
    written = int(service.repository.upsert_boards(pd.concat(frames, ignore_index=True)))
    logger.info("concept-dim: boards=%s rows=%s", len(boards), f"{written:,}")

    total, failed = written, 0
    for index, board in enumerate(boards, 1):
        try:
            total += int(service.sync_one_board_daily(board, scope.start, scope.end) or 0)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("%s 日线失败: %s", board.board_code, str(exc)[:80])
        if index % 50 == 0 or index == len(boards):
            logger.info("  [%s/%s] 累计 %s 行，失败 %s", index, len(boards), f"{total:,}", failed)
    return PhaseResult(phase="concept", rows=total)


@phase("disclosure", tables=("fin_disclosure_calendar", "announcement"))
def disclosure(api, scope: Scope) -> PhaseResult:
    """预约披露日历回补（公告按代码补需传 codes，此处只同步日历）。"""
    if scope.dry_run:
        return PhaseResult(phase="disclosure", rows=0,
                           gaps=[gap_report("fin_disclosure_calendar", scope.start, scope.end)])
    from data_interface.common.config import load_config
    from data_interface.disclosure.service import DisclosureService

    service = DisclosureService(config=load_config())
    result = service.sync_calendar(None, as_of=None)
    rows = int(result.get("rows_written") or 0)
    logger.info("disclosure 日历写入 %s 行", f"{rows:,}")
    for item in (result.get("details") or [])[:5]:
        logger.info("  %s", item)
    return PhaseResult(phase="disclosure", rows=rows)


@phase("boards", tables=("map_stock_board", "dim_board", "concept_board_daily"))
def boards(api, scope: Scope) -> PhaseResult:
    """个股↔板块映射回补（东财 slist）+ 可选自算板块日线。

    代码来源：``--codes`` 显式指定；不传则取库内全部 A 股。
    """
    codes = list(scope.codes)
    if not codes:
        rows = sql("select distinct code from ohlcv_1d where type='stock' order by code")
        codes = [str(r[0]) for r in rows if str(r[0])[:2] in ("60", "00", "30", "68")]
    if not codes:
        logger.warning("boards: 没有可处理的代码")
        return PhaseResult(phase="boards", rows=0)

    if scope.dry_run:
        return PhaseResult(phase="boards", rows=0,
                           gaps=[gap_report("map_stock_board", scope.start, scope.end)])

    from data_interface.common.config import load_config
    from data_interface.concept.service import ConceptService

    service = ConceptService(config=load_config())
    logger.info("boards: 待处理 %s 只，快照日 %s", len(codes), scope.end)
    result = service.sync_stock_boards(codes, as_of=scope.end)
    total = int(result["map_rows"]) + int(result["dim_rows"])
    logger.info("映射写入：%s 只 → map %s 行，dim %s 行，失败 %s 只",
                result["codes"], result["map_rows"], result["dim_rows"], len(result["errors"]))
    if result["errors"]:
        logger.warning("失败样例: %s", result["errors"][:5])
    return PhaseResult(phase="boards", rows=total)


# ---------------------------------------------------------------- P0 基础资料
def _p0_sync(dataset: str, scope: Scope) -> dict:
    """调用 acquisition 的 sync_* 方法处理一批（游标与 pending 由它落库）。"""
    from data_interface.api.runtime import get_sync_service

    service = get_sync_service()
    method = getattr(service, f"sync_{dataset}")
    return method(start_date=scope.start, end_date=scope.end)


def _p0_phase_run(dataset: str, scope: Scope) -> PhaseResult:
    if scope.dry_run:
        return PhaseResult(
            phase=dataset,
            rows=0,
            gaps=[gap_report(dataset, scope.start, scope.end)],
        )
    result = _p0_sync(dataset, scope)
    rows = int(result.get("rows_written") or 0)
    logger.info(
        "%s: 写入 %s 行，done=%s%s",
        dataset,
        f"{rows:,}",
        result.get("done"),
        "（权限不足 BLOCKED）" if result.get("blocked") else "",
    )
    return PhaseResult(
        phase=dataset,
        rows=rows,
        done=bool(result.get("done", True)),
        blocked=(dataset,) if result.get("blocked") else (),
    )


@phase("stock_basic_history", tables=("stock_basic_history",))
def stock_basic_history(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """全市场 L/P/D 名单快照（每次运行一份，DEDUP 覆盖同一天）。"""
    return _p0_phase_run("stock_basic_history", scope)


@phase("namechange", tables=("namechange",))
def namechange(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """历史名称/ST 区间，按公告日分年回补。"""
    return _p0_phase_run("namechange", scope)


@phase("new_share", tables=("new_share",))
def new_share(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """新股发行/上市，按上市日分年回补。"""
    return _p0_phase_run("new_share", scope)


@phase("suspend_d", tables=("suspend_d",))
def suspend_d(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """停复牌明细，按交易日回补。"""
    return _p0_phase_run("suspend_d", scope)


@phase("stk_limit", tables=("stk_limit",))
def stk_limit(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """每日涨跌停价格，按交易日回补。"""
    return _p0_phase_run("stk_limit", scope)


@phase("disclosure_date", tables=("disclosure_date",))
def disclosure_date(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """财报披露计划，按报告期回补。"""
    return _p0_phase_run("disclosure_date", scope)


@phase("forecast", tables=("forecast",))
def forecast(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """业绩预告，按自然日回补（公告可能落在周末）。"""
    return _p0_phase_run("forecast", scope)


@phase("express", tables=("express",))
def express(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """业绩快报，按报告期回补（单次 2000 行，自动翻页）。"""
    return _p0_phase_run("express", scope)


@phase("p0", tables=tuple(P0_DATASETS))
def p0(api, scope: Scope) -> PhaseResult:  # noqa: ARG001
    """P0 基础资料整体回补：按 ``P0_DATASETS`` 顺序各推进一批。"""
    datasets = tuple(scope.datasets) or P0_DATASETS
    unknown = [name for name in datasets if name not in P0_DATASETS]
    if unknown:
        raise KeyError(f"未知 P0 数据集: {unknown}；可用: {list(P0_DATASETS)}")
    if scope.dry_run:
        return PhaseResult(
            phase="p0",
            rows=0,
            gaps=[
                gap_report(dataset, scope.start, scope.end) for dataset in datasets
            ],
        )
    total = 0
    done = True
    blocked: list[str] = []
    errors: list[str] = []
    for dataset in datasets:
        try:
            result = _p0_sync(dataset, scope)
        except Exception as exc:  # noqa: BLE001 - 单个数据集失败不阻断其它
            logger.warning("%s 回补失败: %s", dataset, exc)
            errors.append(f"{dataset}: {exc}")
            done = False
            continue
        total += int(result.get("rows_written") or 0)
        done = done and bool(result.get("done", True))
        if result.get("blocked"):
            blocked.append(dataset)
        logger.info(
            "%s: 写入 %s 行，done=%s%s",
            dataset,
            f"{int(result.get('rows_written') or 0):,}",
            result.get("done"),
            "（权限不足 BLOCKED）" if result.get("blocked") else "",
        )
    if errors:
        logger.warning("p0 本轮错误 %s 个: %s", len(errors), errors[:5])
    return PhaseResult(phase="p0", rows=total, done=done, blocked=tuple(blocked))
