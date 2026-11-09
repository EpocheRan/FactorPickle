# 07 · backtest（回测引擎）

## 1. 架构与依赖

### 1.1 职责边界

`backtest` 是**唯一的撮合与资金曲线实现**：给定策略 + 行情 + 因子，产出订单、成交、持仓、
净值与绩效指标。它保证 A 股规则：T+1、整手、涨跌停不可成交、停牌、印花税/佣金/过户费。

- **执行引擎**：RQAlpha 6.5.x（optional extra `backtest`，MCP 运行时不加载）。
- **数据**：`data_interface` 已载入的 `DataBundle`；引擎不直接访问 QuestDB。
- **规则**：自建 `a_share.py`（涨跌停价、整手、费用、T+1 台账）。
- **净值**：`equity/metrics` 以 RQAlpha 组合为准；自建 `PortfolioLedger` 保留为对账 oracle。

### 1.2 上下游

| 方向 | 模块 | 关系 |
|---|---|---|
| 上游 | `strategies` | 策略类 / `STRATEGY` 模块变量 |
| 上游 | `factors` | 因子面板（`build_panel`） |
| 上游 | `factor_engine` | 读 STORED 因子 |
| 上游 | `data_interface` | 行情、交易日历、证券状态 |
| 下游 | `output/reports/backtest/<生成日>/<run_id>/` | orders/trades/equity/metrics |

### 1.3 关键文件

| 文件 | 作用 |
|---|---|
| `application/runner.py` | `BacktestRunner`：`run` / `optimize` / `walkforward` |
| `application/cross_section.py` | 向量化选股 → 调仓日 targets + 每日 ranks |
| `application/rebalance.py` | 调仓日期门控 |
| `application/registry.py` | 数据源 / 输出 / 引擎注册（当前只有 `rqalpha`） |
| `infrastructure/engine/rqalpha/engine.py` | `RQAlphaEngine`：`EngineRequest → BacktestResult` |
| `infrastructure/engine/rqalpha/source.py` | `DataBundle` → RQAlpha 日线数据源 |
| `infrastructure/engine/rqalpha/adapter.py` | 策略回调：T 日排队、T+1 开盘提交、止损 |
| `infrastructure/engine/rqalpha/cost.py` | `FeeModel` → RQAlpha 费用接口 |
| `infrastructure/engine/rqalpha/mod.py` | 把数据源 / 费用注入 RQAlpha |
| `infrastructure/execution/a_share.py` | 涨跌停价、整手、T+1、费用 |
| `infrastructure/execution/ledger.py` | `PortfolioLedger`、`reconcile`、指标 |
| `domain/models.py` | `RunSpec` / `ExecutionRules` / `BacktestResult` |

### 1.4 数据流

```
BacktestRunner.run(strategy_path)
   ├─ loader 加载 STRATEGY → BaseStrategy
   ├─ _resolve_universe（explicit / all_a / index）
   ├─ 取行情 + 因子（stored 优先，AUTO 回退 compute）
   ├─ 横截面：CrossSectionPlan（targets / ranks / candidates）
   ├─ RQAlphaEngine.run(EngineRequest)
   │    ├─ build_instruments / RQAlphaDataSource
   │    ├─ run_func(init / open_auction / handle_bar)
   │    └─ trades → orders → PortfolioLedger → metrics
   └─ 输出 console / csv
```

## 2. 核心类与方法

### 2.1 类表

| 类 | 关键成员 | 说明 |
|---|---|---|
| `BacktestRunner` | `run(path)` / `optimize(path)` / `walkforward(path)` | 单次 / 网格 / 滚动 |
| `BacktestSettings` | `from_dict(payload)`；`engine` 默认 `rqalpha` | 配置与校验 |
| `RunSpec` | dataclass | 一次运行的完整规格 |
| `ExecutionRules` | `lot_size/t_plus_1/limit_up_down/suspension/commission_rate/stamp_tax_schedule` | 撮合规则 |
| `RQAlphaEngine` | `run(request) -> BacktestResult` | `EnginePort` 实现 |
| `RQAlphaDataSource` | `get_bar/history_bars/is_suspended/...` | DataBundle → RQAlpha |
| `StrategyAdapter` | `init/open_auction/handle_bar/after_trading` | 策略回调与订单排队 |
| `FinBridgeCostDecider` | `calc(args) -> TransactionCost` | 佣金/印花税/过户费 |
| `PortfolioLedger` | 持仓、现金、成交 | 多标的账本 |

