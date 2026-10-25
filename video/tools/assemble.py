"""Assemble the demo: recorded clips + title/slide cards + narration + burned-in captions
-> video/smokewatch-demo.mp4 (1920x1080, 30 fps, H.264 + AAC, no music) and
video/smokewatch-thumbnail.png (1280x720).

  video/.venv/bin/python video/tools/assemble.py
"""
import asyncio
import html
import os
import subprocess

from playwright.async_api import async_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
V = os.path.join(ROOT, "video")
CLIPS, BUILD, NARR = (os.path.join(V, d) for d in ("clips", "build", "narr"))
ANIM = os.path.join(ROOT, "out", "site", "events", "media", "lahaina-2023-08-08.mp4")
PROD = "https://dgmg5co6ld62forty2ubtcwehy0uawnd.lambda-url.us-east-1.on.aws/"
REPO = "github.com/nguyenhuulong2222/kahuola-smokewatch"
LEAD, TAIL, FADE = 0.4, 1.0, 0.2
FONT = "-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif"   # no quotes: used inside style='...'
AMBER = "#f2a900"


def ff(*a):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)


def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                                capture_output=True, text=True).stdout)


STILL = os.path.join(BUILD, "lahaina_0136.png")

TITLE = f"""<div style="display:flex;height:100%;align-items:center;padding:0 110px;gap:70px;color:#e2e8f0">
 <div style="flex:1.1">
  <div style="font-size:24px;letter-spacing:.12em;text-transform:uppercase;color:{AMBER};font-weight:600">OpenCV AI Competition 2026 · AWS</div>
  <div style="font-size:92px;font-weight:800;color:#fff;margin:18px 0 10px;line-height:1.02">Kahu Ola<br>SmokeWatch</div>
  <div style="font-size:32px;color:#cbd5e1;line-height:1.4;max-width:820px">Wildfire watch for Hawaiʻi from GOES-18, every 5 minutes. An AI agent gathers the evidence; a human decides what is published.</div>
  <div style="font-size:34px;margin-top:54px;color:#fff;font-weight:600">Long Nguyen</div>
  <div style="font-size:28px;color:#94a3b8;margin-top:6px">solo developer · Wailuku, Maui</div>
 </div>
 <div style="flex:0.9"><img src="{{still}}" style="width:100%;border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.5)">
  <div style="font-size:20px;color:#94a3b8;margin-top:12px">GOES-18 replay, Lahaina, 8 Aug 2023 15:36 HST</div></div>
</div>"""

FACT = """<div style="display:flex;flex-direction:column;height:100%;align-items:center;justify-content:center;color:#e2e8f0;text-align:center">
 <div style="font-size:34px;letter-spacing:.06em;color:#94a3b8">August 8, 2023 · Lahaina, Maui</div>
 <div style="font-size:120px;font-weight:800;color:#fff;margin-top:16px">102 people killed</div>
 <div style="font-size:30px;color:#94a3b8;margin-top:22px">official count</div></div>"""

LIMITS = f"""<div style="box-sizing:border-box;height:100%;display:flex;flex-direction:column;justify-content:center;padding:0 120px;color:#e2e8f0">
 <div style="font-size:26px;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8">Measured honestly</div>
 <div style="font-size:56px;font-weight:700;margin:12px 0 36px;color:#fff">Limitations and responsible use</div>
 <div style="display:grid;grid-template-columns:1fr 1fr;gap:44px;font-size:29px;line-height:1.45">
  <div style="background:#1e293b;border-radius:18px;padding:30px 36px">
   <div style="color:#fca5a5;font-weight:700;margin-bottom:12px">Limitations</div>
   • Missed 2 of 7 fires (Lahaina morning, Kula): too small for a 2 km pixel<br>
   • Daytime false positives on Hawaiʻi Island on vog days<br>
   • Hawaiʻi Island report times from news; Pūlehu attribution uncertain<br>
   • Agent on the Anthropic API: Bedrock not yet enabled on this new account</div>
  <div style="background:#1e293b;border-radius:18px;padding:30px 36px">
   <div style="color:#86efac;font-weight:700;margin-bottom:12px">Responsible use</div>
   • The agent cannot publish; only a human reviewer can<br>
   • Review is gated in code: tracked, ≥ 2 scans, outside volcanic zones<br>
   • Every public feature carries the disclaimer:<br>
   <span style="color:#fff">“Ước tính tự động từ vệ tinh, không thay thế cảnh báo chính thức”</span><br>
   <span style="color:#94a3b8">(automated satellite estimate, not an official warning)</span></div>
 </div></div>"""

