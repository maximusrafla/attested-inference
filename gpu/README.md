# GPU phase COMPLETE: the scheme reaching the accelerator, on a confidential H100 (2026-07-30)

The CPU build proved the wrapper. This closes the accelerator gap: one real NVIDIA H100 confidential-computing
attestation, bound under a single nonce to the CPU's SEV-SNP report and to a vTPM quote over the register that
records every file executed in the domain, all verified from a separate machine.

Substrate: `Standard_NCC40ads_H100_v5`, eastus2, community-gallery VMI `cgpu-NCC-2204-base-image`. One H100 NVL
(94 GB) inside an AMD SEV-SNP CVM. Driver 595.71.05, VBIOS 96.00.9F.00.04, CUDA 13.2, torch 2.13.0+cu130.
**1.36 GPU-hours, roughly $9.50 to $12 against a $150 cap.** Full command output: `../EVIDENCE/RUN-LOG.md`.

## Results

| Tier | Result |
|---|---|
| **0** accelerator binding | Three roots (AMD SEV-SNP via MAA, vTPM AK quote, NVIDIA NRAS ES384) bound to one disclosure hash. **20/20 off-machine checks**, on both an accept case and a reject case. **Partly perishable: ~13 of the 20 still verify offline today, the MAA and NRAS checks have expired (8h and 1h windows, key rotated). see the perishability note in `../EVIDENCE/RUN-LOG.md`** |
| **0.5** occupancy | 84.7% of the device's measured ceiling sustained across a 93.6 s verifier-bracketed interval on a nonce-seeded dependent chain. **Second pass measured what this actually detects, see below: a strong presence detector for a second CUDA context, a weak meter of undeclared work inside the declared process** |
| **1** completeness (executed files, under the policy in force) | 5 cases on one boot: declared-only accepts; a flipped bit in the declared payload rejects; an executed undeclared binary rejects on 1 measured entry; **undeclared code imported as data is NOT caught** |
| **2** reviewer | Qwen2.5-Coder-7B in the CC GPU over the M3 fixtures, bounded verdict through the disclosure wrapper. Missed attack A once the fixtures' explanatory comments were stripped. **Second pass separated the variables: the token budget does nothing, naming the pattern moves the greedy verdict, and rejections per violation are 6 of 40 (15%, CI 7-29%) with 0 of 20 false alarms** |
| **3a** tamper catch | The tampered row: identical verification, reject on the same chain, accept and reject side by side |
| appendix | Bit-exact re-execution floor at the canonical shape, 100% catch across the tamper ladder, 30,000-record audit batch projected at 0.35 H100-hours |

## Tier 1 in full, because it is the tier the reshape put at the centre

Baseline 2214 entries after a 300 s settle; declaration 53 digests from one approved reference run with real
GPU work. All five cases replayed against their quote exactly.

| case | verdict | undeclared | payload matches | rejected by |
|---|---|---|---|---|
| declared stack only | **accept** | 0 | yes | - |
| undeclared code imported as data, non-root | **accept** | 0 | yes | - |
| declared weight file, one bit flipped | **reject** | 0 | **no** | payload_identity |
| undeclared binary executed alongside | **reject** | **1** | yes | undeclared_execution |
| same import, run as root under tcb | **reject** | **994** (one run; 976 on a second boot, so "roughly 1000") | yes | undeclared_execution |

The chain, each link cited and none of it ours: the SEV-SNP report roots the CVM, measured boot roots the
kernel and the IMA policy, Linux IMA extends vTPM PCR 10 with the digest of every executed file, a quote over
PCR 10 answering a fresh regulator challenge makes that set non-repudiable, and Tier 0's GPU attestation
carries the same nonce to the accelerator. IMA plus TPM as load-time integrity is a twenty-year lineage
(Sailer et al., USENIX Security 2004, and measured boot); the composition into a regulator-facing disclosure
is what is demonstrated here.

## What this is and is not

**IS:** a running, end-to-end demonstration that the attested boundary can be made to span the accelerator, and
that attested code can tell a regulator **no undeclared executable was executed in the attested domain under the
measurement policy in force**, while the measurement log, the paths, the weights and the outputs stay on the
machine. Concreteness, not a new mechanism.

**The claim is deliberately not "nothing undeclared ran."** That phrasing is too strong and does not survive the
build. Data-driven execution through a declared interpreter stays uncovered unless the policy widens, and both
halves of that were measured, not assumed.

