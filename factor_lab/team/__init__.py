"""Portable durable-teammate role definitions.

Session runtime (who is currently spawned) is not committed. This module is the
committed source of truth for role names, prompts and contracts, so a fresh
environment can recreate the same team from the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.core.store import read_json

ROLES_PATH = Path(__file__).with_name("roles.json")
REQUIRED = ("name", "description", "context", "prompt")


def load_roles(path: Path | None = None) -> list[dict[str, Any]]:
    raw = read_json(path or ROLES_PATH)
    if not isinstance(raw, dict):
        return []
    roles = raw.get("roles")
    return roles if isinstance(roles, list) else []


def validate_roles(path: Path | None = None) -> dict[str, Any]:
    roles = load_roles(path)
    names = [str(role.get("name") or "") for role in roles if isinstance(role, dict)]
    missing = [
        {"name": role.get("name"), "missing": [key for key in REQUIRED if not role.get(key)]}
        for role in roles
        if isinstance(role, dict) and any(not role.get(key) for key in REQUIRED)
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    return {
        "ok": bool(roles) and not missing and not duplicates,
        "count": len(roles),
        "names": names,
        "missing": missing,
        "duplicates": duplicates,
    }
