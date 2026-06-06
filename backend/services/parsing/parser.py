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
from typing import Any, Optional

import httpx

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
# Step 1: MinerU parsing — unified entry (dispatches by MINERU_PROVIDER)
# ---------------------------------------------------------------------------

async def _call_mineru(pdf_bytes: bytes) -> dict:
    """Dispatch to the configured MinerU provider."""
    provider = settings.MINERU_PROVIDER
    if provider == "agent":
        return await _call_mineru_agent(pdf_bytes)
    if provider == "kie":
        return await _call_mineru_kie(pdf_bytes)
    raise ValueError(f"unsupported MINERU_PROVIDER: {provider}")


# -- Agent API (lightweight, signature upload + poll) --

async def _call_mineru_agent(pdf_bytes: bytes) -> dict:
    """Submit PDF to MinerU Agent Parse API (file mode), poll, return parsed markdown.

    Flow:
      1. POST /api/v1/agent/parse/file      → get task_id + signed file_url
      2. PUT  pdf_bytes → file_url           (direct OSS upload)
      3. Poll GET /api/v1/agent/parse/{task_id} → download markdown on done
    """
    import time

    # MinerU Agent API now requires an API key.
    # If MINERU_API_KEY is empty, the server returns "user authenticate failed".
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.MINERU_API_KEY:
        headers["Authorization"] = f"Bearer {settings.MINERU_API_KEY}"

    body = {
        "file_name": "paper.pdf",
        "language": settings.MINERU_LANGUAGE,
        "enable_table": settings.MINERU_ENABLE_TABLE,
        "enable_formula": settings.MINERU_ENABLE_FORMULA,
        "is_ocr": settings.MINERU_IS_OCR,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
        # 1) Acquire signed upload URL
        logger.info("[parse-agent] requesting signed upload URL")
        resp = await client.post(
            f"{settings.MINERU_AGENT_BASE_URL}/parse/file",
            json=body, headers=headers,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"MinerU Agent submit failed: code={result.get('code')} msg={result.get('msg')}")

        task_id = result["data"]["task_id"]
        file_url = result["data"]["file_url"]
        logger.info(f"[parse-agent] task_id={task_id}, uploading file...")

        # 2) PUT file directly to the signed OSS URL (no extra auth header needed)
        put_resp = await client.put(file_url, content=pdf_bytes)
        if put_resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"MinerU Agent file upload failed: HTTP {put_resp.status_code}")
        logger.info("[parse-agent] file uploaded, waiting for parse...")

        # 3) Poll until done or timeout
        poll_url = f"{settings.MINERU_AGENT_BASE_URL}/parse/{task_id}"
        start = time.time()
        while time.time() - start < settings.MINERU_TIMEOUT:
            await asyncio.sleep(settings.MINERU_POLL_INTERVAL)

            resp = await client.get(poll_url, headers=headers)
            result = resp.json()
            state = result.get("data", {}).get("state", "")

            if state == "done":
                markdown_url = result["data"]["markdown_url"]
                logger.info(f"[parse-agent] parse done, downloading markdown from {markdown_url}")
                md_resp = await client.get(markdown_url)
                return {"markdown": md_resp.text, "task_id": task_id}

            if state == "failed":
                err = result.get("data", {}).get("err_msg", "unknown")
                raise RuntimeError(f"MinerU Agent parse failed: {err}")

            logger.debug(f"[parse-agent] state={state}, elapsed={int(time.time() - start)}s")

        raise RuntimeError(f"MinerU Agent poll timeout after {settings.MINERU_TIMEOUT}s (task_id={task_id})")


# -- KIE SDK (legacy, needs Pipeline ID + mineru-kie-sdk package) --

async def _call_mineru_kie(pdf_bytes: bytes) -> dict:
    """Async wrapper: run the blocking KIE SDK in a thread."""
    return await asyncio.to_thread(_sync_mineru_kie_call, pdf_bytes)


