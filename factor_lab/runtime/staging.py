"""In-process staging for generated Lab factors.

Generated factors live in the Lab factor library, not in FactorPickle's
production registry. A staged factor is injected into ``EXTRA_FACTORS`` inside
the current Python process and disappears when the process exits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from factors.ops import price_factors


@dataclass(frozen=True)
class StagedFactor:
    factor_id: str
    window: int
    unit: str
    source: str


def stage_price_factor(
    factor_id: str,
    fn: Callable[[pd.DataFrame], pd.Series],
    *,
    window: int,
    unit: str,
    source: str,
) -> StagedFactor:
    price_factors.EXTRA_FACTORS[factor_id] = (fn, int(window), str(unit))
    return StagedFactor(factor_id=factor_id, window=int(window), unit=str(unit), source=source)


def ensure_staged(factor_id: str) -> StagedFactor | None:
    """Stage a generated Lab factor, or return None when already registered.

    No static idea catalog or historical factor compatibility layer is
    consulted. Unknown factor ids fail explicitly.
    """
    try:
        price_factors.lookup(factor_id)
        return None
    except KeyError:
        pass

    from factor_lab.runtime.factor_library import factor_library_lookup

    generated = factor_library_lookup(factor_id)
    if generated is None or generated.fn is None:
        raise KeyError(
            f"factor {factor_id!r} is neither registered nor present in the Lab factor library"
        )
    return stage_price_factor(
        factor_id,
        generated.fn,
        window=generated.window or 20,
        unit=generated.unit,
        source=f"factor_lab.factor_library:{generated.family}/{generated.version}",
    )


def unstage(factor_id: str) -> None:
    price_factors.EXTRA_FACTORS.pop(factor_id, None)
