"""Reproducible ten-year dataset cache: atomic pickle + coverage sidecar."""

from __future__ import annotations

import hashlib
import json
import pickle

import pandas as pd
import pytest

from factor_lab.alpha import ten_year
from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset


def _dataset() -> AlphaDataset:
    dates = pd.date_range("2024-01-02", periods=4, freq="D")
    close = pd.DataFrame({"000001": [10.0, 10.5, 10.8, 11.0], "600519": [1700.0, 1710.0, None, 1720.0]},
                         index=dates)
    fields = {
        "ev_sales": pd.DataFrame({"000001": [1.0, 1.1, None, 1.2], "600519": [9.0, 9.1, 9.2, 9.3]},
                                 index=dates),
        "list_days": pd.DataFrame({"000001": [100.0] * 4, "600519": [2000.0] * 4}, index=dates),
        "float_share": pd.DataFrame({"000001": [1e9, 1e9, 1e9, 1e9], "600519": [1.2e9] * 4}, index=dates),
    }
    context = AlphaContext(close=close, fields=fields)
    return AlphaDataset(
        context=context,
        codes=["000001", "600519"],
        start="2024-01-02",
        end="2024-01-05",
        warmup_days=0,
        data_manifest={
            "services": ["market", "valuation"],
            "fields": ["close", "ev_sales", "list_days", "float_share"],
            "codes": 2,
            "start": "2024-01-02",
            "end": "2024-01-05",
            "rows": 8,
            "data_hash": "deadbeef",
            "category": "price",
            "price_basis": "pit_cumulative_adjusted_hfq",
            "execution_price_basis": "raw",
            "per_field_coverage": {"ev_sales": 0.75},
        },
    )


def test_build_dataset_cache_writes_pickle_and_sidecar(monkeypatch, tmp_path):
    monkeypatch.setattr(ten_year, "load_alpha_dataset", lambda *args, **kwargs: _dataset())
    monkeypatch.setattr(ten_year, "load_pool", lambda gateway=None: ["000001", "600519"])
    target = tmp_path / "ten_year_cache_hfq.pkl"

    payload = ten_year.build_dataset_cache(out_path=target, coverage_start="2024-01-03")

    assert target.exists()
    assert not target.with_name(target.name + ".tmp").exists()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert payload["sha256"] == digest
    assert payload["bytes"] == target.stat().st_size
    assert payload["schema"] == ten_year.DATASET_CACHE_SCHEMA
    assert payload["price_basis"] == "pit_cumulative_adjusted_hfq"
    assert payload["shape"] == [4, 2]

    sidecar = json.loads((tmp_path / "ten_year_cache_hfq_dataset_v5.json").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == digest
    assert sidecar["coverage_window"] == {"start": "2024-01-03", "end": "2025-10-31"}
    # ev_sales over 2024-01-03..2024-01-05: 3 dates x 2 codes, one null cell
    assert sidecar["per_field_coverage"]["ev_sales"] == pytest.approx(5 / 6)
    # PIT-eligible denominator: 600519 has no close on 2024-01-04, so 5 cells
    assert sidecar["n_eligible_cells"] == 5
    assert sidecar["per_field_coverage_eligible"]["ev_sales"] == pytest.approx(4 / 5)
    assert sidecar["per_field_coverage"]["list_days"] == pytest.approx(1.0)
    assert sidecar["per_field_coverage"]["float_share"] == pytest.approx(1.0)
    assert "list_days" in sidecar["data_manifest"]["fields"]
    assert sidecar["n_fields"] == len(sidecar["per_field_coverage"])

    restored = pickle.loads(target.read_bytes())
    assert restored.codes == ["000001", "600519"]
    assert list(restored.context.close.columns) == ["000001", "600519"]


def test_build_dataset_cache_uses_protocol_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(ten_year, "load_alpha_dataset", lambda *args, **kwargs: _dataset())
    monkeypatch.setattr(ten_year, "load_pool", lambda gateway=None: ["000001"])
    target = tmp_path / "cache.pkl"
    payload = ten_year.build_dataset_cache(out_path=target)
    protocol = ten_year.load_protocol()
    assert payload["start"] == protocol["dataset_start"]
    assert payload["end"] == protocol["dataset_end"]
    assert payload["warmup_days"] == protocol["warmup_days"]
    assert payload["coverage_window"]["start"] == protocol["expanded_axes_effective_start"]
