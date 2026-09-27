#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仪容仪表：按**人**、按营业日一张自拍（今天排到班次的人才要拍）。

跟卫生现有的三类活不一样：日常 / 专项 / 整改都挂在**责任区**上，这一项挂在**人**上 ——
判据是「排班今天排到他了」（`staff_assignments` 那天 `shift_id` 非空），经公共层只读入口
`DutyRoster` 读；卫生不 import 排班（DESIGN 决定 3：谁是上游是数据上的事实）。

状态机跟日常一个形状：

    （没行 = 待拍）──交一张──▶ pending ──管理员通过──▶ passed
                                    └──管理员驳回（带原因）──▶ rejected ──重拍──▶ pending

同一天只留一行（`UNIQUE(tenant_id, employee_id, business_date)`）：重拍覆盖「等验收」
或「被驳回」那张；**已经通过的不能再交**（跟日常那条 `already_accepted` 同一口径）。

**没有截止钟点**（用户定的口径）：这一项不催、不产生逾期、不进看板、不推企微 ——
所以这个模块里没有 `overdue` 那一套，也没有时钟配置。

存图与现场拍摄校验借 `HygieneWork` 那两个公开入口（`store_capture` / `require_live_capture`）：
它已经跟 `FileCaptureStore` 与图片变体生成器接好了，这里再连一遍只会多一处要同步的东西。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from database import CHINA_TZ
from services.business_day import business_date_of
from services.hygiene.work import HygieneWork, HygieneWorkError
from services.identity.duty import DutyRoster

__all__ = ["HygieneAttire", "STATUS_TODO", "STATUS_PENDING", "STATUS_PASSED", "STATUS_REJECTED"]

logger = logging.getLogger(__name__)

# 一个人一个营业日一行；「还没拍」在库里是**没有行**，不是一行 `todo`。
STATUS_TODO = "todo"
STATUS_PENDING = "pending"
STATUS_PASSED = "passed"
STATUS_REJECTED = "rejected"

# 管理端列表的排序：要处理的排前面，已经落定的沉下去。
_STATUS_ORDER = {STATUS_PENDING: 0, STATUS_TODO: 1, STATUS_REJECTED: 2, STATUS_PASSED: 3}


