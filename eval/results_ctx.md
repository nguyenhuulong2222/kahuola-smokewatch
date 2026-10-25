# SmokeWatch evaluation

Generated 2026-10-25T16:39Z · policy `rules` · 158 unique GOES-18 PACUS scans. Cases: `eval/cases.yaml`.

## Time to detection

First GOES scan where the agent queued a human review within the radius of the origin, vs the official first-report time and vs the first FIRMS detection by any polar-orbiting sensor. Polar sensors: NOAA-20 included (FIRMS API, VIIRS_NOAA20_SP); S-NPP VIIRS and MODIS Aqua/Terra from the FIRMS archive. Δ: + = GOES later, − = GOES earlier.

| Fire | Official first report | First thermal anomaly | **First GOES review** | Δ vs report | First polar-orbiter detection (sensor) | **Δ vs first polar** |
|---|---|---|---|---|---|---|
| Olinda fire (Upcountry Maui) | 08-08 10:22Z (00:22 HST) | 08-08 11:46Z (01:46 HST) | **08-08 11:51Z (01:51 HST)** | +1h29m | 08-08 11:44Z (01:44 HST) (VIIRS_SNPP) | **+7m** |
| Lahaina morning fire (Lahainaluna Rd) | 08-08 16:34Z (06:34 HST) | — | **—** | — | 08-09 07:50Z (21:50 HST) (MODIS_Terra) | **—** |
| Kula fire (Kualono Pl) | 08-08 21:27Z (11:27 HST) | — | **—** | — | 08-08 23:45Z (13:45 HST) (VIIRS_NOAA20) | **—** |
| Lahaina afternoon fire (rekindle, Kuʻialua St) | 08-09 00:55Z (14:55 HST) | 08-09 01:51Z (15:51 HST) | **08-09 01:56Z (15:56 HST)** | +1h01m | 08-09 07:50Z (21:50 HST) (MODIS_Terra) | **−5h54m** |
| Pūlehu–Kīhei fire | 08-09 04:01Z (18:01 HST) | 08-09 04:06Z (18:06 HST) | **08-09 04:06Z (18:06 HST)** | +5m | 08-09 07:50Z (21:50 HST) (MODIS_Terra) | **−3h44m** |
| North Kohala (Akoni Pule Hwy) fire, Hawaiʻi Island | 08-08 13:00Z (03:00 HST) | 08-08 14:21Z (04:21 HST) | **—** | — | 08-08 20:57Z (10:57 HST) (MODIS_Terra) | **—** |
| Mauna Kea Resort / Kawaihae fires, Hawaiʻi Island | 08-09 01:00Z (15:00 HST) | 08-09 01:36Z (15:36 HST) | **08-09 01:41Z (15:41 HST)** | +41m | 08-09 11:25Z (01:25 HST) (VIIRS_SNPP) | **−9h44m** |

In words (GOES review vs official report vs first polar-orbiter pass):

- Olinda fire (Upcountry Maui): GOES review 1 h 29 min after the official first report, 7 min after the first polar-orbiter detection (VIIRS_SNPP).
- Lahaina morning fire (Lahainaluna Rd): not detected by GOES in 30 scans.
- Kula fire (Kualono Pl): not detected by GOES in 27 scans.
- Lahaina afternoon fire (rekindle, Kuʻialua St): GOES review 1 h 01 min after the official first report, ~6 h before the first polar-orbiter detection (MODIS_Terra).
- Pūlehu–Kīhei fire: GOES review 5 min after the official first report, ~4 h before the first polar-orbiter detection (MODIS_Terra).
- North Kohala (Akoni Pule Hwy) fire, Hawaiʻi Island: not detected by GOES in 36 scans.
- Mauna Kea Resort / Kawaihae fires, Hawaiʻi Island: GOES review 41 min after the official first report, ~10 h before the first polar-orbiter detection (VIIRS_SNPP).

Per-sensor first detection within the radius:

