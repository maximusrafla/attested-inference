#!/usr/bin/env python3
"""Turn the occupancy observation into a stated bound: what can hide under the noise.

A detection threshold needs the null distribution: how much does the SAME clean measurement vary
when nothing undeclared is running at all. Two nulls, because there are two deployments:

  paired    the verifier obtains a clean reference immediately adjacent to the challenge, so
            warmup and thermal drift move both halves together and cancel in the ratio. This is
            the best case, and it is not free: it assumes the verifier can get an uncontended
            baseline on demand, which an operator who controls when it cheats can deny.
  unpaired  the verifier compares the challenge against a ceiling measured earlier and stored.

WHAT v2 FIXED, AND IT CHANGES THE HEADLINE NUMBER (2026-09-03)
--------------------------------------------------------------
v1 interpolated the detection threshold back through the DUTY axis and printed the answer as
"percent of device capacity". Duty here is undeclared matmuls per declared step. That is not a
device share, and the two differ by about 1.5x. The published figures of roughly 6 percent and
roughly 19 percent were duty values wearing a device-capacity label; the device shares they
correspond to are roughly 4 percent and roughly 12 percent.

v2 measures the undeclared work's absolute throughput from its own iteration count, divides by a
solo rate calibrated on the same device in the same run, and interpolates the SHARE at the
threshold. The duty axis is still swept, because it is the knob, but it is never the answer.

The bound also now carries both decision rules. v1 compared a mean of several pairs against three
sigma of individual runs, which is the standard deviation of the wrong thing. The two-sample rule
on the mean is the one a verifier averaging several windows would actually use, and it is tighter.
"""

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import torch


def _device():
    return torch.device(os.environ.get("CCV_DEVICE", "cuda"))


def _sync():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        torch.cuda.synchronize()


def _device_name():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        return torch.cuda.get_device_name(0)
    return "cpu (DRY RUN, measures nothing)"


def calibrate_solo(n, seconds, seed=99):
    """Solo rate for the UNDECLARED operation, which is a bare matmul of the same size.

    This is the denominator every device share divides by, so it is measured here, in this run,
    on this device, and retained in the output. The first version had no retained calibration at
    all and its shares divided by three different and incommensurable numbers.
    """
    dev = _device()
    g = torch.Generator(device=dev).manual_seed(seed)
    u = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) / (n ** 0.5)
    for _ in range(4):
        u = u @ w
    _sync()
    it = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        u = u @ w
        it += 1
        if it % 4 == 0:
            _sync()
    _sync()
    dt = time.time() - t0
    return it / dt, it, dt


def run_chain(n, seconds, seed, inline_undeclared=0.0, yield_after_s=None, counts=None):
    """Declared chain throughput in TFLOP/s. Only DECLARED ops are counted, which is all the
    verifier knows about, so undeclared work shows up purely as inflated wall time.

    yield_after_s models the ADAPTIVE adversary: the whole measured run here IS the challenge, so
    an operator that pauses while a challenge is in flight stops submitting yield_after_s into the
    window. With yield_after_s = 0 it never submits at all, which is worth saying out loud: that
    arm is arithmetic, not a detection measurement, and the printout says so.
    """
    dev = _device()
    g = torch.Generator(device=dev).manual_seed(seed)
    x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    u = (torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) / (n ** 0.5)
         if inline_undeclared else None)
    wn = w / (n ** 0.5)
    _sync()
    steps = undecl = 0
    credit = 0.0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for _ in range(10):
            x = torch.nn.functional.normalize(x @ w, dim=1)
            steps += 1
            allowed = yield_after_s is None or (time.time() - t0) < yield_after_s
            if inline_undeclared and allowed:
                credit += inline_undeclared
                while credit >= 1.0:
                    u = u @ wn
                    undecl += 1
                    credit -= 1.0
        _sync()
    _sync()
    dt = time.time() - t0
    if counts is not None:
        counts["undeclared_iterations_in_window"] = undecl
        counts["elapsed_s"] = dt
        counts["declared_steps"] = steps
    return (2.0 * n * n * n * steps) / dt / 1e12


