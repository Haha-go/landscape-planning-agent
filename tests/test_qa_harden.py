# tests/test_qa_harden.py
# direct_no_hit 可观测、snippet≤200、编译图空检索拒答

import asyncio

from backend.agents.qa import graph as qa_graph
from backend.rag.retriever import clamp_snippet


def test_clamp_snippet_max_200():
    text = "字" * 500
    assert len(clamp_snippet(text)) == 200
    assert clamp_snippet("") == ""
    assert clamp_snippet("短") == "短"


def test_outbound_citation_truncates_snippet():
    cite = qa_graph._outbound_citation({"title": "t", "snippet": "a" * 300, "page": 1})
    assert len(cite["snippet"]) == 200


def test_generate_node_empty_retrieve_uses_no_hit(monkeypatch):
    async def fake_llm(*_a, **_k):
        class R:
            content = "通用建议，无具体国标编号。"

        return R()

    class FakeLLM:
        async def ainvoke(self, _msgs):
            return await fake_llm()

    monkeypatch.setattr(qa_graph, "get_llm", lambda *a, **k: FakeLLM())

    state = {
        "query": "火星绿化率国标？",
        "route": "direct_no_hit",
        "retrieved_docs": [],
    }
    out = asyncio.run(qa_graph.generate_node(state))
    assert out["route"] == "direct_no_hit"
    assert out["citations"] == []
    assert "通用建议" in out["answer"]


def test_retrieve_node_sets_direct_no_hit(monkeypatch):
    async def empty_retrieve(_q):
        return []

    monkeypatch.setattr(qa_graph, "retrieve", empty_retrieve)
    state = {"query": "库外偏题规范问法"}
    out = asyncio.run(qa_graph.retrieve_node(state))
    assert out["route"] == "direct_no_hit"
    assert out["citations"] == []


def test_eval_fixtures_meet_minimum_sizes():
    import csv
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evals" / "kb"

    def count(name: str) -> int:
        return sum(1 for line in (root / name).read_text(encoding="utf-8").splitlines() if line.strip())

    assert count("intent_golden.jsonl") >= 100
    assert count("retrieve_hit.jsonl") >= 50
    assert count("hallucination.jsonl") >= 30
    must_support = 0
    for line in (root / "hallucination.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if json.loads(line).get("must_support_in_citations"):
            must_support += 1
    assert must_support >= 50
    with (root / "pilot_template.csv").open(encoding="utf-8-sig", newline="") as f:
        pilot_n = sum(1 for _ in csv.DictReader(f))
    assert pilot_n >= 20
