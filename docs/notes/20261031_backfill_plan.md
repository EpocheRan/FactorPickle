# 补数方案：2017-01-01 → 今天（不含分钟线）

> 范围：除 `ohlcv_5m/15m/…` 外的全部数据表。
> 原则：**幂等 DEDUP + 断点续跑 + 分批验收**，每批独立可回滚，不一次性动全库。

## 0. 统一机制（所有批次共用）

| 机制 | 做法 |
|---|---|
| 幂等 | QuestDB `DEDUP UPSERT KEYS`，重复写不产生重复行；补数失败重跑安全 |
| 分片 | 行情/资金按**交易日**分片；财务/公告按**报告期**分片；成分按**月份**分片 |
| 断点 | 每片写 `scheduled_task_state` / `fin_sync_state`，中断后从 cursor 续跑 |
| 限频 | 统一走 `data_interface` 的 Tushare 限流器；失败指数退避，单日 3 次后跳过并记录 |
| 校验 | 每批跑一次「行数 + 覆盖年份 + 抽检 3 个 (code, date)」 |

## 1. 批次与命令

### P0 · 行情与成分（最先做，量最大但最成熟）

| 表 | 现状 | 补法 | 命令 |
|---|---|---|---|
| `ohlcv_1d` | 2017-11 前覆盖不足 | 复用历史回补脚本，起点改 2017-01-01 | `python scripts/backfill_history.py --start 2017-01-01 --end 2022-10-09 --start-year 2017` |
| `index_weight` | 000852 到 2019-06、000001 到 2017-05 | 按月重拉 5 个指数 | `python -m acquisition run --datasets index_weight --start 2017-01-01` |
| `trading_days` | 已全 | 无需 | — |

预计：`ohlcv_1d` 约 2017–2022 三年半 × 3500 只 ≈ 300 万行；`index_weight` 约 120 个月 × 5 指数 ≈ 1.5 万行。

### P1 · 资金与情绪（因子侧最缺）

| 表 | 现状 | 补法 | 命令 |
|---|---|---|---|
| `moneyflow_daily` | 2022-10 起 | 起点改 2017 | `python scripts/backfill_moneyflow.py --start 2017-01-01 --end 2022-10-09` |
| `margin_detail` | 2019-01 起 | 补 2017–2019 | `python -m acquisition run --datasets margin_detail --start 2017-01-01` |
| `top_list` | 2019-01 起 | 补 2017–2019 | 同上，`--datasets top_list` |
| `block_trade` | 2019-01 起 | 补 2017–2019 | 同上，`--datasets block_trade` |
| `stk_holdernumber` | 2019-01 起 | 补 2017–2019 | 同上，`--datasets stk_holdernumber` |
| `repurchase` | 2019-01 起 | 补 2017–2019 | 同上，`--datasets repurchase` |
| `share_float` | 覆盖 56% | 全量重拉（数据量小） | 同上，`--datasets share_float` |

预计：`moneyflow_daily` 约 2017–2022 × 3500 只 ≈ 500 万行，是本轮最大单项。

### P2 · 财务与估值

| 表 | 现状 | 补法 |
|---|---|---|
| `fin_income_statement` / `balance_sheet` / `cash_flow` / `indicator` | 2010 起已补，但本次查询因无 `ts` 列报错 | **先修查询**（按 `end_date`/`ann_date`），再核对 2017–2026 是否完整 |
| `fin_valuation_daily` | 2020-01 起 | 补 2017–2019；日频全市场约 3 年 × 4000 只 ≈ 300 万行 |
| `fin_disclosure_calendar` | 已有 | 补 2017 起 |

### P3 · 事件与板块

| 表 | 现状 | 补法 | 风险 |
|---|---|---|---|
| `announcement` | 227 只、2024-11 起 | 全量重拉 2017 起 | Tushare 公告接口按日全市场，量大；需确认积分 |
| `concept_board_daily` | 2022-10 起 | 起点改 2017 | 板块指数历史可能缺失，能补多少算多少 |
| `concept_member_snapshot` | 快照 | 按月补 | 同上 |

## 2. 执行顺序与时间估算

```
P0 行情+成分   ~2–4 小时（受 Tushare 日线限频）
P1 资金情绪    ~4–8 小时（moneyflow 是大头）
P2 财务估值    ~2–3 小时
P3 事件板块    ~2–4 小时（公告不确定）
```

每批结束后立刻跑一次校验与 `factor_eval`，确认新数据能被评估链路用上（覆盖率上升）再进下一批。

## 3. 验收标准（每批必过）

1. **行数与年份**：新增行数符合估算 ±30%；`min(ts)` ≤ 2017-01-05；
2. **覆盖度**：2017–2026 每年股票数 ≥ 当年上市数 × 95%；
3. **抽检**：随机 3 个 `(code, date)` 与数据源原始值一致；
4. **链路可用**：`factor_eval.py --universe all_a` 的「数据完整性」里该域 `status=ok` 且起点 ≤ 2017-02。

## 4. 风险与回滚

| 风险 | 应对 |
|---|---|
| Tushare 限频导致大面积失败 | 分日跑、断点续跑；失败日记录到 `fin_sync_state`，次日重试 |
| 退市股缺失（历史上退市约 200 家） | 已知偏差，报告中继续申明；不用补数掩盖 |
| 概念板块历史不可得 | 接受"补到哪算哪"，在 `docs/data/coverage.md` 标注实际起点 |
| 财务重述导致历史值变化 | 按 `announce_date + version` 版本化，不覆盖旧行 |
| 补数写坏表 | 每批前后各做一次 `count()/min(ts)` 快照；QuestDB 不支持 DELETE，异常分区按分区重建 |

## 5. 需要先确认的三件事

1. **Tushare 积分/限频**：`moneyflow`、`announcement` 是重度接口，当前 token 是否够；
2. **`fin_income_statement` 的时间列**：本次查询报错，需确认 schema（`end_date` / `ann_date`）；
3. **是否接受"分批上线"**：P0 完成后就先跑因子矩阵，还是等四批全补完再开始。
