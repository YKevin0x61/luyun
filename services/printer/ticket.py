#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把采购今日汇总排成一张热敏小票。

平台只收纯文本 + 少量排版标签，所以版式得在服务端算好：58mm 机型一行
16 个汉字 / 32 个字母（官方 FAQ 原话），80mm 按 48 个半角列推。这里一律
按「半角列」计数，汉字与全角标点各占 2 列。

三件事必须在这里做完，不能留给调用方：

- **折行**：菜名 + 数量 + 库存很容易越过 32 列，越界不折会被机器直接裁掉。
- **字节预算**：接口 content 上限 5000 字节（UTF-8），一个汉字 3 字节；
  80 项的一天约 2.6KB，条目翻倍就会顶住。超了就从后往前少打几个供应商，
  并在票尾写明「还有几项没放下」，而不是让云端报错、店员以为打全了。
- **转义**：内容里出现 < 或 > 会被当成排版标签，统一换全角。

纯函数，不碰网络与数据库——测试见 tests/test_spyun_printer.py。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Tuple

__all__ = [
    "DEFAULT_FONT_SCALE",
    "FONT_SCALES",
    "MAX_CONTENT_BYTES",
    "PAPER_WIDTHS",
    "Ticket",
    "build_ticket",
    "SCALE_TAGS",
    "SCALE_WIDTH_PADDING",
    "display_width",
    "format_qty",
    "normalize_scale",
    "wrap_text",
]

#: 接口硬上限（文档：content 不能超过 5000 字节）。
MAX_CONTENT_BYTES = 5000
#: 实际下发的预算：留 200 字节余量，别正好卡在线上。
DEFAULT_BUDGET_BYTES = MAX_CONTENT_BYTES - 200

#: 纸宽（mm）-> 一行能放多少半角列。58mm 有官方口径；80mm 按热敏通用 48 列。
PAPER_WIDTHS = {58: 32, 80: 48}

#: 字号档。文档只有「放大一倍 / 放大两倍」两种放大标签，外加一个只变高
#: 的 <H>，所以这里给三档：tall 只把字拔高（列宽不变，排版不动）；
#: big 等比放大一倍；huge 等比放大两倍。纸宽是死的，字放大后一行能放的
#: 字反而变少，所以列宽必须跟着除下去，否则右边会被机器直接裁掉。
FONT_SCALES = ("normal", "tall", "big", "huge")
#: 档位 -> 包裹整票的排版标签（空串 = 不加标签）。
SCALE_TAGS = {"normal": "", "tall": "H", "big": "L1", "huge": "L2"}
#: 档位 -> 列宽除数（字放大 N 倍，一行能放的列就剩 1/N）。
SCALE_WIDTH_DIVISORS = {"normal": 1, "tall": 1, "big": 2, "huge": 3}
#: 档位 -> 额外让出的安全列数。实测 58mm 纸 32 列时字贴到纸边仍可读，
#: 但 <L1>/<L2> 放大后按「32/2=16 列」排会整行超出打印头有效宽度、右边被
#: 机器裁掉（2026-11-23 真机：11-21 汇总票右缘缺字）。让出 2 列后不再裁。
SCALE_WIDTH_PADDING = {"normal": 0, "tall": 0, "big": 2, "huge": 2}
#: 默认档位：58mm 上正常字号店员反馈偏小。<H> 只拔高不改列宽，行数不变、
#: 必然不会超宽被裁，所以默认开这一档；要横竖都大再手动切 big / huge。
DEFAULT_FONT_SCALE = "tall"

#: 商品行缩进 4 列（前面 4 个半角空格 ≈ 两个汉字宽）。
INDENT = "    "

#: 不能出现在行首的标点（中文避头尾）。断到这些字前面时，把上一行最后一个字
#: 一起带下来，免得「下单人员：李雨欣」后面跟一行孤零零的「、郑碧莹」。
NO_LINE_START = "、，。；：？！）】》」』’”%"


def display_width(text: str) -> int:
    """按半角列数算宽度：全角（W/F）算 2，其余算 1。"""
    total = 0
    for char in str(text):
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _escape(text: Any) -> str:
    """标签转义：< > 换全角，避免被当成 <BR> / <QRCODE> 之类。"""
    return str(text if text is not None else "").replace("<", "＜").replace(">", "＞")


