"""Build smokewatch-submission.zip from HEAD: the source (git archive) plus REPORT.pdf and PNG
renders of the two diagrams.

  video/.venv/bin/python scripts/make_submission.py     (needs playwright + markdown)

Zip layout (prefix kahuola-smokewatch/): every tracked file at HEAD, plus
REPORT.pdf, docs/architecture.png, docs/agent-workflow.png.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import zipfile

import markdown
from playwright.async_api import async_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "smokewatch-submission.zip")
PREFIX = "kahuola-smokewatch/"

CSS = """
body{font:10.5pt/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#111;max-width:none;margin:0}
h1{font-size:22pt;margin:0 0 6pt} h2{font-size:15pt;margin:18pt 0 6pt;border-bottom:1px solid #ddd;padding-bottom:3pt}
h3{font-size:12pt;margin:12pt 0 4pt} p,li{margin:4pt 0} code{font:9pt ui-monospace,Menlo,monospace;background:#f3f4f6;padding:0 2px}
table{border-collapse:collapse;margin:8pt 0;font-size:9pt;width:100%} th,td{border:1px solid #d1d5db;padding:3pt 5pt;text-align:left;vertical-align:top}
th{background:#f3f4f6} img{max-width:100%;display:block;margin:8pt auto} a{color:#0b6e99;text-decoration:none}
blockquote{border-left:3px solid #ddd;margin:6pt 0;padding-left:8pt;color:#444} figure{page-break-inside:avoid;margin:10pt 0}
figcaption{font-size:9pt;color:#555;text-align:center}
"""


def sh(*a, **k):
    return subprocess.run(a, check=True, capture_output=True, text=True, cwd=ROOT, **k).stdout


async def render(tmp):
    md = open(os.path.join(ROOT, "REPORT.md"), encoding="utf-8").read()
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    body += ('<h2>Figures</h2><figure><img src="docs/agent-workflow.svg">'
             "<figcaption>Agent workflow for one GOES scan (docs/agent-workflow.svg)</figcaption></figure>")
    head = sh("git", "rev-parse", "--short", "HEAD").strip()
    page = os.path.join(ROOT, f".report_{os.getpid()}.html")   # in the repo root so docs/*.svg resolve
    with open(page, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>{body}</body></html>")
    try:
        async with async_playwright() as pw:
            br = await pw.chromium.launch()
            pg = await br.new_page()
            await pg.goto("file://" + page, wait_until="load")
            await pg.pdf(path=os.path.join(tmp, "REPORT.pdf"), format="Letter", print_background=True,
                         margin={"top": "0.7in", "bottom": "0.7in", "left": "0.75in", "right": "0.75in"},
                         display_header_footer=True, header_template="<span></span>",
                         footer_template=f"<div style='font-size:8px;width:100%;text-align:center;color:#777'>"
                                         f"Kahu Ola SmokeWatch — REPORT (commit {head}) · <span class=pageNumber></span>/<span class=totalPages></span></div>")
            for name in ("architecture", "agent-workflow"):   # PNG at 2x the SVG's own size, white background
                svg = open(os.path.join(ROOT, "docs", name + ".svg"), encoding="utf-8").read()
                p2 = await br.new_page(device_scale_factor=2)
                await p2.set_content(f"<!doctype html><html><body style='margin:0;background:#fff'>{svg}</body></html>")
                el = await p2.query_selector("svg")
                await el.screenshot(path=os.path.join(tmp, name + ".png"), omit_background=False)
                await p2.close()
            await br.close()
    finally:
        os.remove(page)


def main():
    if sh("git", "status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("working tree has uncommitted changes; commit first (the zip is built from HEAD)")
    tmp = tempfile.mkdtemp(prefix="sw_sub_")
    try:
        asyncio.run(render(tmp))
        sh("git", "archive", "--format=zip", f"--prefix={PREFIX}", "-o", OUT, "HEAD")
        with zipfile.ZipFile(OUT, "a", zipfile.ZIP_DEFLATED) as z:
            z.write(os.path.join(tmp, "REPORT.pdf"), PREFIX + "REPORT.pdf")
            for name in ("architecture", "agent-workflow"):
                z.write(os.path.join(tmp, name + ".png"), f"{PREFIX}docs/{name}.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(OUT, f"{os.path.getsize(OUT) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
