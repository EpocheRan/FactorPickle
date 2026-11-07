"""导出指数成分股的分钟线为 CSV（12 字段，与日线一致），支持断点续跑。

- 股票池：`index_weight` 里该指数在区间内的成分并集（含调仓）；区间无快照时退回
  该指数在 end 之前的最新一期成分；
- `adj_factor`：按 (code, date) 从 `ohlcv_1d` 关联，分钟价仍是不复权价；
- `turnover`：用日线隐含的流通股本折算，保证日内加总 = 日线换手率；
- 停牌日（当天日线无记录）的"假 bar"直接丢弃。

用法：
    python scripts/export_index_minutes.py \
        --index 000300.SH --start 2025-11-07 --end 2026-11-06 --freq 5min \
        --out output/hs300_5min_1y.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

import pandas as pd

from data_interface.common.config import get_config
from data_interface.common.questdb_client import QuestDBClient
from data_interface.market.sources.zzshare_source import ZzshareMarketSource

COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount",
           "turnover", "adj_factor", "code", "market", "type"]
#: 每种频率每天的理论 bar 数（用于判断缺口）
BARS_PER_DAY = {"1min": 240, "5min": 48, "15min": 16, "30min": 8, "60min": 4}


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if value != value else repr(value)
    if hasattr(value, "strftime"):
        return pd.Timestamp(value).strftime("%Y-%m-%dT%H:%M:%S")
    return str(value)


def load_codes(index_code: str, start: str, end: str) -> list[str]:
    db = QuestDBClient(get_config())
    end_excl = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    rows = db.query(
        f"select distinct code from index_weight where index_code='{index_code}' "
        f"and ts >= '{start}' and ts < '{end_excl}'"
    )
    if not rows:
        # 区间内没有快照（例如只导最近几天）：退回该指数在 end 之前的最新一期成分
        latest = db.query(
            f"select max(ts) mx from index_weight where index_code='{index_code}' and ts < '{end_excl}'"
        )[0][0]
        if latest is None:
            return []
        rows = db.query(
            f"select distinct code from index_weight where index_code='{index_code}' and ts = '{latest}'"
        )
    return sorted({str(r[0]) for r in rows})


def load_daily_maps(start: str, end: str):
    """返回 (adj_map, k_map, day_set)：adj 因子、每单位成交量的换手率、有日线的 (code, day)。"""
    db = QuestDBClient(get_config())
    end_excl = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    rows = db.query(
        "select code, ts, adj_factor, volume, turnover from ohlcv_1d "
        f"where type='stock' and ts >= '{start}' and ts < '{end_excl}'"
    )
    daily = pd.DataFrame(rows, columns=["code", "ts", "adj", "volume", "turnover"])
    daily["code"] = daily["code"].astype(str)
    daily["day"] = pd.to_datetime(daily["ts"]).dt.strftime("%Y-%m-%d")
    adj_map = {(c, d): a for c, d, a in zip(daily["code"], daily["day"], daily["adj"])}
    day_set = set(zip(daily["code"], daily["day"]))
    k_map: dict[tuple[str, str], float] = {}
    for code, group in daily.groupby("code", sort=False):
        group = group.sort_values("day")
        ratio = group["turnover"] / group["volume"].where(group["volume"] > 0)
        ratio = ratio.replace([float("inf"), float("-inf")], pd.NA).ffill().bfill()
        for day, value in zip(group["day"], ratio):
            if pd.notna(value):
                k_map[(str(code), day)] = float(value)
    return adj_map, k_map, day_set


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="000300.SH")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--freq", default="5min", choices=list(BARS_PER_DAY))
    parser.add_argument("--out", default="output/index_minutes.csv")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 只（调试用）")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    progress_path = out.with_suffix(".progress.json")
    progress = {"done": [], "failed": {}}
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    done = set(progress.get("done", []))
    failed = dict(progress.get("failed", {}))

    codes = load_codes(args.index, args.start, args.end)
    if args.limit:
        codes = codes[: args.limit]
    adj_map, k_map, day_set = load_daily_maps(args.start, args.end)
    print(f"index={args.index} codes={len(codes)} window={args.start}..{args.end} "
          f"freq={args.freq} daily={len(day_set):,}", flush=True)

    new_file = not out.exists() or out.stat().st_size == 0
    handle = open(out, "a", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    if new_file:
        writer.writerow(COLUMNS)

    source = ZzshareMarketSource(get_config())
    started = time.time()
    rows_written = 0
    for position, code in enumerate(codes, start=1):
        if code in done:
            continue
        try:
            frame = source.fetch_ohlc(code, args.start, args.end, args.freq)
        except Exception as exc:  # noqa: BLE001
            failed[code] = f"{type(exc).__name__}: {str(exc)[:120]}"
            progress["failed"] = failed
            progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
            print(f"[{position}/{len(codes)}] {code} FAIL {type(exc).__name__}: {exc}", flush=True)
            continue
        if frame is None or frame.empty:
            failed[code] = "empty"
            progress["failed"] = failed
            progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
            print(f"[{position}/{len(codes)}] {code} EMPTY", flush=True)
            continue

        day_series = frame.index.strftime("%Y-%m-%d")
        kept = 0
        for ts, day, row in zip(frame.index, day_series, frame.itertuples(index=False)):
            if (code, day) not in day_set:
                continue  # 停牌日的假 bar
            factor = adj_map.get((code, day))
            k = k_map.get((code, day))
            turnover = float(row.volume) * k if k is not None else None
            writer.writerow([
                fmt(ts), fmt(row.open), fmt(row.high), fmt(row.low), fmt(row.close),
                fmt(row.volume), fmt(row.amount), fmt(turnover), fmt(factor),
                code, str(getattr(row, "market", "") or ""), str(getattr(row, "type", "") or ""),
            ])
            kept += 1
        handle.flush()
        os.fsync(handle.fileno())
        rows_written += kept
        done.add(code)
        progress["done"] = sorted(done)
        progress["failed"] = failed
        progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
        rate = position / max(time.time() - started, 1) * 60
        eta = (len(codes) - position) / max(rate, 0.01)
        print(f"[{position}/{len(codes)}] {code} bars={kept} total={rows_written:,} "
              f"rate={rate:.1f} codes/min eta={eta:.0f}min", flush=True)

    handle.close()
    print(f"done: codes={len(done)} failed={len(failed)} rows={rows_written:,} file={out}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
