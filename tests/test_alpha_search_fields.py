"""Default search must sample the expanded P0/P1 data axes."""

from __future__ import annotations

from types import SimpleNamespace

from factor_lab.alpha.search import _fields


def test_default_search_fields_include_p0_p1_axes() -> None:
    names = [
        "close", "open", "high", "low", "volume", "amount", "turnover", "returns",
        "float_mv", "total_mv", "pb_mrq", "pe_ttm",
        "margin_rzye", "margin_rqye", "mf_net_amount", "fin_net_income", "fin_operating_cash_flow",
        "block_amount", "top_net_amount", "rep_amount", "holder_num",
    ]
    context = SimpleNamespace(field_names=names)
    fields = _fields(context)
    for field in ("margin_rzye", "mf_net_amount", "fin_net_income", "block_amount", "top_net_amount", "holder_num"):
        assert field in fields
    assert len(fields) <= 60
