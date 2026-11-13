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
            "fin_net_income_attributable_to_parent",
            "fin_operating_cash_flow",
            "fin_total_assets",
            "fin_total_equity",
            "fin_total_liabilities",
            "fin_revenue",
            "fin_gross_profit",
            "fin_operating_profit",
            "fin_ebit",
            "fin_ebitda",
            "fin_free_cash_flow",
            "fin_research_and_development_expense",
            "fin_selling_expense",
            "fin_general_and_admin_expense",
            "fin_finance_expense",
            "fin_basic_eps",
            "fin_diluted_eps",
        ],
        "pit": "financial statements aligned to publish_date by data_interface",
        "source": "financial statements via DataInterfaceGateway.financial_bundle",
    },
    "valuation": {
        "label": "估值剖面",
        "fields": [
            "pb_mrq",
            "pe_ttm",
            "ps_ttm",
            "pcf_ttm",
            "pfcf",
            "ev_ebit",
            "ev_ebitda",
            "ev_sales",
            "ev_gross_profit",
            "ev_fcf",
            "p_nwc",
            "earnings_yield",
            "dividend_yield",
            "enterprise_value",
            "market_cap_change_pct",
            "total_mv",
            "float_mv",
        ],
        "pit": "daily valuation, PIT published",
        "source": "fin_valuation_daily via DataInterfaceGateway.valuation_bundle",
    },
    "share_structure": {
        "label": "股本结构",
        "fields": ["total_share", "float_share"],
        "pit": "daily share-count field exposed by the valuation panel; PIT same-date",
        "source": "fin_valuation_daily via DataInterfaceGateway.valuation_bundle",
    },
    "security_master": {
        "label": "证券主数据",
        "fields": ["list_days"],
        "pit": "list_date is static; list_days is an as-of scalar derived from it",
        "source": "stock_basic_info via DataInterfaceGateway.security_master",
    },
    "index_membership": {
        "label": "指数成分/权重",
        "fields": [
            "index_weight_000300",
            "index_weight_000905",
            "index_weight_000852",
            "index_weight_000001",
            "index_weight_000016",
            "index_weight_399006",
        ],
        "pit": "index weight snapshot, forward-filled to the panel",
        "source": "index_weight via DataInterfaceGateway.index_weight_bundle",
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


BASE_MARKET_FIELDS = ["close", "open", "high", "low", "volume", "amount", "turnover", "returns", "float_mv", "total_mv"]

# One source of truth: ML feature groups are generated from the same axes that
# PathFinder/FactorBuilder see.  Do not maintain a second field list.
ML_FEATURE_GROUPS: dict[str, list[str]] = {"base_market": list(BASE_MARKET_FIELDS)}
ML_FEATURE_GROUPS.update({name: list(axis["fields"]) for name, axis in P0_AXES.items()})
ML_FEATURE_GROUPS["p1_events"] = sorted(
    {field for axis in P1_AXES.values() for field in axis["fields"]}
)


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
