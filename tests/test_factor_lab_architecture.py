from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from factor_lab.core import agents, budget
from factor_lab.research.campaign import CampaignPolicy, CampaignRunner, run_ml_plan_campaign
from factor_lab.runtime.data_interface_adapter import DataGap, DataInterfaceGateway
from factor_lab.runtime.factor_library import FactorLibrary, FactorLibraryError
from factor_lab.runtime.factor_builder import build as build_factor
from factor_lab.research.history import HistoryEntry, append as append_history, recent, search
from factor_lab.research.ml_engine import MLExperimentSpec, run_experiment, signal_to_factor_decision
from factor_lab.runtime.panel_builder import build_factor_panel
from factor_lab.runtime.readiness import ReadinessBlocked, require_data
from factor_lab.core.schemas import Hypothesis, OutcomeSpec
from factor_lab.research.web_gateway import build_context, parse_gateway_output, seed_queries
from research.report.panel import Panel


class FakeMarket:
    def ohlc(self, codes, start, end, **kwargs):
        dates = pd.bdate_range("2024-01-01", periods=3)
        return {
            str(code): pd.DataFrame(
                {
                    "open": [1.0, 1.1, 1.2],
                    "high": [1.2, 1.3, 1.4],
                    "low": [0.9, 1.0, 1.1],
                    "close": [1.1, 1.2, 1.3],
                    "volume": [10, 11, 12],
                    "amount": [100, 110, 120],
                },
                index=dates,
            )
            for code in ([codes] if isinstance(codes, str) else codes)
        }


class FakeService:
    def get_statements(self, *args, **kwargs):
        return {"ok": True}


class FakeGateway(DataInterfaceGateway):
    """Gateway with no reference DB queries for unit tests."""

    def valuation_bundle(self, *args, **kwargs):
        return None

    def margin_bundle(self, *args, **kwargs):
        return None

    def industry_map(self):
        return None

    def regime_series(self, dates):
        return None


class FakeCalendar:
    def trading_days(self, start, end):
        return ["2024-01-02"]


def test_data_gateway_is_the_data_door():
    gateway = DataInterfaceGateway(
        market_loader_factory=FakeMarket,
        financial_factory=FakeService,
        moneyflow_factory=FakeService,
        disclosure_factory=FakeService,
        calendar_factory=FakeCalendar,
    )
    status = gateway.status()
    assert status["available"] is True
    assert "market" in status["services"]
    frames, manifest = gateway.ohlc(["600000"], "2024-01-01", "2024-01-05")
    assert "600000" in frames
    assert manifest.source == "data_interface"
    assert manifest.rows == 3
    assert manifest.data_hash
    assert gateway.trading_days("2024-01-01", "2024-01-05") == ["2024-01-02"]


def test_data_gateway_turns_empty_into_data_gap():
    class EmptyMarket:
        def ohlc(self, *args, **kwargs):
            return {"600000": pd.DataFrame()}

    gateway = DataInterfaceGateway(market_loader_factory=EmptyMarket)
    with pytest.raises(DataGap):
        gateway.ohlc("600000", "2024-01-01", "2024-01-05")


def test_factor_library_immutable_versions(tmp_path: Path):
    lib = FactorLibrary(tmp_path / "generated")
    code = "import pandas as pd\n\ndef factor(frame):\n    return frame['close'] / frame['close'].shift(1) - 1\n"
    spec = {"window": 1, "unit": "ratio", "data_contract": {"fields": ["close"]}}
    version = lib.write_version(family="test", factor_id="test.simple", version="v1", code=code, spec=spec)
    assert version.code_hash
    assert version.category == "test"
    assert version.artifact()["factor_id"] == "test.simple"
    assert version.artifact()["category"] == "test"
    loaded = lib.load("test.simple", "v1")
    assert loaded.fn is not None
    assert loaded.window == 1
    with pytest.raises(FactorLibraryError):
        lib.write_version(family="test", factor_id="test.simple", version="v1", code=code + "\n# changed\n", spec=spec)
    assert len(lib.list_versions("test.simple")) == 1


