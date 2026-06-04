"""
parse_paper: PDF → doc_blocks (MySQL) + citations (MySQL) + figures (MinIO).

Pipeline:
  1. MinerU HTTP  — layout parse → blocks (text/table/figure/formula)
  2. VLM          — figure description (async, per figure block)
  3. Ref parser   — extract citations from references section
       llm mode   : LLM reads references text → structured JSON
       grobid mode: POST PDF to GROBID → TEI XML parse (future)
  4. DB write     — upsert doc_blocks, citations, update papers.status
"""
from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from common.clients.llm import chat_complete_json, vlm_describe_image
from common.config import settings
from common.logging import logger

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Block:
    block_type: str          # text | table | figure | formula
    content: str             # raw text / HTML / LaTeX / caption
    page_num: int | None = None
    bbox: list | None = None
    image_key: str | None = None  # MinIO key (figures only)
    content_zh: str = ""          # VLM description (figures) or empty
    block_id: int | None = None   # MySQL doc_blocks.id, filled after insert
    raw_image: Any = None         # raw bytes / base64 str from MinerU, pre-upload

@dataclass
class ParseResult:
    paper_id: int
    user_id: int
    blocks: list[Block] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)  # {title, authors, year, raw_ref}
    title: str = ""
    abstract: str = ""

# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    path = Path(__file__).parents[3] / "prompts" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    return match.group(1) if match else text


# ---------------------------------------------------------------------------
# Normalization helpers (MinerU output is loosely specified — be tolerant)
# ---------------------------------------------------------------------------

_FIGURE_TYPES = {"image", "img", "figure", "fig", "picture"}
_FORMULA_TYPES = {"equation", "formula", "latex", "math", "interline_equation", "inline_equation"}


def _norm_type(raw: str | None) -> str:
    """Map MinerU block type aliases onto our 4 canonical types."""
    t = (raw or "").strip().lower()
    if t in _FIGURE_TYPES:
        return "figure"
    if t in _FORMULA_TYPES:
        return "formula"
    if t == "table":
        return "table"
    return "text"


def _pick(d: dict, *keys: str, default: Any = None) -> Any:
    """Return the first non-empty value among keys."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _extract_page(rb: dict) -> int | None:
    """Extract a 1-based page number, normalizing page_idx (0-based)."""
    for k in ("page_num", "page_no", "page"):
        v = rb.get(k)
        if isinstance(v, int):
            return v
    idx = rb.get("page_idx")
    if isinstance(idx, int):
        return idx + 1
    return None


def _find_block_list(obj: Any) -> list[dict]:
    """Recursively locate the list of block dicts in MinerU's parse result."""
    if isinstance(obj, dict):
        for key in ("blocks", "items", "elements", "content_list", "para_blocks"):
            v = obj.get(key)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        pages = obj.get("pages")
        if isinstance(pages, list):
            agg: list[dict] = []
            for p in pages:
                agg.extend(_find_block_list(p))
            if agg:
                return agg
        for v in obj.values():
            found = _find_block_list(v)
            if found:
                return found
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any(
            ("type" in x or "content" in x or "text" in x) for x in obj
        ):
            return obj
        for v in obj:
            found = _find_block_list(v)
            if found:
                return found
    return []


# ---------------------------------------------------------------------------
# Step 1: MinerU parsing
# ---------------------------------------------------------------------------

def _sync_mineru_call(pdf_bytes: bytes) -> dict:
    """Blocking MinerU KIE call. Wrapped by _call_mineru via asyncio.to_thread."""
    import os
    import tempfile
    from mineru_kie_sdk import MineruKIEClient

    client = MineruKIEClient(
        base_url=settings.MINERU_KIE_BASE_URL,
        pipeline_id=settings.MINERU_PIPELINE_ID,
        timeout=30,
    )
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        client.upload_file(tmp_path)
        results = client.get_result(
            timeout=settings.MINERU_TIMEOUT,
            poll_interval=settings.MINERU_POLL_INTERVAL,
        )
        parse = results.get("parse") if isinstance(results, dict) else None
        if parse is not None and hasattr(parse, "get_result"):
            parse = parse.get_result()
        return parse or {}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _call_mineru(pdf_bytes: bytes) -> dict:
    """Async wrapper: run the blocking SDK in a thread to keep the loop free."""
    return await asyncio.to_thread(_sync_mineru_call, pdf_bytes)


