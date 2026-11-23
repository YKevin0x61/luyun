"""触发点模块（v6 多触发点）的纯函数单测。

这一层是调度、接口、迁移回填三处共用的口径，所以钉得细一点：时间形状、星期、重复、
上限、旧行回退、`last_sent` 的裁剪。都是纯函数，不连库。
"""

import json
from datetime import date, datetime

import pytest

from database import CHINA_TZ
from services.wecom_push_slots import (
    SCHEDULE_SLOT_LIMIT,
    ScheduleSlot,
    dump_last_sent,
    dump_slots,
    last_sent_map,
    normalize_time,
    normalize_weekday,
    parse_slots,
    record_last_sent,
    row_last_sent_at,
    schedule_text,
    slot_labels,
    slot_matches_day,
    slots_from_row,
)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=CHINA_TZ)


# --- 时间与星期 ---------------------------------------------------------------


def test_a_plain_hh_mm_time_is_accepted():
    assert normalize_time("21:30") == "21:30"
    assert normalize_time(" 09:05 ") == "09:05"


def test_seconds_are_trimmed_not_rejected():
    """浏览器 `<input type=time>` 交上来的就是 `21:30:00`，把它当错会让人莫名其妙。"""
    assert normalize_time("21:30:00") == "21:30"


def test_an_empty_or_malformed_time_is_refused():
    with pytest.raises(ValueError, match="不能为空"):
        normalize_time("")
    with pytest.raises(ValueError, match="格式不对"):
        normalize_time("25:00")
    with pytest.raises(ValueError, match="格式不对"):
        normalize_time("21:30:15")


def test_weekday_none_means_every_day():
    assert normalize_weekday(None) is None
    assert normalize_weekday("") is None
    assert normalize_weekday(0) == 0
    assert normalize_weekday("6") == 6


def test_bad_weekdays_are_refused():
    with pytest.raises(ValueError, match="超出范围"):
        normalize_weekday(7)
    with pytest.raises(ValueError, match="星期几不对"):
        normalize_weekday("周一")
    with pytest.raises(ValueError, match="星期几不对"):
        normalize_weekday(True)


# --- parse_slots ---------------------------------------------------------------


def test_slots_accept_mappings_plain_times_and_slot_objects():
    slots = parse_slots([
        {"weekday": 0, "time": "09:00"},
        "21:30",
        ScheduleSlot(6, "13:00"),
    ])
    assert [(slot.weekday, slot.time) for slot in slots] == [(0, "09:00"), (None, "21:30"), (6, "13:00")]


def test_an_empty_list_falls_back_to_the_legacy_column():
    """旧任务只有 `schedule_time`：一条都没有就等于整条不发，回退让它照旧有一行。"""
    assert [slot.time for slot in parse_slots([], fallback_time="13:00")] == ["13:00"]
    assert parse_slots([], fallback_time="") == []


def test_the_legacy_weekday_from_params_is_carried_over():
    slots = parse_slots([], fallback_time="13:00", fallback_weekday=6)
    assert (slots[0].weekday, slots[0].time) == (6, "13:00")


def test_duplicate_slots_are_refused():
    with pytest.raises(ValueError, match="重复"):
        parse_slots([{"weekday": None, "time": "09:00"}, "09:00"])


def test_the_same_time_on_different_weekdays_is_not_a_duplicate():
    slots = parse_slots([{"weekday": 0, "time": "09:00"}, {"weekday": 2, "time": "09:00"}])
    assert len(slots) == 2


def test_the_slot_limit_is_enforced():
    raw = ["%02d:00" % hour for hour in range(SCHEDULE_SLOT_LIMIT + 1)]
    with pytest.raises(ValueError, match="最多"):
        parse_slots(raw)
    assert len(parse_slots(raw[:SCHEDULE_SLOT_LIMIT])) == SCHEDULE_SLOT_LIMIT


# --- 文案 -----------------------------------------------------------------------


def test_a_daily_job_lists_its_times():
    slots = [ScheduleSlot(None, "11:30"), ScheduleSlot(None, "21:30")]
    assert schedule_text(slots) == "每天 11:30、21:30"
    assert slot_labels(slots) == ["每天 11:30", "每天 21:30"]


def test_a_weekly_job_says_which_weekday():
    assert schedule_text([ScheduleSlot(6, "13:00")]) == "每周日 13:00"


