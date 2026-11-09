"""Small, explicit schemas for Factor Lab artifacts."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import EXPERIMENTS_DIR, now_iso
from .store import _lock_file


@dataclass(frozen=True)
class OutcomeSpec:
    """One pre-registered outcome: the unit of a trial."""

    universe: str
    horizon: int = 5
    direction: int = 1
    groups: int = 5
    cost_bps: float = 20.0

    def key(self) -> str:
        return (
            f"{self.universe}|h{self.horizon}|d{self.direction}|"
            f"g{self.groups}|c{self.cost_bps:g}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "horizon": self.horizon,
            "direction": self.direction,
            "groups": self.groups,
            "cost_bps": self.cost_bps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutcomeSpec":
        return cls(
            universe=str(data.get("universe", "all_a")),
            horizon=int(data.get("horizon", 5)),
            direction=int(data.get("direction", 1)),
            groups=int(data.get("groups", 5)),
            cost_bps=float(data.get("cost_bps", 20.0)),
        )


@dataclass
class Hypothesis:
    hypothesis_id: str
    factor_id: str
    title: str
    mechanism: str
    expected_sign: int
    outcomes: list[OutcomeSpec]
    data: list[str] = field(default_factory=list)
    outcome_policy: str = "single"
    kill_criteria: list[str] = field(default_factory=list)
    source: str = "manual"
    staging: str | None = None
    bet: str | None = None
    parent: str | None = None
    family_id: str | None = None
    development_window: str | None = None
    start: str = "2019-01-01"
    end: str | None = None
    created_at: str = field(default_factory=now_iso)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("hypothesis must pre-register at least one outcome")
        if self.outcome_policy == "single" and len(self.outcomes) != 1:
            raise ValueError("outcome_policy=single requires exactly one outcome")
        if self.expected_sign not in (-1, 1):
            raise ValueError("expected_sign must be +1 or -1")
        if not self.family_id:
            family = self.bet or "general"
            self.family_id = f"F-{family}-{self.start[:10]}"
        if not self.development_window:
            self.development_window = f"{self.start}:{self.end or 'open'}"

    def trial_ids(self) -> dict[str, str]:
        return {outcome.key(): trial_id(self.hypothesis_id, outcome) for outcome in self.outcomes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "factor_id": self.factor_id,
            "title": self.title,
            "mechanism": self.mechanism,
            "expected_sign": self.expected_sign,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "outcome_policy": self.outcome_policy,
            "data": list(self.data),
            "kill_criteria": list(self.kill_criteria),
            "source": self.source,
            "staging": self.staging,
            "bet": self.bet,
            "parent": self.parent,
            "family_id": self.family_id,
            "development_window": self.development_window,
            "start": self.start,
            "end": self.end,
            "created_at": self.created_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        return cls(
            hypothesis_id=str(data["hypothesis_id"]),
            factor_id=str(data["factor_id"]),
            title=str(data.get("title", data["factor_id"])),
            mechanism=str(data.get("mechanism", "")),
            expected_sign=int(data.get("expected_sign", 1)),
            outcomes=[OutcomeSpec.from_dict(item) for item in data.get("outcomes", [])],
            data=[str(item) for item in data.get("data", [])],
            outcome_policy=str(data.get("outcome_policy", "single")),
            kill_criteria=[str(item) for item in data.get("kill_criteria", [])],
            source=str(data.get("source", "manual")),
            staging=data.get("staging"),
            bet=data.get("bet"),
            parent=data.get("parent"),
            family_id=data.get("family_id"),
            development_window=data.get("development_window"),
            start=str(data.get("start", "2019-01-01")),
            end=data.get("end"),
            created_at=str(data.get("created_at", now_iso())),
            notes=str(data.get("notes", "")),
        )


def trial_id(hypothesis_id: str, outcome: OutcomeSpec) -> str:
    raw = f"{hypothesis_id}|{outcome.key()}".encode("utf-8")
    return "T-" + hashlib.sha1(raw).hexdigest()[:14]


def new_hypothesis_id(factor_id: str) -> str:
    stamp = now_iso()[:10].replace("-", "")
    slug = "".join(ch if ch.isalnum() else "-" for ch in factor_id).strip("-")[:28]
    raw = f"{stamp}|{factor_id}|{now_iso()}|{uuid.uuid4().hex}".encode("utf-8")
    return f"H-{stamp}-{slug}-{hashlib.sha1(raw).hexdigest()[:8]}"


def next_experiment_id() -> str:
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()[:10].replace("-", "")
    existing = {path.name for path in EXPERIMENTS_DIR.glob(f"E-{stamp}-*")}
    number = 1
    while f"E-{stamp}-{number:03d}" in existing:
        number += 1
    return f"E-{stamp}-{number:03d}"


def reserve_experiment_id() -> str:
    """Allocate and create an experiment directory atomically across processes."""
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()[:10].replace("-", "")
    with _lock_file(EXPERIMENTS_DIR / ".experiment_id"):
        existing = {path.name for path in EXPERIMENTS_DIR.glob(f"E-{stamp}-*")}
        number = 1
        while f"E-{stamp}-{number:03d}" in existing:
            number += 1
        exp_id = f"E-{stamp}-{number:03d}"
        (EXPERIMENTS_DIR / exp_id).mkdir(parents=True, exist_ok=False)
        return exp_id


def experiment_dir(exp_id: str):
    return EXPERIMENTS_DIR / exp_id


def artifact_paths(exp_id: str) -> dict[str, str]:
    root = experiment_dir(exp_id)
    return {
        "experiment_dir": str(root),
        "hypothesis": str(root / "hypothesis.json"),
        "factor_spec": str(root / "factor_spec.json"),
        "manifest": str(root / "manifest.json"),
        "report": str(root / "report.json"),
        "report_html": str(root / "report.html"),
        "audit": str(root / "audit.json"),
        "decision_brief": str(root / "decision_brief.json"),
        "decision": str(root / "decision.json"),
        "review": str(root / "review.json"),
    }