def test_budget_daily_and_cycle_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(budget, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(budget, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(budget, "PRICING_PATH", tmp_path / "pricing.json")
    policy = budget.ensure_policy()
    assert policy["monthly_rmb"] == 3000
    assert budget.load_policy().daily_spend_limit == 80.0
    flash = budget.load_policy().pricing["deepseek-flash"]
    assert flash["input_rmb_per_mtok"] == 0.04
    assert flash["output_rmb_per_mtok"] == 8.0
    ok = budget.check_cycle()
    assert ok["ok"] is True
    priced = budget.record_usage(agent="test", tokens_in=1_000_000, tokens_out=1_000_000)
    assert priced["cost_rmb"] == pytest.approx(8.04)
    budget.record_usage(agent="test", cost_rmb=80.0, cycle=True)
    assert budget.check_cycle()["ok"] is False


def _ml_dataset(periods: int = 90, assets: int = 18) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=periods)
    rows = []
    for date in dates:
        x = rng.normal(size=assets)
        y = 0.08 * x + rng.normal(scale=0.05, size=assets)
        for idx in range(assets):
            rows.append({"date": date, "asset": f"S{idx:02d}", "x": x[idx], "z": rng.normal(), "y": y[idx]})
    return pd.DataFrame(rows)


def test_ml_engine_loads_data_only_through_gateway():
    from factor_lab.research.ml_engine import load_dataset

    gateway = DataInterfaceGateway(market_loader_factory=FakeMarket)
    frame, manifest = load_dataset(gateway, ["600000", "000001"], "2024-01-02", "2024-01-05", fields=["close", "amount"])
    assert {"date", "asset", "close", "amount"}.issubset(frame.columns)
    assert len(frame) == 6
    assert manifest["source"] == "data_interface"


def test_ml_engine_walk_forward_and_non_dl():
    spec = MLExperimentSpec(
        name="synthetic",
        features=["x", "z"],
        model="ridge",
        n_splits=3,
        embargo=2,
        min_train_dates=40,
    )
    result = run_experiment(_ml_dataset(), spec)
    assert result.metrics["n_folds"] == 3
    assert result.metrics["ic_mean"] > 0
    assert result.metrics["net_spread"] > 0
    assert result.feature_importance
    decision = signal_to_factor_decision(result)
    assert decision["candidate"] is False
    assert "turnover>0.8" in decision["reasons"]
    with pytest.raises(Exception):
        MLExperimentSpec(name="bad", features=["x"], model="mlp")


def test_campaign_hard_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("factor_lab.research.campaign.check_campaign", lambda *a, **k: {"ok": True, "reasons": []})
    monkeypatch.setattr("factor_lab.research.campaign.record_usage", lambda **kwargs: {})
    calls = {"n": 0}

    def planner(state):
        calls["n"] += 1
        return {"action": "ml", "id": calls["n"]}

    def executor(plan, state):
        return {"score": float(plan["id"]), "cost_rmb": 0.0}

    runner = CampaignRunner(
        planner=planner,
        executor=executor,
        root=tmp_path / "campaigns",
        policy=CampaignPolicy(max_rounds=3),
    )
    result = runner.run(objective="test")
    assert result["rounds"] == 3
    assert result["stop_reason"] == "max_rounds"
    assert (tmp_path / "campaigns" / result["campaign_id"] / "campaign_trace.jsonl").exists()
    assert (tmp_path / "campaigns" / result["campaign_id"] / "stop_decision.json").exists()


def test_agent_reclaim_is_safety_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agents, "REGISTRY_PATH", tmp_path / "agents.json")
    session = tmp_path / "child"
    session.mkdir()
    (session / "session.v4.jsonl").write_text("{}", encoding="utf-8")
    agents.register("child-1", "Runner", session_dir=str(session))
    held = agents.reclaim("child-1", apply=True, archive_root=tmp_path / "agents")
    assert held["safe"] is False
    assert session.exists()
    agents.update("child-1", state="idle", artifacts_committed=True)
    moved = agents.reclaim("child-1", apply=True, archive_root=tmp_path / "agents")
    assert moved["safe"] is True
    assert not session.exists()
    assert (tmp_path / "agents" / "child-1").exists()


def test_history_is_append_only_and_searchable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import factor_lab.research.history as history_mod

    path = tmp_path / "history.jsonl"
    index = tmp_path / "index.json"
    monkeypatch.setattr(history_mod, "HISTORY_PATH", path)
    monkeypatch.setattr(history_mod, "HISTORY_INDEX_PATH", index)
    append_history(
        HistoryEntry(
            cycle_id="C-1",
            summary="factor failed because transmission was negative",
            tags=["transmission", "ml"],
            failure_modes=["negative_net_spread"],
        ),
        path=path,
    )
    assert recent(path=path)
    assert search("transmission", path=path)
    assert index.exists()


