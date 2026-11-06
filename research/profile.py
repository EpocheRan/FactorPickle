"""报告内容（F）：profile 的存取与解析。

profile 只描述「报告展示哪些章节」；股票池 / 区间 / 成本 / horizon 是运行参数，
不属于 profile。profile 以 name 为唯一键，不再有版本号。

存储：QuestDB ``eval_profile``（旧表名保留兼容）；DB 不可用时回落到内置默认。
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from research.report_sections import DEFAULT_SECTIONS, QUICK_SECTIONS, SECTIONS

TABLE = "eval_profile"


def _query(sql: str, timeout: int = 60):
    """延迟导入，避免 profile 与数据层初始化顺序互相牵制。"""
    from research.data.db import query

    return query(sql, timeout=timeout)

#: 内置 profile：DB 首次初始化时灌入，也是 DB 不可用时的兜底
BUILTIN: dict[str, dict] = {
    "default": {
        "description": "完整单因子报告：定义、时间、空间、评级、附录",
        "items": DEFAULT_SECTIONS,
        "reference": "",
    },
    "quick": {
        "description": "快筛：定义、时间、评级",
        "items": QUICK_SECTIONS,
        "reference": "",
    },
}

DDL = f"""CREATE TABLE IF NOT EXISTS "{TABLE}" (
    updated_at TIMESTAMP,
    name SYMBOL,
    version INT,
    is_default BOOLEAN,
    description STRING,
    items STRING,
    item_params STRING,
    thresholds STRING,
    reference SYMBOL,
    profile_hash SYMBOL,
    enabled BOOLEAN
) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
DEDUP UPSERT KEYS(updated_at, name, version)"""


def _hash(items: tuple[str, ...], params: dict, thresholds: dict, reference: str) -> str:
    payload = json.dumps(
        {"items": sorted(items), "params": params, "thresholds": thresholds, "reference": reference},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ensure_table() -> None:
    _query(DDL, timeout=60)


def _latest_rows() -> pd.DataFrame:
    frame = _query(
        f"select name, version, items, item_params, thresholds, reference, "
        f"description, profile_hash from {TABLE} "
        "latest on updated_at partition by name",
        timeout=60,
    )
    return frame


def seed() -> list[str]:
    """用内置 profile 覆盖同名记录（name 唯一）。"""
    ensure_table()
    written: list[str] = []
    for name, payload in BUILTIN.items():
        save(
            name,
            items=tuple(payload["items"]),
            reference=str(payload["reference"]),
            description=str(payload["description"]),
        )
        written.append(name)
    return written


def load(name: str = "default") -> dict:
    """读 profile；DB 不可用或不存在时回落内置默认。"""
    try:
        frame = _latest_rows()
        rows = frame[frame["name"].astype(str) == str(name)]
        if not rows.empty:
            row = rows.iloc[-1]
            items = tuple(str(x) for x in json.loads(row["items"]))
            # 旧库里的 default/quick 还是旧评估项 id；遇到就回落到内置章节。
            legacy_builtin = name in BUILTIN and not set(items).issubset(SECTIONS)
            if not legacy_builtin:
                return {
                    "name": str(row["name"]),
                    "items": items,
                    "item_params": json.loads(row["item_params"] or "{}"),
                    "thresholds": json.loads(row["thresholds"] or "{}"),
                    "reference": str(row["reference"] or ""),
                    "description": str(row["description"] or ""),
                    "profile_hash": str(row["profile_hash"] or ""),
                    "source": "db",
                }
    except Exception:  # noqa: BLE001 - DB 不可用不阻断评估
        pass
    if name in BUILTIN:
        payload = BUILTIN[name]
        items = tuple(payload["items"])
        return {
            "name": name,
            "items": items,
            "item_params": {},
            "thresholds": {},
            "reference": str(payload["reference"]),
            "description": str(payload["description"]),
            "profile_hash": _hash(items, {}, {}, str(payload["reference"])),
            "source": "builtin",
        }
    raise KeyError(f"未知 profile: {name!r}；可用: {sorted(BUILTIN)}（或库内自定义）")


def save(
    name: str,
    *,
    items: tuple[str, ...],
    item_params: dict | None = None,
    thresholds: dict | None = None,
    reference: str = "",
    description: str = "",
) -> dict:
    """按 name 覆盖 profile。

    ``eval_profile`` 的 ``version`` 列是历史遗留列，仅为兼容旧表保留；
    对外 API 不再暴露版本，name 是唯一逻辑键。
    """
    ensure_table()
    item_params = item_params or {}
    thresholds = thresholds or {}
    version = 1  # legacy column, not exposed; latest row by name wins
    digest = _hash(tuple(items), item_params, thresholds, reference)
    now = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    payload = json.dumps(list(items), ensure_ascii=False).replace("'", "''")
    params = json.dumps(item_params, ensure_ascii=False, sort_keys=True).replace("'", "''")
    limits = json.dumps(thresholds, ensure_ascii=False, sort_keys=True).replace("'", "''")
    desc = str(description).replace("'", "''")
    _query(
        f'insert into {TABLE} values ('
        f"'{now}', '{name}', {version}, false, '{desc}', '{payload}', "
        f"'{params}', '{limits}', '{reference}', '{digest}', true)",
        timeout=60,
    )
    return {"name": name, "profile_hash": digest}


def list_profiles() -> pd.DataFrame:
    try:
        frame = _latest_rows()
    except Exception:  # noqa: BLE001
        frame = pd.DataFrame()
    if frame.empty:
        return pd.DataFrame(
            [
                {"name": name, "items": len(payload["items"]), "source": "builtin"}
                for name, payload in BUILTIN.items()
            ]
        )
    if "version" in frame.columns:
        frame = frame.drop(columns=["version"])
    return frame.assign(source="db")


__all__ = ["BUILTIN", "TABLE", "ensure_table", "list_profiles", "load", "save", "seed"]
