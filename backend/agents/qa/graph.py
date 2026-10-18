# backend/agents/qa/graph.py
# QA Agent：intent → direct | retrieve → rerank → generate
# 正式 HTTP 路径以 stream_qa_answer 为准；编译图与空检索拒答行为对齐。

from __future__ import annotations

import operator
import re
import time
from typing import Any, Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

from backend.core.llm_factory import get_llm
from backend.core.logger import get_logger
from backend.core.metrics import record_metric
from backend.models.intent import get_intent_classifier
from backend.rag.retriever import clamp_snippet, retrieve

logger = get_logger(__name__)

SNIPPET_MAX = 200

_DISCLAIMER_PATTERNS = (
    "SAMPLE knowledge",
    "NOT an official standard",
    "样例知识库",
    "非正式规范",
    "不是正式规范",
    "需以现行有效的官方标准",
    "应以现行有效标准为准",
    "仅供参考",
    "不构成规范依据",
    "合成示例",
    "脱敏教学语料",
    "属合成示例",
)

# 回答中常见整句免责声明（含模型改写后残句）
_ANSWER_DISCLAIMER_RES = (
    re.compile(
        r"(?:需注意|注意)[：:]\s*[^。\n]*?(?:合成示例|现行有效|仅供参考)[^。\n]*。?"
    ),
    re.compile(r"[^。\n]*合成示例[^。\n]*。?"),
    re.compile(r"[^。\n]*应以现行有效[^。\n]*。?"),
    re.compile(r"[^。\n]*脱敏教学语料[^。\n]*。?"),
    re.compile(r"[^。\n]*仅供参考[^。\n]*。?"),
)


