# E1 run plan: the adaptive adversary, yield on puzzle

Written 2026-09-03 before renting anything, and **updated the same day with what was actually run and what it
returned**, so it now works as a reproduce document rather than only a plan. Results:
`../EVIDENCE/e1-adaptive-adversary/`.


**What E1 measures.** Every occupancy number in this repository, the roughly 6 percent and roughly 19 percent
widths included, was measured against an adversary that never reacts to the verifier. E1 measures the one that
does: it pauses its hidden work while a challenge is in flight and resumes after. Two outcomes, both worth
having. Either the occupancy check reads clean, which is the honest demonstration that the instrument does not
survive a reactive operator, or the trade becomes statable: what the verifier's own duty cycle buys, and what
reaction speed the operator needs to get away with it.

**The adversary model, stated once.** The operator owns the machine, so its own orchestrator receives the
challenge and can pause the hidden kernels before running the puzzle. It does not have to detect anything. We
implement it that way, by a flag file the harness sets, rather than by building a detector, because assuming
the operator has to guess would understate the adversary. `--react-ms` then models the weaker operator that
does have to notice, and sweeping it is what turns the negative into a requirement.

---

## Parameters, matched to the published runs

`REPRODUCE.md` section 3 is `tier05_bound.py --null-runs 12 --pairs 4 --seconds 10`, section 5 is
`tier05_sweep.py --paired --seconds 10 --repeats 4 --duties 0.02,0.05,0.25`. Matrix size 8192, bfloat16,
one H100 NVL in an SEV-SNP CVM. Arms below reuse those exactly so the non-adaptive arm is a like-for-like
check that the rig still matches, and the adaptive arm differs in one variable only.

## Setup

```powershell
.\provision_gpu.ps1 # ~10 min, billing starts here
```
```bash
IP=$(cat gpu-vm-ip.txt)
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'python3 -m venv ~/venv && ~/venv/bin/pip install -q torch'
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'mkdir -p ~/t1/bin ~/e1'
scp -i ~/.ssh/ccverify_m1 contention.py tier05_sweep.py tier05_bound.py e1_verifier_duty.py \
 azureuser@$IP:~/t1/bin/
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP '~/venv/bin/python -c "import torch;print(torch.cuda.get_device_name(0))"'
```

## Part A: does the check still fire, on the published grid

| arm | command | expect |
|---|---|---|
| A1 non-adaptive, separate process | `tier05_sweep.py --python ~/venv/bin/python --paired --seconds 10 --repeats 4 --duties 0.02,0.05,0.25 --out ~/e1/A1-sep-nonadaptive.json` | **got 11.4 / 11.9 / 13.5 percent drop**, all detected. NOT the 36 / 43 / 49 this plan first expected, and the difference is the submission bound, see A1b and A1c |
| A2 adaptive, separate process | same plus `--yield-on-challenge --out ~/e1/A2-sep-adaptive.json` | **got 0.12 / 0.23 / 0.10 percent**, none detected. Measured reaction of the told-in-advance operator: 0.6 to 5.1 ms |
| A1b control, one variable: no wait for the competitor to be ready | A1 plus `--no-ready-wait --out ~/e1/A1b-sep-nonadaptive-noreadywait.json` | **got 11.2 / 11.7 / 13.6 percent**, the same answer, so competitor startup inside the window is not the cause |
| A1c control, one variable: original unbounded submission | A1b plus `--queue-depth 0 --out ~/e1/A1c-sep-nonadaptive-unbounded.json` | **got 34.9 / 44.0 / 49.7 percent, reproducing the published 35.8 / 43.4 / 48.8**, with the competitor measured taking 64-66 / 85-88 / essentially 100 percent of the device under labels of 2 / 5 / 25 percent |
| A2c adaptive against an unbounded queue | A2 plus `--queue-depth 0 --out ~/e1/A2c-sep-adaptive-unbounded.json` | **got -0.05 / 0.14 / 5.37 percent**, the last one detected: its queue took 1.38 to 1.46 s to drain against the 0.5 s it was given |
| A3+A4 both adversaries, same context, one shared null | `tier05_bound.py --null-runs 12 --pairs 4 --seconds 10 --duties 0.05,0.15,0.30 --modes nonadaptive,adaptive --out ~/e1/A34-bound.json` | **got 2.74 / 7.19 / 14.05 percent non-adaptive** (July: 2.78 / 7.22 / 14.11) and **-0.04 / -0.03 / +0.01 percent adaptive with zero hidden iterations in the window**, so no tested duty is distinguishable. Paired sigma 1.00 percent, hideable 5.6 percent |