def _mineru_to_blocks(parse_result: dict) -> list[Block]:
    """Normalize MinerU parse result into Block list. Tolerant of unknown shapes."""
    raw_blocks = _find_block_list(parse_result)
    if not raw_blocks:
        logger.warning("[parse] no block list found in MinerU parse result")
        return []

    blocks: list[Block] = []
    for rb in raw_blocks:
        if not isinstance(rb, dict):
            continue
        btype = _norm_type(_pick(rb, "type", "block_type", "category"))
        content = _pick(
            rb, "content", "text", "html", "table_body", "latex", "markdown", "caption",
            default="",
        )
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        blocks.append(Block(
            block_type=btype,
            content=str(content),
            page_num=_extract_page(rb),
            bbox=_pick(rb, "bbox", "box", "poly"),
            image_key=_pick(rb, "image_key", "image_url", "img_path", "image_path"),
            raw_image=rb.get("image") or rb.get("image_base64"),
        ))
    return blocks


# ---------------------------------------------------------------------------
# Step 2: VLM figure descriptions (concurrent)
# ---------------------------------------------------------------------------

async def _describe_figures(blocks: list[Block]) -> None:
    """Fill content_zh for figure blocks in-place using VLM."""
    figure_blocks = [b for b in blocks if b.block_type == "figure" and b.image_key]
    if not figure_blocks:
        return

    async def _describe(block: Block) -> None:
        try:
            # Build a presigned/public URL — MinIO endpoint is accessible from backend container
            image_url = (
                f"http://{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET_FIG}/{block.image_key}"
            )
            block.content_zh = await vlm_describe_image(image_url, caption=block.content)
        except Exception as e:
            logger.warning(f"VLM figure description failed for {block.image_key}: {e}")

    await asyncio.gather(*[_describe(b) for b in figure_blocks])


# ---------------------------------------------------------------------------
# Step 3a: Reference extraction — LLM mode
# ---------------------------------------------------------------------------

async def _extract_refs_llm(blocks: list[Block]) -> list[dict]:
    """Find the references section in text blocks and extract via LLM."""
    # Collect all text content and look for a references section
    full_text = "\n".join(b.content for b in blocks if b.block_type == "text")

    # Heuristic: grab text after the last occurrence of "References" / "Bibliography"
    ref_match = re.search(
        r"\b(?:References|Bibliography|参考文献)\b(.*)$",
        full_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not ref_match:
        logger.info("No references section found in document text")
        return []

    references_text = ref_match.group(1).strip()
    if len(references_text) < 50:
        return []

    # Limit to reasonable length to avoid huge prompts
    references_text = references_text[:8000]

    prompt_template = _load_prompt("extract_references")
    prompt = prompt_template.format(references_text=references_text)

    try:
        result = await chat_complete_json(prompt, system="You are an academic reference parser.")
        if isinstance(result, list):
            return result
        # Some models wrap the array in a key
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    return v
    except Exception as e:
        logger.warning(f"LLM reference extraction failed: {e}")

    return []


# ---------------------------------------------------------------------------
# Step 3b: Reference extraction — GROBID mode (future)
# ---------------------------------------------------------------------------

async def _extract_refs_grobid(pdf_bytes: bytes) -> list[dict]:
    """Parse references via GROBID TEI XML. Only used when REFERENCE_PARSER_PROVIDER=grobid."""
    url = f"{settings.GROBID_BASE_URL}/api/processFulltextDocument"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
            data={"consolidateReferences": "0"},
        )
        resp.raise_for_status()

    tei_xml = resp.text
    return _parse_tei_references(tei_xml)


