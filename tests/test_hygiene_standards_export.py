#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准图导出：标注必须烘焙进像素，且按责任区分子文件夹。

导出是给人看的成品，圆圈/箭头/批注不再是前端叠的一层 DOM —— 所以这里断言的是
「像素里真的有那个颜色」，而不是「函数没抛异常」。
"""

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from services.hygiene.standards_export import (
    MARK_RGB,
    render_markup,
    safe_component,
    write_archive,
)

CIRCLE = {"kind": "circle", "x": 0.5, "y": 0.5, "r": 0.12}
ARROW = {"kind": "arrow", "x1": 0.2, "y1": 0.2, "x2": 0.6, "y2": 0.5}
CAPTION = {"kind": "caption", "x": 0.5, "y": 0.25, "text": "台面要干净"}
BACKDROP = (20, 40, 60)


def make_jpeg(width: int = 800, height: int = 600, color=BACKDROP) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def mark_pixels(data: bytes, tolerance: int = 40) -> int:
    """数一数画面里有多少像素接近标注主色（JPEG 有损，所以给容差）。"""
    with Image.open(io.BytesIO(data)) as image:
        pixels = list(image.convert("RGB").getdata())
    return sum(
        1
        for red, green, blue in pixels
        if abs(red - MARK_RGB[0]) <= tolerance
        and abs(green - MARK_RGB[1]) <= tolerance
        and abs(blue - MARK_RGB[2]) <= tolerance
    )


class RenderMarkupTest(unittest.TestCase):
    def test_marks_are_baked_into_pixels(self):
        plain = make_jpeg()
        self.assertEqual(mark_pixels(plain), 0, "素图里不该有标注色")

        marked = render_markup(plain, [CIRCLE, ARROW, CAPTION])
        self.assertGreater(mark_pixels(marked), 200, "圈/箭头/批注没有画进像素")

    def test_image_size_is_preserved(self):
        marked = render_markup(make_jpeg(1024, 768), [CIRCLE])
        with Image.open(io.BytesIO(marked)) as image:
            self.assertEqual(image.size, (1024, 768))

    def test_markup_can_be_empty_or_junk(self):
        # 没有标注、标注不是 dict、坐标是垃圾：都只跳过那一条，不能整张失败。
        for markup in ([], [None], [{"kind": "unknown"}], [{"kind": "circle", "x": "坏"}]):
            with self.subTest(markup=markup):
                self.assertTrue(render_markup(make_jpeg(), markup))

    def test_circle_is_drawn_where_the_mark_says(self):
        # 圈画在右下，左上角就应该是干净的背景色。
        marked = render_markup(
            make_jpeg(),
            [{"kind": "circle", "x": 0.8, "y": 0.8, "r": 0.1}],
        )
        with Image.open(io.BytesIO(marked)) as image:
            corner = image.convert("RGB").crop((0, 0, 200, 200))
        buffer = io.BytesIO()
        corner.save(buffer, format="JPEG", quality=95)
        self.assertEqual(mark_pixels(buffer.getvalue()), 0)


class ArchiveTest(unittest.TestCase):
    def test_groups_by_zone_and_keeps_item_names(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "capture-a"
            source.write_bytes(make_jpeg())
            target = root / "out.zip"

            written, failed = write_archive(
                [
                    ("案板", "玻璃窗", source, [CIRCLE]),
                    ("西饼", "工作台1", source, [CAPTION]),
                ],
                target,
            )

            self.assertEqual((written, failed), (2, 0))
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                payload = archive.read("案板/玻璃窗.jpg")
            self.assertIn("案板/玻璃窗.jpg", names)
            self.assertIn("西饼/工作台1.jpg", names)
            self.assertGreater(mark_pixels(payload), 200, "包里的图缺标注")

    def test_zone_and_item_names_are_sanitised(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "capture-a"
            source.write_bytes(make_jpeg())
            target = root / "out.zip"

            write_archive([("西饼/地柜", "工作台:1", source, [])], target)

            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
            self.assertEqual(names, ["西饼_地柜/工作台_1.jpg"])

    def test_unreadable_image_is_skipped_not_fatal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "out.zip"

            written, failed = write_archive(
                [("案板", "文件没了", root / "missing", [])], target
            )

            self.assertEqual((written, failed), (0, 1))
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), [])

    def test_duplicate_member_names_do_not_collide(self):
        # 同一区里两条清洗后同名的检查项，不能互相覆盖。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "capture-a"
            source.write_bytes(make_jpeg())
            target = root / "out.zip"

            write_archive(
                [("案板", "台面/1", source, []), ("案板", "台面:1", source, [])],
                target,
            )

            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
            self.assertEqual(len(names), len(set(names)), f"zip 内有重名条目: {names}")


class SafeComponentTest(unittest.TestCase):
    def test_strips_separators_and_leading_dots(self):
        cleaned = safe_component("../../etc/passwd", "兜底")
        self.assertNotIn("/", cleaned)
        self.assertFalse(cleaned.startswith("."))

    def test_blank_falls_back(self):
        self.assertEqual(safe_component("   ", "兜底"), "兜底")
        self.assertEqual(safe_component("", "兜底"), "兜底")
        self.assertEqual(safe_component("...", "兜底"), "兜底")


if __name__ == "__main__":
    unittest.main()