def _scrub_disclaimer_text(text: str) -> str:
    """去掉样例库免责声明行，避免模型原样复述到回答里。"""
    if not text:
        return ""
    kept: list[str] = []
    for line in text.splitlines():
        low = line.strip().lower()
        if any(p.lower() in low or p in line for p in _DISCLAIMER_PATTERNS):
            continue
        if "sample" in low and ("official" in low or "标准" in line or "规范" in line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


# 教学脱敏标号 → 现行正式标准号（仅展示/提示，不改向量库原文）
_STANDARD_CODE_DISPLAY = {
    "CJJ/T 85-教学": "CJJ/T 85-2017",
    "CJJ/T85-教学": "CJJ/T 85-2017",
    "CJJ/T 85": "CJJ/T 85-2017",
    "GB 50180-教学": "GB 50180-2018",
    "GB50180-教学": "GB 50180-2018",
    "GB 50180": "GB 50180-2018",
    "CJJ 48-教学": "GB 51192-2016",
    "CJJ48-教学": "GB 51192-2016",
    "CJJ 48": "GB 51192-2016",
    "GB/T 51327-教学": "GB/T 51345-2018",
    "GB/T51327-教学": "GB/T 51345-2018",
    "GB/T 51327": "GB/T 51345-2018",
}


def _sanitize_cite_label(value: str | None) -> str:
    """展示/提示用：去掉脱敏教学字样，标号映射为现行正式标准号。"""
    if not value:
        return ""
    text = str(value)
    text = text.replace("（脱敏教学稿）", "").replace("(脱敏教学稿)", "")
    text = text.replace("脱敏教学稿", "")
    text = " ".join(text.split()).strip()
    mapped = _STANDARD_CODE_DISPLAY.get(text) or _STANDARD_CODE_DISPLAY.get(
        text.replace(" ", "")
    )
    if mapped:
        return mapped
    # 宽松匹配：去掉空格后再查
    compact = text.replace(" ", "")
    for key, official in _STANDARD_CODE_DISPLAY.items():
        if key.replace(" ", "") == compact:
            return official
    return text.replace("-教学", "").strip()


def _map_standard_codes_in_text(text: str) -> str:
    """把教学标号换成正式号；避免已是正式号时再拼出 2017-2017。"""
    out = text
    for key in sorted(_STANDARD_CODE_DISPLAY.keys(), key=len, reverse=True):
        official = _STANDARD_CODE_DISPLAY[key]
        if "教学" in key:
            out = out.replace(key, official)
        else:
            out = re.sub(re.escape(key) + r"(?!-\d{4})", official, out)
    # 兜底：CJJ/T 85-2017-2017 → CJJ/T 85-2017
    out = re.sub(r"-(20\d{2})-\1\b", r"-\1", out)
    return out


def _sanitize_answer_text(text: str) -> str:
    """回答出站清洗：去掉教学脱敏用语与免责句，标号换成现行正式标准号。"""
    if not text:
        return ""
    out = str(text)
    out = out.replace("（脱敏教学稿）", "").replace("(脱敏教学稿)", "")
    out = out.replace("脱敏教学稿", "")
    out = out.replace("教学口径", "")
    for pat in _ANSWER_DISCLAIMER_RES:
        out = pat.sub("", out)
    out = _map_standard_codes_in_text(out)
    out = out.replace("-教学", "")
    out = out.replace("《》", "")
    # 收紧多余空白（保留换行）
    lines = [" ".join(line.split()) for line in out.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _outbound_citation(
    cite: dict[str, Any] | None,
    text: str = "",
    query: str = "",
) -> dict[str, Any]:
    """出站引用：强制 snippet ≤200；按查询词/指标窗口截取正文；清洗教学脱敏字样。"""
    c = dict(cite or {})
    raw = text or c.get("snippet") or c.get("text") or ""
    c["snippet"] = clamp_snippet(raw, SNIPPET_MAX, query=query)
    if "title" in c:
        c["title"] = _sanitize_cite_label(c.get("title"))
    if "standard_code" in c:
        c["standard_code"] = _sanitize_cite_label(c.get("standard_code"))
    if "section" in c:
        section = _sanitize_cite_label(c.get("section"))
        if section and section in {c.get("title") or "", c.get("standard_code") or ""}:
            section = ""
        c["section"] = section
    return c


DIRECT_SYSTEM = (
    "你是景观规划智能助手。用简洁、专业的中文回答用户问题。"
    "若问题涉及具体规范条文或指标数值，请提示用户给出规范名称以便检索知识库，"
    "此时不要猜测或默写具体百分比、服务半径、宽度、坡度等数值，也不要编造 GB/CJJ/JGJ 编号。"
)

DIRECT_NO_HIT_SYSTEM = (
    "你是景观规划智能助手。知识库本次未检索到直接相关内容。"
    "请基于通用景观规划常识作答；禁止编造具体国标编号或强制条文原文。"
    "不要在回答中写入样例库、非正式规范、仅供参考、需以官方标准为准等免责声明。"
    "回答中不得出现 GB、CJJ、JGJ 等规范编号形态。"
)

RAG_SYSTEM = (
    "你是景观规划智能助手。请严格依据下方「参考资料」作答。"
    "直接给出专业结论与要点；可引用资料中的指标与条款编号。"
    "回答中的数字、百分比、标准号必须能在参考资料原文中找到；资料未出现的数字与编号一律不写。"
    "若资料不足以支撑结论，说明不确定并禁止编造条文。"
    "不要在回答中提及资料为 SAMPLE/样例、非正式规范、NOT an official standard，"
    "也不要添加「仅供参考、需以官方现行标准为准」类免责声明。"
    "禁止写「脱敏教学稿」「教学口径」「教学」等字样；规范名称用正式全称，引用处用（《规范名》条款号）标出。"
    "用中文回答。"
)


class QAState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], operator.add]
    query: str
    route: str
    retrieved_docs: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    answer: str
    thread_id: str
    intent_confidence: float


async def intent_node(state: QAState) -> dict:
    query = state.get("query") or ""
    if not query and state.get("messages"):
        last = state["messages"][-1]
        query = last.content if hasattr(last, "content") else str(last)
    clf = get_intent_classifier()
    result = await clf.classify(query)
    logger.info("qa.intent", label=result["label"], confidence=result["confidence"])
    return {
        "query": query,
        "route": result["label"],
        "intent_confidence": float(result["confidence"]),
    }


def route_after_intent(state: QAState) -> Literal["direct_answer", "retrieve"]:
    return "retrieve" if state.get("route") == "retrieve" else "direct_answer"


async def retrieve_node(state: QAState) -> dict:
    docs = await retrieve(state["query"])
    citations = [_outbound_citation(d.get("citation")) for d in docs]
    if not docs:
        logger.info("qa.retrieve_empty_fallback", query=(state.get("query") or "")[:80])
        return {
            "retrieved_docs": [],
            "citations": [],
            "route": "direct_no_hit",
        }
    return {"retrieved_docs": docs, "citations": citations, "route": "retrieve"}


