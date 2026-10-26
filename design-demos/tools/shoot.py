#!/usr/bin/env python3
"""截图 + 抓控制台错误。用法：
  python3 tools/shoot.py http://127.0.0.1:8899/dir-1/index.html?p=today&role=manager out.png 1440 900
"""
import sys, asyncio
from playwright.async_api import async_playwright

url = sys.argv[1]
out = sys.argv[2]
w = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
h = int(sys.argv[4]) if len(sys.argv) > 4 else 900
wait = int(sys.argv[5]) if len(sys.argv) > 5 else 1800

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2)
        page = await ctx.new_page()
        errs, logs = [], []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(wait)
        await page.screenshot(path=out)
        if errs or logs:
            print("CONSOLE_PROBLEMS:", *errs, *logs, sep="\n  ")
        else:
            print("console clean")
        print("saved", out)
        await b.close()

asyncio.run(main())
