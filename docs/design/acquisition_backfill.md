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
acquisition/
├── __main__.py               # 增加 backfill 子命令
├── cli.py                    # run / update / backfill 三个入口
├── backfill/
│   ├── __init__.py           # 公开 API: run_phase / list_phases
│   ├── registry.py           # @phase 注册、名称→实现、依赖顺序
│   ├── scope.py              # BackfillScope(start, end, codes, shards...)
│   ├── progress.py           # 统一进度/ETA 日志
│   ├── writer.py             # 分批插入（chunk=500）、NaN→None、失败计数
│   ├── coverage.py           # 覆盖判定：max(ts) / 按年计数 / 缺口清单
│   └── phases/
│       ├── daily.py          # p0-daily：行情（按日）
│       ├── delisted.py       # p1-delisted：退市股
│       ├── index.py          # p1-index：指数成分（按月、按 max(ts) 续跑）
│       ├── structure.py      # p2-structure：龙虎榜/两融/大宗
│       ├── periodic.py       # p2-periodic：解禁/回购/股东户数（按月分页）
│       ├── financial.py      # p3-financial：财务四表（按代码、先查覆盖）
│       └── valuation.py      # p3-valuation：估值日表（按日、分批）
└── datasets/                 # 现有 syncers（增量模式保留）
```

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
python -m acquisition backfill --list
python -m acquisition backfill --phase p3-valuation --start 2020-01-01 --end 2022-12-31
python -m acquisition backfill --phase p0-daily --start 2017-01-01 --end 2017-12-31 --chunk 500
python -m acquisition backfill --phase p1-index --start 2017-01-01 --end 2026-10-31 --dry-run
```

`scripts/backfill_history.py` 保留为转发入口，实际执行走 `acquisition.backfill`。

## 7. 实现约定

- 写入器每 500 行切批；NaN/NaT/None 统一转 None；
- 单批失败只记 warning，累计成功行数；
- 续跑基于 `max(ts)`，不看输入行数；
- `--dry-run` 只生成 GapReport，不写库；
- 每次取数命中源上限时自动按月分片；
- 阶段注册表统一在 `acquisition/backfill/registry.py`。

## 8. 测试（离线，不依赖 DB）

| 用例 | 断言 |
|---|---|
| `writer.chunking` | 5,030 行 → 11 批（10×500 + 30），顺序不变 |
| `writer.nan` | `NaN / pd.NaT / None` 一律转 None |
| `writer.failure` | 单批抛错不影响后续批，累计行数正确 |
| `coverage.resume` | 给定 `max(ts)=2019-06-28`，请求 2017 起 → 只补 2019-07 之后 |
| `registry` | 未知 phase 报错并列出可用项；重复注册报错 |
| `phase.dry_run` | 只调用 coverage，不调用 writer |

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
