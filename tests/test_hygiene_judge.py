# -*- coding: utf-8 -*-
"""判据的唯一出口：谁能判这一份。

这些用例不碰库、不建连接：判据是纯函数，输入是 actor 与一行（或没有行）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.hygiene import judge
from services.hygiene.errors import HygieneWorkError
from services.hygiene.status import (
    ATTIRE_STATUS_PASSED,
    ATTIRE_STATUS_PENDING,
    ATTIRE_STATUS_REJECTED,
    ATTIRE_STATUS_TODO,
    OPENER_STAFF,
    OPENER_SUPER,
    STATUS_FIX_TODO,
    STATUS_PASSED,
    STATUS_PENDING,
    STATUS_TODO,
)
from services.identity.capabilities import CAP_DAILY_REVIEW, CAP_DEEP_REVIEW, CAP_FIX

SUPER = {"kind": "super"}
DAILY_ADMIN = {"kind": "staff", "id": 2, "caps": [CAP_DAILY_REVIEW]}
DEEP_ADMIN = {"kind": "staff", "id": 3, "caps": [CAP_DEEP_REVIEW]}
FIX_ADMIN = {"kind": "staff", "id": 4, "caps": [CAP_FIX]}
PLAIN = {"kind": "staff", "id": 5, "caps": []}

DEADLINE = "2026-09-13T12:00:00+08:00"
BEFORE = "2026-09-13T10:00:00+08:00"
AFTER = "2026-09-13T13:00:00+08:00"


def _fix(status=STATUS_PENDING, opener_kind=OPENER_STAFF, opener_id=7):
    return {
        "status": status,
        "opener_kind": opener_kind,
        "opener_id": opener_id,
        "deadline": DEADLINE,
    }


class TestWho(object):
    def test_super_can_judge_every_kind(self):
        for kind in (judge.DAILY, judge.DEEP, judge.FIX, judge.FIX_OPEN, judge.ATTIRE):
            assert judge.review(kind, SUPER, None)["allowed"] is True

    def test_daily_needs_its_own_switch(self):
        assert judge.review(judge.DAILY, DAILY_ADMIN, None)["allowed"] is True
        denied = judge.review(judge.DAILY, PLAIN, None)
        assert denied["allowed"] is False
        assert denied["reason"] == judge.REASON_FORBIDDEN

    def test_deep_and_daily_switches_are_not_interchangeable(self):
        assert judge.review(judge.DEEP, DEEP_ADMIN, None)["allowed"] is True
        assert judge.review(judge.DEEP, DAILY_ADMIN, None)["reason"] == judge.REASON_FORBIDDEN

    def test_cannot_judge_own_submission(self):
        verdict = judge.review(judge.DAILY, DAILY_ADMIN, {"submitter_id": 2})
        assert verdict == {"allowed": False, "reason": judge.REASON_CANNOT_SELF_ACCEPT, "action": judge.ACT_VIEW}

    def test_switch_is_checked_before_self(self):
        # 没有开关的人不该从理由里读出「这份是你交的」。
        verdict = judge.review(judge.DAILY, PLAIN, {"submitter_id": 5})
        assert verdict["reason"] == judge.REASON_FORBIDDEN

    def test_missing_actor_is_forbidden(self):
        assert judge.review(judge.DAILY, None, None)["reason"] == judge.REASON_FORBIDDEN

    def test_fix_opener_may_review_own_ticket_without_switch(self):
        subject = _fix(opener_id=5)
        assert judge.review(judge.FIX, PLAIN, subject, now=BEFORE)["allowed"] is True

    def test_fix_others_wait_for_the_deadline(self):
        subject = _fix(opener_id=7)
        early = judge.review(judge.FIX, FIX_ADMIN, subject, now=BEFORE)
        assert early == {"allowed": False, "reason": judge.REASON_BEFORE_DEADLINE, "action": judge.ACT_VIEW}
        assert judge.review(judge.FIX, FIX_ADMIN, subject, now=AFTER)["allowed"] is True

    def test_fix_super_also_waits_unless_he_opened_it(self):
        subject = _fix(opener_id=7)
        assert judge.review(judge.FIX, SUPER, subject, now=BEFORE)["reason"] == judge.REASON_BEFORE_DEADLINE
        assert judge.review(judge.FIX, SUPER, subject, now=AFTER)["allowed"] is True
        assert (
            judge.review(
                judge.FIX, SUPER, _fix(opener_kind=OPENER_SUPER, opener_id=None), now=BEFORE
            )["allowed"]
            is True
        )

    def test_fix_without_switch_still_waits_then_fails(self):
        assert judge.review(judge.FIX, PLAIN, _fix(opener_id=7), now=AFTER)["reason"] == judge.REASON_FORBIDDEN

    def test_fix_open_needs_the_switch(self):
        assert judge.review(judge.FIX_OPEN, FIX_ADMIN, None)["allowed"] is True
        assert judge.review(judge.FIX_OPEN, PLAIN, None)["reason"] == judge.REASON_FORBIDDEN

    def test_attire_is_super_only_for_now(self):
        # Q29：仪容这次不放权。给员工勾了 attire 也仍然是超管判。
        assert judge.review(judge.ATTIRE, SUPER, None)["allowed"] is True
        assert judge.review(judge.ATTIRE, {"kind": "staff", "id": 9, "caps": ["attire"]}, None)["reason"] == judge.REASON_FORBIDDEN


class TestRowVerdict(object):
    def test_pending_row_allowed_is_a_review_action(self):
        verdict = judge.review(judge.DAILY, DAILY_ADMIN, {"status": STATUS_PENDING, "submitter_id": 1})
        assert verdict == {"allowed": True, "reason": None, "action": judge.ACT_REVIEW}

    def test_pending_row_denied_still_offers_a_view(self):
        verdict = judge.review(judge.DAILY, PLAIN, {"status": STATUS_PENDING, "submitter_id": 1})
        assert verdict == {"allowed": False, "reason": judge.REASON_FORBIDDEN, "action": judge.ACT_VIEW}

    def test_settled_daily_row_is_not_pending(self):
        for status in (STATUS_TODO, STATUS_PASSED):
            verdict = judge.review(judge.DAILY, DAILY_ADMIN, {"status": status, "submitter_id": 1})
            assert verdict == {"allowed": False, "reason": judge.REASON_NOT_PENDING, "action": judge.ACT_VIEW}

    def test_void_and_restore_rows_are_states_of_their_own(self):
        # 「待回拍」不是待判的行：主动作是回拍，不是验收。
        verdict = judge.review(judge.FIX, FIX_ADMIN, _fix(status=STATUS_FIX_TODO), now=AFTER)
        assert verdict == {"allowed": False, "reason": judge.REASON_NOT_PENDING, "action": judge.ACT_RESHOOT}

    def test_attire_rows_use_attire_vocabulary(self):
        pending = judge.review(judge.ATTIRE, SUPER, {"status": ATTIRE_STATUS_PENDING})
        assert pending == {"allowed": True, "reason": None, "action": judge.ACT_REVIEW}
        for status in (ATTIRE_STATUS_TODO, ATTIRE_STATUS_PASSED, ATTIRE_STATUS_REJECTED):
            verdict = judge.review(judge.ATTIRE, SUPER, {"status": status})
            assert verdict["reason"] == judge.REASON_NOT_PENDING


class TestRequire(object):
    def test_raises_the_reason_as_the_error_code(self):
        with pytest.raises(HygieneWorkError) as denied:
            judge.require(judge.DAILY, PLAIN, None)
        assert denied.value.code == judge.REASON_FORBIDDEN

    def test_self_accept_keeps_its_own_code(self):
        with pytest.raises(HygieneWorkError) as denied:
            judge.require(judge.DAILY, DAILY_ADMIN, {"submitter_id": 2})
        assert denied.value.code == judge.REASON_CANNOT_SELF_ACCEPT

    def test_before_deadline_is_its_own_code(self):
        with pytest.raises(HygieneWorkError) as denied:
            judge.require(judge.FIX, SUPER, _fix(opener_id=7), now=BEFORE)
        assert denied.value.code == judge.REASON_BEFORE_DEADLINE

    def test_passes_quietly_when_allowed(self):
        assert judge.require(judge.DAILY, DAILY_ADMIN, {"submitter_id": 1}) is None


class TestRowAndWriteDoorAgree(object):
    """同一份规则表：列表口给的结论与写口放不放行，一格都不许分叉。

    2026-10-05 审查 F-04 的病灶就是两处各判一次；这张表跑遍 (kind, actor, 时刻) 的
    组合，任何一格出现「列表说不能判、写口却放行」立刻红。行都用各自的待验收态 ——
    写口在调用判据之前已经 fetch 过 pending（那是状态机的事，不是判据的事）。
    """

    ROWS = {
        judge.DAILY: {"status": STATUS_PENDING, "submitter_id": 1},
        judge.DEEP: {"status": STATUS_PENDING, "submitter_id": 1},
        judge.FIX: {
            "status": STATUS_PENDING,
            "opener_kind": OPENER_STAFF,
            "opener_id": 7,
            "deadline": DEADLINE,
        },
        judge.ATTIRE: {"status": ATTIRE_STATUS_PENDING},
    }
    ACTORS = (SUPER, DAILY_ADMIN, DEEP_ADMIN, FIX_ADMIN, PLAIN)

    def test_list_verdict_and_write_door_never_disagree(self):
        for kind, row in self.ROWS.items():
            for actor in self.ACTORS:
                for now in (BEFORE, AFTER):
                    verdict = judge.review(kind, actor, row, now=now)
                    try:
                        judge.require(kind, actor, row, now=now)
                    except HygieneWorkError as exc:
                        assert verdict["allowed"] is False, (kind, actor, now)
                        assert verdict["reason"] == exc.code, (kind, actor, now)
                    else:
                        assert verdict["allowed"] is True, (kind, actor, now)
                        assert verdict["action"] == judge.ACT_REVIEW, (kind, actor, now)

REPO = Path(__file__).resolve().parents[1]
HYGIENE = REPO / "services" / "hygiene"


def _modules():
    return sorted(path for path in HYGIENE.glob("*.py") if path.name != "judge.py")


class TestSourceGuard(object):
    """判据只能有一份：别的模块再写一次就算回归。"""

    def test_no_module_outside_judge_calls_has_cap(self):
        offenders = []
        for path in _modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "has_cap":
                    offenders.append("%s:%d" % (path.name, node.lineno))
        assert offenders == []

    def test_no_module_outside_judge_imports_a_decision_switch(self):
        watched = {"CAP_DAILY_REVIEW", "CAP_DEEP_REVIEW", "CAP_FIX", "CAP_ATTIRE"}
        offenders = []
        for path in _modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in watched:
                            offenders.append("%s:%d %s" % (path.name, node.lineno, alias.name))
        assert offenders == []

    def test_the_old_guard_methods_are_gone(self):
        banned = {"_require_reviewer", "_require_fix_opener", "_is_fix_opener", "_require_fix_reviewer"}
        found = []
        for path in HYGIENE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in banned:
                    found.append("%s:%d %s" % (path.name, node.lineno, node.name))
        assert found == []
