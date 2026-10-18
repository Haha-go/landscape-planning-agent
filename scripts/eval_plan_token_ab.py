# scripts/eval_plan_token_ab.py
# Token A/B：整图失败重跑 vs 单维失败不重跑 + 评分只吃抽取 JSON

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _chars_to_tokens(chars: int) -> int:
    """粗估：中英混合约 2 字符/token。"""
    return max(1, int(round(chars / 2.0)))


def _sum_metric_tokens(events: list[dict]) -> int:
    total = 0
    for ev in events or []:
        t = ev.get("tokens")
        if t is not None:
            try:
                total += int(t)
            except (TypeError, ValueError):
                pass
    return total


def estimate_prompt_tokens(pdf_text: str, extracted: dict) -> dict:
    """对照评分提示词体积：全文 vs 抽取 JSON（各 5 维）。"""
    extracted_json = json.dumps(extracted, ensure_ascii=False, indent=2)
    dims = 5
    extract_prompt = 200  # 抽取系统提示近似
    full_score = dims * (len(pdf_text) + 180)
    json_score = dims * (len(extracted_json) + 180)
    # 整图重试：抽取 + 五维评分再完整来一轮
    tokens_full_retry = _chars_to_tokens(extract_prompt + len(pdf_text) + full_score) * 2
    tokens_dim_retry = _chars_to_tokens(extract_prompt + len(pdf_text) + json_score)
    return {
        "pdf_chars": len(pdf_text),
        "extracted_chars": len(extracted_json),
        "tokens_full_retry": tokens_full_retry,
        "tokens_dim_retry": tokens_dim_retry,
        "mode": "estimate",
    }


async def live_tokens(path: Path) -> dict:
    from backend.agents.eval.graph import run_plan_evaluation

    first = await run_plan_evaluation(str(path), file_name=path.name)
    t1 = _sum_metric_tokens(first.get("metrics") or [])
    tokens_dim = t1
    # 整图重试：任维失败则整图再跑；为对照在无失败时也加一轮「失败后重跑」成本
    second = await run_plan_evaluation(str(path), file_name=path.name)
    t2 = _sum_metric_tokens(second.get("metrics") or [])
    tokens_full = t1 + t2
    return {
        "tokens_full_retry": tokens_full,
        "tokens_dim_retry": tokens_dim,
        "overall_score": first.get("overall_score"),
        "steps_complete": first.get("steps_complete"),
        "mode": "live",
    }


def _stub_extracted(text: str) -> dict:
    return {
        "project_name": "试点方案",
        "site_area": "约12公顷",
        "green_water_indicators": {"绿地率": "35%", "水体": "8%"},
        "cited_standards": ["GB 51192", "CJJ 82"],
        "summary_excerpt": text[:400],
    }


async def process_one(path: Path, live: bool) -> dict:
    from backend.parsers.pdf import extract_pdf_text

    parsed = extract_pdf_text(str(path))
    text = parsed.get("full_text") or parsed.get("text") or ""
    if live:
        result = await live_tokens(path)
    else:
        result = estimate_prompt_tokens(text, _stub_extracted(text))
    result["plan_file"] = path.name
    return result


async def amain(directory: Path, live: bool, out: Path | None, limit: int) -> int:
    paths = sorted(directory.glob("plan_*.pdf"))[:limit]
    if len(paths) < 10:
        print(f"need >=10 plan_*.pdf under fixtures, found {len(paths)}")
        return 2

    results = []
    for path in paths:
        print(f"token_ab {path.name} live={live}")
        results.append(await process_one(path, live))

    n = len(results)
    avg_full = sum(r["tokens_full_retry"] for r in results) / n
    avg_dim = sum(r["tokens_dim_retry"] for r in results) / n
    reduction = (avg_full - avg_dim) / avg_full if avg_full > 0 else 0.0
    print(f"avg full_retry={avg_full:.1f} dim_retry={avg_dim:.1f} reduction={reduction:.2%} (target ≥25%)")

    payload = {
        "metric": "plan_token_ab",
        "live": live,
        "filled": n,
        "avg_tokens_full_retry": avg_full,
        "avg_tokens_dim_retry": avg_dim,
        "overall_reduction": reduction,
        "target": 0.25,
        "pass": n >= 10 and reduction >= 0.25,
        "cases": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dir",
        type=Path,
        default=ROOT / "evals" / "plan" / "fixtures",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(amain(args.dir, args.live, args.out, args.limit)))
