# FinBridge

> 面向 AI Agent 的 A 股量化研究基础设施：数据采集、因子物化、研究、选股、回测与报告，收进一条可复现的链路。

**[在线系统图谱](https://epocheran.github.io/FinBridge/)** · [文档索引](docs/README.md) · [模块文档](docs/modules/) · [设计文档](docs/design/) · [使用指南](docs/guides/)

## 为什么是 FinBridge

- **端到端**：多源同步 → 因子注册与物化 → 选股 / 回测 → HTML 研究报告。
- **因子是资产**：`FactorDef` 声明、registry 注册、长表 / latest 物化，方向、单位、版本可追溯。
- **A 股规则内置**：T+1、整手、涨跌停、停牌、佣金与印花税；单标的与横截面共用一套账本。
- **MCP 原生**：行情、财务、因子、选股、持仓、同步统一暴露为工具，AI Agent 可直接调用。
- **可复现**：`run_id` 固化策略、参数、数据快照与执行规则，同输入可重跑、可对照。

## 架构

```text
数据源（Tushare / BaoStock / AkShare / 腾讯 / 新浪）
        │
        ▼
acquisition ── 唯一采集入口：Orchestrator + REAL_SYNCERS
        │
        ▼
data_interface ── 唯一数据访问层：取数、归一、复权、PIT
        │
        ▼
QuestDB ── 行情 / 财务 / 资金 / 板块 / 因子 / 持仓 / 状态
        │
        ├── factor_engine ── 因子物化与宽表
        ├── screener      ── 条件选股
        ├── backtest      ── A 股执行规则与账本
        ├── research      ── IC / 分层 / 衰减 / 报告
        └── position      ── 组合、持仓、交易流水
        │
        ▼
MCPserver ── 统一工具出口（http / sse / stdio）
```

依赖保持单向：`factors` 不依赖业务模块，`strategies` 只依赖 `factors`，`backtest` 单向依赖 `strategies` / `factors`。完整依赖关系见 [docs/README.md](docs/README.md)。

## 主要模块

| 模块 | 职责 | 入口 |
|---|---|---|
| `acquisition/` | 采集编排、断点续跑、快照与作业调度 | `python -m acquisition` |
| `data_interface/` | 行情 / 财务 / 资金 / 情绪 / 板块读写与复权 | `data_interface.api.*` |
| `factors/` | 因子定义、时序与横截面算子 | `from factors import F` |
| `factor_engine/` | 因子物化、宽表、审计与对账 | `python -m factor_engine` |
| `research/` | 统一报告：profile 章节 + 单因子 HTML；默认全市场 | `python -m research.report` / `python -m scripts.cli profile` |
| `screener/` | 因子筛股、基线、停牌与覆盖检查 | `python -m screener` |
| `backtest/` | 单股 / 横截面回测、执行规则、账本 | `python -m backtest` |
| `strategies/` | 策略基类、universe、weighting、模型 | `python -m backtest list` |
| `position/` | 组合、持仓、交易流水、ATR / GRID 模式 | MCP `pm_*` 工具 |
| `scripts/` | 建表、迁移、因子审计、相关性分析 | 手工执行 |
| `MCPserver.py` | FastMCP 注册与启动 | `python MCPserver.py` |

## 快速开始

### Docker（推荐）

```bash
cp config.example.json config.json   # 填入 TUSHARE_TOKEN 等
docker compose up -d
```

启动顺序：`questdb` → `db-init`（一次性建表）→ `mcp-server`。运行期不再建表。

```bash
docker compose ps
docker logs mcp-app --tail 100
```

### 本地运行

前提：QuestDB 已启动并监听 `8812`。

```bash
uv sync
cp config.example.json config.json

python scripts/maintenance/init_database.py     # 只需执行一次
python MCPserver.py
```

`config.json` 已被 `.gitignore` 排除，请勿提交。

### 首次同步

```bash
python -m acquisition run
# 或只更新部分数据集
python -m acquisition run --datasets ohlcv_1d,factors,screener
```

数据集、增量策略与回补命令见 [docs/modules/01-acquisition.md](docs/modules/01-acquisition.md)。

## 关键约定

- **单一存储**：QuestDB 是唯一事实源，WAL + DEDUP UPSERT；不支持 DELETE，删除按分区重建。
- **价格口径**：存不复权价 + 累计复权因子，复权在读取时计算。
- **市场与品种**：`market` 是交易所（`SSE` / `SZSE` / `BSE`），`type` 是品种（`stock` / `etf` / `lof` / `fund` / `idx`）。
- **时点正确**：财务按 `publish_date` 过滤，板块成分按 `as_of` 取最近整组快照。
- **写入边界**：raw 表只由 `data_interface` 写，因子表只由 `factor_engine` 写。
- **报告单一入口**：因子报告只认 `research.report`；报告章节由 `eval_profile` 里的 profile 决定。取数入口在 `research.data`（`loader.py` / `db.py` / `spec.py`），不再有 eval CLI。
- **因子报告唯一入口**：只用 `python -m research.report`。默认 `all_a` 全市场、`2017-01-01` 至 `2026-11-01`、`default` profile、5 日持有；股票池只用 `--universe`（`all_a` / `index:...` / `pool:date:size`），不再使用 `--pool/--pool-date`；只输出 HTML。禁止另写自定义报告脚本或临时 Panel。
- **profile 即报告章节**：`default` 包含定义、时间、空间、评级、附录五章；`quick` 只含定义、时间、评级。`research.eval` 已移除，数据/面板参数在 `research.data`。
- **Panel 缓存**：`output/cache/research_panel/`；数据截止日/universe/extras/schema 变化自动失效；`--no-cache` 强制重建；5 个报告优先一个进程 `--batch`。
- **策略组合**：`band_turnover_combo` 组合 `risk.band_break_21d` 与 `liquidity.turnover_std_bias_21_504`；注意 `risk.band_break` 方向为 −1。
- **策略回测入口**：`python -m backtest`（已存在，不要改 `backtest/reporting`）。
- **profile 唯一键**：profile 以 `name` 唯一，不设版本号；股票池、区间、horizon、cost 都是 run parameter，不写进 profile。

完整字段、分区与迁移说明见 [docs/design/](docs/design/) 与 [docs/data/coverage.md](docs/data/coverage.md)。

## 文档

| 入口 | 内容 |
|---|---|
| [在线系统图谱](https://epocheran.github.io/FinBridge/) | 模块职责、核心函数、数据域与表关系 |
| [docs/README.md](docs/README.md) | 文档总索引、依赖关系、AI 阅读顺序 |
| [docs/modules/](docs/modules/) | 11 个模块的架构与核心类/方法 |
| [docs/design/](docs/design/) | 因子库、物化、回测架构、数据管线 |
| [docs/guides/](docs/guides/) | 回测、选股、`output/` 目录约定 |
| [docs/notes/](docs/notes/) | 变更备注、迁移与回滚 SQL |

## 测试

```bash
python -m pytest -q                 # 离线单测，默认不联网、不依赖 DB
RUN_LIVE_TESTS=1 python -m pytest   # 额外跑真实数据源 / QuestDB 用例
```

## 数据来源

Tushare、AkShare、BaoStock、腾讯行情。商业使用时请遵守各数据源许可协议。
