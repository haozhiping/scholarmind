# ScholarMind 任务 1-5 完整实现对照文档

## 前置说明

项目骨架已完成：路由文件、Schema、worker 入口、config、MySQL/PG 建表 SQL 均已就位。

**当前状态（2026-06-05）**：任务 1（解析）✅ 已完成，任务 2-3（索引+检索）✅ 核心已完成，任务 4（对话）✅ 基础已完成，`advanced.py` 和 `reviewer.py` 仍是 Mock。

告诉 AI 时的通用格式：
> "你是 ScholarMind 项目的后端工程师。项目内容见用 CLAUDE.md。
>  当前文件 XXX 是 Mock 实现，请按以下要求改为真实逻辑，不要改动文件结构和路由路径。"

---

## 任务 1：解析服务对接

### 目标文件
```
backend/services/parsing/parser.py          ← 核心（已创建骨架）
backend/app/routers/papers.py               ← upload 接口需接入真实 DB + RQ
backend/app/worker/main.py                  ← 需分发 parse 任务到 parsing.parser
```

### 现状（2026-06-05）
- ✅ `papers.py` upload 接口已完成真实实现：MySQL 写入、MinIO 上传、RQ 入队
- ✅ `parser.py` 已完成：MinerU Agent + KIE SDK 双 provider、VLM 图描述、LLM/GROBID 参考文献提取
- ✅ `worker/main.py` 已完成：`handle_ingest_job` → `_run_parse_pipeline`（解析→索引流水线）
- ⚠️ upload 接口缺少 `(user_id, file_hash)` 幂等检查，重复上传会创建重复记录

### 要告诉 AI 的内容

**papers.py upload 接口**：
```
请修改 backend/app/routers/papers.py 的 upload_papers 函数：
1. 接收上传的 PDF 文件，用 xxhash64 计算 file_hash（16位hex）
2. 检查 MySQL papers 表是否已有 (user_id, file_hash) 记录，有则跳过（幂等）
3. 将 PDF 上传到 MinIO，bucket=papers，key={user_id}/{paper_id}/original.pdf
4. 向 MySQL 写入 papers 记录（status=pending），ingest_batches 记录，ingest_tasks 记录（stage=queued）
5. 把 parse 任务入 RQ ingest 队列，payload = {user_id, paper_id, pdf_key, task_id}
6. 返回 {batch_id, tasks}

依赖：
- common/clients/minio.py（MinIO 上传）
- common/clients/redis.py（RQ 队列）
- common/db/mysql.py（AsyncSession）
- user_id 从 JWT 中间件注入（当前 Mock 写死 999，暂时保持）
```

**worker/main.py job handler**：
```
请在 backend/app/worker/main.py 中添加 job handler 函数 handle_ingest_job：
1. 接收参数 user_id, paper_id, pdf_key, task_id
2. 更新 ingest_tasks.stage = parsing, progress = 10
3. 调用 services/parsing/parser.py 的 parse_paper(user_id, paper_id, pdf_key, db)
4. 解析完成后更新 ingest_tasks.stage = indexing, progress = 50
5. 调用 services/indexing（待实现），完成后 stage=done, progress=100
6. 异常时 stage=failed, error_msg=str(e)
把这个函数注册为 RQ 队列的 job function。
```

**MinerU 对接（parser.py 的 _call_mineru）**：
```
请修改 backend/services/parsing/parser.py 中的 _call_mineru 函数：
使用 mineru-kie-sdk 的 MineruKIEClient：
1. 上传 PDF bytes 到 MinerU
2. 轮询获取解析结果（带超时，最多等 300s）
3. 解析结果转换为 Block 列表：
   - type=text → Block(block_type='text', content=..., page_num=..., bbox=...)
   - type=table → Block(block_type='table', content=HTML字符串, ...)
   - type=figure → Block(block_type='figure', image_key=..., content=caption, ...)
   - type=formula → Block(block_type='formula', content=LaTeX, ...)
每个 Block 必须带 page_num 和 bbox，不能丢。
```

### 数据契约关键字段
- `papers`：file_hash CHAR(16)，status: pending|done|failed，pdf_key VARCHAR(256)
- `doc_blocks`：block_type, content, content_zh(TEXT, VLM 图描述), page_num, bbox(JSON), image_key
- `citations`：src_paper_id, dst_title, raw_ref
- MinIO bucket `papers`：key = `{user_id}/{paper_id}/original.pdf`
- MinIO bucket `figures`：key = `{user_id}/{paper_id}/{block_id}.png`