def _sync_mineru_kie_call(pdf_bytes: bytes) -> dict:
    """Blocking MinerU KIE call."""
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
        file_ids = client.upload_file(tmp_path)
        if not file_ids:
            raise RuntimeError("MinerU KIE upload_file returned empty file_ids")
        logger.debug(f"[parse-kie] uploaded file, file_ids={file_ids}")
        results = client.get_result(
            file_ids=file_ids,
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


# -- Block normalization (shared) --

def _mineru_to_blocks(parse_result: dict) -> list[Block]:
    """Normalize MinerU parse result into Block list.

    Supports two formats:
      - KIE JSON: structured blocks with type/content/bbox/page_num
      - Agent markdown: raw markdown string under "markdown" key
    """
    # Agent API → markdown string
    md = parse_result.get("markdown")
    if isinstance(md, str) and len(md) > 50:
        return _markdown_to_blocks(md)

    # KIE JSON → structured dict
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


# -- Markdown → Blocks (Agent API output) --

_MATH_BLOCK_RE = re.compile(r"^\${2,}(.*?)\${2,}$", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")


def _markdown_to_blocks(md: str) -> list[Block]:
    """Parse MinerU Agent API markdown output into Block list.

    Heuristic parser — splits by blank lines, then classifies each chunk.
    """
    chunks = _split_markdown_chunks(md)
    blocks: list[Block] = []
    for chunk in chunks:
        blocks.append(_classify_chunk(chunk))
    logger.info(f"[parse-agent] markdown → {len(blocks)} blocks")
    return blocks


def _split_markdown_chunks(md: str) -> list[str]:
    """Split markdown into logical chunks separated by blank lines."""
    lines = md.split("\n")
    chunks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            chunks.append(text)
        buf = []

    for line in lines:
        stripped = line.strip()
        # Blank line → flush current chunk
        if not stripped:
            flush()
            continue
        buf.append(line)

    flush()
    return chunks


def _classify_chunk(text: str) -> Block:
    """Classify a single markdown chunk as text / table / formula / figure."""
    # Formula: $$...$$ or $...$
    if _MATH_BLOCK_RE.search(text):
        return Block(block_type="formula", content=text)

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Table: at least 2 rows starting/filled with |
    table_rows = [l for l in lines if _TABLE_ROW_RE.match(l)]
    if len(table_rows) >= 2:
        return Block(block_type="table", content=text)

    # Image: ![...](...)
    img_match = _IMAGE_RE.search(text)
    if img_match and len(lines) <= 2:
        return Block(
            block_type="figure",
            content=img_match.group(1) or "",
            image_key=img_match.group(2),   # CDN URL for later download
        )

    # Everything else is text
    return Block(block_type="text", content=text)


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
            client = _minio_client()
            image_url = await asyncio.to_thread(
                _sync_presigned, client, settings.MINIO_BUCKET_FIG, block.image_key
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
    *,
    pdf_bytes: bytes,
    db: Any = None,  # AsyncMySQLClient from common.db.mysql_client
) -> ParseResult:
    """
    Full parse pipeline for one paper. Called from RQ worker (not request thread).
    Writes doc_blocks/citations, uploads figures, updates papers.status, returns ParseResult.
    """
    if db is None:
        from common.db.mysql_client import mysql as _db
        db = _db

    logger.info(
        f"[parse] paper_id={paper_id} user_id={user_id} "
        f"provider={settings.REFERENCE_PARSER_PROVIDER}"
    )

    # --- Step 1: MinerU (blocking SDK wrapped in a thread) ---
    parse_result = await _call_mineru(pdf_bytes)
    blocks = _mineru_to_blocks(parse_result)
    logger.info(f"[parse] MinerU returned {len(blocks)} blocks")

    # --- Step 2: write blocks first to obtain block_id (needed for figure keys) ---
    await _write_blocks(user_id, paper_id, blocks, db)

    # --- Step 3: upload figures to MinIO and backfill image_key ---
    await _upload_figures(user_id, paper_id, blocks, db)

    # --- Step 4: VLM descriptions (needs image_key set) ---
    await _describe_figures(blocks)

    # VLM descriptions are computed after _write_blocks — backfill content_zh now
    for b in blocks:
        if b.block_type == "figure" and b.content_zh:
            await db.execute(
                "UPDATE doc_blocks SET content_zh=%s WHERE id=%s AND user_id=%s",
                b.content_zh, b.block_id, user_id,
            )

    # --- Step 5: reference extraction ---
    if settings.REFERENCE_PARSER_PROVIDER == "grobid":
        references = await _extract_refs_grobid(pdf_bytes)
    else:
        references = await _extract_refs_llm(blocks)
    logger.info(f"[parse] extracted {len(references)} references")
    await _write_citations(paper_id, references, db)

    # --- Step 6: mark paper done ---
    await db.execute(
        "UPDATE papers SET status='done' WHERE id=%s AND user_id=%s",
        paper_id, user_id,
    )

    return ParseResult(
        paper_id=paper_id,
        user_id=user_id,
        blocks=blocks,
        references=references,
    )


# ---------------------------------------------------------------------------
# MinIO figure upload (minimal in-parser client; full client is out of scope)
# ---------------------------------------------------------------------------

def _minio_client():
    from minio import Minio
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def _decode_image(raw: Any) -> bytes:
    """Accept raw bytes or (data-URI) base64 string, return PNG bytes."""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        import base64
        payload = raw.split(",", 1)[-1]  # strip optional data: URI prefix
        return base64.b64decode(payload)
    raise ValueError(f"unsupported image payload type: {type(raw)!r}")


def _sync_put_object(client, bucket: str, key: str, data: bytes) -> None:
    from io import BytesIO
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, key, BytesIO(data), length=len(data), content_type="image/png")


def _sync_presigned(client, bucket: str, key: str) -> str:
    return client.presigned_get_object(bucket, key)


async def _upload_figures(user_id: int, paper_id: int, blocks: list[Block], db) -> None:
    """Upload figure images to MinIO and backfill image_key. Needs block_id set first."""
    targets = [b for b in blocks if b.block_type == "figure" and b.raw_image and b.block_id]
    if not targets:
        return
    client = _minio_client()
    bucket = settings.MINIO_BUCKET_FIG
    for b in targets:
        try:
            data = _decode_image(b.raw_image)
            key = f"{user_id}/{paper_id}/{b.block_id}.png"
            await asyncio.to_thread(_sync_put_object, client, bucket, key, data)
            b.image_key = key
            await db.execute(
                "UPDATE doc_blocks SET image_key=%s WHERE id=%s AND user_id=%s",
                key, b.block_id, user_id,
            )
        except Exception as e:
            logger.warning(f"[parse] figure upload failed block_id={b.block_id}: {e}")


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

async def _write_blocks(user_id: int, paper_id: int, blocks: list[Block], db) -> None:
    for b in blocks:
        bbox_json = json.dumps(b.bbox) if b.bbox else None
        block_id = await db.execute(
            "INSERT INTO doc_blocks (paper_id, user_id, block_type, content, content_zh, page_num, bbox, image_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            paper_id, user_id, b.block_type, b.content, b.content_zh, b.page_num, bbox_json, b.image_key,
        )
        b.block_id = block_id


async def _write_citations(paper_id: int, references: list[dict], db) -> None:
    for ref in references:
        await db.execute(
            "INSERT INTO citations (src_paper_id, dst_title, raw_ref) VALUES (%s, %s, %s)",
            paper_id, ref.get("title", ""), ref.get("raw_ref", ""),
        )
