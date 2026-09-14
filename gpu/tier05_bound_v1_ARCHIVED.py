#!/usr/bin/env python3
"""Turn the occupancy observation into a stated bound: what can hide under the noise.

The sweep showed that same-context undeclared work costs 1.9% of throughput at 2% duty. That is
an observation, not a claim, because nothing says whether 1.9% is distinguishable from a clean
run. A detection threshold needs the null distribution: how much does the SAME clean measurement
vary when nothing undeclared is running at all.

Two nulls, because there are two deployments and they differ by a lot:

  paired    the verifier obtains a clean reference immediately adjacent to the challenge, so
            warmup and thermal drift move both halves together and cancel in the ratio. This is
            the best case, and it is not free: it assumes the verifier can get an uncontended
            baseline on demand, which an operator who controls when it cheats can deny.
  unpaired  the verifier compares the challenge against a ceiling measured earlier and stored.
            This is the deployable case and its null includes drift.

Output is the bound in the form Q6 needs: with the threshold set at k sigma of clean-run
variation, same-context undeclared work up to X percent of device capacity is indistinguishable
from noise, so the handoff has to cover everything below X.

This is the on-chip analogue of an identified-set width, and it fails for the same reason a
power meter's does: both infer a quantity from an aggregate signal, against an efficiency
baseline the operator controls.
"""

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import torch


def _device():
    # cuda everywhere real. CCV_DEVICE=cpu exists only for dry-running the harness off the box.
    return torch.device(os.environ.get("CCV_DEVICE", "cuda"))


def _sync():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        torch.cuda.synchronize()


def _device_name():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        return torch.cuda.get_device_name(0)
    return "cpu (DRY RUN, measures nothing)"


def run_chain(n, seconds, seed, inline_undeclared=0.0, yield_after_s=None, counts=None):
    """Declared chain throughput in TFLOP/s. Only DECLARED ops are counted, which is all the
    verifier knows about, so undeclared work shows up purely as inflated wall time.

    yield_after_s models the ADAPTIVE adversary: the whole measured run here IS the verifier's
    challenge, so an operator that pauses its hidden work while a challenge is in flight stops
    submitting yield_after_s into the window. yield_after_s=0 is the operator that is told by its
    own orchestrator; a positive value is one that has to notice first.

    If counts is a dict it receives the undeclared iteration count actually completed inside the
    measured window, which is a researcher-side number the verifier never sees.
    """
    dev = _device()
    g = torch.Generator(device=dev).manual_seed(seed)
    x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    u = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) if inline_undeclared else None
    _sync()
    steps = undecl = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for _ in range(10):
            x = torch.nn.functional.normalize(x @ w, dim=1)
            steps += 1
            hidden_allowed = yield_after_s is None or (time.time() - t0) < yield_after_s
            if inline_undeclared and hidden_allowed and (steps * inline_undeclared) >= (undecl + 1):
                u = u @ w
                undecl += 1
        _sync()
    _sync()
    if counts is not None:
        counts["undeclared_iterations_in_window"] = undecl
    return (2.0 * n * n * n * steps) / (time.time() - t0) / 1e12