async def direct_answer_node(state: QAState) -> dict:
    llm = get_llm("qa", temperature=0.3, streaming=False)
    msgs = [SystemMessage(content=DIRECT_SYSTEM), HumanMessage(content=state["query"])]
    resp = await llm.ainvoke(msgs)
    answer = _sanitize_answer_text(
        resp.content if hasattr(resp, "content") else str(resp)
    )
    return {
        "answer": answer,
        "citations": [],
        "retrieved_docs": [],
        "messages": [AIMessage(content=answer)],
    }


async def generate_node(state: QAState) -> dict:
    """与 stream_qa_answer 对齐：空检索走 DIRECT_NO_HIT，不把空上下文塞进 RAG。"""
    route = state.get("route") or "retrieve"
    docs = state.get("retrieved_docs") or []

    if route == "direct_no_hit" or not docs:
        system = DIRECT_NO_HIT_SYSTEM
        temperature = 0.3
        route = "direct_no_hit"
        citations: list[dict[str, Any]] = []
    else:
        ctx_parts = []
        for i, d in enumerate(docs, 1):
            cite = d.get("citation") or {}
            title = _sanitize_cite_label(cite.get("title"))
            code = _sanitize_cite_label(cite.get("standard_code"))
            header = f"[{i}] {title} {code}"
            body = _sanitize_answer_text(_scrub_disclaimer_text(d.get("text") or ""))
            ctx_parts.append(f"{header}\n{body}")
        system = RAG_SYSTEM + f"\n\n## 参考资料\n" + "\n\n".join(ctx_parts)
        temperature = 0.2
        citations = [_outbound_citation(d.get("citation")) for d in docs]

    llm = get_llm("qa", temperature=temperature, streaming=False)
    resp = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=state["query"])])
    answer = _sanitize_answer_text(
        resp.content if hasattr(resp, "content") else str(resp)
    )
    return {
        "answer": answer,
        "route": route,
        "citations": citations,
        "messages": [AIMessage(content=answer)],
    }


