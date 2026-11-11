# Current Agent Behaviors

Each agent has exactly one current behavior directory:

```text
<agent>/
  system.md
  contract.json
  evals.jsonl
```

There are no `v1`, `v2`, `current`, `candidate`, `legacy` or changelog
directories. Behavior history lives in Git only.

Behavior identity for runtime traces:

```text
behavior_hash = sha256(system.md + contract.json + evals.jsonl)
```

Updates replace the current files on a branch; canary comparisons use Git
branches or worktrees, not parallel directories in the working tree.

The DSH team plan loads only the current behavior files.
