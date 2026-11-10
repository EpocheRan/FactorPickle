# scripts

`scripts/` 只放运维、数据构建与辅助入口；业务模块不 import 它。

## 因子报告

唯一入口：`python -m research.report`。禁止另写报告脚本或临时 Panel。

```powershell
python -m research.report --factor reversal.zjfz
python -m research.report --factor reversal.zjfz --universe pool:2022-01-04:200
python -m research.report --factor reversal.zjfz --universe index:000905
python -m research.report --factor reversal.zjfz --start 2022-01-05 --end 2026-11-01
```

约定：

- 默认 `--universe all_a`，区间 `2017-01-01` 至 `2026-11-01`；
- 股票池只用 `--universe`：`all_a` / `index:000905` / `pool:2022-01-04:200`；
- 只输出 HTML；
- REST `/exec` 返回 503 时，`research/report/db.py` 自动回退 PostgreSQL 协议；
- `profile` 只描述报告章节；股票池、区间、horizon、cost 是运行参数；
- 分层组合至少需要 `groups × 5` 只股票；
- Panel 缓存在 `output/cache/research_panel/`，`--no-cache` 强制重建。

## 辅助入口

| 脚本 | 用途 |
|---|---|
| `scripts/cli/profile.py` | 报告章节 profile 管理：seed / list / show / save |
| `scripts/cli/export_pool.py` | 把研究池导出成回测可用股票池 |

## 数据构建

`scripts/backfill_history.py` 是回补入口，实际实现位于 `acquisition/backfill/`：

```powershell
python -m acquisition backfill --phase <name>
```

可用阶段：`daily` / `delisted` / `index_weight` / `structure` / `periodic` / `financial` / `valuation`。

当前保留的数据/运维脚本：

`backfill_history.py`、`export_index_minutes.py`、`import_minutes_csv.py`、
`fetch_minute_pilot.py`、`init_database.py`、`migrate_database.py`、`migrate_factors.py`、
`repair_index_data.py`、`fix_index_series.py`、`verify_pipeline.py`。

## 统一入口

- 因子评估：`python -m research.report`
- 回测：`python -m backtest`
- 数据回补：`python -m acquisition backfill`
