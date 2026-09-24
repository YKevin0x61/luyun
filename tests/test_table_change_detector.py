#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票 05 / CORR-05 + CORR-06：餐桌「取材失败」「无消费桌」「真没菜」必须三态分明。

背景（CORR-05，引入点 0c94084）：`_get_orders_for_changed_tables` 把「金额 <= 0」也塞进
`failed_tables`，于是 amount=0 的桌每轮都被当成"明细取材失败"：
  * 状态永不推进——`previous_tables_state` 里该桌的金额永远是旧值（实机：新桌三轮后恒
    `{}`、旧桌 88 先 252.0 后 0 恒 `{'88': 252.0}`）；
  * 每轮都被判"有变化" → 每轮告警；
  * 该桌的差分整轮被跳过 → 复用桌号点的新菜永远不入库。

本文件钉三件事：
1. 无消费桌（金额 == 0）不是取材失败：状态推进、不进取材失败告警、第二轮不再判有变化；
   整桌退菜仍要「金额确实变小 + 连续 DINE_IN_CANCEL_MISS_THRESHOLD 轮缺席」佐证；
2. 金额 < 0 是异常值（不是"没消费"）：照常取材，退菜同样按金额佐证；
3. CORR-06：餐桌级（`scrape_table_data`）与明细级（`fetch_table_orders`）的取材失败都
   不与「取材成功但为空」同形——失败返回 None 且不推进状态，成功但为空才推进。

本文件只驱动 `TableChangeDetector` + 假 session，不连库、不连 POS。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from scraper._common import CHINA_TZ
from scraper.pos_session import PosSession
from scraper.state_store import ScraperStateStore
from scraper.table_change_detector import (
    DINE_IN_CANCEL_MISS_THRESHOLD,
    TableChangeDetector,
)

TABLE_LOG = logging.getLogger("test-table-change-detector")


def _run(coro):
    return asyncio.run(coro)


def _pos_line(flow_id, *, qty=1, dish="虾饺", price=12.0, notes="", table="8"):
    """一条 POS 明细行（形态与 tests/test_scraper_dish_changes.py 的 helper 一致）。"""
    return {
        "business_flow_id": flow_id,
        "table_number": table,
        "dish_name": dish,
        "quantity": qty,
        "price": price,
        "total_amount": price * qty,
        "status": "未结",
        "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
        "source": "dine_in",
        "station": "shulong",
        "notes": notes,
    }


def _table(number, amount, *, orders=None, detail="ok", point_id=None):
    return {
        "table_number": number,
        "amount": amount,
        "point_id": f"p{number}" if point_id is None else point_id,
        "orders": list(orders or []),
        "detail": detail,
    }


class _FakeTableSession:
    """假 POS session：只实现 monitor_table_orders 用到的口子，不连 POS。

    ``list_detail="fail"`` 时 `scrape_table_data` 返回 None —— CORR-06 里的餐桌级取材失败。
    """

    def __init__(self, tables, *, list_detail="ok"):
        self.tables = list(tables)
        self.fetch_calls = []
        self._list_detail = list_detail

    async def ensure_ready(self):
        return True

    async def scrape_table_data(self):
        if self._list_detail == "fail":
            return None
        return [dict(table) for table in self.tables]

    def resolve_point_id(self, table_number):
        return f"p{table_number}"

    async def fetch_table_orders(self, table_number, point_id):
        self.fetch_calls.append((table_number, point_id))
        table = next(t for t in self.tables if t["table_number"] == table_number)
        if table["detail"] == "fail":
            return None
        return [dict(line) for line in table["orders"]]


class _RecordingOrdersPort:
    """记录堂食退菜/恢复调用的假 orders 端口（不连库）。"""

    def __init__(self, *, restore_returns_row=False):
        self.cancelled = []
        self.restored = []
        self.restore_returns_row = restore_returns_row

    async def cancel_dine_in_portions(self, table_number, dish_name, portions, notes=""):
        self.cancelled.append((table_number, dish_name, portions, notes))
        return portions

    async def restore_dine_in_cancelled(self, table_number, dish_name, order=None, notes=None):
        self.restored.append((table_number, dish_name))
        return {"id": f"{table_number}-{dish_name}"} if self.restore_returns_row else None


@pytest.fixture
def env(tmp_path):
    state_file = tmp_path / "table_state.json"
    state = ScraperStateStore(
        table_state_file=str(state_file),
        delivery_bills_file=str(tmp_path / "delivery_bills.json"),
    )
    return SimpleNamespace(
        state=state,
        # 生产端口删掉的订单行能恢复回来（见 OrdersPort.restore_dine_in_cancelled）
        orders=_RecordingOrdersPort(restore_returns_row=True),
        detector=TableChangeDetector(
            session=None, state_store=state, logger_=TABLE_LOG
        ),
        state_file=state_file,
    )


