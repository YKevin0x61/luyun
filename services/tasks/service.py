#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台「任务」域的业务层：发布 / 编辑 / 撤回 / 列表（票 02 起逐票长）。

持久层拆成两块，边界按「谁负责落盘」划：

- `services/tasks/store.py`：照片台账（上传、归属、读取权限），文件与库一起管；
- 本模块：任务定义（定义只写一次）与期实例（每期一份快照）、成员指派、列表聚合。

设计见 `docs/adr/0131-work-tasks-independent-domain.md`、
`0132-task-assignment-people-and-pools.md`、`0133-recurring-task-definition-and-instances.md`。

几条容易写错的规则，先钉在这里：

1. **工作台日 00:00 切**。任务的「今天」用 `services.business_day.current_workbench_date`，
   不是营业日（06:00 切）—— 员工嘴里的「今天要交」是自然日（ADR 0125）。
2. **即时任务只有一个期实例**，`period_date` = 截止日；发布即物化。撤回同时把定义与
   实例置 `cancelled`，历史仍可查。
3. **池任务不在发布时冻结成员**：`period_date` 的排班可能还没排出来；成员在读取 / 提交时
   按当期排班实时解析。小组任务是具名指派，发布即把成员快照进 `work_task_instance_members`
   （定义成员也留一份，供后续期复用）。
4. **编辑只对「还没人提交」的待交任务开放**；一旦有人提交（哪怕还在待验收），内容就冻结，
   只能驳回后让员工重交。这条保证「员工拍照时看到的标题/张数/截止」就是定义里的那一份。
5. 写操作共用 `DatabaseManager` 的底层连接与显式事务：多行写入只在最后 commit 一次，
   中途失败整段回滚（照片上传那条路有自己的清理，与这里无关）。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import asyncpg

from database import CHINA_TZ
from services.business_day import current_workbench_date
from services.tasks.notify import enqueue_work_task_push
from services.tasks.store import TaskActor, TaskStoreError

logger = logging.getLogger(__name__)

#: 四种池（ADR 0132 决策 2）：全部在职 / 有管理能力 / 当天当班 / 当天在某工作区当班。
POOL_KINDS = ("all_staff", "managers", "on_duty", "zone_on_duty")
POOL_LABELS = {
    "all_staff": "全体在职员工",
    "managers": "有管理能力的员工",
    "on_duty": "当天当班的人",
    "zone_on_duty": "当天在指定工作区当班的人",
}
ASSIGNEE_KINDS = ("group", "pool")

#: 勾了群推送但门店没订阅「工作任务」时，回给发布页的提示（任务照常创建）。
WECOM_NO_SUBSCRIPTION_WARNING = (
    "任务已经创建，但没有发到企业微信群：当前没有群订阅「工作任务」，"
    "请去「企微推送」页给群勾上这一项。"
)

#: 定期任务频率（ADR 0133）：每天 / 每周几 / 每月几号。weekday 0=周一..6=周日。
FREQUENCY_KINDS = ("daily", "weekly", "monthly")
RECURRING_RECENT_PERIODS = 7

PURPOSE_REFERENCE = "reference"

TITLE_MAX = 200
BODY_MAX = 2000
REJECT_REASON_MAX = 200
FINISHED_LIMIT = 30
MINE_LIMIT = 30

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TaskValidationError(Exception):
    """参数不合法 → API 400，文案直接给用户看。"""


