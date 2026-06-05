# Ingest 端到端入库链路打通 设计

> 日期：2026-06-04 ｜ 分支：`feature/parsing-mineru`
> 前置：任务1 解析服务（`parse_paper`）已完成（仅 parser.py 核心）。本设计把上传→入队→worker→解析串成可运行闭环。
> 范围决策（已与用户确认）：完整闭环；管道停在“解析完即置 done”（indexing 待建）；`user_id` 暂硬编码 999（JWT 中间件未实现）；ingest 进度查询改读真实库。

## 1. 背景与现状

- `app/routers/papers.py` 的 `upload_papers` 是 Mock：写内存 `MOCK_PAPERS`，不落 MySQL、不传 MinIO、不入队。
- `app/routers/ingest.py` 的 batches/tasks/retry 读写 `MOCK_BATCHES`/`MOCK_TASKS`。
- `app/worker/main.py` 仅启动 RQ worker 监听 `ingest` 队列，无 job handler。
- `parse_paper(user_id, paper_id, pdf_key, db, *, pdf_bytes=...)` 已实现，SDK 模式强制要求 `pdf_bytes`，内部提交并置 `papers.status='done'`。
- 缺失基础设施：`common/db/mysql.py`、`common/clients/minio.py`、`common/clients/redis.py` 均不存在。
- 鉴权全 Mock，无 `user_id` 注入中间件 → 暂用常量 `999`。
- `docker-compose.yml`：backend `uvicorn app.main:app`；worker `python -m app.worker.main`，监听队列 `ingest`。

## 2. 范围

### 做
1. `common/db/mysql.py`：async engine + `async_sessionmaker` + `get_session()`（FastAPI 依赖）+ `session_scope()`（worker 上下文管理器）。
2. `common/clients/minio.py`：`minio_client()` 单例 + `ensure_buckets()` + `upload_bytes()` / `download_bytes()` / `presigned_url()`。
3. `common/clients/redis.py`：`get_redis()` + `get_queue(name="ingest")`（RQ Queue）。
4. `app/routers/papers.py`：`upload_papers` 真实实现（落库 + 传 MinIO + 入队，幂等）。
5. `app/routers/ingest.py`：batches/tasks GET 读真实库；retry 重入队。
6. `app/worker/main.py`：`handle_ingest_job`（模块级同步函数）→ `asyncio.run(_handle_ingest_async)`。
7. `common/config.py`：新增 `MYSQL_POOL_SIZE`。

### 不做（YAGNI / 越界）
- 不实现 indexing（管道停在解析完 done）。
- 不接 JWT 中间件（user_id=999 常量）。
- 不重构 task1 parser 内联的 MinIO 封装（避免破坏现有 21 个绿测）；新 client 与之暂有少量重复，记为技术债，后续合并。
- redis.py 只暴露队列访问，不做缓存助手（按需再加）。

## 3. 架构（文件级）

| 文件 | 职责 | 操作 |
|---|---|---|
| `backend/common/db/mysql.py` | async 引擎/会话工厂 + 依赖 + 上下文管理器 | Create |
| `backend/common/clients/minio.py` | MinIO 上传/下载/presigned + bucket 初始化 | Create |
| `backend/common/clients/redis.py` | Redis 连接 + RQ 队列 | Create |
| `backend/app/routers/papers.py` | upload_papers 真实实现 | Modify |
| `backend/app/routers/ingest.py` | 进度查询读真实库 + retry 重入队 | Modify |
| `backend/app/worker/main.py` | handle_ingest_job + _handle_ingest_async | Modify |
| `backend/common/config.py` | MYSQL_POOL_SIZE | Modify |
| `backend/tests/test_infra_clients.py` | mysql/minio/redis 客户端单测 | Create |
| `backend/tests/test_upload_route.py` | upload + ingest GET 路由单测 | Create |
| `backend/tests/test_worker_handler.py` | worker handler 单测 | Create |

### 3.1 common/db/mysql.py
- `_mysql_url()`：`mysql+aiomysql://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4`。
- `engine = create_async_engine(_mysql_url(), pool_size=settings.MYSQL_POOL_SIZE, pool_pre_ping=True, future=True)`（模块级惰性单例）。
- `SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)`。
- `async def get_session() -> AsyncIterator[AsyncSession]`：FastAPI 依赖，`async with SessionLocal() as s: yield s`。
- `@asynccontextmanager async def session_scope()`：worker 用，`async with SessionLocal() as s: yield s`（调用方负责 commit/rollback；与 parse_paper 内部 commit 协作）。

