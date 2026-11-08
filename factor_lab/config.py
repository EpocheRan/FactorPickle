"""Paths and environment facts for Factor Lab."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
LAB_ROOT = Path(os.environ.get("FACTOR_LAB_ROOT", PROJECT_ROOT / "output" / "factor_lab"))
EXPERIMENTS_DIR = LAB_ROOT / "experiments"
HYPOTHESES_DIR = LAB_ROOT / "hypotheses"
POLICY_DIR = LAB_ROOT / "policy"
LEDGER_DIR = LAB_ROOT / "ledger"
CONTROL_DIR = LAB_ROOT / "control"
DASHBOARD_DIR = LAB_ROOT / "dashboard"
ARCHIVE_DIR = PROJECT_ROOT / "output" / "_archive"
TRASH_DIR = PROJECT_ROOT / "output" / "_trash"

GATE_POLICY_PATH = POLICY_DIR / "gate_policy.json"
LEDGER_PATH = LEDGER_DIR / "trial_ledger.jsonl"
LEDGER_SUMMARY_PATH = LEDGER_DIR / "ledger_summary.json"
CONTROL_PATH = CONTROL_DIR / "lab_control.json"
DASHBOARD_PATH = DASHBOARD_DIR / "lab_dashboard.html"
DASHBOARD_DATA_PATH = DASHBOARD_DIR / "dashboard_data.json"
DATA_COVERAGE_PATH = LAB_ROOT / "data_coverage.json"
PORTFOLIO_PATH = LAB_ROOT / "portfolio.json"

SCHEMA_VERSION = "factor_lab.v1"
REPORT_SCHEMA_VERSION = "factor_lab.report.v1"


def ensure_dirs() -> None:
    for path in (
        LAB_ROOT,
        EXPERIMENTS_DIR,
        HYPOTHESES_DIR,
        POLICY_DIR,
        LEDGER_DIR,
        CONTROL_DIR,
        DASHBOARD_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def today_tag() -> str:
    return now_utc().astimezone().strftime("%Y%m%d")


def git_commit() -> str:
    """Return the current short commit without spawning a subprocess."""
    git = PROJECT_ROOT / ".git"
    try:
        if git.is_file():
            text = git.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                git = Path(text.split(":", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            ref_path = git / ref
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()[:12]
            packed = git / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:12]
            return "unknown"
        return head[:12]
    except Exception:
        return "unknown"
