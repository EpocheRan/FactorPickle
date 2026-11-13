"""P0 基础资料数据域：主数据快照、名称变更、新股、停复牌、涨跌停、披露与业绩预告。

取数口径按数据的天然粒度组织（快照 / 交易日 / 自然日 / 报告期 / 区间），
由 ``acquisition`` 的 ``p0`` 数据集组调用；写入统一走 ``BasicRepository``。
"""

from __future__ import annotations

from .models import (
    DATASET_COLUMNS,
    DATASET_MODE,
    DATASET_TABLE,
    P0_DATASETS,
)
from .normalizer import (
    MAPPERS,
    map_disclosure_date,
    map_express,
    map_forecast,
    map_namechange,
    map_new_share,
    map_stk_limit,
    map_stock_basic,
    map_suspend_d,
)
from .repository import BasicRepository
from .service import (
    BasicDataService,
    STOP_ERRORS,
    compact,
    latest_quarter_end,
    quarter_ends,
)

__all__ = [
    "BasicDataService",
    "BasicRepository",
    "DATASET_COLUMNS",
    "DATASET_MODE",
    "DATASET_TABLE",
    "MAPPERS",
    "P0_DATASETS",
    "STOP_ERRORS",
    "compact",
    "latest_quarter_end",
    "map_disclosure_date",
    "map_express",
    "map_forecast",
    "map_namechange",
    "map_new_share",
    "map_stk_limit",
    "map_stock_basic",
    "map_suspend_d",
    "quarter_ends",
]
