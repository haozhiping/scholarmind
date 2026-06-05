import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from redis import Redis
from rq import Queue

from app.schemas.papers import (
    FolderCreate, FolderResponse, PaperDetailResponse, PaperResponse, PaperUploadResponse,
)
from common.auth.deps import get_current_user
from common.clients.minio import upload_bytes
from common.config import settings
from common.db.mysql_client import mysql
from common.exceptions import NotFoundException

router = APIRouter(prefix="/papers", tags=["papers"])
folders_router = APIRouter(prefix="/folders", tags=["folders"])


# Map DB status (pending|done|failed) to the richer set the frontend renders.
_STATUS_DISPLAY = {"done": "completed"}


def _paper_row_to_response(row: Dict[str, Any]) -> PaperDetailResponse:
    """Adapt a MySQL `papers` row to the API response contract.

    DB stores authors as a JSON array and uses pdf_key/num_pages; the frontend
    contract expects an authors string and file_key/pages. file_size is not
    persisted (no column) → 0; journal/batch_id are not on papers → None.
    """
    authors = row.get("authors")
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except json.JSONDecodeError:
            authors = None
    authors_str = ", ".join(authors) if isinstance(authors, list) else (authors or None)

    db_status = row.get("status") or "pending"
    return PaperDetailResponse(
        id=row["id"],
        title=row["title"],
        authors=authors_str,
        journal=None,
        year=row.get("year"),
        abstract=row.get("abstract"),
        folder_id=row.get("folder_id"),
        status=_STATUS_DISPLAY.get(db_status, db_status),
        file_key=row.get("pdf_key") or "",
        file_size=int(row.get("file_size") or 0),
        pages=row.get("num_pages") or 0,
        created_at=row["created_at"],
        batch_id=None,
        meta_data={},
    )


# --- Papers Router Endpoints ---

@router.post("/upload", response_model=PaperUploadResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="批量上传 PDF 论文",
             description="上传一个或多个 PDF 文件，异步入库（202 立即返回）。返回 `batch_id` 和各文件对应的 `task_id`，通过 `/ingest/batches/{batch_id}` 轮询进度。")
async def upload_papers(
    files: List[UploadFile] = File(...),
    folder_id: Optional[int] = Form(None),
    current=Depends(get_current_user),
):
    batch_id = f"batch-{uuid.uuid4().hex[:8]}"
    task_ids: List[str] = []
    # Initialize RQ queue connection (shared for all files in this batch)
    redis_conn = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)
    rq_queue = Queue("ingest", connection=redis_conn)

    for file in files:
        # Derive a paper title from the uploaded filename
        filename = file.filename or "untitled.pdf"
        title = filename.rsplit(".pdf", 1)[0].strip() or filename
        # Calculate filesize and hash
        content = await file.read()
        file_size = len(content)
        file_hash_str = hashlib.md5(content).hexdigest()[:16]
        file_key = f"papers/{current['id']}/{uuid.uuid4().hex[:12]}/{filename}"

        # 1) Store PDF in MinIO (async via thread)
        import asyncio
        await asyncio.to_thread(
            upload_bytes, content, settings.MINIO_BUCKET_PDF, file_key,
            "application/pdf",
        )

        # 2) Insert paper record into DB
        paper_id = await mysql.execute(
            "INSERT INTO papers (user_id, title, pdf_key, folder_id, file_hash, file_size, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
            current["id"], title, file_key, folder_id, file_hash_str, file_size,
        )
        task_uuid = f"task-{uuid.uuid4().hex[:12]}"
        task_ids.append(task_uuid)

        # 3) Insert ingest task record
        await mysql.execute(
            "INSERT INTO ingest_tasks (task_id, paper_id, user_id, status, stage, progress, batch_id) "
            "VALUES (%s, %s, %s, 'pending', 'parsing', 0, %s)",
            task_uuid, paper_id, current["id"], batch_id,
        )

        # 4) Enqueue RQ job to kick off parsing → indexing pipeline
        rq_queue.enqueue(
            "app.worker.main.handle_ingest_job",
            user_id=current["id"],
            paper_id=paper_id,
            pdf_key=file_key,
            task_id=task_uuid,
        )

    return PaperUploadResponse(batch_id=batch_id, tasks=task_ids)


@router.get("", response_model=List[PaperResponse],
            summary="论文列表",
            description="获取当前用户的论文列表，支持按文件夹（`folder_id`）和解析状态（`pending/parsing/indexing/completed/failed`）过滤。")