def interpolate_duty(points, target_drop):
    """Smallest duty whose drop reaches target_drop, linearly interpolated between measurements."""
    pts = sorted(points)
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        if p0 <= target_drop <= p1:
            if p1 == p0:
                return d0
            return d0 + (d1 - d0) * (target_drop - p0) / (p1 - p0)
    if target_drop < pts[0][1]:
        return pts[0][0] * target_drop / pts[0][1] if pts[0][1] else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--null-runs", type=int, default=12)
    ap.add_argument("--duties", default="0.01,0.02,0.03,0.05,0.08,0.15")
    ap.add_argument("--pairs", type=int, default=4)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--modes", default="nonadaptive",
                    help="comma separated: nonadaptive (the adversary every published number "
                         "here was measured against, which never reacts) and adaptive (pauses "
                         "its hidden work while the challenge is in flight). Both share one null "
                         "so the two bounds are directly comparable.")
    ap.add_argument("--react-ms", type=float, default=0.0,
                    help="adaptive mode only: how far into the challenge window the hidden work "
                         "keeps running before it pauses")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    print("== null distribution: repeated clean runs, nothing undeclared ==", flush=True)
    clean = []
    for i in range(args.null_runs):
        v = run_chain(args.size, args.seconds, seed=900 + i)
        clean.append(v)
        print(f"  clean {i}: {v:.1f} TFLOP/s", flush=True)

    # unpaired null: each clean run against the mean of them all, which is what a stored
    # ceiling behaves like. Includes drift, which is the point.
    mean_clean = statistics.mean(clean)
    unpaired_ratios = [v / mean_clean for v in clean]
    sigma_unpaired = statistics.stdev(unpaired_ratios)

    # paired null: consecutive clean runs against each other, the same shape as the paired
    # protocol but with nothing undeclared in either half
    paired_ratios = [clean[i + 1] / clean[i] for i in range(0, len(clean) - 1, 2)]
    sigma_paired = statistics.stdev(paired_ratios)

    print(f"\nclean mean {mean_clean:.1f} TFLOP/s")
    print(f"unpaired null sigma: {100 * sigma_unpaired:.2f}%   (n={len(unpaired_ratios)})")
    print(f"paired   null sigma: {100 * sigma_paired:.2f}%   (n={len(paired_ratios)})")

    duties = [float(d) for d in args.duties.split(",")]
    thr_p = args.sigma * sigma_paired
    thr_u = args.sigma * sigma_unpaired
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    by_mode = {}

    for mode in modes:
        adaptive = mode == "adaptive"
        yield_after = (args.react_ms / 1000.0) if adaptive else None
        print(f"\n== duty response, paired protocol, same CUDA context, {mode.upper()} "
              f"adversary ==", flush=True)
        if adaptive:
            print(f"   (hidden work pauses {args.react_ms:.0f} ms into each challenge window)",
                  flush=True)
        rows = []
        for duty in duties:
            ratios = []
            hidden_iters = []
            for r in range(args.pairs):
                # The clean half is a challenge too, so an adaptive operator yields in both
                # halves of the pair. Only the contended half can carry hidden work at all.
                c = run_chain(args.size, args.seconds, seed=1200 + r)
                cnt = {}
                d = run_chain(args.size, args.seconds, seed=1200 + r, inline_undeclared=duty,
                              yield_after_s=yield_after, counts=cnt)
                ratios.append(d / c)
                hidden_iters.append(cnt.get("undeclared_iterations_in_window", 0))
            mr = statistics.mean(ratios)
            rows.append({"duty": duty, "mean_ratio": mr, "drop": 1 - mr,
                         "stdev_ratio": statistics.stdev(ratios) if len(ratios) > 1 else 0.0,
                         "undeclared_iterations_in_window": hidden_iters})
            print(f"  duty {duty:<6} drop {100 * (1 - mr):>6.2f}%   hidden iters in window "
                  f"{hidden_iters}", flush=True)

        pts = [(r["duty"], r["drop"]) for r in rows]
        duty_p = interpolate_duty(pts, thr_p)
        duty_u = interpolate_duty(pts, thr_u)
        max_drop = max(r["drop"] for r in rows)
        by_mode[mode] = {
            "adversary": "pauses its hidden work while the challenge is in flight" if adaptive
                         else "never reacts to the verifier",
            "react_ms": args.react_ms if adaptive else None,
            "duty_response": rows,
            "hideable_duty_paired": duty_p,
            "hideable_duty_unpaired": duty_u,
            "max_measured_drop": max_drop,
            "no_tested_duty_detectable_paired": max_drop < thr_p,
            "no_tested_duty_detectable_unpaired": max_drop < thr_u,
        }

    legacy = by_mode.get("nonadaptive", by_mode[modes[0]])
    result = {
        "schema": "ccverify.tier05.bound.v2",
        "device": _device_name(),
        "matrix_n": args.size, "seconds_per_run": args.seconds,
        "clean_runs": clean, "clean_mean_tflops": mean_clean,
        "null_sigma_paired": sigma_paired,
        "null_sigma_unpaired": sigma_unpaired,
        "sigma_multiple": args.sigma,
        "detection_threshold_paired_drop": thr_p,
        "detection_threshold_unpaired_drop": thr_u,
        "modes": by_mode,
        # legacy v1 keys, mirroring the non-adaptive mode so older readers still resolve
        "hideable_duty_paired": legacy["hideable_duty_paired"],
        "hideable_duty_unpaired": legacy["hideable_duty_unpaired"],
        "duty_response": legacy["duty_response"],
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))

    print("\n=== THE BOUND ===")
    print(f"at {args.sigma} sigma of clean-run variation:")
    print(f"  paired threshold {100 * thr_p:.2f}% drop, stored-ceiling threshold "
          f"{100 * thr_u:.2f}% drop")
    for mode, m in by_mode.items():
        print(f"\n  {mode} adversary:")
        for label, key, thr in (("clean baseline adjacent to the challenge",
                                 "hideable_duty_paired", thr_p),
                                ("compared against a stored ceiling",
                                 "hideable_duty_unpaired", thr_u)):
            if m["max_measured_drop"] < thr:
                print(f"    {label}: NO tested duty up to {100 * max(duties):.0f}% of capacity "
                      f"is distinguishable from noise (largest drop seen "
                      f"{100 * m['max_measured_drop']:.2f}%)")
            elif m[key] is not None:
                print(f"    {label}: undeclared work up to {100 * m[key]:.1f}% of device "
                      f"capacity is indistinguishable from noise")
            else:
                print(f"    {label}: threshold below the measured range")


if __name__ == "__main__":
    main()
