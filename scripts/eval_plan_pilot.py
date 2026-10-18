# scripts/eval_plan_pilot.py
# §13.2 业务试点汇总：初审/复核/周期/Token 降幅 + 平均绝对改分差

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGETS = {
    "initial_time": 0.50,
    "rereview": 0.50,
    "cycle": 0.60,
    "token": 0.25,
}
MIN_CASES = 10


def _f(row: dict, key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _reduction(baseline: float, improved: float) -> float:
    if baseline <= 0:
        return 0.0
    return (baseline - improved) / baseline


def main(path: Path, out: Path | None) -> int:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    filled: list[dict] = []
    incomplete: list[dict] = []

    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        required = {
            "manual_initial_min": _f(row, "manual_initial_min"),
            "system_wall_min": _f(row, "system_wall_min"),
            "manual_rereview_full_min": _f(row, "manual_rereview_full_min"),
            "manual_rereview_diff_min": _f(row, "manual_rereview_diff_min"),
            "baseline_cycle_hours": _f(row, "baseline_cycle_hours"),
            "system_cycle_hours": _f(row, "system_cycle_hours"),
            "tokens_full_retry": _f(row, "tokens_full_retry"),
            "tokens_dim_retry": _f(row, "tokens_dim_retry"),
            "system_overall": _f(row, "system_overall"),
            "human_overall": _f(row, "human_overall"),
        }
        if any(v is None for v in required.values()):
            incomplete.append({"case_id": case_id, "plan_file": row.get("plan_file")})
            continue
        score_diff = abs(required["human_overall"] - required["system_overall"])
        filled.append(
            {
                "case_id": case_id,
                "plan_file": row.get("plan_file") or "",
                **required,
                "score_abs_diff": score_diff,
                "notes": row.get("notes") or "",
            }
        )

    n = len(filled)

    def avg(key: str) -> float:
        return sum(r[key] for r in filled) / n if n else 0.0

    initial_red = _reduction(avg("manual_initial_min"), avg("system_wall_min"))
    rereview_red = _reduction(avg("manual_rereview_full_min"), avg("manual_rereview_diff_min"))
    cycle_red = _reduction(avg("baseline_cycle_hours"), avg("system_cycle_hours"))
    token_red = _reduction(avg("tokens_full_retry"), avg("tokens_dim_retry"))
    mae = avg("score_abs_diff")

    checks = {
        "initial_time": initial_red >= TARGETS["initial_time"],
        "rereview": rereview_red >= TARGETS["rereview"],
        "cycle": cycle_red >= TARGETS["cycle"],
        "token": token_red >= TARGETS["token"],
        "min_cases": n >= MIN_CASES,
    }
    pilot_pass = all(checks.values())

    print(f"plan pilot filled={n} incomplete={len(incomplete)} (need ≥{MIN_CASES})")
    print(f"initial time reduction={initial_red:.2%} (target ≥50%)")
    print(f"rereview reduction={rereview_red:.2%} (target ≥50%)")
    print(f"cycle reduction={cycle_red:.2%} (target ≥60%)")
    print(f"token reduction={token_red:.2%} (target ≥25%)")
    print(f"avg abs score diff={mae:.2f}")

    payload = {
        "metric": "plan_business_pilot",
        "filled": n,
        "incomplete": incomplete,
        "targets": TARGETS,
        "min_cases": MIN_CASES,
        "reductions": {
            "initial_time": initial_red,
            "rereview": rereview_red,
            "cycle": cycle_red,
            "token": token_red,
        },
        "averages": {
            "manual_initial_min": avg("manual_initial_min"),
            "system_wall_min": avg("system_wall_min"),
            "manual_rereview_full_min": avg("manual_rereview_full_min"),
            "manual_rereview_diff_min": avg("manual_rereview_diff_min"),
            "baseline_cycle_hours": avg("baseline_cycle_hours"),
            "system_cycle_hours": avg("system_cycle_hours"),
            "tokens_full_retry": avg("tokens_full_retry"),
            "tokens_dim_retry": avg("tokens_dim_retry"),
            "avg_abs_score_diff": mae,
        },
        "checks": checks,
        "pass": pilot_pass,
        "cases": filled,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        default=ROOT / "evals" / "plan" / "pilot_template.csv",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(main(args.file, args.out))
