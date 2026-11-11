#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台 HTTP 边界：动作门 + 错误域。

工作台五个模块（考勤 / 加班 / 损耗 / 备货 / 排班）过去各自抄一套「解析身份 → 查能力
→ 403」「错误码 → 中文 + 状态码」的代码，同一个动作在不同模块里说的话、给的状态码
不一样。这一层把两件事收成公共接口（ADR-0134）：

* **动作门**：`require_action(action)` 解析身份（管理端会话 / 员工会话，优先级沿用
  `api.security.require_any_identity_session`）并判动作，产出不可变的
  :class:`WorkbenchActor`；拒绝只会是 401 / 403。另有只解析身份的
  :data:`identity_actor`，以及「仅员工」「仅管理员」两个预设。
* **错误域**：:class:`WorkbenchErrors` 绑定「域名 + 文案表 + 状态覆盖 + 缺省句 +
  填充词表」，把带 `.code` 的领域异常翻成 `HTTPException`。共享的例外状态表住在
  这里，各域只声明自己的覆盖；detail 模板继续用 `str.replace("{}", ...)` 填充。

这一层**只依赖 FastAPI、`api.security` 与 `services.identity.capabilities`**：不认
五个领域 store、不碰数据库、不 import `database` / `main`。它是 HTTP 边界，不是业务层
——动作之外「这条数据归谁」「这一天能不能改」仍由各域自己判。

对外契约（票 02 起冻结，03–06 直接复用）：所有由 :meth:`WorkbenchErrors.http`
产出的拒绝都带 `X-Error-Code: <code>` 头；动作门的 403 分别带
`capability_required` / `admin_only`。401 保持裸 `HTTPException`（不加头），
因为 ADR 只给 403 定了 code。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import Depends, HTTPException

from api.security import (
    require_any_identity_session,
    require_session,
    require_staff_identity,
)
from services.identity.capabilities import (
    CAP_PREP_REVIEW,
    CAP_SCHEDULE,
    CAP_WASTAGE,
    has_cap,
)

logger = logging.getLogger(__name__)

# 没有登记过的 code 的缺省句。各域通常会传自己的那一句（「考勤参数不合法」等）；
# 这句只在域对象没有另给、或旧路径回退过来的防御分支上出现。
DEFAULT_DETAIL = "参数不合法"

# 共享的「例外状态表」：这些 code 在任何域里都不是 400 —— 它们描述的不是参数写错，
# 而是资源状态（还没迁移 / 已封账 / 不是自己的 / 图片没生成）。各域可以在自己的
# status_overrides 里盖掉（考勤逐日写口的 `pending_request` 就是 409 覆盖），
# 但不必为了同一件事在每个模块里再抄一遍。
_EXCEPTION_STATUS = {
    "not_migrated": 503,
    "month_sealed": 409,
    "not_owner": 403,
    "amend_window_closed": 409,
    "images_unavailable": 503,
    "cannot_self_accept": 403,
}


@dataclass(frozen=True)
class WorkbenchActor:
    """一次请求里的工作台身份。

    `caps` 是员工花名册上的能力键集合（管理端为空集）：管理端在本层直接放行，
    不靠「管理员拥有全部能力」这种隐含假设 —— 那会让加一个新能力时管理端悄悄
    多出一项。`session_id` 是**这套 cookie 的会话 id**（管理端与员工端各一套），
    供需要记「谁做的」的模块使用。
    """

    kind: str  # "admin" | "staff"
    employee_id: Optional[int] = None
    name: str = ""
    caps: frozenset[str] = frozenset()
    session_id: Optional[str] = None

    def has_cap(self, key: str) -> bool:
        return has_cap(self.caps, key)


def _actor(identity: dict) -> WorkbenchActor:
    """把 `api.security` 的 `{"kind": ...}` 身份翻成 actor。"""
    if identity.get("kind") == "admin":
        return WorkbenchActor(
            kind="admin",
            name="管理端",
            session_id=identity.get("session_id"),
        )
    employee = identity.get("employee") or {}
    return WorkbenchActor(
        kind="staff",
        employee_id=int(employee["id"]) if employee.get("id") is not None else None,
        name=str(employee.get("name") or ""),
        caps=frozenset(employee.get("admin_caps") or ()),
        session_id=identity.get("session_id"),
    )


