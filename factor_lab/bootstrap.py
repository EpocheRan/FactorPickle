"""Portable entrypoint: bootstrap, doctor, and new-session brief."""

from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path
from typing import Any

from . import budget, config
from .control import ensure_control, load_control
from .data_interface_adapter import DataInterfaceGateway
from .directive import ensure_directive
from .policy import init_policy
from .retention import ensure_policy
from .store import write_json_atomic
from .supervisor import write_heartbeat

AGENTS_PATH = config.PROJECT_ROOT / "AGENTS.md"
FACTOR_LAB_PATH = config.PROJECT_ROOT / "FACTOR_LAB.md"
SESSION_BRIEF_JSON = config.LAB_ROOT / "session_brief.json"
SESSION_BRIEF_MD = config.LAB_ROOT / "session_brief.md"


def _ensure_quarantine() -> None:
    if not config.QUARANTINE_PATH.exists():
        write_json_atomic(
            config.QUARANTINE_PATH,
            {"schema_version": "factor_lab.quarantine.v1", "updated_at": config.now_iso(), "items": []},
        )


def _ensure_coverage() -> None:
    if config.DATA_COVERAGE_PATH.exists():
        return
    try:
        from .examples import pilot_coverage

        write_json_atomic(config.DATA_COVERAGE_PATH, pilot_coverage())
    except Exception:
        pass


def write_session_brief(*, research: bool = False) -> dict[str, Any]:
    directive = ensure_directive()
    control = load_control()
    payload = {
        "schema_version": "factor_lab.session_brief.v1",
        "generated_at": config.now_iso(),
        "workspace": str(config.PROJECT_ROOT),
        "lab_root": str(config.LAB_ROOT),
        "research_requested": bool(research),
        "control": control,
        "directive": directive,
        "commands": {
            "doctor": "python -m factor_lab doctor",
            "start_research": "python -m factor_lab campaign start --objective \"研究因子\"",
            "dashboard": "python -m factor_lab dashboard",
            "budget": "python -m factor_lab budget status",
        },
        "next": (
            "run doctor; if green, start a research campaign"
            if research
            else "run doctor before starting research"
        ),
    }
    write_json_atomic(SESSION_BRIEF_JSON, payload)
    lines = [
        "# Factor Lab session brief",
        "",
        f"- workspace: `{config.PROJECT_ROOT}`",
        f"- lab root: `{config.LAB_ROOT}`",
        f"- research requested: `{bool(research)}`",
        f"- control: `{control.get('state')}`",
        f"- directive family: `{directive.get('family_id')}`",
        "",
        "## Commands",
        "",
        "```powershell",
        "python -m factor_lab doctor",
        "python -m factor_lab campaign start --objective \"研究因子\"",
        "python -m factor_lab budget status",
        "```",
        "",
        "Runner data access is only through `data_interface`.",
    ]
    SESSION_BRIEF_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def bootstrap(*, research: bool = False) -> dict[str, Any]:
    config.ensure_dirs()
    policy = init_policy()
    control = ensure_control()
    directive = ensure_directive()
    retention = ensure_policy()
    budget.ensure_policy()
    write_heartbeat(interval_sec=120)
    _ensure_quarantine()
    _ensure_coverage()
    brief = write_session_brief(research=research)
    return {
        "schema_version": "factor_lab.bootstrap.v1",
        "workspace": str(config.PROJECT_ROOT),
        "policy_version": policy.get("version"),
        "control": control,
        "directive_family": directive.get("family_id"),
        "retention": retention.get("schema_version"),
        "session_brief": str(SESSION_BRIEF_JSON),
        "brief": brief,
        "code_changed": False,
    }


def doctor(*, probe_data: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any, required: bool = True) -> None:
        checks.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})

    add("python", sys.version_info >= (3, 11), platform.python_version())
    for module in ("pandas", "numpy", "sklearn"):
        try:
            importlib.import_module(module)
            add(f"import:{module}", True, "ok")
        except Exception as exc:  # noqa: BLE001
            add(f"import:{module}", False, f"{type(exc).__name__}: {exc}")
    add("workspace:AGENTS.md", AGENTS_PATH.exists(), str(AGENTS_PATH))
    add("workspace:FACTOR_LAB.md", FACTOR_LAB_PATH.exists(), str(FACTOR_LAB_PATH))
    try:
        add("import:data_interface", True, importlib.import_module("data_interface").__name__)
    except Exception as exc:  # noqa: BLE001
        add("import:data_interface", False, f"{type(exc).__name__}: {exc}")
    try:
        status = DataInterfaceGateway().status()
        add("data_interface:services", bool(status.get("available")), status)
    except Exception as exc:  # noqa: BLE001
        add("data_interface:services", False, f"{type(exc).__name__}: {exc}")
    try:
        disk = budget.disk_status()
        add("disk", bool(disk.get("ok")), disk)
    except Exception as exc:  # noqa: BLE001
        add("disk", False, f"{type(exc).__name__}: {exc}")
    try:
        pol = budget.load_policy()
        flash = (pol.pricing or {}).get("deepseek-flash") or {}
        priced = float(flash.get("input_rmb_per_mtok", 0) or 0) > 0 and float(
            flash.get("output_rmb_per_mtok", 0) or 0
        ) > 0
        add("budget", priced, pol.to_dict(), required=True)
    except Exception as exc:  # noqa: BLE001
        add("budget", False, f"{type(exc).__name__}: {exc}", required=True)
    required_ok = all(item["ok"] for item in checks if item["required"])
    return {
        "schema_version": "factor_lab.doctor.v1",
        "ok": required_ok,
        "checks": checks,
        "probe_data": bool(probe_data),
        "workspace": str(config.PROJECT_ROOT),
    }
