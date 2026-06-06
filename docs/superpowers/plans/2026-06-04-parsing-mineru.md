# 任务1 解析服务对接（MinerU + 参考文献 + VLM + 归一化入库）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `backend/services/parsing/parser.py` 从虚构 HTTP stub 改造为对接真实 `mineru-kie-sdk` 云端 SDK 的解析流水线，完成 MinerU 解析 → 容错归一化 → 图片落 MinIO → VLM 描述 → 参考文献提取 → 写 `doc_blocks`/`citations`/更新 `papers.status`。

**Architecture:** 同步阻塞 SDK 用 `asyncio.to_thread` 包裹；MinerU 返回结构未知，用容错适配层归一化；图片需先插 `doc_blocks` 拿自增 `block_id` 再上传 MinIO 回填 `image_key`。范围限定 parser.py 核心 + config 配置项，不接 worker/upload 路由。

**Tech Stack:** Python 3.11(运行)/3.13(本地测试) · asyncio · mineru-kie-sdk(mock) · minio · SQLAlchemy async(text) · pytest 8.3.4

---

## 文件结构

| 文件 | 责任 | 操作 |
|---|---|---|
| `backend/common/config.py` | 新增 MinerU 云端配置项 | Modify |
| `backend/.env.example` | 同步配置项文档 | Modify |
| `backend/services/parsing/parser.py` | 解析主流程 + 容错归一化 + SDK 对接 + 图片落库 | Modify |
| `backend/tests/conftest.py` | 把 backend 加入 sys.path | Create |
| `backend/tests/test_parser_normalize.py` | 纯函数 + 归一化单测 | Create |
| `backend/tests/test_parser_sdk.py` | SDK 包裹 / MinIO / 写库 / 端到端单测 | Create |

**测试运行约定（所有任务统一）：** 在 `backend/` 目录下运行 `python -m pytest tests -v`。

---

## Task 1: 新增 MinerU 云端配置项

**Files:**
- Modify: `backend/common/config.py:82-87`
- Modify: `backend/.env.example:77-79`
- Test: `backend/tests/conftest.py`, `backend/tests/test_parser_sdk.py`

- [ ] **Step 1: 创建 conftest.py 把 backend 加入路径**

Create `backend/tests/conftest.py`:

```python
import os
import sys

# Put backend/ on sys.path so `common` and `services` import cleanly.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
```

- [ ] **Step 2: 写失败测试 —— 断言新配置项存在**

Create `backend/tests/test_parser_sdk.py` with:

```python
from common.config import settings


def test_mineru_kie_config_present():
    assert settings.MINERU_KIE_BASE_URL == "https://mineru.net/api/kie"
    assert hasattr(settings, "MINERU_PIPELINE_ID")
    assert hasattr(settings, "MINERU_API_KEY")
    assert settings.MINERU_POLL_INTERVAL == 5
    assert settings.MINERU_TIMEOUT == 300
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py::test_mineru_kie_config_present -v`
Expected: FAIL —— `AttributeError: 'Settings' object has no attribute 'MINERU_KIE_BASE_URL'`

- [ ] **Step 4: 在 config.py 添加配置项**

In `backend/common/config.py`, after the existing MinerU/GROBID block (`GROBID_BASE_URL: str = "http://grobid:8070"`), add:

```python
    # MinerU KIE cloud SDK (mineru-kie-sdk)
    MINERU_KIE_BASE_URL: str = "https://mineru.net/api/kie"
    MINERU_PIPELINE_ID: str = ""          # required for cloud parsing
    MINERU_API_KEY: str = ""              # reserved, used per SDK auth
    MINERU_POLL_INTERVAL: int = 5         # seconds between polls
    MINERU_TIMEOUT: int = 300             # total poll timeout (s); -1 = until done
```

- [ ] **Step 5: 同步 .env.example**

In `backend/.env.example`, in the 解析服务 section, after the `GROBID_BASE_URL=...` line, add:

