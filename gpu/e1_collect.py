#!/usr/bin/env python3
"""Assemble the E1 result files into one retained artifact, with the arithmetic written out.

Run against the directory of E1 JSONs pulled off the box. Emits e1-summary.json in the pattern of
EVIDENCE/undeclared-gpu-rerun/iteration-count.json: every headline number, the multiplication that
produced it, where it came from, and what it does not cover.

Nothing here recomputes a measurement. It reads what the runs measured and does arithmetic you can
check by hand.
"""

import argparse
import json
from pathlib import Path


def load(d, name):
    p = Path(d) / name
    return json.loads(p.read_text()) if p.exists() else None


def _by_duty(f):
    """Every pair's measured drain, grouped by duty. A dict comprehension over the counters would
    keep only the last pair at each duty and silently drop the rest."""
    out = {}
    for h in f.get("hidden_work_counters") or []:
        out.setdefault(str(h["duty"]), []).extend(
            round(1000 * x, 1) for x in h.get("reaction_latency_s") or [])
    return out


def _shares(f, solo_rate):
    """Device share actually taken by the competing process, per pair.

    Uses the process's own solo calibration where it recorded one, and otherwise the solo rate
    passed in, which comes from a calibration run on the same device in the same session.
    """
    if not f:
        return None
    out = []
    for h in f.get("hidden_work_counters") or []:
        solo = h.get("solo_iterations_per_second") or solo_rate
        elapsed = h.get("counted_elapsed_s")
        if not (solo and elapsed):
            continue
        rate = h["iterations"] / elapsed
        out.append({"duty_label": h["duty"], "iterations": h["iterations"],
                    "counted_elapsed_s": round(elapsed, 2),
                    "iterations_per_second": round(rate, 1),
                    "solo_iterations_per_second": round(solo, 1),
                    "device_share_taken": round(rate / solo, 4),
                    "solo_rate_source": ("this process" if h.get("solo_iterations_per_second")
                                         else "separate calibration run, same device, same session")})
    return out


