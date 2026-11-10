"""岗位二维码页的只读清单（2026-11-10 用户裁定）。

二维码页从管理端专属开只读给员工会话里勾了「岗位二维码」的管理员；
普通员工仍 403，未登录仍 401。这一条是最小读面：只回岗位标题/slug，不读配方正文。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import api.recipes as recipes_module
from api.security import require_any_identity_session


class _FakeStore:
    async def list_stations(self):
        return [{"slug": "changfen", "title": "肠粉档", "recipe_count": 2}]


def _client(identity):
    app = FastAPI()
    app.include_router(recipes_module.reader_router)
    app.dependency_overrides[recipes_module._get_recipe_store] = lambda: _FakeStore()
    app.dependency_overrides[require_any_identity_session] = lambda: identity
    return TestClient(app)


def test_admin_reads_qr_stations():
    resp = _client({"kind": "admin"}).get("/api/recipes/qr-stations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stations"][0]["slug"] == "changfen"


def test_staff_with_recipe_qr_cap_reads_qr_stations():
    identity = {"kind": "staff", "employee": {"id": 2, "admin_caps": ["recipe_qr"]}}
    resp = _client(identity).get("/api/recipes/qr-stations")
    assert resp.status_code == 200, resp.text


def test_staff_without_cap_is_403():
    identity = {"kind": "staff", "employee": {"id": 3, "admin_caps": []}}
    resp = _client(identity).get("/api/recipes/qr-stations")
    assert resp.status_code == 403
    assert "岗位二维码" in resp.json()["detail"]
