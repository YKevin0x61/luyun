"""
将岗位 SOP Markdown 拆成多条「配方/条目」记录（按表格与段落），供写入 sop_recipes。

规则简述：
- 全文首行 # 为岗位标题（写入 sop_stations.title），不参与条目。
- 按 ## 分节；节内交替识别「GFM 表格块」与「非表格段落」。
- 表格：若仅一行数据行，则按「列」拆成多条（列名取表头）；若多行数据，则按「行」拆成多条（名称取首列，正文为其余列合并）。
- 非表格连续行合并为一条，recipe_name 固定为「（本段说明）」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as html_escape


NEW_PRODUCT_MARK = "【新】"

# Keep in lockstep with admin-web/src/utils/recipeCore.js SCALE_UNIT / SCALE_RE.
SCALE_UNIT = (
    "千克|毫升|kg|mg|mL|ml|cc|克|斤|两|钱|升|杯|勺|滴|只|个|块|片|张|"
    "条|根|瓶|包|袋|盒|颗|粒|份|g|L"
)
SCALE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*([-\~\u2013])\s*(\d+(?:\.\d+)?)(\s*)(" + SCALE_UNIT + r")"
    r"|(\d+)\s*/\s*(\d+)(\s*)(" + SCALE_UNIT + r")"
    r"|(\d+(?:\.\d+)?)(\s*)(" + SCALE_UNIT + r")"
)
_LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d+[.)、])\s+")
_NAME_TRAIL_RE = re.compile(r"[:：]+$")
_SENTENCE_PUNCT_RE = re.compile(r"[。！？]")


def infer_recipe_is_new(recipe_name: str, body_markdown: str) -> bool:
    """名称或正文中含「【新】」则视为新品（与 SOP 文档约定一致）。"""
    return NEW_PRODUCT_MARK in (recipe_name or "") or NEW_PRODUCT_MARK in (body_markdown or "")


def _strip_list_marker(line: str) -> str:
    return _LIST_MARKER_RE.sub("", line.strip())


def _scale_match_to_amount_unit(match: re.Match) -> tuple[str, str]:
    if match.group(1) is not None:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}", match.group(5)
    if match.group(6) is not None:
        return f"{match.group(6)}/{match.group(7)}", match.group(9)
    return match.group(10), match.group(12)


def _looks_like_name_plus_qty(line: str) -> bool:
    """Conservative: SCALE hit AND the line is roughly 'name + quantity', not a sentence."""
    text = _strip_list_marker(line)
    if not text or _SENTENCE_PUNCT_RE.search(text):
        return False
    matches = list(SCALE_RE.finditer(text))
    if len(matches) != 1:
        return False
    match = matches[0]
    before = text[: match.start()].strip()
    after = text[match.end() :].strip()
    if before and after:
        return False
    name = before or after
    if not name or len(name) > 40:
        return False
    return True


def _line_to_ingredient(line: str) -> dict | None:
    text = _strip_list_marker(line)
    match = SCALE_RE.search(text)
    if match is None:
        return None
    amount, unit = _scale_match_to_amount_unit(match)
    before = text[: match.start()].strip()
    after = text[match.end() :].strip()
    name = _NAME_TRAIL_RE.sub("", before or after).strip()
    if not name:
        return None
    return {"name": name, "amount": amount, "unit": unit}


def _classify_plain_line(line: str) -> tuple[dict | None, str | None]:
    """Return (ingredient, None) or (None, step_text) for a non-empty line."""
    if _looks_like_name_plus_qty(line):
        item = _line_to_ingredient(line)
        if item is not None:
            return item, None
    return None, line.strip()


def _amount_text_to_amount_unit(amount_text: str) -> tuple[str, str] | None:
    match = SCALE_RE.search(amount_text)
    if match is None:
        return None
    leftover = (amount_text[: match.start()] + amount_text[match.end() :]).strip()
    if leftover:
        return None
    return _scale_match_to_amount_unit(match)


def _row_is_qty_only_cells(data_row: list[str]) -> bool:
    nonempty = [cell.strip() for cell in data_row if cell.strip()]
    if not nonempty:
        return False
    return all(_amount_text_to_amount_unit(cell) is not None for cell in nonempty)


def _table_block_to_structured(table_lines: list[str]) -> tuple[list[dict], list[str]]:
    rows = [_split_pipe_row(line) for line in table_lines]
    rows = [row for row in rows if row]
    if not rows:
        return [], []

    rest = rows[1:]
    if rest and _is_separator_row(rest[0]):
        header = rows[0]
        data_rows = rest[1:]
        if len(data_rows) == 1 and _row_is_qty_only_cells(data_rows[0]):
            return _header_and_single_row_to_structured(header, data_rows[0])
    else:
        data_rows = rows

    ingredients: list[dict] = []
    steps: list[str] = []
    for data_row in data_rows:
        cells = [cell.strip() for cell in data_row]
        nonempty = [cell for cell in cells if cell]
        if not nonempty:
            continue
        if len(nonempty) == 1:
            item, step = _classify_plain_line(nonempty[0])
            if item is not None:
                ingredients.append(item)
            elif step:
                steps.append(step)
            continue
        name = cells[0]
        amount_text = " ".join(cell for cell in cells[1:] if cell)
        parsed = _amount_text_to_amount_unit(amount_text) if amount_text else None
        if name and parsed is not None:
            amount, unit = parsed
            ingredients.append({"name": name, "amount": amount, "unit": unit})
            continue
        steps.append(" | ".join(nonempty))
    return ingredients, steps


def _header_and_single_row_to_structured(
    header: list[str], data_row: list[str],
) -> tuple[list[dict], list[str]]:
    """One data row: header cells are names (same orientation as _table_to_recipes)."""
    num_cols = max(len(header), len(data_row))
    header = (header + [""] * num_cols)[:num_cols]
    data_row = (data_row + [""] * num_cols)[:num_cols]
    ingredients: list[dict] = []
    steps: list[str] = []
    for name_cell, body_cell in zip(header, data_row):
        name = (name_cell or "").strip()
        body = (body_cell or "").strip()
        if not name and not body:
            continue
        if body:
            parsed = _amount_text_to_amount_unit(body)
            if name and parsed is not None:
                amount, unit = parsed
                ingredients.append({"name": name, "amount": amount, "unit": unit})
                continue
            item, step = _classify_plain_line(body)
            if item is not None:
                ingredients.append(item)
                continue
        leftover = " | ".join(part for part in (name, body) if part)
        if leftover:
            steps.append(leftover)
    return ingredients, steps


def migrate_legacy_to_structured(body_markdown: str) -> dict:
    """Best-effort split of a single recipe body's markdown into structured fields.

    Tips are always empty — never guess. Non-empty leftover lines go to steps.
    """
    ingredients: list[dict] = []
    steps: list[str] = []
    lines = (body_markdown or "").splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            table_ings, table_steps = _table_block_to_structured(table_lines)
            ingredients.extend(table_ings)
            steps.extend(table_steps)
            continue
        item, step = _classify_plain_line(stripped)
        if item is not None:
            ingredients.append(item)
        elif step:
            steps.append(step)
        i += 1
    return {"ingredients": ingredients, "steps": steps, "tips": []}


@dataclass(frozen=True)
class ParsedRecipe:
    section: str
    recipe_name: str
    body_markdown: str
    sort_order: int
    is_new: bool = False
    is_active: bool = True
    id: int | None = None
    ingredients: tuple = ()
    steps: tuple = ()
    tips: tuple = ()
    base_servings_qty: float | None = None
    base_servings_unit: str | None = None


def _split_pipe_row(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    if not s.endswith("|"):
        s = s + "|"
    inner = s[1:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        compact = re.sub(r"\s+", "", cell)
        if not re.fullmatch(r":?-+:?", compact or ""):
            return False
    return True


def _table_to_recipes(section: str, rows: list[list[str]], base_order: int) -> tuple[list[ParsedRecipe], int]:
    if not rows:
        return [], base_order

    header = rows[0]
    rest = rows[1:]
    if rest and _is_separator_row(rest[0]):
        rest = rest[1:]

    if not rest:
        return [], base_order

    num_cols = max(len(header), max((len(r) for r in rest), default=0))
    header = (header + [""] * num_cols)[:num_cols]

    def pad_row(r: list[str]) -> list[str]:
        r = r[:num_cols] if len(r) >= num_cols else r + [""] * (num_cols - len(r))
        return r

    data_rows = [pad_row(r) for r in rest if any(c.strip() for c in r)]
    if not data_rows:
        return [], base_order

    out: list[ParsedRecipe] = []
    order = base_order

    if len(data_rows) == 1:
        row0 = data_rows[0]
        for col_idx in range(num_cols):
            name = (header[col_idx] or "").strip() or f"列{col_idx + 1}"
            body = (row0[col_idx] or "").strip()
            if not name and not body:
                continue
            out.append(
                ParsedRecipe(
                    section=section,
                    recipe_name=name,
                    body_markdown=body,
                    sort_order=order,
                    is_new=infer_recipe_is_new(name, body),
                )
            )
            order += 1
        return out, order

    for data_row in data_rows:
        cells = pad_row(data_row)
        name = (cells[0] or "").strip() or "条目"
        tail = [c.strip() for c in cells[1:] if c.strip()]
        body = "\n\n".join(tail) if tail else ""
        out.append(
            ParsedRecipe(
                section=section,
                recipe_name=name,
                body_markdown=body,
                sort_order=order,
                is_new=infer_recipe_is_new(name, body),
            )
        )
        order += 1

    return out, order


def _flush_prose_block(section: str, lines: list[str], order: int) -> ParsedRecipe | None:
    text = "\n".join(lines).strip()
    if not text:
        return None
    return ParsedRecipe(
        section=section,
        recipe_name="（本段说明）",
        body_markdown=text,
        sort_order=order,
    )


def split_station_markdown_to_recipes(markdown_text: str) -> tuple[str, list[ParsedRecipe]]:
    """
    返回 (station_title, recipes)。station_title 来自首行 #；recipes 按阅读顺序带 sort_order。
    """
    lines = markdown_text.splitlines()
    if not lines:
        return "未命名", []

    title_line = lines[0].strip()
    if title_line.startswith("#"):
        station_title = title_line.lstrip("#").strip() or "未命名"
        body_lines = lines[1:]
    else:
        station_title = "未命名"
        body_lines = lines[:]

    section = "正文"
    order = 0
    recipes: list[ParsedRecipe] = []

    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()

        if stripped.startswith("##"):
            section = stripped.lstrip("#").strip() or "正文"
            i += 1
            continue

        if stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(body_lines) and body_lines[i].strip().startswith("|"):
                table_lines.append(body_lines[i])
                i += 1
            table_rows = [_split_pipe_row(tl) for tl in table_lines]
            table_rows = [r for r in table_rows if r]
            chunk, order = _table_to_recipes(section, table_rows, order)
            recipes.extend(chunk)
            continue

        prose_lines: list[str] = []
        while i < len(body_lines):
            s2 = body_lines[i].strip()
            if s2.startswith("##") or s2.startswith("|"):
                break
            prose_lines.append(body_lines[i])
            i += 1

        block = _flush_prose_block(section, prose_lines, order)
        if block is not None:
            recipes.append(block)
            order += 1

    return station_title, recipes


def recipes_to_display_markdown(station_title: str, recipes: list[ParsedRecipe]) -> str:
    """
    将多条记录拼回一篇带结构的文档：章节用 Markdown ##，条目标题新品用 HTML h3（便于加类名高亮），正文仍为 Markdown。

    同名章节的条目会聚合到同一个 ## 之下（即使它们的 sort_order 不连续、被其他章节穿插），
    避免新增/编辑后因排序错位而在岗位页拆分成多个同名网格（“表格”）。
    - 章节之间的先后：以各章节内最小 sort_order 为准（首次出现顺序）。
    - 章节内部：按 sort_order 升序。
    """
    section_items: dict[str, list[ParsedRecipe]] = {}
    section_min_order: dict[str, int] = {}
    for r in recipes:
        section_items.setdefault(r.section, []).append(r)
        if r.section not in section_min_order or r.sort_order < section_min_order[r.section]:
            section_min_order[r.section] = r.sort_order

    ordered_sections = sorted(
        section_items.keys(),
        key=lambda s: (section_min_order[s], s),
    )

    parts: list[str] = [f"# {station_title}", ""]
    for section in ordered_sections:
        parts.append(f"## {section}")
        parts.append("")
        for r in sorted(section_items[section], key=lambda x: x.sort_order):
            parts.append(_recipe_heading_markdown(r))
            parts.append("")
            parts.append(r.body_markdown.strip())
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _recipe_heading_markdown(r: ParsedRecipe) -> str:
    classes = ["recipe-title"]
    if r.is_new:
        classes.append("recipe-title--new")
    if not r.is_active:
        classes.append("recipe-title--inactive")
    needs_html = len(classes) > 1 or r.id is not None
    if not needs_html:
        return f"### {r.recipe_name}"
    safe = html_escape(r.recipe_name.strip(), quote=True)
    id_attr = f' data-recipe-id="{int(r.id)}"' if r.id is not None else ""
    return f'<h3 class="{" ".join(classes)}"{id_attr}>{safe}</h3>'


@dataclass(frozen=True)
class Block:
    section: str
    body_markdown: str
    sort_order: int
    is_new: bool = False


def infer_block_is_new(body_markdown: str) -> bool:
    """正文含「【新】」则视为新品。"""
    return NEW_PRODUCT_MARK in (body_markdown or "")


def split_station_markdown_to_blocks(markdown_text: str) -> tuple[str, list[Block]]:
    """
    返回 (station_title, blocks)。
    - 首行 # 为岗位标题。
    - 按 ## 切节；每节内全部内容（表格+散文）原样进 body_markdown，仅首尾 strip。
    - ## 之前、# 之后的内容（若有）归入默认章节「正文」。
    """
    lines = markdown_text.splitlines()
    if not lines:
        return "未命名", []

    title_line = lines[0].strip()
    if title_line.startswith("#") and not title_line.startswith("##"):
        station_title = title_line.lstrip("#").strip() or "未命名"
        body_lines = lines[1:]
    else:
        station_title = "未命名"
        body_lines = lines[:]

    blocks: list[Block] = []
    order = 0
    current_section = "正文"
    buf: list[str] = []

    def flush() -> None:
        nonlocal order
        text = "\n".join(buf).strip()
        if text:
            blocks.append(
                Block(
                    section=current_section,
                    body_markdown=text,
                    sort_order=order,
                    is_new=infer_block_is_new(text),
                )
            )
            order += 1

    for line in body_lines:
        if line.strip().startswith("##"):
            flush()
            buf = []
            current_section = line.strip().lstrip("#").strip() or "正文"
            continue
        buf.append(line)
    flush()

    return station_title, blocks


def blocks_to_display_markdown(station_title: str, blocks: list[Block]) -> str:
    """将块拼回整篇：# 标题 + 每块 ## 章节 + body_markdown，按 sort_order。"""
    parts: list[str] = [f"# {station_title}", ""]
    for b in sorted(blocks, key=lambda x: x.sort_order):
        parts.append(f"## {b.section}")
        parts.append("")
        parts.append(b.body_markdown.strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
