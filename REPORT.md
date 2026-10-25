# Kahu Ola SmokeWatch — Report

OpenCV AI Competition 2026 (AWS). Code, deploy and evaluation steps: [README.md](README.md).
All evaluation numbers: [eval/results.md](eval/results.md).
Demo video (3:59): https://youtu.be/ZwM0HbwoKGk

## Problem and users

On 8 August 2023 several wind-driven fires burned on Maui at once, including the Lahaina fire,
which killed 102 people. Official awareness of each ignition came from 911 calls and
responders. The satellites that most people know as "fire detectors" are polar orbiters
(VIIRS, MODIS). They pass over Hawaiʻi a few times a day, so on 8 August the first polar
detection of the Lahaina afternoon fire came about 7 hours after it was reported.

GOES-18 sees Hawaiʻi every 5 minutes, but at 2 km resolution, and its raw imagery needs an
expert to read. SmokeWatch watches every scan, finds new thermal anomalies on land, gathers
evidence with an agent, and asks a **human reviewer** whether to publish a public
"possible fire" layer.

* **Residents of Maui and Hawaiʻi Island**: a public map layer with an automated satellite
  estimate. It always carries the disclaimer *"Ước tính tự động từ vệ tinh, không thay thế
  cảnh báo chính thức"* ("automated satellite estimate, not a substitute for official
  warnings").
* **Emergency managers and duty officers** (the reviewer role): a short queue of
  evidence-cited candidates instead of raw imagery, each one approved or rejected in one click.

It is a second, satellite-based signal next to 911 calls, not a replacement for them or for
official warnings.

## Architecture

![architecture](docs/architecture.svg)

1. **Ingest:** every 10 min, EventBridge triggers `ScanFunction`. It reads the latest
   GOES-18 ABI L2 MCMIP scan from NOAA's public bucket (same region, anonymous) and the
   3-day background frames, cached in the stack's own S3 bucket.
2. **Perception (OpenCV):** reprojection onto a 0.02° Hawaiʻi grid, background differencing,
   hotspot and smoke candidates (below).
3. **Agent:** per candidate, `track_plume` → `query_firms` → review, re-check or dismiss
   ([agent workflow](docs/agent-workflow.svg), [example trace](docs/agent-trace-example.md)).
4. **Human in the loop:** `HttpFunction` serves the reviewer page. Only `POST /review` with
   the reviewer token moves a candidate to the public GeoJSON layer.
5. **Public page and archive:** the Lahaina case study, a timeline viewer over stored scans,
   and live status.

## OpenCV 5 implementation

`opencv-python-headless==5.0.0.93`. Every image step runs in OpenCV on NumPy arrays:

| Stage | OpenCV |
|---|---|
| Reprojection of the ABI fixed grid to lat/lon | `cv2.remap` (bilinear, per band) |
| Hotspot clusters | `cv2.dilate`, `cv2.connectedComponentsWithStats` |
| Smoke mask | `cv2.morphologyEx` (top-hat, open, close), `cv2.GaussianBlur`, `cv2.bilateralFilter`, `cv2.blur` (texture), `cv2.connectedComponents` |
| Plume geometry | `cv2.findContours`, `cv2.approxPolyDP` → GeoJSON; `cv2.distanceTransform` (hotspot-to-plume distance); `cv2.boundingRect` |
| Plume motion | `cv2.DISOpticalFlow_create` between consecutive scans; the plume mask is warped and scored by IoU and flow coherence |
| Land and coast | `cv2.dilate` of the Natural Earth land mask |
| Rendering | `cv2.applyColorMap` (ΔT in inferno), `cv2.addWeighted`, `cv2.circle`, `cv2.putText`, `cv2.polylines`, `cv2.imencode` (WebP/PNG) |
| Case-study media | `cv2.VideoWriter` (H.264 MP4) and `cv2.imwriteanimation` (GIF fallback) |

## Detection method (fire-anchored)

