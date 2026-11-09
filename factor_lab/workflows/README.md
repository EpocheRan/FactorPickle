# DSH workflow: Factor Lab research roles

Current role contracts:

- `phase1_scout.js` / `phase1_director.js`: proven Phase 1 selection and memo.
- `research_pathfinder.js`: proposes one bounded campaign round; Python
  `campaign.py` enforces rounds, duplicates, budget and stop rules.
- `ml_planner.js`: non-Deep-Learning ML experiment spec.
- `factor_builder.js`: emits factor code + spec + tests; Python
  `factor-build` writes it into the versioned Lab factor library.
- `web_gateway.js`: no-tool LLM summarizer for raw DSH `web_search` results;
  the DSH main session performs the search and this agent returns a cited
  digest. Python `discovery ingest` writes the map.

No-tool agents are deliberate: file/tool-using children have hung. The Python
runtime owns data access, execution, gates, budget and reclamation.

`phase1_scout.js` and `phase1_director.js` are still the working Phase 1 path;
the newer contracts are additive and can be wired into the campaign runner.


Working pattern:

```text
DSH workflow (Scout, no tools) -> phase1/agent_plan.json
python -m factor_lab phase1-agent --plan phase1/agent_plan.json
DSH workflow (Director, no tools) -> phase1/agent_memo.json
python -m factor_lab phase1-director --memo phase1/agent_memo.json
```

- `phase1_scout.js`: one no-tool Scout agent; pass the current metrics and
  fresh-factor list via `args.context`.
- `phase1_director.js`: one no-tool Director agent; pass the cycle metrics via
  `args.context`.
- The Python Runner is a separate CLI; this avoids the subagent tool-call hang
  observed when an agent tried to read files or run commands.
- Both agents use a JSON schema and explicitly say “不要使用任何工具”.
- Agent budget: at most 2 children per cycle; no ping/probe agents in
  production. Run the reclaim script after the task, not while a workflow is
  active.

The bridge commands:

```powershell
python -m factor_lab phase1-agent --plan output/factor_lab/phase1/agent_plan.json
python -m factor_lab phase1-director --memo output/factor_lab/phase1/agent_memo.json
```

`phase1-agent` validates the selected factors against the fresh catalog, runs
the cycle, writes `agent_cycle.json`, and rebuilds `phase1_report`.
`phase1-director` writes `agent_memo.json` and updates `research_queue.json`.

Note: the Scout/Director prompts must be self-contained. Do not ask them to read
files or execute commands; that path was observed to hang for >10 minutes.

## Reclaim child sessions

DSH has no public “close subagent” tool. Workflow child sessions are stored as
non-`session-*` directories. After a task is complete:

```powershell
powershell -File factor_lab/workflows/reclaim_dsh_children.ps1          # dry run
powershell -File factor_lab/workflows/reclaim_dsh_children.ps1 -Apply   # archive
```

The script moves inactive child sessions to
`%USERPROFILE%\.dsh\reclaimed_sessions\...`; it does not delete them. Refresh the
DSH page afterwards. Do not run this while a workflow is still active.