def build_qa_graph(checkpointer: MemorySaver | None = None):
    g = StateGraph(QAState)
    g.add_node("intent", intent_node)
    g.add_node("direct_answer", direct_answer_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)

    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", route_after_intent, {
        "direct_answer": "direct_answer",
        "retrieve": "retrieve",
    })
    g.add_edge("retrieve", "generate")
    g.add_edge("direct_answer", END)
    g.add_edge("generate", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


_qa_graph = None


def get_qa_graph():
    global _qa_graph
    if _qa_graph is None:
        _qa_graph = build_qa_graph()
    return _qa_graph


def _estimate_tokens(text: str) -> int:
    """粗估 token：中文约 1.5 字/token，英文约 4 字/token，取偏保守整数。"""
    if not text:
        return 0
    return max(1, int(len(text) / 1.8))


async def stream_qa_answer(
    query: str,
    thread_id: str,
    *,
    force_route: Literal["direct", "retrieve"] | None = None,
):
    """异步生成器：意图 → 可选检索 → 流式 token。

    Yields dict events: route / citation / token / done / error
    done.route 可为 direct | retrieve | direct_no_hit（可观测，不回写为 direct）。
    force_route 仅供评测对照（Token A/B、纯 LLM）；生产 HTTP 不传。
    """
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    used_vector = False
    hit_count = 0
    intent_label = "direct"
    confidence = 0.0
    prompt_tokens = 0
    completion_tokens = 0

    if force_route is not None:
        intent_label = force_route
        confidence = 1.0
        timings["intent_ms"] = 0.0
    else:
        clf = get_intent_classifier()
        try:
            t_intent = time.perf_counter()
            intent = await clf.classify(query)
            timings["intent_ms"] = (time.perf_counter() - t_intent) * 1000
        except Exception as e:
            logger.error("qa.classify_failed", error=str(e), exc_info=True)
            yield {"event": "error", "data": {"message": str(e)}}
            return
        intent_label = intent["label"]
        confidence = float(intent["confidence"])

    route = intent_label
    yield {
        "event": "route",
        "data": {
            "route": route,
            "confidence": confidence,
            **({"forced": True} if force_route else {}),
        },
    }
    record_metric(
        route=route,
        tool="intent",
        step="qa_intent",
        success=True,
        latency_ms=timings.get("intent_ms", 0),
        detail={
            "confidence": confidence,
            "label": intent_label,
            "forced": bool(force_route),
        },
    )

    citations: list[dict] = []
    docs: list[dict] = []
    retrieve_timings: dict[str, float] = {}
    if route == "retrieve":
        used_vector = True
        try:
            t_ret = time.perf_counter()
            docs, retrieve_timings = await retrieve(query, return_timings=True)
            timings["retrieve_ms"] = (time.perf_counter() - t_ret) * 1000
            timings.update({k: v for k, v in retrieve_timings.items() if k.endswith("_ms")})
            hit_count = len(docs)
            citations = [
                _outbound_citation(d.get("citation"), d.get("text") or "", query=query)
                for d in docs
            ]
            for c in citations:
                yield {"event": "citation", "data": c}
            if not docs:
                logger.info("qa.retrieve_empty_fallback", query=query[:80])
                route = "direct_no_hit"
                yield {
                    "event": "route",
                    "data": {"route": "direct_no_hit", "reason": "empty_retrieve"},
                }
        except Exception as e:
            logger.error("qa.retrieve_failed", error=str(e), exc_info=True)
            yield {"event": "error", "data": {"message": f"检索失败: {e}"}}
            route = "direct_no_hit"

    if route == "retrieve" and docs:
        ctx_parts = []
        for i, d in enumerate(docs, 1):
            cite = d.get("citation") or {}
            title = _sanitize_cite_label(cite.get("title"))
            code = _sanitize_cite_label(cite.get("standard_code"))
            header = f"[{i}] {title} {code}"
            body = _sanitize_answer_text(_scrub_disclaimer_text(d.get("text") or ""))
            ctx_parts.append(f"{header}\n{body}")
        system = RAG_SYSTEM + "\n\n## 参考资料\n" + "\n\n".join(ctx_parts)
        temperature = 0.2
    elif route == "direct_no_hit":
        system = DIRECT_NO_HIT_SYSTEM
        temperature = 0.3
        # 保持 direct_no_hit，便于命中率 / 拒答统计（不回写为 direct）
    else:
        system = DIRECT_SYSTEM
        temperature = 0.3

    prompt_tokens = _estimate_tokens(system) + _estimate_tokens(query)
    llm = get_llm("qa", temperature=temperature, streaming=True)
    full: list[str] = []
    first_token_ms: float | None = None
    try:
        t_llm = time.perf_counter()
        async for chunk in llm.astream(
            [SystemMessage(content=system), HumanMessage(content=query)]
        ):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - t0) * 1000
                    timings["llm_ttft_ms"] = (time.perf_counter() - t_llm) * 1000
                    timings["e2e_ttft_ms"] = first_token_ms
                full.append(token)
                yield {"event": "token", "data": {"token": token}}
    except Exception as e:
        logger.error("qa.stream_failed", error=str(e), exc_info=True)
        yield {"event": "error", "data": {"message": str(e)}}
        return

    answer = _sanitize_answer_text("".join(full))
    completion_tokens = _estimate_tokens(answer)
    e2e_done_ms = (time.perf_counter() - t0) * 1000
    timings["e2e_done_ms"] = e2e_done_ms

    metrics = {
        "intent_label": intent_label,
        "confidence": confidence,
        "used_vector_store": used_vector,
        "hit_count": hit_count,
        "direct_no_hit": route == "direct_no_hit",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "timings_ms": {k: round(v, 2) for k, v in timings.items()},
    }
    record_metric(
        route=route,
        tool="qa",
        step="qa_e2e",
        success=True,
        latency_ms=e2e_done_ms,
        tokens=prompt_tokens + completion_tokens,
        detail=metrics,
    )
    if first_token_ms is not None:
        record_metric(
            route=route,
            tool="llm",
            step="qa_first_token",
            success=True,
            latency_ms=first_token_ms,
            tokens=completion_tokens,
            detail={"used_vector_store": used_vector, "hit_count": hit_count},
        )

    yield {
        "event": "done",
        "data": {
            "answer": answer,
            "route": route,
            "citations": citations,
            "thread_id": thread_id,
            "metrics": metrics,
        },
    }