END = f"""<div style="display:flex;flex-direction:column;height:100%;align-items:center;justify-content:center;color:#e2e8f0;text-align:center">
 <div style="font-size:84px;font-weight:800;color:#fff">Kahu Ola SmokeWatch</div>
 <div style="font-size:36px;margin-top:16px;color:#cbd5e1">Built with OpenCV 5 · AWS · Claude</div>
 <div style="font-size:28px;margin-top:56px;font-family:ui-monospace,Menlo,monospace;line-height:1.8;color:#fff">{html.escape(PROD)}<br>{REPO}</div>
 <div style="font-size:24px;margin-top:56px;color:#94a3b8">Long Nguyen · solo developer · Wailuku, Maui<br>Narration is AI-generated. Satellite estimates are not official warnings.</div></div>"""

# segment id -> list of video parts: ("clip", name) | ("card", html, seconds or None=fill) | ("anim", start_s)
SEG = [
    ("s01", [("card", TITLE, None)]),
    ("s02", [("card", FACT, 4.5), ("clip", "s02")]),
    ("s03", [("clip", "s03"), ("anim", 6.2)]),
    ("s04", [("clip", "s04")]),
    ("s05", [("clip", "s05")]),
    ("s06", [("clip", "s06")]),
    ("s07", [("clip", "s07")]),
    ("s08", [("clip", "s08")]),
    ("s09", [("card", LIMITS, None)]),
    ("s10", [("card", END, None)]),
]


