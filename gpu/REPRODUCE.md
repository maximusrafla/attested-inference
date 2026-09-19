# Reproduce every headline result: one command each

The claim this file backs is measured feasibility: this was assembled and run today, cheaply, and a third party
can rerun it. It is NOT an institutional deployability claim (retired). That claim is worth
nothing if only we can run it, so every headline result below has a command, an expected output, a
cost and a runtime. Total to reproduce the whole set: **about 2.6 GPU-hours plus a few cents of CPU
box, roughly $20 to $25.**

Prerequisites: an Azure subscription with `StandardNCCads2023Family` quota (40 vCPUs, eastus2; the
portal item is "Standard NCCads2023 Family vCPUs", approval can take 48 hours), the Azure CLI logged
in, an SSH key at `~/.ssh/ccverify_m1`, and Python with `cryptography` and `pyjwt` on the verifier
machine. Nothing else. **Tear down when finished: `az group delete --name ccverify-gpu-rg --yes`.**

Two notes before anything below is trusted:
- **Numbers will not match to the digit.** Throughput depends on the device's thermal state and on
 what else Azure is running. What should reproduce is the SHAPE: the orders of magnitude, the
 direction of every effect, and every accept/reject verdict.
- **Verdicts are exact.** Every accept and every reject below is deterministic given the same
 declaration, and any deviation is a real disagreement worth chasing.

---

## 1. The attested boundary reaches the accelerator

Claim: three hardware roots behind two vendors, bound to one disclosure, verified off-machine.
(The vTPM is provided by the processor's sealed mode rather than soldered on separately, so the
roots are not three independent vendors.)

```powershell
.\provision_gpu.ps1 # ~10 min, then the meter is running
```
```bash
IP=$(cat gpu-vm-ip.txt)
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'mkdir -p ~/t1/bin'
scp -i ~/.ssh/ccverify_m1 *.py *.sh azureuser@$IP:~/t1/bin/
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'cd ~/t1/bin && CCV_GPU=1 CCV_VENV=$HOME/venv ./tier1_sequence.sh eastus2 300'
```
```bash
CCV_GPU=1 bash verify_all.sh $IP <evidence-dir> sevsnpvm # pulls every case and verifies each one
```
**Expect:** `23/23 checks passed` per case, with the expected verdict, and `attested accelerator: GH100 ...`.
Verify while the tokens are fresh: the platform token lasts eight hours and the GPU token one. On archived
bundles add `--allow-expired-tokens`, which checks the signatures but not the expiry.

The ceremony changed on 2026-09-14 and older bundles fail against the current verifier, correctly: it now
requires the quote to be signed by the vTPM's own attestation key (the one the platform token names as
`HCLAkPub`), the quote to cover PCRs 0 to 10 with the platform-attested boot registers and the regulator's
reference values, the log's `boot_aggregate` to equal sha256 over the quoted PCRs 0 to 9, and the verdict to
be recomputed off-box from the log, weights and configuration included. **About 25 min, $3 to $4.**

The forgery this replaced (no hardware needed):
```bash
python forge_quote_demo.py --bundle <a reject bundle> --approved <approved dir> --out /tmp/forged
python verify_completeness.py --disclosure /tmp/forged/disclosure.json --quote /tmp/forged/quote.msg \
 --signature /tmp/forged/quote.sig --ak-pub /tmp/forged/ak.pub.pem \
 --challenge $(cat /tmp/forged/challenge.txt) --ima-log /tmp/forged/ima.bin \
 --declaration <approved>/declaration.json --baseline <approved>/baseline.json --expect accept
```
**Expect:** the pre-2026-09-14 verifier passed this (14/14); the current one fails it on the key binding.

## 2. Completeness holds for executed files, and the five cases

Same `tier1_sequence.sh` run as above. **Expect,** in order (the 2026-09-14 ceremony runs the declared stack as a serving account whose reads are measured, so the data-import case now rejects; see the evidence README for the September table):

