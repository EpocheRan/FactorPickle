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
python -m factor_lab mine --limit 23
python -m factor_lab dashboard
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

## Known Phase 0 limits

- One-shot cycle only; no 24/7 scheduler yet.
- No LLM agents yet; Scout / Director / Builder / Auditor-review are target-state.
- `rho_return` is still pending; G0.5 is exposure-only.
- Multi-trial BH is still a batch step; current q is Bonferroni fallback.
- `capacity` remains a warning when quick profile omits it.
- The first pilot is a D-grade factor; P0 demonstrates the flow and guardrails,
  not alpha.
