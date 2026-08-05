# M3 COMPLETE: the xz two-part demonstration, confirmed on real SEV-SNP hardware (2026-07-27)

The concreteness contribution, demonstrated end to end: an xz-class supply-chain attack is caught only because two
cited layers compose, and neither layer alone catches it. This composes source review with artifact attestation, completing source-hash certification
by binding it to the executed artifact, shown in the domestic frame.

Design and rationale: `DESIGN.md`. Layers, both cited, not reinvented:
- review  = deterministic source review (`review_scan.py`; the AI-reviewer variant 1c.2 is cited to Agentic Witnessing)
- attest  = Kettle build+attest+verify (`confidential-dot-ai/kettle`, MIT), source -> artifact binding

## Results (real Azure SEV-SNP CVM, Kettle `az-snp`, 2026-07-27)

| Case | Kettle attest+verify | Deterministic review | Disclosure verdict | Caught by |
|---|---|---|---|---|
| clean | PASS | PASS (accept) | **accept** | - |
| Attack A: payload in committed source | **PASS** (attests it faithfully) | **REJECT** (rule R1) | **reject** | review (attestation missed it) |
| Attack B: build glue not in git | **FAIL** (checksum mismatch) | PASS (accept) | **reject** | attestation (review missed it) |

Attack B detail, on hardware: clean git attested -> artifact sha `383777...`, verify PASS. Poisoned distribution
(uncommitted `build.rs` glue added, rebuilt) -> artifact sha `800883...`, runs print `(release)` vs `(backdoor-active)`.
`kettle verify` of the distributed poisoned artifact against the clean provenance -> **Verification FAILED**. Review of
the committed source (no `build.rs` there) -> ACCEPT.

**Demonstrated:** neither layer alone catches both A and B; composed, they catch both. The disclosure emits only the
verdict, the attested provenance digest, and which layer objected; it withholds source, weights, customer data, and the
artifact (allowlist enforced, same discipline as M1).

Evidence artifacts: `evidence/` (clean provenance + hardware-signed evidence.json; attack-A provenance).

## What this is and is not (honest)

- IS: the first specific, running demonstration of the CC-draft's unclosed source-to-binary gap, closed by composing
  two cited layers, in the domestic disclosure discipline. Concreteness, not a new mechanism.
- IS NOT: novel primitives. Kettle (artifact attestation) and Agentic Witnessing (AI review) are cited. The review
  here is a minimal deterministic check that catches KNOWN patterns only, not novel obfuscation (can/can't-map limit).
- Attack A's "payload" is an illustrative suspicious pattern, not working malware.

## Reproduce (the one paid run, ~cents on free credit)

Provision a SEV-SNP CVM (M1 recipe). Install Kettle for attest: `apt-get install libtss2-dev build-essential
pkg-config acl`, rustup, `cargo install --features attest --git https://github.com/confidential-dot-ai/kettle kettle`,
add user to `tss` + `setfacl -m u:$USER:rw /dev/tpm0`. Per fixture: `cargo generate-lockfile`, `git init && commit`,
`kettle attest -n <nonce> .`, `kettle verify kettle-build`, `python3 disclose.py --source src --kettle-build
kettle-build`. For B, after the clean attest, copy `GLUE_build.rs.uncommitted -> build.rs`, `cargo build --release`,
overwrite `kettle-build/artifacts/greeter` with the poisoned binary, re-verify.

## Friction added this run (feasibility log)
9. `kettle` needs `Cargo.lock` present to detect the Cargo toolchain; `cargo new` / a fresh checkout without a build
   lacks it. Run `cargo generate-lockfile` (and commit it) before `kettle attest`.

## Status
- [x] deterministic review step built + tested
- [x] disclosure wrapper (compose + withhold) built + tested
- [x] fixtures (clean, attack A, attack B)
- [x] all three cases confirmed on real SEV-SNP hardware
- [x] VM torn down
- [ ] (optional, later) LLM-reviewer variant, GPU-CC binding
