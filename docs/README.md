# FactorPickle 文档总索引

> 面向 **AI 与人类读者**。约定：
> - `modules/` 只描述**当前实现**；
> - `design/` 记录当前设计取舍；
> - `guides/` 是可直接照做的用法与运维约定；
> - 旧方案、迁移记录和历史版本不保留在文档树中，需要时查 git history。

## 一、目录结构

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `modules/` | 11 个模块的现状文档 | 设计争论、历史方案 |
| `design/` | 当前架构、因子库、物化、报告、回测、数据管线 | 逐步操作教程 |
| `guides/` | 回测用法、选股用法、`output/` 目录约定 | 模块 API 全量清单 |
| `data/` | 数据覆盖度报告 | 取数代码 |
| `index.html` | 交互式系统与数据模型图谱；GitHub Pages 首页 | 手工维护的表格 |

## 二、系统全景

FactorPickle 是 A 股量化研究平台，**QuestDB 是唯一存储**，所有模块围绕它读写。

```
                 ┌──────────────────────────────────────────────┐
                 │            acquisition（采集编排）            │
                 │  orchestrator / build_orchestrator / syncers  │
                 └───────────────────┬──────────────────────────┘
                                     │ 写入
                                     ▼
                 ┌──────────────────────────────────────────────┐
                 │  QuestDB：ohlcv_1d / ohlcv_5m / fin_* / ...  │
                 │  margin_detail / top_list / block_trade / ...│
                 │  concept_* / factor_value_* / index_weight   │
                 └───────────────────┬──────────────────────────┘
                                     │ 读取
                                     ▼
                 ┌──────────────────────────────────────────────┐
                 │        data_interface（数据访问层）           │
                 │ market / financial / moneyflow / sentiment /  │
                 │ concept / disclosure / api（工具封装）        │
                 └───────────────────┬──────────────────────────┘
                                     │
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │     factors      │   │   factor_engine   │   │     position     │
   │ 声明式因子/面板   │◄──│  物化/注册/服务    │   │   持仓与交易台账  │
   └────────┬─────────┘   └────────┬─────────┘   └──────────────────┘
            │                      │
            ▼                      ▼
   ┌────────────────────────────────────────────────────────────┐
   │  strategies（策略）→ backtest（回测）                        │
   │  screener（选股） / research（因子研究报告 + 评分卡）        │
   └───────────────────────────┬────────────────────────────────┘
                               ▼
                    MCPserver（对外工具接口）
```

## 三、模块索引

| # | 模块 | 一句话职责 | 文档 |
|---|---|---|---|
| 01 | `acquisition` | 数据采集编排：按数据集增量同步、断点续跑、构建调度 | [01-acquisition.md](modules/01-acquisition.md) |
| 02 | `data_interface` | 统一数据访问：行情/财务/资金流/情绪/板块/披露 + 工具封装 | [02-data-interface.md](modules/02-data-interface.md) |
| 03 | `factors` | 纯计算层：因子定义（DAG）、算子、横截面变换、面板构建 | [03-factors.md](modules/03-factors.md) |
| 04 | `factor_engine` | 因子物化层：注册表、批量计算、长表落库、审计 | [04-factor-engine.md](modules/04-factor-engine.md) |
| 05 | `research` | 因子报告：profile 章节、IC/分层/评级、敏感性、Panel 缓存、pandas/Polars metrics | [05-research.md](modules/05-research.md) |
| 06 | `screener` | 选股：快照构建、条件筛选、覆盖率与断点 | [06-screener.md](modules/06-screener.md) |
| 07 | `backtest` | 回测：RQAlpha 引擎 + A 股规则 + 横截面执行 | [07-backtest.md](modules/07-backtest.md) |
| 08 | `strategies` | 策略基类：单标的/横截面、股票池、权重、上下文 | [08-strategies.md](modules/08-strategies.md) |
| 09 | `position` | 持仓管理：组合/持仓/交易记录、成本与风控模式 | [09-position.md](modules/09-position.md) |
| 10 | `scripts` | 运维脚本：回填、修复、报告、评分卡、方向校准 | [10-scripts.md](modules/10-scripts.md) |
| 11 | `MCPserver` | MCP 工具服务器：注册各模块工具、传输与生命周期 | [11-mcp-server.md](modules/11-mcp-server.md) |
| — | `factor_lab` | 自主因子研究运行时：PathFinder / FactorBuilder / Runner / ML / Supervisor | [factor_lab.md](design/factor_lab.md) |

### 当前因子报告口径

```text
唯一入口：python -m research.report
默认：all_a / 2017-01-01 至 2026-11-01 / default profile / 5 日 / 20bp
输出：只输出 HTML
股票池：--universe all_a | index:... | pool:date:size
```

- profile 只描述报告章节：`default` 五章，`quick` 三章；
- `eval_profile` 以 name 唯一；
- Panel 缓存：`output/cache/research_panel/`，`--no-cache` 强制重建；
- 空间维度含 `ALL / EX_BSE / INVESTABLE / HIGH_LIQ` 样本敏感性；
- 时间维度含分月份 IC；
- metrics 后端：`REPORT_METRICS_BACKEND=auto|pandas|polars`；Rank IC 固定 pandas，
  Quantile NAV 按截面规模路由，`FactorReport.metrics_engine` 记录实际后端；