**IS NOT:**
- A runtime enumeration of GPU kernels. IMA measures executed FILES. A measured, declared process can still
  submit work to the accelerator that nothing here itemises: demonstrated, roughly 22,800 undeclared GPU
  iterations through an accept verdict (the accept verdict is retained as an artifact; the iteration count is
  transcript-sourced, so treat the magnitude as approximate). Tier 0 binds the accelerator's identity and mode, not what was sent to it, and
  Tier 0.5 only bounds it once it reaches a few percent of the device.
- Wider than the IMA policy. Undeclared code loaded as data by the declared interpreter was not caught, and the
  policy that would catch it costs roughly a thousand measured entries instead of one.
- Adversarial robustness. One GPU, same hardware, small models, three review fixtures, a load-time
  completeness measurement, and a cooperative operator running our own scripts.
- A capability measurement of AI code review. Three fixtures and one 7B model.

## The framing that holds all of this together

**On-chip attestation answers identity questions, not quantity questions.** It can establish what code
ran, what configuration was in force, and which weights were loaded, and it does all three well. It
cannot establish how much compute ran. Compute governance is built on quantity: thresholds are written
in FLOP, and a rule about how much compute a model was trained or served with cannot be enforced by a
mechanism that only answers what.

That is why the blind spot below is structural rather than a defect to be patched. It is the seam
between what attestation is for and what governance asks of it, and it is why the handoff to
sovereign instruments is load-bearing rather than decorative.

It is also why the occupancy check fails in the same way a power meter does. Both infer a quantity
from an aggregate signal, and both need a trusted efficiency baseline the operator controls: the power
meter needs joules per operation, the occupancy check needs the device's uncontended throughput. An
operator who can shade that baseline shades the inference built on it. So the occupancy check is not
a second, independent kind of evidence sitting alongside attestation; it is the same kind of evidence
as the external instruments, and it inherits their weakness.

## Second and third passes (2026-07-31): the tiers tested against each other, then bounded

Run on Maxim's instruction to spend budget improving rather than only reporting. Second pass: one cheap Trusted
Launch box (under $1) plus 0.82 more GPU-hours (about $5.70 to $7.20). Third pass, same day:
0.39 more GPU-hours plus a cheap box, about $3.50. **Cumulative build spend across all three
passes: 2.57 GPU-hours, roughly $19 to $23.** Full detail in `../EVIDENCE/RUN-LOG.md`.

**The main result is negative and it is the most load-bearing thing in the build.** Tier 0.5 originally asserted
its own mechanism. Measured with a paired protocol (clean and contended back to back, so thermal drift cancels;
an unpaired bookend showed 4.4% drift, which had made a naive first attempt far too pessimistic):

| undeclared work at duty | as a SEPARATE process | in the SAME process and CUDA context |
|---|---|---|
| 2% of the device | **35.8% drop** | 1.9% drop (below the floor) |
| 5% | **43.4% drop** | 3.3% drop |
| 25% | **48.8% drop** | 12.7% drop |

A second CUDA context is loud because the cost is context switching, not the stolen arithmetic. Same-context
work is quiet and proportional. **And same-context is exactly what Tier 1 cannot see:** the declared process ran
roughly **22,800 undeclared GPU iterations, about 3.1 PFLOP**, and the disclosure came back **accept, 0
undeclared entries**. The verdict is retained in `gpuout2/tier1/undeclared-gpu/disclosure.json`; the iteration
count is not, so the magnitude is approximate (2026-08-03 validity audit).

| undeclared work | Tier 1 (executed files) | Tier 0.5 (occupancy) |
|---|---|---|
| separate undeclared process | **caught** | **caught loudly** (36-49% drop) |
| kernels from the declared process | **missed entirely** | a real but small signal, and detecting it needs a throughput baseline the operator controls |

**Stated carefully, because the loose version is wrong:** the same-context case is not caught by
neither check. Tier 1 misses it outright, but the occupancy signal is real (12.7% at 25% duty is well
clear of noise). What is true is that turning that signal into a verdict requires a trusted
uncontended baseline for the device, and the operator controls the machine that baseline is measured
on. So the checks do not compose the way M3's review and attestation did, and the residual below the
threshold is what the handoff map is for, with power metering as the instrument.

### The bound, so the handoff knows what it must cover

An observation ("1.9% at 2% duty is small") is not a claim until it is compared against how much a
clean run varies on its own. Null distribution from 12 repeated clean runs, nothing undeclared
running: **paired sigma 1.16%** (n=6 consecutive pairs), **unpaired sigma 3.13%** (n=12, against the
mean, which is how a stored ceiling behaves). Duty response measured from 1% to 30%, with the two
independent runs agreeing at their overlap (7.18% and 7.25% at duty 0.15).

