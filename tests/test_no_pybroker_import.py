"""PyBroker 已移除：源码目录不得再出现其 import。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("backtest", "strategies", "factors", "screener", "research")
IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*\bpybroker\b", re.MULTILINE
)


def test_no_pybroker_imports() -> None:
    violations: list[str] = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if IMPORT_PATTERN.search(text):
                violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, "发现 PyBroker import:\n" + "\n".join(violations)


def test_pyproject_has_no_pybroker() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "lib-pybroker" not in text
