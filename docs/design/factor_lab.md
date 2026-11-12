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

All factor-evaluation algorithms live in the IO-free ``factor_metrics`` core,
which carries the production report's calibrated implementations.  The Alpha
gate and the production report are thin products over that core; neither keeps
a private formula copy.  The binding reference is
[docs/design/factor_evaluation_contract.md](factor_evaluation_contract.md).

- Profiles: ``alpha-gate-v1`` (non-overlapping sampling, plain t, PIT universe)
  and ``research-report-v1`` (daily IC + Newey-West); every result records its
  ``metrics_spec`` identity.
- Signal evidence: long-short IC / spread.
- Deployment: long-only next-open excess after tax and cost.
- Universe: point-in-time eligibility from trailing data only (delisted,
  suspended and not-yet-seasoned names are excluded); policy in
  `factor_lab/alpha/policy/universe.json`, applied by every research command
  unless `--no-pit-universe` is passed.
- Verdicts are tri-state (`pass` / `fail` / `insufficient_evidence`); null
  metrics never become pass or fail.
- Research protocol: model-facing work is delegated to the five DSH roles
  (shared tasks + messages, outputs recorded under the campaign `team/`
  directory); the Lead runs deterministic services only. A Lead-only campaign
  is invalid; mechanical re-runs are labeled `protocol: deterministic-maintenance`.
- Gates: net spread, IC, turnover, holdout. Capacity is informational only and
  never vetoes a candidate.
- Edge Card statuses: research, signal_validated, validated, active, parked,
  not_deployable, retired, killed.
- No validated edge means the allocator stays at the broad-index baseline.

## Campaigns

`CampaignController` writes round records, traces, stop decisions, and enforces
max rounds, budget, duplicate stops and researcher stop/pause.

## ML

The ML branch is non-deep-learning. It uses purged walk-forward splits with
embargo at least as long as the target horizon, a model card, and an ML Monitor.
Its daily rank-IC metric depends one-way on the shared
`factor_metrics.rank_ic_frame`; CV, model fitting and the monitor remain
runtime-owned. Its results carry `evaluation_spec` and are not comparable with
cross-sectional factor metrics. Artifacts live under `output/factor_lab/alpha/ml/`.

## Data

Alpha reads panels through `DataInterfaceGateway` and `build_factor_panel`.
Research modules never access QuestDB directly. All mutable state is under
`output/factor_lab`.
