# scripts/eval_kb_retrieve.py
# 检索增强命中率：Top-5 中 standard_code 或关键词命中标注

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _is_hit(docs: list[dict], row: dict) -> bool:
    code = (row.get("expect_standard_code") or "").strip()
    keywords = row.get("expect_keywords") or []
    for d in docs:
        cite = d.get("citation") or {}
        text = (d.get("text") or "") + (cite.get("snippet") or "")
        got_code = (cite.get("standard_code") or d.get("standard_code") or "").strip()
        if code and got_code == code:
            if not keywords or any(k in text for k in keywords):
                return True
        if keywords and any(k in text for k in keywords):
            # 无 code 标注时仅关键词；有 code 时上面已要求 code 一致
            if not code:
                return True
    return False


async def main(path: Path, limit: int | None, out: Path | None) -> int:
    from backend.core.logger import configure_logging
    from backend.rag.embeddings import get_embedder
    from backend.rag.milvus_client import connect_milvus
    from backend.rag.retriever import retrieve

    configure_logging()
    connect_milvus()
    get_embedder().load()

    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if limit:
        rows = rows[:limit]

    evaluated = [
        r for r in rows if (r.get("expect_standard_code") or "").upper() != "SAMPLE"
    ]
    hit = 0
    misses = []
    for row in evaluated:
        docs = await retrieve(row["query"])
        ok = _is_hit(docs, row)
        if ok:
            hit += 1
        else:
            misses.append(
                {
                    "id": row.get("id"),
                    "query": row["query"],
                    "expect": row.get("expect_standard_code"),
                    "top": [
                        (d.get("standard_code"), (d.get("text") or "")[:60]) for d in docs[:3]
                    ],
                }
            )

    total = len(evaluated)
    acc = hit / total if total else 0.0
    print(f"retrieve hit rate: {hit}/{total} = {acc:.2%} (target ≥90%; exclude SAMPLE)")
    for m in misses[:8]:
        print(f"  miss {m['id']}: {m['query'][:40]} expect={m['expect']} top={m['top']}")

    payload = {
        "metric": "retrieve_hit_rate",
        "hit": hit,
        "total": total,
        "accuracy": acc,
        "target": 0.9,
        "pass": acc >= 0.9,
        "misses": misses,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 0 if acc >= 0.9 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=ROOT / "evals" / "kb" / "retrieve_hit.jsonl",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.file, args.limit, args.out)))
