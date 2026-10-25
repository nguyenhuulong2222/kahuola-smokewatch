# Agent trace example — Lahaina, 9 Aug 2023 (afternoon fire)

Real replay of GOES-18 scans with `AgentPolicy=anthropic` (Claude Sonnet 5.5, Messages API
tool use). Source: `eval/policy_compare_anthropic.json`, produced by

```
ANTHROPIC_KEY_PARAM=/smokewatch/anthropic_api_key FIRMS_ARCHIVE_DIR=.cache/firms \
  python scripts/compare_policies.py --llm anthropic \
  --time 2023-08-09T01:21Z --time 2023-08-09T01:36Z --time 2023-08-08T10:31Z --time 2023-08-08T11:21Z
```

Only candidate #1 (Lahaina, 20.88 N 156.67 W) is shown. The same scans also held the
Olinda/Kula front and the Mauna Kea Resort fire, which the agent handled in the same calls.
Official first report: 00:55Z.

## Scan 01:21Z — a new hotspot, seen once: re-check, do not alert

Perception (OpenCV) hands the agent a candidate with no history:

```json
{"id": 1, "centroid": [-156.67, 20.88], "hot_px": 2,
 "max_ddt_k": 12.1, "max_d7_k": 15.7, "max_t7_k": 326.9, "volcanic": null}
```

| Step | Actor | Call | Result |
|---|---|---|---|
| 1 | Claude (2 042 in / 181 out tokens, 2.3 s) | `track_plume(det_id=1, minutes_back=10)` | `hotspot_scans: 1` — absent at 01:11Z and 01:16Z, present at 01:21Z; `persistent: false`; smoke observable, no attached plume |
| 2 | Claude (2 531 / 176, 2.2 s) | `schedule_recheck(1, "Hotspot in 1 of 3 scans (only 01:21Z), ΔT 12.1 K, no attached plume. Possible noise; recheck next scan.")` | `scheduled: true` |

A +12 K anomaly is strong, but one scan can be noise. The agent waited. It could not have
done otherwise: `request_human_review` is refused in code for a hotspot seen in fewer than
2 scans. The refusal would come back to the model as a tool error.

## Scan 01:36Z — the hotspot persists: queue for a human

| Step | Actor | Call | Result |
|---|---|---|---|
| 1 | Claude (2 175 / 219, 1.9 s) | `track_plume(1, 10)` | `hotspot_scans: 3` (01:26Z, 01:31Z, 01:36Z), `persistent: true`; no attached plume |
| 2 | Claude (2 853 / 163, 1.7 s) | `query_firms(1)` | `count: 0` — no VIIRS/MODIS detection yet (first polar detection: MODIS Terra, 07:50Z) |
| 3 | Claude (3 322 / 652, 4.7 s) | `request_human_review(1, confidence=0.4, "Hotspot in 3/3 scans (10 min), ΔT 10.5 K, d7 13.4 K, 2 px, 3.9 µm 324.3 K. No attached plume, FIRMS count 0. Persistent but weakly corroborated.")` | `queued: 20230809T0136Z-c9e62d-1` |

The review is queued **41 minutes after the official first report (00:55Z)** and about
**6 h before the first polar-orbiter detection** (MODIS Terra, 07:50Z). The reviewer sees the
overlay and this rationale. Only the reviewer's `POST /review` can publish to the public layer.

## What this shows — and what it does not

* The agent chooses its next perception step from the evidence. It calls `track_plume`
  before deciding and `query_firms` only once the hotspot persists. Every number in its
  rationale comes from a tool result.
* The confidence value (0.4 here) is the model's own estimate. It is shown to the reviewer
  and is **not** a gate. The decision is bounded by code rails: `track_plume` must have run,
  the hotspot must be present in ≥ 2 scans, and it must lie outside the volcanic zone.
* On this scan the LLM made the same decisions as the deterministic RulePolicy. On real
  false positives (vog days, Kauaʻi) it also requested review, like the rules did — see
  REPORT.md, "Agent vs rules".
