"""Deterministic frame-by-frame recorder for the demo video (same approach and look as the
earlier competition video): Chromium at 1536x864 CSS px, device scale 1.25 -> 1920x1080 frames,
a drawn cursor with a halo, amber highlight boxes, eased scroll and cursor moves.

  video/.venv/bin/python video/tools/rec.py [shot ...]      -> video/clips/<shot>.mp4

Web shots are filmed on the PROD URL. Each shot is re-timed to its narration length
(video/narr/<id>.wav) so the picture and the voice end together.
"""
import asyncio
import html
import json
import os
import shutil
import subprocess
import sys

from playwright.async_api import async_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
V = os.path.join(ROOT, "video")
CLIPS, BUILD, NARR = (os.path.join(V, d) for d in ("clips", "build", "narr"))
PROD = os.environ.get("PROD_URL", "https://dgmg5co6ld62forty2ubtcwehy0uawnd.lambda-url.us-east-1.on.aws/")
FPS = 30
LEAD, TAIL = 0.4, 1.0          # narration starts LEAD s into a shot; TAIL s of picture after it


def narr_len(sid):
    p = os.path.join(NARR, sid + ".wav")
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", p], capture_output=True, text=True).stdout)


# Keyframe times are written for a nominal duration "dur" and scaled to the narration.
# scroll/cursor targets: a number / [x, y], or {"sel", "idx", "text", "ax", "ay", "off"}.
VIEW = PROD + "?date=2023-08-08&t=0051Z&region=maui#viewer"
SHOTS = {
    # problem: header, headline and the report -> polar-orbiter gap on the case-study timeline
    "s02": dict(url=PROD, narr="s02", dur=20.0, skip=4.5, scroll=[(0, 0)],
                cursor=[(0, [1100, 650]), (2.0, {"sel": "#headline"}), (6.0, {"sel": ".tl .dot", "idx": 0}),
                        (11.0, {"sel": ".tl .dot", "idx": 3}), (15.5, {"sel": ".tl .gap span"})],
                hl=[(1.5, 6.0, {"sel": "#headline"}),
                    (6.0, 11.0, {"sel": ".tl .m", "idx": 0}),
                    (11.0, 20.0, {"sel": ".tl .m", "idx": 3}),
                    (15.0, 20.0, {"sel": ".tl .gap span"})]),
    # case study: the timeline step by step (report -> 1 scan, re-check -> 3/3 review -> MODIS)
    "s03": dict(url=PROD, narr="s03", dur=14.0, part=0.55, scroll=[(0, 0), (1.0, 120)],
                cursor=[(0, [900, 500]), (2.0, {"sel": ".tl .dot", "idx": 0}), (4.5, {"sel": ".tl .dot", "idx": 1}),
                        (8.5, {"sel": ".tl .dot", "idx": 2}), (12.0, {"sel": ".tl .dot", "idx": 3})],
                hl=[(2.0, 4.5, {"sel": ".tl .m", "idx": 0}), (4.5, 8.5, {"sel": ".tl .m", "idx": 1}),
                    (8.5, 14.0, {"sel": ".tl .m", "idx": 2}), (11.5, 14.0, {"sel": ".tl .m", "idx": 3})]),
    # timeline viewer: Play through the Lahaina afternoon
    "s04": dict(url=VIEW, narr="s04", dur=17.0, scroll=[(0, {"sel": "#viewer", "off": -10}), (9.8, {"sel": "#viewer", "off": -10}),
                                                    (11.0, {"sel": ".legend", "off": -640})],
                cursor=[(0, [1200, 600]), (1.0, {"sel": "#play"}), (3.0, {"sel": "#cv", "ax": 0.3, "ay": 0.45}),
                        (11.0, {"sel": ".legend", "ax": 0.75})],
                hl=[(0.9, 2.4, {"sel": "#play"}), (10.8, 17.0, {"sel": ".legend"}), (2.5, 9.5, {"sel": "#mode"})],
                play=dict(start=2.0, end=16.0, frm="0051Z", to="0216Z", hold={"0121Z": 1.2, "0136Z": 1.6})),
    # live status
    "s05": dict(url=PROD, narr="s05", dur=8.0, scroll=[(0, {"sel": "#live", "off": -60})],
                cursor=[(0, [1200, 300]), (1.0, {"sel": "#livecard", "ax": 0.6, "ay": 0.25})],
                hl=[(0.8, 8.0, {"sel": "#livecard"})]),
    # evaluation table, then the Claude-vs-rules trace card
    "s08": dict(url=PROD, narr="s08", dur=34.0, scroll=[(0, {"sel": "#results", "off": -20}), (24.0, {"sel": "#results", "off": -20}),
                                                     (25.0, {"sel": "#trace", "off": -30})],
                cursor=[(0, [1300, 300]), (1.5, {"sel": "#evaltab tr", "idx": 4, "ax": 0.2}),
                        (8.0, {"sel": "#evaltab tr", "idx": 4, "ax": 0.8}), (13.5, {"sel": "#evaltab p", "idx": 0, "ax": 0.15}),
                        (16.5, {"sel": "#evaltab tr", "idx": 2, "ax": 0.45}), (19.5, {"sel": "#evaltab tr", "idx": 3, "ax": 0.45}),
                        (25.5, {"sel": "#trace", "ax": 0.3, "ay": 0.35}), (29.0, {"sel": "#trace", "ax": 0.8, "ay": 0.35})],
                hl=[(1.0, 13.0, {"sel": "#evaltab tbody"}), (4.0, 13.0, {"sel": "#evaltab tr", "idx": 4}),
                    (13.0, 16.0, {"sel": "#evaltab p", "idx": 0}),
                    (16.0, 23.5, {"sel": "#evaltab tr", "idx": 2}), (16.0, 23.5, {"sel": "#evaltab tr", "idx": 3}),
                    (25.5, 34.0, {"sel": "#trace"})]),
}

