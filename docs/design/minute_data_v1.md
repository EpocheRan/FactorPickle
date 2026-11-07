# 分钟线数据 v1

> 状态：已上线（2026-11-07）。覆盖沪深 300 近一年 5min，字段与日线完全一致。

## 1. 背景与取舍

日线足够支撑因子研究与选股，但**日内结构**（封板质量、尾盘竞价、VWAP 偏离、分时量能）
必须靠分钟线。此前链路里分钟线是断的：

| 尝试 | 结果 |
|---|---|
| Tushare `stk_mins` | 需 5,000 积分，当前账号不足 |
| AkShare 东财分时 | 本机被断连，且限频 |
| BaoStock 分钟 | 5/15/30/60min 可查，但查询会挂起（历史坑） |
| 新浪分时 | 只有近端约 21 个交易日 |
| **zzshare `stk_mins`** | ✅ 1/5/15/30/60min，可回溯到 **2010-01-04**，带 token 60 次/分 |

## 2. 接口约束（实测）

| 约束 | 数值 | 处理 |
|---|---|---|
| 单次返回上限 | **1,000 根 bar** | 按频率预估每窗 bar 数切窗（5min 每窗 26 天），仍触顶再二分 |
| 限频 | 60 次/分（token），30 次/分（匿名） | 源内匀速节流 + 3 次重试 |
| 历史深度 | 2010-01-04 起 | 再早返回空 |
| 复权参数 | `adjust` 传 `qfq/hfq/raw` 结果完全相同 | **接口只给不复权价**，复权自己做 |

> 单次上限的代价：5min 全历史（2016 至今）每只约 127 次请求、2.1 分钟；
> 全市场 5,574 只约 **8.2 天**连续跑，因此实际按「池子 + 窗口」补。

## 3. 存储契约

分钟表与日线**同构**（12 字段），`DEDUP UPSERT KEYS(ts, code, market, type)`：

```
ts, open, high, low, close, volume, amount, turnover, adj_factor, code, market, type
```

| 表 | 分区 |
|---|---|
| `ohlcv_1m` / `ohlcv_5m` | HOUR |
| `ohlcv_15m` / `ohlcv_30m` / `ohlcv_60m` | DAY |

- 价格：**不复权（raw）**，复权在读取端按 `adj_factor` 计算；
- `market`：交易所（`SSE/SZSE/BSE`），`type`：品种（`stock/etf/lof/fund/idx`）。

## 4. 复权与换手率

**adj_factor**：除权是日级事件，同一交易日内所有分钟 bar 共用日线因子——
按 `(code, date)` 从 `ohlcv_1d` join 写入：

```
hfq_minute = raw_minute × adj_factor(当日)
qfq_minute = raw_minute × adj_factor(当日) / adj_factor(最新)
```

**turnover**：接口不提供，用日线隐含的流通股本折算：

```
turnover_bar(%) = volume_bar / volume_daily × turnover_daily
```

这样**日内加总严格等于日线换手率**（实测 000001 某日 0.4512 vs 0.4512）。
日线缺换手率的交易日，用同股最近可用比例前/后向填充。

## 5. 质量规则

- **停牌日假 bar 必须丢弃**：601059 停牌期间 zzshare 仍返回 48 根/天（价格平盘、成交量 0）。
  规则：`(code, date)` 在 `ohlcv_1d` 无记录 → 视为停牌，不写库；
- 只写 `type='stock'` 的日线匹配；ETF/指数分钟线暂不纳入；
- 5min 日内 48 根（09:35–11:30、13:05–15:00），无 09:30 独立 bar。

## 6. 覆盖（2026-11-07 实测）

| 表 | 行数 | 代码 | 区间 |
|---|---:|---:|---|
| `ohlcv_5m` | **3,820,936** | 332（330 只沪深300 + 2 只试点） | 2025-11-07 ~ 2026-11-06 |
| `ohlcv_1m` | 5,280 | 2 | 2026-10-23 ~ 2026-11-06 |

- `adj_factor` 非空率 100%，`turnover` 非空率 100%；
- 与日线一致性：5min 聚合的开/高/低/收/量/额与 `ohlcv_1d` 完全相等
  （600519 2026-11-06：4,284,772 股、6,199,528,291 元）。

## 7. 工具

| 脚本 | 作用 |
|---|---|
| `scripts/export_index_minutes.py` | 按指数成分导出分钟线为 CSV（含 adj/turnover，断点续跑） |
| `scripts/import_minutes_csv.py` | 把 CSV 写入分钟表（join 日线因子） |
| `scripts/fetch_minute_pilot.py` | 小样本冒烟（`--source zzshare`） |

```bash
# 导出沪深300近一年 5min（约 86 分钟）
python scripts/export_index_minutes.py \
    --index 000300.SH --start 2025-11-07 --end 2026-11-06 --freq 5min \
    --out output/hs300_5min_1y.csv

# 落库（3,820,936 行，约 13 分钟）
python scripts/import_minutes_csv.py \
    --csv output/hs300_5min_1y.csv --freq 5min
```

## 8. 待定

- **保留窗口**：全市场 1min 一天约 134 万行（5,600 × 240），需要先定保留策略；
- **全市场 5min**：一次全历史约 8.2 天，建议只做池子 + 近端增量；
- **`fin_valuation_daily` 单位不一致**：2017 年行是「万股/万元」，2026 年行是「股/元」，
  本次换手率用日线比例绕开了该问题，但表本身需要单独修。
