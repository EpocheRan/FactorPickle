# Factor Evaluation Contract (current implementation)

This document describes how factor evaluation works in the current code base.
It is the single reference for metric semantics shared by the Alpha research
engine and the production factor report.

## 1. Architecture

```text
factor_metrics/                 IO-free evaluation core (the calibrated algorithms)
  stats.py        newey_west_t / independent_t / block_bootstrap_ci / spearman / spread_stats
  returns.py      forward_returns (T+1 open entry, T+h close exit)
  ic.py           rank IC (cross-sectional Spearman)
  winsor.py       winsorize / winsorised group returns / trimmed group returns
  tradability.py  limit matrices, tradeable mask, delayed exit, sell-side summary
  quantile.py     group NAV engine, turnover and cost application
  neutralize.py   size/industry residual IC, stratified IC
  performance.py  period performance, long-short stats
  regime.py       regime slicing, decay note
  capacity.py     ADV-based capacity estimate
  filter.py       filter-value tests
  incremental.py  peer correlation / residual IC incrementality
  cost.py         cost sensitivity table
  backend.py      pandas/polars backend selection
  backends/polars.py  optional Polars implementations
  spec.py         MetricsSpec, CostModel, CapacitySpec, profiles
  result.py       FactorMetrics (payload + spec identity)
  evaluation.py   cross-sectional / long-only evaluation assembled from the primitives

data_interface/panel.py         neutral Panel dataclass (shared data container)

research/report/metrics.py      report orchestration; re-exports the core primitives
research/report/diagnostics.py  report assertions; re-exports the core statistics
research/report/metrics_polars.py / metrics_backend.py   compatibility shims

factor_lab/alpha/evaluation.py       Alpha gate adapter (MetricsSpec profile)
factor_lab/alpha/deployability.py    long-only adapter (core long-only stats)
factor_lab/alpha/red_team.py         falsifier; uses the core rank IC
```

Dependency rules, enforced by `tests/test_architecture_boundaries.py`:

- `factor_metrics` imports only the standard library, numpy/pandas and the
  optional polars backend.
- `factor_lab` does not import `research`.
- `research` does not import `factor_lab`.
- `data_interface/panel.py` is a pure data container.

## 2. Canonical semantics

All formulas below are the production factor report's calibrated
implementations (`research.report.metrics` re-exports them from
`factor_metrics`; there is exactly one implementation).

| Item | Semantics |
|---|---|
| Forward return | `close[t+h] / open[t+1] - 1` (T+1 open entry, T+h close exit) |
| Rank IC | Per-date cross-sectional Spearman between factor and forward return; minimum names configurable (`min_ic_names`) |
| t statistic | Newey-West (lag = h-1) for daily overlapping series; `independent_t` for non-overlapping subsamples |
| Group returns | Equal-weight quantile groups; membership from `rank(method="first")` + `qcut`; minimum `groups * 5` valid names |
| Turnover | One-way membership replacement: `|current - previous| / |current|` per group per rebalance |
| Cost | `CostModel`: `one_way_turnover × bps × multiplier`; `bps_meaning="round_trip"` → multiplier 1, `"per_side"` → multiplier 2 (see §2.1) |
| NAV | Non-overlapping rebalance; daily close-to-close compounding inside a period (entry day uses open), daily returns clipped to ±30% |
| Winsorised spread | `winsorized_factor_net_spread`: cross-sectional 1%/99% winsorisation of the factor, then the group engine is re-run |
| Trimmed spread | Experimental only: `extras.trimmed_net_spread` with `trimmed_net_spread_experimental=True`; never used by a gate, decision, or Edge Card |
| Capacity | `capacity_multiple` = research ADV estimate / target AUM. **Informational**: reported as a metric/warning, never a pass/fail gate |
| Annualisation | `TRADING_DAYS = 242` |
| Coverage | Factor-valid cells / panel cells; when a PIT universe is supplied, factor-valid eligible cells / eligible cells |

## 2.1 Cost semantics proof

Notation: for an equal-weight group, one-way turnover `t` is the fraction of
the book replaced at a rebalance (`|current - previous| / |current|`).

- A replacement of fraction `t` requires buying `t` notional (new names) and
  selling `t` notional (leavers): total traded notional is `2t`.
- If `bps` is a **round-trip fee** (buy+sell cost per unit of one-way turnover),
  the round-trip cost of the replacement is `t × bps`. Equivalently, with
  per-side fee `bps/2`, cost is `2t × (bps/2) = t × bps`.
- If `bps` is a **per-side fee** (charged on each side of the trade), the cost
  is `2t × bps`.

The production report's `_quantile_nav` documents `cost_bps` as the
**单腿往返费率（买+卖）**, i.e. the round-trip interpretation:
`cost = (turnover_Q5 + turnover_Q1) × bps`. `tests/test_factor_metrics_core.py`
proves this against an independent buy/sell accounting simulation.

