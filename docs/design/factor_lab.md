# Factor Lab

Factor Lab 是 FactorPickle 上的自主因子研究运行时。目标是让研究循环由机制约束、
可审计、可中断，同时不改变生产研究与数据模块的语义。

## 1. 边界

读取：

- `data_interface`：行情、财务、资金流、两融、估值、行业、regime、ML frame
- Research Module：报告、评级、敏感性
- Lab 历史、预算、控制状态、数据覆盖

写入：

- `output/factor_lab/**`
- `factor_lab/factors/generated/**`

不写：

- `research/`、`factors/`、`data_interface/` 的生产行为
- 生产因子 registry
- QuestDB 业务数据

## 2. 运行时结构

```text
PathFinder
  ├─ Web Gateway
  ├─ FactorBuilder
  └─ ML Engine
        │
        ▼
Runner
  ├─ DataInterfaceGateway
  ├─ build_factor_panel
  └─ Research Adapter
        │
        ▼
Evaluator
  ├─ research.report.runner.generate
  ├─ audit
  └─ rating
        │
        ▼
Supervisor
  ├─ campaign rounds
  ├─ budget
  ├─ readiness
  └─ agent reclamation
```

Runner 是 LLM 执行角色，不计算因子矩阵。FactorBuilder 的产物是代码 + spec +
测试 + hash；Research Module 负责因子计算和 `FactorReport`。

## 3. 数据契约

`DataInterfaceGateway` 暴露 typed bundles：

| Bundle | 用途 |
|---|---|
| `market_bundle` | OHLC、amount、turnover |
| `flow_bundle` | 资金流，映射为 `mf_*` |
| `margin_bundle` | 两融，映射为 `margin_*` |
| `valuation_bundle` | `total_mv` / `float_mv` |
| `industry_map` | 主行业映射 |
| `regime_series` | 创业板指 200D MA regime |
| `ml_frame` | ML Engine long-form market frame |

`build_factor_panel` 按因子 category 路由：

```text
price      → market + reference
sentiment  → market + moneyflow + margin
quality    → market + valuation/reference
value      → market + valuation/reference
```

Quality/Value 的 materialized factor values 仍由 Research Module 读取。

## 4. 因子库与工件

FactorBuilder 写入：

```text
factor_lab/factors/generated/<family>/<factor_id>/vN/
  factor.py
  spec.json
  test_factor.py
  manifest.json
```

每个 artifact 必须包含：

- `factor_id`
- `family`
- `category`
- `version`
- `code_hash`
- `data_contract`

Runner 校验 artifact 的 `factor_id` 与 hypothesis 一致，并把 artifact 写入
`experiments/<id>/data_manifest.json`。

## 5. 实验与门禁

- Trial = hypothesis × 预注册 outcome。
- 默认 `outcome_policy=single`。
- N 为 family-scoped；confirmation 使用 `family=confirm`。
- 已决策候选保留 `n_at_decision`，不回溯重判。
- L0–L3 是同一 trial 的阶段，不重复计数。
- 门禁：G0.5 暴露相关；G2 审计完整性；G3 交易性。
- 预算与存储上限由 supervisor 和 R4 统一检查。

## 6. 控制与恢复

```powershell
python -m factor_lab control pause --reason "review"
python -m factor_lab control resume
python -m factor_lab control show
```

- `control` 人工所有；
- stop = drain，不杀正在收尾的 worker；
- lease、heartbeat、HLC 保证多进程下不重复决策；
- 崩溃后的 experiment 通过 ledger repair / quarantine 恢复；
- 所有状态可从 workspace 恢复，不依赖聊天历史。

## 7. 评估后端

Research Module 的 quantile NAV 支持 pandas/Polars：

```text
REPORT_METRICS_BACKEND=auto | pandas | polars
```

- 默认 auto；
- Rank IC 固定 pandas；
- Quantile NAV 中等截面 Polars，全市场 pandas；
- `FactorReport.metrics_engine` 记录实际后端；
- 两条路径必须保持数值 parity；
- Polars 是可选依赖，缺失时自动回退 pandas。

## 8. 观测

Dashboard 读取：

- experiments
- ledger
- policy
- campaigns
- data coverage
- storage / budget

人工入口：

```powershell
python -m factor_lab dashboard
python -m factor_lab campaign start --objective "研究因子"
```