```dotenv
# MinerU KIE 云端 SDK (mineru-kie-sdk, 任务1 解析实际使用)
MINERU_KIE_BASE_URL=https://mineru.net/api/kie
MINERU_PIPELINE_ID=                              # 云端 pipeline id, 必填
MINERU_API_KEY=                                  # 预留, 按 SDK 鉴权方式填
MINERU_POLL_INTERVAL=5                            # 轮询间隔(秒)
MINERU_TIMEOUT=300                               # 轮询总超时(秒), -1=直到完成
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py::test_mineru_kie_config_present -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/common/config.py backend/.env.example backend/tests/conftest.py backend/tests/test_parser_sdk.py
git commit -m "feat(parsing): 新增 MinerU 云端 KIE SDK 配置项

为对接 mineru-kie-sdk 云端服务新增 MINERU_KIE_BASE_URL/PIPELINE_ID/API_KEY/
POLL_INTERVAL/TIMEOUT 配置, 并同步 .env.example; 新增 tests/conftest.py 接入测试路径。"
```

---

## Task 2: Block 数据类扩展 + 纯函数归一化助手

**Files:**
- Modify: `backend/services/parsing/parser.py:33-40` (Block dataclass)
- Modify: `backend/services/parsing/parser.py` (新增助手函数)
- Test: `backend/tests/test_parser_normalize.py`

- [ ] **Step 1: 写失败测试 —— 类型归一与字段助手**

Create `backend/tests/test_parser_normalize.py`:

```python
from services.parsing.parser import (
    Block,
    _norm_type,
    _pick,
    _extract_page,
    _find_block_list,
)


def test_norm_type_aliases():
    assert _norm_type("image") == "figure"
    assert _norm_type("IMG") == "figure"
    assert _norm_type("equation") == "formula"
    assert _norm_type("interline_equation") == "formula"
    assert _norm_type("table") == "table"
    assert _norm_type("paragraph") == "text"
    assert _norm_type(None) == "text"


def test_pick_first_nonempty():
    d = {"a": "", "b": None, "c": "hit", "d": "later"}
    assert _pick(d, "a", "b", "c", "d") == "hit"
    assert _pick(d, "a", "b", default="fallback") == "fallback"


def test_extract_page_variants():
    assert _extract_page({"page_num": 3}) == 3
    assert _extract_page({"page_idx": 0}) == 1          # 0-based -> 1-based
    assert _extract_page({"page": 7}) == 7
    assert _extract_page({"nothing": 1}) is None


def test_find_block_list_direct_key():
    blocks = [{"type": "text", "content": "a"}]
    assert _find_block_list({"blocks": blocks}) == blocks
    assert _find_block_list({"content_list": blocks}) == blocks


def test_find_block_list_nested_pages():
    obj = {"pages": [{"blocks": [{"type": "text"}]}, {"blocks": [{"type": "table"}]}]}
    found = _find_block_list(obj)
    assert len(found) == 2


def test_find_block_list_is_top_list():
    blocks = [{"type": "text", "content": "x"}]
    assert _find_block_list(blocks) == blocks


def test_block_has_new_fields():
    b = Block(block_type="figure", content="cap")
    assert b.block_id is None
    assert b.raw_image is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_normalize.py -v`
Expected: FAIL —— `ImportError: cannot import name '_norm_type'`

- [ ] **Step 3: 扩展 Block 数据类**

In `backend/services/parsing/parser.py`, replace the `Block` dataclass (lines ~33-40) with:

```python
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
```

- [ ] **Step 4: 新增纯函数助手**

In `backend/services/parsing/parser.py`, add a module-level import near the top (after `from sqlalchemy.ext.asyncio import AsyncSession`):

```python
from sqlalchemy import text
```

Then, just below the Prompt loader section (`_load_prompt`), add:

```python
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_normalize.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: 提交**

```bash
git add backend/services/parsing/parser.py backend/tests/test_parser_normalize.py
git commit -m "feat(parsing): Block 扩展 block_id/raw_image 字段 + 归一化纯函数助手

