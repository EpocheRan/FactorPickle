# 05 · research（因子报告）

## 1. 架构与依赖

### 1.1 职责边界

`research` 回答一个问题：**这个因子值不值得用**。它把因子面板 + 行情变成一份可复核的 HTML 报告，并给出时间/空间/评级结论。

- **唯一入口**：`python -m research.report`
- **不生成 JSON/PDF/PNG**，只输出 HTML
- **不做选股、不做回测撮合**
- 可执行口径：T+1 开盘、单腿往返 20bp、多空合计 40bp

### 1.2 上下游

| 方向 | 模块 | 关系 |
|---|---|---|
| 上游 | `factors` | `F.ts(...)` / 价量算子 |
| 上游 | `data_interface` | 只读 config / timeutil |
| 上游 | `research.data` | `loader.load_panel` / `PanelSpec` / QuestDB |
| 下游 | HTML | `output/reports/factor/<日期>/<factor>.html` |
| 下游 | profile | `eval_profile` 表；name 唯一，无版本号 |

### 1.3 关键文件

| 文件 | 作用 |
|---|---|
| `data/db.py` | QuestDB 访问；REST 失败后固定走 PG |
| `data/loader.py` | 股票池解析、PIT 成员、面板长表/宽表 |
| `data/spec.py` | `PanelSpec`：universe / 区间 / horizon / cost / extras |
| `report/panel_adapter.py` | 数据层 `PanelData` → 报告 `Panel` |
| `report/panel_cache.py` | Panel 宽表本地缓存（key + 数据版本失效） |
| `report_sections.py` | 报告章节目录（definition/timing/cross_section/rating/appendix） |
| `report/metrics.py` | `build_report`：IC/分层/衰减/中性化/成本/容量/可成交性 |
| `report/metrics_backend.py` | metrics 后端选择：Rank IC pandas，Quantile NAV pandas/Polars auto |
| `report/metrics_polars.py` | Polars Rank IC、quantile spread、完整 quantile NAV |
| `report/benchmark_polars.py` | 真实 Panel 的 pandas/Polars parity 与性能 benchmark |
| `report/rating.py` | 100 分制评分卡 + 一票否决 + 四象限 |
| `report/render.py` | HTML 渲染；章节按 profile 过滤；宽表响应式 |
| `report/sensitivity.py` | ALL / EX_BSE / INVESTABLE / HIGH_LIQ 四样本敏感性 |
| `report/runner.py` | `generate`：取数 → 因子 → build → 评级 → 敏感性 → HTML |
| `report/cli.py` | CLI：默认全市场、只输出 HTML |

## 2. 数据流

```text
python -m research.report
  └─> panel_adapter.load_panel
        ├─ Panel 缓存命中 → 直接读宽表
        └─ 未命中 → research.data.loader.load_panel → 写缓存
  └─> price_factor_panel(factor_id)
  └─> metrics.build_report(...)
        ├─ Rank IC → pandas
        └─ Quantile NAV → metrics_backend
             ├─ auto / pandas
             └─ auto / polars (中等截面)
  └─> rating.rate(report)
  └─> sensitivity.compute(...)
  └─> render.render(...)
  └─> output/reports/factor/<日期>/<factor>.html
```

## 3. Metrics 后端

```python
REPORT_METRICS_BACKEND = "auto" | "pandas" | "polars"
```

默认 `auto`。

| 指标 | 后端 |
|---|---|
| Rank IC | 始终 pandas |
| Quantile NAV | auto：中等截面 Polars，全市场 pandas |

规则：

- `pandas`：强制 pandas，生产回退开关；
- `polars`：强制 Polars，用于 benchmark/debug；
- `auto`：根据 `rows × symbols` 选择；超过阈值使用 pandas；
- Polars 缺失时自动回退 pandas；
- `FactorReport.metrics_engine` 记录实际使用的 engine。

两条路径共享同一套 `QuantileResult`：

```text
nav
turnover_df
periods_net
periods_gross
daily
ls_cost
turnover_mean
```

验收标准：

