#!/usr/bin/env python3
"""Undeclared GPU work at a controlled duty cycle, for the Tier 0.5 sensitivity sweep.

Stands in for an operator running something it did not declare on the same device, at a chosen
intensity. Deliberately a SEPARATE process, so the contention is real GPU scheduler contention
rather than a Python-level effect. Default time-slicing, no MPS, which is the realistic case.

TWO ADVERSARIES (--yield-flag)
------------------------------
Without --yield-flag this process never looks at what the verifier is doing. It runs at its duty
cycle whatever happens. That is the NON-ADAPTIVE adversary.

With --yield-flag it pauses while the flag file exists and resumes when it is removed. That is the
ADAPTIVE adversary, and the modelling choice matters: the flag is set by the operator's OWN
harness, not by anything this process infers about the device. A company that owns the machine does
not have to detect the verifier's puzzle, because its own orchestrator receives the puzzle and can
pause the hidden kernels before running it. Building a detector here would model a weaker adversary
than the real one, so we do not. --react-ms then models the weaker operator that has to notice.

WHY THE PACING WAS REWRITTEN (2026-09-03, v2)
--------------------------------------------
The first version paced on wall time: it submitted matmuls for `slice * duty` seconds and then
synchronised. Submission is asynchronous and costs about ten microseconds against a matmul of about
two milliseconds, so that loop queued far more device work than the slice was long, and the
synchronise waited for all of it. The consequence was that --duty never meant the share of the
device taken. Measured, a competitor asked for 2 percent took 40 to 65 percent of the device under
unbounded submission, and even with submission bounded at four matmuls ahead the minimum slice was
four matmuls, so duties of 0.02 and 0.05 produced the same experiment.

The fix is to pace on measured DEVICE time. The per-matmul device time is calibrated first, at the
same queue depth the run will use. Each period then submits a whole number of matmuls, waits for
them, and sleeps for however long the requested duty implies. Achieved duty is measured and
reported, so the label is never the thing quoted: device_share_taken is.

--queue-depth 0 restores the original unbounded submission, kept selectable because it is what the
published July figures were measured with, and reproducing them is how the correction was pinned.
"""

import argparse
import json
import os
import time
from pathlib import Path

import torch


def _device():
    # cuda everywhere real. CCV_DEVICE=cpu exists only so the control flow can be dry-run off the
    # confidential box before renting one; it measures nothing meaningful.
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
        return _UNREADABLE
    try:
        return float(raw)
    except ValueError:
        return time.time()


def submit_burst(a, b, k, queue_depth, should_stop=None):
    """Submit k matmuls and wait for all of them. Returns (tensor, matmuls actually submitted).

    queue_depth bounds how far the CPU may run ahead of the device. Zero or less means unbounded,
    which is the original behaviour and the reason the duty label used to be meaningless.

    should_stop is checked at each synchronisation point so a burst can be abandoned partway. Without
    that the pause could only happen between bursts, and at high duty a burst is long: pacing on
    device time sizes it at period times duty over the per-matmul time, which is fifty matmuls for a
    100 ms period at full tilt. The reaction floor would then be the burst length rather than
    anything about the operator, and the reaction sweep would be measuring the harness.
    """
    done = 0
    for i in range(k):
        a = a @ b
        done += 1
        if queue_depth > 0 and (i + 1) % queue_depth == 0:
            _sync()
            if should_stop is not None and should_stop():
                return a, done
    _sync()
    return a, done


