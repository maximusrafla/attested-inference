# Run log: actual outputs from the live SEV-SNP runs (evidence dossier)

Consolidated real command outputs, so the record does not depend on any chat surviving. Cross-check against the
hardware-signed files in this folder and the milestone READMEs.

## M1 (2026-07-26): substrate + attestation, off-machine verify

VM: Standard_DC2as_v5, eastus, ConfidentialVM + vTPM + secure boot. Inside guest: "Memory Encryption Features
active: AMD SEV"; /dev/tpm0 present; no /dev/sev-guest (Azure vTPM path). Verifier output (9/9):

```
[PASS] token signature verifies against MAA JWKS
[PASS] issuer is an attest.azure.net endpoint
[PASS] attestation type is sevsnpvm (from x-ms-isolation-tee)
[PASS] MAA compliance status is azure-compliant-cvm
[PASS] SEV-SNP guest is not debuggable (False)
[PASS] token nonce equals sha256 of this disclosure (nonce peels to 64276bb5..)
[PASS] disclosure verdict is accept
[PASS] code that ran matches the approved declaration
[PASS] disclosure carries no fields beyond the allowlist
9/9 checks passed
```
SEV-SNP report facts in the token: chip family Milan, launch measurement 5b0ce64a.., compliance azure-compliant-cvm.

## M3 gate (2026-07-26): Kettle runs on our substrate

```
kettle build. -> provenance.json (SLSA v1.2: git commit, lockfile hash, pinned cargo/rustc/kettle digests)
kettle attest. -> "Running on platform: az-snp" ; "Attestation complete! Evidence written to evidence.json"
kettle verify -> Attestation hardware signature valid | Provenance valid SLSA v1.2 | Provenance checksum match
 | Checksum match for binary artifacts/hello | Verification PASSED
```

## M3 full demonstration (2026-07-27): the xz split, three cases on real hardware

CASE 1 clean:
```
Attesting build ... checksum cabe11f82fb78999..
kettle verify: ✅ Verification PASSED
disclosure: {"verdict":"accept","review":"pass","attestation":"pass","rejected_by":[]}
```

CASE 2 Attack A (payload committed in source):
```
kettle verify: ✅ Checksum match for binary artifacts/greeter ; ✅ Verification PASSED <- attest MISSES it
review: REVIEW: REJECT [R1] src/payload.rs:14 obfuscated embedded payload <- review CATCHES it
disclosure: {"verdict":"reject","review":"fail","attestation":"pass","rejected_by":["review"]}
```

CASE 3 Attack B (build glue not in git):
```
clean git attested -> artifact sha 383777822998b10e ; verify PASSED
poisoned rebuild (uncommitted build.rs) -> artifact sha 800883e4dd67b091
clean run: "greeter v1.0.0 (release)" poisoned run: "greeter v1.0.0 (backdoor-active)"
kettle verify (poisoned artifact vs clean provenance): ⛔️ Verification FAILED <- attest CATCHES it
review (committed source): REVIEW: ACCEPT <- review MISSES it
disclosure: {"verdict":"reject","review":"pass","attestation":"fail","rejected_by":["attestation"]}
```

Result: neither layer alone catches both A and B; composed, they catch both. Disclosure withholds source, weights,
customer data, and the artifact.

## GPU phase (2026-07-30): confidential H100, Tiers 0, 0.5, 1, 2, 3a and the appendix

Substrate: `Standard_NCC40ads_H100_v5`, eastus2, community-gallery VMI `cgpu-NCC-2204-base-image`.
One NVIDIA H100 NVL (94 GB) inside an AMD SEV-SNP CVM. Driver 595.71.05, VBIOS 96.00.9F.00.04, CUDA 13.2,
torch 2.13.0+cu130. Kernel `6.8.0-1058-azure-fde`. **1.36 GPU-hours, about $9.50 to $12 against a $150 cap.**
Phase A (all Tier 1 machinery built and validated first on a cheap vTPM box) cost under $1.

### Tier 0: the accelerator is inside the attested boundary

```
CC State : ON
Multi-GPU Mode : None
CPU CC Capabilities : AMD SEV-SNP(vTOM Mode)
GPU CC Capabilities : CC Capable
CC GPUs Ready State : Ready
DevTools Mode : OFF
```

Three roots bound to one disclosure hash, verified from a Windows machine that never touched the box:

```
[PASS] quote is signed by the attestation key named in the disclosure (RSASSA)
[PASS] quote answers the regulator's challenge
[PASS] quoted PCR digest matches the PCR 10 value in the disclosure
[PASS] MAA token signature verifies against Microsoft's JWKS (sharedeus2.eus2.attest.azure.net)
[PASS] MAA reports an AMD SEV-SNP confidential VM (sevsnpvm)
[PASS] MAA nonce equals sha256 of this disclosure
[PASS] bundle contains an NVIDIA-issued (not local self-signed) GPU attestation (2 of 3 tokens)
[PASS] GPU attestation verifies against NVIDIA's published JWKS (alg ES384, iss nras.attestation.nvidia.com)
[PASS] GPU attestation nonce equals sha256 of this disclosure
[PASS] NVIDIA's verifier reports the GPU measurements matched golden values (success)
[PASS] GPU reports secure boot on and debug disabled (secboot=True dbgstat=disabled)
[PASS] GPU is in full confidential-compute mode, not DevTools mode
20/20 checks passed
attested accelerator: GH100 ueid 640622247271244735.., driver 595.71.05, vbios 96.00.9F.00.04
```

