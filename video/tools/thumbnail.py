"""Devpost thumbnail (1500x1000, 3:2) in the style of the YouTube thumbnail, with safe margins.

  video/.venv/bin/python video/tools/thumbnail.py    -> video/devpost-thumbnail.png
(the GOES-18 still video/build/lahaina_0136.png comes from assemble.py)
"""
import asyncio
import base64
import os

from playwright.async_api import async_playwright

from assemble import AMBER, FONT, STILL, V

W, H, PAD = 1500, 1000, 96


async def main():
    still = "data:image/png;base64," + base64.b64encode(open(STILL, "rb").read()).decode()
    body = f"""<div style="box-sizing:border-box;width:{W}px;height:{H}px;padding:{PAD}px;display:flex;align-items:center;gap:64px;color:#e2e8f0">
      <div style="flex:1">
        <div style="font-size:24px;letter-spacing:.1em;text-transform:uppercase;color:{AMBER};font-weight:700">GOES-18 · AI agent · human review</div>
        <div style="font-size:92px;font-weight:800;color:#fff;line-height:1.02;margin:18px 0 26px">Kahu Ola<br>SmokeWatch</div>
        <div style="font-size:34px;line-height:1.35;color:#fff">Lahaina: flagged <b style="color:{AMBER}">41&nbsp;min</b> after the first report,
          <b style="color:{AMBER}">~6&nbsp;h</b> before the first polar satellite</div>
        <div style="font-size:24px;color:#94a3b8;margin-top:34px">Wildfire watch for Hawaiʻi · OpenCV 5 · AWS · Claude</div>
      </div>
      <div style="flex:1.05"><img src="{still}" style="width:100%;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.5)"></div>
    </div>"""
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": W, "height": H})
        await pg.set_content(f"<!doctype html><html><body style='margin:0;background:radial-gradient(1200px 800px at 30% 20%,#1e293b,#0f172a);"
                             f"font-family:{FONT}'>{body}</body></html>", wait_until="load")
        out = os.path.join(V, "devpost-thumbnail.png")
        await pg.screenshot(path=out)
        # every text/image box must stay inside the safe margin
        boxes = await pg.evaluate("() => [...document.querySelectorAll('body div div, img')].map(e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; })")
        await br.close()
    l, t, r, b = min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes)
    print(out, f"content box {l:.0f},{t:.0f} – {r:.0f},{b:.0f} (margins {l:.0f}/{t:.0f}/{W - r:.0f}/{H - b:.0f} px)")


if __name__ == "__main__":
    asyncio.run(main())
