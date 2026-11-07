"""把分钟线 CSV 写入 QuestDB（ohlcv_5m / ohlcv_1m / …），字段与日线一致。

契约：
- 价格是不复权价；`adj_factor` 按 (code, date) 取 `ohlcv_1d` 的当日因子一并写入；
- `turnover` 由 CSV 提供（导出时已按日线隐含流通股本折算）；缺失时按同股最近可用值补；
- 只写 `(code, date)` 在 `ohlcv_1d` 有记录的 bar（停牌日假 bar 不落库）。

用法：
    python scripts/maintenance/import_minutes_csv.py \
        --csv output/hs300_5min_1y.csv --freq 5min
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from data_interface.common.config import get_config
from data_interface.common.questdb_client import QuestDBClient
from data_interface.market.fetcher import MarketDataFetcher

CHUNK = 200_000


def load_daily_maps(start: str, end: str):
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
    k_map: dict[tuple[str, str], float] = {}
    for code, group in daily.groupby("code", sort=False):
        group = group.sort_values("day")
        ratio = group["turnover"] / group["volume"].where(group["volume"] > 0)
        ratio = ratio.replace([float("inf"), float("-inf")], pd.NA).ffill().bfill()
        for day, value in zip(group["day"], ratio):
            if pd.notna(value):
                k_map[(str(code), day)] = float(value)
    return adj_map, k_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--freq", default="5min")
    parser.add_argument("--start", default=None, help="仅导入 >= 该日期（默认 CSV 全量）")
    parser.add_argument("--end", default=None, help="仅导入 < 该日期（默认 CSV 全量）")
    args = parser.parse_args()

    header = pd.read_csv(args.csv, nrows=0)
    ts_col = pd.read_csv(args.csv, usecols=["ts"])["ts"]
    start = args.start or str(ts_col.min())[:10]
    end = args.end or (
        pd.Timestamp(str(ts_col.max())[:10]) + pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    adj_map, k_map = load_daily_maps(start, end)
    print(f"daily base: adj={len(adj_map):,} k={len(k_map):,}", flush=True)

    fetcher = MarketDataFetcher("zzshare")
    total = written = 0
    adj_missing = turnover_missing = 0
    t0 = time.time()
    for chunk in pd.read_csv(args.csv, chunksize=CHUNK, dtype={"code": str}):
        day = chunk["ts"].str[:10]
        adj, turnover = [], []
        for code, d, vol in zip(chunk["code"], day, chunk["volume"]):
            factor = adj_map.get((code, d))
            adj.append(factor)
            k = k_map.get((code, d))
            turnover.append(float(vol) * k if k is not None else None)
            adj_missing += factor is None
            turnover_missing += k is None
        chunk["adj_factor"] = adj
        chunk["turnover"] = turnover
        written += fetcher.update_ohlc(chunk, args.freq)
        total += len(chunk)
        rate = total / max(time.time() - t0, 1)
        print(f"  rows={total:,} written={written:,} ({rate:,.0f} rows/s)", flush=True)
    print(f"done rows={total:,} written={written:,} "
          f"adj_missing={adj_missing} turnover_missing={turnover_missing}", flush=True)
    print(f"csv columns: {list(header.columns)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
