#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班落笔闸：一道判据、五个写口。

这里只验判据本身：给一份「那天谁在册、谁请假、那天有没有班」的读层快照，问
「这一格能不能落笔」。写口翻成传输语义（单格抛错 / 批量 skipped）各自那组用例验，
本文件只钉住 single_error 的对照表 —— 它是单格口唯一允许的翻译。
"""

import ast
import unittest
from pathlib import Path

from services.business_day import shift_business_date
from services.scheduling import paint_gate

TODAY = "2026-09-26"


def _limits():
    # 89 = store 的 EXPANSION_DAYS - 1（展开窗口铺到哪天由调用方给）。
    return paint_gate.limits_for(TODAY, shift_business_date(TODAY, 89))


def _person(*, approved=True, left=None):
    return paint_gate.Person(approved=approved, left=left)


def _reads(roster=None, leave=None, shift_days=None):
    return paint_gate.Reads(
        roster={1: _person()} if roster is None else roster,
        leave={} if leave is None else leave,
        shift_days=set() if shift_days is None else shift_days,
    )


class PaintGateLimitsTest(unittest.TestCase):
    def test_window_is_thirty_days_back_and_eighty_nine_forward(self):
        limits = _limits()
        self.assertEqual(limits.today, TODAY)
        self.assertEqual(limits.first_day, shift_business_date(TODAY, -30))
        self.assertEqual(limits.first_day, "2026-08-27")
        self.assertEqual(limits.last_day, shift_business_date(TODAY, 89))
        self.assertEqual(limits.last_day, "2026-12-24")

    def test_window_edges_are_inclusive(self):
        limits = _limits()
        reads = _reads()
        self.assertIsNone(
            paint_gate.judge(paint_gate.PAINT_SHIFT, (1, limits.first_day), reads, limits)
        )
        self.assertIsNone(
            paint_gate.judge(paint_gate.PAINT_SHIFT, (1, limits.last_day), reads, limits)
        )
        before = paint_gate.judge(
            paint_gate.PAINT_SHIFT, (1, shift_business_date(limits.first_day, -1)), reads, limits
        )
        after = paint_gate.judge(
            paint_gate.PAINT_SHIFT, (1, shift_business_date(limits.last_day, 1)), reads, limits
        )
        self.assertEqual((before.reason, before.detail), ("past_day", limits.first_day))
        self.assertEqual((after.reason, after.detail), ("beyond_window", limits.last_day))


class PaintGateRulesTest(unittest.TestCase):
    def test_a_clean_cell_passes_every_action(self):
        reads = _reads(shift_days={(1, TODAY)})
        for action in paint_gate.PAINT_ACTIONS:
            with self.subTest(action=action):
                self.assertIsNone(paint_gate.judge(action, (1, TODAY), reads, _limits()))

    def test_pending_or_missing_person_is_out_of_roster(self):
        limits = _limits()
        pending = _reads(roster={1: _person(approved=False)})
        missing = _reads(roster={})
        self.assertEqual(
            paint_gate.judge(paint_gate.PAINT_SHIFT, (1, TODAY), pending, limits).reason,
            "out_of_roster",
        )
        self.assertEqual(
            paint_gate.judge(paint_gate.PAINT_SHIFT, (1, TODAY), missing, limits).reason,
            "out_of_roster",
        )

    def test_a_resigned_person_can_be_written_up_to_the_last_day(self):
        # 离职日当天仍是他最后一天（跟单格旧口径一致）；之后整块拒。
        limits = _limits()
        left = "2026-09-20"
        reads = _reads(roster={1: _person(left=left)}, shift_days={(1, left)})
        self.assertIsNone(paint_gate.judge(paint_gate.PAINT_SHIFT, (1, left), reads, limits))
        self.assertIsNone(
            paint_gate.judge(paint_gate.PAINT_SHIFT, (1, "2026-09-19"), reads, limits)
        )
        skip = paint_gate.judge(paint_gate.PAINT_SHIFT, (1, "2026-09-21"), reads, limits)
        self.assertEqual((skip.reason, skip.detail), ("resigned_employee", left))

    def test_a_resigned_person_without_a_date_is_refused_everywhere(self):
        limits = _limits()
        reads = _reads(roster={1: _person(left="")})
        skip = paint_gate.judge(paint_gate.PAINT_SHIFT, (1, "2026-09-01"), reads, limits)
        self.assertEqual(skip.reason, "resigned_employee")

    def test_a_leave_day_is_refused_for_every_action_that_writes(self):
        limits = _limits()
        reads = _reads(leave={1: {TODAY}}, shift_days={(1, TODAY)})
        for action in paint_gate.PAINT_ACTIONS:
            with self.subTest(action=action):
                skip = paint_gate.judge(action, (1, TODAY), reads, limits)
                self.assertEqual(skip.reason, "leave_approved")

    def test_half_rest_needs_a_shift_on_that_day(self):
        limits = _limits()
        self.assertEqual(
            paint_gate.judge(paint_gate.PAINT_HALF_REST, (1, TODAY), _reads(), limits).reason,
            "half_rest_needs_shift",
        )
        self.assertIsNone(
            paint_gate.judge(
                paint_gate.PAINT_HALF_REST, (1, TODAY), _reads(shift_days={(1, TODAY)}), limits
            )
        )

    def test_half_rest_on_a_leave_day_reports_the_leave_first(self):
        # 顺序与批量口旧口径一致：请假 → 那天有没有班次。
        limits = _limits()
        reads = _reads(leave={1: {TODAY}}, shift_days={(1, TODAY)})
        self.assertEqual(
            paint_gate.judge(paint_gate.PAINT_HALF_REST, (1, TODAY), reads, limits).reason,
            "leave_approved",
        )


class PaintGateOrderTest(unittest.TestCase):
    def test_roster_is_judged_before_the_window(self):
        # 批量口曾经先看在册再看窗口（批量端点早就这样）；这一条钉住统一后的顺序。
        limits = _limits()
        reads = _reads(roster={})
        skip = paint_gate.judge(paint_gate.PAINT_SHIFT, (1, limits.first_day), reads, limits)
        self.assertEqual(skip.reason, "out_of_roster")

    def test_judge_all_keeps_the_input_order(self):
        limits = _limits()
        reads = _reads(roster={1: _person(), 2: _person(approved=False)})
        cells = [(1, TODAY), (2, TODAY), (1, limits.last_day)]
        skips = paint_gate.judge_all(paint_gate.PAINT_SHIFT, cells, reads, limits)
        self.assertEqual(
            [None if s is None else s.reason for s in skips], [None, "out_of_roster", None]
        )

    def test_an_unknown_action_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            paint_gate.judge("paint", (1, TODAY), _reads(), _limits())


class PaintGateSingleErrorTest(unittest.TestCase):
    def test_single_writes_translate_skips_to_error_codes(self):
        limits = _limits()
        cases = {
            paint_gate.Skip("out_of_roster"): ("unknown_employee", "unknown_employee"),
            paint_gate.Skip("past_day", limits.first_day): ("past_day", limits.first_day),
            paint_gate.Skip("beyond_window", limits.last_day): ("beyond_window", limits.last_day),
            paint_gate.Skip("leave_approved"): ("leave_approved", "leave_approved"),
            paint_gate.Skip("half_rest_needs_shift"): (
                "half_rest_needs_shift",
                "half_rest_needs_shift",
            ),
            paint_gate.Skip("resigned_employee", ""): ("resigned_employee", "resigned_employee"),
            paint_gate.Skip("resigned_employee", "2026-09-20"): (
                "resigned_employee",
                "2026-09-20",
            ),
        }
        for skip, expected in cases.items():
            with self.subTest(reason=skip.reason, detail=skip.detail):
                self.assertEqual(paint_gate.single_error(skip), expected)


class PaintGateGuardTest(unittest.TestCase):
    """源码守卫：判据不许在别处再写一遍。"""

    REASONS = {
        "out_of_roster",
        "resigned_employee",
        "past_day",
        "beyond_window",
        "leave_approved",
        "half_rest_needs_shift",
    }
    NUMBERS = {"BACKFILL_DAYS", "MAX_BATCH_CELLS"}

    def test_scheduling_modules_do_not_copy_the_reasons(self):
        base = Path(__file__).resolve().parent.parent / "services" / "scheduling"
        offenders = []
        for path in sorted(base.glob("*.py")):
            if path.name == "paint_gate.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in self.REASONS
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        self.assertEqual(
            offenders,
            [],
            "判据理由只许出现在 paint_gate.py：这些字面量把判据又抄了一份，" + ", ".join(offenders),
        )

    def test_the_two_numbers_are_defined_once(self):
        base = Path(__file__).resolve().parent.parent / "services" / "scheduling"
        offenders = []
        for path in sorted(base.glob("*.py")):
            if path.name == "paint_gate.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                for name in sorted(names & self.NUMBERS):
                    # 只认「重新给了一个数」：`X = paint_gate.X` 是转出同一个对象，
                    # 不是第二份定义（api / 测试仍从 store 拿这两个名字）。
                    value = node.value
                    if isinstance(value, ast.Attribute) and value.attr == name:
                        continue
                    offenders.append(f"{path.name}:{node.lineno} {name}")
        self.assertEqual(
            offenders,
            [],
            "可补记天数与批量上限只许在 paint_gate.py 定义（store 只许转出）：" + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
