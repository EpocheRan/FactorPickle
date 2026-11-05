"""单因子横截面骨架：换 ``--param factor=...`` 就能扫任意已注册因子。

存在的理由：策略层面最贵的不是写策略，是**每个因子都要重写一遍策略**。
这个骨架把「战场（universe）+ 因子 + 方向 + 持仓数」全部参数化，
用一条命令就能把一个因子放到任意池子上跑出净值，作为因子取舍的最终裁判。

用法::

    python -m backtest run strategies/models/factor_rank.py --start 2020-01-01 --end 2026-10-29 \
        --param index=000905 --param factor=reversal.ret_21d --param direction=-1

    # 不带 index 则用固定 200 池
    python -m backtest run strategies/models/factor_rank.py ... --param factor=size.float_mv

方向必须显式给（族先验，不按样本内 IC 翻转）；方向错的结果不能拿来当证据。
"""

from __future__ import annotations

import pandas as pd

from factors import F, FactorDef
from strategies.base import CrossSectionStrategy
from strategies.pools.pool_2017_200 import POOL_2017_11_01_200
from strategies.universe import Rebalance, Universe


class FactorRank(CrossSectionStrategy):
    name = "factor_rank"
    universe = Universe.explicit(POOL_2017_11_01_200)
    top_n = 30
    buffer = 10
    rebalance = Rebalance.MONTHLY
    params = {
        "factor": "liquidity.turnover_std_bias_42_252",
        "direction": -1,
        "index": "",
        "top_n": 30,
        "buffer": 10,
        "rebalance": "",          # daily / weekly / monthly，空则用类默认
        "skip_edge_guard": "",    # 1 = 跳过「净价差覆盖成本」预检
    }

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])
        _reb = str(self.params.get("rebalance") or "").strip().lower()
        if _reb:
            self.rebalance = Rebalance(_reb)
        index = str(self.params.get("index") or "").strip()
        if index:
            self.universe = Universe.index(index.zfill(6) if index.isdigit() else index)
        # 快速失败：因子 id 写错时在构造期报错，而不是回测跑完给一条假净值
        F.ts(str(self.params["factor"]))
        self._edge_guard()
        from strategies.weighting import EqualWeight

        self.weighting = EqualWeight()

    def _edge_guard(self) -> None:
        """报告口径的预检：净价差为负或覆盖不了单期成本，直接拒绝回测。"""
        import json
        from pathlib import Path

        if str(self.params.get("skip_edge_guard") or "").strip() in ("1", "true", "True"):
            return
        fid = str(self.params["factor"])
        root = Path(__file__).resolve().parents[2]
        for cand in (root / "output" / "factor_reports" / f"{fid.replace('.', '_')}.json",
                     root / "output" / "factor_reports_29" / f"{fid.replace('.', '_')}.json"):
            if not cand.exists():
                continue
            try:
                payload = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rating = payload.get("rating") or {}
            gate = rating.get("edge_gate") or {}
            if gate.get("under_water") or gate.get("cannot_pay"):
                raise SystemExit(
                    f"[edge_guard] {fid} 报告显示净价差 {gate.get('net_mean')}、"
                    f"单期成本 {gate.get('cost_mean')}：扣成本后不赚钱，拒绝回测。"
                    " 如确需复现，传 --param skip_edge_guard=1"
                )
            return

    def factors(self) -> list[FactorDef]:
        return [F.ts(str(self.params["factor"])), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        raw = panel.at(dt, str(self.params["factor"]))
        score = raw * int(self.params["direction"])
        return score.dropna()


STRATEGY = FactorRank
