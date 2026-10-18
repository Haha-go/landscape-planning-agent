# tests/test_route_and_tools.py
import asyncio

import pytest

from backend.agents.orchestrator.graph import AGENT_LABELS, route_intent
from backend.core.metrics import percentile, recent_metrics, reset_metrics
from backend.parsers.pdf import extract_pdf_text
from tests.fixtures.pdf_factory import write_text_pdf
from tests.fixtures.route_golden import agent_mode_cases, golden_cases

pytest.importorskip("fitz")


def test_route_golden_accuracy_and_latency():
    cases = golden_cases()
    assert len(cases) >= 100
    reset_metrics()
    hits = 0
    for case in cases:
        decision = route_intent(
            case["text"],
            has_file=case["has_file"],
            expected=case["expected"],
            agent_mode=case.get("agent_mode", "auto"),
        )
        if decision.intent == case["expected"]:
            hits += 1
    accuracy = hits / len(cases)
    assert accuracy >= 0.95, accuracy
    samples = [e["latency_ms"] for e in recent_metrics() if e.get("step") == "route"]
    assert len(samples) >= 100
    assert percentile(samples, 95) < 5000


def test_agent_mode_routing():
    cases = agent_mode_cases()
    assert len(cases) >= 15
    for case in cases:
        decision = route_intent(
            case["text"],
            has_file=case["has_file"],
            agent_mode=case["agent_mode"],
            expected=case["expected"],
        )
        assert decision.intent == case["expected"], case
        assert decision.mode == case["agent_mode"]
        pub = decision.to_public()
        assert pub["mode"] == case["agent_mode"]
        if decision.agent:
            assert pub["agent_label"] == AGENT_LABELS[decision.agent]


def test_parse_and_mocked_retrieve_p95(monkeypatch, tmp_path):
    path = tmp_path / "warm.pdf"
    write_text_pdf(path, "滨水公园规划方案。" * 40)
    extract_pdf_text(path)
    reset_metrics()
    for _ in range(50):
        payload = extract_pdf_text(path)
        assert payload["char_count"] >= 50
        assert isinstance(payload["full_text"], str)
    parse_ms = [e["latency_ms"] for e in recent_metrics() if e.get("tool") == "parse" and e["success"]]
    assert len(parse_ms) >= 50
    assert percentile(parse_ms, 95) < 800

    class _Embedder:
        async def encode_query(self, query):
            return {"dense": [0.0], "sparse": {0: 0.2}}

    class _Reranker:
        async def rerank(self, query, passages, top_n=5):
            return [(0, 0.91)]

    def _hybrid(dense, sparse, top_k=20):
        return [
            {
                "text": "绿地率不宜低于百分之三十五",
                "title": "样例",
                "section": "3.1",
                "page": 2,
                "standard_code": "SAMPLE",
                "doc_id": "doc-1",
            }
        ]

    monkeypatch.setattr("backend.rag.retriever.get_embedder", lambda: _Embedder())
    monkeypatch.setattr("backend.rag.retriever.get_reranker", lambda: _Reranker())
    monkeypatch.setattr("backend.rag.retriever.hybrid_search", _hybrid)

    from backend.rag.retriever import retrieve

    reset_metrics()

    async def _run():
        for _ in range(50):
            docs = await retrieve("绿地率规范", top_k=5, top_n=3)
            assert isinstance(docs, list) and docs
            assert "text" in docs[0]

    asyncio.run(_run())
    retrieve_ms = [e["latency_ms"] for e in recent_metrics() if e.get("tool") == "retrieve" and e["success"]]
    rerank_ms = [e["latency_ms"] for e in recent_metrics() if e.get("tool") == "rerank" and e["success"]]
    assert len(retrieve_ms) >= 50 and len(rerank_ms) >= 50
    assert percentile(retrieve_ms, 95) < 800
    assert percentile(rerank_ms, 95) < 800
    assert all(e["success"] for e in recent_metrics() if e.get("tool") in {"retrieve", "rerank"})
