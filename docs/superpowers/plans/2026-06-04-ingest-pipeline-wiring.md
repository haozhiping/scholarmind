# Ingest 端到端入库链路打通 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把上传 PDF → RQ 入队 → worker 解析落库串成可运行闭环：新建 mysql/minio/redis 三个 common 客户端，改 upload 路由真实落库入队，写 worker `handle_ingest_job`，ingest 进度查询改读真实库。

**Architecture:** FastAPI 路由用 `Depends(get_session)` 注入 async 会话；MinIO/Redis 为同步 SDK，async 上下文用 `asyncio.to_thread` 包裹；RQ worker 是同步进程，`handle_ingest_job` 内 `asyncio.run(_handle_ingest_async)` 自建会话执行。管道停在解析完置 `done`（indexing 待建），`user_id` 暂硬编码 999。

**Tech Stack:** FastAPI · SQLAlchemy async(aiomysql) · minio · redis + rq · xxhash · pytest 8.3.4 + TestClient

---

**测试运行约定（所有任务统一）：** 在 `backend/` 目录运行。若 `python` 命中 WindowsApps 桩报 "Python was not found"，改用绝对路径 `C:/Users/18308/anaconda3/python.exe`。下文统一写 `python`，等价。

---

## Task 1: 配置新增 MYSQL_POOL_SIZE

**Files:**
- Modify: `backend/common/config.py:55-60`
- Test: `backend/tests/test_infra_clients.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_infra_clients.py`:

```python
from common.config import settings


def test_mysql_pool_size_config():
    assert settings.MYSQL_POOL_SIZE == 10
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_infra_clients.py::test_mysql_pool_size_config -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'MYSQL_POOL_SIZE'`

- [ ] **Step 3: 添加配置项**

In `backend/common/config.py`, in the MySQL block, after `MYSQL_DB: str = "scholarmind"`, add:

```python
    MYSQL_POOL_SIZE: int = 10
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_infra_clients.py::test_mysql_pool_size_config -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/common/config.py backend/tests/test_infra_clients.py
git commit -m "feat(common): config 新增 MYSQL_POOL_SIZE 字段

为 async engine 连接池大小提供配置入口, 默认 10, 对齐 .env.example。"
```

---

## Task 2: common/db/mysql.py — async 引擎/会话

**Files:**
- Create: `backend/common/db/mysql.py`
- Test: `backend/tests/test_infra_clients.py`

- [ ] **Step 1: 追加失败测试**

Append to `backend/tests/test_infra_clients.py`:

```python
import inspect


def test_mysql_url_format():
    from common.db import mysql
    url = mysql._mysql_url()
    assert url.startswith("mysql+aiomysql://")
    assert "?charset=utf8mb4" in url
    assert str(settings.MYSQL_DB) in url
    assert str(settings.MYSQL_HOST) in url


def test_get_session_is_async_generator():
    from common.db import mysql
    assert inspect.isasyncgenfunction(mysql.get_session)
    assert callable(mysql.session_scope)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_infra_clients.py -k "mysql_url or async_generator" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.db.mysql'`

- [ ] **Step 3: 创建 mysql.py**

Create `backend/common/db/mysql.py`:

```python
"""Async MySQL engine + session factory (business DB)."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from common.config import settings


def _mysql_url() -> str:
    return (
        f"mysql+aiomysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}?charset=utf8mb4"
    )


engine = create_async_engine(
    _mysql_url(),
    pool_size=settings.MYSQL_POOL_SIZE,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for non-request callers (worker). Caller manages commit/rollback."""
    async with SessionLocal() as session:
        yield session
```

Also create an empty package marker if missing — Create `backend/common/db/__init__.py` (empty file) if it does not already exist.

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_infra_clients.py -k "mysql_url or async_generator" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/common/db/mysql.py backend/common/db/__init__.py backend/tests/test_infra_clients.py
git commit -m "feat(common): 新增 common/db/mysql.py async 引擎与会话工厂

create_async_engine(aiomysql) + async_sessionmaker; 暴露 get_session(FastAPI 依赖)与
session_scope(worker 上下文); _mysql_url 构造 utf8mb4 DSN。"
```

---

## Task 3: common/clients/minio.py — 对象存储

**Files:**
- Create: `backend/common/clients/minio.py`
- Test: `backend/tests/test_infra_clients.py`

- [ ] **Step 1: 追加失败测试**

Append to `backend/tests/test_infra_clients.py`:

```python
from unittest.mock import MagicMock