About 21 minutes for A1, A2 and A3+A4, plus about 15 for the three controls. A3 and A4 share one null
distribution so the two bounds are directly comparable.

## Part B: the verifier's duty cycle, which is the new axis

```bash
# B1r separate process, adaptive
~/venv/bin/python ~/t1/bin/e1_verifier_duty.py --python ~/venv/bin/python \
 --window 60 --challenge-seconds 1.0 --settle 0.15 --duties 0,0.25,0.50 \
 --hidden-mode separate --adaptive --out ~/e1/B1r-sep-adaptive.json
# B2r same context, adaptive: the case that matters, because it is the quiet one
~/venv/bin/python ~/t1/bin/e1_verifier_duty.py --python ~/venv/bin/python \
 --window 60 --challenge-seconds 1.0 --settle 0.15 --duties 0,0.05,0.10,0.25,0.50 \
 --hidden-mode inline --adaptive --out ~/e1/B2r-inline-adaptive.json
# B3 POSITIVE CONTROL: same shape, adversary that never reacts. Run this one.
# Without it a null result says nothing, because nothing has shown the instrument works.
~/venv/bin/python ~/t1/bin/e1_verifier_duty.py --python ~/venv/bin/python \
 --window 60 --challenge-seconds 1.0 --duties 0,0.25 \
 --hidden-mode separate --out ~/e1/B3-sep-nonadaptive.json
```

Each duty point is a reference run and a contended run of the same 60 seconds with the same randomly placed
challenge windows, so the in-window null and the contended in-window throughput come from the same schedule.
Recorded per point: in-window drop against that null, out-of-window drop, and the hidden iterations actually
completed. The `d=0` point is the hidden-work ceiling, the denominator for how much the operator keeps.

**Got:** in-window drop inside the null everywhere under B1r and B2r (0.06 and -0.10 percent for the separate
process, 0.06 / 0.07 / 0.06 / -0.04 percent for the same context), and hidden work kept at **0.941 / 0.884 /
0.718 / 0.464 at duty 5 / 10 / 25 / 50 percent** against `1 - d` of 0.95 / 0.90 / 0.75 / 0.50. B3 caught at
**47.61 percent against a 0.13 percent null**, keeping 0.999 of its hidden work. About 26 minutes.

**One thing to get right if you re-run this, because it is easy to get wrong and it flatters the scheme.** The
drain interval set by `--settle` is applied in the reference run as well as the contended one. Applying it only
when hidden work is present gives the contended run an idle pause before every timed window that the reference
never gets, and the comparison then measures the device's clock response to that pause instead of contention.
Measured, that was a constant **7 percent in the adversary's favour against a null of 0.05 percent**, which is
large enough to manufacture the negative result. The uncorrected run is retained as
`../EVIDENCE/e1-adaptive-adversary/B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json` so the artefact is
visible.

## Part C: how fast does the operator have to be

```bash
# --settle 0 here on purpose: this is the operator that has to NOTICE the challenge,
# so it gets no interval between being told and the challenge starting.
for R in 0 25 50 100 250 500; do
 ~/venv/bin/python ~/t1/bin/e1_verifier_duty.py --python ~/venv/bin/python \
 --window 40 --challenge-seconds 1.0 --settle 0.0 --duties 0.25 --hidden-mode separate \
 --adaptive --react-ms $R --out ~/e1/C-sep-react$R.json
done
for R in 0 100 250; do
 ~/venv/bin/python ~/t1/bin/e1_verifier_duty.py --python ~/venv/bin/python \
 --window 40 --challenge-seconds 1.0 --settle 0.0 --duties 0.25 --hidden-mode inline \
 --adaptive --react-ms $R --out ~/e1/C-inline-react$R.json
done
```

Ten challenge windows of one second each per run. A reaction that takes R milliseconds leaves R of every
1000 milliseconds of challenge window overlapped at full contention, so the expected in-window drop is
roughly `(R / 1000) x (the full-contention drop)`.

