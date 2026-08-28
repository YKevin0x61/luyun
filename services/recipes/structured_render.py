"""Structured recipe fields → card-body HTML. Does not round-trip through Markdown."""

from __future__ import annotations

from html import escape as html_escape

from .rendering import render_markdown_fragment
from .sop_layout import wrap_sop_semantic_layout
from .sop_parse import ParsedRecipe

# Keep in sync with admin-web/src/utils/recipeIngredients.js
RECIPE_INGREDIENTS_TABLE_CLASS = "recipe-ingredients"
RECIPE_INGREDIENTS_NAME_CLASS = "recipe-ingredients-name"
RECIPE_INGREDIENTS_AMOUNT_CLASS = "recipe-ingredients-amount"


def _esc(value) -> str:
    return html_escape("" if value is None else str(value), quote=True)


def format_ingredient_qty(amount: str, unit: str) -> str:
    amount_text = (amount or "").strip()
    unit_text = (unit or "").strip()
    if amount_text and unit_text:
        return f"{amount_text} {unit_text}"
    return amount_text or unit_text


def render_structured_recipe_body(
    ingredients=None,
    steps=None,
    tips=None,
) -> str:
    """Inner HTML of `.recipe-card-body` from structured fields.

    `steps` and `tips` are accepted for later tickets and ignored here.
    """
    del steps, tips
    rows = []
    for item in ingredients or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        amount = (item.get("amount") or "").strip()
        unit = (item.get("unit") or "").strip()
        if not name and not amount and not unit:
            continue
        rows.append((name, amount, unit))
    if not rows:
        return ""
    parts = [f'<table class="{RECIPE_INGREDIENTS_TABLE_CLASS}"><tbody>']
    for name, amount, unit in rows:
        qty = format_ingredient_qty(amount, unit)
        parts.append(
            "<tr>"
            f'<td class="{RECIPE_INGREDIENTS_NAME_CLASS}">{_esc(name)}</td>'
            f'<td class="{RECIPE_INGREDIENTS_AMOUNT_CLASS}">{_esc(qty)}</td>'
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _heading_html(recipe: ParsedRecipe) -> str:
    classes = ["recipe-title"]
    if recipe.is_new:
        classes.append("recipe-title--new")
    if not recipe.is_active:
        classes.append("recipe-title--inactive")
    id_attr = f' data-recipe-id="{int(recipe.id)}"' if recipe.id is not None else ""
    return (
        f'<h3 class="{" ".join(classes)}"{id_attr}>'
        f"{_esc(recipe.recipe_name.strip())}</h3>"
    )


def _group_recipes_by_section(recipes: list[ParsedRecipe]) -> list[tuple[str, list[ParsedRecipe]]]:
    section_items: dict[str, list[ParsedRecipe]] = {}
    section_min_order: dict[str, int] = {}
    for recipe in recipes:
        section_items.setdefault(recipe.section, []).append(recipe)
        if recipe.section not in section_min_order or recipe.sort_order < section_min_order[recipe.section]:
            section_min_order[recipe.section] = recipe.sort_order
    ordered_sections = sorted(
        section_items.keys(),
        key=lambda section: (section_min_order[section], section),
    )
    return [
        (section, sorted(section_items[section], key=lambda item: item.sort_order))
        for section in ordered_sections
    ]


def render_station_html(station_title: str, recipes: list[ParsedRecipe]) -> str:
    """Full station fragment: structured cards and markdown cards share one grid."""
    parts = [f"<h1>{_esc(station_title)}</h1>"]
    for section, items in _group_recipes_by_section(recipes):
        parts.append(f"<h2>{_esc(section)}</h2>")
        for recipe in items:
            parts.append(_heading_html(recipe))
            if recipe.ingredients:
                body = render_structured_recipe_body(list(recipe.ingredients))
            else:
                body = render_markdown_fragment(recipe.body_markdown)
            if body:
                parts.append(body)
    return wrap_sop_semantic_layout("\n".join(parts))
