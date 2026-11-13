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
    parser.add_argument("--datasets", default="", help="p0 阶段只跑这些数据集（逗号分隔）")
    parser.add_argument("--loop", action="store_true", help="分批阶段自动续跑到完成")
    parser.add_argument("--max-rounds", type=int, default=50, help="--loop 最多轮数")
    parser.add_argument("--fresh", action="store_true",
                        help="忽略 valuation_derived 的逐月续跑状态，从头重算")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        from . import phases  # noqa: F401 - 触发注册

        print("可用阶段：")
        for name in sorted(PHASES):
            tables = getattr(PHASES[name], "tables", ())
            print(f"  {name:<22} → {', '.join(tables) or '-'}")
        return 0

    if not args.phase:
        parser.print_help()
        return 2

    end = args.end or str(__import__("pandas").Timestamp.today().date())
    datasets = tuple(
        item.strip() for item in (args.datasets or "").split(",") if item.strip()
    )
    scope = Scope(start=args.start, end=end, dry_run=args.dry_run, chunk=args.chunk,
                  min_codes=args.min_codes,
                  codes=tuple(c.strip() for c in (args.codes or "").split(",") if c.strip()),
                  datasets=datasets, fresh=args.fresh)
    result = run_phase(args.phase, scope)
    rounds = 1
    while args.loop and not args.dry_run and not result.done and rounds < args.max_rounds:
        more = run_phase(args.phase, scope)
        rounds += 1
        result.rows += more.rows
        result.done = more.done
        result.blocked = more.blocked
        if not more.rows:
            break

    if args.dry_run:
        for gap in result.gaps or []:
            if "years" not in gap:
                print(f"[dry-run] {gap['table']}: {gap['rows']:,} 行覆盖")
                for field, stat in gap["fields"].items():
                    print(f"    {field:<22} {stat / gap['rows'] * 100 if gap['rows'] else 0:6.2f}%")
                continue
            print(f"[dry-run] {gap['table']}: {gap['first']} ~ {gap['last']}")
            for year, count in gap["years"].items():
                print(f"    {year}: {count:,}")
        _print_details(result.details, dry_run=True)
        return 0

    print(f"{result.phase} 完成：写入 {result.rows:,} 行，"
          f"done={result.done}，rounds={rounds}")
    _print_details(result.details, dry_run=False)
    if result.blocked:
        print(f"权限不足（已跳过，代码保留）: {', '.join(result.blocked)}")
    return 0 if result.done else 1


def _print_details(details: dict | None, *, dry_run: bool) -> None:
    """打印逐月进度与覆盖（valuation_derived 的显式 coverage 输出）。"""
    if not details:
        return
    months = details.get("months_detail") or []
    if months:
        print(f"{'month':<9}{'read':>10}{'written':>10}{'sec':>8}  ev_sales  p_nwc   earnings_yield")
        for item in months:
            fields = item.get("coverage", {}).get("fields", {})
            def _pct(name: str) -> str:
                stat = fields.get(name) or {}
                return f"{stat.get('coverage', 0.0) * 100:6.2f}%"
            print(f"{item['month']:<9}{item['rows_read']:>10,}{item['rows_written']:>10,}"
                  f"{item['seconds']:>8.1f}  {_pct('ev_sales')}  {_pct('p_nwc')}  "
                  f"{_pct('earnings_yield')}")
    payload = details.get("predicted" if dry_run else "measured") or {}
    totals = details.get("totals") or {}
    if dry_run:
        print(f"[dry-run] predicted coverage ({details.get('start')} ~ {details.get('end')}, "
              f"{totals.get('rows_read', 0):,} rows, nothing written)")
    else:
        print(f"[results] measured coverage ({details.get('start')} ~ {details.get('end')}, "
              f"{payload.get('rows', 0):,} rows)")
    for field, stat in (payload.get("fields") or {}).items():
        print(f"    {field:<22} {stat['coverage'] * 100:6.2f}%  ({stat['nonnull']:,} rows)")
    if details.get("skipped_months"):
        print(f"    skipped {len(details['skipped_months'])} completed month(s)")


if __name__ == "__main__":
    raise SystemExit(main())
