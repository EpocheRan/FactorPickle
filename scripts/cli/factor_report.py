"""因子报告入口（薄封装：只解析参数并调用 research.report.cli.main，无业务逻辑）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.report.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