class TaskConflictError(Exception):
    """当前状态不允许这个动作（已有人提交 / 已撤回）→ API 409。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TaskPermissionError(Exception):
    """没有权限做这个动作 → API 403。"""


class TaskNotFoundError(Exception):
    """任务不存在，或无权看（一律 404，避免用 403 探出别人有什么）。"""


def _as_int(value: Any, *, field: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise TaskValidationError(f"{field}必须是一个整数") from None


class TaskService:
    def __init__(self, conn_or_db: Any, *, now=None):
        self._conn = getattr(conn_or_db, "_conn", conn_or_db)
        # 企微入队走 DatabaseManager 的公开方法；只拿到裸连接时（测试/内部调用）
        # 跳过推送，任务本身不受影响。
        self._db = conn_or_db if hasattr(conn_or_db, "wecom_outbox_enqueue") else None
        self._now = now or (lambda: datetime.now(CHINA_TZ))

    # ── 基础 ────────────────────────────────────────────────────────────────
    def _now_dt(self) -> datetime:
        moment = self._now()
        return moment if moment.tzinfo else moment.replace(tzinfo=CHINA_TZ)

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def _today(self) -> str:
        return current_workbench_date(self._now_dt())

    async def _rows(self, sql: str, params: tuple = ()) -> list:
        try:
            cur = await self._conn.execute(sql, params)
            return list(await cur.fetchall())
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            raise TaskStoreError("not_migrated", str(exc)) from exc

    async def _row(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = await self._rows(sql, params)
        return dict(rows[0]) if rows else None

    async def _commit(self) -> None:
        await self._conn.commit()

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # noqa: BLE001 - 回滚失败不能盖住原始错误
            logger.warning("任务写入失败后回滚也失败了", exc_info=True)

    # ── 员工与工作区（表单的数据源）────────────────────────────────────────
    async def _active_employees(self, on_date: str) -> list:
        """某一天仍在职、已审批、未停用的员工。

        `resigned_at` 三态（0041）：NULL 在职 / '' 待补 / `YYYY-MM-DD` 当天仍在职、次日起
        失效 —— 所以是 `>=` 而不是 `>`。未来日期发布时也按那一天算，不能拿「今天在职」
        去覆盖「下周一已离职」。
        """
        return await self._rows(
            "SELECT id, name, job_title, admin_caps FROM hygiene_employees"
            " WHERE approved = 1 AND disabled = 0"
            "   AND (resigned_at IS NULL OR resigned_at = ''"
            "        OR substr(resigned_at, 1, 10) >= ?)"
            " ORDER BY name, id",
            (on_date,),
        )

    async def assignees_catalog(self) -> dict:
        """发布表单要的两样：在职员工名单 + 工作区名单（员工端有 `task` 能力也能读）。"""
        today = self._today()
        employees = await self._active_employees(today)
        zones = await self._rows("SELECT id, name FROM hygiene_zones ORDER BY name, id")
        return {
            "today": today,
            "employees": [
                {
                    "id": int(row["id"]),
                    "name": str(row["name"] or ""),
                    "job_title": str(row["job_title"] or ""),
                }
                for row in employees
            ],
            "zones": [
                {"id": int(row["id"]), "name": str(row["name"] or "")}
                for row in zones
            ],
        }

    # ── 池解析 ──────────────────────────────────────────────────────────────
    async def _pool_members(self, pool_kind: str, pool_zone_id: Optional[int], period_date: str) -> list:
        if pool_kind not in POOL_KINDS:
            raise TaskValidationError("请选择一个有效的指派池")
        # 占位符顺序 = SQL 文本出现顺序：JOIN 里的 business_date / zone_id 在前，
        # WHERE 里的离职日期在后。写反了不会报错，只会筛出错的人（最坏是空池）。
        params: list[Any] = []
        joins = ""
        where = [
            "e.approved = 1",
            "e.disabled = 0",
            "(e.resigned_at IS NULL OR e.resigned_at = ''"
            " OR substr(e.resigned_at, 1, 10) >= ?)",
        ]
        if pool_kind == "managers":
            where.append("e.admin_caps IS NOT NULL AND btrim(e.admin_caps) NOT IN ('', '[]')")
        if pool_kind in ("on_duty", "zone_on_duty"):
            joins = (
                " JOIN staff_assignments sa ON sa.employee_id = e.id"
                "  AND sa.business_date = ? AND sa.shift_id IS NOT NULL"
            )
            params.append(period_date)
        if pool_kind == "zone_on_duty":
            if pool_zone_id is None:
                raise TaskValidationError("选了「当天在某工作区当班」，还要选一个工作区")
            joins += " AND sa.zone_id = ?"
            params.append(pool_zone_id)
        params.append(period_date)
        sql = (
            "SELECT DISTINCT e.id, e.name FROM hygiene_employees e"
            + joins
            + " WHERE " + " AND ".join(where)
            + " ORDER BY e.name, e.id"
        )
        rows = await self._rows(sql, tuple(params))
        # 池本来就只解析在职的人，resigned 恒 False；字段统一，前端不用分两套判断。
        return [
            {"id": int(row["id"]), "name": str(row["name"] or ""), "resigned": False}
            for row in rows
        ]

    # ── 校验 ────────────────────────────────────────────────────────────────
    def _normalize_assignee(self, assignee) -> tuple:
        """把前端那份 assignee 收成 (kind, group_ids, pool_kind, pool_zone_id)。

        发布、改派共用一套校验，避免两边口径漂移。
        """
        if not isinstance(assignee, dict):
            raise TaskValidationError("请选择指派对象")
        assignee_kind = str(assignee.get("kind") or "")
        if assignee_kind not in ASSIGNEE_KINDS:
            raise TaskValidationError("请选择指派对象：一组员工，或一个池")
        group_ids: list[int] = []
        pool_kind: Optional[str] = None
        pool_zone_id: Optional[int] = None
        if assignee_kind == "group":
            raw_ids = assignee.get("employee_ids") or []
            if not isinstance(raw_ids, list):
                raise TaskValidationError("指派员工名单格式不对")
            for raw in raw_ids:
                value = _as_int(raw, field="员工编号")
                if value not in group_ids:
                    group_ids.append(value)
            if not group_ids:
                raise TaskValidationError("至少选一位员工，或者改选一个池")
        else:
            pool_kind = str(assignee.get("pool_kind") or "")
            if pool_kind not in POOL_KINDS:
                raise TaskValidationError("请选择一个有效的指派池")
            if pool_kind == "zone_on_duty":
                if assignee.get("zone_id") in (None, ""):
                    raise TaskValidationError("选了「当天在某工作区当班」，还要选一个工作区")
                pool_zone_id = _as_int(assignee.get("zone_id"), field="工作区编号")
        return assignee_kind, group_ids, pool_kind, pool_zone_id

    def _normalize_payload(self, actor: TaskActor, payload: dict) -> dict:
        kind = str(payload.get("kind") or "once")
        if kind not in ("once", "recurring"):
            raise TaskValidationError("任务类型只能是「发一次」或「定期」")

        title = str(payload.get("title") or "").strip()
        if not title:
            raise TaskValidationError("任务标题不能为空")
        if len(title) > TITLE_MAX:
            raise TaskValidationError(f"任务标题不能超过 {TITLE_MAX} 个字")
        body = str(payload.get("body") or "").strip()
        if len(body) > BODY_MAX:
            raise TaskValidationError(f"任务说明不能超过 {BODY_MAX} 个字")

        photo_count = _as_int(payload.get("photo_count", 1), field="照片张数")
        if photo_count < 1 or photo_count > 3:
            raise TaskValidationError("照片张数只能是 1～3 张")

        assignee_kind, group_ids, pool_kind, pool_zone_id = self._normalize_assignee(
            payload.get("assignee")
        )

        hhmm = str(payload.get("deadline_hhmm") or "").strip()
        if not _HHMM_RE.match(hhmm):
            raise TaskValidationError("截止时刻格式不对，应为 HH:MM")

        reference_capture_id = payload.get("reference_capture_id")
        if reference_capture_id not in (None, ""):
            reference_capture_id = str(reference_capture_id)
        else:
            reference_capture_id = None

        data: dict = {
            "kind": kind,
            "title": title,
            "body": body,
            "photo_count": photo_count,
            "reference_capture_id": reference_capture_id,
            "assignee_kind": assignee_kind,
            "group_ids": group_ids,
            "pool_kind": pool_kind,
            "pool_zone_id": pool_zone_id,
            "deadline_hhmm": hhmm,
            "wecom_group_push": 1 if payload.get("wecom_group_push") else 0,
            "frequency_kind": None,
            "weekday": None,
            "month_day": None,
            "start_date": None,
            "end_date": None,
            "due_date": None,
            "deadline_at": None,
            "member_check_date": None,
        }

        if kind == "once":
            due_date = str(payload.get("due_date") or "").strip()
            try:
                due = date.fromisoformat(due_date)
            except ValueError:
                raise TaskValidationError("截止日期格式不对，应为 YYYY-MM-DD") from None
            if due.isoformat() < self._today():
                raise TaskValidationError("截止日期不能早于今天")
            hour, minute = (int(part) for part in hhmm.split(":"))
            deadline = datetime(due.year, due.month, due.day, hour, minute, tzinfo=CHINA_TZ)
            if deadline <= self._now_dt():
                raise TaskValidationError("截止时刻已经过了，请选一个晚一点的时间")
            data["due_date"] = due.isoformat()
            data["deadline_at"] = deadline.isoformat()
            data["member_check_date"] = due.isoformat()
            return data

        # 定期任务：开始日期必填，结束可选；频率配平按 migration 里的 CHECK 走。
        start_text = str(payload.get("start_date") or "").strip()
        try:
            start_day = date.fromisoformat(start_text)
        except ValueError:
            raise TaskValidationError("开始日期格式不对，应为 YYYY-MM-DD") from None
        end_text = str(payload.get("end_date") or "").strip()
        end_day: Optional[date] = None
        if end_text:
            try:
                end_day = date.fromisoformat(end_text)
            except ValueError:
                raise TaskValidationError("结束日期格式不对，应为 YYYY-MM-DD") from None
            if end_day < start_day:
                raise TaskValidationError("结束日期不能早于开始日期")

        frequency = str(payload.get("frequency_kind") or "").strip()
        if frequency not in FREQUENCY_KINDS:
            raise TaskValidationError("请选择频率：每天、每周几或每月几号")
        weekday: Optional[int] = None
        month_day: Optional[int] = None
        if frequency == "weekly":
            weekday = _as_int(payload.get("weekday"), field="星期")
            if weekday < 0 or weekday > 6:
                raise TaskValidationError("请选择星期几")
        elif frequency == "monthly":
            month_day = _as_int(payload.get("month_day"), field="日期")
            if month_day < 1 or month_day > 31:
                raise TaskValidationError("每月日期只能是 1～31 号")
        data.update(
            {
                "frequency_kind": frequency,
                "weekday": weekday,
                "month_day": month_day,
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat() if end_day else None,
            }
        )
        # 组成员按「开始日与今天里较晚的那天」校验：开始日可能在过去，拿那天的
        # 在职状态会误判后来才离职的人。
        first_period = self._first_period(data)
        data["member_check_date"] = max(first_period, self._today())
        return data

    @staticmethod
    def _frequency_matches(definition: dict, day: date) -> bool:
        """这一天是否命中定义（工作日口径：自然日，ADR 0125）。"""
        start_text = str(definition.get("start_date") or "")
        if start_text and day.isoformat() < start_text:
            return False
        end_text = str(definition.get("end_date") or "")
        if end_text and day.isoformat() > end_text:
            return False
        frequency = str(definition.get("frequency_kind") or "")
        if frequency == "daily":
            return True
        if frequency == "weekly":
            return day.weekday() == int(definition.get("weekday") or 0)
        if frequency == "monthly":
            return day.day == int(definition.get("month_day") or 1)
        return False

    def _first_period(self, data: dict) -> str:
        cursor = date.fromisoformat(data["start_date"])
        for _ in range(400):
            if self._frequency_matches(data, cursor):
                return cursor.isoformat()
            cursor += timedelta(days=1)
        return data["start_date"]

    async def _validate_group_members(self, group_ids: list[int], period_date: str) -> list:
        active = await self._active_employees(period_date)
        by_id = {int(row["id"]): row for row in active}
        missing = [employee_id for employee_id in group_ids if employee_id not in by_id]
        if missing:
            raise TaskValidationError(
                f"选的人里有 {len(missing)} 位不在在职花名册（可能已离职或还没审批），请重新选"
            )
        return [
            {"id": employee_id, "name": str(by_id[employee_id]["name"] or "")}
            for employee_id in group_ids
        ]

    async def _validate_pool_zone(self, pool_kind: str, pool_zone_id: Optional[int]) -> None:
        if pool_kind != "zone_on_duty":
            return
        row = await self._row("SELECT id FROM hygiene_zones WHERE id = ?", (pool_zone_id,))
        if row is None:
            raise TaskValidationError("选的工作区不存在，请重新选")

    async def _validate_reference(self, actor: TaskActor, capture_id: Optional[str]) -> None:
        """参考图必须是这个人刚上传、还没被别的任务绑走的那一张。"""
        if not capture_id:
            return
        row = await self._row(
            "SELECT capture_id, purpose, owner_key FROM work_task_images WHERE capture_id = ?",
            (capture_id,),
        )
        if row is None or str(row["purpose"]) != PURPOSE_REFERENCE:
            raise TaskValidationError("参考图还没上传成功，请重新上传")
        if str(row["owner_key"]) != actor.owner_key:
            raise TaskValidationError("这张参考图不是你上传的，请重新上传")

    async def _group_rows(self, table: str, key_column: str, key: int) -> list:
        # 带上 resign 状态：成员快照要能标「负责人已离职」（票 08）。不新增列，
        # 也不靠任务表自己记状态 —— 花名册才是唯一事实。
        return await self._rows(
            f"SELECT m.employee_id, m.employee_name, e.resigned_at"
            f" FROM {table} m"
            f" LEFT JOIN hygiene_employees e ON e.id = m.employee_id"
            f" WHERE m.{key_column} = ? ORDER BY m.sort_order, m.employee_id",
            (key,),
        )

    def _is_resigned(self, resigned_at) -> bool:
        """resigned_at 是最后工作日：空串=待补日期（仍在职），次日起才算离职。"""
        text = str(resigned_at or "").strip()
        return bool(text) and text[:10] < self._today()

    def _members_out(self, rows: list) -> list:
        return [
            {
                "id": int(row["employee_id"]),
                "name": str(row["employee_name"] or ""),
                "resigned": self._is_resigned(row.get("resigned_at")),
            }
            for row in rows
        ]

    # ── 序列化 ──────────────────────────────────────────────────────────────
    async def _definition(self, definition_id: int) -> Optional[dict]:
        return await self._row("SELECT * FROM work_task_definitions WHERE id = ?", (definition_id,))

    async def _instance_row(self, instance_id: int) -> Optional[dict]:
        return await self._row(
            "SELECT i.*, d.kind AS definition_kind, d.status AS definition_status,"
            "       d.wecom_group_push AS wecom_group_push,"
            "       d.created_by_kind AS created_by_kind,"
            "       d.created_by_employee_id AS created_by_employee_id,"
            "       d.created_by_name AS created_by_name"
            " FROM work_task_instances i"
            " JOIN work_task_definitions d ON d.id = i.definition_id"
            " WHERE i.id = ?",
            (instance_id,),
        )

    async def _submitted_count(self, instance_id: int) -> int:
        rows = await self._rows(
            "SELECT count(*) AS n FROM work_task_submissions WHERE instance_id = ?",
            (instance_id,),
        )
        return int(rows[0]["n"]) if rows else 0

    async def _zone_names(self) -> dict:
        rows = await self._rows("SELECT id, name FROM hygiene_zones")
        return {int(row["id"]): str(row["name"] or "") for row in rows}

    async def _assignees_of(self, row: dict, zone_names: dict) -> list:
        instance_id = int(row["id"])
        if str(row["assignee_kind"]) == "group":
            members = await self._group_rows("work_task_instance_members", "instance_id", instance_id)
            return self._members_out(members)
        return await self._pool_members(
            str(row["pool_kind"] or ""),
            row.get("pool_zone_id"),
            str(row["period_date"]),
        )

    def _serialize(
        self,
        row: dict,
        *,
        assignees: list,
        zone_names: dict,
        submitted: int,
        summary: Optional[dict] = None,
    ) -> dict:
        deadline = _parse_iso(row.get("deadline_at"))
        now = self._now_dt()
        status = str(row["status"])
        overdue = status == "todo" and deadline is not None and now > deadline
        assignee_kind = str(row["assignee_kind"])
        pool_zone_id = row.get("pool_zone_id")
        can_change = status == "todo" and submitted == 0
        summary = summary or {}
        return {
            "id": int(row["id"]),
            "definition_id": int(row["definition_id"]),
            "kind": str(row.get("definition_kind") or "once"),
            "period_date": str(row["period_date"]),
            "title": str(row["title"] or ""),
            "body": str(row["body"] or ""),
            "photo_count": int(row["photo_count"]),
            "reference_capture_id": row.get("reference_capture_id"),
            "reference_image_url": (
                f"/api/tasks/images/{row['reference_capture_id']}"
                if row.get("reference_capture_id")
                else None
            ),
            "wecom_group_push": bool(row.get("wecom_group_push")),
            "assignee_kind": assignee_kind,
            "pool_kind": row.get("pool_kind"),
            "pool_label": POOL_LABELS.get(str(row.get("pool_kind") or ""), ""),
            "pool_zone_id": int(pool_zone_id) if pool_zone_id is not None else None,
            "pool_zone_name": zone_names.get(int(pool_zone_id)) if pool_zone_id is not None else None,
            "assignees": assignees,
            "assignee_names": [member["name"] for member in assignees],
            "needs_reassign": any(bool(member.get("resigned")) for member in assignees),
            "deadline_at": row.get("deadline_at"),
            "status": str(row["status"]),
            "overdue": overdue,
            "submitted_count": submitted,
            "pending_submitter_id": summary.get("pending_employee_id"),
            "pending_submitter_name": summary.get("pending_name"),
            "last_reject_reason": summary.get("reject_reason"),
            "created_by": {
                "kind": str(row.get("created_by_kind") or ""),
                "name": str(row.get("created_by_name") or ""),
            },
            "created_at": row.get("created_at"),
            "can_edit": can_change,
            "can_cancel": can_change,
        }

    async def _serialize_instance(self, row: dict) -> dict:
        zone_names = await self._zone_names()
        assignees = await self._assignees_of(row, zone_names)
        submitted = await self._submitted_count(int(row["id"]))
        summaries = await self._submission_summaries([int(row["id"])])
        return self._serialize(
            row,
            assignees=assignees,
            zone_names=zone_names,
            submitted=submitted,
            summary=summaries.get(int(row["id"])),
        )

    async def _resigned_instance_ids(self, instance_ids: list[int]) -> set:
        if not instance_ids:
            return set()
        placeholders = ",".join("?" for _ in instance_ids)
        rows = await self._rows(
            "SELECT DISTINCT m.instance_id FROM work_task_instance_members m"
            " JOIN hygiene_employees e ON e.id = m.employee_id"
            f" WHERE m.instance_id IN ({placeholders})"
            "   AND e.resigned_at IS NOT NULL AND e.resigned_at <> ''"
            "   AND substr(e.resigned_at, 1, 10) < ?",
            tuple(instance_ids) + (self._today(),),
        )
        return {int(row["instance_id"]) for row in rows}

    async def _submission_summaries(self, instance_ids: list[int]) -> dict:
        """每个实例最近一条「待验收」提交人，以及最近一次驳回理由。

        列表页要显示「已被 XX 完成」与「驳回原因」，一次查完，避免每张卡两条 SQL。
        """
        if not instance_ids:
            return {}
        placeholders = ",".join("?" for _ in instance_ids)
        rows = await self._rows(
            "SELECT instance_id, submitter_employee_id, submitter_name, status, reject_reason"
            " FROM work_task_submissions"
            f" WHERE instance_id IN ({placeholders})"
            " ORDER BY id ASC",
            tuple(instance_ids),
        )
        out: dict = {}
        for row in rows:
            key = int(row["instance_id"])
            item = out.setdefault(key, {})
            if str(row["status"]) == "pending":
                item["pending_employee_id"] = int(row["submitter_employee_id"])
                item["pending_name"] = str(row["submitter_name"] or "")
            if str(row["status"]) == "rejected":
                reason = str(row.get("reject_reason") or "").strip()
                if reason:
                    item["reject_reason"] = reason
        return out

    # ── 发布 / 编辑 / 撤回 ─────────────────────────────────────────────────
    async def create_definition(self, actor: TaskActor, payload: dict) -> dict:
        data = self._normalize_payload(actor, payload)
        check_date = str(data["due_date"] or data["member_check_date"])
        members: list = []
        if data["assignee_kind"] == "group":
            members = await self._validate_group_members(data["group_ids"], check_date)
        else:
            await self._validate_pool_zone(data["pool_kind"], data["pool_zone_id"])
            # 发布时先按当期排班解析一次，只为返回给前端看；真正的可交名单在提交时再算。
            members = await self._pool_members(data["pool_kind"], data["pool_zone_id"], check_date)
        await self._validate_reference(actor, data["reference_capture_id"])

        now = self._now_iso()
        definition_id = 0
        instance_id = 0
        try:
            definition_rows = await self._rows(
                "INSERT INTO work_task_definitions"
                " (kind, title, body, photo_count, reference_capture_id, assignee_kind,"
                "  pool_kind, pool_zone_id, deadline_hhmm, due_date, frequency_kind,"
                "  weekday, month_day, start_date, end_date, status, wecom_group_push,"
                "  created_by_kind, created_by_employee_id, created_by_name,"
                "  created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)"
                " RETURNING id",
                (
                    data["kind"],
                    data["title"],
                    data["body"],
                    data["photo_count"],
                    data["reference_capture_id"],
                    data["assignee_kind"],
                    data["pool_kind"],
                    data["pool_zone_id"],
                    data["deadline_hhmm"],
                    data["due_date"],
                    data["frequency_kind"],
                    data["weekday"],
                    data["month_day"],
                    data["start_date"],
                    data["end_date"],
                    data["wecom_group_push"],
                    actor.kind,
                    actor.employee_id,
                    actor.name,
                    now,
                    now,
                ),
            )
            definition_id = int(definition_rows[0]["id"])
            if data["assignee_kind"] == "group":
                for index, member in enumerate(members):
                    await self._rows(
                        "INSERT INTO work_task_definition_members"
                        " (definition_id, employee_id, employee_name, sort_order)"
                        " VALUES (?, ?, ?, ?)",
                        (definition_id, member["id"], member["name"], index),
                    )
            if data["kind"] == "once":
                instance_rows = await self._rows(
                    "INSERT INTO work_task_instances"
                    " (definition_id, period_date, title, body, photo_count,"
                    "  reference_capture_id, assignee_kind, pool_kind, pool_zone_id,"
                    "  deadline_at, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo', ?, ?)"
                    " RETURNING id",
                    (
                        definition_id,
                        data["due_date"],
                        data["title"],
                        data["body"],
                        data["photo_count"],
                        data["reference_capture_id"],
                        data["assignee_kind"],
                        data["pool_kind"],
                        data["pool_zone_id"],
                        data["deadline_at"],
                        now,
                        now,
                    ),
                )
                instance_id = int(instance_rows[0]["id"])
                if data["assignee_kind"] == "group":
                    for index, member in enumerate(members):
                        await self._rows(
                            "INSERT INTO work_task_instance_members"
                            " (instance_id, employee_id, employee_name, sort_order)"
                            " VALUES (?, ?, ?, ?)",
                            (instance_id, member["id"], member["name"], index),
                        )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise

        if data["kind"] == "once":
            row = await self._instance_row(instance_id)
            if row is None:  # pragma: no cover - 插入成功后立刻读不到只可能是并发删除
                raise TaskNotFoundError("任务刚刚被删了，请刷新列表")
            result = {
                "definition_id": definition_id,
                "instance": await self._serialize_instance(row),
            }
            warning = await self._push_once(
                definition_id=definition_id,
                period_date=str(data["due_date"]),
                title=str(data["title"]),
                deadline_hhmm=str(data["deadline_hhmm"]),
                assignee_kind=str(data["assignee_kind"]),
                members=members,
                pool_kind=data["pool_kind"],
                pool_zone_id=data["pool_zone_id"],
                push=bool(data["wecom_group_push"]),
            )
            if warning:
                result["warning"] = warning
            return result

        # 定期任务发布后立刻物化一次（含最近 7 天回补），常驻循环负责后续的期。
        await self.materialize_recurring(definition_ids=[definition_id])
        result = {
            "definition_id": definition_id,
            "definition": await self._definition_payload(definition_id),
        }
        warning = await self._push_definition_today(definition_id)
        if warning:
            result["warning"] = warning
        return result

    # ── 企微群推送（票 09） ──────────────────────────────────────────────────
    async def _assignee_text(
        self,
        *,
        assignee_kind: str,
        members: list,
        pool_kind: Optional[str],
        pool_zone_id: Optional[int],
    ) -> str:
        if assignee_kind == "group":
            names = [str(member.get("name") or "") for member in members if member.get("name")]
            return "、".join(names) or "选定的小组"
        label = POOL_LABELS.get(str(pool_kind or ""), "员工池")
        if str(pool_kind) == "zone_on_duty" and pool_zone_id is not None:
            zone = (await self._zone_names()).get(int(pool_zone_id))
            if zone:
                label = f"{label}（{zone}）"
        return label

    async def _push_once(
        self,
        *,
        definition_id: int,
        period_date: str,
        title: str,
        deadline_hhmm: str,
        assignee_kind: str,
        members: list,
        pool_kind: Optional[str],
        pool_zone_id: Optional[int],
        push: bool,
    ) -> Optional[str]:
        """按开关入队一条群消息；返回「没订阅」提示（无需提示时 None）。

        推送失败只记日志、不改任务状态：任务创建不依赖企微通道。
        """
        if not push or self._db is None:
            return None
        try:
            assignee_text = await self._assignee_text(
                assignee_kind=assignee_kind,
                members=members,
                pool_kind=pool_kind,
                pool_zone_id=pool_zone_id,
            )
            outbox_ids = await enqueue_work_task_push(
                self._db,
                definition_id=definition_id,
                period_date=period_date,
                title=title,
                deadline_hhmm=deadline_hhmm,
                assignee_text=assignee_text,
            )
        except Exception:  # noqa: BLE001 - 推送失败不能拦住发布
            logger.warning("工作任务群推送入队失败，任务照常创建", exc_info=True)
            return None
        if outbox_ids:
            return None
        return WECOM_NO_SUBSCRIPTION_WARNING

    async def _push_definition_today(self, definition_id: int) -> Optional[str]:
        """定期任务：只给「今天这一期」入队（回补的过去期不发）。"""
        definition = await self._definition(definition_id)
        if definition is None or not int(definition.get("wecom_group_push") or 0):
            return None
        today = self._today()
        instance = await self._row(
            "SELECT id FROM work_task_instances WHERE definition_id = ? AND period_date = ?",
            (definition_id, today),
        )
        if instance is None:
            return None
        members: list = []
        if str(definition["assignee_kind"]) == "group":
            members = self._members_out(
                await self._group_rows(
                    "work_task_definition_members", "definition_id", definition_id
                )
            )
        return await self._push_once(
            definition_id=definition_id,
            period_date=today,
            title=str(definition["title"]),
            deadline_hhmm=str(definition["deadline_hhmm"]),
            assignee_kind=str(definition["assignee_kind"]),
            members=members,
            pool_kind=definition.get("pool_kind"),
            pool_zone_id=definition.get("pool_zone_id"),
            push=True,
        )

    async def push_recurring_today(self, definition_ids: Optional[list[int]] = None) -> None:
        """常驻循环补出新期后调用：给今天这期的定期任务入队（幂等键防重）。"""
        sql = (
            "SELECT id FROM work_task_definitions"
            " WHERE kind = 'recurring' AND status = 'active' AND wecom_group_push = 1"
        )
        params: tuple = ()
        if definition_ids:
            placeholders = ",".join("?" for _ in definition_ids)
            sql += f" AND id IN ({placeholders})"
            params = tuple(int(item) for item in definition_ids)
        for row in await self._rows(sql, params):
            try:
                await self._push_definition_today(int(row["id"]))
            except Exception:  # noqa: BLE001 - 一轮失败不拦下一轮
                logger.warning("定期任务群推送失败（definition=%s）", row["id"], exc_info=True)

    async def update_definition(self, actor: TaskActor, definition_id: int, payload: dict) -> dict:
        definition = await self._definition(definition_id)
        if definition is None:
            raise TaskNotFoundError("任务不存在")
        if str(definition["kind"]) == "recurring":
            return await self._update_recurring_definition(actor, definition_id, definition, payload)

        instance = await self._row(
            "SELECT * FROM work_task_instances WHERE definition_id = ? AND status <> 'cancelled'"
            " ORDER BY id DESC LIMIT 1",
            (definition_id,),
        )
        if instance is None:
            raise TaskConflictError("cancelled", "任务已撤回，不能再编辑")
        if await self._submitted_count(int(instance["id"])) > 0:
            raise TaskConflictError("has_submissions", "已经有人提交过了，内容不能再改；需要调整请驳回后让员工重交")

        data = self._normalize_payload(actor, payload)
        members: list = []
        if data["assignee_kind"] == "group":
            members = await self._validate_group_members(data["group_ids"], data["due_date"])
        else:
            await self._validate_pool_zone(data["pool_kind"], data["pool_zone_id"])
        await self._validate_reference(actor, data["reference_capture_id"])

        now = self._now_iso()
        try:
            await self._rows(
                "UPDATE work_task_definitions SET"
                " title = ?, body = ?, photo_count = ?, reference_capture_id = ?,"
                " assignee_kind = ?, pool_kind = ?, pool_zone_id = ?,"
                " due_date = ?, deadline_hhmm = ?, wecom_group_push = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    data["title"],
                    data["body"],
                    data["photo_count"],
                    data["reference_capture_id"],
                    data["assignee_kind"],
                    data["pool_kind"],
                    data["pool_zone_id"],
                    data["due_date"],
                    data["deadline_hhmm"],
                    data["wecom_group_push"],
                    now,
                    definition_id,
                ),
            )
            await self._rows(
                "UPDATE work_task_instances SET"
                " title = ?, body = ?, photo_count = ?, reference_capture_id = ?,"
                " assignee_kind = ?, pool_kind = ?, pool_zone_id = ?,"
                " period_date = ?, deadline_at = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    data["title"],
                    data["body"],
                    data["photo_count"],
                    data["reference_capture_id"],
                    data["assignee_kind"],
                    data["pool_kind"],
                    data["pool_zone_id"],
                    data["due_date"],
                    data["deadline_at"],
                    now,
                    int(instance["id"]),
                ),
            )
            await self._rows(
                "DELETE FROM work_task_definition_members WHERE definition_id = ?",
                (definition_id,),
            )
            await self._rows(
                "DELETE FROM work_task_instance_members WHERE instance_id = ?",
                (int(instance["id"]),),
            )
            if data["assignee_kind"] == "group":
                for index, member in enumerate(members):
                    await self._rows(
                        "INSERT INTO work_task_definition_members"
                        " (definition_id, employee_id, employee_name, sort_order)"
                        " VALUES (?, ?, ?, ?)",
                        (definition_id, member["id"], member["name"], index),
                    )
                    await self._rows(
                        "INSERT INTO work_task_instance_members"
                        " (instance_id, employee_id, employee_name, sort_order)"
                        " VALUES (?, ?, ?, ?)",
                        (int(instance["id"]), member["id"], member["name"], index),
                    )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise

        row = await self._instance_row(int(instance["id"]))
        return {"instance": await self._serialize_instance(row)}

    async def _update_recurring_definition(
        self, actor: TaskActor, definition_id: int, definition: dict, payload: dict
    ) -> dict:
        """改定期任务的定义：只影响还没生成的期，已有实例保留快照（ADR 0133）。"""
        if str(definition["status"]) == "stopped":
            raise TaskConflictError("stopped", "这个定期任务已经停用了，不能再改")
        payload = dict(payload or {})
        payload["kind"] = "recurring"
        data = self._normalize_payload(actor, payload)
        members: list = []
        if data["assignee_kind"] == "group":
            members = await self._validate_group_members(data["group_ids"], data["member_check_date"])
        else:
            await self._validate_pool_zone(data["pool_kind"], data["pool_zone_id"])
        await self._validate_reference(actor, data["reference_capture_id"])

        now = self._now_iso()
        try:
            await self._rows(
                "UPDATE work_task_definitions SET"
                " title = ?, body = ?, photo_count = ?, reference_capture_id = ?,"
                " assignee_kind = ?, pool_kind = ?, pool_zone_id = ?,"
                " deadline_hhmm = ?, frequency_kind = ?, weekday = ?, month_day = ?,"
                " start_date = ?, end_date = ?, wecom_group_push = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    data["title"],
                    data["body"],
                    data["photo_count"],
                    data["reference_capture_id"],
                    data["assignee_kind"],
                    data["pool_kind"],
                    data["pool_zone_id"],
                    data["deadline_hhmm"],
                    data["frequency_kind"],
                    data["weekday"],
                    data["month_day"],
                    data["start_date"],
                    data["end_date"],
                    data["wecom_group_push"],
                    now,
                    definition_id,
                ),
            )
            await self._rows(
                "DELETE FROM work_task_definition_members WHERE definition_id = ?",
                (definition_id,),
            )
            if data["assignee_kind"] == "group":
                for index, member in enumerate(members):
                    await self._rows(
                        "INSERT INTO work_task_definition_members"
                        " (definition_id, employee_id, employee_name, sort_order)"
                        " VALUES (?, ?, ?, ?)",
                        (definition_id, member["id"], member["name"], index),
                    )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise
        return {"definition": await self._definition_payload(definition_id)}

    async def stop_definition(self, definition_id: int) -> dict:
        """停用定期任务：以后不再生成新期，历史实例保留。"""
        definition = await self._definition(definition_id)
        if definition is None or str(definition["kind"]) != "recurring":
            raise TaskNotFoundError("定期任务不存在")
        if str(definition["status"]) != "stopped":
            now = self._now_iso()
            await self._rows(
                "UPDATE work_task_definitions SET status = 'stopped', updated_at = ? WHERE id = ?",
                (now, definition_id),
            )
            await self._commit()
        return {"definition": await self._definition_payload(definition_id)}

    async def materialize_recurring(
        self,
        *,
        definition_ids: Optional[list[int]] = None,
        today: Optional[str] = None,
        backfill_days: int = RECURRING_RECENT_PERIODS - 1,
    ) -> int:
        """按频率把最近若干工作台日的期实例补出来（幂等）。

        常驻循环每 30 秒跑一次；发布定期任务时也立即跑一次。`UNIQUE(definition_id,
        period_date)` 兜住重复，重启/补跑不会多生成一份。
        """
        today_date = date.fromisoformat(today or self._today())
        if definition_ids:
            placeholders = ",".join("?" for _ in definition_ids)
            definitions = await self._rows(
                "SELECT * FROM work_task_definitions"
                f" WHERE kind = 'recurring' AND status = 'active' AND id IN ({placeholders})",
                tuple(definition_ids),
            )
        else:
            definitions = await self._rows(
                "SELECT * FROM work_task_definitions WHERE kind = 'recurring' AND status = 'active'"
            )
        if not definitions:
            return 0
        days = sorted(today_date - timedelta(days=offset) for offset in range(backfill_days + 1))
        now = self._now_iso()
        created = 0
        try:
            for definition in definitions:
                for day in days:
                    if not self._frequency_matches(definition, day):
                        continue
                    day_text = day.isoformat()
                    hour, minute = (int(part) for part in str(definition["deadline_hhmm"]).split(":"))
                    deadline_at = datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=CHINA_TZ
                    ).isoformat()
                    rows = await self._rows(
                        "INSERT INTO work_task_instances"
                        " (definition_id, period_date, title, body, photo_count,"
                        "  reference_capture_id, assignee_kind, pool_kind, pool_zone_id,"
                        "  deadline_at, status, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo', ?, ?)"
                        " ON CONFLICT DO NOTHING RETURNING id",
                        (
                            int(definition["id"]),
                            day_text,
                            definition["title"],
                            definition["body"],
                            int(definition["photo_count"]),
                            definition.get("reference_capture_id"),
                            definition["assignee_kind"],
                            definition.get("pool_kind"),
                            definition.get("pool_zone_id"),
                            deadline_at,
                            now,
                            now,
                        ),
                    )
                    if not rows:
                        continue
                    instance_id = int(rows[0]["id"])
                    created += 1
                    if str(definition["assignee_kind"]) == "group":
                        members = await self._group_rows(
                            "work_task_definition_members", "definition_id", int(definition["id"])
                        )
                        for index, member in enumerate(members):
                            await self._rows(
                                "INSERT INTO work_task_instance_members"
                                " (instance_id, employee_id, employee_name, sort_order)"
                                " VALUES (?, ?, ?, ?)",
                                (instance_id, int(member["employee_id"]), member["employee_name"], index),
                            )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise
        return created

    async def recurring_definitions(self) -> dict:
        rows = await self._rows(
            "SELECT * FROM work_task_definitions WHERE kind = 'recurring'"
            " ORDER BY (status = 'active') DESC, updated_at DESC, id DESC"
        )
        return {"items": [await self._serialize_definition(row) for row in rows]}

    async def _definition_payload(self, definition_id: int) -> dict:
        row = await self._definition(definition_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        return await self._serialize_definition(row)

    async def _recent_periods(self, definition: dict) -> list:
        """近 N 期（按频率往回数，不按自然日），带每期状态；前端算完成率。"""
        latest = date.fromisoformat(self._today())
        start_text = str(definition.get("start_date") or "")
        earliest = date.fromisoformat(start_text) if start_text else latest - timedelta(days=370)
        cursor = latest
        days: list[date] = []
        while cursor >= earliest and len(days) < RECURRING_RECENT_PERIODS:
            if self._frequency_matches(definition, cursor):
                days.append(cursor)
            cursor -= timedelta(days=1)
        if not days:
            return []
        days.reverse()
        placeholders = ",".join("?" for _ in days)
        rows = await self._rows(
            "SELECT id, period_date, status FROM work_task_instances"
            f" WHERE definition_id = ? AND period_date IN ({placeholders})",
            (int(definition["id"]), *[day.isoformat() for day in days]),
        )
        by_date = {str(row["period_date"]): row for row in rows}
        recent = []
        for day in days:
            row = by_date.get(day.isoformat())
            recent.append(
                {
                    "period_date": day.isoformat(),
                    "instance_id": int(row["id"]) if row else None,
                    "status": str(row["status"]) if row else None,
                }
            )
        return recent

    async def _serialize_definition(self, row: dict) -> dict:
        zone_names = await self._zone_names()
        assignees: list = []
        if str(row["assignee_kind"]) == "group":
            members = await self._group_rows(
                "work_task_definition_members", "definition_id", int(row["id"])
            )
            assignees = self._members_out(members)
        pool_zone_id = row.get("pool_zone_id")
        recent = await self._recent_periods(row)
        return {
            "id": int(row["id"]),
            "kind": "recurring",
            "title": str(row["title"] or ""),
            "body": str(row["body"] or ""),
            "photo_count": int(row["photo_count"]),
            "reference_capture_id": row.get("reference_capture_id"),
            "reference_image_url": (
                f"/api/tasks/images/{row['reference_capture_id']}"
                if row.get("reference_capture_id")
                else None
            ),
            "assignee_kind": str(row["assignee_kind"]),
            "pool_kind": row.get("pool_kind"),
            "pool_label": POOL_LABELS.get(str(row.get("pool_kind") or ""), ""),
            "pool_zone_id": int(pool_zone_id) if pool_zone_id is not None else None,
            "pool_zone_name": zone_names.get(int(pool_zone_id)) if pool_zone_id is not None else None,
            "assignees": assignees,
            "assignee_names": [member["name"] for member in assignees],
            "deadline_hhmm": str(row["deadline_hhmm"] or ""),
            "frequency_kind": str(row["frequency_kind"] or ""),
            "weekday": row.get("weekday"),
            "month_day": row.get("month_day"),
            "start_date": str(row["start_date"] or ""),
            "end_date": row.get("end_date"),
            "status": str(row["status"]),
            "wecom_group_push": bool(row.get("wecom_group_push")),
            "created_by": {
                "kind": str(row.get("created_by_kind") or ""),
                "name": str(row.get("created_by_name") or ""),
            },
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "recent": recent,
            "recent_passed": sum(1 for item in recent if item["status"] == "passed"),
            "recent_total": len(recent),
        }

    async def reassign_instance(self, actor: TaskActor, instance_id: int, payload: dict) -> dict:
        """把一期还没交的任务改派给另一组人/另一个池；历史提交与期号都不动。"""
        row = await self._instance_row(instance_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        if str(row["status"]) != "todo":
            raise TaskConflictError(
                "not_reassignable", "这个任务现在不能改派：只有还没交的任务能改派"
            )
        assignee_kind, group_ids, pool_kind, pool_zone_id = self._normalize_assignee(
            payload.get("assignee")
        )
        now = self._now_iso()
        try:
            if assignee_kind == "group":
                members = await self._validate_group_members(group_ids, str(row["period_date"]))
            else:
                members = []
                await self._validate_pool_zone(pool_kind, pool_zone_id)
            await self._rows(
                "UPDATE work_task_instances"
                " SET assignee_kind = ?, pool_kind = ?, pool_zone_id = ?, updated_at = ?"
                " WHERE id = ? AND status = 'todo'",
                (assignee_kind, pool_kind, pool_zone_id, now, instance_id),
            )
            await self._rows(
                "DELETE FROM work_task_instance_members WHERE instance_id = ?",
                (instance_id,),
            )
            for position, member in enumerate(members):
                await self._rows(
                    "INSERT INTO work_task_instance_members"
                    " (tenant_id, instance_id, employee_id, employee_name, sort_order)"
                    " VALUES (1, ?, ?, ?, ?)",
                    (instance_id, int(member["id"]), str(member["name"]), position),
                )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise
        fresh = await self._instance_row(instance_id)
        return {"instance": await self._serialize_instance(fresh)}

    async def cancel_instance(self, actor: TaskActor, instance_id: int) -> dict:
        row = await self._instance_row(instance_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        if str(row["status"]) != "todo":
            raise TaskConflictError("not_cancellable", "只有还没人提交的任务能撤回")
        if await self._submitted_count(instance_id) > 0:
            raise TaskConflictError("has_submissions", "已经有人提交过了，不能撤回；请驳回后让员工重交")
        # 发布者才有撤回权；管理端共享账号视为系统发布者，可以撤回任何人发错的任务。
        is_creator = (
            str(row.get("created_by_kind") or "") == "staff"
            and actor.employee_id is not None
            and int(row.get("created_by_employee_id") or 0) == int(actor.employee_id)
        )
        if actor.kind != "admin" and not is_creator:
            raise TaskPermissionError("只有发布者能撤回这个任务")

        now = self._now_iso()
        try:
            await self._rows(
                "UPDATE work_task_instances SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, instance_id),
            )
            await self._rows(
                "UPDATE work_task_definitions SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, int(row["definition_id"])),
            )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise
        fresh = await self._instance_row(instance_id)
        return {"instance": await self._serialize_instance(fresh)}

    # ── 列表 ────────────────────────────────────────────────────────────────
    async def _list_rows(self, where: str, params: tuple, *, order: str, limit: int) -> list:
        return await self._rows(
            "SELECT i.*, d.kind AS definition_kind, d.status AS definition_status,"
            "       d.created_by_kind AS created_by_kind,"
            "       d.created_by_employee_id AS created_by_employee_id,"
            "       d.created_by_name AS created_by_name"
            " FROM work_task_instances i"
            " JOIN work_task_definitions d ON d.id = i.definition_id"
            f" WHERE {where} ORDER BY {order} LIMIT {int(limit)}",
            params,
        )

    async def _serialize_many(self, rows: list) -> list:
        zone_names = await self._zone_names()
        summaries = await self._submission_summaries([int(row["id"]) for row in rows])
        out = []
        for row in rows:
            assignees = await self._assignees_of(row, zone_names)
            instance_id = int(row["id"])
            submitted = await self._submitted_count(instance_id)
            out.append(
                self._serialize(
                    row,
                    assignees=assignees,
                    zone_names=zone_names,
                    submitted=submitted,
                    summary=summaries.get(instance_id),
                )
            )
        return out

    async def board(self, actor: TaskActor, *, counts_only: bool = False) -> dict:
        rows = await self._list_rows(
            "1 = 1", tuple(), order="i.deadline_at ASC", limit=500
        )
        active: list = []
        pending: list = []
        finished: list = []
        now = self._now_dt()
        for row in rows:
            status = str(row["status"])
            if status == "todo":
                active.append(row)
            elif status == "pending":
                pending.append(row)
            else:
                finished.append(row)
        finished = list(reversed(finished[-FINISHED_LIMIT:]))
        counts = {
            "active": len(active),
            "pending_review": len(pending),
            "overdue": sum(
                1
                for row in active
                if (deadline := _parse_iso(row.get("deadline_at"))) is not None and now > deadline
            ),
            "needs_reassign": len(await self._resigned_instance_ids([int(row["id"]) for row in active])),
            "finished": len(finished),
        }
        if counts_only:
            return {
                "groups": {"active": [], "pending": [], "finished": []},
                "counts": counts,
            }
        return {
            "groups": {
                "active": await self._serialize_many(active),
                "pending": await self._serialize_many(pending),
                "finished": await self._serialize_many(finished),
            },
            "counts": counts,
        }

    async def mine(self, employee: dict, *, include_review: bool = False) -> dict:
        employee_id = int(employee.get("id") or 0)
        published_rows = await self._list_rows(
            "d.created_by_kind = 'staff' AND d.created_by_employee_id = ?",
            (employee_id,),
            order="i.id DESC",
            limit=MINE_LIMIT,
        )
        todo_rows = await self._list_rows(
            "i.status IN ('todo', 'pending')",
            tuple(),
            order="i.deadline_at ASC",
            limit=200,
        )
        todo_rows = [row for row in todo_rows if await self._is_assigned(row, employee_id)]
        review_rows: list = []
        if include_review:
            pending_rows = await self._list_rows(
                "i.status = 'pending'", tuple(), order="i.deadline_at ASC", limit=200
            )
            # 「待我验收」只放别人的提交：自己交的那份进得来也验不了，别摆在队列里。
            summaries = await self._submission_summaries([int(row["id"]) for row in pending_rows])
            review_rows = [
                row
                for row in pending_rows
                if (summaries.get(int(row["id"]), {}).get("pending_employee_id") != employee_id)
            ][:MINE_LIMIT]
        todo_items = await self._serialize_many(todo_rows[:MINE_LIMIT])
        for item in todo_items:
            # 「已被我提交、还在等验收」与「已被别人完成」在员工端的措辞不一样。
            item["claimed_by_me"] = (
                item.get("status") == "pending"
                and item.get("pending_submitter_id") == employee_id
            )
        groups = {
            "todo": todo_items,
            "review": await self._serialize_many(review_rows),
            "published": await self._serialize_many(published_rows),
        }
        return {
            "groups": groups,
            "counts": {
                "todo": sum(1 for item in todo_items if item.get("status") == "todo"),
                "review": len(review_rows),
                "published": len(published_rows),
            },
        }

    async def _is_assigned(self, row: dict, employee_id: int) -> bool:
        if str(row["assignee_kind"]) == "group":
            members = await self._group_rows(
                "work_task_instance_members", "instance_id", int(row["id"])
            )
            return any(int(member["employee_id"]) == employee_id for member in members)
        members = await self._pool_members(
            str(row["pool_kind"] or ""), row.get("pool_zone_id"), str(row["period_date"])
        )
        return any(int(member["id"]) == employee_id for member in members)

    async def _submissions_detail(self, instance_id: int) -> list:
        rows = await self._rows(
            "SELECT * FROM work_task_submissions WHERE instance_id = ? ORDER BY id ASC",
            (instance_id,),
        )
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        photos = await self._rows(
            "SELECT submission_id, position, capture_id FROM work_task_submission_photos"
            f" WHERE submission_id IN ({placeholders})"
            " ORDER BY position ASC",
            tuple(ids),
        )
        by_submission: dict = {}
        for photo in photos:
            capture_id = str(photo["capture_id"])
            by_submission.setdefault(int(photo["submission_id"]), []).append(
                {
                    "position": int(photo["position"]),
                    "capture_id": capture_id,
                    "url": f"/api/tasks/images/{capture_id}",
                }
            )
        return [
            {
                "id": int(row["id"]),
                "submitter_id": int(row["submitter_employee_id"]),
                "submitter_name": str(row["submitter_name"] or ""),
                "submitted_at": row.get("submitted_at"),
                "late": bool(row.get("late")),
                "status": str(row["status"]),
                "reviewed_by_name": row.get("reviewed_by_name"),
                "reviewed_at": row.get("reviewed_at"),
                "reject_reason": str(row.get("reject_reason") or ""),
                "photos": by_submission.get(int(row["id"]), []),
            }
            for row in rows
        ]

    async def submit_instance(self, employee: dict, instance_id: int, capture_ids: list) -> dict:
        """员工提交完成照：先交为准，其他人只能看到「已被 XX 完成」。

        校验顺序有讲究：先判「这个任务是不是派给我的」，不是就当不存在（404，别泄露
        别人的任务）；再看状态与照片，最后用条件 UPDATE 抢下 todo→pending，两个人同时
        点提交时只有一个成功。
        """
        employee_id = int(employee.get("id") or 0)
        employee_name = str(employee.get("name") or "")
        row = await self._instance_row(instance_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        if not await self._is_assigned(row, employee_id):
            raise TaskNotFoundError("任务不存在")

        status = str(row["status"])
        if status == "pending":
            summaries = await self._submission_summaries([instance_id])
            who = (summaries.get(instance_id) or {}).get("pending_name") or "其他人"
            raise TaskConflictError("already_submitted", f"这个任务已经由{who}提交了，等验收结果就行")
        if status == "passed":
            raise TaskConflictError("already_passed", "这个任务已经验收通过了，不用再交")
        if status == "cancelled":
            raise TaskNotFoundError("任务不存在")

        wanted = int(row["photo_count"])
        ids = [str(capture_id).strip() for capture_id in capture_ids if str(capture_id).strip()]
        if len(ids) != wanted:
            raise TaskValidationError(f"这个任务要交 {wanted} 张照片，现在是 {len(ids)} 张")
        if len(set(ids)) != len(ids):
            raise TaskValidationError("同一张照片不能交两次，请重新挑")

        placeholders = ",".join("?" for _ in ids)
        images = await self._rows(
            "SELECT capture_id, purpose, owner_kind, owner_employee_id"
            " FROM work_task_images"
            f" WHERE capture_id IN ({placeholders})",
            tuple(ids),
        )
        by_capture = {str(image["capture_id"]): image for image in images}
        for capture_id in ids:
            image = by_capture.get(capture_id)
            if image is None:
                raise TaskValidationError("有照片还没上传成功，请重新拍一张")
            if str(image["purpose"]) != "submission":
                raise TaskValidationError("参考图不能当完成照，请重新拍")
            if image.get("owner_kind") != "staff" or int(image.get("owner_employee_id") or 0) != employee_id:
                raise TaskValidationError("只能提交自己拍的照片")
        used = await self._rows(
            "SELECT capture_id FROM work_task_submission_photos"
            f" WHERE capture_id IN ({placeholders})",
            tuple(ids),
        )
        if used:
            raise TaskValidationError("这张照片已经交给别的任务了，请重新拍")

        deadline = _parse_iso(row.get("deadline_at"))
        late = 1 if deadline is not None and self._now_dt() > deadline else 0
        now = self._now_iso()
        try:
            cursor = await self._conn.execute(
                "UPDATE work_task_instances SET status = 'pending', updated_at = ?"
                " WHERE id = ? AND status = 'todo'",
                (now, instance_id),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise TaskConflictError("already_submitted", "这个任务刚刚被别人提交了，刷新看看")
            cursor = await self._conn.execute(
                "INSERT INTO work_task_submissions"
                " (instance_id, submitter_kind, submitter_employee_id, submitter_name,"
                "  submitted_at, late, status)"
                " VALUES (?, 'staff', ?, ?, ?, ?, 'pending') RETURNING id",
                (instance_id, employee_id, employee_name, now, late),
            )
            submission = await cursor.fetchone()
            submission_id = int(submission["id"])
            for position, capture_id in enumerate(ids):
                await self._conn.execute(
                    "INSERT INTO work_task_submission_photos"
                    " (submission_id, position, capture_id, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (submission_id, position, capture_id, now),
                )
            await self._conn.execute(
                "UPDATE work_task_instances SET pending_submission_id = ? WHERE id = ?",
                (submission_id, instance_id),
            )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise

        fresh = await self._instance_row(instance_id)
        return {"instance": await self._serialize_instance(fresh)}

    # ── 验收（票 04）────────────────────────────────────────────────────────
    async def accept_instance(self, actor: TaskActor, instance_id: int) -> dict:
        """验收通过：实例进「已结束」，本次提交标 passed。"""
        return await self._review_instance(actor, instance_id, accept=True, reason="")

    async def reject_instance(self, actor: TaskActor, instance_id: int, reason: str) -> dict:
        """驳回：理由必填，实例回「待交」，本次提交标 rejected（历史保留）。"""
        return await self._review_instance(actor, instance_id, accept=False, reason=reason)

    async def _review_instance(
        self, actor: TaskActor, instance_id: int, *, accept: bool, reason: str
    ) -> dict:
        row = await self._instance_row(instance_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        if str(row["status"]) != "pending":
            raise TaskConflictError("not_pending", "这个任务现在没有待验收的提交")
        submission = await self._row(
            "SELECT * FROM work_task_submissions"
            " WHERE instance_id = ? AND status = 'pending'"
            " ORDER BY id DESC LIMIT 1",
            (instance_id,),
        )
        if submission is None:
            raise TaskConflictError("not_pending", "这个任务现在没有待验收的提交")
        submitter_id = int(submission["submitter_employee_id"])
        if actor.employee_id is not None and int(actor.employee_id) == submitter_id:
            raise TaskPermissionError("不能验收自己提交的照片，请让别的管理员来验收")

        reason_text = str(reason or "").strip()
        if not accept:
            if not reason_text:
                raise TaskValidationError("驳回要写清原因，员工才知道怎么改")
            if len(reason_text) > REJECT_REASON_MAX:
                raise TaskValidationError(
                    f"驳回原因最多 {REJECT_REASON_MAX} 字，现在 {len(reason_text)} 字"
                )

        now = self._now_iso()
        submission_id = int(submission["id"])
        review_params = (
            actor.kind,
            actor.employee_id,
            actor.name,
            now,
            submission_id,
        )
        try:
            cursor = await self._conn.execute(
                "UPDATE work_task_instances SET status = ?, updated_at = ?"
                " WHERE id = ? AND status = 'pending'",
                ("passed" if accept else "todo", now, instance_id),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise TaskConflictError("already_reviewed", "这个任务刚刚被别人验收了，刷新看看")
            if accept:
                await self._rows(
                    "UPDATE work_task_submissions SET status = 'passed',"
                    " reviewed_by_kind = ?, reviewed_by_employee_id = ?, reviewed_by_name = ?,"
                    " reviewed_at = ? WHERE id = ?",
                    review_params,
                )
            else:
                await self._rows(
                    "UPDATE work_task_submissions SET status = 'rejected', reject_reason = ?,"
                    " reviewed_by_kind = ?, reviewed_by_employee_id = ?, reviewed_by_name = ?,"
                    " reviewed_at = ? WHERE id = ?",
                    (reason_text,) + review_params,
                )
                await self._rows(
                    "UPDATE work_task_instances SET pending_submission_id = NULL WHERE id = ?",
                    (instance_id,),
                )
            await self._commit()
        except Exception:
            await self._rollback_quietly()
            raise

        fresh = await self._instance_row(instance_id)
        return {"instance": await self._serialize_instance(fresh)}

    async def instance_detail(self, actor: TaskActor, instance_id: int, *, can_manage: bool) -> dict:
        row = await self._instance_row(instance_id)
        if row is None:
            raise TaskNotFoundError("任务不存在")
        assigned = actor.employee_id is not None and await self._is_assigned(row, int(actor.employee_id))
        created = (
            actor.employee_id is not None
            and str(row.get("created_by_kind") or "") == "staff"
            and int(row.get("created_by_employee_id") or 0) == int(actor.employee_id)
        )
        if not (can_manage or assigned or created):
            raise TaskNotFoundError("任务不存在")
        item = await self._serialize_instance(row)
        item["submissions"] = await self._submissions_detail(instance_id)
        # 列表里的 can_edit/can_cancel 只按任务状态算；详情要再按「谁在看」收窄：
        # 被指派的员工能看详情，但不能编辑/撤回别人发的任务（撤回后端也只认发布者）。
        can_change = str(row["status"]) == "todo" and await self._submitted_count(instance_id) == 0
        item["can_edit"] = bool(can_change and (can_manage or created))
        item["can_cancel"] = bool(can_change and (created or actor.kind == "admin"))
        # 员工端据此决定显不显示「去完成」；管理端拿到 true 也不影响（它不提交照片）。
        item["can_submit"] = assigned and str(row["status"]) == "todo"
        # 待交（含驳回后回待交）才能改派；待验收/已通过/已撤回都不行。
        item["can_reassign"] = bool(can_manage and str(row["status"]) == "todo")
        # 有任务管理能力的人可以验收，但不能验自己交的那份。
        item["can_review"] = bool(
            can_manage
            and str(row["status"]) == "pending"
            and (
                actor.employee_id is None
                or item.get("pending_submitter_id") != int(actor.employee_id)
            )
        )
        item["claimed_by_me"] = (
            item.get("status") == "pending"
            and actor.employee_id is not None
            and item.get("pending_submitter_id") == int(actor.employee_id)
        )
        return item


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=CHINA_TZ)
