# backend/services/reviews.py
# 人工终审：状态迁移（纯函数）与落库

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.eval.graph import dimension_weights
from backend.agents.eval.schemas import clamp_score

DECISION_ACTIONS = {
    "approved": "approve",
    "rejected": "reject",
    "revision_required": "request_revision",
}
CLAIMABLE = {"in_review"}
DECIDED = frozenset(DECISION_ACTIONS.keys())


class ReviewError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value) if value else {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def prepare_claim(row: dict, actor_id: str) -> dict[str, Any]:
    if row.get("review_status") not in CLAIMABLE:
        raise ReviewError("当前状态不可认领", 409)
    assignee = row.get("assignee_id")
    if assignee and assignee != actor_id:
        raise ReviewError("任务已被他人认领", 409)
    return {"assignee_id": actor_id, "review_status": "in_review", "action": "claim"}


def prepare_assign(row: dict, reviewer_id: str) -> dict[str, Any]:
    if row.get("review_status") not in CLAIMABLE:
        raise ReviewError("当前状态不可改派", 409)
    if not (reviewer_id or "").strip():
        raise ReviewError("缺少 reviewer_id", 400)
    return {
        "assignee_id": reviewer_id.strip(),
        "review_status": "in_review",
        "action": "assign",
    }


def merge_human_scores(system_scores: Any, human_scores: Any, patch: dict | None) -> dict:
    """系统分打底，再叠已有人工分与本次补丁。不回写系统分。"""
    merged: dict[str, Any] = {}
    for source in (_as_dict(system_scores), _as_dict(human_scores)):
        for dim, item in source.items():
            base = copy.deepcopy(merged.get(dim) or {})
            if isinstance(item, dict):
                base.update(copy.deepcopy(item))
            merged[dim] = base
    for dim, item in (patch or {}).items():
        base = copy.deepcopy(merged.get(dim) or {})
        if isinstance(item, dict):
            base.update(copy.deepcopy(item))
        if base.get("score") is not None:
            base["score"] = clamp_score(base.get("score"))
        for key in ("evidence", "risks", "suggestions"):
            if key not in base or base[key] is None:
                base[key] = []
        merged[dim] = base
    return merged


def recompute_overall(scores: dict) -> float:
    total = 0.0
    for dim, weight in dimension_weights().items():
        item = scores.get(dim) or {}
        if item.get("score") is None:
            continue
        total += clamp_score(item.get("score")) * weight
    return round(total, 2)


def prepare_override(
    row: dict,
    patch: dict | None,
    comment: str | None,
    *,
    recompute: bool = True,
) -> dict[str, Any]:
    if row.get("review_status") != "in_review":
        raise ReviewError("仅审核中可改分", 409)
    human = merge_human_scores(row.get("scores"), row.get("human_scores"), patch)
    overall = recompute_overall(human) if recompute else row.get("human_overall")
    return {
        "human_scores": human,
        "human_overall": overall,
        "human_comment": comment if comment is not None else row.get("human_comment"),
        "action": "override_score",
    }


def prepare_decision(row: dict, decision: str, comment: str | None, actor_id: str) -> dict[str, Any]:
    if decision not in DECISION_ACTIONS:
        raise ReviewError("decision 必须是 approved、rejected 或 revision_required", 400)
    if row.get("review_status") != "in_review":
        raise ReviewError("仅审核中可终审", 409)
    return {
        "final_decision": decision,
        "review_status": decision,
        "human_comment": comment if comment is not None else row.get("human_comment"),
        "decided_by": actor_id,
        "action": DECISION_ACTIONS[decision],
    }


def assert_assignee_or_admin(row: dict, user: dict) -> None:
    if user.get("role") == "admin":
        return
    if user.get("role") != "reviewer":
        raise ReviewError("权限不足", 403)
    if row.get("assignee_id") != user.get("user_id"):
        raise ReviewError("只有当前认领人可以操作", 403)


def _snapshot(row: dict) -> dict[str, Any]:
    return {
        "review_status": row.get("review_status"),
        "assignee_id": row.get("assignee_id"),
        "human_scores": _as_dict(row.get("human_scores")) or None,
        "human_overall": row.get("human_overall"),
        "human_comment": row.get("human_comment"),
        "final_decision": row.get("final_decision"),
        "scores": _as_dict(row.get("scores")) or None,
        "overall_score": row.get("overall_score"),
    }


async def _load(db: AsyncSession, review_id: str) -> dict | None:
    result = await db.execute(
        text("SELECT * FROM plan_reviews WHERE id = :id FOR UPDATE"),
        {"id": review_id},
    )
    row = result.mappings().fetchone()
    return dict(row) if row else None


async def _audit(
    db: AsyncSession,
    review_id: str,
    actor_id: str,
    action: str,
    before: dict,
    after: dict,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO review_actions
                (review_id, actor_id, action, before_snapshot, after_snapshot)
            VALUES
                (:review_id, :actor_id, :action,
                 CAST(:before AS jsonb), CAST(:after AS jsonb))
            """
        ),
        {
            "review_id": review_id,
            "actor_id": actor_id,
            "action": action,
            "before": json.dumps(before, ensure_ascii=False, default=str),
            "after": json.dumps(after, ensure_ascii=False, default=str),
        },
    )


async def _set_assignment(db: AsyncSession, review_id: str, reviewer_id: str) -> None:
    await db.execute(
        text(
            """
            UPDATE review_assignments
            SET status = 'released'
            WHERE review_id = :review_id AND status = 'active' AND reviewer_id <> :reviewer_id
            """
        ),
        {"review_id": review_id, "reviewer_id": reviewer_id},
    )
    existing = await db.execute(
        text(
            """
            SELECT id FROM review_assignments
            WHERE review_id = :review_id AND reviewer_id = :reviewer_id AND status = 'active'
            LIMIT 1
            """
        ),
        {"review_id": review_id, "reviewer_id": reviewer_id},
    )
    if existing.fetchone():
        return
    await db.execute(
        text(
            """
            INSERT INTO review_assignments (review_id, reviewer_id, status)
            VALUES (:review_id, :reviewer_id, 'active')
            """
        ),
        {"review_id": review_id, "reviewer_id": reviewer_id},
    )


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text_v = value.strip()
        if not text_v:
            return None
        if text_v.endswith("Z"):
            text_v = text_v[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text_v)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def compute_review_report(rows: list[dict], *, now: datetime | None = None) -> dict[str, Any]:
    """从审核行聚合通过率、平均改分差、积压队列（纯函数，便于单测）。"""
    now = now or datetime.now(timezone.utc)
    decided = [r for r in rows if r.get("final_decision") in DECIDED]
    approved = [r for r in decided if r.get("final_decision") == "approved"]
    pass_rate = (len(approved) / len(decided)) if decided else None

    diffs: list[float] = []
    for row in rows:
        sys_score = row.get("overall_score")
        human = row.get("human_overall")
        if sys_score is None or human is None:
            continue
        try:
            diffs.append(abs(float(human) - float(sys_score)))
        except (TypeError, ValueError):
            continue
    avg_abs_score_diff = (sum(diffs) / len(diffs)) if diffs else None

    backlog_in_review = 0
    backlog_unassigned = 0
    age_lt_1d = 0
    age_1_to_3d = 0
    age_gt_3d = 0
    for row in rows:
        status = row.get("review_status")
        if status not in CLAIMABLE:
            continue
        backlog_in_review += 1
        if not row.get("assignee_id"):
            backlog_unassigned += 1
        created = _parse_ts(row.get("created_at"))
        if created is None:
            continue
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours < 24:
            age_lt_1d += 1
        elif age_hours <= 72:
            age_1_to_3d += 1
        else:
            age_gt_3d += 1

    return {
        "pass_rate": None if pass_rate is None else round(pass_rate, 4),
        "decided_count": len(decided),
        "approved_count": len(approved),
        "avg_abs_score_diff": None if avg_abs_score_diff is None else round(avg_abs_score_diff, 2),
        "score_diff_sample_count": len(diffs),
        "backlog": {
            "in_review": backlog_in_review,
            "unassigned": backlog_unassigned,
            "age_buckets": {
                "lt_1d": age_lt_1d,
                "d1_to_3d": age_1_to_3d,
                "gt_3d": age_gt_3d,
            },
        },
    }


async def review_report(
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
            SELECT overall_score, human_overall, assignee_id, review_status,
                   final_decision, created_at
            FROM plan_reviews
            WHERE {where}
            """
        ),
        params,
    )
    payload = compute_review_report([dict(r) for r in rows.mappings().fetchall()])
    payload["date_from"] = date_from.isoformat() if date_from else None
    payload["date_to"] = date_to.isoformat() if date_to else None
    return payload


