"""Pixel diagnostics for threshold tuning.

  python scripts/pixel_stats.py --time 2023-08-09T02:00Z \
      --box plume=-157.2,20.6,-156.7,20.9 --box ocean=-157.8,20.0,-157.3,20.4

Prints percentiles of every band and every smoke cue inside each named lon/lat box
(lon_min,lat_min,lon_max,lat_max), and writes a zoomed true-color crop with a 0.1°
grid and the boxes drawn, so boxes can be placed by eye.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from scripts.run_local import parse_time  # noqa: E402
from smokewatch import goes, vision  # noqa: E402

P = vision.PARAMS


def cues(frame: goes.Frame) -> dict[str, np.ndarray]:
    """Same intermediate quantities smoke_score() uses."""
    B = np.nan_to_num(frame.bands["C01"]).astype(np.float32)
    R = np.nan_to_num(frame.bands["C02"]).astype(np.float32)
    B_s = cv2.GaussianBlur(B, (3, 3), 0)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (P["tophat_px"], P["tophat_px"]))
    mean = cv2.blur(B, (7, 7))
    out = {b: np.nan_to_num(v) for b, v in frame.bands.items()}
    out["excess"] = cv2.morphologyEx(B_s, cv2.MORPH_TOPHAT, k)
    out["ratio"] = B_s / np.maximum(cv2.GaussianBlur(R, (3, 3), 0), 1e-3)
    out["std7"] = np.sqrt(np.maximum(cv2.blur(B * B, (7, 7)) - mean * mean, 0))
    out["t7-t14"] = out["C07"] - out["C14"]
    out["score"] = vision.smoke_score(frame)
    return out


def box_px(frame, box):
    lon0, lat0, lon1, lat1 = box
    c = lambda lon: int(round((lon - frame.lons[0]) / (frame.lons[1] - frame.lons[0])))
    r = lambda lat: int(round((lat - frame.lats[0]) / (frame.lats[1] - frame.lats[0])))
    return r(lat1), r(lat0), c(lon0), c(lon1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", required=True)
    ap.add_argument("--sector", default="C")
    ap.add_argument("--cache", default="./.cache/goes")
    ap.add_argument("--box", action="append", default=[], help="name=lon0,lat0,lon1,lat1")
    ap.add_argument("--crop", default="-157.4,20.4,-155.9,21.3", help="crop for the PNG")
    ap.add_argument("--png", default="./out/diag")
    a = ap.parse_args(argv)

    fr = goes.fetch_frame(parse_time(a.time), a.sector, a.cache)
    cs = cues(fr)
    boxes = {n: tuple(map(float, v.split(","))) for n, v in (b.split("=") for b in a.box)}
    names = ["C01", "C02", "C03", "C05", "C13", "excess", "ratio", "std7", "t7-t14", "score"]
    for n, box in boxes.items():
        r0, r1, c0, c1 = box_px(fr, box)
        print(f"\n[{n}] {box}  {(r1 - r0) * (c1 - c0)} px   p10 / p50 / p90")
        for k in names:
            v = cs[k][r0:r1, c0:c1].ravel()
            print(f"  {k:7s} {np.percentile(v, 10):8.3f} {np.percentile(v, 50):8.3f} "
                  f"{np.percentile(v, 90):8.3f}")

    s = 8
    r0, r1, c0, c1 = box_px(fr, tuple(map(float, a.crop.split(","))))
    img = vision.true_color(fr)[r0:r1, c0:c1]
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    dets, mask, _ = vision.detect(fr)
    m = cv2.resize(mask[r0:r1, c0:c1], (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, cnts, -1, (0, 165, 255), 2)
    hot = vision.hotspot_mask(fr)[r0:r1, c0:c1]
    for y, x in zip(*np.nonzero(hot)):
        cv2.circle(img, (x * s + s // 2, y * s + s // 2), 6, (0, 0, 255), 2)
    for lon in np.arange(np.ceil(fr.lons[c0] * 10) / 10, fr.lons[c1 - 1], 0.1):
        x = int((lon - fr.lons[c0]) / 0.02 * s)
        cv2.line(img, (x, 0), (x, img.shape[0]), (90, 90, 90), 1)
        cv2.putText(img, f"{lon:.1f}", (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    for lat in np.arange(np.ceil(fr.lats[r1 - 1] * 10) / 10, fr.lats[r0], 0.1):
        y = int((fr.lats[r0] - lat) / 0.02 * s)
        cv2.line(img, (0, y), (img.shape[1], y), (90, 90, 90), 1)
        cv2.putText(img, f"{lat:.1f}", (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    for n, box in boxes.items():
        br0, br1, bc0, bc1 = box_px(fr, box)
        p0 = ((bc0 - c0) * s, (br0 - r0) * s)
        p1 = ((bc1 - c0) * s, (br1 - r0) * s)
        cv2.rectangle(img, p0, p1, (0, 255, 0), 1)
        cv2.putText(img, n, (p0[0] + 3, p0[1] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    os.makedirs(a.png, exist_ok=True)
    path = os.path.join(a.png, f"{fr.time:%Y%m%dT%H%MZ}_crop.png")
    cv2.imwrite(path, img)
    print("\ncrop", path)


if __name__ == "__main__":
    main()
