# 策略回测 HTML 报告设计方案 v1

> 目标：所有策略回测默认产出一份**单文件、离线、可交互**的 HTML 报告；
> 报告必须回答“赚了多少、怎么赚的、风险在哪、相对基准有没有 alpha”。

## 1. 现状与缺口

当前 `scripts/report_backtest.py` 是原型：KPI + 净值 SVG + 月度表 + 成交统计，够看但不够研究。

| 缺口 | 影响 |
|---|---|
| `BacktestResult` 没有保存 benchmark 序列 | 报告只能显示 alpha/beta 数字，不能画基准对比、滚动 beta、超额曲线 |
| `trades` 是逐笔成交（fills），不是回合交易 | 算不了胜率、盈亏比、持仓天数、最大有利/不利波动 |
| 没有持仓/暴露序列 | 画不了仓位、行业暴露、集中度 |
| 没有统一报告数据契约 | 每个策略/脚本各写一份 HTML，口径漂移 |
| 没有交互 | 不能缩放、切换基准、点月份筛选、看单笔明细 |

## 2. 报告信息架构

单页 + 顶部 Tab，深色主题，对齐你给的 TradingView 风格。

```
┌ 标题：策略名 / 区间 / run_id / 基准 / 初始资金 / 生成时间
├ KPI：累计收益 · 年化 · 最大回撤 · Sharpe · Alpha · Beta
├ Tab：概览 | 绩效 | 风险 | 交易 | 暴露 | 归因 | 明细
└ 页脚：数据口径、费用假设、缓存/引擎版本
```

### 2.1 概览

- 净值 vs 基准（双线，可切换对数轴）
- 回撤面积图
- 超额收益曲线（策略 − 基准）
- KPI 卡片：累计、年化、MaxDD、Sharpe、Sortino、Calmar、Alpha、Beta、IR、胜率、盈亏比
- 最近 10 笔交易/最新持仓

### 2.2 绩效

- 年度收益柱状图（策略 vs 基准）
- 月度收益热力图（年 × 月，点击某月联动交易表）
- 滚动 60/120/250 日 Sharpe、Alpha、Beta
- 上涨/下跌捕获率
- 牛/熊/震荡分段收益与 beta

### 2.3 风险

- 最大回撤 Top5 区间（起止、深度、持续天数、修复天数）
- 波动率、下行波动、VaR95、CVaR95
- 回撤持续期分布
- 集中度：前 5 大持仓占比、HHI
- 换手率与费用拖累

### 2.4 交易

- 收益分布直方图（赢家/输家分色）
- 盈亏比、期望值、Profit Factor
- 连续盈利/亏损纪录
- 持仓天数分布
- 回合交易表：代码、方向、入场/出场日期与价格、股数、净盈亏、收益率、持仓天数、MAE/MFE、退出原因
- 可按代码/月份/盈亏筛选、排序、分页

### 2.5 暴露

- 总仓位、净仓位、现金比例时间序列
- 持仓数量时间序列
- 行业/板块暴露（需要行业映射）
- 单票最大权重、前十大权重变化

### 2.6 归因

- CAPM：Alpha、Beta、R²、Alpha t 值、跟踪误差、信息比率
- 超额收益分解：`beta × 基准超额 + alpha + 残差`
- 按行业 Brinson 归因（配置/选股/交互），Phase 3
- 个股贡献 Top/Bottom 10
- 费用/换手拖累

### 2.7 明细

- 每日净值表（可下载 CSV）
- 全部订单/成交
- 运行配置：engine、freq、fill、benchmark、费用、滑点、缓存命中
- 数据口径与已知边界

## 3. 数据契约

报告不直接读数据库；由 `ReportDataBuilder` 从 `BacktestResult` 生成一个 `report_data.json`，HTML 只消费这个 JSON。

