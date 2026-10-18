# tests/test_reviews.py
import time

from backend.agents.eval.schemas import DIM_COMPLIANCE, DIM_CONCEPT
from backend.core.security import authenticate, create_access_token, decode_token
from datetime import datetime, timedelta, timezone

from backend.core.security import can_view_plan
from backend.services.plan_store import parse_bool_form
from backend.services.reviews import (
    CLAIMABLE,
    ReviewError,
    assert_assignee_or_admin,
    compute_review_report,
    merge_human_scores,
    prepare_assign,
    prepare_claim,
    prepare_decision,
    prepare_override,
    recompute_overall,
)


def _row(**extra):
    base = {
        "review_status": "in_review",
        "assignee_id": None,
        "scores": {
            DIM_CONCEPT: {"score": 90, "evidence": ["理念清楚"], "risks": [], "suggestions": []},
            DIM_COMPLIANCE: {"score": 80, "evidence": ["引用规范"], "risks": ["条款不足"], "suggestions": []},
        },
        "human_scores": None,
        "human_overall": None,
        "human_comment": None,
        "final_decision": None,
        "overall_score": 85,
    }
    base.update(extra)
    return base


def test_login_and_jwt_roundtrip():
    user = authenticate("reviewer", "reviewer123")
    assert user and user["role"] == "reviewer"
    assert authenticate("reviewer", "nope") is None
    token, _expires = create_access_token(user)
    payload = decode_token(token)
    assert payload["role"] == "reviewer"
    assert payload["sub"] == "user_reviewer"


def test_expired_token_is_rejected():
    from backend.core import security

    user = authenticate("admin", "admin123")
    payload = {
        "sub": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "iat": int(time.time()) - 20,
        "exp": int(time.time()) - 10,
    }
    token = security._sign(payload, security.get_settings().jwt_secret)
    try:
        decode_token(token)
        assert False, "expired token should fail"
    except security.AuthenticationError:
        pass


def test_claim_assign_and_conflict():
    claimed = prepare_claim(_row(), "user_reviewer")
    assert claimed["action"] == "claim"
    assert claimed["review_status"] == "in_review"
    again = prepare_claim(_row(assignee_id="user_reviewer"), "user_reviewer")
    assert again["assignee_id"] == "user_reviewer"
    try:
        prepare_claim(_row(assignee_id="user_other"), "user_reviewer")
        assert False, "expected conflict"
    except ReviewError as exc:
        assert exc.status_code == 409
    assigned = prepare_assign(_row(), "user_reviewer")
    assert assigned["action"] == "assign"
    assert assigned["assignee_id"] == "user_reviewer"


def test_system_only_not_claimable():
    assert CLAIMABLE == {"in_review"}
    try:
        prepare_claim(_row(review_status="system_only"), "user_reviewer")
        assert False, "system_only must not be claimable"
    except ReviewError as exc:
        assert exc.status_code == 409
    try:
        prepare_claim(_row(review_status="system_done"), "user_reviewer")
        assert False, "system_done must not be claimable"
    except ReviewError as exc:
        assert exc.status_code == 409


def test_needs_human_review_form_parse():
    assert parse_bool_form("true") is True
    assert parse_bool_form("false") is False
    assert parse_bool_form("0") is False
    assert parse_bool_form(None, default=True) is True
    assert parse_bool_form("", default=False) is False


def test_can_view_plan_ownership():
    submitter = {"role": "submitter", "user_id": "user_submitter"}
    other = {"role": "submitter", "user_id": "user_other"}
    expert = {"role": "reviewer", "user_id": "user_reviewer"}
    row = {"submitter_id": "user_submitter"}
    assert can_view_plan(submitter, row) is True
    assert can_view_plan(other, row) is False
    assert can_view_plan(expert, row) is True


