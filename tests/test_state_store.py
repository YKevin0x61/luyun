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


class AtomicStateFileTest(unittest.TestCase):
    """状态文件必须原子写；解析失败不许被静默当成「首次运行」（CORR-06）。

    现场形态：`open(path, "w")` + `json.dump` 直接覆盖。断电 / `kill -9` / 盘满留下
    半截 JSON 后，`load_table_state` 的 except 只 warning，`is_first_run` 保持 `True`，
    下一轮把所有在座桌台按「新增」重放（`table_change_detector` 的判据就是它），
    外卖侧还会丢 `delivery_bill_state` 与 sweep 标记。去重兜底在
    `db_core/orders_repo.py`（按 `business_flow_id` 查重），所以不重复计金额，
    但那一轮的全量查重 + nudge 风暴是实打实的。
    """

    def _store(self, tmp, name="table_state.json"):
        from pathlib import Path

        path = Path(tmp) / name
        return ScraperStateStore(
            table_state_file=str(path),
            delivery_bills_file=str(Path(tmp) / "delivery.json"),
        ), path

    def test_truncated_table_state_is_not_treated_as_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self._store(tmp)
            store.previous_tables_state = {"A1": 88.0}
            store.is_first_run = False
            store.save_table_state()
            # 模拟写一半就断电：留下一个合法的 JSON 前缀。
            path.write_text(path.read_text(encoding="utf-8")[:20], encoding="utf-8")

            reloaded, _ = self._store(tmp)
            self.assertFalse(
                reloaded.is_first_run,
                "截断的状态文件被当成首次运行 —— 下一轮会把所有在座桌台当新增重放",
            )
            self.assertEqual(reloaded.previous_tables_state, {})

    def test_truncated_delivery_bills_do_not_reset_sweep_marker(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.json"
            path.write_text("{not json", encoding="utf-8")
            store = ScraperStateStore(
                table_state_file=str(Path(tmp) / "t.json"),
                delivery_bills_file=str(path),
            )
            self.assertEqual(store.collected_delivery_bills, set())
            self.assertEqual(store.delivery_bill_state, {})

    def test_save_leaves_no_tmp_file_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store, path = self._store(tmp)
            store.previous_tables_state = {"A1": 88.0}
            store.save_table_state()
            store.save_delivery_bills()

            leftovers = sorted(p.name for p in Path(tmp).iterdir() if p.suffix == ".tmp")
            self.assertEqual(leftovers, [], f"临时文件没被清掉：{leftovers}")
            self.assertTrue(path.exists())

    def test_save_replaces_the_whole_file(self):
        """覆盖写不能留下旧内容的尾巴（原子替换的直接后果）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self._store(tmp)
            store.previous_tables_state = {"A" * 200: 1.0}
            store.is_first_run = False
            store.save_table_state()
            store.previous_tables_state = {}
            store.save_table_state()

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["table_states"], {})


if __name__ == "__main__":
    unittest.main()
