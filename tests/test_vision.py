"""Synthetic-scene tests: smoke must be detected, clouds/land/ocean rejected,
and the plume must track coherently between two frames."""
import datetime as dt

import cv2
import numpy as np

from smokewatch.goes import Frame, HAWAII_BBOX, GRID_DEG
from smokewatch import vision

H, W = 200, 300


def make_scene(shift=0, smoke=True, cloud=True, fire=True, night=False, seed=0):
    rng = np.random.default_rng(seed)
    lats = np.arange(HAWAII_BBOX[1], HAWAII_BBOX[1] - H * GRID_DEG, -GRID_DEG)[:H]
    lons = np.arange(HAWAII_BBOX[2], HAWAII_BBOX[2] + W * GRID_DEG, GRID_DEG)[:W]
    # ocean
    C01 = np.full((H, W), 0.07, np.float32) + rng.normal(0, 0.003, (H, W)).astype(np.float32)
    C02 = np.full((H, W), 0.05, np.float32)
    C03 = np.full((H, W), 0.03, np.float32)
    C05 = np.full((H, W), 0.02, np.float32)
    C13 = np.full((H, W), 295.0, np.float32)
    C07 = np.full((H, W), 298.0, np.float32)
    C14 = np.full((H, W), 295.0, np.float32)
    # island (land: red/NIR bright, not blue-dominant)
    island = np.zeros((H, W), np.uint8)
    cv2.ellipse(island, (90, 110), (45, 30), 20, 0, 360, 1, -1)
    il = island.astype(bool)
    C01[il], C02[il], C03[il], C05[il] = 0.08, 0.12, 0.30, 0.25
    C13[il], C14[il], C07[il] = 300, 300, 303
    if smoke:
        plume = np.zeros((H, W), np.float32)
        cv2.ellipse(plume, (150 + shift, 110), (55, 14), -10, 0, 360, 1.0, -1)
        plume = cv2.GaussianBlur(plume, (0, 0), 4)
        C01 += 0.14 * plume
        C02 += 0.08 * plume
        C05 += 0.01 * plume
    if cloud:
        cl = np.zeros((H, W), np.float32)
        for _ in range(25):
            cv2.circle(cl, (int(rng.integers(200, 290)), int(rng.integers(20, 70))),
                       int(rng.integers(3, 8)), 1.0, -1)
        cl = cv2.GaussianBlur(cl, (0, 0), 1.5)
        C01 += 0.5 * cl
        C02 += 0.5 * cl
        C05 += 0.35 * cl
        C13 -= 20 * cl
    if fire:
        C07[108:111, 98 + shift // 3:101 + shift // 3] = 340
    if night:
        C01[:] = C02[:] = C03[:] = C05[:] = 0.0
    bands = dict(C01=C01, C02=C02, C03=C03, C05=C05, C07=C07, C13=C13, C14=C14)
    t = dt.datetime(2026, 10, 24, 22, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=shift)
    return Frame(time=t, key=f"synthetic_{shift}", bands=bands, lats=lats, lons=lons)


def test_detects_smoke_rejects_cloud_and_land():
    f = make_scene()
    dets, mask, score = vision.detect(f)
    assert len(dets) == 1, dets
    d = dets[0]
    x, y, w, h = d.pixel_bbox
    assert 100 < x + w / 2 < 200 and 90 < y + h / 2 < 130
    # no smoke pixels in the cloud field or on the island core
    assert mask[20:70, 200:290].sum() == 0
    assert mask[100:120, 70:100].sum() == 0
    assert d.hotspots_px > 0


def test_no_smoke_scene_is_empty():
    dets, mask, _ = vision.detect(make_scene(smoke=False))
    assert dets == [] and mask.sum() == 0


def test_night_flag():
    assert vision.is_daytime(make_scene())
    assert not vision.is_daytime(make_scene(night=True))


def test_motion_tracking_persistent_and_coherent():
    a, b = make_scene(shift=0, seed=1), make_scene(shift=4, seed=2)
    da, ma, _ = vision.detect(a)
    db, mb, _ = vision.detect(b)
    m = vision.track_motion(a, b, ma, mb, db[0])
    assert m["persistent"], m
    assert m["coherence"] > 0.6, m


def test_geojson_feature():
    dets, _, _ = vision.detect(make_scene())
    feat = dets[0].to_feature({"status": "pending_review"})
    ring = feat["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) >= 4
    lon, lat = dets[0].centroid
    assert HAWAII_BBOX[2] < lon < HAWAII_BBOX[3] and HAWAII_BBOX[0] < lat < HAWAII_BBOX[1]


def test_overlay_renders():
    f = make_scene()
    dets, mask, _ = vision.detect(f)
    img = vision.render_overlay(f, mask, dets, vision.hotspot_mask(f))
    assert img.shape == (H * 3, W * 3, 3) and img.dtype == np.uint8
