"""Versioned Lab factor library.

FactorBuilder writes generated factor code here, never into the production
``factors`` registry.  Every version is immutable; a change creates a new
version directory.  Runner resolves ``factor_id + version`` through this module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from factor_lab.core.config import FACTOR_LIBRARY_DIR, now_iso
from factor_lab.core.store import write_json_atomic

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,80}$")
_VERSION_RE = re.compile(r"^v(\d+)$")


class FactorLibraryError(RuntimeError):
    pass


@dataclass
class FactorVersion:
    family: str
    factor_id: str
    version: str
    root: Path
    code: str
    spec: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    fn: Callable[[pd.DataFrame], pd.Series] | None = None

    @property
    def code_hash(self) -> str:
        return str(self.manifest.get("code_hash") or hashlib.sha256(self.code.encode("utf-8")).hexdigest())

    @property
    def window(self) -> int:
        return int(self.spec.get("window", 0) or 0)

    @property
    def unit(self) -> str:
        return str(self.spec.get("unit", "ratio"))

    @property
    def category(self) -> str:
        return str(self.spec.get("category") or self.factor_id.split(".", 1)[0])

    def artifact(self) -> dict[str, Any]:
        """Minimal artifact contract consumed by Runner/PanelBuilder."""

        return {
            "factor_id": self.factor_id,
            "family": self.family,
            "version": self.version,
            "code_hash": self.code_hash,
            "category": self.category,
            "spec": dict(self.spec),
            "data_contract": dict(self.spec.get("data_contract") or {}),
        }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{now_iso().replace(':', '')}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class FactorLibrary:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or FACTOR_LIBRARY_DIR)

    def _validate_ids(self, family: str, factor_id: str, version: str) -> None:
        if not _ID_RE.match(family):
            raise FactorLibraryError(f"invalid family: {family!r}")
        if not _ID_RE.match(factor_id):
            raise FactorLibraryError(f"invalid factor_id: {factor_id!r}")
        if not _VERSION_RE.match(version):
            raise FactorLibraryError(f"invalid version: {version!r} (expected v1, v2, ...)")

    def _dir(self, family: str, factor_id: str, version: str) -> Path:
        self._validate_ids(family, factor_id, version)
        return self.root / family / factor_id / version

    def write_version(
        self,
        *,
        family: str,
        factor_id: str,
        version: str,
        code: str,
        spec: dict[str, Any] | None = None,
        tests: str | None = None,
        data_contract: dict[str, Any] | None = None,
        parent_version: str | None = None,
    ) -> FactorVersion:
        """Create one immutable version.

        The caller is FactorBuilder; it has already generated Python code.  We
        only do syntax/function/unit-test checks here (not a permission gate).
        """

        target = self._dir(family, factor_id, version)
        if target.exists():
            existing = self.load(factor_id, version)
            if existing.code == code and existing.spec == (spec or {}):
                return existing
            raise FactorLibraryError(f"version already exists and is immutable: {target}")
        try:
            compile(code, str(target / "factor.py"), "exec")
        except SyntaxError as exc:
            raise FactorLibraryError(f"factor code syntax error: {exc}") from exc
        if "def factor(" not in code and "def compute(" not in code:
            raise FactorLibraryError("factor code must define factor(frame) or compute(frame)")

        spec_payload = dict(spec or {})
        spec_payload.setdefault("factor_id", factor_id)
        spec_payload.setdefault("family", family)
        spec_payload.setdefault("version", version)
        spec_payload.setdefault("category", factor_id.split(".", 1)[0] if "." in factor_id else "price")
        if data_contract:
            spec_payload["data_contract"] = dict(data_contract)
        elif not isinstance(spec_payload.get("data_contract"), dict):
            spec_payload["data_contract"] = {}
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        manifest = {
            "schema_version": "factor_lab.factor_manifest.v1",
            "factor_id": factor_id,
            "family": family,
            "version": version,
            "code_hash": code_hash,
            "parent_version": parent_version,
            "spec": spec_payload,
            "created_at": now_iso(),
            "tests": {"status": "not-run"},
        }
        target.mkdir(parents=True, exist_ok=False)
        _atomic_write(target / "factor.py", code)
        if tests:
            _atomic_write(target / "test_factor.py", tests)
        write_json_atomic(target / "spec.json", spec_payload)
        write_json_atomic(target / "manifest.json", manifest)

        # Validate import and callable. Running arbitrary unit tests is the
        # caller's explicit step (``run_tests``), not implicit here.
        loaded = self.load(factor_id, version)
        if loaded.fn is None:
            raise FactorLibraryError("factor module loaded but no callable factor/compute")
        return loaded

    def run_tests(self, family: str, factor_id: str, version: str) -> dict[str, Any]:
        target = self._dir(family, factor_id, version)
        test_file = target / "test_factor.py"
        if not test_file.exists():
            return {"status": "missing", "file": str(test_file)}
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(test_file)],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=180,
        )
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        manifest["tests"] = {
            "status": "pass" if proc.returncode == 0 else "fail",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
        write_json_atomic(target / "manifest.json", manifest)
        return manifest["tests"]

    def load(self, factor_id: str, version: str | None = None, *, family: str | None = None) -> FactorVersion:
        if family:
            candidates = [self._dir(family, factor_id, version)] if version else []
            if not candidates:
                versions = sorted(
                    [p for p in (self.root / family / factor_id).glob("v*") if p.is_dir()],
                    key=lambda p: int(p.name[1:]),
                )
                candidates = versions[-1:]
        else:
            candidates = list(self.root.glob(f"*/{factor_id}/v*"))
            candidates = [p for p in candidates if p.is_dir()]
            if version:
                candidates = [p for p in candidates if p.name == version]
            else:
                candidates.sort(key=lambda p: int(p.name[1:]))
                candidates = candidates[-1:]
        if not candidates:
            raise FactorLibraryError(f"factor not found: {factor_id}@{version or 'latest'}")
        target = candidates[0]
        if not (target / "factor.py").exists():
            raise FactorLibraryError(f"factor.py missing: {target}")
        code = (target / "factor.py").read_text(encoding="utf-8")
        spec = json.loads((target / "spec.json").read_text(encoding="utf-8"))
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        module_name = f"factor_lab_generated_{target.parent.parent.name}_{factor_id}_{target.name}".replace(
            ".", "_"
        ).replace("-", "_")
        module_spec = importlib.util.spec_from_file_location(module_name, target / "factor.py")
        if module_spec is None or module_spec.loader is None:
            raise FactorLibraryError(f"cannot load factor module: {target / 'factor.py'}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            raise FactorLibraryError(f"factor import failed: {type(exc).__name__}: {exc}") from exc
        fn = getattr(module, "factor", None) or getattr(module, "compute", None)
        return FactorVersion(
            family=str(manifest.get("family") or target.parent.parent.name),
            factor_id=str(manifest.get("factor_id") or factor_id),
            version=str(manifest.get("version") or target.name),
            root=target,
            code=code,
            spec=spec,
            manifest=manifest,
            fn=fn if callable(fn) else None,
        )

    def list_versions(self, factor_id: str) -> list[FactorVersion]:
        result: list[FactorVersion] = []
        for path in sorted(self.root.glob(f"*/{factor_id}/v*")):
            if path.is_dir():
                try:
                    result.append(self.load(factor_id, path.name, family=path.parent.parent.name))
                except FactorLibraryError:
                    continue
        return result

    def lookup(self, factor_id: str) -> FactorVersion | None:
        try:
            return self.load(factor_id)
        except FactorLibraryError:
            return None


LIBRARY = FactorLibrary()


def factor_library_lookup(factor_id: str) -> FactorVersion | None:
    """Convenience hook for ``staging.ensure_staged``."""

    return LIBRARY.lookup(factor_id)
