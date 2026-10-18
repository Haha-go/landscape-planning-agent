# backend/agents/orchestrator/graph.py
# 编排者：规则优先路由 qa | eval | other；支持 agent_mode 锁定产品 Agent

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from backend.core.logger import get_logger
from backend.core.metrics import record_metric

logger = get_logger(__name__)

RouteName = Literal["qa", "eval", "other"]
AgentMode = Literal["auto", "kb", "plan"]
ProductAgent = Literal["kb", "plan"]

AGENT_LABELS: dict[ProductAgent, str] = {
    "kb": "规范智库",
    "plan": "规划通",
}

_EVAL_KEYWORDS = (
    "评估", "评价", "审查", "批改", "打分", "评分",
    "规划方案", "方案评审", "方案评估", "上传方案",
    "evaluate", "review plan",
)


@dataclass
class RouteDecision:
    intent: RouteName
    reason: str
    mode: AgentMode = "auto"
    agent: ProductAgent | None = None

    @property
    def agent_label(self) -> str | None:
        if self.agent is None:
            return None
        return AGENT_LABELS[self.agent]

    def to_public(self) -> dict:
        return {
            "intent": self.intent,
            "reason": self.reason,
            "mode": self.mode,
            "agent": self.agent,
            "agent_label": self.agent_label,
        }


def _normalize_mode(agent_mode: str | None) -> AgentMode:
    mode = (agent_mode or "auto").strip().lower()
    if mode in ("auto", "kb", "plan"):
        return mode  # type: ignore[return-value]
    return "auto"


def _auto_route(user_text: str, has_file: bool) -> RouteDecision:
    """自动档：有文件 → eval；评估话术但无文件 → other；否则 → qa。"""
    text = (user_text or "").strip()
    lowered = text.lower()
    if has_file:
        return RouteDecision(intent="eval", reason="has_file", mode="auto", agent="plan")
    if any(k.lower() in lowered for k in _EVAL_KEYWORDS):
        if "上传" in text or "pdf" in lowered or "方案" in text:
            return RouteDecision(
                intent="other", reason="eval_without_file", mode="auto", agent="plan"
            )
        return RouteDecision(
            intent="other", reason="eval_keyword_no_file", mode="auto", agent="plan"
        )
    if not text:
        return RouteDecision(intent="other", reason="empty", mode="auto", agent=None)
    return RouteDecision(intent="qa", reason="default_qa", mode="auto", agent="kb")


def route_intent(
    user_text: str = "",
    has_file: bool = False,
    expected: str | None = None,
    agent_mode: str | None = "auto",
) -> RouteDecision:
    """按 agent_mode 路由。

    - auto：原规则（文件 → eval；评估话术无文件 → other；其余 → qa）
    - kb：强制规范智库问答；附带文件时 → other（需切规划通）
    - plan：强制规划通；有文件 → eval；无文件 → other（上传引导）
    """
    started = time.perf_counter()
    mode = _normalize_mode(agent_mode)
    text = (user_text or "").strip()

    if mode == "kb":
        if has_file:
            decision = RouteDecision(
                intent="other",
                reason="kb_mode_with_file",
                mode="kb",
                agent="kb",
            )
        elif not text:
            decision = RouteDecision(
                intent="other", reason="empty", mode="kb", agent="kb"
            )
        else:
            decision = RouteDecision(
                intent="qa", reason="mode_kb", mode="kb", agent="kb"
            )
    elif mode == "plan":
        if has_file:
            decision = RouteDecision(
                intent="eval", reason="mode_plan_has_file", mode="plan", agent="plan"
            )
        else:
            decision = RouteDecision(
                intent="other",
                reason="mode_plan_without_file",
                mode="plan",
                agent="plan",
            )
    else:
        decision = _auto_route(user_text, has_file)

    record_metric(
        route=decision.intent,
        expected_route=expected,
        tool="route",
        step="route",
        success=True,
        latency_ms=(time.perf_counter() - started) * 1000,
        detail={
            "reason": decision.reason,
            "agent_mode": decision.mode,
            "agent": decision.agent,
        },
    )
    return decision


OTHER_GUIDE = (
    "我可以帮您：\n"
    "1. 规范智库：直接提问景观规划 / 规范相关问题\n"
    "2. 规划通：请上传 PDF 规划方案文件（可在对话中附带附件，或切换到「规划通」模式）\n"
)

GUIDE_KB_WITH_FILE = (
    "当前锁定为「规范智库」，不处理方案 PDF。\n"
    "请切换到「规划通」或「自动」后再上传评估；或去掉附件后继续提问规范问题。"
)

GUIDE_PLAN_WITHOUT_FILE = (
    "当前为「规划通」。请上传 PDF 规划方案文件后发送，即可开始五维评估。\n"
    "若只想查规范，请切换到「规范智库」或「自动」。"
)


def guide_for(decision: RouteDecision) -> str:
    """按路由原因返回引导文案。"""
    if decision.reason == "kb_mode_with_file":
        return GUIDE_KB_WITH_FILE
    if decision.reason == "mode_plan_without_file":
        return GUIDE_PLAN_WITHOUT_FILE
    if decision.reason in ("eval_without_file", "eval_keyword_no_file"):
        return (
            "检测到您想做方案评估。请上传 PDF 规划方案文件后发送，"
            "或切换到「规划通」模式并附带附件。\n"
            "若只想查规范，可切换到「规范智库」。"
        )
    return OTHER_GUIDE
