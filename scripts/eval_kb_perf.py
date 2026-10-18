# scripts/eval_kb_perf.py
# 首 token P95、Token A/B（强制 retrieve vs 意图分流）、相对纯 LLM 质量提升

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _keyword_score(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    hit = sum(1 for k in keywords if k in (answer or ""))
    return hit / len(keywords)


async def _run_once(
    query: str,
    thread_id: str,
    force_route: str | None = None,
) -> dict:
    from backend.agents.qa.graph import stream_qa_answer

    answer = ""
    route = "direct"
    metrics: dict = {}
    citations: list = []
    async for ev in stream_qa_answer(
        query, thread_id=thread_id, force_route=force_route
    ):
        if ev["event"] == "token":
            answer += ev["data"].get("token", "")
        elif ev["event"] == "citation":
            citations.append(ev["data"])
        elif ev["event"] == "done":
            data = ev["data"]
            answer = data.get("answer") or answer
            route = data.get("route") or route
            metrics = data.get("metrics") or {}
            citations = data.get("citations") or citations
    timings = metrics.get("timings_ms") or {}
    return {
        "answer": answer,
        "route": route,
        "citations": citations,
        "total_tokens": int(metrics.get("total_tokens") or 0),
        "e2e_ttft_ms": timings.get("e2e_ttft_ms"),
        "e2e_done_ms": timings.get("e2e_done_ms"),
        "metrics": metrics,
    }


async def main(
    retrieve_file: Path,
    intent_file: Path,
    out: Path | None,
    ttft_n: int,
    warmups: int,
    quality_limit: int | None,
    token_limit: int | None,
) -> int:
    from backend.core.logger import configure_logging
    from backend.core.metrics import percentile
    from backend.rag.embeddings import get_embedder
    from backend.rag.milvus_client import connect_milvus

    configure_logging()
    connect_milvus()
    get_embedder().load()

    retrieve_rows = _load_jsonl(retrieve_file)
    intent_rows = _load_jsonl(intent_file)

    # --- warmups ---
    warm_q = (retrieve_rows[0]["query"] if retrieve_rows else "绿地率")
    for i in range(warmups):
        await _run_once(warm_q, thread_id=f"eval-perf-warm-{i}", force_route="retrieve")

    # --- first-token P95 ---
    ttft_pool = [r["query"] for r in retrieve_rows[: max(ttft_n, 1)]]
    while len(ttft_pool) < ttft_n:
        ttft_pool.extend(ttft_pool)
    ttft_pool = ttft_pool[:ttft_n]
    ttft_values: list[float] = []
    for i, q in enumerate(ttft_pool):
        r = await _run_once(q, thread_id=f"eval-perf-ttft-{i}", force_route="retrieve")
        if r["e2e_ttft_ms"] is not None:
            ttft_values.append(float(r["e2e_ttft_ms"]))
    ttft_p95 = percentile(ttft_values, 95) if ttft_values else None
    ttft_pass = ttft_p95 is not None and ttft_p95 < 3000.0
    print(
        f"first-token P95: {ttft_p95:.1f} ms over {len(ttft_values)} samples "
        f"(target <3000 ms)"
        if ttft_p95 is not None
        else "first-token P95: no samples"
    )

    # --- Token A/B ---
    # A = force retrieve; B = natural intent. Use mixed intent set for fair routing savings.
    token_rows = intent_rows[: token_limit] if token_limit else intent_rows
    if not token_rows:
        token_rows = retrieve_rows[: token_limit or 30]
    tokens_a = 0
    tokens_b = 0
    token_details = []
    for i, row in enumerate(token_rows):
        q = row["query"]
        ra = await _run_once(q, thread_id=f"eval-perf-tokA-{i}", force_route="retrieve")
        rb = await _run_once(q, thread_id=f"eval-perf-tokB-{i}", force_route=None)
        tokens_a += ra["total_tokens"]
        tokens_b += rb["total_tokens"]
        token_details.append(
            {
                "id": row.get("id"),
                "query": q,
                "tokens_a_force_retrieve": ra["total_tokens"],
                "tokens_b_intent": rb["total_tokens"],
                "route_b": rb["route"],
            }
        )
    token_reduction = (
        (tokens_a - tokens_b) / tokens_a if tokens_a > 0 else 0.0
    )
    token_pass = token_reduction >= 0.25
    print(
        f"token reduction (A force-retrieve vs B intent): "
        f"{tokens_a} → {tokens_b} = {token_reduction:.2%} (target ≥25%)"
    )

    # --- RAG vs pure LLM quality ---
    quality_rows = retrieve_rows[: quality_limit] if quality_limit else retrieve_rows
    pure_scores: list[float] = []
    rag_scores: list[float] = []
    quality_details = []
    for i, row in enumerate(quality_rows):
        kws = row.get("expect_keywords") or []
        pure = await _run_once(
            row["query"], thread_id=f"eval-perf-pure-{i}", force_route="direct"
        )
        rag = await _run_once(
            row["query"], thread_id=f"eval-perf-rag-{i}", force_route="retrieve"
        )
        sp = _keyword_score(pure["answer"], kws)
        sr = _keyword_score(rag["answer"], kws)
        pure_scores.append(sp)
        rag_scores.append(sr)
        quality_details.append(
            {
                "id": row.get("id"),
                "query": row["query"],
                "keywords": kws,
                "pure_score": sp,
                "rag_score": sr,
            }
        )
    pure_avg = sum(pure_scores) / len(pure_scores) if pure_scores else 0.0
    rag_avg = sum(rag_scores) / len(rag_scores) if rag_scores else 0.0
    if pure_avg > 0:
        lift = (rag_avg - pure_avg) / pure_avg
    else:
        # 纯 LLM 全 0 时：RAG 有分则视为大幅提升，用 rag_avg 作为相对抬升代理
        lift = rag_avg if rag_avg > 0 else 0.0
    lift_pass = lift >= 0.40
    print(
        f"quality lift vs pure LLM: pure={pure_avg:.2%} rag={rag_avg:.2%} "
        f"lift={lift:.2%} (target ≥40%)"
    )

    overall = bool(ttft_pass and token_pass and lift_pass)
    payload = {
        "metric": "perf",
        "first_token": {
            "samples": len(ttft_values),
            "p95_ms": ttft_p95,
            "target_ms": 3000,
            "pass": ttft_pass,
            "values_ms": ttft_values,
        },
        "token_ab": {
            "n": len(token_rows),
            "tokens_a_force_retrieve": tokens_a,
            "tokens_b_intent": tokens_b,
            "reduction": token_reduction,
            "target": 0.25,
            "pass": token_pass,
            "details": token_details,
        },
        "vs_pure_llm": {
            "n": len(quality_rows),
            "pure_avg": pure_avg,
            "rag_avg": rag_avg,
            "lift": lift,
            "target": 0.40,
            "pass": lift_pass,
            "details": quality_details,
        },
        "pass": overall,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 0 if overall else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retrieve-file",
        type=Path,
        default=ROOT / "evals" / "kb" / "retrieve_hit.jsonl",
    )
    parser.add_argument(
        "--intent-file",
        type=Path,
        default=ROOT / "evals" / "kb" / "intent_golden.jsonl",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--ttft-n", type=int, default=30, help="预热后首 token 样本数")
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument(
        "--quality-limit",
        type=int,
        default=None,
        help="RAG vs 纯 LLM 题数，默认全量 retrieve_hit",
    )
    parser.add_argument(
        "--token-limit",
        type=int,
        default=None,
        help="Token A/B 题数，默认全量 intent_golden",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main(
                args.retrieve_file,
                args.intent_file,
                args.out,
                args.ttft_n,
                args.warmups,
                args.quality_limit,
                args.token_limit,
            )
        )
    )