- olinda: VIIRS_SNPP 08-08 11:44Z (01:44 HST), MODIS_Aqua 08-08 12:23Z (02:23 HST), VIIRS_NOAA20 08-08 12:33Z (02:33 HST), MODIS_Terra 08-08 20:56Z (10:56 HST)
- lahaina_am: MODIS_Terra 08-09 07:50Z (21:50 HST), VIIRS_SNPP 08-09 11:25Z (01:25 HST), VIIRS_NOAA20 08-09 12:15Z (02:15 HST), MODIS_Aqua 08-09 13:05Z (03:05 HST)
- kula: VIIRS_NOAA20 08-08 23:45Z (13:45 HST), VIIRS_SNPP 08-09 00:36Z (14:36 HST), MODIS_Terra 08-09 07:50Z (21:50 HST), MODIS_Aqua 08-09 13:05Z (03:05 HST)
- lahaina_pm: MODIS_Terra 08-09 07:50Z (21:50 HST), VIIRS_SNPP 08-09 11:25Z (01:25 HST), VIIRS_NOAA20 08-09 12:15Z (02:15 HST), MODIS_Aqua 08-09 13:05Z (03:05 HST)
- pulehu: MODIS_Terra 08-09 07:50Z (21:50 HST), VIIRS_SNPP 08-09 11:25Z (01:25 HST), VIIRS_NOAA20 08-09 12:15Z (02:15 HST), MODIS_Aqua 08-09 13:05Z (03:05 HST)
- kohala_ranch: MODIS_Terra 08-08 20:57Z (10:57 HST), VIIRS_SNPP 08-08 22:55Z (12:55 HST), VIIRS_NOAA20 08-08 23:45Z (13:45 HST)
- mauna_kea_resort: VIIRS_SNPP 08-09 11:25Z (01:25 HST), VIIRS_NOAA20 08-09 12:15Z (02:15 HST)

Sources per fire (time / origin):

- **Olinda fire (Upcountry Maui)** — time: F1 p.31, F2, AAR p.10 (primary); origin 20.8087, -156.2829 (AAR p.10 + OSM geocode); match radius 4 km; 23 scans replayed.
- **Lahaina morning fire (Lahainaluna Rd)** — time: F2, F1, AAR (primary); origin 20.8847, -156.663 (F2 §3.7.7 (primary)); match radius 4 km; 30 scans replayed.
- **Kula fire (Kualono Pl)** — time: F1, F2, AAR (primary); origin 20.7724, -156.3069 (F1/AAR address + OSM geocode); match radius 3 km; 27 scans replayed.
- **Lahaina afternoon fire (rekindle, Kuʻialua St)** — time: F1, F2, AAR (primary). ATF news coverage cites ~14:25 HST; not verified; origin 20.8847, -156.663 (F2 (primary)); match radius 4 km; 17 scans replayed.
- **Pūlehu–Kīhei fire** — time: F1 p.32, AAR p.13 (primary); origin 20.8002, -156.3701 (F1/AAR address + OSM geocode); match radius 3 km; 6 scans replayed.
- **North Kohala (Akoni Pule Hwy) fire, Hawaiʻi Island** — time: bigislandnow.com 2023-08-08; bigislandvideonews.com 2023-08-08 (secondary, approximate); origin 20.0902, -155.8312 (news + OSM geocode (secondary)); match radius 6 km; 36 scans replayed.
- **Mauna Kea Resort / Kawaihae fires, Hawaiʻi Island** — time: bigislandnow.com 2023-08-08 (secondary, approximate); origin 20.0069, -155.8262 (news + OSM geocode (secondary)); match radius 6 km; 14 scans replayed.

First-review rationale (generated from evidence):