---

## 任务 2：切分与向量化入库

### 目标文件
```
backend/services/indexing/indexer.py        ← 已完成：Chunker + enrich + vectorize 合并在一个文件
backend/services/indexing/__init__.py       ← 待创建：暴露 index_paper(ParseResult)
```

### 现状（2026-06-05）
- ✅ 核心索引流水线已完成（`indexer.py` 含 Chunker 类 + `enrich_bilingual()` + `vectorize_chunks()` + `index_paper()`）
- ⚠️ 与指南的差异：非 `chunker.py/enricher.py/vectorizer.py` 三文件拆分，而是合并在 `indexer.py` 中
- ⚠️ 切分用自定义句子切分，非 LlamaIndex SentenceSplitter
- ⚠️ `enrich_bilingual()` 串行处理（非 asyncio.gather 并发）
- ❌ sparse 向量未实现（`sparse_vec` 始终为空 dict）
- ❌ section header 识别未实现
- ❌ chunk_paper 读 `doc_blocks` 时未读取 `content_zh` 列（VLM 图描述丢失）

### 要告诉 AI 的内容

> ⚠️ **注意**：当前索引逻辑已合并在 `backend/services/indexing/indexer.py`（`Chunker` 类 + `enrich_bilingual()` + `vectorize_chunks()` + `index_paper()`）。以下任务描述保留原计划，如需重构为独立文件可参考。

**chunker.py**（当前实现在 `indexer.py` 的 `Chunker` 类）：
```
请修改 backend/services/indexing/indexer.py 的 Chunker.chunk_paper 方法：
Chunk 数据类包含：content_en, content_zh, block_type, page_num, bbox, block_id, image_key, section

切分规则：
1. block_type=table/figure/formula：整块不切，直接作为一个 Chunk
2. block_type=text：按章节语义切分，目标 512 token，重叠 15-20%（约 80 token）
3. 每个 Chunk 记录来源 block_id（→ MySQL doc_blocks.id，用于小-大检索）
4. [待实现] 章节标题识别：text 块首行全大写或 ## 开头的视为 section header
5. [Bug] SELECT 查询缺少 content_zh 列，导致 VLM 图描述丢失
```

**enricher.py**（当前实现在 `indexer.py` 的 `enrich_bilingual()`）：
```
请修改 backend/services/indexing/indexer.py 的 enrich_bilingual 函数：
1. 对 block_type=text 的英文 chunk，调用 LLM 生成中文摘要+关键词，写入 chunk.content_zh
2. 使用 prompts/enrich_zh_summary.md 中的提示词
3. [待改进] 改为批量并发处理（asyncio.gather），每批 8 个（当前是串行 for 循环）
4. 非英文 chunk 或 table/figure/formula：content_zh = content_en（直接复用）
5. figure block：content_zh 从 doc_blocks.content_zh 读取（需先修复 chunker 的 SELECT）
```

**vectorizer.py**（当前实现在 `indexer.py` 的 `vectorize_chunks()` + `index_paper()`）：
```
请修改 backend/services/indexing/indexer.py：
1. 调用 common/clients/llm.py 的 embed_texts() 批量获取 dense 向量（维度=EMBEDDING_DIM）
2. [待实现] sparse 向量用 BM25 / BGE-M3 sparse 输出
3. 每个 chunk 的 Milvus 写入字段：
   id = xxhash64(content_en + str(paper_id))  ← 幂等去重
   dense_vec, sparse_vec
   content_en, content_zh
   user_id, paper_id, folder_id
   chunk_type, section, page_num, bbox, block_id, image_key
4. 用 Milvus 的 insert() 批量写入（通过 common/clients/milvus.py 的 bulk_insert）
5. 写完后更新 MySQL papers.chunk_count += len(chunks)
```

### 数据契约关键字段
- Milvus `scholarmind_chunks`：dense_vec(1024), sparse_vec, content_en, content_zh, user_id(partition_key), paper_id, folder_id, context, chunk_type, section, page_num, bbox, block_id, image_key
- id = xxhash64，幂等去重
- HNSW 索引：M=16, efConstruction=200；sparse：SPARSE_INVERTED_INDEX

---

## 任务 3：混合检索服务

