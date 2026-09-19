# E1b: does the separate-process leak scale with the number of challenge windows?

One session on 2026-09-03 (UTC 2026-09-04 01:20 to 02:29), one NVIDIA H100 NVL inside an AMD SEV-SNP
confidential virtual machine, same image, driver (595.71.05), torch (2.13.0+cu130) and CC state (ON) as the
v2 run. **About 1.3 GPU-hours, $9 to $12.** The protocol was fixed before the run and followed as written, with six repeats and a five second warmup;
the launch script `e1b_run.sh` is retained here.

**`ANALYSIS.txt` is the file to read first.** It is the output of `../../gpu/e1v2_analyse.py`, whose E1b
section recomputes every figure from the raw per-window and per-repeat values.

## The question

The v2 run (`../e1v2-adaptive-adversary/`) found that a separate-process adaptive operator, one that pauses its undeclared work when a
challenge arrives, still leaks a little at every pause, and that at fifteen windows the leak crossed the
detection threshold, marginally: 0.48 percent against 0.46 on three repeats. The prediction that would
establish or kill it: at a FIXED verifier duty cycle, the leak should rise with the number of challenge
windows if it is a per-transition cost, and stay flat if it was noise.

## The answer: the leak is real, and it is paid per transition

Same total challenge time in every arm (25 percent of a 60 second run, fifteen seconds), six independent
reference/contended pairs per arm, no drain gap, decision rule on the across-repeat spread:

```
windows window in-window drop 3*SE called lost challenge time per window
 5 3.00 s 0.08% 0.19% no 2.3 ms
 15 1.00 s 0.45%* 0.19% YES 4.6 ms
 30 0.50 s 0.64% 0.20% YES 3.3 ms
 60 0.25 s 0.97% 0.21% YES 2.5 ms
same-context comparison, 60 windows of 0.25 s: -0.15% 0.10% no
```

`*` five repeats: the first pair of the session is excluded under the rule in `ANALYSIS.txt` (its reference
ran at 457 TFLOP/s against 434 for every other reference in the session, a cold, boosted device). With it
included the arm reads 1.06 percent with a spread of 1.50 and is not called, and the means are not monotone. The
detections at 30 and 60 windows, the per-arm medians (0.09 / 0.41 / 0.66 / 0.93, monotone, slope +0.0145 per
window) and the three arms with no flagged repeat (slope +0.0160) do not depend on the exclusion; the fitted
slope on means does. `ANALYSIS.txt` prints all four fits.

The log-log exponent of drop against window count is 1.02 with the flagged pair removed, 0.96 with it in:
proportional to the number of windows. The same fifteen-window count at HALF the v2 duty cycle gives the same
leak (0.45 here at 25 percent, 0.48 in v2 at 50 percent). Verifier duty does not set the leak; the count of
transitions does.

## The mechanism, from the competitor's own counters

The competitor records its flag-to-synchronised latency at every yield: mean 8 to 10 ms, occasional maxima
of 20 to 70 ms. That latency is the poll interval plus the in-flight submissions draining (four matmuls of
about 2.6 ms at queue depth 4). During that overlap the declared chain runs at about half speed (the
out-of-window drop is 51 percent). Half of 8 to 10 ms is 4 to 5 ms; the measured lost time per window is
2.3 to 4.6 ms. So the leak is the reaction overlap at the start of each window, and it is roughly constant
per transition across a twelve-fold range of window counts. The alternative hypothesis of a separate
context-switch cost is not needed to explain the data and was not separately tested.

Same-context work reacts at zero cost by construction (the pause is a branch in the same loop) and shows no
leak; its small negative reading (-0.15 percent, beyond its own 3*SE) is a systematic bias of the paired
null of order 0.1 to 0.2 percent, which bounds how small a leak can ever be claimed with this rig.

## What it means

Stated as the verifier's lever: for a told-in-advance separate-process operator with effective reaction
overlap L, the in-window leak fraction is about L over the window length W, independent of duty. At W = 0.25
s the leak was about 1 percent against a threshold of 0.2 percent; an operator that wanted to hide under it
would need L below roughly 0.5 ms, and its own queue drain alone is several milliseconds here. This is the
constant-duty form of the reaction bracket in the v2 results. It does NOT change the same-context conclusion: work
inside the declared process's own context is still invisible to the occupancy check.

## Files

| file | what it is |
|---|---|
| `ANALYSIS.txt` | every figure recomputed from raw values, both with and without the flagged pair |
| `E1b-sep-win{3.0,1.0,0.5,0.25}.json` | separate-process arms, six pairs each, full per-window data |
| `E1b-inline-win0.25.json` | the same-context comparison at the most leak-favourable point |
| `hidden-*.json` | the competitor's own counters per contended run: calibration, iterations, paused seconds, every reaction latency |
| `env.txt`, `env-extra.txt` | device, driver, VBIOS, torch, kernel, CC state at run time |
| `run.log` | the harness's own console output for the whole session |
| `e1b_run.sh` | the launch script, run as-is on the box |

Undeclared iteration counts and reaction latencies are researcher-side measurements from the operator's
side of the boundary; a regulator receives none of them.
