"""Indexer service: chunking + bilingual enrichment + vectorization."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import re
import xxhash
from pathlib import Path
from common.clients.llm import embed_texts, chat_complete_json
from common.config import settings
from common.logging import logger

try:
    from common.clients.milvus import bulk_insert
except ImportError:
    logger.warning("pymilvus not installed, Milvus operations will be mocked")
    
    def bulk_insert(chunks: List[Dict[str, Any]]) -> None:
        """Mock bulk insert for testing."""
        logger.info(f"[mock] Would insert {len(chunks)} chunks into Milvus")


@dataclass
class Chunk:
    id: str = ""
    content_en: str = ""
    content_zh: str = ""
    user_id: int = 0
    paper_id: int = 0
    folder_id: int = 0
    chunk_type: str = "text"
    section: str = ""
    page_num: int = 0
    bbox: str = ""
    block_id: int = 0
    image_key: str = ""
    dense_vec: List[float] = field(default_factory=list)
    sparse_vec: Dict[int, float] = field(default_factory=dict)


class Chunker:
    """Smart chunker: split by section/semantics, keep tables/formulas intact."""

    def __init__(self, chunk_size: int = 512, overlap_ratio: float = 0.15):
        self.chunk_size = chunk_size
        self.overlap_ratio = overlap_ratio
        self.overlap_size = int(chunk_size * overlap_ratio)

    def _split_text(self, text: str) -> List[str]:
        """Split text by semantics while preserving sentence integrity."""
        if not text:
            return []

        sentences = re.split(r'(?<=[.!?。！？])\s+', text)
        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sentence_len = len(sentence)
            if current_length + sentence_len > self.chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                overlap_count = max(1, int(len(current_chunk) * self.overlap_ratio))
                current_chunk = current_chunk[-overlap_count:] + [sentence]
                current_length = sum(len(s) for s in current_chunk)
            else:
                current_chunk.append(sentence)
                current_length += sentence_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    async def chunk_paper(self, db, paper_id: int, user_id: int) -> List[Chunk]:
        """Chunk all doc_blocks for a paper."""
        blocks = await db.fetchall(
            "SELECT id, block_type, content, page_num, bbox, image_key "
            "FROM doc_blocks WHERE paper_id=%s AND user_id=%s "
            "ORDER BY page_num, id",
            paper_id, user_id,
        )

        chunks = []
        for block in blocks:
            block_id = block["id"]
            block_type = block["block_type"]
            content = block["content"]
            page_num = block["page_num"]
            bbox = block["bbox"]
            image_key = block["image_key"]

            if block_type in ("table", "figure", "formula"):
                chunk = Chunk(
                    content_en=str(content)[:8192],
                    chunk_type=block_type,
                    page_num=page_num or 0,
                    bbox=bbox or "",
                    block_id=block_id,
                    image_key=image_key or "",
                    user_id=user_id,
                    paper_id=paper_id,
                )
                chunks.append(chunk)
            else:
                text_chunks = self._split_text(str(content))
                for text_chunk in text_chunks:
                    chunk = Chunk(
                        content_en=text_chunk[:8192],
                        chunk_type="text",
                        page_num=page_num or 0,
                        block_id=block_id,
                        user_id=user_id,
                        paper_id=paper_id,
                    )
                    chunks.append(chunk)

        for chunk in chunks:
            chunk.id = xxhash.xxh64(f"{chunk.content_en}{chunk.paper_id}").hexdigest()

        return chunks


async def enrich_bilingual(chunks: List[Chunk]) -> None:
    """Generate Chinese summary and keywords for English text chunks."""
    prompt_path = Path(__file__).parents[2] / "prompts" / "enrich_zh_summary.md"
    
    try:
        prompt_template = prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt file not found: {prompt_path}")
        prompt_template = """
        请为以下英文文本生成中文摘要和关键词。
        
        文本：{content}
        
        输出格式：
        中文摘要：<中文摘要>
        关键词：<关键词1>, <关键词2>, ...
        """

    text_chunks = [c for c in chunks if c.chunk_type == "text" and c.content_en]

    for chunk in text_chunks:
        try:
            prompt = prompt_template.format(content=chunk.content_en[:2000])
            result = await chat_complete_json(prompt, system="你是学术论文翻译和摘要助手。")

            if isinstance(result, dict):
                summary = result.get("summary", "")
                keywords = result.get("keywords", "")
                chunk.content_zh = f"{summary}\n关键词：{keywords}"[:8192]
            elif isinstance(result, str):
                chunk.content_zh = result[:8192]
            else:
                chunk.content_zh = ""
        except Exception as e:
            logger.warning(f"Bilingual enrichment failed for chunk {chunk.id}: {e}")
            chunk.content_zh = ""


async def vectorize_chunks(chunks: List[Chunk]) -> None:
    """Get dense + sparse vectors for chunks."""
    if not chunks:
        return

    texts = [chunk.content_en + " " + chunk.content_zh for chunk in chunks]
    embeddings = await embed_texts(texts)

    for i, chunk in enumerate(chunks):
        if embeddings[i] is not None:
            chunk.dense_vec = embeddings[i]


async def index_paper(
    user_id: int,
    paper_id: int,
    db=None,
) -> int:
    """Complete indexing pipeline: chunk → enrich → vectorize → write to Milvus."""
    if db is None:
        from common.db.mysql_client import mysql as _db
        db = _db

    logger.info(f"[index] Starting indexing for paper_id={paper_id} user_id={user_id}")

    chunker = Chunker(chunk_size=512, overlap_ratio=0.15)
    chunks = await chunker.chunk_paper(db, paper_id, user_id)
    logger.info(f"[index] Chunked into {len(chunks)} chunks")

    if not chunks:
        logger.warning(f"[index] No chunks generated for paper {paper_id}")
        return 0

    await enrich_bilingual(chunks)
    logger.info(f"[index] Bilingual enrichment completed")

    await vectorize_chunks(chunks)
    logger.info(f"[index] Vectorization completed")

    records = [
        {
            "id": chunk.id,
            "dense_vec": chunk.dense_vec,
            "sparse_vec": chunk.sparse_vec,
            "content_en": chunk.content_en,
            "content_zh": chunk.content_zh,
            "user_id": chunk.user_id,
            "paper_id": chunk.paper_id,
            "folder_id": chunk.folder_id,
            "acl": "user",
            "chunk_type": chunk.chunk_type,
            "section": chunk.section,
            "page_num": chunk.page_num,
            "bbox": chunk.bbox,
            "block_id": chunk.block_id,
            "image_key": chunk.image_key,
        }
        for chunk in chunks
    ]

    bulk_insert(records)
    logger.info(f"[index] Written {len(chunks)} chunks to Milvus")

    await db.execute(
        "UPDATE papers SET chunk_count=%s WHERE id=%s AND user_id=%s",
        len(chunks), paper_id, user_id,
    )

    return len(chunks)
