# E1c: the same-context bound measured below 4 percent of a device, with the denominator bracketed in-session

2026-09-04 (UTC 04:40 to 04:53), one NVIDIA H100 NVL in an AMD SEV-SNP confidential VM, same image, driver
595.71.05, torch 2.13.0+cu130, CC ON. **About 0.45 GPU-hours, $3 to $4.** Protocol written before the run:
the project's own notes.
**Read `ANALYSIS.txt` first**; it is `e1c_analyse.py` run on the raw files and uses none of the harness's summary
fields except to cross-check them.

## What it was for

Two open items from the v2 analysis: the published 2.7 percent width was a line carried below the smallest
load measured (4.33 percent of a device), and the device-share denominator was unsettled between a run's
own short calibration and the warm sustained rate, which differ by about a tenth.

## What it found

**1. The response curve below 4 percent is linear, and measured.** Six pairs per point, 10 s runs:

```
knob share (in-run) drop drop/share
0.01 0.85% 0.92% 1.09
0.02 1.69% 1.80% 1.06
0.03 2.53% 2.72% 1.07
0.05 4.14% 4.46% 1.08
0.10 7.93% 8.42% 1.06
```

Drop is 1.06 to 1.09 times the in-run share from 0.85 percent up, against v2's 1.03 to 1.04 at 4.3 and 8.3
percent. No steepening at the bottom, unlike the separate-process case. So the shape behind the v2
extrapolation was right, and the width is simply the threshold divided by about 1.07 in in-run units.

**2. The denominator is settled in principle and its size is pinned.** Sustained over short calibration is
1.103 before the run and 1.093 after it, matching CAL-qd4's 1.105 from v2. Both calibrations synchronise every
four matmuls, so this is warm-up state, not launch pattern. Against warm sustained capacity every in-run share
is about 10 percent smaller and drop is 1.17 to 1.19 times share. Session drift in the sustained rate was
-6.45 percent over thirteen minutes.

**3. The width is threshold-limited, not curve-limited, and this session's own null is worse than v2's.** The
twelve clean runs fell from 412 to 384 TFLOP/s, most of it across the first five, because the 20 s full-tilt
calibration placed just before the null left the device in a cooling transient. The paired sigma is 1.45
percent on all runs and 0.57 dropping the first, so the run's own width reads 3.6 to 4.0 percent (all runs) or
1.4 to 1.6 percent (first run dropped), all interpolated between measured points. That swing is the null, not
the curve. **Do not replace the v2 null with this one.** Protocol lesson: never put a sustained burst
immediately before the null; the null must sit in the thermal state of the pairs it will judge.

**Putting it together against the v2 threshold of 2.81 percent of throughput:** width 2.81 / 1.07 = about 2.6
percent of a device in in-run units, 2.81 / 1.18 = about 2.4 percent against warm sustained capacity. The
published 2.7 survives as the conservative headline; the honest range is 2.4 to 2.7, now with no extrapolation
under it.

## Files

| file | what |
|---|---|
| `ANALYSIS.txt` | every figure from raw values, with the null cut three ways and shares against three denominators |
| `BOUND-lowload.json` | the bound run: 12 clean runs, per-pair undeclared iteration counts and shares, in-run calibration |
| `CAL-before.json`, `CAL-after.json` | 20 s sustained solo runs bracketing the bound, each with its own short calibration |
| `env.txt`, `run.log`, `e1c_run.sh`, `e1c_analyse.py` | environment, console output, the launch script, the analysis |
