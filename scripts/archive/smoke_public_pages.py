#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Playwright 冒烟测试：验证核心页面能打开并渲染关键模块。

用法：
  python scripts/start.py
  python scripts/archive/smoke_public_pages.py --base-url http://127.0.0.1:8000
"""

import argparse
import asyncio

from playwright.async_api import async_playwright


PAGE_CHECKS = [
    ("/", "监控大屏"),
    ("/admin/", "数据管理"),
    ("/sales-report", "销售报表"),
    ("/wecom-push", "Webhook 管理"),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        for path, expected_text in PAGE_CHECKS:
            url = args.base_url.rstrip("/") + path
            response = await page.goto(url, wait_until="domcontentloaded")
            assert response and response.ok, f"{url} 加载失败: {response.status if response else '无响应'}"
            await page.get_by_text(expected_text).first.wait_for(timeout=8000)
            print(f"OK {path} -> {expected_text}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