At GOES-18's 2 km resolution, wildfire smoke over Hawaiʻi changes visible reflectance by
~0.01 — about the noise level — and our first smoke-first detector had zero recall on the
Lahaina plume with 6–17 false positives per frame (thin cloud edges over the ocean). A burning
fire, however, raises the 3.9 µm brightness temperature (ABI C07) by many kelvin in a single
pixel, by day and by night. SmokeWatch therefore anchors on the fire and treats smoke as
supporting evidence:

1. **Thermal background.** For every pixel, the background is the **median** of the scans
   within ±5 min of the same clock time on the **3 previous days** (up to 9 scans). Before the
   median, reference samples that were cloudy are dropped (C13 < 273 K, or > 4 K colder than
   the warmest sample at that pixel). A max would let one hot afternoon dominate; a median of
   cloud-free samples is robust to both. This removes the earlier Kauaʻi false positive, which
   came from a cloud in the single reference frame.
2. **Anomaly.** A pixel is a hotspot when Δ(C07−C14) > 6 K, ΔC07 > 6 K and ΔC14 > −3 K (the
   current pixel is not cloud-covered) relative to the background, on land (Natural Earth 10 m
   coastline, dilated 1 px so coastal Lahaina is included). Sun-heated dry ground (west
   Molokaʻi, Kona) is warm every day, so it cancels out; sun glint over water is masked out.
3. **Candidates** are clusters of hotspot pixels (OpenCV dilation + connected components).
4. **Smoke** (daytime): change vs the same background — visible brightening with low
   ΔC05/ΔVIS (clouds ≈ 1.1, smoke ≈ 0.3), no C13 cooling, smooth texture. A smoke component
   whose nearest pixel is within ~8 km of the hotspot is attached as the candidate's plume; its
   extent gives the downwind bearing.

## Volcanic exclusion mask

Kīlauea and Mauna Loa produce real, persistent thermal anomalies (FIRMS flags them as
`type = 1`, "active volcano"). A fixed mask (`smokewatch/fire.py: VOLCANIC_ZONES`) covers:

| Zone | Geometry | Buffer |
|---|---|---|
| Kīlauea summit caldera (Halemaʻumaʻu 19.405 N, 155.282 W) | point | 7 km |
| Kīlauea East Rift Zone (summit → Puʻu ʻŌʻō → Leilani → Kapoho) | polyline | 5 km |
| Kīlauea Southwest Rift Zone | polyline | 4 km |
| Mauna Loa summit (Mokuʻāweoweo 19.475 N, 155.608 W) | point | 7 km |
| Mauna Loa NE Rift Zone + 2022 flow field | polyline | 5 km |
| Mauna Loa SW Rift Zone | polyline | 4 km |

Coordinates: USGS HVO (summits) and approximate rift traces; buffers are deliberately generous
to cover 2 km pixel smear and recent flow fields. A candidate touching the mask is labelled
`volcanic`; the agent dismisses it with the zone name in the rationale, and the review tool
refuses it in code, so a volcanic anomaly can never reach a human reviewer or the public layer.
Trade-off: a genuine wildfire inside the mask (e.g., near Volcano village, ~5 km from the
caldera, or in the Kaʻū desert along the SW rift) will not be reported.

## Agent loop

hotspot → `track_plume` (is the hotspot present in the 2 earlier 5-min scans? by day, is a
plume attached, which way does it extend, does optical flow move it coherently that way?) →
`query_firms` (VIIRS/MODIS detections within 5 km in the 24 h *before* the scan) → decision.

* volcanic → `dismiss`;
* hotspot in only 1 of 3 scans → `schedule_recheck` (code refuses review);
* hotspot in ≥ 2 of 3 scans → `request_human_review`, confidence from persistence, ΔT,
  plume and FIRMS. Absence of FIRMS does not block review: polar orbiters pass a few times a
  day, GOES every 5 minutes — that gap is the point of the system.

Rationales are generated from the gathered evidence (ΔT, scans with hotspot and their times,
plume size/bearing/motion, FIRMS count and latest pass), never from fixed text.

