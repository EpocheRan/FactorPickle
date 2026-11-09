# 回测架构

> 以 RQAlpha 6.5.x 为唯一执行引擎，保留 `EnginePort`、自建 A 股规则、自建账本与对账。

## 1. 目标与边界

**目标**：给 FactorPickle 的策略提供一条可复现的 A 股回测链路，满足：

- T+1、涨跌停、整手、停牌、佣金/印花税/过户费；
- 单股 `on_bar` 与横截面 `rank/weights` 两种策略；
- 逐笔止损/移动止损（收盘触发、T+1 开盘执行）；
- 输出 `equity/orders/trades/metrics`，并能与引擎组合对账。

**不做**：分钟/tick、日内止损、融资融券、期权/期货、分红送转、PIT 成分。

## 2. 分层

```
BacktestRunner（application）
  ├─ loader / universe / factors / cross_section
  └─ EnginePort
        └─ RQAlphaEngine（infrastructure/engine/rqalpha）
              ├─ RQAlphaDataSource   DataBundle → 日线 bars
              ├─ StrategyAdapter     FactorPickle 策略 → RQAlpha 回调
              ├─ FactorPickleCostDecider FeeModel → RQAlpha 费用
              └─ FactorPickleMod       把数据源/费用注入 RQAlpha
```

依赖方向：

- `backtest/application` 只依赖 `domain`、`strategies`、`factors`；
- `backtest/infrastructure/engine/rqalpha` 是唯一 import `rqalpha` 的地方；
- `factors` / `strategies` 不 import RQAlpha、QuestDB、`data_interface`；
- `PortfolioLedger` 只消费成交，不依赖引擎。

## 3. 时序：T 收盘排队，T+1 开盘成交

RQAlpha 日频的 `matching_type="current_bar"` 会用**当日收盘价**撮合。直接
`handle_bar` 下单会形成“用收盘价决定、又用收盘价成交”的前视。因此采用两段式：

```
T 日 15:00  handle_bar
  ├─ 读 T 日 bar（close/high/low/volume）
  ├─ strategy.on_bar(ctx) 或 cross_plan.target_for(T)
  ├─ 止损检查（收盘价 <= stop 且 sellable > 0）
  └─ 订单写入 runtime.pending（不提交）

T+1 09:25   open_auction
  ├─ 检查涨跌停（open >= limit_up 的买单、open <= limit_down 的卖单 → 拒）
  ├─ order_shares(pending)
  └─ 清空 pending

T+1 15:00  handle_bar
  └─ 重复下一轮
```

T+1 由 RQAlpha 股票账户强制执行：`Instrument.market_tplus=1` +
`sys_accounts.stock_t1=True`；适配器在卖单前再按 `position.sellable` 截断一次。

## 4. 规则映射

| 规则 | 实现位置 | 说明 |
|---|---|---|
| T+1 | `sys_accounts.stock_t1` + `market_tplus=1` | 当日买入不可卖 |
| 涨跌停 | `RQAlphaDataSource.limit_up/down` + `sys_simulation.price_limit` | 按前收与板块规则算，拒单 |
| 整手 | `Instrument.round_lot` + `order_shares` | 主板/创业板 100；科创板 1（200 起由下单层保证） |
| 停牌 | `sys_simulation.inactive_limit` + `is_suspended` | 成交量为 0 不成交 |
| 佣金/印花税/过户费 | `FactorPickleCostDecider` | 复用 `FeeModel`，印花税按日期切换 |
| 滑点 | `sys_simulation.slippage=0` | 暂不计；后续可配 |
| 成交量上限 | 关闭 | QuestDB `volume` 单位待校准 |

## 5. 数据模型

`DataBundle.frame` 是长表：`symbol/date/open/high/low/close/volume/amount/adj_factor/hfq_close`。

`RQAlphaDataSource` 为每个 `order_book_id` 建一个结构化数组：

```
datetime(u8) open high low close volume total_turnover
limit_up limit_down prev_close hfq_close settlement open_interest
```

- `datetime` 用 `YYYYMMDD` 整数，`searchsorted` 取 bar；
- `prev_close` 用 `shift(1)`；`limit_up/down` 用 `a_share.limit_pct`；
- `hfq_close` 供 `StrategyContext.hfq_close`（因子信号用）；
- 复权/分红/拆股暂不处理，用原始价撮合与估值。

`Instrument` 由 `build_instruments` 生成：交易所后缀 `.XSHG/.XSHE/.BJSE`、
`board_type`、`market_tplus=1`、`special_type=ST/Normal`。

## 6. 策略 API

