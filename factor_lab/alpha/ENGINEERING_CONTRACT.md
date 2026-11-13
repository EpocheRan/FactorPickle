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
- universe policy
- evaluation metric contract and profiles
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

### 3.0 Agent-team change control

Any optimization to agent-team logic is a source change, not a chat-only
decision. A change to routing, delegation, roles, campaign protocol, task-board
workflow, manifest requirements or audit rules must update the relevant
`roles.json`, behavior files, enforcement code (`campaign.py`,
`protocol_audit.py`, `preflight.py`), tests and current docs in the same change.
Conversation-only changes are not binding.

### 3.1 Campaign protocol (binding)

The portable protocol is `campaign_protocol` in `roles.json` and is emitted by
`python -m factor_lab alpha team plan` / session brief.

Every new campaign or research round MUST delegate model-facing work to the
five DSH teammates:

- **Required per campaign**: a PathFinder research question, a FactorBuilder
  artifact, a Web Gateway cited discovery map, and an ML Planner decision
  (pre-registered plan or explicit falsifiable skip reason).
- **Use when needed**: Reader (structured evidence extraction from cited
  sources). The Gateway step must record "no new external evidence" rather than
  silently skip.
- **Reader mandatory trigger**: Reader is required when the Web Gateway map has
  at least four cited sources, when the map sets `reader_required: true`, or
  when the map records unread/paywalled full-text sources. The Reader task must
  write `team/reader_evidence.json` with schema
  `factor_lab.alpha.reader_evidence.v1`; the policy lives in
  `roles.json` under `campaign_protocol.reader_policy`. Campaigns created before
  the policy `effective_from` are warning-only and are not retro-edited.
- **Enforcement**: new `alpha-campaign-v2` campaigns fail the strict campaign
  audit if the team manifest lacks completed web-gateway or ml-planner evidence,
  or if a Reader-required campaign lacks completed Reader evidence.
- **Delegation mechanics**: one shared task per delegated role on the task
  board, claimed and completed; `send_message` with a self-contained task
  (role boundaries, inputs, output contract); wait for outputs.
- **Recording**: task ids and teammate outputs live under
  `output/factor_lab/alpha/campaigns/<campaign_id>/team/`. Each campaign has a
  `team_manifest.json`; each round records `agents`, `team_tasks` and
  `protocol`. `alpha campaign-audit --campaign-id <id>` must pass with
  `strict: true` before a campaign is described as protocol-compliant.
- **No silent provenance gap**: a campaign whose rounds lack agent/task
  provenance is a process failure even if its factor evidence is reproducible.
  Do not retro-edit round files; record the audit defect and fix future rounds.
- **Lead boundary**: the Lead may execute deterministic services
  (cycle/search/eval/campaign recording/Allocator) only on teammate outputs.
  The Lead must not generate, rank or interpret candidates alone.
