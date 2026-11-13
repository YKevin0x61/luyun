"""月度员工餐菜单 xlsx → 每日菜单。

全项目唯一的「读 Excel」点：管理员上传当月/下月菜单模板，这里把它拆成一天一条的
菜单数据，后续 service 负责落库与建单。本模块**不碰数据库**（不 import store），
可以脱离应用单独测试。

模板结构（2026-09 / 10 / 11 三个月真实文件实测一致）：

    ┌ 第 1 行：标题「2026年09月员工餐饮菜单」
    ├ 每个「周块」9 行：
    │   「时间」行    A 列写「时间」，右侧各列是月内日号
    │                   （可能是数字 25，也可能是文本「25（中秋节）」）
    │   「星期」行    日号下方是 一/二/…/日
    │   「午餐」行    B 列标记 主菜 / 副菜 / 副菜，C 列起是菜名
    │   「饮品/水果」行
    │   「晚餐」行    同午餐
    └ 周块天数随月首/月末截断（1 / 3 / 4 / 6 / 7 天都出现过），**不要按 7 天硬编码**；
      而且周块之间的日期列会左右错位（第一块从第 4 列起，后面从第 3 列起），
      所以只能逐块用「时间」行自己找日期列。

单元格取值口径：
- `/`（或全角 `／`）= 当天没有 → ``None``；
- 「自主发挥」= 厨师自由发挥、当天菜单留白 → 保留成菜名，同时 ``freeform=True``；
- 空单元格 = 未填 → ``None``；
- 同名菜可以带做法，例如「鹅肉（卤/炒/焖）」，原样保留。
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Iterable

import openpyxl

# 日号：取单元格文本的前导数字。「25（中秋节）」→ 25。
_DAY_RE = re.compile(r"^(\d{1,2})")
# 标题里的年月：「2026年09月员工餐饮菜单」。
_TITLE_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")

_CN_WEEKDAY = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
NONE_MARKERS = frozenset({"/", "／"})
FREEFORM_MARKER = "自主发挥"


class MenuImportError(Exception):
    """菜单模板读不懂。消息是给管理员看的中文，带工作表名和行号，绝不裸抛解析异常。"""


@dataclass(frozen=True)
class MenuDay:
    """一天菜单（与 ``staff_meal_menu_days`` 的写列一一对应）。"""

    menu_date: str  # YYYY-MM-DD，使用日（自然日）
    weekday: int | None  # 1=周一 … 7=周日
    lunch_main: str | None = None
    lunch_side1: str | None = None
    lunch_side2: str | None = None
    dinner_main: str | None = None
    dinner_side1: str | None = None
    dinner_side2: str | None = None
    drink: str | None = None
    freeform: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "menu_date": self.menu_date,
            "weekday": self.weekday,
            "lunch_main": self.lunch_main,
            "lunch_side1": self.lunch_side1,
            "lunch_side2": self.lunch_side2,
            "dinner_main": self.dinner_main,
            "dinner_side1": self.dinner_side1,
            "dinner_side2": self.dinner_side2,
            "drink": self.drink,
            "freeform": self.freeform,
        }


def parse_menu_workbook(data: bytes, year: int, month: int) -> list[MenuDay]:
    """把一个月度菜单 xlsx 的字节拆成按日期排好序的 ``MenuDay`` 列表。

    ``year`` / ``month`` 是这次导入的目标月份（管理员在页面上选的），会与标题里的
    年月核对——传错文件要在导入时当场报错，而不是把 9 月的菜单写进 10 月。
    """
    if not isinstance(data, (bytes, bytearray)) or not bytes(data):
        raise MenuImportError("上传内容为空，请重新选择 .xlsx 文件。")
    if not 1 <= month <= 12:
        raise MenuImportError(f"导入月份不合法：{month}。")

    sheet_name, rows = _read_rows(bytes(data))
    _check_title_month(rows, sheet_name, year, month)
    days = _parse_rows(rows, sheet_name, year, month)
    if not days:
        raise MenuImportError(
            f"工作表「{sheet_name}」里没找到「时间」行，可能不是员工餐菜单模板。"
        )
    return [days[key] for key in sorted(days)]


# ── 读文件 ──────────────────────────────────────────────────────


def _read_rows(data: bytes) -> tuple[str, list[tuple[Any, ...]]]:
    try:
        workbook = openpyxl.load_workbook(
            BytesIO(data), read_only=True, data_only=True
        )
    except Exception as exc:  # zipfile.BadZipFile / 坏 XML / 不支持的扩展名…
        raise MenuImportError(
            "不是有效的 .xlsx 文件，请从 Excel 另存为 .xlsx 后重试。"
        ) from exc

    worksheets = list(workbook.worksheets)
    if not worksheets:
        raise MenuImportError("这个 .xlsx 里没有工作表。")
    sheet = worksheets[0]
    rows = [tuple(row) for row in sheet.iter_rows(values_only=True)]
    return sheet.title, rows


def _at(rows: list[tuple[Any, ...]], row_index: int, col_index: int) -> Any:
    if not 0 <= row_index < len(rows):
        return None
    row = rows[row_index]
    if not 0 <= col_index < len(row):
        return None
    return row[col_index]


def _text(value: Any) -> str | None:
    """去首尾空白，空串当没填；数字等非文本转成字符串（菜名/饮品不会是数字）。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


