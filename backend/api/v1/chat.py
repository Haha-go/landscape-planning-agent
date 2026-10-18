# backend/api/v1/chat.py
# POST /api/v1/chat/stream — SSE

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from backend.agents.orchestrator.graph import guide_for, route_intent
from backend.agents.qa.graph import stream_qa_answer
from backend.core.metrics import record_metric
from backend.dependencies import AsyncSessionLocal
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户问题")
    thread_id: str | None = Field(None, description="会话线程 ID，不传则新建")
    agent_mode: str = Field(
        "auto",
        description="auto | kb（规范智库）；chat 无文件，plan 仅返回上传引导",
    )


async def _ensure_session(thread_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO qa_sessions (thread_id)
                VALUES (:tid)
                ON CONFLICT (thread_id) DO NOTHING
                """
            ),
            {"tid": thread_id},
        )
        await db.commit()


async def _save_message(
    thread_id: str,
    role: str,
    content: str,
    route: str | None = None,
    citations: list | None = None,
    metrics: dict | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM qa_sessions WHERE thread_id = :tid LIMIT 1"),
            {"tid": thread_id},
        )
        session = row.fetchone()
        session_id = session[0] if session else None
        await db.execute(
            text(
                """
                INSERT INTO qa_messages (
                    session_id, thread_id, role, content, route, citations, metrics
                )
                VALUES (
                    :sid, :tid, :role, :content, :route,
                    CAST(:citations AS jsonb), CAST(:metrics AS jsonb)
                )
                """
            ),
            {
                "sid": session_id,
                "tid": thread_id,
                "role": role,
                "content": content,
                "route": route,
                "citations": json.dumps(citations or [], ensure_ascii=False),
                "metrics": json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
            },
        )
        await db.commit()


async def _persist_qa_metric(route: str | None, metrics: dict | None) -> None:
    """将问答埋点写入 agent_metrics，便于汇总命中率 / P95 / Token。"""
    if not metrics:
        return
    timings = metrics.get("timings_ms") or {}
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(
                text(
                    """
                    INSERT INTO agent_metrics (
                        route, tool, success, latency_ms, step, tokens, detail
                    ) VALUES (
                        :route, 'qa', TRUE, :latency_ms, 'qa_e2e', :tokens,
                        CAST(:detail AS jsonb)
                    )
                    """
                ),
                {
                    "route": route,
                    "latency_ms": timings.get("e2e_done_ms"),
                    "tokens": metrics.get("total_tokens"),
                    "detail": json.dumps(metrics, ensure_ascii=False),
                },
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning("chat.metric_persist_failed", error=str(e))


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """兼容入口：无文件。agent_mode 仅 auto|kb 有意义；plan 返回上传引导。"""
    thread_id = req.thread_id or str(uuid.uuid4())
    mode = (req.agent_mode or "auto").strip().lower()
    if mode not in ("auto", "kb", "plan"):
        mode = "auto"
    decision = route_intent(req.message, has_file=False, agent_mode=mode)

    async def event_gen():
        yield {
            "event": "orchestrator",
            "data": json.dumps(
                {**decision.to_public(), "thread_id": thread_id},
                ensure_ascii=False,
            ),
        }
        try:
            await _ensure_session(thread_id)
            await _save_message(thread_id, "user", req.message)
        except Exception as e:
            logger.warning("chat.session_save_failed", error=str(e))

        if decision.intent != "qa":
            guide = guide_for(decision)
            record_metric(
                route=decision.intent,
                tool="llm",
                step="qa_first_token",
                success=True,
                latency_ms=0,
                detail={"guide": True, "agent_mode": decision.mode},
            )
            yield {
                "event": "token",
                "data": json.dumps({"token": guide}, ensure_ascii=False),
            }
            yield {
                "event": "done",
                "data": json.dumps(
                    {
                        "answer": guide,
                        "route": "other",
                        "citations": [],
                        "thread_id": thread_id,
                        "orchestrator": decision.reason,
                        **decision.to_public(),
                    },
                    ensure_ascii=False,
                ),
            }
            try:
                await _save_message(thread_id, "assistant", guide, route="other")
            except Exception:
                pass
            return

        answer = ""
        route = "direct"
        citations: list = []
        qa_metrics: dict | None = None
        try:
            async for ev in stream_qa_answer(req.message, thread_id):
                etype = ev["event"]
                data = ev["data"]
                if etype == "token":
                    answer += data.get("token", "")
                elif etype == "done":
                    answer = data.get("answer") or answer
                    route = data.get("route") or route
                    citations = data.get("citations") or citations
                    qa_metrics = data.get("metrics") or qa_metrics
                    data = {**data, **decision.to_public(), "thread_id": thread_id}
                yield {
                    "event": etype,
                    "data": json.dumps(data, ensure_ascii=False),
                }
        except Exception as e:
            logger.error("chat.stream_error", error=str(e), exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
            return

        try:
            await _save_message(
                thread_id,
                "assistant",
                answer,
                route=route,
                citations=citations,
                metrics=qa_metrics,
            )
            await _persist_qa_metric(route, qa_metrics)
        except Exception as e:
            logger.warning("chat.assistant_save_failed", error=str(e))

    return EventSourceResponse(event_gen())
