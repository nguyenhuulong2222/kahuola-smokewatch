"""Fire-anchored detection: thermal anomaly vs a multi-day background, then smoke as
supporting evidence.

Why: at 2 km, smoke over Hawaiʻi is ~0.01 reflectance — about the noise level — while a
burning fire raises 3.9 µm (C07) by many kelvin in a single pixel, day or night. Absolute
C07 thresholds fire on sun-heated dry ground (west Molokaʻi, Kona), so every pixel is
compared with ITSELF at the same time of day on the previous days.

Background (per pixel): median of the scans within ±5 min of the same clock time on the
2–3 previous days. Samples where the reference was cloudy (C13 cold, absolute or relative
to the warmest sample) are dropped before the median, so a cloud in yesterday's frame
cannot make today's clear ground look anomalously hot (the Kauaʻi false positive).

Volcanic exclusion: Kīlauea and Mauna Loa produce real thermal anomalies. Candidates
inside the fixed zones below are labelled "volcanic"; the agent dismisses them and the
review tool refuses them.
"""
from __future__ import annotations

import datetime as dt
import os
import warnings
from dataclasses import asdict, dataclass, field
from typing import Callable

import cv2
import numpy as np

from . import vision
from .goes import Frame

FP = {
    "bg_days": (1, 2, 3),
    "bg_window_min": 5,
    "bg_min_refs": 3,          # fewer reference scans: background too noisy, no detection
    "cloud_bt_abs": 273.0,     # C13 below this: cloud
    "cloud_bt_rel": 4.0,       # C13 this far below the warmest sample: cloud
    "d_dt_min": 6.0,           # Δ(C07−C14) vs background, K
    "d_t7_min": 6.0,           # ΔC07, K
    "d_t14_min": -3.0,         # ΔC14 > this: current pixel not cloud-covered
    "cluster_px": 3,           # anomaly pixels this close form one candidate
    "persist_px": 2,           # hotspot "same place" tolerance between scans
    # contextual test (experiment, off by default): MODIS parameters, see contextual_test()
    "contextual": False,
    # smoke as change vs background (reflectance), daytime only
    "smoke_dvis": 0.008,
    "smoke_swir_frac": 0.45,   # ΔC05/ΔVIS: clouds ≈ 1.1, smoke ≈ 0.3
    "smoke_dbt": -4.0,
    "smoke_tex": 0.02,
    "smoke_thresh": 0.4,
    "smoke_min_px": 6,
    "smoke_touch_px": 4,       # plume must start within ~8 km of the hotspot
}

# Volcanic exclusion zones: (name, kind, coords (lat, lon), buffer km).
# Summit/rift geometry approximated from USGS HVO maps; buffers cover 2 km pixel
# smear + recent flow fields. Coordinates are deliberately generous.
VOLCANIC_ZONES = [
    ("Kīlauea summit caldera", "point", [(19.406, -155.283)], 7.0),
    ("Kīlauea East Rift Zone", "line", [(19.406, -155.283), (19.380, -155.200), (19.388, -155.105),
                                        (19.430, -155.000), (19.470, -154.900), (19.505, -154.815)], 5.0),
    ("Kīlauea Southwest Rift Zone", "line", [(19.406, -155.283), (19.330, -155.360),
                                             (19.250, -155.450)], 4.0),
    ("Mauna Loa summit (Mokuʻāweoweo)", "point", [(19.475, -155.608)], 7.0),
    ("Mauna Loa Northeast Rift Zone + 2022 flows", "line", [(19.475, -155.608), (19.530, -155.550),
                                                            (19.600, -155.505), (19.660, -155.460)], 5.0),
    ("Mauna Loa Southwest Rift Zone", "line", [(19.475, -155.608), (19.350, -155.680),
                                               (19.200, -155.730), (19.080, -155.760)], 4.0),
]

_LAND_PNG = os.path.join(os.path.dirname(__file__), "data", "land.png")


def _px(frame: Frame, lat: float, lon: float) -> tuple[float, float]:
    col = (lon - frame.lons[0]) / (frame.lons[1] - frame.lons[0])
    row = (lat - frame.lats[0]) / (frame.lats[1] - frame.lats[0])
    return col, row


def _km_per_px(frame: Frame) -> float:
    return abs(frame.lats[1] - frame.lats[0]) * 111.32


