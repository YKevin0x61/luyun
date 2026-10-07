#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送内容类型注册表：八类可订阅的推送内容在**一处**声明（ADR 0097）。

每一类声明：稳定 id、显示名、触发方式（定时 / 事件，**同一个内容类型可以两者都支持**）、
参数 pydantic 模型（JSON Schema 由它生成）、uischema（控件与标签）、定时侧的默认推送
时间。``/meta`` 把这份注册表原样输出，前端据此渲染表单与选项——**加一类内容类型只改
这个文件**，前端零改动。

参数模型按**触发方式**分开：定时侧是页面填的表单参数（严格校验，未知字段报错），事件侧
是触发点产出的参数（已声明字段照 schema 校验、未声明的原样保留）。对账差异告警两种触发
方式都有——它既承载现有的「数据质量日报」定时任务（22:10 / 当天），也是日终对账差异的
事件告警；两者合成一个模型就会把定时侧的默认时间写进事件参数里（事件那次投递根本没有
推送时间），所以宁可分开声明。

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
    """触发方式：定时（由推送任务按时间触发）/ 事件（由触发点即时触发）。

    声明顺序也是输出顺序：``/meta`` 里每一类的 ``triggers`` 按这个顺序排，
    两种都支持时固定是「定时、事件」。
    """

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


def _schedule_time_field(default: str) -> Any:
    """推送时间字段：控件、HH:MM 规则与文案只有这一处声明。

    定时类内容类型之间只差默认时间（销售报表 21:30、对账差异告警 22:10），字段
    照抄一份就会漏改其中之一，所以默认值当参数传。
    """
    return Field(
        default,
        pattern=_SCHEDULE_TIME_PATTERN,
        title="推送时间",
        description="每天在这个时间推送（营业日口径）。",
        json_schema_extra={"x-ui-control": CONTROL_TIME},
    )


class _ScheduleFormParams(_FormParams):
    """定时侧表单的公共字段：推送时间 + 日期口径。

    ``date_range_mode`` 与 ``wecom_push_jobs.date_range_mode`` 同义（今天 = 当前营业日，
    昨天 = 上一个营业日），取值与标签就是 ``DATE_RANGE_LABELS``。
    """

    schedule_time: str = _schedule_time_field("21:30")
    date_range_mode: str = Field(
        "today",
        title="日期口径",
        description="今天 = 当前营业日；昨天 = 上一个营业日。",
        json_schema_extra=_select_extras(DATE_RANGE_LABELS),
    )

    @field_validator("date_range_mode")
    @classmethod
    def _check_date_range_mode(cls, value: str) -> str:
        if value not in DATE_RANGE_LABELS:
            raise ValueError("日期口径只支持 today 或 yesterday")
        return value


class SalesReportParams(_ScheduleFormParams):
    """销售报表：日期口径 + 档口 + 推送时间。"""

    station: str = Field(
        "",
        title="档口",
        description="只推某个档口；空 = 全部（排除楼面）。",
        json_schema_extra=_select_extras(_STATION_CHOICES),
    )

    @field_validator("station")
    @classmethod
    def _check_station(cls, value: str) -> str:
        if value not in _STATION_CHOICES:
            raise ValueError(f"档口不存在: {value}")
        return value


class ReconcileDiffScheduleParams(_ScheduleFormParams):
    """对账差异告警的定时侧（原「数据质量日报」）：推送时间 + 日期口径。

    默认 22:10 / 当天，与 ``/meta`` 旧字段 ``job_templates`` 里的 ``data_quality_daily``
    逐字一致（日终对账 22:05 之后）。
    """

    schedule_time: str = _schedule_time_field("22:10")