The Alpha gate profile declares `CostModel(bps=20, bps_meaning="per_side")`
(round-trip cost = `2 × one-way turnover × bps`), matching the historical Alpha
gate's economic requirement that 20 bps is charged on each side; the two
products differ only through this explicit profile field, never through a
forked formula.

## 3. Profiles (audit identity)

| field | `alpha-gate-v1` | `research-report-v1` |
|---|---|---|
| entry | next_open | next_open |
| sampling | non_overlapping | daily |
| groups | 5 | 5 |
| min_ic_names | 10 | 20 |
| t_method | plain (`independent_t`, step=1) | newey_west (lag = h-1) |
| winsorize | 1% / 99% | 1% / 99% |
| trim | 1% / 99% | 1% / 99% |
| cost | two-leg, 20 bps, `bps_meaning=per_side` | two-leg, 20 bps, `bps_meaning=round_trip` |
| capacity | research ADV / 50m target (informational) | research ADV |
| universe denominator | PIT universe | report pool |

Every Alpha payload carries `metrics_spec` (profile dict + `config_hash`), and
every `FactorReport` carries `metrics_spec`. Results computed under different
specs are not directly comparable.

### 3.1 Tri-state evidence and gate policy

Alpha verdicts are tri-state: `pass` / `fail` / `insufficient_evidence`.
Missing or non-finite `net_spread`, `ic_mean`, `turnover`, or too few
observations map to `insufficient_evidence`; they never become `pass` or
`fail`. In the deterministic pipeline this maps to Edge Card status
`research` / `killed` / `parked` respectively, and holdout confirmation uses
the same tri-state (`evidence_status`). Holdout confirmation also attaches
`evidence_warnings` from the same policy.

Sample size uses **two independent levels** in
`factor_lab/alpha/policy/gates.json`:

- `min_dates_insufficient`: below this → `insufficient_evidence`;
- `min_dates_warning`: below this (but at/above the floor) → verdict allowed
  with a "sample size below preferred" warning.

The two fields are calibrated independently (invariant:
`min_dates_warning >= min_dates_insufficient`); `min_coverage` is also in the
same policy file.

Capacity is **not** part of the pass/fail gate: it is reported and surfaced as
an evaluation warning/caveat only.

## 4. Product boundaries

- **Alpha** (`factor_lab.alpha`): non-overlapping rebalance schedule, PIT
  universe mask, deterministic gate/falsifier, Edge Cards, trial accounting.
  It selects a `MetricsSpec` profile and never implements its own formulas.
- **Research report** (`research.report`): daily IC with Newey-West, data
  cleaning/tradability preprocessing, neutralised/stratified IC, rating and
  HTML rendering. It calls the same primitives; report-only layers stay in
  `research.report` (`data_quality`, `diagnostics` assertions, `rating`,
  `render`).
- **ML branch** (`factor_lab.alpha.ml_branch`): separate deterministic ML
  runtime (purged walk-forward CV, embargo, ridge fitting, ML monitor). Its
  daily rank-IC metric is imported from the shared core
  (`factor_metrics.rank_ic_frame`) as a **one-way dependency**
  (`factor_lab -> factor_metrics`); the runtime keeps its own CV/model logic.
  Its result/model card carry `evaluation_spec` (`ml-runtime-v1`,
  `rank_ic_source: factor_metrics.rank_ic_frame`,
  `comparable_with_cross_section_core: false`) and must not be put in the same
  comparison table as cross-sectional factor metrics.

## 5. Tests that protect this contract

| test | purpose |
|---|---|
| `tests/test_factor_metrics_core.py` | research report output equals the frozen baseline; Alpha payload equals the A-full golden; primitive conventions and properties |
| `tests/test_factor_metrics_parity.py` | report re-exports the same callables; matched-spec Alpha IC equals the report's rank IC; profile differences are explicit |
| `tests/test_architecture_boundaries.py` | import-direction rules above |
| `tests/test_alpha_universe.py` | point-in-time universe behaviour |
| `tests/test_research_report.py` / `test_research_metrics_polars.py` | existing report regressions (metrics, polars parity) |

Characterization fixtures: `tests/fixtures/metrics/research_baseline.json` and
`tests/fixtures/metrics/alpha_golden.json`. They are only regenerated together
with an explicit semantic change (and a spec version bump), never casually.

## 6. Extension rules

1. A new metric or formula belongs in `factor_metrics` and must be covered by
   the characterization fixtures.
2. A product-specific threshold or convention is a `MetricsSpec` field, not a
   forked implementation.
3. Do not import `research` from `factor_lab` (or the reverse).
4. Report-only logic (cleaning, rating, HTML, diagnostics assertions) stays in
   `research.report`.