> **Perishability, found by the 2026-08-03 validity audit. This is a design finding, not a
> filing error.** Re-running the verifier today FAILS with `ExpiredSignatureError`. The MAA token carried an
> **8 hour** validity window (issued 00:49:41Z, expires 08:49:41Z) and the NRAS token a **1 hour** one, and
> NVIDIA's signing key has since rotated out of the published JWKS (the token's `kid` is absent from today's
> 40 keys). So the 20/20 splits in two. **Durable, still passing offline today with no network:** the TPM
> quote signature, its challenge binding, its PCR digest against the disclosure, the IMA log replaying to the
> quoted PCR (prefix 2426 of 2446), the AK and quote hashes, the declaration and baseline hashes, the verdict
> and the allowlist. **Perishable, verified at capture and no longer re-derivable:** the MAA chain, the NRAS
> chain, and the platform claims riding on them (SEV-SNP type, compliance, `measres`, `secboot`, `dbgstat`).
> **Implication for the scheme:** attestation evidence has a verification half-life measured in hours, so a
> regulator that archives disclosures and audits later needs prompt verification at receipt, a timestamping
> step over the verifier's own result, or an archive of vendor signing keys. None of that is in the build.

**Finding (attestation tooling).** The image's own `gpu-attestation` wrapper emits an EAT signed **HS256** and
issued by **LOCAL_GPU_VERIFIER**, that is, a symmetric self-assertion no third party can check. Neither
`nvattest` nor the attestation SDK ships on the VMI. Third-party-verifiable evidence needed the remote NRAS
path, which required pip-installing `nv-attestation-sdk`, deprecated 2026-03-15 with end of support
2026-09-15. A regulator relying on the shipped image would receive evidence it cannot verify.

Riders, all three:
- **(a) Clocks: locking WORKS in CC-On and the locked state IS externally queryable.** `nvidia-smi -lgc
 1200,1200` returned "All done" (exit 0), and `clocks.current.graphics` then read 1200 MHz against a
 1785 MHz max. `clocks.applications.graphics` returns "Requested functionality has been deprecated", so a
 verifier must read the current clock, not the applications clock. This is the measurement the Kimpson power
 paper's clock rung was left waiting on; both authoritative NVIDIA CC guides are silent on it.
- **(b)(i) Profiling counters: refused, with a named error.** Nsight is absent from the image, so the probe went
 through CUPTI via torch.profiler: `CUPTI_ERROR_CONFIDENTIAL_COMPUTING_NOT_SUPPORTED (41)`, "CUPTI
 initialization failed", 0 CUDA events with device time.
- **(b)(ii) NVML card-health telemetry still reads**, on the separate path: `utilization.gpu 0 %,
 utilization.memory 0 %, power.draw 66.05 W, temperature.gpu 40`. Recorded separately on purpose; a working
 NVML read is not evidence that the profiling counters are on.
- **(c)** `conf-compute -d` reports DevTools mode, so CC-On is distinguishable from CC-DevTools.

### Tier 0.5: a kernel ran and occupied the GPU across a verifier-bracketed interval

Ceiling measured once, outside the bracket. Occupancy from arithmetic, since the counters are off; interval
from the verifier's own clock, since the enclave has none.

```
device ceiling on an unconstrained bf16 GEMM stream : 440.49 TFLOP/s
orchestration slack (no-op round trip) : 2.21 s
verifier bracket : 93.59 s
total flops in a nonce-seeded dependent chain : 3.49e16
sustained over the FULL bracket : 373.22 TFLOP/s
occupancy over the full bracket : 84.7%
```

So over an interval the verifier itself bracketed, at most about **15.3%** of the device's arithmetic capacity
was left for undeclared work, and that figure is charged the full 2.2 s of orchestration slack, so it
overstates the headroom. Bounds arithmetic throughput only; memory-bound or low-occupancy work can hide in the
gap; says nothing outside the bracket; the mechanism is SM contention, an argument rather than a seal.

### Tier 1: the completeness demo, five cases on one boot

Claim wording, fixed 2026-07-30 after seeing the results: **no undeclared executable was executed in the attested
domain, under the measurement policy in force.** Not "nothing undeclared ran", which the fourth case below
refutes.

Chain: SEV-SNP report roots the CVM, measured boot roots the kernel and the IMA policy, Linux IMA extends vTPM
PCR 10 with the digest of every executed file, a quote over PCR 10 answering the regulator's challenge makes
that set non-repudiable, and the GPU attestation carries the same nonce to the accelerator. Baseline 2214
entries (476 digests) after a 300 s settle; declaration 53 digests from one approved reference run with real
GPU work.

| case | verdict | undeclared | payload matches | rejected by |
|---|---|---|---|---|
| accept (declared stack only) | **accept** | 0 | yes | - |
| undeclared code IMPORTED as data, non-root | **accept** | 0 | yes | - (the limit, see below) |
| declared weight file, one bit flipped | **reject** | 0 | **no** | payload_identity |
| undeclared binary EXECUTED alongside | **reject** | **1** | yes | undeclared_execution |
| same import, stack run as root under tcb | **reject** | **994** (one run; a second boot gave 976, so write "roughly 1000") | yes | undeclared_execution |

Both the accept case and the undeclared-exec case were bound to all three roots and verified off-machine at
**20/20**. The disclosure carries verdict, counts, digests and the quoted PCR value; it never carries the
measurement log, the paths, the weights or the output.

**Finding (the completeness claim is exactly as wide as the IMA policy).** Undeclared code that the declared
interpreter loads as DATA is invisible: under the tcb policy a non-root read is not measured, so the run came
back accept while undeclared code had in fact run. Running the same stack as root makes the tcb `euid=0` read
rule fire and the module is caught, but at the cost of measuring the interpreter's whole data footprint, 994
entries instead of 1. The honest statement is that this measures executed FILES, not GPU kernel launches and
not interpreted code loaded as data.

**Finding (PCR 10 never holds still on this host).** Azure's in-guest security agent (`azsec-monitor` /
`auoms`) continuously reads files that keep changing, and the tcb policy measures every one, so the register
advances between any two commands and a read-then-quote ceremony cannot be made atomic. The first attempt did
read separately, and the off-machine verifier correctly failed the binding check. The fix is to derive the
attested value from the quote: with one PCR selected, pcrDigest is sha256 of the register's value, so
replaying the log until a running value hashes to it recovers the exact prefix the quote covers.

**Finding (the operator does not control the declared set).** The same provider agents mean the approved
platform baseline must contain the cloud provider's own in-guest agents. A regulator checking a cloud-hosted
operator inherits the provider's agent set into what it is approving.

