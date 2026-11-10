"""Install portable factor artifacts into the local Lab runtime library."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import config

SOURCE_DIR = Path(__file__).with_name("factor_artifacts") / "generated"


def install(source: Path | None = None, target: Path | None = None) -> dict[str, Any]:
    src = Path(source or SOURCE_DIR)
    dst = Path(target or config.FACTOR_LIBRARY_DIR)
    if not src.exists():
        return {"ok": False, "error": f"artifact source missing: {src}", "installed": []}
    installed: list[str] = []
    for version_dir in sorted(src.glob("*/*/v*")):
        if not version_dir.is_dir():
            continue
        relative = version_dir.relative_to(src)
        target_dir = dst / relative
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(version_dir, target_dir, dirs_exist_ok=True)
        installed.append(str(relative))
    return {"ok": True, "source": str(src), "target": str(dst), "installed": installed}