| FactorPickle | RQAlpha 侧 | 说明 |
|---|---|---|
| `SingleSymbolStrategy.on_bar(ctx)` | `handle_bar` 里逐 bar 调用 | `ctx` 为 `StrategyContext` |
| `ctx.target_weight` | 排队买单/卖单，T+1 开盘 `order_shares` | 目标权重 × 权益 / close |
| `ctx.buy_shares/sell_shares/sell_percent/close_position` | 同上 | 卖单按 `sellable` 截断 |
| `ctx.set_stop` | `runtime.stops` | 收盘触发、T+1 开盘卖 |
| `CrossSectionStrategy.rank` | `CrossSectionPlan` 预计算 | 引擎只执行 `target_for(dt)` |
| `buffer` 退出 | 非调仓日 `rank > top_n + buffer` | 用 `plan.rank_at(dt, code)` |

横截面不做 `on_bar` 买入；开仓统一由 `CrossSectionPlan` 的 targets 驱动。

## 7. 止损

只做**收盘价止损**：

- `handle_bar` 读 T 日收盘；
- 若 `close <= stop` 且 `position.sellable > 0`，排队卖单；
- T+1 `open_auction` 按开盘价成交（可能是跳空价，不是止损价）；
- 买入当天 `sellable=0`，不会卖；次日再判。

这是对 T+1 的诚实近似：**不是日内止损**。要精确日内止损，需要分钟线或自定义
`AbstractBroker`，后续扩展。

### 7.1 分钟模式（1m）

- `RunSpec.freq="1m"` → `RQAlphaDataSource(freq="1m")`，bar key 为 `YYYYMMDDHHMMSS`；
- `matching_type="next_bar"`：`handle_bar` 里下的单在下一根 1m 开盘成交；
- 止损看 `bar.low`，成交在下一根 1m 开盘；
- T+1 由适配器 `entry_dates` 执行（`sys_accounts.stock_t1=False`，规避 RQAlpha 1m
  结算的 `sellable` 问题）；
- `equity` 仍来自 `sys_analyser` 的日频快照，`trades/orders` 是分钟时间戳；
- 真实分钟数据不足，`tests/test_rqalpha_minute.py` 用 mock 1m 验证机制。

## 8. 结果与指标

- `trades/orders`：从 `sys_analyser["trades"]` 转换；一笔成交一条 order；
- `equity`：RQAlpha `portfolio` 的 `cash/market_value/total_value`；
- `equity/metrics`：RQAlpha 组合直接出，`metrics_source="engine"`；
- `meta["ledger_reconciliation"]`：账本 vs RQAlpha 组合的诊断误差。
- `meta["ledger_reconciliation_common"]`：逐日逐仓只比较两边都持有的 `(date, symbol)`，
  用来区分会计差异与数据缺口。

2024Q1 横截面实测 `max_rel_diff ≈ 4.7e-11`；2020–2026 长区间因退市/换股股
存在约 1.9% 的全仓偏离（见 §10），**只作诊断，不进入报告**。
共同持仓对账 `max_abs_diff < 1e-6`，说明账本会计本身与 RQAlpha 一致。

## 9. 测试与验收

- `tests/test_rqalpha_engine.py`：单股、横截面、涨停拒单；
- `tests/test_backtest_execution.py`：涨跌停/整手/费用/T+1 单元；
- `tests/test_backtest_ledger.py`：账本与对账；
- `tests/test_no_pybroker_import.py`：防止 PyBroker 回流；
- 基线烟雾：`turnover_regime` 2020–2026，年化 8.45%、Sharpe 0.54、MaxDD -23.5%，
  与旧 PyBroker 基线 8.23%/0.54/-23.2% 基本一致。

## 10. 已知边界与后续

| 边界 | 影响 | 后续 |
|---|---|---|
| 复权/分红送转未接 | 跨除权日净值偏差 | 接 `get_dividend/get_split` 或改用复权价 |
| 退市/换股/停牌 | 全仓对账 ~1.9%，共同持仓对账 ~7e-12；差异来自数据缺口，不是会计错误 | 接 `share_transformation` / `dividend` 数据 |
| 成交量上限关闭 | 大资金容量高估 | 校准 volume 单位后开 `volume_limit` |
| 指数覆盖不足 | 000852 等未入库时 alpha/beta 不计算 | 补指数数据 |
| 分钟线/日内止损 | 引擎已支持 1m + `next_bar`；真实数据仅 2 只 × 1 月 | 先跑 pilot，再扩 5m/15m |
| 横截面 explicit 池 | 幸存者偏差 | PIT 成分池 |
| 无日内止损 | 止损按次日开盘 | 分钟线 + 自定义 Broker |
