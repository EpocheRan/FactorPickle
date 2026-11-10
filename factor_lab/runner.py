"""Factor Lab Runner orchestration layer.

Naming boundary:

- ``factor_lab.runner.FactorRunner`` is the Runner role: data preflight,
  data_bundle construction, factor-artifact loading and Adapter invocation.
- ``research.report.runner.generate`` is a research-library module, not a
  Runner role.  It remains the Research Module facade and is not modified here.

The Runner never computes a factor matrix.  It may build a raw ``Panel`` /
``data_bundle``; ``research.report.runner.generate`` computes the factor and
metrics internally, then returns the Research Result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config
from .data_interface_adapter import DataInterfaceGateway
from .panel_builder import BuiltPanel, build_factor_panel
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

    def build_panel(
        self,
        codes: list[str],
        start: str,
        end: str,
        *,
        factor_artifact: Any | None = None,
        factor_id: str | None = None,
    ) -> BuiltPanel:
        """Build a Runner-owned Panel from DataInterfaceGateway only."""

        if not codes:
            raise RunnerError("codes are required to build a Runner panel")
        artifact = factor_artifact or {"factor_id": factor_id or ""}
        if not factor_id and not factor_artifact:
            raise RunnerError("factor_artifact or factor_id is required to build a panel")
        self.preflight()
        # Do not fetch a fixed 800-day warmup for every factor: the gateway
        # otherwise tries to repair years of irrelevant history from external
        # sources.  Use 3x the factor window plus a small buffer, capped.
        spec = dict(getattr(artifact, "spec", {}) or {}) if not isinstance(artifact, dict) else dict(artifact.get("spec") or {})
        window = int(spec.get("window", 20) or 20)
        warmup_days = max(60, min(800, window + 20))
        return build_factor_panel(
            self.gateway, artifact, codes, start, end, warmup_days=warmup_days
        )

    def _build_panel_for_hypothesis(
        self,
        hypothesis: Hypothesis,
        codes: list[str],
        factor_artifact: Any | None = None,
    ) -> BuiltPanel:
        return self.build_panel(
            codes,
            hypothesis.start,
            hypothesis.end or "2026-11-01",
            factor_artifact=factor_artifact,
            factor_id=hypothesis.factor_id,
        )

    def run(
        self,
        hypothesis: Hypothesis,
        *,
        profile: str = "quick",
        robustness: bool = False,
        auto_audit: bool = True,
        panel: Any | None = None,
        built_panel: BuiltPanel | None = None,
        use_data_bundle: bool = True,
        factor_artifact: Any | None = None,
        codes: list[str] | None = None,
    ) -> dict[str, Any]:
        status = self.preflight()
        artifact_meta: dict[str, Any] = {}
        if factor_artifact is None:
            from .factor_library import factor_library_lookup

            generated = factor_library_lookup(hypothesis.factor_id)
            if generated is not None:
                factor_artifact = generated
        if factor_artifact is not None:
            artifact_factor_id = (
                getattr(factor_artifact, "factor_id", None)
                or (factor_artifact.get("factor_id") if isinstance(factor_artifact, dict) else None)
            )
            if not artifact_factor_id:
                raise RunnerError("factor_artifact must carry factor_id")
            if str(artifact_factor_id) != hypothesis.factor_id:
                raise RunnerError(
                    f"factor_artifact.factor_id={artifact_factor_id!r} does not match hypothesis.factor_id={hypothesis.factor_id!r}"
                )
            artifact_meta = {
                "factor_id": str(artifact_factor_id),
                "version": getattr(factor_artifact, "version", None)
                or (factor_artifact.get("version") if isinstance(factor_artifact, dict) else None),
                "code_hash": getattr(factor_artifact, "code_hash", None)
                or (factor_artifact.get("code_hash") if isinstance(factor_artifact, dict) else None),
                "family": getattr(factor_artifact, "family", None)
                or (factor_artifact.get("family") if isinstance(factor_artifact, dict) else None),
            }
        if built_panel is None and use_data_bundle:
            if not codes:
                raise RunnerError("Runner data path requires codes (Runner LLM should select data)")
            built_panel = self._build_panel_for_hypothesis(hypothesis, codes, factor_artifact)
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
                    "factor_artifact": artifact_meta,
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
