# scripts/eval_kb_pilot.py
# 业务试点：查规范时间降幅 = (manual - system) / manual

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(path: Path, out: Path | None) -> int:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    filled = []
    incomplete = []
    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        question = (row.get("question") or "").strip()
        m_raw = (row.get("manual_minutes") or "").strip()
        s_raw = (row.get("system_assist_minutes") or "").strip()
        if not m_raw or not s_raw:
            incomplete.append({"case_id": case_id, "question": question})
            continue
        try:
            manual = float(m_raw)
            system = float(s_raw)
        except ValueError:
            incomplete.append({"case_id": case_id, "question": question, "error": "non-numeric"})
            continue
        if manual <= 0:
            incomplete.append({"case_id": case_id, "question": question, "error": "manual<=0"})
            continue
        reduction = (manual - system) / manual
        filled.append(
            {
                "case_id": case_id,
                "question": question,
                "manual_minutes": manual,
                "system_assist_minutes": system,
                "reduction": reduction,
                "notes": row.get("notes") or "",
            }
        )

    n = len(filled)
    avg_manual = sum(r["manual_minutes"] for r in filled) / n if n else 0.0
    avg_system = sum(r["system_assist_minutes"] for r in filled) / n if n else 0.0
    # 口径：平均耗时降幅（先平均再算），与逐题平均降幅一并报告
    overall_reduction = (
        (avg_manual - avg_system) / avg_manual if avg_manual > 0 else 0.0
    )
    mean_case_reduction = (
        sum(r["reduction"] for r in filled) / n if n else 0.0
    )
    pilot_pass = n >= 20 and overall_reduction >= 0.70

    print(f"pilot filled={n} incomplete={len(incomplete)} (need ≥20)")
    print(
        f"avg manual={avg_manual:.2f} min, avg system={avg_system:.2f} min, "
        f"overall reduction={overall_reduction:.2%} (target ≥70%)"
    )
    print(f"mean per-case reduction={mean_case_reduction:.2%}")

    payload = {
        "metric": "pilot_lookup_time",
        "filled": n,
        "incomplete": incomplete,
        "avg_manual_minutes": avg_manual,
        "avg_system_assist_minutes": avg_system,
        "overall_reduction": overall_reduction,
        "mean_case_reduction": mean_case_reduction,
        "target": 0.70,
        "min_cases": 20,
        "pass": pilot_pass,
        "cases": filled,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 0 if pilot_pass else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=ROOT / "evals" / "kb" / "pilot_template.csv",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(main(args.file, args.out))
