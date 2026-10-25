"""Replay SmokeWatch on eval/cases.yaml and write eval/results.{md,json}.

  FIRMS_ARCHIVE_DIR=.cache/firms python eval/run_eval.py [--only lahaina_pm,olinda] [--resume]

Every case runs in its own LocalStore (eval/runs/<id>/), so rechecks from one case can
never leak into another. Each finished case is checkpointed to eval/runs/<id>/records.json;
--resume reuses those and only runs the missing cases (a killed run continues, not restarts). Events replay every 5-min scan of their window in order (the
agent sees only the past: earlier scans, background days, FIRMS passes before the scan).

Metrics
  * time to detection: first scan with a review within radius_km of the origin, vs the
    official first-report time and vs the first detection by ANY polar orbiter in FIRMS
    (S-NPP VIIRS, MODIS Aqua/Terra from the keyless archive; NOAA-20 VIIRS too when
    FIRMS_MAP_KEY is set — fetched once into the archive dir);
    also the first raw thermal anomaly (before the agent's 2-scan persistence rule);
  * per-frame precision, attributed at EVENT level: a review is TP if it lies within radius
    of ANY known fire in cases.yaml that has started (official time − 2 h slack, since some
    report times are approximate) — e.g. Olinda hotspots seen in Lahaina frames are TP;
    FP only when it matches no known fire;
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
    """First detection per polar sensor within radius after `after` (sensor -> time)."""
    pts = firms.archive_points(cfg_dir, after, after + dt.timedelta(days=2))
    out: dict[str, dt.datetime] = {}
    for p in sorted(pts, key=lambda p: p["t"]):
        if km((p["lat"], p["lon"]), origin) <= radius_km:
            out.setdefault(p["src"], p["t"])
    return out


def ensure_noaa20(archive: str, cfg) -> str:
    """With FIRMS_MAP_KEY, add VIIRS_NOAA20_SP for the event days to the archive dir."""
    if not os.environ.get("FIRMS_MAP_KEY"):
        return "NOAA-20 not included (no FIRMS_MAP_KEY; not in the keyless archive)"
    days = sorted({parse_t(e["first_report_utc"]).date() for e in cfg["events"]})
    path = os.path.join(archive, f"noaa20_sp_{days[0]:%Y%m%d}.csv")
    if not os.path.exists(path):
        n = firms.download_area("VIIRS_NOAA20_SP", (-160.6, 18.5, -154.6, 22.6), days[0],
                                (days[-1] - days[0]).days + 3, path)
        print(f"FIRMS NOAA-20: {n} rows -> {path}", flush=True)
    firms._archive.cache_clear()
    return "NOAA-20 included (FIRMS API, VIIRS_NOAA20_SP)"


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


RUNS, SUFFIX = "runs", ""   # --contextual: eval/runs_ctx/, results_ctx.*, failures_ctx/


def run_case(case, cfg, cache, policy_name) -> list[dict]:
    """Replay one case in a fresh store; return one record per scan."""
    root = os.path.join(HERE, RUNS, case["id"])
    shutil.rmtree(root, ignore_errors=True)
    store = LocalStore(root)

    @functools.lru_cache(maxsize=96)
    def source(t: dt.datetime) -> goes.Frame:
        return goes.fetch_frame(t, "C", cache)

    recs = []
    stop_origin = tuple(case["origin"]) if "origin" in case else None
    extra = 0
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
        # decisions depend only on this scan, the 2 before it and the background, so once the
        # event is detected a couple more scans add nothing to time-to-detection
        if stop_origin:
            near = [c for c in anoms if c["action"] == "request_human_review"
                    and km((c["lat"], c["lon"]), stop_origin) <= case["radius_km"]]
            extra = extra + 1 if (near or extra) else 0
            if extra > 2:
                break
    return recs


def cached_case(case, cfg, cache, policy_name, resume: bool) -> list[dict]:
    path = os.path.join(HERE, RUNS, case["id"], "records.json")
    if resume and os.path.exists(path):
        with open(path) as f:
            recs = json.load(f)
        for r in recs:
            r["time"] = parse_t(r["time"])
        print(f"  resumed {len(recs)} scans from {os.path.relpath(path, HERE)}", flush=True)
        return recs
    recs = run_case(case, cfg, cache, policy_name)
    with open(path + ".part", "w") as f:
        json.dump(recs, f, default=str)
    os.replace(path + ".part", path)
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
    ap.add_argument("--resume", action="store_true", help="reuse checkpointed cases")
    ap.add_argument("--contextual", action="store_true",
                    help="experiment: MODIS contextual test on; separate runs/results files")
    a = ap.parse_args(argv)
    global RUNS, SUFFIX
    if a.contextual:
        fire.FP["contextual"] = True
        RUNS, SUFFIX = "runs_ctx", "_ctx"
    cfg = load_cases()
    cache = cfg.get("frames_dir", ".cache/goes")
    archive = os.environ.get("FIRMS_ARCHIVE_DIR", ".cache/firms")
    os.environ["FIRMS_ARCHIVE_DIR"] = archive
    polar_note = ensure_noaa20(archive, cfg)
    only = set(filter(None, a.only.split(",")))
    fires = known_fires(cfg)
    results = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "policy": a.policy, "contextual": fire.FP["contextual"],
               "polar_sensors_note": polar_note,
               "events": [], "negatives": [], "frames": []}
    frames_seen: dict[str, dict] = {}
    gallery = os.path.join(HERE, "failures" + SUFFIX)
    shutil.rmtree(gallery, ignore_errors=True)

    for ev in cfg["events"]:
        if only and ev["id"] not in only:
            continue
        print(f"[event] {ev['id']}", flush=True)
        recs = cached_case(ev, cfg, cache, a.policy, a.resume)
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
        polar_sensor = min(ff, key=ff.get) if ff else None
        polar = ff.get(polar_sensor)
        row = {"id": ev["id"], "name": ev["name"], "first_report": report, "origin": origin,
               "radius_km": rad, "time_source": ev["time_source"], "origin_source": ev["origin_source"],
               "scans": len(recs), "first_anomaly": first_anom, "first_review": first_rev,
               "firms_first_by_sensor": ff, "first_polar": polar, "first_polar_sensor": polar_sensor,
               "review_minus_report_min": mins(first_rev, report),
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
        recs = cached_case(neg, cfg, cache, a.policy, a.resume)
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
    # Secondary breakdown (the strict rule above stays the headline): is a strict FP a real
    # fire nonetheless — spread beyond the origin radius, or a fire not in cases.yaml?
    # Corroborated = ≥1 FIRMS vegetation-fire point (type 0) within 3 km within ±24 h.
    n_corr = 0
    for r, c in fp_list:
        pts = firms.archive_points(archive, r["time"] - dt.timedelta(hours=24), r["time"] + dt.timedelta(hours=24))
        c["firms_corroborated"] = sum(p["type"] == "0" and km((p["lat"], p["lon"]), (c["lat"], c["lon"])) <= 3
                                      for p in pts)
        c["nearest_known_km"] = round(min(km((c["lat"], c["lon"]), f["origin"]) for f in fires), 1)
        n_corr += c["firms_corroborated"] > 0
    order = sorted(fp_list, key=lambda rc: rc[1]["firms_corroborated"] > 0)   # uncorroborated first
    for r, c in order[:8]:
        c["gallery"] = overlay_crop(r["case"], r, cache, (c["lat"], c["lon"]), gallery, f"fp{c['det_id']}")
    results["precision"] = {"tp_reviews": tp, "fp_reviews": fp,
                            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                            "negative_frames": neg_frames, "false_alarm_frames": fa_frames,
                            "unique_frames": len(frames_seen),
                            "fp_firms_corroborated": n_corr,
                            "precision_if_corroborated_tp": round((tp + n_corr) / (tp + fp), 3) if tp + fp else None,
                            "fp_detail": [{"case": r["case"], "time": r["time"], "lat": c["lat"], "lon": c["lon"],
                                           "max_ddt_k": c["max_ddt_k"], "rationale": c["rationale"],
                                           "firms_corroborated": c["firms_corroborated"],
                                           "nearest_known_km": c["nearest_known_km"],
                                           "gallery": c.get("gallery")} for r, c in fp_list]}
    lat = [f["latency_s"] for f in results["frames"]]
    results["latency_s"] = {k: {"median": round(sorted(x[k] for x in lat)[len(lat) // 2], 2),
                                "max": round(max(x[k] for x in lat), 2)} for k in ("frame", "background", "agent")} if lat else {}
    tool = {}
    for f in results["frames"]:
        for k, v in f["tool_ms"].items():
            tool.setdefault(k, []).append(v)
    results["tool_ms"] = {k: {"median": sorted(v)[len(v) // 2], "max": max(v), "n": len(v)} for k, v in tool.items()}

    with open(os.path.join(HERE, f"results{SUFFIX}.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    write_md(results)
    print(f"wrote eval/results{SUFFIX}.md, eval/results{SUFFIX}.json")


def _dur(m: int) -> str:
    m = abs(m)
    if m < 60:
        return f"{m} min"
    h = m / 60
    return f"~{h:.0f} h" if h >= 3 else f"{m // 60} h {m % 60:02d} min"


def say(e) -> str:
    """Plain statement of the timing. Compares with the first REPORT (not with when people
    first noticed the fire) and with the first polar-orbiter pass."""
    if e["first_review"] is None:
        return f"not detected by GOES in {e['scans']} scans."
    out = []
    d = e["review_minus_report_min"]
    out.append(f"GOES review {_dur(d)} {'after' if d >= 0 else 'before'} the official first report")
    p = e["review_minus_first_polar_min"]
    if p is None:
        out.append("no polar-orbiter detection within 2 days")
    else:
        out.append(f"{_dur(p)} {'before' if p < 0 else 'after'} the first polar-orbiter detection "
                   f"({e['first_polar_sensor']})")
    return ", ".join(out) + "."


def write_md(R):
    L = ["# SmokeWatch evaluation", "",
         f"Generated {R['generated_utc'][:16]}Z · policy `{R['policy']}` · "
         f"{R['precision']['unique_frames']} unique GOES-18 PACUS scans. Cases: `eval/cases.yaml`.", "",
         "## Time to detection", "",
         "First GOES scan where the agent queued a human review within the radius of the origin, vs the "
         "official first-report time and vs the first FIRMS detection by any polar-orbiting sensor. "
         f"Polar sensors: {R['polar_sensors_note']}; S-NPP VIIRS and MODIS Aqua/Terra from the FIRMS archive. "
         "Δ: + = GOES later, − = GOES earlier.", "",
         "| Fire | Official first report | First thermal anomaly | **First GOES review** | Δ vs report "
         "| First polar-orbiter detection (sensor) | **Δ vs first polar** |",
         "|---|---|---|---|---|---|---|"]
    for e in R["events"]:
        L.append(f"| {e['name']} | {fmt(e['first_report'])} | {fmt(e['first_anomaly'])} | "
                 f"**{fmt(e['first_review'])}** | {hm(e['review_minus_report_min'])} | "
                 f"{fmt(e['first_polar'])} ({e['first_polar_sensor'] or '—'}) | "
                 f"**{hm(e['review_minus_first_polar_min'])}** |")
    L += ["", "In words (GOES review vs official report vs first polar-orbiter pass):", ""]
    for e in R["events"]:
        L.append(f"- {e['name']}: {say(e)}")
    L += ["", "Per-sensor first detection within the radius:", ""]
    for e in R["events"]:
        per = ", ".join(f"{k} {fmt(v)}" for k, v in sorted(e["firms_first_by_sensor"].items(), key=lambda kv: kv[1]))
        L.append(f"- {e['id']}: {per or 'none within 2 days'}")
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
          f"- Negative-day frames with any false review: **{P['false_alarm_frames']} / {P['negative_frames']}**.",
          f"- Of the {P['fp_reviews']} strict FPs, **{P['fp_firms_corroborated']} are corroborated by FIRMS** "
          "(≥1 vegetation-fire detection within 3 km, ±24 h) — fire spreading beyond the origin radius "
          f"or a fire not listed; counting those as real fires would give precision "
          f"{P['precision_if_corroborated_tp']}. The remaining {P['fp_reviews'] - P['fp_firms_corroborated']} "
          "have no corroboration and are listed first under Failure cases.", "",
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
    for f in sorted(P["fp_detail"], key=lambda f: f["firms_corroborated"] > 0):
        tag = (f"FIRMS-corroborated ({f['firms_corroborated']} pts), {f['nearest_known_km']} km from a known origin"
               if f["firms_corroborated"] else "no FIRMS corroboration")
        L.append(f"- **FP — {tag}** — {f['case']} {f['time']:%m-%d %H:%M}Z at {f['lat']:.3f}, {f['lon']:.3f} "
                 f"(+{f['max_ddt_k']} K): {f['rationale']}" + (f" ![]({f['gallery']})" if f.get("gallery") else ""))
    with open(os.path.join(HERE, f"results{SUFFIX}.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
