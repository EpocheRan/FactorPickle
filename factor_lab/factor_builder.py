"""FactorBuilder write path.

The LLM FactorBuilder calls this module with generated source + spec.  This
module writes an immutable version into the Lab factor library and optionally
runs the generated unit test.  It never touches the production factor registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .factor_library import FactorLibrary, FactorLibraryError, FactorVersion


class FactorBuildError(RuntimeError):
    pass


def build(
    *,
    family: str,
    factor_id: str,
    code: str,
    spec: dict[str, Any],
    tests: str | None = None,
    version: str = "v1",
    parent_version: str | None = None,
    library: FactorLibrary | None = None,
    run_tests: bool = False,
) -> FactorVersion:
    lib = library or FactorLibrary()
    try:
        result = lib.write_version(
            family=family,
            factor_id=factor_id,
            version=version,
            code=code,
            spec=spec,
            tests=tests,
            data_contract=dict(spec.get("data_contract") or {}),
            parent_version=parent_version,
        )
    except FactorLibraryError as exc:
        raise FactorBuildError(str(exc)) from exc
    if run_tests:
        test_result = lib.run_tests(family, factor_id, version)
        if test_result.get("status") != "pass":
            raise FactorBuildError(f"generated factor tests failed: {test_result}")
    return result


def build_from_files(
    *,
    family: str,
    factor_id: str,
    code_path: str | Path,
    spec_path: str | Path,
    tests_path: str | Path | None = None,
    version: str = "v1",
    library: FactorLibrary | None = None,
) -> FactorVersion:
    import json

    code = Path(code_path).read_text(encoding="utf-8")
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    tests = Path(tests_path).read_text(encoding="utf-8") if tests_path else None
    return build(
        family=family,
        factor_id=factor_id,
        code=code,
        spec=spec,
        tests=tests,
        version=version,
        library=library,
    )