def land_mask(frame: Frame) -> np.ndarray:
    m = cv2.imread(_LAND_PNG, cv2.IMREAD_GRAYSCALE)
    if m is None or m.shape != frame.shape:
        return np.ones(frame.shape, bool)   # unknown grid: do not suppress anything
    # coastal towns (Lahaina) sit on the edge of a 2 km land cell
    return cv2.dilate(m, np.ones((3, 3), np.uint8)) > 0


def land_outline(frame: Frame) -> np.ndarray:
    """1-px coastline (bool) from the static land mask; all False on an unknown grid."""
    m = cv2.imread(_LAND_PNG, cv2.IMREAD_GRAYSCALE)
    if m is None or m.shape != frame.shape:
        return np.zeros(frame.shape, bool)
    return cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((2, 2), np.uint8)) > 0


def volcanic_zones(frame: Frame) -> np.ndarray:
    """int8 map: zone index + 1 inside a volcanic exclusion zone, else 0."""
    out = np.zeros(frame.shape, np.int8)
    kpp = _km_per_px(frame)
    for i, (_, kind, pts, buf_km) in enumerate(VOLCANIC_ZONES):
        m = np.zeros(frame.shape, np.uint8)
        px = np.array([_px(frame, la, lo) for la, lo in pts])
        r = max(int(round(buf_km / kpp)), 1)
        if kind == "point":
            cv2.circle(m, tuple(np.round(px[0]).astype(int)), r, 1, -1)
        else:
            cv2.polylines(m, [np.round(px).astype(np.int32)], False, 1, thickness=2 * r + 1)
            for p in px:
                cv2.circle(m, tuple(np.round(p).astype(int)), r, 1, -1)
        out[(m > 0) & (out == 0)] = i + 1
    return out


# ------------------------------------------------------------------ background

@dataclass
class Background:
    bands: dict[str, np.ndarray]        # per-pixel median of clear samples
    n_clear: np.ndarray                 # clear samples per pixel
    keys: list[str]

    @property
    def n_refs(self) -> int:
        return len(self.keys)


def reference_times(t: dt.datetime, p=FP) -> list[dt.datetime]:
    """Most useful first: same clock time on each previous day, then the ±5 min scans.
    A partial bootstrap (live mode under a time budget) therefore gets 3 days first."""
    w = p["bg_window_min"]
    offsets = [0] + [m for k in range(5, w + 1, 5) for m in (-k, k)]
    return [t - dt.timedelta(days=d) + dt.timedelta(minutes=m) for m in offsets for d in p["bg_days"]]


def build_background(refs: list[Frame], p=FP) -> Background:
    if not refs:
        raise ValueError("no reference frames")
    t13 = np.stack([np.nan_to_num(r.bands["C13"], nan=0.0) for r in refs])
    warm = t13.max(0)
    cloudy = (t13 < p["cloud_bt_abs"]) | (t13 < warm - p["cloud_bt_rel"])
    bands = {}
    for b in ("C01", "C02", "C05", "C07", "C13", "C14"):
        s = np.stack([r.bands[b] for r in refs]).astype(np.float32)
        s[cloudy] = np.nan
        with warnings.catch_warnings():   # all-cloudy pixel -> NaN, no anomaly possible
            warnings.simplefilter("ignore", RuntimeWarning)
            bands[b] = np.nanmedian(s, 0)
    return Background(bands=bands, n_clear=(~cloudy).sum(0), keys=[r.key for r in refs])


def fetch_background(t: dt.datetime, source: Callable[[dt.datetime], Frame], p=FP) -> Background:
    refs, seen = [], set()
    for rt in reference_times(t, p):
        try:
            fr = source(rt)
        except Exception:   # missing scan: median of what we have
            continue
        if fr.key not in seen and abs((fr.time - rt).total_seconds()) < 240:
            seen.add(fr.key)
            refs.append(fr)
    if not refs:
        raise ValueError(f"no reference scans available for {t:%Y-%m-%d %H:%M}Z")
    return build_background(refs, p)


# ------------------------------------------------------------------ thermal anomaly

def thermal_delta(frame: Frame, bg: Background) -> dict[str, np.ndarray]:
    t7, t14 = frame.bands["C07"], frame.bands["C14"]
    d7 = t7 - bg.bands["C07"]
    d14 = t14 - bg.bands["C14"]
    return {"d7": d7, "d14": d14, "ddt": d7 - d14}


