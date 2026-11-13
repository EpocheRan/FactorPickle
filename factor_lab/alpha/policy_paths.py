"""Small helpers for repo-owned Alpha policy files."""

from __future__ import annotations

from pathlib import Path

from factor_lab.core import config


def policy_file(name: str) -> Path:
    return Path(config.ALPHA_POLICY_DIR) / name
