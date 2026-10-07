#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送内容类型注册表与 `/meta`（票 02）。

缝隙（spec「Testing Decisions」第 3 条）：注册表输出的 schema + uischema 可被消费，
以及 `/meta` 这条 API。断言的是**能不能被前端拿去渲染**——字段名、控件类型、必填项、
下拉选项；不测 uischema 是怎么推导出来的这类内部实现。

注册表 id 与迁移 ``0016_wecom_push_subscriptions.sql`` 里那批字面量必须一致：
订阅与出站行按 topic_id 关联，改名字而不同步迁移 = 订阅与内容类型对不上。

触发方式是**多值**：对账差异告警同时支持定时与事件——定时侧承载现有的「数据质量日报」
任务（22:10 / 当天，票 08 的定时任务表单必须能选到它），事件侧是日终对账差异告警。
其余七类各自只有一种触发方式。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from api.security import verify_admin_token
from api.wecom_push import router as wecom_push_router
from config import KITCHEN_STATIONS
from services.wecom_push_service import WECOM_TEXT_BYTE_LIMIT, validate_schedule_time
from services.wecom_push_topics import (
    TOPIC_MANUAL_SEND,
    TOPIC_TEST_MESSAGE,
    PushTopic,
    PushTrigger,
    all_topics,
    get_topic,
    register_topic,
    topic_display_name,
    unregister_topic,
    validate_params,
)


class FakeOpsParams(BaseModel):
    """夹具用的假内容类型参数：证明「新加一类只改后端」这件事。"""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(
        "warn",
        title="等级",
        json_schema_extra={
            "x-ui-control": "select",
            "oneOf": [{"const": "warn", "title": "告警"}, {"const": "recover", "title": "恢复"}],
        },
    )


# 八类内容类型：id / 显示名 / 触发方式（按声明顺序）。来源是票面与迁移 0016 的头注释。
# 只有对账差异告警两种触发方式都支持。
EXPECTED_TOPICS = [
    ("sales_report", "销售报表", ("scheduled",)),
    ("reconcile_diff", "对账差异告警", ("scheduled", "event")),
    ("unmapped_dish", "未映射菜品提醒", ("event",)),
    ("scraper_failure", "采集失败告警", ("event",)),
    ("hygiene_reminder", "卫生提醒", ("event",)),
    ("hygiene_photo", "验收照片", ("event",)),
    ("update_backup", "系统更新与备份结果", ("event",)),
    ("resource_watermark", "磁盘与内存水位", ("event",)),
]

TOPIC_IDS = [row[0] for row in EXPECTED_TOPICS]
EVENT_ONLY_TOPICS = [row[0] for row in EXPECTED_TOPICS if row[2] == ("event",)]

# 定时类（支持定时触发的内容类型）的默认推送时间。与 `/meta` 旧字段 job_templates 里
# 那两个模板逐字一致：销售报表 21:30、数据质量日报（= 对账差异告警的定时侧）22:10。
EXPECTED_SCHEDULE_TIMES = {"sales_report": "21:30", "reconcile_diff": "22:10"}
SCHEDULED_TOPICS = list(EXPECTED_SCHEDULE_TIMES)

# 每类的参数：字段名 → (控件类型, 是否必填)。事件类的参数由触发点产出，注册表不声明
# 表单字段（照片是例外：它的参数形状已经被迁移里那批出站行的 params_json 定死了；
# 对账差异告警是两种触发方式都有，表单形状来自它的定时侧）。
EXPECTED_PARAMS = {
    "sales_report": {
        "schedule_time": ("time", False),
        "date_range_mode": ("select", False),
        "station": ("select", False),
    },
    "reconcile_diff": {
        "schedule_time": ("time", False),
        "date_range_mode": ("select", False),
    },
    "hygiene_photo": {
        "ref_key": ("text", True),
        "capture_id": ("text", True),
        "extra_capture_id": ("text", False),
    },
    "unmapped_dish": {},
    "scraper_failure": {},
    "hygiene_reminder": {},
    "update_backup": {},
    "resource_watermark": {},
}

CONTROL_TYPES = {"time", "select", "text"}


def _payload(topic_id: str) -> dict:
    topic = get_topic(topic_id)
    assert topic is not None, f"注册表里没有 {topic_id}"
    return topic.payload()


def _controls(payload: dict) -> dict:
    """把 uischema 摊成 {字段名: 控件类型}，供逐类断言。"""
    return {
        element["scope"].rsplit("/", 1)[-1]: element["options"]["control"]
        for element in payload["uischema"]["elements"]
    }


