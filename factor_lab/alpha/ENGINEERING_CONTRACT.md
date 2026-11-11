# Alpha Engineering Contract

This is the binding rulebook for coding agents working on `factor_lab/alpha`.
It describes the current system only. Git history is the only place for
superseded behavior.

## 1. Source of truth vs runtime output

```text
Repository = source of truth
output/    = disposable runtime artifacts
```

The following are source-of-truth and MUST live in the repository:

- code
- role roster
- behavior prompts
- behavior contracts
- eval suites
- holdout policy
- campaign policy
- model / CV policy
- portable team bootstrap

The following MUST live under `output/factor_lab`:

- Edge Cards
- memory attempts / failures / discoveries
- search / confirm results
- campaign round records and traces
- ML plans / bundles / results / model cards
- generated factor artifacts
- discovery maps

Deleting `output/factor_lab` MUST NOT make the engine unable to restart.
If it does, source-of-truth was placed in output.

## 2. Forbidden in output

Never write these under `output/`:

```text
agent roles
system prompts
behavior contracts
behavior eval suites
model configs
holdout policy
campaign policy
team registry
source-of-truth JSON/YAML
```

`output/factor_lab/alpha/team/traces` is forbidden.

## 3. Agents and DSH

DeepSeek Harness owns the agent runtime. This repository MUST NOT implement an
agent execution framework.

The five model-facing roles are:

```text
pathfinder
web-gateway
ml-planner
factor-builder
reader
```

Deterministic services:

```text
Runner
Research Runtime
Evaluator
Supervisor
Falsifier
Registrar
Allocator
ML Runtime
```

New session / new environment:

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab alpha team plan
python -m factor_lab alpha team verify
```

The DSH Lead then uses `list_agents`, `spawn_teammate`, and `list_agents`
again. The repository supplies the plan; DSH supplies the runtime.

## 4. Behavior is current-only

Each agent has exactly one current behavior directory:

```text
factor_lab/alpha/team/behaviors/<agent>/
  system.md
  contract.json
  evals.jsonl
```

There MUST NOT be:

```text
<agent>/v1/
<agent>/v2/
<agent>/current/
<agent>/candidate/
<agent>/changelog.md
<agent>/migration.md
<agent>/legacy.md
```

Behavior updates replace the current files in a branch. Canary comparison uses
a Git branch or worktree, not a second directory in the working tree.

`behavior_hash = sha256(system.md + contract.json + evals.jsonl)` may identify
the behavior in runtime evidence. It is an identity, not a version history.

## 5. Behavior update protocol

1. Edit `behaviors/<agent>/system.md`.
2. Update `contract.json` and `evals.jsonl`.
3. Run the current eval suite.
4. Use a branch/worktree for canary comparison.
5. Merge only after schema, boundary, cost and quality checks pass.
6. Do not write old behavior, old prompts, or migration notes anywhere in the
   repository.

The current agent must load only the current behavior files.

## 6. Module boundaries

The current package is:

```text
factor_lab/alpha/
factor_lab/core/
factor_lab/runtime/data_interface_adapter.py
factor_lab/runtime/panel_builder.py
```

Do not resurrect the removed Factor Lab research stack:

```text
factor_lab/research/
factor_lab/evaluation/
factor_lab/reporting/
factor_lab/team/
factor_lab/workflows/
factor_lab/templates/
```

Alpha reads data only through `DataInterfaceGateway` and
`build_factor_panel`. No direct QuestDB/SQL access.

## 7. Research invariants

- Search only on the development window.
- Locked holdout is never used for candidate generation.
- Every label-touching candidate is trial-accounted.
- Red Team failure cannot become a candidate.
- Research uses long-short signal evidence; deployment is long-only.
- Only validated/active Edge Cards may be allocated.
- No validated edge means 100% broad-index baseline.
- Research modes: `explore`, `validate`, `deploy`.
- ML is non-deep-learning; purged walk-forward with embargo at least as long as
  the target horizon.
- Web discovery requires cited sources and retrieval times.

## 8. Coding agent protocol

Before changing code:

1. Read `AGENTS.md`.
2. Read `FACTOR_LAB.md`.
3. Read this contract.
4. Inspect current code and tests; do not assume old module behavior.
5. Make the smallest change that keeps the current engine runnable.

Before claiming completion:

```powershell
python -m pytest -q
python -m factor_lab doctor
python -m factor_lab dry-run
```

Do not add historical narratives to READMEs or design docs. Do not write
business logic or config to `output/`.

## 9. Tests that protect this contract

The intended guard tests are:

- deleting `output/factor_lab` then `doctor` and `team plan` still work
- no config/behavior files exist under `output/`
- behavior directories contain only current files
- no imports of removed modules
- team plan contains exactly five roles
- full test suite passes

## 10. Definition of done

A change is done only when:

- code and tests pass
- current docs describe only the current state
- output remains disposable
- DSH team bootstrap still works
- no behavior history or config leaked into `output/`