async def list_reviews(
    db: AsyncSession,
    *,
    review_status: str | None,
    assignee_id: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    filters = ["1=1"]
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if review_status:
        filters.append("review_status = :review_status")
        params["review_status"] = review_status
    if assignee_id:
        filters.append("assignee_id = :assignee_id")
        params["assignee_id"] = assignee_id
    where = " AND ".join(filters)
    total_row = await db.execute(
        text(f"SELECT COUNT(*) FROM plan_reviews WHERE {where}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )
    total = int(total_row.scalar() or 0)
    rows = await db.execute(
        text(
            f"""
            SELECT id, file_name, overall_score, human_overall, assignee_id,
                   review_status, final_decision, status, created_at, updated_at
            FROM plan_reviews
            WHERE {where}
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


async def claim_review(db: AsyncSession, review_id: str, user: dict) -> dict:
    if user.get("role") not in ("reviewer", "admin"):
        raise ReviewError("权限不足", 403)
    row = await _load(db, review_id)
    if not row:
        raise ReviewError("评估记录不存在", 404)
    change = prepare_claim(row, user["user_id"])
    before = _snapshot(row)
    await db.execute(
        text(
            """
            UPDATE plan_reviews
            SET assignee_id = :assignee_id, review_status = :review_status
            WHERE id = :id
            """
        ),
        {"id": review_id, **change},
    )
    await _set_assignment(db, review_id, user["user_id"])
    after = {**before, "assignee_id": change["assignee_id"], "review_status": change["review_status"]}
    await _audit(db, review_id, user["user_id"], "claim", before, after)
    await db.commit()
    return after


async def assign_review(db: AsyncSession, review_id: str, reviewer_id: str, user: dict) -> dict:
    if user.get("role") != "admin":
        raise ReviewError("只有管理员可以改派", 403)
    row = await _load(db, review_id)
    if not row:
        raise ReviewError("评估记录不存在", 404)
    change = prepare_assign(row, reviewer_id)
    before = _snapshot(row)
    await db.execute(
        text(
            """
            UPDATE plan_reviews
            SET assignee_id = :assignee_id, review_status = :review_status
            WHERE id = :id
            """
        ),
        {"id": review_id, "assignee_id": change["assignee_id"], "review_status": change["review_status"]},
    )
    await _set_assignment(db, review_id, change["assignee_id"])
    after = {**before, "assignee_id": change["assignee_id"], "review_status": change["review_status"]}
    await _audit(db, review_id, user["user_id"], "assign", before, after)
    await db.commit()
    return after


async def override_scores(
    db: AsyncSession,
    review_id: str,
    user: dict,
    patch: dict | None,
    comment: str | None,
    recompute: bool,
) -> dict:
    row = await _load(db, review_id)
    if not row:
        raise ReviewError("评估记录不存在", 404)
    assert_assignee_or_admin(row, user)
    change = prepare_override(row, patch, comment, recompute=recompute)
    before = _snapshot(row)
    await db.execute(
        text(
            """
            UPDATE plan_reviews
            SET human_scores = CAST(:human_scores AS jsonb),
                human_overall = :human_overall,
                human_comment = :human_comment
            WHERE id = :id
            """
        ),
        {
            "id": review_id,
            "human_scores": json.dumps(change["human_scores"], ensure_ascii=False),
            "human_overall": change["human_overall"],
            "human_comment": change["human_comment"],
        },
    )
    after = {
        **before,
        "human_scores": change["human_scores"],
        "human_overall": change["human_overall"],
        "human_comment": change["human_comment"],
    }
    await _audit(db, review_id, user["user_id"], "override_score", before, after)
    await db.commit()
    return after


async def decide_review(
    db: AsyncSession,
    review_id: str,
    user: dict,
    decision: str,
    comment: str | None,
) -> dict:
    row = await _load(db, review_id)
    if not row:
        raise ReviewError("评估记录不存在", 404)
    assert_assignee_or_admin(row, user)
    change = prepare_decision(row, decision, comment, user["user_id"])
    before = _snapshot(row)
    await db.execute(
        text(
            """
            UPDATE plan_reviews
            SET final_decision = :final_decision,
                review_status = :review_status,
                human_comment = :human_comment,
                decided_by = :decided_by,
                decided_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": review_id, **{k: change[k] for k in ("final_decision", "review_status", "human_comment", "decided_by")}},
    )
    if change["review_status"] in ("approved", "rejected", "revision_required"):
        await db.execute(
            text(
                """
                UPDATE review_assignments
                SET status = 'done'
                WHERE review_id = :review_id AND status = 'active'
                """
            ),
            {"review_id": review_id},
        )
    after = {
        **before,
        "final_decision": change["final_decision"],
        "review_status": change["review_status"],
        "human_comment": change["human_comment"],
    }
    await _audit(db, review_id, user["user_id"], change["action"], before, after)
    await db.commit()
    return after


async def list_actions(db: AsyncSession, review_id: str) -> list[dict]:
    exists = await db.execute(
        text("SELECT 1 FROM plan_reviews WHERE id = :id"),
        {"id": review_id},
    )
    if not exists.fetchone():
        raise ReviewError("评估记录不存在", 404)
    rows = await db.execute(
        text(
            """
            SELECT id, review_id, actor_id, action, before_snapshot, after_snapshot, created_at
            FROM review_actions
            WHERE review_id = :id
            ORDER BY created_at DESC
            """
        ),
        {"id": review_id},
    )
    return [dict(r) for r in rows.mappings().fetchall()]