def _parse_tei_references(tei_xml: str) -> list[dict]:
    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as e:
        logger.warning(f"GROBID TEI parse error: {e}")
        return []

    refs = []
    for bibl in root.findall(".//tei:listBibl/tei:biblStruct", ns):
        title_el = bibl.find(".//tei:title[@level='a']", ns) or bibl.find(".//tei:title", ns)
        title = title_el.text or "" if title_el is not None else ""

        authors = []
        for author in bibl.findall(".//tei:author/tei:persName", ns):
            forename = author.findtext("tei:forename", "", ns)
            surname = author.findtext("tei:surname", "", ns)
            name = f"{forename} {surname}".strip()
            if name:
                authors.append(name)

        year_el = bibl.find(".//tei:date[@type='published']", ns)
        year_text = year_el.get("when", "") if year_el is not None else ""
        year = int(year_text[:4]) if year_text and year_text[:4].isdigit() else None

        raw_ref = ET.tostring(bibl, encoding="unicode")
        refs.append({"title": title, "authors": authors, "year": year, "raw_ref": raw_ref})

    return refs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def parse_paper(
    user_id: int,
    paper_id: int,
    pdf_key: str,
    db: AsyncSession,
    *,
    pdf_bytes: bytes | None = None,  # required only for grobid mode
) -> ParseResult:
    """
    Full parse pipeline for one paper. Called from RQ worker (not request thread).
    Writes doc_blocks and citations to DB, returns ParseResult for indexing.
    """
    logger.info(f"[parse] paper_id={paper_id} user_id={user_id} provider={settings.REFERENCE_PARSER_PROVIDER}")

    # --- Step 1: MinerU ---
    raw_blocks = await _call_mineru(pdf_key)
    blocks = _mineru_to_blocks(raw_blocks)
    logger.info(f"[parse] MinerU returned {len(blocks)} blocks")

    # --- Step 2: VLM figure descriptions (concurrent with next steps) ---
    vlm_task = asyncio.create_task(_describe_figures(blocks))

    # --- Step 3: Reference extraction ---
    if settings.REFERENCE_PARSER_PROVIDER == "grobid":
        if pdf_bytes is None:
            logger.warning("[parse] grobid mode requires pdf_bytes, falling back to llm")
            references = await _extract_refs_llm(blocks)
        else:
            references = await _extract_refs_grobid(pdf_bytes)
    else:
        references = await _extract_refs_llm(blocks)

    # Wait for VLM to finish
    await vlm_task

    logger.info(f"[parse] extracted {len(references)} references")

    # --- Step 4: Write to DB ---
    await _write_blocks(user_id, paper_id, blocks, db)
    await _write_citations(paper_id, references, db)
    await db.commit()

    return ParseResult(
        paper_id=paper_id,
        user_id=user_id,
        blocks=blocks,
        references=references,
    )


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

async def _write_blocks(user_id: int, paper_id: int, blocks: list[Block], db: AsyncSession) -> None:
    from sqlalchemy import text
    for b in blocks:
        await db.execute(
            text("""
                INSERT INTO doc_blocks (paper_id, user_id, block_type, content, page_num, bbox, image_key)
                VALUES (:paper_id, :user_id, :block_type, :content, :page_num, :bbox, :image_key)
            """),
            {
                "paper_id": paper_id,
                "user_id": user_id,
                "block_type": b.block_type,
                "content": b.content,
                "page_num": b.page_num,
                "bbox": json.dumps(b.bbox) if b.bbox else None,
                "image_key": b.image_key,
            },
        )


async def _write_citations(paper_id: int, references: list[dict], db: AsyncSession) -> None:
    from sqlalchemy import text
    for ref in references:
        await db.execute(
            text("""
                INSERT INTO citations (src_paper_id, dst_title, raw_ref)
                VALUES (:src_paper_id, :dst_title, :raw_ref)
            """),
            {
                "src_paper_id": paper_id,
                "dst_title": ref.get("title", ""),
                "raw_ref": ref.get("raw_ref", ""),
            },
        )
