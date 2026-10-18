# backend/api/v1/metrics.py
# 运营指标汇总读接口

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import require_roles
from backend.dependencies import get_db
from backend.services.metrics_report import metrics_summary

router = APIRouter()


@router.get("/summary")
async def get_metrics_summary(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer", "admin")),
):
    del user
    return await metrics_summary(db, date_from=date_from, date_to=date_to)
