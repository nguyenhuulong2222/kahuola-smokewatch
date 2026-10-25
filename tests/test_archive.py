"""Scan archive (render + index) and the public page / image routes."""
import datetime as dt
import json

import pytest

import handler
from smokewatch import fire, render
from smokewatch.store import LocalStore
from tests.test_fire import T0, refs, scene


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BUCKET", raising=False)
    monkeypatch.setenv("LOCAL_STORE", str(tmp_path))
    return LocalStore(str(tmp_path))


def _summary(t, review=True):
    return {"frame_time": t.isoformat(), "daytime": True, "background_scans": 9, "policy": "rules",
            "episode": f"{t:%Y%m%dT%H%MZ}-abc123",
            "evidence": {1: {"hotspot": {"id": 1, "centroid": [-156.67, 20.88], "max_ddt_k": 12.0,
                                         "hot_px": 2, "volcanic": None}}},
            "outcomes": {1: {"action": "request_human_review" if review else "schedule_recheck",
                             "confidence": 0.6, "rationale": "test"}}}


def test_archive_scan_writes_layers_index_and_event_copy(store):
    bg = fire.build_background(refs())
    cur = scene(T0, fire_px=(110, 95))
    rec = render.archive_scan(store, cur, bg, _summary(T0))
    day = render.day_of(T0)                                  # HST calendar day
    p = render.scan_prefix("archive", T0)
    assert p == f"archive/{day.replace('-', '/')}/{T0:%H%M}Z/"
    for f in ("overlay.webp", "anomaly.png", "summary.json"):
        assert store.exists(p + f) and store.exists(render.scan_prefix("events", T0) + f)
    idx = store.get_json(f"archive/index/{day}.json")
    assert idx["scans"][0]["reviews"] == 1 and idx["geo"]["rows"] == cur.shape[0]
    assert store.list("archive/index/tmp/") == []           # temp file removed after copy
    assert rec["candidates"][0]["review"] and store.exists("static/coast.png")
    assert len(store.get_bytes(p + "overlay.webp")) < 150_000


def test_scan_without_review_is_not_copied_to_events(store):
    render.archive_scan(store, scene(T0), fire.build_background(refs()), _summary(T0, review=False))
    assert store.list("events/") == []


def test_index_update_is_idempotent_and_sorted(store):
    t1 = T0 + dt.timedelta(minutes=10)
    for t in (t1, T0, t1):
        rec = render.scan_record(_summary(t), [])
        render.update_index(store, "archive", rec, render.scan_prefix("archive", t))
    idx = store.get_json(f"archive/index/{render.day_of(T0)}.json")
    assert [s["t"] for s in idx["scans"]] == [f"{T0:%H%M}Z", f"{t1:%H%M}Z"]


def test_hst_day_boundary():
    assert render.day_of(dt.datetime(2023, 8, 9, 1, 36, tzinfo=dt.timezone.utc)) == "2023-08-08"
    assert render.day_of(dt.datetime(2023, 8, 9, 10, 0, tzinfo=dt.timezone.utc)) == "2023-08-09"


def _get(path, qs=None):
    return handler.http({"requestContext": {"http": {"method": "GET"}}, "rawPath": path,
                         "queryStringParameters": qs})


def test_public_page_and_day_api(store):
    render.archive_scan(store, scene(T0), fire.build_background(refs()), _summary(T0))
    r = _get("/")
    assert r["statusCode"] == 200 and "Ước tính tự động từ vệ tinh" in r["body"] and "Automated satellite estimate" in r["body"]
    day = render.day_of(T0)
    assert json.loads(_get("/api/days")["body"])["days"] == [day]
    assert len(json.loads(_get("/api/day", {"date": day})["body"])["scans"]) == 1
    assert _get("/api/day", {"date": "2023-08-08; rm"})["statusCode"] == 400
    assert _get("/api/day", {"date": "1999-01-01"})["statusCode"] == 404


def test_img_route_only_redirects_allowed_prefixes(store):
    ok = _get("/img/static/coast.png")
    assert ok["statusCode"] == 302 and ok["headers"]["location"]
    for bad in ("/img/reviews/pending/x.json", "/img/traces/x.json", "/img/events/../traces/x.json",
                "/img/public/../../etc/passwd"):
        assert _get(bad)["statusCode"] == 400, bad
