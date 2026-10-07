#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送内容类型注册表：八类可订阅的推送内容在**一处**声明（ADR 0097）。

每一类声明：稳定 id、显示名、触发方式（定时 / 事件）、参数 pydantic 模型（JSON
Schema 由它生成）、uischema（控件与标签）、定时类的默认推送时间。``/meta`` 把这份
注册表原样输出，前端据此渲染表单与选项——**加一类内容类型只改这个文件**，前端零改动。

id 必须与迁移 ``0016_wecom_push_subscriptions.sql`` 里的字面量完全一致（订阅与出站行
按 ``topic_id`` 关联，改名而不同步迁移就是订阅与内容类型对不上）。这条约束由
``tests/test_wecom_push_topics.py`` 的漂移断言钉住，别在别处复制这些字面量。

uischema 也由后端给（「时间用 time 控件、档口用下拉」属于后端知识）：控件类型写在
参数模型的字段上（``x-ui-control``），uischema 由 schema 推导，所以 schema 与
uischema 不可能各说各话。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config import KITCHEN_STATIONS

# ── 控件类型（uischema 里 options.control 的取值）──────────────────────────────
CONTROL_TIME = "time"
CONTROL_SELECT = "select"
CONTROL_TEXT = "text"

# 档口下拉排除楼面：销售报表不带楼面（既有口径，见 db_core/reports.py 的
# KDS_EXCLUDED_STATION 与页面上原先写死的 jobStations）。
_FLOOR_STATION_ID = "loumian"

# 与 services/wecom_push_service.validate_schedule_time 同一条规则（HH:MM，
# 00:00–23:59）。两个实现由 tests/test_wecom_push_topics.py 的一致性用例钉住。
_SCHEDULE_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

DATE_RANGE_LABELS = {"today": "当天", "yesterday": "昨天"}


def _select_extras(choices: Mapping[str, str]) -> Dict[str, Any]:
    """下拉控件的选项：JSON Forms 的枚举控件认 ``oneOf`` 里的 const / title。"""
    return {
        "x-ui-control": CONTROL_SELECT,
        "oneOf": [{"const": value, "title": label} for value, label in choices.items()],
    }


def _station_choices() -> Dict[str, str]:
    choices = {"": "全部（排除楼面）"}
    for station_id, info in KITCHEN_STATIONS.items():
        if station_id == _FLOOR_STATION_ID:
            continue
        choices[station_id] = str(info.get("name") or station_id)
    return choices


_STATION_CHOICES = _station_choices()


class PushTrigger(str, Enum):
    """触发方式：定时（由推送任务按时间触发）/ 事件（由触发点即时触发）。"""

    SCHEDULED = "scheduled"
    EVENT = "event"

    @property
    def label(self) -> str:
        return "定时" if self is PushTrigger.SCHEDULED else "事件"


# ── 参数模型 ────────────────────────────────────────────────────────────────


class _FormParams(BaseModel):
    """表单参数（定时类，来自页面）：多一个字段就报错。

    静默丢弃未知字段会让店长以为改了、其实没改（spec「页面」一节的同一条理由），
    所以这里严格拒绝，schema 也是 ``additionalProperties: false``。
    """

    model_config = ConfigDict(extra="forbid")


class _EventParams(BaseModel):
    """事件参数（事件类，由触发点产出）。

    已声明的字段照 schema 校验，未声明的**原样保留**：参数的形状由触发点决定，
    注册表只声明其中对渲染有意义的部分，不该把触发点给的参数吃掉。
    """

    model_config = ConfigDict(extra="allow")


