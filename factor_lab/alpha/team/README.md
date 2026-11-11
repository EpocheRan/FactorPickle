# Alpha DSH Team

The agent runtime is DeepSeek Harness. This directory contains only portable
contracts, not an agent runtime.

## Team

Five durable LLM teammates:

```text
pathfinder
web-gateway
ml-planner
factor-builder
reader
```

The Lead is the current DSH session.

## New session / new environment

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab alpha team plan
python -m factor_lab alpha team verify
python -m factor_lab alpha team brief
```

Then, in the DSH session:

1. `list_agents`
2. spawn every missing member from the plan with `spawn_teammate`
3. `list_agents` again; verify all five are running or inactive
4. start research with `alpha campaign` or an L0 cycle

No previous chat history or absolute path is required.

Behavior files are current-only:

```text
behaviors/<agent>/
  system.md
  contract.json
  evals.jsonl
```

There are no version directories or historical behavior files. Git carries
behavior history. The roster is read from
this directory, and all mutable state is under `output/factor_lab`.

## Boundaries

- PathFinder decides research direction; no data access or code execution.
- Web Gateway discovers and cites; no factor/model judgment.
- ML Planner designs non-deep-learning experiments; no metric rewriting.
- FactorBuilder writes versioned artifacts; no promotion or capital allocation.
- Reader extracts text with evidence; no investment conclusion.
- Deterministic Gate/Eval/Allocator services are not agents and cannot be
  overridden by LLM output.
