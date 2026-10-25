# Kahu Ola SmokeWatch — Report (draft)

> Sections marked TODO are filled in Step 5. Evaluation numbers: `eval/results.md`.

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

## Evaluation — TODO (full table: eval/results.md)

Headline (Lahaina afternoon fire, preliminary until the full eval run): **GOES review 41 min
after the official first report (00:55Z → 01:36Z), ~6 h before the first polar-orbiter
detection (MODIS, 07:50Z).** The first thermal anomaly appeared at 01:21Z but in a single scan
only, so the agent re-checked rather than reviewed (2-scan persistence rule). Timing is measured
against the first *report*, not against when people first noticed the fire.

## Limitations — TODO (Step 5), known so far

* 2 km pixels: small fires (< ~1 ha flaming) may not reach the +6 K threshold.
* Smoke is supporting evidence only; smoke without a hotspot is not detected.
* Background needs the 3 previous days; a pixel cloudy in all 9 reference scans has no
  background and cannot be tested.
* Volcanic mask suppresses real wildfires inside it (see above).
* Report times for the Hawaiʻi Island fires are from news (approximate), not primary sources.
* Eval FIRMS comparison uses S-NPP VIIRS + MODIS (keyless archive); NOAA-20 not included.
