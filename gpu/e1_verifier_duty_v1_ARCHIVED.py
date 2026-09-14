#!/usr/bin/env python3
"""E1: what unpredictable challenge timing actually buys against an operator that yields.

The occupancy check in this repository was measured against an adversary that never reacts. It
runs its undeclared work at a fixed duty cycle whatever the verifier does, so a challenge that
lands at a random moment still lands on top of it. Every published occupancy number here, the
roughly 6 percent and roughly 19 percent widths included, is a NON-ADAPTIVE figure.

A company that owns the machine is not that adversary. Its own orchestrator receives the
verifier's challenge, so it can pause the hidden kernels before running the puzzle and resume
after. Unpredictable timing does not defeat that, because the operator reacts rather than
predicts. What unpredictable timing does buy is that the operator has to surrender the device
for whatever fraction of wall time the verifier is genuinely challenging it. That fraction is
the verifier's DUTY CYCLE, and it is the axis this script sweeps.

The measurement, per verifier duty cycle d:

  reference run   the declared chain runs for the whole window, no hidden work, with challenge
                  windows placed at random offsets totalling d of wall time. This is the null:
                  it says how much the in-window throughput varies on its own.
  contended run   the same schedule, same seed, same placement, with hidden work running. Under
                  --adaptive the hidden work pauses while a challenge is in flight.

Recorded for both: throughput inside the challenge windows, throughput outside them, and the
hidden iterations actually completed. That gives three numbers per point:

  1. does the occupancy check fire at 3 sigma of the in-window null
  2. what fraction of its hidden-work ceiling the operator keeps, against a d=0 control
  3. what the contention costs the declared workload outside the windows

The trade the whole experiment exists to state: the verifier's duty cycle d takes d of the
device outright, because those windows run the verifier's puzzle rather than the company's own
work, and it caps hidden work at roughly (1 - d) of what the operator could otherwise steal.

Reaction latency is the second axis. --react-ms keeps the hidden work running that long after
the challenge is signalled, which is the operator that has to notice the puzzle rather than
being told about it by its own scheduler. With short challenge windows a slow reaction leaves a
partial overlap, and a partial overlap is a signal. Sweeping it turns the negative result into a
requirement on the adversary.

Enclave-side timing is used here for the same reason tier05_sweep.py uses it: this measures a
physical property of the device, not a trust claim.

Disclosure discipline: the hidden-work iteration counts below are RESEARCHER-side
measurements. They live on the operator's side of the boundary and a regulator would never
receive them. They are extracted only so the arithmetic in the writeup is checkable.
"""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import torch

HERE = Path(__file__).parent


def _device():
    # cuda everywhere real. CCV_DEVICE=cpu exists only for dry-running this harness off the box,
    # so the control flow can be checked before renting a confidential H100. It measures nothing.
    return torch.device(os.environ.get("CCV_DEVICE", "cuda"))


def _sync():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        torch.cuda.synchronize()


def _device_name():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        return torch.cuda.get_device_name(0)
    return "cpu (DRY RUN, measures nothing)"


def _pct(v):
    """Format a percentage that may be absent, which it is at verifier duty 0 where there are no
    challenge windows to measure in the first place."""
    return "n/a" if v is None else f"{v:.2f}%"


def set_flag(path):
    """The operator's own orchestrator marking a verifier challenge as in flight. The contents
    are the epoch time of the signal, so the hidden work can report a true reaction latency."""
    Path(path).write_text(repr(time.time()))


def clear_flag(path):
    """Remove the in-flight marker. Retried, and loud if it cannot be done: a flag that stays set
    would leave the hidden work paused for the rest of the run and quietly fake the result."""
    for _ in range(50):
        try:
            Path(path).unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(f"could not clear the challenge flag at {path}; the measurement after this "
                       f"point would be wrong, so stopping instead of reporting it")


