#!/usr/bin/env python
"""生成因子研究报告（只输出 HTML）。

统一入口，禁止另写自定义报告脚本。用法::

    python -m research.report --factor reversal.zjfz
    python -m research.report --factor reversal.zjfz --universe pool:2022-01-04:200
    python -m research.report --factor momentum.ret \
        --start 2017-01-01 --end 2026-11-01

默认：all_a 全市场，2017-01-01 至 2026-11-01，default profile，5 日持有。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from research.report import quality_audit  # noqa: E402
from research.report.runner import generate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.report",
        description="因子研究报告生成器（只输出 HTML）",
    )
    parser.add_argument("--factor", default="momentum.return_20d")
    parser.add_argument("--batch", default=None, help="逗号分隔，批量生成")
    parser.add_argument(
        "--universe",
        default="all_a",
        help="all_a | index:000905 | pool:2022-01-04:200",
    )
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--horizon", type=int, default=5, help="持有期（交易日）")
    parser.add_argument("--groups", type=int, default=5, help="分组数")
    parser.add_argument("--neutralize", action="store_true", help="横截面市值中性化后再评估")
    parser.add_argument("--no-robustness", action="store_true",
                        help="跳过多池稳健性重算，报告更快")
    parser.add_argument("--no-cache", action="store_true",
                        help="不使用 Panel 本地缓存，强制重新取数")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="批量模式下单个因子失败后继续")
    parser.add_argument("--out", default="output/reports/factor")
    parser.add_argument("--category", default=None)
    parser.add_argument("--direction", type=int, default=None)
    parser.add_argument("--profile", default="default", help="报告章节 profile")
    parser.add_argument("--quality-audit", action="store_true",
                        help="生成数据质量/归因审计 CSV（同目录 quality_audit.csv）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    factors = [f.strip() for f in (args.batch or args.factor).split(",") if f.strip()]

    shared_panel = None
    if len(factors) > 1:
        from research.report.panel_adapter import load_panel as _load_panel

        _t0 = time.time()
        _needs = any(f.startswith("sentiment.") for f in factors)
        shared_panel = _load_panel(
            None,
            None,
            args.start,
            args.end,
            universe=args.universe,
            extras=("fin_valuation_daily", "margin_detail", "moneyflow_daily") if _needs
            else ("fin_valuation_daily",),
            use_cache=not args.no_cache,
        )
        print(
            f"共享面板就绪：{len(shared_panel.symbols)} 只 / {len(shared_panel.close)} 天"
            f"（{time.time() - _t0:.1f}s），{len(factors)} 个因子复用"
        )

    audit_rows: list[dict] = []
    for factor in factors:
        try:
            result = generate(
                factor,
                start=args.start,
                end=args.end,
                universe=args.universe,
                category=args.category,
                direction=args.direction,
                out_dir=args.out,
                neutralize=args.neutralize,
                horizon=args.horizon,
                groups=args.groups,
                panel=shared_panel,
                profile=args.profile,
                robustness=not args.no_robustness,
                use_cache=not args.no_cache,
            )
        except Exception as exc:  # noqa: BLE001
            if not args.continue_on_error:
                raise
            print(f"[{factor}] FAILED: {type(exc).__name__}: {exc}")
            continue
        rating = result["rating"]
        print(
            f"[{factor}] 评级 {rating['grade']} / {rating['score']} 分"
            f"（时间 {rating['time_score']:.0f} × 空间 {rating['space_score']:.0f}）"
            f" → {result['html']}"
        )
        for veto in rating["vetoes"]:
            print(f"    否决: {veto}")
        if args.quality_audit:
            audit_rows.append(quality_audit.row_from_report(result["report"], rating))
    if args.quality_audit and audit_rows:
        path = quality_audit.write_csv(Path(args.out) / "quality_audit.csv", audit_rows)
        print(f"数据质量审计: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
