# E1 v2: the occupancy check, measured after an internal review

One session on 2026-09-03, one NVIDIA H100 NVL inside an AMD SEV-SNP confidential virtual machine.
**1.82 GPU-hours, $12.70 to $16.04.** The protocol changes were written before the rerun.

**`ANALYSIS.txt` is the file to read first.** It is the output of `../../gpu/e1v2_analyse.py`, which
recomputes every figure from the raw per-window and per-repeat values rather than trusting any summary field
written by the harness.

The v1 run is kept at `../e1-adaptive-adversary/`. Several of its numbers are wrong; its own README is
stamped with which ones and why. Read it only alongside that stamp.

## The headline, which is not about the adaptive adversary at all

The project has been reporting the occupancy check's hideable width in units of the sweep's own duty knob
while labelling it "percent of device capacity". Measured against a solo calibration retained with the run:

```
duty knob throughput drop device share actually taken
0.05 4.50% 4.33%
0.10 8.52% 8.27%
0.15 12.27% 11.93%
0.20 15.74% 15.29%
0.30 21.94% 21.21%
```

Drop and device share are the same quantity to within three percent, so the hideable width is the detection
threshold itself: **2.71 percent of a device** with a baseline next to the challenge, **1.82 percent** against
a stored ceiling. The published figures of roughly 6 and roughly 19 percent were the knob, not the share. The
95 percent interval on the first is roughly 1.7 to 6.7 percent, because the null rests on six paired ratios.

## The files

| file | what it is |
|---|---|
| `ANALYSIS.txt` | every figure, recomputed from raw values, with the decision rules side by side |
| `CAL-qd4.json`, `CAL-qd0.json` | retained solo calibrations. The short in-run calibration reads about 10 percent low against the sustained rate; the sustained rate is the denominator to use |
| `A1-sep-nonadaptive.json` | the corrected duty axis, separate process, five distinct levels |
| `A2-sep-unbounded.json` | **not a valid July reproduction.** With no ready-wait the in-run calibration lands inside the measured window at full tilt; kept because it quantifies that contamination |
| `A2b-july-protocol-v1pacing.json` | the valid reproduction, using the archived v1 pacing: 35.5 / 44.3 / 49.8 against the published 35.8 / 43.4 / 48.8 |
| `A3-dormant-context.json` | the cost of an undeclared context present but submitting nothing: 0.31 percent |
| `BOUND-nonadaptive.json` | the width, in device share, against a twelve-run null |
| `BOUND-adaptive.json` | the adaptive arm, which correctly reports that nothing undeclared executed and that this is arithmetic |
| `B1-sep-adaptive.json`, `B2-inline-adaptive.json` | verifier duty sweep, three repeats per point, no drain gap |
| `B3-poscontrol-large.json` | positive control at 49 percent of a device: 54.24 percent drop, caught |
| `B4-poscontrol-small.json` | **positive control near the threshold, which v1 never ran:** 3.97 percent of a device gives a 4.47 percent drop and is caught |
| `C1-sep-react*.json` | reaction sweep with the 5 to 30 ms bracket filled in, two repeats |
| `C2-inline-react*.json` | same context; its reaction axis is uninstrumented and coarse |
| `hidden-*.json`, `*.hidden.*.json` | per-run counters from the undeclared process, including its own calibration and achieved duty |
| `env.json` | device, driver, VBIOS, CUDA, PyTorch, kernel, confidential-compute state |

## The results in one page

**The adaptive adversary is still not caught, and now that is a measurement.** Over thirty second windows with
one second challenges at random offsets, three repeats:

```
 separate process same context
verifier duty drop 3*SE caught drop 3*SE caught kept 1-d
10% 0.21% 0.46% no 0.09% 0.20% no 0.90 0.90
25% 0.29% 0.35% no 0.07% 0.10% no 0.74 0.75
50% 0.48% 0.46% YES 0.03% 0.09% no 0.51 0.50
```

**The new positive: a separate process leaks at every pause.** Its in-window drop rises with the number of
challenge windows and becomes marginally detectable at fifteen of them. Same-context work has nothing to
schedule in and out and does not leak. The call at 50 percent is 0.48 against 0.46 on three repeats and should
be described as suggestive rather than established.

**The reaction deadline is bracketed rather than fitted:** not caught at a measured 9.6 milliseconds, caught
at 15.2 milliseconds, for one second windows with the undeclared work at about a third of the device. It
scales with the window length, inversely with the undeclared work's share, and with the baseline quality.

**Undeclared work kept is one minus the verifier's duty cycle** to within a percentage point. That follows
from the adversary model rather than the device; what the measurement adds is that there is no restart
penalty.

## What is not covered

The challenge is the declared chain itself, timed, and the operator learns of it from a flag the harness sets.
So this measures an operator that is **told**. Whether a real challenge is distinguishable is untested, and no
puzzle displacing production work was modelled, so the cost side of the trade is not addressed.

The out-of-window column is retained because it bounds what a covert challenge would be worth: in every
adaptive run the declared chain runs 49 to 54 percent below reference for the whole time the verifier is not
challenging.

One device, one operator, one workload shape, dense bfloat16 matrix multiplication, default time-slicing, no
multi-process service, clocks not locked. Undeclared-work counts are researcher-side measurements from the
operator's side of the boundary; a regulator would receive none of them.
