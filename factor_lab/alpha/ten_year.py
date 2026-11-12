"""Reproducible 10-year PIT research protocol.

The actual campaign results are disposable runtime artifacts. This module
codifies the *procedure*: dataset window, ex-ante candidate-pool selection,
PIT universe policy, cost policy and a deterministic backtest entry point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.data import load_alpha_dataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.gates import alpha_gate_spec
from factor_lab.alpha.universe import build_pit_universe, load_universe_policy, universe_summary
from factor_lab.core import config
from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway


PROTOCOL_PATH = config.ALPHA_POLICY_DIR / "ten_year.json"
TEN_YEAR_SCHEMA = "factor_lab.alpha.ten_year_result.v1"


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
