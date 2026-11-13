"""Long-only portfolio construction with industry/size controls and a simple risk model.

The research Gate evaluates a signal book (equal-weight top quintile).  This
module is the next layer: it turns a factor row into constrained long-only
weights before execution.  It is deliberately deterministic and auditable; it
does not fit expected returns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factor_lab.core import config


PORTFOLIO_SCHEMA = "factor_lab.alpha.portfolio_policy.v1"


@dataclass(frozen=True)
class PortfolioPolicy:
    schema: str = PORTFOLIO_SCHEMA
    top_fraction: float = 0.20
    min_names: int = 20
    max_weight: float = 0.05
    max_industry_weight: float = 0.30
    size_neutral: bool = True
    size_tilt: float = 0.0
    risk_lookback: int = 60
    risk_shrinkage: float = 0.50
    turnover_limit: float = 0.50
    min_coverage: float = 0.90

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "top_fraction": float(self.top_fraction),
            "min_names": int(self.min_names),
            "max_weight": float(self.max_weight),
            "max_industry_weight": float(self.max_industry_weight),
            "size_neutral": bool(self.size_neutral),
            "size_tilt": float(self.size_tilt),
            "risk_lookback": int(self.risk_lookback),
            "risk_shrinkage": float(self.risk_shrinkage),
            "turnover_limit": float(self.turnover_limit),
            "min_coverage": float(self.min_coverage),
        }


def load_portfolio_policy(path: str | Path | None = None) -> PortfolioPolicy:
    target = Path(path) if path is not None else Path(config.ALPHA_POLICY_DIR) / "portfolio.json"
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return PortfolioPolicy()
    if raw.get("schema") not in (None, PORTFOLIO_SCHEMA):
        raise ValueError(f"unsupported portfolio policy schema: {raw.get('schema')}")
    return PortfolioPolicy(
        top_fraction=float(raw.get("top_fraction", 0.20)),
        min_names=int(raw.get("min_names", 20)),
        max_weight=float(raw.get("max_weight", 0.05)),
        max_industry_weight=float(raw.get("max_industry_weight", 0.30)),
        size_neutral=bool(raw.get("size_neutral", True)),
        size_tilt=float(raw.get("size_tilt", 0.0)),
        risk_lookback=int(raw.get("risk_lookback", 60)),
        risk_shrinkage=float(raw.get("risk_shrinkage", 0.50)),
        turnover_limit=float(raw.get("turnover_limit", 0.50)),
        min_coverage=float(raw.get("min_coverage", 0.90)),
    )


def _cap_and_normalize(weights: pd.Series, max_weight: float) -> pd.Series:
    w = weights.clip(lower=0.0)
    if w.sum() <= 0:
        return w
    w = w / w.sum()
    for _ in range(50):
        over = w > max_weight + 1e-12
        if not over.any():
            break
        excess = float((w[over] - max_weight).sum())
        w[over] = max_weight
        under = ~over & (w > 0)
        if not under.any():
            break
        w[under] += excess * w[under] / w[under].sum()
    return w / w.sum() if w.sum() > 0 else w


def _risk_weights(
    names: pd.Index,
    context: Any,
    *,
    lookback: int,
    shrinkage: float,
    as_of: pd.Timestamp | None = None,
) -> pd.Series:
    returns = context.fields.get("returns")
    if returns is None or returns.empty:
        return pd.Series(1.0 / len(names), index=names)
    if as_of is not None:
        returns = returns.loc[: pd.Timestamp(as_of)]
    recent = returns.reindex(columns=names).tail(int(lookback)).dropna(how="all", axis=1)
    vol = recent.std(axis=0, ddof=1).replace(0.0, np.nan)
    if vol.notna().sum() < 2:
        return pd.Series(1.0 / len(names), index=names)
    median_vol = float(vol.median())
    vol = vol.fillna(median_vol)
    inv = 1.0 / vol
    # Shrink raw inverse-vol weights toward equal weight.
    equal = pd.Series(1.0 / len(names), index=names)
    inv = inv / inv.sum()
    return (1.0 - float(shrinkage)) * inv + float(shrinkage) * equal


def build_long_only_weights(
    factor_row: pd.Series,
    eligible: pd.Series,
    *,
    context: Any,
    industry: pd.Series | None = None,
    float_mv: pd.DataFrame | None = None,
    previous_weights: pd.Series | None = None,
    policy: PortfolioPolicy | None = None,
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.Series, dict[str, Any]]:
    """Build constrained long-only weights for one rebalance date."""
    policy = policy or load_portfolio_policy()
    eligible = eligible.reindex(factor_row.index).fillna(False).astype(bool)
    scores = factor_row.where(eligible).dropna()
    if len(scores) < max(10, int(policy.min_names)):
        raise ValueError(f"portfolio has too few eligible names: {len(scores)}")
    n_select = max(int(policy.min_names), int(round(len(scores) * float(policy.top_fraction))))
    n_select = min(n_select, len(scores))
    selected = scores.nlargest(n_select).index
    weights = _risk_weights(selected, context, lookback=policy.risk_lookback, shrinkage=policy.risk_shrinkage, as_of=as_of)
    weights = _cap_and_normalize(weights, policy.max_weight)
    diagnostics: dict[str, Any] = {
        "schema": "factor_lab.alpha.portfolio_construction.v1",
        "n_selected": int(len(selected)),
        "n_eligible": int(len(scores)),
        "policy": policy.to_dict(),
        "industry_neutral": bool(industry is not None),
        "size_neutral": bool(policy.size_neutral),
    }
    # Industry cap: redistribute excess to industries below the cap.
    if industry is not None:
        groups = industry.reindex(weights.index).fillna("UNKNOWN").astype(str)
        for _ in range(100):
            group_w = weights.groupby(groups).sum()
            over = group_w[group_w > policy.max_industry_weight + 1e-12]
            if over.empty:
                break
            for group, value in over.items():
                members = groups[groups == group].index
                scale = float(policy.max_industry_weight) / float(value)
                weights.loc[members] *= scale
            # Redistribute to underweight groups proportional to existing weight.
            under = weights.index[~weights.index.isin(groups[groups.isin(over.index)].index)]
            deficit = 1.0 - float(weights.sum())
            if deficit > 0 and len(under):
                weights.loc[under] += deficit * weights.loc[under] / weights.loc[under].sum() if weights.loc[under].sum() > 0 else deficit / len(under)
            weights = _cap_and_normalize(weights, policy.max_weight)
        diagnostics["industry_weights"] = weights.groupby(groups).sum().round(6).to_dict()

    # Optional size tilt: shrink names with larger float_mv.  No exposure model,
    # so this is an explicit deterministic tilt, not a fitted factor exposure.
    if policy.size_neutral and float_mv is not None and policy.size_tilt != 0.0:
        mv_frame = float_mv.loc[: pd.Timestamp(as_of)] if as_of is not None else float_mv
        mv = mv_frame.reindex(columns=weights.index).iloc[-1] if len(mv_frame) else None
        if mv is not None:
            ranks = mv.rank(pct=True).fillna(0.5)
            scale = np.exp(-float(policy.size_tilt) * (ranks - 0.5))
            weights = weights * pd.Series(scale, index=weights.index)
            weights = _cap_and_normalize(weights, policy.max_weight)
    diagnostics["gross_weight"] = float(weights.sum())
    diagnostics["max_weight"] = float(weights.max()) if len(weights) else 0.0
    # Turnover limit: blend toward previous weights until the one-way turnover
    # (0.5 * sum|w_new - w_old|) is within policy.turnover_limit.
    if previous_weights is not None and len(previous_weights):
        prev = previous_weights.reindex(weights.index).fillna(0.0)
        prev = prev / prev.sum() if prev.sum() > 0 else prev
        target = weights
        def turnover(candidate: pd.Series) -> float:
            all_names = candidate.index.union(prev.index)
            return 0.5 * float((candidate.reindex(all_names).fillna(0) - prev.reindex(all_names).fillna(0)).abs().sum())
        full_turnover = turnover(target)
        diagnostics["target_turnover"] = full_turnover
        if full_turnover > policy.turnover_limit and full_turnover > 0:
            lam = float(policy.turnover_limit) / full_turnover
            blended = prev + lam * (target - prev)
            blended = blended.clip(lower=0.0)
            if blended.sum() > 0:
                weights = blended / blended.sum()
            diagnostics["turnover_lambda"] = lam
        else:
            diagnostics["turnover_lambda"] = 1.0
    diagnostics["realized_turnover"] = (
        None
        if previous_weights is None
        else 0.5 * float((weights - previous_weights.reindex(weights.index).fillna(0) - 0.0).abs().sum() + max(0.0, previous_weights.sum() - weights.reindex(previous_weights.index).fillna(0).sum()))
    )
    return weights, diagnostics
