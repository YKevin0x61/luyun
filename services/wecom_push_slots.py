"""推送任务的触发点：一条任务可以有多个「哪天 + 几点」。

为什么单独一个模块：调度（`dispatch_due_jobs` / `today_state`）、接口（`/jobs` 的读与写）
和迁移回填三处都要按同一套口径解析时间点，分散着写就会在「没有 weekday 算哪天」「浏览器
给的是 21:30:00」这类细节上漂移。

口径（2026-11-23 的访谈结论）：
* 一条任务 = **同一份内容参数** + 最多 `SCHEDULE_SLOT_LIMIT` 个触发点；点与点之间只有
  时刻（和可选的星期几）不同。想给两份不同的内容就建两条任务。
* `weekday` 为 `None` 表示每天都发；`0`=`周一` … `6`=`周日`（与 `date.weekday()` 一致）。
* 同一条任务里不允许两个完全相同的点 —— 同一分钟发两遍不是想要的东西，保存时就拒。
* `slots_json` 为空（旧行，或迁移之前的写入路径）时回退到 `schedule_time` 列，于是
  升级后旧任务照旧只有一行，不会因为读不到 slots 就整条不发。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from services.wecom_push_topics import WEEKDAY_LABELS

logger = logging.getLogger(__name__)

#: 一条任务最多几个触发点。12 个 = 一天里每小时一次还有富余；再多基本是建错了任务。
SCHEDULE_SLOT_LIMIT = 12

#: `last_sent_json` 只留最近几天：它只回答「今天这个点发过没」，不是发送流水账。
LAST_SENT_KEEP_DAYS = 7

_SLOT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class ScheduleSlot:
    """一个触发点。`weekday` 为 None 表示每天。"""

    weekday: Optional[int]
    time: str

    def label(self, *, with_weekday: bool = True) -> str:
        if not with_weekday:
            return self.time
        if self.weekday is None:
            # 「每天」比只写时刻清楚：卡片上一行 `21:30` 看不出是每天还是漏了星期那栏。
            return f"每天 {self.time}"
        return f"{WEEKDAY_LABELS.get(self.weekday, '')} {self.time}"

    def as_dict(self) -> dict[str, Any]:
        return {"weekday": self.weekday, "time": self.time}


def normalize_time(value: Any) -> str:
    """校验并归一成 `HH:MM`。浏览器 `<input type=time>` 给的 `21:30:00` 剪到分钟。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("推送时间不能为空")
    if _SLOT_TIME_RE.match(text):
        return text
    head = text[:5]
    if _SLOT_TIME_RE.match(head) and text[5:] in ("", ":00", ":000"):
        return head
    raise ValueError(f"推送时间格式不对（要 HH:MM）：{text}")


