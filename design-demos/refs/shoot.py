import asyncio, sys, pathlib
from playwright.async_api import async_playwright

URLS = [
    ("7shifts-home", "https://www.7shifts.com/"),
    ("7shifts-appstore", "https://apps.apple.com/us/app/7shifts-team-scheduling/id1020774078"),
    ("toast-pos", "https://pos.toasttab.com/"),
    ("linear-home", "https://linear.app/"),
]
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "refs")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for name, url in URLS:
            try:
                ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                await page.screenshot(path=str(OUT / f"{name}.png"))
                print("ok", name)
                await ctx.close()
            except Exception as e:
                print("fail", name, type(e).__name__, str(e)[:120])
        await b.close()

asyncio.run(main())
