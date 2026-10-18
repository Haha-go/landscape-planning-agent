# scripts/eval_kb_report.py
# 汇总 intent / retrieve / judge / perf / pilot JSON → summary.json + REPORT.md

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2%}"


def _ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1f} ms"


def main(run_dir: Path) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    intent = _load(run_dir / "intent.json")
    retrieve = _load(run_dir / "retrieve.json")
    judge = _load(run_dir / "judge.json")
    perf = _load(run_dir / "perf.json")
    pilot = _load(run_dir / "pilot.json")

    rows = []

    def add(name: str, value: str, target: str, passed: bool | None, note: str = ""):
        status = "—" if passed is None else ("PASS" if passed else "FAIL")
        rows.append(
            {
                "name": name,
                "value": value,
                "target": target,
                "pass": passed,
                "status": status,
                "note": note,
            }
        )

    if intent:
        add(
            "意图准确率",
            _pct(intent.get("accuracy")),
            "≥95%",
            intent.get("pass"),
            f"{intent.get('hit')}/{intent.get('total')}",
        )
    else:
        add("意图准确率", "—", "≥95%", None, "missing intent.json")

    if retrieve:
        add(
            "检索增强命中率",
            _pct(retrieve.get("accuracy")),
            "≥90%",
            retrieve.get("pass"),
            f"{retrieve.get('hit')}/{retrieve.get('total')}",
        )
    else:
        add("检索增强命中率", "—", "≥90%", None, "missing retrieve.json")

    if judge:
        cite = judge.get("citation") or {}
        hallu = judge.get("hallucination") or {}
        add(
            "引用准确率",
            _pct(cite.get("accuracy")),
            "≥95%",
            cite.get("pass"),
            f"{cite.get('ok')}/{cite.get('total')}",
        )
        add(
            "幻觉率",
            _pct(hallu.get("rate")),
            "<3%",
            hallu.get("pass"),
            f"{hallu.get('count')}/{hallu.get('total')}",
        )
    else:
        add("引用准确率", "—", "≥95%", None, "missing judge.json")
        add("幻觉率", "—", "<3%", None, "missing judge.json")

    if perf:
        ft = perf.get("first_token") or {}
        tok = perf.get("token_ab") or {}
        vs = perf.get("vs_pure_llm") or {}
        add(
            "首 token P95",
            _ms(ft.get("p95_ms")),
            "<3000 ms",
            ft.get("pass"),
            f"n={ft.get('samples')}",
        )
        add(
            "Token 降约",
            _pct(tok.get("reduction")),
            "≥25%",
            tok.get("pass"),
            f"A={tok.get('tokens_a_force_retrieve')} B={tok.get('tokens_b_intent')}",
        )
        add(
            "相对纯 LLM",
            _pct(vs.get("lift")),
            "≥40%",
            vs.get("pass"),
            f"pure={_pct(vs.get('pure_avg'))} rag={_pct(vs.get('rag_avg'))}",
        )
    else:
        add("首 token P95", "—", "<3000 ms", None, "missing perf.json")
        add("Token 降约", "—", "≥25%", None, "missing perf.json")
        add("相对纯 LLM", "—", "≥40%", None, "missing perf.json")

    if pilot:
        add(
            "查规范时间降幅",
            _pct(pilot.get("overall_reduction")),
            "≥70%",
            pilot.get("pass"),
            f"filled={pilot.get('filled')}",
        )
    else:
        add("查规范时间降幅", "—", "≥70%", None, "pilot pending")

    eng_flags = [r["pass"] for r in rows if r["name"] != "查规范时间降幅" and r["pass"] is not None]
    eng_pass = all(eng_flags) if eng_flags else False
    all_known = [r["pass"] for r in rows if r["pass"] is not None]
    overall_pass = all(all_known) if all_known else False

    summary = {
        "run_id": run_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engineering_pass": eng_pass,
        "overall_pass": overall_pass,
        "metrics": rows,
        "sources": {
            "intent": bool(intent),
            "retrieve": bool(retrieve),
            "judge": bool(judge),
            "perf": bool(perf),
            "pilot": bool(pilot),
        },
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 规范智库评测报告 `{run_dir.name}`",
        "",
        f"生成时间（UTC）：{summary['generated_at']}",
        "",
        f"- 工程指标：{'**PASS**' if eng_pass else '**FAIL**'}",
        f"- 含试点总体：{'**PASS**' if overall_pass else '**FAIL / 试点未齐**'}",
        "",
        "| 指标 | 实测 | 门槛 | 结果 | 备注 |",
        "|------|------|------|------|------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['value']} | {r['target']} | {r['status']} | {r['note']} |"
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 意图 / 检索 / 引用 / 幻觉：见 `evals/kb/` 题集与对应 `eval_kb_*.py`",
            "- 首 token：预热后 `e2e_ttft_ms` P95",
            "- Token：对照 A 强制全 retrieve vs B 现网意图分流",
            "- 相对纯 LLM：同题关键词命中率提升 `(RAG−纯)/纯`",
            "- 查规范时间：试点 CSV `(manual−system)/manual`，平均耗时口径",
            "",
            "## 产物",
            "",
            "- `intent.json` / `retrieve.json` / `judge.json` / `perf.json` / `pilot.json`",
            "- `summary.json`（本报告机器可读版）",
            "",
        ]
    )
    report_path = run_dir / "REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {summary_path}")
    print(f"wrote {report_path}")
    print(f"engineering_pass={eng_pass} overall_pass={overall_pass}")
    return 0 if eng_pass else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="evals/kb/results/<run_id>",
    )
    args = parser.parse_args()
    raise SystemExit(main(args.dir))
