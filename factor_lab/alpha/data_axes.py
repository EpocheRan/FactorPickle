"""P0/P1 data-axis catalog for Alpha research.

P0 axes are already materialized in the Alpha panel by the DataInterfaceGateway
(margin, moneyflow structure, financial quality, valuation).  P1 axes are the
event features produced by ``data_interface.p1_events``.  Keeping the catalog
next to Alpha makes the integration explicit for FactorBuilder/PathFinder and
for coverage tests; it is descriptive only and never bypasses the Gate.
"""

from __future__ import annotations

from typing import Any

from data_interface.p1_events import P1_EVENT_FIELDS


P0_AXES: dict[str, dict[str, Any]] = {
    "margin": {
        "label": "两融 / 杠杆",
        "fields": ["margin_rzye", "margin_rqye", "margin_rzmre", "margin_rqyl"],
        "pit": "exchange disclosure after close; use T+1 in expressions",
        "source": "margin_detail via DataInterfaceGateway.margin_bundle",
    },
    "moneyflow": {
        "label": "资金流结构",
        "fields": [
            "mf_buy_sm",
            "mf_buy_lg",
            "mf_buy_elg",
            "mf_sell_sm",
            "mf_sell_lg",
            "mf_sell_elg",
            "mf_net_amount",
        ],
        "pit": "after-close moneyflow; T+1",
        "source": "moneyflow_daily via DataInterfaceGateway.flow_bundle",
    },
    "financial_quality": {
        "label": "财务质量 / 应计",
        "fields": [
            "fin_net_income",
            "fin_operating_cash_flow",
            "fin_total_assets",
            "fin_total_equity",
            "fin_total_liabilities",
            "fin_revenue",
            "fin_gross_profit",
            "fin_research_and_development_expense",
            "fin_basic_eps",
        ],
        "pit": "financial statements aligned to publish_date by data_interface",
        "source": "financial statements via DataInterfaceGateway.financial_bundle",
    },
    "valuation": {
        "label": "估值剖面",
        "fields": ["pb_mrq", "pe_ttm", "ps_ttm", "total_mv", "float_mv"],
        "conditional_fields": ["p_nwc"],
        "pit": "daily valuation, PIT published",
        "source": "fin_valuation_daily via DataInterfaceGateway.valuation_bundle",
    },
}

P1_AXES: dict[str, dict[str, Any]] = {
    "block_trade": {
        "label": "大宗交易",
        "fields": P1_EVENT_FIELDS["block_trade"],
        "pit": "trade-day disclosure; data_interface.p1_events lags one trading day",
        "source": "block_trade via DataInterfaceGateway.event_bundle",
    },
    "top_list": {
        "label": "龙虎榜",
        "fields": P1_EVENT_FIELDS["top_list"],
        "pit": "trade-day disclosure; data_interface.p1_events lags one trading day",
        "source": "top_list via DataInterfaceGateway.event_bundle",
    },
    "repurchase": {
        "label": "回购",
        "fields": P1_EVENT_FIELDS["repurchase"],
        "pit": "announcement date is the table ts; p1_events lags one trading day",
        "source": "repurchase via DataInterfaceGateway.event_bundle",
    },
    "holder_number": {
        "label": "股东户数",
        "fields": P1_EVENT_FIELDS["stk_holdernumber"],
        "pit": "announcement date is the table ts; p1_events lags one trading day",
        "source": "stk_holdernumber via DataInterfaceGateway.event_bundle",
    },
}


ML_FEATURE_GROUPS: dict[str, list[str]] = {
    "base_market": ["close", "open", "high", "low", "volume", "amount", "turnover", "returns", "float_mv", "total_mv"],
    "valuation": ["pb_mrq", "pe_ttm", "ps_ttm"],
    "margin": ["margin_rzye", "margin_rqye", "margin_rzmre", "margin_rqyl"],
    "moneyflow": ["mf_buy_sm", "mf_buy_lg", "mf_buy_elg", "mf_sell_sm", "mf_sell_lg", "mf_sell_elg", "mf_net_amount"],
    "financial_quality": ["fin_net_income", "fin_operating_cash_flow", "fin_total_assets", "fin_total_equity", "fin_total_liabilities", "fin_revenue", "fin_gross_profit", "fin_research_and_development_expense", "fin_basic_eps"],
    "p1_events": ["block_amount", "block_vol", "block_vwap", "block_count", "top_amount", "top_l_buy", "top_l_sell", "top_net_amount", "top_count", "rep_amount", "rep_vol", "rep_count", "holder_num", "holder_chg", "holder_days_since"],
}


def ml_feature_catalog() -> dict[str, Any]:
    return {
        "schema": "factor_lab.alpha.ml_feature_catalog.v1",
        "groups": ML_FEATURE_GROUPS,
        "expanded_fields": sorted({field for group, fields in ML_FEATURE_GROUPS.items() if group != "base_market" for field in fields}),
        "guidance": "Prefer economically motivated base + expanded-axis features; verify coverage and PIT before use. Do not auto-inject features.",
    }


def all_axes() -> dict[str, dict[str, Any]]:
    return {**P0_AXES, **P1_AXES}


def catalog() -> dict[str, Any]:
    return {
        "schema": "factor_lab.alpha.data_axes.v1",
        "p0": P0_AXES,
        "p1": P1_AXES,
        "fields": {
            "p0": sorted({field for axis in P0_AXES.values() for field in axis["fields"]}),
            "p1": sorted({field for axis in P1_AXES.values() for field in axis["fields"]}),
        },
        "ml": ml_feature_catalog(),
    }
