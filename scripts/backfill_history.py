#!/usr/bin/env python
"""历史回补转发入口：等价于 ``python -m acquisition.backfill``。

用法::

    python -m acquisition.backfill --list
    python -m acquisition.backfill --phase valuation --start 2020-01-01 --end 2022-12-31
    python -m acquisition.backfill --phase daily --start 2017-01-01 --end 2017-12-31 --dry-run
    python -m acquisition.backfill --phase p0 --start 2017-01-01 --end 2026-11-07 --loop

阶段用语义名：daily / delisted / index_weight / structure / periodic / financial /
valuation / moneyflow / concept / disclosure / boards，以及 P0 基础资料的
p0 / stock_basic_history / namechange / new_share / suspend_d / stk_limit /
disclosure_date / forecast / express。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acquisition.backfill.__main__ import main  # noqa: E402

if __name__ == "__main__":
    print("[deprecated] 请直接使用 python -m acquisition.backfill", file=sys.stderr)
    raise SystemExit(main())
