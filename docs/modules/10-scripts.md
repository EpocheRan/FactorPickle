# 10 · scripts（运维与辅助脚本）

## 1. 边界

`scripts/` 只放**运维、数据构建与辅助入口**，不构成运行时依赖，也没有业务模块 import 它。

统一原则：

- 因子评估只走 `python -m research.report`；
- 回测只走 `python -m backtest`；
- 数据回补只走 `python -m acquisition backfill --phase <name>`；
- `scripts/` 只承载运维、数据构建与辅助入口。

## 2. 当前脚本

### 2.1 数据构建

| 脚本 | 作用 | 典型用法 |
|---|---|---|
| `scripts/backfill_history.py` | 回补入口，实际实现位于 `acquisition/backfill/` | `python -m acquisition backfill --phase daily` |
| `scripts/export_index_minutes.py` | 按指数成分导出分钟线 CSV（断点续跑） | `--index 000300.SH --start ... --end ... --freq 5min` |
| `scripts/import_minutes_csv.py` | 分钟线 CSV 落库 | `--csv output/xxx.csv --freq 5min` |
| `scripts/fetch_minute_pilot.py` | 小样本分钟线冒烟 | `--source zzshare --freq 5min --codes ...` |

### 2.2 辅助入口

| 脚本 | 作用 |
|---|---|
| `scripts/cli/profile.py` | 报告章节 profile 管理：seed / list / show / save |
| `scripts/cli/export_pool.py` | 把研究池导出成回测显式股票池 |
| `scripts/report_backtest.py` | 从已有 backtest 产物重生成报告（原型保留） |

### 2.3 运维

| 脚本 | 作用 |
|---|---|
| `scripts/maintenance/init_database.py` | 建全部表 |
| `scripts/maintenance/migrate_database.py` | 表结构迁移 |
| `scripts/maintenance/migrate_factors.py` | 因子注册表迁移 |
| `scripts/maintenance/repair_index_data.py` | 修复指数日线 |
| `scripts/maintenance/fix_index_series.py` | 修复指数序列 |
| `scripts/maintenance/verify_pipeline.py` | 分时/板块/披露链路验收 |
| `scripts/schemas/*.py` | 表结构定义与校验 |
| `scripts/compact-docker-vhdx.ps1` | 重建 Docker 数据盘，回收 vhdx 空间 |

## 3. Docker 数据盘回收

QuestDB 跑在 Docker 里，数据盘 `docker_data.vhdx` 只增不减。`diskpart compact` 与
`wsl --manage --set-sparse` 都回收不了真实释放的块，唯一可靠方式是重建数据盘。

```powershell
# 先干跑，只打印计划
powershell -ExecutionPolicy Bypass -File "$PWD\scripts\compact-docker-vhdx.ps1" -DryRun

# 管理员 PowerShell 正式执行（需要输入 YES）
powershell -ExecutionPolicy Bypass -File "$PWD\scripts\compact-docker-vhdx.ps1"
```

流程：停容器 → 备份命名卷到 `D:\docker-backup` → compose down / 退出 Docker →
删除 vhdx → 重建 → 恢复卷 → compose up → 校验行数。

注意：

- 会中断 Docker / QuestDB 约 10–20 分钟；备份先做，数据不会丢；
- 备份目录不会自动删除，确认服务正常后手动清理；
- 脚本必须保存为 UTF-8 with BOM（PowerShell 5.1 兼容）。

## 4. 数据回补

数据回补实现位于 `acquisition`，`scripts/backfill_history.py` 提供入口。

```bash
python -m acquisition backfill --phase daily
python -m acquisition backfill --phase delisted
python -m acquisition backfill --phase index_weight
python -m acquisition backfill --phase structure
python -m acquisition backfill --phase periodic
python -m acquisition backfill --phase financial
python -m acquisition backfill --phase valuation
```

常用约束：

| 约束 | 说明 |
|---|---|
| 按日拉取 | `daily(trade_date=...)` 一次返回全市场，明显快于按代码 |
| 单位 | Tushare `vol`（手）×100 → 股；`amount`（千元）×1000 → 元 |
| 幂等 | 回补按缺失判断，可中断后重跑 |
| 退市股 | `volume` 可能为 NaN，写 LONG 列前要填充 |
| 区间接口 | 单次上限约 6,000 行，解禁/回购/股东户数需按月分页 |
