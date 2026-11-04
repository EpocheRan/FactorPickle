"""快速评分 CLI：一次加载面板，批量给多个因子打分（不生成 HTML）。

用法::

    python -m scripts.cli factor-score --factors liquidity.a,momentum.rsrs
    python -m scripts.cli factor-score --factors ... --horizon 20 --groups 10
    python -m scripts.cli factor-score --factors ... --out output/score.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factor-score", description="快速评分（不生成报告）")
    parser.add_argument("--factors", required=True, help="逗号分隔 factor_id")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--universe", default="all_a")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--groups", type=int, default=10)
    parser.add_argument("--neutralize", action="store_true")
    parser.add_argument("--out", default="output/factor_scores.csv")
    ns = parser.parse_args(argv)

    from research.report.quick import score_factors, write_csv

    factors = [f.strip() for f in ns.factors.split(",") if f.strip()]
    result = score_factors(
        factors, start=ns.start, end=ns.end, universe=ns.universe,
        horizon=ns.horizon, groups=ns.groups, neutralize=ns.neutralize,
    )
    path = write_csv(result, ns.out)
    print(result.to_string(index=False))
    print(f"\n面板加载 {result.attrs.get('load_seconds', 0):.1f}s，"
          f"评分合计 {result['sec'].sum() if 'sec' in result else 0:.1f}s → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
