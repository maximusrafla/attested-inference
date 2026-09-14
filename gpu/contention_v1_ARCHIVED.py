#!/usr/bin/env python3
"""Undeclared GPU work at a controlled duty cycle, for the Tier 0.5 sensitivity sweep.

Stands in for an operator running something it did not declare on the same device, at a chosen
intensity. The duty cycle is what makes the sweep possible: at duty d the process tries to
occupy the device for d of every cycle, so the sweep answers the question Tier 0.5 actually
needs answered, which is how much undeclared work has to be running before the occupancy check
can see it.

Deliberately a SEPARATE process, so the contention is real GPU scheduler contention rather than
a Python-level effect. Default time-slicing, no MPS, which is the realistic case.

TWO ADVERSARIES (--yield-flag added for E1, 2026-09-03)
------------------------------------------------------
Without --yield-flag this process never looks at what the verifier is doing. It runs at its duty
cycle whatever happens. That is the NON-ADAPTIVE adversary, and it is the one every published
occupancy number in this repository was measured against.

With --yield-flag it pauses while the flag file exists and resumes when it is removed. That is
the ADAPTIVE adversary, and the modelling choice matters: the flag is set by the operator's OWN
harness, not by anything this process infers about the device. A company that owns the machine
does not have to detect the verifier's puzzle, because its own orchestrator receives the puzzle
and can pause the hidden kernels before running it. Building a detector here would model a
weaker adversary than the real one, so we do not.

--react-ms exists to model the weaker adversary anyway, on purpose: it keeps the hidden work
running for that long after the flag appears, which is what a company that has to notice the
puzzle rather than being told about it would look like. Sweeping it turns "the adaptive
adversary wins" into "the adaptive adversary wins if it can react within X milliseconds".

The flag file's contents are the epoch timestamp at which the operator's harness signalled, so
the reaction latency reported below is measured against when the challenge was actually
signalled, not against when this process happened to poll.
"""

import argparse
import json
import os
import time
from pathlib import Path

import torch


def _device():
    # cuda everywhere real. CCV_DEVICE=cpu exists only so the control flow of the E1 harness can
    # be dry-run off the confidential box before renting one; it measures nothing meaningful.
    return torch.device(os.environ.get("CCV_DEVICE", "cuda"))


def _sync():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        torch.cuda.synchronize()


_UNREADABLE = object()


