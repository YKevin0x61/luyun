#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当月考勤表投影成那份《厨房排班表》.xlsx（票 08）。

**只做版式，不算任何口径**：进来的 ``sheet`` 就是 `AttendanceStore.sheet()` 的那份响应，
出去的是可以直接下发的字节。本月出勤天数、加班小时、年限、是否新入职、存休天数全部
取响应上已经算好的值 —— 这一层再算一遍就成了第二套口径（`docs/adr/0112` 说这张表是
**投影**，不是新的事实来源）。

版式逐项实测于 6 份历史样表，规格见 `.scratch/考勤表导出/export-layout.md` 与
`.scratch/考勤表导出/样例表结构实测报告.md`（复刻 4~8 月那套模板族，2 月那份旧模板
不复刻）。钉死的几件事：

* 表名 ``Sheet1 `` **带一个尾随空格**（六份样表完全一致），单工作表；
* R1 大标题、R2 月份行（**不写店名**）、R3 空样式行、R4/R5 两行表头，A~H 纵向合并；
* **列数随当月天数变**：日期列从 I 列起铺 ``当月自然天数`` 列，30 天的月根本没有 31 日那一列，
  汇总三列（存休天数 / 是否新入职 / 是否离职）跟着平移；
* 日格只有「留空（出勤）/ 休 / 休半天 / 待补」四种写法，``待补`` 黄底标出；
* 底部三块（班次时间行、注意行、尾部空块）整行合并，注意行原文一字不差；
* 横向 A4 / fitToPage / 页边距 0 / 定义 ``_xlnm.Print_Area`` / 周六日表头黄底 / 两类条件格式。

