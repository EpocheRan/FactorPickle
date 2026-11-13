"""数据接口层（Data Interface）。

该包是行情、财务、交易日历等外部数据能力的统一入口，按职责拆分为：

- ``common``    通用连接、配置、限流、异常、交易日历
- ``market``    行情数据（OHLCV、实时行情、基础信息、概念板块）
- ``financial`` 财务数据（三大报表、估值、一致预期、板块归属）
- ``moneyflow`` / ``sentiment`` / ``concept`` / ``disclosure`` 资金、情绪、板块、披露
- ``basic``     P0 基础资料（名单快照、名称变更、新股、停复牌、涨跌停、预告/快报）
- ``api``       对外暴露的 MCP 工具函数

上层（MCPserver、position）只依赖本包，不再直接依赖数据源 SDK。
"""

__all__ = ["common", "market", "financial", "moneyflow", "sentiment", "concept", "disclosure", "basic", "api"]
