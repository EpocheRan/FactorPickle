"""快速评分：面板只加载一次，逐因子算核心指标并复用评分卡（不生成 HTML）。

比 `factor_report` 快一个数量级：跳过分层 IC、中性化对比表、复现自检与稳健性池，
只保留评分卡必需的量（IC 统计、分组收益、换手、单调性、覆盖率、增量性）。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import metrics, rating as rating_mod
from .panel import flow_factor_panel, price_factor_panel
from .panel_adapter import load_panel


def score_factors(
    factor_ids: list[str],
    *,
    start: str = "2019-01-01",
    end: str | None = None,
    universe: str = "all_a",
    horizon: int = 20,
    groups: int = 10,
    neutralize: bool = False,
) -> pd.DataFrame:
    """一次加载面板，批量给因子打分，返回汇总表。"""
    t0 = time.time()
    panel = load_panel(None, None, start, end, universe=universe,
                       extras=("fin_valuation_daily",))
    load_seconds = time.time() - t0

    rows = []
    for factor_id in factor_ids:
        t1 = time.time()
        try:
            category = factor_id.split(".", 1)[0]
            frame = (flow_factor_panel(factor_id, panel) if category == "sentiment"
                     else price_factor_panel(factor_id, panel))
            start_ts = pd.Timestamp(start).normalize()
            frame = frame.loc[frame.index >= start_ts]

            report = metrics.build_report(
                frame, panel.close, panel.open,
                factor_id=factor_id, category=category, direction=_direction(factor_id),
                primary_horizon=horizon, groups=groups,
                market_cap=panel.market_cap, neutralize=neutralize, fast=True,
            )
            grade = rating_mod.rate(report)
            rows.append({
                "factor": factor_id,
                "ic": float(report.ic_stats.loc[horizon, "mean"]),
                "t": float(report.ic_stats.loc[horizon, "t_indep"]),
                "net_pct": float((report.spread_net or {}).get("mean", np.nan)) * 100,
                "turnover": float(report.turnover),
                "mono": float(report.monotonicity),
                "coverage": float(report.coverage),
                "grade": grade["grade"],
                "score": grade["score"],
                "time": grade["time_score"],
                "space": grade["space_score"],
                "realization": grade.get("realization_score"),
                "incremental": grade.get("incremental_score"),
                "tradable": grade.get("tradability_score"),
                "sec": round(time.time() - t1, 1),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"factor": factor_id, "grade": "ERR", "score": np.nan,
                         "ic": f"{type(exc).__name__}: {str(exc)[:60]}"})
    out = pd.DataFrame(rows)
    out.attrs["load_seconds"] = load_seconds
    return out


def _direction(factor_id: str) -> int:
    from factors.direction import direction_of

    try:
        return int(direction_of(factor_id))
    except Exception:  # noqa: BLE001
        return 1


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path
