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
