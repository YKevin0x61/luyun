#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购超量 / 低量预警的纯函数与入队边界。"""

import asyncio

from services.purchase import notify
from services.wecom_outbox import PushTrigger


PRODUCT = {"id": 1, "name": "菜心", "unit": "斤"}


def _detect(quantity, *, previous=0, reference=10.0, over_ratio=1.5, under_ratio=0.5):
    return notify.detect_anomalies(
        products=[PRODUCT],
        quantities={1: quantity},
        references={1: reference},
        previous_quantities={1: previous} if previous else {},
        over_ratio=over_ratio,
        under_ratio=under_ratio,
    )


def test_thresholds_are_strict_at_the_boundary():
    """恰好 1.5 / 0.5 倍不触发，和采购分析页的 > / < 一致。"""
    assert _detect(15) == []
    assert _detect(15.01)[0]["kind"] == "over"
    assert _detect(5) == []
    assert _detect(4.99)[0]["kind"] == "under"


def test_already_over_or_under_before_this_order_does_not_repeat():
    """当天已经超量 / 低量的商品，再补一点不重新提醒；只看「这一单新跨过」。"""
    assert _detect(1, previous=16) == []
    assert _detect(1, previous=4) == []
    # 从「没到低量」跨到「低量」才提醒；从「已经低量」继续补货不提醒。
    under = _detect(4, previous=0)
    assert under[0]["kind"] == "under"
    assert under[0]["quantity"] == 4


def test_products_without_a_usable_reference_are_skipped():
    assert _detect(999, reference=0) == []
    assert _detect(999, reference=None) == []
    assert notify.detect_anomalies(
        products=[PRODUCT],
        quantities={1: 999},
        references={},
        previous_quantities={},
    ) == []


def test_render_groups_over_and_under_with_daily_totals():
    anomalies = [
        {
            "kind": "over",
            "name": "菜心",
            "unit": "斤",
            "order_quantity": 10,
            "quantity": 12,
            "reference": 2,
            "ratio": 6.0,
        },
        {
            "kind": "under",
            "name": "生菜",
            "unit": "斤",
            "order_quantity": 1,
            "quantity": 1,
            "reference": 10,
            "ratio": 0.1,
        },
    ]
    text = notify.render_purchase_anomaly_text(
        {
            "order_no": "P20261122-0001",
            "position_name": "西饼",
            "submitter_name": "张织纷",
            "submitted_at": "2026-11-22T10:20:00+08:00",
        },
        anomalies,
        base_url="https://luyun.example.com",
    )

    assert text.splitlines()[0] == "【厨房采购预警】P20261122-0001"
    assert "西饼 · 张织纷 · 11-22 10:20" in text
    assert "超量（>1.5 倍）" in text
    assert "· 菜心 本单 10斤 / 今日 12斤 / 参考 2斤（6.00 倍）" in text
    assert "低量（<0.5 倍）" in text
    assert "· 生菜 本单 1斤 / 今日 1斤 / 参考 10斤（0.10 倍）" in text
    assert "看分析：https://luyun.example.com/workbench/kitchen/purchase/analysis" in text
    assert notify.render_purchase_anomaly_text({}, []) == ""


class _RecordingOutbox:
    def __init__(self, ids=None, error=None):
        self.ids = [9] if ids is None else ids
        self.error = error
        self.calls = []

    async def enqueue_topic(self, db, topic_id, **kwargs):
        self.calls.append({"db": db, "topic_id": topic_id, **kwargs})
        if self.error:
            raise self.error
        return list(self.ids)


def _order():
    return {
        "id": 42,
        "order_no": "P20261122-0001",
        "position_name": "西饼",
        "submitter_name": "张织纷",
        "submitted_at": "2026-11-22T10:20:00+08:00",
    }


def test_notify_enqueues_one_message_with_text_and_business_reference(monkeypatch):
    outbox = _RecordingOutbox(ids=[9])
    monkeypatch.setattr(notify, "wecom_outbox", outbox)

    queued = asyncio.run(
        notify.notify_purchase_anomalies(
            object(),
            order=_order(),
            products=[PRODUCT],
            quantities={1: 10},
            references={1: 2},
            previous_quantities={},
        )
    )

    assert queued == [9]
    assert len(outbox.calls) == 1
    call = outbox.calls[0]
    assert call["topic_id"] == "purchase_anomaly"
    assert call["trigger"] is PushTrigger.EVENT
    assert call["business_reference"] == "purchase_anomaly:42"
    assert call["params"]["text"].startswith("【厨房采购预警】P20261122-0001")
    assert call["summary"] == "【厨房采购预警】P20261122-0001"


def test_notify_skips_when_nothing_crosses_a_threshold(monkeypatch):
    outbox = _RecordingOutbox()
    monkeypatch.setattr(notify, "wecom_outbox", outbox)

    queued = asyncio.run(
        notify.notify_purchase_anomalies(
            object(),
            order=_order(),
            products=[PRODUCT],
            quantities={1: 10},
            references={1: 10},
            previous_quantities={},
        )
    )

    assert queued == []
    assert outbox.calls == []


def test_notify_swallows_outbox_failures(monkeypatch):
    """群通知失败不能把已经成功的采购单提交带崩。"""
    outbox = _RecordingOutbox(error=RuntimeError("企微不可达"))
    monkeypatch.setattr(notify, "wecom_outbox", outbox)

    queued = asyncio.run(
        notify.notify_purchase_anomalies(
            object(),
            order=_order(),
            products=[PRODUCT],
            quantities={1: 10},
            references={1: 2},
            previous_quantities={},
        )
    )

    assert queued == []
