"""Multi-round campaign control for the Alpha research loop.

Campaigns are bounded research containers, not infinite agent loops.  The
controller writes every round and enforces hard stop rules before another
round can start.  It is deterministic; an LLM may decide the research
question, but it cannot bypass these limits.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import append_jsonl, read_json, read_jsonl, write_json_atomic


CAMPAIGN_SCHEMA = "factor_lab.alpha.campaign.v1"
ROUND_SCHEMA = "factor_lab.alpha.campaign_round.v1"


class CampaignError(ValueError):
    pass


def default_campaign_root() -> Path:
    return config.LAB_ROOT / "alpha" / "campaigns"


def _campaign_dir(campaign_id: str, root: Path) -> Path:
    safe = campaign_id.strip()
    if not safe or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in safe):
        raise CampaignError(f"invalid campaign_id: {campaign_id!r}")
    return root / safe


class CampaignController:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_campaign_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        objective: str,
        *,
        max_rounds: int = 5,
        budget: float | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        if not str(objective).strip():
            raise CampaignError("objective must not be empty")
        if max_rounds < 1 or max_rounds > 20:
            raise CampaignError("max_rounds must be between 1 and 20")
        campaign_id = campaign_id or f"CAMP-{uuid.uuid4().hex[:10]}"
        directory = _campaign_dir(campaign_id, self.root)
        if directory.exists():
            raise FileExistsError(f"campaign already exists: {campaign_id}")
        directory.mkdir(parents=True)
        (directory / "rounds").mkdir()
        record = {
            "schema": CAMPAIGN_SCHEMA,
            "campaign_id": campaign_id,
            "objective": str(objective),
            "max_rounds": int(max_rounds),
            "budget": budget,
            "used_budget": 0.0,
            "state": "running",
            "n_rounds": 0,
            "created_at": config.now_iso(),
            "updated_at": config.now_iso(),
        }
        write_json_atomic(directory / "campaign.json", record)
        return record

    def _path(self, campaign_id: str) -> Path:
        return _campaign_dir(campaign_id, self.root)

    def load(self, campaign_id: str) -> dict[str, Any]:
        path = self._path(campaign_id) / "campaign.json"
        record = read_json(path)
        if not isinstance(record, dict) or record.get("schema") != CAMPAIGN_SCHEMA:
            raise FileNotFoundError(f"campaign not found: {campaign_id}")
        return record

    def rounds(self, campaign_id: str) -> list[dict[str, Any]]:
        directory = self._path(campaign_id) / "rounds"
        if not directory.exists():
            return []
        rows = []
        for path in sorted(directory.glob("round-*.json")):
            value = read_json(path)
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def record_round(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        campaign = self.load(campaign_id)
        if campaign.get("state") != "running":
            raise CampaignError(f"campaign is {campaign.get('state')}, cannot add a round")
        existing = self.rounds(campaign_id)
        if len(existing) >= int(campaign["max_rounds"]):
            self.stop(campaign_id, "max_rounds")
            raise CampaignError("campaign already reached max_rounds")
        number = len(existing) + 1
        if payload.get("duplicate"):
            self.stop(campaign_id, "duplicate_research")
            raise CampaignError("duplicate research is a hard stop")
        cost = float(payload.get("cost") or 0.0)
        used = float(campaign.get("used_budget") or 0.0) + cost
        budget = campaign.get("budget")
        round_record = {
            "schema": ROUND_SCHEMA,
            "campaign_id": campaign_id,
            "round_id": f"R{number}",
            "round_number": number,
            "research_question": payload.get("research_question"),
            "factor_refs": payload.get("factor_refs") or [],
            "data_manifest": payload.get("data_manifest"),
            "ml_plan_id": payload.get("ml_plan_id"),
            "research_result": payload.get("research_result"),
            "agents": list(payload.get("agents") or []),
            "team_tasks": list(payload.get("team_tasks") or payload.get("team_task_ids") or []),
            "protocol": payload.get("protocol"),
            "decision": payload.get("decision") or "continue",
            "cost": cost,
            "used_budget": used,
            "created_at": config.now_iso(),
        }
        write_json_atomic(self._path(campaign_id) / "rounds" / f"round-{number:03d}.json", round_record)
        append_jsonl(
            self._path(campaign_id) / "campaign_trace.jsonl",
            {"event": "round_recorded", "round_id": round_record["round_id"], "decision": round_record["decision"], "created_at": round_record["created_at"]},
        )
        campaign.update(
            {
                "n_rounds": number,
                "used_budget": used,
                "updated_at": config.now_iso(),
            }
        )
        if budget is not None and used >= float(budget):
            campaign["state"] = "stopped"
            campaign["stop_reason"] = "budget_exhausted"
        elif number >= int(campaign["max_rounds"]):
            campaign["state"] = "stopped"
            campaign["stop_reason"] = "max_rounds"
        elif round_record["decision"] == "stop":
            campaign["state"] = "stopped"
            campaign["stop_reason"] = "researcher_stop"
        elif round_record["decision"] == "pause":
            campaign["state"] = "paused"
            campaign["stop_reason"] = "researcher_pause"
        write_json_atomic(self._path(campaign_id) / "campaign.json", campaign)
        if campaign["state"] != "running":
            self._write_stop(campaign_id, str(campaign.get("stop_reason") or "stopped"))
        return round_record

    def stop(self, campaign_id: str, reason: str) -> dict[str, Any]:
        campaign = self.load(campaign_id)
        if campaign.get("state") in {"stopped", "completed"}:
            return campaign
        campaign["state"] = "stopped"
        campaign["stop_reason"] = str(reason)
        campaign["updated_at"] = config.now_iso()
        write_json_atomic(self._path(campaign_id) / "campaign.json", campaign)
        self._write_stop(campaign_id, reason)
        return campaign

    def _write_stop(self, campaign_id: str, reason: str) -> Path:
        path = self._path(campaign_id) / "stop_decision.json"
        write_json_atomic(
            path,
            {
                "campaign_id": campaign_id,
                "reason": str(reason),
                "created_at": config.now_iso(),
            },
        )
        append_jsonl(self._path(campaign_id) / "campaign_trace.jsonl", {"event": "stopped", "reason": reason, "created_at": config.now_iso()})
        return path

    def summary(self, campaign_id: str) -> dict[str, Any]:
        campaign = self.load(campaign_id)
        rounds = self.rounds(campaign_id)
        return {
            "campaign": campaign,
            "n_rounds": len(rounds),
            "rounds": rounds,
            "trace": read_jsonl(self._path(campaign_id) / "campaign_trace.jsonl"),
            "stop_decision": read_json(self._path(campaign_id) / "stop_decision.json"),
        }