def normalize_weekday(value: Any) -> Optional[int]:
    """`None` / 空串 = 每天；0-6 = 周一到周日；其它一律拒。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.isdigit():
            raise ValueError(f"星期几不对（要 0-6 或空）：{value}")
        value = int(text)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"星期几不对（要 0-6 或空）：{value}")
    if value < 0 or value > 6:
        raise ValueError(f"星期几超出范围（0-6）：{value}")
    return value


def _unique(slots: Iterable[ScheduleSlot]) -> list[ScheduleSlot]:
    seen: set[tuple[Optional[int], str]] = set()
    result: list[ScheduleSlot] = []
    for slot in slots:
        key = (slot.weekday, slot.time)
        if key in seen:
            raise ValueError(f"推送时间重复了：{slot.label()}")
        seen.add(key)
        result.append(slot)
    return result


def parse_slots(
    raw: Any,
    *,
    fallback_time: str = "",
    fallback_weekday: Any = None,
    limit: int = SCHEDULE_SLOT_LIMIT,
) -> list[ScheduleSlot]:
    """把请求体 / 数据库里的形状解析成触发点列表。

    `raw` 接受三种写法：`[{"weekday": 0, "time": "09:00"}]`、`["09:00"]`、
    以及已经解析好的 `ScheduleSlot` 列表。空列表时回退到 `fallback_time` —— 那是
    `schedule_time` 列（旧任务、旧 bundle 的请求体）。
    """
    rows = list(raw or [])
    if not rows:
        if not str(fallback_time or "").strip():
            return []
        return [ScheduleSlot(normalize_weekday(fallback_weekday), normalize_time(fallback_time))]
    slots: list[ScheduleSlot] = []
    for row in rows:
        if isinstance(row, ScheduleSlot):
            slots.append(ScheduleSlot(normalize_weekday(row.weekday), normalize_time(row.time)))
        elif isinstance(row, Mapping):
            slots.append(
                ScheduleSlot(
                    normalize_weekday(row.get("weekday")),
                    normalize_time(row.get("time")),
                )
            )
        else:
            slots.append(ScheduleSlot(None, normalize_time(row)))
    slots = _unique(slots)
    if len(slots) > limit:
        raise ValueError(f"一条任务最多 {limit} 个推送时间，现在有 {len(slots)} 个")
    return slots


def dump_slots(slots: Sequence[ScheduleSlot]) -> str:
    return json.dumps([slot.as_dict() for slot in slots], ensure_ascii=False)


def slots_from_row(job: Mapping[str, Any]) -> list[ScheduleSlot]:
    """读一条任务行的触发点。读不出来就回退 `schedule_time` + params 里的 weekday。"""
    params: Mapping[str, Any] = {}
    raw_params = job.get("params_json")
    if isinstance(raw_params, str) and raw_params.strip():
        try:
            loaded = json.loads(raw_params)
            if isinstance(loaded, dict):
                params = loaded
        except (TypeError, ValueError):
            params = {}
    elif isinstance(raw_params, Mapping):
        params = raw_params

    raw_slots = job.get("slots_json")
    parsed: list[Any] = []
    if isinstance(raw_slots, str) and raw_slots.strip():
        try:
            loaded = json.loads(raw_slots)
            if isinstance(loaded, list):
                parsed = loaded
        except (TypeError, ValueError):
            logger.warning("推送任务 %s 的 slots_json 解析失败，回退到 schedule_time", job.get("id"))
            parsed = []
    elif isinstance(raw_slots, list):
        parsed = raw_slots

    try:
        return parse_slots(
            parsed,
            fallback_time=str(job.get("schedule_time") or ""),
            fallback_weekday=params.get("weekday"),
        )
    except ValueError as exc:
        # 库里存了非法时间不该让整个调度循环崩掉：这条任务当天跳过，日志里点名。
        logger.warning("推送任务 %s 的触发点不合法：%s", job.get("id"), exc)
        return []


def slot_labels(slots: Sequence[ScheduleSlot]) -> list[str]:
    return [slot.label() for slot in slots]


def schedule_text(slots: Sequence[ScheduleSlot], *, head: int = 3) -> str:
    """卡片徽章文案：`每天 11:30、21:30` / `每周日 13:00` / 长的折成「… 等 4 个」。"""
    if not slots:
        return ""
    weekdays = {slot.weekday for slot in slots}
    if len(weekdays) == 1:
        only = next(iter(weekdays))
        times = "、".join(slot.time for slot in slots)
        prefix = "每天" if only is None else f"每{WEEKDAY_LABELS.get(only, '')}"
        return f"{prefix} {times}"
    labels = [slot.label() for slot in slots]
    if len(labels) <= head:
        return "、".join(labels)
    return "、".join(labels[:head]) + f" 等 {len(labels)} 个"


def slot_matches_day(slot: ScheduleSlot, day: date) -> bool:
    return slot.weekday is None or slot.weekday == day.weekday()


def last_sent_map(job: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """`{营业日: {"HH:MM": 发送时刻 ISO}}`；读坏了当空。"""
    raw = job.get("last_sent_json")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("推送任务 %s 的 last_sent_json 解析失败，按未发送处理", job.get("id"))
        return {}
    if not isinstance(loaded, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for day, times in loaded.items():
        if isinstance(times, dict):
            result[str(day)] = {str(k): str(v) for k, v in times.items()}
    return result


def record_last_sent(
    mapping: Mapping[str, Mapping[str, str]],
    business_date: str,
    slot_time: str,
    when: datetime,
) -> dict[str, dict[str, str]]:
    """记一笔并裁掉过期的日子。返回**新**字典，调用方负责写回。"""
    result = {day: dict(times) for day, times in mapping.items()}
    result.setdefault(business_date, {})[slot_time] = when.isoformat()
    if len(result) > LAST_SENT_KEEP_DAYS:
        for day in sorted(result)[: len(result) - LAST_SENT_KEEP_DAYS]:
            result.pop(day, None)
    return result


def dump_last_sent(mapping: Mapping[str, Mapping[str, str]]) -> str:
    return json.dumps(
        {day: dict(times) for day, times in mapping.items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def row_last_sent_at(job: Mapping[str, Any]) -> str:
    """最近一次（任何触发点）的发送时刻 ISO；没有就空串。"""
    latest = ""
    for times in last_sent_map(job).values():
        for stamp in times.values():
            if stamp > latest:
                latest = stamp
    return latest