INIT = r"""(() => {
window.__find = q => {
  if (Array.isArray(q) || typeof q === 'number') return null;
  let els = [...document.querySelectorAll(q.sel || '*')];
  if (q.text) els = els.filter(e => (e.innerText || '').includes(q.text)).sort((a, b) => a.innerText.length - b.innerText.length);
  return els[q.text ? 0 : (q.idx || 0)] || null; };
window.__pt = q => { if (Array.isArray(q)) return q; const e = __find(q); if (!e) return [768, 432];
  const b = e.getBoundingClientRect(); return [b.x + b.width * (q.ax ?? 0.5), b.y + b.height * (q.ay ?? 0.5)]; };
window.__top = q => { if (typeof q === 'number') return q; const e = __find(q); if (!e) return scrollY;
  return e.getBoundingClientRect().top + scrollY + (q.off || 0); };
const s = document.createElement('style');
s.textContent = `#__cur{position:fixed;z-index:2147483647;pointer-events:none;width:44px;height:44px;margin:-6px 0 0 -6px}
 #__cur .halo{position:absolute;left:-14px;top:-14px;width:40px;height:40px;border-radius:50%;background:rgba(255,196,0,.35)}
 .__hl{position:fixed;z-index:2147483646;pointer-events:none;border:3px solid #f2a900;border-radius:8px;box-shadow:0 0 0 4px rgba(242,169,0,.25),0 0 18px rgba(242,169,0,.45)}
 *{caret-color:transparent!important} html{scroll-behavior:auto!important}`;
document.head.appendChild(s);
const c = document.createElement('div'); c.id = '__cur';
c.innerHTML = '<div class="halo"></div><svg width="28" height="34" viewBox="0 0 28 34" style="position:absolute;left:0;top:0"><path d="M2 2 L2 27 L8.5 21 L13 31 L17.5 29 L13 19.5 L22 19.5 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>';
document.body.appendChild(c);
})()"""

