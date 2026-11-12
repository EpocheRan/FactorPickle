"""P0 instrument-calibration probes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.probes import PROBE_SCHEMA, run_probes


def _dataset(days: int = 160, symbols: int = 30) -> AlphaDataset:
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2023-01-02", periods=days)
    columns = [f"{index:06d}" for index in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0005, 0.018, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1.0 + rets).cumprod()
    volume = pd.DataFrame(rng.lognormal(13, 0.4, (days, symbols)), index=dates, columns=columns)
    amount = volume * close
    context = AlphaContext.from_fields(
        {
            "close": close,
            "open": close * 1.001,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": volume,
            "amount": amount,
            "returns": close.pct_change(fill_method=None),
            "pb_mrq": pd.DataFrame(rng.uniform(0.5, 8.0, (days, symbols)), index=dates, columns=columns),
            "float_mv": pd.DataFrame(rng.lognormal(24, 0.6, (days, symbols)), index=dates, columns=columns),
            "mf_net_amount": pd.DataFrame(rng.normal(0, 1e7, (days, symbols)), index=dates, columns=columns),
        },
        close=close,
    )
    return AlphaDataset(
        context=context,
        codes=list(columns),
        start=str(dates.min().date()),
        end=str(dates.max().date()),
        warmup_days=10,
        data_manifest={"fields": context.field_names, "codes": len(columns)},
    )


def test_probe_report_has_three_probes_and_is_written(tmp_path) -> None:
    dataset = _dataset()
    payload = run_probes(dataset, random_n=5, known=[("close_rank", "rank(close)")], out_path=tmp_path / "probe.json")
    assert payload["schema"] == PROBE_SCHEMA
    assert payload["cheat"]["passed"] is True
    assert payload["random"]["n"] == 5
    assert set(payload["random"]) >= {"expected_rate", "observed_rate", "n_passed"}
    assert payload["known"]["n"] == 1
    assert payload["conclusion"]["code"] in {
        "A_pipeline_bias",
        "B_factor_family_dead_or_data_gap",
        "C_gate_or_trial_accounting_miscalibrated",
        "instrument_plausible",
    }
    path = Path(payload["path"])
    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["probe_id"] == payload["probe_id"]


def test_cheat_probe_fails_when_open_is_missing() -> None:
    dataset = _dataset()
    # rebuilding a context without open should raise a readable error
    import pytest

    from factor_lab.alpha.probes import _run_cheat

    with pytest.raises(ValueError, match="open"):
        _run_cheat(
            type(dataset)(context=_without_open(dataset.context), codes=dataset.codes, start=dataset.start, end=dataset.end, warmup_days=10, data_manifest=dataset.data_manifest),
            horizon=5, groups=5, cost_bps=20.0, universe=None,
        )


def _without_open(context: AlphaContext) -> AlphaContext:
    from factor_lab.alpha.compiler import AlphaContext as Ctx
    fields = {name: value for name, value in context.fields.items() if name != "open"}
    return Ctx.from_fields(fields, close=context.close)
