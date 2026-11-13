# acquisition.backfill 设计

> 目标：把「历史回补」从 `scripts/backfill_history.py` 收进 `acquisition` 模块，
> **业务逻辑不出现在 `scripts/`**，脚本只做参数转发。
>
> 现状问题：增量（`acquisition run`）与回补（`scripts/backfill_history.py`）两套逻辑并存，
> 分页、批量写入、时区、续跑判断各写一遍，坑也各踩一遍。

## 1. 边界

| 层 | 职责 | 不做什么 |
|---|---|---|
| `acquisition/backfill/` | 阶段编排、覆盖率判定、分批写入、续跑 | 不做因子计算、不碰 `factors/` |
| `acquisition/datasets/` | 单表取数（Tushare/BaoStock/AkShare） | 不做跨表编排 |
| `data_interface` | 表结构、写入器、`max(ts)` 查询 | 不知道"阶段"概念 |
| `scripts/*.py` | **仅**解析 argv → 调 `acquisition` 公开入口 | 零业务逻辑、零 SQL |

## 2. 目录结构

```
acquisition/backfill/
├── __init__.py     # 公开 API：Scope / PhaseResult / @phase 注册 / sql / write / gap_report
├── __main__.py     # python -m acquisition.backfill（--list / --phase / --loop）
└── phases.py       # 全部阶段实现
```

阶段按语义名注册，`--list` 可查：

| 分类 | 阶段 |
|---|---|
| 行情 | `daily` / `delisted` / `benchmark` 类日常增量 |
| 指数与估值 | `index_weight` / `valuation` / `valuation_derived` |
| 结构 | `structure`（龙虎榜/两融/大宗）/ `periodic`（解禁/回购/股东户数） |
| 财务 | `financial` / `disclosure` |
| 板块 | `concept` / `boards` |
| **P0 基础资料** | `p0`（整体）/ `stock_basic_history` / `namechange` / `new_share` / `suspend_d` / `stk_limit` / `disclosure_date` / `forecast` / `express` |

P0 阶段直接复用 `acquisition.datasets.AShareSyncService` 的 `sync_*` 方法：
游标、pending 重试、权限阻断（`BLOCKED`）与增量同步走同一条代码路径，
回补即「把同一批次按历史窗口重复调用」。

### 2.1 `valuation` 与 `valuation_derived` 的分工

| 阶段 | 数据来源 | 写什么 | 单位 |
|---|---|---|---|
| `valuation` | Tushare `daily_basic` | 13 列（`close`/股本/市值/PE/PB/PS/股息率） | 写入前转 canonical（万股→股、万元→元、百分数→小数） |
| `valuation_derived` | **零 Tushare 请求**：库内 `fin_valuation_daily` + 本地三大报表 | 全部 24 列（9 个目标字段 + 廉价伴随字段） | 已在表内，只对 `formula_version='backfill.v1'` 的行做一次单位修复 |

`valuation` 只向前补交易日、永不回改历史，所以「历史派生列为空 + 单位缺陷」由
`valuation_derived` 原地重算解决：读已有行 → 按 PIT 规则取当时可见的报表 →
`FinancialMetricsCalculator` 重算 → 以原 `(ts, code, source)` 键 `INSERT`（DEDUP 覆盖）。
不新建表、不加列、不删行，行数在重算前后保持不变。

## 3. 核心抽象

```python
@dataclass(frozen=True)
class BackfillScope:
    start: date
    end: date
    codes: tuple[str, ...] | None = None
    shard: int = 0
    shards: int = 1

class BackfillPhase(Protocol):
    name: str                       # "p0-daily"
    tables: tuple[str, ...]         # 写哪些表（用于覆盖检查与审计）
    def run(self, scope: BackfillScope, ctx: PhaseContext) -> PhaseResult: ...
```

`PhaseContext` 注入三件事：**取数器**（`api.call`，带限流）、**写入器**（`ctx.write(table, cols, rows)`，内部切批）、**进度**（`ctx.progress(...)`）。
阶段自己不写 SQL、不管分页、不管 NaN。

## 4. 写入器契约（把踩过的坑固化）

```python
ctx.write(table, columns, rows, *, chunk=500)
# - 每 500 行一批（QuestDB 请求体积上限）
# - NaN/NaT → None（DOUBLE 列不接受 NaN）
# - 单批失败只记 warning，不中断阶段；返回成功行数
# - 全部失败时抛 WriteError，由阶段决定是否终止
```

## 5. 覆盖率与续跑（单一实现）

