"""
Papers + Folders router — real implementation.

upload flow (per tasks_guide / data-contracts):
  1. xxhash64(pdf bytes) -> file_hash (idempotent per user)
  2. insert papers (status=pending) -> paper_id
  3. upload PDF to MinIO papers bucket: {user_id}/{paper_id}/original.pdf
  4. insert ingest_batches + ingest_tasks (stage=queued)
  5. enqueue handle_ingest_job to RQ ingest queue
  6. return {batch_id, tasks}
"""
import asyncio
import json
from typing import List, Optional

import xxhash
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.papers import (
    FolderCreate,
    FolderResponse,
    PaperDetailResponse,
    PaperResponse,
    PaperUploadResponse,
)
from common.auth import get_current_user_id
from common.clients.minio import ensure_bucket, upload_bytes
from common.clients.redis import get_ingest_queue
from common.config import settings
from common.db.mysql import get_db
from common.logging import logger

router = APIRouter(prefix="/papers", tags=["papers"])
folders_router = APIRouter(prefix="/folders", tags=["folders"])


def _authors_to_str(raw) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, list):
        return ", ".join(str(a) for a in raw)
    return str(raw)


def _row_to_paper(row: dict) -> PaperDetailResponse:
    return PaperDetailResponse(
        id=row["id"],
        title=row["title"],
        authors=_authors_to_str(row.get("authors")),
        journal=None,
        year=row.get("year"),
        abstract=row.get("abstract"),
        folder_id=row.get("folder_id"),
        status=row["status"],
        file_key=row.get("pdf_key") or "",
        file_size=0,
        pages=row.get("num_pages") or 0,
        created_at=row["created_at"],
        batch_id=None,
        meta_data={"chunk_count": row.get("chunk_count", 0)},
    )


# --- Papers ---------------------------------------------------------------

@router.post("/upload", response_model=PaperUploadResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="批量上传 PDF 论文",
             description="上传一个或多个 PDF，异步入库（202 立即返回）。返回 batch_id 与 task_id 列表。")