async def list_papers(
    folder_id: Optional[int] = None,
    status: Optional[str] = None,
    current=Depends(get_current_user),
):
    sql = "SELECT * FROM papers WHERE user_id=%s"
    args: List[Any] = [current["id"]]
    if folder_id is not None:
        sql += " AND folder_id=%s"
        args.append(folder_id)
    if status is not None:
        # Frontend sends "completed", DB stores "done"
        db_status = "done" if status == "completed" else status
        sql += " AND status=%s"
        args.append(db_status)
    sql += " ORDER BY created_at DESC"
    rows = await mysql.fetchall(sql, *args)
    return [_paper_row_to_response(r) for r in rows]


@router.get("/{id}", response_model=PaperDetailResponse,
            summary="论文详情",
            description="获取单篇论文的完整信息，包含标题、作者、摘要、解析状态、MinIO 文件路径及附加元数据。")
async def get_paper_detail(id: int, current=Depends(get_current_user)):
    row = await mysql.fetchone(
        "SELECT * FROM papers WHERE id=%s AND user_id=%s", id, current["id"]
    )
    if not row:
        raise NotFoundException("论文不存在")
    return _paper_row_to_response(row)


@router.delete("/{id}", status_code=status.HTTP_200_OK,
               summary="删除论文",
               description="删除指定论文，同步清理 MinIO 中的 PDF/图片文件及 Milvus 中对应的所有 chunk 向量。")
async def delete_paper(id: int, current=Depends(get_current_user)):
    deleted = await mysql.execute_rowcount(
        "DELETE FROM papers WHERE id=%s AND user_id=%s", id, current["id"]
    )
    if deleted == 0:
        raise NotFoundException("论文不存在")
    # Clean up parent blocks (user-scoped). MinIO/Milvus cleanup is handled
    # in the ingest link where those clients live.
    await mysql.execute_rowcount(
        "DELETE FROM doc_blocks WHERE paper_id=%s AND user_id=%s", id, current["id"]
    )
    return {"status": "success", "message": f"Paper {id} has been deleted successfully."}


# --- Folders Router Endpoints ---

@folders_router.get("", response_model=List[FolderResponse],
                    summary="文件夹列表",
                    description="获取当前用户的所有文件夹及每个文件夹的论文数量。")
async def list_folders(current=Depends(get_current_user)):
    rows = await mysql.fetchall(
        "SELECT f.id, f.name, f.parent_id, f.created_at, "
        "COUNT(p.id) AS paper_count "
        "FROM folders f "
        "LEFT JOIN papers p ON p.folder_id = f.id AND p.user_id = %s "
        "WHERE f.user_id = %s "
        "GROUP BY f.id, f.name, f.parent_id, f.created_at "
        "ORDER BY f.created_at ASC",
        current["id"], current["id"],
    )
    return [
        FolderResponse(
            id=r["id"],
            name=r["name"],
            parent_id=r["parent_id"],
            paper_count=int(r["paper_count"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


@folders_router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED,
                     summary="创建文件夹",
                     description="新建论文文件夹，支持嵌套（传 `parent_id`）。")
async def create_folder(folder_data: FolderCreate, current=Depends(get_current_user)):
    folder_id = await mysql.execute(
        "INSERT INTO folders (user_id, name, parent_id) VALUES (%s, %s, %s)",
        current["id"], folder_data.name, folder_data.parent_id,
    )
    row = await mysql.fetchone(
        "SELECT id, name, parent_id, created_at FROM folders WHERE id=%s", folder_id
    )
    return FolderResponse(
        id=row["id"],
        name=row["name"],
        parent_id=row["parent_id"],
        paper_count=0,
        created_at=row["created_at"],
    )


@folders_router.delete("/{id}", status_code=status.HTTP_200_OK,
                       summary="删除文件夹",
                       description="删除指定文件夹；该文件夹下论文的 `folder_id` 置空（不删除论文本身）。")
async def delete_folder(id: int, current=Depends(get_current_user)):
    deleted = await mysql.execute_rowcount(
        "DELETE FROM folders WHERE id=%s AND user_id=%s", id, current["id"]
    )
    if deleted == 0:
        raise NotFoundException("文件夹不存在")
    # Detach papers rather than orphaning their folder_id.
    await mysql.execute_rowcount(
        "UPDATE papers SET folder_id=NULL WHERE folder_id=%s AND user_id=%s",
        id, current["id"],
    )
    return {"status": "success", "message": f"Folder {id} has been deleted successfully."}
