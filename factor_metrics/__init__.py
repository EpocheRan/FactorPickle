"""factor_metrics — IO-free, research-calibrated factor evaluation core.

A-full contract
---------------
Every numerical algorithm in this package was extracted verbatim from
``research/report/metrics.py`` (and its diagnostics helpers), which is the
calibrated source of truth for the production factor report.  The two products
in this repository share these primitives:

- ``research.report.metrics`` (production factor report) calls them directly;
- ``factor_lab.alpha.evaluation`` / ``deployability`` / ``red_team`` assemble
  the Alpha gate on top of them with an explicit ``MetricsSpec`` profile.

Do not change numerical behaviour without updating the characterization
fixtures under ``tests/fixtures/metrics/`` and the evaluation contract doc.
"""

from __future__ import annotations

from .backend import (
    QUANTILE_POLARS_MAX_CELLS,
    Backend,
    engine_metadata,
    polars_available,
    requested_backend,
    resolve_quantile_backend,
)
from .capacity import _capacity
from .cost import _cost_sensitivity
from .evaluation import evaluate_factor, evaluate_long_only
from .filter import _filter_test, _filter_test_side
from .ic import _rank_ic_frame, _spearman_ic
from .incremental import _incremental
from .neutralize import (
    _as_industry_frame,
    _stratified_ic,
    neutralize_factor,
    neutralize_panel,
    neutralized_ic,
)
from .performance import (
    _annual_ic_return,
    _annual_returns,
    _ls_stats,
    _period_performance,
    _performance,
)
from .quantile import QuantileResult, _quantile_nav
from .regime import _decay_note, _regime_table
from .result import FactorMetrics
from .returns import forward_returns
from .spec import (
    ALPHA_GATE_SPEC,
    RESEARCH_SPEC,
    CapacitySpec,
    CostModel,
    MetricsSpec,
)
from .stats import (
    TRADING_DAYS,
    _corr_p,
    _zscore,
    block_bootstrap_ci,
    group_monotonicity,
    independent_t,
    newey_west_t,
    spearman,
    spearman_p,
    spread_stats,
)
from .tradability import (
    _delayed_exit,
    _limit_matrix,
    _sell_side_summary,
    _tradeability,
    board_limit_pct,
    tradeable_mask,
)
from .winsor import (
    _trimmed_group_returns,
    _winsor_group_returns,
    _winsorize,
    _winsorize_frame,
)

#: Public aliases (without the historical underscore prefix).
rank_ic_frame = _rank_ic_frame
spearman_ic = _spearman_ic
quantile_nav = _quantile_nav
winsorize = _winsorize
winsorize_frame = _winsorize_frame
winsor_group_returns = _winsor_group_returns
trimmed_group_returns = _trimmed_group_returns
capacity = _capacity
cost_sensitivity = _cost_sensitivity
filter_test = _filter_test
filter_test_side = _filter_test_side
incremental = _incremental
regime_table = _regime_table
decay_note = _decay_note
performance = _performance
period_performance = _period_performance
annual_returns = _annual_returns
annual_ic_return = _annual_ic_return
ls_stats = _ls_stats
tradeability = _tradeability
sell_side_summary = _sell_side_summary
delayed_exit = _delayed_exit
limit_matrix = _limit_matrix
neutralize = neutralize_panel
stratified_ic = _stratified_ic
industry_frame = _as_industry_frame

__all__ = [
    "ALPHA_GATE_SPEC",
    "RESEARCH_SPEC",
    "Backend",
    "CapacitySpec",
    "CostModel",
    "FactorMetrics",
    "MetricsSpec",
    "QUANTILE_POLARS_MAX_CELLS",
    "QuantileResult",
    "TRADING_DAYS",
    "_annual_ic_return",
    "_annual_returns",
    "_as_industry_frame",
    "_capacity",
    "_corr_p",
    "_cost_sensitivity",
    "_decay_note",
    "_delayed_exit",
    "_filter_test",
    "_filter_test_side",
    "_incremental",
    "_limit_matrix",
    "_ls_stats",
    "_period_performance",
    "_performance",
    "_quantile_nav",
    "_rank_ic_frame",
    "_regime_table",
    "_sell_side_summary",
    "_spearman_ic",
    "_stratified_ic",
    "_tradeability",
    "_trimmed_group_returns",
    "_winsor_group_returns",
    "_winsorize",
    "_winsorize_frame",
    "_zscore",
    "annual_ic_return",
    "annual_returns",
    "block_bootstrap_ci",
    "board_limit_pct",
    "capacity",
    "cost_sensitivity",
    "decay_note",
    "delayed_exit",
    "engine_metadata",
    "evaluate_factor",
    "evaluate_long_only",
    "filter_test",
    "filter_test_side",
    "forward_returns",
    "group_monotonicity",
    "independent_t",
    "incremental",
    "industry_frame",
    "limit_matrix",
    "ls_stats",
    "neutralize",
    "neutralize_factor",
    "neutralize_panel",
    "neutralized_ic",
    "newey_west_t",
    "performance",
    "period_performance",
    "polars_available",
    "quantile_nav",
    "rank_ic_frame",
    "regime_table",
    "requested_backend",
    "resolve_quantile_backend",
    "sell_side_summary",
    "spearman",
    "spearman_ic",
    "spearman_p",
    "spread_stats",
    "stratified_ic",
    "tradeability",
    "tradeable_mask",
    "trimmed_group_returns",
    "winsor_group_returns",
    "winsorize",
    "winsorize_frame",
]
