# Kahu Ola SmokeWatch — video script (≤ 5:00)

Target length 4:45. Spoken lines are in the right column; screen actions are in the shot list.
Every number comes from `REPORT.md` / `eval/results.md`. Say "after the official first
report", never "before humans".

## Timeline

| Time | Section | Spoken (approx.) |
|---|---|---|
| 0:00–0:20 | Title, team | "Kahu Ola SmokeWatch: wildfire detection for Hawaiʻi from GOES-18 every five minutes, with an agent that gathers evidence and a human who decides what is published. Solo entry for the OpenCV AI Competition, built on AWS." |
| 0:20–1:00 | Problem | "On 8 August 2023, several fires burned on Maui at once. The Lahaina fire killed 102 people. The satellites people think of as fire detectors are polar orbiters. They pass a few times a day: the first polar detection of the Lahaina afternoon fire came almost 7 hours after it was reported. GOES-18 sees Hawaiʻi every 5 minutes, but at 2 km per pixel, and smoke at that scale is barely above noise. So SmokeWatch anchors on heat." |
| 1:00–1:45 | How it works | "Every scan is reprojected with OpenCV onto a Hawaiʻi grid. Each pixel is compared with a cloud-screened median of the same clock time on the three previous days. A new 3.9-micron anomaly on land becomes a candidate. Kīlauea and Mauna Loa are masked as volcanic. OpenCV also does the clustering, the smoke mask, optical flow on the plume, and the video you're about to see." |
| 1:45–2:35 | Lahaina case study | "This is the afternoon of 8 August. The fire was reported at 2:55 pm. At 3:21 the first anomaly appears, in one scan only, so the agent schedules a re-check instead of bothering a human. At 3:36 the hotspot is still there in three consecutive scans, and the agent asks for review. That is 41 minutes after the official first report, and about six hours before the first polar-orbiter detection, by MODIS." |
| 2:35–3:30 | Live agent demo | "Here is that 3:36 scan replayed live, with Claude as the agent. It calls track_plume: is the hotspot in the earlier scans, is there a plume? Then query_firms: did any polar satellite already see it? Then it requests review, with a rationale built from that evidence. The rails are in code: no review without tracking, without two scans, or inside the volcanic mask. The agent cannot publish." |
| 3:30–3:55 | Human review | "A reviewer sees the evidence and approves. Only then does it enter the public layer, and every feature carries the disclaimer: an automated satellite estimate, not a substitute for official warnings." |
| 3:55–4:30 | Evaluation | "On seven fires: five reviewed, all of them before the first polar-orbiter detection, 31 minutes to 1 hour 40 after the first report. Precision 0.86, or 0.95 if you count reviews confirmed by FIRMS as fire spread. And honestly: the LLM agent made the same decisions as our rules on every case we tested. Its value is the evidence-cited rationale and an agent loop we can extend, not better discrimination yet." |
| 4:30–4:50 | Limitations | "We missed the Lahaina morning fire and the Kula fire: too small for a 2 km pixel. Hawaiʻi Island by day, on vog days, causes false positives; the human reviewer filters those. And the agent runs on the Anthropic API because Bedrock was not yet enabled on this new account." |
| 4:50–5:00 | Close | "A faster satellite signal, a human in the loop, and honest numbers. Mahalo." |

## Shot list