def _seed_tracked_table(state, table_number, amount, orders):
    state.previous_tables_state = {table_number: amount}
    state.previous_table_orders = {table_number: list(orders)}
    state.is_first_run = False


def _round(detector, orders, tables, *, list_detail="ok", current_tables_data=None):
    detector._session = _FakeTableSession(tables, list_detail=list_detail)
    return _run(
        detector.monitor_table_orders(
            current_tables_data=current_tables_data, orders=orders
        )
    )


# ── 票 05：金额 == 0 的「无消费桌」不是取材失败 ────────────────────────────────


def test_zero_amount_table_advances_state_and_is_not_a_scrape_failure(env, caplog):
    """新桌 amount=0：状态推进到 0、不查明细、不告警，第二轮不再判"有变化"。"""
    env.state.is_first_run = False
    session = _FakeTableSession([_table("99", 0.0)])
    env.detector._session = session

    with caplog.at_level(logging.INFO, logger=TABLE_LOG.name):
        assert _run(env.detector.monitor_table_orders(orders=env.orders)) == []

    assert env.state.previous_tables_state["99"] == 0.0, (
        "无消费桌必须推进状态到 0，否则每轮都会重新判'有变化'（0c94084 的回归）"
    )
    assert env.state.previous_table_orders["99"] == []
    assert session.fetch_calls == [], "金额为 0 的桌没有明细可对，不该发取材请求"
    assert "取材失败" not in caplog.text

    # 第二轮：金额没变 → 走"状态无变化"分支（老实现里它每轮都是 changed + 告警）
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=TABLE_LOG.name):
        assert _run(env.detector.monitor_table_orders(orders=env.orders)) == []

    assert "餐桌状态无变化" in caplog.text
    assert "取材失败" not in caplog.text
    assert env.state.previous_tables_state["99"] == 0.0