def _one_of(payload: dict, field: str) -> dict:
    """某个下拉字段的 {值: 标签} —— 前端渲染下拉用的就是这份数据。"""
    return {
        entry["const"]: entry["title"]
        for entry in payload["params_schema"]["properties"][field]["oneOf"]
    }


def test_a_topic_must_declare_a_params_model_for_every_trigger_it_claims():
    """注册一行时自证：声明了某种触发方式却没有对应的参数模型，等于 /meta 给不出表单。"""
    schedule_model = get_topic("sales_report").schedule_params_model

    with pytest.raises(ValueError):  # 声明定时触发，却没有定时侧模型
        PushTopic(
            id="broken_scheduled",
            name="缺模型",
            triggers=frozenset({PushTrigger.SCHEDULED}),
        )
    with pytest.raises(ValueError):  # 只有事件触发，却带了定时侧模型
        PushTopic(
            id="broken_event",
            name="多模型",
            triggers=frozenset({PushTrigger.EVENT}),
            schedule_params_model=schedule_model,
        )
    with pytest.raises(ValueError):  # 一种触发方式都没有的内容类型推不出去
        PushTopic(id="broken_none", name="无触发", triggers=frozenset())
    with pytest.raises(ValueError):  # id 是订阅与出站行的关联键，不能空
        PushTopic(
            id="",
            name="无 id",
            triggers=frozenset({PushTrigger.EVENT}),
            event_params_model=FakeOpsParams,
        )


def test_only_the_photo_topic_is_marked_as_carrying_employee_photos():
    """票 06：「含员工实拍照片」这一行标注的判据在注册表里，不在页面里。

    标错方向的代价不对称：漏标会让店长把员工照片订到不该去的群（用户故事 11），
    多标只是少订一个群。所以这里正反两面都钉住。
    """
    marked = [topic.id for topic in all_topics() if topic.contains_employee_photos]

    assert marked == ["hygiene_photo"]
    assert _payload("hygiene_photo")["contains_employee_photos"] is True
    assert _payload("hygiene_reminder")["contains_employee_photos"] is False


def test_registry_declares_the_eight_topics_in_order():
    assert [(t.id, t.name) for t in all_topics()] == [
        (row[0], row[1]) for row in EXPECTED_TOPICS
    ]


@pytest.mark.parametrize("topic_id,name,triggers", EXPECTED_TOPICS)
def test_each_topic_declares_its_triggers(topic_id, name, triggers):
    """触发方式按类逐个钉住：只有对账差异告警是「定时 + 事件」。"""
    topic = get_topic(topic_id)
    assert topic is not None, f"注册表里没有 {topic_id}"
    assert topic.name == name
    assert {trigger.value for trigger in topic.triggers} == set(triggers)
    # 输出顺序必须稳定（定时在前、事件在后），否则每次刷新 /meta 都可能变。
    assert _payload(topic_id)["triggers"] == list(triggers)


@pytest.mark.parametrize("topic_id,name,triggers", EXPECTED_TOPICS)
def test_each_topic_payload_exposes_a_consumable_schema_and_uischema(
    topic_id, name, triggers
):
    payload = _payload(topic_id)

    assert payload["id"] == topic_id
    assert payload["name"] == name
    assert payload["triggers"] == list(triggers)
    assert set(payload) == {
        "id",
        "name",
        "triggers",
        "params_schema",
        "uischema",
        "default_schedule_time",
        # 票 06：订阅视图上标「含员工实拍照片」用（判断依据是内容的性质，不是页面）
        "contains_employee_photos",
    }

    schema = payload["params_schema"]
    assert schema["type"] == "object"
    properties = schema["properties"]

    # uischema：每个参数一个控件、顺序与 schema 一致、标签取参数标题。
    ui = payload["uischema"]
    assert ui["type"] == "VerticalLayout"
    assert [el["scope"] for el in ui["elements"]] == [
        f"#/properties/{field}" for field in properties
    ]
    for element in ui["elements"]:
        field = element["scope"].rsplit("/", 1)[-1]
        assert element["type"] == "Control"
        assert element["label"] == properties[field]["title"]
        assert element["options"]["control"] in CONTROL_TYPES

    # 必填项必须是已声明字段的子集，且每个必填项都有对应的控件。
    required = schema.get("required", [])
    assert set(required) <= set(properties)
    assert set(required) <= set(_controls(payload))


