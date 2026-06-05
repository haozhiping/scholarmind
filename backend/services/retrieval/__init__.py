"""Retrieval service — search-optimize → hybrid-search → rerank → quality-filter."""
from .query_optimizer import optimize_query, QueryBundle
from .searcher import hybrid_search, SearchScope, ScoredChunk
from .reranker import rerank_chunks, corrective_grade, evaluate_and_filter
from .retriever import HybridRetriever

__all__ = [
    "optimize_query",
    "QueryBundle",
    "hybrid_search",
    "SearchScope",
    "ScoredChunk",
    "rerank_chunks",
    "corrective_grade",
    "evaluate_and_filter",
    "HybridRetriever",
]
