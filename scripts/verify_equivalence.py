"""CLI 壳：等价性回放（实现见 research.eval.verify_equivalence）。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.eval.verify_equivalence import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
