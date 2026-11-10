# scripts 说明

> 结论先说：**没有任何业务模块 import `scripts/` 或 `tests/`**（全仓 grep 结果为 0），
> 因此这批脚本可以独立整理、归档或删除，不会影响 factors / research / backtest / screener。

## 因子报告：唯一入口（AI 必读）

**只准用模块 CLI：`python -m research.report`。禁止另写自定义报告脚本、临时 Panel 或“先跑通再补”的脚本。**

`research.eval` 已删除；数据/面板参数在 `research.data`。报告由 profile 章节驱动，不再维护第二套评估入口。

旧的 `scripts/cli/factor_report.py` / `factor_eval.py` 已删除；不要再恢复。

```powershell
# 默认：all_a 全市场，2017-01-01 至 2026-11-01，default profile，5 日持有
python -m research.report --factor reversal.zjfz

# 指定股票池：一个 --universe 参数表达，不再用 --pool/--pool-date
python -m research.report --factor reversal.zjfz --universe pool:2022-01-04:200
python -m research.report --factor reversal.zjfz --universe index:000905

# 指定区间
python -m research.report --factor reversal.zjfz --start 2022-01-05 --end 2026-11-01
```

约定：

- **默认全市场**：`--universe all_a`；
- **默认区间**：`2017-01-01` 至 `2026-11-01`；
- 股票池只用 `--universe`：`all_a` / `index:000905` / `pool:2022-01-04:200`；
- 不再使用 `--pool`、`--pool-date`；
- **只输出 HTML**，不生成 JSON/PDF/PNG；
- REST `/exec` 对带 WHERE 的表查询返回 503 时，`research/report/db.py` 自动回退 PostgreSQL 协议，CLI 行为不变；
- `profile` 只描述“报告展示哪些章节”，股票池、区间、horizon、cost 都是运行参数；
- `rating` 是依赖项：选了 `rating` 仍会算时间/空间的核心指标（IC、价差、单调性、换手、增量性），但 `cross_section` 特有的中性化、容量、卖出端、多空明细等重计算会跳过；
- 分层组合至少需要 `groups × 5` 只股票（默认 5 组 = 25 只）；不足会直接报错，不再输出空白章节；
- 全市场报告慢时加 `--no-robustness`：跳过多池稳健性重算（主报告不受影响）；
- Panel 默认缓存到 `output/cache/research_panel/`；数据截止日、universe、extras 或 schema 变化会自动失效；`--no-cache` 强制重建；
- profile 以 `name` 为唯一键，**没有版本号**。

## 辅助脚本

| 脚本 | 用途 |
|---|---|
| `scripts/cli/profile.py` | 报告章节 profile 管理：seed / list / show / save |
| `scripts/cli/export_pool.py` | 把研究池导出成回测可用的显式股票池 |

## 数据构建（按需执行，非日常）

> **历史回补已迁移**：`scripts/backfill_history.py` 现为 shim，实际实现位于
> `acquisition/backfill/`，统一入口 `python -m acquisition backfill --phase <name>`。
> 可用阶段：daily / delisted / index_weight / structure / periodic / financial / valuation。

当前保留：
`backfill_history.py`、`init_database.py`、`migrate_database.py`、`migrate_factors.py`、
`repair_index_data.py`、`fix_index_series.py`。

## 清理记录

- **2026-11-10**：删除 `scripts/analysis/`（一次性专题与归档脚本）；删除 maintenance 下的
  临时 concept 回补脚本；清理全部 `__pycache__`。
- 不再新增一次性研究脚本。因子评估统一走 `python -m research.report`；
  回测统一走 `python -m backtest`；历史回补统一走 `python -m acquisition backfill`。