def test_a_mixed_week_is_listed_per_slot():
    slots = [ScheduleSlot(0, "09:00"), ScheduleSlot(2, "21:00")]
    assert schedule_text(slots) == "周一 09:00、周三 21:00"


def test_a_long_list_is_folded_after_three():
    """不齐的组合（每个点自己的星期）才需要折叠；一天多点的写法本身很短。"""
    slots = [ScheduleSlot(weekday, "%02d:00" % (9 + 2 * weekday)) for weekday in range(5)]
    text = schedule_text(slots)
    assert text == "周一 09:00、周二 11:00、周三 13:00 等 5 个"


def test_label_can_drop_the_weekday():
    assert ScheduleSlot(None, "09:00").label(with_weekday=False) == "09:00"


# --- 数据库里的一行 -------------------------------------------------------------


def test_slots_round_trip_through_the_column():
    slots = [ScheduleSlot(None, "11:30"), ScheduleSlot(6, "13:00")]
    row = {"id": 3, "slots_json": dump_slots(slots), "schedule_time": "11:30", "params_json": "{}"}
    assert [(slot.weekday, slot.time) for slot in slots_from_row(row)] == [(None, "11:30"), (6, "13:00")]


def test_a_row_without_slots_falls_back_to_the_old_columns():
    """迁移之前写下的行（slots_json 是 '[]'）：读 `schedule_time` + params.weekday。"""
    row = {
        "id": 4,
        "slots_json": "[]",
        "schedule_time": "13:00",
        "params_json": json.dumps({"weekday": 6}),
    }
    assert [(slot.weekday, slot.time) for slot in slots_from_row(row)] == [(6, "13:00")]


def test_a_corrupt_slots_column_falls_back_instead_of_raising():
    """一条坏数据不能把整个调度循环带崩。"""
    row = {"id": 5, "slots_json": "{not json", "schedule_time": "22:15", "params_json": "{}"}
    assert [slot.time for slot in slots_from_row(row)] == ["22:15"]


def test_an_illegal_time_in_the_column_yields_no_slot():
    row = {"id": 6, "slots_json": json.dumps([{"weekday": None, "time": "99:99"}]), "schedule_time": "", "params_json": "{}"}
    assert slots_from_row(row) == []


def test_slot_matches_day():
    assert slot_matches_day(ScheduleSlot(None, "09:00"), date(2026, 11, 2))
    assert slot_matches_day(ScheduleSlot(0, "09:00"), date(2026, 11, 2))
    assert not slot_matches_day(ScheduleSlot(6, "09:00"), date(2026, 11, 2))


# --- last_sent_json ------------------------------------------------------------


def test_record_last_sent_is_per_slot():
    sent = record_last_sent({}, "2026-11-02", "11:30", at("2026-11-02T11:30:02"))
    assert sent == {"2026-11-02": {"11:30": "2026-11-02T11:30:02+08:00"}}
    sent = record_last_sent(sent, "2026-11-02", "21:30", at("2026-11-02T21:30:01"))
    assert set(sent["2026-11-02"]) == {"11:30", "21:30"}


def test_record_last_sent_keeps_only_the_recent_days():
    sent = {}
    for day in range(1, 11):
        sent = record_last_sent(sent, "2026-11-%02d" % day, "09:00", at("2026-11-%02dT09:00:00" % day))
    assert sorted(sent) == ["2026-11-%02d" % day for day in range(4, 11)]


def test_last_sent_survives_the_json_round_trip():
    sent = record_last_sent({}, "2026-11-02", "09:00", at("2026-11-02T09:00:00"))
    row = {"last_sent_json": dump_last_sent(sent)}
    assert last_sent_map(row) == sent


def test_a_corrupt_last_sent_column_reads_as_nothing_sent():
    assert last_sent_map({"last_sent_json": "oops"}) == {}


def test_row_last_sent_at_picks_the_latest_slot():
    sent = record_last_sent({}, "2026-11-02", "09:00", at("2026-11-02T09:00:00"))
    sent = record_last_sent(sent, "2026-11-02", "21:30", at("2026-11-02T21:30:00"))
    assert row_last_sent_at({"last_sent_json": dump_last_sent(sent)}) == "2026-11-02T21:30:00+08:00"
    assert row_last_sent_at({}) == ""