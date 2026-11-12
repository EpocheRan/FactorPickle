# Factor Lab workspace

## Documentation contract

All README and documentation files describe only the current implementation.
Never add or preserve historical versions, migration records, old plans,
deleted content, changelogs, superseded designs, or intermediate states.
Git history carries changes. When editing docs, rewrite the current truth
instead of appending "changed", "removed", "old" or "legacy" narratives.
This applies to every README in the repository.

## Alpha engine

The workspace is an Alpha research engine. If the user asks to research
factors or run an Alpha campaign:

1. Read `FACTOR_LAB.md`.
2. Read `factor_lab/alpha/ENGINEERING_CONTRACT.md`; it is binding.
3. Run `python -m factor_lab doctor`.
4. Bootstrap the team: `python -m factor_lab alpha team plan`; use
   `list_agents` and spawn every missing member with `spawn_teammate` and the
   plan prompts; then `list_agents`, `python -m factor_lab alpha team verify`,
   and `python -m factor_lab alpha behavior --behavior-action verify`.
5. **Campaign protocol (mandatory, alpha-campaign-v2):** every new campaign
   must delegate model-facing work via shared tasks and `send_message`:
   a PathFinder research question, a FactorBuilder artifact, a Web Gateway
   cited discovery step, and an ML Planner decision (plan or explicit
   falsifiable skip reason). Reader is used when external sources need
   structured extraction. Record task ids and teammate outputs under
   `output/factor_lab/alpha/campaigns/<campaign_id>/team/`. A Lead-only research
   campaign is a protocol violation; the Lead may run deterministic services
   only on teammate outputs (or for a mechanical re-run explicitly labeled
   `protocol: deterministic-maintenance`).
   Each campaign writes `team/team_manifest.json`; every campaign round records
   `agents`, `team_tasks` and `protocol`. Run
   `python -m factor_lab alpha campaign-audit --campaign-id <id>` and
   `python -m factor_lab alpha campaign-preflight --campaign-id <id>`; a strict
   audit failure is a protocol failure even when the factor evidence is
   reproducible. Do not retro-edit historical rounds.
   ML routing: every new campaign has an explicit ML Planner decision; PathFinder
   must return `ml_branch`, and a false branch needs a concrete falsifiable
   reason. Web Gateway must produce a cited discovery map even when the answer
   is "no new external evidence" (record reuse/gaps). See
   `campaign_protocol.external_evidence_policy` and `ml_planner_policy`.
6. Keep research state under `output/factor_lab`.

The five model-facing roles are `PathFinder`, `Web Gateway`, `ML Planner`,
`FactorBuilder`, and `Reader`. Runner, Research Runtime, Evaluator,
Supervisor, Falsifier, Registrar and Allocator are deterministic services.
The agent runtime is DeepSeek Harness; do not build a separate agent runtime.
The canonical protocol is emitted by `alpha team plan` under
`campaign_protocol`.

## Agent-team change control

Any optimization to the agent-team logic is itself a source change. If we change
routing, delegation, roles, campaign protocol, task-board workflow, manifest
requirements or audit rules, the same change must update the relevant source of
truth before use:

- `factor_lab/alpha/team/roles.json` for roles and campaign policy;
- behavior `system.md` / `contract.json` / `evals.jsonl` for model-facing rules;
- `factor_lab/alpha/campaign.py`, `protocol_audit.py` and `preflight.py` for
  enforcement;
- regression tests and current docs.

Conversation-only protocol changes are not binding. Do not rely on chat memory
as the source of truth.

## Rules

- Keep data access behind `data_interface`; Alpha loads panels through
  `factor_lab.runtime.data_interface_adapter` and `panel_builder`.
- Factor-evaluation algorithms live in the shared IO-free `factor_metrics`
  core (the production report's calibrated implementations); do not add a
  second formula implementation in a product. See
  `docs/design/factor_evaluation_contract.md`.
- Never add direct QuestDB/SQL access to Alpha modules.
- Write only disposable runtime artifacts under `output/factor_lab`; never
  write roles, prompts, behavior contracts, eval suites, policy or config there.
- Generated factor artifacts are runtime data under
  `output/factor_lab/factors/generated/`; do not copy them into Git.
- Edge Cards are evidence, not promotion. Only validated/active cards may be
  allocated; unvalidated cards never reach the account.
- Research uses long-short signal evidence; deployment remains long-only.
- Every expression that touches labels is trial-accounted. Search/dev/ML/holdout events also go to the append-only `output/factor_lab/alpha/trial_events.jsonl` sink; holdout is locked and its use log is `output/factor_lab/alpha/holdout_uses.jsonl`.
- Before opening or resuming a campaign, run `python -m factor_lab alpha campaign-preflight --campaign-id <id>`; it verifies frozen policy, pre-registration, team provenance, artifact membership and holdout state.
- Missing data or environment readiness is a blocker; report it and do not
  fabricate results.
- Web discovery must preserve URLs, retrieval times, and citations. Raw HTML
  is not admitted into PathFinder context.
- Do not use LLM output to override a deterministic Gate verdict.
- Never open a research campaign as Lead-only: create the team tasks, delegate
  the model-facing work, record teammate outputs in the campaign `team/`
  directory, and label mechanical re-runs `protocol: deterministic-maintenance`.