### 3.2 common/clients/minio.py
- `minio_client() -> Minio`：用 settings 构造（endpoint/access/secret/secure），模块级缓存。
- `ensure_buckets()`：确保 `MINIO_BUCKET_PDF`、`MINIO_BUCKET_FIG` 存在。
- `upload_bytes(bucket, key, data: bytes, content_type="application/octet-stream")`：`put_object(BytesIO(data), len(data))`，bucket 不存在则建。
- `download_bytes(bucket, key) -> bytes`：`get_object(...).read()`，确保关闭释放连接。
- `presigned_url(bucket, key) -> str`：`presigned_get_object`。
- 注：均为同步（minio SDK 同步）；调用方在 async 上下文用 `asyncio.to_thread` 包裹。

### 3.3 common/clients/redis.py
- `get_redis() -> Redis`：`Redis(host, port, db)`，模块级缓存。
- `get_queue(name="ingest") -> rq.Queue`：`Queue(name, connection=get_redis())`。

### 3.4 app/routers/papers.py — upload_papers
依赖注入 `db: AsyncSession = Depends(get_session)`。常量 `_DEFAULT_USER_ID = 999`。
```
batch = INSERT ingest_batches(user_id=999, total=len(files), status='running') → batch_id
tasks = []
for f in files:
    data = await f.read()
    file_hash = xxhash.xxh64(data).hexdigest()  # 16 hex
    row = SELECT id FROM papers WHERE user_id=999 AND file_hash=:h
    if row:                       # 幂等命中
        paper_id = row.id
        task_id = INSERT ingest_tasks(batch_id, user_id, paper_id, file_name, file_hash, stage='done', progress=100)
        tasks.append(task_id); continue
    paper_id = INSERT papers(user_id=999, folder_id, title=f.filename, file_hash, pdf_key='', status='pending')
    pdf_key = f"999/{paper_id}/original.pdf"
    await to_thread(upload_bytes, MINIO_BUCKET_PDF, pdf_key, data, "application/pdf")
    UPDATE papers SET pdf_key=:k WHERE id=:paper_id AND user_id=999
    task_id = INSERT ingest_tasks(batch_id, user_id, paper_id, file_name=f.filename, file_hash, stage='queued', progress=0)
    get_queue("ingest").enqueue("app.worker.main.handle_ingest_job",
                                user_id=999, paper_id=paper_id, pdf_key=pdf_key, task_id=task_id)
    tasks.append(task_id)
await db.commit()
return PaperUploadResponse(batch_id=str(batch_id), tasks=[str(t) for t in tasks])
```
单文件异常 → 记 batch.failed+1，task stage='failed'，继续其余。

### 3.5 app/routers/ingest.py
- `GET /ingest/batches/{batch_id}`：`SELECT * FROM ingest_batches WHERE id=:bid`，按字段映射 `IngestBatchResponse`（status 由 done/failed/total 推导：done+failed==total → completed/含 failed；否则 processing）。404 若无。
- `GET /ingest/tasks?batch_id=`：`SELECT * FROM ingest_tasks [WHERE batch_id=:bid]`，映射 `IngestTaskResponse`（status 由 stage 推导；progress=task.progress）。
- `POST /ingest/tasks/{id}/retry`：`SELECT ingest_tasks WHERE id`，若 stage='failed' → UPDATE stage='queued',progress=0,error_msg=NULL,retry_count+1；重新 `enqueue handle_ingest_job(user_id,paper_id,pdf_key,task_id)`（pdf_key 由 papers.pdf_key 取）；返回 `TaskRetryResponse`。404 若无。
- 均用 `Depends(get_session)`。

