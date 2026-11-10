#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常损耗登记台账（从 worktool2 那个小程序搬过来，见 `migrations/pg/0029_wastage.sql`）。

一条记录 = **一样物料 + 一个数量与单位 + 一个原因 + 一个自然日 +（可选）0-3 张现场照片**。
登记人与记录一起落库（`operator_kind` / `employee_id` / `operator_name`）：员工只看得到
自己登记的，管理端看全店 —— 这一条是**服务层**的判据（`WastageActor.is_admin`），
不是接口路径的差别，两个身份走的是同一批方法。

照片不走库里的大字段：字节由 `FileCaptureStore` 落盘（与卫生现场照片同一套设施），
库里只存 `capture_id` 与元数据。上传与登记是两步，所以图可以先传、提交时再绑。

登记后 **24 小时内本人可更正**（改字段或整笔撤回，见 `amend_record` / `revoke_record`；
表与修订表见 `migrations/pg/0046_wastage_amendments.sql`）：窗口从 `created_at`（登记
时刻）算，每次更正往 `wastage_record_revisions` 写一条前后快照。
撤回是软删（`revoked_at`），所有读路径都把它当成不存在；照片不参与更正。

**统计口径照源项目**（`cloudfunctions/manageWastage` 的 `statistics`）：按**自然日**的
范围取记录，算四样 —— 总笔数、物料种类数、按原因分布、按物料排行、逐日趋势。
没有「总金额」：损耗记的是数量，单位还各不相同（kg 与 份 加不到一起）。
"""

from __future__ import annotations

import functools
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional

import asyncpg

from db_core.errors import is_integrity_violation
from db_core.utils import CHINA_TZ
from services.identity import serialized_write
from services.prep_review.retention import PRIMARY_CLOCK

logger = logging.getLogger(__name__)

__all__ = [
    "AMEND_WINDOW_HOURS",
    "KIND_ADMIN",
    "KIND_STAFF",
    "MAX_IMAGES",
    "MAX_ITEM_NAME",
    "MAX_QUANTITY",
    "MAX_REMARK",
    "MAX_TEMPLATES",
    "MAX_UNIT",
    "REASONS",
    "REASON_ORDER",
    "UNITS",
    "WastageActor",
    "WastageError",
    "WastageStore",
    "reason_label",
]

# 登记人那两档。`admin` 是管理端那台共享账号（超管）——它没有员工号，
# 所以「这笔算谁的」对它是空（本店损耗不属于某一个人，这一点与加班不同）。
KIND_STAFF = "staff"
KIND_ADMIN = "admin"

# 六个原因：**键是数据面契约**（库里的 CHECK 与统计都认这六个），中文标签只在读的时候
# 派生（`reason_label`）—— 存两份就一定会漂。
REASONS: dict[str, str] = {
    "spoiled": "变质腐败",
    "expired": "过期丢弃",
    "damaged": "破损/污染",
    "production_fail": "制作失败",
    "excess": "备货过量",
    "other": "其他",
}
REASON_ORDER = tuple(REASONS)

# 常用单位（源项目的 `UNIT_OPTIONS`，一字未改）。选单里的候选，不是白名单：
# 单位可以是别的（「罐」「箱」），登记页允许手输。
UNITS = ("份", "kg", "g", "个", "盒", "袋", "瓶", "包", "斤")

MAX_ITEM_NAME = 50
MAX_UNIT = 10
# 备注限长（同源项目的 `maxlength=200`）。超了报错而不是悄悄截断。
MAX_REMARK = 200
# 现场照片最多 3 张（源项目同）。
MAX_IMAGES = 3
# 每人最多 20 个自定义模板（源项目同）。
MAX_TEMPLATES = 20
# 数量上限：防手滑多打几个 9，不替业务定政策。
MAX_QUANTITY = Decimal("99999")
# 更正窗口（小时）：从登记时刻 `created_at` 算的 24 小时，见 `amend_record` / `revoke_record`。
AMEND_WINDOW_HOURS = 24

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_RECORD_COLUMNS = (
    "id, item_name, quantity, unit, reason, remark, wastage_date,"
    " operator_kind, employee_id, operator_name, created_at, updated_at,"
    " revoked_at, revoked_by_kind, revoked_by_employee_id,"
    " prep_item_id, window_start, window_end"
)


def reason_label(reason: Any) -> str:
    """原因键 → 中文标签。认不出的键原样返回（老数据里可能有不认识的键）。"""
    key = str(reason or "")
    return REASONS.get(key, key)


def _needs_migration(method):
    """表还没建（0029 没应用）时，把 asyncpg 的缺表异常换成一句能照做的错。

    照排班 / 加班台账的同一个装饰器：真正用到损耗登记的那一刻不该是一个 500
    traceback，店长要看到的是「去 Admin『系统更新 → 数据库迁移』应用 0029」。
    顺带把失败的事务回滚掉，否则这条连接后面的查询会全跟着报
    `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise WastageError("not_migrated", str(exc)) from exc

    return wrapper


@dataclass(frozen=True)
class WastageActor:
    """谁在登记 —— 决定这条记录记在谁名下，以及他看得到多少。

    `kind` 是 `staff` / `admin`（见模块头常量）。员工的 `employee_id` 一定有值；
    管理端那台共享账号没有员工号，所以 `employee_id` 为空、`owner_key` 是 `super`。

    `sees_all`：**看全店**（管理端，或开了「损耗分析」能力的员工）。它只由 API 层按
    `admin_caps` 给，服务层不自己去读权限 —— 归属判据因此仍然只有 `_scope_clause` 一处。
    记录落在谁名下与谁能看是两件事：有能力看全店的员工，自己记的那笔照样记在自己名下
    （`operator_kind` / `employee_id` 不变），只是查询不再按归属过滤。
    """

    kind: str
    employee_id: Optional[int] = None
    name: str = ""
    sees_all: bool = False

    @property
    def is_admin(self) -> bool:
        return self.kind == KIND_ADMIN

    @property
    def can_see_all(self) -> bool:
        """查全店（管理端，或开了「损耗分析」能力的员工）。"""
        return self.is_admin or bool(self.sees_all)

    @property
    def owner_key(self) -> str:
        """模板与照片的归属键：`staff:<员工号>` 或 `super`（同加班的 `created_by` 形状）。"""
        if self.employee_id is None:
            return "super"
        return f"staff:{int(self.employee_id)}"

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        return "管理端" if self.is_admin else f"员工{self.employee_id}"


class WastageError(ValueError):
    """台账层的输入错误。``code`` 是给 API 层查文案用的稳定标识，不是给店员看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _require_date(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        raise WastageError("invalid_date", "invalid_date")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        # 形状对、日历上没有这一天（2026-02-30）。
        raise WastageError("invalid_date", "invalid_date")


def _clean_item_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise WastageError("missing_item_name", "missing_item_name")
    if len(text) > MAX_ITEM_NAME:
        raise WastageError("item_name_too_long", str(MAX_ITEM_NAME))
    return text


def _clean_unit(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise WastageError("missing_unit", "missing_unit")
    if len(text) > MAX_UNIT:
        raise WastageError("unit_too_long", str(MAX_UNIT))
    return text


def _require_reason(value: Any) -> str:
    key = str(value or "").strip()
    if key not in REASONS:
        raise WastageError("invalid_reason", "invalid_reason")
    return key


def _require_quantity(value: Any) -> Decimal:
    """数量：大于 0、最多三位小数、不超过上限。

    走 `Decimal` 而不是 float：数量与金额同属「不许有浮点误差」的那一类，而界面上
    0.5 / 1.25 这样的输入很常见。量化到三位（与库里的 NUMERIC(12,3) 同一刻度）——
    多出来的位数直接报错，不四舍五入：店员输入的东西不该被系统改掉。
    """
    if isinstance(value, bool) or value is None or value == "":
        raise WastageError("invalid_quantity", "invalid_quantity")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise WastageError("invalid_quantity", "invalid_quantity")
    if not number.is_finite():
        raise WastageError("invalid_quantity", "invalid_quantity")
    if number != number.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP):
        raise WastageError("quantity_too_precise", "quantity_too_precise")
    if number <= 0:
        raise WastageError("quantity_not_positive", "quantity_not_positive")
    if number > MAX_QUANTITY:
        raise WastageError("quantity_too_large", str(MAX_QUANTITY))
    return number


def _clean_remark(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_REMARK:
        raise WastageError("remark_too_long", str(MAX_REMARK))
    return text


def _clean_prep_item_id(value: Any) -> Optional[int]:
    """可选的备货品主键：空就是空，给了就必须是一个正整数。

    是否**存在**由调用方查库确认（这里只有纯函数，不碰连接）；不存在的 id 挂上去，
    复盘按 id 对齐时永远匹配不到，等于把这笔报废悄悄丢了。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise WastageError("invalid_prep_item", "invalid_prep_item")
    try:
        item_id = int(str(value).strip())
    except (TypeError, ValueError):
        raise WastageError("invalid_prep_item", "invalid_prep_item")
    if item_id <= 0:
        raise WastageError("invalid_prep_item", "invalid_prep_item")
    return item_id


def _window_bounds(day: str) -> tuple[str, str]:
    """自然日 ``YYYY-MM-DD`` → 当天的 07:00 窗口（`[day T07:00, day+1 T07:00)`）。

    钟点复用复盘主口径（`services.prep_review.retention.PRIMARY_CLOCK`）：损耗落在哪个
    品-天，与复盘读的是同一个 07:00 窗口，两处各写一个 "07:00" 就会漂。
    """
    hour, minute = (int(part) for part in PRIMARY_CLOCK.split(":"))
    parsed = date.fromisoformat(day)
    start = datetime(parsed.year, parsed.month, parsed.day, hour, minute, tzinfo=CHINA_TZ)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _clean_keyword(value: Any) -> str:
    """关键词：去空白、限长。它进的是 LIKE 的参数位，不做任何拼接。"""
    text = str(value or "").strip()
    return text[:50]


def _month_range(month: str) -> tuple[str, str]:
    """``YYYY-MM`` → 该月首末日（自然月，同源项目的统计范围算法）。"""
    text = str(month or "").strip()
    if not _MONTH_RE.match(text):
        raise WastageError("invalid_month", "invalid_month")
    try:
        first = date(int(text[:4]), int(text[5:7]), 1)
    except ValueError:
        raise WastageError("invalid_month", "invalid_month")
    if first.month == 12:
        following = date(first.year + 1, 1, 1)
    else:
        following = date(first.year, first.month + 1, 1)
    return first.isoformat(), (following - timedelta(days=1)).isoformat()


def _parse_stamp(value: Any) -> Optional[datetime]:
    """库里的时刻列（ISO 字符串、带北京时区）→ datetime；解析不了返回 ``None``。

    更正窗口的唯一判据，所以解析失败必须 **fail-closed**：调用方拿到 ``None`` 就按
    「不可更正」处理，不能把它悄悄当成「刚登记的」放行。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        # 老行都经由本应用写入，只会是北京时；naive 的补上时区再比，免得 aware/naive 相减报错。
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed


def _amend_deadline(created_at: Any) -> Optional[datetime]:
    """登记时刻 + 24h。窗口从 ``created_at`` 算 —— 改的是「那一刻记错了」，与损耗发生日无关。"""
    created = _parse_stamp(created_at)
    if created is None:
        return None
    return created + timedelta(hours=AMEND_WINDOW_HOURS)


# revision 快照的字段：整条记录（含撤回痕迹），**不含 images** —— 照片不可更正，
# 快照里带 url / 字节数只会让审计文本变长，还会让人误以为照片被换过。
_SNAPSHOT_FIELDS = (
    "id",
    "item_name",
    "quantity",
    "unit",
    "reason",
    "remark",
    "wastage_date",
    "operator_kind",
    "employee_id",
    "operator_name",
    "created_at",
    "updated_at",
    "revoked_at",
    "revoked_by_kind",
    "revoked_by_employee_id",
)


def _snapshot(record: dict) -> dict:
    return {key: record.get(key) for key in _SNAPSHOT_FIELDS}


def _record_row(row: Any) -> dict:
    """一行 → 接口要的形状：Decimal 折成 float、原因补中文标签、图片先给空数组。

    ``amend_deadline_at`` 在这里派生（只依赖 created_at）：明细与详情都带它，
    前端不用自己拿本机时钟去猜窗口。``can_amend`` 要看「谁在看」，由带 actor 的
    调用点补（见 ``record_by_id`` / ``list_records``）。
    """
    record = dict(row)
    quantity = record.get("quantity")
    record["quantity"] = float(quantity) if quantity is not None else 0.0
    record["reason_text"] = reason_label(record.get("reason"))
    deadline = _amend_deadline(record.get("created_at"))
    record["amend_deadline_at"] = deadline.isoformat() if deadline else None
    record["images"] = []
    return record


class WastageStore:
    """损耗台账的读写口。构造要一个已打开的库连接（同 `OvertimeLedger` 的口径）。"""

    def __init__(
        self,
        conn_or_db,
        *,
        captures=None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("WastageStore requires an open database connection")
        self._conn = conn
        # 照片的字节落在磁盘上（`FileCaptureStore`）。不传它就只做台账那一半 ——
        # 单测与不碰照片的调用点不必为此装一个目录。
        self._captures = captures
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            import asyncio

            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def today(self) -> str:
        """今天 —— **自然日**（北京时零点切），不是 06:00 的营业日。

        损耗记的是「哪天报废的」，日历日才是店员嘴里的那天（同 `OvertimeLedger.today`）。
        """
        return self._now_dt().date().isoformat()

    def tomorrow(self) -> str:
        """明天 —— 登记日期选择器的上界（源项目也是「不许选未来」）。"""
        return (self._now_dt().date() + timedelta(days=1)).isoformat()

    def _owns_record(self, record: dict, actor: WastageActor) -> bool:
        """这笔登记是不是他记的 —— 更正与撤回的归属判据只有这一处。

        员工比 ``employee_id``；管理端那台共享账号比 ``operator_kind``（它没有员工号，
        所以「管理端记的」整档都算它记的）。看全店的能力（``sees_all``）**不放宽**这一条：
        看得到不等于改得了。
        """
        if actor.is_admin:
            return record.get("operator_kind") == KIND_ADMIN
        if actor.employee_id is None:
            return False
        employee_id = record.get("employee_id")
        return employee_id is not None and int(employee_id) == int(actor.employee_id)

    def _can_amend(self, record: dict, actor: WastageActor) -> bool:
        """此刻能不能更正：本人 + 未撤回 + 窗口内。created_at 解析不出的按不可更正。"""
        if record.get("revoked_at"):
            return False
        if not self._owns_record(record, actor):
            return False
        deadline = _amend_deadline(record.get("created_at"))
        if deadline is None:
            return False
        return self._now_dt() <= deadline

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _rows(self, sql: str, params: tuple) -> list[dict]:
        cur = await self._conn.execute(sql, params)
        return list(await cur.fetchall())

    async def _count(self, clause: str, params: tuple) -> int:
        rows = await self._rows(
            f"SELECT COUNT(*) AS total FROM wastage_records{clause}", params
        )
        return int(rows[0]["total"]) if rows else 0

    async def _edit_counts(self, record_ids: list[int]) -> dict[int, int]:
        """一批记录各被改过几次：一条 IN (...) 分组查完，不给每条打一次库。

        列表一页最多 100 条，逐条查就是 100 次往返；没有修订的记录不出现在结果里，
        调用方按「查不到 = 0」处理。
        """
        if not record_ids:
            return {}
        placeholders = ", ".join("?" for _ in record_ids)
        rows = await self._rows(
            "SELECT record_id, COUNT(*) AS total FROM wastage_record_revisions"
            f' WHERE record_id IN ({placeholders}) AND "action" = ?'
            " GROUP BY record_id",
            tuple(int(item) for item in record_ids) + ("edit",),
        )
        return {int(row["record_id"]): int(row["total"]) for row in rows}

    async def _prep_item_exists(self, item_id: int) -> bool:
        """这个备货品还在不在主数据里。只查主键，不替调用方判断 active。"""
        rows = await self._rows(
            "SELECT id FROM prep_items WHERE id = ?", (int(item_id),)
        )
        return bool(rows)

    async def _images_for(self, record_ids: list[int]) -> dict[int, list[dict]]:
        """记录号 → 该条的图片（按上传顺序）。一次查完，不给每条记录打一次库。"""
        if not record_ids:
            return {}
        placeholders = ", ".join("?" for _ in record_ids)
        rows = await self._rows(
            "SELECT record_id, capture_id, content_type, byte_size, sort_order"
            f" FROM wastage_images WHERE record_id IN ({placeholders})"
            " ORDER BY record_id, sort_order, id",
            tuple(int(item) for item in record_ids),
        )
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(int(row["record_id"]), []).append(
                {
                    "capture_id": row["capture_id"],
                    "content_type": row["content_type"],
                    "byte_size": int(row["byte_size"] or 0),
                    "url": f"/api/wastage/images/{row['capture_id']}",
                }
            )
        return grouped

    # ── 登记 ────────────────────────────────────────────────────────────────
    @_needs_migration
    @serialized_write
    async def add_record(
        self,
        actor: WastageActor,
        *,
        item_name: Any,
        quantity: Any,
        unit: Any,
        reason: Any,
        remark: Any = "",
        wastage_date: Any = None,
        image_ids: Optional[list] = None,
        prep_item_id: Any = None,
    ) -> dict:
        """登记一笔损耗。两种身份走同一条路，落库时带上「谁登记的」。

        日期默认今天；**未来的日子不收**（源项目把日期选择器卡在今天）。登记后
        **24 小时内本人可更正**（``amend_record`` 改字段 / ``revoke_record`` 撤回，
        每次都留一条 revision）；过了窗口就只能另记一笔说明。

        `prep_item_id` 可选：挂了就多一个「这笔报废是哪个备货品」的对齐，复盘算多备代价
        时先按 id 匹配（没挂的旧行仍按品名兜底，见 `services/prep_review/summary.py`）。
        `window_start` / `window_end` 每次都按 `wastage_date` 的 07:00 窗口写（两列可空，
        只为历史行留的），这样一笔报废能落进某个品-天，而不是只躺在自然日上。
        """
        name = _clean_item_name(item_name)
        amount = _require_quantity(quantity)
        unit_text = _clean_unit(unit)
        reason_key = _require_reason(reason)
        remark_text = _clean_remark(remark)
        if reason_key == "other" and not remark_text:
            # 源项目同一条规矩：「其他」不写说明，统计里那 30% 就没人看得懂。
            raise WastageError("missing_remark", "missing_remark")

        day = self.today() if wastage_date in (None, "") else _require_date(wastage_date)
        if day > self.today():
            raise WastageError("future_day", "future_day")

        item_id = _clean_prep_item_id(prep_item_id)
        if item_id is not None and not await self._prep_item_exists(item_id):
            raise WastageError("unknown_prep_item", str(item_id))
        window_start, window_end = _window_bounds(day)

        ids = [str(item) for item in (image_ids or []) if str(item or "").strip()]
        if len(ids) > MAX_IMAGES:
            raise WastageError("too_many_images", str(MAX_IMAGES))

        stamp = self._now_iso()
        # 先确认这批图确实是**这个人**传的、还没被别的记录用掉 —— 不确认就绑，
        # 等于把别人（或另一笔登记）的照片挂到这一条上。
        for capture_id in ids:
            rows = await self._rows(
                "SELECT id FROM wastage_images"
                " WHERE capture_id = ? AND owner_key = ? AND record_id IS NULL",
                (capture_id, actor.owner_key),
            )
            if not rows:
                raise WastageError("unknown_image", capture_id)

        cur = await self._conn.execute(
            "INSERT INTO wastage_records"
            " (item_name, quantity, unit, reason, remark, wastage_date,"
            "  operator_kind, employee_id, operator_name, created_at, updated_at,"
            "  prep_item_id, window_start, window_end)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                name,
                amount,
                unit_text,
                reason_key,
                remark_text,
                day,
                actor.kind,
                actor.employee_id,
                actor.display_name,
                stamp,
                stamp,
                item_id,
                window_start,
                window_end,
            ),
        )
        created = await cur.fetchone()
        record_id = int(created["id"])

        for index, capture_id in enumerate(ids):
            await self._conn.execute(
                "UPDATE wastage_images SET record_id = ?, sort_order = ?"
                " WHERE capture_id = ? AND owner_key = ? AND record_id IS NULL",
                (record_id, index, capture_id, actor.owner_key),
            )

        await self._conn.commit()
        record = await self.record_by_id(record_id, actor=actor)
        assert record is not None  # 刚写进去的那一行
        return record

    @_needs_migration
    async def record_by_id(
        self, record_id: int, actor: Optional[WastageActor] = None
    ) -> Optional[dict]:
        """按号取一条，**已撤回的当不存在**（撤回是软删，读路径一律不返回它）。

        传 ``actor`` 时顺带算 ``can_amend`` —— 它取决于「谁在看」，所以不能由
        ``_record_row`` 替所有人算一个值。不传就只给 ``amend_deadline_at``。
        """
        record = await self._raw_record(record_id)
        if record is None:
            return None
        images = await self._images_for([int(record["id"])])
        record["images"] = images.get(int(record["id"]), [])
        if actor is not None:
            record["can_amend"] = self._can_amend(record, actor)
        return record

    async def _raw_record(self, record_id: int) -> Optional[dict]:
        """取未撤回的原始行（`_record_row` 形状、无图片）。"""
        rows = await self._rows(
            f"SELECT {_RECORD_COLUMNS} FROM wastage_records"
            " WHERE id = ? AND revoked_at IS NULL",
            (int(record_id),),
        )
        return _record_row(rows[0]) if rows else None

    async def _write_revision(
        self,
        *,
        record_id: int,
        action: str,
        actor: WastageActor,
        before: dict,
        after: dict,
    ) -> None:
        """留一条修订：谁、什么时候、改前改后（快照不含照片）。"""
        await self._conn.execute(
            "INSERT INTO wastage_record_revisions"
            ' (record_id, "action", operator_kind, employee_id, operator_name,'
            "  before_json, after_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(record_id),
                action,
                actor.kind,
                actor.employee_id,
                actor.display_name,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                self._now_iso(),
            ),
        )

    # ── 更正：24 小时内、仅限本人 ──────────────────────────────────────────
    @_needs_migration
    @serialized_write
    async def amend_record(
        self,
        actor: WastageActor,
        record_id: int,
        *,
        item_name: Any = None,
        quantity: Any = None,
        unit: Any = None,
        reason: Any = None,
        remark: Any = None,
        wastage_date: Any = None,
    ) -> dict:
        """更正本人 24 小时内登记的一笔：只改传了值的字段（``None`` = 不动），照片不参与。

        校验顺序是刻意的：先「这条还在不在」、再「是不是他的」、最后「窗口关没关」。
        把「不是你的」与「过期了」分成两个错，店员才知道下一步该找谁。字段规则与
        登记复用同一批函数，两条路不会各有一套「什么算合法」。
        """
        record = await self._raw_record(record_id)
        if record is None:
            # 不存在与已撤回对调用方是同一件事：这一笔不能再改，也不该被读出来。
            raise WastageError("unknown_record", str(record_id))
        if not self._owns_record(record, actor):
            raise WastageError("not_owner", str(record_id))
        deadline = _amend_deadline(record.get("created_at"))
        if deadline is None or self._now_dt() > deadline:
            # created_at 解析不了也走这里：拿不准就不许改（fail-closed）。
            raise WastageError("amend_window_closed", str(AMEND_WINDOW_HOURS))

        changes: dict[str, Any] = {}
        if item_name is not None:
            changes["item_name"] = _clean_item_name(item_name)
        if quantity is not None:
            changes["quantity"] = _require_quantity(quantity)
        if unit is not None:
            changes["unit"] = _clean_unit(unit)
        if reason is not None:
            changes["reason"] = _require_reason(reason)
        if remark is not None:
            changes["remark"] = _clean_remark(remark)
        if wastage_date is not None:
            day = _require_date(wastage_date)
            if day > self.today():
                raise WastageError("future_day", "future_day")
            changes["wastage_date"] = day
            # 窗口是按 `wastage_date` 推出来的（票 09 的对齐列）：日期一改就得跟着重算，
            # 否则这笔报废会停在前一天的品-天里。
            changes["window_start"], changes["window_end"] = _window_bounds(day)

        # 「其他」必须带说明：改原因或改备注都可能把这句话抹掉，改完仍要成立。
        final_reason = changes.get("reason", record.get("reason"))
        final_remark = changes.get("remark", record.get("remark"))
        if final_reason == "other" and not final_remark:
            raise WastageError("missing_remark", "missing_remark")

        if not changes:
            # 什么都没给不算一次更正：不写库，也不凭空留一条 revision。
            return await self.record_by_id(record_id, actor=actor)

        stamp = self._now_iso()
        assignments = ", ".join(f"{column} = ?" for column in changes)
        await self._conn.execute(
            f"UPDATE wastage_records SET {assignments}, updated_at = ? WHERE id = ?",
            tuple(changes.values()) + (stamp, int(record_id)),
        )
        before = _snapshot(record)
        # 写锁内不会有人把这条撤回（撤回同在锁里），读回空只可能是库被动过。
        updated = await self._raw_record(record_id)
        if updated is None:  # pragma: no cover - 防御，正常走不到
            raise WastageError("unknown_record", str(record_id))
        await self._write_revision(
            record_id=int(record_id),
            action="edit",
            actor=actor,
            before=before,
            after=_snapshot(updated),
        )
        await self._conn.commit()
        refreshed = await self.record_by_id(record_id, actor=actor)
        if refreshed is None:  # pragma: no cover - 防御，正常走不到
            raise WastageError("unknown_record", str(record_id))
        return refreshed

    @_needs_migration
    @serialized_write
    async def revoke_record(self, actor: WastageActor, record_id: int) -> dict:
        """撤回本人 24 小时内登记的一笔。软删：读路径从此看不到它，修订里留整行快照。

        不物理删 —— 统计与模板都从记录里长出来，物理删会让「昨天那个数字怎么变的」
        无处可查；照片也通过外键挂在记录上，会跟着丢。
        """
        record = await self._raw_record(record_id)
        if record is None:
            raise WastageError("unknown_record", str(record_id))
        if not self._owns_record(record, actor):
            raise WastageError("not_owner", str(record_id))
        deadline = _amend_deadline(record.get("created_at"))
        if deadline is None or self._now_dt() > deadline:
            raise WastageError("amend_window_closed", str(AMEND_WINDOW_HOURS))

        stamp = self._now_iso()
        await self._conn.execute(
            "UPDATE wastage_records"
            " SET revoked_at = ?, revoked_by_kind = ?, revoked_by_employee_id = ?,"
            " updated_at = ?"
            " WHERE id = ?",
            (stamp, actor.kind, actor.employee_id, stamp, int(record_id)),
        )
        await self._write_revision(
            record_id=int(record_id),
            action="revoke",
            actor=actor,
            before=_snapshot(record),
            after={},
        )
        await self._conn.commit()
        record["revoked_at"] = stamp
        record["revoked_by_kind"] = actor.kind
        record["revoked_by_employee_id"] = actor.employee_id
        record["updated_at"] = stamp
        record["can_amend"] = False
        images = await self._images_for([int(record_id)])
        record["images"] = images.get(int(record_id), [])
        return record

    # ── 查询 ────────────────────────────────────────────────────────────────
    def _scope_clause(
        self,
        actor: WastageActor,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        reason: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> tuple[str, list[Any]]:
        """归属 + 筛选 → `WHERE` 子句与参数。**只有这一处**决定员工看不看得到别人的。

        员工那一档按 `employee_id` 过滤（管理端在员工手机上登记的那几笔不算他的）；
        管理端那一档、以及开了「损耗分析」能力的员工（`can_see_all`）不加这一条，看全店。
        """
        # 撤回是软删：明细 / 补全 / 模板历史都从 `_scope_clause` 过，所以撤回过滤只加这一处。
        # 统计不走这里（见 `statistics` 自己的子句），那边单独加同样的一条。
        where: list[str] = ["revoked_at IS NULL"]
        params: list[Any] = []
        if not actor.can_see_all:
            where.append("employee_id = ?")
            params.append(int(actor.employee_id))
        if start_date not in (None, ""):
            where.append("wastage_date >= ?")
            params.append(_require_date(start_date))
        if end_date not in (None, ""):
            where.append("wastage_date <= ?")
            params.append(_require_date(end_date))
        if reason not in (None, ""):
            where.append("reason = ?")
            params.append(_require_reason(reason))
        text = _clean_keyword(keyword)
        if text:
            # 关键词进的是参数位（不拼 SQL），但 LIKE 的 `%` / `_` 仍然是**模式语法**：
            # 不转义的话搜一个 `%` 会把全表捞出来。转义符本身也先转义（顺序不能反）。
            escaped = (
                text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            # 备注与操作人一起搜：店长按「退菜」「张三」找一笔时，不该先知道物料名。
            where.append(
                "(item_name ILIKE ? ESCAPE '\\' OR remark ILIKE ? ESCAPE '\\'"
                " OR operator_name ILIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{escaped}%"] * 3)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        return clause, params

    @_needs_migration
    async def list_records(
        self,
        actor: WastageActor,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        reason: Optional[str] = None,
        keyword: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """明细：**新的在前**，带总条数与每条的图片。

        `total` 是**筛选后的总数**（不是这一页的条数）：页面靠它画「第几页 / 共几页」，
        员工端也靠它显示「这个范围内记了几笔」。

        另给两样页面要的派生数据：`counts`（归属口径的今天 / 昨天笔数，与筛子无关）
        与每条的 `edit_count`（改过几次）。
        """
        clause, params = self._scope_clause(
            actor,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            keyword=keyword,
        )
        total = await self._count(clause, tuple(params))
        size = max(1, min(int(limit or 20), 100))
        start = max(0, int(offset or 0))
        rows = await self._rows(
            f"SELECT {_RECORD_COLUMNS} FROM wastage_records{clause}"
            " ORDER BY wastage_date DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, start),
        )
        records = [_record_row(row) for row in rows]
        images = await self._images_for([int(item["id"]) for item in records])
        edit_counts = await self._edit_counts([int(item["id"]) for item in records])
        for record in records:
            record["images"] = images.get(int(record["id"]), [])
            record["can_amend"] = self._can_amend(record, actor)
            record["edit_count"] = edit_counts.get(int(record["id"]), 0)
        # 状态行说「今天记了 N 笔」：这是**归属口径**的笔数，换关键词 / 原因不该变，
        # 所以不复用上面的筛选子句，只按归属 + 撤回 + 日期重算。今天取台账那一刻钟
        # （self.today()），与登记页上的「今天」是同一个自然日。
        today = self.today()
        yesterday = (self._now_dt().date() - timedelta(days=1)).isoformat()
        count_where = ["revoked_at IS NULL", "wastage_date IN (?, ?)"]
        count_params: list[Any] = [today, yesterday]
        if not actor.can_see_all:
            count_where.insert(0, "employee_id = ?")
            count_params.insert(0, int(actor.employee_id))
        day_rows = await self._rows(
            "SELECT wastage_date, COUNT(*) AS total FROM wastage_records"
            f" WHERE {' AND '.join(count_where)} GROUP BY wastage_date",
            tuple(count_params),
        )
        day_counts = {row["wastage_date"]: int(row["total"]) for row in day_rows}
        return {
            "total": total,
            "offset": start,
            "limit": size,
            "counts": {
                "today": day_counts.get(today, 0),
                "yesterday": day_counts.get(yesterday, 0),
            },
            "records": records,
        }

    @_needs_migration
    async def statistics(self, *, month: Optional[str] = None, start_date=None, end_date=None) -> dict:
        """一段时间的汇总（管理端那一页读的就是它）。

        范围两种给法：`month=YYYY-MM`（页面按月翻）或显式的起止日。统计**只看日期**，
        不分登记人 —— 店长要的是「这个月店里损耗了多少」，不是「我记了几笔」。
        """
        if month not in (None, ""):
            first, last = _month_range(str(month))
        else:
            first = _require_date(start_date) if start_date not in (None, "") else None
            last = _require_date(end_date) if end_date not in (None, "") else None
            if first is None or last is None:
                raise WastageError("missing_range", "missing_range")
            if first > last:
                raise WastageError("invalid_range", "invalid_range")

        params = (first, last)
        # 统计也按「撤回的不算」：这里自己拼子句，所以撤回过滤得单独加（不在 _scope_clause）。
        clause = " WHERE revoked_at IS NULL AND wastage_date >= ? AND wastage_date <= ?"

        totals = await self._rows(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT item_name) AS item_types"
            f" FROM wastage_records{clause}",
            params,
        )
        total_count = int(totals[0]["total"]) if totals else 0
        item_type_count = int(totals[0]["item_types"]) if totals else 0

        by_reason = []
        for row in await self._rows(
            "SELECT reason, COUNT(*) AS count FROM wastage_records"
            f"{clause} GROUP BY reason ORDER BY count DESC, reason",
            params,
        ):
            count = int(row["count"])
            by_reason.append(
                {
                    "reason": row["reason"],
                    "reason_text": reason_label(row["reason"]),
                    "count": count,
                    "percentage": round(count / total_count * 100) if total_count else 0,
                }
            )

        top_items = []
        for row in await self._rows(
            "SELECT item_name, unit, COUNT(*) AS count, SUM(quantity) AS total_quantity"
            f" FROM wastage_records{clause}"
            " GROUP BY item_name, unit ORDER BY count DESC, item_name LIMIT 10",
            params,
        ):
            total_quantity = row["total_quantity"]
            top_items.append(
                {
                    "item_name": row["item_name"],
                    "unit": row["unit"],
                    "count": int(row["count"]),
                    "total_quantity": float(total_quantity) if total_quantity is not None else 0.0,
                }
            )

        daily_stats = [
            {"date": row["wastage_date"], "count": int(row["count"])}
            for row in await self._rows(
                "SELECT wastage_date, COUNT(*) AS count FROM wastage_records"
                f"{clause} GROUP BY wastage_date ORDER BY wastage_date",
                params,
            )
        ]

        # 页面抬头那行「改过 X 笔 · 撤回 Y 笔」读的就是这两个数：范围与上面同一段日期。
        # 撤回按 wastage_date 落范围（它不在上面那个 WHERE 里，所以单独数）；
        # 改过按记录日期 join 修订表去重 —— 一笔改三次也只算一笔。
        revoked_rows = await self._rows(
            "SELECT COUNT(*) AS total FROM wastage_records"
            " WHERE revoked_at IS NOT NULL AND wastage_date >= ? AND wastage_date <= ?",
            params,
        )
        edited_rows = await self._rows(
            "SELECT COUNT(DISTINCT r.id) AS total FROM wastage_records r"
            ' JOIN wastage_record_revisions v ON v.record_id = r.id AND v."action" = ?'
            " WHERE r.wastage_date >= ? AND r.wastage_date <= ?",
            ("edit", first, last),
        )
        revoked_count = int(revoked_rows[0]["total"]) if revoked_rows else 0
        edited_count = int(edited_rows[0]["total"]) if edited_rows else 0

        return {
            "start_date": first,
            "end_date": last,
            "total_count": total_count,
            "item_type_count": item_type_count,
            "revoked_count": revoked_count,
            "edited_count": edited_count,
            "by_reason": by_reason,
            "top_items": top_items,
            "daily_stats": daily_stats,
        }

    # ── 历史建议 ────────────────────────────────────────────────────────────
    @_needs_migration
    async def suggestions(self, actor: WastageActor, *, days: int = 30, limit: int = 10) -> list[dict]:
        """物料建议：近 N 天登记过的名字 + 这个名字最近一次用的单位，去重、保留最近的顺序。

        员工那一档只看自己登记过的（他填的多半是自己经手过的东西）；管理端看全店
        —— 他登记时该认得店里所有会坏的物料。开了「损耗分析」能力的员工也看全店
        （他看得到全店的明细，补全就不该只给一半）。源项目的 `getSuggestions` 只给员工
        自己那一份，这里多出来的一档是给「店长也能登记」这条定的。

        **带单位**（2026-11-07）：页面把这几条摆成「最近记过」的 chips，点一下就该把
        「五花肉 + kg」一起填好 —— 只给名字的话员工还得再点一次单位，而这一页的
        全部意义就是少点几次。单位取这个名字**最近一次**登记的写法（排序后首次出现即
        最近），跟人当下的习惯一致；名字在 30 天里只出现过一次也有单位。
        """
        since = (self._now_dt().date() - timedelta(days=max(1, int(days)))).isoformat()
        clause, params = self._scope_clause(actor, start_date=since)
        rows = await self._rows(
            f"SELECT item_name, unit FROM wastage_records{clause}"
            " ORDER BY wastage_date DESC, id DESC LIMIT 200",
            tuple(params),
        )
        seen: list[dict] = []
        names: set[str] = set()
        for row in rows:
            name = row["item_name"]
            if name and name not in names:
                names.add(name)
                seen.append({"item_name": name, "unit": str(row["unit"] or "")})
            if len(seen) >= max(1, min(int(limit), 20)):
                break
        return seen

    # ── 常用模板 ────────────────────────────────────────────────────────────
    @_needs_migration
    async def templates(self, actor: WastageActor, *, history_days: int = 60) -> dict:
        """常用模板 = **自定义的**（这个人存过的）+ **历史高频的**（近 60 天他自己登记
        最多的那几个「物料 + 单位 + 原因」组合，去重掉已被自定义覆盖的）。

        自定义模板是「我常这么记」，自动那部分是「我最近实际上常这么记」——两者都可能
        有用，源项目也是这么拼的（`getTemplates`）。
        """
        custom = [
            {
                "id": int(row["id"]),
                "item_name": row["item_name"],
                "unit": row["unit"],
                "reason": row["reason"],
                "reason_text": reason_label(row["reason"]),
                "is_custom": True,
            }
            for row in await self._rows(
                "SELECT id, item_name, unit, reason FROM wastage_templates"
                " WHERE owner_key = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (actor.owner_key, MAX_TEMPLATES),
            )
        ]

        since = (self._now_dt().date() - timedelta(days=max(1, int(history_days)))).isoformat()
        clause, params = self._scope_clause(actor, start_date=since)
        rows = await self._rows(
            "SELECT item_name, unit, reason, COUNT(*) AS count"
            f" FROM wastage_records{clause}"
            " GROUP BY item_name, unit, reason ORDER BY count DESC, item_name LIMIT 20",
            tuple(params),
        )
        covered = {(t["item_name"], t["unit"], t["reason"]) for t in custom}
        auto = [
            {
                "id": None,
                "item_name": row["item_name"],
                "unit": row["unit"],
                "reason": row["reason"],
                "reason_text": reason_label(row["reason"]),
                "is_custom": False,
            }
            for row in rows
            if (row["item_name"], row["unit"], row["reason"]) not in covered
        ][:10]
        return {"templates": custom + auto}

    @_needs_migration
    @serialized_write
    async def save_template(
        self,
        actor: WastageActor,
        *,
        item_name: Any,
        unit: Any,
        reason: Any,
    ) -> dict:
        """存一个自定义模板。重复、超上限都在这里拦下来（并发下靠唯一键兜底）。"""
        name = _clean_item_name(item_name)
        unit_text = _clean_unit(unit)
        reason_key = _require_reason(reason)

        rows = await self._rows(
            "SELECT COUNT(*) AS total FROM wastage_templates WHERE owner_key = ?",
            (actor.owner_key,),
        )
        if rows and int(rows[0]["total"]) >= MAX_TEMPLATES:
            raise WastageError("too_many_templates", str(MAX_TEMPLATES))

        try:
            await self._conn.execute(
                "INSERT INTO wastage_templates"
                " (owner_key, owner_kind, employee_id, item_name, unit, reason, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    actor.owner_key,
                    actor.kind,
                    actor.employee_id,
                    name,
                    unit_text,
                    reason_key,
                    self._now_iso(),
                ),
            )
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
            # 唯一键（同一人同一组合）或库那边的 CHECK 拦下来的。并发下重复提交会走到
            # 这一支：先 SELECT 到「没有」，两个请求一起插入，第二个撞唯一键。
            await self._rollback_quietly()
            raise WastageError("template_exists", "template_exists")
        await self._conn.commit()
        return {
            "item_name": name,
            "unit": unit_text,
            "reason": reason_key,
            "reason_text": reason_label(reason_key),
        }

    @_needs_migration
    @serialized_write
    async def delete_template(self, actor: WastageActor, template_id: int) -> None:
        """删一个自定义模板。**只能删自己的** —— 归属键不匹配就是「找不到」。"""
        try:
            target = int(template_id)
        except (TypeError, ValueError):
            raise WastageError("unknown_template", "unknown_template")
        # 先查再删（不靠驱动各自不同的 rowcount 口径）：查不到 = 这条不是他的，或已删。
        rows = await self._rows(
            "SELECT id FROM wastage_templates WHERE id = ? AND owner_key = ?",
            (target, actor.owner_key),
        )
        if not rows:
            raise WastageError("unknown_template", "unknown_template")
        await self._conn.execute(
            "DELETE FROM wastage_templates WHERE id = ?", (target,)
        )
        await self._conn.commit()

    # ── 现场照片 ────────────────────────────────────────────────────────────
    async def add_image(self, actor: WastageActor, data: bytes, content_type: str) -> dict:
        """把一张照片落盘并记一行元数据（此时还没绑到任何一条登记上）。

        先落盘再写库：写库失败时文件成为孤儿（`record_id IS NULL` 的行也记下来了，
        以后要清就按这一列清），反过来则会出现「库里有行、盘上没文件」的坏引用。
        """
        if self._captures is None:
            raise WastageError("images_unavailable", "images_unavailable")
        capture_id = await self._captures.put_async(data, content_type=content_type)
        try:
            cur = await self._conn.execute(
                "INSERT INTO wastage_images"
                " (capture_id, owner_key, content_type, byte_size, sort_order, created_at)"
                " VALUES (?, ?, ?, ?, 0, ?) RETURNING id",
                (capture_id, actor.owner_key, content_type, len(data), self._now_iso()),
            )
            created = await cur.fetchone()
            await self._conn.commit()
        except Exception:
            await self._rollback_quietly()
            await self._captures.delete_async(capture_id)
            raise
        return {
            "id": int(created["id"]),
            "capture_id": capture_id,
            "content_type": content_type,
            "byte_size": len(data),
            "url": f"/api/wastage/images/{capture_id}",
        }

    async def image_meta(self, actor: WastageActor, capture_id: str) -> Optional[dict]:
        """这张图这个人能不能看 —— 能就回元数据，不能回 None（调用方一律回 404）。

        管理端看全部；开了「损耗分析」能力的员工也看全部（明细里每一笔都带照片）；
        其余员工只能看**自己传的**。不区分「不存在」与「不是你的」：
        否则别人传过什么图能被一条 403 问出来。
        """
        params: list[Any] = [str(capture_id or "")]
        scope = ""
        if not actor.can_see_all:
            scope = " AND owner_key = ?"
            params.append(actor.owner_key)
        rows = await self._rows(
            "SELECT capture_id, content_type, byte_size, owner_key FROM wastage_images"
            f" WHERE capture_id = ?{scope}",
            tuple(params),
        )
        return dict(rows[0]) if rows else None

    async def image_bytes(self, actor: WastageActor, capture_id: str) -> Optional[tuple[bytes, str]]:
        """读图的字节。权限在 `image_meta` 那一处判，这里只负责把字节取出来。"""
        if self._captures is None:
            return None
        meta = await self.image_meta(actor, capture_id)
        if meta is None:
            return None
        try:
            data = await self._captures.get_async(str(meta["capture_id"]))
        except FileNotFoundError:
            # 库里有行、盘上没文件（备份恢复只搬库不搬目录时会这样）：当它不存在。
            logger.warning("损耗照片文件缺失 capture=%s", meta.get("capture_id"))
            return None
        return data, str(meta["content_type"] or "application/octet-stream")
