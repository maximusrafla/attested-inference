# M3: the xz two-part demonstration + domestic disclosure wrapper

Goal: show that our scheme catches an xz-class supply-chain attack only because two cited layers compose, and that
neither layer alone catches it. This is the concreteness contribution: completing source-hash certification by binding
it to the executed artifact, demonstrated in the domestic frame.

Standing discipline: build only what is necessary. The review step is the MINIMAL deterministic check, not an LLM
(the AI-reviewer mechanism is cited to Agentic Witnessing, mechanism 1c.2, and not rebuilt). The artifact-attestation
layer is Kettle (cited, MIT, verified working on our substrate in the M3 gate).

## The xz split (the whole point)

xz-utils was a TWO-part attack:
- **Part A, payload in source:** the malicious payload blobs lived in the git repo, hidden in test data files.
- **Part B, build glue in the tarball only:** the code that activated/extracted the payload was in the release
  tarball's build scripts, NOT in the git repo. Someone building from git got a clean binary; from the tarball, a
  backdoored one.

Which layer catches which part:

| Attack part | Kettle (source -> artifact attestation) | Deterministic review (reads the committed source) |
|---|---|---|
| A: payload committed in source | MISSES it. Kettle faithfully attests "this artifact came from this source"; a backdoor in the source is still the attested source, so `kettle verify` PASSES | CATCHES it. The payload pattern is in the source the reviewer scans |
| B: build glue not in git | CATCHES it. Kettle rebuilds from the committed git source and gets a CLEAN artifact; the distributed (poisoned) artifact's digest will not match Kettle's provenance, so `kettle verify` FAILS on checksum | MISSES it. The glue is not in the committed source, so a source reviewer sees nothing wrong |
| xz (A and B together) | catches only B | catches only A |

**Conclusion demonstrated:** neither layer alone catches xz; composed, they catch both. That is why the scheme
needs both source review AND artifact attestation: the artifact binding completes what a source hash alone
certifies.

## Fixtures (minimal Rust/Cargo, since Kettle supports Cargo; Python is Kettle-planned only)

- `fixtures/clean/` — a trivial Cargo binary that prints a version string. Baseline: review clean, attest+verify pass.
- Attack A (payload in source) — add a source file carrying an obfuscated blob + a decode call. Committed. Illustrative,
  not real malware; the point is the reviewer flags it. Kettle attest+verify still PASS (source matches artifact).
- Attack B (build glue) — a `build.rs` that injects a different string into the artifact. The clean git commit does
  NOT contain the glue; the "distributed" poisoned artifact is built WITH it. Kettle attests the clean git build;
  verifying the poisoned artifact fails the checksum. Source review of the clean commit sees nothing.

## The domestic disclosure wrapper

`disclose.py` runs the two layers and emits the regulator-facing disclosure only:
- runs the deterministic review over the declared source -> review_verdict (accept/reject + which rule fired)
- runs / consumes Kettle's attest+verify -> attest_verdict (pass/fail + provenance digest)
- overall verdict = accept only if BOTH pass
- emits: verdict, code provenance digest, review result, attestation result. WITHHELD: source contents, weights,
  customer data, the artifact itself. Same allowlist discipline as M1's `enclave_job.py`.

## What runs where
- Local, no cost: write fixtures, build+test the deterministic reviewer, test the composition logic and the
  checksum-mismatch catch for Part B (plain hashing, no TEE needed).
- One short Azure run: `kettle attest` the clean build on the SEV-SNP CVM, then show verify PASS on clean, FAIL on
  the Part B poisoned artifact, and the reviewer FAIL on Part A. Emit the disclosure. Tear down.

## Honest scope
- The reviewer is a deterministic pattern check. It catches KNOWN patterns, not novel obfuscation. State that limit;
  it is the can/can't map's review-layer row. The LLM variant (cited, not built) would catch more but is out of scope.
- Part A's "payload" is an illustrative suspicious pattern, not working malware.

## Status: local build complete and tested (no cost)

Built and verified locally, no VM:
- `review_scan.py` (deterministic review). Tested on all three fixtures: clean ACCEPT, attack_a REJECT (rule R1,
  in-source payload), attack_b ACCEPT (glue is uncommitted, so review correctly misses it).
- `disclose.py` (composition + domestic disclosure). Tested all three cases: clean -> accept; attack_a -> reject by
  review (attest would pass); attack_b -> reject by attestation (review would pass). Allowlist enforced, source/
  weights/data/artifact withheld.
- Fixtures: `fixtures/{clean, attack_a_insource, attack_b_buildglue}` (+ `GLUE_build.rs.uncommitted` for B).

The ONLY simulated part is the Kettle attest pass/fail (fed via `--attest` in the local test). The M3 gate already
proved Kettle attest+verify works on our substrate; the run below confirms the two specific outcomes.

## The one paid VM run (confirms two empirical claims)

On a fresh SEV-SNP CVM with Kettle (see `../milestone3-kettle-gate/README.md` for install: cargo install --features
attest, pkg-config, setfacl on /dev/tpm0). For each fixture: `git init`, commit, then:

1. **Clean:** `kettle attest -n <nonce> .`; `kettle verify kettle-build` -> PASS. `disclose.py --source src
   --kettle-build kettle-build` -> accept.
2. **Attack A (attest MISSES, expected):** attest the committed source (payload included); `kettle verify` -> PASS
   (artifact matches the committed source). `disclose.py` -> reject, by review. Confirms attestation does not catch a
   payload that is faithfully in the source.
3. **Attack B (attest CATCHES, expected):** attest the CLEAN git commit (no build.rs) -> provenance with D_clean.
   Then copy `GLUE_build.rs.uncommitted` -> `build.rs`, `cargo build --release` -> poisoned artifact D_evil. Point
   `kettle verify` at the poisoned artifact against the clean provenance -> FAIL on binary checksum. `disclose.py`
   -> reject, by attestation. Confirms attestation catches an artifact that does not match the committed source.

Claim to watch (the one real uncertainty): that `kettle verify` fails on B specifically at the binary-checksum step.
Mechanically expected from the gate result; the run confirms it. If Kettle's verify does not compare the distributed
artifact digest the way expected, adapt the check (compare provenance artifact digest vs sha256 of the poisoned
binary directly). Either way the split holds; only the tool-command detail may shift.
