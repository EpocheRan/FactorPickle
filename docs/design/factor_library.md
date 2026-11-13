# 因子资产布局

## 1. Basic 与 Custom

```text
Basic  = 数据
Custom = 因子
```

### Basic

Basic 是数据源直接提供的数据项，包括供应商计算的 PE/PB/PS、股息率、财务指标、涨跌停价、指数权重、宏观等。

来源表保留原名：

- `stock_basic_info`
- `fin_income_statement`
- `fin_balance_sheet`
- `fin_cash_flow`
- `fin_valuation_daily`
- `fin_disclosure_calendar`
- `moneyflow_daily`
- `margin_detail`
- `concept_board_dim`
- `concept_board_daily`
- `concept_member_snapshot`
- `index_weight`
- `announcement`
- `block_trade`
- `top_list`
- `repurchase`
- `stk_holdernumber`
- `fund_basic_info`
- `ohlcv_1d` / `ohlcv_5m` / ...
- `trading_days`

Basic 不做因子版本管理，不进入 Factor Asset。

### Custom

Custom 是所有基于 Basic 定义出来的因子/信号：

- `rank(pe_ttm)`
- momentum / reversal / volatility
- technical / liquidity
- composite / ensemble
- ML / AlphaSearch
- event fusion / conditional / smoothing
- Weak5 / Smooth60 / Core5

只有 Custom 进入 Factor Asset。

## 2. Basic 存储

```text
basic_data_catalog
```

字段：

```text
updated_at
basic_id
name
category
source
source_table
source_column
data_type
freq
pit
unit
description
status
```

`basic_id` 直接映射到源表字段：

```text
basic_id = fin_valuation_daily.pe_ttm
source_table = fin_valuation_daily
source_column = pe_ttm
```

不创建 `basic_data_daily` 实体表。Basic 历史直接查源表；需要统一访问时使用只读 view，而不是复制历史。

## 3. Custom 存储

### `custom_factor_registry`

```text
updated_at
factor_id
name
category
subcategory
source_dataset
source_vendor
source_fields
freq
window
lag
value_kind
update_mode
unit
direction
price_basis
dependencies
warmup
formula
enabled
materialization
```

- 一个 `factor_id` 只对应一个当前定义
- 没有 `version`
- 没有 `definition_hash`
- 定义变更必须新 `factor_id`
- 没有 legacy/migrated 字段

### `custom_factor_value_daily`

```text
ts
code
factor_id
value
quality
computed_at
```

约束：

```text
computed_at >= 2026-11-13T15:15:00+08:00
```

`ts` 可以是历史日期。允许在 cutover 之后回算历史值，但禁止写入 cutover 之前产生的值。

去重键：

```text
(ts, code, factor_id)
```

### `custom_factor_value_announce`

```text
ts
code
factor_id
report_period
announce_date
lag_days
value
quality
computed_at
```

### 其它 custom 表

- `custom_factor_value_latest`
- `custom_factor_alias`
- `custom_factor_lineage`
- `custom_factor_build_state`
- `custom_factor_audit_state`

## 4. 查询规则

- Basic：查 `basic_data_catalog` 找到 `source_table` / `source_column`，直接查源表。
- Custom：查 `custom_factor_registry` 和 `custom_factor_value_daily`。
- 统一发现：查 `asset_catalog` view。
- 不再存在 `factor_registry`、`factor_value_daily`、`factor_value_latest`、`factor_alias`、`factor_build_state`、`factor_audit_state`。

## 5. 物化规则

- Basic 不复制到因子资产。
- Custom 只有已注册 factor 才允许有值。
- 未注册 custom factor 不 materialize。
- 允许 cutover 后回算历史 `ts`，但写入时间必须晚于 cutover。

## 6. Screener 派生层

Screener 读取：

```text
screener_daily_wide_view
```

它是只读 view。底层：

```text
screener_feature_daily
```

是 screener 自己的 derived cache，不是 Basic，也不是 Custom factor asset。写入仍由 `factor_engine.wide_store` 负责；业务查询只读 view。
