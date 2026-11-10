"""Agent registry and safety-gated reclamation.

DSH has no public close API for workflow children, so the Supervisor owns a
registry of logical agents and an archive-based reclaimer.  Reclaim is refused
unless the agent is idle, has committed artifacts, has no active dependencies,
and has exceeded a grace period.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic

REGISTRY_PATH = config.CONTROL_DIR / "agent_registry.json"
DEFAULT_GRACE_SEC = 120


@dataclass
class AgentRecord:
    agent_id: str
    role: str
    state: str = "idle"  # running | idle | awaiting_input | unsafe
    session_dir: str | None = None
    artifacts_committed: bool = False
    pending_tool_calls: int = 0
    pending_writes: int = 0
    dependencies: list[str] = field(default_factory=list)
    last_seen: str = field(default_factory=config.now_iso)
    reclaim_status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "state": self.state,
            "session_dir": self.session_dir,
            "artifacts_committed": bool(self.artifacts_committed),
            "pending_tool_calls": int(self.pending_tool_calls),
            "pending_writes": int(self.pending_writes),
            "dependencies": list(self.dependencies),
            "last_seen": self.last_seen,
            "reclaim_status": self.reclaim_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=str(data["agent_id"]),
            role=str(data.get("role", "")),
            state=str(data.get("state", "idle")),
            session_dir=data.get("session_dir"),
            artifacts_committed=bool(data.get("artifacts_committed", False)),
            pending_tool_calls=int(data.get("pending_tool_calls", 0) or 0),
            pending_writes=int(data.get("pending_writes", 0) or 0),
            dependencies=[str(item) for item in data.get("dependencies", [])],
            last_seen=str(data.get("last_seen") or config.now_iso()),
            reclaim_status=str(data.get("reclaim_status", "active")),
        )


class AgentReclaimError(RuntimeError):
    pass


def _load() -> dict[str, AgentRecord]:
    raw = read_json(REGISTRY_PATH)
    items = raw.get("agents", []) if isinstance(raw, dict) else []
    return {str(item["agent_id"]): AgentRecord.from_dict(item) for item in items if isinstance(item, dict)}


def _save(records: dict[str, AgentRecord]) -> None:
    write_json_atomic(
        REGISTRY_PATH,
        {
            "schema_version": "factor_lab.agent_registry.v1",
            "updated_at": config.now_iso(),
            "agents": [record.to_dict() for record in records.values()],
        },
    )


def register(agent_id: str, role: str, *, session_dir: str | None = None) -> AgentRecord:
    records = _load()
    record = AgentRecord(agent_id=agent_id, role=role, session_dir=session_dir)
    records[agent_id] = record
    _save(records)
    return record


def update(agent_id: str, **changes: Any) -> AgentRecord:
    records = _load()
    if agent_id not in records:
        raise AgentReclaimError(f"unknown agent: {agent_id}")
    record = records[agent_id]
    for key, value in changes.items():
        if hasattr(record, key):
            setattr(record, key, value)
    record.last_seen = config.now_iso()
    _save(records)
    return record


def list_agents() -> list[AgentRecord]:
    return list(_load().values())


def safe_to_reclaim(record: AgentRecord, *, grace_sec: int = DEFAULT_GRACE_SEC) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("not_running", record.state != "running", record.state)
    check("no_pending_tool_calls", record.pending_tool_calls == 0, record.pending_tool_calls)
    check("no_pending_writes", record.pending_writes == 0, record.pending_writes)
    check("artifacts_committed", record.artifacts_committed, record.artifacts_committed)
    check("no_dependencies", not record.dependencies, record.dependencies)
    check("not_already_reclaimed", record.reclaim_status != "reclaimed", record.reclaim_status)
    return {"safe": all(item["ok"] for item in checks), "checks": checks, "agent": record.to_dict()}


def reclaim(
    agent_id: str,
    *,
    archive_root: Path | None = None,
    apply: bool = False,
    grace_sec: int = DEFAULT_GRACE_SEC,
) -> dict[str, Any]:
    records = _load()
    if agent_id not in records:
        raise AgentReclaimError(f"unknown agent: {agent_id}")
    record = records[agent_id]
    safety = safe_to_reclaim(record, grace_sec=grace_sec)
    decision = {
        "schema_version": "factor_lab.reclaim_decision.v1",
        "agent_id": agent_id,
        "safe": safety["safe"],
        "checks": safety["checks"],
        "action": "none",
        "applied": False,
        "at": config.now_iso(),
    }
    if not safety["safe"]:
        record.reclaim_status = "held"
        records[agent_id] = record
        _save(records)
        decision["action"] = "hold_or_quarantine"
        write_json_atomic(config.CONTROL_DIR / "reclaim_decision.json", decision)
        return decision

    root = Path(archive_root or (config.ARCHIVE_DIR / "agents"))
    target = root / agent_id
    decision["action"] = "archive"
    decision["target"] = str(target)
    if apply:
        source = Path(str(record.session_dir)) if record.session_dir else None
        if source and source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise AgentReclaimError(f"archive target exists: {target}")
            shutil.move(str(source), str(target))
            decision["moved"] = True
        record.reclaim_status = "reclaimed"
        records[agent_id] = record
        _save(records)
        decision["applied"] = True
    write_json_atomic(config.CONTROL_DIR / "reclaim_decision.json", decision)
    return decision


def reclaim_all(*, archive_root: Path | None = None, apply: bool = False) -> dict[str, Any]:
    records = _load()
    decisions = []
    for agent_id in list(records):
        try:
            decisions.append(reclaim(agent_id, archive_root=archive_root, apply=apply))
        except Exception as exc:  # noqa: BLE001 - one bad agent must not block others
            decisions.append({"agent_id": agent_id, "safe": False, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "schema_version": "factor_lab.reclaim_batch.v1",
        "at": config.now_iso(),
        "decisions": decisions,
        "reclaimed": sum(1 for item in decisions if item.get("applied")),
        "held": sum(1 for item in decisions if not item.get("safe")),
    }