### 目标文件
```
backend/services/retrieval/query_optimizer.py   ← ✅ 已完成
backend/services/retrieval/searcher.py          ← ✅ 已完成
backend/services/retrieval/reranker.py          ← ✅ 已完成
backend/services/retrieval/retriever.py         ← ✅ 已完成：HybridRetriever 统一入口
backend/services/retrieval/__init__.py          ← ✅ 已完成
```

### 现状（2026-06-05）
- ✅ 检索服务完整度最高，6 个文件均已实现
- ⚠️ 需实测确认 RRF 合并、Corrective RAG 是否按设计工作

### 要告诉 AI 的内容（任务已基本完成，以下为原计划参考）

**query_optimizer.py**：
```
请新建 backend/services/retrieval/query_optimizer.py，实现 optimize_query(question, conversation_history) -> QueryBundle：
QueryBundle 包含：original, rewritten, translated_en, hyde_doc

并发执行（asyncio.gather）：
1. 查询改写：prompts/query_rewrite.md，补全代指词和上下文
2. 中→英翻译：prompts/query_translate.md
3. HyDE：prompts/hyde.md，生成假设性英文答案段落

settings.ENABLE_QUERY_REWRITE / ENABLE_QUERY_TRANSLATION / ENABLE_HYDE 控制开关，
关闭时直接用原始 question。
```

**searcher.py**：
```
请新建 backend/services/retrieval/searcher.py，实现 hybrid_search(query_bundle, scope, top_k) -> list[ScoredChunk]：
scope 包含：user_id, folder_id=None, paper_ids=None

三路检索（asyncio.gather 并发）：
1. 英文检索：embed(translated_en) → Milvus dense 检索 content_en
2. 中文检索：embed(rewritten) → Milvus dense 检索 content_zh
3. HyDE 检索：embed(hyde_doc) → Milvus dense 检索 content_en

所有检索必须带 user_id 过滤：
  单篇：user_id=={uid} && paper_id in {paper_ids}
  文件夹：user_id=={uid} && folder_id=={fid}
  全局：user_id=={uid}

三路结果用 RRF 合并，公式：score = Σ 1/(k+rank_i)，k=60，返回去重后的 top_k。
```

**reranker.py**：
```
请新建 backend/services/retrieval/reranker.py，实现：
1. rerank_chunks(question, chunks, top_n) -> list[ScoredChunk]
   调用 common/clients/llm.py 的 rerank()
   settings.ENABLE_RERANK=False 时直接返回前 top_n 个

2. corrective_grade(question, chunks) -> list[ScoredChunk]（仅 ENABLE_CORRECTIVE_RAG=True 时生效）
   用 prompts/corrective_grade.md 对每个 chunk 打分（0-1）
   过滤低于 0.5 的，不足 3 个时触发查询改写重检（递归一次，不循环）
```

---

## 任务 4：对话与 Agent 综述

### 目标文件
```
backend/app/routers/chat.py                 ← ✅ 已完成：真实 SSE 流式对话
backend/app/routers/advanced.py             ← ❌ 仍是 Mock：硬编码综述 + 虚构图谱
backend/services/chat_agent/agent.py        ← ✅ 已完成：意图路由 + ReviewAgent
backend/services/chat_agent/chat_service.py ← ✅ 已完成：核心对话服务（含会话记忆）
backend/services/chat_agent/intent_router.py← ✅ 已完成
backend/services/chat_agent/prompts.py      ← ✅ 已完成：提示词常量
backend/services/chat_agent/schemas.py      ← ✅ 已完成：数据模型
```

### 现状（2026-06-05）
- ✅ chat 对话链路已完成：意图路由 → 检索 → Rerank → SSE 流式生成
- ✅ 会话记忆在 `chat_service.py` 中内嵌（`create_conversation`/`add_message`/`get_messages`），无独立 `memory.py`
- ✅ 数据库连接用 `common/db/pg_client.py`（非指南说的 `pg.py`）
- ❌ `advanced.py` 的 `/review/generate` 仍是 Mock（硬编码文本 + 假引用逐字流）
- ❌ `advanced.py` 的 `/graph/citations` 仍是 Mock（硬编码节点和边）
- ⚠️ `ReviewAgent` 在 `agent.py` 中，但未接入 `advanced.py` 路由

### 要告诉 AI 的内容