def build_schedule(total_s, duty, window_s, rng):
    """Challenge windows at random offsets, totalling duty of the wall time.

    Randomised because that is how a verifier would actually do it. Note that randomising the
    timing is what this experiment is testing, not a control: it does not help against an
    operator that is told when the challenge arrives.
    """
    if duty <= 0:
        return [("idle", total_s)], 0
    m = max(1, int(round(duty * total_s / window_s)))
    idle = max(0.0, total_s - m * window_s)
    cuts = sorted(rng.random() for _ in range(m))
    gaps, prev = [], 0.0
    for c in list(cuts) + [1.0]:
        gaps.append((c - prev) * idle)
        prev = c
    phases = []
    for i in range(m):
        phases.append(("idle", gaps[i]))
        phases.append(("challenge", window_s))
    phases.append(("idle", gaps[m]))
    return phases, m


class Chain:
    """The declared, verifier-seeded dependent chain, plus optional undeclared work in the SAME
    process and CUDA context. Only declared steps are counted into throughput, because that is
    all the verifier knows about, so undeclared work shows up purely as inflated wall time."""

    def __init__(self, n, seed, need_hidden):
        dev = _device()
        g = torch.Generator(device=dev).manual_seed(seed)
        self.n = n
        self.x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        self.w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        self.u = (torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
                  if need_hidden else None)
        self.steps = 0
        self.hidden = 0
        self._hidden_credit = 0.0
        _sync()

    def batch(self, k, hidden_frac):
        """k declared steps, plus hidden_frac undeclared matmuls per declared step."""
        for _ in range(k):
            self.x = torch.nn.functional.normalize(self.x @ self.w, dim=1)
            self.steps += 1
            if hidden_frac and self.u is not None:
                self._hidden_credit += hidden_frac
                while self._hidden_credit >= 1.0:
                    self.u = self.u @ self.w
                    self.hidden += 1
                    self._hidden_credit -= 1.0
        _sync()

    def tflops(self, steps, elapsed):
        return (2.0 * self.n ** 3 * steps) / elapsed / 1e12


