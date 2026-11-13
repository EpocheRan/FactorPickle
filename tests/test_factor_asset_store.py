"""Custom factor asset store: cutover guard and no-version contract."""

from __future__ import annotations

import pandas as pd
import pytest

from factor_engine.asset_schema import CUSTOM_CUTOVER_AT
from factor_engine.store import FactorStore


class FakeDB:
    def __init__(self):
        self.rows = []
        self.frames = []

    def query_dataframe(self, sql, params=None):
        return pd.DataFrame()

    def execute_values(self, sql, rows, page_size=1000):
        rows = list(rows)
        self.rows.extend(rows)
        self.frames.append(pd.DataFrame(rows))
        return len(rows)


def value_frame(computed_at):
    return pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2022-10-12"),
                "code": "000001",
                "factor_id": "momentum.ret",
                "value": 1.24,
                "quality": "ok",
                "computed_at": pd.Timestamp(computed_at),
            }
        ]
    )


def test_values_before_cutover_are_rejected():
    store = FactorStore(db=FakeDB())
    cutoff = pd.Timestamp(CUSTOM_CUTOVER_AT).tz_localize(None)
    with pytest.raises(ValueError, match="before cutover"):
        store.upsert_values(value_frame(cutoff - pd.Timedelta(days=1)))


def test_historical_ts_after_cutover_is_allowed():
    store = FactorStore(db=FakeDB())
    cutoff = pd.Timestamp(CUSTOM_CUTOVER_AT).tz_localize(None)
    frame = value_frame(cutoff + pd.Timedelta(days=1))
    assert store.upsert_values(frame) == 1
