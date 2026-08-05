#!/usr/bin/env python3
"""Tier 0.5: a verifier-seeded kernel ran, and fully occupied the GPU, across a bracketed interval.

This tier serves completeness directly: a fully occupied machine leaves no room
for undeclared work. Two hardware facts shape the design, and both belong in the write-up.

  Occupancy counters are OFF in CC-On, so occupancy cannot be read from a counter. It comes from
  arithmetic instead: the op count of the kernel chain is known exactly, so achieved throughput
  against the device's own achievable ceiling is a measured fraction of the machine.

  The enclave has no trusted clock, so the interval is not measured inside. The verifier stamps
  t0 before sending the nonce and t1 on receiving the signed result, and the bound is over that
  bracket, network and orchestration slack included. That slack inflates the denominator, so the
  reported occupancy is an UNDERSTATEMENT, which is the conservative direction. Everything this
  script reports about time is labelled UNTRUSTED for the same reason.

The ceiling is measured here rather than taken from a datasheet, because the ratio that matters
is against what this device can actually sustain in CC-On, and because a published sparse figure
halved by hand is exactly the kind of number that turns into a wrong claim. The datasheet peak
belongs in the paper as a cited footnote, not in this measurement.

The chain is dependent (X <- normalize(X @ W) each step), seeded from the nonce, so the final
checksum cannot be precomputed, shortcut, or produced without running the arithmetic.

Usage:  ./tier05_occupancy.py --nonce <hex> --seconds 20 --out tier05.json
"""

import argparse
import hashlib
import json
import time

import torch


def measure_ceiling(n, dtype, dev, seconds=6.0):
    """Best sustained throughput this device gives on a plain independent GEMM stream."""
    g = torch.Generator(device="cuda").manual_seed(0)
    a = torch.randn(n, n, generator=g, device=dev, dtype=dtype)
    b = torch.randn(n, n, generator=g, device=dev, dtype=dtype)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    t0 = time.time()
    k = 0
    while time.time() - t0 < seconds:
        for _ in range(20):
            _ = a @ b
            k += 1
        torch.cuda.synchronize()
    dt = time.time() - t0
    return (2.0 * n * n * n * k) / dt / 1e12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nonce", required=True, help="the verifier's challenge, hex")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--out", required=True)
    ap.add_argument("--noop", action="store_true", help="round-trip slack probe, no work")
    ap.add_argument("--ceiling-only", action="store_true",
                    help="measure the device ceiling and stop. Run OUTSIDE the verifier's "
                         "bracket: the ceiling is a property of the device, and measuring it "
                         "inside the bracket charges it to the workload and understates "
                         "occupancy for no good reason.")
    ap.add_argument("--ceiling", type=float, help="use a previously measured ceiling, TFLOP/s")
    args = ap.parse_args()

    dev = torch.device("cuda")
    name = torch.cuda.get_device_name(0)

    if args.noop:
        json.dump({"schema": "ccverify.tier05.noop.v1", "noop": True,
                   "nonce": args.nonce, "device": name},
                  open(args.out, "w"), indent=2, sort_keys=True)
        print("noop round trip complete")
        return

    n = args.size
    if args.ceiling_only:
        c = measure_ceiling(n, torch.bfloat16, dev)
        json.dump({"schema": "ccverify.tier05.ceiling.v1", "device": name, "matrix_n": n,
                   "measured_ceiling_tflops": round(c, 2)},
                  open(args.out, "w"), indent=2, sort_keys=True)
        print(f"measured ceiling: {c:.2f} TFLOP/s bf16")
        return

    seed = int(hashlib.sha256(bytes.fromhex(args.nonce)).hexdigest()[:15], 16)
    ceiling = args.ceiling if args.ceiling else measure_ceiling(n, torch.bfloat16, dev)

    g = torch.Generator(device="cuda").manual_seed(seed)
    x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    torch.cuda.synchronize()

    flops_per_step = 2.0 * n * n * n
    steps = 0
    t_start = time.time()
    while time.time() - t_start < args.seconds:
        for _ in range(10):
            # dependent: step k+1 cannot start before step k finishes, so the chain cannot be
            # parallelised away or precomputed, and its length is what the op count counts
            x = torch.nn.functional.normalize(x @ w, dim=1)
            steps += 1
        torch.cuda.synchronize()
    torch.cuda.synchronize()
    t_end = time.time()

    checksum = hashlib.sha256(x.float().cpu().numpy().tobytes()).hexdigest()
    inside = t_end - t_start
    total_flops = flops_per_step * steps
    achieved = total_flops / inside / 1e12

    result = {
        "schema": "ccverify.tier05.v1",
        "nonce": args.nonce,
        "device": name,
        "matrix_n": n,
        "dtype": "bfloat16",
        "steps": steps,
        "flops_per_step": flops_per_step,
        "total_flops": total_flops,
        "enclave_elapsed_s_UNTRUSTED": round(inside, 4),
        "enclave_tflops_UNTRUSTED": round(achieved, 2),
        "measured_ceiling_tflops": round(ceiling, 2),
        "occupancy_vs_measured_ceiling_UNTRUSTED": round(achieved / ceiling, 4) if ceiling else None,
        "final_state_sha256": checksum,
    }
    json.dump(result, open(args.out, "w"), indent=2, sort_keys=True)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