def test_ml_plan_campaign_runs_and_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("factor_lab.research.campaign.check_campaign", lambda *a, **k: {"ok": True, "reasons": []})
    monkeypatch.setattr("factor_lab.research.campaign.record_usage", lambda **kwargs: {})
    plans = [
        {
            "action": "ml",
            "spec": {
                "name": "synthetic",
                "features": ["x", "z"],
                "model": "ridge",
                "n_splits": 2,
                "embargo": 2,
                "min_train_dates": 35,
            },
        }
    ]
    result = run_ml_plan_campaign(
        _ml_dataset(),
        plans,
        root=tmp_path / "campaigns",
        policy=CampaignPolicy(max_rounds=1),
    )
    assert result["rounds"] == 1
    assert result["stop_reason"] == "max_rounds"
    assert (tmp_path / "campaigns" / result["campaign_id"] / "campaign_summary.json").exists()


def test_workflows_do_not_forbid_inherited_tools():
    workflows = Path(__file__).resolve().parents[1] / "factor_lab" / "workflows"
    for path in workflows.glob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "不要使用任何工具" not in text, path.name
        assert "Do not use tools" not in text, path.name


def test_panel_builder_routes_factor_categories():
    class FlowService:
        def get_by_code(self, code, start, end):
            dates = pd.bdate_range("2024-01-01", periods=3)
            return pd.DataFrame({"ts": dates, "code": code, "net_mf_amount": [1.0, 2.0, 3.0]})

    gateway = FakeGateway(
        market_loader_factory=FakeMarket,
        moneyflow_factory=FlowService,
    )
    price = build_factor_panel(
        gateway,
        {"factor_id": "momentum.test", "category": "price"},
        ["600000", "000001"],
        "2024-01-02",
        "2024-01-05",
        warmup_days=10,
    )
    assert price.category == "price"
    assert price.panel.extras is None
    sentiment = build_factor_panel(
        gateway,
        {"factor_id": "sentiment.test", "category": "sentiment"},
        ["600000"],
        "2024-01-02",
        "2024-01-05",
        warmup_days=10,
    )
    assert sentiment.category == "sentiment"
    assert "mf_net_amount" in (sentiment.panel.extras or {})
    quality = build_factor_panel(
        gateway,
        {"factor_id": "quality.test", "category": "quality"},
        ["600000"],
        "2024-01-02",
        "2024-01-05",
        warmup_days=10,
    )
    assert quality.category == "quality"


def test_panel_builder_price_matches_raw_data_interface_frames():
    gateway = FakeGateway(market_loader_factory=FakeMarket)
    built = build_factor_panel(
        gateway,
        {"factor_id": "price.test", "category": "price"},
        ["600000", "000001"],
        "2024-01-02",
        "2024-01-05",
        warmup_days=10,
    )
    raw = FakeMarket().ohlc(["600000", "000001"], "2023-12-23", "2024-01-05")
    for code in ("600000", "000001"):
        for field in ("close", "open", "high", "amount"):
            expected = raw[code][field]
            actual = getattr(built.panel, field).loc[expected.index, code]
            assert list(actual.index) == list(expected.index)
            np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy())


def test_panel_builder_from_data_interface():
    gateway = FakeGateway(market_loader_factory=FakeMarket)
    built = build_factor_panel(
        gateway,
        {"factor_id": "price.test", "category": "price"},
        ["600000", "000001"],
        "2024-01-02",
        "2024-01-05",
        warmup_days=10,
    )
    assert built.panel.close.shape[1] == 2
    assert list(built.panel.symbols) == ["600000", "000001"]
    assert built.panel.eval_start is not None
    assert built.manifest.source == "DataInterfaceGateway"
    assert built.manifest.data_hash


def test_web_gateway_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from factor_lab.research.discovery import DiscoveryBrief
    import factor_lab.research.web_gateway as gateway

    brief = DiscoveryBrief(
        research_question="overnight gap reversal",
        constraints=["A-share"],
        known_dead_ends=["turnover trend"],
        max_queries=4,
    )
    queries = seed_queries(brief)
    assert len(queries) == 4
    assert all("overnight gap reversal" in query for query in queries)
    context = build_context(brief, [{"title": "paper", "url": "https://example.com/a", "snippet": "x"}])
    assert "https://example.com/a" in context
    monkeypatch.setattr(gateway, "DIGEST_PATH", tmp_path / "web_digest.json")
    monkeypatch.setattr(gateway, "write_map", lambda *args, **kwargs: tmp_path / "map.json")
    written = gateway.parse_gateway_output(
        {
            "summary": "one lead",
            "gaps": ["no intraday data"],
            "leads": [
                {
                    "title": "gap reversal",
                    "mechanism": "overnight liquidity shock",
                    "confidence": 0.6,
                    "tags": ["reversal"],
                    "sources": [{"title": "paper", "url": "https://example.com/a"}],
                }
            ],
        },
        brief,
    )
    assert written["leads"][0]["mechanism"] == "overnight liquidity shock"
    assert (tmp_path / "web_digest.json").exists()


