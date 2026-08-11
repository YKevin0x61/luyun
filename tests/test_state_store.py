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


if __name__ == "__main__":
    unittest.main()