def thermal_anomaly(frame: Frame, bg: Background, p=FP) -> np.ndarray:
    d = thermal_delta(frame, bg)
    land = land_mask(frame)
    with np.errstate(invalid="ignore"):
        m = ((d["ddt"] > p["d_dt_min"]) & (d["d7"] > p["d_t7_min"]) & (d["d14"] > p["d_t14_min"]))
    m = m & np.isfinite(d["ddt"]) & land
    if p.get("contextual"):
        m = contextual_test(frame, d, m, land, p)
    return m.astype(np.uint8)


# Published constants, Giglio, Descloitres, Justice & Kaufman (2003), "An enhanced contextual
# fire detection algorithm for MODIS", Remote Sens. Environ. 87:273-282 (Sect. 2.2.4-2.2.5);
# kept unchanged in Collection 6 (Giglio, Schroeder & Justice 2016, RSE 178:31-41).
# NOT tuned on our eval set.
MODIS_CTX = {
    "win_min": 3, "win_max": 21,      # background window grows 3x3 -> 21x21
    "min_valid_frac": 0.25,           # until >= 25 % of window pixels are valid ...
    "min_valid": 8,                   # ... and >= 8 valid pixels
    "k_dt": 3.5,                      # test 2: dT > mean_dT + 3.5 delta_dT
    "dt_abs": 6.0,                    # test 3: dT > mean_dT + 6 K
    "k_t4": 3.0,                      # test 4: T4 > mean_T4 + 3 delta_T4
    "t11_margin": 4.0,                # test 5: T11 > mean_T11 + delta_T11 - 4 K
    "bgfire_dev": 5.0,                # test 6: delta'_T4 (rejected background fires) > 5 K
    "t4_abs_day": 360.0, "t4_abs_night": 320.0,   # test 1: absolute pass-through
    # background fire pixels, excluded from the window statistics (raw T4, T4 - T11)
    "bgfire_t4_day": 325.0, "bgfire_dt_day": 20.0,
    "bgfire_t4_night": 310.0, "bgfire_dt_night": 10.0,
}


def _mad(a: np.ndarray) -> float:
    return float(np.mean(np.abs(a - a.mean())))   # Giglio's delta = mean absolute deviation


def contextual_test(frame: Frame, d: dict, cand: np.ndarray, land: np.ndarray, p=FP,
                    c=MODIS_CTX) -> np.ndarray:
    """Adaptation of the MODIS contextual fire test to the background-differenced fields.

    MODIS compares a pixel with its spatial neighbours in raw brightness temperatures. Here
    every field is already a change vs the same pixel on previous days, so the mapping is:
      MODIS dT  (T4 - T11)  ->  our Δ(C07 - C14)   (d["ddt"])
      MODIS T4  (3.9 µm)    ->  our ΔC07            (d["d7"])
      MODIS T11 (11 µm)     ->  our ΔC14            (d["d14"])
    so the contextual test asks: is this pixel's CHANGE an outlier against the change of its
    neighbours? A broad heating anomaly (sun on dry ground, a warm day) warms the neighbours
    too and fails; a fire raises one or two pixels. Valid background pixels: land, finite,
    not cloud-covered now (ΔC14 > d_t14_min) and not a "background fire" by Giglio's raw
    thresholds (T4 > 325 K and T4 - T11 > 20 K by day; 310 K / 10 K at night). A pixel whose window never reaches enough
    valid neighbours is "unknown" in MODIS and is not a fire. Test 1 (absolute) uses the
    raw C07 brightness temperature, as published.
    Fire = test1 OR (tests 2, 3, 4 AND (test 5 OR test 6)).
    """
    ddt, d7, d14 = d["ddt"], d["d7"], d["d14"]
    day = vision.is_daytime(frame)
    t4, t11 = frame.bands["C07"], frame.bands["C14"]
    t4_abs = c["t4_abs_day"] if day else c["t4_abs_night"]
    with np.errstate(invalid="ignore"):
        bgfire = land & (t4 > c["bgfire_t4_day" if day else "bgfire_t4_night"]) \
            & (t4 - t11 > c["bgfire_dt_day" if day else "bgfire_dt_night"])
        valid = land & np.isfinite(ddt) & (d14 > p["d_t14_min"]) & ~bgfire
    out = np.zeros_like(cand)
    H, W = cand.shape
    for r, col in zip(*np.nonzero(cand)):
        if np.isfinite(t4[r, col]) and t4[r, col] > t4_abs:
            out[r, col] = True
            continue
        for w in range(c["win_min"], c["win_max"] + 1, 2):
            h = w // 2
            sl = (slice(max(r - h, 0), min(r + h + 1, H)), slice(max(col - h, 0), min(col + h + 1, W)))
            v = valid[sl]
            n = int(v.sum())
            if n < c["min_valid"] or n < c["min_valid_frac"] * v.size:
                continue
            bdt, b7, b14 = ddt[sl][v], d7[sl][v], d14[sl][v]
            m_dt, s_dt = float(bdt.mean()), _mad(bdt)
            m7, s7 = float(b7.mean()), _mad(b7)
            m14, s14 = float(b14.mean()), _mad(b14)
            bf = bgfire[sl].copy()
            bf[r - sl[0].start, col - sl[1].start] = False
            t6 = bool(bf.sum() > 1 and _mad(d7[sl][bf]) > c["bgfire_dev"])
            out[r, col] = (ddt[r, col] > m_dt + c["k_dt"] * s_dt
                           and ddt[r, col] > m_dt + c["dt_abs"]
                           and d7[r, col] > m7 + c["k_t4"] * s7
                           and (d14[r, col] > m14 + s14 - c["t11_margin"] or t6))
            break
    return out


