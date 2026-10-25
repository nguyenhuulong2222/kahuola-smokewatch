"""Run RulePolicy and an LLM policy (bedrock | anthropic) on the same replayed scans.

  ANTHROPIC_KEY_PARAM=/smokewatch/anthropic_api_key FIRMS_ARCHIVE_DIR=.cache/firms \\
  python scripts/compare_policies.py --llm anthropic --time 2023-08-09T01:21Z ...

  BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-5-5 AWS_REGION=us-east-1 \\
  FIRMS_ARCHIVE_DIR=.cache/firms python scripts/compare_policies.py \\
      --time 2023-08-09T01:21Z --time 2023-08-09T01:36Z

Both policies see identical frames, background and FIRMS archive; each run gets its own
LocalStore so rechecks cannot leak between them. Writes eval/policy_compare.json.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.run_local import parse_time  # noqa: E402
from smokewatch import agent, fire, goes  # noqa: E402
from smokewatch.store import LocalStore  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", action="append", required=True)
    ap.add_argument("--cache", default="./.cache/goes")
    ap.add_argument("--out", default=None)
    ap.add_argument("--llm", choices=["bedrock", "anthropic"], default="bedrock")
    a = ap.parse_args(argv)
    a.out = a.out or f"eval/policy_compare_{a.llm}.json"
    llm_cls = {"bedrock": agent.BedrockPolicy, "anthropic": agent.AnthropicPolicy}[a.llm]

    @functools.lru_cache(maxsize=64)
    def source(t):
        return goes.fetch_frame(t, "C", a.cache)

    rows = []
    for ts in a.time:
        frame = source(parse_time(ts))
        bg = fire.fetch_background(frame.time, source)
        res = {}
        for pol in (agent.RulePolicy(), llm_cls()):
            with tempfile.TemporaryDirectory() as d:
                t0 = time.time()
                s = agent.run_scan(frame, source, LocalStore(d), pol, background=bg)
                res[pol.name] = {
                    "seconds": round(time.time() - t0, 1),
                    "outcomes": {k: {"action": v["action"], "confidence": v.get("confidence"),
                                     "rationale": v["rationale"]} for k, v in ((str(k), v) for k, v in s["outcomes"].items())},
                    "tools": [f"{st['tool']}({st['args'].get('det_id')}" + (f",{st['args']['minutes_back']}min" if 'minutes_back' in st['args'] else '') + ")" for st in s["trace"] if "tool" in st],
                    "fallback": [st for st in s["trace"] if "error" in st and "tool" not in st],
                    "usage": getattr(pol, "usage", None), "trace": s["trace"]}
        cands = {str(d["det_id"]): d["hotspot"] for d in agent.Episode(
            frame, source, LocalStore(tempfile.mkdtemp()), [], bg).observation()["candidates"]}
        rows.append({"time": frame.time.isoformat(), "candidates": cands, **res})
        print(f"\n== {frame.time:%Y-%m-%d %H:%MZ}  ({len(cands)} candidates)")
        for k, h in cands.items():
            r, b = res["rules"]["outcomes"].get(k, {}), res[a.llm]["outcomes"].get(k, {})
            flag = "same" if r.get("action") == b.get("action") else "DIFF"
            print(f"  #{k} {h['centroid']} +{h['max_ddt_k']}K {h['volcanic'] or ''}")
            print(f"     rules  : {r.get('action')} {r.get('confidence') or ''}")
            print(f"     {a.llm:7s}: {b.get('action')} {b.get('confidence') or ''}  [{flag}]")
            print(f"       \"{b.get('rationale')}\"")
        L = res[a.llm]
        u = L["usage"] or []
        print(f"  {a.llm}: {len(u)} calls, in {sum(x['input_tokens'] or 0 for x in u)} / out "
              f"{sum(x['output_tokens'] or 0 for x in u)} tokens, LLM {sum(x['latency_ms'] or 0 for x in u) / 1000:.1f} s, {L['seconds']} s, "
              f"tools {L['tools']}" + (f", FALLBACK {L['fallback']}" if L["fallback"] else ""))
    with open(a.out, "w") as f:
        json.dump({"llm": a.llm, "model_id": getattr(llm_cls, "model_id", None) or os.environ.get(
            "BEDROCK_MODEL_ID" if a.llm == "bedrock" else "ANTHROPIC_MODEL_ID", "claude-sonnet-5-5"), "scans": rows}, f, indent=2, default=str)
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