| case | verdict | undeclared entries |
|---|---|---|
| declared stack only | accept | 0 |
| undeclared code imported as data, non-root | **accept** (the limit) | 0 |
| declared weight file, one bit flipped | reject `payload_identity` | 0 |
| undeclared binary executed alongside | reject `undeclared_execution` | 1 |
| same import, run as root under the widened policy | reject | ~1000 |

Configuration is checked by the same harness:
```bash
ssh ... 'cd ~/t1/bin && ./tier1.sh run cfg --config-changed'
```
**Expect:** `reject`, `rejected_by ["configuration_identity"]`, with the code and weight digests
unchanged. Comment out the `--config` argument in `tier1.sh`'s `declare` phase and the same run
returns **accept**, which is the gap before the fix. **Included in the run above.**

## 3. The occupancy bound: what can hide under the noise

Claim: the detection threshold, stated against the null distribution of clean runs.

```bash
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP \
 '~/venv/bin/python ~/t1/bin/tier05_bound.py --null-runs 12 --pairs 4 --seconds 10 \
 --duties 0.01,0.02,0.03,0.05,0.08,0.15,0.20,0.25,0.30 --out ~/bound.json'
```
**Expect:** a clean-run null sigma of roughly 1% (paired) and 0.5 to 3% (against the mean, which is
how a stored ceiling behaves), a duty-response curve rising to about 22% at a duty knob of 0.30, and a
printed bound of roughly **2.7% of device capacity hideable** under a paired protocol. **~35 min, ~$4.**

**The published figure used to be roughly 6%, and that was the duty knob rather than a device share.**
The script now measures the undeclared work's own throughput, divides by a solo rate calibrated in the
same run, and reports the share. Pass `--modes nonadaptive,adaptive` to get both adversaries against
one null.

Three things to know about that number:
- **It is a device share now, not a duty knob.** Drop and share are the same quantity for same-context
 work to within 3% (knob 0.05 / 0.10 / 0.20 / 0.30 gives drops of 4.50 / 8.52 / 15.74 / 21.94% against
 shares of 4.33 / 8.27 / 15.29 / 21.21%), so the hideable width is just the detection threshold.
- **The interval is wide.** The paired null rests on six ratios, so its 95% interval is 0.59 to 2.30%
 and the width inherits that: roughly **1.7% to 6.7%**. Quote the interval, not the point alone.
- **It is a non-adaptive-adversary figure.** Add `--modes nonadaptive,adaptive` and the adaptive arm
 reports that no undeclared work executed inside the window at all, which is arithmetic rather than a
 detection result. The script now says so itself. See section 9.

## 4. Undeclared accelerator work passes the code measurement

```bash
ssh ... 'cd ~/t1/bin && CCV_GPU=1 ./tier1.sh run undeclared-gpu --undeclared-gpu'
ssh ... 'python3 -c "import json;print(json.load(open(\"/home/azureuser/t1/work/undeclared-gpu.out\"))[\"undeclared_gpu_iterations\"])"'
```
**Expect:** disclosure `accept`, `undeclared_entries: 0`, alongside roughly 20,000 undeclared GPU
iterations recorded in the workload's own output. **~3 min, included in the session above.**

**Retained result, so this claim does not depend on rerunning anything:**
`../EVIDENCE/undeclared-gpu-rerun/`. It holds the measured iteration count with the FLOP arithmetic
written out (`iteration-count.json`), and the disclosure from the same run with its quote, challenge,
attestation key, declaration and baseline, so the accept verdict is checkable too:

```
21,897 iterations x (2 x 4096^3) = 21,897 x 137,438,953,472 = 3,009,500,764,176,384 FLOP
 = 3.0095 PFLOP
```

The per-iteration constant is fixed by the source: `declared_stack.py` runs one 4096x4096 bfloat16
matmul per iteration, so only the integer is measured and the rest is arithmetic you can check.
The undeclared work is **time-bounded, not count-bounded**, so the iteration count will differ on
your run and is not meant to match; an earlier run measured 22,813 (3.1354 PFLOP). Both round to
about 3 PFLOP. Verify the paired disclosure with:

```powershell
$d=".\EVIDENCE\undeclared-gpu-rerun"; $ch=(Get-Content "$d\challenge.txt").Trim
python verify_completeness.py --disclosure "$d\disclosure.json" --quote "$d\quote.msg" `
 --signature "$d\quote.sig" --ak-pub "$d\ak.pub.pem" --challenge $ch `
 --declaration "$d\declaration.json" --baseline "$d\baseline.json"
```

Note what is deliberately absent: the raw workload output is not retained. The counter lives on the
operator's side of the boundary, which the disclosure discipline never transmits, so only the single
integer needed to check the arithmetic was extracted. That is a researcher-side measurement, not
something a regulator would receive.

## 5. Contention asymmetry: separate process versus same context

```bash
ssh ... '~/venv/bin/python ~/t1/bin/tier05_sweep.py --python ~/venv/bin/python --paired \
 --seconds 10 --repeats 4 --duties 0.02,0.05,0.25 --out ~/sep.json' # separate
ssh ... '~/venv/bin/python ~/t1/bin/tier05_sweep.py --python ~/venv/bin/python --paired --inline \
 --seconds 10 --repeats 4 --duties 0.02,0.05,0.25 --out ~/inl.json' # same context
```
**Expect:** separate process roughly 2 / 4 / 14 percent drop at shares of 2 / 5 / 25 percent; same
context roughly 2 / 3 / 13 percent. A separate process costs roughly what it takes across most of the
range, and about twice what it takes at the very bottom. **~15 min, ~$2.**

**These figures were corrected twice on 2026-09-03.** They read 36 / 43 / 49 percent until that day,
then briefly 11 / 12 / 14, and now read the values above. The `--duty` label never meant the share of
the device taken: matmul submission is asynchronous at about 10 microseconds against a matmul of about
2 ms, so a slice paced on wall time queued far more device work than it was long. The first fix bounded
submission but still paced on wall time, leaving a floor of four matmuls per slice, so the 2 and 5
percent labels were one experiment. **The script now paces on measured device time** and reports
`device_share_taken`, so the label is never the figure quoted. To reproduce the July numbers, run the
archived `contention_v1_ARCHIVED.py` in place of `contention.py` with `--queue-depth 0
--no-ready-wait`; that gave 35.5 / 44.3 / 49.8 percent on 2026-09-03 against the published 35.8 / 43.4
/ 48.8.

## 6. Operator-side cost of the widened policy

CPU only, no GPU needed.
```powershell
.\provision_dev.ps1
```
```bash
DIP=$(cat dev-vm-ip.txt)
scp -i ~/.ssh/ccverify_m1 policy_cost.py azureuser@$DIP:~/
ssh -i ~/.ssh/ccverify_m1 azureuser@$DIP 'sudo ~/policy_cost.py --label default --out ~/cost-default.json'
ssh -i ~/.ssh/ccverify_m1 azureuser@$DIP \
 'printf "measure func=BPRM_CHECK mask=MAY_EXEC\nmeasure func=MMAP_CHECK mask=MAY_EXEC\nmeasure func=FILE_CHECK mask=^MAY_READ euid=0\nmeasure func=MODULE_CHECK\n" | sudo tee /sys/kernel/security/ima/policy >/dev/null
 sudo ~/policy_cost.py --label widened --out ~/cost-widened.json'
```
**Expect:** cold reads roughly 30x slower (about 250 MB/s of hashing), cold execs roughly 3x, and
warm anything at 1.0x including interpreter startup. The policy is write-once per boot, so the
default measurement must come first. **~10 min, under $1.**

## 7. Adversarial probes against the completeness mechanism

```bash
ssh ... 'cd ~/t1/bin && ./tier1.sh run probe-base && python3 adversarial.py \
 --quote ~/t1/out/probe-base/quote.msg --out ~/adversarial.json'
```
**Expect:** caught, NOT caught, caught, caught, caught, in that order (trim log, anonymous-memory
JIT, memfd exec, post-approval edit, other user). **~5 min, under $1 on the CPU box.**

## 8. The reviewer matrix