# ------------------------------------------------------------------ smoke (supporting)

def smoke_delta_score(frame: Frame, bg: Background, p=FP) -> np.ndarray:
    n = lambda a: np.nan_to_num(a).astype(np.float32)
    dR = n(frame.bands["C02"] - bg.bands["C02"])
    dB = n(frame.bands["C01"] - bg.bands["C01"])
    dS = n(frame.bands["C05"] - bg.bands["C05"])
    dT = n(frame.bands["C13"] - bg.bands["C13"])
    T = n(frame.bands["C13"])
    dV = np.maximum(dR, dB)
    R = n(frame.bands["C02"])
    mean = cv2.blur(R, (5, 5))
    std = np.sqrt(np.maximum(cv2.blur(R * R, (5, 5)) - mean ** 2, 0))
    soft = vision._soft
    s = (soft(dV, p["smoke_dvis"], 0.002)
         * soft(dS / np.maximum(dV, 1e-3), p["smoke_swir_frac"], 0.08, rising=False)
         * soft(T, 275, 3) * soft(dT, p["smoke_dbt"], 1.5)
         * soft(std, p["smoke_tex"], 0.006, rising=False))
    return cv2.bilateralFilter(s.astype(np.float32), 5, 0.1, 3)


def smoke_mask(frame: Frame, bg: Background, p=FP) -> np.ndarray:
    if not vision.is_daytime(frame):
        return np.zeros(frame.shape, np.uint8)
    m = (smoke_delta_score(frame, bg, p) > p["smoke_thresh"]).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= p["smoke_min_px"]]
    return np.isin(lab, keep).astype(np.uint8)


# ------------------------------------------------------------------ candidates

def _bearing(dx_px: float, dy_px: float) -> float:
    """Compass bearing (0 = N, 90 = E) of a pixel offset on the north-up grid."""
    return float(np.degrees(np.arctan2(dx_px, -dy_px)) % 360)


