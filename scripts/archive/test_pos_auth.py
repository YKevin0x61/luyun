#!/usr/bin/env python3
"""Smoke-test POS auth: 2.0 login, cy7mm SSO, and cy7mm business APIs."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.credentials_store import get_credentials
from scraper.restaurant_scraper import create_restaurant_scraper

SLY_BASE = "https://sly.tcsl.com.cn/newProxy"
CYFAST_VERSION = "https://cyfast.tcsl.com.cn/fast/sysapi/system/version/qdCy7AppVersion"
BUSY_POINT_URL = (
    "https://cy7mm.wuuxiang.com/cy7MobileReports/canyin/mobile/realtimetablestate/getbusypointdata"
)


def _redact_token(value: str | None, keep: int = 8) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep:
        return value[:2] + "…"
    return f"{value[:keep]}…(len={len(value)})"


def _encrypt_login_body(mobile: str, password: str, public_key_b64: str) -> str:
    public_key = serialization.load_der_public_key(
        base64.b64decode(public_key_b64),
        backend=default_backend(),
    )
    plaintext = json.dumps({"mobile": mobile, "password": password}, separators=(",", ":"))
    encrypted = public_key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return json.dumps(base64.b64encode(encrypted).decode("ascii"))


def _auth_failure_hint(payload: dict) -> bool:
    code = payload.get("code")
    if code in (401, 403, 40101, 1000, "401", "403", "40101", "1000"):
        return True
    combined = " ".join(
        str(payload.get(key) or "")
        for key in ("message", "msg", "errorMessage", "errorMsg", "errMsg", "error")
    )
    hints = ("未登录", "请登录", "登录", "无权", "权限", "密码错误", "用户名")
    return any(h in combined for h in hints)


async def test_sly_login(client: httpx.AsyncClient, phone: str, password: str) -> dict:
    print("\n=== [A] 2.0 auth-center login ===")
    version_resp = await client.post(
        CYFAST_VERSION,
        json={"appType": "housekeep", "shopId": "0", "type": 1},
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "okhttp/4.12.0"},
    )
    print(f"  version: HTTP {version_resp.status_code} {version_resp.text[:120]}")

    key_resp = await client.post(
        f"{SLY_BASE}/auth-center/api/ano/rsa/key/publick",
        json={},
        headers={"User-Agent": "okhttp/4.12.0"},
    )
    key_payload = key_resp.json()
    public_key = (key_payload.get("data") or {}).get("publicKey")
    if not public_key:
        return {"ok": False, "step": "rsa_key", "detail": key_payload}

    login_body = _encrypt_login_body(phone, password, public_key)
    login_resp = await client.post(
        f"{SLY_BASE}/auth-center/app/v1/ano/login/mobile",
        content=login_body,
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.12.0",
        },
    )
    login_payload = login_resp.json()
    ok = login_resp.status_code == 200 and login_payload.get("code") == 2000
    token = (login_payload.get("data") or {}).get("token")
    principal = (login_payload.get("data") or {}).get("principal") or {}
    print(f"  login/mobile: HTTP {login_resp.status_code} code={login_payload.get('code')} msg={login_payload.get('message')}")
    print(f"  token: {_redact_token(token)}")
    print(f"  accountId={principal.get('accountId')} orgName={principal.get('orgName')} nickName={principal.get('nickName')}")
    return {
        "ok": ok,
        "token": token,
        "principal": principal,
        "payload": login_payload,
    }


async def test_busy_point_with_token(
    client: httpx.AsyncClient,
    token: str,
    shop_id: str,
    company_id: str,
    shop_name: str,
    referer: str,
) -> dict:
    print("\n=== [B] cy7mm getbusypointdata with TCSL-BP-TOKEN only ===")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "TCSL-BP-TOKEN": token,
        "Origin": "https://cy7mm.wuuxiang.com",
        "Referer": referer,
        "User-Agent": "okhttp/4.12.0",
    }
    payload = {"shopId": shop_id, "companyId": company_id, "shopName": shop_name}
    resp = await client.post(BUSY_POINT_URL, headers=headers, json=payload)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": (await resp.aread()).decode("utf-8", errors="replace")[:500]}
    ok = resp.status_code == 200 and isinstance(body, dict) and not _auth_failure_hint(body)
    data = body.get("data") if isinstance(body, dict) else None
    count = len(data) if isinstance(data, list) else None
    print(f"  HTTP {resp.status_code} code={body.get('code') if isinstance(body, dict) else '?'} msg={body.get('message') if isinstance(body, dict) else body}")
    if count is not None:
        print(f"  tables: {count}")
    return {"ok": ok, "status": resp.status_code, "body": body}


async def test_cy7mm_playwright(phone: str, password: str) -> dict:
    print("\n=== [C] 2.0 login + Playwright cy7mm SSO + POS APIs ===")
    # 仅探测登录/会话与原始 API，不解析菜品，无需真实 DishCatalog
    scraper = await create_restaurant_scraper(None)
    scraper.prepare_unrestricted_probe()
    section_results: dict[str, dict] = {}
    try:
        if not await scraper.ensure_ready(ignore_pause=True):
            detail = "ensure_ready failed"
            if scraper.page and not scraper.page.is_closed():
                try:
                    toast = await scraper.page.locator(
                        '.van-toast, .van-notify, .el-message, [role="alert"]'
                    ).first.text_content(timeout=2000)
                    if toast and toast.strip():
                        detail = toast.strip()
                except Exception:
                    pass
                detail = f"{detail}; url={scraper.page.url}"
            return {"ok": False, "step": "init", "detail": detail, "sections": section_results}

        busy = await scraper.probe_busy_point_api_login_ok()
        section_results["getbusypointdata"] = busy
        print(
            f"  getbusypointdata: {'PASS' if busy.get('ok') else 'FAIL'} "
            f"tables={busy.get('rows_count', '?')} msg={busy.get('message', '')}"
        )

        bs_detail = await scraper.probe_bs_detail_api()
        section_results["getbsdetail"] = bs_detail
        if bs_detail.get("skipped"):
            print(
                f"  getbsdetail: SKIP — {bs_detail.get('message')} "
                f"(tables={bs_detail.get('table_rows', '?')})"
            )
        else:
            print(
                f"  getbsdetail: {'PASS' if bs_detail.get('ok') else 'FAIL'} "
                f"table={bs_detail.get('table_name', '?')} "
                f"dishes={bs_detail.get('dish_count', '?')} "
                f"msg={bs_detail.get('message', '')}"
            )

        settled = await scraper.probe_settled_bill_apis()
        section_results["settledbill"] = settled
        if settled.get("detail_skipped"):
            print(
                f"  settledbill: PASS list={settled.get('bill_count', 0)} "
                f"detail=SKIP ({settled.get('detail_message', '')})"
            )
        else:
            print(
                f"  settledbill: {'PASS' if settled.get('ok') else 'FAIL'} "
                f"bills={settled.get('bill_count', '?')} "
                f"dishes={settled.get('dish_count', '?')} "
                f"bsId={settled.get('sample_bs_id', '-')} "
                f"msg={settled.get('message', '')}"
            )

        ok = all(
            section.get("ok")
            for section in section_results.values()
        )
        return {"ok": ok, "sections": section_results}
    finally:
        try:
            await scraper.close()
        except Exception:
            pass


async def test_session_recovery(phone: str, password: str) -> dict:
    """[D] 清掉 cy7mm Cookie 模拟会话过期，验证下一次采集能自动重新登录。

    这是 P0 修复（会话过期后静默空转）唯一的端到端验证：改动前 Cookie 一失效
    就永远返回空列表直到进程重启，且健康状态仍显示 ok。
    """
    print("\n=== [D] 会话过期自愈（clear_cookies → 自动重登）===")
    # 只用桌台列表验证会话自愈，不解析菜品，无需真实 DishCatalog
    scraper = await create_restaurant_scraper(None)
    scraper.prepare_unrestricted_probe()
    try:
        if not await scraper.ensure_ready(ignore_pause=True):
            return {"ok": False, "detail": "初始化失败"}

        before = await scraper.scrape_table_data()
        print(f"  清 Cookie 前: {len(before)} 个桌台")

        await scraper.context.clear_cookies()
        cookies_left = len(await scraper.context.cookies())
        print(f"  已清空 Cookie（剩余 {cookies_left} 条），模拟会话过期")

        after = await scraper.scrape_table_data()
        print(f"  自愈后:      {len(after)} 个桌台")

        ok = len(after) > 0
        return {
            "ok": ok,
            "detail": "" if ok else "清 Cookie 后未能自动恢复，仍拿不到桌台数据",
            "tables_before": len(before),
            "tables_after": len(after),
        }
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            await scraper.close()
        except Exception:
            pass


async def main() -> int:
    creds = get_credentials()
    if creds is None:
        print("ERROR: no credentials in data/credentials.enc — configure via /setup first")
        return 1

    print("Using credentials:")
    print(f"  phone={creds.phone[:3]}****{creds.phone[-4:]}")
    print(f"  shop_id={creds.shop_id} company_id={creds.company_id} shop_name={creds.shop_name}")

    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        sly = await test_sly_login(client, creds.phone, creds.password)
        results["sly_login"] = sly
        if sly.get("token"):
            results["token_api"] = await test_busy_point_with_token(
                client,
                sly["token"],
                creds.shop_id,
                creds.company_id,
                creds.shop_name,
                creds.table_list_referer,
            )

    results["cy7mm_playwright"] = await test_cy7mm_playwright(creds.phone, creds.password)
    results["session_recovery"] = await test_session_recovery(creds.phone, creds.password)

    print("\n=== SUMMARY ===")
    for name, r in results.items():
        mark = "PASS" if r.get("ok") else "FAIL"
        extra = ""
        if not r.get("ok"):
            if r.get("detail"):
                extra = f" — {r['detail']}"
            elif isinstance(r.get("body"), dict):
                extra = f" — {r['body'].get('message') or r['body'].get('msg') or ''}"
        print(f"  {mark}  {name}{extra}")
        for section_name, section in (r.get("sections") or {}).items():
            section_mark = "PASS" if section.get("ok") else ("SKIP" if section.get("skipped") else "FAIL")
            print(f"         └ {section_mark}  {section_name}: {section.get('message', '')}")

    any_ok = any(r.get("ok") for r in results.values())
    return 0 if any_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
