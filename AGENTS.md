# Factor Lab workspace

This workspace is a Factor Lab research environment. If the user asks to
research factors:

1. Read `FACTOR_LAB.md`.
2. Run `python -m factor_lab doctor`.
3. If green, run `python -m factor_lab campaign start --objective "研究因子"`.

Rules:

- Do not edit production modules (`research/`, `factors/`, `data_interface/`) for a research run.
- Runner data access must go through `data_interface`; never add direct QuestDB/SQL access to Factor Lab.
- Do not promote factors or change policy; those require human G4.
- Write all research state under `output/factor_lab`.
- If the environment or data is not ready, report `blocker_feedback` and wait; do not fabricate results.
- Web discovery: DSH subagents inherit tools by default. The `web_gateway.js` child may call `web_search` directly, or the main session may search and pass results in; then run
  `python -m factor_lab discovery ingest --result <gateway-result.json>`. The Gateway LLM must keep URLs and citations.
- Tool availability is a preset/composition property, not a prompt prohibition. If a child lacks tools, inspect its Agent preset plugins and service activation.
- Budget pricing is configured at input ¥0.04 / output ¥8.00 per million tokens; record actual usage through `factor_lab.budget.record_usage`.
