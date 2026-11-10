"""Append-only research memory.

This is not the ledger.  Ledger records trial facts; History records what the
research loop learned: failure modes, dead ends, open questions, and links.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic

HISTORY_PATH = config.LAB_ROOT / "research_history.jsonl"
HISTORY_INDEX_PATH = config.LAB_ROOT / "research_history_index.json"


@dataclass
class HistoryEntry:
    cycle_id: str
    summary: str
    tags: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    dead_ends: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factor_lab.history_entry.v1",
            "at": config.now_iso(),
            "cycle_id": self.cycle_id,
            "summary": self.summary,
            "tags": list(self.tags),
            "failure_modes": list(self.failure_modes),
            "dead_ends": list(self.dead_ends),
            "open_questions": list(self.open_questions),
            "links": list(self.links),
        }


def append(entry: HistoryEntry, *, path: Path | None = None) -> dict[str, Any]:
    target = Path(path or HISTORY_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = entry.to_dict()
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    rebuild_index(path=target)
    return payload


def read_all(*, path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path or HISTORY_PATH)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def rebuild_index(*, path: Path | None = None) -> dict[str, Any]:
    rows = read_all(path=path)
    tags: dict[str, int] = {}
    failure_modes: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags", []) or []:
            tags[str(tag)] = tags.get(str(tag), 0) + 1
        for mode in row.get("failure_modes", []) or []:
            failure_modes[str(mode)] = failure_modes.get(str(mode), 0) + 1
    index = {
        "schema_version": "factor_lab.history_index.v1",
        "updated_at": config.now_iso(),
        "entries": len(rows),
        "tags": tags,
        "failure_modes": failure_modes,
    }
    write_json_atomic(HISTORY_INDEX_PATH, index)
    return index


def recent(limit: int = 5, *, path: Path | None = None) -> list[dict[str, Any]]:
    rows = read_all(path=path)
    return rows[-max(0, int(limit)) :]


def search(tag: str = "", *, path: Path | None = None) -> list[dict[str, Any]]:
    needle = str(tag).strip().lower()
    if not needle:
        return []
    return [
        row
        for row in read_all(path=path)
        if needle in " ".join(
            [str(row.get("summary", ""))]
            + [str(item) for item in row.get("tags", []) or []]
            + [str(item) for item in row.get("failure_modes", []) or []]
        ).lower()
    ]


def index() -> dict[str, Any]:
    path = HISTORY_INDEX_PATH
    raw = read_json(path)
    return raw if isinstance(raw, dict) else rebuild_index()
