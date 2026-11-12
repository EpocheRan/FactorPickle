# Factor Lab

Portable Alpha research workspace. A new DSH session should not need prior
chat history.

## Start

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab alpha team verify
python -m factor_lab alpha memory
```

Research commands are under `python -m factor_lab alpha ...`.

Instrument calibration: `python -m factor_lab alpha probe --codes <pool> --start <s> --end <e>` runs the cheat / random / known probes before large search campaigns; probe reports live under `output/factor_lab/alpha/probes/`.

All-research dashboard: `python -m factor_lab alpha report` reads campaign rounds, team outputs, memory, family ledger, probes, data axes and holdout policy and writes `output/deliverables/research_portfolio.html` plus a JSON sidecar.

Pre-registration: `factor_lab.alpha.prereg` validates and freezes a PathFinder pre-registration with a hash before evaluation; FactorBuilder artifacts must match the frozen expressions exactly, and the campaign round records the hash.

ML uses explicit pre-registered features, never auto-injection. `python -m factor_lab alpha axes` includes an `ml` feature catalog; ML Planner should include expanded P0/P1 fields when relevant. PathFinder returns `ml_branch` and requests ML Planner after two campaigns without ML or after new axes; see `campaign_protocol.ml_planner_policy`. ML targets match the Gate entry rule (`close[t+h]/open[t+1]-1`), OOS admission reports both daily and overlap-adjusted t, and model cards keep raw/standardized coefficients and fold coefficients. Distillation supports two pre-registered modes: `projection` (fit rank-space weights to folded OOS scores) and `model_card` (export one model-card coefficient vector, normally with a single walk-forward split). The single-fold/single-vector protocol is source-tracked in `factor_lab/alpha/policy/singlefold.json`: n_splits=1, cs_rank features/target, model_card export, report-only fidelity, OOS test segment only, no 0.95 target. Fidelity is descriptive in report-only mode; a 0.95 gate must be pre-registered, never an automatic target. ML experiments are recorded in the unified trial sink and are never frozen as a branch.

Default search includes the expanded P0/P1 axes: `alpha search` samples base OHLCV/valuation fields plus margin, moneyflow, financial quality and P1 event fields. ML plans remain explicit (features are pre-registered, not auto-expanded).

Data axes: P0 fields (margin, moneyflow structure, financial quality, valuation) are loaded by PanelBuilder. P1 event fields (block_trade, top_list, repurchase, stk_holdernumber) are loaded through DataInterfaceGateway.event_bundle from data_interface.p1_events; flow amounts are zero-filled when no event exists, while event-vwap/ownership values remain null and are treated as missing (not fabricated zeros). Event features use the PIT event date and the full pre-roll state, including earlier holder announcements. `alpha axes` prints the P0/P1 axis catalog; coverage and nonzero-event evidence lives under `output/factor_lab/alpha/data_axes/`. The panel exposes both adjusted (`close/open/high/low`) and raw (`close_raw/open_raw/high_raw/low_raw`) prices; level factors must use the raw columns to avoid qfq future-adjustment leakage. Financial statements are indexed by `publish_date` only (no `period_end` fallback) and weekend announcements are carried into the next trading session.

Family FDR / freeze: `alpha family bootstrap --recost` builds the family ledger (re-costing legacy attempts while preserving `original_metrics`); `alpha family fdr` computes FDR; `alpha family reclassify` applies program FDR to historical Edge Cards; `alpha freeze` is the only path to a holdout confirmation. Freeze requires a verified frozen pre-registration containing the expression and rejects Q overrides. `alpha confirm` refuses candidates without a passed freeze decision and refuses to reuse a holdout that has already been consumed; the append-only log is `output/factor_lab/alpha/holdout_uses.jsonl`.

The binding coding contract is `factor_lab/alpha/ENGINEERING_CONTRACT.md`.

## Transaction costs

Alpha research and deployment use the actual China A-share fee schedule rather than a blanket 20bps per side:

- Buy: commission 1.8bps + handling 0.34bps + regulatory 0.20bps + transfer 0.10bps = **2.44bps**.
- Sell: the same components + stamp duty 5.0bps = **7.44bps**.
- Effective round-trip fee per one-way turnover: **9.88bps** (representative screenshot trade: 2.41bps buy / 7.45bps sell / 9.82bps round trip).
- Short borrow: **800bps/year**; the 5-day hold cost is about 1.59bps per rebalance.
- `factor_metrics` keeps the generic 20bps `CostModel` for calibrated research/parity; `alpha_gate_spec()` is the Alpha cost profile and uses the buy/sell fee schedule plus borrow.

## DSH team

The agent runtime is DeepSeek Harness; the repository only carries the portable
roster and contracts.

```text
factor_lab/alpha/team/roles.json
  pathfinder
  web-gateway
  ml-planner
  factor-builder
  reader
