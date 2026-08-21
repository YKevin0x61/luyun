#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ScraperStateStore persistence tests."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from database import CHINA_TZ
from scraper.state_store import ScraperStateStore


class SaveTableStateDatetimeTest(unittest.TestCase):
    """Regression: order_time is a datetime in previous_table_orders."""

    def test_save_table_state_serializes_datetime_order_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "table_state.json")
            store = ScraperStateStore(table_state_file=path, delivery_bills_file=str(Path(tmp) / "d.json"))
            when = datetime(2026, 7, 28, 21, 16, 16, tzinfo=CHINA_TZ)
            store.previous_tables_state = {"A1": 88.0}
            store.previous_table_orders = {
                "A1": [
                    {
                        "table_number": "A1",
                        "dish_name": "虾饺",
                        "quantity": 1,
                        "order_time": when,
                        "price": 18.0,
                    }
                ]
            }
            store.is_first_run = False

            store.save_table_state()

            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["table_states"], {"A1": 88.0})
            self.assertEqual(
                data["table_orders"]["A1"][0]["order_time"],
                when.isoformat(),
            )


class DeliveryBillsRolloverTest(unittest.TestCase):
    def test_load_keeps_bills_when_biz_date_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            path.write_text(
                json.dumps({
                    "biz_date": "2026-08-20",
                    "bills": ["id-1"],
                    "bill_state": {
                        "YY001301-260820-0001": {
                            "bs_id": "id-1",
                            "miss_count": 0,
                            "cancelled": False,
                        }
                    },
                    "last_prev_day_cancel_sweep_biz_date": "2026-08-19",
                }),
                encoding="utf-8",
            )
            store = ScraperStateStore(
                table_state_file=str(Path(tmp) / "t.json"),
                delivery_bills_file=str(path),
            )
            store.current_biz_date = lambda: "2026-08-21"
            store.collected_delivery_bills = store.load_delivery_bills()
            self.assertIn("id-1", store.collected_delivery_bills)
            self.assertIn("YY001301-260820-0001", store.delivery_bill_state)
            self.assertEqual(store.last_prev_day_cancel_sweep_biz_date, "2026-08-19")

    def test_previous_biz_date_of(self):
        from scraper.state_store import previous_biz_date_of

        self.assertEqual(previous_biz_date_of("2026-08-21"), "2026-08-20")


if __name__ == "__main__":
    unittest.main()
