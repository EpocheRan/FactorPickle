"""RQAlpha Mod：把 FinBridge 的数据源与费用模型注入引擎。"""

from __future__ import annotations

from rqalpha.const import INSTRUMENT_TYPE
from rqalpha.interface import AbstractMod

from .runtime import get_runtime


class FinBridgeMod(AbstractMod):
    def start_up(self, env, mod_config):
        runtime = get_runtime()
        env.set_data_source(runtime.source)
        env.set_transaction_cost_decider(INSTRUMENT_TYPE.CS, runtime.cost)

    def tear_down(self, code, exception=None):
        return None


def load_mod():
    return FinBridgeMod()
