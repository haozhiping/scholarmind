"""Milvus client tests."""
import pytest

from common.config import settings


def test_milvus_config():
    """Verify Milvus configuration is set correctly."""
    assert settings.MILVUS_URI == "http://milvus:19530"
    assert settings.MILVUS_COLLECTION == "scholarmind_chunks"
    assert settings.MILVUS_M == 16
    assert settings.MILVUS_EF_CONSTRUCTION == 200
    assert settings.MILVUS_EF_SEARCH == 64
    assert settings.MILVUS_BATCH_SIZE == 100
    assert settings.EMBEDDING_DIM == 1024


def test_milvus_config_dimension_consistency():
    """Verify embedding dimension matches Milvus dense_vec dimension."""
    assert settings.EMBEDDING_DIM == 1024, "EMBEDDING_DIM must be 1024 for Milvus"