class ReconcileDiffParams(_EventParams):
    """对账差异告警的事件侧：日终对账发现差异时即时投递。"""


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
    """一类推送内容。

    参数模型按触发方式分开：``schedule_params_model``（定时侧，页面填的表单）与
    ``event_params_model``（事件侧，触发点产出）。声明了哪种触发方式就必须有对应的
    模型——注册一行时自证，而不是等某个入口去取模型才发现是 None。
    """

    id: str
    name: str
    triggers: FrozenSet[PushTrigger]
    schedule_params_model: Optional[Type[BaseModel]] = None
    event_params_model: Optional[Type[BaseModel]] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("内容类型必须有 id")
        # 收下 set / tuple 也归一成 frozenset：这个 dataclass 是 frozen 的，别留一个
        # 能被外部改掉的字段。
        object.__setattr__(self, "triggers", frozenset(self.triggers))
        if not self.triggers:
            raise ValueError(f"内容类型 {self.id} 至少要声明一种触发方式")
        for trigger, model in (
            (PushTrigger.SCHEDULED, self.schedule_params_model),
            (PushTrigger.EVENT, self.event_params_model),
        ):
            if (trigger in self.triggers) != (model is not None):
                raise ValueError(
                    f"内容类型 {self.id} 的{trigger.label}触发与参数模型对不上"
                )

    @property
    def triggers_in_order(self) -> "tuple[PushTrigger, ...]":
        """声明的触发方式，按 ``PushTrigger`` 的声明顺序（输出顺序必须稳定）。"""
        return tuple(trigger for trigger in PushTrigger if trigger in self.triggers)

    @property
    def params_schema(self) -> Dict[str, Any]:
        """参数 JSON Schema——由 pydantic 模型生成，不手写。

        取**定时侧**模型：页面只在定时任务表单里渲染参数（事件侧的参数由触发点产出，
        没有表单）。只有事件触发的内容类型就用它的事件模型（照片的采集图引用这么来的）。
        """
        model = self.schedule_params_model or self.event_params_model
        if model is None:  # __post_init__ 已保证，这里只是不写 assert
            raise ValueError(f"内容类型 {self.id} 没有参数模型")
        return model.model_json_schema()

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
        """定时侧的默认推送时间；不支持定时触发的内容类型没有（``None``）。

        取值就是定时侧参数模型里 ``schedule_time`` 的默认值——默认时间只有这一处声明。
        """
        if self.schedule_params_model is None:
            return None
        field = self.schedule_params_model.model_fields.get("schedule_time")
        return None if field is None else str(field.default)

    def payload(self) -> Dict[str, Any]:
        """`/meta` 里这一类内容类型的形状（前端只认它）。

        ``triggers`` 是**数组**：一个内容类型可以同时支持定时与事件（对账差异告警既有
        22:10 的数据质量日报任务，也有日终对账差异告警）。
        """
        return {
            "id": self.id,
            "name": self.name,
            "triggers": [trigger.value for trigger in self.triggers_in_order],
            "params_schema": self.params_schema,
            "uischema": self.uischema,
            "default_schedule_time": self.default_schedule_time,
        }

    def _resolve_trigger(self, trigger: Optional[PushTrigger]) -> PushTrigger:
        """定下这次校验用哪种触发方式。

        省略只对**单一触发方式**的内容类型成立；两种都支持的（对账差异告警）必须
        指明——定时侧与事件侧的参数形状不同，靠猜就会把定时侧的默认时间写进事件参数。
        """
        if trigger is not None:
            if trigger not in self.triggers:
                raise ValueError(f"「{self.name}」不支持{trigger.label}触发")
            return trigger
        if len(self.triggers) == 1:
            return next(iter(self.triggers))
        labels = "、".join(item.label for item in self.triggers_in_order)
        raise ValueError(
            f"「{self.name}」同时支持{labels}触发，校验参数时要指明触发方式"
        )

    def params_model_for(self, trigger: PushTrigger) -> Type[BaseModel]:
        """这种触发方式用哪个参数模型（没声明过这种触发方式就报错）。"""
        resolved = self._resolve_trigger(trigger)
        model = (
            self.schedule_params_model
            if resolved is PushTrigger.SCHEDULED
            else self.event_params_model
        )
        if model is None:  # __post_init__ 已保证，这里只是不写 assert
            raise ValueError(f"内容类型 {self.id} 没有{resolved.label}侧的参数模型")
        return model

    def validate_params(
        self,
        params: Optional[Mapping[str, Any]] = None,
        *,
        trigger: Optional[PushTrigger] = None,
    ) -> Dict[str, Any]:
        """按这个类**这一种触发方式**的参数模型校验，返回规范化后的参数字典。

        定时侧（表单）会补上默认值；事件侧不补——事件那次投递没有推送时间，参数形状
        由触发点决定。不合 schema / 缺必填项一律抛 ``ValueError`` 并带上字段名——出站行
        的正文由参数渲染，参数错了要在这里就看得见，而不是发出去一条内容不对的消息。
        """
        resolved = self._resolve_trigger(trigger)
        try:
            model = self.params_model_for(resolved).model_validate(dict(params or {}))
        except ValidationError as exc:
            raise ValueError(f"{self.name}的参数不合法：{_format_errors(exc)}") from exc
        return model.model_dump(mode="json")


