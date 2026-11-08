# Factor Lab workspace

This workspace is a Factor Lab research environment. If the user asks to
research factors:

1. Read `FACTOR_LAB.md`.
2. Run `python -m factor_lab doctor`.
3. If green, run `python -m factor_lab campaign start --objective "研究因子"`.

Rules:

- Do not edit production modules (`research/`, `factors/`, `data_interface/`) for a research run.
- Runner data access must go through `data_interface`; never add direct QuestDB/SQL access to Factor Lab.
- Do not promote factors or change policy; those require human G4.
- Write all research state under `output/factor_lab`.
- If the environment or data is not ready, report `blocker_feedback` and wait; do not fabricate results.
