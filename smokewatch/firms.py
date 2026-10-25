"""NASA FIRMS active-fire lookup (independent evidence for the agent).

Suomi NPP stops delivering Nov 1 2026, so default to NOAA-20/21 VIIRS.
NRT sources cover the last ~2 months; use *_SP (standard processing) for history.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import urllib.request

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
NRT_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
SP_SOURCES = ("VIIRS_NOAA20_SP", "MODIS_SP")


def query(bbox: tuple[float, float, float, float], when: dt.datetime, days: int = 1,
          pad_deg: float = 0.4, map_key: str | None = None, timeout: int = 15) -> dict:
    """bbox = lon_min, lat_min, lon_max, lat_max. Returns {count, points, sources, error}."""
    key = map_key or os.environ.get("FIRMS_MAP_KEY")
    if not key:
        return {"count": 0, "points": [], "sources": [], "error": "FIRMS_MAP_KEY not set"}
    w, s, e, n = bbox[0] - pad_deg, bbox[1] - pad_deg, bbox[2] + pad_deg, bbox[3] + pad_deg
    age = dt.datetime.now(dt.timezone.utc) - when
    sources = NRT_SOURCES if age < dt.timedelta(days=50) else SP_SOURCES
    date = (when - dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    points, used, errors = [], [], []
    for src in sources:
        url = f"{API}/{key}/{src}/{w:.3f},{s:.3f},{e:.3f},{n:.3f}/{days}/{date}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                text = r.read().decode()
        except Exception as ex:  # network errors must not kill the loop
            errors.append(f"{src}: {ex}")
            continue
        if not text.startswith("latitude"):
            errors.append(f"{src}: {text[:80]}")
            continue
        used.append(src)
        for row in csv.DictReader(io.StringIO(text)):
            points.append({
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "time": f'{row.get("acq_date")} {row.get("acq_time")}',
                "conf": row.get("confidence"), "frp": row.get("frp"), "src": src,
            })
    return {"count": len(points), "points": points[:50], "sources": used,
            "error": "; ".join(errors) or None}
