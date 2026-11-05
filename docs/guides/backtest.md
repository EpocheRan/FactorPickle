# Backtest（RQAlpha）

> 设计见 [../design/backtest_architecture.md](../design/backtest_architecture.md)（v3）。
> 本文只说明当前实现状态与用法。

## 状态

| 能力 | 状态 |
|---|---|
| QuestDB 只读数据源（`backtest/infrastructure/data/questdb.py`） | ✅ |
| RQAlpha 6.5.x 引擎（`rqalpha_engine.py`） | ✅ |
| `DataBundle → RQAlpha 日线源`（`rqalpha/source.py`） | ✅ |
| 策略适配（`init / open_auction / handle_bar`） | ✅ 单股 + 横截面 |
| A 股规则（T+1 / 整手 / 涨跌停 / 停牌 / 费用） | ✅ |
| 横截面（explicit universe + 候选集） | ✅ |
| 自建账本 + 对账（诊断） | ✅ |
| 输出 console / csv / html | ✅ |
| CLI（`list/validate/run/research/optimize`） | ✅ |
| `all_a / index / file` universe | ✅ `all_a`/`index` 由数据源 PIT 解析；`file` 为显式池 |
| 分红送转 / 复权 | ⏳ v1 未接，用原始价 |
| 分钟线 / 日内止损 | ⚠️ 引擎支持 1m + `next_bar`；真实数据仅 2 只 × 1 月 |
| benchmark（000300/000852） | ✅ `type='idx'` 已接；无数据时 `benchmark_used=False` |

横截面是“混合架构”：向量化层负责全 universe 排名/选股/权重，RQAlpha 只执行候选集。

执行约定：

- 策略在 `handle_bar`（T 收盘）只**排队**订单，`open_auction`（T+1 开盘）提交；
- 止损在 `handle_bar` 按收盘价触发，T+1 开盘卖；买入当天不卖（`sellable=0`）；
- 非调仓日只做 buffer 退出（`rank > top_n + buffer`）；
- 调仓日先卖后买，卖出回款计入买入预算；
- `equity/metrics` 用 RQAlpha 组合；自建账本只做对账诊断。

## 安装

RQAlpha 是 optional extra，MCP 运行时不加载：

```bash
uv sync --extra backtest
```

## 快速开始

```bash
# 列出策略
python -m backtest list

# 校验策略文件
python -m backtest validate strategies/models/atr_trend.py

# 单股回测
python -m backtest run strategies/models/atr_trend.py \
    --start 2024-03-01 --end 2024-06-28 --data questdb --out console

# 横截面回测（200 只固定池，月度调仓）
python -m backtest run strategies/models/turnover_regime.py \
    --start 2024-01-01 --end 2024-03-29 --data questdb --out console

# 落盘：output/backtest/<run_id>/
python -m backtest run strategies/models/atr_trend.py \
    --start 2024-03-01 --end 2024-06-28 --out console,csv

# 网格优化；加 --windows 2 即 walk-forward
python -m backtest optimize strategies/models/atr_trend.py \
    --start 2024-03-01 --end 2024-12-31 \
    --grid k=1.0,2.0,3.0 --metric sharpe
```

## 性能

横截面只把 `CrossPlan.candidates` 交给 RQAlpha；全 A 5000+ 只全订阅会把事件循环放大
50–200 倍。实测（本机，all-A 5187/5587 只）：

| 区间 | 耗时 |
|---|---:|
| 2024-01（1 个月） | ~1–2s |
| 2024 全年 | ~36s |
| 2020–2026（6.8 年） | ~73s |

另外两处固定开销：股票查询用 id/symbol 映射（O(1)），matplotlib 字体缓存指到可写目录。

## 缓存

`BacktestSettings.cache=True`（默认）会按「数据版本 + 股票池 + 因子声明」缓存
`(DataBundle, FactorPanel)` 到 `output/cache/backtest/`：

- QuestDB 版本 = 表级 `max(ts)`；内存源 = snapshot hash；
- `cache_max_symbols=500`，全 A 池默认跳过（pickle 太大）；
- 同一数据下只改 `top_n` / `weighting` 会命中缓存；改因子、日期、股票池会失效；
- stored 因子更新后需手动清缓存：`rm -rf output/cache/backtest`。

实测 200 只池、2020–2026：首次 19.0s，命中缓存 16.2s；剩余时间主要在
RQAlpha 事件循环与账本，数据/面板已不是瓶颈。

## 写策略

策略文件只定义模块级 `STRATEGY`（类或工厂）：

```python
from factors import F
from strategies.base import CrossSectionStrategy
from strategies.universe import Rebalance, Universe

class RankMom20(CrossSectionStrategy):
    name = "rank_mom20"
    universe = Universe.explicit(["600519", "000001", "300750"])
    top_n = 3
    buffer = 1
    rebalance = Rebalance.WEEKLY

    def factors(self):
        return [F.mom(20, on="hfq_close")]

    def rank(self, panel, dt):
        return panel.at(dt, "mom20")

STRATEGY = RankMom20
```

单股策略实现 `on_bar(ctx)`，用 `ctx.target_weight(...)` / `ctx.set_stop(...)`；
横截面实现 `rank(panel, dt)`，开仓由 `CrossSectionPlan` 统一执行。

