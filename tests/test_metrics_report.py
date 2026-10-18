# tests/test_metrics_report.py
from backend.services.metrics_report import compute_metrics_summary


def test_metrics_summary_empty():
    empty = compute_metrics_summary([])
    assert empty["total"] == 0
    assert empty["success_rate"] is None
    assert empty["latency_ms"]["p95"] is None
    assert empty["tokens"]["sum"] == 0


def test_metrics_summary_aggregates():
    rows = [
        {
            "route": "retrieve",
            "tool": "qa",
            "step": "qa_e2e",
            "success": True,
            "latency_ms": 100,
            "tokens": 200,
            "detail": {"timings_ms": {"llm_ttft_ms": 40, "e2e_done_ms": 100}},
        },
        {
            "route": "retrieve",
            "tool": "qa",
            "step": "qa_e2e",
            "success": True,
            "latency_ms": 200,
            "tokens": 300,
            "detail": {"timings_ms": {"llm_ttft_ms": 80, "e2e_done_ms": 200}},
        },
        {
            "route": "direct",
            "tool": "pdf",
            "step": "parse",
            "success": False,
            "latency_ms": 50,
            "tokens": None,
            "detail": {},
        },
    ]
    summary = compute_metrics_summary(rows)
    assert summary["total"] == 3
    assert summary["success_rate"] == round(2 / 3, 4)
    assert summary["latency_ms"]["avg"] == round((100 + 200 + 50) / 3, 2)
    assert summary["latency_ms"]["p95"] == 200
    assert summary["tokens"]["sum"] == 500
    assert summary["tokens"]["avg"] == 250
    assert summary["qa_timings_ms"]["llm_ttft_p95"] == 80
    routes = {item["key"]: item for item in summary["by_route"]}
    assert routes["retrieve"]["count"] == 2
    assert routes["retrieve"]["success_rate"] == 1.0
    assert routes["direct"]["success_rate"] == 0.0
