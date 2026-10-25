"""Devpost gallery: five 1500x1000 (3:2) PNGs in video/gallery/.

  video/.venv/bin/python video/tools/gallery.py
(1 is video/devpost-thumbnail.png from thumbnail.py; 2, 3 and 5 are filmed on the PROD URL)
"""
import asyncio
import os
import shutil

from playwright.async_api import async_playwright

from rec import PROD, ROOT, V

OUT = os.path.join(V, "gallery")
CSS_W, CSS_H, SCALE = 1200, 800, 1.25          # -> 1500x1000 px


async def shot(ctx, url, out, scroll_js, prep_js=None):
    pg = await ctx.new_page()
    await pg.goto(url, wait_until="networkidle", timeout=90000)
    await pg.wait_for_timeout(3000)
    await pg.evaluate("() => { const v = document.getElementById('vid'); if (v) { v.pause(); v.removeAttribute('controls'); v.currentTime = 13.2; } }")
    if prep_js:
        await pg.evaluate(prep_js)
        await pg.wait_for_timeout(1500)
    await pg.evaluate(scroll_js)
    await pg.wait_for_timeout(800)
    await pg.screenshot(path=out)
    await pg.close()


async def main():
    os.makedirs(OUT, exist_ok=True)
    shutil.copy(os.path.join(V, "devpost-thumbnail.png"), os.path.join(OUT, "1-thumbnail.png"))
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        ctx = await br.new_context(viewport={"width": CSS_W, "height": CSS_H}, device_scale_factor=SCALE, color_scheme="dark")
        top = "sel => window.scrollTo(0, document.querySelector(sel).getBoundingClientRect().top + scrollY - 16)"
        # 2: case-study headline + timeline
        await shot(ctx, PROD, os.path.join(OUT, "2-case-study.png"),
                   f"() => ({top})('#case')")
        # 3: timeline viewer, Maui 05:51Z: review circles + FIRMS points; frame the map and legend
        await shot(ctx, PROD + "?date=2023-08-08&t=0551Z&region=maui#viewer", os.path.join(OUT, "3-timeline-viewer.png"),
                   "() => { const c = document.getElementById('L_base').getBoundingClientRect(); "
                   "window.scrollTo(0, c.top + scrollY - 14); }")
        # 5: agent trace, Claude next to Rules
        await shot(ctx, PROD, os.path.join(OUT, "5-agent-trace.png"),
                   f"() => ({top})('#trace')")
        # 4: architecture on white
        pg = await ctx.new_page()
        svg = open(os.path.join(ROOT, "docs", "architecture.svg")).read()
        await pg.set_content(f"""<!doctype html><html><body style="margin:0;width:{CSS_W}px;height:{CSS_H}px;background:#fff;
          display:flex;align-items:center;justify-content:center"><div id=s style="width:{CSS_W - 80}px;height:{CSS_H - 80}px;
          display:flex;align-items:center;justify-content:center">{svg}</div>
          <style>#s svg{{max-width:100%;max-height:100%;width:auto;height:auto}}</style></body></html>""")
        await pg.evaluate("() => { const e = document.querySelector('#s svg'); e.removeAttribute('width'); e.removeAttribute('height'); }")
        await pg.screenshot(path=os.path.join(OUT, "4-architecture.png"))
        await br.close()
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        print(p, os.path.getsize(p) // 1024, "KB")


if __name__ == "__main__":
    asyncio.run(main())
