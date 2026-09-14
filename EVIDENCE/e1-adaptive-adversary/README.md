# E1: the occupancy check against an operator that reacts

> **SUPERSEDED by `./e1v2-adaptive-adversary/`.** This run was reviewed and rebuilt the same day.
> Its duty axis was quantised, its device-share denominators were incommensurable, its detection rule
> compared a mean against the spread of individuals, and every point was n=1. The results here are
> retained as the record, including the deliberately-kept confounded run. For any number, use the v2
> directory; its README says which of these numbers the rerun superseded.

Retained results from one session on 2026-09-03, on one NVIDIA H100 NVL inside an AMD SEV-SNP confidential
virtual machine. **1.63 GPU-hours, $11 to $14.** Same driver 595.71.05, VBIOS 96.00.9F.00.04 and PyTorch
2.13.0+cu130 as the July runs, deliberately, so the comparisons are like for like.

Every command with its expected output, cost and runtime: `././gpu/E1-RUNPLAN.md`.

## What was measured

Every occupancy figure in this project, the roughly 6 percent and roughly 19 percent hideable widths included,
was measured against undeclared work that runs at a fixed rate whatever the verifier does. E1 measured the
operator that pauses while a challenge is in flight and resumes after. It is modelled as a flag the operator's
own harness writes, not as a detector, because an operator that owns the machine is told by its own scheduler
and does not have to infer anything; building a detector would have modelled a weaker adversary than the real
one.

## The files

`e1-summary.json` is the one to read first. It carries every headline number, the arithmetic that produced it,
the environment, the provenance and the caveats, in the pattern of `./undeclared-gpu-rerun/iteration-count.json`.

| file | what it is |
|---|---|
| `A1-sep-nonadaptive.json` | published grid, separate process, adversary that never reacts |
| `A2-sep-adaptive.json` | same grid, adversary that pauses on the challenge |
| `A1b-sep-nonadaptive-noreadywait.json` | control: original timing, no wait for the competitor to be ready |
| `A1c-sep-nonadaptive-unbounded.json` | control: original unbounded submission, which reproduces the published figures |
| `A2c-sep-adaptive-unbounded.json` | the adaptive adversary with an unbounded queue, which is caught |
| `A34-bound.json` | one null of twelve clean runs, then the duty response against **both** adversaries |
| `B1r-sep-adaptive.json` | verifier duty-cycle sweep, separate process |
| `B2r-inline-adaptive.json` | verifier duty-cycle sweep, same process and CUDA context |
| `B3-sep-nonadaptive.json` | **positive control:** the same protocol against an adversary that never reacts |
| `B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json` | the confounded first run, retained deliberately, see below |
| `C-sep-react{0,25,50,100,250,500}.json` | reaction-latency sweep, separate process |
| `C-inline-react{0,100,250}.json` | reaction-latency sweep, same context |
| `env.json` | device, driver, VBIOS, CUDA, PyTorch, kernel, confidential-compute state |

## The five results

**1. The signal disappears.** Separate process on the published grid: 11.4 / 11.9 / 13.5 percent drop against an
adversary that never reacts, and **0.12 / 0.23 / 0.10 percent** against one that pauses, none detectable. Same
context, against one shared null of twelve clean runs: the number of undeclared multiplications that executed
inside the challenge window was **zero, in all four pairs at all three levels**, and no tested level up to 30
percent of device capacity is distinguishable from noise. Randomising when the challenge arrives does not help,
because the operator reacts rather than predicts.

**2. What unpredictable timing actually buys.** Undeclared work kept, as a rate against the no-verifier ceiling,
is one minus the verifier's duty cycle: 0.941 / 0.884 / 0.718 / 0.464 at duty cycles of 5 / 10 / 25 / 50
percent. Inverted, **bounding undeclared work at 10 percent of a device requires challenging 90 percent of the
time and costs the company 90 percent of that device.** Against a reacting operator this is not a detector, it
is a throughput tax at the verifier's duty cycle.

**3. A reaction deadline, which is a real signal inside the negative.** Stopping is not instantaneous, and the
overlap at the transition is detectable and proportional. At 25 percent verifier duty with one second windows,
the in-window drop goes 0.00 / 1.43 / 2.51 / 4.94 / 11.99 / 23.54 percent at measured reactions of 7.7 / 37 / 55
/ 110 / 258 / 509 milliseconds, and everything past the first is caught at three sigma. That matches the
arithmetic, which is the full-contention drop times the overlap fraction. **The deadline is about 15
milliseconds on a one second window, about 1.5 percent of the window, and it scales with window length.**

**4. The evasion also needs the operator to bound its own submission queue.** With unbounded submission and half
a second between being told and the challenge starting, the queue took 39 to 61 milliseconds to drain at 16
percent of the device, 149 to 175 at 23 percent, and **1.38 to 1.46 seconds at 38 percent**. Where the drain
fits in the budget nothing is detected; where it overruns, the overrun executes inside the challenge window and
is caught at 5.4 percent against a spread of 0.3 percent.

**5. The positive control, which is what makes the negative mean anything.** The same windowed protocol catches
a non-adaptive adversary at **47.61 percent drop against a 0.13 percent null**, keeping 0.999 of its undeclared
work. The instrument detects what is there to detect.

## The confounded run, and why it is still here

`B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json` is the first verifier-duty run and its in-window comparison
is wrong. It reported the contended run as **7 percent faster** inside the challenge windows than the reference
run, at every duty cycle, against a null of 0.05 percent. The cause was ours: the drain interval before each
challenge was applied only in the run that had undeclared work in it, so the contended run's timed windows each
began after a short idle pause the reference never got, and the comparison measured the device's clock response
to that pause rather than contention.

It is retained because the offset was constant and in the direction that hides an adversary, which makes it
large enough to have manufactured exactly the negative result the experiment was looking for. Applying the
interval in both runs removed it completely, and `B1r-sep-adaptive.json` is the corrected run. The
undeclared-work-kept figures were not affected, and that is checkable against this file: 0.720 against 0.721 at
a 25 percent duty cycle and 0.468 against 0.469 at 50 percent.

## What this does not cover

The evasion works because the operator knows a challenge is in flight, and it knows because the challenge is a
seeded job arriving through a channel it controls. A challenge indistinguishable from ordinary production
traffic would not be dodgeable this way. That is untested here and is not claimed in either direction.

Nothing here covers an operator that shapes its undeclared work to imitate the declared profile rather than
simply pausing. Pausing is the simplest evasion and it is the one measured.

One device, one operator, one workload shape, dense bfloat16 matrix multiplication, default GPU time-slicing
with no multi-process service, clocks not locked. Absolute throughput will not reproduce, because it depends on
the device's thermal state and on what else the cloud is running; the shape and the direction of every effect
should. Every run is bounded by wall-clock time rather than by a count, so undeclared iteration counts differ
between runs and magnitudes are quotable where precise counts are not.

Undeclared-work counts here are **researcher-side measurements** taken on the operator's side of the boundary
the scheme is designed never to cross. A regulator would receive none of them. Only the integers needed to check
the published arithmetic were extracted, and the raw workload output was not retained.