@pytest.mark.parametrize("topic_id", TOPIC_IDS)
def test_each_topic_declares_exactly_the_expected_params(topic_id):
    payload = _payload(topic_id)

    assert _controls(payload) == {
        field: control for field, (control, _required) in EXPECTED_PARAMS[topic_id].items()
    }
    assert set(payload["params_schema"].get("required", [])) == {
        field
        for field, (_control, required) in EXPECTED_PARAMS[topic_id].items()
        if required
    }


# ── 触发方式：定时类有默认推送时间，纯事件类没有这个参数 ─────────────────────


def test_only_the_data_quality_daily_topics_support_the_scheduled_trigger():
    """定时类**就是**这两个：销售报表与对账差异告警（原数据质量日报）。

    多一个或少一个都要在这里响——票 08 的定时任务表单的内容类型下拉直接来自这一层，
    注册表把承载定时任务的内容类型标成纯事件，等于把现有功能从表单里抹掉。
    """
    assert {
        topic.id for topic in all_topics() if PushTrigger.SCHEDULED in topic.triggers
    } == set(SCHEDULED_TOPICS)


@pytest.mark.parametrize("topic_id", SCHEDULED_TOPICS)
def test_every_scheduled_topic_declares_its_default_push_time(topic_id):
    """定时类都有默认推送时间，schema 里也带着这个默认值（票 08 的表单直接用它）。"""
    payload = _payload(topic_id)

    assert payload["default_schedule_time"] == EXPECTED_SCHEDULE_TIMES[topic_id]
    assert _controls(payload)["schedule_time"] == "time"

    schedule_time = payload["params_schema"]["properties"]["schedule_time"]
    assert schedule_time["default"] == EXPECTED_SCHEDULE_TIMES[topic_id]
    assert schedule_time["pattern"] == r"^([01]\d|2[0-3]):[0-5]\d$"


def test_reconcile_diff_supports_both_triggers():
    """对账差异告警是唯一一个「定时 + 事件」的内容类型。

    定时侧承载现有的数据质量日报任务（22:10 / 当天），事件侧是日终对账差异告警；
    `/meta` 里两个触发方式都在，票 08 的定时任务表单因此选得到它。
    """
    topic = get_topic("reconcile_diff")
    payload = _payload("reconcile_diff")

    assert topic.triggers == frozenset({PushTrigger.SCHEDULED, PushTrigger.EVENT})
    assert payload["triggers"] == ["scheduled", "event"]

    assert payload["default_schedule_time"] == "22:10"
    assert _controls(payload) == {"schedule_time": "time", "date_range_mode": "select"}
    assert _one_of(payload, "date_range_mode") == {"today": "当天", "yesterday": "昨天"}


@pytest.mark.parametrize("topic_id", EVENT_ONLY_TOPICS)
def test_event_only_topics_have_no_push_time_parameter(topic_id):
    """其余六类不受影响：只有事件触发，没有推送时间参数。"""
    payload = _payload(topic_id)

    assert payload["triggers"] == ["event"]
    assert payload["default_schedule_time"] is None
    assert "schedule_time" not in payload["params_schema"]["properties"]
    assert "time" not in _controls(payload).values()


def test_the_time_rule_is_the_same_one_the_legacy_jobs_use():
    """注册表的 HH:MM 规则与 services.wecom_push_service 的同一条：不能一边收一边拒。"""
    for topic_id in SCHEDULED_TOPICS:
        for value in ["21:30", "22:10", "00:00", "23:59", "24:00", "9:5", "2130", ""]:
            try:
                validate_schedule_time(value)
                legacy_ok = True
            except ValueError:
                legacy_ok = False
            try:
                validate_params(
                    topic_id, {"schedule_time": value}, trigger=PushTrigger.SCHEDULED
                )
                registry_ok = True
            except ValueError:
                registry_ok = False
            assert registry_ok is legacy_ok, f"{topic_id} 上 {value!r} 两边判定不一致"


# ── 下拉选项与标签也来自后端 ────────────────────────────────────────────────


def test_station_dropdown_lists_the_kitchen_stations_minus_the_floor():
    choices = _one_of(_payload("sales_report"), "station")

    assert choices[""] == "全部（排除楼面）"
    assert choices == {
        "": "全部（排除楼面）",
        **{
            station_id: info["name"]
            for station_id, info in KITCHEN_STATIONS.items()
            if station_id != "loumian"
        },
    }


