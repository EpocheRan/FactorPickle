"""Architecture boundary checks for the A-full metrics-core migration.

Rules enforced here:

- ``factor_lab`` must not import ``research`` (the Alpha engine is independent);
- ``research`` must not import ``factor_lab`` (one-way dependency);
- ``factor_metrics`` is IO-free: only the standard library, numpy/pandas and
  the optional polars backend;
- ``data_interface.panel`` stays a pure data container.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                roots.add("<relative>")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _scan(directory: Path) -> list[tuple[str, set[str]]]:
    rows = []
    for path in sorted(directory.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rows.append((str(path.relative_to(ROOT)), _import_roots(path)))
    return rows


def test_factor_lab_does_not_import_research() -> None:
    violations = [(name, roots) for name, roots in _scan(ROOT / "factor_lab") if "research" in roots]
    assert not violations, f"factor_lab still imports research: {violations}"


def test_research_does_not_import_factor_lab() -> None:
    violations = [(name, roots) for name, roots in _scan(ROOT / "research") if "factor_lab" in roots]
    assert not violations, f"research imports factor_lab: {violations}"


def test_factor_metrics_is_io_free() -> None:
    allowed = set(sys.stdlib_module_names) | {"numpy", "pandas", "polars", "factor_metrics"}
    violations = []
    for name, roots in _scan(ROOT / "factor_metrics"):
        bad = {root for root in roots if root != "<relative>" and root not in allowed}
        if bad:
            violations.append((name, sorted(bad)))
    assert not violations, f"factor_metrics has disallowed imports: {violations}"


def test_panel_data_container_is_neutral() -> None:
    allowed = set(sys.stdlib_module_names) | {"numpy", "pandas"}
    roots = _import_roots(ROOT / "data_interface" / "panel.py") - {"<relative>"}
    assert roots <= allowed, f"data_interface/panel.py imports {sorted(roots - allowed)}"


def test_ml_branch_depends_on_core_rank_ic_one_way() -> None:
    source = (ROOT / "factor_lab" / "alpha" / "ml_branch.py").read_text(encoding="utf-8")
    assert "from factor_metrics import" in source
    assert "rank_ic_frame" in source
    assert "evaluate_long_only" in source
    assert "from research" not in source


def test_alpha_panel_builder_uses_neutral_panel() -> None:
    source = (ROOT / "factor_lab" / "runtime" / "panel_builder.py").read_text(encoding="utf-8")
    assert "from data_interface.panel import Panel" in source
    assert "from research" not in source
