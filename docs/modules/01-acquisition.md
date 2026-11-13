# 01 · acquisition（数据采集编排）

## 1. 架构与依赖

### 1.1 职责边界

`acquisition` 是**唯一负责"把外部数据写进 QuestDB"的编排模块**。它不解析业务语义、不算因子，
只做三件事：

1. **增量同步**：按数据集判断缺哪些日期/代码，只补齐缺失部分；
2. **断点续跑**：游标落库（`scheduled_task_state`），中断后从上次位置继续；
3. **构建调度**：为选股/回测准备"快照"（universe + 覆盖率），串行化并发构建请求。

对外**唯一入口**是 `python -m acquisition run` / MCP `acquisition_run`，两者都走
`Orchestrator` + `REAL_SYNCERS`。`AShareSyncService` 只提供各数据集的 `sync_*` 方法，
不再承担总调度（旧的 `run(dataset=...)` 与 `scheduled_sync_a_shares` 已删除）。

它**不**负责：数据读取（→ `data_interface`）、因子计算（→ `factor_engine`）、选股逻辑（→ `screener`）。

### 1.2 上下游

| 方向 | 模块 | 关系 |
|---|---|---|
| 上游 | 外部数据源 | Tushare / BaoStock / 东财 / 同花顺 / 腾讯 / 新浪 |
| 下游 | `data_interface.market/financial/moneyflow/sentiment/concept` | 调用其 `sync_*` 方法写库 |
| 下游 | `screener` | 通过 `BuildOrchestrator` 触发快照构建 |
| 下游 | `factor_engine` | `syncers.factors` / `syncers.screener` 触发因子物化 |
| 被调用 | `MCPserver`（`acquisition.api`）、`data_interface.api.runtime`（延迟导入） | 工具/单例 |

### 1.3 关键文件

| 文件 | 作用 |
|---|---|
| `orchestrator.py` | 对外编排：`Scope`、`DATASET_ORDER`、`Orchestrator.run()` |
| `syncers.py` | `REAL_SYNCERS`：数据集名 → 具体同步函数 |
| `datasets/service.py` | `AShareSyncService`：各数据集 `sync_*` 方法的组合 |
| `datasets/*.py` | 数据集实现（daily/benchmark/financial/valuation/moneyflow/sentiment/screener/concept/basic） |
| `datasets/base.py` | 状态键、代码/日期归一、`TASK_NAME` |
| `build_orchestrator.py` | 快照/作业构建：`ensure_snapshot` / `ensure_job` |
| `state.py` | `TaskStateRepository`：`scheduled_task_state` 读写 |
| `cli.py` / `__main__.py` | `python -m acquisition run/update/status/probe-p0` |
| `backfill/` | 历史回补阶段（`p0` 及单数据集阶段） |
| `api.py` | MCP `acquisition_run` / `acquisition_status`（唯一同步工具） |

### 1.4 数据流

```
Scope(datasets, start, end, codes, boards)
        │
        ▼
Orchestrator.run(scope)                  # 按 DATASET_ORDER 串行
        │
        ├─ REAL_SYNCERS[name](scope)     # 适配到 AShareSyncService.sync_*
        ├─ data_interface.sync_*         # 取数 / 归一 / 复权 / PIT
        ├─ QuestDB（WAL + DEDUP）
        └─ scheduled_task_state          # cursor / pending / status
```

### 1.5 数据集与写入表

