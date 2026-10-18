# backend/rag/retriever.py
# retrieve → rerank → citations

from __future__ import annotations

import time
from typing import Any, Literal, overload

from backend.config import get_settings
from backend.core.logger import get_logger
from backend.core.metrics import record_metric
from backend.rag.embeddings import get_embedder
from backend.rag.milvus_client import hybrid_search
from backend.rag.reranker import get_reranker

logger = get_logger(__name__)

SNIPPET_MAX = 200


def clamp_snippet(text: str, limit: int = SNIPPET_MAX, query: str = "") -> str:
    """出站 snippet 强制截断；优先覆盖查询词，否则取指标密度最高窗口。"""
    import re

    text = text or ""
    if len(text) <= limit:
        return text

    def _window_at(center: int) -> str:
        start = min(max(0, center - limit // 4), max(0, len(text) - limit))
        return text[start : start + limit]

    # 查询词命中：取第一个较长子串所在窗口
    q = (query or "").strip()
    if q:
        for n in (min(8, len(q)), 4, 2):
            if len(q) < n:
                continue
            for i in range(0, len(q) - n + 1):
                sub = q[i : i + n]
                if sub.isspace():
                    continue
                pos = text.find(sub)
                if pos >= 0:
                    return _window_at(pos)

    metric = re.compile(
        r"\d+(?:\.\d+)?%"
        r"|\d+(?:\.\d+)?\s*米"
        r"|\d+\s*～\s*\d+"
        r"|(?<![.\d])\d{2,}(?:\.\d+)?(?![.\d])"
    )
    best_score, best_start = -1, 0
    step = max(1, limit // 5)
    for start in range(0, len(text) - limit + 1, step):
        score = len(metric.findall(text[start : start + limit]))
        if score > best_score:
            best_score, best_start = score, start
    for m in metric.finditer(text):
        start = min(max(0, m.start() - limit // 4), max(0, len(text) - limit))
        score = len(metric.findall(text[start : start + limit]))
        if score > best_score:
            best_score, best_start = score, start
    return text[best_start : best_start + limit]


@overload
async def retrieve(
    query: str,
    top_k: int | None = None,
    top_n: int | None = None,
    *,
    return_timings: Literal[False] = False,
) -> list[dict[str, Any]]: ...


@overload
async def retrieve(
    query: str,
    top_k: int | None = None,
    top_n: int | None = None,
    *,
    return_timings: Literal[True],
) -> tuple[list[dict[str, Any]], dict[str, float]]: ...


async def retrieve(
    query: str,
    top_k: int | None = None,
    top_n: int | None = None,
    *,
    return_timings: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, float]]:
    """Hybrid retrieve + rerank. Returns citation-ready docs."""
    settings = get_settings()
    k = top_k or settings.rag_top_k
    n = top_n or settings.rag_rerank_top_n
    timings: dict[str, float] = {}

    started = time.perf_counter()
    try:
        t_embed = time.perf_counter()
        embedder = get_embedder()
        qvec = await embedder.encode_query(query)
        timings["embed_ms"] = (time.perf_counter() - t_embed) * 1000

        t_milvus = time.perf_counter()
        hits = hybrid_search(qvec["dense"], qvec["sparse"], top_k=k)
        timings["milvus_ms"] = (time.perf_counter() - t_milvus) * 1000
    except Exception as exc:
        record_metric(
            tool="retrieve",
            step="retrieve",
            success=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            detail={"error": type(exc).__name__},
        )
        raise
    record_metric(
        tool="retrieve",
        step="retrieve",
        success=True,
        latency_ms=(time.perf_counter() - started) * 1000,
        detail={
            "hits": len(hits or []),
            "embed_ms": round(timings.get("embed_ms", 0), 2),
            "milvus_ms": round(timings.get("milvus_ms", 0), 2),
        },
    )
    if not hits:
        logger.info("retriever.no_hits", query=query[:80])
        empty: list[dict[str, Any]] = []
        return (empty, timings) if return_timings else empty

    passages = [h["text"] or "" for h in hits]
    rerank_started = time.perf_counter()
    if settings.rag_enable_rerank:
        try:
            reranker = get_reranker()
            ranked = await reranker.rerank(query, passages, top_n=n)
        except Exception as exc:
            record_metric(
                tool="rerank",
                step="rerank",
                success=False,
                latency_ms=(time.perf_counter() - rerank_started) * 1000,
                detail={"error": type(exc).__name__},
            )
            raise
    else:
        # Milvus hybrid 已 RRF 排序，直接截断 top_n，避免 CPU 精排拖垮首 token
        ranked = [
            (i, float(hits[i].get("score") or (len(hits) - i)))
            for i in range(min(n, len(hits)))
        ]
    timings["rerank_ms"] = (time.perf_counter() - rerank_started) * 1000
    record_metric(
        tool="rerank",
        step="rerank",
        success=True,
        latency_ms=timings["rerank_ms"],
        detail={"kept": len(ranked), "enabled": settings.rag_enable_rerank},
    )

    results: list[dict[str, Any]] = []
    for idx, score in ranked:
        h = hits[idx]
        snippet = clamp_snippet(h.get("text") or "", query=query)
        results.append(
            {
                "text": h["text"],
                "title": h.get("title"),
                "section": h.get("section"),
                "page": h.get("page"),
                "standard_code": h.get("standard_code"),
                "doc_id": h.get("doc_id"),
                "score": float(score),
                "citation": {
                    "title": h.get("title"),
                    "standard_code": h.get("standard_code"),
                    "page": h.get("page"),
                    "section": h.get("section"),
                    "snippet": snippet,
                },
            }
        )
    logger.info("retriever.ok", query=query[:80], hits=len(hits), kept=len(results))
    return (results, timings) if return_timings else results
