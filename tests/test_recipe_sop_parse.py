from services.recipes.sop_parse import (
    ParsedRecipe,
    infer_recipe_is_new,
    recipes_to_display_markdown,
    split_station_markdown_to_recipes,
)


def test_inactive_recipe_gets_inactive_class():
    recipes = [
        ParsedRecipe(section="配方", recipe_name="鲜虾肠粉", body_markdown="米浆 80g",
                     sort_order=0, is_new=False, is_active=True),
        ParsedRecipe(section="配方", recipe_name="叉烧肠", body_markdown="叉烧粒",
                     sort_order=1, is_new=False, is_active=False),
    ]
    md = recipes_to_display_markdown("肠粉档", recipes)
    assert "recipe-title--inactive" in md
    assert "鲜虾肠粉" in md and "叉烧肠" in md


def test_infer_new_from_name():
    assert infer_recipe_is_new("【新】菠菜饺", "x") is True
    assert infer_recipe_is_new("普通", "无") is False


def test_infer_new_from_body_only():
    assert infer_recipe_is_new("名称", "正文含【新】标记") is True


def test_table_column_sets_is_new():
    md = """# 测

## 配方

| 【新】A | 普通B |
|:---|:---|
| 内容a | 内容b |
"""
    _, recipes = split_station_markdown_to_recipes(md)
    by_name = {r.recipe_name: r for r in recipes}
    assert by_name["【新】A"].is_new is True
    assert by_name["普通B"].is_new is False


def test_display_markdown_groups_same_section_into_one_grid():
    """同名章节即使被其他章节穿插、排序错位，也应聚合到同一个 ## 下（单个网格）。"""
    recipes = [
        ParsedRecipe("配方", "A", "a", 0),
        ParsedRecipe("出品标准", "B", "b", 1),
        ParsedRecipe("配方", "C", "c", 2),  # 同属「配方」但排在「出品标准」之后
    ]
    md = recipes_to_display_markdown("测", recipes)
    assert md.count("## 配方") == 1  # 不拆成两个同名网格
    assert "### A" in md and "### C" in md
    # 章节顺序按首次出现（最小 sort_order）：配方(0) 先于 出品标准(1)
    assert md.index("## 配方") < md.index("## 出品标准")
    # 章节内部仍按 sort_order：A(0) 先于 C(2)
    assert md.index("### A") < md.index("### C")


def test_display_markdown_appended_item_stays_in_its_section():
    """模拟「新增追加到全岗位末尾」：新条目仍应归入其所属章节，而非另起一个网格。"""
    recipes = [
        ParsedRecipe("配方", "甲", "x", 0),
        ParsedRecipe("食安要求", "乙", "y", 1),
        ParsedRecipe("配方", "新增丙", "z", 99),  # sort_order 追加到末尾
    ]
    md = recipes_to_display_markdown("测", recipes)
    assert md.count("## 配方") == 1
    # 新增条目归位到「配方」块，先于后续章节「食安要求」
    assert md.index("新增丙") < md.index("## 食安要求")
