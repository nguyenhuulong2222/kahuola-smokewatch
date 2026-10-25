"""Replay SmokeWatch on eval/cases.yaml and write eval/results.{md,json}.

  FIRMS_ARCHIVE_DIR=.cache/firms python eval/run_eval.py [--only lahaina_pm,olinda]

Every case runs in its own LocalStore (eval/runs/<id>/), so rechecks from one case can
never leak into another. Events replay every 5-min scan of their window in order (the
agent sees only the past: earlier scans, background days, FIRMS passes before the scan).

Metrics
  * time to detection: first scan with a review within radius_km of the origin, vs the
    official first-report time and vs the first S-NPP VIIRS / MODIS FIRMS detection;
    also the first raw thermal anomaly (before the agent's 2-scan persistence rule);
  * per-frame precision: a review is TP if within radius of a known fire that has started
    (official time − 2 h slack, since some report times are approximate), else FP;
    any review on a negative-day frame is FP. Frames shared by two cases count once.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import math
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402

from eval.prefetch import load_cases, parse_t, target_scans  # noqa: E402
from smokewatch import agent, fire, firms, goes, vision  # noqa: E402
from smokewatch.store import LocalStore  # noqa: E402

HERE = os.path.dirname(__file__)
HST = dt.timezone(dt.timedelta(hours=-10))


def km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    return math.hypot((lo1 - lo2) * 111.32 * math.cos(math.radians((la1 + la2) / 2)), (la1 - la2) * 111.32)


def fmt(t: dt.datetime | None) -> str:
    return "—" if t is None else f"{t:%m-%d %H:%M}Z ({t.astimezone(HST):%H:%M} HST)"


def mins(a: dt.datetime | None, b: dt.datetime | None):
    return None if a is None or b is None else round((a - b).total_seconds() / 60)


def hm(m) -> str:
    if m is None:
        return "—"
    s = "−" if m < 0 else "+"
    m = abs(m)
    return f"{s}{m // 60}h{m % 60:02d}m" if m >= 60 else f"{s}{m}m"


def firms_first(cfg_dir: str, origin, radius_km: float, after: dt.datetime) -> dict:
    pts = firms.archive_points(cfg_dir, after, after + dt.timedelta(days=2))
    out = {}
    for src in ("VIIRS_SNPP", "MODIS"):
        hits = sorted(p["t"] for p in pts if p["src"] == src and km((p["lat"], p["lon"]), origin) <= radius_km)
        out[src] = hits[0] if hits else None
    return out


def known_fires(cfg) -> list[dict]:
    fires = [{"name": e["name"], "origin": e["origin"], "radius_km": e["radius_km"],
              "from": parse_t(e["first_report_utc"]) - dt.timedelta(hours=2)} for e in cfg["events"]]
    fires += [{"name": f["name"], "origin": f["origin"], "radius_km": f["radius_km"],
               "from": parse_t(f["from_utc"]) - dt.timedelta(hours=2)} for f in cfg.get("other_known_fires", [])]
    return fires


def attribute(review_ll, t, fires) -> str | None:
    for f in fires:
        if t >= f["from"] and km(review_ll, f["origin"]) <= f["radius_km"]:
            return f["name"]
    return None


def run_case(case, cfg, cache, policy_name) -> list[dict]:
    """Replay one case in a fresh store; return one record per scan."""
    root = os.path.join(HERE, "runs", case["id"])
    shutil.rmtree(root, ignore_errors=True)
    store = LocalStore(root)

    @functools.lru_cache(maxsize=96)
    def source(t: dt.datetime) -> goes.Frame:
        return goes.fetch_frame(t, "C", cache)

    recs = []
    for key in target_scans(case, cfg.get("step_min", 5)):
        t0 = time.time()
        frame = goes.load_key(key, cache)
        t_fetch = time.time() - t0
        t1 = time.time()
        bg = fire.fetch_background(frame.time, source)
        t_bg = time.time() - t1
        t2 = time.time()
        s = agent.run_scan(frame, source, store, agent.make_policy(policy_name), background=bg)
        t_scan = time.time() - t2
        anoms = []
        for did, ev in s["evidence"].items():
            h = ev["hotspot"]
            out = s["outcomes"].get(did, {})
            anoms.append({"det_id": did, "lat": h["centroid"][1], "lon": h["centroid"][0],
                          "hot_px": h["hot_px"], "max_ddt_k": h["max_ddt_k"], "volcanic": h["volcanic"],
                          "action": out.get("action"), "confidence": out.get("confidence"),
                          "rationale": out.get("rationale"),
                          "hotspot_scans": ev.get("track", {}).get("hotspot_scans"),
                          "smoke": bool((ev.get("track", {}).get("smoke") or {}).get("attached_plume")),
                          "firms_count": (ev.get("firms") or {}).get("count")})
        tools = {}
        for st in s["trace"]:
            if "tool" in st:
                tools.setdefault(st["tool"], []).append(st["ms"])
        recs.append({"case": case["id"], "key": key, "time": frame.time, "daytime": s["daytime"],
                     "episode": s["episode"], "background_scans": s["background_scans"],
                     "errors": s["errors"], "candidates": anoms,
                     "latency_s": {"frame": round(t_fetch, 2), "background": round(t_bg, 2),
                                   "agent": round(t_scan, 2)},
                     "tool_ms": {k: max(v) for k, v in tools.items()}})
        n_rev = sum(a["action"] == "request_human_review" for a in anoms)
        print(f"  {case['id']} {frame.time:%m-%d %H:%M}Z cand {len(anoms)} review {n_rev} "
              f"({t_bg + t_scan:.1f}s)", flush=True)
        if case.get("stop_after_review") and n_rev:
            break
    return recs


def overlay_crop(case_id, rec, cache, center, out_dir, tag) -> str:
    """Zoomed overlay around `center` (lat, lon) for the failure-case gallery."""
    # re-render: latest/ holds only the case's last scan
    fr = goes.load_key(rec["key"], cache)
    bg = fire.fetch_background(fr.time, lambda t: goes.fetch_frame(t, "C", cache))
    dets, anom, smoke = fire.detect_fires(fr, bg)
    img = vision.render_overlay(fr, smoke, dets, anom, scale=6)
    col, row = fire._px(fr, *center)
    r0, c0 = max(int(row * 6) - 240, 0), max(int(col * 6) - 320, 0)
    crop = img[r0:r0 + 480, c0:c0 + 640]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{case_id}_{fr.time:%m%dT%H%M}_{tag}.png")
    cv2.imwrite(path, crop)
    return os.path.relpath(path, HERE)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--policy", default="rules")
    a = ap.parse_args(argv)
    cfg = load_cases()
    cache = cfg.get("frames_dir", ".cache/goes")
    archive = os.environ.get("FIRMS_ARCHIVE_DIR", ".cache/firms")
    os.environ["FIRMS_ARCHIVE_DIR"] = archive
    only = set(filter(None, a.only.split(",")))
    fires = known_fires(cfg)
    results = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "policy": a.policy,
               "events": [], "negatives": [], "frames": []}
    frames_seen: dict[str, dict] = {}
    gallery = os.path.join(HERE, "failures")
    shutil.rmtree(gallery, ignore_errors=True)

    for ev in cfg["events"]:
        if only and ev["id"] not in only:
            continue
        print(f"[event] {ev['id']}", flush=True)
        recs = run_case(ev, cfg, cache, a.policy)
        origin, rad = tuple(ev["origin"]), ev["radius_km"]
        report = parse_t(ev["first_report_utc"])
        first_anom = first_rev = None
        for r in recs:
            near = [c for c in r["candidates"] if km((c["lat"], c["lon"]), origin) <= rad]
            if near and first_anom is None:
                first_anom = r["time"]
            if first_rev is None and any(c["action"] == "request_human_review" for c in near):
                first_rev = r["time"]
                first_rev_rec = r
        ff = firms_first(archive, origin, rad, report - dt.timedelta(hours=3))
        viirs = ff["VIIRS_SNPP"]
        polar = min([x for x in ff.values() if x], default=None)
        row = {"id": ev["id"], "name": ev["name"], "first_report": report, "origin": origin,
               "radius_km": rad, "time_source": ev["time_source"], "origin_source": ev["origin_source"],
               "scans": len(recs), "first_anomaly": first_anom, "first_review": first_rev,
               "firms_first_viirs_snpp": viirs, "firms_first_modis": ff["MODIS"],
               "review_minus_report_min": mins(first_rev, report),
               "review_minus_viirs_min": mins(first_rev, viirs),
               "review_minus_first_polar_min": mins(first_rev, polar), "gallery": []}
        if first_rev is None:
            mid = recs[len(recs) // 2] if recs else None
            if mid:
                row["gallery"].append(overlay_crop(ev["id"], mid, cache, origin, gallery, "missed"))
        else:
            row["review_rationale"] = next(c["rationale"] for c in first_rev_rec["candidates"]
                                           if c["action"] == "request_human_review"
                                           and km((c["lat"], c["lon"]), origin) <= rad)
        results["events"].append(row)
        for r in recs:
            frames_seen.setdefault(r["key"], {**r, "negative": False})

    for neg in cfg["negatives"]:
        if only and neg["id"] not in only:
            continue
        print(f"[negative] {neg['id']}", flush=True)
        recs = run_case(neg, cfg, cache, a.policy)
        t0 = min(r["time"] for r in recs) - dt.timedelta(hours=12)
        t1 = max(r["time"] for r in recs)
        veg = [p for p in firms.archive_points(archive, t0, t1) if p["type"] in ("0", "2", "3")]
        results["negatives"].append({"id": neg["id"], "kind": neg["kind"], "label_source": neg["label_source"],
                                     "frames": len(recs),
                                     "firms_nonvolcanic_points": len(veg)})
        for r in recs:
            frames_seen.setdefault(r["key"], {**r, "negative": True, "kind": neg["kind"]})

    # ---- per-frame precision over unique frames
    tp = fp = 0
    fa_frames = 0
    neg_frames = 0
    fp_list = []
    for key, r in frames_seen.items():
        frame_fp = 0
        for c in r["candidates"]:
            if c["action"] != "request_human_review":
                continue
            who = None if r["negative"] else attribute((c["lat"], c["lon"]), r["time"], fires)
            if who:
                tp += 1
            else:
                fp += 1
                frame_fp += 1
                fp_list.append((r, c))
        if r["negative"]:
            neg_frames += 1
            fa_frames += frame_fp > 0
        results["frames"].append({k: v for k, v in r.items() if k != "candidates"} |
                                 {"reviews": sum(c["action"] == "request_human_review" for c in r["candidates"]),
                                  "fp_reviews": frame_fp,
                                  "volcanic_dismissed": sum(bool(c["volcanic"]) for c in r["candidates"])})
    for r, c in fp_list[:8]:
        c["gallery"] = overlay_crop(r["case"], r, cache, (c["lat"], c["lon"]), gallery, f"fp{c['det_id']}")
    results["precision"] = {"tp_reviews": tp, "fp_reviews": fp,
                            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                            "negative_frames": neg_frames, "false_alarm_frames": fa_frames,
                            "unique_frames": len(frames_seen),
                            "fp_detail": [{"case": r["case"], "time": r["time"], "lat": c["lat"], "lon": c["lon"],
                                           "max_ddt_k": c["max_ddt_k"], "rationale": c["rationale"],
                                           "gallery": c.get("gallery")} for r, c in fp_list]}
    lat = [f["latency_s"] for f in results["frames"]]
    results["latency_s"] = {k: {"median": round(sorted(x[k] for x in lat)[len(lat) // 2], 2),
                                "max": round(max(x[k] for x in lat), 2)} for k in ("frame", "background", "agent")} if lat else {}
    tool = {}
    for f in results["frames"]:
        for k, v in f["tool_ms"].items():
            tool.setdefault(k, []).append(v)
    results["tool_ms"] = {k: {"median": sorted(v)[len(v) // 2], "max": max(v), "n": len(v)} for k, v in tool.items()}

    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    write_md(results)
    print("wrote eval/results.md, eval/results.json")


def write_md(R):
    L = ["# SmokeWatch evaluation", "",
         f"Generated {R['generated_utc'][:16]}Z · policy `{R['policy']}` · "
         f"{R['precision']['unique_frames']} unique GOES-18 PACUS scans. Cases: `eval/cases.yaml`.", "",
         "## Time to detection", "",
         "First GOES scan where the agent queued a human review within the radius of the origin, vs the "
         "official first report and vs the first polar-orbiter (FIRMS) detection. Negative = earlier.", "",
         "| Fire | Official first report | First thermal anomaly | **First GOES review** | Δ vs report "
         "| First VIIRS (S-NPP) | First MODIS | **Δ vs first FIRMS** |",
         "|---|---|---|---|---|---|---|---|"]
    for e in R["events"]:
        L.append(f"| {e['name']} | {fmt(e['first_report'])} | {fmt(e['first_anomaly'])} | "
                 f"**{fmt(e['first_review'])}** | {hm(e['review_minus_report_min'])} | "
                 f"{fmt(e['firms_first_viirs_snpp'])} | {fmt(e['firms_first_modis'])} | "
                 f"**{hm(e['review_minus_first_polar_min'])}** |")
    L += ["", "Sources per fire (time / origin):", ""]
    for e in R["events"]:
        L.append(f"- **{e['name']}** — time: {e['time_source']}; origin {e['origin'][0]}, {e['origin'][1]} "
                 f"({e['origin_source']}); match radius {e['radius_km']} km; {e['scans']} scans replayed.")
    L += ["", "First-review rationale (generated from evidence):", ""]
    for e in R["events"]:
        if e.get("review_rationale"):
            L.append(f"- *{e['id']}*: {e['review_rationale']}")
    P = R["precision"]
    L += ["", "## Precision", "",
          f"- Reviews: **{P['tp_reviews']} TP / {P['fp_reviews']} FP → precision {P['precision']}** "
          "(per review, unique frames, positive + negative days).",
          f"- Negative-day frames with any false review: **{P['false_alarm_frames']} / {P['negative_frames']}**.", "",
          "| Negative case | Kind | Frames | Reviews (all FP) | Volcanic dismissed | FIRMS non-volcanic pts that day | Label source |",
          "|---|---|---|---|---|---|---|"]
    for n in R["negatives"]:
        fr = [f for f in R["frames"] if f["case"] == n["id"]]
        L.append(f"| {n['id']} | {n['kind']} | {n['frames']} | {sum(f['reviews'] for f in fr)} | "
                 f"{sum(f['volcanic_dismissed'] for f in fr)} | {n['firms_nonvolcanic_points']} | {n['label_source']} |")
    L += ["", "## Latency (local replay, cached frames)", "", "| Step | median s | max s |", "|---|---|---|"]
    for k, v in R["latency_s"].items():
        L.append(f"| {k} | {v['median']} | {v['max']} |")
    for k, v in R["tool_ms"].items():
        L.append(f"| tool `{k}` | {v['median'] / 1000:.2f} | {v['max'] / 1000:.2f} |")
    L += ["", "## Failure cases", ""]
    for e in R["events"]:
        if e["first_review"] is None:
            L.append(f"- **Missed: {e['name']}** — no review within {e['radius_km']} km in {e['scans']} scans. "
                     + " ".join(f"![]({g})" for g in e["gallery"]))
    for f in P["fp_detail"]:
        L.append(f"- **FP** {f['case']} {f['time']:%m-%d %H:%M}Z at {f['lat']:.3f}, {f['lon']:.3f} "
                 f"(+{f['max_ddt_k']} K): {f['rationale']}" + (f" ![]({f['gallery']})" if f.get("gallery") else ""))
    with open(os.path.join(HERE, "results.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
