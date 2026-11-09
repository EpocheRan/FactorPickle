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
from .panel_builder import BuiltPanel, build_panel_like_research, build_price_panel
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
        codes: list[str] | None,
        start: str,
        end: str,
        *,
        factor_id: str | None = None,
        pool_date: str | None = None,
        pool_size: int = 200,
        universe: str | None = None,
    ) -> BuiltPanel:
        """Build a Runner-owned panel.

        ``codes`` selects the direct data_interface OHLC builder; without
        codes we copy the data-construction part of research._resolve_panel
        (the same loader for now, so regular factor tests remain untouched).
        """

        self.preflight()
        if codes:
            return build_price_panel(self.gateway, codes, start, end)
        if not factor_id:
            raise RunnerError("factor_id or codes is required to build a panel")
        return build_panel_like_research(
            factor_id,
            pool_date=pool_date,
            pool_size=pool_size,
            start=start,
            end=end,
            universe=universe,
        )

    def _build_panel_for_hypothesis(self, hypothesis: Hypothesis) -> BuiltPanel:
        outcome = hypothesis.outcomes[0] if hypothesis.outcomes else None
        from .directive import load_directive

        directive = load_directive()
        return self.build_panel(
            None,
            hypothesis.start,
            hypothesis.end or str(directive.get("end") or "2026-11-01"),
            factor_id=hypothesis.factor_id,
            pool_date=directive.get("pool_date"),
            pool_size=int(directive.get("pool_size", 200) or 200),
            universe=outcome.universe if outcome else directive.get("universe"),
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
        use_data_bundle: bool = False,
    ) -> dict[str, Any]:
        status = self.preflight()
        if built_panel is None and use_data_bundle:
            built_panel = self._build_panel_for_hypothesis(hypothesis)
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
