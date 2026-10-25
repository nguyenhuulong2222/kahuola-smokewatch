"""Warm the .npz frame cache for every scan eval/run_eval.py will touch.

  python eval/prefetch.py [--only lahaina_pm,olinda] [--workers 6]

Per target scan t: t, the two earlier scans (track_plume) and the ±5 min scans on each
background day. Byte-range reads are latency-bound, so several processes help; HDF5 is
not thread-safe, hence processes rather than threads.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml  # noqa: E402

from smokewatch import fire, goes  # noqa: E402

CASES = os.path.join(os.path.dirname(__file__), "cases.yaml")


def parse_t(s) -> dt.datetime:
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load_cases(path=CASES) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def target_scans(case: dict, step_min: int = 5, horizon_min: int | None = None) -> list[str]:
    """Scan keys an event window (every scan) or a negative case (given frames) runs on."""
    if "window_utc" in case:
        t0, t1 = map(parse_t, case["window_utc"])
        if horizon_min:
            t1 = min(t1, t0 + dt.timedelta(minutes=horizon_min))
        keys = goes.keys_between(t0, t1)
        # PACUS is 5-min; thin to step_min in case a mode change gives more scans
        out, last = [], None
        for k in keys:
            if last is None or (goes.scan_start(k) - last) >= dt.timedelta(minutes=step_min - 1):
                out.append(k)
                last = goes.scan_start(k)
        return out
    return [goes.nearest_key(parse_t(t)) for t in case["frames_utc"]]


def needed_keys(targets: list[str]) -> list[str]:
    need: dict[str, None] = {}
    for k in targets:
        t = goes.scan_start(k)
        for tt in [t, t - dt.timedelta(minutes=5), t - dt.timedelta(minutes=10)] + fire.reference_times(t):
            try:
                need[goes.nearest_key(tt)] = None
            except FileNotFoundError:
                pass
    return list(need)


def _load(key, cache):
    t0 = time.time()
    goes.load_key(key, cache)
    return key, time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--horizon-min", type=int, default=None, help="only the first N min of each window")
    a = ap.parse_args(argv)
    cfg = load_cases()
    cache = cfg.get("frames_dir", ".cache/goes")
    only = set(filter(None, a.only.split(",")))
    order = [x for x in a.only.split(",") if x]
    cases = [c for c in cfg["events"] + cfg["negatives"] if not only or c["id"] in only]
    cases.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 0)
    keys: dict[str, None] = {}
    for c in cases:
        ks = needed_keys(target_scans(c, cfg.get("step_min", 5), a.horizon_min))
        print(f"{c['id']}: {len(ks)} scans", flush=True)
        keys.update(dict.fromkeys(ks))
    todo = [k for k in keys
            if not os.path.exists(os.path.join(cache, os.path.basename(k)[:-3] + ".npz"))]
    print(f"total {len(keys)} scans, {len(todo)} to fetch", flush=True)
    done = 0
    with ProcessPoolExecutor(a.workers) as ex:   # submitted in priority order
        futs = {ex.submit(_load, k, cache): k for k in todo}
        for f in as_completed(futs):
            done += 1
            try:
                k, s = f.result()
                print(f"[{done}/{len(todo)}] {s:5.1f}s {os.path.basename(k)[:48]}", flush=True)
            except Exception as e:
                print(f"[{done}/{len(todo)}] FAILED {os.path.basename(futs[f])}: {e}", flush=True)


if __name__ == "__main__":
    main()
