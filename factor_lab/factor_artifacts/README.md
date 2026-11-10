# Factor artifacts

Portable copies of Agent/FactorBuilder-generated Lab factors.

Runtime library: `output/factor_lab/factors/generated/` (gitignored).
Portable source of truth: this directory.

Each version contains:

- `factor.py`
- `spec.json`
- `test_factor.py`
- `manifest.json`

Install into the local runtime library:

```powershell
python -m factor_lab library install-artifacts
```

The installer copies every `v*` directory into
`output/factor_lab/factors/generated/` without touching other runtime state.
