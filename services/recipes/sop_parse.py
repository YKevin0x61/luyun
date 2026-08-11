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


def infer_recipe_is_new(recipe_name: str, body_markdown: str) -> bool:
    """名称或正文中含「【新】」则视为新品（与 SOP 文档约定一致）。"""
    return NEW_PRODUCT_MARK in (recipe_name or "") or NEW_PRODUCT_MARK in (body_markdown or "")


@dataclass(frozen=True)
class ParsedRecipe:
    section: str
    recipe_name: str
    body_markdown: str
    sort_order: int
    is_new: bool = False
    is_active: bool = True


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
            display_new = r.is_new or infer_recipe_is_new(r.recipe_name, r.body_markdown)
            classes = ["recipe-title"]
            if display_new:
                classes.append("recipe-title--new")
            if not r.is_active:
                classes.append("recipe-title--inactive")
            if len(classes) > 1:
                safe = html_escape(r.recipe_name.strip(), quote=True)
                parts.append(f'<h3 class="{" ".join(classes)}">{safe}</h3>')
            else:
                parts.append(f"### {r.recipe_name}")
            parts.append("")
            parts.append(r.body_markdown.strip())
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


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
