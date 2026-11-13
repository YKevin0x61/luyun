#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运营日报（ADR 0144）：注册表参数、日期口径推断、文字版与出图。

出图那几条在本机没有中文字体时跳过（`load_cjk_font` 返回 None，`render_png` 返回
None —— 那是**降级路径**，不是失败）；渲染器与出站的那条集成在
`tests/test_wecom_daily_ops_delivery.py`。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from db_core.utils import CHINA_TZ
from services.wecom_daily_ops import (
    DEFAULT_MODULES,
    MODULE_DISHES,
    MODULE_HOURLY,
    MODULE_ORDERS,
    MODULE_SCHEDULING,
    DailyOpsReport,
    normalize_modules,
    render_png,
    render_text,
)
from services.wecom_outbox import freeze_business_date
from services.wecom_push_topics import (
    CONTROL_CHECKBOX_GROUP,
    DailyOpsReportParams,
    TOPIC_DAILY_OPS_REPORT,
    auto_business_date_mode,
    get_topic,
    validate_params,
)


def test_topic_is_registered_with_its_own_default_time():
    topic = get_topic(TOPIC_DAILY_OPS_REPORT)
    assert topic is not None
    assert topic.name == "运营日报"
    # 22:15 是在对账（22:05）之后：日报里的数据质量必须是终态，不能一边推一边还在对账。
    assert topic.default_schedule_time == "22:15"
    # 日期口径不进参数，由触发时间推断。
    assert topic.auto_business_date is True
    assert "date_range_mode" not in topic.params_schema["properties"]


def test_modules_field_is_checkbox_group_with_every_module_as_an_option():
    schema = get_topic(TOPIC_DAILY_OPS_REPORT).params_schema["properties"]["modules"]
    assert schema["type"] == "array"
    assert schema["x-ui-control"] == CONTROL_CHECKBOX_GROUP
    values = [option["const"] for option in schema["items"]["oneOf"]]
    assert values == list(DEFAULT_MODULES)


def test_modules_default_is_in_the_schema_so_a_new_job_starts_checked():
    """参数表单的初始值只认 JSON Schema 的 `default`（`defaultJobParams`）。

    用 `default_factory` 时 pydantic 不会把它写进 schema，新建任务就是十个空框 ——
    页面上看起来像「默认什么都不推」。
    """
    schema = get_topic(TOPIC_DAILY_OPS_REPORT).params_schema["properties"]["modules"]
    assert schema["default"] == list(DEFAULT_MODULES)
    assert DailyOpsReportParams().modules is not DailyOpsReportParams().modules


def test_default_modules_are_all_ten():
    params = validate_params(TOPIC_DAILY_OPS_REPORT, {})
    assert params["modules"] == list(DEFAULT_MODULES)
    assert len(params["modules"]) == 10


def test_validate_params_rejects_unknown_module_and_empty_selection():
    with pytest.raises(ValueError):
        validate_params(TOPIC_DAILY_OPS_REPORT, {"modules": ["orders", "nope"]})
    with pytest.raises(ValueError):
        validate_params(TOPIC_DAILY_OPS_REPORT, {"modules": []})


def test_auto_business_date_mode_switches_at_noon():
    """22 点档发当天，早上改点就发上一个营业日。"""
    assert auto_business_date_mode(datetime(2026, 11, 13, 22, 15, tzinfo=CHINA_TZ)) == "today"
    assert auto_business_date_mode(datetime(2026, 11, 13, 8, 0, tzinfo=CHINA_TZ)) == "yesterday"
    assert auto_business_date_mode(datetime(2026, 11, 13, 11, 59, tzinfo=CHINA_TZ)) == "yesterday"
    assert auto_business_date_mode(datetime(2026, 11, 13, 12, 0, tzinfo=CHINA_TZ)) == "today"


def test_freeze_business_date_accepts_an_inferred_mode():
    """没有 date_range_mode 参数的内容类型，也要在入队时把营业日冻下来。"""
    frozen = freeze_business_date(
        {"modules": ["orders"]},
        datetime(2026, 11, 13, 22, 15, tzinfo=CHINA_TZ),
        auto_mode="today",
    )
    assert frozen["business_date"] == "2026-11-13"
    # 冻结后参数里不该留「以后再解析一次」的入口。
    assert "date_range_mode" not in frozen
    assert frozen["modules"] == ["orders"]

    early = freeze_business_date(
        {"modules": ["orders"]},
        datetime(2026, 11, 13, 8, 0, tzinfo=CHINA_TZ),
        auto_mode="yesterday",
    )
    assert early["business_date"] == "2026-11-12"


def test_freeze_business_date_params_win_over_inferred_mode():
    frozen = freeze_business_date(
        {"date_range_mode": "yesterday"},
        datetime(2026, 11, 13, 22, 15, tzinfo=CHINA_TZ),
        auto_mode="today",
    )
    assert frozen["business_date"] == "2026-11-12"


def test_normalize_modules_drops_unknown_and_keeps_display_order():
    assert normalize_modules(["scheduling", "nope", MODULE_ORDERS, MODULE_ORDERS]) == [
        MODULE_ORDERS,
        MODULE_SCHEDULING,
    ]
    # 全不合法时退回全开：历史出站行里的模块可能已经下线，不该因此一张图都画不出。
    assert normalize_modules(["nope"]) == list(DEFAULT_MODULES)


