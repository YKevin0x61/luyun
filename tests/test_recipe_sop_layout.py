"""sop_layout.wrap_sop_semantic_layout 单元测试。"""

from bs4 import BeautifulSoup

from services.recipes.sop_layout import wrap_sop_semantic_layout


def test_inactive_h3_maps_to_inactive_card():
    frag = ('<h2>配方</h2>'
            '<h3 class="recipe-title recipe-title--inactive">叉烧肠</h3><p>叉烧粒</p>')
    out = wrap_sop_semantic_layout(frag)
    assert "recipe-card--inactive" in out


def test_wrap_builds_sections_and_cards():
    html = (
        "<h1>明档</h1>"
        "<h2>粥品</h2>"
        "<h3>艇仔粥</h3><p>比例 1:1</p>"
        "<h3>瘦肉粥</h3><p>肉 100 克</p>"
        "<h2>煎炸</h2>"
        "<h3>春卷</h3><p>油温 180</p>"
    )
    out = wrap_sop_semantic_layout(html)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.select_one(".sop-doc-title h1")
    sections = soup.select("section.sop-section")
    assert len(sections) == 2
    heads = [s.select_one(".sop-section-head h2").get_text(strip=True) for s in sections]
    assert heads == ["粥品", "煎炸"]
    cards = soup.select("article.recipe-card")
    assert len(cards) == 3
    titles = [c.select_one("h3").get_text(strip=True) for c in cards]
    assert titles == ["艇仔粥", "瘦肉粥", "春卷"]


def test_wrap_intro_before_cards():
    html = "<h1>T</h1><h2>节</h2><blockquote>注意</blockquote><h3>A</h3><p>x</p>"
    out = wrap_sop_semantic_layout(html)
    soup = BeautifulSoup(out, "html.parser")
    sec = soup.select_one("section.sop-section")
    assert sec.select_one(".sop-section-intro blockquote")
    assert sec.select_one("article.recipe-card h3").get_text(strip=True) == "A"


def test_wrap_new_recipe_gets_card_modifier_class():
    html = "<h1>T</h1><h2>S</h2><h3 class=\"recipe-title recipe-title--new\">新品A</h3><p>x</p>"
    out = wrap_sop_semantic_layout(html)
    assert "recipe-card--new" in out
    assert "recipe-title--new" in out


def test_wrap_page_break_spans_full_grid():
    html = (
        "<h1>T</h1><h2>S</h2>"
        '<div class="sop-print-page-break" style="page-break-after: always;"></div>'
        "<h3>A</h3><p>x</p>"
    )
    out = wrap_sop_semantic_layout(html)
    assert "sop-grid-span-break" in out
    assert "recipe-card" in out
