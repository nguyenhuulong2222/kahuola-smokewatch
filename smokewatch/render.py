"""Presentation layers for the public page and the scan archive (no detection logic here).

* base:    true color by day; by night a dark land/sea canvas (raw 3.9 µm is unreadable);
* anomaly: what the detector looks at — Δ(3.9−11.2 µm) above the 3-day background, inferno
           colormap, transparent below ANOM_MIN_K, land only;
* coast:   1-px coastline, transparent elsewhere.

Every layer has the same grid geometry (`geo()`), so the browser can stack, toggle and crop
them. `composite()` bakes them for the case-study animation (MP4/GIF via OpenCV).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import cv2
import numpy as np

from . import fire, vision
from .goes import Frame

SCALE = 3                 # archive images: 3 px per 2 km grid cell
ANOM_MIN_K = 3.0          # below this the layer is transparent
ANOM_MAX_K = 20.0         # colormap top
CMAP_LO = 90              # skip inferno's near-black end: it vanishes on dark ground
DETECT_K = fire.FP["d_dt_min"]
HST = dt.timezone(dt.timedelta(hours=-10), "HST")
NIGHT_LAND, NIGHT_SEA = (54, 46, 38), (24, 17, 13)          # BGR
MAUI = (20.45, 21.12, -156.80, -155.95)                     # lat0, lat1, lon0, lon1


def geo(frame: Frame) -> dict:
    """Cell-edge bounds of the grid, for lat/lon -> pixel in the browser."""
    dlat, dlon = frame.lats[1] - frame.lats[0], frame.lons[1] - frame.lons[0]
    return {"rows": int(frame.shape[0]), "cols": int(frame.shape[1]),
            "lat_top": float(frame.lats[0] - dlat / 2), "lat_bottom": float(frame.lats[-1] + dlat / 2),
            "lon_left": float(frame.lons[0] - dlon / 2), "lon_right": float(frame.lons[-1] + dlon / 2)}


def _up(img, scale, interp=cv2.INTER_CUBIC):
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


def base_image(frame: Frame, scale=SCALE) -> np.ndarray:
    if vision.is_daytime(frame):
        return _up(vision.true_color(frame), scale)
    img = np.empty(frame.shape + (3,), np.uint8)
    img[:] = NIGHT_SEA
    img[_land(frame)] = NIGHT_LAND
    return _up(img, scale, cv2.INTER_NEAREST)


def anomaly_field(frame: Frame, bg: fire.Background | None) -> np.ndarray | None:
    if bg is None:
        return None
    ddt = fire.thermal_delta(frame, bg)["ddt"].astype(np.float32)
    ddt[~fire.land_mask(frame)] = np.nan
    return ddt


def anomaly_rgba(ddt: np.ndarray | None, shape, scale=SCALE) -> np.ndarray:
    """BGRA; alpha ramps in from ANOM_MIN_K so faint warming stays faint."""
    out = np.zeros(tuple(shape) + (4,), np.uint8)
    if ddt is not None:
        v = np.nan_to_num(ddt, nan=-99.0)
        norm = np.clip((v - ANOM_MIN_K) / (ANOM_MAX_K - ANOM_MIN_K), 0, 1)
        col = cv2.applyColorMap((CMAP_LO + norm * (255 - CMAP_LO)).astype(np.uint8), cv2.COLORMAP_INFERNO)
        on = v > ANOM_MIN_K
        out[..., :3][on] = col[on]
        # faint below the detector's threshold, solid above it
        out[..., 3][on] = np.where(v[on] >= DETECT_K, 235, 80).astype(np.uint8)
    return _up(out, scale, cv2.INTER_NEAREST)   # keep the 2 km cells honest


def _land(frame: Frame) -> np.ndarray:
    m = cv2.imread(fire._LAND_PNG, cv2.IMREAD_GRAYSCALE)
    return (m > 0) if m is not None and m.shape == frame.shape else fire.land_mask(frame)


COAST_SCALE = 6   # finer than the imagery so the line stays thin when zoomed to one island


def coast_rgba(frame: Frame, scale=COAST_SCALE) -> np.ndarray:
    m = _up(_land(frame).astype(np.uint8) * 255, scale, cv2.INTER_NEAREST)
    edge = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((2, 2), np.uint8)) > 0
    out = np.zeros(m.shape + (4,), np.uint8)
    out[edge] = (235, 225, 200, 190)
    return out


def _blend(img, rgba):
    a = rgba[..., 3:4].astype(np.float32) / 255
    return (img * (1 - a) + rgba[..., :3] * a).astype(np.uint8)


def hst_label(t: dt.datetime) -> str:
    return f"{t.astimezone(HST):%a %d %b %H:%M} HST  ({t:%H:%M}Z)"


def colorbar(width=220, height=12) -> np.ndarray:
    ramp = np.linspace(0, 1, width)
    col = cv2.applyColorMap((CMAP_LO + ramp * (255 - CMAP_LO)).astype(np.uint8)[None, :].repeat(height, 0),
                            cv2.COLORMAP_INFERNO)
    return col


def composite(frame: Frame, ddt: np.ndarray | None, markers: list[dict], crop=MAUI, scale=12,
              title: str = "") -> np.ndarray:
    """Baked frame for video: base + ΔT + coast + markers, cropped, labelled in HST."""
    g = geo(frame)
    r0 = int((g["lat_top"] - crop[1]) / (g["lat_top"] - g["lat_bottom"]) * g["rows"])
    r1 = int((g["lat_top"] - crop[0]) / (g["lat_top"] - g["lat_bottom"]) * g["rows"])
    c0 = int((crop[2] - g["lon_left"]) / (g["lon_right"] - g["lon_left"]) * g["cols"])
    c1 = int((crop[3] - g["lon_left"]) / (g["lon_right"] - g["lon_left"]) * g["cols"])
    sl = (slice(r0, r1), slice(c0, c1))
    img = base_image(frame, 1)[sl]
    img = _up(img, scale, cv2.INTER_CUBIC if vision.is_daytime(frame) else cv2.INTER_NEAREST)
    img = _blend(img, anomaly_rgba(None if ddt is None else ddt[sl], (r1 - r0, c1 - c0), scale))
    img = _blend(img, coast_rgba_crop(frame, sl, scale))
    for m in markers:
        col, row = fire._px(frame, m["lat"], m["lon"])
        x, y = int((col - c0 + 0.5) * scale), int((row - r0 + 0.5) * scale)
        color = (40, 40, 255) if m.get("review") else (0, 215, 255)
        cv2.circle(img, (x, y), int(2.2 * scale), color, 3, cv2.LINE_AA)
        if m.get("text"):
            _text(img, m["text"], (x + int(2.6 * scale), y + 5), 0.55, color)
    _text(img, hst_label(frame.time), (12, 28), 0.75)
    if title:
        _text(img, title, (12, 54), 0.55, (200, 230, 255))
    bar = colorbar()
    h, w = img.shape[:2]
    y0, x0 = h - 34, w - bar.shape[1] - 14
    img[y0:y0 + bar.shape[0], x0:x0 + bar.shape[1]] = bar
    _text(img, f"{ANOM_MIN_K:.0f}", (x0, y0 - 6), 0.42)
    _text(img, f"{ANOM_MAX_K:.0f}+ K above 3-day background", (x0 + 30, y0 - 6), 0.42)
    _text(img, "GOES-18 ABI, 2 km  |  Kahu Ola SmokeWatch", (12, h - 12), 0.45, (210, 210, 210))
    return img


def coast_rgba_crop(frame, sl, scale):
    m = _land(frame).astype(np.uint8)[sl] * 255
    m = _up(m, scale, cv2.INTER_NEAREST)
    edge = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    out = np.zeros(m.shape + (4,), np.uint8)
    out[edge] = (235, 225, 200, 170)
    return out


def _text(img, s, org, size, color=(255, 255, 255)):
    """Text on a translucent dark box (legible on cloud, sea and fire)."""
    (w, h), base = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, size, 1)
    x, y = org
    x0, y0, x1, y1 = max(x - 4, 0), max(y - h - 5, 0), min(x + w + 4, img.shape[1]), min(y + base + 3, img.shape[0])
    img[y0:y1, x0:x1] = (img[y0:y1, x0:x1] * 0.35).astype(np.uint8)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, size, color, 1, cv2.LINE_AA)


# ------------------------------------------------------------------ archive

def day_of(t: dt.datetime) -> str:
    """Archive days are Hawaiʻi calendar days (HST), so one fire night is one day."""
    return t.astimezone(HST).strftime("%Y-%m-%d")


def scan_prefix(root: str, t: dt.datetime) -> str:
    return f"{root}/{day_of(t).replace('-', '/')}/{t:%H%M}Z/"


def scan_record(summary: dict, ev_markers: list[dict], firms_pts: list[dict] | None = None) -> dict:
    """The per-scan JSON the viewer reads (no trace body; trace_id links to /api/trace)."""
    t = dt.datetime.fromisoformat(summary["frame_time"])
    return {"time": t.isoformat(), "t": f"{t:%H%M}Z", "hst": f"{t.astimezone(HST):%H:%M}",
            "daytime": summary.get("daytime"), "background_scans": summary.get("background_scans"),
            "policy": summary.get("policy"), "trace_id": summary.get("episode"),
            "candidates": ev_markers, "firms": firms_pts or []}


def candidates_from_summary(summary: dict) -> list[dict]:
    out = []
    for k, ev in (summary.get("evidence") or {}).items():
        h = ev.get("hotspot") or {}
        o = (summary.get("outcomes") or {}).get(k) or (summary.get("outcomes") or {}).get(str(k)) or {}
        out.append({"id": h.get("id", k), "lon": h["centroid"][0], "lat": h["centroid"][1],
                    "max_ddt_k": h.get("max_ddt_k"), "hot_px": h.get("hot_px"),
                    "volcanic": h.get("volcanic"), "action": o.get("action"),
                    "confidence": o.get("confidence"), "rationale": o.get("rationale"),
                    "review": o.get("action") == "request_human_review"})
    return out


def write_scan(store, prefix: str, frame: Frame, ddt, record: dict):
    ok, webp = cv2.imencode(".webp", base_image(frame), [cv2.IMWRITE_WEBP_QUALITY, 80])
    store.put_bytes(prefix + "overlay.webp", webp.tobytes(), "image/webp")
    ok, png = cv2.imencode(".png", anomaly_rgba(ddt, frame.shape), [cv2.IMWRITE_PNG_COMPRESSION, 9])
    store.put_bytes(prefix + "anomaly.png", png.tobytes(), "image/png")
    store.put_json(prefix + "summary.json", record)


def write_static(store, frame: Frame, force=False):
    """Coastline layer: identical for every scan on this grid, written once."""
    if force or not store.exists("static/coast.png"):
        ok, png = cv2.imencode(".png", coast_rgba(frame), [cv2.IMWRITE_PNG_COMPRESSION, 9])
        store.put_bytes("static/coast.png", png.tobytes(), "image/png")


def update_index(store, root: str, record: dict, prefix: str, geo_info: dict | None = None):
    """Read-modify-write the day index; written to a temp key, then copied into place."""
    day = day_of(dt.datetime.fromisoformat(record["time"]))
    key = f"{root}/index/{day}.json"
    idx = store.get_json(key) or {"date": day, "tz": "HST", "scans": []}
    if geo_info:
        idx["geo"] = geo_info
    entry = {"t": record["t"], "time": record["time"], "hst": record["hst"], "p": prefix,
             "day": record["daytime"], "cands": len(record["candidates"]),
             "reviews": sum(1 for c in record["candidates"] if c.get("review"))}
    idx["scans"] = sorted([s for s in idx["scans"] if s["t"] != entry["t"] or s["p"] != prefix]
                          + [entry], key=lambda s: s["time"])
    tmp = f"{root}/index/tmp/{uuid.uuid4().hex}.json"
    store.put_json(tmp, idx)
    store.copy(tmp, key)
    store.delete(tmp)
    return idx


def archive_scan(store, frame: Frame, bg, summary: dict, root="archive") -> dict:
    """Live: archive one scan; scans with a review are also copied to events/ (kept)."""
    ddt = anomaly_field(frame, bg)
    write_static(store, frame)
    rec = scan_record(summary, candidates_from_summary(summary))
    roots = [root] + (["events"] if any(c["review"] for c in rec["candidates"]) else [])
    for r in roots:
        prefix = scan_prefix(r, frame.time)
        write_scan(store, prefix, frame, ddt, rec)
        update_index(store, r, rec, prefix, geo(frame))
    return rec


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
