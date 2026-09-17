#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""磁盘守护：分级、快照与可用空间读取。"""

import unittest

from services.disk_guard import (
    LEVEL_CRITICAL,
    LEVEL_OK,
    LEVEL_WARNING,
    classify,
    min_free_mb,
    read_usage,
)


class ClassifyTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(classify(10, warn_pct=85, critical_pct=92), LEVEL_OK)
        self.assertEqual(classify(85, warn_pct=85, critical_pct=92), LEVEL_WARNING)
        self.assertEqual(classify(91.9, warn_pct=85, critical_pct=92), LEVEL_WARNING)
        self.assertEqual(classify(92, warn_pct=85, critical_pct=92), LEVEL_CRITICAL)
        self.assertEqual(classify(100, warn_pct=85, critical_pct=92), LEVEL_CRITICAL)


class ReadUsageTest(unittest.TestCase):
    def test_reads_real_path(self):
        usage = read_usage(".")
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertGreater(usage.total_bytes, 0)
        self.assertGreaterEqual(usage.used_pct, 0.0)

    def test_missing_path_returns_none(self):
        self.assertIsNone(read_usage("/definitely/not/a/real/path/luyun"))


class MinFreeTest(unittest.TestCase):
    def test_returns_value_for_real_path(self):
        free = min_free_mb(["."])
        self.assertIsNotNone(free)
        assert free is not None
        self.assertGreater(free, 0)

    def test_returns_none_when_nothing_readable(self):
        self.assertIsNone(min_free_mb(["/definitely/not/a/real/path/luyun"]))


if __name__ == "__main__":
    unittest.main()
