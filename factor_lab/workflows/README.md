# DSH workflow: Phase 1 Scout / Director

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
