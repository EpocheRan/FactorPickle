# Factor Lab

Portable research workspace. A new DSH session should not need prior chat
history.

## Start

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab campaign start --objective "研究因子"
```

Before starting a research campaign, create the full durable team from
`factor_lab/team/roles.json`: `pathfinder`, `web-gateway`, `factor-builder`,
`runner`, `evaluator`, `ml-planner`, `ml-monitor`, `supervisor`.

Create all roles first, verify the roster, then create/assign research tasks.
Do not create a blocked task DAG with only one teammate online.

## Architecture

```text
PathFinder
  → Web Gateway / FactorBuilder / ML Engine
  → Runner
  → Research Adapter
  → Evaluator / Supervisor
```

- PathFinder chooses research direction from history, data coverage and discovery.
- Web Gateway generates search questions and retains cited results.
- FactorBuilder writes versioned factors under `factor_lab/factors/generated/`.
- Runner is an LLM execution role; it selects codes, builds a Panel through
  `DataInterfaceGateway`, and submits one pre-registered experiment.
- Research Module computes factor values and `FactorReport`.
- ML Engine is non-Deep-Learning only.
- Supervisor owns campaign rounds, budgets, readiness blockers and safe reclamation.

## Data and evaluation boundary

```text
DataInterfaceGateway
  → market / flow / margin / valuation / industry / regime / ML bundles
  → build_factor_panel
  → Panel + data_manifest
  → research.report.runner.generate
  → research.report.metrics.build_report
```

Runner never computes a factor matrix and never accesses QuestDB directly.
Factor artifacts must carry `factor_id`, category, version, code hash and
`data_contract`; Runner validates the factor id and records the artifact in
`data_manifest.json`.

Research Module metrics use a pandas/Polars backend:

- `REPORT_METRICS_BACKEND=auto` (default)
- Rank IC stays on pandas
- Quantile NAV uses Polars for moderate cross-sections and pandas for the
  full market
- `FactorReport.metrics_engine` records the actual engines

## Discovery

```powershell
python -m factor_lab discovery queries --question "研究问题"
# DSH Gateway child may use web_search directly (tools inherited from preset)
# or the main session may supply raw results
# DSH LLM Gateway: factor_lab/workflows/web_gateway.js
python -m factor_lab discovery ingest --result gateway_result.json
```

## Alpha DSL

```powershell
python -m factor_lab alpha validate --expression "rank(ts_corr(rank(close), rank(volume), 20))"
python -m factor_lab alpha holdout --end 2026-11-01 --save
python -m factor_lab alpha benchmark --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31
python -m factor_lab alpha search --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31 --budget 80
python -m factor_lab alpha confirm --codes output/factor_lab/hypotheses/pool200.txt --search output/factor_lab/alpha/search/ALPHA-....json
python -m factor_lab alpha trials
```

Search runs only on the development window. The default holdout is the last
12 months; `holdout.json` is locked and cannot be silently overwritten.

## Rules

- One trial = hypothesis × pre-registered outcome.
- N is family-scoped; confirmation runs do not enter development N.
- Quarantine and ledger-repair gaps still count in N.
- `control` is human-owned; stop drains work, it does not kill running workers.
- Max 5 campaign rounds, 12 ML experiments, ¥60/campaign.
- LLM pricing: input ¥0.04 / 1M tokens, output ¥8.00 / 1M tokens.
- Lab quota 150GB: 100 output + 20 cache + 30 archive; 50GB free floor.
- Do not promote factors or change policy without human G4.
- Generated factor code/spec/tests/manifests are runtime research data under
  `output/factor_lab/factors/generated/`. Do not copy, mirror, or move them into
  the repository; they are not portable design assets.
- Portable design is limited to code, contracts, `factor_lab/team/roles.json`,
  and `factor_lab/sources/catalog.json`. Never duplicate runtime state into Git.
- The data axis is chosen by PathFinder from campaign-scoped `data_capabilities`.
  Do not pre-narrow a round to `ohlcv_1d` or prohibit financial/moneyflow/margin.
  Unverified services require a readiness probe.

## State

All mutable state is under `output/factor_lab`:

```text
control/       directive, budget, heartbeat, cycle, agents
ledger/        trial ledger
experiments/   immutable experiment artifacts
campaigns/     campaign traces
factors/       generated Lab factors (versioned runtime data)
dashboard/     read-only dashboard
ml/            ML datasets and outputs
```

`factor_lab/team/` and `factor_lab/sources/` are tracked design/config inputs,
not runtime state.

If data credentials/configuration are absent, `doctor` must fail with an
actionable blocker. Do not bypass `data_interface`.
