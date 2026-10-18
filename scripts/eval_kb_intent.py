# scripts/eval_kb_intent.py
# 意图分类准确率：读取 evals/kb/intent_golden.jsonl，调用 IntentClassifier

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main(path: Path, limit: int | None, out: Path | None) -> int:
    from backend.core.logger import configure_logging
    from backend.models.intent import get_intent_classifier

    configure_logging()
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if limit:
        rows = rows[:limit]

    clf = get_intent_classifier()
    hit = 0
    misses: list[dict] = []
    for row in rows:
        result = await clf.classify(row["query"])
        ok = result["label"] == row["label"]
        if ok:
            hit += 1
        else:
            misses.append(
                {
                    "id": row.get("id"),
                    "query": row["query"],
                    "expected": row["label"],
                    "got": result["label"],
                    "confidence": result["confidence"],
                }
            )

    total = len(rows)
    acc = hit / total if total else 0.0
    print(f"intent accuracy: {hit}/{total} = {acc:.2%} (target ≥95%)")
    if misses[:10]:
        print("sample misses:")
        for m in misses[:10]:
            print(f"  {m['id']}: expect={m['expected']} got={m['got']} q={m['query'][:40]}")

    payload = {
        "metric": "intent_accuracy",
        "hit": hit,
        "total": total,
        "accuracy": acc,
        "target": 0.95,
        "pass": acc >= 0.95,
        "misses": misses,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 0 if acc >= 0.95 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=ROOT / "evals" / "kb" / "intent_golden.jsonl",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.file, args.limit, args.out)))
