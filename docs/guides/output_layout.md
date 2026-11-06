# output 目录约定

> 运行时产物目录，**整个 `output/` 已在 `.gitignore` 中忽略**，不进版本库。
> 报告按生成日期分组：回测报告与因子评估报告各自独立目录。

```
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
│           ├── <factor_id>.html
│           ├── <factor_id>.json
│           └── eval_YYYYMMDDTHHMM.csv
├── logs/YYYY-MM-DD/*.log
├── screenshots/YYYY-MM-DD/*.png
├── tmp/YYYY-MM-DD/                 # 一次性脚本/临时文件
├── cache/                          # 回测/因子缓存
├── concept/                        # 概念数据
├── composite/                      # 合成因子实验
└── ...                             # 其他研究中间数据
```

## 规则

1. **回测报告**：`reports/backtest/<生成日>/<run_id>/`，报告、JSON、CSV 同目录；由 `CsvOutput` / `HtmlReportOutput` 自动写入。
2. **因子报告**：`reports/factor/<生成日>/<factor_id>.html/json`；由 `research.report.runner.generate` 自动加日期目录。
3. **评估 CSV**：`reports/factor/<生成日>/eval_*.csv`；由 `research.eval.cli` 写入。
4. **日志/截图/临时文件**：分别写 `logs/`、`screenshots/`、`tmp/` 的日期子目录，不落 `output/` 根目录。
5. **日期取生成日**，不是回测区间；同一 `run_id` 跨日重跑会进入不同日期目录。

## 生成命令

```bash
# 回测（默认 console + html）
python -m backtest run strategies/models/low_turnover_top10.py \
    --start 2020-01-01 --end 2026-10-29

# 重新渲染最新回测报告
python scripts/report_backtest.py --latest

# 因子研究报告
python -m research.report.cli --factor momentum.rsrs --pool 200

# 批量因子评估 CSV
python -m research.eval.cli --factors momentum.rsrs,liquidity.turnover_std_bias_42_252
```
