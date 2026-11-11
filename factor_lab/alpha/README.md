# Alpha Research Engine

本目录实现 Factor Lab 的 Alpha DSL、证据评估、研究记忆、版本化 Factor Library、Campaign 控制、ML Branch 与 Web Gateway 发现接入。

## 当前模块

```text
factor_lab/alpha/
  expression.py       表达式解析、IR、canonical rendering
  operators.py        时序/横截面/条件/组合算子
  validation.py       字段、arity、窗口、未来函数、复杂度
  compiler.py         表达式 -> date x symbol 因子矩阵
  evaluation.py       long-short 评估、成本、换手、容量、next-open
  data.py             DataInterfaceGateway 数据加载与 locked holdout
  baselines.py        101 / Alpha158 代表性子集
  benchmarks.py       Phase 0 基准评估
  trials.py           trial JSONL、BH-FDR
  red_team.py         分半、置换、成本、容量、尾部审查
  search.py           语法生成 + 变异 + 开发集搜索 + memory 读取/写入
  confirm.py          locked holdout 一次性确认 + memory 记录
  memory.py           attempts / edge_cards / failures / discoveries / summary
  factor_library.py   版本化 factor artifact（factor.dsl / spec / test / manifest）
  campaign.py         多轮 campaign 状态机与停止规则
  runner.py           factor artifact -> data_bundle（只读 data_interface）
  research_runtime.py 研究执行 + deterministic evaluator
  supervisor.py       CampaignController 的硬限制封装
  ml_branch.py        非 DL ML：plan / purged walk-forward / ridge / model card
  discovery.py        Web Gateway discovery_map 校验与 Memory 写入
  roles.py            5 个 LLM 角色 + 确定性角色契约
```

## CLI

```powershell
# 静态校验
python -m factor_lab alpha validate --expression "rank(ts_corr(rank(close), rank(volume), 20))"

# 单因子评估
python -m factor_lab alpha eval --codes output/factor_lab/hypotheses/pool200.txt `
  --start 2022-10-01 --end 2025-10-31 `
  --expression "rank(ts_corr(rank(close), rank(volume), 20))"

# Phase 0 人类基准
python -m factor_lab alpha benchmark --codes output/factor_lab/hypotheses/pool200.txt `
  --start 2022-10-01 --end 2025-10-31

# 锁定 holdout
python -m factor_lab alpha holdout --end 2026-11-01 --save

# 开发集搜索；自动读取 memory 去重，写入 attempts / edge card
python -m factor_lab alpha search --codes output/factor_lab/hypotheses/pool200.txt `
  --start 2022-10-01 --end 2025-10-31 --budget 120

# holdout 确认
python -m factor_lab alpha confirm --codes output/factor_lab/hypotheses/pool200.txt `
  --search output/factor_lab/alpha/search/ALPHA-....json

# 研究记忆
python -m factor_lab alpha memory
python -m factor_lab alpha trials

# 版本化 Factor Library
python -m factor_lab alpha library

# Campaign
python -m factor_lab alpha campaign --objective "研究应计" --max-rounds 5
python -m factor_lab alpha campaign --campaign-id CAMP-...
python -m factor_lab alpha campaign --campaign-id CAMP-... --round-file round.json

# ML Branch（非 DL；使用现有 panel 字段）
python -m factor_lab alpha ml --codes output/factor_lab/hypotheses/pool200.txt `
  --start 2022-10-01 --end 2025-10-31 --features close,volume,amount

# Web Gateway discovery 接入
python -m factor_lab alpha discovery --discovery-file discovery.json

# 角色契约
python -m factor_lab alpha roles
```

## LLM 角色

标准 5 个：

```text
PathFinder
Web Gateway
ML Planner
FactorBuilder
Reader
```

Runner、Research Runtime、Evaluator、Supervisor、Falsifier、Registrar、Allocator、ML Runtime 是确定性/工具层。

## 研究记忆闭环

```text
search 启动
  → AlphaMemory.known_expressions()
  → 跳过已尝试表达式
  → 每个新表达式写 attempts
  → 失败写 failures
  → top candidate 写 edge_cards
  → summary.json
  → 下一次 search 读取
```

`run_search(..., memory=AlphaMemory(path))` 可注入隔离记忆，测试使用临时目录。

## 版本化 Factor Library

生成物是运行时研究数据，写在：

```text
output/factor_lab/factors/generated/<family>/<factor_id>/v1/
  factor.dsl | factor.py
  spec.json
  test_factor.py
  manifest.json
```

- `factor_id` 稳定
- `v1/v2/...` 不可覆盖
- manifest 记录 `code_hash`、`data_contract`、`parent_version`
- `search` 自动为 top candidate 写 artifact，Edge Card 记录 `factor_ref`

## Campaign 控制

`CampaignController` 写：

```text
output/factor_lab/alpha/campaigns/<campaign_id>/
  campaign.json
  campaign_trace.jsonl
  stop_decision.json
  rounds/round-001.json
```

硬限制：

- `max_rounds`（1–20，默认 5）
- budget
- duplicate research
- max rounds / budget / pause / stop decision

Supervisor 只是确定性封装，不替 PathFinder 做研究判断。

## ML Branch

- 非 Deep Learning：Ridge / zero baseline（后续可扩展树模型）
- `ml_plan`、`data_bundle`、`ml_result`、`model_card`
- purged walk-forward + embargo；禁止 random split
- 目标为 forward return；signal evidence 不等于部署批准
- 产物：

```text
output/factor_lab/alpha/ml/<plan_id>.plan.json
output/factor_lab/alpha/ml/<plan_id>.bundle.json
output/factor_lab/alpha/ml/<plan_id>.result.json
output/factor_lab/alpha/ml/<plan_id>.model_card.json
```

## 数据轴

`AlphaContext.from_panel` 接收 Panel 全部矩阵字段：

```text
OHLCV / amount / turnover
moneyflow / margin
valuation
financial statements（按 publish_date PIT，fin_*）
```

## 规则

- 搜索只使用开发集；holdout 锁定 12 个月。
- 表达式必须通过 validation；未知字段、负窗口、未来算子拒绝。
- 每个进入评估的表达式都写入 trial + memory。
- Red Team 不通过不能进入 candidate。
- 研究证据使用 long-short；部署仍只做 long-only；8 只持仓/20% DD 不参与研究统计。
- 未 validated 的 Edge Card 不进账户；默认持有宽基指数。

## 输出

```text
output/factor_lab/alpha/
  holdout.json
  trials.jsonl
  memory/
    attempts.jsonl
    edge_cards.jsonl
    failures.jsonl
    discoveries.jsonl
    summary.json
  benchmark/<benchmark_id>.json
  search/<search_id>.json
  confirm/<search_id>.json
  campaigns/<campaign_id>/
  ml/<plan_id>.*.json
```

## 评估口径

- 入场：信号日 t 的因子值，t+1 开盘成交。
- 持有：`horizon` 个交易日，非重叠 rebalance。
- 成本：换手 × 双边成本。
- 容量：`10% × 顶组成交额中位数 × horizon / ¥50m`，是显式假设。
- 主指标：long-short 净价差 / IC；部署需要单独的 long-only 检查。
- 尾部稳健性：winsorized / trimmed 净价差。