class SalesReportParams(_FormParams):
    """销售报表：日期口径 + 档口 + 推送时间。"""

    schedule_time: str = Field(
        "21:30",
        pattern=_SCHEDULE_TIME_PATTERN,
        title="推送时间",
        description="每天在这个时间推送（营业日口径）。",
        json_schema_extra={"x-ui-control": CONTROL_TIME},
    )
    date_range_mode: str = Field(
        "today",
        title="日期口径",
        description="今天 = 当前营业日；昨天 = 上一个营业日。",
        json_schema_extra=_select_extras(DATE_RANGE_LABELS),
    )
    station: str = Field(
        "",
        title="档口",
        description="只推某个档口；空 = 全部（排除楼面）。",
        json_schema_extra=_select_extras(_STATION_CHOICES),
    )

    @field_validator("date_range_mode")
    @classmethod
    def _check_date_range_mode(cls, value: str) -> str:
        if value not in DATE_RANGE_LABELS:
            raise ValueError("日期口径只支持 today 或 yesterday")
        return value

    @field_validator("station")
    @classmethod
    def _check_station(cls, value: str) -> str:
        if value not in _STATION_CHOICES:
            raise ValueError(f"档口不存在: {value}")
        return value


class ReconcileDiffParams(_EventParams):
    """对账差异告警：日终对账发现差异时即时投递。"""


class UnmappedDishParams(_EventParams):
    """未映射菜品提醒：巡检发现没有档口映射的菜品时即时投递。"""


class ScraperFailureParams(_EventParams):
    """采集失败告警：连续采集失败达到阈值时即时投递。"""


class HygieneReminderParams(_EventParams):
    """卫生提醒：漏拍 / 专项 / 整改开单 / 整改超时合并为一类的汇总。"""


class HygienePhotoParams(_EventParams):
    """验收照片：验收通过后投递（含员工实拍图与专项前后对比图）。

    参数形状已被迁移 0016 里那批出站行的 ``params_json`` 定死：照片存**采集图引用**
    而不是 base64，所以这里三个字段都是引用。
    """

    ref_key: str = Field(
        ..., min_length=1, title="验收单引用", json_schema_extra={"x-ui-control": CONTROL_TEXT}
    )
    capture_id: str = Field(
        ..., min_length=1, title="采集图", json_schema_extra={"x-ui-control": CONTROL_TEXT}
    )
    extra_capture_id: Optional[str] = Field(
        None, title="对比图", json_schema_extra={"x-ui-control": CONTROL_TEXT}
    )


class UpdateBackupParams(_EventParams):
    """系统更新与备份结果：更新作业 / 冷备结束后投递一次。"""


class ResourceWatermarkParams(_EventParams):
    """磁盘与内存水位：等级变化时即时投递（同一等级有静默期）。"""


# ── 注册表 ──────────────────────────────────────────────────────────────────

TOPIC_SALES_REPORT = "sales_report"
TOPIC_RECONCILE_DIFF = "reconcile_diff"
TOPIC_UNMAPPED_DISH = "unmapped_dish"
TOPIC_SCRAPER_FAILURE = "scraper_failure"
TOPIC_HYGIENE_REMINDER = "hygiene_reminder"
TOPIC_HYGIENE_PHOTO = "hygiene_photo"
TOPIC_UPDATE_BACKUP = "update_backup"
TOPIC_RESOURCE_WATERMARK = "resource_watermark"


def _format_errors(exc: ValidationError) -> str:
    return "；".join(
        f"{'.'.join(str(part) for part in error['loc']) or '参数'}: {error['msg']}"
        for error in exc.errors()
    )