@dataclass(frozen=True)
class _ActionRule:
    """动作表的一行：身份档 + 员工侧需要的能力 + 403 的文案与 code。"""

    identity: str  # "any" | "staff" | "admin"
    capability: Optional[str] = None
    denial: str = ""
    code: str = "capability_required"


# 六动作表（spec 硬要求）：身份、能力、403 文案与 `X-Error-Code` 都只在这里声明，
# `require_action` 只查表，不再按动作名分支。`admin_only` 的 403 文案随路由走
# （`label`），其余动作的 `denial` 是唯一来源。
_ACTION_RULES = {
    "schedule_edit": _ActionRule(
        "any",
        CAP_SCHEDULE,
        "只有开了「人事管理」权限的人能改排班与考勤：请找超级管理员在花名册里开通",
    ),
    "overtime_review": _ActionRule(
        # 唯一一档「只认员工会话」的能力动作：店长没有管理端账号，审批面长在员工
        # 手机端（ADR 0134 的裁定 / ADR 0103）。管理端 cookie 在这 8 条上是 401。
        "staff",
        CAP_SCHEDULE,
        "只有开了「人事管理」的人能审批加班登记：请找超级管理员在花名册里开通",
    ),
    "wastage_view_all": _ActionRule(
        "any",
        CAP_WASTAGE,
        "只有开了「损耗分析」权限的人能看全店损耗统计：请找超级管理员在花名册里开通",
    ),
    "prep_review": _ActionRule("any", CAP_PREP_REVIEW, "没有「备货复盘」权限"),
    # 只认管理端会话。无 `label` 时是加班 `/admin` 的 401 形态（员工与匿名都只说
    # 「需要登录」）；带 `label` 时是备货 manager 的 403 形态（员工读到路由那句话）。
    # 两档都不看能力键：员工即使有 cap 也不能过。
    "admin_only": _ActionRule("admin", None, "", "admin_only"),
    # 只认员工会话，`/me` 路由自己取 `employee_id`；没有能力判据，也不会 403。
    "staff_identity": _ActionRule("staff"),
}

_VALID_IDENTITIES = frozenset({"any", "staff", "admin"})

# 加载时校验：identity 是动作门的判据，写错一个字符串不能等到请求进来才发现。
for _name, _rule in _ACTION_RULES.items():
    if _rule.identity not in _VALID_IDENTITIES:
        raise ValueError(f"工作台动作 {_name} 的 identity 非法: {_rule.identity}")
del _name, _rule

# 公开视图（票 07）：动作名与 identity 档。测试从这里取，别再抄一份动作名——
# 抄的那份会漂（考勤测试曾自己维护过一个 `_ACTION_NAMES`）。
ACTION_NAMES: frozenset[str] = frozenset(_ACTION_RULES)
ACTION_IDENTITIES: Mapping[str, str] = {
    name: rule.identity for name, rule in _ACTION_RULES.items()
}


async def _identity_actor(
    identity: dict = Depends(require_any_identity_session),
) -> WorkbenchActor:
    """只解析身份、不判动作：给「登录了就能看」的读面用（损耗 / 备货读面）。"""
    return _actor(identity)


def _capability_gate(rule: _ActionRule):
    """any 档：管理端直接放行，员工要有能力键。"""

    async def _gate(
        identity: dict = Depends(require_any_identity_session),
    ) -> WorkbenchActor:
        actor = _actor(identity)
        if actor.kind == "admin":
            return actor
        if rule.capability is not None and not actor.has_cap(rule.capability):
            raise HTTPException(
                status_code=403,
                detail=rule.denial,
                headers={"X-Error-Code": rule.code},
            )
        return actor

    return _gate


def _staff_gate(rule: _ActionRule):
    """staff 档：只认员工会话（管理端 cookie 也是 401），再过能力门。

    `staff_identity` 是这一档里 `capability=None` 的特例：只解析身份，不判能力。
    加班审批（`overtime_review`）是唯一「只认员工会话 + 能力」的动作 —— 店长就是
    花名册上一个真人加员工会话（ADR 0103）。
    """

    async def _gate(
        identity: dict = Depends(require_staff_identity),
    ) -> WorkbenchActor:
        actor = _actor(identity)
        if rule.capability is not None and not actor.has_cap(rule.capability):
            raise HTTPException(
                status_code=403,
                detail=rule.denial,
                headers={"X-Error-Code": rule.code},
            )
        return actor

    return _gate