FRAME = r"""({sa, sb, sp, ca, cb, cp, hl, hls}) => {
  const y = __top(sa) + (__top(sb) - __top(sa)) * sp; window.scrollTo(0, Math.round(y));
  const A = __pt(ca), B = __pt(cb), x = A[0] + (B[0] - A[0]) * cp, yy = A[1] + (B[1] - A[1]) * cp;
  const c = document.getElementById('__cur'); c.style.left = Math.round(x) + 'px'; c.style.top = Math.round(yy) + 'px';
  document.querySelectorAll('.__hl').forEach(n => n.remove());
  hl.forEach((h, i) => { if (hls[i] <= 0) return; const e = __find(h); if (!e) return; const b = e.getBoundingClientRect();
    const d = document.createElement('div'); d.className = '__hl';
    Object.assign(d.style, {left: (b.x - 6) + 'px', top: (b.y - 5) + 'px', width: (b.width + 12) + 'px', height: (b.height + 10) + 'px', opacity: hls[i]});
    document.body.appendChild(d); });
  return [Math.round(scrollY), Math.round(x), Math.round(yy), hls.map(v => v.toFixed(2)).join(), (window.__state || '')].join('|');
}"""


def ease(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def seg(keys, t):
    i = 0
    while i < len(keys) - 1 and keys[i + 1][0] <= t:
        i += 1
    a = keys[i]
    b = keys[i + 1] if i + 1 < len(keys) else None
    if not b:
        return a[1], a[1], 1.0
    return a[1], b[1], ease((t - a[0]) / (b[0] - a[0]))


def retime(S, total):
    k = total / S["dur"]
    S["dur"] = total
    S["scroll"] = [(t * k, v) for t, v in S["scroll"]]
    S["cursor"] = [(t * k, v) for t, v in S["cursor"]]
    S["hl"] = [(a * k, b * k, q) for a, b, q in S["hl"]]
    if "play" in S:
        P = S["play"]
        P["start"] *= k
        P["end"] *= k


def encode(frames_dir, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{frames_dir}/%05d.jpg",
                    "-vf", "scale=1920:1080:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", "16",
                    "-preset", "medium", "-r", str(FPS), out], check=True)


async def web_shot(ctx, name, S):
    total = narr_len(S["narr"]) + LEAD + TAIL
    if "skip" in S:            # the first seconds of the segment are a title card (assemble)
        total -= S["skip"]
    if "part" in S:            # the rest of the segment is another clip (assemble)
        total *= S["part"]
    retime(S, total)
    p = await ctx.new_page()
    await p.goto(S["url"], wait_until="networkidle", timeout=90000)
    await p.wait_for_timeout(3000)
    # freeze the looping case-study video on a review frame so screenshots are deterministic
    await p.evaluate("() => { const v = document.getElementById('vid'); if (v) { v.pause(); v.removeAttribute('controls'); "
                     "v.currentTime = Math.min(v.duration || 0, 13.2); } }")
    await p.evaluate(INIT)
    await p.wait_for_timeout(800)
    play = S.get("play")
    steps = []
    if play:   # step the viewer scan by scan (what Play does, without its wall-clock timer)
        sc = await p.evaluate("() => st.idx.scans.map(s => s.t)")
        i0, i1 = sc.index(play["frm"]), sc.index(play["to"])
        await p.evaluate(f"() => show({i0})")
        hold = play.get("hold", {})
        weights = [1 + hold.get(sc[i], 0) / 0.5 for i in range(i0, i1 + 1)]
        span, acc = play["end"] - play["start"], 0.0
        for w, i in zip(weights, range(i0, i1 + 1)):
            steps.append((play["start"] + span * acc / sum(weights), i))
            acc += w
    d = os.path.join(BUILD, name + "_frames")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    n, prev_key, prev_file, k = round(S["dur"] * FPS), None, None, 0
    for f in range(n):
        t = f / FPS
        if play and t >= play["start"] and not await p.evaluate("() => window.__playing || false"):
            await p.evaluate("() => { const b = document.getElementById('play'); b.textContent = '❚❚ Pause'; "
                             "b.classList.add('on'); window.__playing = true; }")
        while k < len(steps) and steps[k][0] <= t:
            await p.evaluate(f"async () => {{ await show({steps[k][1]}); window.__state = 'i{steps[k][1]}'; }}")
            k += 1
        sa, sb, sp = seg(S["scroll"], t)
        ca, cb, cp = seg(S["cursor"], t)
        hls = [max(0.0, min(1.0, (t - a) / 0.3, (b - t) / 0.3)) for a, b, _ in S["hl"]]
        key = await p.evaluate(FRAME, dict(sa=sa, sb=sb, sp=sp, ca=ca, cb=cb, cp=cp,
                                           hl=[q for _, _, q in S["hl"]], hls=hls))
        file = f"{d}/{f:05d}.jpg"
        if key == prev_key:
            os.link(prev_file, file)
        else:
            await p.screenshot(path=file, type="jpeg", quality=93)
            prev_key, prev_file = key, file
    encode(d, os.path.join(CLIPS, name + ".mp4"))
    shutil.rmtree(d)
    await p.close()
    print(name, f"{S['dur']:.1f}s", n, "frames", flush=True)