@dataclass(frozen=True)
class PushTopic:
    """一类推送内容。"""

    id: str
    name: str
    trigger: PushTrigger
    params_model: Type[BaseModel]

    @property
    def params_schema(self) -> Dict[str, Any]:
        """参数 JSON Schema——由 pydantic 模型生成，不手写。"""
        return self.params_model.model_json_schema()

    @property
    def uischema(self) -> Dict[str, Any]:
        """JSON Forms 的 UI Schema：每个参数一个控件，标签取参数标题。"""
        properties = self.params_schema.get("properties", {})
        return {
            "type": "VerticalLayout",
            "elements": [
                {
                    "type": "Control",
                    "scope": f"#/properties/{field}",
                    "label": str(prop.get("title") or field),
                    "options": {"control": prop.get("x-ui-control", CONTROL_TEXT)},
                }
                for field, prop in properties.items()
            ],
        }

    @property
    def default_schedule_time(self) -> Optional[str]:
        """定时类的默认推送时间；事件类没有（票面：事件类不出现推送时间参数）。

        取值就是参数模型里 ``schedule_time`` 的默认值——默认时间只有这一处声明。
        """
        if self.trigger is not PushTrigger.SCHEDULED:
            return None
        field = self.params_model.model_fields.get("schedule_time")
        return None if field is None else str(field.default)

    def payload(self) -> Dict[str, Any]:
        """`/meta` 里这一类内容类型的形状（前端只认它）。"""
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger.value,
            "params_schema": self.params_schema,
            "uischema": self.uischema,
            "default_schedule_time": self.default_schedule_time,
        }

    def validate_params(self, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """按这个类的参数模型校验，返回规范化后的参数字典（补上默认值）。

        不合 schema / 缺必填项一律抛 ``ValueError`` 并带上字段名——出站行的正文由参数
        渲染，参数错了要在这里就看得见，而不是发出去一条内容不对的消息。
        """
        try:
            model = self.params_model.model_validate(dict(params or {}))
        except ValidationError as exc:
            raise ValueError(f"{self.name}的参数不合法：{_format_errors(exc)}") from exc
        return model.model_dump(mode="json")


_TOPICS: Dict[str, PushTopic] = {}


def register_topic(topic: PushTopic) -> None:
    """登记一类内容类型（同 id 覆盖）。

    新增一类内容类型 = 写好它的参数模型 + 在这里登记的**一行**，前端零改动；
    测试夹具也用它来证明这一点。
    """
    if not topic.id:
        raise ValueError("内容类型必须有 id")
    _TOPICS[topic.id] = topic


def unregister_topic(topic_id: str) -> bool:
    """注销一类内容类型，返回是否原本存在（测试夹具的清理入口）。"""
    return _TOPICS.pop(str(topic_id), None) is not None


def all_topics() -> "tuple[PushTopic, ...]":
    """全部内容类型，按声明顺序（页面上订阅矩阵的行序）。"""
    return tuple(_TOPICS.values())


def get_topic(topic_id: str) -> Optional[PushTopic]:
    return _TOPICS.get(str(topic_id or ""))


def validate_params(topic_id: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """按 ``topic_id`` 那一类的参数模型校验参数（入队前唯一的校验入口）。"""
    topic = get_topic(topic_id)
    if topic is None:
        raise ValueError(f"未知的推送内容类型: {topic_id}")
    return topic.validate_params(params)


# ── 八类内容类型（声明顺序 = 页面上订阅矩阵的行序）─────────────────────────
register_topic(PushTopic(
    TOPIC_SALES_REPORT, "销售报表", PushTrigger.SCHEDULED, SalesReportParams))
register_topic(PushTopic(
    TOPIC_RECONCILE_DIFF, "对账差异告警", PushTrigger.EVENT, ReconcileDiffParams))
register_topic(PushTopic(
    TOPIC_UNMAPPED_DISH, "未映射菜品提醒", PushTrigger.EVENT, UnmappedDishParams))
register_topic(PushTopic(
    TOPIC_SCRAPER_FAILURE, "采集失败告警", PushTrigger.EVENT, ScraperFailureParams))
register_topic(PushTopic(
    TOPIC_HYGIENE_REMINDER, "卫生提醒", PushTrigger.EVENT, HygieneReminderParams))
register_topic(PushTopic(
    TOPIC_HYGIENE_PHOTO, "验收照片", PushTrigger.EVENT, HygienePhotoParams))
register_topic(PushTopic(
    TOPIC_UPDATE_BACKUP, "系统更新与备份结果", PushTrigger.EVENT, UpdateBackupParams))
register_topic(PushTopic(
    TOPIC_RESOURCE_WATERMARK, "磁盘与内存水位", PushTrigger.EVENT, ResourceWatermarkParams))
