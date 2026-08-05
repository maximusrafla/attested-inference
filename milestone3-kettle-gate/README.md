# M3 gate: does Kettle attest on our Azure SEV-SNP substrate? YES (2026-07-26)

The one big unknown before M3 was whether Kettle (the artifact-attestation layer we decided to build on rather
than reinvent) runs on Azure confidential VMs at all, given Azure uses the vTPM path with no `/dev/sev-guest`.
Answer: yes, cleanly. Kettle has native `az-snp` platform support.

## What ran

On a fresh `Standard_DC2as_v5` SEV-SNP CVM (eastus), on a trivial Cargo project:

- `kettle build .` -> `provenance.json` (SLSA v1.2 in-toto: git commit, git tree, lockfile hash, pinned cargo +
  rustc + kettle toolchain digests, build command `cargo build --locked --release`).
- `kettle attest -n <16B hex> .` -> `provenance.json` + `evidence.json` (7.8 KB). Log: "Running on platform:
  **az-snp**", "Attestation complete". So Kettle detects and uses the Azure SEV-SNP TEE.
- `kettle verify kettle-build` -> all pass: Attestation hardware signature valid; Provenance is valid SLSA v1.2;
  Provenance checksum match; Checksum match for binary `artifacts/hello`; **Verification PASSED**.

Artifacts saved here: `provenance.json`, `evidence.json`.

## Why this matters for the plan

This is the artifact-attestation layer (source -> built binary binding) working on our substrate, built on Kettle,
MIT-licensed, not reinvented. It completes source-hash certification by binding the reviewed source to the executed
artifact (the xz failure mode). The `-n` nonce (up to 16 bytes hex) is the disclosure-binding hook already built in.

What is NOT done yet (the rest of M3, next session):
- the **source-review step** Kettle scopes out (mechanism 1c), the layer that catches an in-source payload;
- the **xz demonstration**: show Kettle catches the build-glue/reproducibility discrepancy and the review step
  catches the in-source payload, neither alone catching both;
- the **domestic disclosure discipline** wrapped around it (accept/reject + provenance, weights/data withheld);
- Python toolchain: Kettle currently supports Cargo/pnpm/Nix (Python via uv is "planned"), so an AI workload in
  Python is not yet directly `kettle build`-able. The xz demo can use a supported toolchain (Rust) meanwhile.

## Friction log (integration reality, for the feasibility section)

6. **Prebuilt Kettle binary is build-only.** `kettle attest` errors "Attestation is disabled. Rebuild with
   `--features attest`." Must `cargo install --force --features attest` from source (~5.5 min compile on 2 vCPU).
7. **Source build needs `pkg-config`** (to link libtss2 via tss-esapi-sys); not pulled by `libtss2-dev` alone.
8. **TPM permissions.** Kettle opens `/dev/tpm0`, whose group is `root` (only `/dev/tpmrm0` is `tss`-group), so a
   normal user gets "Permission denied -> no TEE platform detected." Adding the user to `tss` is NOT enough. Fix:
   `setfacl -m u:<user>:rw /dev/tpm0` (needs the `acl` package). A regulator deployment would set a udev rule.

Findings 1, 2, 5 from M1 (provider registration, regional SKU availability, vTPM-not-sev-guest) carry forward.

## Status
- [x] Kettle installs and runs on Azure SEV-SNP CVM
- [x] build -> attest -> verify loop passes end to end (`az-snp`)
- [ ] source-review step (next)
- [ ] xz two-part demonstration (next)
- [ ] domestic disclosure wrapper (next)
- [x] VM torn down
