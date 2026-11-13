"""acquisition.backfill：历史回补（业务逻辑层）。

设计见 ``docs/design/acquisition_backfill_v1.md``。核心约定：

- 续跑判据只认 ``max(ts)``，不看行数（行数多 ≠ 覆盖全）；
- 写入统一走 ``write()``：500 行/批、NaN→None、单批失败不中断；
- 阶段用语义名注册，旧编号名保留为 alias；
- ``--dry-run`` 只出缺口报告，不写库。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable, Sequence

import pandas as pd
import requests

from data_interface.common.config import get_config, resolve_questdb_host

logger = logging.getLogger("acquisition.backfill")

_cfg = get_config()
BASE = f"http://{resolve_questdb_host(_cfg)}:{_cfg['questdb'].get('rest_port', 9000)}"

# ---------------------------------------------------------------- 基础设施
def sql(query: str, *, timeout: int = 120, retries: int = 5) -> list[list[Any]]:
    last: str | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(f"{BASE}/exec", params={"query": query}, timeout=timeout)
            if resp.status_code != 200 or not resp.text.strip():
                last = f"HTTP {resp.status_code} 空响应"
                time.sleep(3 * (attempt + 1))
                continue
            payload = resp.json()
            if "error" in payload:
                raise RuntimeError(f"{payload['error']} :: {query[:160]}")
            return payload.get("dataset") or []
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:120]
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"QuestDB 查询失败（{last}）:: {query[:160]}")


def _clean(value: Any) -> Any:
    """QuestDB 的 DOUBLE/TIMESTAMP 列不接受 NaN/NaT。"""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)) and pd.isna(value):
        return None
    return value


def write(table: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], *,
          chunk: int = 500) -> int:
    """分批写入：500 行/批，NaN→None，单批失败只告警。"""
    if not rows:
        return 0
    total = 0
    cols = ", ".join(f'"{c}"' for c in columns)
    for offset in range(0, len(rows), chunk):
        batch = rows[offset: offset + chunk]
        values = ", ".join(
            "(" + ", ".join(_literal(_clean(v)) for v in row) + ")" for row in batch
        )
        try:
            sql(f'INSERT INTO "{table}" ({cols}) VALUES {values}', timeout=180)
            total += len(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 第 %s 批写入失败: %s", table, offset // chunk, str(exc)[:100])
    return total


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return f"'{pd.Timestamp(value).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}'"
    return "'" + str(value).replace("'", "''") + "'"


def max_ts(table: str, ts_col: str = "ts", where: str = "") -> pd.Timestamp | None:
    clause = f" where {where}" if where else ""
    rows = sql(f"select max({ts_col}) from {table}{clause}")
    if not rows or rows[0][0] in (None, ""):
        return None
    ts = pd.Timestamp(rows[0][0])
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def gap_report(table: str, start: str, end: str, *, ts_col: str = "ts",
               where: str = "") -> dict:
    """逐年覆盖率（不写库，供 --dry-run）。"""
    clause = f" and {where}" if where else ""
    rows = sql(
        f"select year({ts_col}) y, count() n from {table} "
        f"where {ts_col} between '{start}' and '{end}'{clause} group by y order by y"
    )
    years = {int(r[0]): int(r[1]) for r in rows}
    return {
        "table": table,
        "start": start,
        "end": end,
        "years": years,
        "first": str(min(years)) if years else None,
        "last": str(max(years)) if years else None,
    }


def fetch_range(api, name: str, start: str, end: str, *, cap: int,
                key: tuple[str, ...] = ("ts_code", "ann_date"), **kwargs):
    """区间取数：未触顶一次拿；命中单次上限自动改按月分片并去重。"""
    frame = api.call(name, start_date=start.replace("-", ""), end_date=end.replace("-", ""), **kwargs)
    if frame is None or len(frame) < cap:
        return frame
    logger.info("%s 命中单次上限 %s 行，改按月分片", name, cap)
    parts = []
    for lo, hi in _month_chunks(start, end):
        part = api.call(name, start_date=lo.replace("-", ""), end_date=hi.replace("-", ""), **kwargs)
        if part is not None and not part.empty:
            parts.append(part)
    if not parts:
        return None
    merged = pd.concat(parts, ignore_index=True)
    keep = [c for c in key if c in merged.columns]
    return merged.drop_duplicates(subset=keep) if keep else merged


def _month_chunks(start: str, end: str) -> list[tuple[str, str]]:
    begin = pd.Timestamp(start).normalize()
    finish = pd.Timestamp(end).normalize()
    chunks: list[tuple[str, str]] = []
    cursor = begin.replace(day=1)
    while cursor <= finish:
        nxt = cursor + pd.DateOffset(months=1)
        lo, hi = max(cursor, begin), min(nxt - pd.Timedelta(days=1), finish)
        if lo <= hi:
            chunks.append((str(lo.date()), str(hi.date())))
        cursor = nxt
    return chunks


def trading_days(start: str, end: str) -> list[str]:
    rows = sql(
        "select ts from trading_days "
        f"where ts between '{start}' and '{end}' order by ts"
    )
    return [str(r[0])[:10] for r in rows]


class Api:
    """带重试与限速的 Tushare 包装。"""

    def __init__(self, sleep: float = 0.12, retries: int = 3):
        import tushare as ts

        token = _cfg.get("tushare", {}).get("token")
        if not token:
            raise RuntimeError("config.json 缺少 tushare.token")
        self.pro = ts.pro_api(token)
        self.sleep = sleep
        self.retries = retries

    def call(self, name: str, **kwargs) -> pd.DataFrame | None:
        for attempt in range(self.retries):
            try:
                frame = getattr(self.pro, name)(**kwargs)
                time.sleep(self.sleep)
                return frame
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 调用失败(%s/%s): %s", name, attempt + 1, self.retries, str(exc)[:80])
                time.sleep(2 * (attempt + 1))
        return None


# ---------------------------------------------------------------- 阶段
@dataclass(frozen=True)
class Scope:
    start: str
    end: str
    dry_run: bool = False
    chunk: int = 500
    min_codes: int = 2500
    codes: tuple[str, ...] = ()
    datasets: tuple[str, ...] = ()
    fresh: bool = False


@dataclass
class PhaseResult:
    phase: str
    rows: int
    skipped: bool = False
    gaps: list[dict] | None = None
    done: bool = True
    blocked: tuple[str, ...] = ()
    #: 结构化明细（如 valuation_derived 的逐月覆盖），供 CLI 打印/落盘。
    details: dict | None = None


PHASES: dict[str, Callable[[Api, Scope], PhaseResult]] = {}


def phase(name: str, *, tables: tuple[str, ...] = ()) -> Callable:
    def decorator(fn: Callable[[Api, Scope], PhaseResult]) -> Callable:
        fn.tables = tables  # type: ignore[attr-defined]
        PHASES[name] = fn
        return fn

    return decorator


def resolve_phase(name: str) -> Callable[[Api, Scope], PhaseResult]:
    if name not in PHASES:
        raise KeyError(f"未知阶段: {name!r}；可用: {sorted(PHASES)}")
    return PHASES[name]


def run_phase(name: str, scope: Scope) -> PhaseResult:
    from . import phases as _phases  # noqa: F401 - 触发注册

    impl = resolve_phase(name)
    return impl(_LazyApi(), scope)


class _LazyApi:
    """Only build the Tushare client when a phase actually asks for data.

    Local-only phases (``valuation_derived``) need zero vendor calls, so
    constructing ``Api()`` up front would demand a token for no reason.
    """

    __slots__ = ("_api",)

    def __init__(self) -> None:
        self._api: "Api | None" = None

    def call(self, name: str, **kwargs):
        if self._api is None:
            self._api = Api()
        return self._api.call(name, **kwargs)


__all__ = [
    "Api",
    "PHASES",
    "PhaseResult",
    "Scope",
    "fetch_range",
    "gap_report",
    "max_ts",
    "phase",
    "resolve_phase",
    "run_phase",
    "sql",
    "trading_days",
    "write",
]