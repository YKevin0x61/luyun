#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理权限开关的契约：前后端十项键名逐字一致，解析 fail-closed。

`services/identity/capabilities.py`（后端）与 `admin-web/src/utils/adminCaps.js`（前端）是
**同一份契约的两半**：线上只走键名。键名一旦对不上，症状都是**静默**的 —— 花名册里勾了、
服务端认不出（`parse_caps` 把认不出的键丢掉，fail-closed），于是"勾了不生效"；反过来前端
认不出后端给的键，就是"生效了但界面不显示"。两种都很难从现象倒推回原因，所以在这里钉死。

另外两条钉的是**迁移口径**：`0015` 的回填值与 `LEGACY_ADMIN_CAPABILITIES` 必须是同一件事
（升级当场行为不变就靠它），以及解析/序列化的稳定性（去重、按声明顺序，便于比对与显示）。

2026-10-06（票 02）又加了两条门，钉的是**「档位」不再是第二个真相来源**：

* `test_permission_label_is_derived_from_the_caps`：库列与开关不一致时，读出来的标签跟
  开关走（有任一项即「管理员」，一项都没有即「普通员工」）—— 界面上不可能再出现
  "标签说管理员、一项开关都没给"这种自相矛盾的行。
* `test_every_wired_capability_is_referenced_by_a_judgment`：每个能力键至少被一处判据引用，
  未接线的必须落在显式白名单里 —— 新增一个键却不接线时，这条会红（把"勾了不生效"从人工
  走查变成自动门）。
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

#: 员工账号读取点那条用例用的账号（跑在 conftest 钉死的测试库上，不碰真库）。
STAFF_PHONE = "13800138007"
STAFF_PASSWORD = "password123"

#: **未接线**的能力键：键与数据面都保留，但服务层没有任何判据引用它们。
#:
#: 逐字写死在这里（**不**从 `capabilities.py` 的分组读）是有意的：新增一个键、忘了接线时，
#: 这份名单会与代码里的分组对不上，测试当场变红，逼出一次显式决定 —— 要么接线（在服务层用
#: `has_cap(actor.get("caps"), CAP_XXX)` 判定），要么把它写进这张白名单，并在票 / ADR 里说明
#: 它为什么现在不生效。来源：`docs/adr/0093` 的十项分组（那七项对应的全是超级管理员在电脑端
#: 的活，员工端没有入口）。
UNWIRED_CAPABILITIES: tuple[str, ...] = (
    "attire",
    "standard",
    "zone",
    "roster",
    "boards",
    "clock",
    "data",
)

#: 判据消费点：卫生的业务层（`_require_reviewer` / `_require_fix_*`）。扫服务层与接口层；
#: `services/identity/capabilities.py` 是键的定义处，不算消费。
CONSUMER_DIRS = ("services", "api")
_CAPABILITIES_MODULE = REPO_ROOT / "services" / "identity" / "capabilities.py"


def _capability_reference_sites() -> dict[str, list[str]]:
    """每个能力键的常量（`CAP_XXX`）在判据消费点出现的文件清单。"""
    sites: dict[str, list[str]] = {key: [] for key in CAPABILITIES}
    for folder in CONSUMER_DIRS:
        for path in sorted((REPO_ROOT / folder).rglob("*.py")):
            if path == _CAPABILITIES_MODULE:
                continue
            text = path.read_text(encoding="utf-8")
            for key in CAPABILITIES:
                if re.search(rf"\bCAP_{key.upper()}\b", text):
                    sites[key].append(str(path.relative_to(REPO_ROOT)))
    return sites


