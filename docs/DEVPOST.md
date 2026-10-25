# Devpost submission — Kahu Ola SmokeWatch

Fields in the order of the Devpost form. Numbers are from `REPORT.md` and
`eval/results.md`. Wording rule: timings are "after the official first report", never
"before humans".

## Project name

Kahu Ola SmokeWatch

## Elevator pitch (≤ 200 characters)

GOES-18 wildfire watch for Hawaiʻi: OpenCV finds new heat every 5 min, a Claude agent gathers
evidence, a human approves what is published. Lahaina: hours before the first polar satellite.

## Links ("Try it out")

* Live page (prod): https://dgmg5co6ld62forty2ubtcwehy0uawnd.lambda-url.us-east-1.on.aws/
* Lahaina replay, deep link:
  https://dgmg5co6ld62forty2ubtcwehy0uawnd.lambda-url.us-east-1.on.aws/?date=2023-08-08&t=0136Z&region=maui#viewer
* Code: https://github.com/nguyenhuulong2222/kahuola-smokewatch
* Video: _(to be added)_ — video description must include: "Narration is AI-generated."

## Inspiration

On 8 August 2023 several wind-driven fires burned on Maui at the same time; the Lahaina fire
killed 102 people. The satellites most people think of as fire detectors are polar orbiters.
They pass a few times a day, and the first polar detection of the Lahaina afternoon fire came
almost 7 hours after it was reported. GOES-18 sees Hawaiʻi every 5 minutes, but nobody watches
its raw 2 km imagery around the clock. We wanted a watcher that never sleeps, and that never
publishes anything a human has not checked.

## What it does

* Every 10 minutes it reads the newest GOES-18 scan and compares each land pixel with the same
  clock time on the 3 previous days. New 3.9 µm heat becomes a candidate. Kīlauea and Mauna
  Loa are masked as volcanic.
* For each candidate an agent (Claude Sonnet 5.5) calls tools:
  * `track_plume`: is the hotspot in the earlier scans, and is there a moving smoke plume?
  * `query_firms`: did VIIRS or MODIS already see it?

  It then asks a human to review, schedules a re-check, or dismisses the candidate, with a
  rationale built from that evidence.
* Rails in code decide what can reach a reviewer: `track_plume` has run, the hotspot is in
  ≥ 2 scans, and it is outside the volcanic mask. The agent cannot publish.
* A reviewer approves or rejects on a small web page. Only approved detections enter the
  public GeoJSON layer, and every feature carries the disclaimer "automated satellite
  estimate, not a substitute for official warnings".
* The public page shows a Lahaina case study, a timeline viewer over every stored scan
  (true color, ΔT anomaly, FIRMS points), and the live status.

## How we built it

* **OpenCV 5:**
  * `remap` reprojects the ABI fixed grid onto a 0.02° Hawaiʻi grid.
  * `dilate` and `connectedComponentsWithStats` build the hotspot clusters.
  * The smoke mask uses morphology, `GaussianBlur` and `bilateralFilter`.
  * Plume geometry uses `findContours`, `approxPolyDP` and `distanceTransform`.
  * Plume motion uses `DISOpticalFlow`.
  * Rendering uses `applyColorMap`.
  * The case-study media is made with `VideoWriter` (H.264) and `imwriteanimation` (GIF).
* **AWS**, one SAM stack in us-east-1, in the same region as NOAA's GOES bucket:
  * one container image in Amazon ECR, used by two AWS Lambda functions (scan and web);
  * an Amazon EventBridge schedule every 10 minutes;
  * an Amazon S3 bucket for the background cache, scan archive, review queue and public
    layer, with lifecycle rules;
  * a Lambda Function URL for the page;
  * SSM Parameter Store for the API key.
* **Agent:** Claude through the Anthropic Messages API with tool use. A deterministic
  RulePolicy runs on the same tools and is the fallback on any LLM, network or FIRMS error.
* **Evaluation:** a replay of 148 archived GOES-18 scans. It covers 7 fires with cited
  first-report times and 5 negative days (clear, cloudy, vog ×2, sun glint). Each review is
  compared with the first detection by *any* polar orbiter (VIIRS S-NPP, VIIRS NOAA-20,
  MODIS).

## Challenges we ran into

* **Smoke is invisible at 2 km.** Our first smoke-first detector had zero recall on Lahaina,
  so we anchored on heat and kept smoke as supporting evidence.
* **Hawaiʻi has volcanoes.** Kīlauea is a permanent "fire", so we built a volcanic exclusion
  mask and made the review tool refuse volcanic candidates in code.
* **Sun-heated ground** looks like fire to a single-threshold test. A 3-day, same-clock-time,
  cloud-screened median background removed most of it. Vog-day heating on Hawaiʻi Island
  remains (see Limitations).
* **Bedrock was not yet enabled** on our new AWS account (model access and quota). The agent
  therefore runs on the Anthropic API; `BedrockPolicy` is implemented and selectable.
* **Honest timing:** we report against the official first report, and against any polar
  sensor, not just the slowest one.

## Accomplishments that we're proud of

* **Lahaina afternoon fire:** review 41 minutes after the official first report, about 6 hours
  before the first polar-orbiter detection (MODIS).
* **Recall:** 5 of 7 fires reviewed, each before the first polar-orbiter detection.
* **Precision:** 0.86 per review, or 0.95 when reviews confirmed by FIRMS as fire spread are
  counted as fires.
* **Safety:** a human-in-the-loop design where the model's confidence is never a gate and the
  agent cannot publish.
* **Cost:** about $1 a month to run, plus a few cents of LLM per scan with a candidate.

## What we learned

* At 2 km, heat is the signal, and persistence over 2–3 scans is the cheapest strong filter.
* An LLM agent is not automatically better than the rules. On our cases Claude made the same
  decision as the rules every time, including on the false positives. Its value was the
  evidence-cited rationale and an extensible tool loop. We report that rather than hide it.
* Tested properly, a published contextual fire test (MODIS C6) removed every real false
  positive. It also cost a fire and delayed three, so we kept it off.

## What's next

* Add GOES-18 Fire Detection and Characterization (FDC) and VIIRS 375 m as agent tools.
* Find a better perception signal for vog-day heating.
* Bedrock once the account quota is enabled.
* Seek a pilot of the reviewer workflow with county emergency management.

## Limitations (also in REPORT.md)

* Missed the Lahaina morning and Kula fires, which were too small for a 2 km pixel.
* The Pūlehu attribution is uncertain.
* Daytime false positives on Hawaiʻi Island on vog days.
* Hawaiʻi Island report times come from news coverage.
* The agent uses the Anthropic API because of the Bedrock block.

## Built with

OpenCV 5, AWS Lambda, Amazon S3, Amazon EventBridge, AWS SAM, Amazon ECR, NOAA GOES-18 Open
Data on AWS, NASA FIRMS, Claude (Anthropic API), Python

Tags: `opencv` `aws-lambda` `amazon-s3` `amazon-eventbridge` `aws-sam` `amazon-ecr`
`noaa-goes` `nasa-firms` `claude` `anthropic` `python`