def test_factor_builder_writes_direct_code_into_lab_library(tmp_path: Path):
    lib = FactorLibrary(tmp_path / "generated")
    factor = build_factor(
        family="demo",
        factor_id="demo.momentum",
        version="v1",
        code="import pandas as pd\n\ndef factor(frame):\n    return frame['close'].pct_change()\n",
        spec={"window": 1, "unit": "ratio", "data_contract": {"fields": ["close"]}},
        library=lib,
    )
    assert factor.version == "v1"
    assert factor.code_hash
    assert lib.load("demo.momentum", "v1").fn is not None


def test_readiness_writes_blocker_when_data_interface_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import factor_lab.runtime.readiness as readiness

    monkeypatch.setattr(readiness, "BLOCKER_PATH", tmp_path / "blocker.json")
    with pytest.raises(ReadinessBlocked):
        require_data({"available": False, "services": [], "error": "no config"})
    payload = json.loads((tmp_path / "blocker.json").read_text(encoding="utf-8"))
    assert payload["awaiting_user"] is True
    assert payload["blocked_by"] == "data_interface"


def test_runner_default_data_bundle_uses_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from factor_lab.runtime import runner as runner_mod

    class Gateway(FakeGateway):
        def status(self):
            return {"available": True, "services": ["market"]}

    experiment_dir = tmp_path / "E-2"
    experiment_dir.mkdir()

    def fake_run_experiment(hypothesis, **kwargs):
        assert kwargs["panel"] is not None
        return {"exp_id": "E-2", "run_id": "R-2", "experiment_dir": str(experiment_dir)}

    monkeypatch.setattr("factor_lab.runtime.run.run_experiment", fake_run_experiment)
    hypothesis = Hypothesis(
        hypothesis_id="H-2",
        factor_id="momentum.ret_21d",
        title="test",
        mechanism="test",
        expected_sign=1,
        outcomes=[OutcomeSpec(universe="all_a")],
    )
    result = runner_mod.FactorRunner(gateway=Gateway(market_loader_factory=FakeMarket)).run(
        hypothesis,
        codes=["600000", "000001"],
        use_data_bundle=True,
    )
    manifest = json.loads((experiment_dir / "data_manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_path"] == "runner_panel"
    assert manifest["factor_id"] == "momentum.ret_21d"
    assert result["data_manifest"].endswith("data_manifest.json")


def test_runner_writes_data_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from factor_lab.runtime import runner

    class Gateway:
        def status(self):
            return {"available": True, "services": ["market", "financial"]}

    experiment_dir = tmp_path / "E-1"
    experiment_dir.mkdir()

    def fake_run_experiment(hypothesis, **kwargs):
        return {"exp_id": "E-1", "run_id": "R-1", "experiment_dir": str(experiment_dir)}

    monkeypatch.setattr("factor_lab.runtime.run.run_experiment", fake_run_experiment)
    hypothesis = Hypothesis(
        hypothesis_id="H-1",
        factor_id="test.factor",
        title="test",
        mechanism="test",
        expected_sign=1,
        outcomes=[OutcomeSpec(universe="all_a")],
    )
    result = runner.FactorRunner(gateway=Gateway()).run(
        hypothesis,
        factor_artifact={"factor_id": "test.factor", "version": "v1", "code_hash": "abc"},
        use_data_bundle=False,
    )
    manifest = json.loads((experiment_dir / "data_manifest.json").read_text(encoding="utf-8"))
    assert result["data_manifest"].endswith("data_manifest.json")
    assert manifest["source"] == "data_interface"
    assert manifest["factor_id"] == "test.factor"
    assert manifest["factor_artifact"]["version"] == "v1"
    with pytest.raises(Exception):
        runner.FactorRunner(gateway=Gateway()).run(
            hypothesis,
            factor_artifact={"factor_id": "other.factor", "version": "v1"},
            use_data_bundle=False,
        )