def _day_of(value: Any) -> int | None:
    """日号：数字、datetime、文本（取前导数字）三种都要认。"""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.day
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        day = int(value)
        return day if 1 <= day <= 31 else None
    match = _DAY_RE.match(str(value).strip())
    if not match:
        return None
    day = int(match.group(1))
    return day if 1 <= day <= 31 else None


def _check_title_month(
    rows: list[tuple[Any, ...]], sheet_name: str, year: int, month: int
) -> None:
    title = _text(_at(rows, 0, 0))
    if not title:
        return
    match = _TITLE_MONTH_RE.search(title)
    if not match:
        return  # 模板标题没写年月：放过，日期会被「时间」行兜住
    title_year, title_month = int(match.group(1)), int(match.group(2))
    if (title_year, title_month) != (year, month):
        raise MenuImportError(
            f"工作表「{sheet_name}」标题写的是 {title_year}年{title_month:02d}月，"
            f"与要导入的 {year}年{month:02d}月 不一致，请确认选对了文件。"
        )


# ── 解析 ────────────────────────────────────────────────────────


def _parse_rows(
    rows: list[tuple[Any, ...]], sheet_name: str, year: int, month: int
) -> dict[str, MenuDay]:
    max_day = monthrange(year, month)[1]
    days: dict[str, MenuDay] = {}
    row = 0
    block_no = 0

    while row < len(rows):
        if _text(_at(rows, row, 0)) != "时间":
            row += 1
            continue

        block_no += 1
        date_cols = _date_columns(rows, row, sheet_name, block_no, year, month, max_day)
        weekday_row = row + 1
        if _text(_at(rows, weekday_row, 0)) != "星期":
            raise MenuImportError(
                f"第 {row + 1} 行「时间」下面缺少「星期」行"
                f"（工作表「{sheet_name}」第 {block_no} 个周块）。"
            )
        weekdays = {
            day: _CN_WEEKDAY.get(_text(_at(rows, weekday_row, col)) or "")
            for col, day in date_cols.items()
        }

        buckets = {
            day: {"lunch": [], "dinner": [], "drink": None}
            for day in date_cols.values()
        }
        meal: str | None = None
        labels: set[str] = set()
        rr = row + 2
        while rr < len(rows):
            label_a = _text(_at(rows, rr, 0))
            if label_a == "时间":
                break  # 下一块的起点
            if label_a in ("午餐", "晚餐"):
                meal = label_a
                labels.add(label_a)
            elif label_a == "饮品/水果":
                meal = None
                labels.add("drink")
            label_b = _text(_at(rows, rr, 1))
            for col, day in date_cols.items():
                value = _text(_at(rows, rr, col))
                if value is None or value in NONE_MARKERS:
                    continue
                bucket = buckets[day]
                if label_a == "饮品/水果":
                    bucket["drink"] = value
                elif meal in ("午餐", "晚餐") and label_b in ("主菜", "副菜"):
                    bucket["lunch" if meal == "午餐" else "dinner"].append(value)
            rr += 1

        if "午餐" not in labels or "晚餐" not in labels:
            raise MenuImportError(
                f"第 {row + 1} 行开始的周块缺少「午餐」或「晚餐」行"
                f"（工作表「{sheet_name}」）。"
            )

        for col, day in date_cols.items():
            menu_date = f"{year:04d}-{month:02d}-{day:02d}"
            if menu_date in days:
                raise MenuImportError(
                    f"日期 {menu_date} 出现了两次（工作表「{sheet_name}」第 {block_no} 个周块）。"
                )
            days[menu_date] = _to_menu_day(
                menu_date=menu_date,
                weekday=weekdays[day],
                lunch=buckets[day]["lunch"],
                dinner=buckets[day]["dinner"],
                drink=buckets[day]["drink"],
                sheet_name=sheet_name,
            )

        # rr 停在下一个「时间」行（或越界），它就是下一块的起点
        row = rr

    return days


