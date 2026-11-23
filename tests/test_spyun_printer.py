#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商鹏云小票打印的离线测试：签名、排版、协议层（用 httpx MockTransport 挡网）。

不连数据库、不连外网：这里要证明的是「排出来的字节是平台能吃的」和
「签名跟文档算得一样」，真机链路由人工验收（页面上点一次）。
"""

from __future__ import annotations

import unittest
from datetime import datetime
from urllib.parse import unquote

import httpx

from services.printer.service import build_test_ticket
from services.printer.spyun import SpyunClient, SpyunError, make_sign
from services.printer.ticket import (
    DEFAULT_BUDGET_BYTES,
    DEFAULT_FONT_SCALE,
    build_ticket,
    display_width,
    format_qty,
    normalize_scale,
    wrap_text,
)


def _summary(products_per_supplier: int = 2, suppliers: int = 1) -> dict:
    data = {
        "date": "2026-11-23",
        "suppliers": [],
    }
    for index in range(suppliers):
        products = [
            {
                "name": "商品" + str(number),
                "quantity": number + 1,
                "unit": "斤",
                "stock_qty": 3,
                "submitters": ["张三"],
            }
            for number in range(products_per_supplier)
        ]
        data["suppliers"].append(
            {
                "supplier_name": "供应商" + str(index + 1),
                "categories": [{"category_name": "蔬菜", "products": products}],
            }
        )
    return data


class SignTests(unittest.TestCase):
    def test_document_example(self) -> None:
        """文档正文那个例子：sign 3C24E50A...，抄错 timestamp 会一直报签名错误。"""
        params = {
            "appid": "sp5c1314095ed15",
            "name": "test",
            "pkey": "22222222",
            "sn": "111111111",
            "timestamp": "1544765945",
        }
        self.assertEqual(
            make_sign(params, "735aa25a15b75e6c1e0760823a22346a"),
            "3C24E50A3F1E0B4AB60FFA3A78E19E18",
        )

    def test_empty_values_are_skipped(self) -> None:
        """空串等于没传：/printer/add 的 name 经常是空的。"""
        with_empty = {"appid": "a", "name": "", "sn": "s", "timestamp": "1"}
        without = {"appid": "a", "sn": "s", "timestamp": "1"}
        self.assertEqual(make_sign(with_empty, "k"), make_sign(without, "k"))

    def test_sign_is_order_independent(self) -> None:
        first = {"appid": "a", "sn": "s", "timestamp": "1"}
        second = {"timestamp": "1", "sn": "s", "appid": "a"}
        self.assertEqual(make_sign(first, "k"), make_sign(second, "k"))


class LayoutTests(unittest.TestCase):
    def test_display_width_counts_cjk_as_two(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("采购"), 4)

    def test_wrap_keeps_lines_within_width(self) -> None:
        lines = wrap_text("一二三四五六七八九十", 8)
        self.assertTrue(all(display_width(line) <= 8 for line in lines))
        self.assertEqual("".join(lines), "一二三四五六七八九十")

    def test_format_qty_drops_trailing_zeros(self) -> None:
        self.assertEqual(format_qty(12.0), "12")
        self.assertEqual(format_qty("12.50"), "12.5")

    def test_ticket_fits_58mm(self) -> None:
        ticket = build_ticket(
            _summary(products_per_supplier=3),
            paper_width=58,
            now=datetime(2026, 11, 23, 9, 5, 0),
            scale="normal",
        )
        self.assertFalse(ticket.truncated)
        self.assertGreater(ticket.printed_products, 0)
        for line in ticket.lines:
            if line == "<CUT>":
                continue
            self.assertLessEqual(display_width(line), 32, line)
        self.assertIn("2026/11/23 09:05:00", ticket.text)
        self.assertIn("采购汇总", ticket.text)

    def test_everything_is_truncated_not_overflowed(self) -> None:
        """几百项的一天：宁可少打几个供应商并在票尾说明，也不能让接口 5000 字节顶穿。"""
        ticket = build_ticket(
            _summary(products_per_supplier=60, suppliers=8),
            paper_width=58,
        )
        self.assertLessEqual(ticket.byte_length, DEFAULT_BUDGET_BYTES)
        self.assertTrue(ticket.truncated)
        self.assertIn("还有", ticket.text)
        self.assertLess(ticket.printed_products, ticket.total_products)

    def test_tags_in_names_are_escaped(self) -> None:
        data = _summary(products_per_supplier=1)
        data["suppliers"][0]["categories"][0]["products"][0]["name"] = "A<BR>B"
        ticket = build_ticket(data)
        self.assertNotIn("A<BR>B", ticket.text)
        self.assertIn("A＜BR＞B", ticket.text)

    def test_empty_day_prints_header_only(self) -> None:
        ticket = build_ticket({"date": "2026-11-23", "suppliers": []})
        self.assertEqual(ticket.printed_products, 0)
        self.assertFalse(ticket.truncated)
        self.assertIn("供应商总数：0 家", ticket.text)

    def test_zero_quantity_is_skipped(self) -> None:
        data = _summary(products_per_supplier=2)
        data["suppliers"][0]["categories"][0]["products"][1]["quantity"] = 0
        ticket = build_ticket(data)
        self.assertEqual(ticket.total_products, 1)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, handler) -> SpyunClient:
        return SpyunClient("appid", "secret", transport=httpx.MockTransport(handler))

    async def test_print_returns_order_id(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("sign=", request.content.decode())
            return httpx.Response(200, json={"errorcode": 0, "id": "o1", "create_time": "2026-11-23 09:00:00"})

        payload = await self._client(handler).print_text("sn1", "hello")
        self.assertEqual(payload["id"], "o1")

    async def test_error_code_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errorcode": -4, "errormsg": "签名错误"})

        with self.assertRaises(SpyunError) as ctx:
            await self._client(handler).print_text("sn1", "hello")
        self.assertEqual(ctx.exception.code, -4)
        self.assertTrue(ctx.exception.is_sign_error)

    async def test_already_added_is_not_failure(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errorcode": 5, "errormsg": "该设备已添加"})

        result = await self._client(handler).add_printer("sn1", "pkey1")
        self.assertTrue(result["already_added"])

    async def test_add_fills_default_name(self) -> None:
        # 平台把 name 当必填：空串回 {"errorcode":3,"errormsg":"Field name is required"}。
        # 前端只填 SN / PKEY，客户端得自己补一个名字，否则绑定永远失败。
        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = unquote(request.content.decode())
            return httpx.Response(200, json={"errorcode": 0})

        await self._client(handler).add_printer("sn1", "pkey1")
        self.assertIn("name=厨务管家", captured["body"])

    async def test_add_keeps_explicit_name(self) -> None:
        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = unquote(request.content.decode())
            return httpx.Response(200, json={"errorcode": 0})

        await self._client(handler).add_printer("sn1", "pkey1", name="后厨")
        self.assertIn("name=后厨", captured["body"])

    async def test_non_json_body_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        with self.assertRaises(SpyunError):
            await self._client(handler).print_text("sn1", "hello")


class TestTicketTests(unittest.TestCase):
    def test_br_by_default(self) -> None:
        text = build_test_ticket(now="2026-11-23 09:00:00")
        self.assertIn("<BR>", text)
        self.assertNotIn("\n", text)

    def test_lf_switch(self) -> None:
        text = build_test_ticket(now="2026-11-23 09:00:00", line_break="lf")
        self.assertNotIn("<BR>", text)
        self.assertIn("\n", text)

    def test_no_cut_unless_asked(self) -> None:
        self.assertNotIn("<CUT>", build_test_ticket())
        self.assertIn("<CUT>", build_test_ticket(cut=True))


class PaperCutTests(unittest.TestCase):
    """58mm 是撕纸机，80mm 才发切纸标签：发错了纸上会印出 <CUT> 四个字。"""

    def test_58mm_has_no_cut(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=1), paper_width=58)
        self.assertNotIn("<CUT>", ticket.text)

    def test_80mm_cuts(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=1), paper_width=80)
        self.assertIn("<CUT>", ticket.text)

    def test_explicit_cut_wins(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=1), paper_width=58, cut=True)
        self.assertIn("<CUT>", ticket.text)


class FontScaleTests(unittest.TestCase):
    """字号：字放大后一行能放的字变少，排版必须跟着压列宽，否则右边被裁。"""

    def test_tall_is_default_and_keeps_32_columns(self) -> None:
        """默认 <H>：只拔高不改列宽，行数不变，必然不会超宽被裁。"""
        ticket = build_ticket(_summary(products_per_supplier=2))
        self.assertEqual(ticket.scale, "tall")
        self.assertTrue(ticket.text.startswith("<H>"))
        self.assertTrue(ticket.text.endswith("</H>"))
        for line in ticket.lines:
            self.assertLessEqual(display_width(line), 32, line)

    def test_big_wraps_whole_ticket_and_leaves_margin(self) -> None:
        """<L1> 按 32/2=16 列排会顶满打印头、右缘被裁，必须让出 2 列。"""
        ticket = build_ticket(_summary(products_per_supplier=2), scale="big")
        self.assertTrue(ticket.text.startswith("<L1>"))
        self.assertTrue(ticket.text.endswith("</L1>"))
        for line in ticket.lines:
            self.assertLessEqual(display_width(line), 14, line)

    def test_normal_keeps_32_columns(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=2), scale="normal")
        self.assertNotIn("<L1>", ticket.text)
        self.assertNotIn("<H>", ticket.text)
        for line in ticket.lines:
            self.assertLessEqual(display_width(line), 32, line)

    def test_huge_falls_back_on_58mm(self) -> None:
        """58mm 一行放不下 10 列的日期，L2 在这一档没有实用价值，退回 big。"""
        ticket = build_ticket(_summary(products_per_supplier=2), scale="huge")
        self.assertEqual(ticket.scale, "big")

    def test_huge_on_80mm_keeps_extra_margin(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=2), paper_width=80, scale="huge")
        self.assertEqual(ticket.scale, "huge")
        for line in ticket.lines:
            self.assertLessEqual(display_width(line), 14, line)

    def test_tall_only_raises_characters(self) -> None:
        ticket = build_ticket(_summary(products_per_supplier=2), scale="tall")
        self.assertIn("<H>", ticket.text)
        for line in ticket.lines:
            self.assertLessEqual(display_width(line), 32, line)

    def test_unknown_scale_falls_back_to_default(self) -> None:
        self.assertEqual(normalize_scale("gigantic"), DEFAULT_FONT_SCALE)
        self.assertEqual(normalize_scale(None), DEFAULT_FONT_SCALE)

    def test_quantity_is_never_split_across_lines(self) -> None:
        """放大后一行只有 8 个汉字，硬折会把「12.5件」拆成两行，拣货的人会看错。"""
        data = _summary(products_per_supplier=1)
        product = data["suppliers"][0]["categories"][0]["products"][0]
        product["name"] = "甘竹豆豉鲮鱼罐头"
        product["quantity"] = 12.5
        product["unit"] = "件"
        product["stock_qty"] = None
        ticket = build_ticket(data, scale="big")
        self.assertTrue(any("12.5件" in line for line in ticket.lines), ticket.lines)

    def test_test_ticket_follows_scale(self) -> None:
        self.assertTrue(build_test_ticket(scale="big").startswith("<L1>"))
        self.assertTrue(build_test_ticket(scale="normal").startswith("【测试小票】"))


if __name__ == "__main__":
    unittest.main()
