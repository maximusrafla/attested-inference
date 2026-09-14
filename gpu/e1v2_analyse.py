#!/usr/bin/env python3
"""Read the E1 v2 result files and print every headline number with its decision rule.

Deliberately separate from the harness, and it recomputes from the raw per-window and per-repeat
values rather than trusting any summary field, so a mistake in the harness's own aggregation
cannot hide here. Prints the alternative decision rules side by side, because the single largest
error in v1 was quoting one rule as though it were the only one.
"""

import argparse
import json
import math
import statistics
from pathlib import Path


def L(d, name):
    p = Path(d) / name
    return json.loads(p.read_text()) if p.exists() else None


def pct(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}%"


def sweep_table(f, label):
    if not f:
        return
    print(f"\n{label}   [{f.get('adversary')}]")
    print(f"  {'duty knob':>10}{'drop':>9}{'sd of ratio':>14}{'device share taken':>21}")
    hidden = {}
    for h in f.get("hidden_work_counters") or []:
        hidden.setdefault(h["duty_requested"], []).append(h.get("device_share_taken"))
    for r in f["rows"]:
        sh = hidden.get(r["duty"], [])
        sh = [x for x in sh if x is not None]
        shs = f"{100*statistics.mean(sh):.2f}%" if sh else "n/a"
        print(f"  {r['duty']:>10}{r['drop_pct']:>8.2f}%{100*r['stdev_ratio']:>13.2f}%{shs:>21}")
    note = f.get("what_the_adaptive_arm_actually_measures")
    if note:
        print(f"  NOTE: {note}")


def bound_table(f, label):
    if not f:
        return
    print(f"\n{label}")
    cal = f["solo_calibration"]
    print(f"  solo calibration (retained): {cal['iterations_per_second']:.1f} matmul/s = "
          f"{cal['tflops']:.1f} TFLOP/s")
    print(f"  clean mean {f['clean_mean_tflops']:.1f} TFLOP/s   "
          f"paired sigma {100*f['null_sigma_paired']:.2f}%   "
          f"unpaired sigma {100*f['null_sigma_unpaired']:.2f}%")
    if f.get("null_sigma_paired_excl_warmup"):
        print(f"  excluding the warmup run: paired {100*f['null_sigma_paired_excl_warmup']:.2f}%   "
              f"unpaired {100*f['null_sigma_unpaired_excl_warmup']:.2f}%")
    print(f"  paired null mean ratio {f['paired_null_mean_ratio']:.5f} "
          f"(1.0 means no systematic bias between the halves of a pair)")
    n_pairs = len(f["clean_runs"]) // 2
    sd = f["null_sigma_paired"]
    # chi-square 95% interval on a standard deviation with n_pairs-1 degrees of freedom
    # chi-square 0.025 / 0.975 quantiles by degrees of freedom
    chi = {2: (0.0506, 7.378), 3: (0.216, 9.348), 4: (0.484, 11.143), 5: (0.831, 12.833),
           6: (1.237, 14.449)}.get(n_pairs - 1)
    if chi:
        lo, hi = sd * math.sqrt((n_pairs - 1) / chi[1]), sd * math.sqrt((n_pairs - 1) / chi[0])
        print(f"  95% interval on the paired sigma from {n_pairs-1} df: "
              f"{100*lo:.2f}% to {100*hi:.2f}%  <-- the width inherits this")
    for mode, m in f["modes"].items():
        print(f"  -- {mode}")
        if m["nothing_undeclared_executed"]:
            print("     nothing undeclared executed inside the window at any level; arithmetic, "
                  "not a detection result")
            continue
        for r in m["duty_response"]:
            print(f"     knob {r['duty_knob']:<6} drop {100*r['drop']:>6.2f}%   "
                  f"DEVICE SHARE {100*r['device_share_taken']:>6.2f}%")
        hp = m.get("hideable_device_share_paired")
        hu = m.get("hideable_device_share_unpaired")
        dp = m.get("hideable_duty_knob_paired_DEPRECATED")
        print(f"     hideable DEVICE SHARE: paired "
              f"{'n/a' if hp is None else f'{100*hp:.2f}%'}   stored ceiling "
              f"{'n/a' if hu is None else f'{100*hu:.2f}%'}")
        print(f"     (the duty knob at that threshold, which v1 wrongly reported as device "
              f"capacity: {'n/a' if dp is None else f'{100*dp:.2f}%'})")


