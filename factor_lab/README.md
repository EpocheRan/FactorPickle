# Factor Lab

`factor_lab` 是 FactorPickle 的自主因子研究运行时。它把数据、因子构建、执行、评估、
评分和预算约束组织成可审计的闭环，研究状态全部写入 `output/factor_lab/`。

## 边界

- 只通过 `DataInterfaceGateway` 读取数据；Runner 不直接访问 QuestDB/SQL。
- 不修改 `research/`、`factors/`、`data_interface/` 的生产语义。
- 不写生产因子 registry，不写数据库。
- 因子晋升与策略变更需要人工 G4，运行时不能自动晋升。
- 所有可变研究状态位于 `output/factor_lab/`。

## 角色

> 本 README 只描述当前实现；不保留历史版本、迁移记录或旧方案。

| 角色 | 职责 |
|---|---|
| PathFinder | 从研究历史、数据覆盖和发现结果中选择方向 |
| Web Gateway | 生成检索问题、接收 DSH 检索结果并保留 URL 引用 |
| FactorBuilder | 生成版本化因子代码、spec、测试并写入 Lab 因子库 |
| Runner | 选择数据、构建 Panel、执行一次预注册实验 |
| ML Engine | 非深度学习 walk-forward 实验与信号准入 |
| Evaluator | 调用 Research Module 生成 `FactorReport`，执行审计与评级 |
| Supervisor | 驱动多轮 campaign，处理预算、数据 readiness、失败回收 |

## 数据路径

```text
DataInterfaceGateway
  ├─ market_bundle
  ├─ flow_bundle
  ├─ margin_bundle
  ├─ valuation_bundle
  ├─ industry_map
  ├─ regime_series
  └─ ml_frame
        │
        ▼
build_factor_panel
  ├─ price: market + reference
  ├─ sentiment: market + moneyflow + margin
  └─ quality/value: market + valuation/reference
        │
        ▼
Panel + data_manifest
        │
        ▼
Research Adapter
  → research.report.runner.generate
    → research.report.metrics.build_report
    → rating / sensitivity / render
```

Runner 不计算因子矩阵。`panel=` 注入后，因子仍由 Research Module 计算；
Quality/Value 的 materialized factor values 也由 Research Module 读取。

## Metrics 后端

Research Module 的 quantile NAV 支持 pandas / Polars 自动路由：

```powershell
$env:REPORT_METRICS_BACKEND = "auto"
$env:REPORT_METRICS_BACKEND = "pandas"
$env:REPORT_METRICS_BACKEND = "polars"
```

- 默认 `auto`；
- Rank IC 固定 pandas；
- Quantile NAV 在中等截面使用 Polars，全市场大截面保持 pandas；
- `FactorReport.metrics_engine` 记录实际后端；
- Polars 是可选依赖：`uv sync --extra perf`。

## 启动

```powershell
python -m factor_lab bootstrap
python -m factor_lab doctor
python -m factor_lab campaign start --objective "研究因子"
```

`doctor` 检查数据服务、配置和依赖；不 ready 时必须返回 blocker，不能伪造运行。

## 命令

> 研究入口只接受 PathFinder plan。没有静态 idea 目录，也没有历史因子兼容层。

```powershell
python -m factor_lab bootstrap --research
python -m factor_lab doctor
python -m factor_lab budget status
python -m factor_lab agents status
python -m factor_lab library list

python -m factor_lab factor-build --family demo --factor-id demo.mom --code factor.py --spec spec.json --tests test_factor.py
python -m factor_lab campaign start --objective "研究因子"
python -m factor_lab campaign run --dataset data.csv --plan campaign_plan.json --objective "ml smoke"

python -m factor_lab runner --hypothesis output/factor_lab/hypotheses/H.json --codes 600000,000001
python -m factor_lab cycle --plan output/factor_lab/hypotheses/plan.json
python -m factor_lab parity --codes "600000,000001,600519,000858,601318" --start 2022-01-04 --end 2026-11-01

python -m factor_lab ml run --dataset data.csv --spec ml_spec.json --out output/factor_lab/ml/demo
python -m factor_lab ml data --codes "600000,000001" --start 2022-01-04 --end 2026-11-01 --out output/factor_lab/ml/data

python -m factor_lab discovery queries --question "研究问题"
python -m factor_lab discovery ingest --result gateway_result.json

python -m factor_lab dashboard
python -m factor_lab research-context
python -m factor_lab evaluate --experiment E-... --finalize
python -m factor_lab web-mode --mode exploit
python -m factor_lab dry-run
python -m factor_lab control pause --reason "review"
python -m factor_lab control resume
python -m factor_lab maintenance inventory
python -m factor_lab maintenance clean
python -m factor_lab maintenance clean --apply
```