_TOPICS: Dict[str, PushTopic] = {}


def register_topic(topic: PushTopic) -> None:
    """登记一类内容类型（同 id 覆盖）。

    新增一类内容类型 = 写好它的参数模型 + 在这里登记的**一行**，前端零改动；
    测试夹具也用它来证明这一点。
    """
    _TOPICS[topic.id] = topic


def unregister_topic(topic_id: str) -> bool:
    """注销一类内容类型，返回是否原本存在（测试夹具的清理入口）。"""
    return _TOPICS.pop(str(topic_id), None) is not None


def all_topics() -> "tuple[PushTopic, ...]":
    """全部内容类型，按声明顺序（页面上订阅矩阵的行序）。"""
    return tuple(_TOPICS.values())


def get_topic(topic_id: str) -> Optional[PushTopic]:
    return _TOPICS.get(str(topic_id or ""))


def validate_params(
    topic_id: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    trigger: Optional[PushTrigger] = None,
) -> Dict[str, Any]:
    """按 ``topic_id`` 那一类内容类型的参数模型校验参数（入队前唯一的校验入口）。

    ``trigger`` 见 ``PushTopic.validate_params``：单一触发方式的内容类型可省略。
    """
    topic = get_topic(topic_id)
    if topic is None:
        raise ValueError(f"未知的推送内容类型: {topic_id}")
    return topic.validate_params(params, trigger=trigger)


# ── 八类内容类型（声明顺序 = 页面上订阅矩阵的行序）─────────────────────────
register_topic(PushTopic(
    id=TOPIC_SALES_REPORT,
    name="销售报表",
    triggers=frozenset({PushTrigger.SCHEDULED}),
    schedule_params_model=SalesReportParams,
))
# 唯一一个两种触发方式都有的内容类型：定时侧承载原「数据质量日报」（22:10 / 当天），
# 事件侧是日终对账差异告警（迁移 0016 把旧 push_type `data_quality_alert` 映射到它）。
register_topic(PushTopic(
    id=TOPIC_RECONCILE_DIFF,
    name="对账差异告警",
    triggers=frozenset({PushTrigger.SCHEDULED, PushTrigger.EVENT}),
    schedule_params_model=ReconcileDiffScheduleParams,
    event_params_model=ReconcileDiffParams,
))
register_topic(PushTopic(
    id=TOPIC_UNMAPPED_DISH,
    name="未映射菜品提醒",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=UnmappedDishParams,
))
register_topic(PushTopic(
    id=TOPIC_SCRAPER_FAILURE,
    name="采集失败告警",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=ScraperFailureParams,
))
register_topic(PushTopic(
    id=TOPIC_HYGIENE_REMINDER,
    name="卫生提醒",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=HygieneReminderParams,
))
register_topic(PushTopic(
    id=TOPIC_HYGIENE_PHOTO,
    name="验收照片",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=HygienePhotoParams,
))
register_topic(PushTopic(
    id=TOPIC_UPDATE_BACKUP,
    name="系统更新与备份结果",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=UpdateBackupParams,
))
register_topic(PushTopic(
    id=TOPIC_RESOURCE_WATERMARK,
    name="磁盘与内存水位",
    triggers=frozenset({PushTrigger.EVENT}),
    event_params_model=ResourceWatermarkParams,
))
