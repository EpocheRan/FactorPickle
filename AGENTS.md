# Factor Lab workspace

## Documentation contract

All README and documentation files describe only the current implementation.
Never add or preserve historical versions, migration records, old plans,
deleted content, changelogs, superseded designs, or intermediate states.
Git history carries changes. When editing docs, rewrite the current truth
instead of appending "changed", "removed", "old" or "legacy" narratives.
This applies to every README in the repository.

This workspace is a Factor Lab research environment. If the user asks to
research factors:

1. Read `FACTOR_LAB.md`.
2. Read `factor_lab/team/roles.json` and create **all 8 durable teammates** from it in parallel.
3. Verify the full team is running/inactive (Lead + 8) before creating any research task.
4. Run `python -m factor_lab doctor`.
5. If green, run `python -m factor_lab campaign start --objective "研究因子"`.

Never create the R1 task DAG before the team roster exists. A task board is
not a team; spawning one worker and queueing blocked tasks is a bootstrap bug.

Rules:

- Do not edit production modules (`research/`, `factors/`, `data_interface/`) for a research run.
- Runner data access must go through `data_interface`; never add direct QuestDB/SQL access to Factor Lab.
- Do not promote factors or change policy; those require human G4.
- Write all research state under `output/factor_lab`.
- Generated factor code/spec/tests/manifests are runtime research data under
  `output/factor_lab/factors/generated/`. Do not copy, mirror, or move them into
  the repository; they are not portable design assets.
- Portable design is limited to code, contracts, `factor_lab/team/roles.json`,
  and `factor_lab/sources/catalog.json`. Never duplicate runtime state into Git.
- Data axis is chosen by PathFinder from the campaign-scoped `data_capabilities`.
  Do not write "ohlcv_1d only" / "no financial/moneyflow/margin" into a round
  directive. Unverified services require a readiness probe, not a prohibition.
- If the environment or data is not ready, report `blocker_feedback` and wait; do not fabricate results.
- Web discovery: DSH subagents inherit tools by default. The `web_gateway.js` child may call `web_search` directly, or the main session may search and pass results in; then run
  `python -m factor_lab discovery ingest --result <gateway-result.json>`. The Gateway LLM must keep URLs and citations.
- Tool availability is a preset/composition property, not a prompt prohibition. If a child lacks tools, inspect its Agent preset plugins and service activation.
- Budget pricing is configured at input ¥0.04 / output ¥8.00 per million tokens; record actual usage through `factor_lab.budget.record_usage`.
