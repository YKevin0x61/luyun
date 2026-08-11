"""
将岗位页 Markdown 渲染后的扁平 HTML 包装为「章节 + 卡片网格」结构，
便于响应式排版与 A4 打印；增删条目时仅依赖 h1/h2/h3 层级，无需手写版式类名。
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag


def _take_first_element(parent: Tag) -> Tag | None:
    for child in list(parent.children):
        if isinstance(child, Tag):
            return child.extract()
    return None


def _classes(tag: Tag) -> list[str]:
    raw = tag.get("class")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _is_print_page_break_div(tag: Tag) -> bool:
    return tag.name == "div" and "sop-print-page-break" in _classes(tag)


def wrap_sop_semantic_layout(html_fragment: str) -> str:
    soup = BeautifulSoup(f"<div class='md-frag'>{html_fragment}</div>", "html.parser")
    root = soup.select_one(".md-frag")
    if root is None:
        return html_fragment

    doc = soup.new_tag("div", attrs={"class": "sop-doc"})

    while True:
        el = _take_first_element(root)
        if el is None:
            break

        if el.name == "h1":
            wrap = soup.new_tag("div", attrs={"class": "sop-doc-title"})
            wrap.append(el)
            doc.append(wrap)
            continue

        if el.name == "h2":
            section = soup.new_tag("section", attrs={"class": "sop-section"})
            head = soup.new_tag("header", attrs={"class": "sop-section-head"})
            head.append(el)
            section.append(head)

            intro: Tag | None = None
            grid = soup.new_tag("div", attrs={"class": "sop-section-grid"})

            while True:
                n2 = _take_first_element(root)
                if n2 is None:
                    break
                if n2.name == "h2":
                    root.insert(0, n2)
                    break

                if _is_print_page_break_div(n2):
                    span = soup.new_tag("div", attrs={"class": "sop-grid-span-break"})
                    span.append(n2)
                    grid.append(span)
                    continue

                if n2.name == "h3":
                    h3_classes = _classes(n2)
                    card_classes = "recipe-card"
                    if "recipe-title--new" in h3_classes:
                        card_classes += " recipe-card--new"
                    if "recipe-title--inactive" in h3_classes:
                        card_classes += " recipe-card--inactive"
                    card = soup.new_tag("article", attrs={"class": card_classes})
                    chead = soup.new_tag("header", attrs={"class": "recipe-card-head"})
                    chead.append(n2)
                    card.append(chead)
                    cbody = soup.new_tag("div", attrs={"class": "recipe-card-body"})
                    card.append(cbody)

                    while True:
                        n3 = _take_first_element(root)
                        if n3 is None:
                            break
                        if n3.name in ("h2", "h3"):
                            root.insert(0, n3)
                            break
                        if _is_print_page_break_div(n3):
                            span2 = soup.new_tag("div", attrs={"class": "sop-card-span-break"})
                            span2.append(n3)
                            cbody.append(span2)
                            continue
                        cbody.append(n3)

                    grid.append(card)
                    continue

                if intro is None:
                    intro = soup.new_tag("div", attrs={"class": "sop-section-intro"})
                    section.append(intro)
                intro.append(n2)

            if any(isinstance(c, Tag) for c in grid.children):
                section.append(grid)
            doc.append(section)
            continue

        doc.append(el)

    return doc.decode_contents()