**Finding (the policy itself is not attested here).** This image has no `/etc/default/grub`; the kernel command
line is managed by the snapd FDE boot chain, so the boot-pinned `ima_policy=tcb` route was unavailable and the
runtime policy write was used instead. It is write-once per boot (a second write is denied), so the policy
cannot be loosened later, but the FIRST load is not itself covered by the measured boot chain.

### Tier 2: open-weight reviewer inside the confidential GPU

Qwen2.5-Coder-7B-Instruct, loaded on the CC GPU, bounded verdict through the M3 disclosure wrapper, over the
M3 fixtures. Mechanism cited to Agentic Witnessing (2604.24203).

| variant | clean | attack A (payload in source) | attack B (build glue) |
|---|---|---|---|
| terse prompt, fixtures as written | accept | reject (embedded_payload) | accept |
| terse prompt, explanatory comments stripped | accept | **accept (missed)** | accept |
| prompt naming the patterns, comments stripped | accept | reject (embedded_payload) | accept |
| same, declared source only (glue not in repo) | accept | reject, but category WRONG (hidden_network_call) | accept |

**Finding.** The fixtures' own explanatory comments were doing the work in the first run: with them stripped,
the reviewer accepted an obfuscated payload that M3's roughly 30-line deterministic scanner catches by rule.
A prompt that names the pattern recovers the catch, so this layer is scaffolding dependent rather than a fixed
capability, and when it did object the category it gave was sometimes wrong. Attack B was accepted in every
variant, including when the build-glue file setting `BUILD_TAG=backdoor-active` was shown to it directly, so
the M3 composition argument survives with the AI reviewer substituted for the deterministic one. Three
fixtures and one model: a datapoint for the can/can't map, not a capability measurement (the small-versus-
frontier reviewer gap is unquantified in the public literature, and this does not close it).

### Tier 3a and the appendix

Tier 3a is the tampered row above: one bit flipped in the declared weight file, identical verification, reject
on the same attestation chain, accept and reject side by side.

Appendix (re-execution, demoted by the reshape, reported as a redundancy option only):
```
same-hardware noise floor at the canonical batch=1 shape : 0.0 nats (median and max)
tolerance T : 1e-6 nats
perturb eps=1e-4 / 1e-3 / 1e-2 caught : 100% / 100% / 100%
median divergence at eps=1e-4 : 3.85e-4 nats
verification throughput : 0.0417 s per record
projected 30,000-record audit batch : 0.35 hours on one H100
```
The determinism harness (`CUBLAS_WORKSPACE_CONFIG=:4096:8`, deterministic algorithms, cudnn.benchmark off,
batch=1 on both sides) collapsed the GPU floor to bit-exact, as the pre-run design review predicted. Throughput is stated
in CC-On on the verification workload; it is NOT an overhead measurement, since CC-off is not togglable on a
rented instance.

## Second pass (2026-07-31): the two tiers tested against each other, and three method fixes

*Run after the first pass, on Maxim's instruction to spend budget on improving rather than only
reporting. One cheap Trusted Launch box (under $1) and a second H100 session (0.82 GPU-hours, about
$5.70 to $7.20). Cumulative GPU spend across both passes: 2.18 hours, roughly $15 to $19.*

### The headline: the two tiers share a blind spot, they do not cover each other

The first pass left Tier 0.5 asserting its own mechanism. It measured that the challenge occupied
84.7% of the device and then ARGUED that undeclared work would contend for SMs and show up. That is
the argument the whole tier rests on, so it was the thing to measure. The measurement splits sharply
on **where the undeclared work runs**, and the split is the finding.

Paired protocol (clean and contended measured back to back, so warmup and thermal drift cancel in the
ratio; an unpaired bookend control showed 4.4% drift across one sweep, which is what made the naive
version far too pessimistic):

| undeclared work at this share of the device | as a SEPARATE process | in the SAME process and CUDA context |
|---|---|---|
| 2% | **2.0% drop** (detectable) | 1.9% drop (NOT detectable) |
| 5% | **3.8% drop** | 3.3% drop |
| 25% | **13.9% drop** | 12.7% drop |
| 50% | **27.4% drop** | 21.9% drop |

Run-to-run stdev of the paired ratio is 0.1% to 1.2%, so the detection floor is tight.

> **CORRECTED TWICE ON 2026-09-03, and the second correction is the one to trust.** This column read
> 35.8 / 43.4 / 48.8% until that date, then briefly 11.4 / 11.9 / 13.5%, and now reads the figures above. The
> sentence under it used to say a second CUDA context is loud "because the cost is context switching rather
> than the stolen arithmetic". That is mostly wrong too.
>
> **The cause was a mislabelled axis, not a bad measurement.** `contention.py --duty` asked for a slice of
> wall time. Submitting a matmul is asynchronous and costs about 10 microseconds; executing one at 8192 square
> in bf16 costs about 2 ms. A slice paced on wall time therefore queued far more device work than it was long,
> and the `synchronize` at the end waited for all of it. A 1 ms slice does not take 2% of the device. The
> first correction bounded submission to four matmuls ahead but still paced on wall time, which left a floor of
> four matmuls per slice, so the 2% and 5% labels produced the same experiment (measured rates 60.2 and 60.9
> iterations per second).
>
> **The fix is to pace on measured device time.** Each period submits a whole number of matmuls sized from a
> calibrated per-matmul time, and the achieved share is measured and reported, so the label is never the figure
> quoted. Measured 2026-09-03, same VM size, driver 595.71.05, VBIOS 96.00.9F.00.04, torch 2.13.0+cu130:
>
> ```
> asked for drop protocol
> 2% 35.5% archived original pacing, unbounded submission
> 5% 44.3% archived original pacing, unbounded submission
> 25% 49.8% archived original pacing, unbounded submission
> 2% 2.0% device-time pacing (correct)
> 5% 3.8% device-time pacing (correct)
> 25% 13.9% device-time pacing (correct)
> 50% 27.4% device-time pacing (correct)
> ```
>
> The archived-pacing runs **reproduce the published figures** (35.5 / 44.3 / 49.8 against 35.8 / 43.4 / 48.8),
> so the July numbers were correct measurements of a competitor taking far more of the device than its label
> said. A control changing only the other candidate variable, starting the measurement without waiting for the
> competitor to be ready, gave the same answer as the main arm, so competitor startup was never the cause.
>
> **What survives unchanged, and it is the load-bearing part:** a separate undeclared process is still loud and
> still caught, and a positive control at a 4% share gives a 4.5% drop and is called; same-context work is
> still quiet, still roughly proportional, and still the case Tier 1 cannot see; and the same-context duty
> response reproduces across sessions. **What changes besides the numbers:** the cost is roughly the arithmetic
> taken across most of the range, not context switching, with the exception of the very bottom of the range
> where a 2% share costs about 2.0%, roughly twice what it takes.; evidence
> `e1v2-adaptive-adversary/`, superseding `e1-adaptive-adversary/`.