With the verifier's detection threshold set at 3 sigma of clean-run variation:

| verifier protocol | threshold | undeclared work indistinguishable from noise |
|---|---|---|
| clean baseline taken adjacent to the challenge (best case) | 3.47% drop | **up to ~6% of device capacity** |
| compared against a stored ceiling (deployable) | 9.39% drop | **up to ~19% of device capacity** |

The paired figure is a floor on what this instrument could ever do, and it is not free: it assumes the
verifier can obtain an uncontended baseline on demand, which an operator choosing when to cheat can
deny. **So roughly 6% to 19% of a device is what the handoff has to cover**, and that is the on-chip
analogue of an identified-set width. Sigma estimates from n=12 carry perhaps 20% relative uncertainty,
so treat these as one significant figure.

**Configuration: a correctness fix to our mechanism, not a discovery.** Measuring configuration alongside code
is long-standing practice in measured-launch systems, and our declaration simply did not do it, which was a bug
on our side. Recording it because the demonstration is useful and the price is worth knowing: with config
undeclared, switching `safety_filter` off, swapping the system prompt and raising temperature to 1.4, with code
and weights untouched, produced **accept** with every attested value identical. Declaring the config digest turns
the same run into **reject, `configuration_identity`**. Cost of the fix: **one hash**, against 994 measured
entries for the IMA-policy widening. What makes it worth a sentence in the paper is not novelty but that this is
exactly where SB 53's promise-versus-practice hook lands, so a scheme aimed at that statute cannot omit it.

**What the widened policy costs the operator, in time.** 994 log entries is a verifier-side burden; the operator
will ask what it costs them. Measured on the same workloads under the default policy and the widened one:

| workload | default | widened | ratio |
|---|---|---|---|
| 200 unique binaries executed (cold) | 100.9 ms | 301.8 ms | 3.0x |
| same binaries again (warm) | 96.1 ms | 98.3 ms | 1.0x |
| 419 MB read as root (cold) | 54.7 ms | 1726.1 ms | **31.6x** |
| same bytes again (warm) | 54.6 ms | 52.9 ms | 1.0x |
| interpreter startup | 13.3 ms | 13.1 ms | 1.0x |

The shape matters more than the worst number. IMA hashes a file once per boot and caches it, so the price is
paid on **first touch, proportional to bytes**, at about **250 MB/s** as measured on this 2-vCPU box, and the
steady state is free: warm execs, warm reads and interpreter startup are all unchanged. For a serving operator
that means a one-time cost at model load (a 100 GB checkpoint would cost roughly 7 minutes of hashing on this
hardware, once) and no ongoing tax on serving. The pathological case is a workload that continuously streams
new large files, which pays the 31.6x. Measured on one small VM; the MB/s figure will move with the CPU.

**Adversarial pass, 4 of 5 probes caught.** Log trimming, post-approval edits, other-user execution and even
`memfd_create` fileless execution are all caught (IMA hooks exec, not the filesystem). The one miss is a
declared program JITing code into anonymous memory, which generalises the Python-import result: file-backed
measurement cannot see execution that is not file-backed.

**Two detector designs were discarded before that table was trustworthy**, and both had produced confident wrong
answers. "Did PCR 10 move" is useless on a live cloud guest. "Is the digest new since the probe started" gave a
false negative on the post-approval edit and would have supported a false and serious claim.

## Findings worth carrying into the paper

1. **The shipped attestation path is not third-party verifiable.** The VMI's `gpu-attestation` wrapper emits an
   EAT signed HS256 and issued by `LOCAL_GPU_VERIFIER`. Neither `nvattest` nor the SDK ships on the image. Real
   off-machine verification needed the remote NRAS path via the deprecated Python SDK (EOL 2026-09-15).
2. **Clock locking works in CC-On and stays externally queryable** (`-lgc 1200,1200` accepted;
   `clocks.current.graphics` reads 1200 against a 1785 max), while `clocks.applications.graphics` is
   deprecated. This is the measurement the Kimpson power paper's clock rung was waiting on.
3. **Profiling counters refuse with a named error**, `CUPTI_ERROR_CONFIDENTIAL_COMPUTING_NOT_SUPPORTED (41)`,
   while NVML card-health telemetry still returns numbers on its separate path. Two probes, reported apart.
4. **PCR 10 never holds still on a live cloud guest**, because the provider's own in-guest security agent keeps
   reading files that keep changing. A read-then-quote ceremony cannot be atomic; derive the attested value
   from the quote's pcrDigest instead.
