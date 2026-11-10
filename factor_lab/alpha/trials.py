"""Trial accounting and multiple-testing controls for Alpha search."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic


def alpha_dir() -> Path:
    path = config.LAB_ROOT / "alpha"
    path.mkdir(parents=True, exist_ok=True)
    return path


def trials_path() -> Path:
    return alpha_dir() / "trials.jsonl"


def append_trial(payload: dict[str, Any]) -> dict[str, Any]:
    record = dict(payload)
    path = trials_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record


def read_trials() -> list[dict[str, Any]]:
    path = trials_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def count_trials() -> int:
    return len(read_trials())


def p_value_from_t(t_stat: float, n: int) -> float:
    """Two-sided normal approximation; conservative for small n."""
    if not math.isfinite(t_stat) or n < 3:
        return 1.0
    # Normal survival approximation without scipy dependency.
    z = abs(float(t_stat))
    return float(min(1.0, 2.0 * 0.5 * math.erfc(z / math.sqrt(2.0))))


def benjamini_hochberg(pvalues: Iterable[float], q: float = 0.1) -> list[dict[str, Any]]:
    items = [(index, float(value)) for index, value in enumerate(pvalues)]
    items.sort(key=lambda item: item[1])
    n = len(items)
    accepted: set[int] = set()
    last_k = 0
    for rank, (_, pvalue) in enumerate(items, start=1):
        if pvalue <= q * rank / max(1, n):
            last_k = rank
    for rank, (index, _) in enumerate(items, start=1):
        if rank <= last_k:
            accepted.add(index)
    return [
        {
            "index": index,
            "p_value": pvalue,
            "rank": rank,
            "passed": index in accepted,
            "threshold": q * rank / max(1, n),
        }
        for rank, (index, pvalue) in enumerate(items, start=1)
    ]


def summarize_trials(limit: int = 20) -> dict[str, Any]:
    rows = read_trials()
    return {
        "count": len(rows),
        "last": rows[-limit:],
        "path": str(trials_path()),
    }