def test_override_keeps_system_scores_and_recomputes_human_overall():
    system = {
        DIM_CONCEPT: {"score": 90, "evidence": ["理念清楚"], "risks": [], "suggestions": []},
        DIM_COMPLIANCE: {"score": 80, "evidence": ["引用规范"], "risks": ["条款不足"], "suggestions": []},
    }
    row = _row(scores=system)
    change = prepare_override(
        row,
        {DIM_COMPLIANCE: {"score": 70}},
        "规范引用不足，下调合规分",
        recompute=True,
    )
    assert system[DIM_COMPLIANCE]["score"] == 80
    assert row["overall_score"] == 85
    assert change["action"] == "override_score"
    assert change["human_scores"][DIM_COMPLIANCE]["score"] == 70
    assert change["human_scores"][DIM_CONCEPT]["score"] == 90
    assert change["human_overall"] == recompute_overall(change["human_scores"])
    assert change["human_comment"] == "规范引用不足，下调合规分"


def test_decision_actions_and_assignee_guard():
    approved = prepare_decision(_row(assignee_id="user_reviewer"), "approved", "同意", "user_reviewer")
    assert approved["action"] == "approve"
    assert approved["review_status"] == "approved"
    rejected = prepare_decision(_row(), "rejected", None, "user_admin")
    assert rejected["action"] == "reject"
    revision = prepare_decision(_row(), "revision_required", "请改", "user_admin")
    assert revision["action"] == "request_revision"
    try:
        prepare_decision(_row(review_status="approved"), "approved", None, "user_admin")
        assert False, "terminal state"
    except ReviewError as exc:
        assert exc.status_code == 409
    reviewer = {"role": "reviewer", "user_id": "user_reviewer"}
    assert_assignee_or_admin(_row(assignee_id="user_reviewer"), reviewer)
    try:
        assert_assignee_or_admin(_row(assignee_id="user_other"), reviewer)
        assert False, "not assignee"
    except ReviewError as exc:
        assert exc.status_code == 403
    assert_assignee_or_admin(_row(assignee_id="user_other"), {"role": "admin", "user_id": "user_admin"})


def test_merged_scores_do_not_alias_system_lists():
    system = {DIM_COMPLIANCE: {"score": 80, "evidence": ["原文"], "risks": [], "suggestions": []}}
    merged = merge_human_scores(system, None, {DIM_COMPLIANCE: {"score": 60, "evidence": ["人工依据"]}})
    merged[DIM_COMPLIANCE]["evidence"].append("后加")
    assert system[DIM_COMPLIANCE]["evidence"] == ["原文"]


def test_review_report_empty_and_boundaries():
    empty = compute_review_report([])
    assert empty["pass_rate"] is None
    assert empty["decided_count"] == 0
    assert empty["avg_abs_score_diff"] is None
    assert empty["backlog"]["in_review"] == 0
    assert empty["backlog"]["unassigned"] == 0

    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "final_decision": "approved",
            "review_status": "approved",
            "overall_score": 80,
            "human_overall": 85,
            "assignee_id": "u1",
            "created_at": now - timedelta(hours=2),
        },
        {
            "final_decision": "rejected",
            "review_status": "rejected",
            "overall_score": 70,
            "human_overall": None,
            "assignee_id": "u1",
            "created_at": now - timedelta(days=2),
        },
        {
            "final_decision": None,
            "review_status": "in_review",
            "overall_score": 90,
            "human_overall": 88,
            "assignee_id": None,
            "created_at": now - timedelta(days=4),
        },
        {
            "final_decision": None,
            "review_status": "in_review",
            "overall_score": None,
            "human_overall": 50,
            "assignee_id": "u2",
            "created_at": now - timedelta(hours=30),
        },
        {
            "final_decision": None,
            "review_status": "system_only",
            "overall_score": 95,
            "human_overall": None,
            "assignee_id": None,
            "created_at": now - timedelta(hours=1),
        },
    ]
    report = compute_review_report(rows, now=now)
    assert report["decided_count"] == 2
    assert report["approved_count"] == 1
    assert report["pass_rate"] == 0.5
    # only rows with both scores: |85-80|=5 and |88-90|=2 → avg 3.5
    assert report["avg_abs_score_diff"] == 3.5
    assert report["score_diff_sample_count"] == 2
    assert report["backlog"]["in_review"] == 2
    assert report["backlog"]["unassigned"] == 1
    assert report["backlog"]["age_buckets"]["lt_1d"] == 0
    assert report["backlog"]["age_buckets"]["d1_to_3d"] == 1
    assert report["backlog"]["age_buckets"]["gt_3d"] == 1
