# scripts/eval_kb_judge.py
# 引用对齐与幻觉规则检查
# - 默认：对已有 answer/citations 做离线规则（JSONL 可含 answer/citations 字段）
# - --live：调用 stream_qa_answer 生成后再判

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CODE_RE = re.compile(
    r"(?:GB(?:/T)?|CJJ(?:/T)?|JGJ(?:/T)?)\s*[\d\-教学]+",
    re.IGNORECASE,
)


def citation_blob(citations: list[dict]) -> str:
    parts = []
    for c in citations or []:
        parts.append(
            " ".join(
                str(c.get(k) or "")
                for k in ("title", "standard_code", "section", "page", "snippet", "text")
            )
        )
    return "\n".join(parts)


def citations_meta_ok(citations: list[dict]) -> bool:
    if not citations:
        return False
    for c in citations:
        if not (c.get("title") or c.get("standard_code")):
            return False
        snip = c.get("snippet") or ""
        if len(snip) > 200:
            return False
    return True


def judge_row(row: dict, answer: str, citations: list[dict], route: str | None) -> dict:
    """返回 {hallucination: bool, citation_ok: bool|None, reasons: [...]}。"""
    reasons: list[str] = []
    hallucination = False
    citation_ok: bool | None = None
    blob = citation_blob(citations)

    if row.get("forbid_codes"):
        codes = CODE_RE.findall(answer or "")
        if codes:
            hallucination = True
            reasons.append(f"拒答集出现编号: {codes}")
        if route and route not in ("direct_no_hit", "direct"):
            reasons.append(f"拒答期望 direct_no_hit/direct，实际 route={route}")

    if row.get("must_support_in_citations"):
        citation_ok = citations_meta_ok(citations)
        if not citation_ok:
            reasons.append("引用元数据不完整或 snippet>200")
        codes_in_answer = CODE_RE.findall(answer or "")
        for code in codes_in_answer:
            if code not in blob and code.replace(" ", "") not in blob.replace(" ", ""):
                # 编造未出现在引用中的标准号 → 幻觉；同时记引用失败
                hallucination = True
                citation_ok = False
                reasons.append(f"答案编号未出现在引用: {code}")
        # 数值/关键词：计入引用准确率，不计入幻觉率（口径分离）
        for kw in row.get("expect_keywords") or []:
            if kw in (answer or "") and kw not in blob:
                citation_ok = False
                reasons.append(f"答案要点未在引用中: {kw}")


    return {
        "id": row.get("id"),
        "hallucination": hallucination,
        "citation_ok": citation_ok,
        "reasons": reasons,
        "route": route,
    }


async def _live_answer(query: str) -> tuple[str, list[dict], str]:
    from backend.agents.qa.graph import stream_qa_answer

    answer = ""
    citations: list[dict] = []
    route = "direct"
    async for ev in stream_qa_answer(query, thread_id="eval-judge"):
        if ev["event"] == "done":
            data = ev["data"]
            answer = data.get("answer") or answer
            citations = data.get("citations") or citations
            route = data.get("route") or route
        elif ev["event"] == "token":
            answer += ev["data"].get("token", "")
        elif ev["event"] == "citation":
            citations.append(ev["data"])
    return answer, citations, route


async def main(path: Path, live: bool, limit: int | None, out: Path | None) -> int:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if limit:
        rows = rows[:limit]

    if live:
        from backend.core.logger import configure_logging
        from backend.rag.embeddings import get_embedder
        from backend.rag.milvus_client import connect_milvus

        configure_logging()
        connect_milvus()
        get_embedder().load()

    results = []
    for row in rows:
        if live:
            answer, citations, route = await _live_answer(row["query"])
        else:
            answer = row.get("answer") or ""
            citations = row.get("citations") or []
            route = row.get("route")
            if not answer and not live:
                # 无答案的纯规则集：仅统计可判定项
                results.append(
                    {
                        "id": row.get("id"),
                        "skipped": True,
                        "note": "offline needs answer/citations; use --live",
                    }
                )
                continue
        results.append(judge_row(row, answer, citations, route))

    judged = [r for r in results if not r.get("skipped")]
    hallu_n = sum(1 for r in judged if r.get("hallucination"))
    cite_ok = [r for r in judged if r.get("citation_ok") is True]
    cite_bad = [r for r in judged if r.get("citation_ok") is False]
    total = len(judged)
    hallu_rate = hallu_n / total if total else 0.0
    cite_total = len(cite_ok) + len(cite_bad)
    cite_acc = (len(cite_ok) / cite_total) if cite_total else None

    print(f"judged={total} skipped={len(results) - total}")
    print(f"hallucination rate: {hallu_n}/{total} = {hallu_rate:.2%} (target <3%)")
    if cite_total:
        print(
            f"citation support accuracy: {len(cite_ok)}/{cite_total} = "
            f"{cite_acc:.2%} (target ≥95%)"
        )
    for r in judged:
        if r.get("hallucination") or r.get("citation_ok") is False:
            print(f"  ! {r.get('id')} {r.get('reasons')}")

    hallu_pass = hallu_rate < 0.03
    cite_pass = True if cite_acc is None else cite_acc >= 0.95
    payload = {
        "metric": "judge",
        "judged": total,
        "skipped": len(results) - total,
        "hallucination": {
            "count": hallu_n,
            "total": total,
            "rate": hallu_rate,
            "target": 0.03,
            "pass": hallu_pass,
        },
        "citation": {
            "ok": len(cite_ok),
            "total": cite_total,
            "accuracy": cite_acc,
            "target": 0.95,
            "pass": cite_pass,
        },
        "results": results,
        "pass": hallu_pass and cite_pass,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    if not live and any(r.get("skipped") for r in results):
        print("提示：离线模式需题集含 answer/citations，或加 --live 现网评测。")
        return 0
    return 0 if (hallu_pass and cite_pass) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=ROOT / "evals" / "kb" / "hallucination.jsonl",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.file, args.live, args.limit, args.out)))
