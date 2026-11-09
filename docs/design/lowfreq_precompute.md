# 低频因子预计算 + ILP 写入方案

> 目标：让 `sentiment.holder_num_chg_q`、`event.unlock_pressure_60d`、`event.repurchase_intensity_120d`
> 三个已注册但因数据未接而跑不起来的因子，走「离线算一次 → 落库 → 报告直接读」的路径。

## 1. 影响范围一览

### 只读（不改结构）

| 表 | 用到的字段 | 用途 |
|---|---|---|
| `stk_holdernumber` | `ts`(公告日), `code`, `end_date`, `holder_num` | 股东户数序列 |
| `share_float` | `ts`(公告日), `code`, `float_date`, `float_share` | 解禁事件 |
| `repurchase` | `ts`, `code`, `amount`, `end_date` | 回购金额 |
| `fin_valuation_daily` | `code`, `ts`, `float_share` 或 `float_mv` | 流通股/流通市值分母 |
| `ohlcv_1d` | `ts`（type='stock'） | 交易日历 |

### 写入（不改结构，新增行）

| 表 | 新增内容 | 说明 |
|---|---|---|
| `factor_value_daily` | 3 个新 `factor_id` 的行 | 复用现有列：`ts, code, factor_id, value, quality, source, source_version, version, computed_at` |

**不新建表，不删改任何现有字段。**

新增 factor_id：

```
sentiment.holder_num_chg_q
event.unlock_pressure_60d
event.repurchase_intensity_120d
```

## 2. 行数估算

| 因子 | 写入粒度 | 行数 | 说明 |
|---|---|---|---|
| `holder_num_chg_q` | **事件日**（每次披露） | ≈ 32 万 | 5,821 只 × 约 55 个披露点 |
| `unlock_pressure_60d` | **日频** | ≈ 1,530 万 | 窗口随日期滑动，不能稀疏 |
| `repurchase_intensity_120d` | **日频** | ≈ 1,530 万 | 同理 |
| **合计** | | **≈ 3,090 万** | 约占现有 `factor_value_daily`（1,162 万）的 2.7 倍 |

为什么前者可以稀疏、后两者不行：

- 户数变化率在两个披露日之间是**常数** → 稀疏写入 + 读取时前值填充即可；
- 解禁压力 = 「未来 60 日解禁股数 / 流通股数」，窗口每天滑动 → 值每天变；
- 回购强度 = 「过去 120 日回购金额 / 流通市值」，分母每天变 → 值每天变。

## 3. 写入通道

现有写入只有 REST `INSERT`（约 2 千行/秒），3,090 万行需要 **4 小时以上**，不可接受。

| 通道 | 吞吐 | 结论 |
|---|---|---|
| REST `/exec` INSERT | ~2k 行/s | ❌ 太慢 |
| ILP `/write`（HTTP） | ~50 万行/s | ✅ 首选，需先修通格式 |
| psycopg `COPY`（8812） | ~10 万行/s | ✅ 备选，稳定 |

**实测**：`/write?precision=ns` 端点存在，但当前报文被判定为解析失败（返回 400，line protocol 报错）。需要按 QuestDB 要求补全表名/时间戳格式后重测；若仍失败，直接走 `COPY`（psycopg 已是依赖）。

线协议格式（待验证）：

```
factor_value_daily,code=000001,factor_id=sentiment.holder_num_chg_q,quality=1,source=lowfreq,source_version=v1 value=-0.0523 1735689600000000000
```

## 4. 预计算流程

```
交易日历(T) ─┐
             ├─> 因子值矩阵(ts × code) ─> 长表 ─> ILP/COPY ─> factor_value_daily
三张源表 ────┘                              （分批写入 + 每批校验行数）
```

### 4.1 股东户数变化率

```
1. 取 stk_holdernumber，按 (code, end_date) 去重，保留 ts 最大的版本（重述）
2. 按 code 排序，取上一期 holder_num
3. value = holder_num / prev − 1
4. 只写事件日（ts），不展开到日频
```

### 4.2 解禁压力

```
1. share_float 按 code 聚合 float_date → 解禁股数
2. 对每个交易日 t：sum(float_share where float_date ∈ (t, t+60])
3. 分母：当日流通股数（fin_valuation_daily.float_share）
4. value = 分子 / 分母，clip 到 [0, 5]
```

### 4.3 回购强度

```
1. repurchase 按 ts 聚合 amount
2. 对每个交易日 t：sum(amount where ts ∈ [t-120, t])
3. 分母：当日流通市值
4. value = 分子 / 分母
```

## 5. 读取路径改动

现状（`research/eval/data.py::stored_factor_panel`）：

```python
frame = _query("select ts, code, value ... where ts between ...")
return _pivot(...).reindex(index=close.index, columns=close.columns)
```

**精确 reindex，不做前值填充** —— 稀疏写入的户数因子会被读成大量 NaN。

必改：读取后按 code 做 `ffill`（并限制最多填充 400 个交易日，避免用两年前的旧值）。

## 6. 校验清单

| 检查 | 及格线 |
|---|---|
| 行数 | 户数 ≈ 32 万；两个日频各 ≈ 1,530 万（±5%） |
| 覆盖 | 每个交易日 ≥ 4,000 只有值（日频两项） |
| PIT | 抽 3 只股票，值的变化日 = 公告日，不是报告期末 |
| 重述 | 同一 `end_date` 多版本时，生效日 = 最新公告日 |
| 对账 | 抽 5 只股票手算，与库内值误差 < 1e-6 |
| 回归 | 现有 118 个 STORED 因子行数不变 |

## 7. 风险

| 风险 | 应对 |
|---|---|
| 3,090 万行写入把库撑大 | 先写 1 个月样本压测，确认磁盘增长 |
| 解禁数据 `float_date` 缺失 | 跳过该行并计数，超过 5% 则停下排查 |
| `fin_valuation_daily` 流通股缺失 | 用 `float_mv / close` 兜底 |
| 读取 ffill 引入陈旧值 | 限制最大填充 400 个交易日 |
| ILP 写失败无事务 | 分批提交，每批写后 `count()` 校验 |