def duty_table(f, label):
    if not f:
        return
    print(f"\n{label}   [{f['adversary']}; {f['hidden_work_mode']}; "
          f"react {f['react_ms']:.0f} ms; settle {f['settle_s']}s; "
          f"{f['repeats_per_point']} repeats]")
    print(f"  {'duty':>6}{'drop':>9}{'sd(reps)':>10}{'3*SE':>8}{'detected':>10}"
          f"{'out-win':>10}{'share':>9}{'kept':>8}{'1-d':>7}")
    for p in f["points"]:
        a = p["aggregate"]
        drops = [r["summary"].get("in_window_drop_pct") for r in p["repeats"]
                 if r["summary"].get("in_window_drop_pct") is not None]
        sd = statistics.stdev(drops) if len(drops) > 1 else None
        se3 = 3 * sd / math.sqrt(len(drops)) if sd else None
        det = (statistics.mean(drops) > se3) if se3 else None
        sh = a.get("hidden_device_share_mean")
        kept = a.get("hidden_kept_vs_no_verifier")
        print(f"  {p['duty']:>6.2f}{pct(a.get('in_window_drop_pct_mean')):>9}"
              f"{pct(sd):>10}{pct(se3):>8}{str(det):>10}"
              f"{pct(a.get('out_window_drop_pct_mean')):>10}"
              f"{pct(100*sh if sh else None):>9}"
              f"{'-' if kept is None else format(kept,'.3f'):>8}"
              f"{1-p['duty']:>7.2f}")


def reaction_table(d, prefix, reacts, label):
    rows = []
    for r in reacts:
        f = L(d, f"{prefix}{r}.json")
        if not f:
            continue
        p = f["points"][0]
        drops = [x["summary"].get("in_window_drop_pct") for x in p["repeats"]
                 if x["summary"].get("in_window_drop_pct") is not None]
        lats = []
        for x in p["repeats"]:
            hc = x["contended"].get("hidden_counters")
            if hc:
                lats += [1000 * y for y in hc.get("reaction_latency_s", [])]
        sd = statistics.stdev(drops) if len(drops) > 1 else None
        se3 = 3 * sd / math.sqrt(len(drops)) if sd else None
        mean_drop = statistics.mean(drops) if drops else None
        rows.append({"nominal": r, "measured_ms": statistics.mean(lats) if lats else None,
                     "drop": mean_drop, "sd": sd, "thr": se3,
                     "detected": (mean_drop > se3) if (se3 and mean_drop is not None) else None})
    if not rows:
        return rows
    print(f"\n{label}")
    print(f"  {'asked':>7}{'measured':>11}{'drop':>9}{'sd(reps)':>10}{'3*SE':>8}{'caught':>9}")
    for r in rows:
        meas = "n/a" if r["measured_ms"] is None else "%.1fms" % r["measured_ms"]
        print(f"  {r['nominal']:>7}{meas:>11}"
              f"{pct(r['drop']):>9}{pct(r['sd']):>10}{pct(r['thr']):>8}{str(r['detected']):>9}")
    caught = [r for r in rows if r["detected"]]
    missed = [r for r in rows if r["detected"] is False]
    if caught and missed:
        hi = min(caught, key=lambda r: r["nominal"])
        lo = max(missed, key=lambda r: r["nominal"])
        lo_m = lo["measured_ms"] or lo["nominal"]
        hi_m = hi["measured_ms"] or hi["nominal"]
        print(f"  BRACKETED BY MEASUREMENT: not caught at {lo_m:.1f} ms, caught at {hi_m:.1f} ms.")
        print( "  Quote the bracket. A fit through these points is an extrapolation and v1's was")
        print( "  contradicted by its own zero-reaction measurement.")
    return rows



