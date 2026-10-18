# backend/services/metrics_report.py
# agent_metrics 在线汇总（命中/成功率/P95/Token）

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.metrics import percentile


def _group_key(row: dict, field: str) -> str:
    value = row.get(field)
    if value is None or value == "":
        return "(none)"
    return str(value)


def compute_metrics_summary(rows: list[dict]) -> dict[str, Any]:
    """纯函数聚合，便于单测。"""
    by_route: dict[str, list[dict]] = defaultdict(list)
    by_tool: dict[str, list[dict]] = defaultdict(list)
    by_step: dict[str, list[dict]] = defaultdict(list)
    latencies: list[float] = []
    tokens: list[int] = []
    ttft: list[float] = []
    e2e: list[float] = []
    success_n = 0

    for row in rows:
        by_route[_group_key(row, "route")].append(row)
        by_tool[_group_key(row, "tool")].append(row)
        by_step[_group_key(row, "step")].append(row)
        if row.get("success"):
            success_n += 1
        if row.get("latency_ms") is not None:
            try:
                latencies.append(float(row["latency_ms"]))
            except (TypeError, ValueError):
                pass
        if row.get("tokens") is not None:
            try:
                tokens.append(int(row["tokens"]))
            except (TypeError, ValueError):
                pass
        detail = row.get("detail") or {}
        if isinstance(detail, str):
            continue
        timings = detail.get("timings_ms") or {}
        if timings.get("llm_ttft_ms") is not None:
            try:
                ttft.append(float(timings["llm_ttft_ms"]))
            except (TypeError, ValueError):
                pass
        if timings.get("e2e_done_ms") is not None:
            try:
                e2e.append(float(timings["e2e_done_ms"]))
            except (TypeError, ValueError):
                pass

    def bucket(items: dict[str, list[dict]]) -> list[dict[str, Any]]:
        out = []
        for key, group in sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            ok = sum(1 for r in group if r.get("success"))
            out.append(
                {
                    "key": key,
                    "count": len(group),
                    "success_rate": round(ok / len(group), 4) if group else None,
                }
            )
        return out

    n = len(rows)
    return {
        "total": n,
        "success_rate": round(success_n / n, 4) if n else None,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p95": round(percentile(latencies, 95), 2) if latencies else None,
            "sample_count": len(latencies),
        },
        "tokens": {
            "sum": sum(tokens) if tokens else 0,
            "avg": round(sum(tokens) / len(tokens), 2) if tokens else None,
            "sample_count": len(tokens),
        },
        "qa_timings_ms": {
            "llm_ttft_p95": round(percentile(ttft, 95), 2) if ttft else None,
            "e2e_done_p95": round(percentile(e2e, 95), 2) if e2e else None,
            "ttft_sample_count": len(ttft),
            "e2e_sample_count": len(e2e),
        },
        "by_route": bucket(by_route),
        "by_tool": bucket(by_tool),
        "by_step": bucket(by_step),
    }


async def metrics_summary(
    db: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    filters = ["1=1"]
    params: dict[str, Any] = {}
    if date_from is not None:
        filters.append("created_at >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        filters.append("created_at < :date_to")
        params["date_to"] = date_to
    where = " AND ".join(filters)
    rows = await db.execute(
        text(
            f"""
            SELECT route, tool, step, success, latency_ms, tokens, detail
            FROM agent_metrics
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT 20000
            """
        ),
        params,
    )
    payload = compute_metrics_summary([dict(r) for r in rows.mappings().fetchall()])
    payload["date_from"] = date_from.isoformat() if date_from else None
    payload["date_to"] = date_to.isoformat() if date_to else None
    return payload
