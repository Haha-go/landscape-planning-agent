# 规划通业务试点（§13.2）

与规范智库题集**不混用**。本目录只测方案评估业务成效，不进单测。

## 对照协议

同一批 ≥10 份真实或脱敏可选中文字方案 PDF，分别记录：

1. **纯人工**：通读打分分钟数（`manual_initial_min`）；整份重读复核分钟数（`manual_rereview_full_min`）；从提交到人工出结论的日历小时（`baseline_cycle_hours`）。
2. **系统初审后再人工**：系统评估墙钟分钟数（`system_wall_min`）；只改分歧维度的复核分钟数（`manual_rereview_diff_min`）；上传到 `final_decision` 的日历小时（`system_cycle_hours`）；系统分 / 人工总分（`system_overall` / `human_overall`）。
3. **Token**：同批在「整图失败重跑」与「单维失败不重跑 + 评分只吃抽取 JSON」下的 prompt+completion 总和（见 `scripts/eval_plan_token_ab.py`）。

目标：初审耗时 −50%、复核 −50%、周期 −60%、Token −25%。报告给出平均绝对改分差，不另造准确率。

## 命令

```text
# 生成 ≥10 份文字 PDF 到 evals/plan/fixtures
D:\Software\anaconda3\envs\edu_agent\python.exe scripts\run_plan_pilot_batch.py --prepare-only

# 批量评估（需服务/密钥；写出 batch.jsonl 墙钟与 review_id）
D:\Software\anaconda3\envs\edu_agent\python.exe scripts\run_plan_pilot_batch.py --live

# Token A/B（默认提示词字符估 token；--live 跑真评估）
D:\Software\anaconda3\envs\edu_agent\python.exe scripts\eval_plan_token_ab.py
D:\Software\anaconda3\envs\edu_agent\python.exe scripts\eval_plan_token_ab.py --live

# 汇总业务降幅 → results/<run_id>/pilot.json
D:\Software\anaconda3\envs\edu_agent\python.exe scripts\eval_plan_pilot.py --out evals\plan\results\latest\pilot.json
```

计时口径：先做纯人工路径再做系统助审路径，避免系统分污染人工通读时间。
