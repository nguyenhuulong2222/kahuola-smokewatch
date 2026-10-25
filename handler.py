"""AWS Lambda entry points (one container image, two functions).

scan(event)  EventBridge rate(10 minutes). Runs the fire-anchored detector + triage agent on
             the latest GOES-18 PACUS scan. Fire detection is thermal, so it runs day AND
             night (the Olinda fire started at 00:22 HST); smoke evidence is daytime-only.
             Replay: {"time": "2023-08-09T01:31Z"}.
http(event)  Lambda Function URL:
               GET  /                     review page (HTML)
               GET  /api/latest           latest scan summary (JSON)
               GET  /api/trace/{episode}  full agent trace (JSON)
               GET  /public/smoke.geojson human-approved layer, with disclaimer
               POST /review               {review_id, decision: approve|reject, token}

Invariants: the agent never publishes — only POST /review with REVIEW_TOKEN moves a
candidate to the public layer; every public GeoJSON carries the disclaimer; no PII is
stored (decisions are recorded without reviewer identity).
"""
from __future__ import annotations

import base64
import datetime as dt
import functools
import hmac
import html
import json
import os
import re

from smokewatch import agent, fire, goes
from smokewatch.store import default_store

CACHE = os.environ.get("GOES_CACHE", "/tmp/goes")
DISCLAIMER_VI = "Ước tính tự động từ vệ tinh, không thay thế cảnh báo chính thức"
DISCLAIMER_EN = ("Automated satellite estimate, human-reviewed. Not an official warning — "
                 "follow Maui Emergency Management / county alerts.")
_ID = re.compile(r"^[0-9A-Za-z-]{1,80}$")


# ------------------------------------------------------------------ scan

def _parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def scan(event, context=None):
    event = event or {}
    when = _parse_time(event["time"]) if event.get("time") else dt.datetime.now(dt.timezone.utc)

    @functools.lru_cache(maxsize=32)
    def source(t: dt.datetime) -> goes.Frame:
        return goes.fetch_frame(t, "C", CACHE)

    # download everything this scan needs in parallel, then decode one by one
    key = goes.nearest_key(when)
    t0 = goes.scan_start(key)
    want = [key]
    for t in [t0 - dt.timedelta(minutes=5), t0 - dt.timedelta(minutes=10)] + fire.reference_times(t0):
        try:
            want.append(goes.nearest_key(t))
        except FileNotFoundError:
            pass
    goes.prefetch(want, CACHE)
    frame = goes.load_key(key, CACHE)
    store = default_store()
    summary = agent.run_scan(frame, source, store)
    out = {k: summary[k] for k in ("episode", "frame_time", "daytime", "background_scans",
                                   "candidates", "outcomes", "errors")}
    print(json.dumps(out, default=str))
    return out


# ------------------------------------------------------------------ public layer

