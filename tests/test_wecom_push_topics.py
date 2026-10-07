#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送内容类型注册表与 `/meta`（票 02）。

缝隙（spec「Testing Decisions」第 3 条）：注册表输出的 schema + uischema 可被消费，
以及 `/meta` 这条 API。断言的是**能不能被前端拿去渲染**——字段名、控件类型、必填项、
下拉选项；不测 uischema 是怎么推导出来的这类内部实现。

注册表 id 与迁移 ``0016_wecom_push_subscriptions.sql`` 里那批字面量必须一致：
订阅与出站行按 topic_id 关联，改名字而不同步迁移 = 订阅与内容类型对不上。
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
    PushTopic,
    PushTrigger,
    all_topics,
    get_topic,
    register_topic,
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


# 八类内容类型：id / 显示名 / 触发方式。来源是票面与迁移 0016 的头注释（两者一致）。
EXPECTED_TOPICS = [
    ("sales_report", "销售报表", "scheduled"),
    ("reconcile_diff", "对账差异告警", "event"),
    ("unmapped_dish", "未映射菜品提醒", "event"),
    ("scraper_failure", "采集失败告警", "event"),
    ("hygiene_reminder", "卫生提醒", "event"),
    ("hygiene_photo", "验收照片", "event"),
    ("update_backup", "系统更新与备份结果", "event"),
    ("resource_watermark", "磁盘与内存水位", "event"),
]

# 每类的参数：字段名 → (控件类型, 是否必填)。事件类的参数由触发点产出，注册表不声明
# 表单字段（照片是例外：它的参数形状已经被迁移里那批出站行的 params_json 定死了）。
EXPECTED_PARAMS = {
    "sales_report": {
        "schedule_time": ("time", False),
        "date_range_mode": ("select", False),
        "station": ("select", False),
    },
    "hygiene_photo": {
        "ref_key": ("text", True),
        "capture_id": ("text", True),
        "extra_capture_id": ("text", False),
    },
    "reconcile_diff": {},
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


def test_registry_declares_the_eight_topics_in_order():
    assert [(t.id, t.name, t.trigger.value) for t in all_topics()] == EXPECTED_TOPICS


@pytest.mark.parametrize("topic_id,name,trigger", EXPECTED_TOPICS)
def test_each_topic_payload_exposes_a_consumable_schema_and_uischema(
    topic_id, name, trigger
):
    payload = _payload(topic_id)

    assert payload["id"] == topic_id
    assert payload["name"] == name
    assert payload["trigger"] == trigger
    assert set(payload) == {
        "id",
        "name",
        "trigger",
        "params_schema",
        "uischema",
        "default_schedule_time",
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


@pytest.mark.parametrize("topic_id", [row[0] for row in EXPECTED_TOPICS])
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


# ── 推送时间：定时类有默认值，事件类没有这个参数 ──────────────────────────────


def test_scheduled_topic_declares_its_default_push_time():
    """销售报表的默认推送时间与老模板一致（21:30），schema 里也带着这个默认值。"""
    payload = _payload("sales_report")

    assert payload["default_schedule_time"] == "21:30"
    assert _controls(payload)["schedule_time"] == "time"

    schedule_time = payload["params_schema"]["properties"]["schedule_time"]
    assert schedule_time["default"] == "21:30"
    assert schedule_time["pattern"] == r"^([01]\d|2[0-3]):[0-5]\d$"


@pytest.mark.parametrize(
    "topic_id", [row[0] for row in EXPECTED_TOPICS if row[2] == "event"]
)
def test_event_topics_have_no_push_time_parameter(topic_id):
    payload = _payload(topic_id)

    assert payload["default_schedule_time"] is None
    assert "schedule_time" not in payload["params_schema"]["properties"]
    assert "time" not in _controls(payload).values()


def test_the_time_rule_is_the_same_one_the_legacy_jobs_use():
    """注册表的 HH:MM 规则与 services.wecom_push_service 的同一条：不能一边收一边拒。"""
    for value in ["21:30", "00:00", "23:59", "24:00", "9:5", "2130", ""]:
        try:
            validate_schedule_time(value)
            legacy_ok = True
        except ValueError:
            legacy_ok = False
        try:
            validate_params("sales_report", {"schedule_time": value})
            registry_ok = True
        except ValueError:
            registry_ok = False
        assert registry_ok is legacy_ok, f"{value!r} 两边判定不一致"


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


def test_validate_params_rejects_fields_the_schema_does_not_declare():
    """表单参数严格校验：静默丢掉未知字段 = 以为改了、其实没改。"""
    with pytest.raises(ValueError):
        validate_params("sales_report", {"webhook_id": 3})


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
    """事件类的参数由触发点产出：注册表不认识也要原样留着，不能悄悄吃掉。"""
    params = {"biz_date": "2026-10-07", "diff_count": 3}

    assert validate_params("reconcile_diff", params) == params


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
    assert {topic["trigger"] for topic in payload["topics"]} <= {
        entry["id"] for entry in payload["triggers"]
    }
    assert payload["triggers"] == [
        {"id": "scheduled", "name": "定时"},
        {"id": "event", "name": "事件"},
    ]
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
            trigger=PushTrigger.EVENT,
            params_model=FakeOpsParams,
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
_HEADER_ROW = re.compile(r"^--\s+([a-z][a-z0-9_]*)\s+(\S+)\s+（(定时|事件)[^）]*）")
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


def test_every_topic_id_the_migration_writes_is_a_registry_topic():
    """订阅与出站行按 topic_id 关联：迁移写的字面量必须都能在注册表里找到。"""
    written = _topic_ids_the_migration_writes(_migration_sql())

    # 解析没破的护栏（改了 SQL 形状就会在这里响，而不是悄悄退化成空集通过）。
    assert len(written) >= 6, f"迁移里只解析出 {sorted(written)}"
    assert written <= {topic.id for topic in all_topics()}, sorted(
        written - {topic.id for topic in all_topics()}
    )


def test_registry_matches_the_topics_documented_in_the_migration_header():
    """注册表的 id / 显示名 / 触发方式与迁移头注释那张表逐条一致。"""
    rows = _topics_documented_in_the_migration_header(_migration_sql())
    assert len(rows) >= 8, f"迁移头注释只解析出 {rows}"

    registered = {topic.id: topic for topic in all_topics()}
    for topic_id, name, trigger_label in rows:
        topic = registered.get(topic_id)
        assert topic is not None, f"注册表里没有迁移声明的 {topic_id}"
        assert (topic.name, topic.trigger.value) == (
            name,
            TRIGGER_BY_HEADER_LABEL[trigger_label],
        )