# ---------------------------------------------------------------- agent replay (terminal)

def agent_lines():
    """Terminal transcript of the local replay with the deployed image, from the real outputs
    in video/build/agent (run by video/tools/agent_replay.sh)."""
    A = os.path.join(BUILD, "agent")
    digest = open(os.path.join(A, "digest.txt")).read().strip()
    out = json.load(open(os.path.join(A, "out_0136.json")))
    tr = json.load(open(os.path.join(A, "store", "traces", out["episode"] + ".json")))
    names = {1: "Lahaina", 2: "Upcountry Maui", 3: "Kawaihae, Hawaiʻi Island"}
    L = [("cmd", "docker inspect scanfunction:v1 --format '{{index .RepoDigests 0}}'"),
         ("out", digest + "   # same digest as the deployed Lambda"),
         ("cmd", "docker run -d -p 9000:8080 -e AGENT_POLICY=anthropic -e LOCAL_STORE=/opt/out … scanfunction:v1 handler.scan"),
         ("cmd", "curl -s localhost:9000/2015-03-31/functions/function/invocations -d '{\"time\":\"2023-08-09T01:36:00Z\"}'"),
         ("dim", f"perception (OpenCV): {tr['candidates']} thermal anomalies · background from {tr['background_scans']} reference scans")]
    for s in tr["trace"]:
        if "llm_call" in s:
            c = s["llm_call"]
            L.append(("llm", f"claude-sonnet-5-5 → tool_use   ({c['input_tokens']:,} in / {c['output_tokens']:,} out tokens, {c['latency_ms'] / 1000:.1f} s)"))
            continue
        a, r, who = s["args"], s.get("result", {}), names.get(s["args"].get("det_id"), "")
        if s["tool"] == "track_plume":
            seen = " ".join(x["time"] for x in r["scans"] if x["hotspot"])
            L.append(("tool", f"track_plume(#{a['det_id']} {who}, 10 min)  →  hotspot in {r['hotspot_scans']}/{r['scans_checked']} scans ({seen}) · plume attached: {'yes' if r['smoke'].get('attached_plume') else 'no'}"))
        elif s["tool"] == "query_firms":
            near = f", nearest {r['nearest_km']} km" if r.get("nearest_km") is not None else ""
            L.append(("tool", f"query_firms(#{a['det_id']} {who})  →  {r['count']} VIIRS/MODIS detections in prior 24 h{near}"))
        elif s["tool"] == "request_human_review":
            L.append(("act", f"request_human_review(#{a['det_id']} {who}, confidence {a['confidence']})"))
            L.append(("why", "“" + a["rationale"] + "”"))
    u = tr["llm_usage"]
    L.append(("ok", f"{len(out['outcomes'])} reviews queued for a human · errors: {out['errors']} · {u['calls']} model calls · {out['seconds']} s"))
    return L


