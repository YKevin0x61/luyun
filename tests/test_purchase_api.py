#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""厨房采购的 HTTP 面：谁能下单、盘点写哪、单据怎么走、汇总与分析看什么。

主缝在 services/purchase/，这里只钉用户真会撞上的几件事：
岗位必选、只写所选岗位的盘点（未填按 0）、员工只看自己的单、
取消只能本人或店长、汇总/导出/分析口径与权限。
"""

import asyncio
import io
import json
import time
import zipfile
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import api.auth as auth_module
import api.purchase as purchase_module
import main as main_module
from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.purchase.migration import migration_manager
from services.purchase.store import PurchaseStore

PASSWORD = "password123"
ADMIN = {"username": "admin", "password": PASSWORD, "confirm_password": PASSWORD}
PHONE = "13800138000"
NAME = "张三"
PHONE2 = "13900139000"
NAME2 = "李四"
FIXED_NOW = datetime(2026, 11, 21, 10, 0, tzinfo=CHINA_TZ)
TODAY = "2026-11-21"


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


@pytest.fixture
def purchase_http(tmp_path, monkeypatch):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    migration_manager.reset_for_tests()
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(db, now=lambda: FIXED_NOW)
    monkeypatch.setattr(main_module, "employee_accounts", accounts)
    monkeypatch.setattr(PurchaseStore, "_now_dt", lambda self: FIXED_NOW)

    app = FastAPI()
    app.include_router(auth_module.router)
    app.include_router(purchase_module.reader_router)
    app.include_router(purchase_module.manager_router)
    with TestClient(app) as client:
        init = client.post("/api/auth/init", json=ADMIN)
        assert init.status_code == 200, init.text
        login = client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": ADMIN["password"]},
        )
        assert login.status_code == 200, login.text
        yield client, accounts
    migration_manager.reset_for_tests()
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _employee(accounts, phone=PHONE, name=NAME, caps=None):
    employee = _run(accounts.register(phone, PASSWORD, name))
    _run(accounts.approve(employee["id"]))
    if caps is not None:
        _run(accounts.set_admin_caps(employee["id"], caps))
    return int(employee["id"])


def _staff_cookie(client, accounts, phone=PHONE):
    session_id = _run(accounts.login(phone, PASSWORD))["session_id"]
    client.cookies.clear()
    client.cookies.set(settings.STAFF_SESSION_COOKIE_NAME, session_id)


def _admin_cookie(client):
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    assert login.status_code == 200, login.text


def _post(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _setup_catalog(client):
    supplier = _post(
        client, "/api/purchase/admin/suppliers", {"name": "华南供应商", "code": "01"}
    )["supplier"]
    category = _post(
        client,
        "/api/purchase/admin/categories",
        {"supplier_id": supplier["id"], "name": "蔬菜", "code": "01"},
    )["category"]
    first = _post(
        client, "/api/purchase/admin/positions", {"name": "测试岗A", "sort_order": 101}
    )["position"]
    second = _post(
        client, "/api/purchase/admin/positions", {"name": "测试岗B", "sort_order": 102}
    )["position"]
    product = _post(
        client,
        "/api/purchase/admin/products",
        {
            "supplier_id": supplier["id"],
            "category_id": category["id"],
            "name": "土豆",
            "specification": "大个",
            "unit": "斤",
            "order_step": 5,
            "safety_factor": 1.2,
            "need_stock_check": True,
            "position_ids": [first["id"]],
        },
    )["product"]
    other = _post(
        client,
        "/api/purchase/admin/products",
        {
            "supplier_id": supplier["id"],
            "category_id": category["id"],
            "name": "白菜",
            "unit": "斤",
            "need_stock_check": True,
            "position_ids": [second["id"]],
        },
    )["product"]
    return supplier, category, first, second, product, other


# ── 权限与目录 ─────────────────────────────────────────────────────────────


def test_catalog_permissions_and_code_generation(purchase_http):
    client, accounts = purchase_http
    client.cookies.clear()
    assert client.get("/api/purchase/meta").status_code == 401

    _admin_cookie(client)
    supplier, category, first, _second, product, _other = _setup_catalog(client)
    assert product["product_code"].isdigit() and len(product["product_code"]) == 7
    assert product["positions"][0]["id"] == first["id"]

    _employee(accounts, caps=[])
    _staff_cookie(client, accounts)
    assert client.get("/api/purchase/products").status_code == 403
    denied = client.post("/api/purchase/admin/suppliers", json={"name": "越权"})
    assert denied.status_code == 403
    assert denied.json()["detail"]

    _admin_cookie(client)
    # 给员工开通采购能力后，他能读到商品，但仍不能维护目录。
    employee_id = _run(accounts.register("13700137000", PASSWORD, "王五"))["id"]
    _run(accounts.approve(employee_id))
    _run(accounts.set_admin_caps(employee_id, ["purchase"]))
    _staff_cookie(client, accounts, "13700137000")
    products = client.get("/api/purchase/products")
    assert products.status_code == 200, products.text
    assert products.json()["products"][0]["id"] == product["id"]
    assert client.post("/api/purchase/admin/suppliers", json={"name": "越权2"}).status_code == 403
    assert client.get("/api/purchase/analysis/overview").status_code == 403

    _admin_cookie(client)
    duplicate = client.post(
        "/api/purchase/admin/suppliers", json={"name": "华南供应商", "code": "01"}
    )
    assert duplicate.status_code == 409
    assert client.post(
        "/api/purchase/admin/products",
        json={
            "supplier_id": supplier["id"],
            "category_id": category["id"],
            "name": "无岗位",
        },
    ).status_code == 400


# ── 下单、盘点范围与汇总 ───────────────────────────────────────────────────


def test_submit_only_counts_the_selected_position(purchase_http):
    client, accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, second, product, other = _setup_catalog(client)
    _employee(accounts, caps=["purchase"])
    _staff_cookie(client, accounts)

    created = _post(
        client,
        "/api/purchase/orders",
        {
            "position_id": first["id"],
            "items": [{"product_id": product["id"], "quantity": 3}],
            "stock": {str(product["id"]): 2},
        },
    )
    assert created["order"]["order_no"].startswith("P20261121-")
    assert created["order"]["position_name"] == first["name"]
    assert created["items"][0]["stock_qty"] == 2
    order_id = created["order"]["id"]

    # 没填盘点的那一项按 0 落库，但只落所选岗位；另一个岗位的商品一条都不写。
    counts = client.get(
        "/api/purchase/stock", params={"day": TODAY, "position_id": first["id"]}
    ).json()["counts"]
    assert [row["product_id"] for row in counts] == [product["id"]]
    assert float(counts[0]["current_stock"]) == 2
    other_counts = client.get(
        "/api/purchase/stock", params={"day": TODAY, "position_id": second["id"]}
    ).json()["counts"]
    assert not any(row["product_id"] == other["id"] for row in other_counts)

    summary = client.get("/api/purchase/summary", params={"day": TODAY}).json()
    assert summary["order_count"] == 1
    assert summary["item_count"] == 1
    assert summary["total_quantity"] == 3
    supplier_node = summary["suppliers"][0]
    assert supplier_node["categories"][0]["products"][0]["name"] == "土豆"

    export = client.get("/api/purchase/summary/export", params={"day": TODAY})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert export.content.startswith(b"PK")

    listing = client.get(
        "/api/purchase/orders", params={"day_from": TODAY, "day_to": TODAY}
    ).json()
    assert listing["total"] == 1 and listing["orders"][0]["id"] == order_id
    # 时间统一按北京时区下发：库里是 TIMESTAMPTZ，asyncpg 会给 UTC，
    # 前端只截 ISO 前 16 位显示，不转时区就会把 07:30 的单显示成前一天。
    assert listing["orders"][0]["submitted_at"].startswith("2026-11-21T10:00:00+08:00")

    # 改商品编码要同步历史订单行的快照。
    _admin_cookie(client)
    changed = client.patch(
        "/api/purchase/admin/products/" + str(product["id"]),
        json={"product_code": "0101999"},
    )
    assert changed.status_code == 200, changed.text
    detail = client.get("/api/purchase/orders/" + str(order_id)).json()
    assert detail["items"][0]["product_code"] == "0101999"


def test_complete_cancel_and_employee_visibility(purchase_http):
    client, accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, _second, product, _other = _setup_catalog(client)
    _employee(accounts, caps=["purchase"])
    _employee(accounts, phone=PHONE2, name=NAME2, caps=["purchase"])
    _staff_cookie(client, accounts)
    created = _post(
        client,
        "/api/purchase/orders",
        {"position_id": first["id"], "items": [{"product_id": product["id"], "quantity": 1}]},
    )
    order_id = created["order"]["id"]

    # 另一个人看不到，也取消不了。
    _staff_cookie(client, accounts, PHONE2)
    assert client.get("/api/purchase/orders/" + str(order_id)).status_code == 403
    assert client.post("/api/purchase/orders/" + str(order_id) + "/cancel").status_code == 403
    assert client.get(
        "/api/purchase/orders", params={"day_from": TODAY, "day_to": TODAY}
    ).json()["total"] == 0

    # 提交者本人可以取消待处理单。
    _staff_cookie(client, accounts)
    cancelled = client.post("/api/purchase/orders/" + str(order_id) + "/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["order"]["status"] == "cancelled"
    assert client.post("/api/purchase/orders/" + str(order_id) + "/complete").status_code == 403

    # 店长核对完成；已完成的单不能再改 / 取消。
    _admin_cookie(client)
    second = _post(
        client,
        "/api/purchase/orders",
        {"position_id": first["id"], "items": [{"product_id": product["id"], "quantity": 2}]},
    )
    second_id = second["order"]["id"]
    completed = client.post("/api/purchase/orders/" + str(second_id) + "/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["order"]["status"] == "completed"
    assert client.post("/api/purchase/orders/" + str(second_id) + "/cancel").status_code == 409
    assert client.patch(
        "/api/purchase/orders/" + str(second_id) + "/items",
        json={"items": [{"product_id": product["id"], "quantity": 9}]},
    ).status_code == 409


def test_analysis_counts_and_zero_stock(purchase_http):
    client, accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, _second, product, _other = _setup_catalog(client)
    _employee(accounts, caps=["purchase"])
    _staff_cookie(client, accounts)
    _post(
        client,
        "/api/purchase/orders",
        {"position_id": first["id"], "items": [{"product_id": product["id"], "quantity": 4}]},
    )

    _admin_cookie(client)
    overview = client.get("/api/purchase/analysis/overview", params={"day": TODAY}).json()
    assert overview["order_count"] == 1
    assert overview["item_count"] == 4
    assert overview["sku_count"] == 1
    assert overview["submitter_count"] == 1
    assert len(overview["trend"]) == 7
    assert overview["trend"][-1]["date"] == TODAY
    assert overview["trend"][-1]["quantity"] == 4

    anomalies = client.get("/api/purchase/analysis/anomalies", params={"day": TODAY}).json()
    assert any(row["product_id"] == product["id"] for row in anomalies["zero_stock"])
    assert anomalies["parameters"]["no_order_days"] == 3

    # 日期标签：管理端维护，meta 与目录页读得到。
    tagged = client.put(
        "/api/purchase/admin/date-tags", json={"tag_date": TODAY, "tag": "5万"}
    )
    assert tagged.status_code == 200, tagged.text
    tags = client.get(
        "/api/purchase/admin/date-tags", params={"start": TODAY, "end": TODAY}
    ).json()
    by_date = {row["tag_date"]: row["tag"] for row in tags["date_tags"]}
    assert by_date[TODAY] == "5万"


# ── 目录 Excel 导入 / 导出 ─────────────────────────────────────────────────


def _workbook_bytes(rows, headers=None):
    import io

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        list(headers)
        if headers
        else [
            "供应商编码", "供应商", "分类编码", "分类", "商品编码", "商品名称",
            "规格", "单位", "岗位", "下单步长", "安全系数", "需盘点", "长周期", "启用", "备注",
        ]
    )
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(client, content, *, mode="upsert", dry_run=True):
    response = client.post(
        "/api/purchase/admin/products/import",
        files={
            "file": (
                "catalog.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": mode, "dry_run": "true" if dry_run else "false"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_supplier_and_category_cannot_be_disabled_while_products_are_active(purchase_http):
    client, _accounts = purchase_http
    _admin_cookie(client)
    supplier, category, _first, _second, product, other = _setup_catalog(client)

    blocked = client.patch(
        f"/api/purchase/admin/suppliers/{supplier['id']}", json={"active": False}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.headers.get("X-Error-Code") == "supplier_in_use"
    assert "2 个启用商品" in blocked.json()["detail"]

    blocked = client.patch(
        f"/api/purchase/admin/categories/{category['id']}", json={"active": False}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.headers.get("X-Error-Code") == "category_in_use"
    assert "2 个启用商品" in blocked.json()["detail"]

    for row in (product, other):
        turned_off = client.patch(
            f"/api/purchase/admin/products/{row['id']}", json={"active": False}
        )
        assert turned_off.status_code == 200, turned_off.text

    assert client.patch(
        f"/api/purchase/admin/categories/{category['id']}", json={"active": False}
    ).status_code == 200
    assert client.patch(
        f"/api/purchase/admin/suppliers/{supplier['id']}", json={"active": False}
    ).status_code == 200


def test_catalog_excel_import_export_and_template(purchase_http):
    client, _accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, second, product, _other = _setup_catalog(client)

    exported = client.get("/api/purchase/admin/products/export")
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"
    from openpyxl import load_workbook
    import io

    sheet = load_workbook(io.BytesIO(exported.content)).active
    headers = [cell.value for cell in sheet[1]]
    assert headers[:6] == ["供应商编码", "供应商", "分类编码", "分类", "商品编码", "商品名称"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert any(row[5] == "土豆" for row in rows)

    template = client.get("/api/purchase/admin/products/template")
    assert template.status_code == 200
    template_book = load_workbook(io.BytesIO(template.content))
    assert "填写说明" in template_book.sheetnames

    content = _workbook_bytes(
        [
            ["01", "华南供应商", "01", "蔬菜", product["product_code"], "土豆（大）", "大个", "斤", "测试岗A、测试岗B", 2, 1.3, "是", "否", "是", "改过"],
            ["01", "华南供应商", "01", "蔬菜", "", "红薯", "", "斤", "测试岗B", 1, 1.2, "是", "否", "是", ""],
            ["99", "不存在的供应商", "01", "蔬菜", "", "坏行", "", "斤", "测试岗A", 1, 1.2, "是", "否", "是", ""],
        ]
    )

    preview = _upload(client, content, mode="upsert", dry_run=True)
    assert preview["dry_run"] is True
    assert (preview["created"], preview["updated"], preview["failed"]) == (1, 1, 1)
    unchanged = client.get("/api/purchase/admin/products").json()["products"]
    assert next(row for row in unchanged if row["id"] == product["id"])["name"] == "土豆"

    applied = _upload(client, content, mode="upsert", dry_run=False)
    assert (applied["created"], applied["updated"], applied["failed"]) == (1, 1, 1)
    products = client.get("/api/purchase/admin/products").json()["products"]
    updated = next(row for row in products if row["id"] == product["id"])
    assert updated["name"] == "土豆（大）"
    assert {row["id"] for row in updated["positions"]} == {first["id"], second["id"]}
    assert float(updated["order_step"]) == 2.0
    created = next(row for row in products if row["name"] == "红薯")
    assert len(created["product_code"]) == 7

    add_only = _upload(client, content, mode="add", dry_run=True)
    assert add_only["updated"] == 0
    assert add_only["failed"] >= 1
    assert any("已存在" in (row["error"] or "") for row in add_only["rows"])


def test_mini_program_export_header_is_accepted_as_product_code(purchase_http):
    """小程序导出的表头是「商品ID」，导入要按编码更新而不是新建重名商品。"""
    client, _accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, _first, _second, product, _other = _setup_catalog(client)

    content = _workbook_bytes(
        [["华南供应商", "蔬菜", product["product_code"], "土豆（老表改名）", "斤", "测试岗A"]],
        headers=["供应商", "分类", "商品ID", "商品名称", "单位", "岗位"],
    )
    preview = _upload(client, content, mode="upsert", dry_run=True)
    assert (preview["created"], preview["updated"], preview["failed"]) == (0, 1, 0)

    applied = _upload(client, content, mode="upsert", dry_run=False)
    assert (applied["created"], applied["updated"], applied["failed"]) == (0, 1, 0)
    products = client.get("/api/purchase/admin/products").json()["products"]
    renamed = next(row for row in products if row["id"] == product["id"])
    assert renamed["name"] == "土豆（老表改名）"
    assert len([row for row in products if row["product_code"] == product["product_code"]]) == 1


# ── 分析导出任务 ─────────────────────────────────────────────────────────


def test_analysis_export_runs_as_a_job(purchase_http):
    client, accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, _second, product, _other = _setup_catalog(client)

    # 员工即使有采购能力也不能导出分析。
    _employee(accounts, caps=["purchase"])
    _staff_cookie(client, accounts)
    assert client.post("/api/purchase/analysis/export").status_code == 403
    assert client.get("/api/purchase/analysis/export/not-a-job").status_code == 403
    assert client.get("/api/purchase/analysis/export/not-a-job/download").status_code == 403

    _admin_cookie(client)
    _post(
        client,
        "/api/purchase/orders",
        {"position_id": first["id"], "items": [{"product_id": product["id"], "quantity": 3}]},
    )
    started = client.post("/api/purchase/analysis/export", params={"day": TODAY})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]

    state = None
    for _ in range(100):
        state = client.get(f"/api/purchase/analysis/export/{job_id}").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state and state["state"] == "done", state

    download = client.get(f"/api/purchase/analysis/export/{job_id}/download")
    assert download.status_code == 200, download.text
    assert download.content[:2] == b"PK"
    book = load_workbook(io.BytesIO(download.content))
    assert {"概览", "近7日", "异常"} <= set(book.sheetnames)
    assert book["概览"]["B2"].value == TODAY



def test_reference_all_view_tags_bulk_and_position_visibility(purchase_http):
    client, accounts = purchase_http
    _admin_cookie(client)
    _supplier, _category, first, second, product, other = _setup_catalog(client)

    # 下单页的「全部」视图：不带岗位也能拿到全部可用商品，提交仍要岗位。
    all_view = client.get("/api/purchase/reference", params={"day": TODAY})
    assert all_view.status_code == 200, all_view.text
    all_products = {row["id"] for row in all_view.json()["products"]}
    assert {product["id"], other["id"]} <= all_products
    scoped = client.get(
        "/api/purchase/reference", params={"day": TODAY, "position_id": first["id"]}
    ).json()
    assert [row["id"] for row in scoped["products"]] == [product["id"]]

    # 岗位的「下单可见」开关要能写进去（旧实现会忽略这个字段）。
    hidden = _post(
        client,
        "/api/purchase/admin/positions",
        {"name": "不显示岗位", "sort_order": 103, "show_in_order": False},
    )["position"]
    assert hidden["show_in_order"] is False

    # 日期标签：月历多选批量设置，清空区间（星期默认不受影响）。
    bulk = client.put(
        "/api/purchase/admin/date-tags/bulk",
        json={"dates": [TODAY, "2026-11-22"], "tag": "6万"},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["count"] == 2
    tags = client.get(
        "/api/purchase/admin/date-tags",
        params={"start": TODAY, "end": "2026-11-22"},
    ).json()["date_tags"]
    assert {row["tag_date"] for row in tags} == {TODAY, "2026-11-22"}
    cleared = client.delete(
        "/api/purchase/admin/date-tags",
        params={"start": TODAY, "end": "2026-11-22"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["deleted"] == 2
    assert client.get(
        "/api/purchase/admin/date-tags",
        params={"start": TODAY, "end": "2026-11-22"},
    ).json()["date_tags"] == []

    # 汇总能按岗位过滤；另一个岗位没有单时汇总为空。
    _employee(accounts, caps=["purchase"])
    _staff_cookie(client, accounts)
    _post(
        client,
        "/api/purchase/orders",
        {"position_id": first["id"], "items": [{"product_id": product["id"], "quantity": 3}]},
    )
    only_second = client.get(
        "/api/purchase/summary",
        params={"day": TODAY, "position_ids": str(second["id"])},
    ).json()
    assert only_second["order_count"] == 0
    assert only_second["suppliers"] == []
    only_first = client.get(
        "/api/purchase/summary",
        params={"day": TODAY, "position_ids": str(first["id"])},
    ).json()
    assert only_first["order_count"] == 1


# ── 网页全量迁移 ─────────────────────────────────────────────────────────


def _migration_documents():
    """一份最小的 8 集合云导出；迁移器只认字段，不要求顺序。"""
    supplier = {
        "_id": "sup_migrate",
        "name": "迁移供应商",
        "code": "05",
        "nextCategoryCode": 1,
        "isActive": True,
    }
    category = {
        "_id": "cat_migrate",
        "supplierId": "sup_migrate",
        "supplierName": "迁移供应商",
        "supplierCode": "05",
        "name": "迁移分类",
        "code": "02",
        "nextProductSeq": 1,
        "isActive": True,
    }
    position = {"_id": "pos_migrate", "name": "迁移岗", "sortOrder": 201}
    product = {
        "_id": "prod_migrate",
        "productId": "0502001",
        "supplier": "迁移供应商",
        "supplierCode": "05",
        "categoryId": "cat_migrate",
        "categoryName": "迁移分类",
        "position": ["迁移岗"],
        "name": "迁移土豆",
        "unit": "斤",
        "needStockCheck": True,
        "orderStep": 1,
        "safetyFactor": 1.2,
        "isActive": True,
    }
    order = {
        "_id": "order_migrate",
        "createTime": "2026-11-20T10:00:00+08:00",
        "status": "pending",
        "userInfo": {"name": "迁移测试员"},
        "items": [
            {
                "productId": "prod_migrate",
                "name": "迁移土豆",
                "quantity": 2,
                "unit": "斤",
                "specification": "",
                "supplier": "迁移供应商",
                "position": "迁移岗",
            }
        ],
    }
    stock = {
        "productId": "prod_migrate",
        "productName": "迁移土豆",
        "date": "2026-11-20",
        "currentStock": 4,
        "countByName": "迁移测试员",
    }
    tag = {"_id": "tag_migrate", "date": "2026-11-20", "tag": "6万", "remark": "测试"}
    weekday = {"_id": "wd_migrate", "weekday": 5, "tag": "4万"}
    return {
        "suppliers.json": json.dumps([supplier], ensure_ascii=False).encode("utf-8"),
        "categories.json": json.dumps([category], ensure_ascii=False).encode("utf-8"),
        "products.json": json.dumps([product], ensure_ascii=False).encode("utf-8"),
        "positions.json": json.dumps([position], ensure_ascii=False).encode("utf-8"),
        "date_factor_tags.json": json.dumps([tag], ensure_ascii=False).encode("utf-8"),
        "date_factor_weekday_defaults.json": json.dumps([weekday], ensure_ascii=False).encode("utf-8"),
        "orders.json": json.dumps([order], ensure_ascii=False).encode("utf-8"),
        "material_stock.json": json.dumps([stock], ensure_ascii=False).encode("utf-8"),
    }


def _migration_upload_files(documents=None):
    documents = documents or _migration_documents()
    return [
        ("files", (name, data, "application/json"))
        for name, data in documents.items()
    ]


def _wait_migration_job(client, migration_id, kind):
    last = None
    for _ in range(240):
        response = client.get(f"/api/purchase/admin/migrations/{migration_id}")
        assert response.status_code == 200, response.text
        last = response.json()["migration"]
        job = last.get(kind)
        if job and job["state"] in {"done", "failed"}:
            return last, job
        time.sleep(0.05)
    raise AssertionError(f"迁移任务没有结束：{last}")


def test_web_migration_preview_apply_and_discard(purchase_http):
    client, accounts = purchase_http
    documents = {
        name: data
        for name, data in _migration_documents().items()
        if name in {"suppliers.json", "categories.json", "products.json", "positions.json"}
    }

    # 员工即使有采购能力，也不能上传全量迁移包。
    _employee(accounts, caps=["purchase"])
    _staff_cookie(client, accounts)
    denied = client.post(
        "/api/purchase/admin/migrations", files=_migration_upload_files(documents)
    )
    assert denied.status_code == 403

    _admin_cookie(client)
    started = client.post(
        "/api/purchase/admin/migrations",
        files=_migration_upload_files(documents),
        data={"limit": "0", "skip_orders": "false", "skip_stock": "false"},
    )
    assert started.status_code == 200, started.text
    migration = started.json()["migration"]
    migration_id = migration["id"]
    assert set(migration["missing"]) == {
        "date_factor_tags",
        "date_factor_weekday_defaults",
        "orders",
        "material_stock",
    }
    assert {row["name"] for row in migration["files"]} == set(documents)

    migration, preview = _wait_migration_job(client, migration_id, "preview")
    assert preview["state"] == "done", preview
    assert preview["report"]["apply"] is False
    assert preview["report"]["stats"]["suppliers"]["created"] == 1
    assert preview["report"]["stats"]["products"]["created"] == 1
    assert preview["report"]["failures"] == []

    # 报告可以从任务里下载成 JSON。
    report = client.get(
        f"/api/purchase/admin/migrations/{migration_id}/jobs/{preview['id']}/report"
    )
    assert report.status_code == 200, report.text
    assert "attachment" in report.headers["content-disposition"]
    assert json.loads(report.text)["apply"] is False

    # 没有确认不能写库；确认后预览过的同一份上传会被真正导入。
    unconfirmed = client.post(
        f"/api/purchase/admin/migrations/{migration_id}/apply", json={}
    )
    assert unconfirmed.status_code == 400
    applied = client.post(
        f"/api/purchase/admin/migrations/{migration_id}/apply", json={"confirm": True}
    )
    assert applied.status_code == 200, applied.text
    migration, apply_job = _wait_migration_job(client, migration_id, "apply")
    assert apply_job["state"] == "done", apply_job
    assert apply_job["report"]["apply"] is True

    products = client.get("/api/purchase/admin/products").json()["products"]
    assert any(row["name"] == "迁移土豆" for row in products)
    tags = client.get(
        "/api/purchase/admin/date-tags", params={"start": TODAY, "end": TODAY}
    )
    assert tags.status_code == 200

    discarded = client.delete(f"/api/purchase/admin/migrations/{migration_id}")
    assert discarded.status_code == 200
    assert discarded.json()["discarded"] is True
    assert client.get(f"/api/purchase/admin/migrations/{migration_id}").status_code == 404


def test_web_migration_accepts_zip_and_rejects_bad_archive(purchase_http):
    client, _accounts = purchase_http
    _admin_cookie(client)
    documents = _migration_documents()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in documents.items():
            archive.writestr(name, data)

    started = client.post(
        "/api/purchase/admin/migrations",
        files=[("files", ("worktool2-export.zip", buffer.getvalue(), "application/zip"))],
        data={"limit": "0"},
    )
    assert started.status_code == 200, started.text
    migration_id = started.json()["migration"]["id"]
    migration, preview = _wait_migration_job(client, migration_id, "preview")
    assert preview["state"] == "done", preview
    assert migration["missing"] == []
    assert preview["report"]["stats"]["orders"]["created"] == 1
    assert preview["report"]["stats"]["material_stock"]["created"] == 1

    bad = client.post(
        "/api/purchase/admin/migrations",
        files=[("files", ("bad.zip", b"not a zip", "application/zip"))],
    )
    assert bad.status_code == 400
    assert bad.headers.get("x-error-code") == "bad_archive"
    assert "zip" in bad.json()["detail"]