**Confidence is not a decision threshold.** With an LLM policy, the `confidence` passed to
`request_human_review` (e.g. 0.45) is the model's own estimate. It is shown to the reviewer
and nothing else. Reviews are blocked only by hard rails in code (`Episode.t_request_human_review`):
`track_plume` must have run, the candidate must be a thermal anomaly outside the volcanic mask,
and the hotspot must be present in ≥ 2 scans. A refused call returns to the model as a tool
error, so it must choose again. The agent can never publish; only a human at `POST /review` can.

## Agent vs rules

Policies are interchangeable (`AgentPolicy` = `rules` | `anthropic` | `bedrock`) and share
the same tools and rails. Bedrock was blocked for this account (Sonnet 5.5 tokens-per-minute
quota applied at 0; increase requested), so the LLM runs used **Claude Sonnet 5.5 via the
Anthropic Messages API**. The prompt was not changed to steer these results.

**True fires** (Lahaina 01:21Z/01:36Z, Olinda 11:21Z; `eval/policy_compare_anthropic.json`):
6/6 decisions identical to RulePolicy. For Lahaina at 01:21Z the agent re-checked the
single-scan hotspot, then queued review at 01:36Z. Trace: `docs/agent-trace-example.md`.

**Real false positives** (scans with the uncorroborated FPs of the rules eval: vog 12–13 Sep
20:01Z, 23:01Z, 01:01Z; Kauaʻi 8 Aug 21:21Z; clear 6 Aug 23:01Z; cloudy 29 Jan 09:01Z;
`eval/policy_compare_anthropic_fp.json`):

* 18/18 candidates got the same action as RulePolicy. That includes the **9 uncorroborated
  false positives** that rules sent to review: the LLM also sent all 9 to review. It did not
  dismiss or re-check any of them.
* It never asked for more history (`minutes_back` > 10), even on 1-px, +6–7 K hotspots. The
  system prompt tells it to start with 10 min, and 2–3 persistent scans satisfied it.
* It did lower its own confidence on them (0.25–0.55, vs 0.40–0.80 on the true fires). The
  ranges overlap, so confidence cannot separate them. It is not used as a gate (see above).

**Conclusion (honest):** with today's evidence — thermal persistence, FIRMS, and a smoke
plume that is rarely resolved at 2 km — the LLM agent matches the rules. It does not reduce
false positives. Its value here is an evidence-cited rationale for the reviewer and an
agent loop that can take new tools. It is not better discrimination. Rejecting the vog-day
heating needs a better perception signal (see the contextual test), not a different policy.

Cost and latency (Sonnet 5.5, $2 / $10 per M input / output tokens): scans with candidates
used 3 model calls, 6.2–10.0 k input and 0.3–1.2 k output tokens. That is **$0.016–0.032 per
scan** and 5–12 s of LLM time. Scans with no candidate make no model call (most live scans).

## Experiment: contextual test (not enabled)

The 12 real false positives are broad, low-amplitude warmings (Hawaiʻi Island by day, a vog
day). Fires are compact and intense. We therefore tried the MODIS contextual fire test
(Giglio et al. 2003, RSE 87:273–282; kept in Collection 6, Giglio et al. 2016) **with its
published parameters, not tuned on our cases**:

* window from 3×3 to 21×21 until ≥ 25 % / ≥ 8 valid pixels;
* ΔT > mean + 3.5 δ, ΔT > mean + 6 K, T4 > mean + 3 δ (δ = mean absolute deviation);
* applied to our background-differenced fields (ΔT → Δ(C07−C14), T4 → ΔC07).

Decision rule fixed in advance: enable only if it removes real FPs **and** loses no fire
and delays no first review.

| | off (shipped) | on |
|---|---|---|
| Real (uncorroborated) FPs | 12 | 0 |
| Negative-day scans with a false review | 6/19 | 0/19 |
| Fires reviewed | 5/7 | 4/7 (North Kohala lost) |
| First review later | — | Olinda +30 min (after VIIRS), Lahaina PM +20 min, Mauna Kea +10 min |

