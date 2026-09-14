#!/usr/bin/env python3
"""E1 v2: what unpredictable challenge timing buys against an operator that reacts.

The occupancy check times a seeded computation on the operator's device and asks whether it ran as
fast as an uncontended device should. An operator that owns the machine is told by its own
scheduler when a challenge arrives, so it can pause undeclared work for the duration and resume
after. Unpredictable timing does not defeat that, because the operator reacts rather than predicts.
What it does buy is that the operator must surrender the device for whatever fraction of wall time
the verifier is genuinely challenging. That fraction is the verifier's DUTY CYCLE.

WHAT v2 FIXED (2026-09-03), each of which flattered the scheme in v1
-------------------------------------------------------------------
1. **n was 1.** Every point was a single reference run against a single contended run, so any error
   constant within a run and different between the two was invisible. One such bug was found at 7
   percent; a 1 percent version would have read as a confident detection. v2 repeats every point
   and reports the spread ACROSS repeats, which is the null a verifier actually faces.

2. **The decision rule compared a mean against the spread of individuals.** The statistic is a
   ratio of means over m windows, so its null spread is sigma/sqrt(m), not sigma. v2 reports three
   rules side by side: the across-repeat rule (primary), the within-run standard error, and the
   old raw-sigma rule for continuity.

3. **The drain gap put every window in a clock-boost transient.** A 0.15 s idle pause before each
   timed window raised in-window throughput about 7 percent above the same run's steady state.
   Applying it symmetrically cancelled it between arms but left the measurement in a regime never
   characterised, and made these runs incomparable with the reaction sweep, which used no gap.
   v2 defaults the gap to zero and treats any nonzero value as a separate, labelled condition.

4. **Undeclared work was quoted in a duty knob, not a device share.** v2 calibrates the solo rate
   of the undeclared operation and reports the share taken.

The challenge here is the declared chain itself, timed; the operator's knowledge that a challenge
is in flight comes from a flag the harness sets. So this measures how well an operator that is TOLD
can evade. It does not measure whether a real challenge is distinguishable, and it never modelled a
puzzle that displaces production work, so the cost side of the trade is not addressed. The
out-of-window column is retained precisely because it bounds what a covert challenge would be worth.
"""

import argparse
import json
import math
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
    return torch.device(os.environ.get("CCV_DEVICE", "cuda"))


def _sync():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        torch.cuda.synchronize()


def _device_name():
    if os.environ.get("CCV_DEVICE", "cuda") == "cuda":
        return torch.cuda.get_device_name(0)
    return "cpu (DRY RUN, measures nothing)"


def _pct(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}%"


def calibrate_solo(n, seconds, seed=99):
    """Solo rate of the undeclared operation, the denominator for every device share."""
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
    return it / (time.time() - t0)


def set_flag(path):
    Path(path).write_text(repr(time.time()))


def clear_flag(path):
    """Retried and loud: a flag left set would pause the undeclared work for the rest of the run
    and quietly manufacture the negative result."""
    for _ in range(50):
        try:
            Path(path).unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(f"could not clear the challenge flag at {path}")


def build_schedule(total_s, duty, window_s, rng):
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
    """The declared, verifier-seeded dependent chain, plus optional undeclared work in the same
    process and CUDA context. Only declared steps count into throughput."""

    def __init__(self, n, seed, need_hidden):
        dev = _device()
        g = torch.Generator(device=dev).manual_seed(seed)
        self.n = n
        self.x = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        self.w = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        self.wn = self.w / (n ** 0.5)
        self.u = (torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16) / (n ** 0.5)
                  if need_hidden else None)
        self.steps = 0
        self.hidden = 0
        self._credit = 0.0
        _sync()

    def batch(self, k, hidden_frac):
        for _ in range(k):
            self.x = torch.nn.functional.normalize(self.x @ self.w, dim=1)
            self.steps += 1
            if hidden_frac and self.u is not None:
                self._credit += hidden_frac
                while self._credit >= 1.0:
                    self.u = self.u @ self.wn
                    self.hidden += 1
                    self._credit -= 1.0
        _sync()

    def tflops(self, steps, elapsed):
        return (2.0 * self.n ** 3 * steps) / elapsed / 1e12


