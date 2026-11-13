"""Reproducible 10-year PIT research protocol.

The actual campaign results are disposable runtime artifacts. This module
codifies the *procedure*: dataset window, ex-ante candidate-pool selection,
PIT universe policy, cost policy and a deterministic backtest entry point.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any

import pandas as pd

from factor_lab.alpha.data import load_alpha_dataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.gates import alpha_gate_spec
from factor_lab.alpha.universe import build_pit_universe, load_universe_policy, universe_summary
from factor_lab.core import config
from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway


PROTOCOL_PATH = config.ALPHA_POLICY_DIR / "ten_year.json"
TEN_YEAR_SCHEMA = "factor_lab.alpha.ten_year_result.v1"
DATASET_CACHE_SCHEMA = "factor_lab.alpha.dataset_cache.v5"
DEFAULT_CACHE_PATH = config.LAB_ROOT / "alpha" / "data_axes" / "ten_year_cache_hfq.pkl"


def load_protocol(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else PROTOCOL_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "factor_lab.alpha.ten_year_protocol.v1":
        raise ValueError(f"invalid ten-year protocol: {target}")
    return payload


def build_pool(
    *,
    gateway: DataInterfaceGateway | None = None,
    out_path: str | Path | None = None,
    top_n: int | None = None,
) -> list[str]:
    protocol = load_protocol()
    selection = protocol["candidate_pool"]
    gateway = gateway or DataInterfaceGateway()
    codes = gateway.liquidity_pool(
        start=str(selection["selection_period_start"]),
        end=str(selection["selection_period_end"]),
        top_n=int(top_n or selection["top_n"]),
    )
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(",".join(codes) + "\n", encoding="utf-8")
    return codes


def data_audit(gateway: DataInterfaceGateway | None = None) -> dict[str, Any]:
    protocol = load_protocol()
    gateway = gateway or DataInterfaceGateway()
    coverage = gateway.research_coverage(
        start=str(protocol["candidate_pool"]["selection_period_start"]),
        end=str(protocol["dataset_end"]),
    )
    return {"schema": "factor_lab.alpha.ten_year_audit.v1", "protocol": protocol, "coverage": coverage}


def load_pool(gateway: DataInterfaceGateway | None = None) -> list[str]:
    protocol = load_protocol()
    path = Path(protocol["candidate_pool"].get("generated_file") or "output/factor_lab/hypotheses/pool1000_10y.txt")
    if not path.is_absolute():
        path = Path(config.LAB_ROOT).parents[1] / path
    if path.exists():
        return sorted({part.strip() for part in path.read_text(encoding="utf-8").replace("\n", ",").split(",") if part.strip()})
    return build_pool(gateway=gateway, out_path=path)


def _artifact_expression(artifact_path: str | Path) -> str:
    from factor_lab.alpha.runner import load_artifact

    artifact = load_artifact(Path(artifact_path))
    return artifact.expression


def run_expression(
    expression: str,
    *,
    gateway: DataInterfaceGateway | None = None,
    horizon: int | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    protocol = load_protocol()
    codes = load_pool(gateway=gateway)
    dataset = load_alpha_dataset(
        codes,
        str(protocol["dataset_start"]),
        str(protocol["dataset_end"]),
        warmup_days=int(protocol["warmup_days"]),
        gateway=gateway,
    )
    policy = load_universe_policy()
    mask = build_pit_universe(dataset.context, policy)
    metrics = evaluate_expression(
        expression,
        dataset.context,
        horizon=int(horizon or protocol["horizon"]),
        cost_bps=20.0,
        start=str(protocol["dataset_start"]),
        end=str(protocol["dataset_end"]),
        universe=mask,
        spec=alpha_gate_spec(),
        use_tradability=True,
    )
    payload = {
        "schema": TEN_YEAR_SCHEMA,
        "expression": expression,
        "dataset": {"start": protocol["dataset_start"], "end": protocol["dataset_end"], "warmup_days": protocol["warmup_days"], "n_codes": len(codes)},
        "universe": universe_summary(mask, policy),
        "cost": alpha_gate_spec().cost.to_dict(),
        "metrics": metrics,
    }
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def run_artifact(artifact_path: str | Path, *, gateway: DataInterfaceGateway | None = None, horizon: int | None = None, out_path: str | Path | None = None) -> dict[str, Any]:
    return run_expression(_artifact_expression(artifact_path), gateway=gateway, horizon=horizon, out_path=out_path)


def _sha256(path: Path, block: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(block)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _field_coverage(dataset: Any, start: str, end: str) -> dict[str, Any]:
    """Non-null ratio per field over ``[start, end]`` of the loaded context.

    Two denominators are recorded: every date x code cell in the window, and the
    PIT-eligible cells (a last price exists, i.e. the stock was trading).  The
    admission gate's ``coverage_ge_0_90`` uses the eligible-cell ratio, so the
    same number must be reproducible from the sidecar.
    """
    context = dataset.context
    index = context.close.index
    mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    eligible = context.close.loc[mask].notna()
    eligible_cells = int(eligible.to_numpy().sum())
    coverage: dict[str, Any] = {}
    eligible_coverage: dict[str, Any] = {}
    frames = {"close": context.close, **context.fields}
    for name, frame in frames.items():
        window = frame.loc[mask]
        if window.empty:
            coverage[name] = 0.0
            eligible_coverage[name] = 0.0
            continue
        coverage[name] = round(float(window.notna().to_numpy().mean()), 6)
        if eligible_cells:
            hits = int((window.notna() & eligible).to_numpy().sum())
            eligible_coverage[name] = round(hits / eligible_cells, 6)
        else:
            eligible_coverage[name] = 0.0
    return {
        "all_cells": coverage,
        "eligible_cells": eligible_coverage,
        "n_cells": int(mask.sum()) * int(len(context.close.columns)),
        "n_eligible_cells": eligible_cells,
    }


def build_dataset_cache(
    *,
    pool: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    warmup_days: int | None = None,
    out_path: str | Path | None = None,
    gateway: DataInterfaceGateway | None = None,
    coverage_start: str | None = None,
) -> dict[str, Any]:
    """Build the ten-year dataset cache atomically and write its sidecar.

    The pickle is written through a temp file and ``os.replace`` so a crash never
    leaves a half-written cache behind.  The sidecar is the machine-readable
    coverage evidence: shape, sha256, price basis, ``data_manifest`` (including
    the derived ``list_days`` / ``float_share`` / ``index_weight_*`` fields) and
    per-field coverage over ``[coverage_start, end]``.
    """
    protocol = load_protocol()
    codes = list(pool) if pool else load_pool(gateway=gateway)
    window_start = str(start or protocol["dataset_start"])
    window_end = str(end or protocol["dataset_end"])
    warmup = int(warmup_days if warmup_days is not None else protocol["warmup_days"])
    target = Path(out_path) if out_path is not None else DEFAULT_CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    dataset = load_alpha_dataset(
        codes, window_start, window_end, warmup_days=warmup, gateway=gateway
    )
    build_seconds = time.perf_counter() - started

    temp = target.with_name(target.name + ".tmp")
    started = time.perf_counter()
    with temp.open("wb") as handle:
        pickle.dump(dataset, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temp, target)
    write_seconds = time.perf_counter() - started
    size = target.stat().st_size

    coverage_from = str(coverage_start or protocol.get("expanded_axes_effective_start") or window_start)
    coverage = _field_coverage(dataset, coverage_from, window_end)
    sidecar = {
        "schema": DATASET_CACHE_SCHEMA,
        "cache_path": str(target),
        "sha256": _sha256(target),
        "bytes": int(size),
        "price_basis": "pit_cumulative_adjusted_hfq",
        "execution_price_basis": "raw",
        "shape": [int(len(dataset.context.close.index)), int(len(dataset.context.close.columns))],
        "codes": len(codes),
        "start": window_start,
        "end": window_end,
        "warmup_days": warmup,
        "fetch_start": (
            pd.Timestamp(window_start) - pd.Timedelta(days=warmup)
        ).date().isoformat(),
        "build_seconds": round(build_seconds, 1),
        "write_seconds": round(write_seconds, 1),
        "coverage_window": {"start": coverage_from, "end": window_end},
        "data_manifest": dataset.data_manifest,
        "per_field_coverage": coverage["all_cells"],
        "per_field_coverage_eligible": coverage["eligible_cells"],
        "n_cells": coverage["n_cells"],
        "n_eligible_cells": coverage["n_eligible_cells"],
        "n_fields": len(coverage["all_cells"]),
        "generated_at": config.now_iso(),
    }
    sidecar_path = target.with_name(f"{target.stem}_dataset_v5.json")
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {**sidecar, "sidecar_path": str(sidecar_path)}
