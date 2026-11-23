"""任务卡上「今天这一次到点了没发」的判据（票 10 / v6 多触发点）。

纯函数，不连库、不投递：`today_state` 只是把调度循环已经在用的几把尺子（营业日 06:00
切、触发点自己的星期、`last_sent_json`）读出来说给人听。页面直接显示它的结果，所以
这里钉的每一条都是「页面会显示什么」。
"""

import json
from datetime import datetime

from services.wecom_push_service import WeComPushService

# 与后端其它地方同一个时区常量（`database.CHINA_TZ`）；这里单独取一次，避免为了一个
# 纯函数测试去碰 database 的导入链。
from database import CHINA_TZ


def job(*, time="09:00", weekday=None, slots=None, **over):
    """一行触发点的任务行；`slots` 直接给整张表（多点用例用）。"""
    if slots is None:
        slots = [{"weekday": weekday, "time": time}]
    base = {
        "id": 1,
        "name": "每日销售报表",
        "topic_id": "sales_report",
        "schedule_time": slots[0]["time"] if slots else "",
        "slots_json": json.dumps(slots),
        "enabled": True,
        "last_sent_date": "",
        "last_sent_json": "{}",
        "params_json": "{}",
    }
    base.update(over)
    return base


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=CHINA_TZ)


def test_before_due_time_is_pending():
    assert WeComPushService.today_state(job(), at("2026-11-02T08:59")) == "pending"


def test_a_slot_turns_missed_only_after_the_grace_window():
    """过点 30 分钟才算漏：定时投递失败会在 +10 分钟补发一次，一过点就报红是假告警。"""
    assert WeComPushService.today_state(job(), at("2026-11-02T09:00")) == "pending"
    assert WeComPushService.today_state(job(), at("2026-11-02T09:29")) == "pending"
    assert WeComPushService.today_state(job(), at("2026-11-02T09:31")) == "missed"


def test_sent_today():
    sent = json.dumps({"2026-11-02": {"09:00": "2026-11-02T09:00:05"}})
    assert WeComPushService.today_state(
        job(last_sent_json=sent), at("2026-11-02T10:00")
    ) == "sent"


def test_a_legacy_row_without_slot_times_still_counts_as_sent():
    """0063 回填把「发过但没记下时刻」写成空串键：发过就算发过，不误报漏发。"""
    assert WeComPushService.today_state(
        job(last_sent_date="2026-11-02"), at("2026-11-02T10:00")
    ) == "sent"


def test_six_am_is_the_boundary_of_the_business_day():
    """营业日按 06:00 切（与 `dispatch_due_jobs` 同一把尺子，CORR-05）。

    凌晨 05:00 仍属**前一天**的营业日：那时已经发过的任务，`last_sent_date` 是前一天，
    页面上必须显示「今天已发送」，而不是因为日期字符串对不上就误报「今日未发送」。
    """
    assert WeComPushService.today_state(
        job(last_sent_date="2026-11-01"), at("2026-11-02T05:00")
    ) == "sent"
    # 仍是 11-01 这个营业日，它排在 23:00 的点早就过点了：那是漏发，不是「还没到点」。
    assert WeComPushService.today_state(
        job(time="23:00"), at("2026-11-02T05:00")
    ) == "missed"


def test_a_midnight_slot_belongs_to_the_previous_business_day():
    """营业日 D 的 00:30 落在 D+1 凌晨（06:00 之前），是那一天的收尾而不是再后一天。"""
    assert WeComPushService.today_state(
        job(time="00:30"), at("2026-11-02T02:00")
    ) == "missed"


def test_disabled_job_is_off():
    assert WeComPushService.today_state(job(enabled=False), at("2026-11-02T10:00")) == "off"


def test_job_without_any_slot_is_off():
    assert WeComPushService.today_state(job(slots=[]), at("2026-11-02T10:00")) == "off"


def test_each_slot_reports_its_own_state():
    """一天两个点：11:30 发过了、21:30 还没到点，汇总就是 pending。"""
    slots = [{"weekday": None, "time": "11:30"}, {"weekday": None, "time": "21:30"}]
    sent = json.dumps({"2026-11-02": {"11:30": "2026-11-02T11:30:02"}})

    sched = WeComPushService.today_schedule(
        job(slots=slots, last_sent_json=sent), at("2026-11-02T15:00")
    )
    assert sched["slots"] == [
        {"time": "11:30", "weekday": None, "state": "sent"},
        {"time": "21:30", "weekday": None, "state": "pending"},
    ]
    assert sched["state"] == "pending"
    assert sched["next_time"] == "21:30"
    assert sched["missed"] == []

    # 21:30 过点还没发：页面要能点名是哪一条（「今天 21:30 漏发」）。
    late = WeComPushService.today_schedule(
        job(slots=slots, last_sent_json=sent), at("2026-11-02T22:05")
    )
    assert late["state"] == "missed"
    assert late["missed"] == ["21:30"]


def test_a_weekly_slot_only_rings_on_its_weekday():
    """每周触发的触发点，非当天不该报「今日未发送」。

    2026-11-02 是周一；weekday=6 是每周日（`date.weekday()` 周一为 0）。这天它本来
    就不发，写成漏发会让人去查一个不存在的故障。
    """
    slots = [{"weekday": 6, "time": "13:00"}]
    assert WeComPushService.today_state(
        job(topic_id="weekly_schedule", slots=slots), at("2026-11-02T23:00")
    ) == "off"

    # 到了周日（2026-11-08）同一钟点：没到点 pending、过点 30 分钟 missed。
    assert WeComPushService.today_state(
        job(topic_id="weekly_schedule", slots=slots), at("2026-11-08T12:00")
    ) == "pending"
    assert WeComPushService.today_state(
        job(topic_id="weekly_schedule", slots=slots), at("2026-11-08T13:31")
    ) == "missed"


def test_a_mixed_week_slot_table_only_fires_the_matching_days():
    """周一 09:00 + 周三 21:00：周二两头都不该动。"""
    slots = [{"weekday": 0, "time": "09:00"}, {"weekday": 2, "time": "21:00"}]

    assert WeComPushService.today_state(job(slots=slots), at("2026-11-03T23:00")) == "off"
    assert WeComPushService.today_state(job(slots=slots), at("2026-11-02T08:00")) == "pending"
    assert WeComPushService.today_state(job(slots=slots), at("2026-11-04T20:00")) == "pending"