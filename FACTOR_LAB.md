# Factor Lab

Portable Alpha research workspace. A new DSH session should not need prior
chat history.

## Start

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab alpha team verify
python -m factor_lab alpha memory
```

Research commands are under `python -m factor_lab alpha ...`.

The binding coding contract is `factor_lab/alpha/ENGINEERING_CONTRACT.md`.

## DSH team

The agent runtime is DeepSeek Harness; the repository only carries the portable
roster and contracts.

```text
factor_lab/alpha/team/roles.json
  pathfinder
  web-gateway
  ml-planner
  factor-builder
  reader
```

Each agent has one current behavior directory under
`factor_lab/alpha/team/behaviors/<agent>/`; behavior history lives in Git only.

New session / new environment:

1. `python -m factor_lab doctor`
2. `python -m factor_lab alpha team plan`
3. In DSH, `list_agents`; spawn every missing member with `spawn_teammate`
   using the plan prompts.
4. `list_agents` again; verify all five teammates are running or inactive.
5. `python -m factor_lab alpha team verify`
6. Start `alpha campaign` or an L0 cycle.

No previous session or absolute path is required.

## Engine

```text
PathFinder / Web Gateway / ML Planner / FactorBuilder / Reader
  → Factor Library
  → Runner
  → Research Runtime
  → Falsifier
  → Edge Card
  → Memory
  → Campaign
  → long-only deployability / allocator
```

Five model-facing roles only: PathFinder, Web Gateway, ML Planner,
FactorBuilder, Reader. Runner, Research Runtime, Evaluator, Supervisor,
Falsifier, Registrar, Allocator and ML Runtime are deterministic or tool
services.

## Factor Library

Versioned artifacts live under:

```text
output/factor_lab/factors/generated/<family>/<factor_id>/v1/
  factor.dsl | factor.py
  spec.json
  test_factor.py
  manifest.json
```

Versions are immutable. Edge Cards reference `factor_id`, `version`, and
`code_hash`.

## Edge Cards and memory

```text
output/factor_lab/alpha/memory/
  attempts.jsonl
  edge_cards.jsonl
  failures.jsonl
  discoveries.jsonl
  summary.json
```

`search` reads known expressions before generating candidates, records every
attempt, writes top-candidate Edge Cards and finalizes the summary. `confirm`
writes holdout cards.

## Commands

```powershell
python -m factor_lab alpha validate --expression "rank(ts_corr(rank(close), rank(volume), 20))"
python -m factor_lab alpha eval --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31 --expression "rank(close)"
python -m factor_lab alpha benchmark --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31
python -m factor_lab alpha holdout --end 2026-11-01 --save
python -m factor_lab alpha search --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31 --budget 80
python -m factor_lab alpha confirm --codes output/factor_lab/hypotheses/pool200.txt --search output/factor_lab/alpha/search/ALPHA-....json
python -m factor_lab alpha memory
python -m factor_lab alpha library
python -m factor_lab alpha campaign --objective "研究应计" --max-rounds 5
python -m factor_lab alpha ml --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31 --features close,volume,amount
python -m factor_lab alpha discovery
python -m factor_lab alpha deploy --codes output/factor_lab/hypotheses/pool200.txt --start 2022-10-01 --end 2025-10-31 --expression "rank(close)"
python -m factor_lab alpha allocate
python -m factor_lab alpha cycle --factor-artifact <factor-dir-or-id> --campaign-id CAMP-...
python -m factor_lab alpha roles
```

## Data boundary

Alpha uses:

```text
factor_lab.runtime.data_interface_adapter.DataInterfaceGateway
factor_lab.runtime.panel_builder.build_factor_panel
data_interface services
```

Do not add direct QuestDB/SQL access to Alpha. Data without PIT is not admitted.

## Research rules

- Search only on the development window; the holdout policy lives in
  `factor_lab/alpha/policy/holdout.json`, not in `output/`, and is locked.
- Research evidence uses long-short spreads; deployment is long-only.
- Every label-touching candidate is trial-accounted.
- Red Team failure cannot become a candidate.
- No validated Edge Card means 100% broad-index baseline.
- Research modes: `explore`, `validate`, `deploy`. Exploration is allowed to
  be non-deployable, but still trial-accounted.
- Campaigns enforce max rounds, budget, duplicate stops and stop decisions.
- ML is non-deep-learning only; purged walk-forward with embargo is required.
- Web Gateway discoveries require cited sources; raw HTML is not passed to
  PathFinder.

## State

```text
output/factor_lab/
  alpha/          memory, search, confirm, campaigns, ml
  factors/        versioned runtime factor artifacts
  discovery/      Web Gateway artifacts
  hypotheses/     code pools
```

All mutable state is under `output/factor_lab`. Generated factor artifacts are
runtime research data, not portable design assets.
