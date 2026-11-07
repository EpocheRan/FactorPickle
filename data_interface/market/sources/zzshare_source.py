"""zzshare（zzquant）行情源：分钟线为主、日线为辅的补充源。

定位
----
- **分钟线**：``stk_mins`` 支持 1/5/15/30/60min，带 token 后 60 次/分，
  可按时间区间一次取多天，是当前链路里最稳的分钟源（东财在本机被断连、
  baostock 分钟查询会挂起、新浪只有近端 21 天）。
- **日线**：``daily`` 带 token 后 180 次/分，用作交叉校验/兜底。

口径
----
- ``vol`` 单位为**股**、``amount`` 为**元**，与库内一致，不做换算；
- ``trade_time`` 为北京时间墙钟（``YYYYMMDDHHMM``），转成 naive timestamp 存储，
  与 ``fetcher._ts_to_text`` 的写入口径一致；
- 接口返回按时间**倒序**，这里统一升序并去重。

限频
----
token 下 ``stk_mins`` 60 次/分、``daily`` 180 次/分；这里按 endpoint 做最小间隔
节流（默认每分钟次数可在 ``config.zzshare.requests_per_minute`` 覆盖）。
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from data_interface.common.config import get_config
from data_interface.common.exceptions import DataNotFoundError, SourceUnavailableError
from data_interface.common.security_code import (
    exchange_of,
    normalize_code,
    ticker_type,
    to_ts_code,
)

logger = logging.getLogger("DataInterface.Zzshare")

MINUTE_FREQS = ("1min", "5min", "15min", "30min", "60min")
_OHLC = ("open", "high", "low", "close", "volume", "amount")


class _RateLimiter:
    """按最小间隔节流（简单匀速，够用且不引人额外依赖）。"""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / max(float(per_minute), 1.0)
        self._next_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next_at:
            time.sleep(self._next_at - now)
            now = time.monotonic()
        self._next_at = now + self.interval


class ZzshareMarketSource:
    """zzshare 行情适配器（分钟线 + 日线）。"""

    name = "zzshare"

    def __init__(self, config: dict | None = None, client=None):
        self.config = config or get_config()
        section = (self.config.get("zzshare") or {}) if isinstance(self.config, dict) else {}
        self.token = (section.get("token") or "").strip()
        self.enabled = bool(section.get("enabled", True))
        self.max_retries = max(1, int(section.get("max_retries", 3)))
        self.timeout = float(section.get("timeout_seconds", 15))
        rates = section.get("requests_per_minute") or {}
        self._limiters = {
            "stk_mins": _RateLimiter(float(rates.get("stk_mins", 60))),
            "daily": _RateLimiter(float(rates.get("daily", 180))),
        }
        self._client = client

    # ------------------------------------------------------------------ 客户端
    @property
    def client(self):
        if self._client is None:
            if not self.token:
                raise SourceUnavailableError(
                    "zzshare 未配置 token（config.zzshare.token）", source="zzshare"
                )
            try:
                from zzshare.client import DataApi
            except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确指引
                raise SourceUnavailableError(
                    "未安装 zzshare：请执行 `uv pip install zzshare`", source="zzshare"
                ) from exc
            self._client = DataApi(token=self.token)
        return self._client

    @staticmethod
    def available(freq: str) -> bool:
        freq = str(freq or "").lower()
        return freq in MINUTE_FREQS or freq == "d"

    # ------------------------------------------------------------------ 取数
    def _call(self, endpoint: str, **kwargs) -> pd.DataFrame:
        limiter = self._limiters[endpoint]
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            limiter.wait()
            try:
                frame = getattr(self.client, endpoint)(**kwargs)
                return frame if frame is not None else pd.DataFrame()
            except Exception as exc:  # noqa: BLE001 - 网络/限频统一重试
                last = exc
                logger.warning("zzshare %s 失败（第 %s/%s 次）：%s", endpoint, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(min(2.0 * attempt, 10.0))
        raise SourceUnavailableError(
            f"zzshare {endpoint} 重试耗尽: {last}", source="zzshare"
        )

    def fetch_ohlc(
        self, stock_code: str, start_date: str, end_date: str, freq: str
    ) -> pd.DataFrame:
        freq = str(freq or "").lower()
        code = normalize_code(stock_code)
        if not code:
            raise DataNotFoundError("股票代码为空", source="zzshare")
        if freq in MINUTE_FREQS:
            return self._fetch_minute(code, start_date, end_date, freq)
        if freq == "d":
            return self._fetch_daily(code, start_date, end_date)
        raise SourceUnavailableError(f"zzshare 不支持频率 {freq}", source="zzshare")

    def _fetch_minute(self, code: str, start_date: str, end_date: str, freq: str) -> pd.DataFrame:
        raw = self._fetch_minute_chunked(code, str(start_date)[:10], str(end_date)[:10], freq)
        if raw is None or raw.empty:
            return pd.DataFrame(columns=list(_OHLC))
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(raw["trade_time"].astype(str), format="%Y%m%d%H%M", errors="coerce"),
                "open": pd.to_numeric(raw.get("open"), errors="coerce"),
                "high": pd.to_numeric(raw.get("high"), errors="coerce"),
                "low": pd.to_numeric(raw.get("low"), errors="coerce"),
                "close": pd.to_numeric(raw.get("close"), errors="coerce"),
                "volume": pd.to_numeric(raw.get("vol"), errors="coerce"),
                "amount": pd.to_numeric(raw.get("amount"), errors="coerce"),
            }
        ).dropna(subset=["ts", "close"])
        frame = frame.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")
        frame = frame.set_index("ts")
        start_dt = pd.to_datetime(str(start_date)[:10])
        end_dt = pd.to_datetime(str(end_date)[:10]) + pd.Timedelta(days=1)
        frame = frame[(frame.index >= start_dt) & (frame.index < end_dt)]
        # 与 sina/baostock 一致：带上 code，写库层靠它定位标的；market/type 显式给出，
        # 避免再走「按 6 位代码猜」的旧逻辑（000001 会踩指数/个股冲突）。
        frame["code"] = code
        frame["market"] = exchange_of(code)
        frame["type"] = ticker_type(code)
        return frame

    #: ``stk_mins`` 单次最多返回 1000 根 bar。直接按整段请求会被截断，
    #: 而"从整段开始二分"会白白多发一倍请求（内部节点也要发一次），
    #: 所以先按频率估算每日 bar 数，切成 <950 根的小窗口，再对仍触顶的窗口二分。
    _MINUTE_ROW_CAP = 1000
    _BARS_PER_DAY = {"1min": 240, "5min": 48, "15min": 16, "30min": 8, "60min": 4}

    def _minute_chunk_days(self, freq: str) -> int:
        per_day = self._BARS_PER_DAY.get(freq, 48)
        trade_days = max(1, int(950 / per_day))
        return max(1, int(trade_days * 7 / 5))  # 交易日 -> 自然日（含周末）

    def _fetch_minute_chunked(
        self, code: str, start: str, end: str, freq: str, depth: int = 0
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        cursor = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        step = self._minute_chunk_days(freq)
        while cursor <= end_dt:
            chunk_end = min(cursor + pd.Timedelta(days=step - 1), end_dt)
            part = self._fetch_minute_window(code, cursor, chunk_end, freq, depth)
            if part is not None and not part.empty:
                frames.append(part)
            cursor = chunk_end + pd.Timedelta(days=1)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _fetch_minute_window(
        self, code: str, start, end, freq: str, depth: int
    ) -> pd.DataFrame:
        raw = self._call(
            "stk_mins",
            ts_code=to_ts_code(code),
            start_time=f"{start.strftime('%Y-%m-%d')} 09:30:00",
            end_time=f"{end.strftime('%Y-%m-%d')} 15:00:00",
            freq=freq,
        )
        if raw is None or raw.empty:
            return pd.DataFrame()
        if len(raw) >= self._MINUTE_ROW_CAP and start < end and depth < 8:
            mid = start + (end - start) / 2
            left = self._fetch_minute_window(code, start, mid, freq, depth + 1)
            right = self._fetch_minute_window(
                code, mid + pd.Timedelta(days=1), end, freq, depth + 1
            )
            parts = [frame for frame in (left, right) if frame is not None and not frame.empty]
            return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        return raw

    def _fetch_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raw = self._call(
            "daily",
            ts_code=to_ts_code(code),
            start_date=str(start_date)[:10].replace("-", ""),
            end_date=str(end_date)[:10].replace("-", ""),
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=list(_OHLC))
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(raw["trade_date"].astype(str), format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(raw.get("open"), errors="coerce"),
                "high": pd.to_numeric(raw.get("high"), errors="coerce"),
                "low": pd.to_numeric(raw.get("low"), errors="coerce"),
                "close": pd.to_numeric(raw.get("close"), errors="coerce"),
                "volume": pd.to_numeric(raw.get("vol"), errors="coerce"),
                "amount": pd.to_numeric(raw.get("amount"), errors="coerce"),
            }
        ).dropna(subset=["ts", "close"])
        frame = frame.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")
        frame = frame.set_index("ts")
        frame["code"] = code
        frame["market"] = exchange_of(code)
        frame["type"] = ticker_type(code)
        return frame
