# Factor Lab

Portable research workspace. A new DSH session should not need any prior chat
history.

## Start

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab campaign start --objective "研究因子"
```

## Architecture

- PathFinder → FactorBuilder / ML Engine → Runner → Evaluator
- Runner is an LLM execution role; it fetches data **only through `data_interface`** and loads the Research Adapter.
  - `python -m factor_lab runner --data-bundle` copies the data-construction part of research `_resolve_panel` (same loader for now) and passes `panel=` into the adapter; ordinary factor tests keep the legacy path.
  - `python -m factor_lab runner --data-bundle --codes ...` uses the direct data_interface OHLC panel builder.
- FactorBuilder writes versioned generated factors under `factor_lab/factors/generated/`.
  - Every factor artifact must carry `factor_id` (plus version/code hash); the panel builder and Runner use it to select the correct data/contract.
- ML Engine is non-Deep-Learning only.
- Supervisor controls rounds, budget, readiness feedback, and safe agent reclamation.
- Hard limits: ≤5 campaign rounds; ¥3,000/month LLM; 150GB Lab quota; human G4 for promotion.
- LLM pricing: input ¥0.04 / 1M tokens, output ¥8.00 / 1M tokens.

## Discovery

```powershell
python -m factor_lab discovery queries --question "研究问题"
# DSH Gateway child may use web_search directly (tools inherited from preset)
# or the main session may supply raw results
# DSH LLM Gateway: factor_lab/workflows/web_gateway.js
python -m factor_lab discovery ingest --result gateway_result.json
```

## State

All mutable state is under `output/factor_lab`:

```text
control/       directive, budget, heartbeat, cycle, agents
ledger/        trial ledger
experiments/   immutable experiment artifacts
campaigns/     multi-round campaign traces
factors/       generated Lab factors (versioned)
dashboard/     read-only dashboard
```

If data credentials/configuration are absent, `doctor` must fail with an
actionable blocker. Do not bypass `data_interface`.
