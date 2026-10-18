# Changelog

本文件记录规划助手的对外版本。版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-18

首个可展示稳定版（求职作品集）。

### 产品

- **规划通**：规划方案智能审核 Agent（PDF 抽取、五维评分、人机协同终审）
- **规范智库**：专业规范可溯源智能问答（RAG、引用清洗、SSE）
- 统一入口：自动 / 规范智库 / 规划通

### 工程

- LangGraph 多 Agent（Orchestrator 路由 + QA / Eval 子图）
- BGE-M3 混合检索、Milvus、可选精排
- JWT / RBAC、审核队列、审计与报表
- 评测套件：`evals/kb`、`evals/plan` 与试点结果快照

### 文档与仓库

- 求职向 [README.md](README.md) 与技术规格 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 清理未挂载旧接口与临时脚本