- **Violation**: a Lead-only campaign is a protocol violation and is invalid as
  research. Mechanical re-runs are allowed only when explicitly labeled
  `protocol: deterministic-maintenance` and must not generate new candidates.

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
factor_metrics/                     # IO-free, research-calibrated evaluation core
data_interface/panel.py             # neutral Panel data container
factor_lab/alpha/
factor_lab/core/
factor_lab/runtime/data_interface_adapter.py
factor_lab/runtime/panel_builder.py
research/report/                    # production factor report (thin product over the core)
```

Alpha evaluation algorithms must live in `factor_metrics`; neither
`factor_lab` nor `research` may fork a private formula implementation.

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

P1 event features are loaded through `DataInterfaceGateway.event_bundle`
(`data_interface.p1_events`); they are zero-filled event flows and PIT-lagged
one trading day. Financial statement fields are carried forward from their
PIT announcement dates before entering the daily panel. P0/P1 field coverage is
recorded under `output/factor_lab/alpha/data_axes/coverage.json`.

## 7. Research invariants

- Search only on the development window.
- Evaluation preserves pre-window history: `AlphaDataset.segment` keeps the requested evaluation window in `context`, but `evaluation_context()` carries the pre-segment history so `ts_mean`/`ts_std`/`ref` do not lose warmup at the OOS edge. The evaluator still slices results to `start`/`end`.
- Each campaign round records a first-class `evaluation_contract` with schema `factor_lab.alpha.evaluation_contract.v1`: `evaluation_start`, `evaluation_end`, `universe`, `size_screen`, `pool_mode`, `horizon`, actual `cost`, `n_eff`, `n_trials`, `hypothesis_count`, `pre_registration_hash`, frozen `ledger_snapshot_hash`, `price_basis`, `tradability`, and the forward-holdout state. `evaluation_universe` remains the mask provenance.
- The forward holdout is frozen in `policy/holdout_v2.json` starting `2026-11-02`; it is not evaluable until `confirmation_allowed` is explicitly changed. A round whose evaluation window reaches it is refused.
- Research prices use the point-in-time cumulative adjusted convention (`hfq`), not forward-adjusted `qfq`; raw OHLC is retained for execution/limit checks. The data manifest records both price bases.
- The family ledger is frozen once into `output/factor_lab/alpha/families/ledger_snapshot.json`; evaluations use the snapshot hash. Re-freezing is an explicit `refresh=True` action and changes the denominator identity.
- Every pre-registration declares its evaluation conditions, has at most 10 candidates, and every used family must be registered in `policy/families.json`. Freeze rejects `require_prereg=False` and Q overrides, and selects the latest recorded trial rather than the largest sample.
- Each campaign round records `evaluation_universe` with schema `factor_lab.alpha.evaluation_universe.v1`; `kind` is `full_panel` or `custom_mask` and the nested universe summary is the auditable PIT mask. The field is first-class, not a team-manifest workaround.
- P0 data axes are the single field registry used by PathFinder (pre-registration) and ML Planner: expanded `valuation` fields (`dividend_yield`, `earnings_yield`, `pcf_ttm`, `pfcf`, `ev_*`, `p_nwc`, ...), `share_structure` (`total_share`, `float_share`), `security_master.list_days`, and `index_membership.index_weight_*` are admitted once the panel provides them. Do not maintain a separate ML-only field list.
- ML features may be raw PIT fields or validated deterministic DSL feature expressions (for example `rank(div(fin_net_income, fin_total_assets))`, `ts_std(returns, 20)`, `div(ts_mean(abs(returns),20), ts_mean(amount,20))`). The runtime compiles expressions on the evaluation context, so long-lookback features keep their warmup; raw fields are never auto-injected.
- The ML result includes a deterministic long-only construction block built from OOS scores: top-quintile equal weight, next-open entry, actual A-share buy/sell fees. It is deployability evidence, not a promotion gate.
- Universe is point-in-time: eligibility uses trailing data only. Delisted,
  suspended and not-yet-seasoned names leave or stay out of the universe and
  are never forward-filled. The policy lives in
  `factor_lab/alpha/policy/universe.json`.
- Factor evaluation algorithms are shared through `factor_metrics`; every
  result records its `metrics_spec` profile/version. `factor_lab` must not
  import `research`, and `research` must not import `factor_lab`.
- Every new campaign or research round delegates model-facing work to the five
  DSH teammates and records their outputs; a Lead-only campaign is invalid.
- Instrument calibration (cheat / random / known probes) runs before a large
  search campaign; probes are calibration reports, not candidate trials.
- The forward holdout is frozen and unused; historical holdout uses remain recorded and never reopen.
- Holdout confirmation requires a passed freeze decision; freeze uses the
  program-wide FDR denominator (all pre-registered families on the same
  standardized pool/window), not a small family-only denominator.
- Verdicts are tri-state (`pass` / `fail` / `insufficient_evidence`); missing
  or non-finite metrics never become pass or fail. Capacity is informational
  only and never vetoes a candidate.
- Locked holdout is never used for candidate generation.
- Every label-touching candidate and ML experiment is trial-accounted; search/dev/ML/holdout events are mirrored to the append-only `output/factor_lab/alpha/trial_events.jsonl` sink.
- Freeze requires a verified frozen pre-registration containing the candidate expression and rejects Q overrides. Confirmation is refused once any holdout use is recorded (`holdout_uses.jsonl`).
- Pre-registration is frozen with a hash before evaluation; artifacts must match the frozen expressions exactly. Attempts record `family_id` and `pre_registered`.
- ML features are explicit and pre-registered; the runtime validates that expanded P0/P1 fields exist, but never auto-injects them. PathFinder must expose `ml_branch` and route to ML Planner after two ML-free campaigns or when expanded axes changed.
- Event features carry the disclosure/trade date already present as `ts`; the p1_events adapter applies exactly one trading-day lag. Do not apply a second arbitrary lag in factor expressions.
- Red Team failure cannot become a candidate.
- Research uses long-short signal evidence; deployment is long-only. Portfolio construction uses `policy/portfolio.json`; execution uses `policy/execution.json`. Neither layer may silently fabricate ST/new/delisting/suspension flags.
- Only validated/active Edge Cards may be allocated. Portfolio construction and paper execution are separate deterministic layers; a portfolio backtest is not a promotion, and missing ST/new/delisting/security-master data is an explicit execution gap.
- No validated edge means 100% broad-index baseline.
- Research modes: `explore`, `validate`, `deploy`.
- Alpha cost model: actual A-share fees (buy 2.44bps, sell 7.44bps including 5bps sell stamp duty) plus 800bps/year short borrow; the generic research `CostModel` is not the Alpha deployment assumption.
- ML targets use the same entry rule as the Gate; OOS admission uses an overlap-adjusted t in addition to the naive daily t. Model cards record raw and standardized coefficients. Distillation supports `projection` and `model_card` modes. The single-fold/single-vector protocol is binding when pre-registered in `factor_lab/alpha/policy/singlefold.json`: n_splits=1, cs_rank features/target, model_card export, report-only fidelity, OOS-test-only evaluation. A 0.95 gate is optional and must be pre-registered.
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
- team plan contains exactly five roles and a `campaign_protocol`
- every research campaign records teammate task ids and outputs (or is labeled
  `protocol: deterministic-maintenance`)
- `factor_lab` does not import `research` (and vice versa)
- the research report output matches the frozen characterization fixture
- `alpha probe` produces a cheat/random/known calibration report
- `alpha freeze` rejects candidates without program FDR; `alpha confirm` rejects without a passed freeze
- full test suite passes

## 10. Definition of done

A change is done only when:

- code and tests pass
- current docs describe only the current state
- output remains disposable
- DSH team bootstrap still works
- no behavior history or config leaked into `output/`
