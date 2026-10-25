"""Write the "Contextual test — on vs off" section of eval/results.md.

Reads eval/results.json (contextual off, the default pipeline) and eval/results_ctx.json
(`run_eval.py --contextual`) and replaces the marked section at the end of results.md,
so re-running run_eval.py followed by this script reproduces both sets of numbers.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "<!-- contextual-comparison -->"


def _t(s):
    return s[5:16].replace("T", " ") + "Z" if s else "—"


def _d(m):
    if m is None:
        return "—"
    sign, m = ("+" if m >= 0 else "−"), abs(m)
    return f"{sign}{m // 60}h{m % 60:02d}m" if m >= 60 else f"{sign}{m}m"


def section(off, on) -> list[str]:
    po, pn = off["precision"], on["precision"]
    unc = lambda p: sum(1 for f in p["fp_detail"] if not f["firms_corroborated"])
    L = [MARK, "", "## Contextual test (MODIS C6 parameters) — on vs off", "",
         "Experiment, **off by default**. `fire.contextual_test` adapts the MODIS contextual fire test "
         "(Giglio et al. 2003, RSE 87:273–282; unchanged in Collection 6, Giglio, Schroeder & Justice "
         "2016, RSE 178:31–41) to our background-differenced fields: window 3×3 → 21×21 until ≥ 25 % "
         "and ≥ 8 valid neighbours; fire = T4 > 360 K day / 320 K night, or (ΔT > mean + 3.5 δ and "
         "ΔT > mean + 6 K and T4 > mean + 3 δ and (T11 > mean + δ − 4 K or δ′4 > 5 K)), δ = mean "
         "absolute deviation. Published constants, not tuned on this eval. "
         "Off: `run_eval.py`; on: `run_eval.py --contextual` (results_ctx.md/json).", "",
         "| Fire | First GOES review — off | Δ vs report | Δ vs first polar | First GOES review — **on** | Δ vs report | Δ vs first polar |",
         "|---|---|---|---|---|---|---|"]
    for a, b in zip(off["events"], on["events"]):
        L.append(f"| {a['name']} | {_t(a['first_review'])} | {_d(a['review_minus_report_min'])} | "
                 f"{_d(a['review_minus_first_polar_min'])} | {_t(b['first_review'])} | "
                 f"{_d(b['review_minus_report_min'])} | {_d(b['review_minus_first_polar_min'])} |")
    L += ["", "| | off | on |", "|---|---|---|",
          f"| Reviews matching a known fire (TP) | {po['tp_reviews']} | {pn['tp_reviews']} |",
          f"| False reviews (FP) | {po['fp_reviews']} | {pn['fp_reviews']} |",
          f"| — of which FIRMS-corroborated (fire spread) | {po['fp_firms_corroborated']} | {pn['fp_firms_corroborated']} |",
          f"| — of which uncorroborated (real FP) | {unc(po)} | {unc(pn)} |",
          f"| Precision (strict) | {po['precision']} | {pn['precision']} |",
          f"| Negative-day frames with a false review | {po['false_alarm_frames']}/{po['negative_frames']} | "
          f"{pn['false_alarm_frames']}/{pn['negative_frames']} |", ""]
    lost = [a["name"] for a, b in zip(off["events"], on["events"]) if a["first_review"] and not b["first_review"]]
    later = [a["name"] for a, b in zip(off["events"], on["events"])
             if a["first_review"] and b["first_review"] and b["first_review"] > a["first_review"]]
    keep = unc(pn) < unc(po) and not lost and not later
    L += [f"**Decision rule** (fixed before the run): enable only if it removes real FPs AND loses no "
          f"fire AND delays no first review. Result: real FPs {unc(po)} → {unc(pn)}; fires lost: "
          f"{', '.join(lost) or 'none'}; first review later: {', '.join(later) or 'none'}. "
          f"→ **{'enabled' if keep else 'kept OFF'}**.", ""]
    return L


def main():
    off = json.load(open(os.path.join(HERE, "results.json")))
    on = json.load(open(os.path.join(HERE, "results_ctx.json")))
    path = os.path.join(HERE, "results.md")
    md = open(path).read().split(MARK)[0].rstrip() + "\n\n"
    with open(path, "w") as f:
        f.write(md + "\n".join(section(off, on)))
    print("updated", os.path.relpath(path))


if __name__ == "__main__":
    main()