def one_run(args, duty, schedule, with_hidden, adaptive, flag, tag, outdir):
    """One wall-clock window. Returns the per-phase record."""
    inline = args.hidden_mode == "inline"
    chain = Chain(args.size, 1234, need_hidden=(with_hidden and inline))

    # warm the device so the first challenge window is not the cold one
    t0 = time.time()
    while time.time() - t0 < args.warmup:
        chain.batch(args.batch, 0.0)
    chain.steps = 0

    proc = None
    counts_path = None
    stop_path = str(Path(outdir) / f"stop-{tag}.flag")
    Path(stop_path).unlink(missing_ok=True)
    if with_hidden and not inline:
        counts_path = str(Path(outdir) / f"hidden-{tag}.json")
        # The hidden process warms up for the same 2 seconds the harness waits, then counts until
        # the harness stops it. So its counted period is the scheduled window and nothing else:
        # no uncounted ramp inside it, no tail of unchallenged work counted after it.
        ready_path = str(Path(outdir) / f"ready-{tag}.flag")
        Path(ready_path).unlink(missing_ok=True)
        cmd = [args.python, str(HERE / "contention.py"),
               "--duty", str(args.hidden_duty),
               "--warmup-s", str(args.hidden_warmup),
               "--seconds", str(args.window + 120.0),
               "--ready-flag", ready_path,
               "--stop-flag", stop_path,
               "--size", str(args.size),
               "--out", counts_path]
        if adaptive:
            cmd += ["--yield-flag", flag, "--react-ms", str(args.react_ms),
                    "--poll-ms", str(args.poll_ms)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait for the hidden work to be warm and counting rather than guessing at it. Starting
        # the schedule early would leave the first windows uncontended and understate everything.
        deadline = time.time() + 180
        while not Path(ready_path).exists():
            if proc.poll() is not None:
                raise RuntimeError("the hidden-work process exited before it was ready")
            if time.time() > deadline:
                raise RuntimeError("the hidden-work process never reported ready")
            time.sleep(0.1)
        Path(ready_path).unlink(missing_ok=True)

    challenges, idles = [], []
    run_t0 = time.time()
    for kind, dur in schedule:
        if dur <= 0:
            continue
        if kind == "challenge":
            if adaptive and with_hidden:
                set_flag(flag)
            # The drain gap is the operator being told and clearing its own queue before handing
            # the device over, which is the strong adversary. It is applied in the REFERENCE run
            # too, and that matters: applying it only when hidden work was present gave the
            # contended run a short idle pause before every timed window that the reference never
            # got, so the comparison measured the device's clock response to that pause instead of
            # contention. Measured, that was a constant 7 percent in the adversary's favour
            # against a null of 0.05 percent, which is large enough to hide a real drop.
            if args.adaptive and args.settle > 0:
                time.sleep(args.settle)
            offset = time.time() - run_t0
            s0, t0 = chain.steps, time.time()
            hid0 = chain.hidden
            while time.time() - t0 < dur:
                # inline hidden work pauses during the challenge exactly when adaptive
                frac = 0.0
                if with_hidden and inline:
                    if not adaptive:
                        frac = args.hidden_frac
                    elif (time.time() - t0) < (args.react_ms / 1000.0):
                        frac = args.hidden_frac
                chain.batch(args.batch, frac)
            el = time.time() - t0
            challenges.append({
                "offset_s": round(offset, 3), "elapsed_s": round(el, 4),
                "declared_steps": chain.steps - s0,
                "tflops": chain.tflops(chain.steps - s0, el),
                "hidden_iterations_in_window": chain.hidden - hid0,
            })
            if adaptive and with_hidden:
                clear_flag(flag)
        else:
            s0, t0 = chain.steps, time.time()
            while time.time() - t0 < dur:
                chain.batch(args.batch, args.hidden_frac if (with_hidden and inline) else 0.0)
            el = time.time() - t0
            idles.append({"elapsed_s": round(el, 4), "declared_steps": chain.steps - s0,
                          "tflops": chain.tflops(chain.steps - s0, el)})

    total_elapsed = time.time() - run_t0
    hidden_counts = None
    if proc:
        Path(stop_path).write_text("stop")
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.terminate(); proc.wait(timeout=30)
        if counts_path and Path(counts_path).exists():
            hidden_counts = json.loads(Path(counts_path).read_text())
        time.sleep(1.0)
    Path(stop_path).unlink(missing_ok=True)
    clear_flag(flag)

    if inline:
        hidden_iters = chain.hidden
        hidden_counted_s = total_elapsed
    else:
        hidden_iters = (hidden_counts or {}).get("iterations")
        hidden_counted_s = (hidden_counts or {}).get("counted_elapsed_s")
    hidden_rate = (hidden_iters / hidden_counted_s
                   if (hidden_iters is not None and hidden_counted_s) else None)
    return {
        "tag": tag, "duty_nominal": duty,
        "with_hidden": with_hidden, "adaptive": adaptive,
        "total_elapsed_s": round(total_elapsed, 3),
        "declared_steps_total": chain.steps,
        "declared_tflops_overall": chain.tflops(chain.steps, total_elapsed),
        "challenge_windows": challenges,
        "idle_phases": idles,
        "challenge_seconds": round(sum(c["elapsed_s"] for c in challenges), 3),
        "duty_achieved": (sum(c["elapsed_s"] for c in challenges) / total_elapsed
                          if total_elapsed else 0.0),
        "in_window_tflops": [c["tflops"] for c in challenges],
        "out_window_tflops": [i["tflops"] for i in idles],
        "hidden_iterations": hidden_iters,
        "hidden_counted_seconds": hidden_counted_s,
        "hidden_iterations_per_second": hidden_rate,
        "hidden_counters": hidden_counts,
    }


def summarise(ref, con):
    """Compare a contended run against its reference run of the same shape."""
    def mean(v):
        return statistics.mean(v) if v else float("nan")

    ref_in, con_in = ref["in_window_tflops"], con["in_window_tflops"]
    ref_out, con_out = ref["out_window_tflops"], con["out_window_tflops"]
    sigma_in = (statistics.stdev(ref_in) / mean(ref_in)) if len(ref_in) > 1 else None
    ratio_in = mean(con_in) / mean(ref_in) if ref_in and con_in else None
    ratio_out = mean(con_out) / mean(ref_out) if ref_out and con_out else None
    detected = None
    if ratio_in is not None and sigma_in:
        detected = (1 - ratio_in) > 3 * sigma_in
    return {
        "in_window_ratio": ratio_in,
        "in_window_drop_pct": None if ratio_in is None else 100 * (1 - ratio_in),
        "in_window_null_sigma_pct": None if sigma_in is None else 100 * sigma_in,
        "detected_at_3sigma": detected,
        "out_window_ratio": ratio_out,
        "out_window_drop_pct": None if ratio_out is None else 100 * (1 - ratio_out),
        "hidden_iterations": con["hidden_iterations"],
        "hidden_counted_seconds": con["hidden_counted_seconds"],
        "hidden_iterations_per_second": con["hidden_iterations_per_second"],
        # What the operator actually surrendered, measured rather than assumed. It exceeds the
        # verifier's duty cycle by the drain interval before each window, which is a harness
        # parameter and not a measured property of the device, so it is reported and not sold.
        "hidden_paused_seconds": ((con.get("hidden_counters") or {}).get("paused_s")
                                  if not con.get("hidden_counters") is None else None),
        "hidden_paused_fraction": (
            ((con["hidden_counters"] or {}).get("paused_s") / con["hidden_counted_seconds"])
            if (con.get("hidden_counters") and con.get("hidden_counted_seconds")) else None),
        "hidden_yield_events": ((con.get("hidden_counters") or {}).get("yield_events")
                                if con.get("hidden_counters") else None),
        "declared_steps_ratio": (con["declared_steps_total"] / ref["declared_steps_total"]
                                 if ref["declared_steps_total"] else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--window", type=float, default=60.0, help="wall-clock seconds per run")
    ap.add_argument("--challenge-seconds", type=float, default=1.0,
                    help="length of one challenge window")
    ap.add_argument("--duties", default="0,0.05,0.10,0.25,0.50",
                    help="the VERIFIER's duty cycle, the fraction of wall time it challenges")
    ap.add_argument("--hidden-mode", choices=["separate", "inline"], default="separate")
    ap.add_argument("--hidden-duty", type=float, default=1.0,
                    help="separate mode: duty cycle of the hidden process while not yielding")
    ap.add_argument("--hidden-frac", type=float, default=1.0,
                    help="inline mode: undeclared matmuls per declared step while not yielding")
    ap.add_argument("--adaptive", action="store_true",
                    help="the operator pauses its hidden work while a challenge is in flight")
    ap.add_argument("--react-ms", type=float, default=0.0,
                    help="how long the hidden work keeps running after the challenge is "
                         "signalled: 0 is the operator its own scheduler tells, a positive value "
                         "is the operator that has to notice")
    ap.add_argument("--settle", type=float, default=0.3,
                    help="seconds between signalling and starting the challenge, the operator "
                         "draining its own queue")
    ap.add_argument("--poll-ms", type=float, default=5.0)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--warmup", type=float, default=8.0,
                    help="declared-chain warmup before the schedule starts")
    ap.add_argument("--hidden-warmup", type=float, default=3.0,
                    help="separate mode: uncounted warmup inside the hidden process, so its "
                         "counted period is steady state")
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--label", default="e1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    outdir = Path(args.out).parent
    outdir.mkdir(parents=True, exist_ok=True)
    flag = str(Path(args.out).with_suffix(".flag"))
    clear_flag(flag)

    print(f"E1 verifier-duty sweep: hidden work {args.hidden_mode}, adversary "
          f"{'ADAPTIVE' if args.adaptive else 'NON-ADAPTIVE'}, react {args.react_ms:.0f} ms, "
          f"challenge window {args.challenge_seconds}s, run {args.window}s", flush=True)

    points = []
    for duty in [float(d) for d in args.duties.split(",")]:
        rng = random.Random(args.seed + int(duty * 1000))
        schedule, m = build_schedule(args.window, duty, args.challenge_seconds, rng)
        print(f"\n-- verifier duty {duty:.2f}: {m} challenge windows of "
              f"{args.challenge_seconds}s in {args.window}s --", flush=True)
        ref = one_run(args, duty, schedule, with_hidden=False, adaptive=False,
                      flag=flag, tag=f"{args.label}-d{duty}-ref", outdir=outdir)
        con = one_run(args, duty, schedule, with_hidden=True, adaptive=args.adaptive,
                      flag=flag, tag=f"{args.label}-d{duty}-con", outdir=outdir)
        s = summarise(ref, con)
        points.append({"duty": duty, "challenge_windows": m,
                       "duty_achieved": con["duty_achieved"],
                       "reference": ref, "contended": con, "summary": s})
        print(f"   in-window drop {_pct(s['in_window_drop_pct'])}  "
              f"(null sigma {_pct(s['in_window_null_sigma_pct'])})  "
              f"detected={s['detected_at_3sigma']}", flush=True)
        print(f"   out-of-window drop {_pct(s['out_window_drop_pct'])}   "
              f"hidden iterations {s['hidden_iterations']}", flush=True)

    # the d=0 point, if measured, is the hidden-work ceiling: what the operator steals when the
    # verifier never challenges at all
    ceiling = next((p["summary"]["hidden_iterations_per_second"]
                    for p in points if p["duty"] == 0), None)
    ceiling_iters = next((p["summary"]["hidden_iterations"] for p in points if p["duty"] == 0), None)
    for p in points:
        h = p["summary"]["hidden_iterations_per_second"]
        p["summary"]["hidden_yield_vs_ceiling"] = (h / ceiling) if (ceiling and h is not None) else None

    result = {
        "schema": "ccverify.e1.verifier_duty.v1",
        "device": _device_name(),
        "hidden_work_mode": ("same process, same CUDA context" if args.hidden_mode == "inline"
                             else "separate competing process"),
        "adversary": ("adaptive, pauses while a challenge is in flight" if args.adaptive
                      else "non-adaptive, never reacts"),
        "react_ms": args.react_ms if args.adaptive else None,
        "settle_s": args.settle if args.adaptive else None,
        "matrix_n": args.size,
        "run_seconds": args.window,
        "challenge_window_seconds": args.challenge_seconds,
        "hidden_ceiling_iterations_per_second": ceiling,
        "hidden_ceiling_iterations": ceiling_iters,
        "hidden_yield_note": ("hidden work kept is a RATE ratio, iterations per counted second at "
                              "verifier duty d over the same at d=0, so a few seconds of timing "
                              "difference between runs cannot masquerade as a result"),
        "disclosure_note": ("hidden iteration counts are researcher-side measurements taken on "
                            "the operator's side of the boundary; a regulator receives none of "
                            "them"),
        "points": points,
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))

    print("\n=== SUMMARY ===")
    print("verifier   in-window   null    detected   out-window   hidden iters   hidden kept")
    print("duty       drop        sigma   at 3 sig   drop         (researcher)   vs ceiling")
    for p in points:
        s = p["summary"]
        kept = s.get("hidden_yield_vs_ceiling")
        print(f"{p['duty']:<11.2f}{_pct(s['in_window_drop_pct']):>9}  "
              f"{_pct(s['in_window_null_sigma_pct']):>7}  {str(s['detected_at_3sigma']):>9}  "
              f"{_pct(s['out_window_drop_pct']):>11}   {str(s['hidden_iterations']):>12}   "
              f"{'-' if kept is None else format(kept, '.3f'):>10}")
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
