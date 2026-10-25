"""Run one SmokeWatch scan locally on real GOES-18 data.

  python scripts/run_local.py --time 2023-08-08T22:00Z --sector C

Fetches the frame nearest --time (and, via the agent's track_plume tool, the frame
10 min earlier), runs run_scan with RulePolicy into a LocalStore, prints a summary
and writes the overlay PNG to <out>/overlays/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from smokewatch import agent, goes, vision  # noqa: E402
from smokewatch.store import LocalStore  # noqa: E402


def parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def coverage(frame: goes.Frame) -> float:
    """Fraction of grid cells with valid C01 data (0 = bbox outside sector)."""
    return float(np.isfinite(frame.bands["C01"]).mean())


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", required=True, help="ISO time, UTC (e.g. 2023-08-08T22:00Z)")
    ap.add_argument("--sector", default="C", choices=["C", "F"])
    ap.add_argument("--out", default="./out")
    ap.add_argument("--cache", default="./.cache/goes")
    ap.add_argument("--policy", default="rules", choices=["rules", "bedrock"])
    a = ap.parse_args(argv)

    when = parse_time(a.time)

    @functools.lru_cache(maxsize=8)
    def source(t: dt.datetime) -> goes.Frame:
        return goes.fetch_frame(t, a.sector, a.cache)

    t0 = time.time()
    frame = source(when)
    t_fetch = time.time() - t0
    cov = coverage(frame)
    if cov < 0.5:
        raise SystemExit(f"sector {a.sector} covers only {cov:.0%} of the Hawaiʻi bbox — use --sector F")

    store = LocalStore(a.out)
    t1 = time.time()
    summary = agent.run_scan(frame, source, store, agent.make_policy(a.policy))
    t_scan = time.time() - t1

    os.makedirs(os.path.join(a.out, "overlays"), exist_ok=True)
    png = os.path.join(a.out, "overlays", f"{frame.time:%Y%m%dT%H%MZ}_{a.sector}.png")
    shutil.copy(os.path.join(a.out, "latest", "overlay.png"), png)

    print(f"frame     {frame.key.split('/')[-1]}")
    print(f"time      {frame.time.isoformat()}  daytime={summary['daytime']}  coverage={cov:.0%}")
    print(f"latency   fetch {t_fetch:.1f}s  scan {t_scan:.1f}s")
    print(f"detections {summary['candidates']}  hotspot_px {int(vision.hotspot_mask(frame).sum())}")
    for did, ev in summary["evidence"].items():
        v, m = ev["vision"], ev.get("motion", {})
        out = summary["outcomes"].get(did, {})
        print(f"  #{did} {v['area_km2']:>8} km² score {v['mean_score']:.2f} at {v['centroid']} "
              f"hot {v['goes_hotspot_px']} iou {m.get('iou')} coh {m.get('coherence')} "
              f"-> {out.get('action')}")
    print(f"overlay   {png}")
    print(f"trace     {os.path.join(a.out, 'traces', summary['episode'] + '.json')}")
    return summary


if __name__ == "__main__":
    main()
