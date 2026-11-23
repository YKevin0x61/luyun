#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日汇总的排序口径：供应商 / 分类按目录编码，商品按编码数字序。

旧小程序「今日汇总」的打印预览就是这个顺序（供应商码 → 分类码 → 商品码数字序），
工作台打印与页面展示直接照接口顺序渲染，所以排序错了打印就错。这里只测纯函数，
不连库；接口层另有一条 test_summary_orders_suppliers_and_categories_by_code。
"""

from services.purchase.service import _group_summary


def _row(
    *,
    supplier_name: str,
    supplier_code,
    category_name: str,
    category_code,
    product_id: int,
    product_code: str,
    name: str,
    quantity: float = 1.0,
) -> dict:
    return {
        "supplier_name": supplier_name,
        "supplier_code": supplier_code,
        "category_name": category_name,
        "category_code": category_code,
        "product_id": product_id,
        "product_code": product_code,
        "name": name,
        "specification": "",
        "unit": "斤",
        "quantity": quantity,
        "stock_qty": None,
        "order_count": 1,
        "position_name": "墩头",
        "submitters": "张三",
    }


def test_supplier_order_follows_code_and_missing_code_goes_last():
    # 名称码点序是「东海冻品 < 阿强水产」，编码序是 11 < 12，故意相反。
    rows = [
        _row(
            supplier_name="东海冻品",
            supplier_code="12",
            category_name="冻品",
            category_code="01",
            product_id=1,
            product_code="1201001",
            name="冻虾",
        ),
        _row(
            supplier_name="未分供应商",
            supplier_code=None,
            category_name="杂项",
            category_code=None,
            product_id=2,
            product_code="",
            name="抹布",
        ),
        _row(
            supplier_name="阿强水产",
            supplier_code="11",
            category_name="水产",
            category_code="01",
            product_id=3,
            product_code="1101001",
            name="鲈鱼",
        ),
    ]
    summary = _group_summary(rows)
    assert [supplier["supplier_name"] for supplier in summary["suppliers"]] == [
        "阿强水产",
        "东海冻品",
        "未分供应商",
    ]


def test_category_order_follows_code_not_name():
    # 「干」的码点比「豆」小，按名称排会是干货在前；按分类编码应是豆制品在前。
    rows = [
        _row(
            supplier_name="阿强水产",
            supplier_code="11",
            category_name="干货",
            category_code="02",
            product_id=1,
            product_code="1102001",
            name="木耳",
        ),
        _row(
            supplier_name="阿强水产",
            supplier_code="11",
            category_name="豆制品",
            category_code="01",
            product_id=2,
            product_code="1101001",
            name="豆腐",
        ),
        _row(
            supplier_name="阿强水产",
            supplier_code="11",
            category_name="未分类",
            category_code=None,
            product_id=3,
            product_code="",
            name="塑料袋",
        ),
    ]
    summary = _group_summary(rows)
    assert [
        category["category_name"] for category in summary["suppliers"][0]["categories"]
    ] == ["豆制品", "干货", "未分类"]


def test_product_order_uses_numeric_code_and_keeps_missing_codes_last():
    # 字符串序 "1000000" < "999999"，数字序反过来；旧小程序按 parseInt(productId) 排。
    rows = [
        _row(
            supplier_name="华南供应商",
            supplier_code="01",
            category_name="蔬菜",
            category_code="01",
            product_id=1,
            product_code="999999",
            name="六位码商品",
        ),
        _row(
            supplier_name="华南供应商",
            supplier_code="01",
            category_name="蔬菜",
            category_code="01",
            product_id=2,
            product_code="1000000",
            name="七位码商品",
        ),
        _row(
            supplier_name="华南供应商",
            supplier_code="01",
            category_name="蔬菜",
            category_code="01",
            product_id=3,
            product_code="A001",
            name="非数字编码",
        ),
        _row(
            supplier_name="华南供应商",
            supplier_code="01",
            category_name="蔬菜",
            category_code="01",
            product_id=4,
            product_code="",
            name="无编码",
        ),
    ]
    summary = _group_summary(rows)
    names = [
        product["name"]
        for product in summary["suppliers"][0]["categories"][0]["products"]
    ]
    assert names == ["六位码商品", "七位码商品", "非数字编码", "无编码"]
