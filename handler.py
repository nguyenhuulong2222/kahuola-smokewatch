"""AWS Lambda entry points (one container image, two functions).

scan(event)  EventBridge rate(10 minutes). Runs the fire-anchored detector + triage agent on
             the latest GOES-18 PACUS scan. Fire detection is thermal, so it runs day AND
             night (the Olinda fire started at 00:22 HST); smoke evidence is daytime-only.
             Replay: {"time": "2023-08-09T01:31Z"}.
http(event)  Lambda Function URL:
               GET  /, /archive           public page: case study, timeline viewer, live status
               GET  /review               reviewer page (HTML)
               GET  /api/latest           latest scan summary (JSON)
               GET  /api/trace/{episode}  full agent trace (JSON)
               GET  /api/featured         featured events + eval table
               GET  /api/days             HST days with stored scans
               GET  /api/day?date=        scan index of one HST day
               GET  /img/{key}            archive/, events/, static/ only: images 302 to a presigned
                                         S3 URL, .json returned inline (same origin for fetch)
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
import time

from smokewatch import agent, fire, goes, render
from smokewatch.store import default_store

CACHE = os.environ.get("GOES_CACHE", "/tmp/goes")
DISCLAIMER_VI = "Ước tính tự động từ vệ tinh, không thay thế cảnh báo chính thức"
DISCLAIMER_EN = ("Automated satellite estimate, human-reviewed. Not an official warning — "
                 "follow Maui Emergency Management / county alerts.")
_ID = re.compile(r"^[0-9A-Za-z-]{1,80}$")
_IMG = re.compile(r"^(archive|events|static)/[0-9A-Za-z_./-]{1,200}$")   # public prefixes only
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smokewatch", "site", "index.html")


# ------------------------------------------------------------------ scan

def _parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


SCAN_RESERVE_S = 120   # time kept back for detection, agent and writes (Bedrock can be slow)


class FrameCache:
    """Reprojected Hawaiʻi frames (~1.5 MB .npz): /tmp -> S3 bg/frames/ -> NOAA.

    Every frame fetched from NOAA is copied to S3, so the next scan — even on a cold Lambda —
    reuses it, and a day later today's scans are tomorrow's background. NOAA fetches stop at
    `deadline`; whatever is missing is fetched by the following scans (partial bootstrap)."""

    def __init__(self, store, cache_dir: str = CACHE):
        self.store, self.dir = store, cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _npz(self, key):
        return os.path.join(self.dir, os.path.basename(key)[:-3] + ".npz")

    @staticmethod
    def _s3key(key):
        return "bg/frames/" + os.path.basename(key)[:-3] + ".npz"

    def warm(self, keys: list[str], deadline: float) -> dict:
        from concurrent.futures import ThreadPoolExecutor
        keys = list(dict.fromkeys(keys))
        todo = [k for k in keys if not os.path.exists(self._npz(k))]

        def pull(k):
            b = self.store.get_bytes(self._s3key(k))
            if not b:
                return False
            with open(self._npz(k) + ".part", "wb") as f:
                f.write(b)
            os.replace(self._npz(k) + ".part", self._npz(k))
            return True

        with ThreadPoolExecutor(8) as ex:
            hits = list(ex.map(pull, todo))
        misses = [k for k, h in zip(todo, hits) if not h]
        fetched = []
        for i in range(0, len(misses), 8):           # priority order is preserved
            if time.monotonic() > deadline:
                break
            batch = misses[i:i + 8]
            goes.prefetch(batch, self.dir)            # parallel S3 GETs (in-region)
            for k in batch:
                if time.monotonic() > deadline:
                    break
                goes.load_key(k, self.dir)            # decode window -> .npz
                fetched.append(k)
        for f in os.listdir(self.dir):                # raw files left by a cut-off batch
            if f.endswith(".nc"):
                os.remove(os.path.join(self.dir, f))
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda k: self.store.put_bytes(self._s3key(k), open(self._npz(k), "rb").read(),
                                                       "application/octet-stream"), fetched))
        return {"wanted": len(keys), "tmp": len(keys) - len(todo), "s3": sum(hits),
                "noaa": len(fetched), "missing": len(misses) - len(fetched)}

    def cached(self, t: dt.datetime) -> goes.Frame:
        """Only frames already warmed; used for the background so it never blocks."""
        key = goes.nearest_key(t)
        if not os.path.exists(self._npz(key)):
            raise FileNotFoundError(key)
        return goes.load_key(key, self.dir)

    def source(self, t: dt.datetime) -> goes.Frame:
        """Any frame; fetches if needed (track_plume asks for the 2 earlier scans)."""
        key = goes.nearest_key(t)
        if not os.path.exists(self._npz(key)):
            self.warm([key], float("inf"))
        return goes.load_key(key, self.dir)


def scan(event, context=None):
    event = event or {}
    started = time.monotonic()
    budget = (context.get_remaining_time_in_millis() / 1000) if context else 300.0
    deadline = started + budget - SCAN_RESERVE_S
    when = _parse_time(event["time"]) if event.get("time") else dt.datetime.now(dt.timezone.utc)
    store = default_store()
    cache = FrameCache(store)

    key = goes.nearest_key(when)
    t0 = goes.scan_start(key)
    must = [key] + [goes.nearest_key(t0 - dt.timedelta(minutes=m)) for m in (5, 10)]
    refs = []
    for t in fire.reference_times(t0):
        try:
            refs.append(goes.nearest_key(t))
        except FileNotFoundError:
            pass
    stats = cache.warm(must, float("inf"))           # current + 2 earlier scans: required
    rs = cache.warm(refs, deadline)                   # background: as many as time allows
    stats = {k: stats[k] + rs[k] for k in stats}
    stats["fetch_s"] = round(time.monotonic() - started, 1)

    bg = None
    try:
        bg = fire.fetch_background(t0, cache.cached)
    except ValueError:
        pass                                          # Episode records "no background"
    frame = goes.load_key(key, CACHE)
    summary = agent.run_scan(frame, functools.lru_cache(maxsize=16)(cache.source), store,
                             background=bg, fetch_background=False)
    latest = store.get_json("latest/summary.json", {})
    latest["frame_cache"] = stats
    try:   # presentation only: an archive failure must never fail the scan
        rec = render.archive_scan(store, frame, bg, summary)
        latest["thumb"] = render.scan_prefix("archive", frame.time) + "overlay.webp"
        stats["archived_candidates"] = len(rec["candidates"])
    except Exception as ex:
        latest.setdefault("errors", []).append(f"archive: {type(ex).__name__}: {ex}")
    store.put_json("latest/summary.json", latest)
    out = {k: summary[k] for k in ("episode", "frame_time", "daytime", "background_scans",
                                   "candidates", "outcomes", "errors")}
    out["frame_cache"] = stats
    out["seconds"] = round(time.monotonic() - started, 1)
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
        if method == "GET" and path in ("/", "/archive"):
            return _resp(200, _site(), "text/html; charset=utf-8", {"cache-control": "max-age=300"})
        if method == "GET" and path == "/review":
            return _resp(200, review_page(store), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/featured":
            return _resp(200, store.get_json("events/featured.json") or {"featured": []},
                         extra={"cache-control": "max-age=300"})
        if method == "GET" and path == "/api/days":
            return _resp(200, {"days": list_days(store)})
        if method == "GET" and path == "/api/day":
            day = (event.get("queryStringParameters") or {}).get("date", "")
            if not _DATE.match(day):
                return _resp(400, {"error": "date must be YYYY-MM-DD"})
            idx = day_index(store, day)
            return _resp(200, idx) if idx else _resp(404, {"error": "no stored scans for this day"})
        if method == "GET" and path.startswith("/img/"):
            key = path[len("/img/"):]
            if not _IMG.match(key) or ".." in key:
                return _resp(400, {"error": "bad key"})
            if key.endswith(".json"):   # read by fetch(): a cross-origin S3 redirect fails CORS
                b = store.get_bytes(key)
                return (_resp(200, b.decode(), extra={"cache-control": "max-age=3600"}) if b
                        else _resp(404, {"error": "not found"}))
            return _resp(302, "", "text/plain", {"location": store.url(key, 7200),
                                                  "cache-control": "max-age=3600"})
        if method == "GET" and path == "/api/latest":
            return _resp(200, store.get_json("latest/summary.json", {}))
        if method == "GET" and path.startswith("/api/trace/"):
            eid = path.rsplit("/", 1)[-1]
            if not _ID.match(eid):
                return _resp(400, {"error": "bad id"})
            t = store.get_json(f"traces/{eid}.json") or store.get_json(f"events/traces/{eid}.json")
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


@functools.lru_cache(maxsize=1)
def _site() -> str:
    with open(_SITE, encoding="utf-8") as f:
        return f.read()


def list_days(store) -> list[str]:
    days = set()
    for root in ("archive", "events"):
        for k in store.list(f"{root}/index/"):
            d = k.rsplit("/", 1)[-1].removesuffix(".json")
            if _DATE.match(d):
                days.add(d)
    return sorted(days)


def day_index(store, day: str) -> dict | None:
    """Live archive first (complete day); events/ holds featured and reviewed scans."""
    live, ev = store.get_json(f"archive/index/{day}.json"), store.get_json(f"events/index/{day}.json")
    if live and ev:   # a reviewed live scan is in both: keep the live entry
        seen = {s["t"] for s in live["scans"]}
        live["scans"] = sorted(live["scans"] + [s for s in ev["scans"] if s["t"] not in seen],
                               key=lambda s: s["time"])
    return live or ev


def _pending(store, limit=30) -> list[dict]:
    out = []
    for k in store.list("reviews/pending/")[-limit:][::-1]:
        f = store.get_json(k)
        if f:
            p = f["properties"]
            out.append({**p, "img": store.url(f"reviews/img/{p['review_id']}.png")})
    return out


HST = dt.timezone(dt.timedelta(hours=-10), "HST")


def _when(iso: str | None) -> str:
    """'2026-10-25 06:51 UTC · 20:51 HST (Oct 24)' — frame times in both zones."""
    if not iso:
        return ""
    t = _parse_time(iso)
    h = t.astimezone(HST)
    return f"{t:%Y-%m-%d %H:%M} UTC · {h:%H:%M} HST ({h:%b %d})"


def _overlay_html(store, key: str, alt: str) -> str:
    if not store.exists(key):
        return '<div class=noimg>No overlay yet</div>'
    return (f'<img src="{html.escape(store.url(key))}" alt="{html.escape(alt)}" loading=lazy '
            f'onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),'
            f'{{className:\'noimg\',textContent:\'No overlay yet\'}}))">')


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
{_overlay_html(store, f"reviews/img/{p['review_id']}.png", f"overlay for {p['review_id']}")}
<h3>{lat:.3f}°N {abs(lon):.3f}°W</h3><p class=muted>{e(_when(p.get('frame_time')))}</p>
<p class=conf>confidence {p.get('confidence')}</p>
<ul>{''.join(f'<li>{e(x)}</li>' for x in facts)}</ul>
<p class=why>{e(p.get('rationale', ''))}</p>
<p><a href="api/trace/{e(p['review_id'].rsplit('-', 1)[0])}">agent trace</a></p>
<div class=btns><button data-id="{e(p['review_id'])}" data-d=approve>Approve → publish</button>
<button data-id="{e(p['review_id'])}" data-d=reject class=rej>Reject</button></div></article>"""

    cards = "".join(card(p) for p in pend) or "<p class=muted>No candidates waiting for review.</p>"
    if latest.get("frame_time"):
        fc = latest.get("frame_cache") or {}
        status = (f"{latest.get('candidates', 0)} candidate(s) · background from "
                  f"{latest.get('background_scans', 0)} reference scans · "
                  f"{'day' if latest.get('daytime') else 'night (3.9 µm thermal only)'}")
        if fc:
            status += (f" · frames: {fc.get('s3', 0)} from cache, {fc.get('noaa', 0)} from NOAA, "
                       f"{fc.get('missing', 0)} still to fetch")
        notes = "".join(f"<li>{e(x)}</li>" for x in latest.get("errors") or [])
        latest_html = (f"<h2>Latest scan</h2><p><b>{e(_when(latest['frame_time']))}</b></p>"
                       f"<p class=muted>{e(status)} · <a href=\"api/latest\">summary JSON</a></p>"
                       + (f"<ul class=warn>{notes}</ul>" if notes else "")
                       + _overlay_html(store, "latest/overlay.png", "latest GOES-18 overlay"))
    else:
        latest_html = ("<h2>Latest scan</h2><p class=muted>No scan has run yet. The scanner runs every "
                       "10 minutes; the first one also bootstraps the 3-day thermal background.</p>"
                       "<div class=noimg>No overlay yet</div>")
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
a{{color:var(--acc)}}.noimg{{display:grid;place-items:center;min-height:140px;border:1px dashed var(--mut);
border-radius:6px;color:var(--mut)}}.warn{{color:var(--acc);font-size:.9rem}}
</style></head><body><main>
<h1>Kahu Ola SmokeWatch — human review</h1>
<p class=banner>The agent can only <b>queue</b> candidates. Nothing reaches the public layer until a
reviewer approves it here. Public layer: <a href="public/smoke.geojson">smoke.geojson</a>.<br>
<small>{e(DISCLAIMER_VI)} · {e(DISCLAIMER_EN)}</small></p>
<section class=latest>{latest_html}</section>
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