## 评估状态

每个实验只有一个 `evaluation.json` 状态：

`draft / running / needs_fix / blocked / parked / candidate / promoted / failed`

`audit`、`g3`、`rating` 是评估子字段；只有 Supervisor 能写最终状态。

## 研究上下文

`research_context.json` 汇总历史实验、失败构造、数据覆盖、来源引用、ML 结果和 agent trace；
PathFinder 必须基于它提案，并声明 `informed_by` 与 `differentiation`。

## Campaign 规则

- 一个 trial = hypothesis × 预注册 outcome。
- 默认 `outcome_policy=single`：1 universe × 1 horizon × 1 direction。
- N 是 family-scoped；confirmation run 使用 `family=confirm`，不进入开发 N。
- 已决策候选保留 `n_at_decision`；后续 trial 不回溯重判。
- quarantine / ledger repair 缺口仍计入 N。
- 最大 5 轮、12 个 ML experiment、¥60/campaign；重复或无改进停止。
- control 是人工所有权；stop 是 drain，不杀正在收尾的 worker。

## 预算与存储

- LLM：¥3,000/月，¥100/天，20% reserve，¥25/cycle，3 cycles/day，¥60/campaign。
- 定价：input ¥0.04 / 1M tokens，output ¥8.00 / 1M tokens。
- Lab 配额 150GB：100 output + 20 cache + 30 archive，50GB free floor。
- R4：free disk < 50GB、output > 25GB 或 panel cache > 3GB 时暂停。

## 产物

```text
output/factor_lab/
  control/          directive / budget / heartbeat / cycle / agents
  policy/           gate_policy.json
  ledger/           trial_ledger.jsonl / ledger_summary.json
  clock/            event_clock.json
  factors/          generated factors（versioned）
  experiments/      immutable experiment artifacts
  campaigns/        campaign traces
  dashboard/        read-only dashboard
  ml/               ML datasets and experiment outputs
  research_queue.json
  data_coverage.json
  quarantine.json
  storage_report.json
```

`experiments/<id>/` 包含：

```text
hypothesis.json
factor_spec.json
manifest.json
data_manifest.json
report.json
report.html
audit.json
orthogonality.json
decision_brief.json
decision.json
```

`report.json` 是 `FactorReport` 的紧凑序列化，不包含重型 Panel 和 peer residual matrix。

## Durable teammates

Factor Lab 的研究角色由 DSH durable teammates 承担：

| Teammate | 职责 |
|---|---|
| `pathfinder` | 选择研究方向和下一轮动作 |
| `web-gateway` | 外部检索、保留 URL 引用、生成 discovery map |
| `factor-builder` | 生成因子代码、spec、测试并写入 Lab 因子库 |
| `runner` | 选择 codes 和数据窗口，通过 data_interface 执行一次实验 |
| `evaluator` | 解释评级/审计，检查口径一致性，给出 caveats |
| `ml-planner` | 设计非 Deep Learning 实验方案 |
| `ml-monitor` | 检查 leakage、over-search、成本与可解释性 |
| `supervisor` | 审计预算、provenance、gate 和 agent 行为 |

Gate Service、DataInterfaceGateway、Research Module/Adapter 是确定性服务，不是 teammate。
Lead 通过 `send_message` 派任务，teammate 完成后保持 inactive，可在下一轮唤醒。
Teammate 共享工作区，但不得修改生产模块、直连数据库、promote 或绕过 Gate。

## 移植性

- `AGENTS.md` 与 `FACTOR_LAB.md` 是新会话入口。
- `bootstrap` 幂等；`doctor` 失败必须给出可执行 blocker。
- 研究运行不依赖聊天历史；所有上下文从 workspace 文件恢复。
