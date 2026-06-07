# ScholarMind 任务 1-5 交付说明

本次将任务 1-5 的全部 Mock 实现替换为真实逻辑。所有外部模型调用（LLM/Embedding/VLM/Rerank）
均做了**优雅降级**：当 `.env` 中模型 key 无效或服务不可达时，系统不会崩溃，而是降级运行
（embedding 用确定性哈希向量兜底、MinerU 解析失败时用 pypdf 文本兜底、LLM 失败时返回明确提示），
保证整条链路在任何环境下都能跑通、可演示、可测试；配置有效 key 后自动切换为真实模型。

## 一、改动总览

### 新增基础设施（backend/common）
| 文件 | 职责 |
|---|---|
| `db/pg.py` | PostgreSQL 异步引擎 + session（对话记忆库） |
| `clients/redis.py` | Redis 连接 + RQ ingest 队列 + JSON 缓存助手 |
| `clients/milvus.py` | Milvus 集合创建/索引/加载 + 稀疏编码 + 混合检索(RRF) + 增删 |
| `auth/security.py` + `auth/__init__.py` | bcrypt 密码哈希 + JWT 签发/校验 + `get_current_user_id` 依赖 |
| `prompts.py` | 加载 `prompts/*.md` 并填充变量（兼容 JSON 大括号） |
| `clients/llm.py` | 新增 `chat_stream`（流式）+ embedding 确定性兜底 |
| `db/mysql.py` | 新增 `get_db` FastAPI 依赖；修复 `MYSQL_POOL_SIZE` |

### 任务 1 — 解析服务对接
- `routers/auth.py`：真实注册/登录/me（MySQL + bcrypt + JWT）
- `routers/papers.py`：上传 = xxhash 幂等 → MySQL papers → MinIO → ingest_batches/tasks → RQ 入队；列表/详情/删除/文件夹 CRUD 全部真实化（带 user_id 隔离）
- `worker/tasks.py`：`handle_ingest_job` 驱动 解析→索引 全流程 + 阶段进度更新 + 批次计数
- `services/parsing/parser.py`：MinerU 失败时 **pypdf 文本兜底**，保证任何 PDF 都能产出 blocks

### 任务 2 — 切分与向量化入库（services/indexing）
- `chunker.py`：表/图/公式整块不切；正文句子级切分(~512token,18%重叠)；章节识别；带 block_id
- `enricher.py`：正文 LLM 生成中文摘要+关键词写 content_zh；图复用 VLM 描述；并发 8
- `vectorizer.py`：dense(embedding) + sparse(词频) → Milvus；id=xxhash64 幂等
- `__init__.py`：`index_paper(ParseResult, db)` 读 doc_blocks → 切分 → 增强 → 入库 → 更新 chunk_count

### 任务 3 — 混合检索（services/retrieval）
- `query_optimizer.py`：改写 + 翻译 + HyDE 并发（开关控制）
- `searcher.py`：英文/中文/HyDE 三路 dense+sparse 混检，跨路 RRF 融合，强制 user_id 过滤
- `reranker.py`：Rerank API + Corrective RAG 打分
- `__init__.py`：`retrieve(question, user_id, scope...)`

### 任务 4 — 对话与 Agent 综述（services/chat_agent）
- `memory.py`：PostgreSQL 会话/消息读写
- `agent.py`：意图路由 → RAG → 带角标流式生成 → 落库 + query_logs
- `reviewer.py`：主题分解为子问题 → 多路检索 → 流式综述
- `routers/chat.py`：会话 CRUD + `/query` 真实 SSE + 反馈
- `routers/advanced.py`：`/review/generate` 真实 SSE + `/graph/citations` 取 MySQL citations
- `routers/observability.py`：stats/查询日志/访问日志全部真实
- `routers/ingest.py`：批次/任务进度 + 重试入队
- `app/main.py`：访问日志中间件

### 任务 5 — 前端联调（frontend/src）
- `api/index.ts`：axios 实例(baseURL=/api) + JWT 拦截 + 401 跳登录 + `figureUrl`
- `stores/auth.ts`：真实 login/register/fetchMe
- `views/Login.vue`：真实登录注册
- `views/Library.vue`：真实文件夹/论文加载、拖拽上传、进度轮询、删除
- `views/Chat.vue`：fetch+ReadableStream 解析 SSE，cite/token/done 渲染，点击引用看原文/表格/图(MinIO)/公式
- `views/Observability.vue`：真实指标/任务/查询日志，5s 轮询
- `views/Settings.vue`：本地持久化（模型密钥由后端 .env 统一管理）

### 依赖新增（backend/requirements.txt）
`openai==1.51.0`、`pypdf==5.1.0`

## 二、启动方式

基础设施已在 Docker 运行。本次改了 `requirements.txt`，需重建后端与 worker：

```bash
# 1. 重建并启动后端 + worker（安装 openai/pypdf，加载新代码）
docker compose up -d --build backend worker

# 2. 启动前端（本地）
cd frontend
npm run dev
# 打开 http://localhost:5173
```

## 三、逐页验收清单

1. **登录页**：注册新账号 → 登录 → 跳转论文库（JWT 存 localStorage）
2. **论文库**：新建文件夹；拖拽/选择 PDF 上传（202 立即返回）；列表轮询解析状态由 排队→就绪；删除论文
3. **可观测页**：文档数/chunk 数/查询次数/平均延迟实时刷新；入库任务进度条；查询日志表
4. **对话页**：提问 → SSE 流式答案 + 底部引用卡片；点击引用在右侧看原文/表格/图/公式
5. **设置页**：开关与参数本地保存

## 四、已知降级点（无有效模型 key 时）
- 解析：MinerU 云端不可用 → 自动用 pypdf 提取文本（无图/表抠图）
- 向量：dashscope embedding 失败 → 确定性哈希向量（可入库可检索，但相关性弱）
- 生成：LLM 失败 → SSE 返回"模型服务不可用"提示
- 配置真实可用的 `LLM_API_KEY`/`EMBEDDING_API_KEY`/`MINERU_PIPELINE_ID` 后，以上自动恢复为真实效果。

> 注：`.env` 当前的 `LLM_MODEL=deepseek-v4-flash`、`VLM_API_KEY=<URL>` 等疑似占位/无效值，
> 建议核对后填入真实值以获得完整问答效果。
