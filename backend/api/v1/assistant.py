# backend/api/v1/assistant.py
# 统一入口：message + 可选 PDF + agent_mode → Orchestrator 路由 qa | eval | other

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from backend.agents.eval.graph import run_plan_evaluation
from backend.agents.orchestrator.graph import guide_for, route_intent
from backend.agents.qa.graph import stream_qa_answer
from backend.api.v1.chat import _ensure_session, _save_message
from backend.api.v1.eval import _save_upload
from backend.core.exceptions import FileParseError, LandscapeAgentError
from backend.core.logger import get_logger
from backend.core.security import get_optional_user
from backend.dependencies import AsyncSessionLocal
from backend.services.plan_store import (
    insert_processing_review,
    parse_bool_form,
    save_evaluation,
    save_failure,
)

router = APIRouter()
logger = get_logger(__name__)


def _orch_payload(decision, tid: str | None = None) -> dict:
    payload = decision.to_public()
    if tid is not None:
        payload["thread_id"] = tid
    return payload


@router.post("")
@router.post("/")
async def assistant(
    message: str = Form(default=""),
    thread_id: str | None = Form(default=None),
    agent_mode: str = Form(default="auto"),
    needs_human_review: str = Form(default="true"),
    file: UploadFile | None = File(default=None),
    user: dict | None = Depends(get_optional_user),
):
    """统一助手入口。

    - agent_mode: auto | kb | plan
    - 有 PDF 且路由到 eval → 须登录；同步 JSON（规划通结果）
    - 否则 SSE（规范智库问答或引导，可匿名）
    """
    has_file = bool(file and file.filename)
    decision = route_intent(message, has_file=has_file, agent_mode=agent_mode)
    tid = thread_id or str(uuid.uuid4())
    orch = _orch_payload(decision, tid)
    human = parse_bool_form(needs_human_review, default=True)

    if decision.intent == "eval" and has_file:
        assert file is not None
        if not user:
            return JSONResponse(
                status_code=401,
                content={**orch, "detail": "上传方案请先登录（提交人或评审账号）"},
            )
        if not file.filename.lower().endswith(".pdf"):
            return JSONResponse(status_code=400, content={"detail": "仅支持 PDF 文件"})
        review_id, dest = await _save_upload(file)
        async with AsyncSessionLocal() as db:
            await insert_processing_review(
                db,
                review_id=review_id,
                path=str(dest),
                file_name=file.filename or "",
                submitter_id=user["user_id"],
                needs_human_review=human,
            )
        try:
            result = await run_plan_evaluation(str(dest), file_name=file.filename or "")
            if result.get("fallback_used"):
                async with AsyncSessionLocal() as db:
                    await save_failure(db, review_id, result.get("content") or "fallback")
                return {
                    **orch,
                    "orchestrator": decision.reason,
                    "intent": "eval",
                    "review_id": review_id,
                    "status": "failed",
                    "review_status": "failed",
                    "needs_human_review": human,
                    "message": result.get("content"),
                }
            async with AsyncSessionLocal() as db:
                saved = await save_evaluation(
                    db, review_id, result, needs_human_review=human
                )
            return {
                **orch,
                "orchestrator": decision.reason,
                "intent": "eval",
                "review_id": review_id,
                "status": saved["status"],
                "review_status": saved["review_status"],
                "needs_human_review": human,
                "overall_score": result.get("overall_score"),
                "summary": result.get("summary"),
                "scores": result.get("scores") or {},
                "issues": result.get("issues") or [],
                "extracted": result.get("extracted") or {},
                "steps_complete": result.get("steps_complete"),
                "error": saved["error"],
            }
        except (FileParseError, LandscapeAgentError) as e:
            async with AsyncSessionLocal() as db:
                await save_failure(db, review_id, str(e))
            return JSONResponse(
                status_code=400,
                content={**orch, "detail": str(e), "intent": "eval"},
            )
        except Exception as e:
            logger.error("assistant.eval_failed", error=str(e), exc_info=True)
            async with AsyncSessionLocal() as db:
                await save_failure(db, review_id, str(e))
            return JSONResponse(
                status_code=500,
                content={**orch, "detail": str(e), "intent": "eval"},
            )

    # qa / other → SSE
    async def event_gen():
        yield {
            "event": "orchestrator",
            "data": json.dumps(orch, ensure_ascii=False),
        }
        if decision.intent != "qa":
            guide = guide_for(decision)
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
                        "thread_id": tid,
                        "orchestrator": decision.reason,
                        **decision.to_public(),
                    },
                    ensure_ascii=False,
                ),
            }
            return

        try:
            await _ensure_session(tid)
            await _save_message(tid, "user", message or "")
        except Exception as e:
            logger.warning("assistant.session_save_failed", error=str(e))

        answer = ""
        route = "direct"
        citations: list = []
        qa_metrics: dict | None = None
        try:
            async for ev in stream_qa_answer(message or "", tid):
                etype = ev["event"]
                data = ev["data"]
                if etype == "token":
                    answer += data.get("token", "")
                elif etype == "done":
                    answer = data.get("answer") or answer
                    route = data.get("route") or route
                    citations = data.get("citations") or citations
                    qa_metrics = data.get("metrics") or qa_metrics
                    data = {
                        **data,
                        **decision.to_public(),
                        "thread_id": tid,
                    }
                yield {"event": etype, "data": json.dumps(data, ensure_ascii=False)}
        except Exception as e:
            logger.error("assistant.stream_error", error=str(e), exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
            return
        try:
            from backend.api.v1.chat import _persist_qa_metric

            await _save_message(
                tid,
                "assistant",
                answer,
                route=route,
                citations=citations,
                metrics=qa_metrics,
            )
            await _persist_qa_metric(route, qa_metrics)
        except Exception:
            pass

    return EventSourceResponse(event_gen())