def part_a(d, solo_rate):
    a1 = load(d, "A1-sep-nonadaptive.json")
    a1b = load(d, "A1b-sep-nonadaptive-noreadywait.json")
    a1c = load(d, "A1c-sep-nonadaptive-unbounded.json")
    a2c = load(d, "A2c-sep-adaptive-unbounded.json")
    a2 = load(d, "A2-sep-adaptive.json")
    bound = load(d, "A34-bound.json")
    rows = []
    if a1 and a2:
        by_duty_2 = {r["duty"]: r for r in a2["rows"]}
        for r in a1["rows"]:
            adp = by_duty_2.get(r["duty"], {})
            rows.append({
                "duty": r["duty"],
                "nonadaptive_drop_pct": round(r["drop_pct"], 2),
                "nonadaptive_stdev_of_ratio_pct": round(100 * r["stdev_ratio"], 3),
                "adaptive_drop_pct": round(adp.get("drop_pct", float("nan")), 2),
                "adaptive_stdev_of_ratio_pct": round(100 * adp.get("stdev_ratio", 0), 3),
                "nonadaptive_detected_at_3sigma_of_own_spread":
                    (1 - r["mean_ratio"]) > 3 * r["stdev_ratio"] if r["stdev_ratio"] else None,
                "adaptive_detected_at_3sigma_of_own_spread":
                    ((1 - adp["mean_ratio"]) > 3 * adp["stdev_ratio"]
                     if adp.get("stdev_ratio") else None),
            })
    out = {
        "what_this_is": ("The published grid from REPRODUCE section 5, run twice in one session: "
                         "once against the adversary that never reacts, once against the one that "
                         "pauses while the challenge is in flight. Separate competing process."),
        "separate_process_rows": rows,
        "reaction_latency_measured_s": ([h.get("reaction_latency_s") for h in
                                         (a2.get("hidden_work_counters") or [])] if a2 else None),
        "device_share_actually_taken": _shares(a1, solo_rate),
        "device_share_note": (
            "The --duty label is not the share of the device the competing process takes. Matmul "
            "submission is asynchronous and costs microseconds against a matmul of about two "
            "milliseconds, so a work slice queues more device work than the slice is long. The "
            "share below is the competitor's measured iteration rate over the solo rate on the "
            "same device, and it is the figure that should be quoted."),
        "contention_ready_wait_s": (a2.get("contention_ready_wait_s") if a2 else None),
    }
    if a1b:
        out["original_protocol_control"] = {
            "what_this_is": (
                "The same non-adaptive arm run under the ORIGINAL protocol, which starts measuring "
                "two seconds after launching the undeclared process without confirming that "
                "process has finished creating its CUDA context. The published separate-process "
                "figures were measured this way. Reported because the difference is large: it "
                "separates the cost of a second context being CREATED inside the measured window "
                "from the cost of a second context COMPETING for the device."),
            "drop_pct_by_duty": {r["duty"]: round(r["drop_pct"], 2) for r in a1b["rows"]},
            "competitor_startup_excluded_from_window":
                a1b.get("competitor_startup_excluded_from_window"),
            "startup_wait_observed_s_in_the_other_arm":
                (a1.get("contention_ready_wait_s") if a1 else None),
        }
    if a1c:
        out["unbounded_submission_control"] = {
            "what_this_is": (
                "The ORIGINAL protocol restored exactly: unbounded submission, so a work slice queues far more "
                "device work than the slice is long, and no wait for the competitor to be ready. This is how "
                "the published separate-process figures were measured, and it reproduces them."),
            "drop_pct_by_duty": {r["duty"]: round(r["drop_pct"], 2) for r in a1c["rows"]},
            "device_share_actually_taken": _shares(a1c, solo_rate),
            "published_july_figures_for_comparison": {0.02: 35.8, 0.05: 43.4, 0.25: 48.8},
            "reading": (
                "The duty label was never the share of the device taken. Under the original protocol a "
                "competitor labelled 2 percent took about 65 percent of the device and one labelled 25 percent "
                "took essentially all of it, which is why the drop was large. Bounding submission so the "
                "process runs at most four multiplications ahead of the device brings the share close to the "
                "label and the drop falls to 11 to 14 percent."),
        }
    if a2c:
        out["adaptive_against_an_unbounded_queue"] = {
            "what_this_is": (
                "The adaptive adversary again, but submitting without a bound, so that when it decides to stop "
                "there is a long queue of already-submitted work still to execute. The harness gives it half a "
                "second between being told and the challenge starting."),
            "drop_pct_by_duty": {r["duty"]: round(r["drop_pct"], 2) for r in a2c["rows"]},
            "detected_by_duty": {r["duty"]: ((1 - r["mean_ratio"]) > 3 * r["stdev_ratio"]
                                             if r["stdev_ratio"] else None) for r in a2c["rows"]},
            "measured_drain_ms_by_duty": _by_duty(a2c),
            "drain_budget_ms": 500,
            "device_share_actually_taken": _shares(a2c, solo_rate),
            "reading": (
                "The evasion needs the operator's own queue to have drained before the challenge starts. "
                "Measured, an unbounded queue takes 39 to 61 milliseconds to drain at the lowest level and 1.38 "
                "to 1.46 seconds at the highest, against the half second it was given. Where the drain fits in "
                "the budget nothing is detected; where it overruns, the overrun executes inside the challenge "
                "window and is detected. So an operator that pauses on the challenge also has to bound how far "
                "it runs ahead of the device, which is an engineering requirement rather than a free win."),
        }
    if bound:
        out["same_context_bound"] = {
            "what_this_is": ("One null distribution of clean runs, then the duty response measured "
                             "against BOTH adversaries, so the two bounds share a denominator."),
            "clean_mean_tflops": round(bound["clean_mean_tflops"], 1),
            "null_sigma_paired_pct": round(100 * bound["null_sigma_paired"], 2),
            "null_sigma_unpaired_pct": round(100 * bound["null_sigma_unpaired"], 2),
            "threshold_paired_drop_pct": round(100 * bound["detection_threshold_paired_drop"], 2),
            "threshold_unpaired_drop_pct": round(100 * bound["detection_threshold_unpaired_drop"], 2),
            "modes": {
                m: {
                    "duty_response_drop_pct": {r["duty"]: round(100 * r["drop"], 2)
                                               for r in v["duty_response"]},
                    "undeclared_iterations_inside_the_challenge_window":
                        {r["duty"]: r.get("undeclared_iterations_in_window")
                         for r in v["duty_response"]},
                    "hideable_duty_paired": v["hideable_duty_paired"],
                    "hideable_duty_unpaired": v["hideable_duty_unpaired"],
                    "max_measured_drop_pct": round(100 * v["max_measured_drop"], 2),
                    "no_tested_duty_detectable_paired": v["no_tested_duty_detectable_paired"],
                    "no_tested_duty_detectable_unpaired": v["no_tested_duty_detectable_unpaired"],
                } for m, v in bound["modes"].items()
            },
        }
    return out


