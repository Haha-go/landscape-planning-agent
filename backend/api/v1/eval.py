# backend/api/v1/eval.py
# 规划方案评估 API

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.eval.graph import run_plan_evaluation
from backend.config import get_settings
from backend.core.exceptions import FileParseError, LandscapeAgentError
from backend.core.logger import get_logger
from backend.core.security import can_view_plan, get_current_user
from backend.dependencies import get_db
from backend.services.plan_store import (
    insert_processing_review,
    list_my_plans,
    parse_bool_form,
    save_evaluation,
    save_failure,
)

router = APIRouter()
logger = get_logger(__name__)


async def _save_upload(file: UploadFile) -> tuple[str, Path]:
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    review_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "plan.pdf").name
    dest = upload_dir / f"{review_id}_{safe_name}"
    data = await file.read()

    def _write() -> None:
        dest.write_bytes(data)

    await asyncio.to_thread(_write)
    return review_id, dest


@router.post("/evaluate")
async def evaluate_plan(
    file: UploadFile = File(...),
    needs_human_review: str = Form(default="true"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """上传 PDF 评估。须登录；needs_human_review 控制是否进入人工队列。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    human = parse_bool_form(needs_human_review, default=True)
    review_id, dest = await _save_upload(file)

    await insert_processing_review(
        db,
        review_id=review_id,
        path=str(dest),
        file_name=file.filename,
        submitter_id=user["user_id"],
        needs_human_review=human,
    )

    try:
        result = await run_plan_evaluation(str(dest), file_name=file.filename or "")
        if result.get("fallback_used"):
            await save_failure(db, review_id, result.get("content") or "fallback")
            return {
                "review_id": review_id,
                "status": "failed",
                "review_status": "failed",
                "needs_human_review": human,
                "message": result.get("content"),
            }

        saved = await save_evaluation(
            db, review_id, result, needs_human_review=human
        )
        return {
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
    except FileParseError as e:
        await save_failure(db, review_id, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except LandscapeAgentError as e:
        await save_failure(db, review_id, str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("eval.api_failed", error=str(e), exc_info=True)
        await save_failure(db, review_id, str(e))
        raise HTTPException(status_code=500, detail=f"评估失败: {e}")


@router.get("/mine")
async def my_plans(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """当前登录用户上传的方案列表。"""
    return await list_my_plans(
        db, submitter_id=user["user_id"], page=page, page_size=page_size
    )


@router.get("/{review_id}")
async def get_plan_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    row = await db.execute(
        text(
            """
            SELECT id, file_name, extracted_data, scores, overall_score,
                   summary, issues, status, error_msg, created_at, updated_at,
                   review_status, submitter_id, assignee_id, human_scores,
                   human_overall, human_comment, final_decision, decided_at, decided_by,
                   needs_human_review
            FROM plan_reviews WHERE id = :id
            """
        ),
        {"id": review_id},
    )
    r = row.mappings().fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="评估记录不存在")
    payload = dict(r)
    if not can_view_plan(user, payload):
        raise HTTPException(status_code=403, detail="无权查看该方案")
    return payload
