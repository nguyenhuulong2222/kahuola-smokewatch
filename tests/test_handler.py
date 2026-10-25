"""HTTP handler: review flow, token check, public layer disclaimer, input validation."""
import json

import pytest

import handler
from smokewatch.store import LocalStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BUCKET", raising=False)
    monkeypatch.setenv("LOCAL_STORE", str(tmp_path))
    monkeypatch.setenv("REVIEW_TOKEN", "s3cret-token-for-tests-123456")
    s = LocalStore(str(tmp_path))
    feat = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "properties": {"review_id": "20230809T0131Z-abc123-1", "status": "pending_review",
                           "confidence": 0.8, "centroid": [-156.67, 20.88], "hot_px": 3, "max_ddt_k": 20.0,
                           "frame_time": "2023-08-09T01:31:17+00:00", "rationale": "Hotspot …",
                           "volcanic": None, "evidence": {"track": {"hotspot_scans": 3, "scans_checked": 3}}}}
    s.put_json("reviews/pending/20230809T0131Z-abc123-1.json", feat)
    s.put_json("latest/summary.json", {"frame_time": "2023-08-09T01:31:17+00:00", "candidates": 1})
    return s


def req(method, path, body=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path,
            "body": json.dumps(body) if body is not None else None}


def test_review_page_lists_pending(store):
    r = handler.http(req("GET", "/review"))
    assert r["statusCode"] == 200 and "20230809T0131Z-abc123-1" in r["body"]
    assert handler.DISCLAIMER_VI in r["body"]


def test_bad_token_cannot_publish(store):
    r = handler.http(req("POST", "/review", {"review_id": "20230809T0131Z-abc123-1",
                                             "decision": "approve", "token": "wrong"}))
    assert r["statusCode"] == 403
    assert store.list("reviews/published/") == []


def test_approve_publishes_with_disclaimer(store):
    r = handler.http(req("POST", "/review", {"review_id": "20230809T0131Z-abc123-1",
                                             "decision": "approve", "token": "s3cret-token-for-tests-123456"}))
    assert r["statusCode"] == 200, r
    assert store.list("reviews/pending/") == []
    layer = json.loads(handler.http(req("GET", "/public/smoke.geojson"))["body"])
    assert layer["disclaimer"] == handler.DISCLAIMER_VI and len(layer["features"]) == 1
    assert layer["features"][0]["properties"]["disclaimer"] == handler.DISCLAIMER_VI
    assert "rationale" not in layer["features"][0]["properties"]   # public layer stays minimal


def test_reject_keeps_layer_empty(store):
    handler.http(req("POST", "/review", {"review_id": "20230809T0131Z-abc123-1",
                                         "decision": "reject", "token": "s3cret-token-for-tests-123456"}))
    assert json.loads(handler.http(req("GET", "/public/smoke.geojson"))["body"])["features"] == []


def test_path_traversal_rejected(store):
    assert handler.http(req("GET", "/api/trace/..%2F..%2Fetc"))["statusCode"] == 400
    r = handler.http(req("POST", "/review", {"review_id": "../latest/summary", "decision": "approve",
                                             "token": "s3cret-token-for-tests-123456"}))
    assert r["statusCode"] == 400


def test_empty_server_token_refuses_everything(store, monkeypatch):
    monkeypatch.setenv("REVIEW_TOKEN", "")
    r = handler.http(req("POST", "/review", {"review_id": "20230809T0131Z-abc123-1",
                                             "decision": "approve", "token": ""}))
    assert r["statusCode"] == 403


def test_page_before_first_scan(tmp_path, monkeypatch):
    monkeypatch.delenv("BUCKET", raising=False)
    monkeypatch.setenv("LOCAL_STORE", str(tmp_path))
    body = handler.http(req("GET", "/review"))["body"]
    assert "No scan has run yet" in body and "No overlay yet" in body and "<img" not in body


def test_page_shows_utc_and_hst(store):
    body = handler.http(req("GET", "/review"))["body"]
    assert "2023-08-09 01:31 UTC · 15:31 HST (Aug 08)" in body
    assert "No overlay yet" in body          # summary exists but no overlay.png


def test_frame_cache_uses_s3_and_respects_deadline(tmp_path, monkeypatch):
    import numpy as np
    from smokewatch import goes
    s = LocalStore(str(tmp_path / "store"))
    cache = handler.FrameCache(s, str(tmp_path / "tmp"))
    key = "ABI-L2-MCMIPC/2023/221/01/OR_ABI-L2-MCMIPC-M6_G18_s20232210131174_e2_c3.nc"
    np.savez(tmp_path / "x.npz", a=np.zeros(1))
    s.put_bytes(cache._s3key(key), (tmp_path / "x.npz").read_bytes())
    called = []
    monkeypatch.setattr(goes, "prefetch", lambda *a, **k: called.append(a))
    other = key.replace("0131174", "0136174")
    st = cache.warm([key, other], deadline=0.0)      # deadline already passed
    assert st == {"wanted": 2, "tmp": 0, "s3": 1, "noaa": 0, "missing": 1}
    assert called == []                              # no NOAA fetch after the deadline
