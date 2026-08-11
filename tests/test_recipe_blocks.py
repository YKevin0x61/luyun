from services.recipes.sop_parse import (
    Block,
    split_station_markdown_to_blocks,
    blocks_to_display_markdown,
    infer_block_is_new,
)


def test_split_keeps_table_block_verbatim():
    md = """# 肠粉档

## 配方

| A | B |
|:---|:---|
| 1 | 2 |

## 浆比例

| 肠粉浆 |
|:---|
| 水：2.3斤 |

文字说明一行
"""
    title, blocks = split_station_markdown_to_blocks(md)
    assert title == "肠粉档"
    assert [b.section for b in blocks] == ["配方", "浆比例"]
    assert [b.sort_order for b in blocks] == [0, 1]
    assert "| A | B |" in blocks[0].body_markdown
    assert "|:---|:---|" in blocks[0].body_markdown
    assert "文字说明一行" in blocks[1].body_markdown


def test_infer_new_from_block_body():
    assert infer_block_is_new("含【新】的正文") is True
    assert infer_block_is_new("普通正文") is False


def test_split_marks_new_block():
    md = "# 测\n\n## 新品\n\n| 【新】A |\n|:---|\n| x |\n"
    _, blocks = split_station_markdown_to_blocks(md)
    assert blocks[0].is_new is True


def test_preamble_before_first_h2_goes_to_default_section():
    md = "# 测\n\n开场说明\n\n## 配方\n\n| A |\n|:---|\n| 1 |\n"
    _, blocks = split_station_markdown_to_blocks(md)
    assert blocks[0].section == "正文"
    assert "开场说明" in blocks[0].body_markdown
    assert blocks[1].section == "配方"


def test_blocks_roundtrip_to_display_markdown():
    md = "# 肠粉档\n\n## 配方\n\n| A | B |\n|:---|:---|\n| 1 | 2 |\n"
    title, blocks = split_station_markdown_to_blocks(md)
    out = blocks_to_display_markdown(title, blocks)
    assert out.startswith("# 肠粉档")
    assert "## 配方" in out
    assert "| A | B |" in out
