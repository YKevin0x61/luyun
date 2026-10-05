#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理权限开关的契约：前后端十项键名逐字一致，解析 fail-closed。

`services/identity/capabilities.py`（后端）与 `admin-web/src/utils/adminCaps.js`（前端）是
**同一份契约的两半**：线上只走键名。键名一旦对不上，症状都是**静默**的 —— 花名册里勾了、
服务端认不出（`parse_caps` 把认不出的键丢掉，fail-closed），于是"勾了不生效"；反过来前端
认不出后端给的键，就是"生效了但界面不显示"。两种都很难从现象倒推回原因，所以在这里钉死。

另外两条钉的是**迁移口径**：`0015` 的回填值与 `LEGACY_ADMIN_CAPABILITIES` 必须是同一件事
（升级当场行为不变就靠它），以及解析/序列化的稳定性（去重、按声明顺序，便于比对与显示）。
"""

import json
import pathlib
import re

from services.identity.capabilities import (
    CAPABILITIES,
    CAPABILITY_LABELS,
    LEGACY_ADMIN_CAPABILITIES,
    dump_caps,
    has_cap,
    parse_caps,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "admin-web" / "src" / "utils" / "adminCaps.js"
MIGRATION = REPO_ROOT / "migrations" / "pg" / "0015_hygiene_employee_admin_caps.sql"


def test_frontend_keys_match_backend_exactly():
    """十项键名与顺序逐字相同 —— 这是前后端唯一共享的东西。"""
    src = FRONTEND.read_text(encoding="utf-8")
    keys = re.findall(r"key:\s*'([a-z_]+)'", src)
    assert tuple(keys) == CAPABILITIES, (
        "前端 adminCaps.js 的键名/顺序与后端 capabilities.py 不一致："
        f"前端 {keys} vs 后端 {list(CAPABILITIES)}"
    )
    # 每个键都要有中文标签，否则花名册上会出现一个没有名字的开关。
    # （不假定前端怎么写：`ADMIN_CAP_DEFS` 数组派生也好、逐键映射也好，键出现过就行。）
    for key in CAPABILITIES:
        assert CAPABILITY_LABELS.get(key), f"{key} 缺中文标签"
        assert key in src, f"前端 adminCaps.js 里找不到 {key}"


def test_parse_is_fail_closed():
    """认不出的键丢掉、坏数据当空 —— 权限宁可少给一项，也不能因为格式坏了就放行。"""
    assert parse_caps(None) == ()
    assert parse_caps("") == ()
    assert parse_caps("not json") == ()
    assert parse_caps('{"a": 1}') == ()
    assert parse_caps('["daily_review", "不存在的键"]') == ("daily_review",)
    assert parse_caps('[1, null, true]') == ()
    # 顺序按声明归一，不按输入顺序 —— 两边比对才稳定。
    assert parse_caps('["fix", "daily_review"]') == ("daily_review", "fix")


def test_dump_is_stable_and_round_trips():
    """写回库里的形状：去重、只留认识的键、按声明顺序。"""
    assert dump_caps(None) == "[]"
    assert dump_caps([]) == "[]"
    assert dump_caps(["fix", "fix", "daily_review", "bogus"]) == '["daily_review", "fix"]'
    # 往返一致：库里读出来再写回去形状不变（不然每次保存都在改数据）。
    for raw in ('["daily_review"]', "[]", '["fix", "boards"]'):
        assert dump_caps(parse_caps(raw)) == raw


def test_has_cap_reads_both_shapes():
    assert has_cap(("daily_review",), "daily_review") is True
    assert has_cap(["fix"], "daily_review") is False
    assert has_cap(None, "fix") is False
    assert has_cap(("fix",), "") is False


def test_migration_backfill_matches_the_legacy_tier():
    """迁移 `0015` 的回填 = 升级前那个「管理员」档位，逐字对齐。

    改 `LEGACY_ADMIN_CAPABILITIES` 而忘了改迁移，会让已经升级过的门店和新装的门店
    拿到不同的默认权限 —— 那种差异事后极难查，所以在这里比对。
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"SET admin_caps = '(\[[^']*\])'", sql)
    assert match, "迁移里找不到回填 admin_caps 的那一句"
    backfilled = json.loads(match.group(1))
    assert tuple(backfilled) == LEGACY_ADMIN_CAPABILITIES
    # 回填只该给「管理员」那一档，普通员工必须留空（否则等于给所有人放权）。
    assert "permission = '管理员'" in sql


def test_frontend_checkbox_group_is_exactly_the_staff_side_caps():
    """花名册上**可勾**的那一组，必须与后端 `STAFF_SIDE_CAPABILITIES` 逐字同序。

    这一条钉的是「**可勾面 == 生效面**」。2026-10-06 的真机实测之前，十项并排画在花名册上，
    其中七项在员工端根本没有执行点：超管勾「数据与归档」→ 保存成功 → 员工端零变化，而界面
    一句提示都没有（最坏的一类缺陷：静默无效）。现在可勾的只剩员工端真有落点的三项，其余
    七项降级成只读说明 —— 若哪天有人把某项的 `staffSide` 打开却没接后端判据，这里会红。
    """
    from services.identity.capabilities import (
        STAFF_SIDE_CAPABILITIES,
        SUPERVISOR_ONLY_CAPABILITIES,
    )

    src = FRONTEND.read_text(encoding="utf-8")
    assert "ADMIN_CAP_STAFF_DEFS = ADMIN_CAP_DEFS.filter((item) => item.staffSide)" in src, (
        "adminCaps.js 里 ADMIN_CAP_STAFF_DEFS 的派生被改动了：花名册的可勾面靠它"
    )
    staff_side = tuple(re.findall(r"key:\s*'([a-z_]+)'[^}]*staffSide:\s*true", src))
    supervisor_only = tuple(re.findall(r"key:\s*'([a-z_]+)'[^}]*staffSide:\s*false", src))
    assert staff_side == STAFF_SIDE_CAPABILITIES, (
        f"前端可勾的三项 {staff_side} 与后端 {STAFF_SIDE_CAPABILITIES} 不一致"
    )
    assert supervisor_only == SUPERVISOR_ONLY_CAPABILITIES, (
        f"前端只读的七项 {supervisor_only} 与后端 {SUPERVISOR_ONLY_CAPABILITIES} 不一致"
    )
    # 两组刚好把十项分完，不重不漏。
    assert set(staff_side) | set(supervisor_only) == set(CAPABILITIES)
    assert not set(staff_side) & set(supervisor_only)
