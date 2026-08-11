#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta, timezone

from services.urgency_policy import (
    high_cutoff,
    high_threshold_ms,
    level_for_wait_ms,
    urgent_cutoff,
    urgent_threshold_ms,
)


class UrgencyPolicyTest(unittest.TestCase):
    def test_thresholds_match_config_defaults(self):
        self.assertEqual(urgent_threshold_ms(), 20 * 60 * 1000)
        self.assertEqual(high_threshold_ms(), 15 * 60 * 1000)

    def test_level_boundaries(self):
        urgent = urgent_threshold_ms()
        high = high_threshold_ms()
        self.assertEqual(level_for_wait_ms(0), "normal")
        self.assertEqual(level_for_wait_ms(high), "normal")
        self.assertEqual(level_for_wait_ms(high + 1), "high")
        self.assertEqual(level_for_wait_ms(urgent), "high")
        self.assertEqual(level_for_wait_ms(urgent + 1), "urgent")

    def test_cutoff_offsets(self):
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            urgent_cutoff(now),
            now - timedelta(milliseconds=urgent_threshold_ms()),
        )
        self.assertEqual(
            high_cutoff(now),
            now - timedelta(milliseconds=high_threshold_ms()),
        )


if __name__ == "__main__":
    unittest.main()
