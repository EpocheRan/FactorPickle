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


## Research memory

Every new campaign writes `team/research_context.json`
(`factor_lab.alpha.research_context.v2`). PathFinder must read it and treat
`hard_constraints.hard_negatives` as binding: failed/unverified validation
cannot be ignored, source hashes are checked by audit, and exact duplicate
expressions are blocked.

Validation evidence is appended to
`output/factor_lab/alpha/memory/validation_results.jsonl`. The structured facts
are authoritative; an LLM summary may be used for long lineages but cannot
replace the facts or source ids. DeepSeek Flash is the optional compressor
adapter (`factor_lab.alpha.llm_compressor.DeepSeekCompressor`), configured only
in gitignored `config.json`.

The Lead should use:

```bash
python -m factor_lab alpha memory
python -m factor_lab alpha memory --memory-action build-context --campaign-id CAMP-... --parent-campaign-id CAMP-...
```

## Boundaries

- PathFinder decides research direction; no data access or code execution.
- Web Gateway discovers and cites; no factor/model judgment.
- ML Planner designs non-deep-learning experiments; no metric rewriting.
- FactorBuilder writes versioned artifacts; no promotion or capital allocation.
- Reader extracts text with evidence; no investment conclusion.
- Deterministic Gate/Eval/Allocator services are not agents and cannot be
  overridden by LLM output.
