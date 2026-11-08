# DSH workflow: Phase 1 Scout / Director

`phase1_scout_director.js` is a DSH `workflow` script. It runs:

1. **Scout** agent: reads `phase1/phase1_report.json` and `cycle_ideas.py`,
   selects fresh factors, returns a structured plan.
2. **Runner** agent: writes the plan to `phase1/agent_plan.json` and runs
   `python -m factor_lab phase1-agent --plan ...`.
3. **Director** agent: reads `phase1/agent_cycle.json` and `phase1_report.json`,
   returns a structured memo.

The Python side has no LLM calls. It exposes two contract commands:

```powershell
python -m factor_lab phase1-agent --plan output/factor_lab/phase1/agent_plan.json
python -m factor_lab phase1-director --memo output/factor_lab/phase1/agent_memo.json
```

The workflow script itself has no filesystem access; the agents perform I/O.
After the workflow returns, write `memo` to `phase1/agent_memo.json` and run
`phase1-director` to update `research_queue.json`.
