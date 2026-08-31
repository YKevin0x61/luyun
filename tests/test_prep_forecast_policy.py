#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货预测纯计算策略单元测试（边界、收缩、缺失回退、40%上限、指标）。"""

from datetime import date, datetime, time
import unittest

from services.prep_forecast_policy import (
    DEFAULT_BASE_WEIGHTS,
    DEFAULT_SAFETY_STOCK_RATIO,
    MAX_SAFETY_STOCK_RATIO,
    MODEL_CALIBRATED_SAFETY_RATIO,
    SLOT_BASE_WEIGHTS,
    calculate_coverage,
    calculate_wape,
    compute_effective_safety_ratio,
    compute_signals,
    extract_signal_dates,
    find_complete_store_days,
    is_store_day_complete,
    pinball_loss,
    renormalize_and_combine,
    score_confidence,
)


class PrepForecastPolicyTests(unittest.TestCase):
    def test_is_store_day_complete(self):
        # 正常营业日：08:00 开门，21:30 打烊
        self.assertTrue(is_store_day_complete(time(8, 0), time(21, 30)))
        self.assertTrue(is_store_day_complete(time(8, 15), time(20, 0)))

        # 开门晚于 08:15
        self.assertFalse(is_store_day_complete(time(8, 30), time(21, 0)))
        # 打烊早于 20:00
        self.assertFalse(is_store_day_complete(time(8, 0), time(19, 30)))
        # 缺失输入
        self.assertFalse(is_store_day_complete(None, time(20, 30)))
        self.assertFalse(is_store_day_complete(time(8, 0), None))

    def test_find_complete_store_days(self):
        spans = {
            "2026-08-01": (time(9, 0), time(21, 0)),   # 晚开 -> 不完整
            "2026-08-02": (time(8, 0), time(21, 0)),   # 完整
            "2026-08-03": (time(8, 10), time(19, 0)),  # 早关 -> 不完整
            "2026-08-04": (time(8, 15), time(20, 30)), # 完整
        }
        complete = find_complete_store_days(spans)
        self.assertEqual(complete, {"2026-08-02", "2026-08-04"})

    def test_extract_signal_dates_strictly_before_target_and_skips_incomplete(self):
        target = "2026-08-30"  # 周日
        # 过去 4 个周日：08-23, 08-16, 08-09, 08-02
        # 假设 08-16 不完整
        complete = {
            "2026-08-29", "2026-08-28", "2026-08-27", "2026-08-26",
            "2026-08-25", "2026-08-24", "2026-08-23",
            "2026-08-09", "2026-08-02", "2026-07-26",
            # 目标日自身即使在集合中也绝不能被选入
            "2026-08-30",
        }
        signals = extract_signal_dates(target, complete)
        self.assertNotIn("2026-08-30", signals["same_week"])
        self.assertNotIn("2026-08-30", signals["recent"])
        self.assertNotIn("2026-08-30", signals["yesterday"])

        # same_week 跳过了 08-16，向前取到了 08-23, 08-09, 08-02, 07-26
        self.assertEqual(signals["same_week"], ["2026-08-23", "2026-08-09", "2026-08-02", "2026-07-26"])
        self.assertEqual(len(signals["recent"]), 7)
        self.assertEqual(signals["yesterday"], ["2026-08-29"])

    def test_renormalize_and_combine_with_missing_signals(self):
        # 完整三信号：0.5 * 10 + 0.3 * 20 + 0.2 * 30 = 5 + 6 + 6 = 17
        val, w = renormalize_and_combine(10.0, 20.0, 30.0, DEFAULT_BASE_WEIGHTS)
        self.assertAlmostEqual(val, 17.0)
        self.assertAlmostEqual(sum(w.values()), 1.0)

        # 缺少同星期 S (None)：剩余 recent (0.3) 和 yesterday (0.2) 归一化为 0.6 和 0.4
        # 0.6 * 20 + 0.4 * 30 = 12 + 12 = 24
        val_no_s, w_no_s = renormalize_and_combine(None, 20.0, 30.0, DEFAULT_BASE_WEIGHTS)
        self.assertAlmostEqual(val_no_s, 24.0)
        self.assertAlmostEqual(w_no_s["recent"], 0.6)
        self.assertAlmostEqual(w_no_s["yesterday"], 0.4)

        # 仅有同星期 S：val = 10.0, w = {"same": 1.0}
        val_only_s, w_only_s = renormalize_and_combine(10.0, None, None, DEFAULT_BASE_WEIGHTS)
        self.assertAlmostEqual(val_only_s, 10.0)
        self.assertAlmostEqual(w_only_s["same"], 1.0)

        # 全缺失：有 fallback
        val_fallback, w_fb = renormalize_and_combine(None, None, None, fallback_candidates=[15.0, 25.0])
        self.assertAlmostEqual(val_fallback, 20.0)

        # 全缺失无 fallback
        val_zero, _ = renormalize_and_combine(None, None, None)
        self.assertEqual(val_zero, 0.0)

    def test_compute_signals_preserves_real_zero_on_complete_days(self):
        # 08-23 和 08-16 是完整营业日，但该品真实销量为 0
        day_slot_values = {
            ("2026-08-23", "lunch"): 0.0,
            ("2026-08-16", "lunch"): 0.0,
        }
        signal_dates = {
            "same_week": ["2026-08-23", "2026-08-16"],
            "recent": [],
            "yesterday": [],
        }
        s_val, r_val, y_val = compute_signals(day_slot_values, "lunch", signal_dates)
        self.assertEqual(s_val, 0.0)  # 真实 0 保持 0.0，而不是 None
        self.assertIsNone(r_val)
        self.assertIsNone(y_val)

    def test_effective_safety_ratio_cap_and_floors(self):
        # 默认使用模型校准系数 (0.31)
        eff_default = compute_effective_safety_ratio(item_safety_ratio=None, model_safety_ratio=0.31)
        self.assertAlmostEqual(eff_default, 0.31)

        # 单品配置更高人工下限 (0.35) -> 提升至 0.35
        eff_high = compute_effective_safety_ratio(item_safety_ratio=0.35, model_safety_ratio=0.31)
        self.assertAlmostEqual(eff_high, 0.35)

        # 单品配置超过 40% (0.60) -> 强制封顶 0.40
        eff_capped = compute_effective_safety_ratio(item_safety_ratio=0.60, model_safety_ratio=0.31)
        self.assertAlmostEqual(eff_capped, 0.40)

    def test_weight_constraints(self):
        # 验证所有时段权重均符合已确定约束：
        # θsame ∈ [0.30, 0.70], θrecent ∈ [0.20, 0.60], θyesterday ∈ [0, 0.25], sum == 1.0
        for slot, w in SLOT_BASE_WEIGHTS.items():
            self.assertGreaterEqual(w["same"], 0.30, f"{slot} same weight too low")
            self.assertLessEqual(w["same"], 0.70, f"{slot} same weight too high")
            self.assertGreaterEqual(w["recent"], 0.20, f"{slot} recent weight too low")
            self.assertLessEqual(w["recent"], 0.60, f"{slot} recent weight too high")
            self.assertGreaterEqual(w["yesterday"], 0.0, f"{slot} yesterday weight too low")
            self.assertLessEqual(w["yesterday"], 0.25, f"{slot} yesterday weight too high")
            self.assertAlmostEqual(sum(w.values()), 1.0, places=5, msg=f"{slot} sum != 1")

    def test_evaluation_metrics(self):
        actuals = [10.0, 20.0, 30.0]
        forecasts = [12.0, 18.0, 30.0]
        # total abs err = 2 + 2 + 0 = 4; total actual = 60 => WAPE = 4 / 60 = 0.0667
        self.assertAlmostEqual(calculate_wape(actuals, forecasts), 4.0 / 60.0)

        # 覆盖率：cap >= actual
        caps = [12.0, 19.0, 30.0]  # 第二个 19 < 20 未覆盖
        self.assertAlmostEqual(calculate_coverage(actuals, caps), 2.0 / 3.0)

        # Pinball loss
        self.assertAlmostEqual(pinball_loss(20.0, 15.0, tau=0.8), (20.0 - 15.0) * 0.8)
        self.assertAlmostEqual(pinball_loss(10.0, 15.0, tau=0.8), (10.0 - 15.0) * (0.8 - 1.0))


if __name__ == "__main__":
    unittest.main()