```

Each agent has one current behavior directory under
`factor_lab/alpha/team/behaviors/<agent>/`; behavior history lives in Git only.

New session / new environment:

1. `python -m factor_lab doctor`
2. `python -m factor_lab alpha team plan`
3. In DSH, `list_agents`; spawn every missing member with `spawn_teammate`
   using the plan prompts.
4. `list_agents` again; verify all five teammates are running or inactive.
5. `python -m factor_lab alpha team verify`
6. `python -m factor_lab alpha behavior --behavior-action verify`
7. Start `alpha campaign` or an L0 cycle **through the campaign protocol**.

No previous session or absolute path is required.

### Campaign protocol (mandatory)

`python -m factor_lab alpha team plan` emits the current `campaign_protocol`.
Every new `alpha-campaign-v2` campaign must delegate model-facing work to the
five DSH teammates:

- required per campaign: PathFinder research question + FactorBuilder artifact
  + Web Gateway cited discovery + ML Planner decision (plan or explicit
  falsifiable skip reason);
- Reader is used when external sources need structured event extraction;
- one shared task per delegated role (claim → work → complete), plus
  `send_message` with a self-contained task and output contract;
- record task ids and teammate outputs under
  `output/factor_lab/alpha/campaigns/<campaign_id>/team/`;
- write `team/team_manifest.json` and record `agents`, `team_tasks`, `protocol`
  on every campaign round;
- run `python -m factor_lab alpha campaign-audit --campaign-id <id>`; a strict
  audit must pass before calling the campaign protocol-compliant; campaigns
  created with `protocol_version=alpha-campaign-v2` require completed
  web-gateway and ml-planner roles in the manifest;
- the Lead may run deterministic services only on teammate outputs; a Lead-only
  campaign is invalid. Mechanical re-runs must be labeled
  `protocol: deterministic-maintenance` and must not generate new candidates.

## Engine

```text
PathFinder / Web Gateway / ML Planner / FactorBuilder / Reader
  → Factor Library
  → Runner
  → Research Runtime
  → Falsifier
  → Edge Card
  → Memory
  → Campaign
  → long-only deployability / allocator
```

Five model-facing roles only: PathFinder, Web Gateway, ML Planner,
FactorBuilder, Reader. Runner, Research Runtime, Evaluator, Supervisor,
Falsifier, Registrar, Allocator and ML Runtime are deterministic or tool
services.

## Evaluation core

Factor-evaluation algorithms live once, in the IO-free `factor_metrics` core
(the production report's calibrated implementations). Alpha chooses the
`alpha-gate-v1` profile (non-overlapping sampling, plain t, PIT universe); the
report chooses `research-report-v1` (daily IC + Newey-West). Products must not
fork formulas. The binding reference is
`docs/design/factor_evaluation_contract.md`.

## Factor Library

Versioned artifacts live under:

```text
output/factor_lab/factors/generated/<family>/<factor_id>/v1/
  factor.dsl | factor.py
  spec.json
  test_factor.py
  manifest.json
```

Versions are immutable. Edge Cards reference `factor_id`, `version`, and
`code_hash`.

## Edge Cards and memory

```text
output/factor_lab/alpha/memory/
  attempts.jsonl
  edge_cards.jsonl
  failures.jsonl
  discoveries.jsonl
  summary.json