class HygieneAttire:
    """仪容仪表的读写。构造拿一个已经接好 deps 的 `HygieneWork`。"""

    def __init__(self, work: HygieneWork, now=None):
        conn = getattr(work, "_conn", None)
        if conn is None:
            raise RuntimeError("HygieneAttire requires an open HygieneWork")
        self._work = work
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))

    # ── 时与日 ──────────────────────────────────────────────────────────

    def today(self) -> str:
        """营业日（06:00 切）——跟卫生其它地方同一口径，不另起一套。"""
        return business_date_of(self._now())

    def _now_iso(self) -> str:
        return self._now().isoformat()

    # ── 判据：今天排到他了吗 ────────────────────────────────────────────

    async def _duty(self, employee_id: int, business_date: str) -> tuple[bool, Optional[int]]:
        """`(今天要不要拍, 排到的那条班次 id)`。

        「要拍」= 排到了**班次**：那天休（`scheduled=True` 但 `shift_id` 为空）与没排到
        （新人还没配规则）都不用拍 —— 这正是需求那句「排班上面除了休假的都要拍」。
        """
        duty = await DutyRoster(self._conn).duty_for(int(employee_id), str(business_date))
        if not duty.get("scheduled") or duty.get("shift_id") is None:
            return False, None
        return True, int(duty["shift_id"])

    # ── 行 ──────────────────────────────────────────────────────────────

    async def _shot(self, employee_id: int, business_date: str) -> Optional[dict]:
        cur = await self._conn.execute(
            """SELECT id, employee_id, business_date, shift_id, status,
                      capture_id, note, created_at, updated_at
                 FROM hygiene_attire_shots
                WHERE employee_id = ? AND business_date = ?""",
            (int(employee_id), str(business_date)),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _shots_for_day(self, business_date: str) -> list[dict]:
        cur = await self._conn.execute(
            """SELECT id, employee_id, business_date, shift_id, status,
                      capture_id, note, created_at, updated_at
                 FROM hygiene_attire_shots
                WHERE business_date = ?""",
            (str(business_date),),
        )
        return [dict(row) for row in await cur.fetchall()]

    # ── 员工侧 ──────────────────────────────────────────────────────────

    async def staff_view(self, employee_id: int) -> dict:
        """员工那一行：今天要不要拍、到哪一步、标准图有没有。

        `required=False` 时前端**整行不显示**（休假的、今天没排到的人不该看见这一项）。
        """
        day = self.today()
        required, shift_id = await self._duty(employee_id, day)
        standard = await self.current_standard()
        shot = await self._shot(employee_id, day) if required else None
        status = (shot or {}).get("status") or STATUS_TODO
        return {
            "business_date": day,
            "required": required,
            "shift_id": shift_id,
            "status": status,
            "note": (shot or {}).get("note"),
            "has_standard": bool(standard),
            # 提交时间只在「交过」之后才有意义（待拍时那一列是上一次重拍的残留，不该显示）。
            "submitted_at": None if status == STATUS_TODO else (shot or {}).get("updated_at"),
        }

    async def submit(self, employee_id: int, capture: Any) -> dict:
        """交一张：必须**今天排到班次**、必须**已经传过标准图**、必须是**现场拍的**。

        已经通过的那张不再接受重拍（跟日常 `already_accepted` 同一条口径：验收过的
        是记录，不能被后来的一张盖掉）。
        """
        day = self.today()
        required, shift_id = await self._duty(employee_id, day)
        if not required:
            raise HygieneWorkError("attire_not_required", "attire_not_required")
        if await self.current_standard() is None:
            # 说清楚是「管理员还没传标准图」，不是一句「参数不对」—— 员工照不了没有的东西拍。
            raise HygieneWorkError("attire_standard_required", "attire_standard_required")

        existing = await self._shot(employee_id, day)
        if existing is not None and existing["status"] == STATUS_PASSED:
            raise HygieneWorkError("already_accepted", "already_accepted")

        data = self._work.require_live_capture(capture)
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id, _generated = await self._work.store_capture(
            data, content_type, require_image=True
        )

        stamp = self._now_iso()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO hygiene_attire_shots
                       (employee_id, business_date, shift_id, status,
                        capture_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(employee_id), day, shift_id, STATUS_PENDING, capture_id, stamp, stamp),
            )
        else:
            await self._conn.execute(
                """UPDATE hygiene_attire_shots
                      SET shift_id = ?, status = ?, capture_id = ?,
                          note = NULL, updated_at = ?
                    WHERE id = ?""",
                (shift_id, STATUS_PENDING, capture_id, stamp, existing["id"]),
            )
        await self._conn.commit()
        return await self.staff_view(employee_id)

    async def pending_capture(self, employee_id: int, business_date: Optional[str] = None) -> Optional[str]:
        """那一张待验收（或刚被驳回）的实拍 capture_id —— 图片端点用它取图。"""
        shot = await self._shot(int(employee_id), str(business_date or self.today()))
        return None if shot is None else shot.get("capture_id")

    # ── 管理员侧 ────────────────────────────────────────────────────────

    async def admin_day(self, business_date: Optional[str] = None) -> dict:
        """某一天：谁要拍、各自到哪一步。

        名单来自**排班**（那天排到班次的人），不是「谁有账号」—— 休假的、今天没排到的
        人不出现在这张表上，管理员看到的待办量就是真实要拍的人数。
        """
        day = str(business_date or self.today())
        duty_map = await DutyRoster(self._conn).duty_map(day)
        required = {
            int(employee_id): duty
            for employee_id, duty in duty_map.items()
            if duty.get("scheduled") and duty.get("shift_id") is not None
        }
        shots = {int(row["employee_id"]): row for row in await self._shots_for_day(day)}
        names = await self._names()

        people = []
        for employee_id, duty in required.items():
            shot = shots.get(employee_id) or {}
            person = names.get(employee_id) or {}
            people.append({
                "employee_id": employee_id,
                "name": person.get("name") or person.get("phone") or f"员工 {employee_id}",
                "job_title": person.get("job_title") or "",
                "shift_id": int(duty["shift_id"]),
                "status": shot.get("status") or STATUS_TODO,
                "note": shot.get("note"),
                "updated_at": shot.get("updated_at"),
                # 有没有图上：管理员列表要缩略图，没交的人那一格留空。
                "has_shot": shot.get("capture_id") is not None,
            })
        people.sort(key=lambda item: (_STATUS_ORDER.get(item["status"], 9), item["employee_id"]))

        standard = await self.current_standard()
        return {
            "business_date": day,
            "has_standard": bool(standard),
            "standard_updated_at": (standard or {}).get("created_at"),
            "counts": {
                "required": len(people),
                "pending": sum(1 for p in people if p["status"] == STATUS_PENDING),
                "passed": sum(1 for p in people if p["status"] == STATUS_PASSED),
                "rejected": sum(1 for p in people if p["status"] == STATUS_REJECTED),
                "todo": sum(1 for p in people if p["status"] == STATUS_TODO),
            },
            "people": people,
        }

    async def _names(self) -> dict[int, dict]:
        """员工 id → 姓名/职位（一次查完，不按人循环）。"""
        from services.hygiene.accounts import EmployeeAccounts

        roster = await EmployeeAccounts(self._conn).list_roster()
        return {
            int(person["id"]): {
                "name": person.get("name"),
                "phone": person.get("phone"),
                "job_title": person.get("job_title"),
            }
            for person in roster
        }

    async def accept(self, employee_id: int, business_date: Optional[str] = None) -> dict:
        """通过这一张。"""
        return await self._decide(employee_id, business_date, STATUS_PASSED, None)

    async def reject(
        self, employee_id: int, note: Any, business_date: Optional[str] = None
    ) -> dict:
        """驳回，并写清哪里不合格 —— 员工要照着这句重拍（跟日常驳回同一条口径）。"""
        text = str(note or "").strip()
        if not text:
            raise HygieneWorkError("attire_note_required", "attire_note_required")
        return await self._decide(employee_id, business_date, STATUS_REJECTED, text)

    async def _decide(
        self,
        employee_id: int,
        business_date: Optional[str],
        status: str,
        note: Optional[str],
    ) -> dict:
        day = str(business_date or self.today())
        shot = await self._shot(int(employee_id), day)
        if shot is None:
            raise HygieneWorkError("attire_not_found", "attire_not_found")
        if shot["status"] != STATUS_PENDING:
            # 只剩「等验收」这一个状态能被处理：重复点一次、或者员工刚撤/刚重拍，
            # 都该回一句人话而不是悄悄改掉。
            raise HygieneWorkError("attire_not_pending", "attire_not_pending")
        stamp = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_attire_shots SET status = ?, note = ?, updated_at = ? WHERE id = ?",
            (status, note, stamp, shot["id"]),
        )
        await self._conn.commit()
        logger.info(
            "hygiene attire %s employee=%s date=%s", status, int(employee_id), day
        )
        return {
            "employee_id": int(employee_id),
            "business_date": day,
            "status": status,
            "note": note,
        }

    # ── 标准图 ──────────────────────────────────────────────────────────

    async def current_standard(self) -> Optional[dict]:
        """最新的标准图（每次上传插一行，读最新的那条 —— 跟日常标准图「新版本是新行」一致）。"""
        cur = await self._conn.execute(
            """SELECT id, capture_id, content_type, markup_json, byte_size, created_at
                 FROM hygiene_attire_standard
                ORDER BY id DESC LIMIT 1"""
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def set_standard(self, capture: Any) -> dict:
        """传/换标准图（管理员）。

        这里**不要求现场拍摄**：标准图是管理员手里那张「照这个样子拍」的样板，跟员工交活
        是两回事 —— 日常标准图那条路也是从上传文件来的。
        """
        data = (capture or {}).get("bytes")
        if not data:
            raise HygieneWorkError("capture_required", "capture_required")
        content_type = (capture.get("content_type") or "image/jpeg").strip()
        capture_id, _generated = await self._work.store_capture(
            data, content_type, require_image=True
        )
        stamp = self._now_iso()
        await self._conn.execute(
            """INSERT INTO hygiene_attire_standard
                   (capture_id, content_type, markup_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (capture_id, content_type, self._work.markup_json_for(capture), stamp),
        )
        await self._conn.commit()
        return await self.current_standard()