- periods_gross / periods_net / turnover_df / nav 数值一致；
- NaN、ties、qcut 边界、tradeable mask、不同 horizon 均有 parity 测试。

Benchmark：

```powershell
python -m research.report.benchmark_polars --universe all_a --factor-id momentum.ret_21d
```

## 4. CLI

默认：

```text
all_a 全市场
2017-01-01 至 2026-11-01
default profile
5 日持有
20bp 单腿往返
只输出 HTML
```

```powershell
python -m research.report --factor reversal.zjfz
python -m research.report --factor reversal.zjfz --universe pool:2022-01-04:200
python -m research.report --factor reversal.zjfz --profile quick
python -m research.report --factor reversal.zjfz --no-robustness
python -m research.report --factor reversal.zjfz --no-cache
```

批量：

```powershell
python -m research.report `
  --batch f1,f2,f3 `
  --universe pool:2022-01-04:200 `
  --profile quick `
  --no-robustness `
  --continue-on-error
```

## 5. profile 与章节

profile 只描述“报告展示哪些章节”，不包含股票池、区间、horizon、cost。

| profile | 章节 |
|---|---|
| `default` | definition / timing / cross_section / rating / appendix |
| `quick` | definition / timing / rating |

- 章节由 `report_sections.py` 定义；
- `render.py` 按章节裁剪 HTML；
- `build_report` 会跳过未选中的 `cross_section` 专属计算；
- `rating` 是依赖项：选它仍会计算时间/空间核心指标。

## 6. 报告内容

- 分年 IC（95% CI）
- **分月份 IC（1–12 月，跨年合并）**
- IC 衰减
- 分组收益、多空净值、回撤
- 中性化/分市值/分行业/增量性
- 成本敏感性、容量、可交易性
- **不同样本敏感性**：ALL / EX_BSE / INVESTABLE / HIGH_LIQ
- 评级、否决项、失效归因

### 样本敏感性口径

| 版本 | 定义 |
|---|---|
| ALL | 当前报告样本 |
| EX_BSE | 排除 BSE |
| INVESTABLE | 排除 BSE/当前 ST，上市 ≥365 天，20 日成交额中位数 ≥2000 万，价格 ≥2 |
| HIGH_LIQ | INVESTABLE 内按 20 日成交额每日取前 200 |

注意：ST 使用当前 `is_st`，不是 PIT，存在前视；报告内已注明。

## 7. 核心方法

### `generate`

```python
generate(
    factor_id,
    *,
    universe="all_a",
    start="2017-01-01",
    end="2026-11-01",
    profile="default",
    robustness=True,
    use_cache=True,
    ...
) -> dict
```

返回 `html`、`rating`、`report`、`meta`。

### `build_report`

新增 `sections` 参数；只计算 profile 需要的章节。`QuantileResult` 复用主分组结果，成本敏感性不再重复 `_quantile_nav`。

### 缓存

- 位置：`output/cache/research_panel/<key>.pkl`
- key：universe / start / end / warmup / extras / 数据截止日 / schema
- 数据更新、extras 变化、schema 升级自动失效
- `--no-cache` 强制重建
- 全市场缓存约 707MB，200 池约 31MB
- 全市场 cache miss 首次构建约 60s；命中后读缓存约 2s（进程冷启动时约 3s）
- 因子计算与 forward returns 仍是主要计算成本；metrics 后端的收益仅作用于 quantile 部分

## 8. 注意事项

| 坑 | 说明 |
|---|---|
| 只输出 HTML | 不要再生成 JSON/PDF/PNG |
| 不再有 eval CLI | `research.eval` 已移除；数据层在 `research.data` |
| 方向不翻转 | 以 registry/族先验为准，不在样本内按 IC 符号翻转 |
| 小样本 | 5 组至少 25 只；不足直接报错 |
| `--no-robustness` | 跳过额外 3 个池的稳健性重算，主报告不变 |
| `--no-cache` | 不使用缓存，强制重新取数 |
| 缓存不是共享内存 | 5 个报告建议一个进程 `--batch` |
| 全市场仍慢 | 缓存主要省取数；因子计算、forward returns 和报告章节仍是主要耗时 |
