"""GOES-18 (GOES-West) ABI access from NOAA Open Data on AWS + reprojection to a
regular lat/lon grid over Hawaiʻi using OpenCV remap.

Product: ABI-L2-MCMIP (Cloud & Moisture Imagery, all 16 bands, 2 km).
  sector "C" on GOES-18 = PACUS (5-min cadence, covers Hawaiʻi)
  sector "F" = Full Disk (10-min cadence) — fallback.
Bucket is public: s3://noaa-goes18 (us-east-1), anonymous access.

Reads use HTTP byte ranges (netCDF "#mode=bytes"): only the ~4 HDF5 chunks per band
that cover Hawaiʻi are transferred, not the 54 MB file. Reprojected frames are cached
as .npz (~1.7 MB) so eval replays and the multi-day thermal background are cheap.
"""
from __future__ import annotations

import datetime as dt
import functools
import os
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

BUCKET = os.environ.get("GOES_BUCKET", "noaa-goes18")
# prefetch runs PREFETCH_WORKERS files at once and download_file splits each into up to
# PART_CONCURRENCY ranged GETs, all on the one shared client: size its pool for both.
PREFETCH_WORKERS = 8
PART_CONCURRENCY = 10   # boto3 TransferConfig default

# Hawaiʻi main islands bbox (lat_min, lat_max, lon_min, lon_max)
HAWAII_BBOX = (18.5, 22.6, -160.6, -154.6)
GRID_DEG = 0.02  # ~2 km, matches ABI native resolution near Hawaiʻi

BANDS = ("C01", "C02", "C03", "C05", "C07", "C13", "C14")

_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{3})")


@dataclass
class Frame:
    """One GOES scene resampled to the Hawaiʻi lat/lon grid."""
    time: dt.datetime
    key: str
    bands: dict[str, np.ndarray]          # name -> float32 HxW (refl 0-1 or BT K)
    lats: np.ndarray                      # H
    lons: np.ndarray                      # W
    meta: dict = field(default_factory=dict)

    @property
    def shape(self):
        return next(iter(self.bands.values())).shape

    def pixel_to_lonlat(self, col: float, row: float) -> tuple[float, float]:
        lon = self.lons[0] + col * (self.lons[1] - self.lons[0])
        lat = self.lats[0] + row * (self.lats[1] - self.lats[0])
        return float(lon), float(lat)


def scan_start(key: str) -> dt.datetime:
    m = _START_RE.search(key)
    if not m:
        raise ValueError(f"no start time in {key}")
    y, doy, hh, mm, ss = m.groups()
    t = dt.datetime(int(y), 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=int(doy) - 1)
    return t.replace(hour=int(hh), minute=int(mm), second=int(ss[:2]))