| # | 数据集 | 写入表 | 说明 |
|---|---|---|---|
| 1 | `trading_calendar` | `trading_days` | 读路径按需补齐日历 |
| 2 | `stock_basic` | `stock_basic_info` | 当前画像（A 股 L/P/D 状态） |
| 3 | `stock_basic_history` | `stock_basic_history` | 全市场名单快照（L/P/D + 名称/ST/行业） |
| 4 | `namechange` | `namechange` | 历史名称与 ST 区间 |
| 5 | `new_share` | `new_share` | 新股发行/上市 |
| 6 | `suspend_d` | `suspend_d` | 逐日停牌（S）/复牌（R） |
| 7 | `stk_limit` | `stk_limit` | 逐日涨跌停价格 |
| 8 | `disclosure_date` | `disclosure_date` | 财报预约/实际披露日（按报告期） |
| 9 | `forecast` | `forecast` | 业绩预告（按公告日） |
| 10 | `express` | `express` | 业绩快报（按报告期） |
| 11 | `ohlcv_1d` | `ohlcv_1d` | 股票 + ETF/LOF 日线、`adj_factor` |
| 12 | `benchmark` | `ohlcv_1d` | 基准指数（默认沪深 300） |
| 13 | `financial` | `fin_report_meta`、三张报表、`fin_indicator` | 按股票池分批 |
| 14 | `valuation` | `fin_valuation_daily` | 按交易日全市场 |
| 15 | `moneyflow` | `moneyflow_daily`、`moneyflow_source` | 四档资金流 |
| 16 | `margin_detail` | `margin_detail` | 两融余额/买入/偿还 |
| 17 | `top_list` | `top_list` | 龙虎榜 |
| 18 | `block_trade` | `block_trade` | 大宗交易 |
| 19 | `concept_meta` | `concept_board_dim` | 板块目录 |
| 20 | `concept_daily` | `concept_board_daily` | 板块日线 |
| 21 | `concept_member` | `concept_member_snapshot` | 成分快照 |
| 22 | `concept_class` | `concept_classification` | 概念分类树 |
| 23 | `factors` | `factor_value_daily` / `factor_value_latest` | 调 `factor_engine` |
| 24 | `screener` | `screener_feature_daily` | 调 `BuildOrchestrator` |

第 3–10 项是 **P0 最小基础资料集**：`stock_basic_history` 每次运行写一份
当天全市场快照（含已退市），`namechange` 提供历史名称/ST 区间；其余按交易日、
自然日或报告期批量拉取。它们不写 `stock_basic_info` / `market_suspension` /
`fin_disclosure_calendar`，三张旧表保持原有语义。

`fin_valuation_daily` 有两个写入方：`sync_valuation*` / 回补阶段 `valuation`
（Tushare `daily_basic`，向前增量，写 13 列）与回补阶段 `valuation_derived`
（零请求，读本表 + 本地三大报表，原地重算 24 列）。后者用于把历史派生列补齐、
修掉 2017-2022 行的单位缺陷，详见
[../design/acquisition_backfill.md](../design/acquisition_backfill.md)。

## 2. 核心类与方法

### 2.1 类表

| 类/函数 | 关键方法 | 说明 |
|---|---|---|
| `Scope` | 数据类 | 同步范围：`start/end/codes/boards/datasets/factor_ids` |
| `Orchestrator` | `run(scope)` | **对外主入口**：按顺序执行、续跑、收集错误 |
| `REAL_SYNCERS` | 数据集函数表 | 数据集名 → `sync_daily` / `sync_margin_detail` … |
| `AShareSyncService` | `sync_daily` / `sync_financials` / `sync_benchmark` / `sync_screener` / `sync_moneyflow` / `sync_margin_detail` / `sync_top_list` / `sync_block_trade` / `sync_concept_*` / `sync_valuation` / `sync_stock_basic_history` / `sync_namechange` / `sync_new_share` / `sync_suspend_d` / `sync_stk_limit` / `sync_disclosure_date` / `sync_forecast` / `sync_express` / `probe_basic_permissions` / `status` | 各数据集的有界批次实现 |
| `BasicDataService` | `sync` / `sync_days` / `sync_periods` / `sync_range` / `probe` | P0 基础资料的取数与归一（`data_interface.basic`） |
| `BuildOrchestrator` | `ensure_snapshot` / `ensure_job` / `ensure_baseline_snapshot` / `recover` | 快照与作业调度，带互斥锁 |
| `TaskStateRepository` | `load/save` | 断点状态持久化 |
| `build_orchestrator()` | — | 工厂，返回已注入 `REAL_SYNCERS` 的编排器 |

### 2.2 用法

```python
from datetime import date
from acquisition import Scope, build_orchestrator

scope = Scope(
    start=date(2026, 10, 1),
    end=date(2026, 10, 30),
    datasets=("ohlcv_1d", "margin_detail", "top_list", "block_trade"),
)
result = build_orchestrator().run(scope)
print(result["results"], result["errors"])
```

