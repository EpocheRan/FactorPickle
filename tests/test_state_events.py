"""State event axes: PIT lag, forward-fill and ST state."""

from __future__ import annotations

import pandas as pd

import data_interface.state_events as state_events
from factor_lab.alpha.data_axes import P1_AXES, catalog


DATES = pd.DatetimeIndex(
    pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
)


class _FakeDB:
    def query_dataframe(self, sql: str):
        if "from forecast" in sql:
            return pd.DataFrame(
                [
                    {
                        "ts": "2024-01-02",
                        "code": "000001",
                        "p_change_min": 10.0,
                        "p_change_max": 30.0,
                        "net_profit_min": 100.0,
                        "net_profit_max": 300.0,
                    }
                ]
            )
        if "from express" in sql:
            return pd.DataFrame(
                [
                    {
                        "ts": "2024-01-03",
                        "code": "000001",
                        "revenue": 1000.0,
                        "n_income": 100.0,
                        "total_assets": 5000.0,
                        "diluted_eps": 0.5,
                        "yoy_net_profit": 20.0,
                    }
                ]
            )
        if "from namechange" in sql:
            return pd.DataFrame(
                [{"ts": "2024-01-02", "code": "000001", "name": "ST测试"}]
            )
        return pd.DataFrame()


def test_state_event_fields_are_registered() -> None:
    p1 = catalog()["fields"]["p1"]
    for field in (
        "fc_p_change_mid",
        "fc_net_profit_mid",
        "fc_days_since",
        "ex_revenue",
        "ex_n_income",
        "ex_total_assets",
        "ex_diluted_eps",
        "ex_yoy_net_profit",
        "is_st",
    ):
        assert field in p1, field
    assert set(P1_AXES["earnings_forecast"]["fields"]) == set(
        state_events.STATE_EVENT_FIELDS["forecast"]
    )


def test_state_events_are_forward_filled_and_lagged(monkeypatch) -> None:
    monkeypatch.setattr(state_events, "_trading_days", lambda start, end: DATES)
    bundle = state_events.load_state_events(
        ["000001"], "2024-01-02", "2024-01-05", db=_FakeDB()
    )
    p_change = bundle.frames["fc_p_change_mid"]["000001"]
    assert pd.isna(p_change.loc["2024-01-02"])
    assert p_change.loc["2024-01-03"] == 20.0
    assert p_change.loc["2024-01-05"] == 20.0
    assert bundle.frames["fc_days_since"]["000001"].loc["2024-01-03"] == 1.0

    revenue = bundle.frames["ex_revenue"]["000001"]
    assert pd.isna(revenue.loc["2024-01-03"])
    assert revenue.loc["2024-01-04"] == 1000.0

    is_st = bundle.frames["is_st"]["000001"]
    assert is_st.loc["2024-01-02"] == 0.0
    assert is_st.loc["2024-01-03"] == 1.0
    assert is_st.loc["2024-01-05"] == 1.0