**memory.py**：
```
请新建 backend/services/chat_agent/memory.py，实现：
1. get_history(conversation_id, limit=10) -> list[dict]
   从 PostgreSQL messages 表读取最近 N 条，格式 [{role, content}]
2. save_message(conversation_id, role, content, citations=None)
   写入 PostgreSQL messages 表，citations 序列化为 JSONB
3. get_or_create_conversation(user_id, title, folder_id, paper_ids) -> int
   PostgreSQL conversations 表 upsert，返回 conversation_id

数据库连接用 common/db/pg.py 的 AsyncSession。
```

**chat.py query 接口**：
```
请修改 backend/app/routers/chat.py 的 chat_query 接口，替换 SSE mock 为真实实现：

流程：
1. 从 PostgreSQL 读取 conversation 最近 10 条历史（memory.get_history）
2. 意图路由（prompts/intent_router.md）：
   - 闲聊/常识 → 直接 LLM 回答，跳过检索
   - 知识问题 → RAG 流程
   - 复杂综述/对比 → 转 Agent
3. RAG 流程：
   a. optimize_query(question, history) → QueryBundle
   b. hybrid_search(query_bundle, scope) → chunks
   c. rerank_chunks(question, chunks) → top_n_chunks
   d. 构造 prompt（prompts/answer_with_citation.md），流式生成
4. SSE 输出格式：
   event: cite  data: {paper_id, paper_title, page_num, bbox, chunk_type, content, image_key}
   event: token data: {delta: "..."}
   event: done  data: {latency_ms: ...}
5. 生成完成后写 PostgreSQL messages 表（SSE done 之后才写，防截断落库）
6. 写 MySQL query_logs 表（question, latency_ms, prompt_tokens, completion_tokens）
```

**reviewer.py（Agent 综述）**：
```
请新建 backend/services/chat_agent/reviewer.py，实现 generate_review(topic, scope, user_id) -> AsyncGenerator[str]：

用 LlamaIndex ReActAgent 实现：
1. 把 hybrid_search 封装为 LlamaIndex QueryEngineTool
2. Agent 收到 topic 后自动分解为 3-5 个子问题
3. 对每个子问题调用检索工具获取 chunks
4. 用 prompts/review_generation.md 提示词整合生成综述
5. 流式 yield，cite 事件先于 token 事件发出

在 advanced.py 的 /review/generate 接口调用，替换 mock。
```

---

## 任务 5：前端页面与 API 联调

### 目标文件
```
frontend/src/api/index.ts（或 request.ts）  ← Axios 封装 + JWT 拦截器
frontend/src/stores/auth.ts                 ← 真实登录/注册流程
frontend/src/pages/Chat.vue                 ← SSE 流解析 + 引用溯源渲染
frontend/src/pages/Observability.vue        ← 真实接口数据
```

### 现状（2026-06-05）
- ⚠️ 前端状态未在本次校验中覆盖，以下任务描述保留原计划

### 要告诉 AI 的内容

**Axios 封装**：
```
请在 frontend/src/api/ 中创建统一 Axios 实例：
1. baseURL = http://localhost:8008/api
2. 请求拦截器：从 localStorage 取 token，自动加 Authorization: Bearer <token> 头
3. 响应拦截器：401 时跳转登录页
4. 把所有 mock 调用替换为真实接口，接口路径见 docs/api.md
```

**Chat.vue SSE 流解析**：
```
请修改 frontend/src/pages/Chat.vue 的问答流程：
1. 用 fetch + ReadableStream 读取 GET /api/chat/query 的 SSE 响应（参数走 query string：conversation_id, question, scope_type, scope_ids）。EventSource 可用但不支持 Authorization header，推荐 fetch
2. 解析事件：
   event: cite → citations 数组存储，等文本中 [N] 角标出现时关联
   event: token → delta 追加到消息文本，实时渲染
   event: done → 停止流
3. 点击 [N] 角标或底部引用卡片时，右侧 Preview 区展示：
   chunk_type=text → 原文段落
   chunk_type=table → 渲染 HTML 表格
   chunk_type=figure → 显示图片（URL = http://localhost:9000/figures/{image_key}）
   chunk_type=formula → KaTeX 渲染 LaTeX
```

---

## 实现优先级

```
已完成：任务1（解析）+ 任务2（索引）+ 任务3（检索）+ 任务4 基础对话（chat.py+chat_service）
待完成：任务4 reviewer（advanced.py Mock→真实 Agent）+ 任务5（前端联调）
```

