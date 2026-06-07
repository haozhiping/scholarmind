"""
Milvus client: collection lifecycle, hybrid (dense + sparse) search, insert, delete.

Design notes:
- Collection `scholarmind_chunks` per docs/data-contracts.md.
- dense_vec: HNSW / COSINE (EMBEDDING_DIM). sparse_vec: SPARSE_INVERTED_INDEX / IP.
- user_id is the partition key (tenant isolation); every query MUST pass a user_id filter.
- All operations degrade gracefully: if Milvus is unreachable, functions log and return
  empty/no-op instead of raising, so the API stays up.

Sparse vectors are produced by a lightweight, dependency-free BM25-style term-frequency
encoder (token -> hashed id -> weight). This keeps hybrid search working without pulling in
FlagEmbedding/BGE-M3, while still satisfying the collection schema.
"""
from __future__ import annotations

import math
import re
import threading
from typing import Any, Optional

from common.config import settings
from common.logging import logger

COLLECTION = settings.MILVUS_COLLECTION
_DIM = settings.EMBEDDING_DIM
_SPARSE_BUCKETS = 2_000_003  # large prime hashing space for sparse token ids

_connected = False
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Connection + collection lifecycle
# ---------------------------------------------------------------------------

def _connect() -> bool:
    """Connect to Milvus once. Returns True on success."""
    global _connected
    if _connected:
        return True
    with _lock:
        if _connected:
            return True
        try:
            from pymilvus import connections

            uri = settings.MILVUS_URI
            connections.connect(alias="default", uri=uri, token=settings.MILVUS_TOKEN or "")
            _connected = True
            logger.info(f"[milvus] connected: {uri}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[milvus] connect failed: {e}")
            return False


