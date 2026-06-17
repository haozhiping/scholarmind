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