**Got,** separate process, against a full-contention drop of 47.8 percent:

```
reaction asked for 0 25 50 100 250 500 ms
reaction measured 7.7 37.1 55.3 110.3 257.7 509.4 ms
in-window drop 0.00 1.43 2.51 4.94 11.99 23.54 %
three sigma 0.68 0.61 0.79 0.77 0.79 0.84 %
caught no YES YES YES YES YES
```

Same context, against a full-contention drop of 37.5 percent: 0.07 percent at 0 ms, 4.82 at 100, 9.35 at 250,
the last two caught. Fitted slope is 0.046 percent of throughput per millisecond of reaction, so the crossover
lands at **about 15 milliseconds on a one second window, about 1.5 percent of the window**, and it scales with
window length. Our own told-in-advance implementation measures 7.7 ms mean and 15.7 ms max, inside the deadline
by a factor of two with its worst case touching it. About 16 minutes.

## Teardown

```powershell
az group delete --name ccverify-gpu-rg --yes
```

## Cost and time

| | |
|---|---|
| SKU | `Standard_NCC40ads_H100_v5`, eastus2, quota confirmed 0 of 40 vCPUs used |
| meter, checked 2026-09-03 | $6.98/hr or $8.82/hr depending on meter |
| measurement | about 63 minutes |
| setup and teardown | about 25 minutes |
| VM lifetime | about 1.5 hours |
| **expected** | **$11 to $14** |
| **actual, run 2026-09-03** | **1.63 GPU-hours, $11.38 to $14.38** |

**What actually changed from this plan, and why.** Three arms were added and one was re-run.
`A1b` and `A1c` were added when the non-adaptive arm came in at a third of the published figure, to
pin the cause to one variable each: `A1b` removes the wait for the competitor to be ready, `A1c`
restores the original unbounded submission and reproduces the published numbers exactly. `A2c` was
added to ask whether the pause survives an unbounded queue, and it does not at high occupancy, which
is a result. `B1` was re-run as `B1r` after its first pass turned out to be confounded, and `B2` was
re-run as `B2r` with the fix before it had produced anything. Net effect on cost was about zero,
because the re-run replaced work that would have been wasted anyway.

Trimmed variant, if the number matters more than the coverage: drop B3, drop the three inline reaction
points, cut Part C to four points and B2 to three duties. About 45 minutes of measurement, VM about 1.2
hours, **$9 to $11**. The full plan is worth the extra because Part C is what converts a negative result
into a stated requirement on the adversary.

## Checking the results afterwards

`e1_audit.py` rechecks every headline figure straight from the retained run files, without going
through `e1_collect.py`, so a mistake in the collector cannot hide a mistake in the numbers:

```bash
python e1_audit.py
```
**Expect:** `ALL HEADLINE FIGURES CHECK OUT`.

## Retained evidence

`../EVIDENCE/e1-adaptive-adversary/`: every JSON above, a summary with the arithmetic written out longhand,
the environment block, provenance, and the caveats. Hidden-work iteration counts are researcher-side
measurements from the operator's side of the boundary and a regulator would receive none of them.

## E1b (2026-09-03): leak versus window count at fixed duty

Launch script `e1b_run.sh` (retained in the evidence
folder too). Same box, torch pinned 2.13.0, six pairs per arm, 25 percent duty over 60 s throughout, window
length 3.0 / 1.0 / 0.5 / 0.25 s giving 5 / 15 / 30 / 60 windows, plus the same-context arm at 0.25 s.
**Got:** 0.08 / 0.45 / 0.64 / 0.97 percent, the last three called, same-context -0.15. About 70 minutes of
measurement (the planned 25 was an underestimate: each pair is two 65 s runs plus competitor startup),
VM about 1.3 hours, $9 to $12. Analyse with `python e1v2_analyse.py --dir <dir>`, which now carries an E1b
section. Evidence `../EVIDENCE/e1b-leak-vs-windows/`, Two things to know: `provision_gpu.ps1` exits nonzero on an Azure stderr warning after the VM is
created (write the IP and start files by hand), and the first pair of a fresh session runs on a cold, boosted
device; run a throwaway pair first or discard it under the stated rule.