**Why that matters: it is the same case Tier 1 cannot see.** Demonstrated directly, in one session on
one boot: the declared process ran roughly 22,800 undeclared GPU matmul iterations (about 3.1 PFLOP)
and the Tier 1 disclosure came back **accept, with 0 undeclared entries**, identical to the clean run.

> **Evidence note (validity audit, 2026-08-03).** The accept verdict and the zero
> undeclared entries ARE retained, in `gpuout2/tier1/undeclared-gpu/disclosure.json`, and that is the
> load-bearing half. The **iteration count is NOT retained in any artifact**: it lived in the
> withheld-side output, which the disclosure discipline deliberately never collects. So the magnitude
> is transcript-sourced and should be written as approximate. Either re-run and retain a counter in an
> allowlisted field, or keep saying "roughly".

So the two mechanisms lean the same way rather than covering each other:

| undeclared work | Tier 1 (executed files) | Tier 0.5 (occupancy) |
|---|---|---|
| separate undeclared process | **caught** (new executable measured) | **caught loudly** (11 to 14% drop against a ~1% floor; 47.6% flat out) |
| kernels from the declared process | **missed entirely** | a real but small signal, and converting it to a verdict needs a trusted uncontended baseline the operator controls (see the bound below) |

The honest conclusion, and it is a negative one: an operator submitting undeclared accelerator work
from inside its own approved process is invisible to the file measurement, and while the occupancy
signal is genuinely present, acting on it requires knowing how fast the device runs when nothing is
stealing from it, which is a figure measured on the operator's own machine. That residual is
precisely what the handoff map exists for, and power metering (Kimpson) is the instrument that has to
carry it.

### The bound: what the handoff has to cover (added 2026-07-31)

An observation is not a claim until it is compared against how much a clean run varies on its own.
Null distribution, 12 repeated clean runs with nothing undeclared running:

```
clean mean 382.4 TFLOP/s
paired null sigma (n=6 consecutive pairs) 1.16%
unpaired null sigma (n=12, against the mean) 3.13% <- how a stored ceiling behaves
```

Duty response, paired, same CUDA context, two independent runs agreeing at their overlap (7.18% and
7.25% at duty 0.15):

```
duty 0.01 0.02 0.03 0.05 0.08 0.15 0.20 0.25 0.30
drop 0.88% 1.34% 1.80% 2.78% 4.69% 7.22% 9.97% 11.79% 14.11%
```

At a 3-sigma verifier threshold:

| verifier protocol | threshold | duty knob at that threshold (WHAT THIS USED TO REPORT) | device share, corrected |
|---|---|---|---|
| clean baseline adjacent to the challenge | 3.47% drop | ~6% (was mislabelled "device capacity") | **about 3.5% of a device** |
| compared against a stored ceiling | 9.39% drop | ~19% (same mislabel) | **about 9% of a device** |

The paired figure is a floor on what this instrument could ever achieve, and it assumes the verifier
can obtain an uncontended baseline on demand, which an operator choosing when to cheat can deny. So
**roughly 6% to 19% of a device is the width the handoff must cover.**

> **CORRECTED 2026-09-03: these widths are in the wrong units and are roughly twice too large.**
>
> `tier05_bound.py` interpolated the detection threshold back through its own duty knob and printed the answer
> as "percent of device capacity". The knob is undeclared matmuls per declared step, which is not a device
> share. Measured directly, with the undeclared work's throughput divided by a solo rate calibrated in the same
> run and retained, drop and device share are the same quantity for same-context work to within 3% (duty knob
> 0.05 / 0.10 / 0.20 / 0.30 gives drops of 4.50 / 8.52 / 15.74 / 21.94% against shares of 4.33 / 8.27 / 15.29 /
> 21.21%).
>
> So the hideable width is the detection threshold itself: **2.7% of a device** with a baseline next to the
> challenge and **1.8%** against a stored ceiling, on a twelve-run null. The 95% interval on the first is
> roughly **1.7% to 6.7%**, because the null rests on six paired ratios, so the old ~6% sits at the top of that
> interval rather than outside it. **The instrument is about twice as sensitive as this section claimed** and
> the handoff residual is correspondingly smaller; the structural point, that the width is set by a baseline
> the operator controls, is unchanged.
>
> **These remain non-adaptive-adversary figures.** Against an operator that pauses while the challenge is in
> flight, no undeclared work executes inside the window at all, so there is no width to state. What
> unpredictable timing buys is measured separately: the operator keeps one minus the verifier's duty cycle of
> whatever it was achieving unchallenged (0.90, 0.74, 0.51 at duty 10, 25, 50%).; evidence
> `e1v2-adaptive-adversary/`. Sigma from n=12 carries about
20% relative uncertainty; treat these as one significant figure. This is the on-chip analogue of an
identified-set width, and it fails the way a power meter does: both infer a quantity from an aggregate
signal against an efficiency baseline the operator controls.

## E1 (2026-09-03): the occupancy check against an adversary that reacts

Two sessions on one confidential H100 in an SEV-SNP CVM. The first was reviewed and several of its claims
did not survive, so it was rebuilt and rerun the same day. **v1: 1.63 GPU-hours, $11.38 to $14.38. v2: 1.82
GPU-hours, $12.70 to $16.04.** Read the project's own notes, not the v1 note; section 7 of
it lists everything v1 got wrong. Evidence in `e1v2-adaptive-adversary/` with `ANALYSIS.txt` recomputing every
figure from raw values. Protocol written before the rerun: the project's own notes.

