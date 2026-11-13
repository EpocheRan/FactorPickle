"""Deterministic Runner: factor artifact -> data_bundle -> research request.

Runner is deliberately not an LLM and never touches the database directly.
It consumes a versioned Factor Library artifact and a loaded AlphaDataset,
then emits an auditable data_bundle manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.factor_library import FactorArtifact, load_manifest
from factor_lab.core.store import sha256_json


DATA_BUNDLE_SCHEMA = "factor_lab.alpha.data_bundle.v1"


def build_data_bundle(
    dataset: AlphaDataset,
    *,
    factor_ref: dict[str, Any] | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    context = dataset.context
    selected_fields = sorted(fields or context.field_names)
    bundle = {
        "schema": DATA_BUNDLE_SCHEMA,
        "factor_ref": factor_ref or {},
        "data_manifest": dataset.data_manifest,
        "fields": selected_fields,
        "universe": list(dataset.codes),
        "start": dataset.start,
        "end": dataset.end,
        "warmup_days": dataset.warmup_days,
        "masks": {
            "train": {"start": dataset.start, "end": dataset.end},
            "valid": None,
            "test": None,
            "embargo_days": 0,
        },
        "pit": {
            "required": True,
            "available_at": "data_manifest",
            "policy": "all fields must expose PIT or be carried forward by the adapter",
        },
        "rows": int(context.close.shape[0]),
        "symbols": int(context.close.shape[1]),
    }
    bundle["bundle_hash"] = sha256_json(bundle)
    return bundle


def load_artifact(path_or_factor: str | Path) -> FactorArtifact:
    from factor_lab.alpha.factor_library import resolve_factor

    path = Path(path_or_factor)
    if path.is_dir() and (path / "manifest.json").exists():
        manifest = load_manifest(path)
        return FactorArtifact(
            factor_id=str(manifest["factor_id"]),
            family=str(manifest["family"]),
            version=str(manifest["version"]),
            path=path,
            manifest=manifest,
        )
    if path.exists() and path.suffix == ".json":
        manifest = load_manifest(path)
        return FactorArtifact(
            factor_id=str(manifest["factor_id"]),
            family=str(manifest["family"]),
            version=str(manifest["version"]),
            path=path.parent,
            manifest=manifest,
        )
    return resolve_factor(str(path_or_factor))


def artifact_expression(artifact: FactorArtifact) -> str:
    asset = artifact.path / "factor.dsl"
    if not asset.exists():
        raise ValueError(f"Runner currently executes DSL artifacts only: {artifact.path}")
    return asset.read_text(encoding="utf-8").strip()


def prepare_research_request(
    artifact: FactorArtifact,
    dataset: AlphaDataset,
) -> dict[str, Any]:
    expression = artifact_expression(artifact)
    factor_ref = {
        "factor_id": artifact.factor_id,
        "version": artifact.version,
        "code_hash": artifact.code_hash,
        "family": artifact.family,
    }
    bundle = build_data_bundle(dataset, factor_ref=factor_ref)
    return {
        "schema": "factor_lab.alpha.runner_request.v1",
        "factor_ref": factor_ref,
        "expression": expression,
        "data_bundle": bundle,
    }
