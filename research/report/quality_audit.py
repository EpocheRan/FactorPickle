"""跨因子数据质量/归因审计。

同一套 ``data_quality`` 检测器对任意因子生效；本模块只负责把报告对象
摊平成一行 CSV，便于批量找出“同类情况”，不绑定具体因子。
"""

from __future__ import annotations

import csv
from pathlib import Path


FIELDS = [
    "factor_id", "grade", "score", "triggered",
    "zero_share", "zero_no_data_share",
    "clean_g1", "clean_g5", "clean_spread_net", "clean_spread_net_t",
    "max_corr", "residual_ic", "retention", "attribution",
    "review_candidate", "reverse_net", "prior_sign_ratio",
]


def row_from_report(report, rating: dict) -> dict:
    dq = getattr(report, "data_quality", None) or {}
    zero = dq.get("zero", {})
    clean = dq.get("clean", {})
    inc = getattr(report, "incrementality", None) or {}
    attribution = (rating or {}).get("attribution", {}) or {}
    direction = (rating or {}).get("direction_review", {}) or {}
    return {
        "factor_id": getattr(report, "factor_id", ""),
        "grade": (rating or {}).get("grade", ""),
        "score": (rating or {}).get("score", ""),
        "triggered": bool(dq.get("triggered", False)),
        "zero_share": zero.get("share", ""),
        "zero_no_data_share": zero.get("no_data_share", ""),
        "clean_g1": clean.get("g1", ""),
        "clean_g5": clean.get("g5", ""),
        "clean_spread_net": clean.get("spread_net", ""),
        "clean_spread_net_t": clean.get("spread_net_t", ""),
        "max_corr": inc.get("max_corr", ""),
        "residual_ic": inc.get("ic_resid", ""),
        "retention": inc.get("effective_ratio", inc.get("ratio", "")),
        "attribution": attribution.get("label", ""),
        "review_candidate": bool((rating or {}).get("review_candidate", False)),
        "reverse_net": direction.get("reverse_net", ""),
        "prior_sign_ratio": (rating or {}).get("prior_sign_ratio", ""),
    }


def write_csv(path: str | Path, rows: list[dict]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return target


__all__ = ["FIELDS", "row_from_report", "write_csv"]
