"""backfill CLI：``python -m acquisition.backfill --list / --phase ...``"""

from __future__ import annotations

import argparse
import logging
import sys

from . import PHASES, Scope, run_phase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m acquisition.backfill", description="历史数据回补")
    parser.add_argument("--list", action="store_true", help="列出可用阶段")
    parser.add_argument("--phase", default=None, help="阶段名（兼容旧编号名）")
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--chunk", type=int, default=500, help="每批写入行数")
    parser.add_argument("--dry-run", action="store_true", help="只出缺口报告，不写库")
    parser.add_argument("--codes", default="", help="逗号分隔代码（部分阶段使用）")
    parser.add_argument("--min-codes", type=int, default=2500, help="每日已有行数达标阈值")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        from . import phases  # noqa: F401 - 触发注册

        print("可用阶段：")
        for name in sorted(PHASES):
            tables = getattr(PHASES[name], "tables", ())
            print(f"  {name:<14} → {', '.join(tables) or '-'}")
        return 0

    if not args.phase:
        parser.print_help()
        return 2

    end = args.end or str(__import__("pandas").Timestamp.today().date())
    scope = Scope(start=args.start, end=end, dry_run=args.dry_run, chunk=args.chunk,
                  min_codes=args.min_codes,
                  codes=tuple(c.strip() for c in (args.codes or "").split(",") if c.strip()))
    result = run_phase(args.phase, scope)

    if args.dry_run:
        for gap in result.gaps or []:
            print(f"[dry-run] {gap['table']}: {gap['first']} ~ {gap['last']}")
            for year, count in gap["years"].items():
                print(f"    {year}: {count:,}")
        return 0

    print(f"{result.phase} 完成：写入 {result.rows:,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
