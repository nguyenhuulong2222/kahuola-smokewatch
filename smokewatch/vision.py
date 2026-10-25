"""OpenCV 5 perception tools for wildfire smoke over Hawaiʻi.

Physical basis (daytime only):
  * Smoke scatters short wavelengths  -> blue (C01) elevated vs red (C02).
  * Smoke is nearly transparent at 1.61 µm (C05); liquid-water clouds are bright there.
  * Smoke sits low/warm; high clouds are cold in 10.3 µm (C13).
  * Smoke is spatially smooth; cumulus is textured.
  * Active fire: 3.9 µm (C07) much warmer than 11.2 µm (C14).
Each cue becomes a soft membership map; OpenCV does filtering, morphology,
connected components, contour extraction, optical-flow tracking and rendering.
These are heuristic thresholds tuned on a small Hawaiʻi set — see eval/ for
measured performance and failure cases (vog, sun glint, thin cirrus).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import cv2
import numpy as np

from .goes import Frame

PARAMS = {
    "haze_excess_min": 0.03, # C01 above local background (white top-hat)
    "tophat_px": 41,         # background scale ~80 km
    "br_ratio_min": 1.05,    # C01/C02
    "swir_max": 0.12,        # C05 reflectance
    "bt_warm_min": 275.0,    # C13 K
    "texture_max": 0.025,    # local std of C01 (7x7)
    "score_thresh": 0.40,
    "min_area_px": 12,       # ~48 km² at 2 km
    "hotspot_radius_px": 40,  # ~80 km: plume over land is often masked, fire sits inland
    "fire_dt_min": 12.0,     # C07 - C14 K
    "fire_t7_min": 310.0,
    "night_blue_max": 0.02,
}


def _soft(x: np.ndarray, center: float, width: float, rising=True) -> np.ndarray:
    z = (x - center) / width
    s = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
    return s if rising else 1.0 - s


def true_color(frame: Frame) -> np.ndarray:
    """CIMSS synthetic-green true color, gamma-stretched, BGR uint8 for OpenCV."""
    b = np.nan_to_num(frame.bands["C01"])
    r = np.nan_to_num(frame.bands["C02"])
    n = np.nan_to_num(frame.bands["C03"])
    g = 0.45 * r + 0.1 * n + 0.45 * b
    rgb = np.stack([r, g, b], axis=-1)
    rgb = np.clip(rgb, 0, 1) ** (1 / 2.2)
    return cv2.cvtColor((rgb * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def ir_gray(frame: Frame) -> np.ndarray:
    """Night background: inverted 11.2 µm (cold cloud tops white), BGR uint8."""
    t = np.nan_to_num(frame.bands["C14"], nan=300.0)
    g = np.clip((310.0 - t) / 90.0, 0, 1) ** 0.7
    return cv2.cvtColor((g * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def is_daytime(frame: Frame, p=PARAMS) -> bool:
    return float(np.nanmedian(frame.bands["C01"])) > p["night_blue_max"]


def smoke_score(frame: Frame, p=PARAMS) -> np.ndarray:
    """Per-pixel smoke likelihood in [0, 1]."""
    B = np.nan_to_num(frame.bands["C01"]).astype(np.float32)
    R = np.nan_to_num(frame.bands["C02"]).astype(np.float32)
    S = np.nan_to_num(frame.bands["C05"]).astype(np.float32)
    T = np.nan_to_num(frame.bands["C13"], nan=200.0).astype(np.float32)

    B_s = cv2.GaussianBlur(B, (3, 3), 0)
    # white top-hat: brightness excess over a large-scale background. Clear ocean is
    # itself blue (Rayleigh), so absolute blue is not enough — haze must stand out.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p["tophat_px"], p["tophat_px"]))
    excess = cv2.morphologyEx(B_s, cv2.MORPH_TOPHAT, k)
    ratio = B_s / np.maximum(cv2.GaussianBlur(R, (3, 3), 0), 1e-3)
    mean = cv2.blur(B, (7, 7))
    std = np.sqrt(np.maximum(cv2.blur(B * B, (7, 7)) - mean * mean, 0))

    m_vis = _soft(excess, p["haze_excess_min"], 0.008)
    m_ratio = _soft(ratio, p["br_ratio_min"], 0.04)
    m_swir = _soft(S, p["swir_max"], 0.02, rising=False)
    m_warm = _soft(T, p["bt_warm_min"], 3.0)
    m_smooth = _soft(std, p["texture_max"], 0.006, rising=False)
    # product: every cue must agree (a fuzzy AND)
    score = m_vis * m_ratio * m_swir * m_warm * m_smooth
    return cv2.bilateralFilter(score.astype(np.float32), 5, 0.1, 3)


def hotspot_mask(frame: Frame, p=PARAMS, ref=None) -> np.ndarray:
    """Active-fire pixels. With `ref` (a fire.Background) the test is the anomaly vs the
    same pixel on previous days; without it, the legacy absolute C07−C14 threshold."""
    if ref is not None:
        from . import fire
        return fire.thermal_anomaly(frame, ref)
    t7 = np.nan_to_num(frame.bands["C07"]).astype(np.float32)
    t14 = np.nan_to_num(frame.bands["C14"]).astype(np.float32)
    m = ((t7 - t14) > p["fire_dt_min"]) & (t7 > p["fire_t7_min"])
    return m.astype(np.uint8)


@dataclass
class Detection:
    id: int
    area_km2: float
    centroid: tuple[float, float]         # lon, lat
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max
    mean_score: float
    polygon: list[tuple[float, float]]    # lon, lat ring
    hotspots_px: int = 0
    nearest_hotspot_km: float | None = None
    motion: dict = field(default_factory=dict)
    pixel_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h

    def to_feature(self, extra: dict | None = None) -> dict:
        props = {k: v for k, v in asdict(self).items() if k not in ("polygon", "pixel_bbox")}
        props.update(extra or {})
        ring = list(self.polygon)
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        return {"type": "Feature", "properties": props,
                "geometry": {"type": "Polygon", "coordinates": [ring]}}


def detect(frame: Frame, p=PARAMS) -> tuple[list[Detection], np.ndarray, np.ndarray]:
    """Returns detections, binary smoke mask (uint8), score map."""
    score = smoke_score(frame, p)
    mask = (score > p["score_thresh"]).astype(np.uint8)
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k5)

    fire = hotspot_mask(frame, p)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    dlat = abs(frame.lats[1] - frame.lats[0])
    dlon = abs(frame.lons[1] - frame.lons[0])
    dets: list[Detection] = []
    keep = np.zeros_like(mask)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < p["min_area_px"]:
            continue
        comp = (labels == i).astype(np.uint8)
        keep |= comp
        contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(cnt, 1.0, True).reshape(-1, 2)
        ring = [frame.pixel_to_lonlat(float(cx), float(cy)) for cx, cy in approx]
        lat_c = frame.pixel_to_lonlat(*cents[i])[1]
        px_km2 = (dlat * 111.32) * (dlon * 111.32 * np.cos(np.deg2rad(lat_c)))
        # fire sits at the plume head, often outside the mask: distance transform gives
        # each hotspot pixel's distance (px) to the nearest plume pixel
        dist = cv2.distanceTransform((1 - comp).astype(np.uint8), cv2.DIST_L2, 5)
        fd = dist[fire > 0]
        near_fd = fd[fd <= p["hotspot_radius_px"]]
        lon0, lat1 = frame.pixel_to_lonlat(x, y)
        lon1, lat0 = frame.pixel_to_lonlat(x + w, y + h)
        dets.append(Detection(
            id=len(dets) + 1,
            area_km2=round(float(area * px_km2), 1),
            centroid=frame.pixel_to_lonlat(*cents[i]),
            bbox=(lon0, lat0, lon1, lat1),
            mean_score=round(float(score[labels == i].mean()), 3),
            polygon=ring,
            hotspots_px=int(near_fd.size),
            nearest_hotspot_km=round(float(fd.min()) * 2.0, 1) if fd.size else None,
            pixel_bbox=(int(x), int(y), int(w), int(h)),
        ))
    return dets, keep, score


def track_motion(prev: Frame, curr: Frame, prev_mask: np.ndarray, curr_mask: np.ndarray,
                 det: Detection) -> dict:
    """Dense optical flow (DIS) on the blue band: is the plume a coherent, persistent
    feature that advects with the wind? Clouds and vog behave differently."""
    def u8(fr):
        return cv2.normalize(np.nan_to_num(fr.bands["C01"]), None, 0, 255,
                             cv2.NORM_MINMAX).astype(np.uint8)
    a, b = u8(prev), u8(curr)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flow = dis.calc(a, b, None)  # prev -> curr

    x, y, w, h = det.pixel_bbox
    region = curr_mask[y:y + h, x:x + w].astype(bool)
    fx = flow[y:y + h, x:x + w, 0][region]
    fy = flow[y:y + h, x:x + w, 1][region]
    if fx.size == 0:
        return {"persistent": False, "iou": 0.0, "coherence": 0.0}

    # warp previous mask forward with backward flow approximation, compare to current
    hgt, wid = curr_mask.shape
    gx, gy = np.meshgrid(np.arange(wid, dtype=np.float32), np.arange(hgt, dtype=np.float32))
    warped = cv2.remap(prev_mask.astype(np.float32), gx - flow[..., 0], gy - flow[..., 1],
                       cv2.INTER_NEAREST)
    cm = curr_mask[y:y + h, x:x + w].astype(bool)
    wm = warped[y:y + h, x:x + w] > 0.5
    union = (cm | wm).sum()
    iou = float((cm & wm).sum() / union) if union else 0.0

    mag = np.hypot(fx, fy)
    # 1 = whole plume moves one way; 0 = random/boiling (convective cloud)
    coherence = float(np.hypot(fx.mean(), fy.mean()) / max(mag.mean(), 1e-6))
    minutes = max((curr.time - prev.time).total_seconds() / 60, 1)
    km_per_px = 2.2
    return {
        "persistent": iou > 0.3,
        "iou": round(iou, 3),
        "coherence": round(coherence, 3),
        "speed_kmh": round(float(np.median(mag)) * km_per_px * 60 / minutes, 1),
        "heading_deg": round(float(np.degrees(np.arctan2(-fy.mean(), fx.mean())) % 360), 0),
        "minutes": round(minutes, 1),
    }


def render_overlay(frame: Frame, mask: np.ndarray, dets: list[Detection],
                   fire: np.ndarray | None = None, scale: int = 3) -> np.ndarray:
    img = true_color(frame) if is_daytime(frame) else ir_gray(frame)
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    m = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    tint = img.copy()
    tint[m > 0] = (0, 140, 255)
    img = cv2.addWeighted(tint, 0.35, img, 0.65, 0)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, contours, -1, (0, 165, 255), 2, cv2.LINE_AA)
    if fire is not None and fire.any():
        ys, xs = np.nonzero(fire)
        for fx, fy in zip(xs, ys):
            cv2.circle(img, (int(fx * scale + scale // 2), int(fy * scale + scale // 2)),
                       5, (0, 0, 255), 2, cv2.LINE_AA)
    for d in dets:
        x, y, w, h = d.pixel_bbox
        text = d.label() if hasattr(d, "label") else f"#{d.id} {d.mean_score:.2f}"
        cv2.putText(img, text, (x * scale, max(y * scale - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    stamp = frame.time.strftime("GOES-18 %Y-%m-%d %H:%M UTC")
    cv2.putText(img, stamp, (8, img.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)
    return img