```

`search` reads known expressions before generating candidates, records every
attempt, writes top-candidate Edge Cards and finalizes the summary. `confirm`
writes holdout cards.

## Commands

```powershell
python -m factor_lab alpha validate --expression "rank(ts_corr(rank(close), rank(volume), 20))"
python -m factor_lab alpha eval --codes output/factor_lab/hypotheses/pool1000_10y.txt --start 2016-01-04 --end 2025-10-31 --expression "rank(close)"
python -m factor_lab alpha benchmark --codes output/factor_lab/hypotheses/pool1000_10y.txt --start 2016-01-04 --end 2025-10-31
python -m factor_lab alpha holdout --end 2026-11-01 --save
python -m factor_lab alpha search --codes output/factor_lab/hypotheses/pool1000_10y.txt --start 2016-01-04 --end 2025-10-31 --budget 80
python -m factor_lab alpha confirm --codes output/factor_lab/hypotheses/pool200.txt --search output/factor_lab/alpha/search/ALPHA-....json
python -m factor_lab alpha memory
python -m factor_lab alpha library
python -m factor_lab alpha campaign --objective "研究应计" --max-rounds 5
python -m factor_lab alpha campaign-audit --campaign-id CAMP-...
python -m factor_lab alpha campaign-preflight --campaign-id CAMP-...
python -m factor_lab alpha power --n-trials 276 --n-eff 145 --observations 148
python -m factor_lab alpha ten-year
python -m factor_lab alpha ten-year --pool-out output/factor_lab/hypotheses/pool1000_10y.txt --audit-out output/deliverables/data_coverage_10y.json
python -m factor_lab alpha ten-year --expression "rank(close)" --out output/factor_lab/alpha/ten_year_eval.json
python -m factor_lab alpha report --report-out output/deliverables/research_portfolio.html
python -m factor_lab alpha ml --codes output/factor_lab/hypotheses/pool1000_10y.txt --start 2016-01-04 --end 2025-10-31 --features close,volume,amount
python -m factor_lab alpha discovery
python -m factor_lab alpha axes
python -m factor_lab alpha deploy --codes output/factor_lab/hypotheses/pool1000_10y.txt --start 2016-01-04 --end 2025-10-31 --expression "rank(close)"
python -m factor_lab alpha allocate
python -m factor_lab alpha cycle --factor-artifact <factor-dir-or-id> --campaign-id CAMP-...
python -m factor_lab alpha roles
```

## Data boundary

Alpha uses:

```text
factor_lab.runtime.data_interface_adapter.DataInterfaceGateway
factor_lab.runtime.panel_builder.build_factor_panel
data_interface services
```

Do not add direct QuestDB/SQL access to Alpha. Data without PIT is not admitted.

## Research rules

- Search only on the development window; the holdout policy lives in
  `factor_lab/alpha/policy/holdout.json`, not in `output/`, and is locked.
  Current dev is `2016-01-04..2025-10-31`; expanded P0/P1/valuation axes are
  effective from 2017-01-03. Holdout remains `2025-11-01..2026-11-01` and is
  already consumed four times, so no confirmation is independent.
- Data audit: `output/deliverables/data_audit_10y.md` / `data_coverage_10y.json`, reproducible with `python -m factor_lab alpha ten-year --audit-out ...`.
- The 10-year procedure is source-tracked in `factor_lab/alpha/policy/ten_year.json`; `factor_lab/alpha/ten_year.py` exposes `build_pool`, `data_audit`, `load_pool`, `run_expression`, `run_artifact` and the `alpha ten-year` CLI. Campaign results remain disposable runtime artifacts under `output/factor_lab`; they are reproducible from the protocol + data, not committed to Git.
  Current universe policy is PIT top-800 liquidity with a 60-day trailing
  amount and 120-day history; the 1,000-code ex-ante pool is
  `output/factor_lab/hypotheses/pool1000_10y.txt`.
- The universe is point-in-time: eligibility comes from trailing data only
  (delisted/suspended/newly listed names are excluded, never forward-filled);
  the policy lives in `factor_lab/alpha/policy/universe.json`. Every research
  command applies it by default; `--no-pit-universe` is for fixed-pool
  comparison only.
- Research evidence uses long-short spreads; deployment is long-only.
- Verdicts are tri-state (`pass` / `fail` / `insufficient_evidence`); missing
  metrics never become pass or fail. Capacity is informational, not a gate.
- Every label-touching candidate and ML experiment is trial-accounted. Search, dev, ML and holdout events also go to the append-only unified sink `output/factor_lab/alpha/trial_events.jsonl`; the legacy memory/ledger remain the FDR denominators until the sink is promoted.
- A new or resumed campaign must pass `alpha campaign-preflight` (frozen policy, pre-registration, team provenance, artifact membership, holdout state) before touching labels.
- Red Team failure cannot become a candidate.
- No validated Edge Card means 100% broad-index baseline.
- Research modes: `explore`, `validate`, `deploy`. Exploration is allowed to
  be non-deployable, but still trial-accounted.
- Campaigns enforce max rounds, budget, duplicate stops and stop decisions.
- ML is non-deep-learning only; purged walk-forward with embargo is required.
- Web Gateway discoveries require cited sources; raw HTML is not passed to
  PathFinder.

## State

```text
output/factor_lab/
  alpha/          memory, search, confirm, campaigns, ml
  factors/        versioned runtime factor artifacts
  discovery/      Web Gateway artifacts
  hypotheses/     code pools
```

All mutable state is under `output/factor_lab`. Generated factor artifacts are
runtime research data, not portable design assets.
