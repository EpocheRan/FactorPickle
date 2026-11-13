# factor_engine

`factor_engine` 计算并物化 **custom factor**。Basic 数据保留在原来的源表里，不进入因子资产。

## 当前表

| 表 | 用途 |
|---|---|
| `basic_data_catalog` | Basic 字段目录：`basic_id -> source_table.source_column` |
| `custom_factor_registry` | 当前 custom 因子定义，一行一个 `factor_id` |
| `custom_factor_value_daily` | custom 因子日频值 |
| `custom_factor_value_announce` | custom 事件型因子值 |
| `custom_factor_value_latest` | 最新值缓存 |
| `custom_factor_alias` | 当前别名 |
| `custom_factor_lineage` | custom 因子依赖 |
| `custom_factor_build_state` | build 状态 |
| `custom_factor_audit_state` | 对账状态 |
| `asset_catalog` | Basic + Custom 的总览 view |

`basic_data_daily` 不是实体表；Basic 历史直接查源表，字段映射查 `basic_data_catalog`。

## 写入边界

- Basic 源表由 `data_interface` 写。
- `custom_factor_*` 由 `factor_engine` 写。
- custom 值必须满足：

```text
computed_at >= 2026-11-13T15:15:00+08:00
```

`ts` 可以是任意历史日期；cutover 限制的是写入时间，不是数据时间。

## 计算流程

```text
FactorEngineService.build(ids, start, end, codes)
  ├─ _ensure_registry()            当前定义写入 custom_factor_registry
  ├─ 分流：legacy / fundamental / valuation / compute
  ├─ PriceData.load()
  ├─ 计算
  ├─ 每 5 万行 upsert custom_factor_value_daily + custom_factor_value_latest
  └─ 写 custom_factor_build_state
```

## 约束

- `custom_factor_registry` 一个 `factor_id` 只对应当前定义；定义变更必须新 ID，旧 ID 删除。
- 没有 `version` / `definition_hash`。
- 没有旧表、legacy 表或兼容视图。
- `custom_factor_value_daily` 的 `(ts, code, factor_id)` 是去重键。
- Basic 不写入 custom 表。