def agent_html(lines, shown):
    col = {"cmd": "#e2e8f0", "out": "#94a3b8", "dim": "#94a3b8", "llm": "#c4b5fd", "tool": "#7dd3fc",
           "act": "#fca5a5", "why": "#cbd5e1", "ok": "#86efac"}
    rows = []
    for i, (k, s) in enumerate(lines[:shown]):
        pre = '<span style="color:#f2a900">$ </span>' if k == "cmd" else ("&nbsp;&nbsp;&nbsp;&nbsp;" if k == "why" else "")
        style = f"color:{col[k]};" + ("font-style:italic;" if k == "why" else "") + ("font-weight:600;" if k in ("act", "ok") else "")
        rows.append(f'<div style="{style}">{pre}{html.escape(s)}</div>')
    return f"""<!doctype html><html><body style="margin:0;width:1536px;height:864px;background:#0f172a;font-family:-apple-system,'SF Pro Text',Helvetica,sans-serif">
<div style="position:absolute;left:0;right:0;top:0;padding:16px 40px;background:#f2a900;color:#111;font-size:22px;font-weight:700;text-align:center">
Local replay of the same container image — prod is not replayed to keep the live queue clean</div>
<div style="position:absolute;left:40px;right:40px;top:78px;bottom:96px;background:#020617;border-radius:14px;border:1px solid #334155;padding:22px 26px;
font-family:ui-monospace,Menlo,monospace;font-size:15.5px;line-height:1.55;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end">
{''.join(rows)}</div></body></html>"""


async def agent_shot(br, name="s06"):
    total = narr_len("s06") + LEAD + TAIL
    lines = agent_lines()
    pg = await br.new_page(viewport={"width": 1536, "height": 864}, device_scale_factor=1.25)
    d = os.path.join(BUILD, name + "_frames")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    n = round(total * FPS)
    # commands appear in the first 18 %, the trace steps over the next 70 %
    t_cmd, t_end = 0.04 * total, 0.88 * total
    times = [t_cmd + (t_end - t_cmd) * i / (len(lines) - 1) for i in range(len(lines))]
    prev, prev_file = None, None
    for f in range(n):
        shown = sum(1 for x in times if x <= f / FPS)
        file = f"{d}/{f:05d}.jpg"
        if shown == prev:
            os.link(prev_file, file)
            continue
        await pg.set_content(agent_html(lines, shown))
        await pg.screenshot(path=file, type="jpeg", quality=93)
        prev, prev_file = shown, file
    encode(d, os.path.join(CLIPS, name + ".mp4"))
    shutil.rmtree(d)
    await pg.close()
    print(name, f"{total:.1f}s", n, "frames", flush=True)


# ---------------------------------------------------------------- architecture pan/zoom

async def svg_shot(br, name="s07"):
    total = narr_len("s07") + LEAD + TAIL
    arch = open(os.path.join(ROOT, "docs", "architecture.svg")).read()
    flow = open(os.path.join(ROOT, "docs", "agent-workflow.svg")).read()
    pg = await br.new_page(viewport={"width": 1536, "height": 864}, device_scale_factor=1.25)
    d = os.path.join(BUILD, name + "_frames")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    n, split = round(total * FPS), 0.62 * total
    page = """<!doctype html><html><body style="margin:0;width:1536px;height:864px;background:#f8fafc;overflow:hidden">
<div id="w" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;transform-origin:50% 50%">
<div id="s" style="width:1380px;height:760px;display:flex;align-items:center;justify-content:center"></div></div>
<style>#s svg{max-width:100%;max-height:100%;width:auto;height:auto}</style></body></html>"""
    await pg.set_content(page)
    cur = None
    for f in range(n):
        t = f / FPS
        which = "arch" if t < split else "flow"
        if which != cur:
            await pg.evaluate("svg => { document.getElementById('s').innerHTML = svg; "
                              "const e = document.querySelector('#s svg'); e.removeAttribute('width'); e.removeAttribute('height'); }",
                              arch if which == "arch" else flow)
            cur = which
        u = t / split if which == "arch" else (t - split) / (total - split)
        if which == "arch":   # slow zoom toward the OpenCV / Lambda part, then back out
            z = 1 + 0.10 * ease(min(1, u))
            ox = 0
        else:
            z, ox = 1 + 0.12 * u, 0
        await pg.evaluate(f"() => document.getElementById('w').style.transform = 'translateX({ox:.1f}px) scale({z:.4f})'")
        await pg.screenshot(path=f"{d}/{f:05d}.jpg", type="jpeg", quality=93)
    encode(d, os.path.join(CLIPS, name + ".mp4"))
    shutil.rmtree(d)
    await pg.close()
    print(name, f"{total:.1f}s", n, "frames", flush=True)