def test_every_wired_capability_is_referenced_by_a_judgment():
    """每个能力键至少被一处判据引用；未接线的必须落在**显式白名单**里。

    「勾了不生效」是最坏的一类缺陷：花名册上多一个勾、服务层一处判据都没有，勾完保存成功、
    员工端零变化，界面也不给提示（2026-10-06 真机实测复现：勾「数据与归档」→ 保存成功 →
    员工端零变化）。这条门把它从人工走查变成自动断言：新增一个能力键却不接线，这里就红。

    只断言**契约是否成立**，不断言判据怎么写：看的是"这个键有没有被判据层用到"，不是
    "它在哪一行、怎么判"。引用处判得对不对（有没有真拦住）由服务层自己的用例管。
    """
    from services.identity.capabilities import (
        STAFF_SIDE_CAPABILITIES,
        SUPERVISOR_ONLY_CAPABILITIES,
    )

    # 白名单与领域分组必须是同一件事：把某项挪出只读组（= 声明它接线了）就得先改这份名单，
    # 而下面的断言又会要求它真的被判据引用。
    assert UNWIRED_CAPABILITIES == SUPERVISOR_ONLY_CAPABILITIES, (
        "显式白名单与 SUPERVISOR_ONLY_CAPABILITIES 不一致："
        f"{UNWIRED_CAPABILITIES} vs {SUPERVISOR_ONLY_CAPABILITIES}"
    )
    wired = tuple(key for key in CAPABILITIES if key not in UNWIRED_CAPABILITIES)
    assert wired == STAFF_SIDE_CAPABILITIES, (
        f"要判据的应当是员工端有执行点的那三项，实际 {wired} vs {STAFF_SIDE_CAPABILITIES}"
    )
    sites = _capability_reference_sites()
    missing = [key for key in wired if not sites[key]]
    assert not missing, (
        "这些能力键没有任何判据引用它们（勾了也不会生效）："
        + "、".join(missing)
        + "。要么在服务层接线（`has_cap(actor.get(\"caps\"), CAP_XXX)`），"
        "要么把它写进本文件顶部的 UNWIRED_CAPABILITIES（显式白名单）。"
    )


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


def test_permission_label_is_derived_from_the_caps():
    """档位标签由开关派生：有任一管理能力即「管理员」，一项都没有即「普通员工」。

    `hygiene_employees.permission` 那一列还在（花名册的下拉照旧写它），但**读出来的标签
    一律跟开关走** —— 库里写着「管理员」、一项开关都没给时，返回的是「普通员工」。两者
    不一致是合法状态，一律以开关为准；界面上因此不会再出现"标签说管理员、一项开关都没给"
    这种自相矛盾的行（2026-10-06 评审 S12）。

    只在**读取点**上断言（花名册与登录返回的 employee），不看派生写在哪一行。
    """
    import asyncio

    from database import DatabaseManager
    from services.identity.accounts import EmployeeAccounts

    async def scenario() -> dict:
        db = DatabaseManager()
        await db.connect()
        try:
            accounts = EmployeeAccounts(db)
            employee = await accounts.register(STAFF_PHONE, STAFF_PASSWORD, "张三")
            await accounts.approve(employee["id"])
            employee_id = employee["id"]
            seen = {}

            async def roster_label() -> str:
                roster = await accounts.list_roster()
                return next(row["permission"] for row in roster if row["id"] == employee_id)

            async def login_label() -> str:
                logged_in = await accounts.login(STAFF_PHONE, STAFF_PASSWORD)
                return logged_in["employee"]["permission"]

            seen["一项都没有"] = await roster_label()
            # 「有任一项」只看有没有，不看那一项能不能用：未接线的那七项也算有管理能力。
            await accounts.set_admin_caps(employee_id, ["data"])
            seen["给了未接线的一项"] = await roster_label()
            await accounts.set_admin_caps(employee_id, ["daily_review"])
            seen["给了员工端真生效的一项"] = await roster_label()
            seen["登录返回的 employee"] = await login_label()
            # 库列与开关不一致（标签「管理员」、开关为空）—— 读出来跟开关走。
            await accounts.set_admin_caps(employee_id, [])
            await accounts.set_permission(employee_id, "管理员")
            seen["库列写管理员、开关为空（花名册）"] = await roster_label()
            seen["库列写管理员、开关为空（登录）"] = await login_label()
            return seen
        finally:
            await db.close()

    seen = asyncio.run(scenario())
    assert seen["一项都没有"] == "普通员工"
    assert seen["给了未接线的一项"] == "管理员"
    assert seen["给了员工端真生效的一项"] == "管理员"
    assert seen["登录返回的 employee"] == "管理员"
    assert seen["库列写管理员、开关为空（花名册）"] == "普通员工"
    assert seen["库列写管理员、开关为空（登录）"] == "普通员工"


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
