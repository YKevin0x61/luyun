#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""复盘的度量模块：参考值与下达量是两条并列基线。


这里钉住三件事：谁选择基线（只有 measure）、旧快照缺 final_qty 时怎样回落、
当天摘要与 90 天汇总是否用同一套公式。表驱动，不碰数据库。
"""

import ast
import pathlib
import unittest

from services.prep_review import measure

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BasisTests(unittest.TestCase):
    def test_snapshot_without_manual_edit_reports_reference_as_planned(self):
        row = measure.basis({"suggested_qty": 100, "final_qty": None, "qty_source": "suggested"})
        self.assertEqual(row, {"reference_qty": 100.0, "planned_qty": 100.0, "planned_source": "suggested"})

    def test_manual_final_is_the_planned_basis(self):
        row = measure.basis({"suggested_qty": 50, "final_qty": 60, "qty_source": "manual"})
        self.assertEqual(row["reference_qty"], 50.0)
        self.assertEqual(row["planned_qty"], 60.0)
        self.assertEqual(row["planned_source"], "manual")

    def test_legacy_row_with_final_but_suggested_source_is_still_manual(self):
        """旧快照 / 手工修数可能留下 final != suggested 但 qty_source 默认值的情况。"""
        row = measure.basis({"suggested_qty": 50, "final_qty": 60, "qty_source": "suggested"})
        self.assertEqual(row["planned_qty"], 60.0)
        self.assertEqual(row["planned_source"], "manual")

    def test_same_value_still_marks_manual_when_source_says_manual(self):
        row = measure.basis({"suggested_qty": 50, "final_qty": 50, "qty_source": "manual"})
        self.assertEqual(row["planned_source"], "manual")

class MetricsTests(unittest.TestCase):
    def _rows(self):
        return [
            {"day": "2026-10-19", "reference_qty": 100.0, "planned_qty": 100.0, "sold": 80.0, "planned_source": "suggested"},
            {"day": "2026-10-20", "reference_qty": 50.0, "planned_qty": 60.0, "sold": 70.0, "planned_source": "manual"},
            {"day": "2026-10-21", "reference_qty": 40.0, "planned_qty": 40.0, "sold": None, "planned_source": "suggested"},
        ]

    def test_two_baselines_report_different_conclusions_from_the_same_rows(self):
        body = measure.metrics(self._rows())
        self.assertEqual(body["measured_days"], 2)
        self.assertEqual(body["measured_item_days"], 2)
        self.assertEqual(body["sold_total"], 150.0)
        self.assertEqual(body["planned_sources"], {"manual": 1, "suggested": 1})

        reference = body["reference"]
        self.assertEqual(reference["qty_total"], 150.0)
        self.assertAlmostEqual(reference["shortfall_qty"], 20.0)
        self.assertAlmostEqual(reference["overfill_qty"], 20.0)
        self.assertAlmostEqual(reference["absolute_deviation"], 40 / 150)
        self.assertAlmostEqual(reference["direction_deviation"], 0.0)
        self.assertAlmostEqual(reference["coverage_qty"], 130 / 150)
        self.assertEqual(reference["covered_items"], 1)
        self.assertAlmostEqual(reference["coverage_items"], 1 / 2)

        planned = body["planned"]
        self.assertEqual(planned["qty_total"], 160.0)
        self.assertAlmostEqual(planned["shortfall_qty"], 10.0)
        self.assertAlmostEqual(planned["overfill_qty"], 20.0)
        self.assertAlmostEqual(planned["absolute_deviation"], 30 / 150)
        self.assertAlmostEqual(planned["direction_deviation"], 10 / 150)
        self.assertAlmostEqual(planned["coverage_qty"], 140 / 150)
        self.assertEqual(planned["covered_items"], 1)

    def test_no_sold_quantity_means_no_ratio_in_either_baseline(self):
        body = measure.metrics([])
        self.assertEqual(body["measured_days"], 0)
        self.assertIsNone(body["reference"]["absolute_deviation"])
        self.assertIsNone(body["planned"]["absolute_deviation"])

class SnapshotSummaryTests(unittest.TestCase):
    def test_day_summary_uses_the_same_two_baselines(self):
        items = [
            {"reference_qty": 100.0, "planned_qty": 100.0, "sold_qty": 80.0, "sold_out_at": None, "sold_out_reliable": None, "same_rules": True},
            {"reference_qty": 50.0, "planned_qty": 60.0, "sold_qty": 70.0, "sold_out_at": "x", "sold_out_reliable": True, "same_rules": True},
            {"reference_qty": 40.0, "planned_qty": 40.0, "sold_qty": None, "sold_out_at": None, "sold_out_reliable": None, "same_rules": None},
        ]
        body = measure.snapshot_summary(items)
        self.assertEqual(body["measured"], 2)
        self.assertEqual(body["pending"], 1)
        self.assertEqual(body["sold_out"], 1)
        self.assertEqual(body["sold_out_reliable"], 1)
        self.assertEqual(body["sold_total"], 150.0)
        self.assertAlmostEqual(body["reference"]["absolute_deviation"], 40 / 150)
        self.assertAlmostEqual(body["planned"]["absolute_deviation"], 30 / 150)
        self.assertAlmostEqual(body["planned"]["shortfall_qty"], 10.0)

    def test_empty_day_still_has_both_baseline_shapes(self):
        body = measure.snapshot_summary([])
        self.assertEqual(body["measured"], 0)
        self.assertIsNone(body["reference"]["coverage_items"])
        self.assertIsNone(body["planned"]["coverage_items"])

class DayTotalsTests(unittest.TestCase):
    def test_day_totals_carry_both_directions(self):
        rows = [
            {"day": "2026-10-19", "reference_qty": 100.0, "planned_qty": 100.0, "sold": 80.0},
            {"day": "2026-10-19", "reference_qty": 50.0, "planned_qty": 60.0, "sold": 70.0},
        ]
        totals = measure.day_totals(rows)
        row = totals["2026-10-19"]
        self.assertEqual(row["items"], 2)
        self.assertAlmostEqual(row["sold"], 150.0)
        self.assertAlmostEqual(row["reference_direction"], 0.0)
        self.assertAlmostEqual(row["planned_direction"], 10.0)
        self.assertAlmostEqual(row["reference_absolute"], 40.0)
        self.assertAlmostEqual(row["planned_absolute"], 30.0)

class SourceGuardTests(unittest.TestCase):
    """两条基线只能由 measure 选：别处再写一次 `final_qty if ... else suggested_qty` 就算回归。"""

    def test_only_measure_selects_between_the_two_baselines(self):
        offenders = []
        for path in (ROOT / "services").rglob("*.py"):
            if path == ROOT / "services" / "prep_review" / "measure.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.IfExp, ast.BoolOp)):
                    continue
                text = ast.unparse(node)
                if "final_qty" in text and "suggested_qty" in text:
                    offenders.append("%s:%d %s" % (path.relative_to(ROOT), node.lineno, text[:80]))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()