# ---------------------------------------------------------------- human review (local)

DEMO_TOKEN = "local-demo-token-not-a-real-secret"   # throwaway, local server only; typed into a password field


async def review_shot(br, name="s06b"):
    """Local review page on a fresh copy of the 01:36Z replay output: type the token (dots only),
    Approve the Lahaina candidate, then show the public layer the handler now serves."""
    import tempfile
    import urllib.request
    total = max(15.0, narr_len(name) + LEAD + TAIL)
    store = tempfile.mkdtemp(prefix="sw_review_")
    shutil.copytree(os.path.join(BUILD, "agent", "store"), store, dirs_exist_ok=True)
    port = 8767
    srv = subprocess.Popen([os.path.join(ROOT, ".venv", "bin", "python"), os.path.join(ROOT, "scripts", "serve_local.py")],
                           env={**os.environ, "LOCAL_STORE": store, "REVIEW_TOKEN": DEMO_TOKEN, "PORT": str(port)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "review", timeout=1)
                break
            except Exception:
                await asyncio.sleep(0.2)
        pg = await br.new_page(viewport={"width": 1536, "height": 864}, device_scale_factor=1.25, color_scheme="dark")
        await pg.goto(base + "review", wait_until="networkidle")
        banner = ("Local review page on the replay output — only a human can publish")
        BAN = ("() => { const b = document.createElement('div'); b.id = '__ban'; b.textContent = %s;"
               " Object.assign(b.style, {position:'fixed', left:0, right:0, top:0, zIndex:2147483645, padding:'14px 30px',"
               " background:'#f2a900', color:'#111', font:'700 22px -apple-system,Helvetica,sans-serif', textAlign:'center'});"
               " document.body.appendChild(b); document.body.style.paddingTop = '56px'; }") % json.dumps(banner)
        await pg.evaluate(BAN)
        await pg.evaluate(INIT)
        rid = "-1"   # det 1 = Lahaina
        card = {"sel": f"article.card:has(button[data-id$='{rid}'])"}
        tok, appr = {"sel": "#tok"}, {"sel": f"button[data-id$='{rid}'][data-d=approve]"}
        S = dict(dur=total, scroll=[(0, 0), (1.0, 0), (2.5, {**card, "off": -70})],
                 cursor=[(0, [900, 400]), (2.6, {**card, "ax": 0.35, "ay": 0.35}), (5.0, {**card, "ax": 0.7, "ay": 0.55}),
                         (6.2, tok), (8.6, tok), (9.2, appr), (10.6, appr)],
                 hl=[(2.6, 6.0, card), (6.2, 8.8, tok), (9.0, 10.4, appr)])
        k = total / 15.0
        S["scroll"] = [(t * k, v) for t, v in S["scroll"]]
        S["cursor"] = [(t * k, v) for t, v in S["cursor"]]
        S["hl"] = [(a * k, b * k, q) for a, b, q in S["hl"]]
        t_type0, t_type1, t_click, t_geo = 6.3 * k, 8.4 * k, 9.4 * k, 11.2 * k
        d = os.path.join(BUILD, name + "_frames")
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        n, prev_key, prev_file, typed, clicked, geo = round(total * FPS), None, None, 0, False, False
        for f in range(n):
            t = f / FPS
            if t_type0 <= t and typed < len(DEMO_TOKEN):
                want = min(len(DEMO_TOKEN), int(len(DEMO_TOKEN) * (t - t_type0) / (t_type1 - t_type0)) + 1)
                if want > typed:
                    await pg.evaluate(f"v => {{ document.getElementById('tok').value = v; window.__state = 'tok{want}'; }}",
                                      DEMO_TOKEN[:want])
                    typed = want
            if t >= t_click and not clicked:
                await pg.evaluate(f"() => document.querySelector(\"button[data-id$='{rid}'][data-d=approve]\").click()")
                await pg.wait_for_function("() => /→ (published|rejected)|error:/.test(document.body.innerText)", timeout=10000)
                await pg.evaluate("() => { window.__state = 'clicked'; }")
                clicked = True
                S["hl"].append((t_click + 0.2, t_geo, {"sel": "article.card .btns", "text": "published"}))
            if t >= t_geo and not geo:   # the public layer, exactly as GET /public/smoke.geojson returns it
                body = urllib.request.urlopen(base + "public/smoke.geojson", timeout=5).read().decode()
                g, coords = json.loads(body), {}
                for i, ft in enumerate(g.get("features", [])):   # coordinates on one line, 4 decimals
                    c = json.loads(json.dumps(ft["geometry"]["coordinates"]), parse_float=lambda x: round(float(x), 4))
                    coords[f"__C{i}__"] = json.dumps(c, separators=(",", ":"))
                    ft["geometry"]["coordinates"] = f"__C{i}__"
                pretty = json.dumps(g, indent=2, ensure_ascii=False)
                for m, c in coords.items():
                    pretty = pretty.replace(json.dumps(m), c)
                await pg.set_content(
                    "<!doctype html><html><body style='margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,Helvetica,sans-serif'>"
                    "<div style='margin:76px 40px 0;padding:10px 16px;background:#1e293b;border-radius:10px;font:18px ui-monospace,Menlo,monospace;color:#94a3b8'>"
                    f"GET {html.escape(base)}public/smoke.geojson</div>"
                    "<pre id=geo style='margin:16px 40px;padding:20px 24px;background:#020617;border:1px solid #334155;border-radius:12px;"
                    "font:15px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap;max-height:640px;overflow:hidden'>"
                    + html.escape(pretty) + "</pre></body></html>")
                await pg.evaluate(BAN)
                await pg.evaluate("() => { document.body.style.paddingTop = '0'; }")
                await pg.evaluate(INIT)
                await pg.evaluate("""() => { const pre = document.getElementById('geo');
                  pre.innerHTML = pre.innerHTML.replace(/("disclaimer(_en)?": "[^\\n]*)/g, '<span class=__d>$1</span>')
                                               .replace(/("status": "published")/, '<span class=__s>$1</span>'); }""")
                S["scroll"].append((t, 0))
                S["cursor"] += [(t, [1300, 700]), (t + 1.0, {"sel": ".__d", "ax": 0.9})]
                S["hl"] = [(t + 0.3, total, {"sel": ".__d"}), (t + 1.2, total, {"sel": ".__s"})]
                geo = True
            sa, sb, sp = seg(S["scroll"], t)
            ca, cb, cp = seg(S["cursor"], t)
            hls = [max(0.0, min(1.0, (t - a) / 0.3, (b - t) / 0.3)) for a, b, _ in S["hl"]]
            key = await pg.evaluate(FRAME, dict(sa=sa, sb=sb, sp=sp, ca=ca, cb=cb, cp=cp,
                                                hl=[q for _, _, q in S["hl"]], hls=hls))
            file = f"{d}/{f:05d}.jpg"
            if key == prev_key:
                os.link(prev_file, file)
            else:
                await pg.screenshot(path=file, type="jpeg", quality=93)
                prev_key, prev_file = key, file
        encode(d, os.path.join(CLIPS, name + ".mp4"))
        shutil.rmtree(d)
        await pg.close()
        print(name, f"{total:.1f}s", n, "frames", flush=True)
    finally:
        srv.terminate()
        shutil.rmtree(store, ignore_errors=True)


async def main(only):
    os.makedirs(CLIPS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        ctx = await br.new_context(viewport={"width": 1536, "height": 864}, device_scale_factor=1.25,
                                   color_scheme="dark")
        for name, S in SHOTS.items():
            if not only or name in only:
                await web_shot(ctx, name, dict(S))
        if not only or "s06" in only:
            await agent_shot(br)
        if not only or "s06b" in only:
            await review_shot(br)
        if not only or "s07" in only:
            await svg_shot(br)
        await br.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
