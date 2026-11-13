# Agent-team change control

Any optimization to the agent-team logic is a source change, not a chat-only
decision. Update the relevant sources of truth in the same change:

1. `factor_lab/alpha/team/roles.json` — role list and `campaign_protocol`.
2. `factor_lab/alpha/team/behaviors/*/system.md`, `contract.json`,
   `evals.jsonl` — model-facing routing and boundaries.
3. `factor_lab/alpha/team/bootstrap.py` / `alpha team plan` — portable plan.
4. `factor_lab/alpha/campaign.py`, `protocol_audit.py`, `preflight.py` —
   enforcement and strict-audit behaviour.
5. `factor_lab/alpha/policy/*.json` — protocol constants.
6. Regression tests and current `AGENTS.md` / `FACTOR_LAB.md` /
   `ENGINEERING_CONTRACT.md` text.

Verification:

```powershell
python -m factor_lab alpha team verify
python -m factor_lab alpha behavior --behavior-action verify
python -m pytest -q
```

A change that exists only in conversation is not binding.
