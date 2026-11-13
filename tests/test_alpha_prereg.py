"""Deterministic pre-registration validation and freeze."""

from __future__ import annotations

import pytest

from factor_lab.alpha.prereg import (
    PRE_REG_SCHEMA,
    freeze_pre_registration,
    validate_pre_registration,
    verify_frozen,
)


def _payload() -> dict:
    return {
        "schema": PRE_REG_SCHEMA,
        "campaign_id": "CAMP-TEST",
        "research_question": "does margin predict returns?",
        "constraints": ["dev only"],
        "evaluation": {"start": "2017-01-03", "end": "2025-10-31", "universe": "pit_liquidity_1000_size_screen", "horizon": 20, "cost": {"buy": 2.44, "sell": 7.44, "borrow": 0}},
        "families": [
            {
                "family_id": "fam-max-lottery",
                "research_question": "margin crowding",
                "pre_registered": True,
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "expression": "neg(rank(div(ts_delta(margin_rzye, 5), float_mv)))",
                        "expected_sign": "positive",
                        "kill_criteria": ["net_t < 2"],
                    }
                ],
            }
        ],
    }


def test_validate_rejects_unknown_field_and_duplicates() -> None:
    good = _payload()
    assert validate_pre_registration(good, campaign_id="CAMP-TEST")["ok"] is True
    bad = _payload()
    bad["families"][0]["candidates"][0]["expression"] = "rank(not_a_field)"
    assert validate_pre_registration(bad)["ok"] is False
    duplicate = _payload()
    duplicate["families"][0]["candidates"].append(dict(duplicate["families"][0]["candidates"][0], candidate_id="cand-2"))
    assert validate_pre_registration(duplicate)["ok"] is False


def test_freeze_writes_hash_and_verifies(tmp_path) -> None:
    target = tmp_path / "pre_registration.frozen.json"
    frozen = freeze_pre_registration(_payload(), campaign_id="CAMP-TEST", path=target)
    assert target.exists()
    assert frozen["schema"].endswith("frozen.v1")
    assert frozen["pre_registration_hash"]
    check = verify_frozen(target)
    assert check["ok"] is True
    tampered = target.read_text(encoding="utf-8").replace("ts_delta(margin_rzye, 5)", "ts_delta(margin_rzye, 10)")
    target.write_text(tampered, encoding="utf-8")
    assert verify_frozen(target)["ok"] is False


def test_total_share_is_admitted_pre_registration_field() -> None:
    payload = _payload()
    payload["families"][0]["candidates"][0]["expression"] = "rank(div(holder_chg, total_share))"
    report = validate_pre_registration(payload, campaign_id="CAMP-TEST")
    assert report["ok"] is True, report["issues"]
