"""Paths and environment facts for the Alpha research engine."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
LAB_ROOT = Path(os.environ.get("FACTOR_LAB_ROOT", PROJECT_ROOT / "output" / "factor_lab"))
ALPHA_DIR = LAB_ROOT / "alpha"
FACTOR_LIBRARY_DIR = LAB_ROOT / "factors" / "generated"
DISCOVERY_DIR = LAB_ROOT / "discovery"
HYPOTHESES_DIR = LAB_ROOT / "hypotheses"

SCHEMA_VERSION = "factor_lab.alpha.v1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")