| # | Time | Shot | Source / action |
|---|---|---|---|
| 1 | 0:00 | Title card over the live page header | Prod `ReviewUrl` `/`, top of page |
| 2 | 0:20 | Map of Maui with the four 8 Aug fires; the timeline bar with the "≈ 6 h 14 min" gap | Public page, case-study timeline |
| 3 | 0:45 | Polar vs geostationary: the case-study timeline (report 14:55 → review 15:36 → MODIS 21:50 HST), then the slider ticks of one scan every 5 min | Public page, case-study timeline, then viewer `?date=2023-08-08&region=maui` |
| 4 | 1:00 | `docs/architecture.svg` full screen, then zoom on the OpenCV box | Repo file in a browser |
| 5 | 1:25 | ΔT anomaly layer toggled on/off on one scan; the colorbar with the 6 K tick | Viewer, `?date=2023-08-08&t=0136Z&region=maui` |
| 6 | 1:45 | Case-study animation (MP4) from 22:00Z to 03:00Z | Public page, video card (or `events/media/lahaina-2023-08-08.mp4`) |
| 7 | 2:10 | Pause on 01:21Z (yellow circle, re-check), step ▶| to 01:36Z (red circle, review) | Viewer, `?date=2023-08-08&t=0121Z&region=maui` |
| 8 | 2:35 | Terminal: live replay with Claude (command A below); scroll the tool calls | Terminal, large font |
| 9 | 3:05 | Trace card: Claude next to Rules for 01:36Z; then `docs/agent-trace-example.md` | Public page, trace card |
| 10 | 3:30 | Local reviewer page: the Lahaina candidate (20.88 N 156.67 W, from the 01:36Z replay), overlay, Approve with the demo token; then `/public/smoke.geojson` showing the disclaimer | Command B below, `http://127.0.0.1:8765/review` |
| 11 | 3:55 | Evaluation table | Public page, eval table (or `REPORT.md` → Evaluation) |
| 12 | 4:15 | "Agent vs rules" paragraph highlighted | `REPORT.md` |
| 13 | 4:30 | Failure gallery: missed Kula, a vog-day FP | `eval/failures/` images |
| 14 | 4:50 | Live status card ("silence = good"), end card | Prod `/` |

## Demo commands (run before recording; all local, nothing touches prod)

Prerequisites: the repo venv, the GOES cache in `.cache/goes` (from the eval run), the FIRMS
archive in `.cache/firms`, AWS credentials that can read `/smokewatch/anthropic_api_key`.

**A. Live Lahaina replay with Claude** (~2 s for perception from cache, 5–12 s for the agent):

```bash
# 15:21 HST: single-scan anomaly -> the agent re-checks
ANTHROPIC_KEY_PARAM=/smokewatch/anthropic_api_key FIRMS_ARCHIVE_DIR=.cache/firms \
  .venv/bin/python scripts/run_local.py --time 2023-08-09T01:21Z --policy anthropic --out out/demo

# 15:36 HST: 3/3 scans -> the agent requests human review
ANTHROPIC_KEY_PARAM=/smokewatch/anthropic_api_key FIRMS_ARCHIVE_DIR=.cache/firms \
  .venv/bin/python scripts/run_local.py --time 2023-08-09T01:36Z --policy anthropic --out out/demo
```

Without network or key, use `--policy rules`: same decisions, deterministic rationale.

**B. Reviewer page on the replay output** (local store, demo-only token):

```bash
LOCAL_STORE=out/demo REVIEW_TOKEN=local-demo-token-not-a-real-secret \
  .venv/bin/python scripts/serve_local.py
# open http://127.0.0.1:8765/review -> Approve with the token above
# then http://127.0.0.1:8765/public/smoke.geojson (shows the disclaimer)
```

Both commands were tested on 25 Oct 2026: at 01:21Z Claude re-checked Lahaina (1/3 scans) and
queued the 3/3 Upcountry hotspot; Approve returned `published` and the layer carried the
disclaimer.

Do **not** replay on the deployed Lambda (`aws lambda invoke … {"time": …}`): it would
overwrite the live status and create real pending reviews.

## Checklist before recording

* Prod page loads on desktop and phone; viewer deep links above open at the right scan.
* `out/demo` is empty before take 1 (`rm -rf out/demo`), so the review page shows only the
  replayed candidates.
* Terminal font ≥ 18 pt; no `.env.local`, SSM values or tokens on screen (the demo token in
  command B is a throwaway, local only).
