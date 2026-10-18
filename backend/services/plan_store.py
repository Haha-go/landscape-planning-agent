# backend/services/plan_store.py
# 评估结果落库：系统分写入 scores；按 needs_human_review 进入人工队列或 system_only

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.eval.graph import is_successful_evaluation
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def parse_bool_form(value: str | bool | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


async def insert_processing_review(
    db: AsyncSession,
    *,
    review_id: str,
    path: str,
    file_name: str,
    submitter_id: str | None = None,
    needs_human_review: bool = True,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO plan_reviews (
                id, file_path, file_name, status, review_status,
                submitter_id, needs_human_review
            )
            VALUES (
                :id, :path, :name, 'processing', 'processing',
                :submitter, :needs_human
            )
            """
        ),
        {
            "id": review_id,
            "path": path,
            "name": file_name,
            "submitter": submitter_id,
            "needs_human": bool(needs_human_review),
        },
    )
    await db.commit()


async def save_evaluation(
    db: AsyncSession,
    review_id: str,
    result: dict[str, Any],
    *,
    needs_human_review: bool | None = None,
) -> dict[str, Any]:
    """系统分只写 scores / overall_score。

    齐套且需要人工 → review_status=in_review；
    齐套且不需要 → review_status=system_only；
    未齐套 → failed。
    """
    complete = bool(result.get("steps_complete")) and is_successful_evaluation(result)
    status = "done" if complete else "failed"
    if not complete:
        review_status = "failed"
    else:
        flag = needs_human_review
        if flag is None:
            row = await db.execute(
                text("SELECT needs_human_review FROM plan_reviews WHERE id = :id"),
                {"id": review_id},
            )
            fetched = row.fetchone()
            flag = True if fetched is None else bool(fetched[0])
        review_status = "in_review" if flag else "system_only"

    error = None
    if not complete:
        failed = [
            dim
            for dim, item in (result.get("scores") or {}).items()
            if isinstance(item, dict) and item.get("failed")
        ]
        error = "评估未齐套" + (f"：{', '.join(failed)}" if failed else "")
    await db.execute(
        text(
            """
            UPDATE plan_reviews SET
                extracted_data = CAST(:extracted AS jsonb),
                scores = CAST(:scores AS jsonb),
                overall_score = :overall,
                summary = :summary,
                issues = CAST(:issues AS jsonb),
                run_metrics = CAST(:metrics AS jsonb),
                status = :status,
                review_status = :review_status,
                error_msg = :error,
                updated_at = NOW()
            WHERE id = :id
            """
        ),
        {
            "id": review_id,
            "extracted": _dumps(result.get("extracted") or {}),
            "scores": _dumps(result.get("scores") or {}),
            "overall": result.get("overall_score"),
            "summary": result.get("summary"),
            "issues": json.dumps(result.get("issues") or [], ensure_ascii=False),
            "metrics": json.dumps(result.get("metrics") or [], ensure_ascii=False),
            "status": status,
            "review_status": review_status,
            "error": error,
        },
    )
    await db.commit()
    await _write_metrics(db, result.get("metrics") or [])
    return {
        "status": status,
        "review_status": review_status,
        "error": error,
        "needs_human_review": bool(needs_human_review)
        if needs_human_review is not None
        else (review_status == "in_review"),
    }


async def _write_metrics(db: AsyncSession, events: list[dict]) -> None:
    if not events:
        return
    try:
        for event in events:
            await db.execute(
                text(
                    """
                    INSERT INTO agent_metrics (
                        route, expected_route, route_hit, tool, success,
                        latency_ms, retried, recovered, step, tokens, detail
                    ) VALUES (
                        :route, :expected_route, :route_hit, :tool, :success,
                        :latency_ms, :retried, :recovered, :step, :tokens,
                        CAST(:detail AS jsonb)
                    )
                    """
                ),
                {
                    "route": event.get("route"),
                    "expected_route": event.get("expected_route"),
                    "route_hit": event.get("route_hit"),
                    "tool": event.get("tool"),
                    "success": event.get("success"),
                    "latency_ms": event.get("latency_ms"),
                    "retried": event.get("retried"),
                    "recovered": event.get("recovered"),
                    "step": event.get("step"),
                    "tokens": event.get("tokens"),
                    "detail": json.dumps(event.get("detail") or {}, ensure_ascii=False),
                },
            )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("metrics.persist_failed", error=str(exc))


async def save_failure(db: AsyncSession, review_id: str, error: str) -> None:
    await db.execute(
        text(
            """
            UPDATE plan_reviews
            SET status = 'failed', review_status = 'failed', error_msg = :err, updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": review_id, "err": error},
    )
    await db.commit()


async def list_my_plans(
    db: AsyncSession,
    *,
    submitter_id: str,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    params = {
        "sid": submitter_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    total_row = await db.execute(
        text("SELECT COUNT(*) FROM plan_reviews WHERE submitter_id = :sid"),
        {"sid": submitter_id},
    )
    total = int(total_row.scalar() or 0)
    rows = await db.execute(
        text(
            """
            SELECT id, file_name, overall_score, status, review_status,
                   needs_human_review, final_decision, summary, created_at, updated_at
            FROM plan_reviews
            WHERE submitter_id = :sid
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": [dict(r) for r in rows.mappings().fetchall()],
        "page": page,
        "page_size": page_size,
        "total": total,
    }