- `research.eval` 独立 CLI 已移除，数据层在 `research.data`。

## 四、文档索引

### 4.1 设计

| 文档 | 内容 |
|---|---|
| [design/factor_lab.md](design/factor_lab.md) | Factor Lab：角色、数据契约、因子库、门禁、控制 |
| [design/factor_library.md](design/factor_library.md) | 因子分类、命名、版本与增量方式 |
| [design/factor_materialization.md](design/factor_materialization.md) | 因子物化：registry/长表/latest/缓存 |
| [design/acquisition_backfill.md](design/acquisition_backfill.md) | 历史回补：切批、覆盖率判定、自适应分片 |
| [design/backtest_architecture.md](design/backtest_architecture.md) | 回测引擎详细架构（RQAlpha + 自建账本） |
| [design/backtest_report.md](design/backtest_report.md) | 回测报告结构与归因 |
| [design/data_pipeline.md](design/data_pipeline.md) | 分时、板块映射、披露数据链路 |
| [design/minute_data.md](design/minute_data.md) | 分钟线：数据源、字段/复权/换手率、停牌规则 |
| [design/fin_daily_materialization.md](design/fin_daily_materialization.md) | 财务因子日频化与面板完整性 |
| [design/lowfreq_precompute.md](design/lowfreq_precompute.md) | 低频因子预计算与写入 |
| [design/ai_module.md](design/ai_module.md) | AI/ML 因子模型接入研究体系 |

### 4.2 指南

| 文档 | 内容 |
|---|---|
| [guides/backtest.md](guides/backtest.md) | 回测安装、CLI、策略/因子写法与当前限制 |
| [guides/screener.md](guides/screener.md) | 选股四层结构、表、MCP 与 CLI 用法 |
| [guides/output_layout.md](guides/output_layout.md) | `output/` 目录约定（交付物 / 日志 / 回测产物） |

### 4.3 数据

| 文档 | 内容 |
|---|---|
| [data/coverage.md](data/coverage.md) | `ohlcv_1d` 与结构数据的实际覆盖区间、可研究起点 |

### 4.4 图谱

| 文档 | 内容 |
|---|---|
| [index.html](index.html) | 交互图谱：模块职责/核心函数 + 数据域、表结构与逻辑关系 |

## 五、依赖规则（实测 import 关系）

```
factors          -> （无内部依赖，纯计算）★
strategies       -> factors
data_interface   -> （无硬依赖；对 acquisition/position 是函数内延迟导入）
factor_engine    -> factors, data_interface
position         -> factors, data_interface
screener         -> factors, factor_engine, data_interface, acquisition
backtest         -> factors, factor_engine, data_interface, strategies
research         -> data_interface（仅 common.config/timeutil）
acquisition      -> data_interface, factor_engine, screener
MCPserver        -> 全部（注册工具）
```

硬约束：

1. **`factors/` 不得 import 任何其他业务模块**——它必须能在无 DB、无网络的环境下单测；
2. `data_interface` 对 `acquisition` / `position` 的引用必须留在**函数体内**（`api/runtime.py`），
   否则会形成循环依赖；
3. `screener` 对 `acquisition.BuildOrchestrator` 是**硬依赖**，改这个类要同步改 screener；
4. 任何模块写 `output/` 时必须遵守 [guides/output_layout.md](guides/output_layout.md) 的目录约定。

## 六、AI 阅读顺序建议

| 目标 | 建议顺序 |
|---|---|
| 改因子计算 | 03-factors → 04-factor-engine → 05-research |
| 加数据源 | 02-data-interface → 01-acquisition |
| 改回测 | 08-strategies → 07-backtest → design/backtest_architecture |
| 改选股 | 06-screener → 04-factor-engine |
| 接新工具 | 11-mcp-server → 对应模块的 `api.py` |

## 七、通用注意事项

| 主题 | 结论 |
|---|---|
| 时区 | QuestDB 存的北京时间**标签为 UTC**；读出后 `.dt.tz_localize(None)` 剥掉，**不要转换** |
| WAL | 写入后立即读可能读不到，需要重试（`concept/repository.py` 有现成实现） |
| 去重 | 所有长表用 `DEDUP UPSERT KEYS`；QuestDB 不支持 `DELETE` |
| 行情 schema | `market` 存交易所（`SSE/SZSE/BSE`），`type` 存品种（`stock/etf/lof/fund/idx`）；OHLC 的 DEDUP 含 `type` |
| 大表 | `factor_value_daily` 已千万行级，查询必须带 `ts` 范围或 `factor_id` |
| 内存 | 批量写入要分批（`service.build` 每 5 万行冲刷），32G 机器同时只跑一个重任务 |
| 命名 | 因子 ID 统一 `category.semantic[_window]`，小写；见 `factors/naming.py` |

## 八、维护约定

- **模块文档**：实现变更时同步更新对应 `modules/NN-*.md`，不保留过时签名；
- **设计文档**：只描述当前设计，不保留版本号、阶段和已废弃方案；
- **指南**：命令必须可在当前代码上直接执行；
- **历史**：由 git history 保存，不在文档树中维护变更记录；
- **图谱**：`index.html` 按当前模块与表结构维护，不包含本地数据行数；模块入口或表结构变化后同步更新。