def interpolate(points, target):
    """Smallest x whose y reaches target, linearly interpolated between measurements."""
    pts = sorted(points)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 <= target <= y1:
            return x0 if y1 == y0 else x0 + (x1 - x0) * (target - y0) / (y1 - y0)
    if target < pts[0][1]:
        return pts[0][0] * target / pts[0][1] if pts[0][1] else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--null-runs", type=int, default=12)
    ap.add_argument("--duties", default="0.05,0.15,0.30")
    ap.add_argument("--pairs", type=int, default=4)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--modes", default="nonadaptive")
    ap.add_argument("--react-ms", type=float, default=0.0)
    ap.add_argument("--calibrate-s", type=float, default=3.0)
    ap.add_argument("--drop-first-null", action="store_true",
                    help="exclude the first clean run from the null. It is a warmup and it "
                         "inflates the unpaired sigma; reported both ways regardless.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    print("== solo calibration for the undeclared operation ==", flush=True)
    solo_ips, cal_it, cal_dt = calibrate_solo(args.size, args.calibrate_s)
    flop_it = 2 * args.size ** 3
    print(f"  {solo_ips:.1f} matmul/s solo = {solo_ips*flop_it/1e12:.1f} TFLOP/s "
          f"({cal_it} its in {cal_dt:.2f}s)", flush=True)

    print("== null distribution: repeated clean runs, nothing undeclared ==", flush=True)
    clean = []
    for i in range(args.null_runs):
        v = run_chain(args.size, args.seconds, seed=900 + i)
        clean.append(v)
        print(f"  clean {i}: {v:.1f} TFLOP/s", flush=True)

    def nulls(vals):
        if len(vals) < 4:
            return None, None, None, []
        m = statistics.mean(vals)
        unp = statistics.stdev([v / m for v in vals])
        pr = [vals[i + 1] / vals[i] for i in range(0, len(vals) - 1, 2)]
        return m, unp, (statistics.stdev(pr) if len(pr) > 1 else None), pr

    mean_clean, sigma_unpaired, sigma_paired, paired_ratios = nulls(clean)
    m2, su2, sp2, _ = nulls(clean[1:])
    print(f"\nclean mean {mean_clean:.1f} TFLOP/s")
    print(f"unpaired null sigma: {100*sigma_unpaired:.2f}%   (n={len(clean)})")
    if sigma_paired is None:
        raise SystemExit("need at least 4 clean runs to form a paired null")
    print(f"paired   null sigma: {100*sigma_paired:.2f}%   (n={len(paired_ratios)}) "
          f"mean ratio {statistics.mean(paired_ratios):.5f}")
    if su2 is not None and sp2 is not None:
        print(f"  excluding the first (warmup) clean run: unpaired {100*su2:.2f}%  "
              f"paired {100*sp2:.2f}%")

    if args.drop_first_null and su2 is not None and sp2 is not None:
        sigma_unpaired, sigma_paired = su2, sp2
        mean_clean = m2

    duties = [float(d) for d in args.duties.split(",")]
    thr_p = args.sigma * sigma_paired
    thr_u = args.sigma * sigma_unpaired
    by_mode = {}

    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        adaptive = mode == "adaptive"
        yield_after = (args.react_ms / 1000.0) if adaptive else None
        print(f"\n== duty response, paired, same CUDA context, {mode.upper()} ==", flush=True)
        rows = []
        for duty in duties:
            ratios, shares, iters = [], [], []
            for r in range(args.pairs):
                c = run_chain(args.size, args.seconds, seed=1200 + r)
                cnt = {}
                d = run_chain(args.size, args.seconds, seed=1200 + r, inline_undeclared=duty,
                              yield_after_s=yield_after, counts=cnt)
                ratios.append(d / c)
                it = cnt["undeclared_iterations_in_window"]
                iters.append(it)
                shares.append((it / cnt["elapsed_s"]) / solo_ips)
            mr = statistics.mean(ratios)
            sh = statistics.mean(shares)
            rows.append({
                "duty_knob": duty, "mean_ratio": mr, "drop": 1 - mr,
                "stdev_ratio": statistics.stdev(ratios) if len(ratios) > 1 else 0.0,
                "undeclared_iterations_in_window": iters,
                "device_share_taken": sh,
                "device_share_per_pair": shares,
            })
            print(f"  duty knob {duty:<6} drop {100*(1-mr):>6.2f}%   "
                  f"DEVICE SHARE TAKEN {100*sh:>6.2f}%   undeclared its {iters}", flush=True)

        share_pts = [(r["device_share_taken"], r["drop"]) for r in rows]
        duty_pts = [(r["duty_knob"], r["drop"]) for r in rows]
        max_drop = max(r["drop"] for r in rows)
        by_mode[mode] = {
            "adversary": ("pauses its undeclared work while the challenge is in flight" if adaptive
                          else "never reacts to the verifier"),
            "react_ms": args.react_ms if adaptive else None,
            "duty_response": rows,
            "max_measured_drop": max_drop,
            "hideable_device_share_paired": interpolate(share_pts, thr_p),
            "hideable_device_share_unpaired": interpolate(share_pts, thr_u),
            "hideable_duty_knob_paired_DEPRECATED": interpolate(duty_pts, thr_p),
            "hideable_duty_knob_unpaired_DEPRECATED": interpolate(duty_pts, thr_u),
            "no_tested_level_detectable_paired": max_drop < thr_p,
            "no_tested_level_detectable_unpaired": max_drop < thr_u,
            "nothing_undeclared_executed": all(
                sum(r["undeclared_iterations_in_window"]) == 0 for r in rows),
        }

    result = {
        "schema": "ccverify.tier05.bound.v3",
        "device": _device_name(),
        "matrix_n": args.size, "seconds_per_run": args.seconds,
        "solo_calibration": {"iterations_per_second": solo_ips,
                             "tflops": solo_ips * flop_it / 1e12,
                             "iterations": cal_it, "elapsed_s": cal_dt,
                             "note": "denominator for every device share below, measured in this "
                                     "run on this device"},
        "clean_runs": clean, "clean_mean_tflops": mean_clean,
        "null_sigma_paired": sigma_paired, "null_sigma_unpaired": sigma_unpaired,
        "null_sigma_paired_excl_warmup": sp2, "null_sigma_unpaired_excl_warmup": su2,
        "paired_null_mean_ratio": statistics.mean(paired_ratios),
        "sigma_multiple": args.sigma,
        "detection_threshold_paired_drop": thr_p,
        "detection_threshold_unpaired_drop": thr_u,
        "units_note": ("hideable figures are DEVICE SHARES, measured from the undeclared work's "
                       "own iteration count over the solo calibration. The duty knob is retained "
                       "only as the knob; v1 of this script reported the knob and labelled it "
                       "device capacity, which overstated the width by about 1.5x"),
        "modes": by_mode,
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))

    print("\n=== THE BOUND, in device share ===")
    print(f"at {args.sigma} sigma: paired threshold {100*thr_p:.2f}% drop, "
          f"stored-ceiling threshold {100*thr_u:.2f}% drop")
    for mode, m in by_mode.items():
        print(f"\n  {mode} adversary:")
        if m["nothing_undeclared_executed"]:
            print("    NOTHING undeclared executed inside the challenge window at any level, so "
                  "there was nothing to detect. This is arithmetic, not a detection result.")
            continue
        for lab, key, thr in (("clean baseline adjacent to the challenge",
                               "hideable_device_share_paired", thr_p),
                              ("compared against a stored ceiling",
                               "hideable_device_share_unpaired", thr_u)):
            if m["max_measured_drop"] < thr:
                print(f"    {lab}: no tested level is distinguishable "
                      f"(largest drop {100*m['max_measured_drop']:.2f}%)")
            elif m[key] is not None:
                print(f"    {lab}: undeclared work up to {100*m[key]:.1f}% OF THE DEVICE is "
                      f"indistinguishable from noise")
            else:
                print(f"    {lab}: threshold outside the measured range")


if __name__ == "__main__":
    main()