```python
coverage.resume_from(table, ts_col="ts", index=None) -> date | None   # 取 max(ts)
coverage.assert_covered(table, start, end, key=("code",), min_ratio=0.95) -> GapReport
```

规则（固化本轮教训）：

1. **判据永远是 `max(ts)`，不是行数**；
2. 续跑只补 `max(ts)` 之后的区间，不从头重拉；
3. 阶段结束自动输出 `GapReport`：缺哪些年、哪些月、覆盖比例；
4. `--dry-run` 只出 `GapReport`，不写库。

## 6. CLI

```bash
python -m acquisition.backfill --list
python -m acquisition.backfill --phase index_weight --start 2017-01-01 --end 2026-11-07
python -m acquisition.backfill --phase daily --start 2017-01-01 --end 2017-12-31 --chunk 500
python -m acquisition.backfill --phase p0 --start 2017-01-01 --end 2026-11-07 --loop
python -m acquisition.backfill --phase p0 --start 2017-01-01 --end 2026-11-07 --dry-run

# 估值派生列原地重算（零请求，按月续跑）
python -m acquisition.backfill --phase valuation_derived \
  --start 2017-01-01 --end 2026-11-06 --chunk 2000
python -m acquisition.backfill --phase valuation_derived \
  --start 2017-01-01 --end 2017-03-31 --dry-run     # 只算不写，打印预测覆盖
python -m acquisition.backfill --phase valuation_derived \
  --start 2017-01-01 --end 2026-11-06 --fresh       # 忽略逐月续跑状态
```

`--loop` 让分批阶段自动续跑到 `done`（受 `--max-rounds` 限制）；每个 P0 阶段也
可以单独跑，例如 `--phase forecast`。`scripts/backfill_history.py` 保留为转发入口，
实际执行走 `acquisition.backfill`。

`valuation_derived` 的续跑状态落在
`output/factor_lab/backfill/valuation_derived_progress.json`：只记录已完成的自然月，
区间或代码集变化时自动视为全新运行；`--fresh` 强制清空。
`run_phase` 只在阶段真正取数时才构造 Tushare 客户端（`_LazyApi`），
所以纯本地阶段不依赖 token。

## 7. 实现约定

- 写入器每 500 行切批；NaN/NaT/None 统一转 None；
- 单批失败只记 warning，累计成功行数；
- 续跑基于 `max(ts)`，不看输入行数；
- `--dry-run` 只生成 GapReport，不写库；
- 每次取数命中源上限时自动按月分片；
- 阶段注册表统一在 `acquisition/backfill/phases.py`（`@phase` 装饰器）。

## 8. 测试（离线，不依赖 DB）

| 用例 | 断言 |
|---|---|
| `writer.chunking` | 5,030 行 → 11 批（10×500 + 30），顺序不变 |
| `writer.nan` | `NaN / pd.NaT / None` 一律转 None |
| `writer.failure` | 单批抛错不影响后续批，累计行数正确 |
| `coverage.resume` | 给定 `max(ts)=2019-06-28`，请求 2017 起 → 只补 2019-07 之后 |
| `registry` | 未知 phase 报错并列出可用项；重复注册报错 |
| `phase.dry_run` | 只调用 coverage，不调用 writer |
| `valuation.canonical_units` | `valuation` 阶段写 股/元/小数，缺失值保持 NULL |
| `valuation_derived.dry_run` | 写入 0 行，边界覆盖不变，仍输出预测覆盖 |
| `valuation_derived.local_only` | 阶段只调服务，不构造 Tushare 客户端 |

`tests/test_financial_rebuild.py` 用内存仓储覆盖重算语义：单位修复、PIT（披露日当天可见、
之后的重述不回改历史）、TTM 四连季、幂等、按月续跑、双 source 键不变、`market_cap_change_pct` 衔接。

## 9. 风险

| 风险 | 应对 |
|---|---|
| 阶段改动改坏现有行为 | 同区间行数对比做回归，先验证两个阶段 |
| Tushare 限频 | 沿用现有 `_Api` 限流 + 退避，阶段内不做并发 |
| `p3-financial` 再次白跑 | 阶段启动先 `assert_covered`，已完整直接跳过并记录 |
| 脚本 shim 被误用 | shim 打印 deprecation 警告，并在 `scripts/README.md` 标注 |

## 10. 不在本期范围

- 分钟线回补（源有问题）；
- 概念成分历史（源无数据）；
- 把增量 syncer 也改成可回补（后续可加 `mode="backfill"`）。
