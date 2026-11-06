"""输出目录约定：按生成日期分组。"""

from __future__ import annotations

from datetime import date
from pathlib import Path


def dated_dir(base: str | Path, when: date | None = None) -> Path:
    """``base/YYYY-MM-DD``；调用方可传入固定日期做归档。"""
    day = when or date.today()
    return Path(base) / day.isoformat()
