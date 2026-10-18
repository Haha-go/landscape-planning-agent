# backend/agents/eval/graph.py
# 方案评估：PDF 抽取 → 结构化 → 五维并行评分 → 聚合
# 重试只包住单次模型调用；某一维失败写入该维错误，不重跑已成功的维。

from __future__ import annotations

import json
import uuid
from typing import Any, Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from backend.agents.eval.schemas import (
    PlanExtractSchema,
    DimensionScore,
    DIM_CONCEPT,
    DIM_COMPLIANCE,
    DIM_ECOLOGY,
    DIM_LAYOUT,
    DIM_ECONOMY,
    DIM_LABELS,
    SUMMARY_MAX_CHARS,
    ISSUES_MAX,
    clamp_score,
)
from backend.config import get_settings
from backend.core.llm_factory import get_llm, get_structured_llm
from backend.core.logger import get_logger
from backend.core.metrics import begin_run, end_run, record_metric
from backend.core.retry import retry_call
from backend.parsers.pdf import extract_pdf_text
from backend.rag.retriever import retrieve

logger = get_logger(__name__)

ALL_DIMS = [DIM_CONCEPT, DIM_COMPLIANCE, DIM_ECOLOGY, DIM_LAYOUT, DIM_ECONOMY]


def _merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


def truncate_summary(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    return (text or "").strip()[:limit]


def message_text(msg: Any) -> str:
    content = getattr(msg, "content", msg)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def token_usage(msg: Any) -> int | None:
    usage = getattr(msg, "usage_metadata", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
    else:
        total = getattr(usage, "total_tokens", None)
    return int(total) if isinstance(total, int) else None


def dimension_weights() -> dict[str, float]:
    settings = get_settings()
    return {
        DIM_CONCEPT: settings.eval_weight_concept,
        DIM_COMPLIANCE: settings.eval_weight_compliance,
        DIM_ECOLOGY: settings.eval_weight_ecology,
        DIM_LAYOUT: settings.eval_weight_layout,
        DIM_ECONOMY: settings.eval_weight_economy,
    }


def is_successful_evaluation(result: dict[str, Any]) -> bool:
    """五维齐套、分数合法、摘要与问题清单符合规格。"""
    scores = result.get("scores") or {}
    if any(dim not in scores for dim in ALL_DIMS):
        return False
    for dim in ALL_DIMS:
        item = scores.get(dim) or {}
        if item.get("failed") or item.get("error"):
            return False
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return False
        if score < 0 or score > 100:
            return False
        for key in ("evidence", "risks", "suggestions"):
            if not isinstance(item.get(key), list):
                return False
    summary = result.get("summary") or ""
    if len(summary) > SUMMARY_MAX_CHARS:
        return False
    issues = result.get("issues") or []
    if len(issues) > ISSUES_MAX:
        return False
    return True


class EvalState(TypedDict, total=False):
    file_path: str
    file_name: str
    raw_text: str
    extracted: dict[str, Any]
    dimension: str
    scores: Annotated[dict, _merge_dicts]
    overall_score: float
    summary: str
    issues: list[str]
    review_id: str
    error: str
    steps_complete: bool


async def parse_pdf_node(state: EvalState) -> dict:
    result = extract_pdf_text(state["file_path"])
    text = result["full_text"]
    if len(text) > 60000:
        text = text[:60000] + "\n...(文本过长已截断)"
    return {"raw_text": text}


async def extract_node(state: EvalState) -> dict:
    llm = get_structured_llm("eval_extract", PlanExtractSchema)

    async def _call() -> PlanExtractSchema:
        prompt = (
            "你是景观规划专家。请从下列规划方案文本中抽取结构化信息。"
            "缺失字段填空字符串或空列表，不要编造。\n\n"
            f"{state['raw_text']}"
        )
        return await llm.ainvoke(
            [
                SystemMessage(content="抽取规划方案关键结构化字段。"),
                HumanMessage(content=prompt),
            ]
        )

    result: PlanExtractSchema = await retry_call(
        _call, agent_type="eval_extract", tool="llm", step="extract"
    )
    data = result.model_dump()
    logger.info("eval.extracted", project=data.get("project_name"))
    return {"extracted": data}


def fanout_dimensions(state: EvalState) -> list[Send]:
    return [Send("score_dimension", {**state, "dimension": d}) for d in ALL_DIMS]


def _failed_dimension(dim: str, error: str) -> dict[str, Any]:
    return {
        "label": DIM_LABELS[dim],
        "score": None,
        "evidence": [],
        "risks": [],
        "suggestions": [],
        "error": error,
        "failed": True,
    }


async def _score_dimension(state: EvalState) -> dict[str, Any]:
    dim = state["dimension"]
    label = DIM_LABELS[dim]
    extracted = state.get("extracted") or {}
    extracted_json = json.dumps(extracted, ensure_ascii=False, indent=2)

    extra_ctx = ""
    if dim == DIM_COMPLIANCE:
        q_parts = []
        if extracted.get("green_water_indicators"):
            q_parts.append(str(extracted["green_water_indicators"]))
        if extracted.get("cited_standards"):
            q_parts.extend(extracted["cited_standards"][:5])
        q_parts.append("景观规划绿地率规范要求")
        query = " ".join(q_parts)[:500]
        try:
            docs = await retrieve(query, top_n=3)
            if docs:
                extra_ctx = "\n\n## 相关规范摘录\n" + "\n\n".join(
                    f"- {d.get('citation', {}).get('title')}: {d.get('text', '')[:400]}"
                    for d in docs
                )
        except Exception as e:
            logger.warning("eval.compliance_retrieve_failed", error=str(e))

    llm = get_structured_llm("eval", DimensionScore)
    system = (
        f"你是景观规划评审专家。请对「{label}」维度打分（0-100）。"
        "给出 evidence（方案中的依据）、risks、suggestions。"
        "评分要严格、有据；信息不足时分数偏低并说明。"
    )
    human = f"## 结构化抽取\n{extracted_json}\n{extra_ctx}\n\n请仅评估维度：{label}"

    async def _call() -> DimensionScore:
        return await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )

    score: DimensionScore = await retry_call(
        _call, agent_type="eval", tool="llm", step=f"score:{dim}"
    )
    payload = score.model_dump()
    payload["score"] = clamp_score(payload.get("score"))
    payload["label"] = label
    payload["failed"] = False
    logger.info("eval.scored", dimension=dim, score=payload.get("score"))
    return payload


async def score_dimension_node(state: EvalState) -> dict:
    dim = state["dimension"]
    try:
        payload = await _score_dimension(state)
    except Exception as e:
        logger.error("eval.dimension_failed", dimension=dim, error=str(e))
        payload = _failed_dimension(dim, str(e))
    return {"scores": {dim: payload}}


async def aggregate_node(state: EvalState) -> dict:
    weights = dimension_weights()
    scores = state.get("scores") or {}
    issues: list[str] = []
    overall = 0.0
    for dim, weight in weights.items():
        item = dict(scores.get(dim) or {})
        if item.get("failed") or item.get("score") is None:
            issues.append(f"[{DIM_LABELS[dim]}] 评分失败：{item.get('error') or '缺失'}")
            continue
        score = clamp_score(item.get("score"))
        item["score"] = score
        scores[dim] = item
        overall += score * weight
        for risk in item.get("risks") or []:
            issues.append(f"[{DIM_LABELS[dim]}] {risk}")
    overall = round(overall, 2)
    issues = issues[:ISSUES_MAX]

    summary = truncate_summary(f"加权总分{overall}。请结合各维度风险复核。")
    try:
        llm = get_llm("eval", temperature=0.2)

        async def _call():
            return await llm.ainvoke(
                [
                    SystemMessage(content="用中文写一段不超过120字的规划方案总评。"),
                    HumanMessage(
                        content=json.dumps(
                            {"overall": overall, "scores": scores, "top_issues": issues[:5]},
                            ensure_ascii=False,
                        )
                    ),
                ]
            )

        brief = await retry_call(_call, agent_type="eval", tool="llm", step="summary")
        summary = truncate_summary(message_text(brief))
        tokens = token_usage(brief)
        if tokens:
            record_metric(
                tool="llm",
                step="summary_tokens",
                success=True,
                latency_ms=0,
                tokens=tokens,
            )
    except Exception as e:
        logger.warning("eval.summary_failed", error=str(e))

    return {
        "overall_score": overall,
        "summary": summary,
        "issues": issues,
        "scores": scores,
        "steps_complete": is_successful_evaluation(
            {"scores": scores, "summary": summary, "issues": issues}
        ),
    }


def build_eval_graph():
    g = StateGraph(EvalState)
    g.add_node("parse_pdf", parse_pdf_node)
    g.add_node("extract", extract_node)
    g.add_node("score_dimension", score_dimension_node)
    g.add_node("aggregate", aggregate_node)

    g.add_edge(START, "parse_pdf")
    g.add_edge("parse_pdf", "extract")
    g.add_conditional_edges("extract", fanout_dimensions, ["score_dimension"])
    g.add_edge("score_dimension", "aggregate")
    g.add_edge("aggregate", END)
    return g.compile()


_eval_graph = None


def get_eval_graph():
    global _eval_graph
    if _eval_graph is None:
        _eval_graph = build_eval_graph()
    return _eval_graph


async def run_plan_evaluation(file_path: str, file_name: str = "") -> dict[str, Any]:
    """执行评估图。解析失败立即抛出；单维失败留在 scores 里，不重跑整图。"""
    run_id = str(uuid.uuid4())
    begin_run(run_id)
    try:
        graph = get_eval_graph()
        result = await graph.ainvoke(
            {"file_path": file_path, "file_name": file_name or file_path, "scores": {}}
        )
        payload = dict(result)
        payload["metrics"] = end_run(run_id)
        payload["steps_complete"] = is_successful_evaluation(payload)
        return payload
    except Exception:
        end_run(run_id)
        raise
