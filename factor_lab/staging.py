"""In-process staging for factors that are not in FinBridge's registry yet.

P0 deliberately does not write ``factors/ops/price_factors.py``.  A staged
factor is injected into ``EXTRA_FACTORS`` inside the current Python process and
therefore disappears when the process exits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from factors.ops import price_factors

from .examples import amount_bias_21_252

PILOT_STAGERS: dict[str, dict[str, Any]] = {
    "liquidity.amount_bias_21_252": {
        "fn": amount_bias_21_252,
        "window": 252,
        "unit": "ratio",
        "source": "factor_lab.examples.amount_bias_21_252",
        "name": "amount_bias_21_252",
    }
}


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
    """Return staging metadata when the factor is a known lab factor.

    If the factor already resolves in FinBridge, return ``None`` (nothing to
    stage).  If it is unknown, raise a clear error instead of silently running a
    different factor.
    """

    try:
        price_factors.lookup(factor_id)
        return None
    except KeyError:
        pass

    spec = PILOT_STAGERS.get(factor_id)
    if spec is None:
        from .ideas import idea_for

        idea = idea_for(factor_id)
        if idea is not None:
            return stage_price_factor(
                factor_id,
                idea.fn,
                window=int(idea.window),
                unit=str(idea.unit),
                source="factor_lab.ideas",
            )
    if spec is None:
        from .cycle_ideas import CYCLE_SPECS

        cycle = next((item for item in CYCLE_SPECS if item.factor_id == factor_id), None)
        if cycle is not None:
            return stage_price_factor(
                factor_id,
                cycle.fn,
                window=int(cycle.window),
                unit=str(cycle.unit),
                source="factor_lab.cycle_ideas",
            )
    if spec is None:
        # Generated Lab factors are versioned outside the production registry.
        from .factor_library import factor_library_lookup

        generated = factor_library_lookup(factor_id)
        if generated is not None and generated.fn is not None:
            return stage_price_factor(
                factor_id,
                generated.fn,
                window=generated.window or 252,
                unit=generated.unit,
                source=f"factor_lab.factor_library:{generated.family}/{generated.version}",
            )
    if spec is None:
        raise KeyError(
            f"factor {factor_id!r} is neither registered nor known to the lab stager; "
            f"known lab factors: {sorted(PILOT_STAGERS)} + {len(_idea_factor_ids())} ideas"
        )
    return stage_price_factor(
        factor_id,
        spec["fn"],
        window=int(spec["window"]),
        unit=str(spec["unit"]),
        source=str(spec["source"]),
    )


def _idea_factor_ids() -> list[str]:
    from .ideas import IDEA_SPECS
    from .cycle_ideas import CYCLE_SPECS

    return [spec.factor_id for spec in IDEA_SPECS] + [spec.factor_id for spec in CYCLE_SPECS]


def unstage(factor_id: str) -> None:
    price_factors.EXTRA_FACTORS.pop(factor_id, None)
