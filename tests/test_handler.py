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
    r = handler.http(req("GET", "/"))
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
