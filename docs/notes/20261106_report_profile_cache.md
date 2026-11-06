# 2026-11-06：因子报告收敛、Panel 缓存与样本敏感性

## 变更

### 1. 唯一入口

因子报告只保留：

```powershell
python -m research.report
```

删除：

- `research.eval.cli`
- `research.pipeline`
- `research.eval_items`
- `scripts/cli/factor_report.py`
- `scripts/cli/factor_eval.py`
- `research.report.inputs`

数据层迁到：

```text
research/data/
├── db.py       # QuestDB 访问
├── loader.py   # 面板加载
└── spec.py     # PanelSpec
```

### 2. profile 即报告章节

| profile | 章节 |
|---|---|
| `default` | definition / timing / cross_section / rating / appendix |
| `quick` | definition / timing / rating |

profile 以 name 唯一，不设版本号。股票池、区间、horizon、cost 都是运行参数。

### 3. 报告默认值

```text
all_a 全市场
2017-01-01 至 2026-11-01
default profile
5 日持有
20bp 单腿往返
只输出 HTML
```

股票池统一用：

```text
--universe all_a
--universe index:000905
--universe pool:2022-01-04:200
```

### 4. 样本敏感性

空间维度增加四版本：

| 版本 | 定义 |
|---|---|
| ALL | 当前报告样本 |
| EX_BSE | 排除 BSE |
| INVESTABLE | 排除 BSE/当前 ST、上市≥365天、20日成交额中位数≥2000万、价格≥2 |
| HIGH_LIQ | INVESTABLE 内按 20 日成交额每日取前 200 |

ST 使用当前 `is_st`，非 PIT，报告内已注明。

### 5. 分月份 IC

时间维度增加 1–12 月 IC 柱状图（跨年合并）。  
注意：这不是月度时间序列，也不能直接当稳定季节性。

### 6. Panel 缓存

- 路径：`output/cache/research_panel/<key>.pkl`
- key：universe / start / end / warmup / extras / 数据截止日 / schema
- 数据更新或 schema 变化自动失效
- `--no-cache` 强制重建
- 全市场缓存约 1.5GB；200 池约 31MB

### 7. 快速路径

```powershell
python -m research.report --factor reversal.zjfz --no-robustness
python -m research.report --factor reversal.zjfz --no-cache
python -m research.report --factor reversal.zjfz --profile quick
```

批量：

```powershell
python -m research.report `
  --batch f1,f2,f3 `
  --profile quick `
  --no-robustness `
  --continue-on-error
```

## 性能

- 全市场 Panel 构建：约 67s
- 缓存命中：约 0.5s
- 200 池、缓存命中、`--no-robustness`：约 12s
- 全市场端到端仍约 8–9 分钟；`build_report` 仍是主要耗时
