# output 目录约定

> 运行期产物目录，**整个 `output/` 已在 `.gitignore` 中忽略**，不进版本库。
> 报告按生成日期分组；回测报告与因子报告各自独立目录。

```text
output/
├── reports/
│   ├── backtest/
│   │   └── YYYY-MM-DD/
│   │       └── <run_id>/
│   │           ├── report.html
│   │           ├── report_data.json
│   │           ├── metrics.json
│   │           ├── equity.csv
│   │           ├── orders.csv
│   │           └── ...
│   └── factor/
│       └── YYYY-MM-DD/
│           └── <factor_id>.html
├── cache/
│   ├── research_panel/
│   │   └── <key>.pkl
│   └── backtest/
├── logs/YYYY-MM-DD/*.log
├── screenshots/YYYY-MM-DD/*.png
└── tmp/YYYY-MM-DD/
```

## 规则

1. **因子报告只输出 HTML**：
   `reports/factor/<生成日>/<factor_id>.html`，不再生成 JSON/PDF/PNG。
2. **Panel 缓存**：
   `cache/research_panel/<key>.pkl`；key 包含 universe、区间、warmup、extras、数据截止日与 schema。
   `--no-cache` 可强制重建。
3. **回测报告**：
   `reports/backtest/<生成日>/<run_id>/`，由 `--out console,html` 显式开启 HTML；默认仅 console。
4. **日期取生成日**，不是回测区间；同一 `run_id` 跨日重跑会进入不同日期目录。
5. 日志、截图、临时文件分别写 `logs/`、`screenshots/`、`tmp/` 的日期子目录，不落 `output/` 根目录。

## 生成命令

```bash
# 回测（默认 console；要 HTML 加 --out console,html）
python -m backtest run strategies/models/low_turnover_top10.py \
    --start 2020-01-01 --end 2026-10-29 --out console,html

# 因子报告：只输出 HTML
python -m research.report --factor reversal.zjfz

# 批量因子报告
python -m research.report \
    --batch f1,f2,f3 \
    --universe pool:2022-01-04:200 \
    --profile quick \
    --no-robustness
```
