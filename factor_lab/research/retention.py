"""R4 disk guard and retention policy."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": "factor_lab.retention.v1",
    "min_free_gb": 50.0,
    "max_output_gb": 25.0,
    "max_panel_cache_gb": 3.0,
    "keep_experiments": 500,
    "keep_reports_days": 30,
    "archive_after_days": 14,
    "updated_at": None,
}


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _panel_bytes() -> int:
    panel = config.PROJECT_ROOT / "output" / "cache" / "research_panel"
    return sum(path.stat().st_size for path in panel.glob("*.pkl") if path.is_file())


def ensure_policy() -> dict[str, Any]:
    current = read_json(config.RETENTION_POLICY_PATH)
    if isinstance(current, dict) and current.get("min_free_gb") is not None:
        return current
    value = dict(DEFAULT_POLICY)
    value["updated_at"] = config.now_iso()
    write_json_atomic(config.RETENTION_POLICY_PATH, value)
    return value


def storage_report() -> dict[str, Any]:
    policy = ensure_policy()
    output = config.PROJECT_ROOT / "output"
    usage = shutil.disk_usage(config.PROJECT_ROOT)
    free_gb = usage.free / 1024**3
    output_gb = _dir_bytes(output) / 1024**3
    lab_gb = _dir_bytes(config.LAB_ROOT) / 1024**3
    panel_gb = _panel_bytes() / 1024**3
    breaches = []
    if free_gb < float(policy["min_free_gb"]):
        breaches.append("free_disk")
    if output_gb > float(policy["max_output_gb"]):
        breaches.append("output")
    if panel_gb > float(policy["max_panel_cache_gb"]):
        breaches.append("panel_cache")
    report = {
        "schema_version": "factor_lab.storage.v1",
        "checked_at": config.now_iso(),
        "free_gb": round(free_gb, 2),
        "output_gb": round(output_gb, 2),
        "lab_gb": round(lab_gb, 2),
        "panel_cache_gb": round(panel_gb, 2),
        "policy": policy,
        "breaches": breaches,
        "ok": not breaches,
    }
    write_json_atomic(config.STORAGE_REPORT_PATH, report)
    return report


def enforce_r4() -> dict[str, Any]:
    report = storage_report()
    if report["breaches"]:
        from factor_lab.core.control import is_running, set_state

        if is_running():
            set_state("paused", reason=f"R4 breach: {','.join(report['breaches'])}", actor="supervisor")
    return report
