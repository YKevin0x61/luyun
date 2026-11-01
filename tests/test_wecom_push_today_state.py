"""任务卡上「今天这一次到点了没发」的判据（票 10）。

纯函数，不连库、不投递：`WeComPushService.today_state` 只是把调度循环已经在用的三把尺子
（营业日 06:00 切、每周规则、`last_sent_date`）读出来说给人听。页面直接显示它的结果，
所以这里钉的每一条都是「页面会显示什么」。
"""

import json
from datetime import datetime

from services.wecom_push_service import WeComPushService

# 与后端其它地方同一个时区常量（`database.CHINA_TZ`）；这里单独取一次，避免为了一个
# 纯函数测试去碰 database 的导入链。
from database import CHINA_TZ


def job(**over):
    base = {
        "id": 1,
        "name": "每日销售报表",
        "topic_id": "sales_report",
        "schedule_time": "09:00",
        "enabled": True,
        "last_sent_date": "",
        # 库里存的是这一列（JSON 文本），`job_params()` 就是这么解析的。
        "params_json": "{}",
    }
    base.update(over)
    return base


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=CHINA_TZ)


def test_before_due_time_is_pending():
    assert WeComPushService.today_state(job(), at("2026-11-02T08:59")) == "pending"


def test_at_due_time_and_not_sent_is_missed():
    # 到点那一刻就该发；没发出来（或入队失败）就是漏了。
    assert WeComPushService.today_state(job(), at("2026-11-02T09:00")) == "missed"


def test_sent_today():
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
    # 同一个钟点，排在 23:00 的任务还没到点。
    assert WeComPushService.today_state(
        job(schedule_time="23:00"), at("2026-11-02T05:00")
    ) == "pending"


def test_disabled_job_is_off():
    assert WeComPushService.today_state(job(enabled=False), at("2026-11-02T10:00")) == "off"


def test_job_without_schedule_time_is_off():
    assert WeComPushService.today_state(job(schedule_time=""), at("2026-11-02T10:00")) == "off"


def test_weekly_job_is_off_on_days_it_does_not_fire():
    """每周触发的任务，非当天不该报「今日未发送」。

    2026-11-02 是周一；`weekday=6` 是每周日（`date.weekday()` 周一为 0）。这天它本来
    就不发，写成漏发会让人去查一个不存在的故障。
    """
    weekly = job(
        topic_id="weekly_schedule",
        params_json=json.dumps({"weekday": 6}),
        schedule_time="13:00",
    )
    assert WeComPushService.today_state(weekly, at("2026-11-02T23:00")) == "off"

    # 到了周日（2026-11-08）同一钟点：发过就是 sent，没发过就是 missed。
    assert WeComPushService.today_state(weekly, at("2026-11-08T12:00")) == "pending"
    assert WeComPushService.today_state(weekly, at("2026-11-08T13:30")) == "missed"
    assert WeComPushService.today_state(
        job(topic_id="weekly_schedule", params_json=json.dumps({"weekday": 6}),
            schedule_time="13:00", last_sent_date="2026-11-08"),
        at("2026-11-08T14:00"),
    ) == "sent"


def test_weekly_job_with_broken_weekday_never_fires_so_never_missed():
    """参数里没有合法 weekday 时调度器不发（记警告），页面也不该说漏发。"""
    broken = job(topic_id="weekly_schedule", params_json=json.dumps({"weekday": "周日"}))
    assert WeComPushService.today_state(broken, at("2026-11-08T23:00")) == "off"
