from services.recipes.sections import (
    DEFAULT_RECIPE_SECTION,
    RECIPE_SECTIONS,
    SECTION_NOT_ALLOWED_MSG,
    RecipeSectionError,
    canonicalize_section,
    require_recipe_section,
    section_sort_key,
)
from services.recipes.sop_parse import ParsedRecipe, recipes_to_display_markdown


def test_require_accepts_only_the_four_sections():
    for name in RECIPE_SECTIONS:
        assert require_recipe_section(f"  {name}  ") == name
    try:
        require_recipe_section("粥品")
        assert False, "expected RecipeSectionError"
    except RecipeSectionError as exc:
        assert str(exc) == SECTION_NOT_ALLOWED_MSG


def test_canonicalize_maps_legacy_headings():
    assert canonicalize_section("配方") == "配方"
    assert canonicalize_section("粥品") == DEFAULT_RECIPE_SECTION
    assert canonicalize_section("浆比例") == DEFAULT_RECIPE_SECTION
    assert canonicalize_section("新品馅料") == DEFAULT_RECIPE_SECTION
    assert canonicalize_section("二十大招牌检核") == "检核要求"
    assert canonicalize_section("常规检核") == "检核要求"
    assert canonicalize_section("食安要求") == "食安要求"
    assert canonicalize_section("出品标准") == "出品标准"
    assert canonicalize_section("  ") == DEFAULT_RECIPE_SECTION


def test_display_markdown_uses_canonical_section_order():
    md = recipes_to_display_markdown(
        "明档",
        [
            ParsedRecipe("食安要求", "异物", "x", 0),
            ParsedRecipe("检核要求", "咸蛋黄肉松蛋挞", "y", 1),
            ParsedRecipe("配方", "艇仔粥", "z", 2),
            ParsedRecipe("出品标准", "出餐不符", "w", 3),
        ],
    )
    assert md.index("## 配方") < md.index("## 出品标准")
    assert md.index("## 出品标准") < md.index("## 检核要求")
    assert md.index("## 检核要求") < md.index("## 食安要求")


def test_section_sort_key_puts_unknown_last():
    assert section_sort_key("配方") < section_sort_key("出品标准")
    assert section_sort_key("食安要求") < section_sort_key("粥品")
