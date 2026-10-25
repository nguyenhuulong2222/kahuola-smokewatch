"""NASA FIRMS active-fire lookup (independent evidence for the agent).

Suomi NPP stops delivering Nov 1 2026, so default to NOAA-20/21 VIIRS.
NRT sources cover the last ~2 months; use *_SP (standard processing) for history.

Only detections acquired at or before `when` are returned: in a replay the agent must
not see satellite passes that happen after the GOES scan it is judging.

Offline/eval: if FIRMS_ARCHIVE_DIR holds the keyless yearly country CSVs
(firms.modaps.eosdis.nasa.gov/data/country/{viirs-snpp,modis}/YYYY/*_United_States.csv),
they are used instead of the API (S-NPP VIIRS + MODIS only).
"""
from __future__ import annotations

import csv
import datetime as dt
import functools
import glob
import io
import os
import urllib.request

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
NRT_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
SP_SOURCES = ("VIIRS_NOAA20_SP", "VIIRS_SNPP_SP", "MODIS_SP")


def acq_time(row: dict) -> dt.datetime:
    hhmm = f'{int(row["acq_time"]):04d}'
    return dt.datetime.fromisoformat(f'{row["acq_date"]}T{hhmm[:2]}:{hhmm[2:]}+00:00')


@functools.lru_cache(maxsize=4)
def _archive(path: str) -> tuple[dict, ...]:
    """Hawaiʻi rows of one yearly CSV (the US files are ~30 MB; keep only what we need)."""
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            lat, lon = float(r["latitude"]), float(r["longitude"])
            if 18.0 < lat < 23.0 and -161.0 < lon < -154.0:
                src = "MODIS" if "modis" in os.path.basename(path).lower() else "VIIRS_SNPP"
                out.append({"lat": lat, "lon": lon, "t": acq_time(r), "conf": r.get("confidence"),
                            "frp": r.get("frp"), "type": r.get("type"), "src": src})
    return tuple(out)


def archive_points(directory: str, t0: dt.datetime, t1: dt.datetime) -> list[dict]:
    pts = []
    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        pts += [p for p in _archive(path) if t0 <= p["t"] <= t1]
    return pts


def _query_archive(directory, w, s, e, n, when, days):
    t0 = when - dt.timedelta(days=days)
    pts = [p for p in archive_points(directory, t0, when) if s <= p["lat"] <= n and w <= p["lon"] <= e]
    pts.sort(key=lambda p: p["t"])
    return {"count": len(pts),
            "points": [{**p, "time": p["t"].strftime("%Y-%m-%d %H%M")} for p in pts[:50]],
            "sources": sorted({p["src"] for p in pts}) or ["archive"],
            "latest": pts[-1]["t"].isoformat() if pts else None, "error": None}


def query(bbox: tuple[float, float, float, float], when: dt.datetime, days: int = 1,
          pad_deg: float = 0.4, map_key: str | None = None, timeout: int = 15) -> dict:
    """bbox = lon_min, lat_min, lon_max, lat_max. Returns {count, points, sources, error}."""
    w, s, e, n = bbox[0] - pad_deg, bbox[1] - pad_deg, bbox[2] + pad_deg, bbox[3] + pad_deg
    archive = os.environ.get("FIRMS_ARCHIVE_DIR")
    if archive:
        return _query_archive(archive, w, s, e, n, when, days)
    key = map_key or os.environ.get("FIRMS_MAP_KEY")
    if not key:
        return {"count": 0, "points": [], "sources": [], "error": "FIRMS_MAP_KEY not set"}
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
            if not (when - dt.timedelta(days=days) <= acq_time(row) <= when):
                continue
            points.append({
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "time": f'{row.get("acq_date")} {row.get("acq_time")}',
                "conf": row.get("confidence"), "frp": row.get("frp"), "src": src,
            })
    return {"count": len(points), "points": points[:50], "sources": used,
            "error": "; ".join(errors) or None}