def test_minio_upload_bytes(monkeypatch):
    from common.clients import minio as m
    fake = MagicMock()
    fake.bucket_exists.return_value = True
    monkeypatch.setattr(m, "minio_client", lambda: fake)
    m.upload_bytes("papers", "u/p/original.pdf", b"PDFDATA", "application/pdf")
    args, kwargs = fake.put_object.call_args
    assert args[0] == "papers"
    assert args[1] == "u/p/original.pdf"
    assert kwargs["length"] == len(b"PDFDATA")


def test_minio_download_bytes(monkeypatch):
    from common.clients import minio as m
    fake = MagicMock()
    resp = MagicMock()
    resp.read.return_value = b"BYTES"
    fake.get_object.return_value = resp
    monkeypatch.setattr(m, "minio_client", lambda: fake)
    assert m.download_bytes("papers", "k") == b"BYTES"
    resp.close.assert_called_once()
    resp.release_conn.assert_called_once()


def test_minio_presigned(monkeypatch):
    from common.clients import minio as m
    fake = MagicMock()
    fake.presigned_get_object.return_value = "http://signed"
    monkeypatch.setattr(m, "minio_client", lambda: fake)
    assert m.presigned_url("figures", "k") == "http://signed"


def test_minio_ensure_buckets_creates_missing(monkeypatch):
    from common.clients import minio as m
    fake = MagicMock()
    fake.bucket_exists.return_value = False
    monkeypatch.setattr(m, "minio_client", lambda: fake)
    m.ensure_buckets()
    assert fake.make_bucket.call_count == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_infra_clients.py -k minio -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.clients.minio'`

- [ ] **Step 3: 创建 minio.py**

Create `backend/common/clients/minio.py`:

```python
"""MinIO object storage client: upload/download/presigned + bucket init."""
from io import BytesIO

from minio import Minio

from common.config import settings

_client: Minio | None = None


def minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    return _client


def ensure_buckets() -> None:
    client = minio_client()
    for bucket in (settings.MINIO_BUCKET_PDF, settings.MINIO_BUCKET_FIG):
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def upload_bytes(bucket: str, key: str, data: bytes,
                 content_type: str = "application/octet-stream") -> None:
    client = minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, key, BytesIO(data), length=len(data), content_type=content_type)


def download_bytes(bucket: str, key: str) -> bytes:
    client = minio_client()
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def presigned_url(bucket: str, key: str) -> str:
    return minio_client().presigned_get_object(bucket, key)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_infra_clients.py -k minio -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/common/clients/minio.py backend/tests/test_infra_clients.py
git commit -m "feat(common): 新增 common/clients/minio.py 对象存储客户端

minio_client 单例 + ensure_buckets + upload_bytes/download_bytes/presigned_url;
均为同步, 调用方在 async 上下文用 to_thread 包裹。"
```

---

## Task 4: common/clients/redis.py — Redis + RQ 队列

**Files:**
- Create: `backend/common/clients/redis.py`
- Test: `backend/tests/test_infra_clients.py`

- [ ] **Step 1: 追加失败测试**

Append to `backend/tests/test_infra_clients.py`:

```python
def test_get_queue_named_ingest(monkeypatch):
    from common.clients import redis as r
    monkeypatch.setattr(r, "get_redis", lambda: MagicMock())
    q = r.get_queue("ingest")
    assert q.name == "ingest"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_infra_clients.py -k get_queue -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.clients.redis'`

- [ ] **Step 3: 创建 redis.py**

Create `backend/common/clients/redis.py`:

```python
"""Redis connection + RQ queue accessor."""
from redis import Redis
from rq import Queue

from common.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)
    return _redis


def get_queue(name: str = "ingest") -> Queue:
    return Queue(name, connection=get_redis())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_infra_clients.py -k get_queue -v`
Expected: PASS

- [ ] **Step 5: 运行全部基础设施测试**

Run: `python -m pytest tests/test_infra_clients.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: 提交**

```bash
git add backend/common/clients/redis.py backend/tests/test_infra_clients.py
git commit -m "feat(common): 新增 common/clients/redis.py 连接与 RQ 队列访问