def ensure_collection() -> bool:
    """Create the collection + indexes if missing, then load it. Returns True if ready."""
    if not _connect():
        return False
    try:
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            utility,
        )

        if not utility.has_collection(COLLECTION):
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
                FieldSchema(name="dense_vec", dtype=DataType.FLOAT_VECTOR, dim=_DIM),
                FieldSchema(name="sparse_vec", dtype=DataType.SPARSE_FLOAT_VECTOR),
                FieldSchema(name="content_en", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="content_zh", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="user_id", dtype=DataType.INT64, is_partition_key=True),
                FieldSchema(name="paper_id", dtype=DataType.INT64),
                FieldSchema(name="folder_id", dtype=DataType.INT64),
                FieldSchema(name="acl", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=16),
                FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(name="page_num", dtype=DataType.INT64),
                FieldSchema(name="bbox", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="block_id", dtype=DataType.INT64),
                FieldSchema(name="image_key", dtype=DataType.VARCHAR, max_length=256),
            ]
            schema = CollectionSchema(fields, description="ScholarMind chunks", enable_dynamic_field=False)
            coll = Collection(COLLECTION, schema=schema)
            coll.create_index(
                "dense_vec",
                {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}},
            )
            coll.create_index(
                "sparse_vec",
                {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
            )
            logger.info(f"[milvus] created collection {COLLECTION} + indexes")
        else:
            coll = Collection(COLLECTION)

        coll.load()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[milvus] ensure_collection failed: {e}")
        return False


def milvus_available() -> bool:
    return _connect()


# ---------------------------------------------------------------------------
# Sparse encoding (dependency-free BM25-ish term frequency)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def sparse_encode(text: str) -> dict[int, float]:
    """Encode text into a sparse {token_id: weight} dict (log-scaled term frequency)."""
    if not text:
        return {1: 0.0}
    counts: dict[int, int] = {}
    for tok in _TOKEN_RE.findall(text.lower()):
        tid = (hash(tok) % _SPARSE_BUCKETS) + 1  # avoid id 0
        counts[tid] = counts.get(tid, 0) + 1
    vec = {tid: 1.0 + math.log(c) for tid, c in counts.items()}
    return vec or {1: 0.0}


# ---------------------------------------------------------------------------
# Insert / delete / count
# ---------------------------------------------------------------------------

def insert_chunks(rows: list[dict[str, Any]]) -> int:
    """Insert chunk rows. Returns number inserted (0 on failure / empty)."""
    if not rows:
        return 0
    if not ensure_collection():
        return 0
    try:
        from pymilvus import Collection

        coll = Collection(COLLECTION)
        # Prefer upsert (idempotent on primary key); fall back to insert if unsupported.
        try:
            coll.upsert(rows)
        except Exception as up_err:  # noqa: BLE001
            logger.debug(f"[milvus] upsert unsupported ({up_err}); deleting + inserting")
            ids = "', '".join(str(r["id"]) for r in rows)
            try:
                coll.delete(f"id in ['{ids}']")
            except Exception:
                pass
            coll.insert(rows)
        coll.flush()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[milvus] insert failed: {e}")
        return 0


def delete_by_paper(user_id: int, paper_id: int) -> None:
    if not ensure_collection():
        return
    try:
        from pymilvus import Collection

        coll = Collection(COLLECTION)
        coll.delete(f"user_id == {int(user_id)} and paper_id == {int(paper_id)}")
        coll.flush()
        logger.info(f"[milvus] deleted chunks user={user_id} paper={paper_id}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[milvus] delete failed: {e}")


def count_chunks(user_id: Optional[int] = None) -> int:
    if not ensure_collection():
        return 0
    try:
        from pymilvus import Collection

        coll = Collection(COLLECTION)
        expr = f"user_id == {int(user_id)}" if user_id is not None else ""
        res = coll.query(expr=expr or "id != ''", output_fields=["id"], limit=16384)
        return len(res)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[milvus] count failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

_OUTPUT_FIELDS = [
    "content_en", "content_zh", "user_id", "paper_id", "folder_id",
    "chunk_type", "section", "page_num", "bbox", "block_id", "image_key",
]


def build_scope_expr(user_id: int, folder_id: Optional[int] = None, paper_ids: Optional[list[int]] = None) -> str:
    """Build a Milvus scalar filter expression enforcing user_id isolation + scope."""
    parts = [f"user_id == {int(user_id)}"]
    if paper_ids:
        ids = ", ".join(str(int(p)) for p in paper_ids)
        parts.append(f"paper_id in [{ids}]")
    elif folder_id is not None:
        parts.append(f"folder_id == {int(folder_id)}")
    return " and ".join(parts)


def hybrid_search(
    dense_vec: list[float],
    sparse_vec: dict[int, float],
    expr: str,
    top_k: int = 20,
) -> list[dict]:
    """
    Hybrid dense + sparse search fused with RRF. Returns list of chunk dicts (with `score`).
    Degrades to [] if Milvus is unavailable.
    """
    if not ensure_collection():
        return []
    try:
        from pymilvus import AnnSearchRequest, Collection, RRFRanker

        coll = Collection(COLLECTION)
        dense_req = AnnSearchRequest(
            data=[dense_vec],
            anns_field="dense_vec",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=expr,
        )
        sparse_req = AnnSearchRequest(
            data=[sparse_vec],
            anns_field="sparse_vec",
            param={"metric_type": "IP"},
            limit=top_k,
            expr=expr,
        )
        results = coll.hybrid_search(
            [dense_req, sparse_req],
            rerank=RRFRanker(k=60),
            limit=top_k,
            output_fields=_OUTPUT_FIELDS,
        )
        hits = results[0] if results else []
        out: list[dict] = []
        for h in hits:
            entity = h.entity
            out.append({
                "id": h.id,
                "score": float(h.distance),
                "content_en": entity.get("content_en"),
                "content_zh": entity.get("content_zh"),
                "paper_id": entity.get("paper_id"),
                "folder_id": entity.get("folder_id"),
                "chunk_type": entity.get("chunk_type"),
                "section": entity.get("section"),
                "page_num": entity.get("page_num"),
                "bbox": entity.get("bbox"),
                "block_id": entity.get("block_id"),
                "image_key": entity.get("image_key"),
            })
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[milvus] hybrid_search failed: {e}")
        return []
