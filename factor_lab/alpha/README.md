# Alpha DSL / Beyond Human

本目录实现 Factor Lab 的 Alpha DSL、Program Search 与 Red Team。

## 当前能力

```text
factor_lab/alpha/
  expression.py   表达式解析、IR、canonical rendering
  operators.py    时序/横截面/条件/组合算子
  validation.py   字段、arity、窗口、未来函数、复杂度
  compiler.py     表达式 -> date x symbol 因子矩阵
  evaluation.py   forward return、IC、分组价差、成本、换手、容量
  data.py         DataInterfaceGateway 数据加载与 holdout
  baselines.py    101 / Alpha158 代表性子集（人类基准）
  benchmarks.py   Phase 0 基准评估
  trials.py       trial 记录、BH-FDR
  red_team.py     分半、置换、成本、容量审查
  search.py       语法生成 + 变异 + 开发集搜索
```

## CLI

```powershell
# 静态校验
python -m factor_lab alpha validate \
  --expression "rank(ts_corr(rank(close), rank(volume), 20))"

# 单因子评估
python -m factor_lab alpha eval \
  --codes output/factor_lab/hypotheses/pool200.txt \
  --start 2022-10-01 --end 2025-10-31 \
  --expression "rank(ts_corr(rank(close), rank(volume), 20))"

# 人类基准
python -m factor_lab alpha benchmark \
  --codes output/factor_lab/hypotheses/pool200.txt \
  --start 2022-10-01 --end 2025-10-31

# 默认 last-12-month holdout
python -m factor_lab alpha holdout --end 2026-11-01 --save

# 开发集搜索；可加 --seed-file 让 PathFinder/LLM 提供种子表达式
python -m factor_lab alpha search \
  --codes output/factor_lab/hypotheses/pool200.txt \
  --start 2022-10-01 --end 2025-10-31 \
  --budget 120

# 锁定 holdout 上的一次性确认
python -m factor_lab alpha confirm \
  --codes output/factor_lab/hypotheses/pool200.txt \
  --search output/factor_lab/alpha/search/ALPHA-....json

# trial 汇总
python -m factor_lab alpha trials
```

## 数据轴

`AlphaContext.from_panel` 接收 `Panel` 的全部矩阵字段：

```text
OHLCV
moneyflow / margin
valuation 全字段
financial statements（按 publish_date PIT，fin_* 前缀）
```

财务字段由 `DataInterfaceGateway.financial_bundle` 按 `publish_date` 建矩阵，
`AlphaContext` 对 `fin_*` 做 PIT forward-fill。没有 `publish_date` 的字段不会进入。

## 规则

- 搜索只使用开发集；holdout 默认锁定 12 个月。
- 任何表达式都必须通过 `validation.py`；未知字段、负窗口、未来算子直接拒绝。
- 表达式复杂度有上限；同等表现下简单表达式优先。
- 每个进入评估的表达式写入 `output/factor_lab/alpha/trials.jsonl`。
- Red Team 检查分半符号、置换 p 值、成本、换手、容量；不通过不能进入 candidate。
- 超参数、窗口、方向、成本在预注册后不因结果调整。

## 输出

```text
output/factor_lab/alpha/
  holdout.json
  trials.jsonl
  benchmark/<benchmark_id>.json
  search/<search_id>.json
```

`search` 文件包含 top 候选、FDR、Red Team 与数据 manifest。  
holdout 只用于最终确认，不参与候选生成或参数选择。
