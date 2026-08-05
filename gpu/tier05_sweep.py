#!/usr/bin/env python3
"""Does undeclared GPU work actually show up as an occupancy drop, and from what level?

Tier 0.5 as first run asserted its own mechanism. It measured that the declared challenge
occupied 84.7% of the device's ceiling and then ARGUED that a competing context would contend
for SMs and depress that ratio. That is an argument, not a measurement, and it is the argument
the whole tier rests on, so it is the thing to test.

This sweeps a separate undeclared process across duty cycles and measures the challenge's
achieved throughput at each level. The output is the sensitivity curve, which converts the tier
from "undeclared work would show up" into "undeclared work above roughly X of the device shows
up as a drop of Y, and below that it is inside run-to-run noise".

Repeats at duty 0 give the noise floor, without which no detection threshold can be stated.

Enclave-side timing is used deliberately here and it is sound: this measures a physical property
of the device (how throughput responds to contention), not a trust claim. The trust claim is
Tier 0.5's verifier-bracketed run, which is separate and unaffected.
"""

import argparse
import json
import subprocess
import statistics
import sys
import time
from pathlib import Path

import torch

HERE = Path(__file__).parent


def challenge_throughput(n, seconds, seed, inline_undeclared=0.0):
    """The declared, verifier-seeded dependent chain. Returns achieved TFLOP/s.

    inline_undeclared adds undeclared matmuls in the SAME process and the same CUDA context,
    which is what "a declared process submits undeclared kernels" actually looks like. Only the
    DECLARED op count goes into the throughput, because that is all the verifier knows about, so
    undeclared work shows up purely as inflated wall time.

    That distinction is the whole point of the comparison: a separate process pays a context
    switching penalty and is loud, while work in the same context just takes its share.
    """
    dev = torch.device("cuda")
    g = torch.Generator(device="cuda").manual_seed(seed)
    x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    u = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) if inline_undeclared else None
    torch.cuda.synchronize()
    steps = 0
    undeclared_steps = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for _ in range(10):
            x = torch.nn.functional.normalize(x @ w, dim=1)
            steps += 1
            if inline_undeclared and (steps * inline_undeclared) >= (undeclared_steps + 1):
                u = u @ w                      # undeclared, and never counted
                undeclared_steps += 1
        torch.cuda.synchronize()
    torch.cuda.synchronize()
    dt = time.time() - t0
    return (2.0 * n * n * n * steps) / dt / 1e12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--duties", default="0,0.05,0.1,0.25,0.5,1.0")
    ap.add_argument("--inline", action="store_true",
                    help="undeclared work in the SAME process and context, not a competitor")
    ap.add_argument("--paired", action="store_true",
                    help="measure clean and contended back to back and compare within the pair, "
                         "so thermal drift cancels instead of inflating the noise")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    duties = [float(d) for d in args.duties.split(",")]

    if args.paired:
        # Each pair is clean-then-contended, back to back, so warmup and thermal drift move
        # both halves together and cancel in the ratio. Without this the run-to-run spread is
        # dominated by drift (a bookend control measured 4.4% across one sweep) and the
        # detection threshold comes out far too pessimistic.
        out_rows = []
        for duty in [d for d in duties if d > 0]:
            ratios = []
            for r in range(args.repeats):
                clean_v = challenge_throughput(args.size, args.seconds, seed=1234 + r)
                proc = None
                if not args.inline:
                    proc = subprocess.Popen(
                        [args.python, str(HERE / "contention.py"), "--duty", str(duty),
                         "--seconds", str(args.seconds + 4)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(2.0)
                try:
                    dirty_v = challenge_throughput(
                        args.size, args.seconds, seed=1234 + r,
                        inline_undeclared=duty if args.inline else 0.0)
                finally:
                    if proc:
                        proc.terminate(); proc.wait(timeout=30); time.sleep(1.0)
                ratios.append(dirty_v / clean_v)
                print(f"  duty={duty} pair={r} clean={clean_v:.1f} contended={dirty_v:.1f} "
                      f"ratio={ratios[-1]:.3f}", flush=True)
            mean_r = statistics.mean(ratios)
            sd_r = statistics.stdev(ratios) if len(ratios) > 1 else 0.0
            out_rows.append({"duty": duty, "ratios": ratios, "mean_ratio": mean_r,
                             "stdev_ratio": sd_r, "drop_pct": 100 * (1 - mean_r)})
        result = {
            "schema": "ccverify.tier05.sweep.paired.v1",
            "undeclared_work_mode": "same process, same CUDA context" if args.inline
                                    else "separate competing process",
            "device": torch.cuda.get_device_name(0),
            "matrix_n": args.size, "seconds_per_run": args.seconds,
            "pairs_per_duty": args.repeats, "rows": out_rows,
        }
        Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))
        print("\nduty   mean ratio   drop     stdev of ratio   drop exceeds 3 stdev?")
        for r in out_rows:
            det = (1 - r["mean_ratio"]) > 3 * r["stdev_ratio"] if r["stdev_ratio"] > 0 else None
            print(f"{r['duty']:<7}{r['mean_ratio']:<13.3f}{r['drop_pct']:>5.1f}%   "
                  f"{r['stdev_ratio']:<17.4f}{det}")
        return

    rows = []
    for duty in duties:
        vals = []
        for r in range(args.repeats):
            proc = None
            if duty > 0 and not args.inline:
                proc = subprocess.Popen(
                    [args.python, str(HERE / "contention.py"), "--duty", str(duty),
                     "--seconds", str(args.seconds + 4)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2.0)          # let the competitor reach steady state
            try:
                vals.append(challenge_throughput(
                    args.size, args.seconds, seed=1234 + r,
                    inline_undeclared=duty if args.inline else 0.0))
            finally:
                if proc:
                    proc.terminate()
                    proc.wait(timeout=30)
                    time.sleep(1.0)
            print(f"  duty={duty} repeat={r} -> {vals[-1]:.1f} TFLOP/s", flush=True)
        rows.append({"duty": duty, "tflops": vals,
                     "mean": statistics.mean(vals),
                     "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0})

    clean = rows[0]["mean"]
    noise = rows[0]["stdev"]
    for r in rows:
        r["fraction_of_clean"] = r["mean"] / clean
        r["drop_pct"] = 100.0 * (1 - r["mean"] / clean)
        # detectable if the drop exceeds three standard deviations of the clean measurement
        r["detectable_at_3sigma"] = (clean - r["mean"]) > 3 * noise if noise > 0 else None

    result = {
        "schema": "ccverify.tier05.sweep.v1",
        "undeclared_work_mode": "same process, same CUDA context" if args.inline
                                else "separate competing process",
        "device": torch.cuda.get_device_name(0),
        "matrix_n": args.size,
        "seconds_per_run": args.seconds,
        "repeats": args.repeats,
        "clean_mean_tflops": clean,
        "clean_stdev_tflops": noise,
        "clean_relative_noise": noise / clean if clean else None,
        "rows": rows,
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))
    print("\nduty   mean TFLOP/s   drop      detectable at 3 sigma")
    for r in rows:
        print(f"{r['duty']:<7}{r['mean']:<15.1f}{r['drop_pct']:>6.1f}%   {r['detectable_at_3sigma']}")
    print(f"\nclean run-to-run noise: {100 * result['clean_relative_noise']:.2f}% "
          f"(stdev {noise:.2f} TFLOP/s over {args.repeats} repeats)")


if __name__ == "__main__":
    main()
