# scripts/run_plan_pilot_batch.py
# 生成规划通试点 PDF，可选本地评估并写出墙钟 batch.jsonl

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def prepare_fixtures(directory: Path, count: int = 10) -> list[Path]:
    from tests.fixtures.pdf_factory import build_plan_fixtures

    directory.mkdir(parents=True, exist_ok=True)
    text_paths, _scans = build_plan_fixtures(directory)
    selected = text_paths[:count]
    print(f"prepared {len(selected)} fixtures")
    return selected


async def evaluate_local(paths: list[Path], out_jsonl: Path) -> list[dict]:
    from backend.agents.eval.graph import run_plan_evaluation

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for path in paths:
            started = time.perf_counter()
            try:
                result = await run_plan_evaluation(str(path), file_name=path.name)
                wall_min = (time.perf_counter() - started) / 60.0
                tokens = 0
                for ev in result.get("metrics") or []:
                    if ev.get("tokens") is not None:
                        tokens += int(ev["tokens"])
                row = {
                    "plan_file": path.name,
                    "ok": True,
                    "system_wall_min": round(wall_min, 3),
                    "overall_score": result.get("overall_score"),
                    "steps_complete": result.get("steps_complete"),
                    "tokens_dim_retry": tokens,
                    "error": None,
                }
            except Exception as exc:
                wall_min = (time.perf_counter() - started) / 60.0
                row = {
                    "plan_file": path.name,
                    "ok": False,
                    "system_wall_min": round(wall_min, 3),
                    "overall_score": None,
                    "steps_complete": False,
                    "tokens_dim_retry": 0,
                    "error": str(exc),
                }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{path.name} ok={row['ok']} wall_min={row['system_wall_min']} score={row['overall_score']}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dir",
        type=Path,
        default=ROOT / "evals" / "plan" / "fixtures",
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--live", action="store_true", help="本地跑评估图并写 batch.jsonl")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "evals" / "plan" / "results" / "batch.jsonl",
    )
    args = parser.parse_args()

    paths = prepare_fixtures(args.dir, args.count)
    if args.prepare_only and not args.live:
        meta = {
            "prepared": [p.name for p in paths],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = args.dir / "manifest.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {meta_path}")
        return 0

    if args.live:
        rows = asyncio.run(evaluate_local(paths, args.out))
        ok = sum(1 for r in rows if r["ok"])
        print(f"batch done ok={ok}/{len(rows)} -> {args.out}")
        return 0 if ok == len(rows) else 1

    print("nothing to do; pass --prepare-only and/or --live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