get_redis 单例 + get_queue(默认 ingest 队列)。"
```

---

## Task 5: papers.py upload_papers 真实落库入队

**Files:**
- Modify: `backend/app/routers/papers.py:1-87`
- Test: `backend/tests/test_upload_route.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_upload_route.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import app.routers.papers as papers_mod
from app.main import app
from common.db.mysql import get_session


def _make_client(first_value, fake_queue):
    async def _fake_session():
        db = AsyncMock()
        db.execute.return_value = MagicMock(
            lastrowid=1, first=MagicMock(return_value=first_value)
        )
        yield db

    app.dependency_overrides[get_session] = _fake_session
    return TestClient(app)


def test_upload_enqueues_and_returns_202(monkeypatch):
    fake_queue = MagicMock()
    monkeypatch.setattr(papers_mod, "get_queue", lambda name="ingest": fake_queue)
    monkeypatch.setattr(papers_mod, "upload_bytes", lambda *a, **k: None)
    client = _make_client(first_value=None, fake_queue=fake_queue)
    try:
        resp = client.post(
            "/api/papers/upload",
            files=[("files", ("a.pdf", b"PDFDATA", "application/pdf"))],
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "batch_id" in body and len(body["tasks"]) == 1
        fake_queue.enqueue.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_upload_idempotent_skips_enqueue(monkeypatch):
    fake_queue = MagicMock()
    monkeypatch.setattr(papers_mod, "get_queue", lambda name="ingest": fake_queue)
    monkeypatch.setattr(papers_mod, "upload_bytes", lambda *a, **k: None)
    client = _make_client(first_value=(7,), fake_queue=fake_queue)  # existing paper id=7
    try:
        resp = client.post(
            "/api/papers/upload",
            files=[("files", ("a.pdf", b"PDFDATA", "application/pdf"))],
        )
        assert resp.status_code == 202
        fake_queue.enqueue.assert_not_called()
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_upload_route.py -v`
Expected: FAIL — current `upload_papers` ignores DB/queue; `fake_queue.enqueue` not called (AssertionError)

- [ ] **Step 3: 重写 upload_papers + 导入**

In `backend/app/routers/papers.py`, replace the import line and the entire `upload_papers` function. First change the top imports block (lines 1-5) to:

```python
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from typing import List, Optional
from datetime import datetime
import uuid
import asyncio
import xxhash
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.papers import PaperResponse, PaperUploadResponse, PaperDetailResponse, FolderCreate, FolderResponse
from common.db.mysql import get_session
from common.clients.minio import upload_bytes
from common.clients.redis import get_queue
from common.config import settings
from common.logging import logger

_DEFAULT_USER_ID = 999
```

Then replace the whole `async def upload_papers(...)` function (the `@router.post("/upload", ...)` block) with:

```python
@router.post("/upload", response_model=PaperUploadResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="批量上传 PDF 论文",
             description="上传一个或多个 PDF 文件，异步入库（202 立即返回）。返回 `batch_id` 和各文件对应的 `task_id`，通过 `/ingest/batches/{batch_id}` 轮询进度。")
async def upload_papers(
    files: List[UploadFile] = File(...),
    folder_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    user_id = _DEFAULT_USER_ID
    batch_result = await db.execute(
        text("INSERT INTO ingest_batches (user_id, total, status) VALUES (:uid, :total, 'running')"),
        {"uid": user_id, "total": len(files)},
    )
    batch_id = batch_result.lastrowid
    task_ids: list = []

    for f in files:
        try:
            data = await f.read()
            file_hash = xxhash.xxh64(data).hexdigest()

            existing = await db.execute(
                text("SELECT id FROM papers WHERE user_id=:uid AND file_hash=:h"),
                {"uid": user_id, "h": file_hash},
            )
            row = existing.first()
            if row:  # idempotent: same file already ingested
                paper_id = row[0]
                t = await db.execute(
                    text("""INSERT INTO ingest_tasks
                            (batch_id, user_id, paper_id, file_name, file_hash, stage, progress)
                            VALUES (:bid, :uid, :pid, :fn, :h, 'done', 100)"""),
                    {"bid": batch_id, "uid": user_id, "pid": paper_id, "fn": f.filename, "h": file_hash},
                )
                task_ids.append(t.lastrowid)
                continue

            p = await db.execute(
                text("""INSERT INTO papers (user_id, folder_id, title, file_hash, pdf_key, status)
                        VALUES (:uid, :fid, :title, :h, '', 'pending')"""),
                {"uid": user_id, "fid": folder_id, "title": f.filename, "h": file_hash},
            )
            paper_id = p.lastrowid
            pdf_key = f"{user_id}/{paper_id}/original.pdf"
            await asyncio.to_thread(upload_bytes, settings.MINIO_BUCKET_PDF, pdf_key, data, "application/pdf")
            await db.execute(
                text("UPDATE papers SET pdf_key=:k WHERE id=:pid AND user_id=:uid"),
                {"k": pdf_key, "pid": paper_id, "uid": user_id},
            )
            t = await db.execute(
                text("""INSERT INTO ingest_tasks
                        (batch_id, user_id, paper_id, file_name, file_hash, stage, progress)
                        VALUES (:bid, :uid, :pid, :fn, :h, 'queued', 0)"""),
                {"bid": batch_id, "uid": user_id, "pid": paper_id, "fn": f.filename, "h": file_hash},
            )
            task_id = t.lastrowid
            get_queue("ingest").enqueue(
                "app.worker.main.handle_ingest_job",
                user_id=user_id, paper_id=paper_id, pdf_key=pdf_key, task_id=task_id,
            )
            task_ids.append(task_id)
        except Exception as e:
            logger.warning(f"[upload] file {getattr(f, 'filename', '?')} failed: {e}")
            await db.execute(
                text("UPDATE ingest_batches SET failed=failed+1 WHERE id=:bid"),
                {"bid": batch_id},
            )

    await db.commit()
    return PaperUploadResponse(batch_id=str(batch_id), tasks=[str(t) for t in task_ids])
```

(Leave `list_papers` / `get_paper_detail` / `delete_paper` / folders endpoints unchanged — out of scope.)

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_upload_route.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/routers/papers.py backend/tests/test_upload_route.py
git commit -m "feat(parsing): papers.upload 真实落库+传MinIO+入RQ队列(幂等)

upload_papers 用 Depends(get_session): 建 ingest_batches, 逐文件 xxh64 去重(命中则 task 直接 done),
否则插 papers 拿 paper_id→传 PDF 到 MinIO papers bucket→回填 pdf_key→插 ingest_tasks→
enqueue handle_ingest_job; 单文件失败计 batch.failed 不影响整批; 返回 batch_id/tasks。"
```

---

## Task 6: ingest.py 进度查询读真实库 + retry 重入队

**Files:**
- Modify: `backend/app/routers/ingest.py` (全文替换)
- Test: `backend/tests/test_upload_route.py`

- [ ] **Step 1: 追加失败测试**

Append to `backend/tests/test_upload_route.py`:

```python
from datetime import datetime
import app.routers.ingest as ingest_mod


def _client_with_session(execute_return):
    async def _fake_session():
        db = AsyncMock()
        db.execute.return_value = execute_return
        yield db

    app.dependency_overrides[get_session] = _fake_session
    return TestClient(app)


def test_get_batch_progress_completed():
    now = datetime.now()
    ret = MagicMock(first=MagicMock(return_value=(5, 2, 2, 0, "running", now)))
    client = _client_with_session(ret)
    try:
        resp = client.get("/api/ingest/batches/5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["total_tasks"] == 2 and body["completed_tasks"] == 2
    finally:
        app.dependency_overrides.clear()


def test_get_batch_progress_404():
    ret = MagicMock(first=MagicMock(return_value=None))
    client = _client_with_session(ret)
    try:
        resp = client.get("/api/ingest/batches/999")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_list_tasks_maps_stage():
    now = datetime.now()
    ret = MagicMock(all=MagicMock(return_value=[(1, 7, "done", 100.0, None, now)]))
    client = _client_with_session(ret)
    try:
        resp = client.get("/api/ingest/tasks")
        assert resp.status_code == 200
        rows = resp.json()
        assert rows[0]["status"] == "completed" and rows[0]["stage"] == "done"
        assert rows[0]["paper_id"] == 7
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_upload_route.py -k "batch_progress or list_tasks" -v`
Expected: FAIL — current ingest.py reads MOCK, returns wrong shape / 200 instead of 404

- [ ] **Step 3: 全文替换 ingest.py**

Replace the entire contents of `backend/app/routers/ingest.py` with:

```python
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ingest import IngestBatchResponse, IngestTaskResponse, TaskRetryResponse
from common.db.mysql import get_session
from common.clients.redis import get_queue

router = APIRouter(prefix="/ingest", tags=["ingest"])

_STAGE_TO_STATUS = {
    "queued": "pending",
    "parsing": "parsing",
    "indexing": "indexing",
    "done": "completed",
    "failed": "failed",
}


@router.get("/batches/{batch_id}", response_model=IngestBatchResponse,
            summary="批次解析进度",
            description="查询一次批量上传的整体进度，返回总任务数、已完成数、失败数及状态（processing/completed/failed）。前端上传后轮询此接口。")
async def get_batch_progress(batch_id: str, db: AsyncSession = Depends(get_session)):
    try:
        bid = int(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Batch not found")
    result = await db.execute(
        text("SELECT id, total, done, failed, status, created_at FROM ingest_batches WHERE id=:bid"),
        {"bid": bid},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Batch not found")
    total, done, failed = row[1], row[2], row[3]
    if total > 0 and done + failed >= total:
        status_str = "failed" if failed > 0 else "completed"
    else:
        status_str = "processing"
    return IngestBatchResponse(
        batch_id=str(row[0]), status=status_str, total_tasks=total,
        completed_tasks=done, failed_tasks=failed, created_at=row[5],
    )


@router.get("/tasks", response_model=List[IngestTaskResponse],
            summary="解析任务列表",
            description="查询单个或所有解析任务的详细状态，包含当前阶段（queued/parsing/indexing/done）、进度百分比和错误信息。可按 `batch_id` 过滤。")
async def list_tasks(batch_id: Optional[str] = None, db: AsyncSession = Depends(get_session)):
    if batch_id is not None:
        try:
            bid = int(batch_id)
        except ValueError:
            return []
        result = await db.execute(
            text("""SELECT id, paper_id, stage, progress, error_msg, created_at
                    FROM ingest_tasks WHERE batch_id=:bid"""),
            {"bid": bid},
        )
    else:
        result = await db.execute(
            text("SELECT id, paper_id, stage, progress, error_msg, created_at FROM ingest_tasks")
        )
    out: list = []
    for r in result.all():
        stage = r[2]
        out.append(IngestTaskResponse(
            id=str(r[0]), paper_id=r[1] or 0,
            status=_STAGE_TO_STATUS.get(stage, stage), stage=stage,
            progress=float(r[3]), error=r[4], updated_at=r[5],
        ))
    return out


@router.post("/tasks/{id}/retry", response_model=TaskRetryResponse,
             summary="重试失败任务",
             description="将 `failed` 状态的解析任务重新入队，从头开始解析。任务 stage 重置为 queued，progress 重置为 0。")
async def retry_task(id: str, db: AsyncSession = Depends(get_session)):
    try:
        tid = int(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found")
    result = await db.execute(
        text("""SELECT t.id, t.stage, t.user_id, t.paper_id, p.pdf_key
                FROM ingest_tasks t JOIN papers p ON p.id = t.paper_id
                WHERE t.id = :tid"""),
        {"tid": tid},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if row[1] != "failed":
        return TaskRetryResponse(task_id=id, status=row[1],
                                 message="Task is not in failed state; not re-queued.")
    await db.execute(
        text("""UPDATE ingest_tasks
                SET stage='queued', progress=0, error_msg=NULL, retry_count=retry_count+1
                WHERE id=:tid"""),
        {"tid": tid},
    )
    await db.commit()
    get_queue("ingest").enqueue(
        "app.worker.main.handle_ingest_job",
        user_id=row[2], paper_id=row[3], pdf_key=row[4], task_id=tid,
    )
    return TaskRetryResponse(task_id=id, status="pending",
                             message="Task has been successfully queued for retry.")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_upload_route.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/routers/ingest.py backend/tests/test_upload_route.py
git commit -m "feat(parsing): ingest 进度查询读真实库 + retry 重入队

batches/{id} 与 tasks GET 改读 ingest_batches/ingest_tasks 真实行, status 由 stage 与
done/failed/total 推导; retry 仅对 failed 任务重置并重新 enqueue handle_ingest_job(pdf_key 取自 papers)。"
```

---

## Task 7: worker handle_ingest_job

**Files:**
- Modify: `backend/app/worker/main.py`
- Test: `backend/tests/test_worker_handler.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_worker_handler.py`:

```python
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


def _patch_deps(monkeypatch, db, parse_paper, download_ret=b"PDF", parse_raises=None):
    @asynccontextmanager
    async def fake_scope():
        yield db

    monkeypatch.setattr("common.db.mysql.session_scope", fake_scope)
    monkeypatch.setattr("common.clients.minio.download_bytes", lambda b, k: download_ret)
    monkeypatch.setattr("services.parsing.parser.parse_paper", parse_paper)


def _sql_calls(db):
    return " ".join(str(c.args[0]) for c in db.execute.await_args_list)


def test_handle_ingest_success(monkeypatch):
    import app.worker.main as wm
    db = AsyncMock()
    parse_paper = AsyncMock()
    _patch_deps(monkeypatch, db, parse_paper)

    asyncio.run(wm._handle_ingest_async(999, 2, "999/2/original.pdf", 5))

    # parse_paper received pdf_bytes
    assert parse_paper.await_args.kwargs["pdf_bytes"] == b"PDF"
    sql = _sql_calls(db)
    assert "stage='parsing'" in sql
    assert "stage='done'" in sql


def test_handle_ingest_failure_marks_failed(monkeypatch):
    import app.worker.main as wm
    db = AsyncMock()

    async def boom(*a, **k):
        raise RuntimeError("parse exploded")

    _patch_deps(monkeypatch, db, boom)

    with pytest.raises(RuntimeError):
        asyncio.run(wm._handle_ingest_async(999, 2, "k", 5))

    sql = _sql_calls(db)
    assert "stage='failed'" in sql
    assert "UPDATE papers SET status='failed'" in sql
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_worker_handler.py -v`
Expected: FAIL — `AttributeError: module 'app.worker.main' has no attribute '_handle_ingest_async'`

- [ ] **Step 3: 重写 worker/main.py**

Replace the entire contents of `backend/app/worker/main.py` with:

```python
import asyncio
from datetime import datetime

from redis import Redis
from rq import Queue, Worker, Connection
from sqlalchemy import text

from common.config import settings
from common.logging import logger


def handle_ingest_job(user_id: int, paper_id: int, pdf_key: str, task_id: int):
    """RQ job entrypoint (sync). Bridges to async pipeline via asyncio.run."""
    return asyncio.run(_handle_ingest_async(user_id, paper_id, pdf_key, task_id))


async def _handle_ingest_async(user_id: int, paper_id: int, pdf_key: str, task_id: int) -> None:
    from common.db.mysql import session_scope
    from common.clients.minio import download_bytes
    from services.parsing.parser import parse_paper

    try:
        async with session_scope() as db:
            await db.execute(
                text("UPDATE ingest_tasks SET stage='parsing', progress=10, started_at=:now WHERE id=:tid"),
                {"now": datetime.now(), "tid": task_id},
            )
            await db.commit()

            pdf_bytes = await asyncio.to_thread(download_bytes, settings.MINIO_BUCKET_PDF, pdf_key)
            await parse_paper(user_id, paper_id, pdf_key, db, pdf_bytes=pdf_bytes)

            await db.execute(
                text("UPDATE ingest_tasks SET stage='done', progress=100, finished_at=:now WHERE id=:tid"),
                {"now": datetime.now(), "tid": task_id},
            )
            await db.execute(
                text("""UPDATE ingest_batches SET done=done+1
                        WHERE id=(SELECT batch_id FROM ingest_tasks WHERE id=:tid)"""),
                {"tid": task_id},
            )
            await db.commit()
        logger.info(f"[worker] ingest done task_id={task_id} paper_id={paper_id}")
    except Exception as e:
        logger.error(f"[worker] ingest failed task_id={task_id}: {e}")
        async with session_scope() as db:
            await db.execute(
                text("UPDATE ingest_tasks SET stage='failed', error_msg=:e WHERE id=:tid"),
                {"e": str(e), "tid": task_id},
            )
            await db.execute(
                text("UPDATE papers SET status='failed' WHERE id=:pid AND user_id=:uid"),
                {"pid": paper_id, "uid": user_id},
            )
            await db.execute(
                text("""UPDATE ingest_batches SET failed=failed+1
                        WHERE id=(SELECT batch_id FROM ingest_tasks WHERE id=:tid)"""),
                {"tid": task_id},
            )
            await db.commit()
        raise


def start_worker():
    logger.info("Starting ScholarMind RQ Worker...")
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
    )
    queue_name = "ingest"
    with Connection(redis_conn):
        worker = Worker([Queue(queue_name)])
        logger.info(f"Worker listening on queue: {queue_name}")
        worker.work()


if __name__ == "__main__":
    start_worker()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_worker_handler.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `python -m pytest tests -v`
Expected: PASS (all tests, ~37 passed)

- [ ] **Step 6: 提交**

```bash
git add backend/app/worker/main.py backend/tests/test_worker_handler.py
git commit -m "feat(parsing): worker handle_ingest_job 驱动解析全流程

同步 handle_ingest_job 经 asyncio.run 桥接到 _handle_ingest_async; session_scope 自建会话,
从 MinIO 取 PDF 字节→parse_paper→ingest_tasks 流转 parsing→done 并累加 batch.done;
异常分支另开会话置 ingest_tasks.failed/papers.failed/batch.failed 并 re-raise 供 RQ。"
```

---

## Task 8: 验证文档 + 收尾

**Files:**
- Create: `docs/STATUS.md`（更新入库链路验证记录）

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest tests -v`
记录通过数，用于报告。

- [ ] **Step 2: 编写验证报告**

Create `docs/STATUS.md` documenting:
- 实现摘要（3 客户端 + upload + ingest + worker 各做了什么）
- 数据流图（上传→入队→worker→解析落库）
- 离线单测覆盖项 + `python -m pytest tests` 结果
- **Docker 环境待验证项**：真实 MySQL/MinIO/Redis 连通；RQ worker 在 Linux 容器 fork 行为；端到端上传一篇真实 PDF（需 MinerU 凭据）后核对 papers/doc_blocks/figures/citations/ingest_tasks 落地与状态流转
- 已知技术债（parser 内联 MinIO 与 common/clients/minio.py 短期重复，待合并）
- user_id=999 常量待 JWT 中间件替换

内容须如实反映测试实际结果。

- [ ] **Step 3: 提交**

```bash
git add docs/STATUS.md
git commit -m "docs: 更新 STATUS.md 记录 ingest 端到端入库链路验证结果

记录三客户端+upload+ingest+worker 实现、数据流、离线单测覆盖与结果、
Docker 待验证项、MinIO 重复技术债与 user_id 常量待替换说明。"
```

- [ ] **Step 4: 更新 MEMORY.md（如有跨会话洞察）**

If worth persisting, append to `MEMORY.md` a line about: RQ worker 同步进程用 `asyncio.run` 桥接 async + 自建 `session_scope` 会话；upload 先插 papers 拿 paper_id 再传 MinIO 回填 pdf_key（与 block_id 同模式）；管道现停在解析完 done（indexing 待建）。Then commit.

---

## Self-Review

**Spec coverage:**
- common/db/mysql.py（get_session/session_scope/_mysql_url）→ Task 2 ✅
- common/clients/minio.py（minio_client/ensure_buckets/upload_bytes/download_bytes/presigned_url）→ Task 3 ✅
- common/clients/redis.py（get_redis/get_queue）→ Task 4 ✅
- papers.upload 真实落库+传MinIO+入队+幂等 → Task 5 ✅
- ingest GET 读真实库 + retry 重入队 → Task 6 ✅
- worker handle_ingest_job + _handle_ingest_async（parsing→done / 异常 failed + re-raise）→ Task 7 ✅
- config MYSQL_POOL_SIZE → Task 1 ✅
- 错误矩阵（单文件失败/幂等/worker 异常/404/非failed retry）→ Task 5/6/7 测试与实现 ✅
- 多租户 user_id 过滤 → 所有 SQL 带 user_id（upload/worker papers 更新）✅
- docs/STATUS.md 验证文档 → Task 8 ✅
- 测试策略（离线 + Docker 待验证标注）→ Task 2-7 单测 + Task 8 文档 ✅

**Placeholder scan:** 无 TBD/TODO；每个改代码步骤含完整代码。

**Type consistency:** `get_session`/`session_scope`/`_mysql_url`（Task2）、`minio_client`/`upload_bytes`/`download_bytes`/`presigned_url`/`ensure_buckets`(Task3)、`get_redis`/`get_queue`(Task4)、`handle_ingest_job`/`_handle_ingest_async`(Task7) 跨任务命名一致；RQ enqueue 目标字符串统一 `"app.worker.main.handle_ingest_job"`（Task5/6/7）；job kwargs 统一 `user_id/paper_id/pdf_key/task_id`；parse_paper 调用统一 `(user_id, paper_id, pdf_key, db, pdf_bytes=...)` 与任务1签名一致。
