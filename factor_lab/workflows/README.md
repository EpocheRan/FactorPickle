# DSH workflows: Factor Lab research roles

DSH subagents are full local Agents. Their tools come from the selected Agent
preset / parent composition; by default a child joins the parent composition
and inherits its tools. `maxDepth` limits grandchildren, not tools. If a child
appears to have no tools, check the preset's plugins / imported services /
activation state before changing the prompt.

Tool-capable contracts:

- `phase1_scout.js` / `phase1_director.js`: Phase 1 selection and memo; may
  read artifacts with inherited tools. Inline context is still provided so the
  call can also work in a minimal composition.
- `research_pathfinder.js`: may use `read` / `glob` / `grep` / `web_search` to
  inspect History, discovery maps and campaigns; it only proposes one round.
- `web_gateway.js`: may use `web_search` directly; returns a cited digest with
  `discovery_map`/`web_digest` inputs.
- `ml_planner.js`: may read data coverage / history / prior result; returns a
  non-Deep-Learning spec; Python executes.
- `factor_builder.js`: may read existing factors and write generated factor
  versions into the Lab library; Python `factor-build` remains the validator.

The Python runtime owns data access, execution, Gate, budget, campaign rounds
and reclamation. Do not let an LLM child promote a factor or bypass a Gate.

Web discovery flow:

```text
python -m factor_lab discovery queries --question "..."
DSH web_gateway.js (web_search + summarize) -> gateway_result.json
python -m factor_lab discovery ingest --result gateway_result.json
```

Phase 1 bridge:

```text
DSH workflow (Scout) -> phase1/agent_plan.json
python -m factor_lab phase1-agent --plan phase1/agent_plan.json
DSH workflow (Director) -> phase1/agent_memo.json
python -m factor_lab phase1-director --memo phase1/agent_memo.json
```

Agent budget: keep a bounded number of children per cycle; no ping/probe
agents in production. Use `factor_lab/agents.py` / `supervisor reclaim` for
safety-gated registry-based reclamation; the PowerShell script remains a
manual utility.
