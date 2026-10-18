# tests/test_eval_constraints.py
import asyncio

import pytest

from backend.agents.eval.schemas import (
    ISSUES_MAX,
    SUMMARY_MAX_CHARS,
    DimensionScore,
    clamp_score,
)
from backend.agents.eval.graph import (
    ALL_DIMS,
    aggregate_node,
    is_successful_evaluation,
    truncate_summary,
)
from backend.core.exceptions import FileParseError, InvalidInputError
from backend.core.retry import is_non_retryable


def test_score_is_clamped_to_unit_interval():
    assert clamp_score(150) == 100
    assert clamp_score(-3) == 0
    assert DimensionScore(score=140, evidence=["a"]).score == 100
    assert DimensionScore(score=-1).score == 0


def test_summary_truncation_and_issue_cap():
    assert len(truncate_summary("甲" * 200)) == SUMMARY_MAX_CHARS
    assert ISSUES_MAX == 20


def test_non_retryable_parse_and_input_errors():
    assert is_non_retryable(FileParseError("scan"))
    assert is_non_retryable(InvalidInputError("bad"))
    assert not is_non_retryable(TimeoutError("later"))
    assert not is_non_retryable(ConnectionError("reset"))


def test_aggregate_enforces_summary_and_issue_limits(monkeypatch):
    class _Msg:
        content = "总" * 300

    class _LLM:
        async def ainvoke(self, messages):
            return _Msg()

    monkeypatch.setattr("backend.agents.eval.graph.get_llm", lambda *args, **kwargs: _LLM())
    monkeypatch.setattr("backend.core.retry.RETRY_DELAYS", [0, 0])

    scores = {}
    for dim in ALL_DIMS:
        scores[dim] = {
            "score": 88,
            "evidence": ["依据"],
            "risks": [f"风险{i}" for i in range(6)],
            "suggestions": ["建议"],
            "failed": False,
            "label": dim,
        }

    result = asyncio.run(aggregate_node({"scores": scores}))
    assert len(result["summary"]) <= SUMMARY_MAX_CHARS
    assert len(result["issues"]) <= ISSUES_MAX
    assert result["steps_complete"] is True
    assert is_successful_evaluation(result)


def test_success_predicate_rejects_incomplete_dimension():
    scores = {
        dim: {
            "score": 80,
            "evidence": [],
            "risks": [],
            "suggestions": [],
            "failed": False,
        }
        for dim in ALL_DIMS
    }
    scores[ALL_DIMS[0]]["failed"] = True
    scores[ALL_DIMS[0]]["error"] = "timeout"
    payload = {"scores": scores, "summary": "尚可", "issues": []}
    assert is_successful_evaluation(payload) is False