It stays **off** (`FP["contextual"] = False`). At 2 km the fire's neighbouring pixels are
already warm from blur and spread. For Olinda at 11:21Z the fire pixel is +7.5 K, and its
neighbours are +4.8 and +5.0 K, so the contextual threshold rises to 8.9 K. The test
penalises exactly the small, smeared fires we most want early. Full numbers:
`eval/results.md`, "Contextual test".

## Evaluation

The evaluation replays 148 archived GOES-18 scans (`eval/run_eval.py`, policy `rules`; the
LLM agent made the same decisions, see "Agent vs rules").
* **Positive cases:** 7 fires of 8–9 August 2023, with official first-report times. Maui
  times come from primary sources (fire and police reports, the after-action report).
  Hawaiʻi Island times come from news.
* **Negative cases:** 5 days (clear, cloudy, vog ×2, sun glint).

Labels and citations are in `eval/cases.yaml`.

**What is timed:** the first scan where the agent *queued a human review* within the match
radius of the origin. This is not the first thermal anomaly. It is compared with the official
first report, and with the first FIRMS detection by **any** polar orbiter (VIIRS S-NPP,
VIIRS NOAA-20, MODIS Aqua/Terra).

| Fire | First report | First GOES review | vs report | First polar detection | vs first polar |
|---|---|---|---|---|---|
| Olinda (Upcountry Maui) | 00:22 HST | 01:21 HST | +59 min | 01:44 HST (VIIRS S-NPP) | 23 min before |
| Lahaina morning | 06:34 HST | missed | — | 21:50 HST (MODIS) | — |
| Kula | 11:27 HST | missed | — | 13:45 HST (VIIRS NOAA-20) | — |
| **Lahaina afternoon** | 14:55 HST | **15:36 HST** | **+41 min** | 21:50 HST (MODIS) | **~6 h before** |
| Pūlehu–Kīhei (attribution uncertain) | 18:01 HST | 19:41 HST | +1 h 40 min | 21:50 HST (MODIS) | 2 h 09 min before |
| North Kohala, Hawaiʻi Island | 03:00 HST | 03:41 HST | +41 min | 10:57 HST (MODIS) | ~7 h before |
| Mauna Kea Resort / Kawaihae, Hawaiʻi Island | 15:00 HST | 15:31 HST | +31 min | 01:25 HST next day (VIIRS S-NPP) | ~10 h before |

**Headline (Lahaina afternoon fire): review 41 min after the official first report, ~6 h
before the first polar-orbiter detection (MODIS).** The first thermal anomaly appeared at
15:21 HST in a single scan, so the agent re-checked instead of asking for review (2-scan
rule). It queued the review at 15:36 HST. Every review came *after* the first official
report: SmokeWatch is a faster satellite signal than the polar orbiters, not an earlier
signal than people.

* **Recall:** 5 of 7 fires reviewed. All 5 were reviewed before the first polar-orbiter
  detection.
* **Precision:** 207 of 241 reviews fell within a known fire's radius (0.859).
  * Of the 34 others, 22 have FIRMS vegetation-fire detections within 3 km: the fire spread
    beyond the radius, or it was an unlisted fire. Counting those as fires gives 0.95.
  * **12 reviews are uncorroborated** false positives.
* **Negative days:** 6 of 19 scans produced a false review, mostly Hawaiʻi Island by day on
  a vog day. Kīlauea's volcanic anomalies were dismissed every time and never reached review.

**Live operation:**
* Deployed in `us-east-1` since 24 Oct 2026: 1536 MB, a scan every 10 min.
* In the 24 h to 25 Oct 20:20Z: 84 scans logged, 0 candidates. No fires were burning, so
  there were no false reviews and no LLM cost.
* Scan time: 7 s median, 28 s max. NOAA fetch: 4–10 s per scan.

**Running cost (estimate, us-east-1 list prices):**
* Lambda: 144 scans/day × ~7 s × 1.5 GB ≈ 45 k GB-s/month, about $0.8.
* S3: about $0.3 (archive kept 90 days, plus PUT requests).
* NOAA reads: free (public dataset in the same region).
* LLM (Anthropic API): called only on scans with candidates, $0.016–0.032 per such scan.

## Limitations

