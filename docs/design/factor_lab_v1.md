# Factor Lab v1（P0 实施说明）

> 分支：`feat/factor-lab-p0`。只新增 `factor_lab/`、`tests/test_factor_lab*.py` 和本文档；不改生产模块。

## 1. 边界

Factor Lab 读取：

- `research.report.runner.generate`
- `research.report.panel_adapter.load_panel`
- `factors.ops.price_factors`
- QuestDB / `config.json` / `.venv`（只读）

Factor Lab 写入：

- `output/factor_lab/**`
- `output/cache/research_panel/*.pkl`（复用现有缓存，可能新增条目）

不写生产 registry，不写 DB，不改 `research/`、`factors/`、`factor_engine/`。

## 2. 产物

| 产物 | 作用 |
|---|---|
| `policy/gate_policy.json` | 版本化阈值、gate type、唯一 adjusted p 口径 |
| `ledger/trial_ledger.jsonl` | hash chain、repair marker、hypothesis/run/outcome 事件 |
| `experiments/<id>/manifest.json` | run_id、trial_id、outcome、数据版本、policy hash |
| `experiments/<id>/report.json` | `FactorReport` 的紧凑序列化 |
| `experiments/<id>/audit.json` | 确定性检查 + G0.5 相关性 + G3 交易性子检查 |
| `experiments/<id>/orthogonality.json` | P1：与 library snapshot 的暴露相关 + block bootstrap CI |
| `experiments/<id>/decision_brief.json` | ≤2KB，人工决策摘要 |
| `experiments/<id>/decision.json` | park / iterate / repair / promote |
| `dashboard/lab_dashboard.html` | 自包含控制台，读汇总产物 |
| `control/lab_control.json` | running / paused / stopped |

## 3. 计数口径

- `trial = hypothesis × pre-registered outcome`。
- `outcome_policy: single` 是默认：1 universe × 1 horizon × 1 direction。
- 同一 trial 的 L0–L3 阶段不重复计入 N。
- 超出 hypothesis 预注册 outcome 的运行，视为新 hypothesis。
- `report.run_id` 必须能反查到更早的 `hypothesis_registered`；否则 audit fail。

## 4. 审计

`python -m factor_lab audit --experiment <id>` 检查：

- artifacts / run_id / 参数窗口 / 样本覆盖；
- IC 均值重算；
- Q5−Q1 毛价差重算；
- `net = gross - cost` 恒等式；
- 单调性（不一致只 warn）；
- gate policy sha256；
- ledger hash chain / repair / orphan run；
- outcome 是否在预注册集合；
- G0.5：`orthogonality.json` 的 exposure CI（下界 > 0.9 fail，上界 < 0.7 pass，跨区间 warn）；
- turnover / capacity / 2× cost 的 G3 子检查；
- rating 字段存在；
- `q_value`：单 trial 记 raw p；多 trial 先记 Bonferroni fallback，BH 留到 batch。

`audit.verdict` 是 G2 完整性；`audit.g3` 是交易性。两者分开，避免把“报告算错”和“因子不可交易”混为一谈。

## 5. Dashboard

```powershell
python -m factor_lab mine --limit 23
python -m factor_lab dashboard
```

生成 `output/factor_lab/dashboard/lab_dashboard.html`，读取 experiments、ledger、
policy、portfolio、coverage、control。页面包含：

- 红黄绿状态与「待处理」队列；
- KPI：本周新增假设、本月进入 G3、假设→G3、去重 trial、孤儿 run、审计失败率；
- 门禁漏斗、试验累计、研究押注；
- 实验表（过滤 / 搜索 / 点击详情）；
- 审计发现、数据覆盖、存储与清理。

只读，不带 pause/approve 按钮；暂停走 `control`。

## 6. 暂停 / 继续

```powershell
python -m factor_lab control pause --reason "review"
python -m factor_lab control resume
python -m factor_lab control show
```

`run_experiment` 开始前调用 `assert_running()`；暂停时新 run 直接拒绝。已在跑的
report 不会被打断，按 trial 边界收尾。

## 7. 清理

```powershell
python -m factor_lab maintenance inventory
python -m factor_lab maintenance clean          # dry-run
python -m factor_lab maintenance clean --apply  # 移到 output/_archive 或 _trash
```

规则：

- `factor_lab_smoke*`、`_tmp_*`、旧日志 → `_trash`；
- panel cache 超过 2GB → `_archive`；
- 每个 report family 保留最近两个日期目录；
- `backup_*` 只报告，不自动移动。

## 8. 已知限制

- `orthogonality.py` 目前只算 exposure 相关；`rho_return` 仍待补，G0.5 结论标注为 exposure-only。
- `q_value` 单 trial 先记 raw p；多 trial 目前是 Bonferroni fallback，BH 是 batch 步骤。
- 2× cost 已由 `cost_mean` 重算；`capacity` 在 quick profile 下仍可能缺失，audit 记 warn。
- 首个 pilot 是 D 级因子；P0/P1 验收的是链路与门禁，不是 alpha。
