# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ScholarMind（文渊）** — 跨语言学术文献智能调研系统（科研 Copilot）。
读懂双栏英文论文（含公式/图表），用中文对话问答并精确溯源，沿引用网络做跨论文对比与自动综述。
优化优先级：**回答准确性 + 可溯源 > 检索召回 > 响应速度 > 视觉花哨**。

## 30 秒速答（新人读这一段就够上手）

- **这是什么**：基于 LlamaIndex 的多租户 RAG 系统，论文入库→混合检索→重排→带引用生成。
- **技术栈**：FastAPI(后端) · Vue3(前端) · Milvus(向量) · MySQL(业务) · PostgreSQL(对话记忆) · Redis(缓存/RQ队列) · MinIO(PDF/图)。
- **新代码放哪**：后端按服务分模块 `backend/services/{parsing,indexing,retrieval,chat_agent}`，公用代码进 `backend/common/`，运行时提示词进 `prompts/`。

## 技术栈

- 后端：Python 3.11 + FastAPI(async) + SQLAlchemy + RQ(Redis 队列)
- RAG：LlamaIndex（IngestionPipeline + MilvusVectorStore + Agent）
- 模型（全走 OpenAI 兼容接口，版本在 `.env` 配）：Qwen3 系列 LLM / Qwen3-Embedding / Qwen3-Reranker / Qwen3-VL
- 解析：MinerU（正文/公式/表/图）+ GROBID（参考文献结构化）
- 前端：Vue 3 + Vite + Pinia + Vue Router（本地 `npm run dev`，基础设施全 Docker）

## Do NOT introduce（除非明确要求，这些是踩过的坑/已定方案）

- ❌ **裸用单一 embedding 跨语言**：中文搜英文必须配 Query翻译 + 中文摘要增强，见 `docs/rag-pipeline.md`。
- ❌ **把大表塞进一个 chunk**：大表走"小-大检索"（摘要入库→`doc_blocks` 取整表）。
- ❌ **丢弃图片/页码**：每个 chunk 必带 `page_num/bbox/block_id/image_key`。
- ❌ **用 FastAPI BackgroundTasks 跑批量**：必须走 RQ worker（可重试/可恢复/限并发）。
- ❌ **Milvus 不建索引或全库扫**：必须 HNSW 索引 + `user_id` 分区 + scalar 作用域过滤。
- ❌ 换 Celery / Pinecone / LangChain / Elasticsearch：本项目已锁 RQ / Milvus / LlamaIndex。
- ❌ 关系库混用：业务事实进 MySQL，对话记忆进 PostgreSQL，别互串。

## Coding Rules（可操作，5 秒能判断是否合规）

- 所有 DB / Milvus 查询**必须带 `user_id` 过滤**（多租户隔离，无 user_id 视为 bug）。
- 每个入库 chunk 必含：`user_id / paper_id / folder_id / page_num / block_id`。
- LLM/Embedding 调用必须有超时 + 重试 + 失败降级；入库用 `xxhash` 幂等去重。
- 长任务（解析/索引）一律进 RQ，禁止在请求线程内同步跑。
- 用 async/await，不用阻塞 IO；类型注解齐全，禁止裸 `dict` 传契约。
- 提示词不硬编码在代码里，统一放 `prompts/*.md`，代码加载后填变量。
- 回复用中文，代码注释用英文，文件路径用绝对路径。

## 架构与细节（指针，不在此展开 —— Progressive Disclosure）

- 总体架构、服务边界：`docs/architecture.md`
- **数据契约（所有表/Milvus/Redis schema，唯一真相源）**：`docs/data-contracts.md`
- RAG 全链路与意图路由设计：`docs/rag-pipeline.md`
- API 接口：`docs/api.md` ｜ 部署与运行：`docs/deploy.md`
- 运行时提示词库：`prompts/`
- 各服务的本地约定：对应目录下的 `CLAUDE.md`（操作该目录时自动加载）

## Context Tiers

- Tier 1（每次加载）：本 `CLAUDE.md` + `MEMORY.md`
- Tier 2（按需）：`docs/*.md`、目标服务目录的 `CLAUDE.md`、`prompts/*.md`
- Tier 3（忽略，除非明确要求）：`docs/archive/`、历史实验

## Working Style

- 先给方案再写代码；不确定时列选项，不猜测。
- 重大变更（schema/接口/依赖）先确认，小优化可直接做。
- 每次实现或更新完需求后，**必须及时进行 Git 提交**，防止后续修改破坏当前可用功能。
- **Git 提交信息必须使用中文**，且描述要详实具体，清晰表达本次修改的目的、具体改动内容以及可追溯的相关需求或问题。
- 不说"好问题/很乐意帮忙"这类废话，直接给结论和依据。
- 改了行为要如实说清，测试失败就报失败 + 输出。

## Memory

`MEMORY.md` 记录跨会话的关键洞察与已知陷阱。**每次任务开始前先读它，结束后有新发现就追加。**

## 模块地图

| 目录 | 职责 |
|---|---|
| `backend/services/parsing` | MinerU/GROBID/VLM 解析，产物落 MinIO + MySQL |
| `backend/services/indexing` | 双语增强 + 向量化 + 写 Milvus |
| `backend/services/retrieval` | 意图路由/混检/RRF/重排/CorrectiveRAG |
| `backend/services/chat_agent` | 对话/记忆/Agent 综述/SSE，连 PostgreSQL |
| `backend/worker` | RQ worker，跑批量解析索引 |
| `backend/common` | 配置/DB客户端/鉴权/日志/异常（公用） |
| `prompts/` | 运行时 LLM 提示词（提示词调优主战场） |
| `frontend/` | Vue3 门户（鉴权/对话/上传/可观测页） |