async def upload_papers(
    files: List[UploadFile] = File(...),
    folder_id: Optional[int] = Form(None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")

    # Batch row
    batch_res = await db.execute(
        text("INSERT INTO ingest_batches (user_id, total, status) VALUES (:u, :t, 'running')"),
        {"u": user_id, "t": len(files)},
    )
    batch_id = batch_res.lastrowid

    await asyncio.to_thread(ensure_bucket, settings.MINIO_BUCKET_PDF)

    task_ids: List[str] = []
    queue = get_ingest_queue()

    for f in files:
        content = await f.read()
        file_hash = xxhash.xxh64(content).hexdigest()  # 16 hex chars
        filename = f.filename or "untitled.pdf"
        title = filename[:-4] if filename.lower().endswith(".pdf") else filename

        # Idempotency: same (user_id, file_hash) already ingested?
        existing = await db.execute(
            text("SELECT id, status FROM papers WHERE user_id = :u AND file_hash = :h"),
            {"u": user_id, "h": file_hash},
        )
        ex_row = existing.mappings().first()

        if ex_row:
            paper_id = ex_row["id"]
            task_res = await db.execute(
                text("""
                    INSERT INTO ingest_tasks (batch_id, user_id, paper_id, file_name, file_hash, stage, progress)
                    VALUES (:b, :u, :p, :fn, :fh, 'done', 100)
                """),
                {"b": batch_id, "u": user_id, "p": paper_id, "fn": filename, "fh": file_hash},
            )
            task_ids.append(str(task_res.lastrowid))
            logger.info(f"[upload] duplicate file_hash={file_hash} reused paper_id={paper_id}")
            continue

        # New paper
        paper_res = await db.execute(
            text("""
                INSERT INTO papers (user_id, folder_id, title, source, file_hash, pdf_key, status)
                VALUES (:u, :fid, :title, 'upload', :fh, 'pending', 'pending')
            """),
            {"u": user_id, "fid": folder_id, "title": title, "fh": file_hash},
        )
        paper_id = paper_res.lastrowid
        pdf_key = f"{user_id}/{paper_id}/original.pdf"

        try:
            await asyncio.to_thread(
                upload_bytes, settings.MINIO_BUCKET_PDF, pdf_key, content, "application/pdf"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[upload] MinIO upload failed for {filename}: {e}")

        await db.execute(
            text("UPDATE papers SET pdf_key = :k WHERE id = :id"),
            {"k": pdf_key, "id": paper_id},
        )

        task_res = await db.execute(
            text("""
                INSERT INTO ingest_tasks (batch_id, user_id, paper_id, file_name, file_hash, stage, progress)
                VALUES (:b, :u, :p, :fn, :fh, 'queued', 0)
            """),
            {"b": batch_id, "u": user_id, "p": paper_id, "fn": filename, "fh": file_hash},
        )
        task_id = task_res.lastrowid
        task_ids.append(str(task_id))

        # Must commit before enqueue so the worker can read task/paper rows.
        await db.commit()

        try:
            queue.enqueue(
                "app.worker.tasks.handle_ingest_job",
                task_id=task_id,
                paper_id=paper_id,
                user_id=user_id,
                pdf_key=pdf_key,
                job_timeout=1800,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[upload] enqueue failed task_id={task_id}: {e}")
            await db.execute(
                text("UPDATE ingest_tasks SET stage='failed', error_msg=:m WHERE id=:id"),
                {"m": f"enqueue failed: {e}", "id": task_id},
            )

    return PaperUploadResponse(batch_id=str(batch_id), tasks=task_ids)


@router.get("", response_model=List[PaperResponse],
            summary="论文列表",
            description="获取当前用户论文列表，支持按 folder_id / status 过滤。")
async def list_papers(
    folder_id: Optional[int] = None,
    status: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    sql = "SELECT * FROM papers WHERE user_id = :u"
    params: dict = {"u": user_id}
    if folder_id is not None:
        sql += " AND folder_id = :fid"
        params["fid"] = folder_id
    if status is not None:
        sql += " AND status = :st"
        params["st"] = status
    sql += " ORDER BY created_at DESC"
    res = await db.execute(text(sql), params)
    return [_row_to_paper(dict(r)) for r in res.mappings().all()]


@router.get("/{id}", response_model=PaperDetailResponse,
            summary="论文详情", description="获取单篇论文完整信息。")
async def get_paper_detail(id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("SELECT * FROM papers WHERE id = :id AND user_id = :u"),
        {"id": id, "u": user_id},
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="论文不存在")
    return _row_to_paper(dict(row))


@router.delete("/{id}", status_code=status.HTTP_200_OK,
               summary="删除论文",
               description="删除论文，连带清理 doc_blocks / citations / Milvus 向量。")
async def delete_paper(id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("SELECT id FROM papers WHERE id = :id AND user_id = :u"),
        {"id": id, "u": user_id},
    )
    if not res.first():
        raise HTTPException(status_code=404, detail="论文不存在")

    await db.execute(text("DELETE FROM doc_blocks WHERE paper_id = :id AND user_id = :u"), {"id": id, "u": user_id})
    await db.execute(text("DELETE FROM citations WHERE src_paper_id = :id"), {"id": id})
    await db.execute(text("DELETE FROM papers WHERE id = :id AND user_id = :u"), {"id": id, "u": user_id})

    try:
        from common.clients.milvus import delete_by_paper
        await asyncio.to_thread(delete_by_paper, user_id, id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[delete] milvus cleanup failed: {e}")

    return {"status": "success", "message": f"论文 {id} 已删除"}


# --- Folders --------------------------------------------------------------

@folders_router.get("", response_model=List[FolderResponse],
                    summary="文件夹列表", description="获取当前用户的文件夹及论文数量。")
async def list_folders(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("""
            SELECT f.id, f.name, f.parent_id, f.created_at,
                   (SELECT COUNT(*) FROM papers p WHERE p.folder_id = f.id AND p.user_id = f.user_id) AS paper_count
            FROM folders f
            WHERE f.user_id = :u
            ORDER BY f.created_at ASC
        """),
        {"u": user_id},
    )
    return [
        FolderResponse(
            id=r["id"], name=r["name"], parent_id=r["parent_id"],
            paper_count=r["paper_count"], created_at=r["created_at"],
        )
        for r in res.mappings().all()
    ]


@folders_router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED,
                     summary="创建文件夹", description="新建论文文件夹，支持嵌套（parent_id）。")
async def create_folder(folder_data: FolderCreate, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("INSERT INTO folders (user_id, name, parent_id) VALUES (:u, :n, :p)"),
        {"u": user_id, "n": folder_data.name, "p": folder_data.parent_id},
    )
    new_id = res.lastrowid
    row = await db.execute(text("SELECT created_at FROM folders WHERE id = :id"), {"id": new_id})
    created_at = row.scalar()
    return FolderResponse(id=new_id, name=folder_data.name, parent_id=folder_data.parent_id, paper_count=0, created_at=created_at)


@folders_router.delete("/{id}", status_code=status.HTTP_200_OK,
                       summary="删除文件夹", description="删除文件夹，其中论文归为未分类（folder_id=NULL）。")
async def delete_folder(id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(text("SELECT id FROM folders WHERE id = :id AND user_id = :u"), {"id": id, "u": user_id})
    if not res.first():
        raise HTTPException(status_code=404, detail="文件夹不存在")
    await db.execute(text("UPDATE papers SET folder_id = NULL WHERE folder_id = :id AND user_id = :u"), {"id": id, "u": user_id})
    await db.execute(text("DELETE FROM folders WHERE id = :id AND user_id = :u"), {"id": id, "u": user_id})
    return {"status": "success", "message": f"文件夹 {id} 已删除"}