---

## 现有文件速查（2026-06-05 校正）

| 需要用到 | 文件位置 | 状态 | 说明 |
|---------|---------|------|------|
| LLM/Embedding/Rerank 调用 | `backend/common/clients/llm.py` | ✅ 已实现 | |
| 解析入口 | `backend/services/parsing/parser.py` | ✅ 完整实现 | MinerU Agent + KIE 双 provider |
| 索引服务 | `backend/services/indexing/indexer.py` | ✅ 基本实现 | Chunker+enrich+vectorize 合并 |
| 检索服务 | `backend/services/retrieval/` | ✅ 完整实现 | 6 文件：optimizer/searcher/reranker/retriever |
| 对话服务 | `backend/services/chat_agent/chat_service.py` | ✅ 完整实现 | 意图路由+RAG+SSE 流式 |
| 综述 Agent | `backend/services/chat_agent/agent.py` | ✅ 已实现 | ReviewAgent，未接入路由 |
| 配置 | `backend/common/config.py` | ✅ 已实现 | |
| 提示词（根级） | `prompts/*.md` | ✅ 13 文件 | **实际加载路径**（parser/indexer 从根级读） |
| 提示词（后端） | `backend/prompts/*.md` | ⚠️ 7 文件 | **未被代码引用**，与根级有重复 |
| API Schema | `backend/app/schemas/*.py` | ✅ 全部已有 | |
| 路由 — papers | `backend/app/routers/papers.py` | ✅ 完整实现 | 缺 file_hash 幂等检查 |
| 路由 — chat | `backend/app/routers/chat.py` | ✅ 完整实现 | SSE 流式+反馈 |
| 路由 — ingest | `backend/app/routers/ingest.py` | ✅ 完整实现 | 批次进度+重试 |
| 路由 — advanced | `backend/app/routers/advanced.py` | ❌ Mock | 唯一待实现的接口路由 |
| 路由 — auth | `backend/app/routers/auth.py` | ✅ 已实现 | |
| 路由 — observability | `backend/app/routers/observability.py` | ✅ 已实现 | |
| MySQL 客户端 | `backend/common/db/mysql_client.py` | ✅ 已实现 | **非 `mysql.py`** |
| PG 客户端 | `backend/common/db/pg_client.py` | ✅ 已实现 | **非 `pg.py`** |
| Milvus 客户端 | `backend/common/clients/milvus.py` | ✅ 已实现 | |
| MinIO 客户端 | `backend/common/clients/minio.py` | ✅ 已实现 | |
| Redis/RQ 客户端 | `backend/common/db/redis_client.py` | ✅ 已实现 | **在 `db/` 非 `clients/`** |
| DB 迁移 | `backend/common/db/migrations.py` | ✅ 已实现 | 运行时自检补列 |
| 数据契约 | `docs/data-contracts.md` | ✅ 字段定义真相源 |
| API 文档 | `docs/api.md` | ✅ | |

---

## 踩坑预防（必读）

1. **所有 DB/Milvus 查询必须带 user_id 过滤**，没有等于多租户泄露
2. **EMBEDDING_DIM=1024** 与 Milvus dense_vec 维度必须一致，建 collection 前确认
3. **大表/公式/图不切碎**，整块存 doc_blocks，chunk 里只存摘要+block_id 指针
4. **解析任务只在 RQ worker 里跑**，不在 FastAPI 请求线程同步执行
5. **xxhash64 幂等**：同一 chunk_id 不重复写 Milvus
   - ⚠️ upload 接口用 `hashlib.md5` 而非 `xxhash64` 做 file_hash
   - ❌ upload 缺少 `(user_id, file_hash)` 重复上传检测
6. **SSE done 事件之后才写 messages 表**，流中断不落库截断内容
7. **Chat.vue 推荐用 fetch + ReadableStream**：SSE 接口是 GET（参数走 query string），EventSource 可用但无法设 Authorization header
8. **prompts 目录双份**：根级 `prompts/`（13 文件，代码实际加载）与 `backend/prompts/`（7 文件，未被引用）。避免在 `backend/prompts/` 中修改却被根级覆盖
9. **chunk_paper 缺 content_zh**：`indexer.py` L76-79 SELECT 未读 `doc_blocks.content_zh`，VLM 图描述无法传递到 Milvus