def e1b_table(d, label):
    """E1b (2026-09-03): hold the verifier's duty cycle fixed and vary the number of challenge
    windows. The v2 separate-process leak, if it is a cost paid at every pause, should grow with
    the window count and not with the duty cycle. Files are E1b-<mode>-win<W>.json.
    Recomputed from the per-repeat values; the harness's own aggregate fields are not used.

    Outlier rule, stated so it can be argued with: a repeat is FLAGGED when its reference run's
    in-window mean sits more than 2 percent from the median reference in-window mean of the same
    file. That catches a reference measured in a different device state (a cold, boosted GPU on
    the first pair of a session) without looking at the drop itself. Both rows are printed."""
    files = sorted(Path(d).glob("E1b-*.json"))
    if not files:
        return
    print(f"\n{label}")
    print(f"  {'arm':>10}{'win s':>7}{'windows':>9}{'duty':>6}{'drop':>8}{'sd':>7}{'3*SE':>7}"
          f"{'n':>3}{'det':>6}{'out-win':>9}{'share':>8}{'lost ms/win':>13}{'react ms':>10}")
    rows = []
    for fp in files:
        f = json.loads(fp.read_text())
        mode = "separate" if "separate" in f["hidden_work_mode"] else "inline"
        for p in f["points"]:
            reps = p["repeats"]
            refmeans = [statistics.mean(r["reference"]["in_window_tflops"]) for r in reps]
            med = statistics.median(refmeans)
            flagged = [abs(x - med) / med > 0.02 for x in refmeans]
            lats = []
            for r in reps:
                hc = r["contended"].get("hidden_counters") or {}
                lats += [1000 * x for x in hc.get("reaction_latency_s", [])]
            chal_s = statistics.mean(sum(c["elapsed_s"] for c in r["contended"]["challenge_windows"])
                                     for r in reps)
            for use_flag, tag in ((False, ""), (True, "*")):
                sel = [r for r, fl in zip(reps, flagged) if not (use_flag and fl)]
                if use_flag and len(sel) == len(reps):
                    continue
                drops = [r["summary"]["in_window_drop_pct"] for r in sel]
                ows = [r["summary"].get("out_window_drop_pct") for r in sel]
                shares = [r["summary"].get("hidden_device_share") for r in sel
                          if r["summary"].get("hidden_device_share")]
                n = len(drops)
                mean = statistics.mean(drops)
                sd = statistics.stdev(drops) if n > 1 else None
                se3 = 3 * sd / math.sqrt(n) if sd is not None else None
                rows.append({"mode": mode, "tag": tag, "win": f["challenge_window_seconds"],
                             "m": p["challenge_windows"],
                             "duty": statistics.mean(r["contended"]["duty_achieved"] for r in sel),
                             "drop": mean, "sd": sd, "se3": se3, "n": n,
                             "det": (mean > se3) if se3 is not None else None,
                             "drops": drops, "ow": statistics.mean(ows), "chal_s": chal_s,
                             "share": statistics.mean(shares) if shares else None,
                             "lost_ms_per_win": mean / 100 * chal_s * 1000 / p["challenge_windows"],
                             "react_ms": statistics.mean(lats) if lats else None,
                             "react_max": max(lats) if lats else None,
                             "flagged": [i for i, fl in enumerate(flagged) if fl],
                             "refmeans": refmeans})
    rows.sort(key=lambda r: (r["mode"] != "separate", r["m"], r["tag"]))
    for r in rows:
        rm = "n/a" if r["react_ms"] is None else f"{r['react_ms']:.1f}"
        print(f"  {r['mode']+r['tag']:>10}{r['win']:>7.2f}{r['m']:>9}{r['duty']:>6.2f}"
              f"{pct(r['drop']):>8}{pct(r['sd']):>7}{pct(r['se3']):>7}{r['n']:>3}"
              f"{str(r['det'])[:5]:>6}{pct(r['ow']):>9}"
              f"{pct(100*r['share'] if r['share'] else None):>8}"
              f"{r['lost_ms_per_win']:>13.1f}{rm:>10}")
    print("  * = flagged repeats removed (reference in-window mean >2% from the file's median "
          "reference).")
    print("  lost ms/win = mean drop x total challenge seconds / windows, the challenge time the "
          "leak cost per transition.")
    print("  react ms = the competitor's own flag-to-synchronised latency, mean over all yields.")
    for r in rows:
        if r["tag"]:
            continue
        fl = (f"   FLAGGED repeats {r['flagged']} (reference means "
              f"{', '.join(f'{x:.1f}' for x in r['refmeans'])} TFLOP/s)") if r["flagged"] else ""
        print(f"    {r['mode']} win {r['win']}s repeats: "
              + ", ".join(f"{x:.3f}" for x in r["drops"]) + fl)

    def fit(sep, name):
        if len(sep) < 3:
            return
        xs = [r["m"] for r in sep]; ys = [r["drop"] for r in sep]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        icpt = my - slope * mx
        dof = len(xs) - 2
        resid = [y - (icpt + slope * x) for x, y in zip(xs, ys)]
        slope_se = math.sqrt(sum(e * e for e in resid) / dof / sxx) if dof > 0 else float("nan")
        lx = [math.log(x) for x in xs]; ly = [math.log(max(y, 1e-3)) for y in ys]
        mlx, mly = statistics.mean(lx), statistics.mean(ly)
        expo = (sum((a - mlx) * (c - mly) for a, c in zip(lx, ly))
                / sum((a - mlx) ** 2 for a in lx))
        mono = all(b["drop"] > a["drop"] for a, b in zip(sep, sep[1:]))
        print(f"  {name}: slope {slope:+.4f} %/window (se {slope_se:.4f}), intercept "
              f"{icpt:+.3f}%, log-log exponent {expo:.2f} (1.0 = proportional to window count), "
              f"monotone rising: {mono}")
    fit([r for r in rows if r["mode"] == "separate" and not r["tag"]],
        "separate, all repeats")
    best = {}
    for r in rows:
        if r["mode"] == "separate":
            best[r["m"]] = r if (r["tag"] or r["m"] not in best) else best[r["m"]]
    fit([best[k] for k in sorted(best)], "separate, flagged repeats removed")
    # Two checks that do not depend on the exclusion at all, because the exclusion is post hoc
    # and the headline must not rest on it alone.
    base = [r for r in rows if r["mode"] == "separate" and not r["tag"]]
    med = [dict(r, drop=statistics.median(r["drops"])) for r in base]
    fit(med, "separate, MEDIAN per arm, no exclusion")
    untouched = [r for r in base if not r["flagged"]]
    if len(untouched) >= 3:
        fit(untouched, "separate, means, only arms with no flagged repeat")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    d = args.dir

    print("=" * 90)
    print("E1 v2. Every figure recomputed from the raw per-window and per-repeat values.")
    print("=" * 90)

    cal4, cal0 = L(d, "CAL-qd4.json"), L(d, "CAL-qd0.json")
    if cal4:
        c = cal4["calibration"]
        sustained = cal4["iterations_per_second"]
        print()
        print("RETAINED CALIBRATION, queue depth 4")
        print("  short in-run calibration : %.1f matmul/s = %.1f TFLOP/s"
              % (c["solo_iterations_per_second"], c["solo_tflops"]))
        print("  sustained 20s solo run   : %.1f matmul/s = %.1f TFLOP/s"
              % (sustained, sustained * cal4["flop_per_iteration"] / 1e12))
        print("  ratio %.3f" % (sustained / c["solo_iterations_per_second"]))
        print("  The short calibration reads low: one warm-up burst is not enough at this matrix")
        print("  size. THE SUSTAINED RATE IS THE DENOMINATOR TO USE. Device shares computed in-run")
        print("  against the short calibration are overstated by that ratio.")
    if cal0:
        print("RETAINED CALIBRATION, unbounded submission: sustained %.1f matmul/s"
              % cal0["iterations_per_second"])

    sweep_table(L(d, "A1-sep-nonadaptive.json"), "A1  separate process, bounded submission")
    sweep_table(L(d, "A2-sep-unbounded.json"), "A2  separate process, UNBOUNDED (the July protocol)")
    sweep_table(L(d, "A3-dormant-context.json"), "A3  dormant undeclared context")

    bound_table(L(d, "BOUND-nonadaptive.json"), "BOUND  non-adaptive adversary")
    bound_table(L(d, "BOUND-adaptive.json"), "BOUND  adaptive adversary")

    duty_table(L(d, "B1-sep-adaptive.json"), "B1  verifier duty sweep, separate process")
    duty_table(L(d, "B2-inline-adaptive.json"), "B2  verifier duty sweep, same context")
    duty_table(L(d, "B3-poscontrol-large.json"), "B3  POSITIVE CONTROL, large effect")
    duty_table(L(d, "B4-poscontrol-small.json"), "B4  POSITIVE CONTROL, near threshold")

    reaction_table(d, "C1-sep-react", [0, 5, 10, 15, 20, 30, 50, 100, 250],
                   "C1  reaction sweep, separate process")
    reaction_table(d, "C2-inline-react", [0, 15, 50, 150],
                   "C2  reaction sweep, same context")
    e1b_table(d, "E1b  leak vs window count at fixed duty (2026-09-03 follow-up)")


if __name__ == "__main__":
    main()
