"""研究数据层的 QuestDB 访问（唯一入口，便于测试替换）。

优先走 HTTP /exec；当前环境里 REST 对带 WHERE 的表查询会返回 503，
第一次失败后就固定走 PostgreSQL 协议，避免每条 SQL 都先等一次 REST 超时。
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
import requests

from data_interface.common.config import get_config, resolve_questdb_host

logger = logging.getLogger("Research.Data.DB")
_rest_disabled = False

_cfg = get_config()
BASE = f"http://{resolve_questdb_host(_cfg)}:{_cfg['questdb'].get('rest_port', 9000)}"


@lru_cache(maxsize=1)
def _pg_client():
    from data_interface.common.questdb_client import QuestDBClient

    return QuestDBClient(get_config(), connect=False)


def _query_pg(sql: str) -> pd.DataFrame:
    client = _pg_client()
    if sql.lstrip().upper().startswith("SELECT"):
        frame = client.query_dataframe(sql)
        if frame is None:
            return pd.DataFrame()
        if frame.index.name == "ts" and "ts" not in frame.columns:
            frame = frame.reset_index()
        return frame
    client.execute(sql)
    return pd.DataFrame()


def query(sql: str, timeout: int = 300) -> pd.DataFrame:
    global _rest_disabled
    if not _rest_disabled:
        try:
            resp = requests.get(f"{BASE}/exec", params={"query": sql}, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            if "error" in payload:
                raise RuntimeError(payload["error"])
            dataset = payload.get("dataset") or []
            columns = [item["name"] for item in payload.get("columns", [])]
            return pd.DataFrame(dataset, columns=columns)
        except Exception as exc:  # noqa: BLE001 - REST 503/空响应时永久回退 PG
            _rest_disabled = True
            logger.warning("QuestDB REST 不可用，后续全部走 PostgreSQL 协议: %s", exc)
    return _query_pg(sql)
