"""Print / QR output surfaces: CSS rules and page wiring, not a second renderer."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 两份已漂移的配方样式表都照旧在这里断言（票 07 明确不做合并，只记录）：
# `admin-web/public/recipe.css` 718 行（构建进 dist，是真源）vs 仓库 `public/recipe.css`
# 684 行（旧副本）—— 服务端优先取 dist（main.py 的 `/recipe.css` 路由）。
RECIPE_CSS_PATHS = (
    ROOT / "public" / "recipe.css",
    ROOT / "admin-web" / "public" / "recipe.css",
)
PRINT_VIEW = ROOT / "admin-web" / "src" / "views" / "recipe" / "RecipePrintView.vue"
QR_VIEW = ROOT / "admin-web" / "src" / "views" / "recipe" / "RecipeQrView.vue"
# 五条配方地址的唯一一份常量（票 07 搬进工作台的「后勤」组）。
RECIPE_PATHS_JS = ROOT / "admin-web" / "src" / "utils" / "recipePaths.js"


def test_print_css_packs_unbroken_cards_in_newspaper_columns():
    for path in RECIPE_CSS_PATHS:
        css = path.read_text(encoding="utf-8")
        preview_css = css[: css.index("@media print{")]
        print_css = css[css.index("@media print{") :]
        assert "body.sop-print-preview-page .markdown-body .sop-section-grid" in preview_css
        assert "body.sop-print-preview-page .markdown-body .sop-section-head" in preview_css
        compact_preview = "".join(preview_css.split())
        assert "grid-template-columns:repeat(3,1fr)" in compact_preview
        assert "body.sop-print-preview-page.markdown-body.sop-print-col{display:flex;flex-direction:column;gap:.2cm" in compact_preview
        assert ".sop-print-preview-measure.sop-print-measure-col{width:calc((100%-.56cm)/3)" in compact_preview
        assert "column-count:3" not in preview_css
        assert "column-fill:balance" not in preview_css
        assert "color-scheme:light" in preview_css
        assert "--surface:#ffffff" in preview_css
        assert "--reader-fs:8.5pt" in compact_preview
        assert "font-size:9.5pt" in compact_preview
        assert "font-size:8.5pt;line-height:1.35;padding:.1cm.2cm.15cm" in compact_preview
        compact_print = "".join(print_css.split())
        assert "grid-template-columns:repeat(3,1fr)" in compact_print
        assert "body.sop-print-preview-page.markdown-body.sop-print-col{display:flex;flex-direction:column;gap:.2cm" in compact_print
        assert "column-count:3" not in compact_print
        assert ".markdown-body.sop-section-head{display:none" in compact_print
        card_idx = compact_print.find(".markdown-body.recipe-card")
        card_slice = compact_print[card_idx : card_idx + 400]
        assert "break-inside:avoid" in card_slice
        assert "page-break-inside:avoid" in card_slice
        assert "height:auto" in card_slice
        assert ".sop-print-preview-measure-host{display:none" in compact_print
        assert ".sop-print-preview-sheet-wrap{display:block" in compact_print
        assert "break-after:page" in compact_print
        assert "@page{size:A4;margin:0}" in compact_print
        assert ".sop-print-preview-sheet.is-print-skipped{display:none!important}" in compact_print
        assert ".sop-print-preview-sheet.is-print-tail" in compact_print


def test_print_view_embeds_station_content_html():
    src = PRINT_VIEW.read_text(encoding="utf-8")
    assert "data.content_html" in src
    assert 'v-html="bodyHtml"' in src
    assert "sop-print-station" in src
    assert "paginateStationCards" in src
    assert "sop-print-measure-col" in src
    assert "sop-print-preview-measure" in src
    assert "pageHtmls" in src
    assert "data-theme" in src
    assert "'light'" in src
    assert 'sop-print-preview-measure-host no-print' in src
    assert 'sop-print-preview-sheet-wrap no-print' not in src
    assert 'class="sop-print-preview-sheet-wrap"' in src
    assert "shouldRepackPrintPreview" in src
    assert "afterprint" in src
    assert "syncSelectedPages" in src
    assert "is-print-skipped" in src
    assert "打印已选页" in src
    assert "第 {{ index + 1 }} 页" in src


def test_qr_view_lists_stations_and_encodes_detail_slug():
    """印码页生成的岗位码指向**新**阅读地址（票 07 搬进工作台「后勤」组）。

    岗位码从未张贴过，改地址没有存量风险 —— 但正因如此，地址写错不会有人当场发现，
    这里就得把「印出去的是哪条路径」钉死：常量来自 `recipePaths.js` 那一份（页面里的
    router-link 与登录回跳白名单也引它），不是页面里各自抄的字面量。
    """
    src = QR_VIEW.read_text(encoding="utf-8")
    assert "/api/recipes/stations" in src
    # 拼地址用的是那份常量，而不是再抄一遍 `/recipe/detail`。
    assert "origin}${RECIPE_DETAIL_PATH}?slug=" in src
    assert "from '../../utils/recipePaths'" in src
    # 旧的独立域地址一条都不留（自然 404，不给别名）。
    assert "/recipe/detail?slug=" not in src

    paths = RECIPE_PATHS_JS.read_text(encoding="utf-8")
    assert "export const RECIPE_DETAIL_PATH = '/workbench/kitchen/recipe/detail'" in paths
    assert "export const RECIPE_HOME_PATH = '/workbench/kitchen/recipe'" in paths