**The largest finding is not about the adaptive adversary.** The hideable width had been reported in units of
the sweep's own duty knob while labelled "percent of device capacity". Corrected, it is **2.7% of a device**
paired and **1.8%** against a stored ceiling, not roughly 6% and 19%. That correction is boxed in the bound
section above.

**The adaptive adversary is not caught, and in v2 that is a measurement rather than arithmetic.** Thirty second
windows, one second challenges at random offsets, three independent repeats per point, no drain gap:

```
 separate process same context
verifier duty drop 3*SE caught drop 3*SE caught kept 1-d
10% 0.21% 0.46% no 0.09% 0.20% no 0.90 0.90
25% 0.29% 0.35% no 0.07% 0.10% no 0.74 0.75
50% 0.48% 0.46% YES 0.03% 0.09% no 0.51 0.50
```

Undeclared work kept is one minus the verifier's duty cycle to within a percentage point. That follows from the
adversary model rather than the device; what the measurement adds is that there is **no restart penalty**, so
an operator interrupted repeatedly loses only the wall time it surrendered.

**New positive: a separate process leaks at every pause.** Its in-window drop rises with the number of
challenge windows, from 0.21% at three windows to 0.48% at fifteen, and at fifteen it clears three standard
errors. A separate context has to be scheduled out and back in each time. Same-context work has nothing to
schedule and leaks nothing, its drop falling as challenges get more frequent. **The call at 50% is marginal,
0.48 against 0.46 on three repeats, and is suggestive rather than established.**

**Positive controls, both of which v1 lacked at the scale that matters.** A separate process at **3.97% of the
device gives a 4.47% drop and is called**; at 49% it gives 54.24%. So the instrument detects at the scale where
decisions are made, which is what licenses the null results above.

**The reaction deadline is bracketed by measurement, not fitted.** Fixed 25% verifier duty, one second windows,
two repeats:

```
asked measured drop 3*SE caught
0 ms 4.7 ms -0.12% 0.03% no
5 ms 9.6 ms -0.00% 0.15% no
10 ms 15.2 ms 0.65% 0.01% YES
30 ms 34.0 ms 1.46% 0.20% YES
100 ms 106.2 ms 4.96% 0.08% YES
250 ms 256.2 ms 11.98% 0.14% YES
```

Not caught at a measured 9.6 ms, caught at 15.2 ms. v1 quoted "about 15 ms" from a fit through the origin whose
own zero-reaction point contradicted it by a factor of 250. The deadline is not a constant of the device: it
scales with the challenge window, inversely with the undeclared work's share of the device, and with the
verifier's baseline quality.

**The cost side of the trade is NOT measured here, and a v1 claim to the contrary is withdrawn.** v1 said
bounding undeclared work at 10% of a device "requires challenging 90 percent of the time and costs the company
90 percent of that device". Both halves were wrong: the 90% treated the operator's own ceiling as a whole
device, and in this harness the challenge window *returns* capacity to the declared workload, which runs about
twice as fast inside a window as outside it. The undeclared ceiling here is about 49% of the device, so a duty
cycle near 0.8 would be needed to hold undeclared work under a tenth of a device.

**A dormant undeclared context**, allocated and resident but submitting nothing, costs **0.31%** and is not
detected. That is what v1's adaptive Part A arm was actually measuring.

> **A confound found mid-run in v1 and fixed in v2, recorded because it would have manufactured the result.**
> v1's first duty sweep reported the contended run as **7% FASTER** in-window than the reference, at every
> duty, against a null of 0.05%. The drain gap before each challenge was applied only when hidden work was
> present, so contended windows began after an idle pause the reference never got and the comparison measured
> the device's clock response to that pause. v2 defaults the gap to zero and the transient is gone. The
> uncorrected run is retained as
> `e1-adaptive-adversary/B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json`.

**Not covered, and not claimed either way:** the challenge here is the declared chain itself, timed, and the
operator learns of it from a flag the harness sets, so this measures an operator that is **told**. Whether a
real challenge is distinguishable from production work is untested, and no puzzle displacing production work
was modelled. The out-of-window column bounds what a covert challenge would be worth: in every adaptive run the
declared chain runs 49 to 54% below reference for the whole time the verifier is not challenging.

### What the widened policy costs the operator

994 log entries is the verifier's burden; this is the operator's. Same workloads, default policy
versus widened, policy written once per boot so the default was measured first:

| workload | default | widened | ratio |
|---|---|---|---|
| 200 unique binaries executed (cold) | 100.9 ms | 301.8 ms | 3.0x |
| the same binaries again (warm) | 96.1 ms | 98.3 ms | 1.0x |
| 419 MB read as root (cold) | 54.7 ms | 1726.1 ms | **31.6x** |
| the same bytes again (warm) | 54.6 ms | 52.9 ms | 1.0x |
| interpreter startup | 13.3 ms | 13.1 ms | 1.0x |

IMA hashes once per boot and caches, so the price is first-touch and proportional to bytes, about
**250 MB/s** as measured on this 2-vCPU box. Steady state is free. For a serving operator that is a
one-time cost at model load (roughly 7 minutes for a 100 GB checkpoint here) and no ongoing tax on
serving; the pathological case is a workload that continuously streams new large files. One small VM,
and the MB/s figure will move with the CPU. Note it is well below SHA-NI hardware rates, so this path
may not be accelerated: measured, not explained.

### Method fixes to the first pass, two of which changed the answer

**1. The Tier 2 reviewer result was wrong in its explanation.** The first pass reported that a terse
prompt missed the payload and a "reasoning prompt with 768 tokens" recovered it. That changed two
variables at once. The full 2x2 (prompt content x token budget), 3 fixtures, greedy plus 5 sampled
generations per cell:

```
prompt budget fixture greedy reject rate (5 samples) tokens emitted
terse 96 attack_a_insource accept 0.0 21
terse 768 attack_a_insource accept 0.4 22
named 96 attack_a_insource reject 0.2 21
named 768 attack_a_insource reject 0.2 22
terse * attack_b_buildglue accept 0.2 / 0.2 22
named * attack_b_buildglue accept 0.0 / 0.0 22
* * clean accept 0.0 in every cell 21-22
```

- **The token budget does nothing.** The model emits about 22 tokens whether given 96 or 768. The
 first pass's "reasoning prompt" label was simply wrong; it never used the budget.
- **Naming the pattern is the variable that moved the greedy verdict** on attack A, from accept to
 reject, at both budgets.
- **But sampling shows the verdict is unstable, which greedy decoding hid.** Attack A is caught in
 only 1 of 5 samples even with the named prompt, and in 2 of 5 with the terse prompt at 768. So
 "naming the pattern recovers the catch" is too strong: it moves the mode, while the underlying
 reliability is low. **Restated as rejections per violation (the correct metric; a positive is a red flag):
 pooled 6 of 40 sampled violation generations = 15%, 95% CI 7 to 29%; attack A alone 4 of 20 = 20%, CI 8 to
 42%; false alarms 0 of 20 = 0%, CI 0 to 16%.** The earlier "20 to 40 percent" was the spread of per-cell
 rates, and cells of n=5 carry almost no information (a single 1-of-5 cell has a 95% interval of 4 to 62%),
 so only the pooled figures should be quoted. Corrected 2026-08-03.
- **No false positives.** The clean fixture was accepted in 5 of 5 samples in every cell, so the
 failure mode is misses rather than noise.
- Attack B was never caught by the named prompt at all, and only 1 in 5 times by the terse one.

**2. Configuration was not declared, and that is where the statute's hook lives.** Behaviour is code
times weights times config, and the build declared only the first two. Demonstrated on the cheap box:
switching `safety_filter` from on to off, swapping the system prompt, and raising temperature from 0.0
to 1.4, with code and weights untouched, produced **accept**, with every attested value identical. The
change was visible only in the withheld output. Then the gap was closed by declaring the config
digest alongside the weights: the same run now comes back **reject, rejected_by
["configuration_identity"]**. Unlike the IMA policy widening (994 measured entries for one catch),
this fix costs one hash, so the gap is cheap to close and any real deployment must close it.

One bug worth recording because it made the first attempt at the fix look like it worked when it did
not: the declaration must name the file the stack actually READS, not the pristine approved copy. It
initially pointed at the copy, so the check hashed a file the operator never touches and a swapped
runtime config matched happily.

**3. Accept-stability.** Three consecutive accept runs, 0 undeclared entries each, so the first
pass's single accept was not luck.

### Bounded adversarial pass against the Tier 1 mechanism

Five probes, each an attempt to do undeclared work without the measurement seeing it. Detector: does
the payload's own digest appear in the log.

| probe | caught? | what it establishes |
|---|---|---|
| trim an entry from the quoted prefix | **caught** | the log has no authority of its own; only agreement with the quoted value does |
| declared program JITs code into anonymous RWX memory and jumps to it | **NOT caught** | the Python-import result generalised: file-backed measurement cannot see execution that is not file-backed |
| `memfd_create` plus exec, a binary that never exists on disk | **caught** | IMA hooks exec rather than the filesystem, so a fileless binary is still hashed. Better than expected |
| edit the approved program after approval, then run it | **caught** | measure-once is keyed on content, so an edited file is re-measured |
| run undeclared code as another user | **caught** | the exec rules carry no uid condition |

**Two detector designs had to be discarded before this table was trustworthy, and both had produced
confident wrong answers.** The first asked "did PCR 10 move during the probe", which is useless here
because the provider's agents move it constantly: everything read as caught. The second asked whether
the payload digest was NEW since the probe began, which gave a false negative on the post-approval
edit, because the edit appended a constant string that an earlier run had already put in the log. That
version would have supported the claim that editing an approved file goes unmeasured, which is false
and would have been a serious error. Payloads are now unique per run and the detector asks whether the
digest is present at all.

### Friction log continued (from entry 9)

10. **Converting to pay-as-you-go zeroed the CPU-CVM quota.** `standardDCASv5Family` read 0 vCPUs in every
 region checked, so M1's `Standard_DC2as_v5` recipe no longer deploys. Only the families explicitly granted
 had room. Phase A moved to a Trusted Launch `Standard_E2bds_v5` (vTPM without SEV-SNP), which is all the
 Tier 1 machinery needs.
11. **`az vm create` from a community gallery image prompts for terms** and dies with "EOF when reading a line"
 non-interactively. Needs `--accept-term`.
12. **Editing `/etc/default/grub` is a no-op on Azure Ubuntu cloud images.**
 `/etc/default/grub.d/50-cloudimg-settings.cfg` is sourced afterwards and overwrites
 `GRUB_CMDLINE_LINUX_DEFAULT`. The append has to go in a file that sorts last. On the confidential-GPU VMI
 there is no `/etc/default/grub` at all.
13. **IMA logs violation entries with an all-zero template digest but extends every PCR bank with all ones.**
 A naive replay fails and looks like log tampering. Fires on `/var/lib/hyperv/.kvp_pool_*` and the systemd
 journal, so every real log on this platform has them. Rule confirmed against both banks of a live vTPM.
14. **A stock cloud image false-rejects on its own housekeeping.** The MOTD pipeline runs on every SSH login;
 apt, sysstat and update-notifier run on timers. Each is a file execution IMA measures.
15. **IMA measures a file once per boot**, so a scheme scoped to "entries since the run started" passes on an
 empty set, and any tool whose first execution lands mid-run is attributed to the workload. The claim has to
 be cumulative-since-boot, and the ceremony has to warm every tool it uses first.
16. **The completeness claim is monotonic per boot.** Once something undeclared has executed, no later run in
 the same boot can return a clean verdict without a reboot. Correct semantics, but it means the order of
 experiment cases is load-bearing.