def one_run(args, schedule, with_hidden, adaptive, flag, tag, outdir):
    inline = args.hidden_mode == "inline"
    chain = Chain(args.size, 1234, need_hidden=(with_hidden and inline))

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
        ready_path = str(Path(outdir) / f"ready-{tag}.flag")
        Path(ready_path).unlink(missing_ok=True)
        cmd = [args.python, str(HERE / "contention.py"),
               "--duty", str(args.hidden_duty),
               "--seconds", str(args.window + 180.0),
               "--size", str(args.size),
               "--queue-depth", str(args.queue_depth),
               "--period-ms", str(args.period_ms),
               "--calibrate-s", str(args.hidden_calibrate_s),
               "--ready-flag", ready_path, "--stop-flag", stop_path,
               "--out", counts_path]
        if adaptive:
            cmd += ["--yield-flag", flag, "--react-ms", str(args.react_ms),
                    "--poll-ms", str(args.poll_ms)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 300
        while not Path(ready_path).exists():
            if proc.poll() is not None:
                raise RuntimeError("the undeclared-work process exited before it was ready")
            if time.time() > deadline:
                raise RuntimeError("the undeclared-work process never reported ready")
            time.sleep(0.05)
        Path(ready_path).unlink(missing_ok=True)

    challenges, idles = [], []
    run_t0 = time.time()
    for kind, dur in schedule:
        if dur <= 0:
            continue
        if kind == "challenge":
            if adaptive and with_hidden:
                set_flag(flag)
            # Any drain gap is applied in the reference run too. It also defaults to zero now,
            # because a nonzero gap raises in-window throughput about 7 percent above the run's
            # own steady state and puts the whole measurement in a transient.
            if args.settle > 0:
                time.sleep(args.settle)
            s0, h0, t0 = chain.steps, chain.hidden, time.time()
            while time.time() - t0 < dur:
                frac = 0.0
                if with_hidden and inline:
                    if not adaptive or (time.time() - t0) < (args.react_ms / 1000.0):
                        frac = args.hidden_frac
                chain.batch(args.batch, frac)
            el = time.time() - t0
            challenges.append({"elapsed_s": round(el, 4), "declared_steps": chain.steps - s0,
                               "tflops": chain.tflops(chain.steps - s0, el),
                               "hidden_iterations_in_window": chain.hidden - h0})
            if adaptive and with_hidden:
                clear_flag(flag)
        else:
            s0, t0 = chain.steps, time.time()
            while time.time() - t0 < dur:
                chain.batch(args.batch, args.hidden_frac if (with_hidden and inline) else 0.0)
            el = time.time() - t0
            idles.append({"elapsed_s": round(el, 4), "declared_steps": chain.steps - s0,
                          "tflops": chain.tflops(chain.steps - s0, el)})

    total = time.time() - run_t0
    hc = None
    if proc:
        Path(stop_path).write_text("stop")
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.terminate(); proc.wait(timeout=30)
        if counts_path and Path(counts_path).exists():
            hc = json.loads(Path(counts_path).read_text())
        time.sleep(0.5)
    Path(stop_path).unlink(missing_ok=True)
    clear_flag(flag)

    if inline:
        hidden_iters, hidden_secs = chain.hidden, total
    else:
        hidden_iters = (hc or {}).get("iterations")
        hidden_secs = (hc or {}).get("counted_elapsed_s")
    return {
        "tag": tag, "with_hidden": with_hidden, "adaptive": adaptive,
        "total_elapsed_s": round(total, 3),
        "declared_steps_total": chain.steps,
        "challenge_windows": challenges, "idle_phases": idles,
        "in_window_tflops": [c["tflops"] for c in challenges],
        "out_window_tflops": [i["tflops"] for i in idles],
        "duty_achieved": (sum(c["elapsed_s"] for c in challenges) / total) if total else 0.0,
        "hidden_iterations": hidden_iters,
        "hidden_counted_seconds": hidden_secs,
        "hidden_iterations_per_second": (hidden_iters / hidden_secs
                                         if hidden_iters is not None and hidden_secs else None),
        "hidden_in_window_iterations": sum(c["hidden_iterations_in_window"] for c in challenges),
        "hidden_paused_seconds": (hc or {}).get("paused_s") if hc else None,
        "hidden_device_share": (hc or {}).get("device_share_taken") if hc else None,
        "hidden_counters": hc,
    }


def pair_stats(ref, con):
    """One reference/contended pair. Reports the within-run rules; the across-repeat rule is
    computed by the caller, and it is the one to trust."""
    ri, ci = ref["in_window_tflops"], con["in_window_tflops"]
    ro, co = ref["out_window_tflops"], con["out_window_tflops"]
    out = {"in_window_ratio": None, "out_window_ratio": None}
    if ri and ci:
        mr, mc = statistics.mean(ri), statistics.mean(ci)
        out["in_window_ratio"] = mc / mr
        out["in_window_drop_pct"] = 100 * (1 - mc / mr)
        if len(ri) > 1 and len(ci) > 1:
            sr, sc = statistics.stdev(ri), statistics.stdev(ci)
            se = math.sqrt(sr ** 2 / len(ri) + sc ** 2 / len(ci)) / mr
            out["within_run_sigma_pct"] = 100 * sr / mr
            out["within_run_standard_error_pct"] = 100 * se
            out["detected_within_run_sem"] = (1 - mc / mr) > 3 * se
            out["detected_within_run_raw_sigma_DEPRECATED"] = (1 - mc / mr) > 3 * (sr / mr)
    if ro and co:
        out["out_window_ratio"] = statistics.mean(co) / statistics.mean(ro)
        out["out_window_drop_pct"] = 100 * (1 - out["out_window_ratio"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--challenge-seconds", type=float, default=1.0)
    ap.add_argument("--duties", default="0,0.10,0.25,0.50")
    ap.add_argument("--repeats", type=int, default=3,
                    help="independent reference/contended pairs per point. The spread across "
                         "these is the null that matters; v1 had one pair and could not see a "
                         "systematic error constant within a run.")
    ap.add_argument("--hidden-mode", choices=["separate", "inline"], default="separate")
    ap.add_argument("--hidden-duty", type=float, default=1.0)
    ap.add_argument("--hidden-frac", type=float, default=1.0)
    ap.add_argument("--adaptive", action="store_true")
    ap.add_argument("--react-ms", type=float, default=0.0)
    ap.add_argument("--settle", type=float, default=0.0,
                    help="drain gap before each challenge. Defaults to ZERO: a nonzero gap puts "
                         "the timed window in a clock-boost transient about 7 percent above the "
                         "run's own steady state. Applied to the reference run too when used.")
    ap.add_argument("--poll-ms", type=float, default=2.0)
    ap.add_argument("--queue-depth", type=int, default=4)
    ap.add_argument("--period-ms", type=float, default=100.0)
    ap.add_argument("--hidden-calibrate-s", type=float, default=1.5)
    ap.add_argument("--calibrate-s", type=float, default=3.0)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--warmup", type=float, default=8.0)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--label", default="e1v2")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    outdir = Path(args.out).parent
    outdir.mkdir(parents=True, exist_ok=True)
    flag = str(Path(args.out).with_suffix(".flag"))
    clear_flag(flag)

    print(f"E1 v2: hidden work {args.hidden_mode}, "
          f"{'ADAPTIVE' if args.adaptive else 'NON-ADAPTIVE'}, react {args.react_ms:.0f} ms, "
          f"settle {args.settle:.2f}s, window {args.challenge_seconds}s, run {args.window}s, "
          f"{args.repeats} repeats", flush=True)
    solo_ips = calibrate_solo(args.size, args.calibrate_s)
    print(f"solo undeclared rate {solo_ips:.1f} matmul/s = "
          f"{solo_ips*2*args.size**3/1e12:.1f} TFLOP/s", flush=True)

    points = []
    for duty in [float(d) for d in args.duties.split(",")]:
        rng = random.Random(args.seed + int(duty * 1000))
        schedule, m = build_schedule(args.window, duty, args.challenge_seconds, rng)
        print(f"\n-- verifier duty {duty:.2f}: {m} windows of {args.challenge_seconds}s "
              f"in {args.window}s, {args.repeats} repeats --", flush=True)
        reps = []
        for rep in range(args.repeats):
            ref = one_run(args, schedule, False, False, flag, f"{args.label}-d{duty}-r{rep}-ref", outdir)
            con = one_run(args, schedule, True, args.adaptive, flag,
                          f"{args.label}-d{duty}-r{rep}-con", outdir)
            st = pair_stats(ref, con)
            share = con.get("hidden_device_share")
            if share is None and con["hidden_iterations_per_second"]:
                share = con["hidden_iterations_per_second"] / solo_ips
            st["hidden_device_share"] = share
            st["hidden_iterations"] = con["hidden_iterations"]
            st["hidden_in_window_iterations"] = con["hidden_in_window_iterations"]
            st["hidden_iterations_per_second"] = con["hidden_iterations_per_second"]
            reps.append({"repeat": rep, "reference": ref, "contended": con, "summary": st})
            print(f"   rep {rep}: in-window drop {_pct(st.get('in_window_drop_pct'))}  "
                  f"out-of-window {_pct(st.get('out_window_drop_pct'))}  "
                  f"hidden share {_pct(100*share if share else None)}  "
                  f"hidden in window {con['hidden_in_window_iterations']}", flush=True)

        drops = [r["summary"].get("in_window_drop_pct") for r in reps
                 if r["summary"].get("in_window_drop_pct") is not None]
        agg = {"n_repeats": len(reps)}
        if drops:
            agg["in_window_drop_pct_mean"] = statistics.mean(drops)
            agg["in_window_drop_pct_across_repeat_sd"] = (statistics.stdev(drops)
                                                          if len(drops) > 1 else None)
            if len(drops) > 1:
                se = statistics.stdev(drops) / math.sqrt(len(drops))
                agg["in_window_drop_pct_standard_error"] = se
                agg["detected_across_repeats"] = statistics.mean(drops) > 3 * se
        shares = [r["summary"]["hidden_device_share"] for r in reps
                  if r["summary"].get("hidden_device_share")]
        if shares:
            agg["hidden_device_share_mean"] = statistics.mean(shares)
        ows = [r["summary"].get("out_window_drop_pct") for r in reps
               if r["summary"].get("out_window_drop_pct") is not None]
        if ows:
            agg["out_window_drop_pct_mean"] = statistics.mean(ows)
        rates = [r["summary"]["hidden_iterations_per_second"] for r in reps
                 if r["summary"].get("hidden_iterations_per_second")]
        if rates:
            agg["hidden_iterations_per_second_mean"] = statistics.mean(rates)
        points.append({"duty": duty, "challenge_windows": m, "repeats": reps, "aggregate": agg})
        print(f"   => mean in-window drop {_pct(agg.get('in_window_drop_pct_mean'))}  "
              f"across-repeat sd {_pct(agg.get('in_window_drop_pct_across_repeat_sd'))}  "
              f"detected {agg.get('detected_across_repeats')}", flush=True)

    ceiling = next((p["aggregate"].get("hidden_iterations_per_second_mean")
                    for p in points if p["duty"] == 0), None)
    for p in points:
        h = p["aggregate"].get("hidden_iterations_per_second_mean")
        p["aggregate"]["hidden_kept_vs_no_verifier"] = (h / ceiling) if (ceiling and h) else None

    result = {
        "schema": "ccverify.e1.verifier_duty.v2",
        "device": _device_name(),
        "solo_undeclared_iterations_per_second": solo_ips,
        "hidden_work_mode": ("same process, same CUDA context" if args.hidden_mode == "inline"
                             else "separate competing process"),
        "adversary": ("adaptive, pauses while a challenge is in flight" if args.adaptive
                      else "non-adaptive, never reacts"),
        "react_ms": args.react_ms, "settle_s": args.settle,
        "matrix_n": args.size, "run_seconds": args.window,
        "challenge_window_seconds": args.challenge_seconds,
        "repeats_per_point": args.repeats,
        "hidden_ceiling_iterations_per_second": ceiling,
        "decision_rules_note": (
            "detected_across_repeats is the primary rule: mean drop against three standard errors "
            "of the across-repeat spread. The within-run rules are reported per repeat for "
            "comparison; the raw-sigma one is what v1 used and it compares a mean against the "
            "spread of individuals, which is the standard deviation of the wrong thing."),
        "scope_note": (
            "the challenge is the declared chain itself, timed, and the operator learns of it from "
            "a flag this harness sets. So this measures an operator that is TOLD. Whether a real "
            "challenge is distinguishable is untested, and no puzzle displacing production work "
            "was modelled, so the cost side of the trade is not addressed here."),
        "disclosure_note": ("undeclared iteration counts are researcher-side measurements from the "
                            "operator's side of the boundary; a regulator receives none of them"),
        "points": points,
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))

    print("\n=== SUMMARY ===")
    print("duty    in-win drop   across-rep sd   detected   out-win drop   hidden share   kept")
    for p in points:
        a = p["aggregate"]
        kept = a.get("hidden_kept_vs_no_verifier")
        sh = a.get("hidden_device_share_mean")
        print(f"{p['duty']:<8}{_pct(a.get('in_window_drop_pct_mean')):>11}"
              f"{_pct(a.get('in_window_drop_pct_across_repeat_sd')):>16}"
              f"{str(a.get('detected_across_repeats')):>11}"
              f"{_pct(a.get('out_window_drop_pct_mean')):>15}"
              f"{_pct(100*sh if sh else None):>15}"
              f"{'-' if kept is None else format(kept, '.3f'):>8}")
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