关键默认值：`fill="next_open"`、`rebalance="weekly"`、`warmup_min_bars=260`、
`max_gross_exposure=0.98`、`commission_rate=0.00025`、印花税 2023-08-28 起 5bp。

### 2.2 用法

```bash
python -m backtest list
python -m backtest run strategies/models/atr_trend.py \
    --start 2024-03-01 --end 2024-06-28 --out console
python -m backtest run strategies/models/turnover_regime.py \
    --start 2024-01-01 --end 2024-03-29 --out console
```

```python
from backtest import BacktestRunner, BacktestSettings

settings = BacktestSettings.from_dict({
    "start": "2024-01-01", "end": "2024-03-29",
    "fill": "next_open", "engine": "rqalpha",
})
runner = BacktestRunner(settings)
result = runner.run("strategies/models/turnover_regime.py")
```

### 2.3 时序（重要）

RQAlpha 日频的 `matching_type="current_bar"` 会用**当日收盘价**撮合。为避免前视：

1. `handle_bar`（T 日收盘）：只算信号/止损，把订单放进 `runtime.pending`；
2. `open_auction`（T+1 开盘）：提交 pending，按 T+1 开盘价成交；
3. `handle_bar`（T+1 收盘）：再排下一批。

T+1 由 RQAlpha 股票账户（`market_tplus=1` + `stock_t1=True`）强制执行；
适配器在卖单前再按 `position.sellable` 截断一次。

**分钟模式**：`RunSpec.freq="1m"` 时 `handle_bar` 每 1m 调用，订单在下一根 1m 开盘成交
（`matching_type="next_bar"`）；止损看 `bar.low`；T+1 由适配器 `entry_dates` 执行，
`sys_accounts.stock_t1` 关闭以规避 RQAlpha 1m 结算的 `sellable` 问题。

### 2.4 注意事项（坑）

| 坑 | 说明 |
|---|---|
| **不要用 `handle_bar` 直接下单做次日开盘** | 日频会按当日收盘撮合，形成前视；必须走 `open_auction` |
| **必须 `subscribe` 股票池** | 否则 `bar_dict` 为空，`handle_bar` 读不到 bar |
| **没有原生止损单** | 止损在 `handle_bar` 里按收盘价触发、T+1 开盘卖；不是日内止损 |
| **复权未接** | 引擎用原始价撮合/估值；分红送转暂不处理 |
| **成交量上限关闭** | QuestDB 的 `volume` 单位需先校准，`volume_limit=False` |
| **benchmark 不在 RQAlpha 内** | 由 `equity_metrics` 用 `request.benchmark` 计算；无数据时 `benchmark_used=False` |
| **Python 3.12** | RQAlpha 6.5.0 可装；升级前先跑 `tests/test_rqalpha_engine.py` |
| **matplotlib 缓存警告** | 无写权限时会打印，不影响回测 |

### 2.5 结果口径

- `equity` / `metrics` 来自 RQAlpha 组合；`metrics_source="engine"`；
- `orders` / `trades` 来自 RQAlpha 成交，转换为 FinBridge 列名；
- `meta["ledger_reconciliation"]` 是诊断项：账本 vs RQAlpha 组合的相对误差；
  2024Q1 横截面实测 `max_rel_diff ≈ 4.7e-11`；长区间因退市股约 1.9%，不影响报告口径。
- `meta["ledger_reconciliation_common"]` 只比较两边都持有的 `(date, symbol)`，
  用来区分“会计差异”和“数据缺口”；2024Q1 实测 `max_abs_diff ≈ 7e-12`。