17. **An administrator logging in false-rejects the run.** A new SSH connection after the baseline is frozen
 adds 21 measured entries (`sshd`, `libwrap`, the host keys, `openssl.cnf`, `gai.conf` and the rest of what
 sshd reads as root). The whole ceremony has to run inside ONE session, or the approved baseline has to cover
 the operator's own administrative access. Found by running the sequence across separate ssh invocations,
 which the first pass had avoided by accident rather than by design.

## Azure Activity Log
`azure-activity-log.json` in this folder: 50 subscription-level entries (VM/disk/NIC/NSG/public-IP create + delete
for ccverify-m1-rg, ccverify-m3-rg, ccverify-m3run-rg). Independent proof the resources existed; survives deletion,
retained ~90 days.

## How to re-verify independently
- The .jwt and evidence.json files are signed by AMD/Azure roots; verify offline (see milestone1/verify_token.py and
 `kettle verify`). No trust in us required.
- Reproduce end to end: milestone3/README.md "Reproduce" section (~cents on free credit).

## E1b, 2026-09-03 (UTC 09-04 01:20 to 02:29): leak versus window count at fixed duty

One `Standard_NCC40ads_H100_v5` in eastus2, same image and driver as E1 v2, torch pinned 2.13.0. About 1.3
GPU-hours, $9 to $12. Question: is the v2 separate-process leak (#220b, marginal at n=3) real? Answer: yes,
and it is paid per transition. At 25 percent duty over 60 s with six pairs per arm, the in-window drop is
0.08 / 0.45 / 0.64 / 0.97 percent at 5 / 15 / 30 / 60 windows (log-log exponent about 1.0), the same-context
arm is -0.15. Mechanism from the competitor's counters: 8 to 10 ms of reaction overlap per window at about
half contention. Evidence `e1b-leak-vs-windows/`. Two gotchas for the log: (18) `provision_gpu.ps1` exits nonzero on an Azure stderr WARNING after the VM
is already created, because `$ErrorActionPreference = "Stop"` turns PowerShell's NativeCommandError wrapper
into a stop; the IP and start files then have to be written by hand. (19) The first reference/contended pair
of a fresh session runs on a cold, boosted device (reference 457 against 434 TFLOP/s for every later
reference); either discard it under a stated rule or run a throwaway pair first.

## E1c, 2026-09-04 (UTC 04:40 to 04:53): the same-context bound below 4 percent, denominator bracketed

Same SKU and image, torch 2.13.0. About 0.45 GPU-hours, $3 to $4. Same-context bound at knobs 0.01 to 0.10,
six pairs per point, 12-run null, 20 s sustained calibrations before and after. Result: the response curve is
linear down to 0.85 percent of a device (drop 1.06 to 1.09 times in-run share), sustained/short calibration
ratio 1.10 in-session, so the published 2.7 percent width becomes a measured 2.4 to 2.7 with nothing extrapolated.
Evidence `e1c-lowload-bound/`, Gotcha (20):
**never place a sustained full-tilt run immediately before the null.** The device cooled 7 percent across the
first five clean runs after the 20 s calibration, the paired sigma read 1.45 percent on all runs and 0.57 with
the first dropped, and the session's own width swung 2.5x on that choice. The null must sit in the thermal
state of the pairs it judges. Same provisioning-script stderr trip as gotcha 18; IP and start written by hand.

## Log export (2026-09-14): the regulator replays the measurement log itself

An external review of the write-up found the hole in the Tier 1 disclosure design: the log stayed inside,
so the regulator could not interpret the quoted PCR 10 value or identify the code that produced the verdict,
and the enclave-side checker was itself never measured (it runs as `python3 completeness_check.py` under the
unprivileged user, so the interpreter is measured and the script is read as data). Check 4 bound the
disclosure to the quote, not the verdict to the log. Fix, the one Keylime uses: export the log with the
receipt and replay it on the verifier's machine. `gpu/verify_completeness.py` gained `--ima-log` and checks
10 to 13; `completeness_check.py` is retained as the operator's pre-check.

No hardware rented. Every July ceremony dumped the log after the quote, so the retained bundles were enough.
Rerun off-box with the expired tokens omitted, all 14/14, the verifier's own recount reproducing every
count the enclave had reported: accept 2426/0 (prefix 2426 of 2446), undeclared-exec 2698/1, undeclared-
import 2503/0, import-as-root 3802/994, tampered 2590/0, and the cheap box's accept 574/0. Outputs and a
table in `log-export-verify-2026-09-14/`. What the regulator now holds that it did not: file names and
digests for the window. What it no longer takes on the operator's word: the counts and the verdict.


## Hardware binding and the serving account (2026-09-14): the forged receipt, and the fix

A second external review asked what pins the kernel and the policy that write PCR 10. Reading the harness to
answer it found two holes that no check in the build had ever failed on.

**(1) The quote was not bound to the attested vTPM.** `tier1.sh` created its own key with `tpm2_createak` and
quoted with it; the verifier checked the signature against whatever key the bundle named and checked the MAA
token only for type, compliance and nonce. The token carries the vTPM's own attestation key as `HCLAkPub`, and
nothing compared the two. On the retained July GPU accept bundle the quote's key matches none of the three keys
in the token (HCLAkPub, HCLEkPub, TpmEphemeralEncryptionKey). `TPM_GENERATED_VALUE` restricts what a TPM
restricted key will sign and says nothing about a key that is not a TPM key.

**(2) Weights and configuration were the operator's word.** `completeness_check.py` hashed the declared files
at their declared paths and wrote the answer into the disclosure; the verifier read the field. The declared
stack ran as the ceremony user, whose reads were not measured, so the weight file never entered the log. The
July tampered case rejected only on `payload_identity`, a field the verifier could not recompute.

**The forgery (`gpu/forge_quote_demo.py`, no hardware).** Take a genuine reject bundle, delete the undeclared
entries from the exported log, replay the edited window, splice the challenge and the new digest into the real
quote structure, sign with a 2048-bit software RSA key, write an accept disclosure naming that key.
**Pre-fix verifier: 14/14, accept.** Fixed verifier: rejected. On the TDX box the same forgery **with a genuine
MAA token requested over the forged disclosure's hash passed 16 of 17 checks and failed only the key check**,
which isolates that check as the load-bearing one. The July accept bundle through the fixed verifier fails the
key binding, the boot registers and the weights presence.

**The fix.** Quote with the vTPM's persistent AK (`0x81000003`, `restricted|sign`) over PCRs 0 to 10; verifier
requires that key to equal MAA's `HCLAkPub`, requires MAA's attested PCR 0 to 7 values to equal the quoted ones
and the quoted registers to equal the regulator's references, and requires the log's first entry
(`boot_aggregate`) to equal sha256 over the quoted PCRs 0 to 9. **Confirmed on hardware: PCRs 0 to 9, not 0 to
7** (`ima_calc_boot_aggregate_tfm` includes 8 and 9 for non-SHA1 banks since 5.8). The declared stack runs as a
dedicated serving account and the policy adds `measure func=FILE_CHECK mask=^MAY_READ uid=<svc>`, with
`dont_measure ... func=FILE_CHECK` for proc, sysfs, securityfs, cgroup and efivarfs only, so memfd execs are
still measured. `lib_verdict.py` defines the verdict once for both sides; weights and configuration are judged
by presence in the attested window.

**Runs.** Dev box `Standard_DC2es_v6` (Intel TDX, westus, kernel 6.8.0-1064-azure-fde), used because this
subscription has **zero AMD confidential-CPU quota in every region checked**: baseline 222 entries, declaration
50 digests, five cases **17/17** each. H100 box `Standard_NCC40ads_H100_v5` (eastus2, H100 NVL, driver
595.71.05, VBIOS 96.00.88.00.11, torch 2.13.0+cu130, `nv-attestation-sdk` 2.7.3 against the **NRAS v4**
endpoint, kernel 6.8.0-1064-azure-fde): run 1 baseline 1,186 entries / 1,058 digests, declaration 915 digests,
five of six cases **23/23**; run 2 after a reboot, the clean pair, baseline 766 / 719, declaration 943, both
**23/23 accept**, the undeclared-GPU case running 22,089 hidden 4096-square bf16 matmuls (about 3.0 PFLOP) in
9.9 s and leaving the window byte-identical to the clean run's. Five probes rerun under the new policy: memfd
exec, post-approval edit and other-user all caught, JIT into anonymous memory not, the trim probe erroring
because it assumes a PCR-10-only quote. Evidence `binding-fix-2026-09-14/`. **About 25 minutes of H100 time,
$3 to $4, plus under an hour of TDX box at about $0.10.**

Gotcha (21): **run 1's undeclared-GPU case rejected on the provider, not the workload.** Between two cases the
in-guest security agent read a changed `/etc/azsec/auoms_desired_state.json`, which lands in every later window
because the log only grows. The clean pair in run 2 exists for that reason. Gotcha (22): `sudo` resets PATH, so
the serving account needs the interpreter chosen explicitly (`sudo -u svc env PATH=..`), or the shebang finds
the system python and the torch import fails. Gotcha (23): NVIDIA's SDK pins `cryptography`, `urllib3` and
`PyJWT`, so it gets its own venv beside torch's.

## Two more holes, and the corrected run (2026-09-14, later the same evening)

Two fresh reviewers were handed the fixed verifier and told to break it. Both did, in ways no hardware run
above could have shown, and both are now fixed with the demonstrations retained.

**(3) The platform issuer was read out of the token.** `check_maa` took `iss` from the token and fetched the
signing keys from that URL, so an operator can sign its own token, publish a matching key set at its own
address, and put a **software key in `HCLAkPub`**, the one claim the key binding rests on. A reviewer
assembled an accepting bundle with **no confidential hardware at all: 17/17**. The GPU path had pinned
NVIDIA's issuer since July; the platform path never pinned Microsoft's. Fixed: the issuer must match
`^https://<name>.<region>.attest.azure.net/?$`, checked before any key is fetched, and the token algorithm
must be asymmetric. A self-issued token now fails offline, with no network call.

**(4) Unmeasurable entries counted as approved.** IMA logs a **violation** when a file is written while being
read: all-zero digest in the log, all-ones into the register, independent of content. `capture_set.py` put
that all-zero digest into the approved set, and `judge()` keyed on digest alone, so approving one violation
approved every unmeasurable file. A reviewer rewrote an existing violation's path to
`/root/undeclared_miner.py` without touching the register (the extend value does not depend on the entry) and
still got **accept, 23/23**. Fixed: `measurement_violation` is its own rejection reason and violations never
enter an approved set.

**Consequence, and a policy change.** Fix (4) rejected runs 1 and 2: the provider's agents read
`/var/log/auth.log`, `/var/log/syslog` and a waagent results file while they were being written, three or four
violations a boot, all through the `euid=0` read rule inherited from `tcb`. **That rule is dropped.** Coverage
now comes from the serving account's reads; a file read by the administrator is no longer covered, and that is
stated in the write-up.

**Run 3, the current results** (`binding-fix-2026-09-14/h100-run3/`): same SKU, driver 595.71.05, VBIOS
96.00.9F.00.04, kernel 6.8.0-1064-azure-fde, 11-rule policy retained in `h100-env-and-policy.txt`. Baseline
**270 entries / 269 digests**, declaration **1,045 digests**. **All six cases 24/24 in one boot, no
violations:** undeclared-GPU accept (**21,739** hidden 4096-square bf16 matmuls, about 3.0 PFLOP in 9.889 s),
clean accept over the same **1,315**-entry window, then tampered, config-changed, undeclared-import and
undeclared-exec each rejecting on exactly the fingerprint it added. Probes rerun: memfd exec, post-approval
edit and other-user caught; JIT into anonymous memory not. **About 17 minutes, $2 to $3.** Box deleted.

Gotcha (24): **a review of the code found what three hardware sessions could not.** The issuer hole and the
violation hole were both invisible to every run, because every run used a genuine token and a quiet machine.
Gotcha (25): the challenge is generated by the ceremony and shipped in the bundle for convenience; a regulator
that feeds it back in gets no freshness at all. Stated as a protocol precondition in the verifier's docstring.
