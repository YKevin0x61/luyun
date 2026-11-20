# -*- coding: utf-8 -*-
"""判据：谁能判这一份。

卫生域里「这份东西现在归谁判」只有这一处实现 —— 日常检查的通过/驳回/撤销、
专项卫生的通过/驳回、整改单的开单/验收/驳回、仪容仪表的判定。写口调
`require`（判不过就抛 `HygieneWorkError`，错误码与列表里的 reason 是同一个），
列表口调 `review` / `attach` 把结论挂到行上，前端只渲染，不再自己算。

本模块是纯函数：不碰库、不读配置；身份与权限开关由调用方组装成 actor。
判据回答三件事：能不能判（`allowed`）、为什么不能（`reason`）、这一行现在对
这个人意味着什么动作（`action`）。行「还能不能判」是状态机的事，但仍然由这里
统一翻译成 `not_pending` —— 否则前端要为每种行各写一遍状态判断。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, NamedTuple, Optional

from database import CHINA_TZ
from services.hygiene.errors import HygieneWorkError
from services.hygiene.status import (
    ATTIRE_STATUS_PENDING,
    OPENER_SUPER,
    STATUS_FIX_TODO,
    STATUS_PENDING,
)
from services.identity.capabilities import (
    CAP_ATTIRE,
    CAP_DAILY_REVIEW,
    CAP_DEEP_REVIEW,
    CAP_FIX,
    has_cap,
)

logger = logging.getLogger(__name__)

# 判什么
DAILY = "daily"
DEEP = "deep"
FIX_OPEN = "fix_open"
FIX = "fix"
ATTIRE = "attire"

# 这一行现在要求他做什么。前端只做 action -> 文案的映射；「通过/驳回」这一对
# 属于界面结构（同一个面板里两个按钮），不是判据分两次给。
ACT_ACCEPT = "accept"
ACT_REJECT = "reject"
ACT_REVIEW = "review"
ACT_RESHOOT = "reshoot"
ACT_VIEW = "view"

# 为什么不行。这些码就是 `HygieneWorkError.code`，也是前端的文案键。
REASON_FORBIDDEN = "forbidden"
REASON_CANNOT_SELF_ACCEPT = "cannot_self_accept"
REASON_BEFORE_DEADLINE = "before_deadline"
REASON_NOT_PENDING = "not_pending"


class _Rule(NamedTuple):
    cap: Optional[str]          # 需要的开关；None 表示这一档现在只认超级管理员
    allow_opener: bool = False  # 整改单：开单人可判自己开的单（不受开关与时限限制）
    deadline: bool = False      # 整改单：别人要等时限过了才能判
    check_self: bool = False    # 日常 / 专项：不能判自己交的


_RULES = {
    DAILY: _Rule(cap=CAP_DAILY_REVIEW, check_self=True),
    DEEP: _Rule(cap=CAP_DEEP_REVIEW, check_self=True),
    FIX_OPEN: _Rule(cap=CAP_FIX),
    FIX: _Rule(cap=CAP_FIX, allow_opener=True, deadline=True),
    ATTIRE: _Rule(cap=None),
}

# 行处于哪些状态时「还有可判的」。仪容用自己那套英文词。
_PENDING_STATUS = {
    DAILY: STATUS_PENDING,
    DEEP: STATUS_PENDING,
    FIX: STATUS_PENDING,
    ATTIRE: ATTIRE_STATUS_PENDING,
}


def _now(now=None) -> datetime:
    """把「现在」归一成带时区的时刻。调用方手里常常是 ISO 文本（库里的格式）。"""
    if now is None:
        return datetime.now(CHINA_TZ)
    moment = datetime.fromisoformat(str(now)) if isinstance(now, str) else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHINA_TZ)
    return moment


def _deadline(subject: Optional[Mapping[str, Any]]):
    raw = None if subject is None else subject.get("deadline")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        # 时限是开单时写死的；解析不了说明数据坏了。宁可判「还没到」，也别让
        # 别人提前收掉一张单。
        logger.warning("hygiene judge: unparsable fix deadline %r", raw)
        return _now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHINA_TZ)
    return moment


def _is_opener(actor: Mapping[str, Any], subject: Optional[Mapping[str, Any]]) -> bool:
    if subject is None:
        return False
    if subject.get("opener_kind") == OPENER_SUPER:
        return actor.get("kind") == "super"
    if actor.get("kind") != "staff":
        return False
    opener_id = subject.get("opener_id")
    return opener_id is not None and int(actor.get("id") or 0) == int(opener_id)


def who(kind: str, actor: Optional[Mapping[str, Any]], subject=None, now=None):
    """这个人能不能判这一份（只看身份与时间，不看行状态）。"""
    rule = _RULES[kind]
    if not actor or actor.get("kind") not in ("super", "staff"):
        return False, REASON_FORBIDDEN
    is_super = actor.get("kind") == "super"
    if rule.allow_opener and _is_opener(actor, subject):
        return True, None
    if rule.deadline:
        deadline = _deadline(subject)
        if deadline is not None and _now(now) < deadline:
            return False, REASON_BEFORE_DEADLINE
    if is_super:
        return True, None
    if rule.cap is None or not has_cap(actor.get("caps"), rule.cap):
        return False, REASON_FORBIDDEN
    submitter_id = None if subject is None else subject.get("submitter_id")
    if rule.check_self and submitter_id is not None:
        if int(actor.get("id") or 0) == int(submitter_id):
            return False, REASON_CANNOT_SELF_ACCEPT
    return True, None


def review(kind: str, actor: Optional[Mapping[str, Any]], row=None, now=None) -> dict:
    """列表口：一行对这个人意味着什么。"""
    allowed, reason = who(kind, actor, row, now=now)
    pending_status = _PENDING_STATUS.get(kind)
    status = None if row is None else row.get("status")
    if pending_status is not None and status is not None and status != pending_status:
        action = ACT_RESHOOT if kind == FIX and status == STATUS_FIX_TODO else ACT_VIEW
        return {"allowed": False, "reason": REASON_NOT_PENDING, "action": action}
    if not allowed:
        return {"allowed": False, "reason": reason, "action": ACT_VIEW}
    return {"allowed": True, "reason": None, "action": ACT_REVIEW}


def require(kind: str, actor: Optional[Mapping[str, Any]], subject=None, now=None) -> None:
    """写口：判不过就抛，错误码就是理由。"""
    verdict = review(kind, actor, subject, now=now)
    if not verdict["allowed"]:
        raise HygieneWorkError(verdict["reason"], verdict["reason"])