新增 _norm_type/_pick/_extract_page/_find_block_list 容错助手, 容忍 MinerU 输出的
多种字段别名与 type 取值; Block 增加 block_id(写库后回填)与 raw_image(上传前暂存)字段。"
```

---

## Task 3: `_mineru_to_blocks` 容错归一化

**Files:**
- Modify: `backend/services/parsing/parser.py:80-91` (替换旧 `_mineru_to_blocks`)
- Test: `backend/tests/test_parser_normalize.py`

- [ ] **Step 1: 追加失败测试 —— 归一化各形态 parse 结果**

Append to `backend/tests/test_parser_normalize.py`:

```python
from services.parsing.parser import _mineru_to_blocks


def test_mineru_to_blocks_mixed_types():
    parse_result = {
        "blocks": [
            {"type": "text", "content": "Intro paragraph", "page_idx": 0, "bbox": [1, 2, 3, 4]},
            {"type": "table", "html": "<table><tr><td>x</td></tr></table>", "page_num": 2},
            {"type": "equation", "latex": "E=mc^2", "page": 3, "box": [5, 6, 7, 8]},
            {"type": "image", "caption": "Fig 1", "image_key": "u/p/9.png", "page_idx": 1},
        ]
    }
    blocks = _mineru_to_blocks(parse_result)
    assert [b.block_type for b in blocks] == ["text", "table", "formula", "figure"]
    assert blocks[0].page_num == 1 and blocks[0].bbox == [1, 2, 3, 4]
    assert "<table>" in blocks[1].content and blocks[1].page_num == 2
    assert blocks[2].content == "E=mc^2" and blocks[2].bbox == [5, 6, 7, 8]
    assert blocks[3].block_type == "figure" and blocks[3].image_key == "u/p/9.png"


def test_mineru_to_blocks_keeps_raw_image():
    parse_result = {"blocks": [{"type": "figure", "image": b"PNGDATA", "caption": "c"}]}
    blocks = _mineru_to_blocks(parse_result)
    assert blocks[0].raw_image == b"PNGDATA"
    assert blocks[0].content == "c"


def test_mineru_to_blocks_empty_returns_empty():
    assert _mineru_to_blocks({}) == []
    assert _mineru_to_blocks({"foo": "bar"}) == []


def test_mineru_to_blocks_stringifies_structured_content():
    parse_result = {"blocks": [{"type": "table", "content": {"rows": [[1, 2]]}}]}
    blocks = _mineru_to_blocks(parse_result)
    assert isinstance(blocks[0].content, str)
    assert "rows" in blocks[0].content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_normalize.py -k mineru_to_blocks -v`
Expected: FAIL —— 旧 `_mineru_to_blocks` 不识别 `html/latex/page_idx/raw_image`，断言不通过

- [ ] **Step 3: 替换 `_mineru_to_blocks` 实现**

In `backend/services/parsing/parser.py`, replace the existing `_mineru_to_blocks` function with:

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_normalize.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/services/parsing/parser.py backend/tests/test_parser_normalize.py
git commit -m "feat(parsing): _mineru_to_blocks 重写为容错归一化适配层

按字段别名优先级提取 content/page/bbox/image, type 经 _norm_type 归一; 结构化 content
序列化为字符串; 暂存二进制图到 raw_image; 找不到 block 列表时 warning 返回空。"
```

---

## Task 4: `_call_mineru` 对接 SDK（asyncio.to_thread 包裹）

**Files:**
- Modify: `backend/services/parsing/parser.py:66-77` (替换旧 HTTP `_call_mineru`)
- Test: `backend/tests/test_parser_sdk.py`

- [ ] **Step 1: 追加失败测试 —— 用伪 SDK 验证包裹/取值/清理/异常透传**

Append to `backend/tests/test_parser_sdk.py`:

```python
import os
import sys
import types
import asyncio
import pytest


def _install_fake_sdk(get_result_impl):
    """Inject a fake mineru_kie_sdk module; return the captured-state dict."""
    state = {"uploaded_path": None, "existed_after": None}
    mod = types.ModuleType("mineru_kie_sdk")

    class FakeClient:
        def __init__(self, **kwargs):
            state["init_kwargs"] = kwargs

        def upload_file(self, path):
            state["uploaded_path"] = str(path)
            state["existed_during"] = os.path.exists(path)
            return [1]

        def get_result(self, **kwargs):
            state["get_kwargs"] = kwargs
            return get_result_impl()

    mod.MineruKIEClient = FakeClient
    sys.modules["mineru_kie_sdk"] = mod
    return state


def test_call_mineru_returns_parse_dict():
    from services.parsing import parser
    state = _install_fake_sdk(lambda: {"parse": {"blocks": [{"type": "text"}]}, "split": None})
    result = asyncio.run(parser._call_mineru(b"%PDF-1.4 fake"))
    assert result == {"blocks": [{"type": "text"}]}
    assert state["uploaded_path"].endswith(".pdf")
    assert state["existed_during"] is True
    # temp file cleaned up after the call
    assert not os.path.exists(state["uploaded_path"])
    del sys.modules["mineru_kie_sdk"]


def test_call_mineru_unwraps_result_object():
    from services.parsing import parser

    class FakeParse:
        def get_result(self):
            return {"blocks": [{"type": "table"}]}

    _install_fake_sdk(lambda: {"parse": FakeParse()})
    result = asyncio.run(parser._call_mineru(b"x"))
    assert result == {"blocks": [{"type": "table"}]}
    del sys.modules["mineru_kie_sdk"]


def test_call_mineru_propagates_errors():
    from services.parsing import parser

    def boom():
        raise TimeoutError("poll timed out")

    _install_fake_sdk(boom)
    with pytest.raises(TimeoutError):
        asyncio.run(parser._call_mineru(b"x"))
    del sys.modules["mineru_kie_sdk"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k call_mineru -v`
Expected: FAIL —— 旧 `_call_mineru` 走 httpx，签名/行为不符（断言失败或类型错误）

- [ ] **Step 3: 替换 `_call_mineru` 实现**

In `backend/services/parsing/parser.py`, replace the existing `_call_mineru` function with:

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k call_mineru -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/services/parsing/parser.py backend/tests/test_parser_sdk.py
git commit -m "feat(parsing): _call_mineru 对接 mineru-kie-sdk 云端 SDK

同步 _sync_mineru_call 用 tempfile 落临时 PDF 后经 MineruKIEClient 上传+轮询, finally
清理临时文件; 兼容 parse 为 dict 或带 get_result() 的对象; 外层用 asyncio.to_thread 包裹
避免阻塞事件循环; 异常向上抛由 worker 标 failed。"
```

---

## Task 5: 图片落 MinIO + 写库回填 block_id + presigned URL

**Files:**
- Modify: `backend/services/parsing/parser.py` (新增 MinIO 助手 + `_upload_figures`；改 `_write_blocks`；改 `_describe_figures` URL)
- Test: `backend/tests/test_parser_sdk.py`

- [ ] **Step 1: 追加失败测试 —— 解码/上传/回填/写库 lastrowid**

Append to `backend/tests/test_parser_sdk.py`:

```python
from unittest.mock import AsyncMock, MagicMock
from services.parsing.parser import Block


def test_decode_image_bytes_and_base64():
    from services.parsing import parser
    assert parser._decode_image(b"raw") == b"raw"
    import base64
    b64 = base64.b64encode(b"hello").decode()
    assert parser._decode_image(b64) == b"hello"
    # data URI prefix tolerated
    assert parser._decode_image("data:image/png;base64," + b64) == b"hello"