@functools.lru_cache(maxsize=1)
def _s3():
    """One shared client: creating clients concurrently from threads is not safe, using one is."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    return boto3.client("s3", region_name="us-east-1",
                        config=Config(signature_version=UNSIGNED, retries={"max_attempts": 3},
                                      max_pool_connections=PREFETCH_WORKERS * PART_CONCURRENCY))


def _hour_keys_uncached(sector: str, hour: dt.datetime) -> tuple[str, ...]:
    prefix = f"ABI-L2-MCMIP{sector}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
    keys: list[str] = []
    for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".nc")]
    return tuple(keys)


_hour_keys_cached = functools.lru_cache(maxsize=512)(_hour_keys_uncached)


def _hour_keys(sector: str, hour: dt.datetime) -> tuple[str, ...]:
    hour = hour.replace(minute=0, second=0, microsecond=0)
    # a finished hour never changes; the current one still gains scans
    if dt.datetime.now(dt.timezone.utc) - hour > dt.timedelta(hours=2):
        return _hour_keys_cached(sector, hour)
    return _hour_keys_uncached(sector, hour)


def list_keys(when: dt.datetime, sector: str = "C", hours_back: int = 1) -> list[str]:
    """Keys for scans in the hour of `when` (and `hours_back` hours before), sorted by time."""
    keys: list[str] = []
    for h in range(hours_back, -1, -1):
        keys += _hour_keys(sector, when - dt.timedelta(hours=h))
    return sorted(keys, key=scan_start)


def keys_between(t0: dt.datetime, t1: dt.datetime, sector: str = "C") -> list[str]:
    """All scan keys with start time in [t0, t1]."""
    keys, h = [], t0.replace(minute=0, second=0, microsecond=0)
    while h <= t1:
        keys += _hour_keys(sector, h)
        h += dt.timedelta(hours=1)
    return sorted((k for k in keys if t0 <= scan_start(k) <= t1), key=scan_start)


def nearest_key(when: dt.datetime, sector: str = "C") -> str:
    keys = [k for k in list_keys(when, sector) if scan_start(k) <= when + dt.timedelta(minutes=5)]
    if not keys:
        raise FileNotFoundError(f"no GOES scans near {when.isoformat()} sector {sector}")
    return min(keys, key=lambda k: abs((scan_start(k) - when).total_seconds()))


def download(key: str, cache_dir: str = "/tmp/goes") -> str:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, os.path.basename(key))
    if not os.path.exists(path):
        from boto3.s3.transfer import TransferConfig
        _s3().download_file(BUCKET, key, path + ".part",
                            Config=TransferConfig(max_concurrency=PART_CONCURRENCY))
        os.replace(path + ".part", path)
    return path


# ---------------------------------------------------------------- reprojection

def latlon_to_scan(lat: np.ndarray, lon: np.ndarray, proj: dict) -> tuple[np.ndarray, np.ndarray]:
    """Geodetic lat/lon (deg) -> ABI fixed-grid scan angles x, y (rad). GOES-R PUG Vol.3 §5.1.2.8."""
    r_eq = proj["semi_major_axis"]
    r_pol = proj["semi_minor_axis"]
    H = proj["perspective_point_height"] + r_eq
    lon0 = np.deg2rad(proj["longitude_of_projection_origin"])
    e2 = (r_eq**2 - r_pol**2) / r_eq**2
    lat_r, lon_r = np.deg2rad(lat), np.deg2rad(lon)
    phi_c = np.arctan((r_pol**2 / r_eq**2) * np.tan(lat_r))
    r_c = r_pol / np.sqrt(1 - e2 * np.cos(phi_c) ** 2)
    sx = H - r_c * np.cos(phi_c) * np.cos(lon_r - lon0)
    sy = -r_c * np.cos(phi_c) * np.sin(lon_r - lon0)
    sz = r_c * np.sin(phi_c)
    x = np.arcsin(-sy / np.sqrt(sx**2 + sy**2 + sz**2))
    y = np.arctan(sz / sx)
    return x, y


def build_remap(x_coords: np.ndarray, y_coords: np.ndarray, proj: dict,
                bbox=HAWAII_BBOX, step=GRID_DEG):
    """cv2.remap maps from the native fixed grid onto a north-up lat/lon grid."""
    lat_min, lat_max, lon_min, lon_max = bbox
    lats = np.arange(lat_max, lat_min, -step, dtype=np.float64)   # north-up
    lons = np.arange(lon_min, lon_max, step, dtype=np.float64)
    LON, LAT = np.meshgrid(lons, lats)
    sx, sy = latlon_to_scan(LAT, LON, proj)
    dx = x_coords[1] - x_coords[0]
    dy = y_coords[1] - y_coords[0]
    map_x = ((sx - x_coords[0]) / dx).astype(np.float32)
    map_y = ((sy - y_coords[0]) / dy).astype(np.float32)
    return map_x, map_y, lats, lons


def read_frame(path: str, key: str | None = None, bbox=HAWAII_BBOX) -> Frame:
    import netCDF4
    with netCDF4.Dataset(path) as ds:
        pv = ds.variables["goes_imager_projection"]
        proj = {a: float(pv.getncattr(a)) for a in (
            "semi_major_axis", "semi_minor_axis", "perspective_point_height",
            "longitude_of_projection_origin")}
        x = np.asarray(ds.variables["x"][:], dtype=np.float64)
        y = np.asarray(ds.variables["y"][:], dtype=np.float64)
        map_x, map_y, lats, lons = build_remap(x, y, proj, bbox)
        # Only read the native window we need (keeps Lambda memory small)
        c0 = max(int(np.floor(np.nanmin(map_x))) - 2, 0)
        c1 = min(int(np.ceil(np.nanmax(map_x))) + 3, len(x))
        r0 = max(int(np.floor(np.nanmin(map_y))) - 2, 0)
        r1 = min(int(np.ceil(np.nanmax(map_y))) + 3, len(y))
        if c1 <= c0 or r1 <= r0:
            raise ValueError("bbox not inside this GOES sector")
        mx, my = map_x - c0, map_y - r0
        bands = {}
        for b in BANDS:
            v = ds.variables[f"CMI_{b}"]
            arr = np.ma.filled(v[r0:r1, c0:c1].astype(np.float32), np.nan)
            bands[b] = cv2.remap(arr, mx, my, interpolation=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
        t = scan_start(key or os.path.basename(path.split("#")[0]))
    return Frame(time=t, key=key or os.path.basename(path), bands=bands, lats=lats, lons=lons,
                 meta={"native_window": [r0, r1, c0, c1]})


def _save_npz(fr: Frame, path: str):
    np.savez_compressed(path + ".part.npz", lats=fr.lats, lons=fr.lons, key=fr.key,
                        time=fr.time.isoformat(), **{f"b_{k}": v for k, v in fr.bands.items()})
    os.replace(path + ".part.npz", path)


def _load_npz(path: str) -> Frame:
    with np.load(path) as z:
        return Frame(time=dt.datetime.fromisoformat(str(z["time"])), key=str(z["key"]),
                     bands={k[2:]: z[k] for k in z.files if k.startswith("b_")},
                     lats=z["lats"], lons=z["lons"], meta={"cache": "npz"})


def load_key(key: str, cache_dir: str = "/tmp/goes") -> Frame:
    """Frame for an exact S3 key: .npz cache -> local .nc -> fetch.

    GOES_FETCH=bytes (default): HTTP byte-range read of the Hawaiʻi window only — best on a
    slow link. GOES_FETCH=download: whole-file S3 download — best inside us-east-1 (Lambda),
    where 54 MB takes ~1 s and downloads parallelise (see prefetch)."""
    os.makedirs(cache_dir, exist_ok=True)
    base = os.path.basename(key)
    npz = os.path.join(cache_dir, base[:-3] + ".npz")
    if os.path.exists(npz):
        return _load_npz(npz)
    nc = os.path.join(cache_dir, base)
    if not os.path.exists(nc) and os.environ.get("GOES_FETCH", "bytes") == "download":
        download(key, cache_dir)
    src = nc if os.path.exists(nc) else f"https://{BUCKET}.s3.amazonaws.com/{key}#mode=bytes"
    fr = read_frame(src, key)
    _save_npz(fr, npz)
    if os.environ.get("GOES_FETCH") == "download":
        os.remove(nc)   # keep /tmp small: the npz is all we need
    return fr


def prefetch(keys: list[str], cache_dir: str = "/tmp/goes", workers: int = PREFETCH_WORKERS) -> None:
    """Download raw files for keys not yet cached, in parallel threads (S3 I/O only; the
    netCDF decode stays single-threaded in load_key because HDF5 is not thread-safe)."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [k for k in dict.fromkeys(keys)
            if not os.path.exists(os.path.join(cache_dir, os.path.basename(k)[:-3] + ".npz"))]
    if todo:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda k: download(k, cache_dir), todo))


def fetch_frame(when: dt.datetime, sector: str = "C", cache_dir: str = "/tmp/goes") -> Frame:
    return load_key(nearest_key(when, sector), cache_dir)
