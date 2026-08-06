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

Claim: three independent hardware roots, bound to one disclosure, verified off-machine.

```powershell
.\provision_gpu.ps1                                        # ~10 min, then the meter is running
```
```bash
IP=$(cat gpu-vm-ip.txt)
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'mkdir -p ~/t1/bin'
scp -i ~/.ssh/ccverify_m1 *.py *.sh azureuser@$IP:~/t1/bin/
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'cd ~/t1/bin && ./gpu_tier1_sequence.sh 300'
```
```powershell
$d="gpuout\collect\tier1\accept"; $ch=(Get-Content "$d\challenge.txt").Trim()
python verify_completeness.py --disclosure "$d\disclosure.json" --quote "$d\quote.msg" `
  --signature "$d\quote.sig" --ak-pub "$d\ak.pub.pem" --challenge $ch `
  --declaration gpuout\collect\approved\declaration.json `
  --baseline gpuout\collect\approved\baseline.json `
  --maa-token "$d\maa-token.jwt" --nras-token "$d\nras-token.json" --cc-mode "$d\cc-mode.txt"
```
**Expect:** `20/20 checks passed`, and `attested accelerator: GH100 ...`. The MAA and NRAS
signatures are checked against Microsoft's and NVIDIA's live JWKS, so this cannot be faked by
whoever hands you the artifacts. **~25 min, ~$3.**

## 2. Completeness holds for executed files, and the five cases

Same `gpu_tier1_sequence.sh` run as above. **Expect,** in order:

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
**Expect:** a clean-run null sigma of roughly 1% (paired) and 3% (against the mean, which is how a
stored ceiling behaves), a duty-response curve rising from about 0.9% drop at 1% duty to about 14%
at 30%, and a printed bound of roughly **6% of device capacity hideable** under a paired protocol
and roughly **19%** against a stored ceiling. **~35 min, ~$4.**

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
$d="..\EVIDENCE\undeclared-gpu-rerun"; $ch=(Get-Content "$d\challenge.txt").Trim()
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
         --seconds 10 --repeats 4 --duties 0.02,0.05,0.25 --out ~/sep.json'          # separate
ssh ... '~/venv/bin/python ~/t1/bin/tier05_sweep.py --python ~/venv/bin/python --paired --inline \
         --seconds 10 --repeats 4 --duties 0.02,0.05,0.25 --out ~/inl.json'          # same context
```
**Expect:** separate process roughly 36 / 43 / 49 percent drop; same context roughly 2 / 3 / 13
percent. The order-of-magnitude gap at low duty is the result. **~15 min, ~$2.**

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

---

## What is NOT reproducible from this repository

Stated so nobody assumes otherwise:
- **The exact throughput numbers**, for the thermal reasons above.
- **The Azure security-agent behaviour**, which depends on the image Azure ships on the
  day and may change without notice.
- **The NRAS attestation path**, if NVIDIA retires the Python SDK as scheduled on 2026-09-15. The
  successor is the `nvattest` CLI / NVAT C++ SDK, and the disclosure format does not change.
- **Anything about an adversary.** Every command above runs a cooperative operator executing our own
  scripts. None of it evaluates the scheme against someone trying to defeat it.
