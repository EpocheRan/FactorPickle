# Durable teammates

`roles.json` is the portable source of truth for Factor Lab's research team.

Each role has:

- `name`: stable teammate name
- `description`: role label
- `context`: `fresh`
- `prompt`: the exact role contract used to create the teammate

## Create the team in a new DSH session

For each role in `roles.json`, the Lead creates one durable teammate:

```text
spawn_teammate(
  name=<role.name>,
  context=<role.context>,
  description=<role.description>,
  prompt=<role.prompt>
)
```

Then send work with `send_message(target=<role.name>, message=...)`.

At runtime the durable teammates themselves live in the DSH session and are
not stored in Git. The committed source of truth is `roles.json`; recreating
the team takes one `spawn_teammate` call per role.

Check the portable roster:

```powershell
python -m factor_lab agents roles
```
