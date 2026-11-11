"""Versioned Factor Library for Alpha artifacts.

Factor artifacts are runtime research data under
``output/factor_lab/factors/generated``.  They are deliberately not copied
into the repository.  Each factor has a stable id and immutable version
directories; an Edge Card points at a concrete version + hash.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from factor_lab.alpha.validation import validate
from factor_lab.core import config
from factor_lab.core.store import sha256_file, write_json_atomic


FACTOR_SCHEMA = "factor_lab.factor_artifact.v1"
_SAFE = re.compile(r"^[a-zA-Z0-9_.-]+$")


@dataclass(frozen=True)
class FactorArtifact:
    factor_id: str
    family: str
    version: str
    path: Path
    manifest: dict[str, Any]

    @property
    def code_hash(self) -> str:
        return str(self.manifest.get("code_hash") or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "family": self.family,
            "version": self.version,
            "path": str(self.path),
            "manifest": self.manifest,
        }


def default_library_root() -> Path:
    return config.LAB_ROOT / "factors" / "generated"


def _safe(value: str) -> str:
    text = str(value).strip()
    if not text or not _SAFE.match(text):
        raise ValueError(f"invalid factor path component: {value!r}")
    return text


def _versions(path: Path) -> list[int]:
    if not path.exists():
        return []
    values: list[int] = []
    for child in path.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            values.append(int(child.name[1:]))
    return sorted(values)


def write_factor_artifact(
    *,
    factor_id: str,
    family: str,
    spec: dict[str, Any],
    expression: str | None = None,
    code: str | None = None,
    source: str = "factor_builder",
    version: str | None = None,
    parent_version: str | None = None,
    library_root: str | Path | None = None,
    extra_tests: str | None = None,
) -> FactorArtifact:
    """Write one immutable factor version and return its artifact."""
    factor_id = _safe(factor_id)
    family = _safe(family)
    if bool(expression) == bool(code):
        raise ValueError("provide exactly one of expression or code")
    if expression:
        report = validate(expression)
        if not report.ok:
            raise ValueError("; ".join(report.issues))
    root = Path(library_root) if library_root is not None else default_library_root()
    factor_dir = root / family / factor_id
    factor_dir.mkdir(parents=True, exist_ok=True)
    existing = _versions(factor_dir)
    if version is None:
        version = f"v{max(existing or [0]) + 1}"
    if not re.fullmatch(r"v[0-9]+", version):
        raise ValueError("version must look like v1, v2, ...")
    target = factor_dir / version
    if target.exists():
        raise FileExistsError(f"factor version is immutable and already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)

    if expression:
        asset = target / "factor.dsl"
        asset.write_text(str(expression).strip() + "\n", encoding="utf-8")
        kind = "dsl"
    else:
        asset = target / "factor.py"
        asset.write_text(str(code), encoding="utf-8")
        kind = "python"
    code_hash = sha256_file(asset)
    spec_payload = dict(spec)
    spec_payload.setdefault("factor_id", factor_id)
    spec_payload.setdefault("family", family)
    spec_payload.setdefault("version", version)
    spec_payload.setdefault("kind", kind)
    spec_payload.setdefault("data_contract", {})
    write_json_atomic(target / "spec.json", spec_payload)

    test_text = (
        '"""Runtime factor artifact tests (generated)."""\n'
        "from pathlib import Path\n\n"
        "def test_artifact_files_exist():\n"
        "    root = Path(__file__).resolve().parent\n"
        "    assert (root / 'spec.json').exists()\n"
        "    assert any((root / name).exists() for name in ('factor.dsl', 'factor.py'))\n"
    )
    if extra_tests:
        test_text += "\n" + extra_tests.strip() + "\n"
    (target / "test_factor.py").write_text(test_text, encoding="utf-8")

    manifest = {
        "schema": FACTOR_SCHEMA,
        "factor_id": factor_id,
        "family": family,
        "version": version,
        "parent_version": parent_version,
        "kind": kind,
        "code_hash": code_hash,
        "source": source,
        "spec": spec_payload,
        "data_contract": spec_payload.get("data_contract") or {},
        "created_at": config.now_iso(),
        "assets": {
            "main": str(asset.relative_to(target)),
            "spec": "spec.json",
            "tests": "test_factor.py",
        },
    }
    write_json_atomic(target / "manifest.json", manifest)
    return FactorArtifact(factor_id=factor_id, family=family, version=version, path=target, manifest=manifest)


def load_manifest(path_or_factor: str | Path) -> dict[str, Any]:
    path = Path(path_or_factor)
    if path.is_dir():
        path = path / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != FACTOR_SCHEMA:
        raise ValueError(f"invalid factor manifest: {path}")
    return raw


def resolve_factor(
    factor_id: str,
    *,
    family: str | None = None,
    version: str | None = None,
    library_root: str | Path | None = None,
) -> FactorArtifact:
    root = Path(library_root) if library_root is not None else default_library_root()
    candidates: list[Path] = []
    if family is not None:
        candidates = [root / _safe(family) / _safe(factor_id)]
    elif root.exists():
        candidates = list(root.glob(f"*/{_safe(factor_id)}"))
    if not candidates:
        raise FileNotFoundError(f"factor not found: {factor_id}")
    factor_dir = candidates[0]
    if version is None:
        versions = _versions(factor_dir)
        if not versions:
            raise FileNotFoundError(f"factor has no versions: {factor_dir}")
        version = f"v{max(versions)}"
    target = factor_dir / version
    if not (target / "manifest.json").exists():
        raise FileNotFoundError(f"factor version not found: {target}")
    manifest = load_manifest(target)
    return FactorArtifact(
        factor_id=str(manifest["factor_id"]),
        family=str(manifest["family"]),
        version=str(manifest["version"]),
        path=target,
        manifest=manifest,
    )


def list_factors(
    *,
    family: str | None = None,
    library_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(library_root) if library_root is not None else default_library_root()
    if not root.exists():
        return []
    pattern = f"{_safe(family)}/*/v*/manifest.json" if family else "*/**/v*/manifest.json"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob(pattern)):
        try:
            manifest = load_manifest(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "factor_id": manifest.get("factor_id"),
                "family": manifest.get("family"),
                "version": manifest.get("version"),
                "code_hash": manifest.get("code_hash"),
                "path": str(path.parent),
            }
        )
    return rows


def library_summary(*, library_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(library_root) if library_root is not None else default_library_root()
    factors = list_factors(library_root=root)
    families: dict[str, int] = {}
    for row in factors:
        family = str(row.get("family") or "unknown")
        families[family] = families.get(family, 0) + 1
    return {
        "schema": "factor_lab.factor_library.v1",
        "root": str(root),
        "n_artifacts": len(factors),
        "n_families": len(families),
        "families": families,
        "factors": factors,
    }