def test_date_range_dropdown_labels_come_from_the_backend():
    assert _one_of(_payload("sales_report"), "date_range_mode") == {
        "today": "当天",
        "yesterday": "昨天",
    }


# ── 参数校验：与 schema 一致 ────────────────────────────────────────────────


def test_validate_params_fills_the_defaults_of_a_form_topic():
    assert validate_params("sales_report", {}) == {
        "schedule_time": "21:30",
        "date_range_mode": "today",
        "station": "",
    }
    # 对账差异告警的定时侧（数据质量日报）：默认时间与旧任务模板一致（22:10 / 当天）。
    assert validate_params("reconcile_diff", {}, trigger=PushTrigger.SCHEDULED) == {
        "schedule_time": "22:10",
        "date_range_mode": "today",
    }


def test_a_dual_trigger_topic_requires_naming_the_trigger():
    """两种触发方式的参数形状不同（表单 vs 触发点），省略触发方式不能靠猜。"""
    with pytest.raises(ValueError):
        validate_params("reconcile_diff", {})


def test_validate_params_rejects_a_trigger_the_topic_does_not_support():
    with pytest.raises(ValueError):
        validate_params("sales_report", {}, trigger=PushTrigger.EVENT)
    with pytest.raises(ValueError):
        validate_params("unmapped_dish", {}, trigger=PushTrigger.SCHEDULED)


def test_validate_params_rejects_fields_the_schema_does_not_declare():
    """表单参数严格校验：静默丢掉未知字段 = 以为改了、其实没改。"""
    with pytest.raises(ValueError):
        validate_params("sales_report", {"webhook_id": 3})
    with pytest.raises(ValueError):
        validate_params("reconcile_diff", {"webhook_id": 3}, trigger=PushTrigger.SCHEDULED)


@pytest.mark.parametrize(
    "params",
    [
        {"station": "no_such_station"},
        {"date_range_mode": "last_week"},
        {"schedule_time": "25:00"},
    ],
)
def test_validate_params_rejects_values_the_schema_forbids(params):
    with pytest.raises(ValueError):
        validate_params("sales_report", params)


@pytest.mark.parametrize(
    "params",
    [
        {"date_range_mode": "last_week"},
        {"schedule_time": "25:00"},
    ],
)
def test_reconcile_diff_schedule_params_are_checked_like_the_report_form(params):
    with pytest.raises(ValueError):
        validate_params("reconcile_diff", params, trigger=PushTrigger.SCHEDULED)


def test_validate_params_rejects_an_unknown_topic():
    with pytest.raises(ValueError):
        validate_params("no_such_topic", {})


def test_photo_params_require_the_capture_references():
    """照片投递缺采集图引用就渲染不出正文，必须在入队时被拦下。"""
    complete = {
        "ref_key": "r-1",
        "capture_id": "c-1",
        "extra_capture_id": None,
    }

    assert validate_params("hygiene_photo", complete) == complete
    for missing in ("ref_key", "capture_id"):
        with pytest.raises(ValueError):
            validate_params("hygiene_photo", {k: v for k, v in complete.items() if k != missing})


def test_event_params_keep_the_fields_the_trigger_produced():
    """事件侧的参数由触发点产出：注册表不认识也要原样留着，不能悄悄吃掉。

    对账差异告警两种触发方式都有，但它的定时侧默认时间（22:10 / 当天）不能漏进事件
    参数里——事件那次投递没有推送时间，这里断言的就是「一个字段都不多」。
    """
    params = {"biz_date": "2026-10-07", "diff_count": 3}

    assert validate_params("reconcile_diff", params, trigger=PushTrigger.EVENT) == params


# ── `/meta`：注册表的出口，旧字段必须原样保留 ────────────────────────────────


@pytest.fixture
def meta_client():
    """`/meta` 不碰数据库：只挂这个路由，鉴权换成放行（沿用既有的 API 测试缝隙）。"""
    app = FastAPI()
    app.include_router(wecom_push_router)
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_meta_keeps_the_fields_the_old_page_reads(meta_client):
    """旧页面（PWA 缓存里的旧 bundle）不能因为这一票报错：老字段逐个断言。"""
    payload = meta_client.get("/api/wecom-push/meta").json()

    assert payload["success"] is True
    assert payload["push_types"] == [
        {"id": "sales_report_text", "name": "销售报表文字版"},
        {"id": "data_quality_alert", "name": "数据质量告警"},
    ]
    assert payload["date_range_modes"] == [
        {"id": "today", "name": "当天"},
        {"id": "yesterday", "name": "昨天"},
    ]
    assert payload["text_limit"] == WECOM_TEXT_BYTE_LIMIT
    assert [tpl["id"] for tpl in payload["job_templates"]] == [
        "sales_report_daily",
        "data_quality_daily",
    ]
    assert payload["job_templates"][0]["schedule_time"] == "21:30"