def duty_rows(f):
    if not f:
        return None
    rows = []
    for p in f["points"]:
        s = p["summary"]
        rows.append({
            "verifier_duty_nominal": p["duty"],
            "verifier_duty_achieved": round(p["duty_achieved"], 4),
            "challenge_windows": p["challenge_windows"],
            "in_window_drop_pct": None if s["in_window_drop_pct"] is None
                                  else round(s["in_window_drop_pct"], 2),
            "in_window_null_sigma_pct": None if s["in_window_null_sigma_pct"] is None
                                        else round(s["in_window_null_sigma_pct"], 2),
            "detected_at_3sigma": s["detected_at_3sigma"],
            "out_of_window_drop_pct": None if s["out_window_drop_pct"] is None
                                      else round(s["out_window_drop_pct"], 2),
            "hidden_iterations": s["hidden_iterations"],
            "hidden_counted_seconds": (None if s["hidden_counted_seconds"] is None
                                       else round(s["hidden_counted_seconds"], 2)),
            "hidden_iterations_per_second": (None if s["hidden_iterations_per_second"] is None
                                             else round(s["hidden_iterations_per_second"], 2)),
            "hidden_kept_vs_no_verifier": (None if s.get("hidden_yield_vs_ceiling") is None
                                           else round(s["hidden_yield_vs_ceiling"], 4)),
            "one_minus_duty_for_comparison": round(1 - p["duty"], 4),
        })
    return {
        "adversary": f["adversary"], "hidden_work_mode": f["hidden_work_mode"],
        "run_seconds": f["run_seconds"],
        "challenge_window_seconds": f["challenge_window_seconds"],
        "hidden_ceiling_iterations_per_second": f.get("hidden_ceiling_iterations_per_second"),
        "rows": rows,
    }


