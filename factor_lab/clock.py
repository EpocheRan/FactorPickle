"""Single-writer hybrid logical clock for ledger events.

The ledger's append order is a lock-acquisition order, not a causal order.  Every
event gets an HLC and an explicit parent id from this clock service; orphan
checks use those values, not line order.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from .config import CLOCK_PATH, now_iso
from .store import _lock_file, read_json, write_json_atomic

_DEFAULT_NODE = "lab"


def node_id() -> str:
    raw = os.environ.get("FACTOR_LAB_NODE") or socket.gethostname() or _DEFAULT_NODE
    keep = [ch if ch.isalnum() or ch in "-_" else "-" for ch in raw]
    return "".join(keep)[:32] or _DEFAULT_NODE


def parse_hlc(value: str) -> tuple[int, int, str]:
    physical, logical, node = str(value).split("-", 2)
    return int(physical), int(logical), node


def hlc_after(left: str | None, right: str | None) -> bool:
    """True when ``left`` is strictly after ``right`` in HLC order."""
    if not left or not right:
        return False
    try:
        return parse_hlc(left) > parse_hlc(right)
    except (TypeError, ValueError):
        return False


def read_clock(path: Path | None = None) -> dict[str, Any]:
    return read_json(path or CLOCK_PATH) or {
        "seq": 0,
        "physical_ms": 0,
        "logical": 0,
        "node": node_id(),
    }


def next_token(path: Path | None = None) -> dict[str, Any]:
    """Return a monotonic ``event_seq`` / ``event_hlc`` token."""
    path = path or CLOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_file(path):
        state = read_clock(path)
        physical = int(time.time() * 1000)
        previous_physical = int(state.get("physical_ms") or 0)
        logical = int(state.get("logical") or 0)
        if physical > previous_physical:
            logical = 0
        else:
            physical = previous_physical
            logical += 1
        seq = int(state.get("seq") or 0) + 1
        token = {
            "event_seq": seq,
            "event_hlc": f"{physical:013d}-{logical:06d}-{node_id()}",
            "physical_ms": physical,
            "logical": logical,
            "node": node_id(),
            "issued_at": now_iso(),
        }
        write_json_atomic(
            path,
            {
                **token,
                "seq": seq,
                "updated_at": now_iso(),
            },
        )
        return token