```python
# 只跑单个数据集（仍是同一个入口）
from acquisition.syncers import REAL_SYNCERS
REAL_SYNCERS["margin_detail"](scope)
```

CLI：

```bash
python -m acquisition run                                  # 全数据集
python -m acquisition run --datasets ohlcv_1d,margin_detail,top_list,block_trade
python -m acquisition run --start 2026-01-01 --no-financial
python -m acquisition status
python -m acquisition probe-p0                            # 最小请求探测 P0 接口权限
```

P0 基础资料回补（可断点续跑，重复执行幂等）：

```bash
python -m acquisition run \
  --datasets stock_basic_history,namechange,new_share,suspend_d,stk_limit,disclosure_date,forecast,express \
  --start 2017-01-01 --end 2026-11-07 --loop --max-rounds 400
```

也可以走 `acquisition.backfill` 的阶段入口（单数据集阶段名与数据集同名）：

```bash
python -m acquisition.backfill --list
python -m acquisition.backfill --phase p0 --start 2017-01-01 --end 2026-11-07 --loop
python -m acquisition.backfill --phase forecast --start 2017-01-01 --end 2026-11-07 --loop
```

MCP：

```python
acquisition_run(datasets="margin_detail,top_list,block_trade",
                start_date="2026-10-01", end_date="2026-10-30")
```

### 2.3 注意事项（坑）

| 坑 | 说明 |
|---|---|
| **dataset 白名单** | 只能是上表 24 个之一，拼错直接抛 `ValueError` |
| **`start_date` 语义随数据集变** | 日线/基准/情绪/概念是交易日；财务是报告年份 + 估值日期范围；P0 的 `forecast` 按自然日、`disclosure_date`/`express` 按报告期 |
| **单次是分批的** | 日线 20 个交易日、财务 15 只、估值 5 日、分档 20 日、情绪 10 日、板块日线 50 个、P0 按日类 20 天、报告期 4 个、区间型 2 年；要 `--loop` 或反复心跳才 `done` |
| **P0 权限不足不阻塞** | 某个接口返回权限错误时记 `status=BLOCKED`、保留游标并返回 `done=True`，编排器继续跑其它数据集；限流/额度耗尽记 `PAUSED` 并抛错 |
| **P0 快照 vs 事实** | `stock_basic_history` 每次运行写一份当天快照；历史名称/ST 看 `namechange`，逐日停复牌看 `suspend_d` |
| **forecast 必须逐自然日** | Tushare `forecast` 只接受 `ann_date` 或 `ts_code`，周末也可能发预告，因此按自然日扫描 |
| **disclosure/express 首期** | 报告期从「起始年上一年年报」开始，保证窗口内披露的上一期报表也被覆盖 |
| **financial 会被自动跳过** | CLI 发现 `--start` 年份与库内游标不一致时会跳过，除非 `--force-financial` |
| **情绪类首次从最新日期续跑** | `margin_detail/top_list/block_trade` 无游标时从库内 `max(ts)` 开始，避免把默认 2020 变成全历史回填；历史用 `scripts/backfill_history.py --phase p2-structure` |
| **情绪空日只重试 3 天** | 龙虎榜/大宗可能当天没有数据；只对最近 3 天记 `pending`，避免历史空日永久挂起 |
| **P0 空日同样只重试 3 天** | 无停牌、无预告的日期不重复拉；最近 3 天内的空日才记 `pending`。`stk_limit` 例外：每个交易日都应有数据，历史空日也记 `pending`（最多重试 3 次） |
| **`force=True` 会重跑** | 跳过"已有数据"判断，慎用（几小时级） |
| **构建互斥** | `BuildOrchestrator` 用锁串行化；并发请求会排队而不是并行，长任务会阻塞其他构建 |
| **WAL 可见性** | 写完立刻读可能读不到，读取侧要重试（见 `concept/repository.py`） |
| **不要在这里算因子** | 因子属于 `factor_engine`，混进来会破坏分层 |
| **异常吞掉** | 单个代码失败通常记 `sync_state.status=ERROR` 后继续，`status()` 才能看到失败清单 |
