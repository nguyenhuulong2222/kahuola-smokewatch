"""Rasterize Natural Earth 10m land polygons onto the Hawaiʻi grid (one-off, dev only).

  curl -LO https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip && unzip ne_10m_land.zip
  python scripts/make_land_mask.py --shp ne_10m_land.shp

Writes smokewatch/data/land.png (255 = land), which fire detection uses to ignore
ocean pixels: sun glint raises 3.9 µm over water and is not a wildfire.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import shapefile  # noqa: E402  (pyshp, requirements-dev)

from smokewatch.goes import GRID_DEG, HAWAII_BBOX  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "smokewatch", "data", "land.png")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp", default=".cache/ne/ne_10m_land.shp")
    a = ap.parse_args(argv)
    lat_min, lat_max, lon_min, lon_max = HAWAII_BBOX
    lats = np.arange(lat_max, lat_min, -GRID_DEG)
    lons = np.arange(lon_min, lon_max, GRID_DEG)
    # supersample 4x so small islands and thin coasts survive
    s = 4
    big = np.zeros((len(lats) * s, len(lons) * s), np.uint8)
    for shp in shapefile.Reader(a.shp).shapes():
        bx = shp.bbox
        if bx[2] < lon_min or bx[0] > lon_max or bx[3] < lat_min or bx[1] > lat_max:
            continue
        parts = list(shp.parts) + [len(shp.points)]
        for i0, i1 in zip(parts[:-1], parts[1:]):
            pts = np.array(shp.points[i0:i1])
            px = np.stack([(pts[:, 0] - lon_min) / GRID_DEG * s,
                           (lat_max - pts[:, 1]) / GRID_DEG * s], 1)
            cv2.fillPoly(big, [np.round(px).astype(np.int32)], 255)
    land = cv2.resize(big, (len(lons), len(lats)), interpolation=cv2.INTER_AREA)
    land = np.where(land > 0, 255, 0).astype(np.uint8)   # any land in the cell counts
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cv2.imwrite(OUT, land)
    print(OUT, land.shape, f"land {(land > 0).mean():.1%}")


if __name__ == "__main__":
    main()
