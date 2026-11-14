"""Registration tests for the complete-but-unregistered data axes.

The axes admitted here are the fields verified as complete in the database
(2017-01-03..2025-10-31, >=95% non-null, full cross-section).  The test pins
the registry contract so a future catalog edit cannot silently drop them.
"""

from __future__ import annotations

import pandas as pd

from factor_lab.alpha.data_axes import P0_AXES, catalog, ml_feature_catalog
from factor_lab.alpha.tradability import build_tradability_mask


def test_complete_fields_are_registered() -> None:
    p0 = catalog()["fields"]["p0"]
    for field in (
        "up_limit",
        "down_limit",
        "is_suspended",
        "margin_rzche",
        "margin_rqchl",
        "mf_buy_md",
        "mf_buy_md_vol",
        "mf_net_vol",
        "fin_total_revenue",
        "fin_cash_and_cash_equivalents",
        "fin_total_debt",
        "fin_net_debt",
        "fin_capital_expenditure",
    ):
        assert field in p0, field

    assert {"up_limit", "down_limit", "is_suspended"} <= set(
        P0_AXES["tradability"]["fields"]
    )
    assert {"margin_rzche", "margin_rqchl"} <= set(P0_AXES["margin"]["fields"])
    assert {"mf_buy_md", "mf_net_vol"} <= set(P0_AXES["moneyflow"]["fields"])


def test_registered_fields_reach_ml_catalog() -> None:
    expanded = set(ml_feature_catalog()["expanded_fields"])
    assert {"up_limit", "is_suspended", "fin_net_debt", "mf_buy_md"} <= expanded


def test_exact_limits_override_the_board_ratio() -> None:
    dates = pd.date_range("2024-01-01", periods=2)
    columns = ["600000", "300001", "000001"]
    close = pd.DataFrame(
        [[10.0, 10.0, 10.0], [10.95, 11.0, 10.0]], index=dates, columns=columns
    )
    high = close.copy()
    low = close.copy()
    volume = pd.DataFrame(100.0, index=dates, columns=columns)
    up_limit = pd.DataFrame([[11.0, 10.0, 11.0], [11.1, 10.8, 11.0]], index=dates, columns=columns)
    down_limit = pd.DataFrame([[9.0, 8.0, 9.0], [9.0, 8.8, 9.0]], index=dates, columns=columns)
    is_suspended = pd.DataFrame(0.0, index=dates, columns=columns)
    is_suspended.loc[dates[1], "000001"] = 1.0
    fields = {
        "high": high,
        "low": low,
        "volume": volume,
        "close_raw": close,
        "high_raw": high,
        "low_raw": low,
        "up_limit": up_limit,
        "down_limit": down_limit,
        "is_suspended": is_suspended,
    }
    context = type("Ctx", (), {"close": close, "fields": fields})()

    mask = build_tradability_mask(context)

    # 600000: the 10% board fallback would block at 10.945, but the exact
    # exchange limit is 11.10, so 10.95 is still tradable.
    assert bool(mask.loc[dates[1], "600000"])
    # 300001: the 20% board fallback would allow 11.00, but the exact limit is
    # 10.80, so the close is limit-up and cannot be bought.
    assert not bool(mask.loc[dates[1], "300001"])
    # 000001: explicit suspension row blocks the name.
    assert not bool(mask.loc[dates[1], "000001"])