```json
{
  "meta": {
    "run_id": "...", "strategy": "...", "mode": "cross",
    "start": "2020-01-01", "end": "2026-10-29",
    "initial_cash": 1000000, "engine": "rqalpha", "freq": "1d",
    "benchmark": "000300", "risk_free_rate": 0.0,
    "generated_at": "..."
  },
  "equity": [{"date": "...", "cash": 0, "market_value": 0, "equity": 0, "drawdown": 0, "ret": 0}],
  "benchmark": {"code": "000300", "series": [{"date": "...", "close": 0, "ret": 0}]},
  "metrics": {"total_return_pct": 0, "sharpe": 0, "max_drawdown_pct": 0},
  "alpha_beta": {
    "alpha_annualized_pct": 0, "beta": 0, "r2": 0, "alpha_t": 0,
    "tracking_error_pct": 0, "information_ratio": 0,
    "up_capture": 0, "down_capture": 0,
    "bull_beta": 0, "bear_beta": 0,
    "rolling": [{"date": "...", "window": 60, "alpha": 0, "beta": 0}]
  },
  "monthly": [{"year": 2024, "month": 1, "ret": 0, "bench_ret": 0}],
  "trades": [{"symbol": "...", "entry_date": "...", "exit_date": "...", "entry": 0,
              "exit": 0, "shares": 0, "pnl": 0, "ret": 0, "days": 0,
              "mae": 0, "mfe": 0, "reason": "rebalance"}],
  "exposure": [{"date": "...", "gross": 0, "net": 0, "cash": 0, "positions": 0}],
  "attribution": {"by_symbol": [], "by_industry": []},
  "selections": []
}
```

### 3.1 `BacktestResult` 需要补的字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `benchmark` | `pd.Series \| None` | 基准收盘序列；当前只在 metrics 里算了 beta，没存序列 |
| `round_trips` | `pd.DataFrame` | 回合交易；由 fills FIFO 配对 |
| `positions` | `pd.DataFrame` | 每日持仓快照（可选，暴露/归因用） |
| `report_data` | `dict \| None` | 可选，渲染层缓存 |

`benchmark` 必须进 `BacktestResult`，否则报告无法画“策略 vs 基准”和滚动 beta。

## 4. Alpha / Beta 口径

设策略日收益 `rp`、基准日收益 `rb`、无风险日收益 `rf = risk_free_rate / 252`。

```
beta        = cov(rp - rf, rb - rf) / var(rb - rf)
alpha_daily = mean(rp - rf) - beta * mean(rb - rf)
alpha_ann   = alpha_daily * 252
R²          = corr(rp, rb)²
TE          = std(rp - rb) * sqrt(252)
IR          = mean(rp - rb) / std(rp - rb) * sqrt(252)
上行捕获    = mean(rp | rb > 0) / mean(rb | rb > 0)
下行捕获    = mean(rp | rb < 0) / mean(rb | rb < 0)
```

补充：

- Alpha 的 t 值用 OLS 标准误；样本足够时可换 Newey-West；
- 牛/熊 beta：按基准 60 日趋势或区间收益符号分段，分别回归；
- 滚动窗口 60/120/250 日，报告可切换；
- 基准缺失时，Alpha/Beta 区域显示“未配置基准”，不显示 0；
- 默认基准 `000300`；小盘策略建议 `000852`，通过 `--benchmark` 指定。

## 5. 回合交易（FIFO）

当前 `trades` 是 RQAlpha 的 fills，一个回合可能被拆成多笔。报告层按 symbol 做 FIFO：

```
买入 lot 入队（shares, price, fees）
卖出按队列消耗，生成 round_trip：
  entry_price = 加权成本
  pnl = (exit - entry) * shares - 分摊费用
  ret = pnl / (entry * shares)
  days = exit_date - entry_date
  mae/mfe = 持仓期内最低/最高价相对 entry 的幅度
  reason = rebalance / stop / buffer / time_stop
```

输出字段见 §3。胜率、盈亏比、Profit Factor、连续纪录、持仓天数分布都由 `round_trips` 计算。

## 6. 交互设计

| 交互 | 说明 |
|---|---|
| 时间范围 | 1M / 3M / 1Y / 3Y / All + 图表刷选缩放 |
| 基准切换 | 报表内切换 000300 / 000852 / 自定义（若数据可用） |
| 对数轴 | 净值图切换线性/对数 |
| 图例 | 点击隐藏/显示策略、基准、回撤、超额 |
| Tooltip | 日期、净值、回撤、基准、超额、当日收益 |
| 月度热力图 | 点击某月 → 交易表过滤到该月 |
| 交易表 | 排序、按代码/盈亏/月份筛选、分页、点击行展开 |
| 滚动窗口 | 60/120/250 日切换 |
| 下载 | equity.csv / trades.csv / report_data.json |
| Tab | 概览/绩效/风险/交易/暴露/归因/明细，URL hash 记忆 |

## 7. 技术方案

### 7.1 渲染库选型（实施修订）