- *olinda*: Hotspot at 20.810N 156.290W: 4 px, Δ(3.9−11.2 µm) +32.4 K vs same time on previous days; present in 2/3 scans over 10 min (11:46Z, 11:51Z); night — smoke not observable; FIRMS: 6 VIIRS/MODIS detections in prior 24 h, nearest 0.0 km, latest 11:44Z; flagged on 1 earlier scan(s)
- *lahaina_pm*: Hotspot at 20.880N 156.670W: 2 px, Δ(3.9−11.2 µm) +20.7 K vs same time on previous days; present in 2/3 scans over 10 min (01:51Z, 01:56Z); no smoke plume attached; FIRMS: no VIIRS/MODIS detection in prior 24 h (GOES ahead of polar passes); flagged on 1 earlier scan(s)
- *pulehu*: Hotspot at 20.790N 156.350W: 2 px, Δ(3.9−11.2 µm) +28.0 K vs same time on previous days; present in 2/3 scans over 10 min (04:01Z, 04:06Z); no smoke plume attached; FIRMS: 71 VIIRS/MODIS detections in prior 24 h, nearest 4.3 km, latest 00:36Z; flagged on 1 earlier scan(s)
- *mauna_kea_resort*: Hotspot at 20.013N 155.827W: 3 px, Δ(3.9−11.2 µm) +23.5 K vs same time on previous days; present in 2/3 scans over 10 min (01:36Z, 01:41Z); no smoke plume attached; FIRMS: 1 VIIRS/MODIS detections in prior 24 h, nearest 7.1 km, latest 20:57Z; flagged on 1 earlier scan(s)

## Precision

- Reviews: **84 TP / 0 FP → precision 1.0** (per review, unique frames, positive + negative days).
- Negative-day frames with any false review: **0 / 19**.
- Of the 0 strict FPs, **0 are corroborated by FIRMS** (≥1 vegetation-fire detection within 3 km, ±24 h) — fire spreading beyond the origin radius or a fire not listed; counting those as real fires would give precision 1.0. The remaining 0 have no corroboration and are listed first under Failure cases.

| Negative case | Kind | Frames | Reviews (all FP) | Volcanic dismissed | FIRMS non-volcanic pts that day | Label source |
|---|---|---|---|---|---|---|
| clear_0806 | clear | 4 | 0 | 0 | 1 | No Maui/Hawaiʻi Island fires reported 5–6 Aug 2023 in AAR/F1/news (absence of evidence); FIRMS archive: only Kīlauea/Mauna Loa volcanic detections on these dates. |
| vog_0110 | vog | 4 | 0 | 0 | 34 | Kīlauea summit eruption 5 Jan–7 Mar 2023 (USGS HVO); vog over Honolulu 10 Jan 2023 (Hawaii News Now, secondary). Also exercises the volcanic exclusion mask. |
| vog_0912 | vog | 4 | 0 | 4 | 5 | Kīlauea summit eruption 10–16 Sep 2023 (USGS HVO); DOH release 11 Sep 2023 vog on west Hawaiʻi Island; Pāhala unhealthy for sensitive groups 12 Sep (HPR). |
| glint_0615 | glint | 3 | 0 | 1 | 25 | Near-solstice afternoon: GOES-18 specular point nearest Hawaiʻi ~13:00–15:00 HST. Kīlauea summit erupting 7–19 Jun 2023 (USGS HVO). |
| cloudy_0128 | cloudy | 4 | 0 | 0 | 0 | Measured: 84% of land pixels C13 < 273 K at 2023-01-28 23:01Z (eval/logs/cloudy.log); winter, no wildfire reports found; Kīlauea summit erupting (USGS HVO). |

## Latency (local replay, cached frames)

| Step | median s | max s |
|---|---|---|
| frame | 0.01 | 67.14 |
| background | 0.07 | 240.1 |
| agent | 0.03 | 0.58 |
| tool `track_plume` | 0.01 | 0.16 |
| tool `schedule_recheck` | 0.00 | 0.00 |
| tool `query_firms` | 0.00 | 0.55 |
| tool `request_human_review` | 0.01 | 0.03 |
| tool `dismiss` | 0.00 | 0.00 |

## Failure cases

- **Missed: Lahaina morning fire (Lahainaluna Rd)** — no review within 4 km in 30 scans. ![](failures_ctx/lahaina_am_0808T1746_missed.png)
- **Missed: Kula fire (Kualono Pl)** — no review within 3 km in 27 scans. ![](failures_ctx/kula_0808T2221_missed.png)
- **Missed: North Kohala (Akoni Pule Hwy) fire, Hawaiʻi Island** — no review within 6 km in 36 scans. ![](failures_ctx/kohala_ranch_0808T1401_missed.png)
