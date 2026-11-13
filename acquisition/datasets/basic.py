"""P0 基础资料同步：主数据快照 / 名称变更 / 新股 / 停复牌 / 涨跌停 / 披露 / 预告 / 快报。

每个数据集一个 ``sync_*`` 方法，统一走 ``_sync_basic``：

- 游标与 pending 落 ``scheduled_task_state``（状态键就是数据集名），中断后续跑；
- 窗口不变且上次已 ``DONE`` 时直接跳过；窗口向后延长时从上次结束日附近续跑，
  不重扫全历史；
- 空日期默认只对最近 3 天记 pending，历史空日（周末无公告、当日无停牌）不会永久
  挂起；``stk_limit`` 例外，每个交易日都应有数据，历史空日也记 pending（最多重试
  3 次）；
- 权限不足记 ``BLOCKED`` 并返回 ``done=True``，编排器继续跑其它数据集；
  限流/额度耗尽记 ``PAUSED`` 并抛出，由编排器记录错误。
"""

from __future__ import annotations

from .base import *  # noqa: F401,F403

from data_interface.basic.service import BasicDataService


class BasicSyncMixin:
    def _get_basic_service(self) -> BasicDataService:
        if self._basic_service is None:
            self._basic_service = BasicDataService(
                config=self.config,
                repository=self._basic_repository,
                tushare=self.tushare,
                trading_calendar=self.trading_calendar,
            )
        return self._basic_service

    # ------------------------------------------------------------------ 通用
    def _sync_basic(
        self,
        dataset: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        max_units: int | None = None,
        cursor: str | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        service = self._get_basic_service()
        default_start = str(self.settings.get("basic_start_date", "2017-01-01"))[:10]
        explicit_start = start_date is not None
        start = str(start_date or default_start)[:10]
        end = str(end_date or today_str())[:10]
        if start > end:
            raise ValueError(f"start_date({start}) 不能晚于 end_date({end})")

        # 快照型数据集（stock_basic_history）：每次运行写一份当天的全市场快照，
        # 同一交易日重复运行由 DEDUP 覆盖，不参与游标/pending 逻辑。
        if dataset == "stock_basic_history":
            result = service.sync(dataset, start, end)
            self.state.save(
                TASK_NAME,
                dataset,
                cursor=None,
                status="DONE",
                total=int(result.get("rows_written") or 0),
                processed=int(result.get("rows_written") or 0),
                message=f"snapshot={result.get('snapshot_date')}",
                payload={"start_date": start, "end_date": end},
            )
            return result

        previous = None if reset else self.state.get(TASK_NAME, dataset)
        old_payload: dict[str, Any] = {}
        if previous and previous.get("payload"):
            try:
                old_payload = json.loads(previous["payload"])
            except Exception:
                old_payload = {}
        # 起始日变化视为新任务；end 变化允许续跑（增量向后延伸）
        if old_payload and old_payload.get("start_date") != start:
            previous = None
            old_payload = {}

        if previous and not cursor and not reset:
            old_end = str(old_payload.get("end_date") or "")[:10]
            if previous.get("status") == "DONE" and old_end == end:
                return {
                    "dataset": dataset,
                    "done": True,
                    "skipped": True,
                    "rows_written": 0,
                    "start_date": start,
                    "end_date": end,
                }
            if old_end and end > old_end:
                # 增量延伸：从上次结束日前 3 天续跑，覆盖边界但不重扫全历史
                overlap = (
                    date.fromisoformat(old_end) - timedelta(days=3)
                ).isoformat()
                start = max(start, overlap)

        retry_limit = max(1, int(self.settings.get("empty_day_retry_limit", 3)))
        attempts: dict[str, int] = dict(old_payload.get("attempts") or {})
        pending = [
            str(day)[:10]
            for day in (old_payload.get("pending") or [])
            if attempts.get(str(day)[:10], 0) < retry_limit
        ]
        current_cursor = cursor or (previous or {}).get("cursor")
        if pending:
            start = min(start, min(pending))

        if not explicit_start and not current_cursor and not pending and previous is None:
            # 没显式指定窗口时从库内最新日期续跑，避免默认 --start 变成全历史回填
            latest = service.latest_ts(dataset)
            if latest is not None:
                start = max(start, latest.date().isoformat())

        try:
            result = service.sync(
                dataset,
                start,
                end,
                cursor=current_cursor,
                pending=pending,
                max_units=max_units,
            )
        except PermissionDeniedError as exc:
            # 权限不足：记 BLOCKED，保留游标，让编排器继续其它数据集
            self.state.save(
                TASK_NAME,
                dataset,
                cursor=current_cursor,
                status="BLOCKED",
                total=0,
                processed=0,
                message=str(exc)[:200],
                payload={
                    "start_date": str(start_date or default_start)[:10],
                    "end_date": end,
                },
            )
            logger.warning("%s 权限不足，已标记 BLOCKED: %s", dataset, exc)
            return {
                "dataset": dataset,
                "done": True,
                "blocked": True,
                "rows_written": 0,
                "error": str(exc)[:200],
                "start_date": start,
                "end_date": end,
            }

        new_pending = [str(day)[:10] for day in (result.get("pending") or [])]
        for day in new_pending:
            attempts[day] = attempts.get(day, 0) + 1
        for day in list(attempts):
            if day not in new_pending:
                attempts.pop(day)
        effective_pending = [
            day for day in new_pending if attempts.get(day, 0) < retry_limit
        ]

        done = bool(result.get("done"))
        total = int(
            result.get("total_days")
            or result.get("total_periods")
            or result.get("total_chunks")
            or 0
        )
        processed = len(
            result.get("processed_days")
            or result.get("processed_periods")
            or result.get("processed_chunks")
            or []
        )
        message = (
            f"rows={result.get('rows_written', 0)} "
            f"pending={len(effective_pending)}"
        )
        if result.get("errors"):
            message += f" errors={len(result['errors'])}"
        self.state.save(
            TASK_NAME,
            dataset,
            cursor=result.get("next_cursor"),
            status="DONE" if done else "RUNNING",
            total=total,
            processed=processed,
            message=message,
            payload={
                "start_date": str(start_date or default_start)[:10],
                "end_date": end,
                "effective_start": start,
                "pending": effective_pending,
                "attempts": attempts,
            },
        )
        result.update(
            {
                "start_date": start,
                "end_date": end,
                "pending": effective_pending,
            }
        )
        return result

    # ------------------------------------------------------------------ 数据集
    def sync_stock_basic_history(
        self, *, reset: bool = False, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        return self._sync_basic(
            "stock_basic_history", start_date=start_date, end_date=end_date, reset=reset
        )

    def sync_namechange(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        max_units: int | None = None,
        cursor: str | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        return self._sync_basic(
            "namechange",
            start_date=start_date,
            end_date=end_date,
            max_units=max_units,
            cursor=cursor,
            reset=reset,
        )

    def sync_new_share(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("new_share", **kwargs)

    def sync_suspend_d(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("suspend_d", **kwargs)

    def sync_stk_limit(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("stk_limit", **kwargs)

    def sync_disclosure_date(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("disclosure_date", **kwargs)

    def sync_forecast(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("forecast", **kwargs)

    def sync_express(self, **kwargs) -> dict[str, Any]:
        return self._sync_basic("express", **kwargs)

    # ------------------------------------------------------------------ 探针
    def probe_basic_permissions(self, sample_date: str | None = None) -> dict[str, Any]:
        """最小请求探测 P0 接口权限；不写库。"""
        return self._get_basic_service().probe(sample_date)
