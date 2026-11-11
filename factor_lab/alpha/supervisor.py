"""Deterministic Supervisor wrapper around the bounded campaign controller."""

from __future__ import annotations

from typing import Any

from factor_lab.alpha.campaign import CampaignController


class Supervisor:
    """Hard-limit controller: rounds, budget, stop rules, no LLM authority."""

    def __init__(self, controller: CampaignController | None = None) -> None:
        self.controller = controller or CampaignController()

    def start(self, objective: str, *, max_rounds: int = 5, budget: float | None = None) -> dict[str, Any]:
        return self.controller.create(objective, max_rounds=max_rounds, budget=budget)

    def round(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.controller.record_round(campaign_id, payload)

    def stop(self, campaign_id: str, reason: str) -> dict[str, Any]:
        return self.controller.stop(campaign_id, reason)

    def status(self, campaign_id: str) -> dict[str, Any]:
        return self.controller.summary(campaign_id)