def _flag_signalled_at(path):
    """Epoch seconds the operator's harness signalled a challenge, None if no flag is set, or
    _UNREADABLE if the file exists but could not be read this instant.

    The three-way answer matters. A transient read failure read as "no challenge in flight" would
    silently resume the hidden work in the middle of a challenge window and corrupt the very
    measurement this script exists to take, so the caller holds its previous state instead.
    """
    try:
        raw = Path(path).read_text().strip()
    except FileNotFoundError:
        return None
    except OSError:
        return _UNREADABLE
    if not raw:
        return _UNREADABLE          # written but not yet flushed
    try:
        return float(raw)
    except ValueError:
        return time.time()          # flag exists but carries no timestamp; treat as just now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duty", type=float, required=True, help="0.0 to 1.0")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--yield-flag", default=None,
                    help="path to a flag file the operator's own orchestrator writes while a "
                         "verifier challenge is in flight. Absent, this is the non-adaptive "
                         "adversary and behaviour is exactly as it was before E1.")
    ap.add_argument("--react-ms", type=float, default=0.0,
                    help="keep working this long after the flag appears, modelling an operator "
                         "that has to notice the challenge instead of being told about it")
    ap.add_argument("--poll-ms", type=float, default=5.0)
    ap.add_argument("--queue-depth", type=int, default=4,
                    help="synchronize every N matmuls during a work slice, so the CPU cannot run "
                         "far ahead of the device and the pause is not defeated by a long queue "
                         "draining after the decision to yield. ZERO OR LESS means never "
                         "synchronize inside a slice, which is the original behaviour of this "
                         "script and is kept selectable: submission is asynchronous and costs "
                         "about ten microseconds against a matmul of about two milliseconds, so "
                         "an unbounded slice queues far more work than the slice is long, and the "
                         "duty cycle asked for is then not the share of the device taken.")
    ap.add_argument("--calibrate-s", type=float, default=0.5,
                    help="measure the solo matmul rate for this long before counting, so the "
                         "share of the device taken is recorded and the duty label is never the "
                         "thing quoted")
    ap.add_argument("--warmup-s", type=float, default=0.0,
                    help="work this long before counting anything, so the counted period is the "
                         "steady state and not the ramp. The verifier flag is ignored during it.")
    ap.add_argument("--ready-flag", default=None,
                    help="written once warmup is done and counting is about to start, so the "
                         "harness can wait for steady state instead of guessing how long torch "
                         "takes to import and allocate on this box")
    ap.add_argument("--stop-flag", default=None,
                    help="exit when this file appears. Lets the harness bracket the counted "
                         "period exactly, instead of guessing a duration and counting a tail.")
    ap.add_argument("--out", default=None, help="write counts and measured latencies here")
    args = ap.parse_args()

    stats = {
        "schema": "ccverify.e1.contention.v1",
        "duty": args.duty,
        "adaptive": bool(args.yield_flag),
        "react_ms_nominal": args.react_ms,
        "seconds_requested": args.seconds,
        "iterations": 0,
        "worked_s": 0.0,
        "paused_s": 0.0,
        "yield_events": 0,
        "reaction_latency_s": [],
    }

    if args.duty <= 0:
        time.sleep(args.seconds)
        if args.out:
            Path(args.out).write_text(json.dumps(stats, indent=2, sort_keys=True))
        print(f"contention duty={args.duty} iters=0")
        return

    dev = _device()
    n = args.size
    g = torch.Generator(device=dev).manual_seed(7)
    a = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    b = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
    _sync()

    slice_s = 0.05                      # one duty period, short enough to interleave
    on = slice_s * args.duty
    off = slice_s - on
    poll_s = args.poll_ms / 1000.0
    react_s = args.react_ms / 1000.0

    # Solo rate on this device, measured back to back with the run and with the same matmul, so
    # the share of the device this process actually takes is recorded rather than inferred later.
    # It happens before the ready flag, so it is over before any harness starts measuring.
    cal_iters = 0
    cal_t0 = time.time()
    while time.time() - cal_t0 < args.calibrate_s:
        a = a @ b
        _sync()
        cal_iters += 1
    cal_dt = time.time() - cal_t0
    stats["solo_iterations_per_second"] = cal_iters / cal_dt if cal_dt > 0 else None
    stats["solo_calibration_iterations"] = cal_iters
    stats["solo_calibration_seconds"] = cal_dt

    if args.warmup_s > 0:
        w_end = time.time() + args.warmup_s
        while time.time() < w_end:
            a = a @ b
            _sync()

    if args.ready_flag:
        Path(args.ready_flag).write_text(repr(time.time()))

    count_t0 = time.time()
    end = count_t0 + args.seconds
    iters = 0

    signalled_at = None                 # epoch time the current in-flight challenge was signalled
    next_poll = 0.0

    def stopped():
        return bool(args.stop_flag) and Path(args.stop_flag).exists()

    def poll():
        """Refresh the flag state at most every poll_s. True once the hidden work should be
        paused, which is react_s after the challenge was signalled."""
        nonlocal signalled_at, next_poll
        now = time.time()
        if now >= next_poll:
            next_poll = now + poll_s
            if args.yield_flag:
                seen = _flag_signalled_at(args.yield_flag)
                if seen is not _UNREADABLE:      # unreadable holds the previous state
                    signalled_at = seen
            else:
                signalled_at = None
        if signalled_at is None:
            return False
        return (time.time() - signalled_at) >= react_s

    while time.time() < end and not stopped():
        if poll():
            # Stop submitting, drain what is already queued, then hold until the flag clears.
            _sync()
            latency = time.time() - signalled_at
            stats["yield_events"] += 1
            stats["reaction_latency_s"].append(round(latency, 6))
            pause_t0 = time.time()
            while time.time() < end and not stopped():
                seen = _flag_signalled_at(args.yield_flag)
                if seen is None:
                    break
                time.sleep(poll_s)
            stats["paused_s"] += time.time() - pause_t0
            signalled_at = None
            next_poll = 0.0
            continue

        t0 = time.time()
        submitted = 0
        broke_to_yield = False
        while time.time() - t0 < on:
            a = a @ b
            iters += 1
            submitted += 1
            if args.queue_depth > 0 and submitted % args.queue_depth == 0:
                _sync()
                if poll():
                    broke_to_yield = True
                    break
        _sync()
        stats["worked_s"] += time.time() - t0
        if broke_to_yield:
            continue
        # Idle half of the duty cycle, slept in poll-sized pieces. Sleeping it in one block would
        # make the reported reaction latency the off-phase length rather than a reaction time.
        idle_end = time.time() + off
        while off > 0 and time.time() < min(idle_end, end) and not stopped():
            time.sleep(min(poll_s, max(0.0, idle_end - time.time())))
            if poll():
                break

    stats["iterations"] = iters
    stats["counted_elapsed_s"] = time.time() - count_t0
    stats["count_start_epoch"] = count_t0
    stats["count_end_epoch"] = time.time()
    stats["iterations_per_second"] = (iters / stats["counted_elapsed_s"]
                                      if stats["counted_elapsed_s"] > 0 else None)
    solo = stats.get("solo_iterations_per_second")
    stats["device_share_taken"] = (
        (stats["iterations_per_second"] / solo)
        if (solo and stats["iterations_per_second"] is not None) else None)
    stats["device_share_note"] = (
        "iterations per counted second divided by the solo rate measured on this device moments "
        "earlier. This, not the --duty label, is the fraction of the device this process took.")
    stats["queue_depth"] = args.queue_depth
    stats["warmup_s"] = args.warmup_s
    stats["stopped_by_flag"] = bool(args.stop_flag) and Path(args.stop_flag).exists()
    stats["matrix_n"] = n
    stats["flop_per_iteration"] = 2 * n ** 3
    if stats["reaction_latency_s"]:
        lat = stats["reaction_latency_s"]
        stats["reaction_latency_mean_s"] = sum(lat) / len(lat)
        stats["reaction_latency_max_s"] = max(lat)
    if args.out:
        Path(args.out).write_text(json.dumps(stats, indent=2, sort_keys=True))
    print(f"contention duty={args.duty} iters={iters} yields={stats['yield_events']} "
          f"paused={stats['paused_s']:.2f}s")


if __name__ == "__main__":
    main()
