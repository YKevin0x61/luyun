"""Canonical recipe sections. Keep in lockstep with admin-web/src/utils/recipeManageOrder.js."""

from __future__ import annotations

RECIPE_SECTIONS = ("配方", "出品标准", "检核要求", "食安要求")
DEFAULT_RECIPE_SECTION = "配方"
SECTION_NOT_ALLOWED_MSG = "章节必须是配方、出品标准、检核要求或食安要求"


class RecipeSectionError(ValueError):
    pass


def require_recipe_section(name: str) -> str:
    text = (name or "").strip()
    if text not in RECIPE_SECTIONS:
        raise RecipeSectionError(SECTION_NOT_ALLOWED_MSG)
    return text


def canonicalize_section(name: str) -> str:
    """Map a stored or imported heading onto one of the four sections."""
    text = (name or "").strip()
    if text in RECIPE_SECTIONS:
        return text
    if "食安" in text:
        return "食安要求"
    if "检核" in text:
        return "检核要求"
    if "出品" in text and "标准" in text:
        return "出品标准"
    return DEFAULT_RECIPE_SECTION


def section_sort_key(section: str) -> tuple[int, int]:
    try:
        return (0, RECIPE_SECTIONS.index(section))
    except ValueError:
        return (1, 0)
