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
4. If green, use `python -m factor_lab alpha ...`.
5. Keep research state under `output/factor_lab`.

The five model-facing roles are `PathFinder`, `Web Gateway`, `ML Planner`,
`FactorBuilder`, and `Reader`. Runner, Research Runtime, Evaluator,
Supervisor, Falsifier, Registrar and Allocator are deterministic services.

The agent runtime is DeepSeek Harness. Do not build a separate agent runtime.
For a new session/environment, load the portable roster from
`factor_lab/alpha/team/roles.json`, use `python -m factor_lab alpha team plan`
and create the five durable DSH teammates with `spawn_teammate`. Verify with
`list_agents` and `python -m factor_lab alpha team verify` before research.

## Rules

- Keep data access behind `data_interface`; Alpha loads panels through
  `factor_lab.runtime.data_interface_adapter` and `panel_builder`.
- Never add direct QuestDB/SQL access to Alpha modules.
- Write only disposable runtime artifacts under `output/factor_lab`; never
  write roles, prompts, behavior contracts, eval suites, policy or config there.
- Generated factor artifacts are runtime data under
  `output/factor_lab/factors/generated/`; do not copy them into Git.
- Edge Cards are evidence, not promotion. Only validated/active cards may be
  allocated; unvalidated cards never reach the account.
- Research uses long-short signal evidence; deployment remains long-only.
- Every expression that touches labels is trial-accounted; holdout is locked.
- Missing data or environment readiness is a blocker; report it and do not
  fabricate results.
- Web discovery must preserve URLs, retrieval times, and citations. Raw HTML
  is not admitted into PathFinder context.
- Do not use LLM output to override a deterministic Gate verdict.