def build_public_layer(store) -> dict:
    feats = []
    for k in store.list("reviews/published/"):
        f = store.get_json(k)
        if not f:
            continue
        p = f["properties"]
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "review_id": p.get("review_id"), "kind": "volcanic" if p.get("volcanic") else "wildfire",
            "frame_time": p.get("frame_time"), "approved_at": p.get("decided_at"),
            "confidence": p.get("confidence"), "centroid": p.get("centroid"),
            "source": "GOES-18 ABI thermal anomaly, Kahu Ola SmokeWatch",
            "disclaimer": DISCLAIMER_VI}})
    layer = {"type": "FeatureCollection", "disclaimer": DISCLAIMER_VI, "disclaimer_en": DISCLAIMER_EN,
             "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "features": feats}
    store.put_json("public/smoke.geojson", layer)
    return layer


def decide(store, review_id: str, decision: str) -> dict:
    if not _ID.match(review_id or ""):
        raise ValueError("bad review_id")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    src = f"reviews/pending/{review_id}.json"
    feat = store.get_json(src)
    if feat is None:
        raise LookupError("no such pending review")
    feat["properties"]["status"] = "published" if decision == "approve" else "rejected"
    feat["properties"]["decided_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    dst = "reviews/published/" if decision == "approve" else "reviews/rejected/"
    store.put_json(f"{dst}{review_id}.json", feat)
    store.delete(src)
    layer = build_public_layer(store)
    return {"review_id": review_id, "status": feat["properties"]["status"],
            "public_features": len(layer["features"])}


# ------------------------------------------------------------------ http

def _resp(status: int, body, ctype="application/json", extra=None):
    if not isinstance(body, str):
        body = json.dumps(body, default=str)
    h = {"content-type": ctype, "cache-control": "no-store", "x-content-type-options": "nosniff"}
    h.update(extra or {})
    return {"statusCode": status, "headers": h, "body": body}


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


def http(event, context=None):
    store = default_store()
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    try:
        if method == "GET" and path == "/":
            return _resp(200, review_page(store), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/latest":
            return _resp(200, store.get_json("latest/summary.json", {}))
        if method == "GET" and path.startswith("/api/trace/"):
            eid = path.rsplit("/", 1)[-1]
            if not _ID.match(eid):
                return _resp(400, {"error": "bad id"})
            t = store.get_json(f"traces/{eid}.json")
            return _resp(200, t) if t else _resp(404, {"error": "not found"})
        if method == "GET" and path == "/public/smoke.geojson":
            layer = store.get_json("public/smoke.geojson") or build_public_layer(store)
            return _resp(200, layer, "application/geo+json", {"access-control-allow-origin": "*"})
        if method == "POST" and path == "/review":
            b = _body(event)
            expected = os.environ.get("REVIEW_TOKEN", "")
            if not expected or not hmac.compare_digest(str(b.get("token", "")).encode(), expected.encode()):
                return _resp(403, {"error": "invalid token"})
            return _resp(200, decide(store, b.get("review_id", ""), b.get("decision", "")))
        return _resp(404, {"error": "not found"})
    except (ValueError, json.JSONDecodeError) as ex:
        return _resp(400, {"error": str(ex)})
    except LookupError as ex:
        return _resp(404, {"error": str(ex)})


def _pending(store, limit=30) -> list[dict]:
    out = []
    for k in store.list("reviews/pending/")[-limit:][::-1]:
        f = store.get_json(k)
        if f:
            p = f["properties"]
            out.append({**p, "img": store.url(f"reviews/img/{p['review_id']}.png")})
    return out


def review_page(store) -> str:
    latest = store.get_json("latest/summary.json", {}) or {}
    pend = _pending(store)
    e = html.escape

    def card(p):
        ev = p.get("evidence", {})
        tr, fi = ev.get("track", {}), ev.get("firms", {})
        lon, lat = p.get("centroid", [0, 0])
        facts = [f"ΔT +{p.get('max_ddt_k')} K, {p.get('hot_px')} px",
                 f"hotspot in {tr.get('hotspot_scans')}/{tr.get('scans_checked')} scans",
                 f"FIRMS prior 24 h: {fi.get('count', '—')}"]
        return f"""<article class=card>
<img src="{e(p['img'])}" alt="overlay for {e(p['review_id'])}" loading=lazy>
<h3>{lat:.3f}°N {abs(lon):.3f}°W · {e(str(p.get('frame_time', ''))[:16])}Z</h3>
<p class=conf>confidence {p.get('confidence')}</p>
<ul>{''.join(f'<li>{e(x)}</li>' for x in facts)}</ul>
<p class=why>{e(p.get('rationale', ''))}</p>
<p><a href="api/trace/{e(p['review_id'].rsplit('-', 1)[0])}">agent trace</a></p>
<div class=btns><button data-id="{e(p['review_id'])}" data-d=approve>Approve → publish</button>
<button data-id="{e(p['review_id'])}" data-d=reject class=rej>Reject</button></div></article>"""

    cards = "".join(card(p) for p in pend) or "<p class=muted>No candidates waiting for review.</p>"
    lt = e(str(latest.get("frame_time", "—"))[:16])
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SmokeWatch review</title>
<style>
:root{{--bg:#0f1418;--fg:#e8edf0;--mut:#93a1ab;--card:#182027;--acc:#ff8a3d;--ok:#3ecf8e;--bad:#ef5b5b}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f8;--fg:#14191d;--mut:#5b6770;--card:#fff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:960px;margin:auto;padding:16px}}h1{{font-size:1.3rem;margin:.2rem 0}}
.muted,.conf{{color:var(--mut)}}.banner{{border-left:4px solid var(--acc);padding:8px 12px;background:var(--card)}}
.grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}}
.card{{background:var(--card);border-radius:10px;padding:12px}}.card img,.latest img{{width:100%;border-radius:6px}}
.card h3{{font-size:1rem;margin:.5rem 0 .1rem}}.why{{font-size:.88rem}}ul{{padding-left:1.1rem;margin:.3rem 0}}
.btns{{display:flex;gap:8px}}button{{flex:1;padding:10px;border:0;border-radius:6px;background:var(--ok);color:#06130d;font-weight:600}}
button.rej{{background:var(--bad);color:#fff}}input{{width:100%;padding:9px;box-sizing:border-box;margin:6px 0 14px}}
a{{color:var(--acc)}}
</style></head><body><main>
<h1>Kahu Ola SmokeWatch — human review</h1>
<p class=banner>The agent can only <b>queue</b> candidates. Nothing reaches the public layer until a
reviewer approves it here. Public layer: <a href="public/smoke.geojson">smoke.geojson</a>.<br>
<small>{e(DISCLAIMER_VI)} · {e(DISCLAIMER_EN)}</small></p>
<section class=latest><h2>Latest scan · {lt}Z</h2>
<p class=muted>{latest.get('candidates', 0)} candidate(s), background from {latest.get('background_scans', 0)} scans,
{'day' if latest.get('daytime') else 'night (thermal only)'} · <a href="api/latest">summary JSON</a></p>
<img src="{e(store.url('latest/overlay.png'))}" alt="latest GOES-18 overlay" loading=lazy></section>
<h2>Pending review ({len(pend)})</h2>
<label>Reviewer token <input id=tok type=password autocomplete=off></label>
<div class=grid>{cards}</div></main>
<script>
const tok=document.getElementById('tok');
try{{tok.value=sessionStorage.getItem('swtok')||''}}catch(e){{}}
document.querySelectorAll('button[data-id]').forEach(b=>b.onclick=async()=>{{
  try{{sessionStorage.setItem('swtok',tok.value)}}catch(e){{}}
  b.disabled=true;
  const r=await fetch('review',{{method:'POST',headers:{{'content-type':'application/json'}},
    body:JSON.stringify({{review_id:b.dataset.id,decision:b.dataset.d,token:tok.value}})}});
  const j=await r.json();
  b.closest('.card').querySelector('.btns').textContent=r.ok?('→ '+j.status):('error: '+j.error);
}});
</script></body></html>"""
