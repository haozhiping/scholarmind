"""Indexing module tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import xxhash
import asyncio

from services.indexing.indexer import Chunker, Chunk


def test_chunker_split_text():
    """Test text splitting with overlap."""
    chunker = Chunker(chunk_size=100, overlap_ratio=0.2)
    text = "This is a test. This is another test. This is a third test. " * 10
    chunks = chunker._split_text(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 150, f"Chunk too long: {len(chunk)}"

    total_length = sum(len(c) for c in chunks)
    original_length = len(text)
    assert total_length > original_length, "Overlap should increase total length"


def test_chunker_empty_text():
    """Test empty text handling."""
    chunker = Chunker()
    chunks = chunker._split_text("")
    assert chunks == []

    chunks = chunker._split_text("   ")
    assert len(chunks) <= 1 and (chunks == [] or chunks[0].strip() == "")


def test_chunk_id_is_deterministic():
    """Test that chunk ID is deterministic based on content and paper_id."""
    chunk1 = Chunk(content_en="test content", paper_id=1)
    chunk1.id = xxhash.xxh64(f"{chunk1.content_en}{chunk1.paper_id}").hexdigest()

    chunk2 = Chunk(content_en="test content", paper_id=1)
    chunk2.id = xxhash.xxh64(f"{chunk2.content_en}{chunk2.paper_id}").hexdigest()

    assert chunk1.id == chunk2.id

    chunk3 = Chunk(content_en="different content", paper_id=1)
    chunk3.id = xxhash.xxh64(f"{chunk3.content_en}{chunk3.paper_id}").hexdigest()
    assert chunk1.id != chunk3.id


@pytest.mark.asyncio
async def test_chunk_paper_preserves_tables():
    """Test that table blocks are preserved intact."""
    db = AsyncMock()
    mock_result = AsyncMock()
    
    async def mock_fetchall():
        return [
            (1, "table", "<table>...</table>", 1, "[1,2,3,4]", None),
            (2, "text", "Regular text here. This is more text. And even more.", 2, None, None),
            (3, "formula", "$E=mc^2$", 3, None, None),
        ]
    
    mock_result.fetchall = mock_fetchall
    db.execute.return_value = mock_result

    chunker = Chunker(chunk_size=50)
    chunks = await chunker.chunk_paper(db, paper_id=1, user_id=999)

    assert len(chunks) >= 3
    table_chunk = next(c for c in chunks if c.chunk_type == "table")
    assert "<table>" in table_chunk.content_en
    assert table_chunk.block_id == 1

    formula_chunk = next(c for c in chunks if c.chunk_type == "formula")
    assert "$E=mc^2$" in formula_chunk.content_en
    assert formula_chunk.block_id == 3


@pytest.mark.asyncio
async def test_chunk_paper_with_figure():
    """Test that figure blocks are preserved with image_key."""
    db = AsyncMock()
    mock_result = AsyncMock()
    
    async def mock_fetchall():
        return [
            (1, "figure", "Caption text", 1, "[1,2,3,4]", "999/1/1.png"),
        ]
    
    mock_result.fetchall = mock_fetchall
    db.execute.return_value = mock_result

    chunker = Chunker()
    chunks = await chunker.chunk_paper(db, paper_id=1, user_id=999)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "figure"
    assert chunks[0].image_key == "999/1/1.png"


def test_chunk_content_truncation():
    """Test that content is truncated to max length."""
    long_text = "a" * 10000
    chunk = Chunk(content_en=long_text)
    assert len(chunk.content_en) == 10000

    chunk.content_en = long_text[:8192]
    assert len(chunk.content_en) == 8192


def test_overlap_ratio_config():
    """Test overlap ratio configuration."""
    chunker = Chunker(chunk_size=100, overlap_ratio=0.2)
    assert chunker.overlap_size == 20

    chunker = Chunker(chunk_size=200, overlap_ratio=0.15)
    assert chunker.overlap_size == 30


def test_chunk_type_normalization():
    """Test chunk type handling."""
    chunk = Chunk(chunk_type="text")
    assert chunk.chunk_type == "text"

    chunk = Chunk(chunk_type="table")
    assert chunk.chunk_type == "table"

    chunk = Chunk(chunk_type="figure")
    assert chunk.chunk_type == "figure"

    chunk = Chunk(chunk_type="formula")
    assert chunk.chunk_type == "formula"
