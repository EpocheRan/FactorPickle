"""分钟线试点：把 5m/15m 数据抓进 QuestDB（小样本，先验证链路）。

为什么单独放脚本：``acquisition`` 目前没有分钟线 dataset，分钟线只用于
日内止损/分时结构研究，不应该阻塞日频主链路。

用法（需要外网访问数据源）::

    # 先 dry-run 看能否取到
    python scripts/fetch_minute_pilot.py --source baostock --freq 5min \
        --start 2026-05-01 --end 2026-07-31 --limit 5 --dry-run

    # 确认后写入 ohlcv_5m
    python scripts/fetch_minute_pilot.py --source baostock --freq 5min \
        --start 2026-05-01 --end 2026-07-31 --limit 20

数据源差异：
- ``baostock``：5/15/30/60min，历史长，需登录；
- ``akshare``：1/5/15/30/60min，接口限频，近端数据；
- ``sina``：1/5/15/30/60min，5min 约 21 个交易日，最快但短。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_interface.market.fetcher import MarketDataFetcher

logger = logging.getLogger("minute_pilot")

DEFAULT_CODES = (
    "600519",
    "000001",
    "300750",
    "600036",
    "000858",
    "601318",
    "002594",
    "600900",
    "601899",
    "000333",
    "600276",
    "000651",
    "600030",
    "601012",
    "002475",
    "300059",
    "600887",
    "000725",
    "601166",
    "600309",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分钟线试点抓取")
    parser.add_argument("--source", default="baostock", choices=["baostock", "akshare", "sina"])
    parser.add_argument("--freq", default="5min", choices=["1min", "5min", "15min", "30min", "60min"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--codes", default="", help="逗号分隔；留空用内置 20 只")
    parser.add_argument("--limit", type=int, default=20, help="最多抓多少只")
    parser.add_argument("--dry-run", action="store_true", help="只取数不写库")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    codes = tuple(item.strip() for item in args.codes.split(",") if item.strip()) or DEFAULT_CODES
    codes = codes[: max(1, int(args.limit))]
    fetcher = MarketDataFetcher(source=args.source)
    total = 0
    ok = 0
    failed: list[tuple[str, str]] = []
    for index, code in enumerate(codes, start=1):
        try:
            frame = fetcher.get_ohlc(code, args.start, args.end, freq=args.freq)
        except Exception as exc:  # noqa: BLE001
            failed.append((code, f"{type(exc).__name__}: {exc}"))
            logger.warning("[%s/%s] %s 取数失败: %s", index, len(codes), code, exc)
            continue
        if frame is None or frame.empty:
            failed.append((code, "empty"))
            logger.warning("[%s/%s] %s 无数据", index, len(codes), code)
            continue
        written = 0
        if not args.dry_run:
            written = fetcher.update_ohlc(frame, args.freq)
        total += written if not args.dry_run else 0
        ok += 1
        logger.info(
            "[%s/%s] %s rows=%s written=%s",
            index,
            len(codes),
            code,
            len(frame),
            written,
        )
    print(
        f"source={args.source} freq={args.freq} codes={len(codes)} ok={ok} "
        f"failed={len(failed)} written={total} dry_run={args.dry_run}"
    )
    for code, reason in failed:
        print(f"  FAIL {code}: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
