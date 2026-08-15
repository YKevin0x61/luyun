#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""POS dine-in / delivery classification for orders.source."""

import unittest

from scraper.order_source import SOURCE_DELIVERY, SOURCE_DINE_IN, classify_order_source


class ClassifyOrderSourceTest(unittest.TestCase):
    def test_dine_in_table_and_scan_channel(self):
        self.assertEqual(
            classify_order_source(table_number="A区22", order_source="扫码点餐", people_qty=2),
            SOURCE_DINE_IN,
        )
        self.assertEqual(
            classify_order_source(table_number="8", order_source="POS", people_qty=1),
            SOURCE_DINE_IN,
        )
        self.assertEqual(
            classify_order_source(table_number="B区福运", order_source="移动银台"),
            SOURCE_DINE_IN,
        )

    def test_delivery_by_order_source(self):
        self.assertEqual(
            classify_order_source(table_number="美团47", order_source="美团", people_qty=0),
            SOURCE_DELIVERY,
        )
        self.assertEqual(
            classify_order_source(order_source="淘宝闪购", people_qty=1),
            SOURCE_DELIVERY,
        )

    def test_delivery_by_legacy_bill_source(self):
        self.assertEqual(
            classify_order_source(bill_source="美团", people_qty=2),
            SOURCE_DELIVERY,
        )

    def test_delivery_by_zero_people(self):
        self.assertEqual(
            classify_order_source(table_number="外卖", people_qty=0),
            SOURCE_DELIVERY,
        )
        self.assertEqual(
            classify_order_source(table_number="A区1", people_qty="0"),
            SOURCE_DELIVERY,
        )

    def test_missing_people_is_not_delivery(self):
        self.assertEqual(classify_order_source(table_number="A区1"), SOURCE_DINE_IN)
        self.assertEqual(
            classify_order_source(table_number="A区1", people_qty=""),
            SOURCE_DINE_IN,
        )
