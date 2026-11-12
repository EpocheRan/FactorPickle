"""Family ledger / FDR / freeze primitives (P1)."""

from __future__ import annotations

import math

from factor_lab.alpha.fdr import estimate_n_eff, family_fdr, program_fdr


POLICY = {
    "schema": "factor_lab.alpha.families.v1",
    "q": 0.1,
    "n_eff": {"method": "expression_jaccard", "threshold": 0.7, "sensitivity_thresholds": [0.5, 0.7, 0.9]},
    "families": [
        {"family_id": "fam-a", "pre_registered": True},
        {"family_id": "fam-b", "pre_registered": True},
    ],
    "legacy_family_id": "legacy-unassigned",
}


def _row(expr: str, family: str, p: float, net_t: float = 2.0) -> dict:
    return {
        "schema": "factor_lab.alpha.family_trial.v1",
        "trial_id": expr,
        "family_id": family,
        "stage": "dev",
        "canonical_expression": expr,
        "expression": expr,
        "p_net": p * 2.0,
        "p_pos": p,
        "n_dates": 148,
        "net_t": net_t,
        "metrics": {"n_dates": 148, "net_t": net_t, "net_spread": 0.004, "coverage": 0.99},
    }


def test_estimate_n_eff_is_clustered_and_no_larger_than_unique() -> None:
    result = estimate_n_eff(["rank(close)", "rank(close)", "rank(volume)", "ts_mean(close, 5)"])
    assert result["n_unique"] == 3
    assert 1 <= result["n_eff"] <= 3
    assert set(result["sensitivity"]) >= {"0.5", "0.7", "0.9"}


def test_program_fdr_uses_joint_denominator_and_one_sided_positive_p() -> None:
    ledger = [
        _row("rank(close)", "fam-a", 0.001),
        _row("rank(volume)", "fam-a", 0.10),
        _row("rank(low)", "fam-b", 0.02),
        _row("rank(pb_mrq)", "fam-b", 0.04),
        _row("rank(amount)", "legacy-unassigned", 0.03),
    ]
    result = program_fdr("rank(pb_mrq)", policy=POLICY, ledger=ledger)
    assert result["family_id"] == "program-all"
    assert result["denominator"] == "program_all_families"
    assert result["n_unique"] == 4  # legacy excluded from freeze denominator
    assert result["p_net"] == 0.04
    # explicit candidate rank is based on the joint positive-alpha p values
    assert result["rank"] == 3
    assert math.isclose(result["bh_threshold"], 0.1 * 3 / result["n_eff"])
    assert result["passed"] is (result["p_net"] <= result["bh_threshold"])


def test_family_fdr_can_include_legacy_for_reclassification() -> None:
    ledger = [_row("rank(close)", "fam-a", 0.001), _row("rank(volume)", "legacy-unassigned", 0.2)]
    result = program_fdr("rank(volume)", policy=POLICY, ledger=ledger, include_legacy=True)
    assert result["denominator"] == "program_all_families+legacy"
    assert result["n_unique"] == 2
    assert result["passed"] is False
