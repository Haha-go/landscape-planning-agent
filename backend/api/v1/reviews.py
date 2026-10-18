# backend/api/v1/reviews.py
# 审核队列：列表、认领、改派、改分、终审、审计

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import require_roles
from backend.dependencies import get_db
from backend.services.reviews import (
    ReviewError,
    assign_review,
    claim_review,
    decide_review,
    list_actions,
    list_reviews,
    override_scores,
    review_report,
)

router = APIRouter()


class AssignBody(BaseModel):
    reviewer_id: str = Field(..., min_length=1)


class ScorePatch(BaseModel):
    scores: dict = Field(default_factory=dict)
    recompute_overall: bool = True
    comment: str | None = None


class DecisionBody(BaseModel):
    decision: str
    comment: str | None = None


def _raise(exc: ReviewError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("")
async def get_reviews(
    review_status: str | None = None,
    assignee_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    del user
    return await list_reviews(
        db,
        review_status=review_status,
        assignee_id=assignee_id,
        page=page,
        page_size=page_size,
    )


@router.get("/report")
async def get_review_report(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    del user
    return await review_report(db, date_from=date_from, date_to=date_to)


@router.post("/{review_id}/claim")
async def post_claim(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    try:
        return await claim_review(db, review_id, user)
    except ReviewError as exc:
        _raise(exc)


@router.post("/{review_id}/assign")
async def post_assign(
    review_id: str,
    body: AssignBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    try:
        return await assign_review(db, review_id, body.reviewer_id, user)
    except ReviewError as exc:
        _raise(exc)


@router.patch("/{review_id}/scores")
async def patch_scores(
    review_id: str,
    body: ScorePatch,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    try:
        return await override_scores(
            db,
            review_id,
            user,
            body.scores,
            body.comment,
            body.recompute_overall,
        )
    except ReviewError as exc:
        _raise(exc)


@router.post("/{review_id}/decision")
async def post_decision(
    review_id: str,
    body: DecisionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    try:
        return await decide_review(db, review_id, user, body.decision, body.comment)
    except ReviewError as exc:
        _raise(exc)


@router.get("/{review_id}/actions")
async def get_actions(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    del user
    try:
        return {"items": await list_actions(db, review_id)}
    except ReviewError as exc:
        _raise(exc)