5. **The operator does not control the declared set.** The provider's in-guest agents must sit inside the
   approved platform baseline, so a regulator checking a cloud-hosted operator inherits them.
6. **The IMA policy itself is not attested here.** This image has no `/etc/default/grub` (snapd FDE boot chain),
   so the runtime write was used. It is write-once per boot, so it cannot be loosened afterwards, but the first
   load is outside the measured boot chain.
7. **The AI reviewer's catch was annotation-driven** until the fixtures were stripped, then prompt-driven, and
   under sampling it rejected only 6 of 40 violation generations (15%, 95% CI 7 to 29%) with 0 of 20 false
   alarms. Worth stating plainly against any claim that an LLM reviewer in a TEE is a fixed capability.
   **Polarity: a positive is a red flag**, so these are rejections per violation, which is recall.
8. **The occupancy check detects a second CUDA context, not stolen arithmetic.** Even 2% duty in a separate
   process costs 36% of throughput, while 25% duty inside the declared process costs 12.7%. Any scheme leaning
   on occupancy should be designed around that asymmetry rather than around a FLOP budget.
9. **Roughly 6% to 19% of a device can hide under the occupancy noise**, depending on whether the verifier can
   get a contemporaneous clean baseline. That is the width the handoff has to cover, and it is the number Q6
   should quote.
10. **The widened measurement policy is free in steady state** and costs about 250 MB/s of hashing on first
    touch. A one-time cost at model load, not a tax on serving.
11. **Configuration has to be in the declaration.** Code and weights are not enough. Standard practice in
    measured launch, omitted in our first build, and exactly the part the live statute reaches.

## Files

| File | Role |
|---|---|
| `provision_gpu.ps1` / `provision_dev.ps1` | the H100 box; the cheap Phase A box where the Tier 1 machinery was built |
| `setup.sh` | quiesce, vTPM access, IMA policy (boot-pinned route preferred, runtime write for dev) |
| `lib_ima.py` | IMA binary-log parsing and PCR replay, including the violation rule |
| `lib_tpm.py` | minimal TPM 2.0 quote parsing, no TSS needed on the verifier's side |
| `capture_set.py` | provisions the approved baseline and declaration |
| `declared_stack.py` | the declared stack, executed as a file so IMA measures it |
| `undeclared_exec.sh`, `undeclared_module.py` | the two undeclared-work probes, executed vs imported |
| `completeness_check.py` | enclave-side: replay, verdict, disclosure under the allowlist |
| `verify_completeness.py` | off-machine: quote, MAA, NRAS, declaration, verdict, allowlist |
| `tier0.sh` | GPU attestation and the three riders |
| `rider_cupti.py` | the CUPTI probe, since Nsight is absent from the image |
| `tier05_occupancy.py` / `tier05_verifier.py` | enclave chain and the verifier that owns the clock |
| `tier2_reviewer.py` / `tier2_reviewer_reason.py` | the in-enclave reviewer, terse and reasoning prompts |
| `appendix_reexec.py` | the demoted re-execution capture |
| `tier1.sh`, `gpu_tier1_sequence.sh` | the ceremony, and the ordered evidence run |
| `gpuout/collect/` | the fetched evidence: disclosures, quotes, tokens, logs |
| `devout/` | the Phase A evidence from the cheap box |

## Reproduce

```powershell
.\provision_gpu.ps1                 # ~$7 to $9/hr, tear down when done
```
```bash
scp -i ~/.ssh/ccverify_m1 *.py *.sh azureuser@$IP:~/t1/bin/
ssh ... 'cd ~/t1/bin && ./gpu_tier1_sequence.sh 300'     # tiers 0 and 1 end to end
python tier05_verifier.py --host $IP --key ~/.ssh/ccverify_m1 --seconds 90 --out gpuout/tier05
```
```powershell
python verify_completeness.py --disclosure ... --quote ... --signature ... --ak-pub ... `
  --challenge <hex> --declaration ... --baseline ... --maa-token ... --nras-token ... --cc-mode ...
az group delete --name ccverify-gpu-rg --yes --no-wait
```

## Status

- [x] Tier 0 accelerator binding, three roots, 20/20 off-machine on accept and on reject
- [x] Tier 0 riders: clocks, counters (two probes), mode reporting
- [x] Tier 0.5 occupancy over a verifier-bracketed interval
- [x] Tier 1 completeness, five cases, including the honest miss
- [x] Tier 2 reviewer in the confidential GPU, four prompt variants
- [x] Tier 3a tamper catch through the full chain
- [x] Appendix re-execution and audit-batch capture
- [x] VM torn down
