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
- FactorBuilder writes versioned generated factors under `factor_lab/factors/generated/`.
- ML Engine is non-Deep-Learning only.
- Supervisor controls rounds, budget, readiness feedback, and safe agent reclamation.
- Hard limits: ≤5 campaign rounds; ¥3,000/month LLM; 150GB Lab quota; human G4 for promotion.

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
