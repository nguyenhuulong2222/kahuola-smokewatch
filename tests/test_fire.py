"""Fire-anchored detection + agent safety rails on synthetic scenes."""
import datetime as dt

import numpy as np

from smokewatch import agent, fire
from smokewatch.goes import Frame
from smokewatch.store import LocalStore
from tests.test_vision import make_scene

T0 = dt.datetime(2026, 10, 24, 22, 0, tzinfo=dt.timezone.utc)


def scene(t, fire_px=None, cloud_box=None, seed=0):
    """Clear background day (no fire, no smoke); optional hot pixel / cold cloud."""
    f = make_scene(smoke=False, cloud=False, fire=False, seed=seed)
    b = {k: v.copy() for k, v in f.bands.items()}
    if fire_px:
        r, c = fire_px
        b["C07"][r:r + 2, c:c + 2] += 30.0
        b["C14"][r:r + 2, c:c + 2] += 2.0
    if cloud_box:
        r0, r1, c0, c1 = cloud_box
        for k in ("C07", "C13", "C14"):
            b[k][r0:r1, c0:c1] -= 30.0
    return Frame(time=t, key=f"syn_{t:%d%H%M}_{seed}", bands=b, lats=f.lats, lons=f.lons)


def refs():
    return [scene(T0 - dt.timedelta(days=d) + dt.timedelta(minutes=m), seed=d * 10 + m)
            for d in (1, 2, 3) for m in (-5, 0, 5)]


def test_background_anomaly_finds_fire_not_hot_land():
    bg = fire.build_background(refs())
    assert bg.n_refs == 9
    cands, anom, _ = fire.detect_fires(scene(T0, fire_px=(110, 95)), bg)
    assert len(cands) == 1 and cands[0].max_ddt_k > 20 and cands[0].volcanic is None
    # the island is ~5 K warmer than ocean in C07 every day: never an anomaly
    assert anom.sum() == cands[0].hot_px


def test_cloudy_reference_does_not_create_anomaly():
    rs = refs()
    rs[4] = scene(rs[4].time, cloud_box=(100, 120, 80, 100), seed=99)   # cloud in one ref
    rs[1] = scene(rs[1].time, cloud_box=(100, 120, 80, 100), seed=98)
    bg = fire.build_background(rs)
    assert bg.n_clear[110, 90] == 7
    cands, anom, _ = fire.detect_fires(scene(T0), bg)
    assert cands == [] and anom.sum() == 0


def test_volcanic_zone_label():
    f = scene(T0)                                # synthetic grid spans the whole Hawaiʻi bbox
    zones = fire.volcanic_zones(f)
    col, row = fire._px(f, 19.406, -155.283)     # Kīlauea summit
    assert zones[int(round(row)), int(round(col))] == 1
    col, row = fire._px(f, 20.885, -156.663)     # Lahaina
    assert zones[int(round(row)), int(round(col))] == 0


def _episode(fire_now=True, fire_prev=True, store=None):
    bg = fire.build_background(refs())
    cur = scene(T0, fire_px=(110, 95) if fire_now else None)
    prevs = {T0 - dt.timedelta(minutes=m): scene(T0 - dt.timedelta(minutes=m),
                                                 fire_px=(110, 95) if fire_prev else None, seed=m)
             for m in (5, 10)}
    src = lambda t: prevs[min(prevs, key=lambda k: abs((k - t).total_seconds()))]
    return agent.Episode(cur, src, store or LocalStore("/tmp/sw_test_store"), [], bg)


def test_persistent_hotspot_is_reviewed_with_evidence_rationale(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.delenv("FIRMS_ARCHIVE_DIR", raising=False)
    ep = _episode(store=LocalStore(str(tmp_path)))
    agent.RulePolicy().run(ep)
    out = ep.outcome[1]
    assert out["action"] == "request_human_review", out
    assert "3/3 scans" in out["rationale"] and "+" in out["rationale"]
    assert ep.evidence[1]["track"]["hotspot_scans"] == 3


def test_single_scan_hotspot_only_rechecked(tmp_path):
    ep = _episode(fire_prev=False, store=LocalStore(str(tmp_path)))
    agent.RulePolicy().run(ep)
    assert ep.outcome[1]["action"] == "schedule_recheck"
    # and the rail refuses a review even if a policy asks for one
    ep2 = _episode(fire_prev=False, store=LocalStore(str(tmp_path)))
    ep2.call("track_plume", {"det_id": 1, "minutes_back": 10}, "test")
    r = ep2.call("request_human_review", {"det_id": 1, "confidence": 0.9, "rationale": "x"}, "test")
    assert "PermissionError" in r["error"]


def test_volcanic_candidate_cannot_be_reviewed(tmp_path):
    ep = _episode(store=LocalStore(str(tmp_path)))
    ep.dets[0].volcanic = "Kīlauea summit caldera"
    ep.evidence[1]["hotspot"]["volcanic"] = "Kīlauea summit caldera"
    ep.call("track_plume", {"det_id": 1}, "test")
    r = ep.call("request_human_review", {"det_id": 1, "confidence": 0.9, "rationale": "x"}, "test")
    assert "volcanic" in r["error"]
    agent.RulePolicy().run(ep)
    assert ep.outcome[1]["action"] == "dismiss" and "Kīlauea" in ep.outcome[1]["rationale"]
