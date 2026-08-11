from services.recipes.rendering import render_markdown_to_html


def test_html_blocks_script_and_unsafe_style():
    html = render_markdown_to_html(
        "# T\n\n## S\n\n<script>alert(1)</script>"
        "<div style=\"background:url(javascript:alert(1))\">x</div>"
        "<a href=\"javascript:alert(1)\" target=\"_self\">bad</a>"
        "<a href=\"https://example.com\" target=\"_blank\">ok</a>"
    )
    assert "<script" not in html
    assert "javascript:" not in html
    assert "background" not in html
    assert 'href="https://example.com"' in html
    assert 'rel="noopener noreferrer"' in html


def test_html_wraps_table_in_scroll():
    html = render_markdown_to_html("## 配方\n\n| A | B |\n|:---|:---|\n| 1 | 2 |\n")
    assert "table-scroll" in html
    assert "<table" in html


def test_html_single_newline_renders_as_line_break():
    """正文内的单行换行应渲染为 <br>，与编辑器逐行罗列保持一致（nl2br）。"""
    html = render_markdown_to_html("## 配方\n\n### 馅\n\n盐：1克\n糖：2克\n")
    assert "<br" in html
    assert "盐：1克" in html
    assert "糖：2克" in html


def test_docx_renders_table_with_correct_dimensions():
    from services.recipes.rendering import render_markdown_to_docx

    md = "# 标题\n\n## 配方\n\n| 名称 | 用量 |\n|:---|:---|\n| 盐 | 1克 |\n| 糖 | 2克 |\n"
    doc = render_markdown_to_docx(md)
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert len(table.rows) == 3
    assert len(table.columns) == 2
    assert table.rows[0].cells[0].text.strip() == "名称"
    assert table.rows[1].cells[0].text.strip() == "盐"


def test_docx_renders_page_break_div():
    from services.recipes.rendering import render_markdown_to_docx

    md = "# T\n\n## A\n\n正文一\n\n<div style=\"page-break-after: always;\"></div>\n\n## B\n\n正文二\n"
    doc = render_markdown_to_docx(md)
    xml = doc.element.xml
    assert 'w:type="page"' in xml