def _sample_report() -> DailyOpsReport:
    return DailyOpsReport(
        business_date="2026-10-13",
        weekday="周二",
        orders={"orders": 1544, "items": 1544, "dishes": 140, "delta_pct": 17.0, "peak_hour": 11, "peak_orders": 304},
        hourly=[{"hour": 11, "orders": 304}, {"hour": 12, "orders": 230}],
        dishes=[{"name": "桐乡胎菊", "qty": 102}],
        scheduling={
            "today": {
                "date": "2026-10-13",
                "weekday": "周二",
                "total": 19,
                "shifts": [{"name": "早班", "count": 12}],
                "off": [{"name": "陈冠初", "leave": True, "half_rest": False}],
            },
            "tomorrow": {
                "date": "2026-10-14",
                "weekday": "周三",
                "total": 18,
                "shifts": [{"name": "早班", "count": 12}],
                "off": [],
            },
        },
        refunds={
            "co": {"lines": 4, "money": 128.0, "qty": 4.0},
            "present": {"lines": 2, "money": 56.0, "qty": 2.0},
            "reasons": {
                "co": [
                    {"reason": "客人误点/多点[退菜]", "lines": 3, "money": 98.0},
                    {"reason": "出品错误[退菜]", "lines": 1, "money": 30.0},
                ],
                "present": [{"reason": "员工餐", "lines": 2, "money": 56.0}],
            },
        },
        empty_reason={"wastage": "暂无报损记录"},
    )


def test_render_text_covers_every_module_and_lists_folded_ones():
    text = render_text(_sample_report())
    assert "【运营日报】2026年10月13日 周二" in text
    assert "订单 1544 单" in text
    assert "较上周同日 +17.0%" in text
    assert "今天休假 1 人：陈冠初（请假）" in text
    assert "明天休假 0 人" in text
    # 空模块合一行列出，不逐块占版面。
    assert "暂无数据：卫生、加班补钟、数据质量、客流、损耗" in text
    # 退单/赠单要带原因，且原因一行一条。
    assert "退单与赠单：退单 4 笔 ¥128.00 · 赠单 2 笔 ¥56.00" in text
    assert "退单·客人误点/多点[退菜] 3 笔 ¥98.00" in text
    assert "赠单·员工餐 2 笔 ¥56.00" in text


def test_render_text_handles_an_empty_day():
    text = render_text(DailyOpsReport(business_date="2026-10-13", weekday="周二"))
    assert "【运营日报】2026年10月13日 周二" in text
    # 十个模块全空也是一条能发出去的文字，不是异常。
    assert "暂无数据：" in text


def test_render_png_is_a_750_wide_png_or_a_clean_none():
    data = render_png(_sample_report())
    if data is None:
        pytest.skip("本机没有中文字体，出图降级为文字（这条路径由 render_text 覆盖）")
    assert data.startswith(b"\x89PNG")
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        assert image.size == (750, image.size[1])
        assert image.size[1] > 400


def test_render_png_only_draws_selected_modules():
    full = render_png(_sample_report())
    partial = render_png(_sample_report(), modules=[MODULE_ORDERS])
    if full is None or partial is None:
        pytest.skip("本机没有中文字体")
    assert len(partial) < len(full)


def test_hourly_and_dishes_modules_are_optional_in_layout():
    """少勾几块的图不该因为量高的常量对不上而画歪（高度必须随块数变化）。"""
    report = _sample_report()
    with_hourly = render_png(report, modules=[MODULE_HOURLY])
    with_dishes = render_png(report, modules=[MODULE_DISHES])
    if with_hourly is None or with_dishes is None:
        pytest.skip("本机没有中文字体")
    assert with_hourly.startswith(b"\x89PNG")
    assert with_dishes.startswith(b"\x89PNG")

def _png_height(data: bytes) -> int:
    """PNG 的 IHDR 高度（偏移 20，4 字节大端）。"""
    return int.from_bytes(data[20:24], "big")


def test_render_png_grows_with_refund_reason_rows():
    """退单卡按原因行数长高，不能和 KPI 卡一样固定高度——多出来的行会压到下一张卡上。"""
    base = _sample_report()
    one_reason = replace(
        base,
        refunds={
            "co": {"lines": 1, "money": 30.0, "qty": 1.0},
            "present": {"lines": 0, "money": 0.0, "qty": 0.0},
            "reasons": {"co": [{"reason": "出品错误", "lines": 1, "money": 30.0}], "present": []},
        },
    )
    many_reasons = replace(
        base,
        refunds={
            "co": {"lines": 6, "money": 180.0, "qty": 6.0},
            "present": {"lines": 0, "money": 0.0, "qty": 0.0},
            "reasons": {
                "co": [{"reason": f"原因{i}", "lines": 1, "money": 30.0} for i in range(6)],
                "present": [],
            },
        },
    )
    short = render_png(one_reason)
    tall = render_png(many_reasons)
    if short is None or tall is None:
        pytest.skip("本机没有中文字体")
    assert _png_height(tall) - _png_height(short) == 5 * 28
