# Factor Lab (P0 / Phase 0)

`factor_lab` is an additive experiment harness for FinBridge. It reads the
existing research pipeline and writes structured artifacts under
`output/factor_lab/`; it does not edit production modules, the factor registry,
or QuestDB.

Phase 0 adds the runtime foundation for one autonomous cycle: HLC ledger,
family-scoped N, atomic experiment IDs, heartbeat/cycle split, process-pool
workers, drain stop, R4 disk guard, single-writer progress, and CORE quarantine.
It is still one-shot; the 24/7 scheduler and LLM agent layer are Phase 1.

## Commands

```powershell
python -m factor_lab init
python -m factor_lab pilot
python -m factor_lab cycle --limit 4 --heartbeat-interval 2   # Phase 0 smoke
python -m factor_lab phase1 --cycles 4 --limit 4 --heartbeat-interval 2
python -m factor_lab phase1-agent --plan output/factor_lab/phase1/agent_plan.json
python -m factor_lab phase1-director --memo output/factor_lab/phase1/agent_memo.json
python -m factor_lab mine --limit 23
python -m factor_lab dashboard
python -m factor_lab eval-adapter
python -m factor_lab directive show
python -m factor_lab supervisor status
python -m factor_lab control show
python -m factor_lab control pause --reason "review"
python -m factor_lab control resume
python -m factor_lab maintenance inventory
python -m factor_lab maintenance clean          # dry run
python -m factor_lab maintenance clean --apply  # move to archive/trash
```

`--heartbeat-interval 2` is only for the smoke cycle; production default is
120 seconds. The cycle runs 2 worker processes and 4 factors.

## What one Phase 0 cycle does

1. Acquire a supervisor lease and write `heartbeat.json` / `cycle.json`.
2. Read `directive.json`, `control.json`, data freshness, budget, and R4.
3. Create a family-scoped hypothesis set and reserve experiment ids atomically.
4. Run 2–4 factors in independent Python processes; each writes its own
   experiment, ledger events, and HLC timestamp.
5. Quarantine failures with `counted_in_n: true`.
6. Re-audit the family, compute orthogonality, build decisions, merge progress,
   render the dashboard, release the lease.

First Phase 0 cycle:

- cycle `C-20261108-a24be3`
- family `F-phase0-cycle`, 4 trials
- 4 successes, 0 failures
- ledger 28 distinct trials (24 legacy + 4 family)
- HLC orphan checks: pass
- R4: no breach

## Phase 1: multi-cycle discovery

```powershell
python -m factor_lab phase1 --cycles 4 --limit 4 --heartbeat-interval 2
```

Phase 1 adds a fresh-factor catalog (29 price/volume factors), a Scout that
selects 4 fresh names per cycle with bet round-robin, a Director memo per cycle,
and a discovery report at `phase1/phase1_report.md`.

Four deterministic cycles with 16 new factors produced:

- decisions: **15 park / 1 iterate / 0 promote**
- G0.5: 9 pass / 4 warn / 3 fail
- G3: 12 fail / 4 warn / 0 pass
- q < 0.1: 3; high-signal-but-negative-spread: 3
- verdict: **transmission_gap** (signals exist, no deployable spread)

A fifth fallback agent-plan cycle added `risk.vol_of_vol_120d` and
`risk.high_low_ratio_60d` (both high IC, negative net). A sixth cycle used a
**real DSH Scout/Director** workflow and added three factors. A seventh
full-flow test added three more. Final Phase 1:

- **24 factors / 7 cycles**
- **20 park / 4 iterate / 0 promote**
- G0.5: 16 pass / 5 warn / 3 fail
- G3: 19 fail / 5 warn / 0 pass
- q < 0.1: 7; high-signal-but-negative-spread: 6
- verdict: **transmission_gap**

The first agent-plan run was correctly blocked by **R4**: panel cache reached
3.11GB > 3GB. After `maintenance clean --apply` archived one old panel file it
completed with 2 successes. This is the intended R4 behavior.

DSH subagents inherit the parent composition by default and can use tools. The
Scout/Director prompts keep their context inline for a deterministic first
decision, but the children may also use `read` / `glob` / `grep` to inspect
artifacts. Tool availability is a property of the selected Agent preset /
parent composition, not a hard DSH restriction; if a child has no tools, check
the preset's plugins and service activation first.

## Evaluation adapter

`factor_lab/run.py` does not import the research runner directly. It goes
through `factor_lab/research_adapter.py`:

```text
ResearchReportAdapter.generate
  -> research.report.runner.generate
     -> research.report.metrics.build_report
     -> research.report.sensitivity.compute
     -> research.report.rating.rate
```

`factor_engine` is not in the evaluation path; `research.report.definitions`
uses its registry specs only for factor metadata. Check the active adapter with:

```powershell
python -m factor_lab eval-adapter
```

## Aligned fixed pool

The fixed pool `pool:2022-01-04:200` selects its 200 names using amount on
2022-01-04. To avoid look-ahead selection, the default directive now uses:

```text
universe = pool:2022-01-04:200
start    = 2022-01-04
```

The evaluation report starts a little later (first valid forward-return date),
but never before the pool date. An aligned smoke cycle produced:

- `E-20261108-054` `momentum.ret_21_252`: report start 2022-02-07, 1167 dates
- `E-20261108-055` `risk.upside_vol_252d`: report start 2022-02-07, 1167 dates

