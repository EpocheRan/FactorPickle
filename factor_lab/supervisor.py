"""Supervisor service primitives: heartbeat, cycle state, and lease."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from . import config
from .clock import read_clock
from .store import _lock_file, read_json, write_json_atomic


def _now() -> datetime:
    return datetime.now(timezone.utc)


def write_heartbeat(*, interval_sec: int = 120) -> dict[str, Any]:
    payload = {
        "schema_version": "factor_lab.heartbeat.v1",
        "host_alive_at": config.now_iso(),
        "service_pid": os.getpid(),
        "interval_sec": int(interval_sec),
        "event_clock_seq": int(read_clock().get("seq") or 0),
        "free_disk_gb": round(__import__("shutil").disk_usage(config.PROJECT_ROOT).free / 1024**3, 2),
    }
    write_json_atomic(config.HEARTBEAT_PATH, payload)
    return payload


class Heartbeat:
    def __init__(self, interval_sec: int = 120) -> None:
        self.interval_sec = max(1, int(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        write_heartbeat(interval_sec=self.interval_sec)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            write_heartbeat(interval_sec=self.interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        write_heartbeat(interval_sec=self.interval_sec)


def write_cycle(*, cycle_id: str, status: str, **extra: Any) -> dict[str, Any]:
    previous = read_json(config.CYCLE_PATH) or {}
    payload = {
        "schema_version": "factor_lab.cycle.v1",
        "cycle_id": cycle_id,
        "status": status,
        **previous,
        **extra,
        "cycle_id": cycle_id,
        "status": status,
        "updated_at": config.now_iso(),
    }
    write_json_atomic(config.CYCLE_PATH, payload)
    return payload


def read_supervisor() -> dict[str, Any]:
    return read_json(config.SUPERVISOR_PATH) or {}


@contextmanager
def lease(owner: str, *, ttl_sec: int = 3600) -> Iterator[bool]:
    """Single-cycle lease.  Returns False when another live lease exists."""
    config.CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    acquired = False
    with _lock_file(config.SUPERVISOR_PATH):
        current = read_supervisor()
        expires_raw = current.get("lease_expires_at")
        live = False
        if expires_raw:
            try:
                live = datetime.fromisoformat(str(expires_raw)) > _now()
            except ValueError:
                live = False
        if current.get("lease_owner") and current.get("lease_owner") != owner and live:
            acquired = False
        else:
            now = _now()
            write_json_atomic(
                config.SUPERVISOR_PATH,
                {
                    **current,
                    "schema_version": "factor_lab.supervisor.v1",
                    "lease_owner": owner,
                    "lease_expires_at": (now + timedelta(seconds=ttl_sec)).isoformat(timespec="seconds"),
                    "heartbeat_at": config.now_iso(),
                    "updated_at": config.now_iso(),
                },
            )
            acquired = True
    try:
        yield acquired
    finally:
        if acquired:
            with _lock_file(config.SUPERVISOR_PATH):
                current = read_supervisor()
                if current.get("lease_owner") == owner:
                    current.update(
                        {
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "updated_at": config.now_iso(),
                        }
                    )
                    write_json_atomic(config.SUPERVISOR_PATH, current)
