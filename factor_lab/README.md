# Factor Lab (P0)

`factor_lab` is an additive experiment harness for FinBridge. It reads the
existing research pipeline and writes structured artifacts under
`output/factor_lab/`; it does not edit production modules, the factor registry,
or QuestDB during P0.

## P0 scope

1. `gate_policy.json` v1.0 with gate types and one adjusted-p policy.
2. `trial_ledger.jsonl`: hash chain, `ledger_repair`, orphan-run check.
3. `audit.json`: deterministic IC / spread / cost checks plus policy and
   ledger linkage.
4. `report.json`, `decision_brief.json`, and an in-process staging runner.
5. A self-contained HTML dashboard for human review.
6. `orthogonality.json`: P1 exposure correlation against a small library snapshot.
7. Non-destructive output inventory / cleanup planning.

## Commands

```powershell
python -m factor_lab init
python -m factor_lab pilot
python -m factor_lab dashboard
python -m factor_lab control show
python -m factor_lab control pause --reason "review"
python -m factor_lab control resume
python -m factor_lab maintenance inventory
python -m factor_lab maintenance clean          # dry run
python -m factor_lab maintenance clean --apply  # move to archive/trash
```

Run one hypothesis:

```powershell
python -m factor_lab run --hypothesis output/factor_lab/hypotheses/<id>.json
```

Audit / rebuild the brief for one experiment:

```powershell
python -m factor_lab audit --experiment E-20261108-001
python -m factor_lab brief --experiment E-20261108-001
python -m factor_lab orthogonality --experiment E-20261108-001
```

## Artifacts

```text
output/factor_lab/
  control/lab_control.json
  policy/gate_policy.json
  ledger/trial_ledger.jsonl
  ledger/ledger_summary.json
  data_coverage.json
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

## P0 rules

- A trial is `hypothesis x pre-registered outcome`; L0–L3 stages do not add a
  second trial.
- Default `outcome_policy` is `single` (1 universe x 1 horizon x 1 direction).
  A robustness grid must be explicit and is a cost.
- The Auditor verifies numbers and risk completeness, not the recommendation.
- `report.run_id` must resolve to an earlier `hypothesis_registered` event.
- Broken ledger chains are repaired with a recorded `ledger_repair` gap; trials
  in the gap are `degraded`.
- `control` is human-owned; a paused lab refuses to start new runs.

## Known P0/P1 limits

- `orthogonality.py` computes exposure correlation only; `rho_return` is still
  pending. The G0.5 verdict is labeled exposure-only.
- `q_value` is a single-trial raw p for now; multi-trial BH is a batch step.
- `capacity` and 2x-cost sensitivity: 2x cost is computed from the report's
  `cost_mean`; capacity remains a warning when the quick profile omits it.
- The first pilot (`liquidity.amount_bias_21_252`) is a D-grade factor; P0
  demonstrates the flow and the guardrails, not alpha.