def test_meta_lists_the_eight_topics_with_their_schema_and_uischema(meta_client):
    payload = meta_client.get("/api/wecom-push/meta").json()

    assert [topic["id"] for topic in payload["topics"]] == [
        row[0] for row in EXPECTED_TOPICS
    ]
    assert payload["triggers"] == [
        {"id": "scheduled", "name": "定时"},
        {"id": "event", "name": "事件"},
    ]
    # 每一类声明的触发方式都在这份名字表里，而且两种触发方式都真的被用上了。
    declared = {entry["id"] for entry in payload["triggers"]}
    used = {trigger for topic in payload["topics"] for trigger in topic["triggers"]}
    assert used == declared
    assert all(set(topic["triggers"]) <= declared for topic in payload["topics"])

    by_id = {topic["id"]: topic for topic in payload["topics"]}
    assert by_id["reconcile_diff"]["triggers"] == ["scheduled", "event"]
    assert by_id["reconcile_diff"]["default_schedule_time"] == "22:10"
    assert by_id["sales_report"]["triggers"] == ["scheduled"]

    for topic in payload["topics"]:
        assert topic["params_schema"]["type"] == "object"
        assert topic["uischema"]["type"] == "VerticalLayout"


def test_a_new_topic_reaches_the_page_without_any_frontend_change(meta_client):
    """夹具注册一个假内容类型：`/meta` 立刻按**同一个形状**发出去 —— 前端认的是这份
    数据（不是名字表、不是写死的下拉），所以加一类内容类型不需要改前端。"""
    builtin_shape = set(meta_client.get("/api/wecom-push/meta").json()["topics"][0])
    register_topic(
        PushTopic(
            id="fake_ops_event",
            name="夹具事件",
            triggers=frozenset({PushTrigger.EVENT}),
            event_params_model=FakeOpsParams,
        )
    )
    try:
        topics = meta_client.get("/api/wecom-push/meta").json()["topics"]
        entry = next(topic for topic in topics if topic["id"] == "fake_ops_event")

        assert set(entry) == builtin_shape
        assert entry["params_schema"]["properties"]["level"]["oneOf"] == [
            {"const": "warn", "title": "告警"},
            {"const": "recover", "title": "恢复"},
        ]
        assert [el["options"]["control"] for el in entry["uischema"]["elements"]] == ["select"]
    finally:
        unregister_topic("fake_ops_event")

    assert "fake_ops_event" not in [
        topic["id"] for topic in meta_client.get("/api/wecom-push/meta").json()["topics"]
    ]


def test_internal_topics_are_named_but_not_subscribable(meta_client):
    """测试发送 / 手工发送也要在发送记录里看得懂，但它们**不是**可订阅的内容类型。

    收件人由点击的那一次调用直接给出（选哪个群就发哪个群），与订阅无关 —— 所以它们
    不进注册表：不出现在 `/meta` 的 topics 里，也没有订阅行。只留一个显示名，让发送
    记录里那一行写「测试消息」而不是 `test_message`。
    """
    assert TOPIC_TEST_MESSAGE not in [topic.id for topic in all_topics()]
    assert get_topic(TOPIC_TEST_MESSAGE) is None
    assert topic_display_name(TOPIC_TEST_MESSAGE) == "测试消息"
    assert topic_display_name(TOPIC_MANUAL_SEND) == "手工发送"
    # 注册表里的内容类型仍然按注册表的显示名走；没见过的 id 退回 id 本身（历史行）。
    assert topic_display_name("sales_report") == "销售报表"
    assert topic_display_name("gone_topic") == "gone_topic"

    ids = [topic["id"] for topic in meta_client.get("/api/wecom-push/meta").json()["topics"]]
    assert TOPIC_TEST_MESSAGE not in ids
    assert TOPIC_MANUAL_SEND not in ids


# ── 与迁移的字面量对齐（防漂移）──────────────────────────────────────────────


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "pg"
    / "0016_wecom_push_subscriptions.sql"
)

