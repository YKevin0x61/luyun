#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from config import settings
from services.scraper_health import (
    current_biz_date_str,
    merge_health,
    read_health,
    record_reconcile_summary,
    write_health,
)


class ScraperHealthTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_dir = settings.DATABASE_DIR
        settings.DATABASE_DIR = self._tmpdir.name

    def tearDown(self):
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    def test_write_and_read_health(self):
        write_health({"biz_date": "2026-06-07", "api_failures": 1})
        data = read_health()
        self.assertEqual(data["biz_date"], "2026-06-07")
        self.assertEqual(data["api_failures"], 1)

    def test_merge_preserves_last_reconcile(self):
        write_health({"last_reconcile": {"missed_qty": 5}})
        merge_health(api_failures=2, biz_date="2026-06-07")
        data = read_health()
        self.assertEqual(data["api_failures"], 2)
        self.assertEqual(data["last_reconcile"]["missed_qty"], 5)

    def test_record_reconcile_summary(self):
        record_reconcile_summary(
            "2026-06-07",
            missed_keys=3,
            missed_qty=4.0,
            miss_rate_pct=0.5,
            report_md="/tmp/a.md",
        )
        data = read_health()
        self.assertEqual(data["last_reconcile"]["missed_keys"], 3)
        self.assertIn("report_md", data["last_reconcile"])

    def test_current_biz_date_format(self):
        value = current_biz_date_str()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
