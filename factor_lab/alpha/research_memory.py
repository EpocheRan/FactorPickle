"""Structured research memory, validation ledger and context builder.

The memory has four layers:

- append-only facts (campaign results and validation records);
- deterministic validation status (independent / FDR / top-N / capacity);
- relevance-ranked context assembly with a hard-negative section;
- optional LLM compression for long lineages.  The compressor is an injected
  callable so the deterministic CLI and tests do not depend on a model vendor.

The builder never invents facts: every compressed claim carries source ids and
source hashes, and the audit checks those references.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from factor_lab.alpha.memory import canonical_expression
from factor_lab.core import config
from factor_lab.core.store import append_jsonl, read_json, read_jsonl, write_json_atomic


VALIDATION_SCHEMA = "factor_lab.alpha.validation_record.v1"
VALIDATION_LEDGER_SCHEMA = "factor_lab.alpha.validation_ledger.v1"
CONTEXT_SCHEMA = "factor_lab.alpha.research_context.v2"
MEMORY_INDEX_SCHEMA = "factor_lab.alpha.research_memory_index.v1"
COMPRESSION_SCHEMA = "factor_lab.alpha.research_context_compression.v1"

VALIDATION_TESTS = ("independent", "fdr", "topn", "capacity")
FAIL_STATUSES = {"fail"}
UNVERIFIED_STATUSES = {"unverified", "warn", "not_run", "insufficient_evidence"}


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _compact_metrics(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("research_result", {}).get("metrics") or result.get("metrics") or {}
    long_only = result.get("long_only") or {}
    size = long_only.get("size_screened") or {}
    promotion = result.get("promotion") or {}
    evaluation = result.get("evaluation") or {}
    return {
        "long_only_t": _number(size.get("long_only_t")),
        "long_only_annualized": _number(size.get("long_only_net_excess_annualized")),
        "long_only_ir": _number((promotion.get("metrics") or {}).get("ir") or metrics.get("ir")),
        "coverage": _number(metrics.get("coverage")),
        "turnover": _number(metrics.get("turnover") or size.get("turnover")),
        "ls_net_t": _number(metrics.get("net_t")),
        "gate": promotion.get("verdict"),
        "overall": evaluation.get("verdict"),
        "reasons": list(evaluation.get("reasons") or []),
    }


def _validation_status(tests: dict[str, Any]) -> str:
    statuses = {
        str((value or {}).get("status") or "unverified").lower()
        for value in tests.values()
        if isinstance(value, dict)
    }
    if statuses & FAIL_STATUSES:
        return "fail"
    if statuses and statuses <= {"pass"}:
        return "pass"
    return "unverified"


class ResearchMemory:
    """Append-only validation ledger plus deterministic context builder."""

    def __init__(self, root: str | Path | None = None, campaign_root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else config.LAB_ROOT / "alpha" / "memory"
        self.campaign_root = (
            Path(campaign_root)
            if campaign_root is not None
            else config.LAB_ROOT / "alpha" / "campaigns"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.validation_path = self.root / "validation_results.jsonl"
        self.context_dir = self.root / "contexts"
        self.context_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # validation ledger
    def record_validation(
        self,
        campaign_id: str,
        factor_id: str,
        tests: dict[str, Any],
        *,
        source_paths: Iterable[str] = (),
        data_hashes: dict[str, str] | None = None,
        result_hash: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        if not campaign_id or not factor_id:
            raise ValueError("campaign_id and factor_id are required")
        if not isinstance(tests, dict) or not tests:
            raise ValueError("tests must be a non-empty mapping")
        record = {
            "schema": VALIDATION_SCHEMA,
            "validation_id": f"VAL-{uuid.uuid4().hex[:12]}",
            "campaign_id": str(campaign_id),
            "factor_id": str(factor_id),
            "status": _validation_status(tests),
            "tests": tests,
            "source_paths": [str(item) for item in source_paths],
            "data_hashes": dict(data_hashes or {}),
            "result_hash": str(result_hash),
            "notes": str(notes),
            "created_at": config.now_iso(),
        }
        append_jsonl(self.validation_path, record)
        return record

    def read_validations(self) -> list[dict[str, Any]]:
        return read_jsonl(self.validation_path)

    def latest_validations(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.read_validations():
            latest[str(row.get("campaign_id"))] = row
        return latest

    def hard_negatives(self, campaign_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = {str(item) for item in campaign_ids} if campaign_ids is not None else None
        rows = []
        for row in self.latest_validations().values():
            if wanted is not None and str(row.get("campaign_id")) not in wanted:
                continue
            status = str(row.get("status") or "unverified")
            if status in {"pass"}:
                continue
            tests = row.get("tests") or {}
            failed = [
                name for name, value in tests.items()
                if isinstance(value, dict) and str(value.get("status") or "").lower() in {"fail", "unverified", "warn", "not_run"}
            ]
            rows.append(
                {
                    "validation_id": row.get("validation_id"),
                    "campaign_id": row.get("campaign_id"),
                    "factor_id": row.get("factor_id"),
                    "status": status,
                    "failed_tests": failed,
                    "tests": tests,
                    "source_paths": row.get("source_paths") or [],
                    "result_hash": row.get("result_hash"),
                }
            )
        rows.sort(key=lambda row: (row["status"] != "fail", str(row.get("campaign_id"))))
        return rows

    # ------------------------------------------------------------------
    # campaign facts
    def _campaign_dir(self, campaign_id: str) -> Path:
        return self.campaign_root / str(campaign_id)

    def load_result(self, campaign_id: str) -> dict[str, Any]:
        directory = self._campaign_dir(campaign_id)
        for path in (directory / "team" / "result.json", directory / "result.json"):
            payload = read_json(path)
            if isinstance(payload, dict) and payload:
                return payload
        rounds = sorted((directory / "rounds").glob("round-*.json"))
        if rounds:
            payload = read_json(rounds[-1])
            if isinstance(payload, dict):
                return payload
        return {}

    def _data_provenance(self, campaign_id: str, result: dict[str, Any]) -> dict[str, Any]:
        provenance = result.get("data_provenance") if isinstance(result, dict) else None
        if isinstance(provenance, dict) and provenance:
            return dict(provenance)
        rounds = sorted((self._campaign_dir(campaign_id) / "rounds").glob("round-*.json"))
        manifest = result.get("data_manifest") if isinstance(result, dict) else None
        factor_ref = ((result.get("research_result") or {}).get("factor_ref") or result.get("factor_ref") or {}) if isinstance(result, dict) else {}
        for round_path in reversed(rounds):
            round_payload = read_json(round_path)
            if not isinstance(round_payload, dict):
                continue
            if isinstance(round_payload.get("data_provenance"), dict):
                return dict(round_payload["data_provenance"])
            manifest = manifest or round_payload.get("data_manifest")
            factor_ref = factor_ref or ((round_payload.get("research_result") or {}).get("factor_ref") or {})
        if isinstance(manifest, dict) and manifest:
            window = [manifest.get("start"), manifest.get("end")]
            return {
                "data_manifest_hash": _sha(manifest),
                "data_hash": manifest.get("data_hash"),
                "price_basis": manifest.get("price_basis"),
                "evaluation_window": [str(item) if item is not None else "" for item in window],
                "factor_code_hash": (factor_ref or {}).get("code_hash"),
            }
        return {}

    def campaign_facts(self, campaign_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        ids = [str(item) for item in campaign_ids] if campaign_ids is not None else sorted(
            path.name for path in self.campaign_root.iterdir() if path.is_dir()
        )
        rows = []
        for campaign_id in ids:
            campaign = read_json(self._campaign_dir(campaign_id) / "campaign.json")
            result = self.load_result(campaign_id)
            if not isinstance(campaign, dict) and not result:
                continue
            factor_refs = (result.get("research_result") or {}).get("factor_ref") or {}
            factor_id = (
                factor_refs.get("factor_id")
                or result.get("factor_id")
                or (result.get("research_result") or {}).get("factor_id")
                or ""
            )
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "objective": (campaign or {}).get("objective") or "",
                    "parent_campaign_ids": list((campaign or {}).get("parent_campaign_ids") or []),
                    "state": (campaign or {}).get("state"),
                    "factor_id": str(factor_id),
                    "result_hash": _sha(result),
                    "data_provenance": self._data_provenance(campaign_id, result),
                    "metrics": _compact_metrics(result),
                    "validation": (self.latest_validations().get(campaign_id) or {}),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # context assembly
    def _relevance(self, row: dict[str, Any], index: int, total: int) -> float:
        score = 0.0
        validation = row.get("validation") or {}
        if validation.get("status") == "fail":
            score += 100.0
        elif validation.get("status") == "unverified":
            score += 70.0
        if row.get("factor_id"):
            score += 5.0
        score += (index / max(1, total)) * 2.0
        return score

    def _deterministic_compress(
        self,
        facts: list[dict[str, Any]],
        hard_negatives: list[dict[str, Any]],
        budget_chars: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # Keep every hard negative; compress old positive facts by family/factor.
        selected: dict[str, dict[str, Any]] = {}
        for row in facts:
            key = str(row.get("factor_id") or row.get("campaign_id"))
            if key not in selected:
                selected[key] = row
        payload = {
            "hard_negatives": hard_negatives,
            "campaigns": list(selected.values()),
        }
        return payload, {
            "mode": "deterministic",
            "original_campaigns": len(facts),
            "compressed_campaigns": len(selected),
            "budget_chars": int(budget_chars),
            "rule": "keep all validations and one latest fact per factor_id",
        }

    def _compress(
        self,
        facts: list[dict[str, Any]],
        hard_negatives: list[dict[str, Any]],
        budget_chars: int,
        compressor: Callable[[dict[str, Any]], dict[str, Any]] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {"hard_negatives": hard_negatives, "campaigns": facts}
        if len(json.dumps(payload, ensure_ascii=False, default=str)) <= int(budget_chars):
            return payload, {"mode": "none", "budget_chars": int(budget_chars)}
        if compressor is not None:
            compressed = compressor(payload)
            if not isinstance(compressed, dict):
                raise ValueError("compressor must return a dict")
            sources = list(compressed.get("source_campaign_ids") or [])
            expected = {str(row.get("campaign_id")) for row in facts}
            missing = sorted(expected - set(sources))
            if missing:
                raise ValueError(f"compressed context missing source campaigns: {missing}")
            return compressed, {
                "mode": "llm",
                "budget_chars": int(budget_chars),
                "source_campaigns": len(sources),
            }
        return self._deterministic_compress(facts, hard_negatives, budget_chars)

    def build_context(
        self,
        parent_campaign_ids: Iterable[str] | None = None,
        *,
        budget_chars: int = 12_000,
        compressor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        current_expression: str = "",
    ) -> dict[str, Any]:
        facts = self.campaign_facts(parent_campaign_ids)
        parents = [str(item) for item in parent_campaign_ids] if parent_campaign_ids is not None else [
            str(row["campaign_id"]) for row in facts
        ]
        validations = self.latest_validations()
        hard = self.hard_negatives(parents)
        ranked = sorted(
            enumerate(facts),
            key=lambda pair: self._relevance(pair[1], pair[0], len(facts)),
            reverse=True,
        )
        ordered = [row for _index, row in ranked]
        compressed, compression = self._compress(ordered, hard, budget_chars, compressor)
        duplicates = self.duplicate_campaigns(current_expression, ordered) if current_expression else []
        context = {
            "schema": CONTEXT_SCHEMA,
            "generated_at": config.now_iso(),
            "parent_campaign_ids": parents,
            "source_hashes": {
                row["campaign_id"]: {
                    "result_hash": row["result_hash"],
                    **{
                        key: value
                        for key, value in (row.get("data_provenance") or {}).items()
                        if value not in (None, "", [], {})
                    },
                }
                for row in facts
            },
            "hard_constraints": {
                "do_not_promote_without": ["independent", "fdr", "capacity"],
                "hard_negatives": hard,
            },
            "validation_summary": {
                "n_parents": len(parents),
                "n_validated": sum(1 for row in validations.values() if row.get("status") == "pass"),
                "n_failed": sum(1 for row in validations.values() if row.get("status") == "fail"),
                "n_unverified": sum(1 for row in validations.values() if row.get("status") == "unverified"),
            },
            "prior_results": [
                {
                    "campaign_id": row["campaign_id"],
                    "factor_id": row["factor_id"],
                    "metrics": row["metrics"],
                }
                for row in ordered
            ],
            "compressed_history": compressed,
            "compression": compression,
            "duplicates": duplicates,
            "open_questions": [
                "Resolve all hard negatives before proposing a new candidate.",
                "Do not repeat a failed hypothesis without a new, pre-registered mechanism.",
            ],
        }
        return context

    def write_context(
        self,
        campaign_id: str,
        parent_campaign_ids: Iterable[str],
        *,
        budget_chars: int = 12_000,
        compressor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        current_expression: str = "",
    ) -> dict[str, Any]:
        context = self.build_context(
            parent_campaign_ids,
            budget_chars=budget_chars,
            compressor=compressor,
            current_expression=current_expression,
        )
        team = self._campaign_dir(campaign_id) / "team"
        team.mkdir(parents=True, exist_ok=True)
        target = team / "research_context.json"
        context["campaign_id"] = campaign_id
        context["context_hash"] = _sha(context)
        write_json_atomic(target, context)
        return {"path": str(target), "context": context}

    # ------------------------------------------------------------------
    # lineage / duplicate checks
    def duplicate_campaigns(self, expression: str, facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        canonical = canonical_expression(str(expression))
        matches = []
        for row in facts:
            existing = read_json(self._campaign_dir(row["campaign_id"]) / "team" / "factor_builder_artifact.json")
            if not isinstance(existing, dict):
                continue
            value = str(existing.get("expression") or "")
            if value and canonical_expression(value) == canonical:
                matches.append({"campaign_id": row["campaign_id"], "reason": "exact_expression_duplicate"})
        return matches

    def lineage_issues(self, campaign_id: str) -> list[str]:
        issues: list[str] = []
        campaign = read_json(self._campaign_dir(campaign_id) / "campaign.json")
        if not isinstance(campaign, dict):
            return [f"campaign not found: {campaign_id}"]
        parents = [str(item) for item in campaign.get("parent_campaign_ids") or []]
        seen: set[str] = set()
        stack = list(parents)
        while stack:
            current = stack.pop()
            if current == campaign_id:
                issues.append("lineage cycle contains the campaign itself")
                break
            if current in seen:
                continue
            seen.add(current)
            parent = read_json(self._campaign_dir(current) / "campaign.json")
            if not isinstance(parent, dict):
                issues.append(f"missing lineage parent: {current}")
                continue
            stack.extend(str(item) for item in parent.get("parent_campaign_ids") or [])
        return issues

    def validate_context(self, context: dict[str, Any], *, campaign_id: str | None = None) -> dict[str, Any]:
        issues: list[str] = []
        if context.get("schema") != CONTEXT_SCHEMA:
            issues.append(f"context schema must be {CONTEXT_SCHEMA}")
        parents = [str(item) for item in context.get("parent_campaign_ids") or []]
        for parent in parents:
            if not (self._campaign_dir(parent) / "campaign.json").exists():
                issues.append(f"context parent does not exist: {parent}")
        source_hashes = context.get("source_hashes") or {}
        for campaign_id_value, expected in source_hashes.items():
            result = self.load_result(str(campaign_id_value))
            if not result:
                issues.append(f"context source result missing: {campaign_id_value}")
                continue
            provenance = self._data_provenance(str(campaign_id_value), result)
            if isinstance(expected, str):
                if _sha(result) != expected:
                    issues.append(f"context source hash mismatch: {campaign_id_value}")
                continue
            if not isinstance(expected, dict):
                issues.append(f"context source hash is invalid: {campaign_id_value}")
                continue
            expected_result = expected.get("result_hash")
            if expected_result and _sha(result) != str(expected_result):
                issues.append(f"context source hash mismatch: {campaign_id_value}")
            for key in ("data_manifest_hash", "data_hash", "factor_code_hash", "pre_registration_hash", "evaluation_window"):
                if key not in expected:
                    continue
                if expected.get(key) != provenance.get(key):
                    issues.append(f"context data provenance mismatch: {campaign_id_value}.{key}")
        hard = (context.get("hard_constraints") or {}).get("hard_negatives") or []
        expected_hard = {str(row.get("validation_id")) for row in self.hard_negatives(parents)}
        actual_hard = {str(row.get("validation_id")) for row in hard}
        missing = sorted(expected_hard - actual_hard)
        if missing:
            issues.append(f"context omits hard negatives: {missing}")
        if campaign_id:
            issues.extend(self.lineage_issues(campaign_id))
        return {"ok": not issues, "issues": issues, "parents": len(parents), "hard_negatives": len(hard)}

    def summary(self) -> dict[str, Any]:
        validations = self.read_validations()
        return {
            "schema": MEMORY_INDEX_SCHEMA,
            "root": str(self.root),
            "n_validations": len(validations),
            "n_failed": sum(1 for row in validations if row.get("status") == "fail"),
            "n_unverified": sum(1 for row in validations if row.get("status") == "unverified"),
            "n_campaigns": len(self.campaign_facts()),
        }


__all__ = [
    "COMPRESSION_SCHEMA",
    "CONTEXT_SCHEMA",
    "MEMORY_INDEX_SCHEMA",
    "VALIDATION_LEDGER_SCHEMA",
    "VALIDATION_SCHEMA",
    "ResearchMemory",
]