### 3.6 app/worker/main.py
```
def handle_ingest_job(user_id, paper_id, pdf_key, task_id):   # RQ 同步入口
    return asyncio.run(_handle_ingest_async(user_id, paper_id, pdf_key, task_id))

async def _handle_ingest_async(user_id, paper_id, pdf_key, task_id):
    from common.db.mysql import session_scope
    from common.clients.minio import download_bytes
    from services.parsing.parser import parse_paper
    try:
        async with session_scope() as db:
            await db.execute(UPDATE ingest_tasks SET stage='parsing', progress=10, started_at=now WHERE id=:tid)
            await db.commit()
            pdf_bytes = await asyncio.to_thread(download_bytes, settings.MINIO_BUCKET_PDF, pdf_key)
            await parse_paper(user_id, paper_id, pdf_key, db, pdf_bytes=pdf_bytes)   # 内部 commit + papers.done
            await db.execute(UPDATE ingest_tasks SET stage='done', progress=100, finished_at=now WHERE id=:tid)
            await db.execute(UPDATE ingest_batches SET done=done+1 WHERE id=(SELECT batch_id FROM ingest_tasks WHERE id=:tid))
            await db.commit()
    except Exception as e:
        async with session_scope() as db:
            await db.execute(UPDATE ingest_tasks SET stage='failed', error_msg=:e WHERE id=:tid)
            await db.execute(UPDATE papers SET status='failed' WHERE id=:pid AND user_id=:uid)
            await db.execute(UPDATE ingest_batches SET failed=failed+1 WHERE id=(SELECT batch_id FROM ingest_tasks WHERE id=:tid))
            await db.commit()
        raise   # 让 RQ 记录失败
```
`start_worker()` 保持不变（监听 `ingest`）。

## 4. 错误处理矩阵

| 失败点 | 处理 |
|---|---|
| 上传单文件读/传 MinIO 失败 | 该文件 task stage='failed'，batch.failed+1，继续其余 |
| 幂等命中（同 user_id+file_hash） | 跳过解析，task 直接 done |
| worker 下载 PDF / 解析异常 | ingest_tasks.failed + error_msg；papers.failed；batch.failed+1；re-raise 供 RQ |
| batch/task 不存在（GET/retry） | 404 |
| retry 非 failed 任务 | 返回提示，不重入队 |

## 5. 配置新增

`common/config.py`：
```python
MYSQL_POOL_SIZE: int = 10
```
（`.env.example` 已有 `MYSQL_POOL_SIZE=10`，仅补 Settings 字段。）

## 6. 测试策略（离线单元测试）

本地无 MySQL/MinIO/Redis 服务，依赖 `aiomysql` 已在 requirements（本地验证可 `pip install aiomysql`）。

- `test_infra_clients.py`：
  - `_mysql_url()` DSN 构造正确；`get_session` 是 async 生成器（不真实连接）。
  - minio：mock `Minio`，断言 `upload_bytes` 调 `put_object`（key/bucket/length 正确）、`download_bytes` 调 `get_object().read()`、`presigned_url` 调 `presigned_get_object`、`ensure_buckets` 建缺失桶。
  - redis：mock `Redis`/`Queue`，`get_queue` 返回 name='ingest' 的 Queue。
- `test_upload_route.py`：FastAPI `TestClient`，`app.dependency_overrides[get_session]` 注入 `AsyncMock` 会话（`execute` 返回带 `lastrowid` 的 MagicMock），monkeypatch `upload_bytes` 与 `get_queue`。断言 202、响应含 batch_id/tasks、enqueue 被调用、幂等命中跳过 enqueue。ingest GET：注入 mock 会话返回行，断言映射。
- `test_worker_handler.py`：monkeypatch `session_scope`(返回 async 上下文管理器包 AsyncMock)、`download_bytes`、`parse_paper`(AsyncMock)。断言 stage 流转（parsing→done）、`parse_paper` 收到 `pdf_bytes`；异常路径断言 failed 更新与 re-raise。
- Docker 待验证项写入 `check/`：真实三件套连通、RQ fork 行为（Linux 容器）、端到端上传一篇 PDF 看 papers/doc_blocks/figures 落地。

## 7. 验收标准
- [ ] `POST /papers/upload` 落 papers/ingest_batches/ingest_tasks 真实行，传 PDF 到 MinIO，入 RQ `ingest` 队列，202 返回 batch_id/tasks。
- [ ] 幂等：同 user_id+file_hash 重复上传不重复解析。
- [ ] worker `handle_ingest_job` 取 PDF 字节 → `parse_paper` → ingest_tasks 流转 queued→parsing→done；异常置 failed。
- [ ] `GET /ingest/batches/{id}`、`GET /ingest/tasks` 返回真实库数据；retry 重入队。
- [ ] 所有 DB 写入/查询带 `user_id`（多租户铁律）。
- [ ] 离线单测全绿；Docker 待验证项在 `check/` 文档列明。
