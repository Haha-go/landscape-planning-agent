# tests/test_fault_and_retry.py
import asyncio

import pytest

from backend.agents.eval.graph import (
    ALL_DIMS,
    DIM_COMPLIANCE,
    is_successful_evaluation,
    run_plan_evaluation,
    score_dimension_node,
)
from backend.agents.eval.schemas import DimensionScore, PlanExtractSchema
from backend.core.exceptions import FileParseError, InvalidInputError
from backend.core.metrics import recent_metrics, reset_metrics
from backend.core.retry import retry_call
from tests.fixtures.pdf_factory import build_plan_fixtures

pytest.importorskip("fitz")


class _Summary:
    content = "这是一段不超过二十字的总评。"


class _LLM:
    async def ainvoke(self, messages):
        return _Summary()


def _structured(fail_compliance: bool, calls: dict):
    def factory(agent_type, schema):
        class Runner:
            async def ainvoke(self, messages):
                calls["n"] = calls.get("n", 0) + 1
                name = getattr(schema, "__name__", "")
                calls.setdefault(name, 0)
                calls[name] += 1
                if name == "PlanExtractSchema":
                    return PlanExtractSchema(
                        project_name="滨水公园",
                        green_water_indicators="绿地率 35%",
                        cited_standards=["GB 51192"],
                    )
                content = ""
                if messages:
                    content = getattr(messages[-1], "content", "") or ""
                if fail_compliance and "规范符合性" in content:
                    calls["compliance"] = calls.get("compliance", 0) + 1
                    raise TimeoutError("compliance down")
                return DimensionScore(
                    score=150,
                    evidence=["文本依据"],
                    risks=["风险点"],
                    suggestions=["补充指标"],
                )

        return Runner()

    return factory


def _install_mocks(monkeypatch, *, fail_compliance: bool = False, retrieve_error: bool = False):
    calls = {"parse": 0}
    monkeypatch.setattr("backend.core.retry.RETRY_DELAYS", [0, 0])
    monkeypatch.setattr("backend.agents.eval.graph.get_llm", lambda *args, **kwargs: _LLM())
    monkeypatch.setattr(
        "backend.agents.eval.graph.get_structured_llm",
        _structured(fail_compliance, calls),
    )

    async def _retrieve(*args, **kwargs):
        calls["retrieve"] = calls.get("retrieve", 0) + 1
        if retrieve_error:
            raise ConnectionError("milvus down")
        return [{"text": "绿地率不宜低于35%", "citation": {"title": "样例"}}]

    monkeypatch.setattr("backend.agents.eval.graph.retrieve", _retrieve)

    import backend.agents.eval.graph as graph_mod

    original = graph_mod.extract_pdf_text

    def _parse(path):
        calls["parse"] += 1
        return original(path)

    monkeypatch.setattr("backend.agents.eval.graph.extract_pdf_text", _parse)
    return calls


def test_one_dimension_failure_does_not_rerun_the_graph(monkeypatch, tmp_path):
    texts, _scans = build_plan_fixtures(tmp_path)
    calls = _install_mocks(monkeypatch, fail_compliance=True)
    result = asyncio.run(run_plan_evaluation(str(texts[0]), "plan.pdf"))
    assert calls["parse"] == 1
    assert calls["PlanExtractSchema"] == 1
    assert calls["compliance"] == 3
    failed = result["scores"][DIM_COMPLIANCE]
    assert failed["failed"] is True
    for dim in ALL_DIMS:
        if dim == DIM_COMPLIANCE:
            continue
        assert result["scores"][dim]["failed"] is False
        assert result["scores"][dim]["score"] == 100
    assert result["steps_complete"] is False
    assert is_successful_evaluation(result) is False
    assert len(result["summary"]) <= 120
    assert len(result["issues"]) <= 20


def test_text_pdfs_succeed_and_scans_fail_without_retry(monkeypatch, tmp_path):
    texts, scans = build_plan_fixtures(tmp_path)
    assert len(texts) >= 20
    assert len(scans) == 3
    calls = _install_mocks(monkeypatch, fail_compliance=False)
    ok = 0
    for path in texts:
        result = asyncio.run(run_plan_evaluation(str(path), path.name))
        if is_successful_evaluation(result):
            ok += 1
        assert calls["parse"] >= 1
    assert ok / len(texts) >= 0.85

    before = calls["parse"]
    for path in scans:
        with pytest.raises(FileParseError):
            asyncio.run(run_plan_evaluation(str(path), path.name))
    assert calls["parse"] - before == len(scans)


def test_compliance_retrieve_failure_still_scores(monkeypatch):
    monkeypatch.setattr("backend.core.retry.RETRY_DELAYS", [0, 0])
    calls = {}
    monkeypatch.setattr(
        "backend.agents.eval.graph.get_structured_llm",
        _structured(False, calls),
    )

    async def boom(*args, **kwargs):
        raise ConnectionError("milvus down")

    monkeypatch.setattr("backend.agents.eval.graph.retrieve", boom)
    for _ in range(20):
        out = asyncio.run(
            score_dimension_node(
                {
                    "dimension": DIM_COMPLIANCE,
                    "extracted": {"green_water_indicators": "绿地率 35%", "cited_standards": ["GB 51192"]},
                    "scores": {},
                }
            )
        )
        item = out["scores"][DIM_COMPLIANCE]
        assert item["failed"] is False
        assert 0 <= item["score"] <= 100
        assert "evidence" in item and "risks" in item and "suggestions" in item


def test_retry_recovers_and_scans_are_not_in_the_denominator(monkeypatch):
    monkeypatch.setattr("backend.core.retry.RETRY_DELAYS", [0, 0])
    reset_metrics()
    recovered = 0
    for _ in range(20):
        state = {"n": 0}

        async def flaky(state=state):
            state["n"] += 1
            if state["n"] == 1:
                raise ConnectionError("reset")
            return "ok"

        result = asyncio.run(retry_call(flaky, agent_type="eval", step="score:test"))
        assert result == "ok"
        recovered += 1
    assert recovered / 20 >= 0.85

    for _ in range(20):
        hits = {"n": 0}

        async def scan(hits=hits):
            hits["n"] += 1
            raise FileParseError("扫描件")

        with pytest.raises(FileParseError):
            asyncio.run(retry_call(scan, agent_type="eval", step="parse"))
        assert hits["n"] == 1

        async def bad_input():
            raise InvalidInputError("空")

        with pytest.raises(InvalidInputError):
            asyncio.run(retry_call(bad_input, agent_type="eval", step="input"))

    events = [e for e in recent_metrics() if e.get("step") == "score:test"]
    assert len(events) == 20
    assert all(e["recovered"] and e["success"] for e in events)
    parse_events = [e for e in recent_metrics() if e.get("step") == "parse" and e.get("tool") == "llm"]
    assert parse_events
    assert all(e["retried"] is False and e["success"] is False for e in parse_events)
