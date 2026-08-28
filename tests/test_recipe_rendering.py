from services.recipes.rendering import render_markdown_to_html
from services.recipes.sop_parse import ParsedRecipe
from services.recipes.structured_render import render_station_html, render_structured_recipe_body


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


def test_html_stamps_recipe_id_on_wrapped_card():
    html = render_markdown_to_html(
        '# T\n\n## S\n\n<h3 class="recipe-title" data-recipe-id="42">艇仔粥</h3>\n\n比例 1:1\n'
    )
    assert 'data-recipe-id="42"' in html
    assert 'article' in html
    assert 'recipe-card' in html


def test_html_keeps_structured_ingredient_table_classes():
    html = render_markdown_to_html(
        '# T\n\n## S\n\n### 面团\n\n'
        '<table class="recipe-ingredients"><tbody>'
        '<tr><td class="recipe-ingredients-name">面粉</td>'
        '<td class="recipe-ingredients-amount">200 g</td></tr>'
        '</tbody></table>\n'
    )
    assert "recipe-ingredients" in html
    assert "recipe-ingredients-name" in html
    assert "recipe-ingredients-amount" in html
    assert "table-scroll" not in html


def test_html_strips_non_numeric_recipe_id():
    html = render_markdown_to_html(
        '# T\n\n## S\n\n<h3 class="recipe-title" data-recipe-id="nope">艇仔粥</h3>\n\n比例 1:1\n'
    )
    assert "data-recipe-id" not in html


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


def test_structured_ingredients_empty_omits_table():
    assert render_structured_recipe_body([]) == ""
    assert render_structured_recipe_body(None) == ""


def test_structured_ingredients_one_row():
    html = render_structured_recipe_body(
        [{"name": "面粉", "amount": "200", "unit": "g"}],
    )
    assert html == (
        '<table class="recipe-ingredients"><tbody>'
        '<tr>'
        '<td class="recipe-ingredients-name">面粉</td>'
        '<td class="recipe-ingredients-amount">200 g</td>'
        '</tr>'
        '</tbody></table>'
    )


def test_structured_ingredients_many_rows():
    html = render_structured_recipe_body([
        {"name": "面粉", "amount": "200", "unit": "g"},
        {"name": "水", "amount": "120", "unit": "ml"},
        {"name": "盐", "amount": "2", "unit": "g"},
    ])
    assert html == (
        '<table class="recipe-ingredients"><tbody>'
        '<tr>'
        '<td class="recipe-ingredients-name">面粉</td>'
        '<td class="recipe-ingredients-amount">200 g</td>'
        '</tr>'
        '<tr>'
        '<td class="recipe-ingredients-name">水</td>'
        '<td class="recipe-ingredients-amount">120 ml</td>'
        '</tr>'
        '<tr>'
        '<td class="recipe-ingredients-name">盐</td>'
        '<td class="recipe-ingredients-amount">2 g</td>'
        '</tr>'
        '</tbody></table>'
    )


def test_structured_ingredients_non_numeric_amount():
    html = render_structured_recipe_body(
        [{"name": "胡椒", "amount": "适量", "unit": ""}],
    )
    assert html == (
        '<table class="recipe-ingredients"><tbody>'
        '<tr>'
        '<td class="recipe-ingredients-name">胡椒</td>'
        '<td class="recipe-ingredients-amount">适量</td>'
        '</tr>'
        '</tbody></table>'
    )


def test_structured_ingredients_escapes_html():
    html = render_structured_recipe_body(
        [{"name": "<script>x</script>", "amount": "1<", "unit": 'g>"'}],
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "1&lt;" in html
    assert "&quot;" in html


def test_station_html_keeps_markdown_and_structured_cards_in_one_grid():
    html = render_station_html("肠粉档", [
        ParsedRecipe(
            section="配方", recipe_name="肠粉酱油", body_markdown="酱油：100g",
            sort_order=0, id=1,
        ),
        ParsedRecipe(
            section="配方", recipe_name="面团", body_markdown="",
            sort_order=1, id=2,
            ingredients=({"name": "面粉", "amount": "200", "unit": "g"},),
        ),
    ])
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    grids = soup.select(".sop-section-grid")
    assert len(grids) == 1
    cards = grids[0].select("article.recipe-card")
    assert len(cards) == 2
    assert cards[0].get("data-recipe-id") == "1"
    assert cards[1].get("data-recipe-id") == "2"
    assert cards[0].select_one(".recipe-ingredients") is None
    assert "酱油：100g" in cards[0].select_one(".recipe-card-body").get_text()
    table = cards[1].select_one("table.recipe-ingredients")
    assert table is not None
    assert table.select_one(".recipe-ingredients-name").get_text() == "面粉"
    assert table.select_one(".recipe-ingredients-amount").get_text() == "200 g"


def test_station_html_markdown_only_still_builds_cards():
    html = render_station_html("肠粉档", [
        ParsedRecipe(
            section="配方", recipe_name="肠粉酱油", body_markdown="酱油：100g",
            sort_order=0, id=7,
        ),
    ])
    assert "recipe-card" in html
    assert 'data-recipe-id="7"' in html
    assert "recipe-ingredients" not in html
    assert "酱油：100g" in html


def test_station_html_escapes_recipe_name():
    html = render_station_html("肠粉档", [
        ParsedRecipe(
            section="配方", recipe_name="<img src=x>", body_markdown="x",
            sort_order=0, id=8,
        ),
    ])
    assert "<img" not in html
    assert "&lt;img src=x&gt;" in html