> 2026-11-05 实施时目标环境无法访问 PyPI 与 CDN，Plotly/ECharts 都拉不下来。
> v1 改为**零依赖 SVG + vanilla JS**：单文件离线、保留缩放/悬停/Tab/筛选/下载，
> 只是图表由手写 JS 绘制。数据契约与交互设计不变，后续网络可用时可平滑替换 ECharts。

| 方案 | 体积 | 交互 | 开发成本 | 结论 |
|---|---:|---|---|---|
| **零依赖 SVG/JS** | ~150KB/报告 | 缩放/悬停/图例/Tab/表格筛选 | 中 | **v1 已采用** |
| ECharts | ~1MB | 强，类 TradingView | 中 | 网络可用后的升级项 |
| Plotly | ~3.5MB（inline） | 缩放/悬停/图例/多图 | 低 | 原推荐，环境拉取失败 |

**v1 实现**：`backtest/reporting/render.py` 内嵌 JSON，用 SVG 画净值/回撤/滚动/散点/
年度/分布/暴露，用 HTML 表格做月度热力与交易表；不引用任何外部脚本。

### 7.2 模块划分

```
backtest/reporting/
  __init__.py
  data.py        # ReportDataBuilder：result -> report_data
  alpha.py       # CAPM / 滚动 / 捕获率
  trades.py      # FIFO 回合交易
  exposure.py    # 仓位/行业暴露
  render.py      # HTML 模板 + Plotly figures
  template.html  # CSS/JS/布局
  output.py      # HtmlReportOutput(OutputPort)
```

`HtmlReportOutput.emit(result)`：

1. `ReportDataBuilder.build(result)`；
2. 写 `output/backtest/<run_id>/report_data.json`；
3. 渲染 `output/backtest/<run_id>/report.html`；
4. 返回 `OutputArtifact(kind="file", path=...)`。

### 7.3 与引擎集成

- `pyproject.toml` 增加 optional extra：`report = ["plotly>=5.0"]`；
- `registry.py` 注册 `html` 输出；
- `BacktestSettings.outputs` 默认从 `("console",)` 改为 `("console","html")`；
- CLI：`--out console,html` 或默认带 html；
- MCP 不需要新工具，报告路径进 `meta["report_path"]`；
- 分钟模式：equity 仍是日频快照，trades 是分钟时间戳，报告照常；
- 未安装 plotly 时降级为现有静态 HTML（或提示 `uv sync --extra report`）。

## 8. 体积与性能

- 10 年日频：2500 个净值点、5000 笔成交、~2000 回合交易 → JSON 1–2MB；
- Plotly inline 3.5MB，单文件 4–6MB；
- 生成时间 < 1s（不含回测）；
- 如嫌大：v2 换 ECharts（1MB）或 `include_plotlyjs="cdn"`（需联网）。

## 9. 测试

- `test_alpha_beta`：构造已知 `rp/rb`，断言 beta/alpha/R²/IR；
- `test_round_trips_fifo`：分批买卖，断言回合数、成本、盈亏；
- `test_report_smoke`：生成 HTML，断言包含 7 个 Tab、无 `http://`/`https://` 外链、JSON 可解析；
- `test_report_benchmark_missing`：无基准时显示“未配置”，不报错；
- Golden 只比 `report_data.json` 的指标，不比 HTML 字符串。

## 10. 分阶段落地

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | `BacktestResult` 补 benchmark/round_trips/positions；FIFO + alpha/beta | 单测通过，数据契约冻结 |
| P1 | 概览/绩效/风险 三个 Tab + Plotly 净值/回撤/月度/滚动 | `atr_trend`、`turnover_regime` 报告可看 |
| P2 | 交易/暴露 Tab + 交易表筛选 | 回合交易与 fills 对账 |
| P3 | 归因 Tab + 行业 Brinson + 个股贡献 | 归因和总收益对账 |
| P4 | 默认 html 输出、文档、旧 `report_backtest.py` 下线 | 所有策略默认有报告 |

## 11. 待确认

1. **Plotly 还是 ECharts**？我倾向 v1 Plotly，v2 视体积再换。
2. **单文件 4–6MB 能否接受**？如果必须 <1MB，直接上 ECharts。
3. **默认基准**：统一 000300，还是按策略市值自动选 000300/000852？
4. **是否需要个股 K 线 + 买卖点标记**？需要的话报告要带每只股票的行情，体积和计算都会上一个量级。
5. **报告语言/主题**：中文深色（对齐截图），还是同时出浅色打印版？