* **Missed fires: Lahaina morning and Kula.** Both were small at 2 km pixel scale.
  * Neither reached the +6 K threshold in the replayed scans: 30 for Lahaina morning, 27
    for Kula.
  * The Lahaina morning fire was reported at 06:34 HST and declared contained the same
    morning.
  * NOAA-20 VIIRS (375 m) saw Kula at 13:45 HST; GOES did not.
  * A fire must be large or hot enough to raise a 2 km pixel by several kelvin.
* **Pūlehu attribution is uncertain.** The hotspot counted as Pūlehu (review 19:41 HST) is
  2.8 km from the Pūlehu origin and 4.5 km from the Kula origin. Hotspots 3 km from the Kula
  origin were already present from 17:56 HST, before Pūlehu was reported at 18:01 HST. The
  review may be the Kula fire spreading rather than Pūlehu, so treat that timing as
  unconfirmed.
* **Daytime false positives on Hawaiʻi Island, especially on vog days.**
  * 12 reviews in the evaluation have no FIRMS corroboration. Most are broad, low-amplitude
    (+6–12 K) daytime warmings of Hawaiʻi Island on a vog day.
  * A contextual test removed them, but lost one fire and delayed three, so it is off (see
    the experiment above).
  * For now the human reviewer is the filter.
* **Report times for the Hawaiʻi Island fires come from news coverage**, not primary
  sources, so their Δ-to-report is approximate. Maui times are from primary reports.
* **The LLM agent uses the Anthropic API, not Amazon Bedrock.**
  * On this new AWS account, Bedrock calls to Claude Sonnet 5.5 returned
    `AccessDeniedException`: the model was not available to the account, and its
    tokens-per-minute quota (L-94A31E46) was 0.
  * A quota increase was requested and was still pending at submission.
  * `BedrockPolicy` is implemented and selectable (`AgentPolicy=bedrock`). The deployed stack
    runs `AgentPolicy=anthropic`, with the API key in SSM.
* **The LLM agent is not better than the rules here.** It made the same decision on all 24
  candidates tested, including all 9 uncorroborated false positives (see "Agent vs rules").
* **The background needs 3 previous days.** A pixel cloudy in all 9 reference scans has no
  background and cannot be tested. A land-cover change within 3 days (e.g. a fresh burn
  scar) shifts the background.
* **Smoke is supporting evidence only.** At 2 km a plume rarely resolves, and smoke without
  a hotspot is not detected.
* **The volcanic mask suppresses real wildfires inside it** (near Volcano village, the Kaʻū
  desert).
* **Thresholds are heuristic** (+6 K, 2 of 3 scans), chosen from Lahaina and Olinda pixel
  values before the full evaluation.
* **The evaluation set is small:** 7 fires over two days of one event, and 5 negative days.
* **The public viewer shows only stored days:** live scans for 90 days, reviewed events
  permanently, and the featured replay. It does not fetch past NOAA data on demand, and
  replay days show RulePolicy decisions.

## Responsible use

* **Human in the loop by construction.**
  * The agent's tools cannot publish. Only `POST /review` with the reviewer token (compared
    in constant time) moves a candidate to the public layer.
  * Code rails decide what can reach a reviewer at all: `track_plume` has run, the hotspot
    is in ≥ 2 scans, and it is outside the volcanic mask.
  * The model's confidence is never a gate.
* **A disclaimer on every public feature:** "Ước tính tự động từ vệ tinh, không thay thế cảnh
  báo chính thức". The public page points people to Maui Emergency Management and county
  alerts.
* **Honest timing.** Detection time is measured from the official first report, never
  "before humans", and compared with the earliest polar orbiter of any kind.
* **Safe failure.** LLM, network or FIRMS errors never stop a scan: the deterministic
  RulePolicy resolves whatever is left open, and the error is recorded in the trace.
* **No personal data** is collected or stored. Secrets live in SSM or NoEcho stack
  parameters, never in code or git.
* **Not for life-safety decisions on its own.** A 2 km pixel cannot place a fire on a
  street, and small fires are missed.
