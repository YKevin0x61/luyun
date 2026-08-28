"""Print / QR output surfaces: CSS rules and page wiring, not a second renderer."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE_CSS_PATHS = (
    ROOT / "public" / "recipe.css",
    ROOT / "admin-web" / "public" / "recipe.css",
)
PRINT_VIEW = ROOT / "admin-web" / "src" / "views" / "recipe" / "RecipePrintView.vue"
QR_VIEW = ROOT / "admin-web" / "src" / "views" / "recipe" / "RecipeQrView.vue"


def test_print_css_keeps_recipe_card_unbroken_and_three_column_grid():
    needle = (
        ".markdown-body .recipe-card,.sop-density-compact .markdown-body .recipe-card"
        "{display:flex;width:auto;margin:0;break-inside:avoid;page-break-inside:avoid;"
    )
    for path in RECIPE_CSS_PATHS:
        css = path.read_text(encoding="utf-8")
        assert needle in css
        assert "grid-template-columns:repeat(3,1fr)" in css


def test_print_view_embeds_station_content_html():
    src = PRINT_VIEW.read_text(encoding="utf-8")
    assert "data.content_html" in src
    assert 'v-html="bodyHtml"' in src
    assert "sop-print-station" in src


def test_qr_view_lists_stations_and_encodes_detail_slug():
    src = QR_VIEW.read_text(encoding="utf-8")
    assert "/api/recipes/stations" in src
    assert "/recipe/detail?slug=" in src