def calibrate(a, b, queue_depth, seconds, burst):
    """Device seconds per matmul, measured at the SAME queue depth the run will use.

    Calibrating at a different queue depth than the run is what made every device-share figure in
    the first version wrong: a calibration that synchronises after every matmul pays a launch
    round-trip the measured run does not, and reads about fifteen percent low.
    """
    a, _ = submit_burst(a, b, burst, queue_depth)      # warm the device before timing anything
    n = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        a, d = submit_burst(a, b, burst, queue_depth)
        n += d
    dt = time.time() - t0
    return dt / n, n, dt, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duty", type=float, required=True, help="0.0 to 1.0, share of the device")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--yield-flag", default=None,
                    help="flag file the operator's own orchestrator writes while a verifier "
                         "challenge is in flight. Absent, this is the non-adaptive adversary.")
    ap.add_argument("--react-ms", type=float, default=0.0,
                    help="keep working this long after the flag appears, modelling an operator "
                         "that has to notice the challenge instead of being told about it")
    ap.add_argument("--poll-ms", type=float, default=2.0)
    ap.add_argument("--queue-depth", type=int, default=4,
                    help="synchronise every N matmuls inside a burst, so the CPU cannot run far "
                         "ahead of the device and the pause is not defeated by a queue draining "
                         "after the decision to yield. ZERO OR LESS is the original unbounded "
                         "submission, kept selectable to reproduce the published July figures.")
    ap.add_argument("--period-ms", type=float, default=100.0,
                    help="target length of one work-plus-sleep cycle. The burst is sized from the "
                         "calibrated per-matmul device time so achieved duty matches --duty.")
    ap.add_argument("--calibrate-s", type=float, default=1.5,
                    help="solo calibration before counting starts, at the run's queue depth")
    ap.add_argument("--warmup-s", type=float, default=0.0,
                    help="work this long after calibrating and before counting, ignoring the flag")
    ap.add_argument("--ready-flag", default=None,
                    help="written once calibration and warmup are done and counting is about to "
                         "start, so a harness can wait for steady state instead of guessing")
    ap.add_argument("--stop-flag", default=None, help="exit when this file appears")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    stats = {
        "schema": "ccverify.e1.contention.v2",
        "duty_requested": args.duty,
        "adaptive": bool(args.yield_flag),
        "react_ms_nominal": args.react_ms,
        "queue_depth": args.queue_depth,
        "matrix_n": args.size,
        "flop_per_iteration": 2 * args.size ** 3,
        "iterations": 0,
        "worked_s": 0.0,
        "slept_s": 0.0,
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
    # Keep the chain numerically finite. Repeated a = a @ b on randn saturates bfloat16 within
    # about twenty iterations. Throughput is data independent on tensor cores so this changed no
    # earlier number, but it was an unstated asymmetry against the declared chain, which
    # normalises every step.
    b = b / (n ** 0.5)
    _sync()

    burst_unit = max(1, args.queue_depth if args.queue_depth > 0 else 4)
    t_mm, cal_iters, cal_dt, a = calibrate(a, b, args.queue_depth, args.calibrate_s, burst_unit)
    solo_ips = 1.0 / t_mm
    stats["calibration"] = {
        "seconds_per_matmul": t_mm,
        "solo_iterations_per_second": solo_ips,
        "solo_tflops": solo_ips * stats["flop_per_iteration"] / 1e12,
        "iterations": cal_iters,
        "elapsed_s": cal_dt,
        "queue_depth": args.queue_depth,
        "note": "solo rate at the run's own queue depth, so device shares divide by a "
                "commensurable denominator",
    }

    # Size the burst so one period delivers exactly the requested duty.
    k = max(1, int(round((args.period_ms / 1000.0) * args.duty / t_mm)))

    if args.warmup_s > 0:
        w_end = time.time() + args.warmup_s
        while time.time() < w_end:
            a, _ = submit_burst(a, b, k, args.queue_depth)

    if args.ready_flag:
        Path(args.ready_flag).write_text(repr(time.time()))

    poll_s = args.poll_ms / 1000.0
    react_s = args.react_ms / 1000.0
    count_t0 = time.time()
    end = count_t0 + args.seconds
    iters = 0
    signalled_at = None
    next_poll = 0.0

    def stopped():
        return bool(args.stop_flag) and Path(args.stop_flag).exists()

    def poll():
        nonlocal signalled_at, next_poll
        now = time.time()
        if now >= next_poll:
            next_poll = now + poll_s
            if args.yield_flag:
                seen = _flag_signalled_at(args.yield_flag)
                if seen is not _UNREADABLE:
                    signalled_at = seen
            else:
                signalled_at = None
        if signalled_at is None:
            return False
        return (time.time() - signalled_at) >= react_s

    while time.time() < end and not stopped():
        if poll():
            _sync()
            stats["yield_events"] += 1
            stats["reaction_latency_s"].append(round(time.time() - signalled_at, 6))
            pause_t0 = time.time()
            while time.time() < end and not stopped():
                if _flag_signalled_at(args.yield_flag) is None:
                    break
                time.sleep(poll_s)
            stats["paused_s"] += time.time() - pause_t0
            signalled_at = None
            next_poll = 0.0
            continue

        t0 = time.time()
        a, done = submit_burst(a, b, k, args.queue_depth, should_stop=poll)
        worked = time.time() - t0
        iters += done
        stats["worked_s"] += worked
        if done < k:
            continue          # abandoned to yield; the pause is handled at the top of the loop

        # Sleep exactly what the requested duty implies for the work just done. Pacing on measured
        # work rather than on a fixed slice is what makes the achieved duty match the label.
        if args.duty < 1.0:
            sleep_for = worked * (1.0 - args.duty) / args.duty
            s_end = time.time() + sleep_for
            slept_t0 = time.time()
            while time.time() < min(s_end, end) and not stopped():
                time.sleep(min(poll_s, max(0.0, s_end - time.time())))
                if poll():
                    break
            stats["slept_s"] += time.time() - slept_t0

    elapsed = time.time() - count_t0
    active = stats["worked_s"] + stats["slept_s"]
    stats["iterations"] = iters
    stats["counted_elapsed_s"] = elapsed
    stats["burst_matmuls"] = k
    stats["iterations_per_second"] = iters / elapsed if elapsed else None
    stats["achieved_duty_of_active_time"] = (stats["worked_s"] / active) if active else None
    stats["device_share_taken"] = ((iters / elapsed) / solo_ips) if elapsed else None
    stats["device_share_note"] = (
        "iterations per counted second over the solo rate calibrated on this device at the same "
        "queue depth. THIS, not the --duty label, is the fraction of the device this process took.")
    stats["tflops_delivered"] = ((iters / elapsed) * stats["flop_per_iteration"] / 1e12
                                 if elapsed else None)
    if stats["reaction_latency_s"]:
        lat = stats["reaction_latency_s"]
        stats["reaction_latency_mean_s"] = sum(lat) / len(lat)
        stats["reaction_latency_max_s"] = max(lat)
    if args.out:
        Path(args.out).write_text(json.dumps(stats, indent=2, sort_keys=True))
    share = stats["device_share_taken"] or 0.0
    print(f"contention requested={args.duty} achieved_share={share:.4f} iters={iters} "
          f"burst={k} yields={stats['yield_events']} paused={stats['paused_s']:.2f}s")


if __name__ == "__main__":
    main()
