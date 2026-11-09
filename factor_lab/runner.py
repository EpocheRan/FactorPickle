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
from .panel_builder import BuiltPanel, build_price_panel
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

    def build_panel(self, codes: list[str], start: str, end: str) -> BuiltPanel:
        """Build a Runner-owned price panel via data_interface.

        Callers remain responsible for choosing codes from the factor data
        contract; no automatic fallback to the legacy research loader.
        """

        self.preflight()
        return build_price_panel(self.gateway, codes, start, end)

    def run(
        self,
        hypothesis: Hypothesis,
        *,
        profile: str = "quick",
        robustness: bool = False,
        auto_audit: bool = True,
        panel: Any | None = None,
        built_panel: BuiltPanel | None = None,
    ) -> dict[str, Any]:
        status = self.preflight()
        if built_panel is not None:
            panel = built_panel.panel
        from .run import run_experiment

        result = run_experiment(
            hypothesis,
            profile=profile,
            robustness=robustness,
            auto_audit=auto_audit,
            panel=panel,
        )
        experiment_dir = Path(str(result.get("experiment_dir") or ""))
        manifest_path = experiment_dir / "data_manifest.json"
        if experiment_dir.exists():
            experiment_manifest = read_json(experiment_dir / "manifest.json") or {}
            manifest = built_panel.manifest.to_dict() if built_panel else {}
            write_json_atomic(
                manifest_path,
                {
                    "schema_version": "factor_lab.data_manifest.v1",
                    "source": "data_interface",
                    "data_path": "runner_panel" if panel is not None else "legacy_research_loader",
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
                    "data_interface_manifest": manifest,
                    "note": (
                        "data_path=runner_panel means Runner built a Panel from data_interface "
                        "and passed panel= into the Research Adapter. data_path=legacy_research_loader "
                        "means the legacy research panel loader was used."
                    ),
                },
            )
        result["data_manifest"] = str(manifest_path)
        result["data_interface_status"] = status
        return result
