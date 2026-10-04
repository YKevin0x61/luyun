#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前后对照拼图（票 04）：两张实拍 → 一张左前右后的对照图。

只断言看得见的东西：左边是「前」、右边是「后」、两边都保持自己的长宽比不变形、
长边不超上限、图上有可辨认的标签、超限时宁可报错也不静默丢。
"""

import io
import unittest

from PIL import Image

from services.hygiene.images import (
    BEFORE_AFTER_GAP,
    BEFORE_AFTER_MAX_EDGE,
    BeforeAfterTooLarge,
    InvalidImageError,
    compose_before_after,
)


def _jpeg(size, color, quality=90) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


class ComposeBeforeAfterTest(unittest.TestCase):
    def _seam_x(self, image: Image.Image) -> int:
        """两张图之间那条白缝的最左列（沿中线找，那里不会压到标签）。"""
        y = image.height // 2
        for x in range(image.width - 1):
            if image.getpixel((x, y)) == (255, 255, 255):
                return x
        self.fail("没找到两张图之间的白缝")

    def test_left_is_before_and_right_is_after(self):
        composed = compose_before_after(
            _jpeg((800, 600), (220, 40, 40)), _jpeg((800, 600), (40, 40, 220))
        )

        image = _open(composed.data)
        self.assertGreater(image.width, image.height)
        left = image.getpixel((10, image.height // 2))
        right = image.getpixel((image.width - 10, image.height // 2))
        self.assertGreater(left[0], 150)
        self.assertLess(left[2], 120)
        self.assertGreater(right[2], 150)
        self.assertLess(right[0], 120)

    def test_output_is_a_jpeg_within_the_edge_limit(self):
        composed = compose_before_after(
            _jpeg((3000, 2000), (200, 200, 200)), _jpeg((3000, 2000), (150, 150, 150))
        )

        self.assertTrue(composed.data.startswith(b"\xff\xd8"))
        self.assertLessEqual(max(composed.width, composed.height), BEFORE_AFTER_MAX_EDGE)
        self.assertGreater(composed.width, composed.height)

    def test_mismatched_aspect_ratios_keep_their_own_shape(self):
        """一宽一高：统一到同一高度，各自保持比例 —— 只有一条缝，没有留白。"""
        wide = _jpeg((1600, 400), (200, 200, 200))
        tall = _jpeg((400, 1600), (100, 100, 100))

        composed = compose_before_after(wide, tall)

        height = composed.height
        expected = (
            round(1600 / 400 * height) + BEFORE_AFTER_GAP + round(400 / 1600 * height)
        )
        self.assertAlmostEqual(composed.width, expected, delta=2)
        self.assertLessEqual(max(composed.width, composed.height), BEFORE_AFTER_MAX_EDGE)

    def test_labels_are_drawn_on_the_top_left_of_both_halves(self):
        composed = compose_before_after(
            _jpeg((800, 600), (220, 40, 40)), _jpeg((800, 600), (40, 40, 220))
        )

        image = _open(composed.data)
        seam = self._seam_x(image)
        left_label = image.getpixel((12, 12))
        right_label = image.getpixel((seam + BEFORE_AFTER_GAP + 12, 12))

        # 半透明黑底压在原图上：两个角落都明显比底色暗
        self.assertLess(sum(left_label), 300)
        self.assertLess(sum(right_label), 300)
        self.assertIn(composed.label_language, {"cjk", "latin"})

    def test_rejects_bytes_that_are_not_an_image(self):
        with self.assertRaises(InvalidImageError):
            compose_before_after(b"not an image", _jpeg((100, 100), (0, 0, 0)))

    def test_raises_instead_of_dropping_a_photo_that_stays_too_large(self):
        with self.assertRaises(BeforeAfterTooLarge):
            compose_before_after(
                _jpeg((800, 600), (200, 100, 50)),
                _jpeg((800, 600), (50, 100, 200)),
                max_bytes=1024,
            )
