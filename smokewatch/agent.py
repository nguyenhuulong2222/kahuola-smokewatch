"""Perception → decision → action loop, anchored on fire.

The agent sees one GOES frame. Candidates are THERMAL ANOMALIES (3.9 µm vs the same pixel
on the previous days, see fire.py); smoke is supporting evidence. For every candidate the
agent must decide what to do next, and the evidence drives the next tool call:
  * hotspot -> ACTIVE PERCEPTION (track_plume): fetch the 2 earlier scans, check the hotspot
    is still there, look for a smoke plume attached to it and track it with optical flow;
  * a persistent hotspot -> FIRMS cross-check (VIIRS/MODIS passes before this scan only);
  * the combined evidence -> dismiss / schedule_recheck / request_human_review.

Safety rails (enforced in code, not prompt):
  * the agent can never publish; only a human at the review endpoint can;
  * review requires track_plume to have run and the hotspot to be present in ≥ 2 scans
    (a single-scan hotspot can only be re-checked);
  * candidates inside a volcanic exclusion zone (Kīlauea, Mauna Loa) can never be reviewed.

Two interchangeable policies: `RulePolicy` (deterministic, used for eval + fallback)
and `BedrockPolicy` (Claude on Amazon Bedrock via the Converse tool-use API).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
import uuid
from types import SimpleNamespace
from typing import Callable

import cv2
import numpy as np

from . import fire, firms, vision
from .goes import Frame

FrameSource = Callable[[dt.datetime], Frame]

TOOL_SPECS = [
    {"name": "track_plume",
     "description": "Active perception. Fetch the GOES scans up to `minutes_back` minutes earlier "
                    "(every 5 min) and report in how many scans hotspot `det_id` is present; by day, "
                    "find the smoke plume attached to it, its downwind bearing, and track it with "
                    "OpenCV DIS optical flow (IoU, coherence, speed). Required before any review.",
     "params": {"det_id": "integer", "minutes_back": "integer (5-30)"}},
    {"name": "query_firms",
     "description": "Look up NASA FIRMS VIIRS/MODIS fire detections within ~5 km of `det_id` acquired "
                    "in the 24 h BEFORE this scan. Polar orbiters pass only a few times a day, so "
                    "zero is common for a new fire.",
     "params": {"det_id": "integer"}},
    {"name": "request_human_review",
     "description": "Queue candidate for a human reviewer with overlay image and evidence. "
                    "Only a human can publish to the public Kahu Ola smoke layer. Refused for "
                    "volcanic candidates and for hotspots seen in fewer than 2 scans.",
     "params": {"det_id": "integer", "confidence": "number 0-1", "rationale": "string"}},
    {"name": "schedule_recheck",
     "description": "Evidence is not yet sufficient; re-examine this location on the next scan.",
     "params": {"det_id": "integer", "rationale": "string"}},
    {"name": "dismiss",
     "description": "Candidate is not a wildfire (volcanic heat source, artefact).",
     "params": {"det_id": "integer", "rationale": "string"}},
]
TERMINAL = {"request_human_review", "schedule_recheck", "dismiss"}
MIN_HOTSPOT_SCANS = 2


def _hhmm(t: dt.datetime) -> str:
    return t.strftime("%H:%MZ")


class Episode:
    """State + tool implementations for one scan. Every call is traced."""

    def __init__(self, frame: Frame, frame_source: FrameSource, store, prior_rechecks=None,
                 background: fire.Background | None = None, fetch_background: bool = True):
        self.id = frame.time.strftime("%Y%m%dT%H%MZ") + "-" + uuid.uuid4().hex[:6]
        self.frame = frame
        self.src = frame_source
        self.store = store
        self.trace: list[dict] = []
        self.errors: list[str] = []
        self.bg = background
        if self.bg is None and not fetch_background:
            self.errors.append("background: no reference scans cached yet (bootstrapping)")
        elif self.bg is None:
            try:
                self.bg = fire.fetch_background(frame.time, frame_source)
            except Exception as ex:   # no background -> no anomaly test; scan still completes
                self.errors.append(f"background: {type(ex).__name__}: {ex}")
        if self.bg is not None and self.bg.n_refs < fire.FP["bg_min_refs"]:
            self.errors.append(f"background bootstrapping: {self.bg.n_refs}/{len(fire.reference_times(frame.time))} "
                               f"reference scans (< {fire.FP['bg_min_refs']}); detection skipped this scan")
        if self.bg is not None and self.bg.n_refs >= fire.FP["bg_min_refs"]:
            self.dets, self.fire, self.mask = fire.detect_fires(frame, self.bg)
        else:
            self.dets, self.fire, self.mask = [], np.zeros(frame.shape, np.uint8), np.zeros(frame.shape, np.uint8)
        self.evidence = {d.id: {"hotspot": d.summary()} for d in self.dets}
        self.outcome: dict[int, dict] = {}
        self._link_rechecks(prior_rechecks or [])

    # ---------------- observation given to the policy
    def _link_rechecks(self, rechecks):
        for d in self.dets:
            lon, lat = d.centroid
            hits = [r for r in rechecks
                    if abs(r["centroid"][0] - lon) < 0.05 and abs(r["centroid"][1] - lat) < 0.05
                    and r.get("time", "") < self.frame.time.isoformat()]
            if hits:
                self.evidence[d.id]["seen_previous_scan"] = len(hits)

    def observation(self) -> dict:
        return {"frame_time_utc": self.frame.time.isoformat(),
                "daytime": vision.is_daytime(self.frame),
                "background_scans": self.bg.n_refs if self.bg else 0,
                "candidates": [{"det_id": i, **e} for i, e in self.evidence.items()],
                "unresolved": [d.id for d in self.dets if d.id not in self.outcome]}

    # ---------------- tools
    def call(self, name: str, args: dict, actor: str) -> dict:
        t0 = time.time()
        try:
            fn = getattr(self, f"t_{name}")
            result = fn(**args)
        except Exception as ex:
            result = {"error": f"{type(ex).__name__}: {ex}"}
        self.trace.append({"step": len(self.trace) + 1, "actor": actor, "tool": name, "args": args,
                           "result": result, "ms": int((time.time() - t0) * 1000)})
        return result

    def _det(self, det_id):
        det_id = int(det_id)
        for d in self.dets:
            if d.id == det_id:
                if det_id in self.outcome:
                    raise ValueError(f"det {det_id} already resolved: {self.outcome[det_id]['action']}")
                return d
        raise ValueError(f"unknown det_id {det_id}")

    def t_track_plume(self, det_id, minutes_back=10):
        d = self._det(det_id)
        minutes_back = int(min(max(int(minutes_back), 5), 30))
        scans, seen = [], {self.frame.key}
        prev_frames = []
        for m in range(5, minutes_back + 1, 5):
            prev = self.src(self.frame.time - dt.timedelta(minutes=m))
            if prev.key in seen or prev.time >= self.frame.time:
                continue
            seen.add(prev.key)
            hit = fire.hotspot_present(fire.thermal_anomaly(prev, self.bg), d)
            scans.append({"time": _hhmm(prev.time), "hotspot": bool(hit)})
            prev_frames.append(prev)
        k = 1 + sum(s["hotspot"] for s in scans)
        span = round((self.frame.time - prev_frames[-1].time).total_seconds() / 60) if prev_frames else 0
        out = {"scans_checked": 1 + len(scans), "hotspot_scans": k,
               "persistent": k >= MIN_HOTSPOT_SCANS, "span_min": span,
               "scans": scans[::-1] + [{"time": _hhmm(self.frame.time), "hotspot": True}]}
        if not vision.is_daytime(self.frame):
            out["smoke"] = {"observable": False, "reason": "night: smoke not visible, thermal only"}
        elif not d.smoke:
            out["smoke"] = {"observable": True, "attached_plume": False}
        else:
            sm = {"observable": True, "attached_plume": True, **{k2: v for k2, v in d.smoke.items()
                                                                  if k2 != "pixel_bbox"}}
            if prev_frames:
                prev = prev_frames[0]
                mo = vision.track_motion(prev, self.frame, fire.smoke_mask(prev, self.bg), self.mask,
                                         SimpleNamespace(pixel_bbox=d.smoke["pixel_bbox"]))
                sm.update({"iou_vs_prev": mo["iou"], "coherence": mo["coherence"]})
                if "heading_deg" in mo:
                    mb = fire.compass_from_flow_heading(mo["heading_deg"])
                    sm.update({"speed_kmh": mo["speed_kmh"], "motion_bearing_deg": round(mb),
                               "downwind_consistent": fire.angle_diff(mb, d.smoke["bearing_deg"]) < 60
                               and mo["speed_kmh"] > 3})
            out["smoke"] = sm
        self.evidence[d.id]["track"] = out
        return out

    def t_query_firms(self, det_id):
        d = self._det(det_id)
        r = firms.query(d.bbox, self.frame.time, days=1, pad_deg=0.05)
        lon, lat = d.centroid
        near = None
        if r["points"]:
            near = min(math.hypot((p["lon"] - lon) * 111.32 * math.cos(math.radians(lat)),
                                  (p["lat"] - lat) * 111.32) for p in r["points"])
        self.evidence[d.id]["firms"] = {"count": r["count"], "sources": r["sources"],
                                        "latest": r.get("latest"),
                                        "nearest_km": round(near, 1) if near is not None else None,
                                        "error": r["error"]}
        return self.evidence[d.id]["firms"]

    def t_request_human_review(self, det_id, confidence, rationale):
        d = self._det(det_id)
        tr = self.evidence[d.id].get("track")
        if tr is None:   # safety rails
            raise PermissionError("track_plume must run before requesting review")
        if d.volcanic:
            raise PermissionError(f"candidate is inside the volcanic exclusion zone ({d.volcanic})")
        if tr["hotspot_scans"] < MIN_HOTSPOT_SCANS:
            raise PermissionError(f"hotspot seen in {tr['hotspot_scans']} scan(s); "
                                  f"need ≥ {MIN_HOTSPOT_SCANS} — schedule_recheck instead")
        rid = f"{self.id}-{d.id}"
        overlay = vision.render_overlay(self.frame, self.mask, [d], self.fire)
        ok, png = cv2.imencode(".png", overlay)
        self.store.put_bytes(f"reviews/img/{rid}.png", png.tobytes(), "image/png")
        feat = d.to_feature({"review_id": rid, "confidence": round(float(confidence), 2),
                             "rationale": rationale, "frame_time": self.frame.time.isoformat(),
                             "evidence": self.evidence[d.id], "status": "pending_review"})
        self.store.put_json(f"reviews/pending/{rid}.json", feat)
        self.outcome[d.id] = {"action": "request_human_review", "review_id": rid,
                              "confidence": confidence, "rationale": rationale}
        return {"queued": rid}

    def t_schedule_recheck(self, det_id, rationale):
        d = self._det(det_id)
        self.store.put_json(f"rechecks/{self.id}-{d.id}.json",
                            {"centroid": d.centroid, "time": self.frame.time.isoformat(),
                             "rationale": rationale})
        self.outcome[d.id] = {"action": "schedule_recheck", "rationale": rationale}
        return {"scheduled": True}

    def t_dismiss(self, det_id, rationale):
        d = self._det(det_id)
        self.outcome[d.id] = {"action": "dismiss", "rationale": rationale}
        return {"dismissed": True}


# ------------------------------------------------------------------ policies

def evidence_confidence(ev: dict) -> float:
    h, tr = ev["hotspot"], ev.get("track", {})
    if h.get("volcanic") or not tr.get("persistent"):
        return 0.0
    sm, fi = tr.get("smoke") or {}, ev.get("firms") or {}
    c = 0.45
    c += 0.15 * min((tr["hotspot_scans"] - 1) / 2, 1.0)        # 3/3 scans -> +0.15
    c += 0.10 * float(h["max_ddt_k"] >= 15)
    c += 0.10 * float(bool(sm.get("attached_plume")))
    c += 0.05 * float(bool(sm.get("downwind_consistent")))
    c += 0.15 * float(fi.get("count", 0) > 0)
    c += 0.05 * float(bool(ev.get("seen_previous_scan")))
    return round(min(c, 1.0), 2)


def explain(ev: dict) -> str:
    """Rationale built only from the evidence actually gathered for this candidate."""
    h = ev["hotspot"]
    lon, lat = h["centroid"]
    parts = [f"Hotspot at {lat:.3f}N {abs(lon):.3f}W: {h['hot_px']} px, Δ(3.9−11.2 µm) "
             f"+{h['max_ddt_k']:.1f} K vs same time on previous days"]
    if h.get("volcanic"):
        parts.append(f"inside {h['volcanic']} exclusion zone — volcanic heat, not wildfire")
        return "; ".join(parts)
    tr = ev.get("track")
    if tr:
        when = ", ".join(s["time"] for s in tr["scans"] if s["hotspot"])
        parts.append(f"present in {tr['hotspot_scans']}/{tr['scans_checked']} scans over "
                     f"{tr['span_min']} min ({when})")
        sm = tr.get("smoke") or {}
        if not sm.get("observable", True):
            parts.append("night — smoke not observable")
        elif sm.get("attached_plume"):
            s = (f"smoke plume {sm['area_km2']:.0f} km² extending {sm['length_km']:.0f} km "
                 f"toward {sm['bearing_deg']}°")
            if "motion_bearing_deg" in sm:
                s += (f", drifting {sm['motion_bearing_deg']}° at {sm['speed_kmh']:.0f} km/h"
                      f" ({'consistent' if sm.get('downwind_consistent') else 'not consistent'} with plume axis)")
            parts.append(s)
        else:
            parts.append("no smoke plume attached")
    fi = ev.get("firms")
    if fi is not None:
        if fi.get("error"):
            parts.append(f"FIRMS unavailable ({fi['error'][:40]})")
        elif fi["count"]:
            parts.append(f"FIRMS: {fi['count']} VIIRS/MODIS detections in prior 24 h, nearest "
                         f"{fi['nearest_km']} km, latest {fi['latest'][11:16]}Z")
        else:
            parts.append("FIRMS: no VIIRS/MODIS detection in prior 24 h (GOES ahead of polar passes)")
    if ev.get("seen_previous_scan"):
        parts.append(f"flagged on {ev['seen_previous_scan']} earlier scan(s)")
    return "; ".join(parts)


class RulePolicy:
    name = "rules"

    def run(self, ep: Episode, max_steps=40):
        for d in [d for d in ep.dets if d.id not in ep.outcome]:
            ev = ep.evidence[d.id]
            if d.volcanic:
                ep.call("dismiss", {"det_id": d.id, "rationale": explain(ev)}, self.name)
                continue
            t = ep.call("track_plume", {"det_id": d.id, "minutes_back": 10}, self.name)
            if "error" in t:
                ep.call("schedule_recheck", {"det_id": d.id,
                                             "rationale": f"tracking failed: {t['error']}"}, self.name)
                continue
            if not t["persistent"]:
                ep.call("schedule_recheck", {"det_id": d.id, "rationale":
                        explain(ev) + "; single-scan hotspot — wait for confirmation"}, self.name)
                continue
            ep.call("query_firms", {"det_id": d.id}, self.name)
            ep.call("request_human_review", {"det_id": d.id, "confidence": evidence_confidence(ev),
                                             "rationale": explain(ev)}, self.name)


SYSTEM_PROMPT = """You are the triage agent for Kahu Ola SmokeWatch, a public wildfire layer for
Hawaiʻi. Each candidate is a THERMAL ANOMALY in a GOES-18 scan: the 3.9 µm channel is much
warmer than the same pixel at the same time on the previous 3 days. For EVERY unresolved
candidate, gather evidence with tools, then resolve it with exactly one of
request_human_review, schedule_recheck or dismiss.

