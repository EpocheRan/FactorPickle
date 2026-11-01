#!/usr/bin/env python
"""已迁移：历史回补统一走 ``acquisition``，本脚本仅保留兼容入口。

用法（新）::

    python -m acquisition backfill --list
    python -m acquisition backfill --phase valuation --start 2020-01-01 --end 2022-12-31
    python -m acquisition backfill --phase daily --start 2017-01-01 --end 2017-12-31 --dry-run

旧的 ``--phase p3-valuation`` 等编号名已废弃（不再提供 alias），请改用语义名：
daily / delisted / index_weight / structure / periodic / financial / valuation。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acquisition.backfill.__main__ import main  # noqa: E402

if __name__ == "__main__":
    print("[deprecated] scripts/backfill_history.py 已迁移到 python -m acquisition backfill",
          file=sys.stderr)
    raise SystemExit(main())
