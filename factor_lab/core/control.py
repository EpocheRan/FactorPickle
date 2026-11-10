"""Human-controlled pause / resume switch for the lab.

The control file is deliberately outside the model's authority: a run checks it
before starting, and only a human should move it back to ``running``.
"""

from __future__ import annotations

from typing import Any

from factor_lab.core.config import CONTROL_PATH, now_iso
from factor_lab.core.store import read_json, write_json_atomic

STATES = {"running", "paused", "stopped"}


def default_control() -> dict[str, Any]:
    return {
        "state": "running",
        "reason": "",
        "actor": "bootstrap",
        "max_running": 4,
        "drain_sec": 120,
        "updated_at": now_iso(),
    }


def ensure_control() -> dict[str, Any]:
    current = read_json(CONTROL_PATH)
    if isinstance(current, dict) and current.get("state") in STATES:
        return current
    value = default_control()
    write_json_atomic(CONTROL_PATH, value)
    return value


def load_control() -> dict[str, Any]:
    current = read_json(CONTROL_PATH)
    if not isinstance(current, dict) or current.get("state") not in STATES:
        return ensure_control()
    return current


def set_state(state: str, *, reason: str = "", actor: str = "human") -> dict[str, Any]:
    if state not in STATES:
        raise ValueError(f"unknown control state: {state}")
    value = {
        "state": state,
        "reason": reason,
        "actor": actor,
        "max_running": int(load_control().get("max_running") or 4),
        "drain_sec": int(load_control().get("drain_sec") or 120),
        "updated_at": now_iso(),
    }
    write_json_atomic(CONTROL_PATH, value)
    return value


def is_running() -> bool:
    return str(load_control().get("state")) == "running"


def is_stopped() -> bool:
    return str(load_control().get("state")) == "stopped"


def assert_running() -> None:
    state = str(load_control().get("state"))
    if state != "running":
        raise RuntimeError(f"factor lab is {state}; set control state to running before starting a new run")