@dataclass
class FireCandidate:
    id: int
    centroid: tuple[float, float]               # lon, lat
    bbox: tuple[float, float, float, float]     # lon_min, lat_min, lon_max, lat_max
    hot_px: int
    max_ddt_k: float                            # Δ(C07−C14) vs background
    max_d7_k: float
    max_t7_k: float
    volcanic: str | None = None                 # zone name, if inside one
    smoke: dict | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)
    pixel_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    pixels: np.ndarray | None = field(default=None, repr=False)   # (row, col) pairs

    # render_overlay() compatibility
    @property
    def mean_score(self) -> float:
        return self.max_ddt_k

    def label(self) -> str:
        return f"#{self.id} {'VOLC' if self.volcanic else 'FIRE'} +{self.max_ddt_k:.0f}K"

    def summary(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k not in ("polygon", "pixel_bbox", "pixels")}
        d["centroid"] = [round(c, 3) for c in self.centroid]
        d["bbox"] = [round(c, 3) for c in self.bbox]
        return d

    def to_feature(self, extra: dict | None = None) -> dict:
        props = self.summary()
        props.update(extra or {})
        ring = list(self.polygon)
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        return {"type": "Feature", "properties": props,
                "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _ring(frame: Frame, mask: np.ndarray) -> list[tuple[float, float]]:
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return []
    cnt = max(cnts, key=cv2.contourArea)
    if len(cnt) < 3:   # single pixel / line: use its box
        x, y, w, h = cv2.boundingRect(cnt)
        cnt = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    return [frame.pixel_to_lonlat(float(x), float(y)) for x, y in cnt.reshape(-1, 2)]


def detect_fires(frame: Frame, bg: Background, p=FP):
    """-> candidates, anomaly mask, smoke mask."""
    anom = thermal_anomaly(frame, bg, p)
    smoke = smoke_mask(frame, bg, p)
    volc = volcanic_zones(frame)
    d = thermal_delta(frame, bg)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (p["cluster_px"], p["cluster_px"]))
    n, lab = cv2.connectedComponents(cv2.dilate(anom, k), connectivity=8)
    ns, slab, sst, scen = cv2.connectedComponentsWithStats(smoke, connectivity=8)
    kpp = _km_per_px(frame)
    cands: list[FireCandidate] = []
    for i in range(1, n):
        hot = (lab == i) & (anom > 0)
        rows, cols = np.nonzero(hot)
        if rows.size == 0:
            continue
        r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
        zones = volc[hot]
        zone = VOLCANIC_ZONES[int(zones[zones > 0][0]) - 1][0] if (zones > 0).any() else None
        cx, cy = float(cols.mean()), float(rows.mean())
        lon0, lat1 = frame.pixel_to_lonlat(c0, r0)
        lon1, lat0 = frame.pixel_to_lonlat(c1 + 1, r1 + 1)
        cand = FireCandidate(
            id=len(cands) + 1, centroid=frame.pixel_to_lonlat(cx, cy), bbox=(lon0, lat0, lon1, lat1),
            hot_px=int(rows.size), max_ddt_k=round(float(np.nanmax(d["ddt"][hot])), 1),
            max_d7_k=round(float(np.nanmax(d["d7"][hot])), 1),
            max_t7_k=round(float(np.nanmax(frame.bands["C07"][hot])), 1), volcanic=zone,
            pixel_bbox=(int(c0), int(r0), int(c1 - c0 + 1), int(r1 - r0 + 1)),
            pixels=np.stack([rows, cols], 1))
        # smoke: components whose nearest pixel is within smoke_touch_px of the hotspot
        region = hot.astype(np.uint8)
        if ns > 1:
            dist = cv2.distanceTransform((1 - region).astype(np.uint8), cv2.DIST_L2, 5)
            near = [j for j in range(1, ns) if dist[slab == j].min() <= p["smoke_touch_px"]]
            if near:
                sm = np.isin(slab, near)
                sr, sc = np.nonzero(sm)
                far = np.hypot(sr - cy, sc - cx)
                cand.smoke = {
                    "area_km2": round(float(sm.sum() * kpp * kpp), 1),
                    "length_km": round(float(far.max() * kpp), 1),
                    # direction the plume extends from the fire = downwind
                    "bearing_deg": round(_bearing(sc.mean() - cx, sr.mean() - cy)),
                }
                region = (region | sm).astype(np.uint8)
                x, y, w, h = cv2.boundingRect(sm.astype(np.uint8))
                cand.smoke["pixel_bbox"] = (int(x), int(y), int(w), int(h))
        cand.polygon = _ring(frame, cv2.dilate(region, np.ones((3, 3), np.uint8)))
        cands.append(cand)
    return cands, anom, smoke


def hotspot_present(anom: np.ndarray, cand: FireCandidate, p=FP) -> bool:
    """Is there an anomaly pixel within persist_px of any of the candidate's pixels?"""
    r = p["persist_px"]
    rows, cols = cand.pixels[:, 0], cand.pixels[:, 1]
    h, w = anom.shape
    for y, x in zip(rows, cols):
        if anom[max(y - r, 0):min(y + r + 1, h), max(x - r, 0):min(x + r + 1, w)].any():
            return True
    return False


def compass_from_flow_heading(math_deg: float) -> float:
    """vision.track_motion reports a math angle (0 = E, counter-clockwise); convert."""
    return float((90 - math_deg) % 360)


def angle_diff(a: float, b: float) -> float:
    return float(abs((a - b + 180) % 360 - 180))