def test_write_blocks_fills_block_id():
    from services.parsing import parser
    db = AsyncMock()
    db.execute.return_value = MagicMock(lastrowid=42)
    blocks = [Block(block_type="text", content="a")]
    asyncio.run(parser._write_blocks(7, 3, blocks, db))
    assert blocks[0].block_id == 42
    assert db.execute.await_count == 1


def test_upload_figures_uploads_and_backfills(monkeypatch):
    from services.parsing import parser

    fake_client = MagicMock()
    fake_client.bucket_exists.return_value = True
    monkeypatch.setattr(parser, "_minio_client", lambda: fake_client)

    db = AsyncMock()
    blocks = [Block(block_type="figure", content="cap", raw_image=b"PNG", block_id=9)]
    asyncio.run(parser._upload_figures(user_id=5, paper_id=2, blocks=blocks, db=db))

    assert blocks[0].image_key == "5/2/9.png"
    # put_object called with the computed key
    args, kwargs = fake_client.put_object.call_args
    assert args[0] == parser.settings.MINIO_BUCKET_FIG
    assert args[1] == "5/2/9.png"
    # UPDATE doc_blocks executed
    assert db.execute.await_count == 1


def test_upload_figures_skips_when_no_raw_image(monkeypatch):
    from services.parsing import parser
    fake_client = MagicMock()
    monkeypatch.setattr(parser, "_minio_client", lambda: fake_client)
    db = AsyncMock()
    blocks = [Block(block_type="figure", content="cap", image_key="x/y/z.png", block_id=1)]
    asyncio.run(parser._upload_figures(user_id=5, paper_id=2, blocks=blocks, db=db))
    fake_client.put_object.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k "decode_image or write_blocks or upload_figures" -v`
Expected: FAIL —— `AttributeError: module ... has no attribute '_decode_image'` 等

- [ ] **Step 3: 新增 MinIO 助手 + `_upload_figures`**

In `backend/services/parsing/parser.py`, add a new section before the DB writes section (before `_write_blocks`):

```python
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


async def _upload_figures(user_id: int, paper_id: int, blocks: list[Block], db: AsyncSession) -> None:
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
                text("UPDATE doc_blocks SET image_key=:k WHERE id=:id AND user_id=:uid"),
                {"k": key, "id": b.block_id, "uid": user_id},
            )
        except Exception as e:
            logger.warning(f"[parse] figure upload failed block_id={b.block_id}: {e}")
