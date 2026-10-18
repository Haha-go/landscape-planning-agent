# 规范智库评测集

工程指标用本目录固定题集；业务「查规范时间减少 70%」用试点计时，不进单元测试。

| 文件 | 用途 | 建议规模 |
|------|------|----------|
| `intent_golden.jsonl` | 意图 `direct` / `retrieve` | ≥100 |
| `retrieve_hit.jsonl` | 检索命中（库内应有答案） | ≥50 |
| `hallucination.jsonl` | 应拒答 + 有库引用对齐 | ≥30 拒答 + ≥50 有库 |
| `pilot_template.csv` | 业务试点耗时记录 | ≥20 |

样例库 `knowledge_base/sample` 仅用于联通，**不计入**「检索增强命中率 90%」分母。

## 前置

```bash
docker compose --env-file .env.local up -d
python scripts/init_milvus.py
python scripts/ingest_kb.py knowledge_base/standards
```

需本地意图模型（MiniLM）与可用 LLM（QA）。

CPU 默认关闭 BGE 精排（`RAG_ENABLE_RERANK=false`），依赖 Milvus RRF，以保证首 token P95；需要精排时在 `.env.local` 设为 `true`。

## 跑数并落盘

```bash
RUN_ID=$(date +%Y%m%d_%H%M%S)   # Windows PowerShell 见下方
OUT=evals/kb/results/$RUN_ID
mkdir -p "$OUT"

python scripts/eval_kb_intent.py --out "$OUT/intent.json"
python scripts/eval_kb_retrieve.py --out "$OUT/retrieve.json"
python scripts/eval_kb_judge.py --live --out "$OUT/judge.json"
python scripts/eval_kb_perf.py --out "$OUT/perf.json"
python scripts/eval_kb_report.py --dir "$OUT"
```

PowerShell：

```powershell
$RUN_ID = Get-Date -Format "yyyyMMdd_HHmmss"
$OUT = "evals/kb/results/$RUN_ID"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

python scripts/eval_kb_intent.py --out "$OUT/intent.json"
python scripts/eval_kb_retrieve.py --out "$OUT/retrieve.json"
python scripts/eval_kb_judge.py --live --out "$OUT/judge.json"
python scripts/eval_kb_perf.py --out "$OUT/perf.json"
python scripts/eval_kb_report.py --dir $OUT
```

产物：`$OUT/summary.json`、`$OUT/REPORT.md`。

### 单测脚本说明

| 脚本 | 作用 |
|------|------|
| `eval_kb_intent.py` | 意图准确率 ≥95% |
| `eval_kb_retrieve.py` | 检索命中率 ≥90%（排除 SAMPLE） |
| `eval_kb_judge.py [--live]` | 幻觉 &lt;3%、引用 ≥95%；离线需题含 answer/citations |
| `eval_kb_perf.py` | 首 token P95 &lt;3s；Token A/B ≥25%；相对纯 LLM ≥40% |
| `eval_kb_pilot.py` | 试点 CSV 平均耗时降幅 ≥70% |
| `eval_kb_report.py` | 汇总为 REPORT.md |

`eval_kb_perf.py` 可选：`--ttft-n 30`、`--warmups 2`、`--token-limit N`、`--quality-limit N`（联调可缩小）。

## 业务试点计时协议

1. 使用 `pilot_template.csv`（≥20 题），同一人、同一题。
2. **先手查**：翻纸质/PDF 规范，记录 `manual_minutes`（分钟，可小数）。
3. **再系统助查**：用本系统问答（可复制题干），记录 `system_assist_minutes`。
4. 两列都填完后：

```bash
python scripts/eval_kb_pilot.py --file evals/kb/pilot_template.csv --out "$OUT/pilot.json"
python scripts/eval_kb_report.py --dir "$OUT"
```

降幅口径：`(avg_manual − avg_system) / avg_manual`，目标 ≥70%。
