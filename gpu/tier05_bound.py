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
import statistics
import time
from pathlib import Path

import torch


def run_chain(n, seconds, seed, inline_undeclared=0.0):
    """Declared chain throughput in TFLOP/s. Only DECLARED ops are counted, which is all the
    verifier knows about, so undeclared work shows up purely as inflated wall time."""
    dev = torch.device("cuda")
    g = torch.Generator(device="cuda").manual_seed(seed)
    x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    u = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) if inline_undeclared else None
    torch.cuda.synchronize()
    steps = undecl = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for _ in range(10):
            x = torch.nn.functional.normalize(x @ w, dim=1)
            steps += 1
            if inline_undeclared and (steps * inline_undeclared) >= (undecl + 1):
                u = u @ w
                undecl += 1
        torch.cuda.synchronize()
    torch.cuda.synchronize()
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

    print("\n== duty response, paired protocol, same CUDA context ==", flush=True)
    duties = [float(d) for d in args.duties.split(",")]
    rows = []
    for duty in duties:
        ratios = []
        for r in range(args.pairs):
            c = run_chain(args.size, args.seconds, seed=1200 + r)
            d = run_chain(args.size, args.seconds, seed=1200 + r, inline_undeclared=duty)
            ratios.append(d / c)
        mr = statistics.mean(ratios)
        rows.append({"duty": duty, "mean_ratio": mr, "drop": 1 - mr,
                     "stdev_ratio": statistics.stdev(ratios) if len(ratios) > 1 else 0.0})
        print(f"  duty {duty:<6} drop {100 * (1 - mr):>5.2f}%", flush=True)

    pts = [(r["duty"], r["drop"]) for r in rows]
    thr_p = args.sigma * sigma_paired
    thr_u = args.sigma * sigma_unpaired
    duty_p = interpolate_duty(pts, thr_p)
    duty_u = interpolate_duty(pts, thr_u)

    result = {
        "schema": "ccverify.tier05.bound.v1",
        "device": torch.cuda.get_device_name(0),
        "matrix_n": args.size, "seconds_per_run": args.seconds,
        "clean_runs": clean, "clean_mean_tflops": mean_clean,
        "null_sigma_paired": sigma_paired,
        "null_sigma_unpaired": sigma_unpaired,
        "sigma_multiple": args.sigma,
        "detection_threshold_paired_drop": thr_p,
        "detection_threshold_unpaired_drop": thr_u,
        "hideable_duty_paired": duty_p,
        "hideable_duty_unpaired": duty_u,
        "duty_response": rows,
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))

    print("\n=== THE BOUND ===")
    print(f"at {args.sigma} sigma of clean-run variation:")
    print(f"  paired protocol   threshold = {100 * thr_p:.2f}% drop  ->  undeclared work up to "
          f"{100 * duty_p:.1f}% of device capacity is indistinguishable from noise"
          if duty_p else "  paired: below the measured range")
    print(f"  stored ceiling    threshold = {100 * thr_u:.2f}% drop  ->  undeclared work up to "
          f"{100 * duty_u:.1f}% of device capacity is indistinguishable from noise"
          if duty_u else "  stored ceiling: above the measured range, so worse than the largest duty tested")


if __name__ == "__main__":
    main()
