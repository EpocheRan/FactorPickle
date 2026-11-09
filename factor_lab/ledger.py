"""Append-only, hash-chained trial ledger with explicit repair markers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .clock import hlc_after, next_token
from .config import LEDGER_PATH, LEDGER_SUMMARY_PATH, now_iso
from .schemas import Hypothesis
from .store import (
    _lock_file,
    append_jsonl,
    read_jsonl_lines,
    sha256_json,
    write_json_atomic,
)

GENESIS = "GENESIS"


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LEDGER_PATH

    def _lines(self) -> list[tuple[int, dict[str, Any] | None, str]]:
        return read_jsonl_lines(self.path)

    def records(self) -> list[dict[str, Any]]:
        return [row for _, row, _ in self._lines() if row is not None]

    def last(self) -> dict[str, Any] | None:
        rows = self.records()
        return rows[-1] if rows else None

    def append(self, event: str, payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock_file(self.path):
            last = self.last()
            token = next_token()
            record: dict[str, Any] = {
                "seq": int(last["seq"]) + 1 if last else 1,
                "ts": now_iso(),
                "event": event,
                "payload": payload,
                "prev_hash": str(last.get("hash")) if last else GENESIS,
                "event_id": f"evt-{token['event_seq']:08d}",
                "event_seq": int(token["event_seq"]),
                "event_hlc": str(token["event_hlc"]),
                "node": str(token["node"]),
            }
            record.update(extra)
            record["hash"] = sha256_json(record)
            append_jsonl(self.path, record, lock=False)
        return record

    # ------------------------------------------------------------ domain events
    def register_hypothesis(self, hypothesis: Hypothesis) -> dict[str, Any]:
        for record in self.records():
            if record.get("event") != "hypothesis_registered":
                continue
            payload = record.get("payload") or {}
            if payload.get("hypothesis_id") == hypothesis.hypothesis_id:
                return record
        return self.append(
            "hypothesis_registered",
            {
                "hypothesis_id": hypothesis.hypothesis_id,
                "factor_id": hypothesis.factor_id,
                "family_id": hypothesis.family_id,
                "development_window": hypothesis.development_window,
                "bet": hypothesis.bet,
                "outcome_policy": hypothesis.outcome_policy,
                "outcomes": [item.to_dict() for item in hypothesis.outcomes],
                "registered_at": now_iso(),
            },
        )

    def start_run(
        self,
        *,
        run_id: str,
        trial_id: str,
        hypothesis_id: str,
        factor_id: str,
        outcome: dict[str, Any],
        stage: str = "L1",
        family_id: str | None = None,
        parent_event_id: str | None = None,
        family_kind: str = "development",
    ) -> dict[str, Any]:
        return self.append(
            "run_started",
            {
                "run_id": run_id,
                "trial_id": trial_id,
                "hypothesis_id": hypothesis_id,
                "factor_id": factor_id,
                "family_id": family_id,
                "family_kind": family_kind,
                "parent_event_id": parent_event_id,
                "outcome": outcome,
                "stage": stage,
                "started_at": now_iso(),
            },
        )

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        report_path: str | None = None,
        duration_sec: float | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return self.append(
            "run_finished",
            {
                "run_id": run_id,
                "status": status,
                "report_path": report_path,
                "duration_sec": duration_sec,
                "finished_at": now_iso(),
                "error": error,
            },
        )

    def repair(
        self,
        *,
        gap_start: int,
        gap_end: int,
        reason: str,
        approved_by: str,
    ) -> dict[str, Any]:
        if gap_start > gap_end:
            raise ValueError("gap_start must be <= gap_end")
        return self.append(
            "ledger_repair",
            {
                "gap_start": int(gap_start),
                "gap_end": int(gap_end),
                "reason": reason,
                "approved_by": approved_by,
                "repaired_at": now_iso(),
            },
        )

    # ---------------------------------------------------------------- integrity
    def verify(self) -> dict[str, Any]:
        rows = self._lines()
        valid: list[dict[str, Any]] = []
        malformed: list[dict[str, Any]] = []
        for line_no, record, raw in rows:
            if record is None:
                malformed.append({"line": line_no, "raw": raw[:160]})
            else:
                valid.append(record)

        gaps: list[dict[str, Any]] = []
        prev: dict[str, Any] | None = None
        for record in valid:
            expected_prev = str(prev.get("hash")) if prev else GENESIS
            if record.get("prev_hash") != expected_prev:
                gaps.append(
                    {
                        "after_seq": int(prev["seq"]) if prev else 0,
                        "at_seq": record.get("seq"),
                        "reason": "prev_hash_mismatch",
                    }
                )
            if prev is not None and int(record.get("seq", 0)) != int(prev.get("seq", 0)) + 1:
                gaps.append(
                    {
                        "after_seq": int(prev.get("seq", 0)),
                        "at_seq": record.get("seq"),
                        "reason": "seq_gap",
                    }
                )
            prev = record

        repairs = [
            record
            for record in valid
            if record.get("event") == "ledger_repair"
        ]
        repaired_ranges = [
            (int(record["payload"]["gap_start"]), int(record["payload"]["gap_end"]))
            for record in repairs
            if isinstance(record.get("payload"), dict)
            and "gap_start" in record["payload"]
            and "gap_end" in record["payload"]
        ]
        unrepaired = []
        for gap in gaps:
            after = int(gap.get("after_seq") or 0)
            at = int(gap.get("at_seq") or (after + 1))
            lo, hi = after + 1, at
            if not any(start <= lo and end >= hi for start, end in repaired_ranges):
                unrepaired.append(gap)

        head = str(valid[-1]["hash"]) if valid else GENESIS
        return {
            "ok": not unrepaired,
            "head": head,
            "records": len(valid),
            "malformed": malformed,
            "gaps": gaps,
            "repairs": repairs,
            "unrepaired": unrepaired,
        }

    # ------------------------------------------------------------------ queries
    def find_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        for record in self.records():
            if record.get("event") == "hypothesis_registered":
                if (record.get("payload") or {}).get("hypothesis_id") == hypothesis_id:
                    return record
        return None

    def find_run(self, run_id: str) -> dict[str, Any] | None:
        for record in self.records():
            if record.get("event") == "run_started":
                if (record.get("payload") or {}).get("run_id") == run_id:
                    return record
        return None

    def orphan_check(
        self,
        *,
        run_id: str,
        hypothesis_id: str,
        trial_id: str,
        require_parent: bool = False,
    ) -> dict[str, Any]:
        hyp = self.find_hypothesis(hypothesis_id)
        run = self.find_run(run_id)
        issues: list[str] = []
        order = "hlc"
        if hyp is None:
            issues.append("hypothesis_registered missing")
        if run is None:
            issues.append("run_started missing")
        if hyp is not None and run is not None:
            hyp_hlc = str(hyp.get("event_hlc") or "")
            run_hlc = str(run.get("event_hlc") or "")
            if hyp_hlc and run_hlc:
                if hlc_after(hyp_hlc, run_hlc):
                    issues.append("hypothesis HLC is after run HLC")
            else:
                order = "legacy_timestamp"
                try:
                    from datetime import datetime

                    registered = datetime.fromisoformat(str(hyp["ts"]))
                    started = datetime.fromisoformat(str(run["ts"]))
                    if registered > started:
                        issues.append("hypothesis registered after run started")
                except Exception:
                    issues.append("timestamp parse failed")
            payload = run.get("payload") or {}
            parent_event_id = payload.get("parent_event_id")
            if parent_event_id:
                if parent_event_id != hyp.get("event_id"):
                    issues.append("parent_event_id does not match hypothesis event")
            elif require_parent:
                issues.append("parent_event_id missing")
        if run is not None:
            payload = run.get("payload") or {}
            if payload.get("trial_id") != trial_id:
                issues.append("trial_id mismatch")
            if payload.get("hypothesis_id") != hypothesis_id:
                issues.append("hypothesis_id mismatch")
        return {
            "ok": not issues,
            "issues": issues,
            "order": order,
            "hypothesis_seq": int(hyp["seq"]) if hyp else None,
            "run_seq": int(run["seq"]) if run else None,
            "hypothesis_event_id": hyp.get("event_id") if hyp else None,
            "run_event_id": run.get("event_id") if run else None,
        }

    def family_trials(self, family_id: str | None, *, include_confirm: bool = False) -> set[str]:
        trials: set[str] = set()
        for record in self.records():
            if record.get("event") != "run_started":
                continue
            payload = record.get("payload") or {}
            if family_id is not None and payload.get("family_id") != family_id:
                continue
            if not include_confirm and payload.get("family_kind") == "confirm":
                continue
            trial = payload.get("trial_id")
            if trial:
                trials.add(str(trial))
        return trials

    def summary(self) -> dict[str, Any]:
        records = self.records()
        counts = Counter(str(record.get("event")) for record in records)
        trials: set[str] = set()
        family_trials: dict[str, set[str]] = {}
        for record in records:
            if record.get("event") != "run_started":
                continue
            payload = record.get("payload") or {}
            trial = payload.get("trial_id")
            if trial:
                trials.add(str(trial))
            if payload.get("family_kind") == "confirm":
                continue
            family = str(payload.get("family_id") or "legacy")
            if trial:
                family_trials.setdefault(family, set()).add(str(trial))
        verification = self.verify()
        return {
            "updated_at": now_iso(),
            "head": verification["head"],
            "records": len(records),
            "events": dict(counts),
            "distinct_trials": len(trials),
            "family_trials": {family: len(items) for family, items in sorted(family_trials.items())},
            "repairs": len(verification["repairs"]),
            "unrepaired": len(verification["unrepaired"]),
            "ok": verification["ok"],
        }

    def write_summary(self, path: Path | None = None) -> dict[str, Any]:
        summary = self.summary()
        write_json_atomic(path or LEDGER_SUMMARY_PATH, summary)
        return summary
