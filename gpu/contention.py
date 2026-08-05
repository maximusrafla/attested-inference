#!/usr/bin/env python3
"""Undeclared GPU work at a controlled duty cycle, for the Tier 0.5 sensitivity sweep.

Stands in for an operator running something it did not declare on the same device, at a chosen
intensity. The duty cycle is what makes the sweep possible: at duty d the process tries to
occupy the device for d of every cycle, so the sweep answers the question Tier 0.5 actually
needs answered, which is how much undeclared work has to be running before the occupancy check
can see it.

Deliberately a SEPARATE process, so the contention is real GPU scheduler contention rather than
a Python-level effect. Default time-slicing, no MPS, which is the realistic case.
"""

import argparse
import time

import torch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duty", type=float, required=True, help="0.0 to 1.0")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--size", type=int, default=8192)
    args = ap.parse_args()

    if args.duty <= 0:
        time.sleep(args.seconds)
        return

    dev = torch.device("cuda")
    n = args.size
    g = torch.Generator(device="cuda").manual_seed(7)
    a = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    b = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    torch.cuda.synchronize()

    slice_s = 0.05                      # one duty period, short enough to interleave
    on = slice_s * args.duty
    off = slice_s - on
    end = time.time() + args.seconds
    iters = 0
    while time.time() < end:
        t0 = time.time()
        while time.time() - t0 < on:
            a = a @ b
            iters += 1
        torch.cuda.synchronize()
        if off > 0:
            time.sleep(off)
    print(f"contention duty={args.duty} iters={iters}")


if __name__ == "__main__":
    main()
