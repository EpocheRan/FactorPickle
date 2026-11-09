# Factor Lab

Portable research workspace. A new DSH session should not need prior chat
history.

## Start

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab campaign start --objective "研究因子"
```

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

## Rules

- One trial = hypothesis × pre-registered outcome.
- N is family-scoped; confirmation runs do not enter development N.
- Quarantine and ledger-repair gaps still count in N.
- `control` is human-owned; stop drains work, it does not kill running workers.
- Max 5 campaign rounds, 12 ML experiments, ¥60/campaign.
- LLM pricing: input ¥0.04 / 1M tokens, output ¥8.00 / 1M tokens.
- Lab quota 150GB: 100 output + 20 cache + 30 archive; 50GB free floor.
- Do not promote factors or change policy without human G4.

## State

All mutable state is under `output/factor_lab`:

```text
control/       directive, budget, heartbeat, cycle, agents
ledger/        trial ledger
experiments/   immutable experiment artifacts
campaigns/     campaign traces
factors/       generated Lab factors (versioned)
dashboard/     read-only dashboard
ml/            ML datasets and outputs
```

If data credentials/configuration are absent, `doctor` must fail with an
actionable blocker. Do not bypass `data_interface`.
