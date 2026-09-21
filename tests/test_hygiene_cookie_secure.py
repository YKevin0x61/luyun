#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话 cookie 的 Secure 决策：显式配置优先，否则跟随 DEBUG，默认安全。

改之前 ``DEBUG`` 默认是 ``True``：没写 .env 的部署会同时得到「/docs 全开」和
「cookie 不带 Secure」。现在 DEBUG 默认关闭，同时把 cookie 的 Secure 解耦成
``SESSION_COOKIE_SECURE``——内网明文 http 部署可以直接关它，不必为了登录把
DEBUG 打开（DEBUG 还管着 /docs 与错误详情）。
"""

import os

from fastapi import Response

from api.hygiene import _set_staff_cookie
from config import Settings, settings


def _fresh():
    """不读 .env，只看环境变量。"""
    return Settings(_env_file=None)


def test_default_deployment_is_secure(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    fresh = _fresh()
    assert fresh.DEBUG is False, "不打任何配置时不能默认开 DEBUG"
    assert fresh.session_cookie_secure is True, "默认部署的会话 cookie 必须带 Secure"


def test_secure_follows_debug_when_unset(monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("DEBUG", "true")
    assert _fresh().session_cookie_secure is False, "开发机跑明文 http 时要能登进去"


def test_explicit_setting_beats_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    assert _fresh().session_cookie_secure is False, "内网 http 部署可以单独关掉"

    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    assert _fresh().session_cookie_secure is True


def test_staff_cookie_header_carries_secure_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", True)
    response = Response()
    _set_staff_cookie(response, "sid-abc")
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "samesite=lax" in header.lower()


def test_staff_cookie_header_drops_secure_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", False)
    response = Response()
    _set_staff_cookie(response, "sid-abc")
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Secure" not in header