def test_tracked_table_cleared_to_zero_advances_state_then_reused_table_is_recorded(env):
    """旧桌 24 → 0：状态推进到 0、按金额佐证确认整桌退菜；复用桌号点同一道菜能再入库。"""
    first = _pos_line("t8_虾饺_001")
    second = _pos_line("t8_虾饺_002")
    _seed_tracked_table(env.state, "8", 24.0, [first, second])

    for _ in range(DINE_IN_CANCEL_MISS_THRESHOLD):
        assert _round(env.detector, env.orders, [_table("8", 0.0)]) == []

    assert env.state.previous_tables_state["8"] == 0.0, (
        "清台后金额必须落到快照里；停在 24.0 就是 0c94084 的回归（实机旧桌恒 {'88': 252.0}）"
    )
    with open(env.state_file, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["table_states"]["8"] == 0.0

    # 整桌退菜仍要三个条件：取材成功 + 连续 3 轮缺席 + 金额确实变小
    assert env.orders.cancelled == [("8", "虾饺", 2, "")]

    # 桌号复用：新客人点同一道菜（同菜名/单价/备注），必须重新入库
    changed = _round(env.detector, env.orders, [_table("8", 12.0, orders=[_pos_line("t8_虾饺_003")])])
    assert env.orders.restored == [("8", "虾饺")], (
        f"复用桌号的菜应回到 orders（恢复已取消行）或记成新增，实际 changed={changed!r}"
    )


def test_negative_amount_is_not_no_consumption_and_still_cancels_on_amount_evidence(env):
    """金额 < 0 是异常值：照常取材，≥3 轮后仍能按「金额变小」佐证走退菜。"""
    first = _pos_line("t8_虾饺_001")
    second = _pos_line("t8_虾饺_002")
    _seed_tracked_table(env.state, "8", 24.0, [first, second])

    session = _FakeTableSession([_table("8", -1.0, orders=[])])
    env.detector._session = session
    for _ in range(DINE_IN_CANCEL_MISS_THRESHOLD):
        _run(env.detector.monitor_table_orders(orders=env.orders))

    assert session.fetch_calls, "amount<0 不是「无消费桌」，必须照常取材"
    assert env.orders.cancelled == [("8", "虾饺", 2, "")]
    assert env.state.previous_tables_state["8"] == -1.0


def test_no_consumption_table_and_failed_table_are_reported_separately(env, caplog):
    """无消费桌（8 号桌金额 0）与明细取材失败（9 号桌）必须走两条路、两组日志。"""
    _seed_tracked_table(env.state, "8", 10.0, [_pos_line("t8_虾饺_001")])
    env.state.previous_tables_state["9"] = 30.0
    env.state.previous_table_orders["9"] = [_pos_line("t9_虾饺_001", table="9")]

    with caplog.at_level(logging.INFO, logger=TABLE_LOG.name):
        _round(
            env.detector,
            env.orders,
            [_table("8", 0.0), _table("9", 40.0, detail="fail")],
        )

    assert env.state.previous_tables_state["8"] == 0.0, "无消费桌的状态要照常推进"
    assert env.state.previous_tables_state["9"] == 30.0, "取材失败的桌要保留旧金额"
    assert "取材失败" in caplog.text and "9" in caplog.text
    failure_line = next(line for line in caplog.text.splitlines() if "取材失败" in line)
    assert "8" not in failure_line.split(":")[-1], (
        f"无消费桌不该出现在取材失败告警里: {failure_line!r}"
    )


# ── CORR-06：餐桌级取材失败（scrape_table_data）──────────────────────────────


def _bare_session(**attrs):
    """只验取材契约的 PosSession：不建浏览器、不登录（同 FetchTableOrdersFailureShapeTests）。"""
    session = PosSession.__new__(PosSession)
    session.logger = logging.getLogger("test-pos-session-shape")
    for key, value in attrs.items():
        setattr(session, key, value)
    return session


class _StubHttp:
    def __init__(self, status, data):
        self.status = status
        self.data = data

    async def request_with_recovery(self, *args, **kwargs):
        return self.status, self.data


async def _always_ready():
    return True


def test_parse_api_response_marks_unrecognized_shape_as_none():
    """结构不认识（例如 success=false 的错误体）→ None；形态认识但空 → []。"""
    session = _bare_session()

    assert session._parse_api_response({"success": False, "message": "未登录"}) is None
    assert session._parse_api_response({"data": []}) == []
    assert session._parse_api_response([]) == []
    assert session._parse_api_response({"rows": []}) == []
    assert session._parse_api_response({"unexpected": "shape"}) is None


def test_scrape_table_data_separates_failure_from_empty():
    """餐桌列表：HTTP 失败/结构不认识 → None；取材成功但没桌 → []；非营业时间 → []。"""
    failing = _bare_session(_creds=object(), http=_StubHttp(500, {}))
    failing._ensure_initialized = _always_ready
    failing._is_business_hours = lambda: True
    assert _run(failing.scrape_table_data()) is None

    unauthorized = _bare_session(
        _creds=object(), http=_StubHttp(200, {"success": False, "message": "未登录"})
    )
    unauthorized._ensure_initialized = _always_ready
    unauthorized._is_business_hours = lambda: True
    assert _run(unauthorized.scrape_table_data()) is None

    no_tables = _bare_session(_creds=object(), http=_StubHttp(200, {"data": []}))
    no_tables._ensure_initialized = _always_ready
    no_tables._is_business_hours = lambda: True
    assert _run(no_tables.scrape_table_data()) == []

    closed = _bare_session(_creds=object(), http=_StubHttp(200, {"data": []}))
    closed._ensure_initialized = _always_ready
    closed._is_business_hours = lambda: False
    assert _run(closed.scrape_table_data()) == []


def test_table_list_failure_freezes_every_tracked_table(env, caplog):
    """餐桌列表取材失败：一个明细请求都不发，所有桌的状态原地不动。"""
    line = _pos_line("t8_虾饺_001")
    _seed_tracked_table(env.state, "8", 10.0, [line])
    session = _FakeTableSession([_table("8", 20.0)], list_detail="fail")
    env.detector._session = session

    with caplog.at_level(logging.WARNING, logger=TABLE_LOG.name):
        assert _run(env.detector.monitor_table_orders(orders=env.orders)) == []

    assert env.state.previous_tables_state["8"] == 10.0
    assert env.state.previous_table_orders["8"] == [line]
    assert session.fetch_calls == []
    assert "餐桌列表取材失败" in caplog.text


# ── CORR-06：明细级取材失败（fetch_table_orders）─────────────────────────────


def test_detail_failure_freezes_state_while_empty_detail_advances_it(env):
    """明细失败 → None：不推进状态；取材成功但为空 → 才允许推进。"""
    line = _pos_line("t8_虾饺_001")
    _seed_tracked_table(env.state, "8", 10.0, [line])

    assert _round(env.detector, env.orders, [_table("8", 20.0, detail="fail")]) == []
    assert env.state.previous_tables_state["8"] == 10.0
    assert env.state.previous_table_orders["8"] == [line]

    assert _round(env.detector, env.orders, [_table("8", 20.0, orders=[])]) == []
    assert env.state.previous_tables_state["8"] == 20.0


# ── 独立验证（verifier-db / V3）：票 05 的现场形态在真实 state store 上复现 ──────
#
# 与上面的实现者用例相互独立：自带的桩 session、直接断言落盘 json，并把
# 「假告警消失」（三轮零条取材失败）与「复用桌号的新菜能入库」钉在一起。


class _V3TableSession:
    """最小 POS 桩：只按桌号给出金额与明细，不连 POS、不复用本文件其它 helper。"""

    def __init__(self, tables):
        self._tables = [dict(table) for table in tables]
        self.fetched = []

    async def ensure_ready(self):
        return True

    async def scrape_table_data(self):
        return [dict(table) for table in self._tables]

    def resolve_point_id(self, table_number):
        return f"v3-{table_number}"

    async def fetch_table_orders(self, table_number, point_id):
        self.fetched.append((table_number, point_id))
        for table in self._tables:
            if table["table_number"] == table_number:
                return [dict(line) for line in table["lines"]]
        return []


def _v3_table(number, amount, lines=()):
    return {
        "table_number": number,
        "amount": amount,
        "point_id": f"v3-{number}",
        "orders": [],
        "lines": list(lines),
    }


def _v3_line(flow_id, dish="虾饺", price=12.0, table="88"):
    return {
        "business_flow_id": flow_id,
        "table_number": table,
        "dish_name": dish,
        "quantity": 1,
        "price": price,
        "total_amount": price,
        "status": "未结",
        "order_time": datetime(2026, 8, 18, 10, 0, tzinfo=CHINA_TZ),
        "source": "dine_in",
        "station": "shulong",
        "notes": "",
    }


def _v3_detector(tmp_path):
    state_file = tmp_path / "v3_table_state.json"
    state = ScraperStateStore(
        table_state_file=str(state_file),
        delivery_bills_file=str(tmp_path / "v3_delivery_bills.json"),
    )
    return (
        state,
        state_file,
        TableChangeDetector(session=None, state_store=state, logger_=TABLE_LOG),
    )


def test_v3_ticket05_new_zero_amount_table_advances_and_stops_alarming(tmp_path, caplog):
    """票 05 子例 A：新桌 amount=0，三轮都不该被读成「取材失败」。"""
    state, state_file, detector = _v3_detector(tmp_path)
    state.is_first_run = False
    session = _V3TableSession([_v3_table("99", 0.0)])
    detector._session = session
    port = _RecordingOrdersPort(restore_returns_row=True)

    texts = []
    for _ in range(3):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=TABLE_LOG.name):
            assert _run(detector.monitor_table_orders(orders=port)) == []
        texts.append(caplog.text)
        assert state.previous_tables_state.get("99") == 0.0, (
            f"无消费桌的金额没推进到 0（0c94084 的现场：新桌三轮后恒 "
            f"{state.previous_tables_state!r}），每轮都会被重新判'有变化'"
        )

    assert session.fetched == [], "金额为 0 的桌没有明细可对，不该发取材请求"
    assert not any("取材失败" in text for text in texts), (
        f"amount=0 的桌被报成了取材失败（假告警）: {[t for t in texts if t]}"
    )
    assert "餐桌状态无变化" in texts[1], "第二轮起不该再判'有变化'"

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["table_states"]["99"] == 0.0, "推进后的状态必须真的落盘"


