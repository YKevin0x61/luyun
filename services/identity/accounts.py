#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工身份：花名册、手机号+密码登录、批准、停用/启用、职位、会话。

这是**公共层**：卫生和排班都从这里取「这个人是谁」，但两边都不认识对方的业务。
「他今天上哪个班、在哪个区」不属于这里——业务层覆写 ``_roster_extras`` /
``_session_extras`` 自己补。

Does not know 日常检查 / 专项卫生 / 整改单，也不知道排班。

表名仍是 ``hygiene_employees`` / ``hygiene_staff_sessions``：这次只搬代码、不搬表，
所以现场一个迁移都不用跑。改名的事等以后有理由再说。
"""

from __future__ import annotations

import logging
import asyncio
import functools
import hashlib
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from config import settings
from database import CHINA_TZ
from db_core.errors import is_integrity_violation
from services.identity.capabilities import dump_caps, parse_caps
from services.identity.profile import (
    ProfileError,
    health_cert_status,
    normalize_base_salary,
    normalize_date,
    normalize_health_cert_date,
    normalize_id_card,
    normalize_seniority_bonus,
    profile_incomplete,
    seniority_base_month,
    seniority_due_month,
    seniority_next_adjust_month,
    seniority_should_be,
)
from services import password_hash

logger = logging.getLogger(__name__)

PERMISSION_STAFF = "普通员工"
PERMISSION_ADMIN = "管理员"
ALLOWED_PERMISSIONS = frozenset({PERMISSION_STAFF, PERMISSION_ADMIN})
FORBIDDEN_SUPER_PERMISSION = "超级管理员"

# Mainland China mobile: 11 digits, 1[3-9]...
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# sha256 十六进制摘要的形状。用来识别「这一行还是明文 cookie」。
_SESSION_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# last_seen_at 的刷新节流：员工端每 30s 轮询一次，不节流的话每次请求都要写库。
# 5 分钟的粒度对「闲置多久算失效」完全够用，也把写放大压回可忽略。
LAST_SEEN_REFRESH_SECONDS = 300


def hash_session_id(session_id: str) -> str:
    """cookie 原文 → 入库值。

    库里只存 sha256：拿到 ``app.db`` 或备份的人无法把哈希还原成 cookie，也就无法
    冒充在线员工。cookie 本身仍然发原文，校验时把收到的值哈希后再等值查。
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _parse_stamp(value: Any) -> Optional[datetime]:
    """ISO 时间戳 → 带时区的 datetime；空值或坏值返回 None（不抛）。"""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CHINA_TZ)
    return stamp


def normalize_phone(phone: str) -> Optional[str]:
    """把手机号的常见写法归一成 11 位；不合法返回 None。

    与 ``EmployeeAccounts._normalize_phone`` 同一套规则。独立成模块级函数是为了让
    登录限流也能用它当桶键：``+8613800138000`` / ``138-0013-8000`` / ``8613800138000``
    打的是同一个账号，不归一化的话每种写法各占一个桶，手机号维度 5 次/分钟的上限
    会被轻易绕过——而那一维正是用来挡分布式撞库的。
    """
    digits = re.sub(r"[\s\-]", "", (phone or "").strip())
    if digits.startswith("+86"):
        digits = digits[3:]
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if not _PHONE_RE.match(digits):
        return None
    return digits

MAX_NAME_LENGTH = 40


