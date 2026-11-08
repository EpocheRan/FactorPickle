from __future__ import annotations

import concurrent.futures
from pathlib import Path

from factor_lab import schemas
from factor_lab.clock import next_token, parse_hlc
from factor_lab.cycle import _select_specs
from factor_lab.cycle_ideas import CYCLE_SPECS
from factor_lab.ledger import Ledger
from factor_lab.schemas import Hypothesis, OutcomeSpec, reserve_experiment_id, trial_id


def _hypothesis(fid: str = "H-PHASE0-001", family: str = "F-phase0-test") -> Hypothesis:
    return Hypothesis(
        hypothesis_id=fid,
        factor_id="risk.max_drawdown_20d",
        title="phase0",
        mechanism="phase0",
        expected_sign=-1,
        outcomes=[OutcomeSpec(universe="pool:2022-01-04:200", horizon=5)],
        family_id=family,
    )


def test_event_clock_is_monotonic(tmp_path: Path) -> None:
    first = next_token(tmp_path / "clock.json")
    second = next_token(tmp_path / "clock.json")
    assert second["event_seq"] == first["event_seq"] + 1
    assert parse_hlc(second["event_hlc"]) > parse_hlc(first["event_hlc"])


def test_ledger_hlc_and_parent_link(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    hyp = _hypothesis()
    hyp_event = ledger.register_hypothesis(hyp)
    trial = trial_id(hyp.hypothesis_id, hyp.outcomes[0])
    ledger.start_run(
        run_id="RUN-P0",
        trial_id=trial,
        hypothesis_id=hyp.hypothesis_id,
        factor_id=hyp.factor_id,
        outcome=hyp.outcomes[0].to_dict(),
        family_id=hyp.family_id,
        parent_event_id=hyp_event["event_id"],
    )
    check = ledger.orphan_check(
        run_id="RUN-P0",
        hypothesis_id=hyp.hypothesis_id,
        trial_id=trial,
        require_parent=True,
    )
    assert check["ok"]
    assert check["order"] == "hlc"


def test_ledger_family_trials(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in (1, 2):
        hyp = _hypothesis(f"H-PHASE0-{index:03d}")
        event = ledger.register_hypothesis(hyp)
        ledger.start_run(
            run_id=f"RUN-{index}",
            trial_id=trial_id(hyp.hypothesis_id, hyp.outcomes[0]),
            hypothesis_id=hyp.hypothesis_id,
            factor_id=hyp.factor_id,
            outcome=hyp.outcomes[0].to_dict(),
            family_id=hyp.family_id,
            parent_event_id=event["event_id"],
        )
    assert len(ledger.family_trials("F-phase0-test")) == 2
    assert ledger.summary()["family_trials"]["F-phase0-test"] == 2


def test_reserve_experiment_ids_are_atomic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(schemas, "EXPERIMENTS_DIR", tmp_path / "experiments")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: reserve_experiment_id(), range(4)))
    assert len(ids) == len(set(ids))
    assert all((tmp_path / "experiments" / exp_id).is_dir() for exp_id in ids)


def test_cycle_catalog_and_scout_selection() -> None:
    ids = [spec.factor_id for spec in CYCLE_SPECS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 20
    selected = _select_specs(list(CYCLE_SPECS), 4)
    assert len(selected) == 4
    assert len({spec.factor_id for spec in selected}) == 4
    assert len({spec.bet for spec in selected}) >= 2