```

- [ ] **Step 4: 改 `_write_blocks` 回填 block_id**

In `backend/services/parsing/parser.py`, replace the existing `_write_blocks` function with:

```python
async def _write_blocks(user_id: int, paper_id: int, blocks: list[Block], db: AsyncSession) -> None:
    for b in blocks:
        result = await db.execute(
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
        b.block_id = result.lastrowid
```

- [ ] **Step 5: 改 `_describe_figures` 用 presigned URL**

In `backend/services/parsing/parser.py`, in `_describe_figures`, replace the body of the inner `_describe` coroutine's URL construction. Replace:

```python
            image_url = (
                f"http://{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET_FIG}/{block.image_key}"
            )
            block.content_zh = await vlm_describe_image(image_url, caption=block.content)
```

with:

```python
            client = _minio_client()
            image_url = await asyncio.to_thread(
                _sync_presigned, client, settings.MINIO_BUCKET_FIG, block.image_key
            )
            block.content_zh = await vlm_describe_image(image_url, caption=block.content)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k "decode_image or write_blocks or upload_figures" -v`
Expected: PASS (4 passed)

- [ ] **Step 7: 提交**

```bash
git add backend/services/parsing/parser.py backend/tests/test_parser_sdk.py
git commit -m "feat(parsing): 图片落 MinIO + 写库回填 block_id/image_key

新增 parser 内最小 MinIO 封装(_minio_client/_decode_image/_sync_put_object/_sync_presigned);
_upload_figures 先用 block_id 拼 key 上传 figures bucket 再 UPDATE 回填 image_key;
_write_blocks 用 lastrowid 回填 Block.block_id; _describe_figures 改用 presigned URL 供 VLM 访问。"
```

---

## Task 6: 串联 `parse_paper` 主流程（新写库顺序 + 状态更新）

**Files:**
- Modify: `backend/services/parsing/parser.py:215-262` (`parse_paper`)
- Test: `backend/tests/test_parser_sdk.py`

- [ ] **Step 1: 追加失败测试 —— 端到端（全部依赖打桩）**

Append to `backend/tests/test_parser_sdk.py`:

```python
def test_parse_paper_end_to_end(monkeypatch):
    from services.parsing import parser

    parse_result = {"blocks": [
        {"type": "text", "content": "Hello", "page_idx": 0},
        {"type": "figure", "image": b"PNG", "caption": "Fig", "page_idx": 0},
    ]}

    async def fake_call_mineru(pdf_bytes):
        return parse_result

    async def fake_upload_figures(user_id, paper_id, blocks, db):
        return None

    async def fake_describe(blocks):
        return None

    async def fake_refs(blocks):
        return [{"title": "Ref A", "authors": ["X"], "year": 2020, "raw_ref": "X 2020"}]

    monkeypatch.setattr(parser, "_call_mineru", fake_call_mineru)
    monkeypatch.setattr(parser, "_upload_figures", fake_upload_figures)
    monkeypatch.setattr(parser, "_describe_figures", fake_describe)
    monkeypatch.setattr(parser, "_extract_refs_llm", fake_refs)

    db = AsyncMock()
    db.execute.return_value = MagicMock(lastrowid=1)

    result = asyncio.run(parser.parse_paper(
        user_id=5, paper_id=2, pdf_key="5/2/original.pdf", db=db, pdf_bytes=b"%PDF",
    ))

    assert len(result.blocks) == 2
    assert result.references[0]["title"] == "Ref A"
    db.commit.assert_awaited()
    # papers status update executed (look for an UPDATE papers ... done call)
    sql_calls = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "UPDATE papers" in sql_calls


def test_parse_paper_requires_pdf_bytes_for_sdk():
    from services.parsing import parser
    db = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(parser.parse_paper(
            user_id=5, paper_id=2, pdf_key="k", db=db, pdf_bytes=None,
        ))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k parse_paper -v`
Expected: FAIL —— 现有 `parse_paper` 不要求 pdf_bytes、无 `UPDATE papers`、写库顺序不含图上传

- [ ] **Step 3: 替换 `parse_paper` 实现**

In `backend/services/parsing/parser.py`, replace the existing `parse_paper` function with:

```python
async def parse_paper(
    user_id: int,
    paper_id: int,
    pdf_key: str,
    db: AsyncSession,
    *,
    pdf_bytes: bytes | None = None,  # required for MinerU KIE SDK
) -> ParseResult:
    """
    Full parse pipeline for one paper. Called from RQ worker (not request thread).
    Writes doc_blocks/citations, uploads figures, updates papers.status, returns ParseResult.
    """
    logger.info(
        f"[parse] paper_id={paper_id} user_id={user_id} "
        f"provider={settings.REFERENCE_PARSER_PROVIDER}"
    )

    if pdf_bytes is None:
        logger.warning("[parse] MinerU KIE SDK requires pdf_bytes; none provided")
        raise ValueError("pdf_bytes is required for MinerU KIE parsing")

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

    # --- Step 5: reference extraction ---
    if settings.REFERENCE_PARSER_PROVIDER == "grobid" and pdf_bytes is not None:
        references = await _extract_refs_grobid(pdf_bytes)
    else:
        references = await _extract_refs_llm(blocks)
    logger.info(f"[parse] extracted {len(references)} references")
    await _write_citations(paper_id, references, db)

    # --- Step 6: mark paper done ---
    await db.execute(
        text("UPDATE papers SET status='done' WHERE id=:pid AND user_id=:uid"),
        {"pid": paper_id, "uid": user_id},
    )
    await db.commit()

    return ParseResult(
        paper_id=paper_id,
        user_id=user_id,
        blocks=blocks,
        references=references,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_parser_sdk.py -k parse_paper -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `cd backend && python -m pytest tests -v`
Expected: PASS (all tests, ~20 passed)

- [ ] **Step 6: 提交**

```bash
git add backend/services/parsing/parser.py backend/tests/test_parser_sdk.py
git commit -m "feat(parsing): parse_paper 串联新流程(写块→传图→VLM→引用→状态)

调整为先写 doc_blocks 拿 block_id 再上传图回填 image_key 的顺序; SDK 模式强制要求
pdf_bytes(缺失抛 ValueError); 末尾 UPDATE papers.status='done' 带 user_id 隔离; commit 收尾。"
```

---

## Task 7: 验证文档 + 收尾

**Files:**
- Create: `docs/STATUS.md`（更新解析服务对接验证记录）

- [ ] **Step 1: 运行完整测试套件并截取输出**

Run: `cd backend && python -m pytest tests -v`
记录通过数与耗时，用于验证报告。

- [ ] **Step 2: 编写验证报告**

Create `docs/STATUS.md` documenting:
- 实现摘要（5 个单元各自做了什么，对应 implement-parsing.md 验收项打勾）
- **容错归一化的字段映射假设表**（type 别名、content/page/bbox/image 取值优先级）—— 供真实联调核对修正
- 离线单元测试覆盖项 + `python -m pytest tests` 输出结果
- **Docker 环境待验证项**清单（真实 MinerU 凭据解析、MinIO 实际上传/presigned、MySQL 真实写入、VLM 真实调用、`pdf_bytes` 由 worker 从 MinIO 取字节传入的接线）
- 已知契约收紧说明（SDK 模式必须 pdf_bytes）

内容必须如实反映测试实际结果，不得声称未验证项已通过。

- [ ] **Step 3: 提交**

```bash
git add docs/STATUS.md
git commit -m "docs: 更新 STATUS.md 记录任务1解析服务对接验证结果

记录5单元实现与验收项对照、容错归一化字段映射假设表、离线单测覆盖与结果、
Docker环境待验证项清单及SDK模式pdf_bytes契约收紧说明。"
```

- [ ] **Step 4: 更新 MEMORY.md（如有跨会话洞察）**

If worth persisting, append to `MEMORY.md` a line about: MinerU KIE 是云端 SDK(非本地容器)、同步需 to_thread 包裹、图上传依赖先写库拿 block_id。Then commit.

---

## Self-Review

**Spec coverage:**
- MinerU SDK 对接 → Task 4 ✅
- 容错归一化 → Task 2+3 ✅
- 图片落 MinIO + image_key 回填 → Task 5 ✅
- VLM 图描述（presigned URL） → Task 5 ✅
- 参考文献 LLM 提取 + 写 citations → 复用现有 `_extract_refs_llm`，Task 6 串联 ✅
- 写 doc_blocks + 回填 block_id → Task 5 ✅
- papers.status 更新 → Task 6 ✅
- 配置新增 → Task 1 ✅
- pdf_bytes 契约收紧 → Task 6 ✅
- 错误处理矩阵 → 各 Task 的 try/warning + Task6 ValueError ✅
- docs/STATUS.md 验证文档 → Task 7 ✅
- 测试策略（离线单测 + Docker 待验证标注） → Task 2-6 单测 + Task 7 文档 ✅

**Placeholder scan:** 无 TBD/TODO；每个改代码步骤均含完整代码。

**Type consistency:** `Block.block_id`/`raw_image`（Task2 定义，Task5/6 使用一致）；`_call_mineru`(async)、`_sync_mineru_call`、`_mineru_to_blocks`、`_upload_figures`、`_write_blocks`、`_decode_image`、`_minio_client`、`_sync_put_object`、`_sync_presigned` 命名跨任务一致；测试均从 `services.parsing.parser` 导入。