def _date_columns(
    rows: list[tuple[Any, ...]],
    row: int,
    sheet_name: str,
    block_no: int,
    year: int,
    month: int,
    max_day: int,
) -> dict[int, int]:
    """「时间」行里 列号 → 日号。日期列位置每块都可能不同，必须逐块扫。"""
    row_values = rows[row]
    date_cols: dict[int, int] = {}
    for col in range(1, len(row_values)):
        raw = _at(rows, row, col)
        if _text(raw) is None:
            continue
        day = _day_of(raw)
        if day is None:
            raise MenuImportError(
                f"第 {row + 1} 行「时间」行的日期格「{_text(raw)}」解析不出日号"
                f"（工作表「{sheet_name}」）。"
            )
        if day in date_cols.values():
            raise MenuImportError(
                f"第 {row + 1} 行「时间」行里 {day} 日出现了两次（工作表「{sheet_name}」）。"
            )
        if day > max_day:
            raise MenuImportError(
                f"{year}年{month:02d}月没有 {day} 日（工作表「{sheet_name}」）。"
            )
        date_cols[col] = day
    if not date_cols:
        raise MenuImportError(
            f"第 {row + 1} 行「时间」行没有解析出任何日期"
            f"（工作表「{sheet_name}」第 {block_no} 个周块）。"
        )
    return date_cols


def _to_menu_day(
    *,
    menu_date: str,
    weekday: int | None,
    lunch: Iterable[str],
    dinner: Iterable[str],
    drink: str | None,
    sheet_name: str,
) -> MenuDay:
    lunch_list = list(lunch)
    dinner_list = list(dinner)
    if len(lunch_list) > 3 or len(dinner_list) > 3:
        raise MenuImportError(
            f"{menu_date} 的午餐/晚餐超过 3 道菜（工作表「{sheet_name}」），模板结构不对。"
        )
    freeform = any(item == FREEFORM_MARKER for item in lunch_list + dinner_list)

    def pick(items: list[str], index: int) -> str | None:
        return items[index] if index < len(items) else None

    return MenuDay(
        menu_date=menu_date,
        weekday=weekday,
        lunch_main=pick(lunch_list, 0),
        lunch_side1=pick(lunch_list, 1),
        lunch_side2=pick(lunch_list, 2),
        dinner_main=pick(dinner_list, 0),
        dinner_side1=pick(dinner_list, 1),
        dinner_side2=pick(dinner_list, 2),
        drink=drink,
        freeform=freeform,
    )
