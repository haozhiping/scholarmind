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
