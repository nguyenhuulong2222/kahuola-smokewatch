"""Perception → decision → action loop.

The agent sees one GOES frame, runs OpenCV detection, and for every candidate plume
must decide what to do next. Visual evidence drives the next tool call:
  * a candidate triggers ACTIVE PERCEPTION: fetch earlier frames, run optical flow;
  * a persistent, coherent plume triggers a FIRMS cross-check;
  * the combined evidence leads to dismiss / schedule_recheck / request_human_review.

Safety rail (enforced in code, not prompt): the agent can never publish. A plume
reaches the public map only after a human approves it at the review endpoint, and
review requires that motion tracking was run for that plume.

Two interchangeable policies: `RulePolicy` (deterministic, used for eval + fallback)
and `BedrockPolicy` (Claude on Amazon Bedrock via the Converse tool-use API).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from typing import Callable

import cv2
import numpy as np

from . import firms, vision
from .goes import Frame

FrameSource = Callable[[dt.datetime], Frame]

TOOL_SPECS = [
    {"name": "track_plume",
     "description": "Active perception. Fetch the GOES frame `minutes_back` minutes earlier, "
                    "run OpenCV DIS optical flow and mask warping, and report whether candidate "
                    "`det_id` is persistent (IoU) and moves coherently. Call before any review.",
     "params": {"det_id": "integer", "minutes_back": "integer (10-30)"}},
    {"name": "query_firms",
     "description": "Look up NASA FIRMS satellite fire detections near candidate `det_id`.",
     "params": {"det_id": "integer"}},
    {"name": "request_human_review",
     "description": "Queue candidate for a human reviewer with overlay image and evidence. "
                    "Only a human can publish to the public Kahu Ola smoke layer.",
     "params": {"det_id": "integer", "confidence": "number 0-1", "rationale": "string"}},
    {"name": "schedule_recheck",
     "description": "Evidence is ambiguous; re-examine this location on the next scan.",
     "params": {"det_id": "integer", "rationale": "string"}},
    {"name": "dismiss",
     "description": "Candidate is not smoke (cloud, glint, vog, artefact).",
     "params": {"det_id": "integer", "rationale": "string"}},
]
TERMINAL = {"request_human_review", "schedule_recheck", "dismiss"}


class Episode:
    """State + tool implementations for one scan. Every call is traced."""

    def __init__(self, frame: Frame, frame_source: FrameSource, store, prior_rechecks=None):
        self.id = frame.time.strftime("%Y%m%dT%H%MZ") + "-" + uuid.uuid4().hex[:6]
        self.frame = frame
        self.src = frame_source
        self.store = store
        self.dets, self.mask, self.score = vision.detect(frame)
        self.fire = vision.hotspot_mask(frame)
        self.evidence = {d.id: {"vision": self._vision_summary(d)} for d in self.dets}
        self.outcome: dict[int, dict] = {}
        self.trace: list[dict] = []
        self._link_rechecks(prior_rechecks or [])

    # ---------------- observation given to the policy
    def _vision_summary(self, d):
        return {"area_km2": d.area_km2, "mean_score": d.mean_score,
                "centroid": [round(c, 3) for c in d.centroid],
                "goes_hotspot_px": d.hotspots_px, "nearest_hotspot_km": d.nearest_hotspot_km}

    def _link_rechecks(self, rechecks):
        for d in self.dets:
            lon, lat = d.centroid
            hits = [r for r in rechecks
                    if abs(r["centroid"][0] - lon) < 0.3 and abs(r["centroid"][1] - lat) < 0.3]
            if hits:
                self.evidence[d.id]["seen_previous_scan"] = len(hits)

    def observation(self) -> dict:
        return {"frame_time_utc": self.frame.time.isoformat(),
                "daytime": vision.is_daytime(self.frame),
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
        prev = self.src(self.frame.time - dt.timedelta(minutes=minutes_back))
        _, prev_mask, _ = vision.detect(prev)
        m = vision.track_motion(prev, self.frame, prev_mask, self.mask, d)
        d.motion = m
        self.evidence[d.id]["motion"] = m
        return m

    def t_query_firms(self, det_id):
        d = self._det(det_id)
        r = firms.query(d.bbox, self.frame.time)
        self.evidence[d.id]["firms"] = {"count": r["count"], "sources": r["sources"],
                                        "error": r["error"]}
        return self.evidence[d.id]["firms"]

    def t_request_human_review(self, det_id, confidence, rationale):
        d = self._det(det_id)
        if "motion" not in self.evidence[d.id]:   # safety rail
            raise PermissionError("track_plume must run before requesting review")
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
    v = ev["vision"]
    m = ev.get("motion", {})
    fire = (v["goes_hotspot_px"] > 0) or (ev.get("firms", {}).get("count", 0) > 0)
    c = (0.30 * min(v["mean_score"] / 0.8, 1.0)
         + 0.25 * float(m.get("persistent", False))
         + 0.15 * m.get("coherence", 0.0)
         + 0.30 * float(fire))
    if ev.get("seen_previous_scan"):
        c += 0.10
    return round(min(c, 1.0), 2)


class RulePolicy:
    name = "rules"

    def run(self, ep: Episode, max_steps=40):
        for d in [d for d in ep.dets if d.id not in ep.outcome]:
            m = ep.call("track_plume", {"det_id": d.id, "minutes_back": 10}, self.name)
            if "error" in m:
                ep.call("schedule_recheck", {"det_id": d.id,
                                             "rationale": f"tracking failed: {m['error']}"}, self.name)
                continue
            if not m["persistent"]:
                ep.call("dismiss", {"det_id": d.id, "rationale":
                        f"not persistent over {m.get('minutes')} min (IoU {m['iou']}) — likely cloud/glint"},
                        self.name)
                continue
            ep.call("query_firms", {"det_id": d.id}, self.name)
            conf = evidence_confidence(ep.evidence[d.id])
            if conf >= 0.6:
                ep.call("request_human_review", {"det_id": d.id, "confidence": conf,
                        "rationale": "persistent coherent plume with fire evidence"}, self.name)
            elif conf >= 0.35:
                ep.call("schedule_recheck", {"det_id": d.id,
                        "rationale": f"confidence {conf}: plume persists but no fire evidence"},
                        self.name)
            else:
                ep.call("dismiss", {"det_id": d.id, "rationale": f"confidence {conf}"}, self.name)


SYSTEM_PROMPT = """You are the triage agent for Kahu Ola SmokeWatch, a public wildfire-smoke
layer for Hawaiʻi. Each candidate plume came from OpenCV analysis of a GOES-18 frame.
For EVERY unresolved candidate, gather evidence with tools, then resolve it with exactly one of
request_human_review, schedule_recheck or dismiss.

