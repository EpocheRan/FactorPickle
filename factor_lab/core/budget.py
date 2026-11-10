"""Campaign / cycle / daily / monthly budget guard.

Design numbers (v1):

- LLM: 3,000 CNY / month, 100 CNY / day, 20% reserve, 25 CNY / cycle,
  at most 3 cycles/day, 60 CNY / campaign.
- Disk: 150GB Lab quota = 100GB output + 20GB cache + 30GB archive;
  keep at least 50GB free on the machine.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic

POLICY_PATH = config.CONTROL_DIR / "budget_policy.json"
STATE_PATH = config.CONTROL_DIR / "budget_state.json"
PRICING_PATH = config.CONTROL_DIR / "llm_pricing.json"


class BudgetExceeded(RuntimeError):
    pass


DEFAULT_INPUT_RMB_PER_MTOK = 0.04
DEFAULT_OUTPUT_RMB_PER_MTOK = 8.0


def _default_pricing() -> dict[str, dict[str, float]]:
    return {
        "deepseek-flash": {
            "input_rmb_per_mtok": float(
                os.environ.get("FACTOR_LAB_INPUT_RMB_PER_MTOK", DEFAULT_INPUT_RMB_PER_MTOK)
            ),
            "output_rmb_per_mtok": float(
                os.environ.get("FACTOR_LAB_OUTPUT_RMB_PER_MTOK", DEFAULT_OUTPUT_RMB_PER_MTOK)
            ),
        }
    }


@dataclass
class BudgetPolicy:
    schema_version: str = "factor_lab.budget.v1"
    monthly_rmb: float = 3000.0
    daily_rmb: float = 100.0
    reserve_ratio: float = 0.20
    per_cycle_rmb: float = 25.0
    max_cycles_per_day: int = 3
    per_campaign_rmb: float = 60.0
    disk_quota_gb: float = 150.0
    output_cap_gb: float = 100.0
    panel_cache_cap_gb: float = 20.0
    archive_cap_gb: float = 30.0
    free_disk_floor_gb: float = 50.0
    pricing: dict[str, dict[str, float]] = field(default_factory=_default_pricing)

    @property
    def daily_spend_limit(self) -> float:
        return round(self.daily_rmb * (1.0 - self.reserve_ratio), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "monthly_rmb": self.monthly_rmb,
            "daily_rmb": self.daily_rmb,
            "reserve_ratio": self.reserve_ratio,
            "per_cycle_rmb": self.per_cycle_rmb,
            "max_cycles_per_day": self.max_cycles_per_day,
            "per_campaign_rmb": self.per_campaign_rmb,
            "disk_quota_gb": self.disk_quota_gb,
            "output_cap_gb": self.output_cap_gb,
            "panel_cache_cap_gb": self.panel_cache_cap_gb,
            "archive_cap_gb": self.archive_cap_gb,
            "free_disk_floor_gb": self.free_disk_floor_gb,
            "pricing": self.pricing,
            "daily_spend_limit": self.daily_spend_limit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BudgetPolicy":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})


def ensure_policy() -> dict[str, Any]:
    current = read_json(POLICY_PATH)
    if isinstance(current, dict) and current.get("monthly_rmb"):
        # migrate the earlier unpriced placeholder without changing user caps
        pricing = current.get("pricing") or {}
        flash = pricing.get("deepseek-flash") or {}
        if not float(flash.get("input_rmb_per_mtok", 0) or 0) and not float(
            flash.get("output_rmb_per_mtok", 0) or 0
        ):
            current["pricing"] = _default_pricing()
            current["daily_spend_limit"] = round(
                float(current.get("daily_rmb", 100.0)) * (1 - float(current.get("reserve_ratio", 0.2))),
                2,
            )
            write_json_atomic(POLICY_PATH, current)
            write_json_atomic(PRICING_PATH, _default_pricing())
        return current
    payload = BudgetPolicy().to_dict()
    write_json_atomic(POLICY_PATH, payload)
    write_json_atomic(PRICING_PATH, payload["pricing"])
    return payload


def load_policy() -> BudgetPolicy:
    return BudgetPolicy.from_dict(ensure_policy())


def default_state(month: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "factor_lab.budget_state.v1",
        "month": month or date.today().strftime("%Y-%m"),
        "day": date.today().isoformat(),
        "month_spent_rmb": 0.0,
        "day_spent_rmb": 0.0,
        "cycles_today": 0,
        "campaigns": {},
        "entries": [],
    }


def _reset_if_needed(state: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    month = today.strftime("%Y-%m")
    if state.get("month") != month:
        state = default_state(month)
    elif state.get("day") != today.isoformat():
        state["day"] = today.isoformat()
        state["day_spent_rmb"] = 0.0
        state["cycles_today"] = 0
    return state


def load_state() -> dict[str, Any]:
    state = read_json(STATE_PATH)
    if not isinstance(state, dict):
        state = default_state()
    return _reset_if_needed(state)


def save_state(state: dict[str, Any]) -> dict[str, Any]:
    write_json_atomic(STATE_PATH, state)
    return state


def _cost_from_tokens(tokens_in: int, tokens_out: int, model: str = "deepseek-flash") -> float | None:
    policy = load_policy()
    price = (policy.pricing or {}).get(model) or {}
    pin = float(price.get("input_rmb_per_mtok", 0) or 0)
    pout = float(price.get("output_rmb_per_mtok", 0) or 0)
    if pin <= 0 and pout <= 0:
        return None
    return round(tokens_in / 1_000_000 * pin + tokens_out / 1_000_000 * pout, 6)


def check_cycle(*, estimated_rmb: float | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = load_policy()
    state = _reset_if_needed(dict(state or load_state()))
    estimated = float(policy.per_cycle_rmb if estimated_rmb is None else estimated_rmb)
    reasons: list[str] = []
    if state["cycles_today"] >= policy.max_cycles_per_day:
        reasons.append(f"cycles_today={state['cycles_today']} >= {policy.max_cycles_per_day}")
    if state["day_spent_rmb"] + estimated > policy.daily_spend_limit:
        reasons.append(
            f"day_spent={state['day_spent_rmb']:.2f} + {estimated:.2f} > {policy.daily_spend_limit:.2f}"
        )
    if state["month_spent_rmb"] + estimated > policy.monthly_rmb * (1 - policy.reserve_ratio):
        reasons.append(
            f"month_spent={state['month_spent_rmb']:.2f} + {estimated:.2f} > monthly cap"
        )
    return {"ok": not reasons, "reasons": reasons, "state": state, "policy": policy.to_dict()}


def check_campaign(campaign_id: str, *, estimated_rmb: float | None = None) -> dict[str, Any]:
    policy = load_policy()
    state = _reset_if_needed(dict(load_state()))
    spent = float((state.get("campaigns", {}).get(campaign_id) or {}).get("spent_rmb", 0.0))
    estimated = float(policy.per_campaign_rmb if estimated_rmb is None else estimated_rmb)
    reasons: list[str] = []
    if spent + estimated > policy.per_campaign_rmb:
        reasons.append(f"campaign spent={spent:.2f} + {estimated:.2f} > {policy.per_campaign_rmb:.2f}")
    global_check = check_cycle(estimated_rmb=estimated, state=state)
    reasons.extend(global_check["reasons"])
    return {"ok": not reasons, "reasons": reasons, "campaign_spent_rmb": spent, "policy": policy.to_dict()}


def record_usage(
    *,
    agent: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_rmb: float | None = None,
    campaign_id: str | None = None,
    cycle: bool = False,
    model: str = "deepseek-flash",
) -> dict[str, Any]:
    policy = load_policy()
    if cost_rmb is None:
        cost_rmb = _cost_from_tokens(tokens_in, tokens_out, model)
    if cost_rmb is None:
        # Unpriced usage is recorded as tokens only; do not invent a price.
        cost_rmb = 0.0
        priced = False
    else:
        priced = True
    state = _reset_if_needed(dict(load_state()))
    state["month_spent_rmb"] = round(float(state["month_spent_rmb"]) + float(cost_rmb), 6)
    state["day_spent_rmb"] = round(float(state["day_spent_rmb"]) + float(cost_rmb), 6)
    if cycle:
        state["cycles_today"] = int(state["cycles_today"]) + 1
    if campaign_id:
        item = state.setdefault("campaigns", {}).setdefault(
            campaign_id, {"spent_rmb": 0.0, "tokens_in": 0, "tokens_out": 0}
        )
        item["spent_rmb"] = round(float(item["spent_rmb"]) + float(cost_rmb), 6)
        item["tokens_in"] = int(item["tokens_in"]) + int(tokens_in)
        item["tokens_out"] = int(item["tokens_out"]) + int(tokens_out)
    state.setdefault("entries", []).append(
        {
            "at": config.now_iso(),
            "agent": agent,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "cost_rmb": round(float(cost_rmb), 6),
            "priced": priced,
            "campaign_id": campaign_id,
            "cycle": bool(cycle),
        }
    )
    save_state(state)
    return {"cost_rmb": cost_rmb, "priced": priced, "state": state}


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _gb(value: int) -> float:
    return round(value / 1024**3, 3)


def disk_status() -> dict[str, Any]:
    policy = load_policy()
    usage = shutil.disk_usage(config.PROJECT_ROOT)
    lab = _dir_size(config.LAB_ROOT)
    cache = _dir_size(config.PROJECT_ROOT / "output" / "cache")
    archive = _dir_size(config.ARCHIVE_DIR)
    free_gb = usage.free / 1024**3
    breaches: list[str] = []
    if free_gb < policy.free_disk_floor_gb:
        breaches.append(f"free={free_gb:.1f}GB < floor={policy.free_disk_floor_gb:.1f}GB")
    if _gb(lab) > policy.output_cap_gb:
        breaches.append(f"lab={_gb(lab)}GB > output cap={policy.output_cap_gb}GB")
    if _gb(cache) > policy.panel_cache_cap_gb:
        breaches.append(f"cache={_gb(cache)}GB > cache cap={policy.panel_cache_cap_gb}GB")
    if _gb(archive) > policy.archive_cap_gb:
        breaches.append(f"archive={_gb(archive)}GB > archive cap={policy.archive_cap_gb}GB")
    return {
        "schema_version": "factor_lab.budget.disk.v1",
        "free_gb": round(free_gb, 2),
        "lab_gb": _gb(lab),
        "cache_gb": _gb(cache),
        "archive_gb": _gb(archive),
        "ok": not breaches,
        "breaches": breaches,
        "policy": policy.to_dict(),
    }


def status() -> dict[str, Any]:
    return {
        "policy": load_policy().to_dict(),
        "state": load_state(),
        "disk": disk_status(),
    }