def test_v3_ticket05_cleared_table_then_reused_number_lands_a_new_dish(tmp_path):
    """票 05 子例 B：旧桌 252→0 推进状态并按金额佐证退菜；复用桌号的新菜要入库。"""
    state, _, detector = _v3_detector(tmp_path)
    state.previous_tables_state = {"88": 252.0}
    state.previous_table_orders = {
        "88": [
            _v3_line("v3-88-1", dish="烧卖", price=126.0),
            _v3_line("v3-88-2", dish="烧卖", price=126.0),
        ]
    }
    state.is_first_run = False
    port = _RecordingOrdersPort(restore_returns_row=True)

    detector._session = _V3TableSession([_v3_table("88", 0.0)])
    for _ in range(DINE_IN_CANCEL_MISS_THRESHOLD):
        assert _run(detector.monitor_table_orders(orders=port)) == []

    assert state.previous_tables_state["88"] == 0.0, (
        f"清台后金额必须落进快照（0c94084 的现场是恒 {{'88': 252.0}}），"
        f"实际 {state.previous_tables_state!r}"
    )
    assert port.cancelled == [("88", "烧卖", 2, "")], "整桌退菜仍要按「金额确实变小」佐证"

    # 桌号复用：新客人点了另一道菜。没有可恢复的旧行 → 必须作为新增进 orders 变更集。
    detector._session = _V3TableSession(
        [_v3_table("88", 12.0, [_v3_line("v3-88-3", dish="虾饺", price=12.0)])]
    )
    changed = _run(detector.monitor_table_orders(orders=_RecordingOrdersPort()))
    assert [row["dish_name"] for row in changed] == ["虾饺"], (
        f"复用桌号点的新菜必须能入库（0c94084 回归时这里恒为空），实际 {changed!r}"
    )
