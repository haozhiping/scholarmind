from common.config import settings


def test_mineru_kie_config_present():
    assert settings.MINERU_KIE_BASE_URL == "https://mineru.net/api/kie"
    assert hasattr(settings, "MINERU_PIPELINE_ID")
    assert hasattr(settings, "MINERU_API_KEY")
    assert settings.MINERU_POLL_INTERVAL == 5
    assert settings.MINERU_TIMEOUT == 300


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
