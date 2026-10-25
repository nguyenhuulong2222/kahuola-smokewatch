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

**Where to film:** the public page, the timeline viewer and the live status are filmed on the **prod
URL** (`PROD` = https://dgmg5co6ld62forty2ubtcwehy0uawnd.lambda-url.us-east-1.on.aws/). Only the agent replay and the review demo are local, because
replaying on prod would put 2023 candidates into the live review queue.

**On-screen caption for shot 8** (lower third, for the whole shot):
> Local replay of the same container image — prod is not replayed to keep the live queue clean

**Caption for shot 10:**
> Local review page on the replay output — prod is not replayed to keep the live queue clean

| # | Time | Shot | Source / action |
|---|---|---|---|
| 1 | 0:00 | Title card over the live page header | PROD `/`, top of page |
| 2 | 0:20 | Map of Maui with the four 8 Aug fires; the timeline bar with the "≈ 6 h 14 min" gap | PROD `/`, case-study timeline |
| 3 | 0:45 | Polar vs geostationary: report 14:55 → review 15:36 → MODIS 21:50 HST, then the slider ticks, one scan every 5 min | PROD `/`, case-study timeline, then PROD `/?date=2023-08-08&region=maui#viewer` |
| 4 | 1:00 | `docs/architecture.svg` full screen, then zoom on the OpenCV box | Repo file on GitHub |
| 5 | 1:25 | ΔT anomaly layer toggled on/off on one scan; the colorbar with the 6 K tick | PROD `/?date=2023-08-08&t=0136Z&region=maui#viewer` |
| 6 | 1:45 | Case-study animation (MP4), 22:00Z → 03:00Z | PROD `/`, video card |
| 7 | 2:10 | Pause on 01:21Z (yellow circle, re-check), step ▶\| to 01:36Z (red circle, review) | PROD `/?date=2023-08-08&t=0121Z&region=maui#viewer` |
| 8 | 2:35 | Terminal: the deployed image replays 01:21Z and 01:36Z with Claude (command A); show the outcomes JSON and the `docker inspect` digest line. **Caption above.** | Local terminal, large font |
| 9 | 3:05 | Trace card: Claude next to Rules for 01:36Z; then `docs/agent-trace-example.md` | PROD `/`, trace card |
| 10 | 3:30 | Review page on the replay output: the Lahaina candidate (20.88 N 156.67 W), overlay, Approve with the demo token; then `/public/smoke.geojson` with the disclaimer. **Caption above.** | Command B, `http://127.0.0.1:8765/review` |
| 11 | 3:55 | Evaluation table | PROD `/`, eval table |
| 12 | 4:15 | "Agent vs rules" paragraph highlighted | `REPORT.md` on GitHub |
| 13 | 4:30 | Failure gallery: missed Kula, a vog-day FP | `eval/failures/` on GitHub |
| 14 | 4:50 | Live status card ("silence = good"), end card | PROD `/`, live status |

## Demo commands (run before recording; local only, nothing touches prod)

Prerequisites: Docker; the deployed image (`sam build --use-buildkit` tags it `scanfunction:v1`);
the GOES cache in `.cache/goes` and the FIRMS archive in `.cache/firms` (from the eval run);
AWS credentials in `~/.aws` that can read `/smokewatch/anthropic_api_key`.

**A. The deployed container image replays Lahaina with Claude.** The Lambda base image includes
the Runtime Interface Emulator, so this calls the real `handler.scan`. No bucket is set, so the
store is a local folder. The frames come from the mounted cache (no NOAA download).

```bash
# show that the local image is the one deployed (same digest as the Lambda's ResolvedImageUri)
docker inspect scanfunction:v1 --format '{{index .RepoDigests 0}}'

rm -rf out/demo && mkdir -p out/demo
docker run -d --name smokewatch-demo --platform linux/amd64 -p 9000:8080 \
  -v "$PWD/.cache/goes:/tmp/goes" -v "$PWD/.cache/firms:/opt/firms:ro" \
  -v "$PWD/out/demo:/opt/out" -v "$HOME/.aws:/root/.aws:ro" \
  -e AWS_REGION=us-east-1 -e LOCAL_STORE=/opt/out -e FIRMS_ARCHIVE_DIR=/opt/firms \
  -e AGENT_POLICY=anthropic -e ANTHROPIC_KEY_PARAM=/smokewatch/anthropic_api_key \
  -e ANTHROPIC_MODEL_ID=claude-sonnet-5-5 \
  scanfunction:v1 handler.scan

# 15:21 HST: single-scan anomaly -> the agent re-checks Lahaina
curl -s localhost:9000/2015-03-31/functions/function/invocations \
  -d '{"time":"2023-08-09T01:21:00Z"}' | python3 -m json.tool
# 15:36 HST: 3/3 scans -> the agent requests human review
curl -s localhost:9000/2015-03-31/functions/function/invocations \
  -d '{"time":"2023-08-09T01:36:00Z"}' | python3 -m json.tool

docker rm -f smokewatch-demo
```

For a run without network or key, use `-e AGENT_POLICY=rules`: same decisions, deterministic
rationale.

**B. Review page on the replay output** (the same `handler.py` as in the image, served by
`scripts/serve_local.py`; demo-only token):

```bash
LOCAL_STORE=out/demo REVIEW_TOKEN=local-demo-token-not-a-real-secret \
  .venv/bin/python scripts/serve_local.py
# open http://127.0.0.1:8765/review -> Approve the Lahaina candidate with the token above
# then http://127.0.0.1:8765/public/smoke.geojson (shows the disclaimer)
```

Tested on 25 Oct 2026 with image `sha256:6b6c5ab6…` (the digest deployed on the Lambda):
* 01:21Z, 12.4 s: Claude re-checked Lahaina (1 of 3 scans) and queued the 3/3 Upcountry
  hotspot.
* 01:36Z, 8.9 s: three reviews, Lahaina among them. `errors: []`, no NOAA download.
* Approve on the local review page returned `published`, and the layer carried the
  disclaimer.

Do **not** replay on the deployed Lambda (`aws lambda invoke … {"time": …}`): it would
overwrite the live status and put real pending reviews in the live queue.

## Checklist before recording

* PROD page loads on desktop and phone; the viewer deep links above open at the right scan.
* `out/demo` is recreated before take 1 (command A does it), so the review page shows only the
  replayed candidates.
* Terminal font ≥ 18 pt. No `.env.local`, SSM values, AWS keys or tokens on screen. The demo
  token in command B is a throwaway, local only.