Rules:
- Always call track_plume before resolving a candidate as review. Use minutes_back 10; if IoU is
  borderline (0.2-0.4) you may track again at 20.
- Non-persistent features (IoU < 0.3) are usually clouds or sun glint — dismiss.
- Persistent + coherent plumes: call query_firms. Fire evidence (FIRMS count > 0 or GOES hotspot
  pixels > 0) plus persistence → request_human_review.
- Persistent but no fire evidence → schedule_recheck (could be vog from Kīlauea, haze, or a fire
  not yet detected). Vog sits downwind (southwest) of Hawaiʻi Island and does not have a point source.
- You cannot publish. Never invent numbers; cite the tool outputs in your rationale (≤ 30 words).
"""


class BedrockPolicy:
    name = "bedrock"

    def __init__(self, model_id: str | None = None, region: str | None = None):
        import boto3
        self.model_id = model_id or os.environ["BEDROCK_MODEL_ID"]
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
                                        inferenceConfig={"maxTokens": 1024, "temperature": 0})
            out = resp["output"]["message"]
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


def run_scan(frame: Frame, frame_source: FrameSource, store, policy=None) -> dict:
    prior = [store.get_json(k) for k in store.list("rechecks/")[-50:]]
    ep = Episode(frame, frame_source, store, [p for p in prior if p])
    policy = policy or make_policy()
    if ep.dets and vision.is_daytime(frame):
        try:
            policy.run(ep)
        except Exception as ex:   # LLM outage must not stop the safety pipeline
            ep.trace.append({"step": len(ep.trace) + 1, "actor": policy.name,
                             "error": f"{type(ex).__name__}: {ex}", "fallback": "rules"})
            RulePolicy().run(ep)   # resolves only what is still open
    summary = {
        "episode": ep.id, "frame": frame.key, "frame_time": frame.time.isoformat(),
        "policy": policy.name, "daytime": vision.is_daytime(frame),
        "candidates": len(ep.dets), "outcomes": ep.outcome, "evidence": ep.evidence,
        "trace": ep.trace,
    }
    store.put_json(f"traces/{ep.id}.json", summary)
    ok, png = cv2.imencode(".png", vision.render_overlay(frame, ep.mask, ep.dets, ep.fire))
    store.put_bytes("latest/overlay.png", png.tobytes(), "image/png")
    store.put_json("latest/summary.json", {k: v for k, v in summary.items() if k != "trace"})
    return summary