def chunks(t):   # caption lines of about 68 characters, split at sentence or clause boundaries
    import re
    parts = [s.strip() for s in re.findall(r"[^.;:!?]+[.;:!?]+[\"']?|[^.;:!?]+$", t)]
    out = []
    for p in parts:
        if len(p) <= 68:
            out.append(p)
            continue
        w, cur = p.split(" "), ""
        n = -(-len(p) // 62)
        tgt = len(p) / n
        for x in w:
            if cur and len(cur + " " + x) > tgt + 6:
                out.append(cur)
                cur = x
            else:
                cur = cur + " " + x if cur else x
        if cur:
            out.append(cur)
    # keep "2:55 p.m." and decimals together: re-join fragments that are too short
    merged = []
    for c in out:
        if merged and (len(c) < 14 or len(merged[-1]) < 14) and len(merged[-1]) + len(c) < 80:
            merged[-1] += " " + c
        else:
            merged.append(c)
    return merged


async def main():
    os.makedirs(BUILD, exist_ok=True)
    ff("-ss", "14.0", "-i", ANIM, "-frames:v", "1", "-vf", "scale=1008:-1:flags=neighbor", STILL)
    import base64
    still_uri = "data:image/png;base64," + base64.b64encode(open(STILL, "rb").read()).decode()
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": 1920, "height": 1080})

        async def png(body, file, transparent=False, w=1920, h=1080):
            await pg.set_viewport_size({"width": w, "height": h})
            bg = "background:transparent" if transparent else "background:radial-gradient(1200px 700px at 30% 20%,#1e293b,#0f172a)"
            body = body.replace("{still}", still_uri)
            await pg.set_content(f"<!doctype html><html><body style='margin:0;width:{w}px;height:{h}px;{bg};font-family:{FONT}'>{body}</body></html>",
                                 wait_until="load")
            await pg.screenshot(path=file, omit_background=transparent)

        def still_clip(p, secs, out):
            ff("-loop", "1", "-i", p, "-t", f"{secs:.3f}", "-r", "30", "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", out)

        listing = []
        for i, (sid, parts) in enumerate(SEG):
            narr = os.path.join(NARR, sid + ".wav")
            nd, text = dur(narr), open(os.path.join(NARR, sid + ".txt")).read().strip()
            total = nd + LEAD + TAIL
            fixed = sum(dur(os.path.join(CLIPS, p[1] + ".mp4")) if p[0] == "clip" else (p[2] or 0) if p[0] == "card" else 0
                        for p in parts)
            vids = []
            for k, p in enumerate(parts):
                out = os.path.join(BUILD, f"{sid}_p{k}.mp4")
                if p[0] == "clip":
                    ff("-i", os.path.join(CLIPS, p[1] + ".mp4"), "-an", "-c:v", "libx264", "-crf", "17", "-r", "30",
                       "-vf", "format=yuv420p", out)
                elif p[0] == "card":
                    img = os.path.join(BUILD, f"{sid}_card{k}.png")
                    await png(p[1], img)
                    still_clip(img, p[2] or max(total - fixed, 2.0), out)
                else:   # the OpenCV case-study animation, letterboxed on slate
                    secs = max(total - fixed, 3.0)
                    ff("-ss", str(p[1]), "-i", ANIM, "-t", f"{secs:.3f}", "-an", "-vf",
                       "scale=-2:900:flags=neighbor,pad=1920:1080:(ow-iw)/2:28:color=0x0f172a,fps=30,format=yuv420p",
                       "-c:v", "libx264", "-crf", "17", out)
                vids.append(out)
            vin = os.path.join(BUILD, f"{sid}_v.mp4")
            # concat filter (re-encode): parts from stills and from clips differ in time base
            ins = [x for v in vids for x in ("-i", v)]
            norm = "".join(f"[{k}:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p[n{k}];" for k in range(len(vids)))
            ff(*ins, "-filter_complex", norm + "".join(f"[n{k}]" for k in range(len(vids))) + f"concat=n={len(vids)}:v=1:a=0[v]",
               "-map", "[v]", "-c:v", "libx264", "-crf", "17", "-r", "30", vin)
            d = max(dur(vin), total)
            # captions, timed by character share of the narration
            cs = chunks(text)
            tot, t, caps = sum(len(c) for c in cs), LEAD, []
            for k, c in enumerate(cs):
                cd = nd * len(c) / tot
                f = os.path.join(BUILD, f"{sid}_cap{k}.png")
                await png(f"<div style='position:absolute;left:0;right:0;bottom:44px;text-align:center'><span style='display:inline-block;"
                          f"max-width:1500px;padding:10px 22px;border-radius:10px;background:rgba(15,23,42,.82);color:#fff;font-size:34px;"
                          f"line-height:1.3;font-weight:500'>{html.escape(c)}</span></div>", f, transparent=True)
                caps.append((f, t, t + cd))
                t += cd
            args = ["-i", vin, "-i", narr]
            for c in caps:
                args += ["-i", c[0]]
            fc = f"[0:v]tpad=stop_mode=clone:stop_duration={d},trim=0:{d},setpts=PTS-STARTPTS[v0]"
            for k, c in enumerate(caps):
                fc += f";[v{k}][{k + 2}:v]overlay=0:0:enable='between(t,{c[1]:.3f},{c[2]:.3f})'[v{k + 1}]"
            fin = "fade=in:st=0:d=0.6," if i == 0 else f"fade=in:st=0:d={FADE},"
            fout = f"fade=out:st={d - 0.9:.3f}:d=0.9" if i == len(SEG) - 1 else f"fade=out:st={d - FADE:.3f}:d={FADE}"
            fc += (f";[v{len(caps)}]{fin}{fout},format=yuv420p[vo];"
                   f"[1:a]aresample=48000,adelay={int(LEAD * 1000)}:all=1,apad,atrim=0:{d}[ao]")
            out = os.path.join(BUILD, f"{i:02d}_{sid}.mp4")
            ff(*args, "-filter_complex", fc, "-map", "[vo]", "-map", "[ao]", "-r", "30", "-c:v", "libx264", "-crf", "17",
               "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-ac", "2", out)
            listing.append(out)
            print(sid, f"{d:.2f}s", flush=True)

        # thumbnail 1280x720
        thumb = f"""<div style="display:flex;height:100%;align-items:center;padding:0 60px;gap:40px;color:#e2e8f0">
          <div style="flex:1"><div style="font-size:20px;letter-spacing:.1em;text-transform:uppercase;color:{AMBER};font-weight:700">GOES-18 · AI agent · human review</div>
          <div style="font-size:68px;font-weight:800;color:#fff;line-height:1.02;margin:12px 0 18px">Kahu Ola<br>SmokeWatch</div>
          <div style="font-size:27px;line-height:1.35;color:#fff">Lahaina: flagged <b style="color:{AMBER}">41&nbsp;min</b> after the first report,
          <b style="color:{AMBER}">~6&nbsp;h</b> before the first polar satellite</div></div>
          <div style="flex:1"><img src="{{still}}" style="width:100%;border-radius:14px"></div></div>"""
        await png(thumb, os.path.join(V, "smokewatch-thumbnail.png"), w=1280, h=720)
        await br.close()
    with open(os.path.join(BUILD, "list.txt"), "w") as f:
        f.write("\n".join(f"file '{p}'" for p in listing))
    out = os.path.join(V, "smokewatch-demo.mp4")
    ff("-f", "concat", "-safe", "0", "-i", os.path.join(BUILD, "list.txt"), "-c", "copy", "-movflags", "+faststart", out)
    print(out, f"{dur(out):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
