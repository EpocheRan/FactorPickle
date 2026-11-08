"""Artifact I/O and JSON-safe serialization."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


def jsonable(obj: Any) -> Any:
    """Convert dataclasses / pandas / numpy values into JSON-safe values.

    Non-finite floats become ``None`` so JSON consumers never see ``NaN``.
    """

    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, np.ndarray):
        return [jsonable(item) for item in obj.tolist()]
    if isinstance(obj, pd.Series):
        return {
            "index": [jsonable(item) for item in obj.index.tolist()],
            "values": [jsonable(item) for item in obj.tolist()],
        }
    if isinstance(obj, pd.DataFrame):
        return {
            "index": [jsonable(item) for item in obj.index.tolist()],
            "columns": [jsonable(item) for item in obj.columns.tolist()],
            "data": [[jsonable(item) for item in row] for row in obj.to_numpy().tolist()],
        }
    if dataclasses.is_dataclass(obj):
        return {field.name: jsonable(getattr(obj, field.name)) for field in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(key): jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(item) for item in obj]
    return str(obj)


def canonical_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json_atomic(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


@contextmanager
def _lock_file(path: Path, timeout: float = 30.0):
    lock = path.with_name(path.name + ".lock")
    started = time.monotonic()
    fd: int | None = None
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > 60:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"timed out waiting for lock: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, record: dict[str, Any], *, lock: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(jsonable(record), ensure_ascii=False, separators=(",", ":"))
    if not lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return
    with _lock_file(path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_jsonl_lines(path: Path) -> list[tuple[int, dict[str, Any] | None, str]]:
    """Return (line number, parsed record or None, raw line)."""
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, Any] | None, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            rows.append((number, None, raw))
            continue
        rows.append((number, value if isinstance(value, dict) else None, raw))
    return rows


def list_json(paths: Iterable[Path]) -> list[Any]:
    return [read_json(path) for path in paths if path.exists()]
