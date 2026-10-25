"""Narration for the demo video: caption text, spoken text, and TTS.

  video/.venv/bin/python video/tools/narration.py [s01 s02 ...]

TTS: OpenAI gpt-4o-mini-tts (voice "marin") when an OpenAI key is in .env.local or the macOS
Keychain (service "openai-api-key"), otherwise the macOS voice in MAC_VOICE. Output: video/narr/<id>.wav (48 kHz mono, trimmed, loudness-normalised).
Numbers follow REPORT.md; timings are "after the official first report", never "before humans".
"""
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NARR = os.path.join(ROOT, "video", "narr")
MAC_VOICE = os.environ.get("MAC_VOICE", "Samantha")
MAC_RATE = os.environ.get("MAC_RATE", "172")

# id: caption text (what is burned in). SPEAK overrides what the voice says where the
# spelling would be mispronounced (ʻokina, code identifiers, decimals).
SEG = {
    "s01": "Kahu Ola SmokeWatch. Built by Long Nguyen, a solo developer in Wailuku, Maui. "
           "It watches every GOES-18 scan of Hawaiʻi for new wildfires, lets an AI agent gather "
           "the evidence, and leaves the decision to publish with a human.",
    "s02": "The August 8, 2023 Lahaina fire killed 102 people. The satellites most people think "
           "of as fire detectors are polar orbiters, and they pass over Hawaiʻi only a few times "
           "a day. The first polar-orbiter detection of the Lahaina afternoon fire came almost "
           "seven hours after it was reported. GOES-18 sees Hawaiʻi every five minutes, but at "
           "two kilometres per pixel, and nobody watches it around the clock.",
    "s03": "Here is that afternoon, replayed from the real satellite data. The fire was first "
           "reported at 2:55 p.m. At 3:21, a thermal anomaly appears over Lahaina, but in one "
           "scan only, so the agent schedules a re-check instead of calling a person. At 3:36 "
           "the hotspot has persisted for three scans, and the agent asks for human review: "
           "41 minutes after the first official report, and about six hours before the first "
           "polar-orbiter detection, by MODIS.",
    "s04": "Every scan is stored, and the timeline viewer replays them. The colored cells are "
           "heat above the three-day background, exactly what the detector tests. Red circles "
           "are review requests, yellow circles are candidates. These historical days show the "
           "decisions of the deterministic rule policy.",
    "s05": "The live system scans every ten minutes on AWS Lambda. Most scans contain no fire, "
           "and silence is the normal, healthy state.",
    "s06": "Now the agent. This is the exact container image deployed on Lambda, replayed "
           "locally on the 3:36 scan, so the live review queue stays clean. Claude, through the "
           "Anthropic API, calls track_plume: is the hotspot in the earlier scans, is there a "
           "plume? Then query_firms: has a polar satellite already seen it? Then it decides, "
           "with a rationale built from that evidence. The rails are in code: no review without "
           "tracking, without two scans, or inside the volcanic mask.",
    "s06b": "The agent can only queue. A human reviewer approves, and only then does the plume "
            "reach the public layer.",
    "s07": "Under the hood, OpenCV 5 reprojects every scan, clusters hotspots with connected "
           "components, follows plumes with DIS optical flow, and renders every image and "
           "video you have seen. One container image runs two Lambda functions, triggered by "
           "EventBridge, with S3 for the archive and the review queue.",
    "s08": "We evaluated seven fires from August 2023 and five negative days. Five of the seven "
           "fires were sent to review, each before the first polar-orbiter detection, between "
           "31 minutes and 1 hour 40 minutes after the first official report. Review precision "
           "is 0.859. We missed two fires, the Lahaina morning fire and the Kula fire, both too "
           "small for a two-kilometre pixel. And honestly, the Claude agent made the same "
           "decisions as our rules on every case we tested. Its value today is the "
           "evidence-cited rationale, not better discrimination.",
    "s09": "Limitations: daytime false positives on Hawaiʻi Island on vog days, news-based "
           "report times for the Big Island fires, and the agent uses the Anthropic API because "
           "Bedrock was not yet enabled on this new account. A human stays in the loop, and "
           "every public estimate says it is not an official warning.",
    "s10": "Kahu Ola SmokeWatch: a faster satellite signal, a human in the loop, and honest "
           "numbers. Mahalo.",
}
SPEAK = {
    "Hawaiʻi": "Hawaii", "track_plume": "track plume", "query_firms": "query firms",
    "0.859": "zero point eight five nine", "GOES-18": "GOES 18", "2:55 p.m.": "2:55 PM",
}


def spoken(text: str) -> str:
    for a, b in SPEAK.items():
        text = text.replace(a, b)
    return text


def openai_key() -> str | None:
    """.env.local, else the macOS Keychain item "openai-api-key" (as in the earlier video
    pipeline). Held in memory for this run only; never written to a file or printed."""
    try:
        for line in open(os.path.join(ROOT, ".env.local")):
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    except FileNotFoundError:
        pass
    r = subprocess.run(["security", "find-generic-password", "-s", "openai-api-key", "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def tts(seg_id: str, key: str | None) -> str:
    raw = os.path.join(NARR, "raw")
    os.makedirs(raw, exist_ok=True)
    text = spoken(SEG[seg_id])
    if key:
        src = os.path.join(raw, seg_id + ".wav")
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=json.dumps({"model": "gpt-4o-mini-tts", "voice": "marin", "input": text,
                             "instructions": "Calm, confident product-demo narrator. Clear American "
                                             "English, measured pace, slight warmth, no hype.",
                             "response_format": "wav"}).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r, open(src, "wb") as f:
            f.write(r.read())
    else:
        src = os.path.join(raw, seg_id + ".aiff")
        subprocess.run(["say", "-v", MAC_VOICE, "-r", MAC_RATE, "-o", src, text], check=True)
    out = os.path.join(NARR, seg_id + ".wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-af",
                    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse,"
                    "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000", "-ar", "48000", "-ac", "1", out],
                   check=True)
    with open(os.path.join(NARR, seg_id + ".txt"), "w") as f:
        f.write(SEG[seg_id])
    return out


if __name__ == "__main__":
    key = openai_key()
    print("voice:", "openai/marin" if key else "macOS/" + MAC_VOICE)
    for sid in sys.argv[1:] or list(SEG):
        p = tts(sid, key)
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", p], capture_output=True, text=True).stdout.strip()
        print(sid, d)
