#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货品主数据管理面：列表（含停用）+ 编辑（名称 / 单位 / 岗位 / 归属 / 启用）。"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.security import verify_admin_token
from api.semi_rules import router
from database import DatabaseManager, get_db


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client():
    db = DatabaseManager()
    _run(db.connect())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_admin_token] = lambda: True
    with TestClient(app) as test_client:
        yield test_client, db
    _run(db.close())


def _create(db, name, *, unit="份", position="熟笼", kind="made", active=1):
    created = _run(db.prep_item_create({
        "item_name": name, "unit": unit, "position": position,
        "category": "半成品" if kind == "made" else "原料",
        "kind": kind, "station": {"熟笼": "shulong", "西饼": "xibing"}.get(position, ""),
    }))
    if active == 0:
        _run(db.prep_item_update(created["id"], {"active": 0}))
    return created["id"]


def test_list_includes_inactive_items(client):
    test_client, db = client
    _create(db, "验收-在用")
    _create(db, "验收-停用", active=0)

    body = test_client.get("/api/semi-rules/prep-items/manage").json()
    names = {row["item_name"]: row for row in body["items"]}
    assert names["验收-在用"]["active"] is True
    assert names["验收-停用"]["active"] is False


def test_list_counts_rules_referencing_item(client):
    test_client, db = client
    item_id = _create(db, "验收-引用")
    _run(db.semi_rules_upsert({
        "dish_name": "验收菜A", "semi_name": "验收-引用", "factor": 1,
        "unit": "份", "category": "半成品", "prep_item_id": item_id,
    }))

    body = test_client.get("/api/semi-rules/prep-items/manage").json()
    row = next(r for r in body["items"] if r["id"] == item_id)
    assert row["rule_count"] == 1

    # 保存后返回的行也要带 rule_count：页面用返回值就地更新那一行，
    # 少了它就会把「1 条规则」冲成「没有规则」。
    patched = test_client.patch(
        f"/api/semi-rules/prep-items/{item_id}", json={"notes": "改备注"}
    )
    assert patched.status_code == 200
    assert patched.json()["item"]["rule_count"] == 1


def test_update_renames_and_checks_duplicates(client):
    test_client, db = client
    item_id = _create(db, "验收-旧名")
    _create(db, "验收-占用名")

    ok = test_client.patch(f"/api/semi-rules/prep-items/{item_id}", json={"item_name": "验收-新名"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["item"]["item_name"] == "验收-新名"

    dup = test_client.patch(f"/api/semi-rules/prep-items/{item_id}", json={"item_name": "验收-占用名"})
    assert dup.status_code == 409


def test_update_derives_category_and_station(client):
    test_client, db = client
    item_id = _create(db, "验收-派生")

    resp = test_client.patch(
        f"/api/semi-rules/prep-items/{item_id}",
        json={"kind": "bought", "position": "西饼", "unit": "包"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["item"]
    assert item["kind"] == "bought"
    assert item["category"] == "原料"
    assert item["position"] == "西饼"
    assert item["station"] == "xibing"
    assert item["unit"] == "包"


def test_update_toggles_active(client):
    test_client, db = client
    item_id = _create(db, "验收-启停")

    off = test_client.patch(f"/api/semi-rules/prep-items/{item_id}", json={"active": False})
    assert off.status_code == 200
    assert off.json()["item"]["active"] is False

    on = test_client.patch(f"/api/semi-rules/prep-items/{item_id}", json={"active": True})
    assert on.json()["item"]["active"] is True


def test_update_rejects_unknown_item_and_empty_payload(client):
    test_client, db = client
    assert test_client.patch("/api/semi-rules/prep-items/999999", json={"unit": "斤"}).status_code == 404

    item_id = _create(db, "验收-空载荷")
    assert test_client.patch(f"/api/semi-rules/prep-items/{item_id}", json={}).status_code == 400


def test_delete_removes_item_and_detaches_rules(client):
    """删除只删备货品：引用它的规则由外键回到「未对齐」，不连带删规则。"""
    test_client, db = client
    item_id = _create(db, "验收-待删")
    _run(db.semi_rules_upsert({
        "dish_name": "验收菜B", "semi_name": "验收-待删", "factor": 1,
        "unit": "份", "category": "半成品", "prep_item_id": item_id,
    }))

    resp = test_client.delete(f"/api/semi-rules/prep-items/{item_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rules_detached"] == 1

    rows = _run(db.prep_items_all())
    assert all(row["id"] != item_id for row in rows)
    rule = _run(db.semi_rules_all())
    assert next(r for r in rule if r["dish_name"] == "验收菜B")["prep_item_id"] is None


def test_delete_unknown_item_is_404(client):
    test_client, db = client
    assert test_client.delete("/api/semi-rules/prep-items/999999").status_code == 404
