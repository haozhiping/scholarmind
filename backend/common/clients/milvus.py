"""Milvus vector store client."""
from typing import List, Dict, Any, Optional
from pymilvus import (
    MilvusClient,
    Collection,
    FieldSchema,
    CollectionSchema,
    DataType,
)

from common.config import settings
from common.logging import logger

_client: Optional[MilvusClient] = None
_collection: Optional[Collection] = None


def get_milvus_client() -> MilvusClient:
    """Get singleton Milvus client."""
    global _client
    if _client is None:
        _client = MilvusClient(
            uri=settings.MILVUS_URI,
            token=settings.MILVUS_TOKEN,
        )
        logger.info(f"Milvus client connected to {settings.MILVUS_URI}")
    return _client


def ensure_collection() -> Collection:
    """Ensure collection exists with proper schema and indexes."""
    global _collection
    if _collection is not None:
        return _collection

    client = get_milvus_client()
    collection_name = settings.MILVUS_COLLECTION

    # Check if collection exists
    if client.has_collection(collection_name):
        logger.info(f"Collection {collection_name} already exists")
    else:
        # Create schema
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
            FieldSchema(name="dense_vec", dtype=DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM),
            FieldSchema(name="sparse_vec", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(name="content_en", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="content_zh", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="user_id", dtype=DataType.INT64),
            FieldSchema(name="paper_id", dtype=DataType.INT64),
            FieldSchema(name="folder_id", dtype=DataType.INT64),
            FieldSchema(name="acl", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="page_num", dtype=DataType.INT64),
            FieldSchema(name="bbox", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="block_id", dtype=DataType.INT64),
            FieldSchema(name="image_key", dtype=DataType.VARCHAR, max_length=256),
        ]
        schema = CollectionSchema(fields=fields, enable_dynamic_field=True)

        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            partition_key_field="user_id",
        )
        logger.info(f"Created collection {collection_name}")

    # Create HNSW index for dense_vec if not exists
    try:
        client.create_index(
            collection_name=collection_name,
            field_name="dense_vec",
            index_params={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {
                    "M": settings.MILVUS_M,
                    "efConstruction": settings.MILVUS_EF_CONSTRUCTION,
                },
            },
        )
        logger.info(f"Created HNSW index for dense_vec")
    except Exception as e:
        logger.info(f"HNSW index may already exist: {e}")

    # Create sparse index if not exists
    try:
        client.create_index(
            collection_name=collection_name,
            field_name="sparse_vec",
            index_params={"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
        )
        logger.info(f"Created sparse index for sparse_vec")
    except Exception as e:
        logger.info(f"Sparse index may already exist: {e}")

    # Load collection
    _collection = Collection(collection_name)
    _collection.load()
    logger.info(f"Loaded collection {collection_name}")

    return _collection


def bulk_insert(chunks: List[Dict[str, Any]]) -> None:
    """Bulk insert chunks into Milvus."""
    collection = ensure_collection()
    collection.insert(chunks)
    collection.flush()
    logger.info(f"Inserted {len(chunks)} chunks into Milvus")


def search(
    query_vector: List[float],
    user_id: int,
    limit: int = 20,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search Milvus for similar chunks."""
    collection = ensure_collection()
    
    search_params = {
        "metric_type": "COSINE",
        "params": {"ef": settings.MILVUS_EF_SEARCH},
    }

    expr = f"user_id == {user_id}"
    if filters:
        if "paper_id" in filters:
            expr += f" && paper_id == {filters['paper_id']}"
        if "folder_id" in filters:
            expr += f" && folder_id == {filters['folder_id']}"
        if "chunk_type" in filters:
            expr += f" && chunk_type == '{filters['chunk_type']}'"

    results = collection.search(
        data=[query_vector],
        anns_field="dense_vec",
        param=search_params,
        limit=limit,
        expr=expr,
        output_fields=["content_en", "content_zh", "paper_id", "page_num", "chunk_type", "image_key"],
    )

    output = []
    for hit in results[0]:
        output.append({
            "id": hit.id,
            "distance": hit.distance,
            "content_en": hit.entity.get("content_en", ""),
            "content_zh": hit.entity.get("content_zh", ""),
            "paper_id": hit.entity.get("paper_id", 0),
            "page_num": hit.entity.get("page_num", 0),
            "chunk_type": hit.entity.get("chunk_type", ""),
            "image_key": hit.entity.get("image_key", ""),
        })
    return output


def count_chunks(user_id: int) -> int:
    """Count chunks for a user."""
    collection = ensure_collection()
    return collection.count(f"user_id == {user_id}")
