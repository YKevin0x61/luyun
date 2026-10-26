import asyncio, sys, json
from playwright.async_api import async_playwright

URL = sys.argv[1]

JS = r"""
() => {
  function lum(c){
    const m = c.match(/[\d.]+/g).map(Number);
    const f = v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(m[0])+0.7152*f(m[1])+0.0722*f(m[2]);
  }
  function bgOf(el){
    let e = el;
    while(e){
      const cs = getComputedStyle(e);
      const b = cs.backgroundColor;
      if(b && b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') return b;
      e = e.parentElement;
    }
    return 'rgb(0,0,0)';
  }
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length) return;
    const t = (el.textContent||'').trim();
    if (!t) return;
    const cs = getComputedStyle(el);
    if (cs.visibility==='hidden' || cs.display==='none' || +cs.opacity===0) return;
    const r = el.getBoundingClientRect();
    if (r.width<2 || r.height<2) return;
    const fg = cs.color, bg = bgOf(el);
    try {
      const L1 = lum(fg), L2 = lum(bg);
      const ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
      if (ratio < 4.4) out.push({text: t.slice(0,18), ratio: +ratio.toFixed(2), px: Math.round(parseFloat(cs.fontSize)), fg, bg, tag: el.tagName, cls: el.className && el.className.toString().slice(0,40)});
    } catch(e){}
  });
  return out;
}
"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width":1440,"height":900})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        res = await page.evaluate(JS)
        print(json.dumps(res[:25], ensure_ascii=False, indent=1))
        print("total", len(res))
        await b.close()

asyncio.run(main())
