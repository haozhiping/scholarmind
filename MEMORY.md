# MEMORY.md

> 跨会话的关键洞察与已知陷阱。任务开始前先读，结束后有新发现就追加一行。
> 格式：`- [类型] 结论 —— 为什么 / 怎么做`

## 已知陷阱（必读）

- [坑] **图片/页码绝不能在入库时丢** —— 前身项目 `process_items` 重建 dict 时漏拷 `image/page_num`，导致图搜不到、无法溯源到页。本项目每个 chunk 强制带 `page_num/bbox/block_id/image_key`。
- [坑] **单一 embedding 跨语言不可靠** —— 实测中文搜英文召回差。解法：Query 翻译(中→英) + 入库时每块生成中文摘要，两条同语言通道 + 跨语言兜底，RRF 融合。
- [坑] **维度必须三方一致** —— `.env` 的 `EMBEDDING_DIM` == Milvus `dense_vec` 维度 == 模型实际输出维度，不一致直接报错或召回乱。
- [坑] **Milvus 必须建索引并 load** —— 没建 HNSW = FLAT 暴力扫，几百万 chunk 卡死。建完确认 index built + collection loaded。
- [事实] **HNSW 是 ANN，不是全库扫** —— 分区内几百万向量也是亚线性图搜索，速度不是瓶颈；相关性收窄靠 `paper_id/folder_id/acl` scalar 过滤 + 大范围时两阶段文档路由，不是靠分区。
- [坑] **大表别塞一个 chunk** —— 一张大表→一个向量语义被稀释。走小-大检索：摘要入库，命中后按 `block_id` 取 `doc_blocks` 整表喂 LLM。
- [坑] **批量上传别用 BackgroundTasks** —— 进程重启任务丢、不能限并发。用 RQ：状态进 `ingest_tasks`，可关页面、可重试、可恢复。
- [坑] **MinerU 是云端 KIE SDK（mineru-kie-sdk），非本地容器** —— 用 `MineruKIEClient(base_url=MINERU_KIE_BASE_URL, pipeline_id=...)` 连 mineru.net，与配置里的 `MINERU_BASE_URL`(本地容器 HTTP) 不是一回事。SDK 同步阻塞(requests)，async 里必须 `asyncio.to_thread` 包裹；上传收文件路径，需先落 tempfile。其 parse 返回结构未文档化，`_mineru_to_blocks` 用容错适配层（字段映射假设见 `check/任务1-解析服务对接-验证报告.md`），真实联调需核对。
- [坑] **图片落 MinIO 依赖先写库拿 block_id** —— figures key=`{user_id}/{paper_id}/{block_id}.png`，block_id 是 doc_blocks 自增。顺序必须：先 `_write_blocks`(回收 lastrowid) → 传图 → UPDATE 回填 image_key。`parse_paper` SDK 模式强制要求 `pdf_bytes`(缺失抛 ValueError)，未来 worker 负责从 MinIO 取字节传入。
- [修复] **2026-06-05 环境依赖同步** —— ① `requirements.txt` 补 `openai`/`llama-index-core`/`llama-index-llms-openai`（原缺失致 ImportError）；② `Dockerfile` 删除 L25 的 `pymilvus==2.4.4` 覆写，统一 2.4.6；③ `.env` 修复 `MYSQL_PASSWORD=root→123456` 与 compose 对齐；④ 新建 `common/clients/minio.py`；⑤ 补全 `services/retrieval/`（query_optimizer/searcher/reranker）；⑥ `worker/main.py` 注册 `handle_ingest_job`；⑦ 修复 `agent.py` 3 处错误导入。详见 `check/环境依赖同步报告.md`。


## 接口完成情况（重要现状）

- [坑] **后端 API 层（`backend/app/routers/*`）原本全是内存 Mock** —— auth/papers/folders/ingest/chat/advanced/observability 七个路由都返回写死数据，未接任何 DB/服务；真实服务代码（`backend/services/*`、`common/clients/*`）是"孤岛"，没被任何路由或 worker import。前端已完整接线到这些接口，所以"前端功能不完善"的根因是后端被 Mock 短路。去 Mock 化按链路推进：①认证+论文库 ②上传→解析→入库 ③对话 RAG。
- [进展] **链路①(认证+论文库)已去 Mock** —— 新增 `common/db/mysql_client.py`(aiomysql 异步池，raw SQL，autocommit，镜像 pg_client)、`common/auth/{security,deps}.py`(bcrypt+jose JWT + `get_current_user` 依赖)。auth/papers/folders 路由全部接真实 MySQL 且强制 `user_id` 过滤；main.py 注册了异常处理器(原本没注册，AuthException 不会映射 401)+MySQL 池生命周期。补全了前端调用但后端缺失的 `DELETE /folders/{id}`。**upload→MinIO+RQ 仍是占位(属链路②)**。
- [坑] **DB↔前端契约字段不一致，需在路由层映射** —— `papers` 表 `authors` 是 JSON 数组/`pdf_key`/`num_pages`/status=`pending|done|failed`，前端 PaperResponse 要 authors 字符串/`file_key`/`pages`/status 含 `completed`。映射逻辑在 `papers._paper_row_to_response`(done→completed，authors join，file_size 无列返回0)。

## 设计决策

- [决策] 关系库双库：MySQL 存业务(用户/论文/引用图/日志)，PostgreSQL 存对话记忆 —— database-per-service，chat_agent 独占 PG。
- [决策] 队列用 RQ 不用 Celery —— 项目已引入 Redis，RQ 是其延伸，并且心智与维护成本较低；架构留了换 Celery 的余地。
- [决策] 意图路由是对话入口 —— 闲聊→直接 LLM 不检索；知识问题→RAG；复杂→Agent。省延迟、抑制幻觉（Self-RAG/自适应检索）。
- [决策] 模型全走 OpenAI 兼容接口 + `.env` 配版本 —— 可一键切 Qwen3/DeepSeek/vLLM/Ollama，版本不绑死代码。

## 开发与协作规范

- [规范] **即时 Git 提交以防破坏代码** —— 每次实现或更新完需求后，必须立即进行 Git 提交以防止后续迭代引入 Regression。提交说明必须使用中文，描述要足够详实、具体，能够清晰追溯改动目的和解决的问题。