```bash
ssh ... '~/venv/bin/python ~/t1/bin/tier2_matrix.py --fixtures ~/fixtures_stripped \
 --samples 5 --out ~/t2-matrix.json'
```
Strip the fixture comments first (the one-liner is in the RUN-LOG second-pass section); leaving them
in is what produced the original contaminated result. **Expect:** about 22 tokens emitted regardless
of a 96 or 768 budget, greedy verdicts flipping on prompt content and not on budget, sampled reject
rates of 0.2 to 0.4 on attack A, and 0.0 on the clean fixture in every cell. **~10 min, ~$1.**


## 9. The occupancy check against an operator that reacts

Claim: the occupancy check does not survive an operator that pauses its undeclared work while a
challenge is in flight, and what unpredictable challenge timing buys instead is a throughput tax
equal to the verifier's duty cycle.

```bash
P=~/venv/bin/python
# does the check still fire, on the published grid, against each adversary
ssh ... "$P ~/t1/bin/tier05_sweep.py --python $P --paired --seconds 10 --repeats 4 \
 --duties 0.02,0.05,0.25 --out ~/A1.json" # never reacts
ssh ... "$P ~/t1/bin/tier05_sweep.py --python $P --paired --seconds 10 --repeats 4 \
 --duties 0.02,0.05,0.25 --yield-on-challenge --out ~/A2.json" # pauses on the challenge
# the same-context bound against both adversaries, sharing one null
ssh ... "$P ~/t1/bin/tier05_bound.py --null-runs 12 --pairs 4 --seconds 10 \
 --duties 0.05,0.15,0.30 --modes nonadaptive,adaptive --out ~/A34.json"
# the verifier's duty cycle, which is the axis the bound lives on
ssh ... "$P ~/t1/bin/e1_verifier_duty.py --python $P --window 60 --challenge-seconds 1.0 \
 --settle 0.15 --duties 0,0.05,0.10,0.25,0.50 --hidden-mode inline --adaptive \
 --out ~/B2.json"
# how fast the operator has to be
ssh ... "$P ~/t1/bin/e1_verifier_duty.py --python $P --window 40 --challenge-seconds 1.0 \
 --settle 0.0 --duties 0.25 --hidden-mode separate --adaptive --react-ms 100 \
 --out ~/C100.json"
```

**Expect:** the adaptive duty response flat, with **zero** undeclared matmuls executing inside the
challenge window; undeclared work kept at about 0.90 / 0.74 / 0.51 at verifier duty cycles of 10 / 25 /
50 percent, which is one minus the duty cycle; and, in the reaction sweep, no detection at a measured
9.6 ms and detection at 15.2 ms. **Quote that bracket rather than a fitted deadline:** an earlier pass
fitted 15 ms through the origin and its own zero-reaction point contradicted the fit by a factor of
250. The deadline scales with the challenge window, inversely with the undeclared work's share of the
device, and with the verifier's baseline quality.

Run the positive controls in the same session or the null results mean nothing. A separate process at a
**4 percent share gives a 4.5 percent drop and is called**; at 49 percent it gives 54 percent.

The positive control matters here and is worth running: drop `--adaptive` from the duty-cycle command
and the same protocol catches the non-adaptive adversary at about 48 percent drop against a 0.13
percent null. Without that, a null result says nothing.

**About 100 min, ~$16** for the full v2 set including replicates and both positive controls. Retained
results in `../EVIDENCE/e1v2-adaptive-adversary/` with `ANALYSIS.txt`. The v1 run is kept at
`../EVIDENCE/e1-adaptive-adversary/` and several of its numbers are wrong; read it only alongside the v2
README, which says which ones.

---

## What is NOT reproducible from this repository

Stated so nobody assumes otherwise:
- **The exact throughput numbers**, for the thermal reasons above.
- **The Azure security-agent behaviour**, which depends on the image Azure ships on the
 day and may change without notice.
- **The NRAS attestation path**, if NVIDIA retires the Python SDK as scheduled on 2026-09-15. The
 successor is the `nvattest` CLI / NVAT C++ SDK, and the disclosure format does not change.
- **Anything about an adversary,** in sections 1 to 8. Those commands all run a cooperative operator
 executing our own scripts. Section 9 is the exception and it is narrow: one specific evasion
 against one specific check.
