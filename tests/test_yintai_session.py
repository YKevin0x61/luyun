#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YintaiSession：取材语义（None vs []）、形状转换、桌号→pointId 映射。"""

import unittest
from unittest.mock import AsyncMock

from scraper.yintai_client import YintaiAuthError, YintaiCredentials
from scraper.yintai_session import (
    YintaiSession,
    _to_hhmm,
    to_legacy_detail_shape,
)

CREDS = YintaiCredentials(
    base_url="https://shop.example.com",
    dev_id="dev-1",
    access_key="ak-1",
    access_token="at-1",
    dev_code="097",
    user_id="1001",
)


class _FakeCatalog:
    """OrderLineBuilder 需要 ``await catalog.resolve(dish_name) -> 档口名``。"""

    async def resolve(self, dish_name):
        return "热菜"


class _Bundle:
    def __init__(self):
        self.extra = {
            "yintai": {
                "base_url": CREDS.base_url,
                "dev_id": CREDS.dev_id,
                "access_key": CREDS.access_key,
                "access_token": CREDS.access_token,
                "dev_code": CREDS.dev_code,
                "user_id": CREDS.user_id,
            }
        }


class _FakePoint:
    def __init__(self, name, pid, amount, occupied, bill_id=None, people=None, progress=""):
        self.point_name = name
        self.point_id = pid
        self.last_total = amount
        self.bill_id = bill_id
        self.people_qty = people
        self.serving_progress = progress
        self.is_occupied = occupied


def _session(fake_client=None) -> YintaiSession:
    session = YintaiSession(dish_catalog=_FakeCatalog(), logger=__import__("logging").getLogger("t"))
    session.inject_credentials(_Bundle())
    if fake_client is not None:
        session._client = fake_client
    return session


class HhmmTest(unittest.TestCase):
    def test_iso_and_plain(self):
        self.assertEqual(_to_hhmm("2026-10-29T13:42:44.407"), "13:42")
        self.assertEqual(_to_hhmm("13:42"), "13:42")
        self.assertEqual(_to_hhmm(""), "")
        self.assertEqual(_to_hhmm(None), "")


class LegacyShapeTest(unittest.TestCase):
    def test_maps_fields_and_unit_price(self):
        payload = to_legacy_detail_shape(
            [
                {
                    "itemName": "金牌虾饺皇",
                    "lastQty": 2,
                    "lastSubtotal": 60,
                    "orderTime": "2026-10-29T13:48:43.393",
                    "itemSizeName": "-",
                }
            ],
            table_number="2",
            bill_id="bs-1",
            people_qty=2,
        )
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["bsCode"], "bs-1")
        self.assertEqual(payload["data"]["pointName"], "2")
        item = payload["data"]["scDetail"][0]
        self.assertEqual(item["itemName"], "金牌虾饺皇")
        self.assertEqual(item["lastPrice"], 30.0)
        self.assertEqual(item["orderTime"], "13:48")

    def test_zero_qty_does_not_divide(self):
        payload = to_legacy_detail_shape(
            [{"itemName": "赠品", "lastQty": 0, "lastSubtotal": 0}], table_number="1"
        )
        self.assertEqual(payload["data"]["scDetail"][0]["lastPrice"], 0.0)


class ScrapeTableDataTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_only_occupied_tables(self):
        fake = AsyncMock()
        fake.fetch_point_states = AsyncMock(
            return_value=[
                _FakePoint("1", "p1", 0.0, False),
                _FakePoint("2", "p2", 208.0, True, bill_id="bs-1", people=2, progress="0/9"),
            ]
        )
        session = _session(fake)

        tables = await session.scrape_table_data()

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["table_number"], "2")
        self.assertEqual(tables[0]["amount"], 208.0)
        self.assertEqual(tables[0]["point_id"], "p2")
        self.assertEqual(session.resolve_point_id("1"), "p1")
        self.assertEqual(session.resolve_point_id("nope"), "")

    async def test_auth_error_returns_none(self):
        fake = AsyncMock()
        fake.fetch_point_states = AsyncMock(side_effect=YintaiAuthError("解密失败"))
        session = _session(fake)

        self.assertIsNone(await session.scrape_table_data())
        self.assertIn("解密失败", session.last_auth_error)

    async def test_no_credentials_returns_none(self):
        session = YintaiSession(dish_catalog=_FakeCatalog())
        self.assertIsNone(await session.scrape_table_data())
        self.assertFalse(session.no_credentials is False)


class FetchTableOrdersTest(unittest.IsolatedAsyncioTestCase):
    async def _ready(self, items):
        fake = AsyncMock()
        fake.fetch_point_states = AsyncMock(
            return_value=[_FakePoint("2", "p2", 60.0, True, bill_id="bs-1", people=2)]
        )
        fake.fetch_bill_items = AsyncMock(return_value=items)
        session = _session(fake)
        await session.scrape_table_data()
        return session

    async def test_empty_bill_id_means_no_dishes(self):
        fake = AsyncMock()
        fake.fetch_point_states = AsyncMock(return_value=[_FakePoint("3", "p3", 0.0, True, bill_id=None)])
        session = _session(fake)
        await session.scrape_table_data()

        self.assertEqual(await session.fetch_table_orders("3", "p3"), [])

    async def test_missing_point_id_returns_none(self):
        session = await self._ready([])
        self.assertIsNone(await session.fetch_table_orders("2", ""))

    async def test_dishes_expanded_to_order_lines(self):
        session = await self._ready(
            [
                {
                    "itemName": "金牌虾饺皇",
                    "lastQty": 1,
                    "lastSubtotal": 30,
                    "orderTime": "2026-10-29T13:48:43.393",
                }
            ]
        )

        lines = await session.fetch_table_orders("2", "p2")

        self.assertIsNotNone(lines)
        self.assertGreaterEqual(len(lines), 1)
        row = lines[0]
        self.assertEqual(row["table_number"], "2")
        self.assertEqual(row["dish_name"], "金牌虾饺皇")
        self.assertEqual(float(row["quantity"]), 1.0)
        self.assertEqual(float(row["price"]), 30.0)

    async def test_auth_error_on_detail_returns_none(self):
        fake = AsyncMock()
        fake.fetch_point_states = AsyncMock(
            return_value=[_FakePoint("2", "p2", 60.0, True, bill_id="bs-1")]
        )
        fake.fetch_bill_items = AsyncMock(side_effect=YintaiAuthError("解密失败"))
        session = _session(fake)
        await session.scrape_table_data()

        self.assertIsNone(await session.fetch_table_orders("2", "p2"))


if __name__ == "__main__":
    unittest.main()
