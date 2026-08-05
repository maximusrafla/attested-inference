# cc-verify PoC: confidential-computing verification of declared AI serving code

This repo: Claude-drafted and Claude-run under my direction; I verify what I present. Results were produced
on real hardware (Azure SEV-SNP confidential VMs; one NVIDIA H100 in confidential mode) in July 2026 and the
machines are torn down. Total build spend was about 2.6 GPU-hours, roughly $20 to $25.

## What this demonstrates

A working chain by which an off-machine verifier checks that the code, configuration, and model a machine
serves are the ones declared and approved, while the machine discloses almost nothing: three hardware roots
(AMD SEV-SNP via Microsoft Azure Attestation, a vTPM quote over the register that logs every executed file,
NVIDIA's GPU attestation) bound to one disclosure hash and verified 20/20 off-machine on an accept case and a
reject case.

The claim the mechanism supports, stated exactly: **no undeclared executable was executed in the attested
domain, under the measurement policy in force.** Code imported as data by a declared program stays uncovered
unless the policy widens (measured cost: roughly a thousand extra measured entries per real catch), and the
strongest negative result is deliberate: a declared, approved process pushed about 3.1 PFLOP of undeclared GPU
work through an accepting verdict, because attestation answers what ran, not how much ran.

## Layout

- `gpu/` — the GPU phase: attestation binding (tier 0), occupancy bound (tier 0.5), completeness five-case
  (tier 1), in-enclave reviewer (tier 2), tamper reject (tier 3a), re-execution appendix. **Start with
  `gpu/README.md` for results and `gpu/REPRODUCE.md` for one documented command per headline result.**
- `milestone1/` — the CPU enclave attestation chain (SEV-SNP, MAA token, disclosure discipline), 9/9 checks.
- `milestone3/` and `milestone3-kettle-gate/` — the xz-style two-part demo: deterministic source review plus
  build attestation (built on Kettle); each layer alone misses half the attack, composed they catch both.
- `milestone0/` — the re-execution divergence harness (appendix material).
- `EVIDENCE/` — the run log (`RUN-LOG.md`) and sanitized Azure activity-log exports for both build phases
  (CPU: `azure-activity-log.json`, 2026-07-26 to 27; GPU: `azure-activity-log-gpu-phase.json`, 2026-07-30 to
  31, provisioning through teardown).

## What is and is not reproducible

Every headline verdict reruns from the commands in `gpu/REPRODUCE.md` given an Azure subscription with the
GPU quota, the CLI, an SSH key and two Python packages. Not reproducible: exact throughput digits (thermal),
Azure's in-guest agent behaviour (image-dependent), the NVIDIA remote attestation path after its SDK leaves
support on 2026-09-15, and anything about a motivated adversary; the operator here was cooperative by
construction.