def part_c(d, prefix, reacts):
    rows = []
    for r in reacts:
        f = load(d, f"{prefix}{r}.json")
        if not f:
            continue
        p = f["points"][0]
        s = p["summary"]
        lat = []
        hc = p["contended"].get("hidden_counters") or {}
        lat = hc.get("reaction_latency_s") or []
        rows.append({
            "react_ms_nominal": r,
            "reaction_latency_measured_ms_mean": (round(1000 * sum(lat) / len(lat), 2)
                                                  if lat else None),
            "reaction_latency_measured_ms_max": round(1000 * max(lat), 2) if lat else None,
            "yield_events": hc.get("yield_events"),
            "challenge_window_ms": int(1000 * f["challenge_window_seconds"]),
            "overlap_fraction_of_window": round(r / (1000 * f["challenge_window_seconds"]), 4),
            "in_window_drop_pct": None if s["in_window_drop_pct"] is None
                                  else round(s["in_window_drop_pct"], 2),
            "in_window_null_sigma_pct": None if s["in_window_null_sigma_pct"] is None
                                        else round(s["in_window_null_sigma_pct"], 2),
            "threshold_3sigma_drop_pct": (None if s["in_window_null_sigma_pct"] is None
                                          else round(3 * s["in_window_null_sigma_pct"], 2)),
            "detected_at_3sigma": s["detected_at_3sigma"],
            "hidden_iterations": s["hidden_iterations"],
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--env", default=None, help="JSON file with the environment block")
    ap.add_argument("--solo-rate", type=float, default=None,
                    help="solo matmul iterations per second measured on the same device in the "
                         "same session, used for runs whose hidden process did not calibrate "
                         "itself")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d = args.dir

    n = 8192
    flop_per_iter = 2 * n ** 3

    b1 = load(d, "B1r-sep-adaptive.json")
    b2 = load(d, "B2r-inline-adaptive.json")
    b3 = load(d, "B3-sep-nonadaptive.json")

    summary = {
        "schema": "ccverify.e1.summary.v1",
        "what_this_is": (
            "E1: the adaptive adversary. Every occupancy figure previously measured in this "
            "repository, including the roughly 6 percent and roughly 19 percent hideable widths, "
            "was measured against an adversary that never reacts to the verifier. E1 measures the "
            "one that pauses its undeclared work while a challenge is in flight and resumes after, "
            "and sweeps the two axes that then matter: the fraction of wall time the verifier "
            "spends challenging, and how fast the operator has to react."
        ),
        "adversary_model": (
            "The operator owns the machine, so its own orchestrator is told when a challenge is in "
            "flight and pauses the undeclared kernels before running the puzzle. It is implemented "
            "that way, by a flag file the harness writes, and not as a detector, because assuming "
            "the operator has to infer the challenge would understate it. The reaction-latency "
            "sweep then models the weaker operator that does have to notice."
        ),
        "part_a_does_the_check_still_fire": part_a(d, args.solo_rate),
        "part_b_verifier_duty_cycle": {
            "what_this_is": (
                "Per verifier duty cycle d: a reference run and a contended run over the same "
                "wall-clock window with the same randomly placed challenge windows. Recorded are "
                "the in-window drop against the reference's own window-to-window null, the drop "
                "outside the windows, and the undeclared work actually completed."
            ),
            "separate_process_adaptive": duty_rows(b1),
            "same_context_adaptive": duty_rows(b2),
            "separate_process_nonadaptive_control": duty_rows(b3),
        },
        "the_confound_that_was_found_and_fixed": {
            "what_happened": (
                "The first verifier-duty run reported the contended run as about 7 percent FASTER inside the "
                "challenge windows than the reference run, at every duty cycle, against a null of 0.05 percent. "
                "The drain interval before each challenge was being applied only in the run that had hidden "
                "work in it, so the contended run's timed windows each began after a short idle pause the "
                "reference never got, and the comparison measured the device's clock response to that pause "
                "rather than contention."),
            "why_it_matters": (
                "The offset was constant and in the direction that hides an adversary, large enough to have "
                "masked any real drop up to that size. It would have manufactured the negative result this "
                "experiment was looking for."),
            "the_fix": "the drain interval is now applied in the reference run too",
            "uncorrected_run_retained_as": "B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json",
            "what_was_not_affected": (
                "The undeclared-work-kept figures, which are checkable against the uncorrected run: 0.720 "
                "against 0.721 at a 25 percent duty cycle and 0.468 against 0.469 at 50 percent."),
        },
        "part_c_how_fast_must_the_operator_react": {
            "what_this_is": (
                "Fixed verifier duty cycle, challenge windows of a fixed length, sweeping how long "
                "the undeclared work keeps running after the challenge is signalled. A reaction "
                "that takes R of a W millisecond window leaves R/W of that window overlapped at "
                "full contention, so the expected drop is roughly (R/W) times the full-contention "
                "drop. Whether it clears three sigma of the null is the result."
            ),
            "separate_process": part_c(d, "C-sep-react", [0, 25, 50, 100, 250, 500]),
            "same_context": part_c(d, "C-inline-react", [0, 100, 250]),
        },
        "flop_arithmetic": {
            "note": "Exact arithmetic, not an estimate. Check the multiplication yourself.",
            "source": ("contention.py runs one 8192x8192 bfloat16 matmul per iteration; "
                       "e1_verifier_duty.py's inline path runs one per hidden iteration at the "
                       "same size."),
            "matrix_dimension_n": n,
            "n_cubed": n ** 3,
            "flop_per_iteration_expression": "2 * n^3",
            "flop_per_iteration": flop_per_iter,
            "how_to_convert": ("total FLOP = iterations x %d. Divide by 1e15 for PFLOP."
                               % flop_per_iter),
            "excluded": ("The declared chain's row-wise normalize() is excluded from declared "
                         "throughput arithmetic for the same reason as the earlier runs: it adds "
                         "roughly 3*n^2, about 0.037 percent, so figures are slight underestimates."),
        },
        "provenance_and_scope": {
            "how_obtained": (
                "Undeclared-work iteration counts live in the workload's own output on the "
                "operator's side of the boundary, which the disclosure discipline deliberately "
                "never transmits. They are extracted here as RESEARCHER-side measurements so the "
                "published arithmetic is checkable. A regulator would receive none of them "
                ""
            ),
            "raw_withheld_output_retained": False,
            "time_bounded_not_count_bounded": (
                "Every run is bounded by wall-clock time, so iteration counts differ between runs "
                "and are not meant to reproduce. Quote magnitudes, not precise counts."
            ),
            "single_session": True,
            "platform": ("one NVIDIA H100 NVL in an AMD SEV-SNP confidential VM, CC mode ON; "
                         "cooperative operator running these scripts"),
            "what_this_does_not_cover": [
                "A verifier's challenge that the operator cannot distinguish from ordinary "
                "production traffic. The adaptive attack here depends on the operator knowing a "
                "challenge is in flight, which it does because the challenge is a seeded job "
                "supplied through a channel it controls. Whether a covert challenge is achievable "
                "is not tested and is not claimed either way.",
                "Any adversary that shapes its hidden work to imitate the declared profile rather "
                "than simply pausing.",
                "Multi-tenant or multi-GPU devices; one device, one operator.",
                "Absolute throughput reproducibility, which depends on the device's thermal state "
                "and on what else the cloud is running.",
            ],
        },
        "relationship_to_the_published_numbers": (
            "The 6 percent and 19 percent widths this field used to call correct were not device shares at "
            "all. They were the sweep's own duty knob printed under a device-capacity label. Measured "
            "directly, the width is a detection threshold of 2.81 percent of throughput, which is 2.4 to 2.7 "
            "percent of a device depending on the denominator, with a 95 percent interval on the threshold of "
            "roughly 1.7 to 6.7 percent. See EVIDENCE/e1v2-adaptive-adversary/ and "
            "EVIDENCE/e1c-lowload-bound/. Separately, every one of those figures is a non-adaptive-adversary "
            "figure: against an operator that pauses while the challenge is in flight there is no width, "
            "because nothing undeclared executes inside the window. "
        ),
        "reproduce": "gpu/E1-RUNPLAN.md",
    }
    if args.env:
        summary["environment"] = json.loads(Path(args.env).read_text())

    Path(args.out).write_text(json.dumps(summary, indent=2, sort_keys=False))
    print(f"written {args.out}")


if __name__ == "__main__":
    main()
