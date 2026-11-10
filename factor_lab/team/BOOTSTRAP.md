# Team bootstrap

This is a hard prerequisite for any Factor Lab research round.

## Order

1. Read `roles.json`.
2. Spawn **all 8 durable teammates in one parallel batch**:
   - `pathfinder`
   - `web-gateway`
   - `factor-builder`
   - `runner`
   - `evaluator`
   - `ml-planner`
   - `ml-monitor`
   - `supervisor`
3. Verify the roster: Lead + 8 teammates.
4. Only after the roster is complete, create research tasks and the campaign DAG.

## Never do this

```text
create R1 task DAG
spawn one web-gateway
queue pathfinder/builder/runner/evaluator as blocked
wait forever
```

That is not a team; it is a blocked task board with one worker.

## Correct pattern

```text
spawn teammate(pathfinder, fresh, roles.json.prompt)
spawn teammate(web-gateway, fresh, roles.json.prompt)
spawn teammate(factor-builder, fresh, roles.json.prompt)
spawn teammate(runner, fresh, roles.json.prompt)
spawn teammate(evaluator, fresh, roles.json.prompt)
spawn teammate(ml-planner, fresh, roles.json.prompt)
spawn teammate(ml-monitor, fresh, roles.json.prompt)
spawn teammate(supervisor, fresh, roles.json.prompt)

verify roster == 8
create R1 tasks
assign task-1 to web-gateway
```

## Recovery in a session where only part of the team exists

Spawn the missing roles immediately, then re-check the task board.
Do not wait for a blocked task to "unblock the team"; the team must exist first.

Check the portable roster:

```powershell
python -m factor_lab agents roles
```