**「借调门店」不写进日格**：历史表是把它写在**某一天**的格子上（`城光荟` / `同和` /
`调东莞`），而台账是按月记的、没有日期可落 —— 父级裁定不造一个日期出来，那一列只留在
卡片上。这是与历史表唯一有意的偏差（票面验收 5 点名要求写进报告的遗留）。
"""

from __future__ import annotations

import io
from calendar import monthrange
from datetime import date
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

__all__ = ["EXPORT_CONTENT_TYPE", "attendance_export_filename", "build_attendance_workbook"]

# 表名带**尾随空格**：六份样表逐字一致，改了它导出件就跟店长手里那份对不上。
SHEET_TITLE = "Sheet1 "

TITLE_TEXT = "厨房排班表"
# 底部两块（「班次时间：」这一行的右侧留白是给店长手写班次时间的）。
SHIFT_TIME_TEXT = "班次时间："
NOTE_TEXT = (
    "注意：①早班、夜班人员都填在一张表；②对应人员新入职或离职都要填"
    "写好；③班次时间可根据自个门店修改；④人员对应班次一定要填写；⑤"
    "考勤表中有啥特殊的事项就写在“备注”"
)

# 日期列之前的八个固定列（`layout` 里的 A~H），顺序就是表头顺序。
DAY_TEXT = "休"
HALF_REST_TEXT = "休半天"
TODO_TEXT = "待补"

FIXED_HEADERS = ("班次", "姓名", "职位", "底薪", "是否住宿", "本月出勤天数", "加班小时", "年限")
# 表末三列的标题与位置随天数平移；`存休天数` 里的换行是样表原文（`存休\n天数`）。
SUMMARY_HEADERS = ("存休\n天数", "是否新入职", "是否离职")

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

# 六份样表逐格实测出来的列宽（原值精确到 1/17 的整数倍，写成分数才不漂；见实测报告 §8.4）。
COLUMN_WIDTHS = {
    "A": 15.0,
    "B": 373 / 17,
    "C": 755 / 34,
    "D": 831 / 34,
    "E": 245 / 17,
    "F": 245 / 17,
    "G": 585 / 34,
    "H": 387 / 34,
}
DAY_COLUMN_WIDTH = 387 / 34
# 汇总列：存休天数 18.05（实测报告 §8.4）、两个标记列各 21.67 —— 那是样表里
# 「是否新入职」那一格的宽度，第三个标记列在样表里是没设宽度的默认列。
SUMMARY_COLUMN_WIDTHS = (306 / 17, 368 / 17, 368 / 17)

ROW_HEIGHT_TITLE = 73.0
ROW_HEIGHT_MONTH = 65.0
ROW_HEIGHT_SPACER = 45.0
ROW_HEIGHT_BODY = 65.0
# 底部说明块 ≈ 31.6（实测报告 §8.4）。
ROW_HEIGHT_FOOTER = 31.6
# 「班次时间：」那一行给它 R3 那一档：一行字要装下 24pt 的红字标题。
ROW_HEIGHT_SHIFT_TIME = 65.0

# 条件格式那两种「坏值」样式（样表 dxf 统一是深红字 + 浅红底）。
BAD_VALUE_FONT = Font(color="FF9C0006")
BAD_VALUE_FILL = PatternFill(patternType="solid", fgColor="FFFFC7CE")

# 表头/表体的字体体系（4~8 月那套模板：黑体 + 宋体大标题）。
TITLE_FONT = Font(name="宋体", size=36, bold=True)
MONTH_FONT = Font(name="宋体", size=28, bold=True)
HEADER_FONT = Font(name="黑体", size=28, bold=True)
HEADER_MARK_FONT = Font(name="黑体", size=28, bold=True, color="FFFF0000")
BODY_FONT = Font(name="黑体", size=20, bold=True)
NOTE_FONT = Font(name="宋体", size=24, color="FFFF0000")
SHIFT_TIME_FONT = Font(name="黑体", size=24, bold=True, color="FFFF0000")

WEEKEND_FILL = PatternFill(patternType="solid", fgColor="FFFFFF00")
TODO_FILL = PatternFill(patternType="solid", fgColor="FFFFFF00")
MARK_HEADER_FILL = PatternFill(patternType="solid", fgColor="FFFFFF00")

THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
WRAPPED_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

# `.xlsx` 的 MIME（响应头与浏览器都按它落盘）。
EXPORT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def attendance_export_filename(month: str) -> str:
    """下发文件名：`考勤表-YYYY-MM.xlsx`（票面验收 1）。``month`` 用响应上的那一位。

    不拼店名、不带「电子版」后缀 —— 样表那个名字是台账管理员起的，与表内写法是两套。
    """
    return f"考勤表-{month}.xlsx"


def build_attendance_workbook(sheet: dict) -> bytes:
    """把 `AttendanceStore.sheet()` 的响应画成工作簿字节。**纯函数**：不读库、不落盘。"""
    month = str(sheet["month"])
    year, month_no = (int(part) for part in month.split("-"))
    day_count = monthrange(year, month_no)[1]
    employees = list(sheet.get("employees") or [])

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = SHEET_TITLE

    _write_headers(worksheet, year, month_no, day_count)
    first_body_row = 6
    for offset, employee in enumerate(employees):
        _write_employee(worksheet, first_body_row + offset, employee, day_count)
    # 表体末行：一个人一行；一个员工都没有时「表体」就是第一行（条件格式铺在表头下方的
    # 那一行上，不铺到页脚去）。
    last_body_row = first_body_row + max(len(employees) - 1, 0)
    footer_row = _write_footer(worksheet, first_body_row + len(employees), day_count)
    _write_print_setup(worksheet, day_count, footer_row, first_body_row, last_body_row)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ── 表头（R1~R5） ────────────────────────────────────────────────────────────


def _write_headers(worksheet, year: int, month_no: int, day_count: int) -> None:
    """R1 大标题 / R2 月份行 / R3 空样式行 / R4+R5 两行表头。

    顶部三行的横向合并与样表一致：R1 盖到**日期列之前**（存休天数那一列是 R1 的右边界），
    R2 盖到**第一个标记列**，日期列本身不合并（一格一天）。
    """
    last_day_column = 8 + day_count
    summary_columns = (last_day_column + 1, last_day_column + 2, last_day_column + 3)

    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_day_column)
    title = worksheet.cell(row=1, column=1, value=TITLE_TEXT)
    title.font = TITLE_FONT
    title.alignment = CENTER
    worksheet.row_dimensions[1].height = ROW_HEIGHT_TITLE

    worksheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_day_column + 1)
    # **不写店名**：系统里没有这一项、只有一家店（票面验收 2）。
    month_cell = worksheet.cell(row=2, column=1, value=f"月份 ：{month_no}月份")
    month_cell.font = MONTH_FONT
    month_cell.alignment = CENTER
    worksheet.row_dimensions[2].height = ROW_HEIGHT_MONTH

    # R3：一整排**有样式、值为空**的过渡行（样表里 40 格都带 28pt 宋体）。
    worksheet.row_dimensions[3].height = ROW_HEIGHT_SPACER
    for column in range(1, summary_columns[2] + 1):
        worksheet.cell(row=3, column=column).font = MONTH_FONT

    # R4/R5：八个固定列 + 日期列 + 三个汇总列；A~H 与汇总列都是 R4:R5 纵向合并。
    for column in range(1, 9):
        worksheet.merge_cells(start_row=4, start_column=column, end_row=5, end_column=column)
    for column in summary_columns:
        worksheet.merge_cells(start_row=4, start_column=column, end_row=5, end_column=column)

    for index, text in enumerate(FIXED_HEADERS, start=1):
        _header_cell(worksheet, 4, index, text)
    for index, text in enumerate(SUMMARY_HEADERS):
        column = summary_columns[index]
        cell = _header_cell(worksheet, 4, column, text, mark=index > 0)
        cell.alignment = WRAPPED_CENTER
    for column in range(9, last_day_column + 1):
        _header_cell(worksheet, 4, column, column - 8)
    worksheet.row_dimensions[4].height = ROW_HEIGHT_BODY
    worksheet.row_dimensions[5].height = ROW_HEIGHT_BODY

    # R5 是星期汉字；周六/周日两列**纯黄底**（样表口径：日期数字那一格不着色）。
    for column in range(9, last_day_column + 1):
        day = date(year, month_no, column - 8)
        cell = _header_cell(worksheet, 5, column, WEEKDAYS[day.weekday()])
        if day.weekday() >= 5:
            cell.fill = WEEKEND_FILL

    _write_column_widths(worksheet, day_count)


def _header_cell(worksheet, row: int, column: int, value: Any, *, mark: bool = False):
    """表头一格：黑体 28 粗体居中四边细线；``mark`` 的那两格是黄底红字（样表口径）。"""
    cell = worksheet.cell(row=row, column=column, value=value)
    cell.font = HEADER_MARK_FONT if mark else HEADER_FONT
    cell.alignment = CENTER
    cell.border = BORDER
    if mark:
        cell.fill = MARK_HEADER_FILL
    return cell


def _write_column_widths(worksheet, day_count: int) -> None:
    """列宽：A~H 与日期列逐项照实测值；三个汇总列各 18.05 / 21.67 / 21.67。"""
    for letter, width in COLUMN_WIDTHS.items():
        worksheet.column_dimensions[letter].width = width
    for column in range(9, 9 + day_count):
        worksheet.column_dimensions[get_column_letter(column)].width = DAY_COLUMN_WIDTH
    for offset, width in enumerate(SUMMARY_COLUMN_WIDTHS):
        letter = get_column_letter(9 + day_count + offset)
        worksheet.column_dimensions[letter].width = width


# ── 员工行（R6 起） ─────────────────────────────────────────────────────────


def _write_employee(worksheet, row: int, employee: dict, day_count: int) -> None:
    """一个人的一行：A~H + 当月每一天 + 表末三列。"""
    worksheet.row_dimensions[row].height = ROW_HEIGHT_BODY
    leave = employee.get("leave") or {}
    values = [
        # A 班次：台账上「本月班次」那一项（票 05）—— 填过的是他填的，没填过的是服务端
        # 给的默认（当月众数）；当月一天排班都没有的人是空串/`None`，**留空**，不编一个名字。
        employee.get("shift_name"),
        employee.get("name") or "",
        employee.get("job_title") or "",
        employee.get("base_salary"),
        # E 是否住宿：票 05 手填的那一项，**照它的字面值写**（没填过 = 空格子）。
        # 它与表末的「是否离职」一样是手填列，只是位置在表头那一段里。
        employee.get("lodging"),
        leave.get("month_attendance_days"),
        employee.get("net_hours"),
        employee.get("seniority_bonus"),
    ]
    for index, value in enumerate(values, start=1):
        _body_cell(worksheet, row, index, value)

    for offset, day in enumerate(employee.get("days") or []):
        if offset >= day_count:
            break
        _body_cell(worksheet, row, 9 + offset, _day_text(day), todo=_is_todo(day))

    last_day_column = 8 + day_count
    summary = (
        leave.get("balance"),
        # 「是否新入职」**只写「是」**（2026-10-29 用户裁定）：不是新入职的留空。
        # 这一列原来写「否」，一列「否」没有信息量，还把表体撑得比别处脏。
        _new_hire_text(employee.get("new_hire")),
        # 「是否离职」同口径（2026-10-29）：改由花名册的离职状态派生，只写「是」。
        # 原来读的是考勤台账上**手填**的那一列，已退役（卡片上的手填项少了一项）。
        _resigned_text(employee),
    )
    for offset, value in enumerate(summary):
        _body_cell(worksheet, row, last_day_column + 1 + offset, value)


def _body_cell(worksheet, row: int, column: int, value: Any, *, todo: bool = False):
    """表体一格：黑体 20 粗体居中四边细线；`todo`（待补）那几格黄底标出。

    ``None`` / 空串一律**不写值**（留空格子）—— 样表里「没有这一项」与「写了个 0」是
    两件事（底薪空、加班小时空都是留空）。
    """
    cell = worksheet.cell(row=row, column=column)
    if value is not None and value != "":
        cell.value = value
    cell.font = BODY_FONT
    cell.alignment = CENTER
    cell.border = BORDER
    if todo:
        cell.fill = TODO_FILL
    return cell


def _day_text(day: dict) -> Optional[str]:
    """那一格写什么：出勤**留空**、休 / 休半天 / 待补写字；今天之后留空。

    半天休（票 07）读的是格子上的 `half_rest`，**不在这里再判一次**「他那天上不上班」——
    那件事是 `AttendanceStore` 的判断（`state == 'work'` 才可能标半天休）。
    """
    state = day.get("state")
    if state == "work":
        return HALF_REST_TEXT if day.get("half_rest") else None
    if state == "rest":
        return DAY_TEXT
    if state == "todo":
        return TODO_TEXT
    return None


def _is_todo(day: dict) -> bool:
    return day.get("state") == "todo"


def _resigned_text(employee: dict) -> Optional[str]:
    """「是否离职」那一格（2026-10-29）：**只写「是」**，其余留空。

    判据按**这个月**看，不按今天：离职日期 ≤ 该月最后一天就算（"干到 10/31" 的人在
    10 月表上写「是」，11 月才轮到他）；空串（已离职、日期待补）也写「是」。
    """
    value = employee.get("resigned_at")
    if value is None:
        return None
    text = str(value)[:10]
    if not text:
        return "是"
    days = employee.get("days") or []
    month_last = str(days[-1].get("business_date") or "")[:10] if days else ""
    return "是" if (not month_last or text <= month_last) else None


def _new_hire_text(value: Any) -> Optional[str]:
    """「是否新入职」那一格：**是**才写「是」，其余（不是新来的 / 算不出来）留空。

    2026-10-29 用户裁定。原来不是新来的写「否」（`_yes_no`），可那一列九成格子都是
    「否」—— 一列「否」既没有信息量，又把表体撑得比别处脏。`None`（入职日期为空、
    这一项算不出来）本来就是留空，两种「不是」在纸面上因此长得一样，这也对：店长要
    看的只有「这个月谁是新来的」。
    """
    return "是" if value is True else None


# ── 底部三块 ────────────────────────────────────────────────────────────────


def _write_footer(worksheet, shift_row: int, day_count: int) -> int:
    """底部三块（班次时间行 / 注意块 / 尾部空块），整行合并；返回打印区域的末行。"""
    last_column = 8 + day_count + 3

    worksheet.merge_cells(start_row=shift_row, start_column=1, end_row=shift_row, end_column=last_column)
    cell = worksheet.cell(row=shift_row, column=1, value=SHIFT_TIME_TEXT)
    cell.font = SHIFT_TIME_FONT
    cell.alignment = CENTER
    cell.border = BORDER
    worksheet.row_dimensions[shift_row].height = ROW_HEIGHT_SHIFT_TIME

    # 注意块：8 行高（样表里是 `A32:AP40` 那一档），原文一字不差。
    note_row = shift_row + 1
    note_last_row = note_row + 7  # 8 行高（样表 A32:AP40 那一档）
    worksheet.merge_cells(start_row=note_row, start_column=1, end_row=note_last_row, end_column=last_column)
    note = worksheet.cell(row=note_row, column=1, value=NOTE_TEXT)
    note.font = NOTE_FONT
    note.alignment = LEFT
    note.border = BORDER
    for row in range(note_row, note_last_row + 1):
        worksheet.row_dimensions[row].height = ROW_HEIGHT_FOOTER

    # 尾部一块空的合并块（样表里 8 行，只有样式没有内容）。
    tail_row = note_last_row + 1
    tail_last_row = tail_row + 7  # 尾部空块也是 8 行（样表 A46:AP53）
    worksheet.merge_cells(start_row=tail_row, start_column=1, end_row=tail_last_row, end_column=last_column)
    tail = worksheet.cell(row=tail_row, column=1)
    tail.border = BORDER
    for row in range(tail_row, tail_last_row + 1):
        worksheet.row_dimensions[row].height = ROW_HEIGHT_FOOTER
    return tail_last_row


# ── 打印与条件格式 ──────────────────────────────────────────────────────────


def _write_print_setup(
    worksheet, day_count: int, last_row: int, first_body_row: int, last_body_row: int
) -> None:
    """横向 A4 / fitToPage / 页边距 0 / 缩放 38 / 定义 `_xlnm.Print_Area`（同样表）。"""
    last_column = 8 + day_count + 3
    last_letter = get_column_letter(last_column)

    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.paperSize = 9  # A4
    worksheet.page_setup.scale = 24
    worksheet.page_setup.fitToHeight = 0
    worksheet.page_setup.fitToWidth = 1
    worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    worksheet.page_margins.left = 0
    worksheet.page_margins.right = 0
    worksheet.page_margins.top = 0
    worksheet.page_margins.bottom = 0
    worksheet.page_margins.header = 0
    worksheet.page_margins.footer = 0

    worksheet.sheet_view.zoomScale = 38
    worksheet.print_area = f"A1:{last_letter}{last_row}"

    _write_conditional_formatting(worksheet, day_count, first_body_row, last_body_row)


def _write_conditional_formatting(
    worksheet, day_count: int, first_body_row: int, last_body_row: int
) -> None:
    """样表里那两类条件格式（dxf 统一深红字 + 浅红底）。

    * `本月出勤天数`（F 列，第 6 行起）是 `cellIs between 0 and 31`；
    * 表末两个标记列是 `containsText "是"`，公式形如
      `NOT(ISERROR(SEARCH("是",<那一格>)))` —— 逐列写自己的列号，与样表同一形状。

    **不作用在日期格上**：历史表里日格的颜色都是手工填的底色，这条不抄。
    """
    worksheet.conditional_formatting.add(
        f"F{first_body_row}:F{last_body_row}",
        CellIsRule(operator="between", formula=["0", "31"], font=BAD_VALUE_FONT, fill=BAD_VALUE_FILL),
    )
    bad = DifferentialStyle(font=BAD_VALUE_FONT, fill=BAD_VALUE_FILL)
    last_day_column = 8 + day_count
    for column in (last_day_column + 2, last_day_column + 3):
        letter = get_column_letter(column)
        worksheet.conditional_formatting.add(
            f"{letter}{first_body_row}:{letter}{last_body_row}",
            Rule(
                type="containsText",
                operator="containsText",
                text="是",
                formula=[f'NOT(ISERROR(SEARCH("是",{letter}{first_body_row})))'],
                dxf=bad,
            ),
        )