def format_qty(value: Any) -> str:
    """数量去掉没意义的尾零：12.0 -> 12，12.50 -> 12.5。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value if value is not None else "")
    if number == int(number):
        return str(int(number))
    return ("%.2f" % number).rstrip("0").rstrip(".")


def wrap_text(text: str, width: int, *, indent: str = "") -> list[str]:
    """按显示宽度折行；续行补 indent，让续行与首行左对齐。"""
    if width <= 0:
        return [str(text)]
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in str(text):
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if current_width + char_width > width and current:
            carry = ""
            if char in NO_LINE_START and len(current) > 1 and current[-1] not in NO_LINE_START:
                carry = current[-1]
                current = current[:-1]
            lines.append(current)
            pad = indent
            while pad and display_width(pad) >= width:
                pad = pad[1:]
            current = pad + carry + char
            current_width = display_width(pad) + display_width(carry) + char_width
        else:
            current += char
            current_width += char_width
    lines.append(current)
    return lines


def _rule(width: int, char: str = "-") -> str:
    return char * max(1, width)


def _center(text: str, width: int) -> str:
    pad = max(0, (width - display_width(text)) // 2)
    return " " * pad + text


def _stamp(now: Optional[datetime] = None) -> str:
    value = now or datetime.now()
    return "%04d/%02d/%02d %02d:%02d:%02d" % (
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    )


@dataclass
class Ticket:
    """排好的小票。text 直接就是接口的 content。"""

    text: str
    lines: list[str] = field(default_factory=list)
    byte_length: int = 0
    printed_suppliers: int = 0
    printed_products: int = 0
    total_suppliers: int = 0
    total_products: int = 0
    truncated_products: int = 0
    scale: str = "normal"
    #: 附在票尾的员工餐日期（空串 = 这张票没带员工餐）。
    staff_meal_date: str = ""

    @property
    def truncated(self) -> bool:
        return self.truncated_products > 0


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def normalize_scale(value: Any) -> str:
    """认不出来的字号一律退回默认档，别让一张配置串把票打成原尺寸。"""
    text = str(value or "").strip().lower()
    return text if text in FONT_SCALES else DEFAULT_FONT_SCALE


def _line_break_token(line_break: str) -> str:
    """默认 <BR>（文档里的换行标签）；填 lf 就退回真换行，作真机试错的口子。"""
    return "\n" if str(line_break).strip().lower() in ("lf", "newline", "0") else "<BR>"


def _product_lines(product: Mapping[str, Any], width: int) -> list[str]:
    quantity = format_qty(product.get("quantity"))
    unit = _escape(product.get("unit") or "")
    stock = product.get("stock_qty")
    stock_text = "" if stock is None else "【库" + format_qty(stock) + "】"
    name = _escape(product.get("name") or "")
    amount = quantity + unit + stock_text
    # 字放大后一行只剩 8 个汉字，4 空格缩进太奢侈，缩进也跟着收窄。
    indent = INDENT if width >= 28 else "  "
    single = indent + name + "： " + amount
    if display_width(single) <= width:
        return [single]
    # 窄排版硬折会把「1包」拆成「1」和「包」，店员看不懂；数量单独一行靠右。
    room = max(4, width - len(indent))
    lines = [indent + line for line in wrap_text(name, room)]
    lines.append(" " * max(0, width - display_width(amount)) + amount)
    return lines


def _supplier_block(supplier: Mapping[str, Any], width: int) -> Tuple[list[Tuple[str, bool]], int]:
    """一个供应商整块。返回 [(行, 是否商品行)] 与商品数；没有正数商品时返回 ([], 0)。"""
    name = _escape(supplier.get("supplier_name") or "未分供应商")
    items: list[Tuple[str, bool]] = []
    for line in wrap_text("【" + name + "】", width):
        items.append((line, False))
    items.append((_rule(width), False))
    type_count = 0
    for category in supplier.get("categories") or []:
        products = [
            product
            for product in (category.get("products") or [])
            if _positive(product.get("quantity"))
        ]
        if not products:
            continue
        label = "[" + _escape(category.get("category_name") or "未分类") + "]"
        for line in wrap_text(label, width):
            items.append((line, False))
        for product in products:
            for line in _product_lines(product, width):
                items.append((line, True))
            type_count += 1
    if not type_count:
        return [], 0
    items.append((_rule(4), False))
    for line in wrap_text("采购种类：" + str(type_count) + "种", width):
        items.append((line, False))
    return items, type_count


#: 员工餐段里「要买的东西」最多打这么多行：条目是员工手填的，理论上能到 300 项，
#: 全打会把 5000 字节的预算吃光、把上面的采购汇总挤光。超出的只报个数。
MAX_MEAL_ITEMS = 20


def _short_date(value: Any) -> str:
    """`2026-11-22` → `11-22`。

    58mm 放大档只剩 14 列，`2026-11-22 周日` 是 15 列，会把星期单独甩到下一行；
    年份在票头已经有了，这里只留月-日。
    """
    text = _escape(value or "")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text[5:]
    return text


def _staff_item_text(item: Mapping[str, Any]) -> str:
    """一条员工餐采购项：`土豆 6.5斤`（数量为空就只写名字，同推送文案）。"""
    name = str(item.get("name") or "").strip()
    quantity = format_qty(item.get("quantity"))
    unit = str(item.get("unit") or "").strip()
    if not quantity:
        return name + (" " + unit if unit else "")
    return name + " " + quantity + unit


def _staff_meal_lines(section: Mapping[str, Any], width: int) -> list[str]:
    """票尾的「员工餐采购清单」：提交人 + 他要买的货。

    数据由 services.printer.service 去员工餐域取，这里只排版 —— 打印小票
    不该为了一个附录去认识员工餐的权限模型，下面这些键就是全部契约：
    {date, weekday, submitter, items:[{name, quantity, unit}]}。
    没有条目就整段不打（一个只有标题的段落只会占纸）。
    """
    items = [
        item
        for item in (section.get("items") or [])
        if str(item.get("name") or "").strip()
    ]
    if not items:
        return []
    lines: list[str] = list(wrap_text("【员工餐】采购计划", width))
    caption = " ".join(
        part
        for part in (
            _short_date(section.get("date") or ""),
            _escape(section.get("weekday") or ""),
        )
        if part
    )
    if caption:
        lines.extend(wrap_text(caption, width))
    submitter = str(section.get("submitter") or "").strip()
    if submitter:
        lines.extend(wrap_text("提交人：" + _escape(submitter), width))
    lines.append(_rule(width, "-"))
    for item in items[:MAX_MEAL_ITEMS]:
        lines.extend(wrap_text(_escape(_staff_item_text(item)), width))
    if len(items) > MAX_MEAL_ITEMS:
        lines.extend(
            wrap_text(
                "还有 " + str(len(items) - MAX_MEAL_ITEMS) + " 项没打，见员工餐页",
                width,
            )
        )
    return lines


def _count_products(summary: Mapping[str, Any]) -> Tuple[int, int]:
    suppliers = 0
    products = 0
    for supplier in summary.get("suppliers") or []:
        count = 0
        for category in supplier.get("categories") or []:
            count += sum(
                1
                for product in (category.get("products") or [])
                if _positive(product.get("quantity"))
            )
        if count:
            suppliers += 1
            products += count
    return suppliers, products


def _submitters(summary: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for supplier in summary.get("suppliers") or []:
        for category in supplier.get("categories") or []:
            for product in category.get("products") or []:
                for raw in product.get("submitters") or []:
                    name = str(raw or "").strip()
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
    return names


def _bytes_of(lines: Iterable[str], br: str) -> int:
    return sum(len(line.encode("utf-8")) + len(br) for line in lines)


def build_ticket(
    summary: Mapping[str, Any],
    *,
    paper_width: int = 58,
    now: Optional[datetime] = None,
    line_break: str = "br",
    max_bytes: int = DEFAULT_BUDGET_BYTES,
    cut: Optional[bool] = None,
    scale: Any = DEFAULT_FONT_SCALE,
    staff_meal: Optional[Mapping[str, Any]] = None,
) -> Ticket:
    """把 /api/purchase/summary 的结构排成小票。

    ``staff_meal`` 是次日员工餐的采购清单（见 ``_staff_meal_lines``），给了就附在
    尾部的总计之后、打印时间之前 —— 用户要的是「打今天的货，顺便把明天员工餐
    要买的也一起拣了」。
    """
    base_width = PAPER_WIDTHS.get(int(paper_width or 58), PAPER_WIDTHS[58])
    scale_key = normalize_scale(scale)
    if scale_key == "huge" and base_width < 40:
        # 58mm 上 <L2> 一行只剩 ~5 个汉字，连「2026-11-23」都排不下，
        # 硬排就是整行超宽被裁——这一档在 58mm 上没有实用价值，退回 big。
        scale_key = "big"
    width = max(8, base_width // SCALE_WIDTH_DIVISORS[scale_key] - SCALE_WIDTH_PADDING[scale_key])
    # 汉字占 2 列，列宽取偶数居中才不会歪半格。
    width -= width % 2
    if cut is None:
        # 58mm 多半是撕纸机、没有切刀，发 <CUT> 有在纸上印出字面标签的风险；
        # 80mm 一般带切刀，打完顺手切一刀。真要改，调用方显式传 cut。
        cut = int(paper_width or 58) >= 80
    br = _line_break_token(line_break)
    total_suppliers, total_products = _count_products(summary)

    date_text = _escape(summary.get("date") or "")
    title = (date_text + " 采购汇总").strip()
    if display_width(title) <= width:
        head: list[str] = [_center(title, width)]
    elif date_text:
        # 放大字号后一行只有 8 个汉字，标题硬折会把「汇总」挤到下一行；
        # 拆成「日期」「采购汇总」两行居中，裁纸后更像一张正经小票。
        head = [_center(date_text, width), _center("采购汇总", width)]
    else:
        head = [_center("采购汇总", width)]
    head.append(_rule(width, "="))
    submitters = _submitters(summary)
    if submitters:
        head.extend(wrap_text("下单人员：" + "、".join(submitters), width))
        head.append(_rule(width, "="))
    elif total_suppliers or total_products:
        head.append(_rule(width, "="))
    head.append("")

    staff_lines = _staff_meal_lines(staff_meal, width) if staff_meal else []

    def tail_lines(suppliers: int, products: int, missing: int) -> list[str]:
        # 空日 head 末尾已经留了一个空行，这里不再补；合计那两条横线也省掉，
        # 免得票面一上来就是三条 ====。
        blank_day = not suppliers and not products and not missing
        lines: list[str] = [] if blank_day else [""]
        if missing:
            lines.append("还有 " + str(missing) + " 项没放下")
            lines.append("全量请看 Excel 导出")
            lines.append("")
        if blank_day:
            # 今天一单都没有（可能只剩员工餐要买的货）：别打「0 家 / 0 种」，
            # 那看着像打坏了。
            lines.append("今天没有采购单")
        else:
            lines.append(_rule(width, "="))
            # 字放大后一行放不下「采购种类总数：12 种」，固定标签也得走折行，
            # 否则这几行会比别的行宽出去、被机器裁掉尾巴。
            for text in (
                "供应商总数：" + str(suppliers) + " 家",
                "采购种类总数：" + str(products) + " 种",
            ):
                lines.extend(wrap_text(text, width))
        lines.append(_rule(width, "="))
        if staff_lines:
            lines.extend(staff_lines)
            lines.append(_rule(width, "="))
        # 时间戳绝不拆行：放大字号后一行放不下 19 列的「2026/11/23 09:05:00」，
        # 硬折会变成「2026/1」+「1/23 09:05:00」，读完不知道是哪天。
        stamp = _stamp(now)
        full = "打印时间：" + stamp
        clock = stamp[11:]
        if display_width(full) <= width:
            lines.append(full)
        elif display_width("打印时间：" + clock) <= width:
            lines.append("打印时间：" + clock)
        else:
            lines.append("打印时间：")
            lines.extend(wrap_text(clock, width))
        return lines

    def assemble(used: list[list[str]], suppliers: int, products: int) -> list[str]:
        flat: list[str] = list(head)
        for block in used:
            flat.extend(block)
        flat.extend(tail_lines(suppliers, products, total_products - products))
        return flat

    blocks: list[Tuple[list[Tuple[str, bool]], int, str]] = []
    for supplier in summary.get("suppliers") or []:
        items, count = _supplier_block(supplier, width)
        if items:
            blocks.append((items, count, str(supplier.get("supplier_name") or "")))

    used_lines: list[list[str]] = []
    printed_suppliers = 0
    printed_products = 0
    for items, count, _name in blocks:
        block = [text for text, _is_product in items]
        next_suppliers = printed_suppliers + 1
        next_products = printed_products + count
        trial = used_lines + [block]
        if _bytes_of(assemble(trial, next_suppliers, next_products), br) > max_bytes:
            break
        used_lines = trial
        printed_suppliers = next_suppliers
        printed_products = next_products

    if not used_lines and blocks:
        # 单个供应商就超预算（几百项挤一家）：按行塞，塞到放不下为止。
        items, _count, _name = blocks[0]
        partial: list[str] = []
        partial_products = 0
        for text, is_product in items:
            trial = partial + [text]
            next_products = printed_products + partial_products + (1 if is_product else 0)
            if _bytes_of(assemble([trial], 1 if next_products else 0, next_products), br) > max_bytes:
                break
            partial = trial
            if is_product:
                partial_products += 1
        if partial:
            used_lines = [partial]
            printed_products = partial_products
            printed_suppliers = 1 if partial_products else 0

    lines = assemble(used_lines, printed_suppliers, printed_products)
    # 整票只包一次放大标签（逐行包会把 5000 字节的预算吃掉好几百），
    # 切纸指令留在标签外面，免得放大状态带到下一张纸。
    tag = SCALE_TAGS[scale_key]
    body = br.join(lines)
    text = "<%s>%s</%s>" % (tag, body, tag) if tag else body
    if cut:
        text += br + "<CUT>"
    return Ticket(
        text=text,
        lines=lines,
        byte_length=len(text.encode("utf-8")),
        scale=scale_key,
        printed_suppliers=printed_suppliers,
        printed_products=printed_products,
        total_suppliers=total_suppliers,
        total_products=total_products,
        truncated_products=max(0, total_products - printed_products),
        # 只有真打出这一段时才有日期：空清单（只有菜单）不算。
        staff_meal_date=str((staff_meal or {}).get("date") or "") if staff_lines else "",
    )
