"""Runner orchestration layer.

The Runner is an LLM role in the target architecture.  This module provides the
deterministic execution seam it calls: preflight the data_interface, run the
existing research experiment, and persist a data_manifest.  The LLM itself is
invoked by the DSH workflow / campaign layer, not by this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config
from .data_interface_adapter import DataInterfaceGateway
from .schemas import Hypothesis
from .store import read_json, write_json_atomic


class RunnerError(RuntimeError):
    pass


class FactorRunner:
    def __init__(self, gateway: DataInterfaceGateway | None = None) -> None:
        self.gateway = gateway or DataInterfaceGateway()

    def preflight(self) -> dict[str, Any]:
        status = self.gateway.status()
        from .readiness import require_data

        require_data(status)
        return status

    def run(
        self,
        hypothesis: Hypothesis,
        *,
        profile: str = "quick",
        robustness: bool = False,
        auto_audit: bool = True,
    ) -> dict[str, Any]:
        status = self.preflight()
        from .run import run_experiment

        result = run_experiment(
            hypothesis,
            profile=profile,
            robustness=robustness,
            auto_audit=auto_audit,
        )
        experiment_dir = Path(str(result.get("experiment_dir") or ""))
        manifest_path = experiment_dir / "data_manifest.json"
        if experiment_dir.exists():
            experiment_manifest = read_json(experiment_dir / "manifest.json") or {}
            write_json_atomic(
                manifest_path,
                {
                    "schema_version": "factor_lab.data_manifest.v1",
                    "source": "data_interface",
                    "services": status.get("services", []),
                    "data_interface": status,
                    "factor_id": hypothesis.factor_id,
                    "start": hypothesis.start,
                    "end": hypothesis.end,
                    "universe": hypothesis.outcomes[0].universe if hypothesis.outcomes else "",
                    "experiment_id": result.get("exp_id"),
                    "run_id": result.get("run_id"),
                    "data_version": experiment_manifest.get("data_version"),
                    "report_meta": experiment_manifest.get("report_meta"),
                    "note": (
                        "Runner preflight is data_interface-only. The legacy research.report "
                        "panel loader still executes behind ResearchReportAdapter; field-level "
                        "lineage is in the experiment manifest/report_meta."
                    ),
                },
            )
        result["data_manifest"] = str(manifest_path)
        result["data_interface_status"] = status
        return result
