"""修复指数序列污染：删除 type='idx' 且 close<100 的伪指数行，再用 Tushare index_daily 重拉。

背景：000001/000016/000688/000852/000905 的 idx 分区里混入了同期个股价格
（如 000852 在 2016-2021 段的 close≈19，实际中证1000 当时在 4000-7000）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from data_interface.common.config import get_config, resolve_questdb_host

CFG = get_config()
BASE = f"http://{resolve_questdb_host(CFG)}:{CFG['questdb'].get('rest_port', 9000)}"

INDEXES = {
    "000001.SH": ("000001", "SSE"),
    "000016.SH": ("000016", "SSE"),
    "000688.SH": ("000688", "SSE"),
    "000852.SH": ("000852", "SSE"),
    "000905.SH": ("000905", "SSE"),
}


def q(sql: str):
    r = requests.get(f"{BASE}/exec", params={"query": sql}, timeout=300)
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"{payload['error']} :: {sql[:160]}")
    return payload.get("dataset") or []


def delete_bad() -> None:
    try:
        q("DELETE FROM ohlcv_1d WHERE type='idx' AND close < 100")
        print("DELETE 已执行（QuestDB 异步生效，稍等片刻）")
    except Exception as exc:  # noqa: BLE001
        print("DELETE 不支持或失败：", str(exc)[:160])


def fetch_index(ts_code: str) -> pd.DataFrame:
    import tushare as ts

    pro = ts.pro_api(CFG["tushare"]["token"])
    frames = []
    for year in range(2014, 2027):
        df = pro.index_daily(ts_code=ts_code, start_date=f"{year}0101", end_date=f"{year}1231")
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["trade_date"])
    return out.sort_values("trade_date")


def insert_rows(code: str, market: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    values = []
    for r in df.itertuples(index=False):
        ts = pd.Timestamp(str(r.trade_date)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        values.append(
            f"('{ts}','{code}','{market}','idx',{float(r.open)},{float(r.high)},"
            f"{float(r.low)},{float(r.close)},{int(float(r.vol) * 100)},"
            f"{float(r.amount) * 1000},0)"
        )
    total = 0
    for i in range(0, len(values), 50):
        chunk = values[i:i + 50]
        sql = ("INSERT INTO ohlcv_1d (ts,code,market,type,open,high,low,close,volume,amount,"
               "adj_factor) VALUES " + ",".join(chunk))
        q(sql)
        total += len(chunk)
    return total


def main() -> int:
    # QuestDB 该表不支持 DELETE，改用 DEDUP UPSERT 覆盖同名键的污染行

    for ts_code, (code, market) in INDEXES.items():
        df = fetch_index(ts_code)
        written = insert_rows(code, market, df)
        print(f"{ts_code}: 拉取 {len(df)} 行，写入 {written} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