# 迁移里 VALUES 列表的形状：一串「(…) 逗号分隔」的元组，紧跟着列别名声明。
_TUPLES = r"(?:\(\s*'[^']*'\s*(?:,\s*'[^']*'\s*)?\)\s*,?\s*)+"
_SINGLE_COLUMN_VALUES = re.compile(r"\(\s*VALUES\s*(" + _TUPLES + r")\)\s*AS t\(topic_id\)")
_MAPPING_VALUES = re.compile(
    r"\(\s*VALUES\s*(" + _TUPLES + r")\)\s*AS m\(legacy_type,\s*topic_id\)"
)
_TUPLE = re.compile(r"\(\s*'([^']*)'\s*(?:,\s*'([^']*)'\s*)?\)")
_HEADER_ROW = re.compile(r"^--\s+([a-z][a-z0-9_]*)\s+(\S+)\s+（([^）]*)）")
TRIGGER_BY_HEADER_LABEL = {"定时": "scheduled", "事件": "event"}


def _migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _migration_sql_body(sql: str) -> str:
    """去掉注释行：头注释里也写着这些 id，不能被当成「SQL 用到的字面量」。"""
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def _topic_ids_the_migration_writes(sql: str) -> set:
    """迁移的 INSERT 实际写进去的 topic id。

    只有两类位置（见 SQL 注释）：单列 ``VALUES`` —— 卫生两类、采集三类；两列
    ``VALUES`` 的第二列 —— 旧 push_type → topic id 的对照表。这里按 SQL 的结构取，
    不按 id 的形状取（照 id 形状取就是拿答案对答案，漂移了也看不出来）。
    """
    body = _migration_sql_body(sql)
    written = set()
    for block in _SINGLE_COLUMN_VALUES.findall(body):
        written.update(first for first, _second in _TUPLE.findall(block))
    for block in _MAPPING_VALUES.findall(body):
        written.update(second for _first, second in _TUPLE.findall(block) if second)
    return written


def _topics_documented_in_the_migration_header(sql: str) -> list:
    """迁移头注释里那张「内容类型 id / 显示名 / 触发方式」表。"""
    rows = []
    inside = False
    for line in sql.splitlines():
        if "内容类型 id" in line:
            inside = True
            continue
        if not inside or not line.startswith("--"):
            continue
        matched = _HEADER_ROW.match(line)
        if matched is None:
            if rows:  # 表到此结束
                break
            continue
        rows.append(matched.groups())
    return rows


def _triggers_of_header_row(label: str) -> set:
    """头注释「触发方式」那一列 → 触发方式 id 的集合。

    单个写 ``定时`` / ``事件``；两种都支持写 ``定时 + 事件``。标签后面还可以跟一句
    说明（``事件，含员工实拍照片``）——说明不算触发方式。
    """
    expression = label.split("，", 1)[0]
    return {
        TRIGGER_BY_HEADER_LABEL[token.strip()]
        for token in expression.split("+")
        if token.strip()
    }


def test_every_topic_id_the_migration_writes_is_a_registry_topic():
    """订阅与出站行按 topic_id 关联：迁移写的字面量必须都能在注册表里找到。"""
    written = _topic_ids_the_migration_writes(_migration_sql())

    # 解析没破的护栏（改了 SQL 形状就会在这里响，而不是悄悄退化成空集通过）。
    assert len(written) >= 6, f"迁移里只解析出 {sorted(written)}"
    assert written <= {topic.id for topic in all_topics()}, sorted(
        written - {topic.id for topic in all_topics()}
    )


def test_registry_matches_the_topics_documented_in_the_migration_header():
    """注册表的 id / 显示名 / 触发方式与迁移头注释那张表逐条一致。

    触发方式两边都是多值：对账差异告警在迁移注释里写 ``定时 + 事件``，注册表里就得
    同时有 ``scheduled`` 与 ``event``——注册表把它标成纯事件就是这条断言拦下的那个缺陷。
    """
    rows = _topics_documented_in_the_migration_header(_migration_sql())
    assert len(rows) >= 8, f"迁移头注释只解析出 {rows}"

    registered = {topic.id: topic for topic in all_topics()}
    for topic_id, name, trigger_label in rows:
        topic = registered.get(topic_id)
        assert topic is not None, f"注册表里没有迁移声明的 {topic_id}"
        assert topic.name == name
        assert {trigger.value for trigger in topic.triggers} == _triggers_of_header_row(
            trigger_label
        ), f"{topic_id} 的触发方式与迁移头注释不一致"

    # 反方向也要对：注册表里的内容类型一个都不能漏在迁移注释之外。
    assert set(registered) == {row[0] for row in rows}