**横截面执行**：只在调仓日换仓；非调仓日不因排名漂移清仓
（旧版 `buffer_out` 日频清仓已移除，`buffer` 只在调仓日保留旧仓）。

## 引擎配置

`BacktestSettings` 关键字段：

| 字段 | 默认 | 说明 |
|---|---|---|
| `engine` | `rqalpha` | 当前唯一实现 |
| `fill` | `next_open` | 适配器固定走 T+1 开盘 |
| `rebalance` | `weekly` | 横截面调仓频率 |
| `initial_cash` | 1,000,000 | 初始资金 |
| `max_gross_exposure` | 0.98 | 总仓位上限 |
| `warmup_min_bars` | 260 | 因子预热 |
| `benchmark` | `000300` | 无数据时自动关闭 |
| `rules` | `ExecutionRules` | T+1、涨跌停、费率、印花税 |

RQAlpha 侧配置在 `rqalpha/engine.py::_config`：`sys_simulation` 开启涨跌停/停牌限制，
`sys_transaction_cost` 关闭并由 `FinBridgeCostDecider` 接管，`sys_analyser` 只收集结果不画图。

## 输出

默认 `outputs=("console","html")`：每次回测都会写
`output/backtest/<run_id>/report.html` 和 `report_data.json`。
报告是单文件离线 HTML，不依赖 CDN/Plotly，含 7 个可交互 Tab：

| Tab | 内容 |
|---|---|
| 概览 | 净值 vs 000300、回撤、Alpha/Beta KPI |
| 绩效 | 月度热力、年度收益、滚动 Alpha/Beta、日收益散点 |
| 风险 | 风险指标、最大回撤事件 |
| 交易 | 收益分布、FIFO 回合交易表（筛选/排序/分页/CSV） |
| 暴露 | 总仓位/现金 |
| 归因 | 个股贡献、最新选股 |
| 明细 | 运行配置、下载 report_data.json |

`report_data.json` 是数据契约：`equity / benchmark / alpha_beta / risk / monthly / rolling /
drawdowns / exposure / concentration / trades / trade_summary / pnl_bridge / checks / attribution`。

报告还包含：

- **数据自检**：回合表=汇总、卖出股数配平、交易覆盖期末、已实现/未实现桥残差、
  持仓只数、仓位上限、卖出原因；
- **收益桥**：已实现 + 未实现 − 未平仓费用 + 残差 = 期末权益；
- **费用/换手**：总费用、年化费用 bps、年化单边/双边换手；
- **风险**：Sortino、Calmar、下行波动、VaR95/CVaR95、偏度/峰度、月度胜率、12M/36M；
- **归因**：个股贡献 Top/Bottom/合计、全量选股历史；
- **R²<0.5** 时 Alpha 卡给出“基准解释力不足”警告；
- **口径表**：年化/Sharpe/IR/超额/捕获率/牛熊β/回合/费用定义。

报告还接入：行业暴露/行业贡献、000852 对比、滚动 12M/36M、70/30 样本外、
HAC Alpha t、成本敏感性、选股前瞻收益、集中度（占权益/占持仓双口径）。
`--out console,csv` 可关闭 HTML；`python scripts/report_backtest.py --latest` 可从
`report_data.json` 重新渲染。

`--out csv` 写 `output/backtest/<run_id>/`：

```
metrics.json       # metrics + meta + factor_meta
equity.csv         # date/cash/market_value/equity/drawdown
orders.csv         # type/symbol/date/shares/fill_price/fees/status
trades.csv         # RQAlpha 原始成交
selections.csv     # 横截面每日 score/rank/weight
rejected.csv       # 涨跌停/资金不足等拒单
unfilled.csv
corporate_actions.csv
```

`meta["ledger_reconciliation"]` 是诊断项：自建账本与 RQAlpha 组合的对账；
长区间因退市/长期停牌股会有约 1.9% 偏离，不影响报告口径（报告用 RQAlpha 组合）。
`meta["ledger_reconciliation_common"]` 只比较两边都持有的 `(date, symbol)`，
正常区间应接近 0；它用来区分“会计差异”和“退市/换股数据缺口”。

## 已知边界

- **复权/分红送转未接**：引擎用原始价撮合与估值，长周期持有跨除权日会有偏差；
- **没有日内止损**：日线只能做“收盘触发、次日开盘卖”；
- **成交量上限关闭**：QuestDB `volume` 单位需先校准；
- **指数覆盖**：只有已入库的指数能作为 benchmark；000852 等缺失时 `benchmark_used=False`；
- **分钟线/日内止损**：引擎已支持 1m + `next_bar`（mock 测试 `tests/test_rqalpha_minute.py`）；
  真实库只有 2 只 × 约 1 个月的 5m，15m 为空；先用 `scripts/fetch_minute_pilot.py` 做试点；
- **横截面仅 explicit universe**：固定池有幸存者偏差，PIT 成分是下一步；
- **Python 3.12**：RQAlpha 6.5.0 可装；升级前先跑 `pytest tests/test_rqalpha_engine.py -q`。

## 测试

```bash
pytest tests/test_rqalpha_engine.py -q      # 单股/横截面/涨停拒单
pytest tests/test_backtest_*.py -q          # 规则、账本、输出、配置
pytest tests/test_no_pybroker_import.py -q  # 防止 PyBroker 回流
```