def serialized_write(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._write_lock:
            try:
                return await method(self, *args, **kwargs)
            except Exception:
                # 与 HygieneWork.serialized_write 同理：异常不能把半截事务留在
                # 连接上。无事务时 rollback 是 no-op。
                await self._conn.rollback()
                raise

    return wrapper


class EmployeeAccountsError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _as_bool(value: Any) -> bool:
    return bool(int(value or 0))


class EmployeeAccounts:
    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("EmployeeAccounts requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _today(self) -> date:
        """**北京时自然日** —— 健康证有效期与「不得晚于今天」都按它算。

        刻意不用营业日（`business_date_of`，06:00 切）：证件上的有效期是日历日，
        员工凌晨看的也是同一个日期。
        """
        return self._now_dt().date()

    def today(self) -> str:
        """同一个日期的 `YYYY-MM-DD` 形式（导出的文件名也用它，好与注入的时钟一致）。"""
        return self._today().isoformat()

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    @serialized_write
    async def prepare(self) -> None:
        """启动期一次性维护：把存量的明文 ``session_id`` 原地哈希化。

        幂等，只处理形状不像 sha256 摘要的行。存量行里存的就是 cookie 原文，所以
        直接对它做 sha256 即可——员工浏览器里的 cookie 还是那个原文，校验时哈希后
        照样匹配，**不需要重新登录**。

        调用点：``main.py`` 启动（与 ``hygiene_work.prepare()`` 并列）。表由
        ``connect()`` 建好；PG 侧由 ``0001`` bootstrap 建好。
        """
        cur = await self._conn.execute("SELECT session_id FROM hygiene_staff_sessions")
        rows = await cur.fetchall()
        stale = [
            str(row[0])
            for row in rows
            if row[0] and not _SESSION_HASH_RE.match(str(row[0]))
        ]
        if not stale:
            return
        for value in stale:
            await self._conn.execute(
                "UPDATE hygiene_staff_sessions SET session_id = ? WHERE session_id = ?",
                (hash_session_id(value), value),
            )
        await self._conn.commit()
        logger.info("hygiene 会话已哈希化 count=%s", len(stale))

    def _normalize_phone(self, phone: str) -> str:
        digits = normalize_phone(phone)
        if digits is None:
            raise EmployeeAccountsError("invalid_phone", "invalid_phone")
        return digits

    def _normalize_name(self, name: str) -> str:
        cleaned = " ".join((name or "").strip().split())
        if not cleaned:
            raise EmployeeAccountsError("invalid_name", "invalid_name")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise EmployeeAccountsError("invalid_name", "invalid_name")
        return cleaned

    @staticmethod
    def _checked(fn, *args):
        """档案字段校验（`services/identity/profile.py`）→ 本层异常族。

        校验实现只此一处；这里只把 `ProfileError` 换成 `EmployeeAccountsError`，
        让接口层每个入口照旧一句 `except EmployeeAccountsError` 就够（与手机号/姓名
        校验同一套），code 逐字保留。
        """
        try:
            return fn(*args)
        except ProfileError as exc:
            raise EmployeeAccountsError(exc.code, exc.code) from exc

    def _employee_from_row(self, row) -> dict:
        return self._project_employee(dict(row))

    def _project_employee(self, mapping: dict, health: Optional[dict] = None) -> dict:
        """身份投影：管理端与员工端**共用**。

        ⚠️ 这里只放"这个人是谁"以及**员工本人也能看到**的档案项（`hire_date` /
        `health_cert_expires_on` / `health_cert_state`）。**敏感项绝不能加进来**：
        `id_card_no` / `base_salary` / `health_cert_date` 只在 :meth:`_roster_row`
        那份管理端投影里下发 —— 本函数同时喂着 `login()` 与 `get_staff_session()`，
        往里加一行就等于把它们发到员工手机上（design §0.3 的红线）。
        """
        # 管理权限开关（2026-10-05）：判据一律看这一组，`permission` 只当人话标签用。
        # 认不出的键会被 `parse_caps` 丢掉（fail-closed）—— 权限宁可少给一项。
        caps = parse_caps(mapping.get("admin_caps"))
        # 健康证：办理日期 + 12 个月，唯一实现在 `services/identity/profile.py`。
        # 这一行的 SELECT 没带 `health_cert_date` 时（老查询）按"没填"渲染。
        status = health or health_cert_status(
            mapping.get("health_cert_date"), self._today()
        )
        return {
            "id": int(mapping["id"]),
            "phone": mapping["phone"],
            "name": mapping.get("name") or "",
            "job_title": mapping.get("job_title") or "",
            # 档位标签**由开关派生**（2026-10-06 起，见 `docs/adr/0093`）：有任一项管理能力就是
            # 「管理员」，一项都没有就是「普通员工」。库里那一列保留（花名册的下拉照旧写它），
            # 但不再是真相来源 —— 两者不一致时一律以开关为准，界面上因此不可能出现
            # "标签说管理员、一项开关都没给"这种自相矛盾的行。
            "permission": PERMISSION_ADMIN if caps else PERMISSION_STAFF,
            "admin_caps": list(caps),
            "approved": _as_bool(mapping["approved"]),
            "disabled": _as_bool(mapping["disabled"]),
            # 员工端「我的」也显示的档案项（非敏感）。空值一律 `None`：
            # 前端那些 `!= null` 的判据（批准门槛、有效期至）靠它。
            "hire_date": mapping.get("hire_date") or None,
            "health_cert_expires_on": status["expires_on"],
            "health_cert_state": status["state"],
            # 工龄奖（2026-10-07，见 `docs/adr/0101`）：`seniority_bonus` 是档案里
            # **落库的当前值**，另两个是按入职日期派生的「应为」值与下次调整月。
            # 员工看得到自己的档位 —— 入职日期本来就在这一份投影里，它不是底薪
            # 那种不下发的东西（0098 的红线是 `id_card_no` / `base_salary` /
            # `health_cert_date`，三个都没动）。派生只走 `profile.py` 一处。
            "seniority_bonus": (
                None
                if mapping.get("seniority_bonus") is None
                else int(mapping["seniority_bonus"])
            ),
            "seniority_should_be": seniority_should_be(
                mapping.get("hire_date"), self._today()
            ),
            "seniority_next_adjust_month": seniority_next_adjust_month(
                mapping.get("hire_date"), self._today()
            ),
        }

    def _roster_row(self, row) -> dict:
        """花名册行 = 身份投影 + **只在管理端下发**的档案字段。

        花名册是管理端专属响应（`require_session`），所以这里可以带身份证号与底薪；
        员工端那份走 `_project_employee`，两条路不会串。
        """
        mapping = dict(row)
        health = health_cert_status(mapping.get("health_cert_date"), self._today())
        employee = self._project_employee(mapping, health)
        employee.update(
            {
                "created_at": mapping.get("created_at") or "",
                "id_card_no": mapping.get("id_card_no") or None,
                "health_cert_date": mapping.get("health_cert_date") or None,
                "base_salary": (
                    None
                    if mapping.get("base_salary") is None
                    else int(mapping["base_salary"])
                ),
                "health_cert_days_left": health["days_left"],
                "profile_incomplete": profile_incomplete(mapping),
            }
        )
        return employee

    async def _fetch_employee(self, employee_id: int):
        cur = await self._conn.execute(
            """SELECT id, phone, name, job_title, permission, admin_caps, approved, disabled,
                      created_at, id_card_no, base_salary, hire_date, health_cert_date,
                      seniority_bonus
               FROM hygiene_employees WHERE id = ?""",
            (employee_id,),
        )
        return await cur.fetchone()

    async def _fetch_employee_by_phone(self, phone: str):
        cur = await self._conn.execute(
            """SELECT id, phone, name, password_hash, job_title, permission, admin_caps,
                      approved, disabled, created_at, id_card_no, base_salary, hire_date,
                      health_cert_date, seniority_bonus
               FROM hygiene_employees WHERE phone = ?""",
            (phone,),
        )
        return await cur.fetchone()

    async def _fetch_employee_by_id_card(self, id_card_no: str):
        cur = await self._conn.execute(
            """SELECT id, phone, name FROM hygiene_employees WHERE id_card_no = ?""",
            (id_card_no,),
        )
        return await cur.fetchone()

    @serialized_write
    async def register(
        self,
        phone: str,
        password: str,
        name: str,
        *,
        id_card_no: Optional[str] = None,
        health_cert_date: Optional[str] = None,
    ) -> dict:
        """员工自助注册。

        两个档案项（身份证号、健康证办理日期）是**注册页的必填项**，但"必填"这一层
        由接口层把住（`StaffRegisterIn` + `_ERROR_DETAILS` 的逐字文案）；这里为空时
        照旧落库成 NULL —— 存量行与测试夹具都走这条路，档案缺项是合法状态（显示
        「待补」、挡在批准门槛前），不是错误。**给了值就必须合法**：形状/校验位
        （`services/identity/profile.py`）与同号唯一都在这里判，注册与 PATCH 共用。
        """
        normalized = self._normalize_phone(phone)
        employee_name = self._normalize_name(name)
        password_hash.validate_password(password)
        id_card = self._checked(normalize_id_card, id_card_no)
        cert_date = self._checked(
            normalize_health_cert_date, health_cert_date, self._today()
        )
        existing = await self._fetch_employee_by_phone(normalized)
        if existing is not None:
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone")
        if id_card is not None and await self._fetch_employee_by_id_card(id_card) is not None:
            raise EmployeeAccountsError("duplicate_id_card", "duplicate_id_card")
        now = self._now_iso()
        hashed = await password_hash.hash_password_async(password)
        try:
            cur = await self._conn.execute(
                """INSERT INTO hygiene_employees
                   (phone, name, password_hash, job_title, permission, approved, disabled,
                    id_card_no, health_cert_date, created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, 0, 0, ?, ?, ?, ?)""",
                (
                    normalized,
                    employee_name,
                    hashed,
                    PERMISSION_STAFF,
                    id_card,
                    cert_date,
                    now,
                    now,
                ),
            )
            await self._conn.commit()
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
            await self._conn.rollback()
            # 预检查在写锁外跑过，两个并发注册还是可能同时挤进来：撞上哪条唯一索引
            # 就报哪件事。按**查库结果**判定而不是按异常类名（`is_integrity_violation`
            # 已经把 SQLite 与 asyncpg 两族都算进来了）。
            if await self._fetch_employee_by_phone(normalized) is not None:
                raise EmployeeAccountsError("duplicate_phone", "duplicate_phone") from exc
            if id_card is not None and await self._fetch_employee_by_id_card(id_card) is not None:
                raise EmployeeAccountsError("duplicate_id_card", "duplicate_id_card") from exc
            raise
        logger.info("hygiene employee registered id=%s", cur.lastrowid)
        row = await self._fetch_employee(cur.lastrowid)
        return self._employee_from_row(row)

    async def login(self, phone: str, password: str, remember: bool = False) -> Optional[dict]:
        try:
            normalized = self._normalize_phone(phone)
        except EmployeeAccountsError:
            return None
        row = await self._fetch_employee_by_phone(normalized)
        if row is None:
            return None
        mapping = dict(row)
        if not await password_hash.verify_password_async(password, mapping["password_hash"]):
            return None
        if not _as_bool(mapping["approved"]) or _as_bool(mapping["disabled"]):
            return None
        if password_hash.needs_rehash(mapping["password_hash"]):
            # legacy 无前缀哈希：验证通过后按现行格式重写，下次登录只需一次
            # bcrypt。老密码可能不满足当前长度策略，此时放弃升级但不阻断登录。
            # 与下面的 session 写入共用同一次 commit。
            try:
                upgraded = await password_hash.hash_password_async(password)
            except ValueError as exc:
                logger.warning(
                    "hygiene legacy hash upgrade skipped id=%s: %s", mapping["id"], exc
                )
            else:
                await self._conn.execute(
                    "UPDATE hygiene_employees SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (upgraded, self._now_iso(), int(mapping["id"])),
                )
        session_id = secrets.token_urlsafe(32)
        now_dt = self._now_dt()
        # 勾了「记住密码，自动登录」的会话活 SESSION_REMEMBER_DAYS 天，否则维持班次级。
        if remember:
            expires_dt = now_dt + timedelta(days=settings.SESSION_REMEMBER_DAYS)
        else:
            expires_dt = now_dt + timedelta(hours=settings.SESSION_TTL_HOURS)
        now = now_dt.isoformat()
        await self._conn.execute(
            """INSERT INTO hygiene_staff_sessions
               (session_id, employee_id, expires_at, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)""",
            (hash_session_id(session_id), mapping["id"], expires_dt.isoformat(), now, now),
        )
        await self._conn.commit()
        employee = self._employee_from_row(mapping)
        return {"session_id": session_id, "employee": employee}

    async def approve(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET approved = 1, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.commit()
        logger.info("hygiene employee approved id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def disable(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET disabled = 1, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.execute(
            "DELETE FROM hygiene_staff_sessions WHERE employee_id = ?",
            (employee_id,),
        )
        await self._conn.commit()
        logger.info("hygiene employee disabled id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def enable(self, employee_id: int) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET disabled = 0, updated_at = ? WHERE id = ?",
            (now, employee_id),
        )
        await self._conn.commit()
        logger.info("hygiene employee enabled id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def _roster_extras(self, employees: list[dict]) -> None:
        """业务层往花名册行里补自己的字段（默认什么也不补）。

        身份层只答「这个人是谁」；「他今天上哪个班、在哪个区」是业务，
        由各自的业务层覆写。排班和卫生都不会在这里认识对方。
        """
        return None

    async def _session_extras(self, employee: dict) -> dict:
        """业务层往员工会话上补自己的字段（默认什么也不补）。"""
        return {}

    async def list_roster(self) -> list[dict]:
        """花名册：每行 = `_roster_row`（含只在管理端下发的档案字段）。

        排序仍是「未停用在前、待批准在前、按 id 升序」；页面自己按分段与姓名重排
        （票面 §2.2 的排序在前端），这里只保证集合与口径。
        """
        cur = await self._conn.execute(
            """SELECT e.id, e.phone, e.name, e.job_title, e.permission, e.admin_caps,
                      e.approved, e.disabled, e.created_at, e.id_card_no, e.base_salary,
                      e.hire_date, e.health_cert_date, e.seniority_bonus
               FROM hygiene_employees e
               ORDER BY e.disabled ASC, e.approved ASC, e.id ASC""",
        )
        rows = await cur.fetchall()
        employees = [self._roster_row(row) for row in rows]
        await self._roster_extras(employees)
        return employees

    async def get_profile_row(self, employee_id: int) -> dict:
        """一个员工的管理端投影（批准门槛这类判据要用 `base_salary` / `hire_date`）。

        跟花名册同一条投影，所以"缺什么算缺"只有一处口径（`profile.py` 的
        `approve_gate` / `profile_incomplete`）。找不到人 → `employee_not_found`（404）。
        """
        row = await self._fetch_employee(int(employee_id))
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        return self._roster_row(row)

    async def list_health_cert_due(self) -> list[dict]:
        """健康证**临期或已过期**的员工（超管首页那块待办）。

        只捞未停用的人（停用的人不用催办），阈值与花名册行标签同源 —— 都走
        `health_cert_status`，所以首页的数字与花名册里能数出来的标签数永远一致。
        每人一行：`{id, name, phone, expires_on, state, days_left}`，按到期日升序
        （最急的在最上面）。
        """
        cur = await self._conn.execute(
            """SELECT id, name, phone, health_cert_date
               FROM hygiene_employees
               WHERE disabled = 0
               ORDER BY id ASC""",
        )
        rows = await cur.fetchall()
        today = self._today()
        due = []
        for row in rows:
            mapping = dict(row)
            status = health_cert_status(mapping.get("health_cert_date"), today)
            if status["state"] not in ("soon", "expired"):
                continue
            due.append(
                {
                    "id": int(mapping["id"]),
                    "name": mapping.get("name") or "",
                    "phone": mapping.get("phone") or "",
                    "expires_on": status["expires_on"],
                    "state": status["state"],
                    "days_left": status["days_left"],
                }
            )
        due.sort(key=lambda item: item["expires_on"])
        return due

    async def list_hr_reminders(self) -> dict:
        """超管「人事提醒」页的清单（票 05：工龄奖该调名单 + 档案待补）。

        2026-10-07 用户裁定，口径见 `docs/adr/0101` / `0102`。**只捞未停用的人**
        （同 :meth:`list_health_cert_due` 的口径：人都停了就不用催）。每块都是
        ``{items, count}`` 的形状 —— 票 06 的生日那一块照这个形状接在同一份响应里。

        * ``seniority``：**现值低于应为**的（含历史欠调）与**现值高于应为**的
          （调过头，只标出来、绝不自动改写）都在列，``state`` 分别是 ``due`` /
          ``over``；现值等于应为的不列 —— 所以「档位到 1000 就不再催」是这条
          通用规则的自然结果，不需要为封顶单开一个分支。``count`` 只数 ``due``：
          首页那一格问的是「要处理几件」。
        * ``incomplete``：入职日期为空**或坏值**的人（本票只这一类）。他们算不出
          工龄奖，进这里点名缺哪一项 —— 「这个月没有要调的」与「算不出来」在界面上
          必须长得不一样（ADR 0102 的教训）。
        """
        cur = await self._conn.execute(
            """SELECT id, name, phone, hire_date, seniority_bonus
               FROM hygiene_employees
               WHERE disabled = 0
               ORDER BY id ASC""",
        )
        rows = await cur.fetchall()
        today = self._today()
        seniority_items: list[dict] = []
        incomplete_items: list[dict] = []
        for row in rows:
            mapping = dict(row)
            hire_date = mapping.get("hire_date") or None
            # 「算不出来」与「还没到时候」是两件事：前者进待补，后者静默。
            if seniority_base_month(hire_date) is None:
                incomplete_items.append(
                    {
                        "id": int(mapping["id"]),
                        "name": mapping.get("name") or "",
                        "phone": mapping.get("phone") or "",
                        "missing": ["hire_date"],
                    }
                )
                continue
            should_be = seniority_should_be(hire_date, today)
            current = (
                None
                if mapping.get("seniority_bonus") is None
                else int(mapping["seniority_bonus"])
            )
            settled = 0 if current is None else current
            if settled == should_be:
                continue
            seniority_items.append(
                {
                    "id": int(mapping["id"]),
                    "name": mapping.get("name") or "",
                    "phone": mapping.get("phone") or "",
                    "hire_date": hire_date,
                    "current": current,
                    "should_be": should_be,
                    "gap": should_be - settled,
                    "state": "due" if settled < should_be else "over",
                    "due_month": seniority_due_month(hire_date, today),
                    "next_adjust_month": seniority_next_adjust_month(hire_date, today),
                }
            )
        # 要处理的在前，组内按「欠自哪个月」升序 —— 欠得最久的在最上面。
        seniority_items.sort(
            key=lambda item: (item["state"] != "due", item["due_month"] or "", item["id"])
        )
        return {
            "seniority": {
                "items": seniority_items,
                "count": sum(1 for item in seniority_items if item["state"] == "due"),
            },
            "incomplete": {"items": incomplete_items, "count": len(incomplete_items)},
        }

    async def set_job_title(self, employee_id: int, title: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        cleaned = (title or "").strip()
        if len(cleaned) > 40:
            raise EmployeeAccountsError("invalid_job_title", "invalid_job_title")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET job_title = ?, updated_at = ? WHERE id = ?",
            (cleaned, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def set_name(self, employee_id: int, name: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        cleaned = self._normalize_name(name)
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET name = ?, updated_at = ? WHERE id = ?",
            (cleaned, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    @serialized_write
    async def update_profile(
        self,
        employee_id: int,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        mapping = dict(row)
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(self._normalize_name(name))
        if phone is not None:
            normalized = self._normalize_phone(phone)
            if normalized != mapping["phone"]:
                existing = await self._fetch_employee_by_phone(normalized)
                if existing is not None and int(dict(existing)["id"]) != int(employee_id):
                    raise EmployeeAccountsError("duplicate_phone", "duplicate_phone")
            fields.append("phone = ?")
            params.append(normalized)
        if not fields:
            raise EmployeeAccountsError("invalid_profile", "invalid_profile")
        fields.append("updated_at = ?")
        params.append(self._now_iso())
        params.append(employee_id)
        try:
            await self._conn.execute(
                f"UPDATE hygiene_employees SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            await self._conn.commit()
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
            await self._conn.rollback()
            raise EmployeeAccountsError("duplicate_phone", "duplicate_phone") from exc
        logger.info("hygiene employee profile updated id=%s", employee_id)
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    async def change_password(
        self,
        employee_id: int,
        current_password: str,
        new_password: str,
        keep_session_id: Optional[str] = None,
    ) -> None:
        cur = await self._conn.execute(
            "SELECT password_hash FROM hygiene_employees WHERE id = ?",
            (int(employee_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        if not await password_hash.verify_password_async(current_password, dict(row)["password_hash"]):
            raise EmployeeAccountsError(
                "invalid_current_password",
                "invalid_current_password",
            )
        password_hash.validate_password(new_password)
        hashed = await password_hash.hash_password_async(new_password)
        await self._conn.execute(
            "UPDATE hygiene_employees SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hashed, self._now_iso(), int(employee_id)),
        )
        if keep_session_id:
            await self._conn.execute(
                """DELETE FROM hygiene_staff_sessions
                   WHERE employee_id = ? AND session_id != ?""",
                (int(employee_id), hash_session_id(keep_session_id)),
            )
        else:
            await self._conn.execute(
                "DELETE FROM hygiene_staff_sessions WHERE employee_id = ?",
                (int(employee_id),),
            )
        await self._conn.commit()
        logger.info("hygiene employee password changed id=%s", employee_id)

    async def set_permission(self, employee_id: int, permission: str) -> dict:
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        value = (permission or "").strip()
        if value == FORBIDDEN_SUPER_PERMISSION or value not in ALLOWED_PERMISSIONS:
            raise EmployeeAccountsError("invalid_permission", "invalid_permission")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET permission = ?, updated_at = ? WHERE id = ?",
            (value, now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    @serialized_write
    async def set_admin_caps(self, employee_id: int, caps) -> dict:
        """整组替换这个人的管理权限开关（花名册里那十个勾，2026-10-05 用户裁定）。

        与 `set_permission` **分开**：那个改的是「普通员工 / 管理员」这一个人话标签
        （显示用），这个改的是他真能做什么。**判据只看这里** —— 见
        `services/identity/capabilities.py` 的说明，别再把两者绑在一起。
        `dump_caps` 会丢掉认不出的键、去重、按声明顺序排，所以库里那一列永远规整。
        """
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        now = self._now_iso()
        await self._conn.execute(
            "UPDATE hygiene_employees SET admin_caps = ?, updated_at = ? WHERE id = ?",
            (dump_caps(caps), now, employee_id),
        )
        await self._conn.commit()
        row = await self._fetch_employee(employee_id)
        return self._employee_from_row(row)

    @serialized_write
    async def update_fields(
        self,
        employee_id: int,
        *,
        name: Optional[str] = None,
        job_title: Optional[str] = None,
        permission: Optional[str] = None,
        admin_caps: Optional[list] = None,
        id_card_no: Optional[str] = None,
        health_cert_date: Optional[str] = None,
        base_salary: Optional[Any] = None,
        hire_date: Optional[str] = None,
        seniority_bonus: Optional[Any] = None,
        changed_by: str = "super",
    ) -> dict:
        """花名册抽屉保存：只 SET **传进来的**列（`None` = 不动这一列）。

        `''`（空串）**是"清空"**：四个档案字段的空值在库里一律是 NULL，接口层把
        JSON 的 `null` 与空串都归一成 `''` 再传进来（见 `api/hygiene.py` 的
        `_profile_updates`）。底薪 `0` 与"没填"是两回事（0 会落成 0）。

        `permission` 仍是独立的一列、`admin_caps` 仍是**整组替换**：这里不做
        permission ↔ caps 的一致性校验或派生写回（那是 ADR 0093 明确留下的现状，
        两者不一致是合法状态）。

        工龄奖（`seniority_bonus`）是**钱**：值真的变了就在同一个事务里补一行
        `seniority_bonus_changes`（谁、何时、从多少到多少）；值没变就一个字都不写。
        `changed_by` 默认 `"super"` —— 眼下只有管理端那个共享账号能改它。它**不进**
        「待补」四项（`profile.py` 的 `PROFILE_FIELDS`），也不挡批准。
        """
        row = await self._fetch_employee(employee_id)
        if row is None:
            raise EmployeeAccountsError("employee_not_found", "employee_not_found")
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(self._normalize_name(name))
        if job_title is not None:
            cleaned_title = (job_title or "").strip()
            if len(cleaned_title) > 40:
                raise EmployeeAccountsError("invalid_job_title", "invalid_job_title")
            fields.append("job_title = ?")
            params.append(cleaned_title)
        if permission is not None:
            value = (permission or "").strip()
            if value == FORBIDDEN_SUPER_PERMISSION or value not in ALLOWED_PERMISSIONS:
                raise EmployeeAccountsError("invalid_permission", "invalid_permission")
            fields.append("permission = ?")
            params.append(value)
        if admin_caps is not None:
            # 整组替换（前端一次提交十个勾的现状）——`dump_caps` 负责去重与排序。
            fields.append("admin_caps = ?")
            params.append(dump_caps(admin_caps))
        if id_card_no is not None:
            value = self._checked(normalize_id_card, id_card_no)
            existing = await self._fetch_employee_by_id_card(value) if value else None
            if existing is not None and int(dict(existing)["id"]) != int(employee_id):
                raise EmployeeAccountsError("duplicate_id_card", "duplicate_id_card")
            fields.append("id_card_no = ?")
            params.append(value)
        if health_cert_date is not None:
            fields.append("health_cert_date = ?")
            params.append(
                self._checked(
                    normalize_health_cert_date, health_cert_date, self._today()
                )
            )
        if base_salary is not None:
            fields.append("base_salary = ?")
            params.append(self._checked(normalize_base_salary, base_salary))
        if hire_date is not None:
            # 入职日期**允许将来**（提前建档），所以只校验日期形状。
            fields.append("hire_date = ?")
            params.append(self._checked(normalize_date, hire_date))
        # 工龄奖：先把归一后的新值与旧值比一比 —— 值没变就不写留痕（一次「保存」
        # 不该平白多出一行历史）。旧值取自这次读到的行。
        bonus_change = None
        if seniority_bonus is not None:
            current = dict(row).get("seniority_bonus")
            old_bonus = None if current is None else int(current)
            new_bonus = self._checked(normalize_seniority_bonus, seniority_bonus)
            fields.append("seniority_bonus = ?")
            params.append(new_bonus)
            if new_bonus != old_bonus:
                bonus_change = (old_bonus, new_bonus)
        if not fields:
            return self._employee_from_row(row)
        fields.append("updated_at = ?")
        params.append(self._now_iso())
        params.append(employee_id)
        try:
            await self._conn.execute(
                f"UPDATE hygiene_employees SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            if bonus_change is not None:
                # 与 UPDATE 同一个事务：钱改了、痕迹没落，两边一起回滚。
                await self._conn.execute(
                    "INSERT INTO seniority_bonus_changes"
                    " (employee_id, old_value, new_value, changed_by, changed_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        int(employee_id),
                        bonus_change[0],
                        bonus_change[1],
                        changed_by,
                        self._now_iso(),
                    ),
                )
            await self._conn.commit()
        except Exception as exc:
            if not is_integrity_violation(exc):
                raise
            await self._conn.rollback()
            # 同号身份证在并发下由局部唯一索引兜底（预检查在写锁外）。
            if id_card_no:
                value = normalize_id_card(id_card_no)
                existing = await self._fetch_employee_by_id_card(value) if value else None
                if existing is not None and int(dict(existing)["id"]) != int(employee_id):
                    raise EmployeeAccountsError(
                        "duplicate_id_card", "duplicate_id_card"
                    ) from exc
            raise
        refreshed = await self._fetch_employee(employee_id)
        return self._employee_from_row(refreshed)

    @serialized_write
    async def _touch_staff_session(self, session_key: str, now_iso: str) -> None:
        """刷新 last_seen_at。

        必须是 ``serialized_write``：``get_staff_session`` 在**每个**员工请求上都会
        跑，如果在锁外 ``commit()``，就会把并发写者（``serialized_write`` 里的显式
        事务）刚写了一半的事务顺手提交掉——``sweep_capture_orphans`` 犯过同样的错。
        """
        await self._conn.execute(
            "UPDATE hygiene_staff_sessions SET last_seen_at = ? WHERE session_id = ?",
            (now_iso, session_key),
        )
        await self._conn.commit()

    async def get_staff_session(self, session_id: Optional[str]) -> Optional[dict]:
        if not session_id:
            return None
        # 库里存的是 sha256，cookie 是原文：哈希后再查。
        # `e.admin_caps` 必须带出来 —— 它是**判据的来源**（`_staff_actor` 把这一组交给
        # 服务层的 `has_cap(...)`），漏掉这一列会让所有管理动作静默变成 403。
        cur = await self._conn.execute(
            """SELECT s.session_id, s.expires_at, s.last_seen_at,
                      e.id, e.phone, e.name, e.job_title, e.permission, e.admin_caps,
                      e.approved, e.disabled, e.hire_date, e.health_cert_date,
                      e.seniority_bonus
               FROM hygiene_staff_sessions s
               JOIN hygiene_employees e ON e.id = s.employee_id
               WHERE s.session_id = ?""",
            (hash_session_id(session_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        mapping = dict(row)
        now_dt = self._now_dt()
        expires_at = _parse_stamp(mapping["expires_at"]) or now_dt
        if now_dt >= expires_at:
            await self.logout(session_id)
            return None
        # 闲置上限：勾了「记住密码」的会话有效期 30 天，这条把「手机一直挂着没人
        # 用」的窗口收窄。在用的会话每次请求都会刷新 last_seen_at，正常使用碰不到。
        idle_hours = int(getattr(settings, "SESSION_IDLE_HOURS", 0) or 0)
        last_seen = _parse_stamp(mapping.get("last_seen_at")) or now_dt
        if idle_hours > 0 and now_dt - last_seen >= timedelta(hours=idle_hours):
            await self.logout(session_id)
            logger.info(
                "hygiene 会话闲置超时已登出 employee_id=%s（阈值 %sh）",
                mapping["id"],
                idle_hours,
            )
            return None
        if (now_dt - last_seen).total_seconds() >= LAST_SEEN_REFRESH_SECONDS:
            await self._touch_staff_session(hash_session_id(session_id), now_dt.isoformat())
        if not _as_bool(mapping["approved"]) or _as_bool(mapping["disabled"]):
            return None
        employee = self._employee_from_row(mapping)
        employee.update(await self._session_extras(employee))
        return employee

    async def logout(self, session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM hygiene_staff_sessions WHERE session_id = ?",
            (hash_session_id(session_id),),
        )
        await self._conn.commit()
