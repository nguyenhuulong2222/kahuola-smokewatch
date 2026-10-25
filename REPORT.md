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

## Evaluation — TODO (see eval/results.md)

## Limitations — TODO (Step 5), known so far

* 2 km pixels: small fires (< ~1 ha flaming) may not reach the +6 K threshold.
* Smoke is supporting evidence only; smoke without a hotspot is not detected.
* Background needs the 3 previous days; a pixel cloudy in all 9 reference scans has no
  background and cannot be tested.
* Volcanic mask suppresses real wildfires inside it (see above).
* Report times for the Hawaiʻi Island fires are from news (approximate), not primary sources.
* Eval FIRMS comparison uses S-NPP VIIRS + MODIS (keyless archive); NOAA-20 not included.
