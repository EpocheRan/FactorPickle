"""factor_engine CLI：list / build / ensure。"""

from __future__ import annotations

import argparse
import json
import sys

from .service import FactorEngineService


def _codes(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _top_by_amount(as_of: str, limit: int) -> list[str]:
    """按参考日成交额取前 N 只 A 股（60/00/30/68 开头）。"""
    import requests

    from data_interface.common.config import get_config, resolve_questdb_host

    cfg = get_config()
    base = f"http://{resolve_questdb_host(cfg)}:{cfg['questdb'].get('rest_port', 9000)}"
    query = (
        "select code from ohlcv_1d "
        f"where ts = (select max(ts) from ohlcv_1d where ts <= '{as_of}') "
        "and type='stock' order by amount desc limit " + str(int(limit) * 2)
    )
    payload = requests.get(f"{base}/exec", params={"query": query}, timeout=120).json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    codes = [str(row[0]) for row in payload.get("dataset") or []]
    return [c for c in codes if c[:2] in ("60", "00", "30", "68")][: int(limit)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="factor_engine")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出已注册内置因子")

    build = sub.add_parser("build", help="计算并写库")
    build.add_argument("--start", required=True)
    build.add_argument("--end", required=True)
    build.add_argument("--factor", default=None, help="逗号分隔 factor_id，默认全部内置")
    build.add_argument("--codes", default=None, help="逗号分隔代码，默认全市场")
    build.add_argument("--resume", action="store_true",
                       help="从各因子最新日期之后续算")
    build.add_argument("--skip-existing", action="store_true",
                       help="跳过已跑到结束日的因子")
    build.add_argument("--top-amount", type=int, default=0,
                       help="按参考日成交额取前 N 只（覆盖 --codes）")

    ensure = sub.add_parser("ensure", help="确保某日因子已就绪（缺则算）")
    ensure.add_argument("--as-of", required=True)
    ensure.add_argument("--codes", required=True)
    ensure.add_argument("--factor", default=None)

    args = parser.parse_args(argv)
    service = FactorEngineService()
    if args.command == "list":
        for spec in service.list_factors():
            print(
                f"{spec.factor_id}\t{spec.category.value}/{spec.subcategory}\t"
                f"window={spec.window}\tfreq={spec.freq}\tversion={spec.version}"
            )
        return 0
    if args.command == "build":
        codes = _codes(args.codes)
        if args.top_amount:
            codes = _top_by_amount(args.start, args.top_amount)
            print(f"[build] 按成交额取前 {len(codes)} 只（参考日 {args.start}）", file=sys.stderr)
        result = service.build(
            [item.strip() for item in (args.factor or "").split(",") if item.strip()] or None,
            args.start,
            args.end,
            codes,
            resume=args.resume,
            skip_existing=args.skip_existing,
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.command == "ensure":
        result = service.ensure(
            _codes(args.codes) or [],
            args.as_of,
            [item.strip() for item in (args.factor or "").split(",") if item.strip()] or None,
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