def _admin_gate(rule: _ActionRule, label: Optional[str]):
    """admin 档：只放管理端会话，员工即使有相应能力键也过不去。

    两种拒绝形态按现行路由逐字保留：无 `label`（加班 `/admin`）走 `require_session`，
    员工与匿名都 401「需要登录」；带 `label`（备货 manager）先过「任一身份」门，
    员工读 403「员工不能{label}」+ `admin_only`。
    """
    if label is None:

        async def _gate(
            session_id: str = Depends(require_session),
        ) -> WorkbenchActor:
            return WorkbenchActor(
                kind="admin", name="管理端", session_id=session_id
            )

        return _gate

    async def _gate(
        identity: dict = Depends(require_any_identity_session),
    ) -> WorkbenchActor:
        actor = _actor(identity)
        if actor.kind == "admin":
            return actor
        raise HTTPException(
            status_code=403,
            detail=f"员工不能{label}",
            headers={"X-Error-Code": rule.code},
        )

    return _gate


def require_action(action: str, *, label: Optional[str] = None):
    """产出一个动作依赖；拒绝时 401 / 403（见模块 docstring）。

    `label` 只用于 `admin_only` 的 403 变体：备货 manager 那几条路由各自有一句话
    （「员工不能查看天气配置」……），由调用方传进来，本层不维护路由文案表。
    """
    rule = _ACTION_RULES.get(action)
    if rule is None:
        raise ValueError(f"未知的工作台动作: {action}")
    if rule.identity == "admin":
        return _admin_gate(rule, label)
    if label is not None:
        raise ValueError(f"label 只用于 admin_only: {action}")
    if rule.identity == "staff":
        return _staff_gate(rule)
    return _capability_gate(rule)


# 两个预设 + 「只解析身份」的公开名字：03–06 的票面按这三个名字接线。
identity_actor = _identity_actor
staff_actor = require_action("staff_identity")
admin_session_actor = require_action("admin_only")


@dataclass(frozen=True)
class WorkbenchErrors:
    """一个 HTTP 出口的错误翻译域。

    `texts` 是 code → 中文模板（`{}` 由异常 `args[0]` 填）；`status_overrides` 是这个
    域对共享例外表的覆盖；`pending_words` 只给「未决申请」那一类 `{}` 用的词表
    （`leave` → 请假、`swap` → 换班）。
    """

    domain: str
    texts: Mapping[str, str]
    status_overrides: Mapping[str, int] = field(default_factory=dict)
    default_text: str = DEFAULT_DETAIL
    pending_words: Mapping[str, str] = field(default_factory=dict)
    pending_fallback: str = "申请"
    missing_word: str = "？"

    def _status(self, code: str) -> int:
        if code in self.status_overrides:
            return self.status_overrides[code]
        status = _EXCEPTION_STATUS.get(code)
        if status is not None:
            return status
        if code.startswith("unknown_"):
            return 404
        return 400

    def _detail(self, code: str, exc: Any) -> str:
        detail = self.texts.get(code, self.default_text)
        if "{}" not in detail:
            return detail
        raw = exc.args[0] if getattr(exc, "args", ()) else ""
        if code == "pending_request" and self.pending_words:
            filled = self.pending_words.get(str(raw), self.pending_fallback)
        elif raw not in (None, "") and str(raw) != code:
            filled = str(raw)
        else:
            filled = self.missing_word
        return detail.replace("{}", filled)

    def http(self, exc: Any) -> HTTPException:
        """把领域异常翻成 `HTTPException`；**返回**而不抛出，调用方决定 raise。"""
        code = str(getattr(exc, "code", "") or "")
        logger.info("%s被拒: code=%s", self.domain, code)
        return HTTPException(
            status_code=self._status(code),
            detail=self._detail(code, exc),
            headers={"X-Error-Code": code},
        )


def wrap_unhandled(exc: Exception, action: str) -> HTTPException:
    """没有码表的模块用的兜底翻译（备货计划已接线，同原 `_wrap` 的用法）。

    PermissionError → 403、ValueError → 400、其余 → 500 并 `logger.exception`；
    返回而不抛出，调用方 `raise wrap_unhandled(exc, "...")` 即可。
    """
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="只能操作自己档口的备货项")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("%s失败", action)
    return HTTPException(status_code=500, detail=f"{action}失败: {exc}")