Rules:
- If hotspot.volcanic is set, the candidate is inside the Kīlauea/Mauna Loa exclusion zone:
  dismiss it, citing the zone. It can never be reviewed.
- Otherwise call track_plume (minutes_back 10) first. If hotspot_scans < 2, schedule_recheck
  (a one-scan hotspot may be noise; the next scan comes in 5 minutes).
- If the hotspot persists (≥ 2 scans), call query_firms, then request_human_review. A smoke
  plume extending downwind and FIRMS detections raise confidence; their absence does not block
  review (polar satellites pass only a few times a day; GOES scans every 5 minutes).
- You cannot publish. Never invent numbers: the rationale (≤ 40 words) must cite the tool
  outputs — ΔT, scans with hotspot, plume size/bearing, FIRMS count.
"""


class BedrockPolicy:
    name = "bedrock"

    def __init__(self, model_id: str | None = None, region: str | None = None):
        import boto3
        self.model_id = model_id or os.environ["BEDROCK_MODEL_ID"]
        self.usage: list[dict] = []   # per Converse call: tokens, stop reason, latency
        self.client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION"))

    @staticmethod
    def _tool_config():
        tools = []
        for t in TOOL_SPECS:
            props, req = {}, []
            for k, desc in t["params"].items():
                typ = desc.split()[0]
                props[k] = {"type": typ, "description": desc}
                req.append(k)
            tools.append({"toolSpec": {"name": t["name"], "description": t["description"],
                                       "inputSchema": {"json": {"type": "object", "properties": props,
                                                                "required": req}}}})
        return {"tools": tools}

    def run(self, ep: Episode, max_steps=24):
        msgs = [{"role": "user", "content": [{"text": "Observation:\n" + json.dumps(ep.observation())}]}]
        for _ in range(max_steps):
            if not ep.observation()["unresolved"]:
                return
            resp = self.client.converse(modelId=self.model_id, messages=msgs,
                                        system=[{"text": SYSTEM_PROMPT}],
                                        toolConfig=self._tool_config(),
                                        # current Claude models reject non-default sampling
                                        # params; adaptive thinking needs output room
                                        inferenceConfig={"maxTokens": 8000})
            out = resp["output"]["message"]   # appended unchanged: reasoning blocks must round-trip
            self.usage.append({k: resp.get("usage", {}).get(k) for k in ("inputTokens", "outputTokens")}
                              | {"stop": resp.get("stopReason"), "ms": resp.get("metrics", {}).get("latencyMs")})
            msgs.append(out)
            uses = [c["toolUse"] for c in out["content"] if "toolUse" in c]
            if not uses:
                msgs.append({"role": "user", "content": [{"text":
                             "Unresolved candidates remain: " + json.dumps(ep.observation()["unresolved"])}]})
                continue
            results = []
            for u in uses:
                r = ep.call(u["name"], u["input"], self.name)
                results.append({"toolResult": {"toolUseId": u["toolUseId"],
                                               "content": [{"json": r}],
                                               **({"status": "error"} if "error" in r else {})}})
            msgs.append({"role": "user", "content": results})
        _finish(ep)  # step budget exhausted: anything still open is re-checked next scan


def _finish(ep: Episode):
    for d in ep.dets:
        if d.id not in ep.outcome:
            ep.call("schedule_recheck", {"det_id": d.id, "rationale": "agent step budget exhausted"},
                    "fallback")


def make_policy(name: str | None = None):
    name = (name or os.environ.get("AGENT_POLICY", "rules")).lower()
    return BedrockPolicy() if name == "bedrock" else RulePolicy()


def run_scan(frame: Frame, frame_source: FrameSource, store, policy=None,
             background: fire.Background | None = None, fetch_background: bool = True) -> dict:
    """fetch_background=False: use only `background` (live mode fetches it under a deadline)."""
    prior = [store.get_json(k) for k in store.list("rechecks/")[-50:]]
    ep = Episode(frame, frame_source, store, [p for p in prior if p], background, fetch_background)
    policy = policy or make_policy()
    if ep.dets:   # thermal works day and night; smoke evidence is daytime-only
        try:
            policy.run(ep)
        except Exception as ex:   # LLM outage must not stop the safety pipeline
            ep.trace.append({"step": len(ep.trace) + 1, "actor": policy.name,
                             "error": f"{type(ex).__name__}: {ex}", "fallback": "rules"})
            RulePolicy().run(ep)   # resolves only what is still open
    summary = {
        "episode": ep.id, "frame": frame.key, "frame_time": frame.time.isoformat(),
        "policy": policy.name, "daytime": vision.is_daytime(frame),
        "background_scans": ep.bg.n_refs if ep.bg else 0, "errors": ep.errors,
        "anomaly_px": int(ep.fire.sum()),
        "candidates": len(ep.dets), "outcomes": ep.outcome, "evidence": ep.evidence,
        "trace": ep.trace,
    }
    store.put_json(f"traces/{ep.id}.json", summary)
    ok, png = cv2.imencode(".png", vision.render_overlay(frame, ep.mask, ep.dets, ep.fire))
    store.put_bytes("latest/overlay.png", png.tobytes(), "image/png")
    store.put_json("latest/summary.json", {k: v for k, v in summary.items() if k != "trace"})
    return summary
