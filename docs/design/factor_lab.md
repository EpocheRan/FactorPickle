# Alpha Research Engine

`factor_lab/alpha` is the current Factor Lab engine. It is a deterministic
research loop with five model-facing roles and a set of non-LLM services.

## Roles

```text
PathFinder
Web Gateway
ML Planner
FactorBuilder
Reader
```

Runner, Research Runtime, Evaluator, Supervisor, Falsifier, Registrar,
Allocator and ML Runtime are deterministic/tool layers. They cannot rewrite
Gate verdicts.

## Loop

```text
PathFinder / Web Gateway
  → FactorBuilder
  → Factor Library
  → Runner
  → Research Runtime
  → Falsifier
  → Edge Card
  → Memory
  → Campaign
  → long-only deployability
  → Allocator
```

## Memory

```text
output/factor_lab/alpha/memory/
  attempts.jsonl
  edge_cards.jsonl
  failures.jsonl
  discoveries.jsonl
  summary.json
```

Search reads known expressions before generation, records every attempt, and
writes top-candidate Edge Cards. Search is therefore closed-loop, not a
one-shot report generator.

## Factor Library

```text
output/factor_lab/factors/generated/<family>/<factor_id>/v1/
  factor.dsl | factor.py
  spec.json
  test_factor.py
  manifest.json
```

Old versions are immutable. Edge Cards reference `factor_id`, `version` and
`code_hash`.

## Evaluation

- Signal evidence: long-short IC / spread.
- Deployment: long-only next-open excess after tax and cost.
- Gates: alpha_per_turn, DSR, residual correlation, capacity, holdout.
- Edge Card statuses: research, signal_validated, validated, active, parked,
  not_deployable, retired, killed.
- No validated edge means the allocator stays at the broad-index baseline.

## Campaigns

`CampaignController` writes round records, traces, stop decisions, and enforces
max rounds, budget, duplicate stops and researcher stop/pause.

## ML

The ML branch is non-deep-learning. It uses purged walk-forward splits with
embargo at least as long as the target horizon, a model card, and an ML Monitor.
Artifacts live under `output/factor_lab/alpha/ml/`.

## Data

Alpha reads panels through `DataInterfaceGateway` and `build_factor_panel`.
Research modules never access QuestDB directly. All mutable state is under
`output/factor_lab`.
