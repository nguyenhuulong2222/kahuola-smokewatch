"""Backfill the Maui fires of 8 Aug 2023 (HST day) into events/ from the eval cache.

No NOAA download: frames come from .cache/goes (.npz), agent decisions from the eval traces
(eval/runs/*/traces), FIRMS points from the offline archive. Also renders the Lahaina case
study animation (OpenCV VideoWriter H.264 MP4 + OpenCV GIF) and events/featured.json.

  FIRMS_ARCHIVE_DIR=.cache/firms python scripts/backfill_events.py --out out/site
  aws s3 sync out/site s3://<bucket>/            # then the live page serves it
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import glob
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smokewatch import fire, firms, goes, render, vision  # noqa: E402
from smokewatch.store import LocalStore  # noqa: E402

UTC = dt.timezone.utc
DAY = "2023-08-08"   # HST calendar day = 10:00Z on 8 Aug .. 10:00Z on 9 Aug
T0 = dt.datetime(2023, 8, 8, 10, 0, tzinfo=UTC)
T1 = T0 + dt.timedelta(days=1)
FIRES = ["olinda", "lahaina_am", "kula", "lahaina_pm", "pulehu"]
ANIM = (dt.datetime(2023, 8, 8, 22, 0, tzinfo=UTC), dt.datetime(2023, 8, 9, 3, 5, tzinfo=UTC))
HOLD = {"0121Z": 8, "0136Z": 12}   # extra frames at the agent's two decisions on Lahaina


def cache_index(cache):
    idx = {}
    for p in glob.glob(os.path.join(cache, "*.npz")):
        k = os.path.basename(p)[:-4]
        idx[goes.scan_start(k)] = p
    return idx


def make_source(idx):
    times = sorted(idx)

    @functools.lru_cache(maxsize=96)
    def source(t):
        best = min(times, key=lambda x: abs((x - t).total_seconds()))
        if abs((best - t).total_seconds()) > 150:
            raise KeyError(f"not cached: {t:%Y-%m-%d %H:%M}Z")   # never download here
        return goes._load_npz(idx[best])
    return source


def eval_summaries():
    """frame_time -> eval summary; when several cases ran a scan, keep the most decided one."""
    by_t = {}
    for p in glob.glob("eval/runs/*/traces/*.json"):
        s = json.load(open(p))
        t = dt.datetime.fromisoformat(s["frame_time"])
        if not (T0 <= t < T1):
            continue
        score = (sum(o["action"] == "request_human_review" for o in s["outcomes"].values()), len(s["outcomes"]))
        if t not in by_t or score > by_t[t][0]:
            by_t[t] = (score, s)
    return {t: s for t, (_, s) in by_t.items()}


def firms_before(t, hours=12):
    d = os.environ.get("FIRMS_ARCHIVE_DIR", ".cache/firms")
    pts = firms.archive_points(d, t - dt.timedelta(hours=hours), t)
    return [{"lat": p["lat"], "lon": p["lon"], "t": f"{p['t']:%H%M}Z", "src": p["src"]}
            for p in pts if p.get("type") in (None, "", "0") and 20.4 < p["lat"] < 21.3
            and -157.2 < p["lon"] < -155.9]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".cache/goes")
    ap.add_argument("--out", default="out/site")
    a = ap.parse_args(argv)
    store = LocalStore(a.out)
    idx = cache_index(a.cache)
    source = make_source(idx)
    ev = eval_summaries()
    scans = sorted(t for t in idx if T0 <= t < T1)
    print(f"{len(scans)} cached scans on HST {DAY}, {len(ev)} with agent decisions")
    video = []
    for i, t in enumerate(scans):
        frame = source(t)
        try:
            bg = fire.fetch_background(t, source)
            bg = bg if bg.n_refs >= fire.FP["bg_min_refs"] else None
        except ValueError:
            bg = None
        ddt = render.anomaly_field(frame, bg)
        s = ev.get(t)
        summary = s or {"frame_time": t.isoformat(), "daytime": vision.is_daytime(frame),
                        "background_scans": bg.n_refs if bg else 0, "policy": None, "episode": None}
        rec = render.scan_record(summary, render.candidates_from_summary(s) if s else [], firms_before(t))
        rec["agent_run"] = s is not None
        if s:   # the viewer links to /api/trace/<id>; events/ is kept, traces/ expires
            store.put_json(f"events/traces/{s['episode']}.json", s)
        rec["source"] = "replay (eval)"
        prefix = render.scan_prefix("events", t)
        if i == 0:
            render.write_static(store, frame, force=True)
        render.write_scan(store, prefix, frame, ddt, rec)
        render.update_index(store, "events", rec, prefix, render.geo(frame))
        if ANIM[0] <= t <= ANIM[1]:
            video.append((t, frame, ddt, rec))
        print(f"  {t:%m-%d %H:%M}Z bg={bg.n_refs if bg else 0} cands={len(rec['candidates'])} "
              f"agent={'y' if s else '-'}", flush=True)
    write_video(store, a.out, video)
    print("done")
    write_featured(store)


def markers(rec):
    out = []
    for c in rec["candidates"]:
        if c.get("volcanic"):
            continue
        label = ("REVIEW" if c["review"] else (c.get("action") or "").replace("schedule_", "")) + \
                f" +{c['max_ddt_k']:.0f}K"
        out.append({"lat": c["lat"], "lon": c["lon"], "review": c["review"], "text": label})
    return out


def caption(t):
    if t < dt.datetime(2023, 8, 9, 0, 55, tzinfo=UTC):
        return "Lahaina, 8 Aug 2023 - before the afternoon fire"
    if t < dt.datetime(2023, 8, 9, 1, 21, tzinfo=UTC):
        return "00:55Z first official report (Lahaina)"
    if t < dt.datetime(2023, 8, 9, 1, 36, tzinfo=UTC):
        return "01:21Z agent: hotspot in 1 of 3 scans -> re-check"
    if t < dt.datetime(2023, 8, 9, 1, 51, tzinfo=UTC):
        return "01:36Z agent: persistent 3/3 scans -> human review"
    return "first polar-orbiter detection: MODIS Terra 07:50Z"


def write_video(store, out, video):
    imgs, holds = [], []
    for t, frame, ddt, rec in video:
        imgs.append(render.composite(frame, ddt, markers(rec), title=caption(t)))
        holds.append(HOLD.get(f"{t:%H%M}Z", 1) if t.date() == dt.date(2023, 8, 9) else 1)
    h, w = imgs[0].shape[:2]
    os.makedirs(os.path.join(out, "events/media"), exist_ok=True)
    mp4 = os.path.join(out, "events/media/lahaina-2023-08-08.mp4")
    vw = cv2.VideoWriter(mp4, cv2.VideoWriter_fourcc(*"avc1"), 4, (w, h))   # H.264
    assert vw.isOpened(), "no H.264 encoder"
    for img, n in zip(imgs, holds):
        for _ in range(n):
            vw.write(img)
    vw.release()
    anim = cv2.Animation()   # GIF fallback, also written by OpenCV; holds become long frames
    anim.frames = [cv2.resize(i, (w * 2 // 3, h * 2 // 3), interpolation=cv2.INTER_AREA) for i in imgs]
    anim.durations = [250 * n for n in holds]
    anim.loop_count = 0
    gif = os.path.join(out, "events/media/lahaina-2023-08-08.gif")
    assert cv2.imwriteanimation(gif, anim)
    print(f"video: {len(imgs)} scans {w}x{h}; mp4 {os.path.getsize(mp4) // 1024} KB, "
          f"gif {os.path.getsize(gif) // 1024} KB")
    # poster = the review frame
    key = next(i for i, (t, *_ ) in enumerate(video) if f"{t:%H%M}Z" == "0136Z")
    t, frame, ddt, rec = video[key]
    ok, jpg = cv2.imencode(".jpg", render.composite(frame, ddt, markers(rec), title=caption(t)),
                           [cv2.IMWRITE_JPEG_QUALITY, 85])
    store.put_bytes("events/media/lahaina-2023-08-08-poster.jpg", jpg.tobytes(), "image/jpeg")


def write_featured(store):
    r = json.load(open("eval/results.json"))
    cmp_ = json.load(open("eval/policy_compare_anthropic.json"))
    s0136 = next(s for s in cmp_["scans"] if s["time"].startswith("2023-08-09T01:36"))
    steps = [st for st in s0136["anthropic"]["trace"] if str((st.get("args") or {}).get("det_id")) == "1"]
    card = {"scan": "2023-08-09T01:36Z", "model": cmp_.get("model_id"),
            "perception": s0136["candidates"]["1"],
            "steps": [{"tool": st["tool"], "args": st["args"], "result": st["result"]} for st in steps],
            "outcome": s0136["anthropic"]["outcomes"]["1"]}
    events = [{k: e.get(k) for k in ("id", "name", "first_report", "first_review", "first_polar",
                                     "first_polar_sensor", "review_minus_report_min",
                                     "review_minus_first_polar_min", "time_source")} for e in r["events"]]
    p = r["precision"]
    store.put_json("events/featured.json", {
        "featured": [{
            "id": "lahaina-2023-08-08", "title": "Lahaina, Maui — 8 Aug 2023", "date": DAY, "t": "0030Z",
            "region": "maui",
            "headline": "Flagged 41 min after the first official report — ~6 h before the first "
                        "polar-orbiting satellite.",
            "timeline": [
                {"t": "2023-08-09T00:55Z", "label": "First official report", "kind": "report"},
                {"t": "2023-08-09T01:21Z", "label": "GOES hotspot, 1 scan → agent re-checks", "kind": "recheck"},
                {"t": "2023-08-09T01:36Z", "label": "Persistent 3/3 scans → human review", "kind": "review"},
                {"t": "2023-08-09T07:50Z", "label": "First polar-orbiter detection (MODIS Terra)", "kind": "polar"}],
            "video": {"mp4": "events/media/lahaina-2023-08-08.mp4", "gif": "events/media/lahaina-2023-08-08.gif",
                      "poster": "events/media/lahaina-2023-08-08-poster.jpg"},
            "agent_card": card}],
        "eval": {"events": events, "precision": {k: p[k] for k in (
            "tp_reviews", "fp_reviews", "precision", "negative_frames", "false_alarm_frames",
            "fp_firms_corroborated", "precision_if_corroborated_tp")},
            "note": "Event-level attribution; report times for the Hawaiʻi Island fires are from news (approx.)."}})


if __name__ == "__main__":
    main()
