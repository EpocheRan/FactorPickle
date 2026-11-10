"""Non-destructive output inventory and cleanup planning."""

from __future__ import annotations

import csv
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factor_lab.core import config


@dataclass
class Entry:
    path: Path
    kind: str
    bytes: int
    files: int
    mtime: float


def _walk(path: Path) -> tuple[int, int, float]:
    files = 0
    total = 0
    newest = path.stat().st_mtime if path.exists() else 0.0
    if path.is_file():
        return 1, path.stat().st_size, newest
    if not path.exists():
        return 0, 0, 0.0
    for root, _, names in os_walk(path):
        for name in names:
            item = Path(root) / name
            try:
                stat = item.stat()
            except OSError:
                continue
            files += 1
            total += stat.st_size
            newest = max(newest, stat.st_mtime)
    return files, total, newest


def os_walk(path: Path):
    import os

    return os.walk(path)


def _top_level() -> list[Entry]:
    output = config.PROJECT_ROOT / "output"
    rows: list[Entry] = []
    if not output.exists():
        return rows
    for item in output.iterdir():
        if item.name in {"factor_lab", "archive", "trash"}:
            continue
        if item.is_dir():
            files, total, newest = _walk(item)
            kind = "cache" if item.name == "cache" else "reports" if item.name == "reports" else "dir"
        else:
            stat = item.stat()
            files, total, newest = 1, stat.st_size, stat.st_mtime
            kind = "temp" if item.name.startswith("_tmp") or item.suffix == ".log" else "file"
        rows.append(Entry(item, kind, total, files, newest))
    return sorted(rows, key=lambda row: row.bytes, reverse=True)


def inventory() -> Path:
    output = config.PROJECT_ROOT / "output"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"_inventory_{config.today_tag()}.csv"
    rows = _top_level()
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "kind", "files", "bytes", "mtime"])
        for row in rows:
            writer.writerow([str(row.path), row.kind, row.files, row.bytes, row.mtime])
    print(f"inventory: {target}")
    for row in rows[:12]:
        print(f"  {row.path.name:36s} {row.kind:8s} {row.files:6d} files {row.bytes / 1024 / 1024:9.1f} MB")
    return target


def _plan() -> list[dict[str, Any]]:
    output = config.PROJECT_ROOT / "output"
    actions: list[dict[str, Any]] = []

    for item in output.iterdir():
        name = item.name
        if name.startswith("factor_lab_smoke"):
            actions.append({"path": item, "action": "trash", "reason": "smoke artifact"})
        elif name == "factor_reports" and item.is_dir() and not any(item.iterdir()):
            actions.append({"path": item, "action": "trash", "reason": "empty directory"})
        elif name.startswith("_tmp"):
            actions.append({"path": item, "action": "trash", "reason": "temporary file"})
        elif item.is_file() and item.suffix in {".log", ".png"} and name.startswith("zz"):
            # Keep source data / logs?  Only old zz logs are noise.
            if time.time() - item.stat().st_mtime > 7 * 86400:
                actions.append({"path": item, "action": "trash", "reason": "old log/image"})

    # Reports: keep the latest two date folders per top-level report family.
    reports = output / "reports"
    if reports.exists():
        for family in reports.iterdir():
            if not family.is_dir():
                continue
            dated = [p for p in family.iterdir() if p.is_dir()]
            dated.sort(key=lambda p: p.name, reverse=True)
            for old in dated[2:]:
                actions.append({"path": old, "action": "archive", "reason": "older report snapshot"})

    # Panel cache: keep newest files up to 2GB.
    panel = output / "cache" / "research_panel"
    if panel.exists():
        files = sorted([p for p in panel.glob("*.pkl") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for item in files:
            size = item.stat().st_size
            if total + size <= 2 * 1024**3:
                total += size
                continue
            actions.append({"path": item, "action": "archive", "reason": "panel cache beyond 2GB budget"})

    # Top-level backups are intentionally report-only, never moved automatically.
    for item in output.iterdir():
        if item.is_file() and item.name.startswith("backup_"):
            actions.append({"path": item, "action": "review", "reason": "backup file; decide archive/delete manually"})
    return actions


def clean(*, apply: bool = False) -> list[dict[str, Any]]:
    actions = _plan()
    stamp = config.today_tag()
    archive_root = config.ARCHIVE_DIR / stamp
    trash_root = config.TRASH_DIR / stamp
    for action in actions:
        path: Path = action["path"]
        target_root = archive_root if action["action"] == "archive" else trash_root
        relative = path.relative_to(config.PROJECT_ROOT)
        target = target_root / relative
        if action["action"] == "review":
            print(f"REVIEW  {relative}  ({action['reason']})")
            continue
        print(f"{'MOVE' if apply else 'DRY'}  {relative} -> {target}")
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
    return actions