The older Phase 1 results used `start=2019-01-01` with the same fixed pool; they
are historical fixed-pool replays and carry look-ahead selection bias. Do not mix
them with the aligned results when judging discovery.

## Artifacts

```text
output/factor_lab/
  control/lab_control.json
  control/directive.json
  control/heartbeat.json
  control/cycle.json
  control/supervisor.json
  policy/gate_policy.json
  ledger/trial_ledger.jsonl
  ledger/ledger_summary.json
  clock/event_clock.json
  data_coverage.json
  quarantine.json
  retention_policy.json
  storage_report.json
  batch_progress.json
  progress/<cycle>/worker-<n>.json
  phase1/scout_c*.json
  phase1/director_c*.json
  phase1/agent_plan.json
  phase1/agent_cycle.json
  phase1/agent_memo.json
  phase1/phase1_cycles.json
  phase1/phase1_report.json
  phase1/phase1_report.md
  phase1/phase1_factors.csv
  research_queue.json
  experiments/E-YYYYMMDD-NNN/
    hypothesis.json
    factor_spec.json
    manifest.json
    report.json
    report.html
    audit.json
    orthogonality.json
    decision_brief.json
    decision.json
  dashboard/lab_dashboard.html
  dashboard/dashboard_data.json
```

`report.json` is a compact serialization of FinBridge's `FactorReport`; heavy
panels and the peer residual matrix are intentionally excluded.

## Phase 0 rules

- A trial is `hypothesis x pre-registered outcome`; L0–L3 stages do not add a
  second trial.
- Default `outcome_policy` is `single` (1 universe x 1 horizon x 1 direction).
- N is **family-scoped**: only family trials touching the development window
  count. A confirmation run uses `family=confirm` and does not enter the
  development N. Existing candidates keep `n_at_decision`; later trials do not
  retroactively re-judge them.
- Quarantine and ledger-repair gaps still count in N.
- Orphan check uses `event_hlc` + `parent_event_id`, not ledger append order.
- `control` is human-owned; stopped means **drain**, not kill: no new factors,
  at most the running workers finish.
- R4 pauses on free disk < 50GB, output > 25GB, or panel cache > 3GB.

## Architecture v4.1 status

- Scout/Director/PathFinder/Gateway/ML Planner/FactorBuilder are tool-capable
  DSH subagents (tools inherited from preset/parent composition); the Python
  runtime still owns data access, execution, gates and budget.
- Runner data preflight goes through `data_interface`; the new `factor_lab/runner.py`
  records a `data_manifest.json`. The legacy research report loader remains the
  production evaluator behind the Research Adapter.
- Vectorized factor evaluation still uses `research.report` / `research.report.metrics`.
- `rho_return` is still pending; G0.5 is exposure-only.
- Multi-trial BH is still a batch step; current q is Bonferroni fallback.
- `capacity` remains a warning when quick profile omits it.
- The first pilot is a D-grade factor; the runtime demonstrates the flow and
  guardrails, not alpha.

## Architecture v4.1 runtime scaffolding

New additive modules implement the target research loop without changing the
production research/factor-data packages:

| Module | Purpose |
|---|---|
| `data_interface_adapter.py` | Runner's only data door: wraps `data_interface` services, never SQL/DB |
| `factor_library.py` | Versioned `factor_lab/factors/generated/<family>/<factor_id>/vN/` |
| `factor_builder.py` | FactorBuilder write path: generated Python + spec + tests + hash |
| `runner.py` | Runner preflight + experiment + `data_manifest.json` |
| `ml_engine.py` | Non-Deep-Learning walk-forward ML runtime + signal admission |
| `campaign.py` | Bounded PathFinder ↔ ML campaign, rounds/traces/stop rules |
| `discovery.py` | Gateway discovery brief/map contracts + offline fallback |
| `web_gateway.py` | DSH LLM Gateway query generation / digest ingestion |
| `history.py` | Append-only research memory, separate from ledger |
| `budget.py` | ¥3,000/month, daily/cycle/campaign caps, 150GB Lab quota |
| `agents.py` | Agent registry, safety-gated reclaim/archive |
| `readiness.py` | Supervisor blocker feedback when data/environment is not ready |
| `bootstrap.py` | Portable bootstrap, doctor, session brief |

Commands:

```powershell
python -m factor_lab bootstrap --research
python -m factor_lab doctor
python -m factor_lab budget status
python -m factor_lab agents status
python -m factor_lab library list
python -m factor_lab factor-build --family demo --factor-id demo.mom --code factor.py --spec spec.json --tests test_factor.py
python -m factor_lab ml run --dataset data.csv --spec ml_spec.json --out output/factor_lab/ml/demo
python -m factor_lab campaign start --objective "研究因子"
python -m factor_lab campaign run --dataset data.csv --plan campaign_plan.json --objective "ml smoke"
python -m factor_lab runner --hypothesis output/factor_lab/hypotheses/H.json
python -m factor_lab discovery queries --question "研究问题"
python -m factor_lab discovery ingest --result gateway_result.json
```

Budget price: input `¥0.04 / 1M tokens`, output `¥8.00 / 1M tokens`.
DSH main session or the Gateway child can use `web_search`; the Gateway LLM
summarizes raw results into citations, then `discovery ingest` writes the map.

Portability:

- `AGENTS.md` and `FACTOR_LAB.md` at repo root are the new-session contract.
- `bootstrap` is idempotent; `doctor` refuses to pretend the environment is ready.
- Runner data access is through `data_interface`, not QuestDB/SQL.
- New evidence/state is under `output/factor_lab`.
