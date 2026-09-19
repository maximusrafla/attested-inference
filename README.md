# attested-inference: confidential-computing verification of declared AI serving code

A working proof of concept for a question a domestic regulator would have to answer without being given a
company's weights: **is the code running on these servers the code that was declared and approved?**

Everything here ran on real hardware, rented and then destroyed: Azure SEV-SNP confidential VMs, one Intel
TDX VM, and one NVIDIA H100 in confidential-computing mode. Sessions ran between late July and mid-September
2026. Total rented time was about 8.5 GPU-hours, roughly $75, itemized session by session in
`EVIDENCE/RUN-LOG.md` (one August session is not itemized, so the total is approximate).

## What this demonstrates

An off-machine verifier checks that the code, configuration and model weights a machine is serving are the
ones declared and approved, while the machine discloses almost nothing about what it is computing. Three
hardware roots (AMD SEV-SNP via Microsoft Azure Attestation, a vTPM quote over the register that records
every file executed in the domain, and NVIDIA's GPU attestation) are bound under one nonce to a single
disclosure hash, and the verdict is recomputed off the company's machines.

**Current results: six cases, 24/24 checks each, in one boot on the H100**
(`EVIDENCE/binding-fix-2026-09-14/h100-run3/`). The regulator holds file names and digests for the attested
window; it never receives weights, outputs or prompts.

The claim the mechanism supports, stated exactly: **no undeclared executable was executed in the attested
domain, under the measurement policy in force.** Not "nothing undeclared ran." That phrasing does not
survive the build.

## The strongest result is a negative one

A declared, approved process pushed **21,739 undeclared 4096-square bf16 matrix multiplications, about 3
quadrillion operations in 9.9 seconds, through an accepting verdict**, leaving no trace in the measurement
window: the clean run immediately after it covered exactly the same window. On-chip attestation answers
**identity** questions (what code, what configuration, what weights) and does not answer **quantity**
questions (how much compute ran). Since compute
governance is built on quantity thresholds, that gap is structural rather than a defect to patch, and it is
why the scheme has to hand off to instruments a sovereign already holds.

The occupancy check that might have covered the gap was measured rather than assumed. Its detection
threshold is **2.81% of throughput** at 3 sigma on a twelve-run paired null, which is about **2.4% to 2.7% of
a device** depending on which solo rate is the denominator, with a 95% interval on the threshold itself of
roughly 1.7% to 6.7%. And against an operator that simply pauses its hidden work while a challenge is in
flight, the check stops being a detector at all: measured drops of 0.09%, 0.07% and 0.03% against thresholds
of 0.20%, 0.10% and 0.09% at verifier duty cycles of 10%, 25% and 50%, none detectable, with the hidden work
at about half the chip.

## What a first build got wrong

Four defects were found by review after the first version passed its own tests, and all four are documented
here with before-and-after evidence rather than quietly fixed:

1. **The quote was signed by a key the ceremony made for itself**, never compared with the vTPM key the
   platform token attests. A forged bundle with the undeclared entry deleted passed the old verifier 14/14.
2. **Weight and configuration verdicts were the operator's own checker's**, never recomputed by the verifier.
3. **The platform token's issuer was read out of the token itself**, so an operator could publish its own key
   set and put a software key in the field the whole binding rests on. An accepting bundle was built with no
   confidential hardware at all (`EVIDENCE/binding-fix-2026-09-14/forged-issuer-nohardware/`).
4. **Entries IMA could not fingerprint counted as approved**, so a renamed unmeasurable file passed.

All four are fixed and the fixes are demonstrated against the same bundles that defeated the old verifier.

## What it still does not establish

- **Which measurement policy was loaded.** The policy is written once after boot and that write is not
  itself measured. A company that loads a narrower policy gets a genuine, correctly signed receipt.
- **That a privileged user cannot subvert the measurement.** The scheme assumes the running kernel will not
  let root bypass or unload IMA, which rests on Secure Boot and kernel lockdown. Assumed here, not measured.
- **How much work a declared process submits to the accelerator**, which the negative result above shows
  directly.
- **Code that never arrives as a file.** Execution of a binary existing only in memory is caught; code
  written into anonymous memory and jumped to is not.
- **Adversarial robustness generally.** One GPU, small models, three review fixtures, and an operator who
  was cooperative by construction.

## Layout

- `gpu/`: the GPU phase and the current verifier. Attestation binding (tier 0), occupancy bound (tier 0.5),
  completeness cases (tier 1), in-enclave reviewer (tier 2), tamper reject (tier 3a), re-execution appendix.
  **Start with `gpu/README.md` for results and `gpu/REPRODUCE.md` for one documented command per headline
  result.**
- `EVIDENCE/`: the run log (`RUN-LOG.md`), sanitized Azure activity-log exports for both build phases, and
  the retained bundles. **`EVIDENCE/binding-fix-2026-09-14/` is the current evidence and the best single
  file to read is its `README.md`.**
- `milestone1/`: the CPU enclave attestation chain (SEV-SNP, MAA token, disclosure discipline), 9/9 checks.
- `milestone3/` and `milestone3-kettle-gate/`: the xz-style two-part demo: deterministic source review plus
  build attestation (built on Kettle). Each layer alone misses half the attack; composed they catch both.
- `milestone0/`: the re-execution divergence harness (appendix material).

## Reproducing

Every headline verdict reruns from the commands in `gpu/REPRODUCE.md`, given an Azure subscription with the
GPU quota, the CLI, an SSH key and two Python packages. Not reproducible: exact throughput digits (thermal),
the cloud provider's in-guest agent behaviour (image-dependent), and the NVIDIA remote attestation path after
its SDK left support on 2026-09-15.

One precondition the code cannot enforce: **the challenge must be the regulator's own and unpredictable to
the operator.** The ceremony writes one into the bundle for convenience, and a regulator that feeds that file
back in gets no freshness, because the operator chose it.

## Credits

Every mechanism here is cited, not claimed. Load-time integrity measurement through IMA and the TPM is a
twenty-year lineage (Sailer et al., USENIX Security 2004, and measured boot); Keylime does remote
attestation of the IMA log; the build-attestation layer is built on Kettle. The contribution is the
composition into a regulator-facing disclosure, and an honest account of where it stops.

## License

MIT. See `LICENSE`.
