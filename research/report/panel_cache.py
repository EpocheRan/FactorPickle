"""本地 Panel 缓存。

缓存的是组装好的 ``Panel``（宽表 + 元数据），不是 SQL 结果、也不是 HTML。
key 包含 universe / 区间 / warmup / extras / 数据截止日 / schema 版本；
数据更新或面板逻辑变化时自动失效。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from pathlib import Path

from research.data.db import query

logger = logging.getLogger("Research.Report.PanelCache")

CACHE_VERSION = 1
CACHE_DIR = Path("output/cache/research_panel")


def data_version(extras: tuple[str, ...]) -> str:
    """用相关表的最大 ts 做数据版本；查询失败时不使用缓存。"""
    try:
        stamps = [str(query("select max(ts) from ohlcv_1d where type='stock'").iloc[0, 0])]
        for dataset in extras:
            if dataset not in {"margin_detail", "moneyflow_daily", "fin_valuation_daily"}:
                continue
            stamps.append(str(query(f"select max(ts) from {dataset}").iloc[0, 0]))
        return "|".join(stamps)
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法确定数据版本，禁用本次缓存: %s", exc)
        return ""


def key(*, universe: str, start: str, end: str | None, warmup_days: int,
        extras: tuple[str, ...], min_amount: float | None, max_amount: float | None,
        version: str) -> str:
    payload = {
        "cache_version": CACHE_VERSION,
        "universe": universe,
        "start": start,
        "end": end,
        "warmup_days": warmup_days,
        "extras": sorted(extras),
        "min_amount": min_amount,
        "max_amount": max_amount,
        "data_version": version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:20]


def read(path: Path):
    try:
        with path.open("rb") as handle:
            obj = pickle.load(handle)
        return obj
    except Exception as exc:  # noqa: BLE001
        logger.warning("Panel 缓存读取失败，忽略: %s", exc)
        path.unlink(missing_ok=True)
        return None


def write(path: Path, panel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    panel._frame = None
    with tmp.open("wb") as handle:
        pickle.dump(panel, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)
    logger.info("Panel 缓存已写入: %s（%.1f MB）", path,
                path.stat().st_size / 1024 / 1024)
