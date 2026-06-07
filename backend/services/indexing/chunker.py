"""
Smart chunker: doc_blocks -> Chunk list.

Rules (per tasks_guide):
  - table / figure / formula: whole block kept as a single chunk (small-big retrieval).
  - text: sentence-aware splitting into ~512-token windows with ~15-20% overlap.
  - each chunk records its source block_id (-> MySQL doc_blocks.id) + page_num/bbox/image_key.
  - section header detection: a short ALL-CAPS line or a line starting with '#'.

Token counting is approximated (no tokenizer dependency): ~1 token ≈ 4 chars for English /
1 char for CJK. We use a char budget that maps to roughly 512 tokens.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ~512 tokens. Mixed EN/CJK -> use a char budget; overlap ~18%.
_CHUNK_CHARS = 1600
_OVERLAP_CHARS = 280

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


@dataclass
class Chunk:
    content_en: str
    block_type: str          # text | table | figure | formula
    page_num: Optional[int] = None
    bbox: Optional[str] = None
    block_id: Optional[int] = None
    image_key: Optional[str] = None
    section: str = ""
    content_zh: str = ""      # filled by enricher


def _is_section_header(text: str) -> Optional[str]:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not line:
        return None
    if line.startswith("#"):
        return line.lstrip("# ").strip()[:200]
    # Short, mostly uppercase line -> treat as a section header
    letters = [c for c in line if c.isalpha()]
    if 2 <= len(line) <= 80 and letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return line[:200]
    return None


def _split_text(text: str) -> list[str]:
    """Sentence-aware split into ~_CHUNK_CHARS windows with overlap."""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if buf and len(buf) + len(sent) + 1 > _CHUNK_CHARS:
            chunks.append(buf.strip())
            # carry overlap from the tail of the previous buffer
            buf = (buf[-_OVERLAP_CHARS:] + " " + sent).strip()
        else:
            buf = (buf + " " + sent).strip() if buf else sent
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def chunk_blocks(block_rows: list[dict]) -> list[Chunk]:
    """
    Turn doc_block rows (dicts with id/block_type/content/content_zh/page_num/bbox/image_key)
    into retrievable chunks.
    """
    chunks: list[Chunk] = []
    current_section = ""

    for row in block_rows:
        btype = row.get("block_type") or "text"
        content = (row.get("content") or "").strip()
        if not content and btype == "text":
            continue

        if btype == "text":
            header = _is_section_header(content)
            if header:
                current_section = header
            for piece in _split_text(content):
                chunks.append(Chunk(
                    content_en=piece,
                    block_type="text",
                    page_num=row.get("page_num"),
                    bbox=row.get("bbox"),
                    block_id=row.get("id"),
                    image_key=row.get("image_key"),
                    section=current_section,
                ))
        else:
            # table / figure / formula -> keep whole, never split
            chunks.append(Chunk(
                content_en=content,
                block_type=btype,
                page_num=row.get("page_num"),
                bbox=row.get("bbox"),
                block_id=row.get("id"),
                image_key=row.get("image_key"),
                section=current_section,
                content_zh=(row.get("content_zh") or ""),  # figures: VLM description from parser
            ))

    return chunks
