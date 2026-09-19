# Binding the receipt to the hardware, and judging weights and settings from the log (2026-09-14)

An external review of the write-up asked what pins the kernel and policy that write PCR 10. Checking that
question in the code found two worse problems, both fixed here and both demonstrated before and after.

## What was wrong

1. **The quote was not bound to the attested vTPM.** The ceremony created its own attestation key with
   `tpm2_createak` and quoted with it. The verifier checked the quote against whatever public key the bundle
   named, and checked the platform token (MAA) only for attestation type, compliance and nonce. The token
   carries the vTPM's own attestation key as `HCLAkPub`; the ceremony's key was never compared to it. On the
   July GPU accept bundle the quote key matches none of the three keys in the token. A software key signs
   anything, including a structure that starts with `TPM_GENERATED_VALUE`, and a machine the operator controls
   will request platform and GPU tokens over any disclosure hash it is given.
2. **Weights and configuration were judged on the operator's side.** The checker hashed the declared files at
   their declared paths and wrote the answer into the disclosure; the verifier read that field. The
   declared stack ran as an account whose reads were not measured, so the weight file never appeared in the
   log, and the July tampered case was rejected only on the checker's word.

## The forgery, before the fix

`gpu/forge_quote_demo.py` takes a genuine reject bundle, deletes every undeclared entry from the exported log,
replays the edited window, splices the regulator's challenge and the new digest into the real quote structure,
signs it with a fresh 2048-bit software RSA key and writes an accept disclosure naming that key.

| bundle | verifier | result |
|---|---|---|
| July GPU undeclared-exec, forged (`forged-undeclared-exec/`) | pre-fix verifier | **14/14, accept** (`forged-vs-old-verifier.txt`) |
| same forged bundle | fixed verifier | fails: no platform token ties the key to hardware, the quote does not cover the boot registers, the declared weights never appear in the log (`forged-vs-new-verifier.txt`) |
| genuine July GPU accept bundle | fixed verifier | fails the same three bindings: a different key signed the quote (`july-accept-vs-new-verifier.txt`) |
| TDX dev box undeclared-exec, forged, with a **genuine** MAA token requested over the forged disclosure's hash (`forged-tdx-with-genuine-token/`) | fixed verifier | **17/18: only the key-binding check stops it** (`forged-tdx-vs-new-verifier.txt`) |

The last row isolates the check that matters. Everything else a forger controls can be made consistent.

## The fix

- `gpu/tier1.sh` quotes with the vTPM's persistent attestation key (`0x81000003`, restricted signing key, the
  key MAA names as `HCLAkPub`) over PCRs 0 to 10, records the PCR 0 to 9 values, archives the raw HCL report
  (NV index `0x01400001`) and the AK certificate (`0x01C101D0`) beside the tokens, and runs the declared stack
  as a dedicated serving account.
- The IMA policy adds `measure func=FILE_CHECK mask=^MAY_READ uid=<serving uid>`, and keeps proc, sysfs,
  securityfs, cgroup and efivarfs reads out of FILE_CHECK only, so program launches from in-memory files are
  still measured. The exact policy is saved with each run as `ima-policy-in-force.txt`.
- `gpu/lib_verdict.py` defines the verdict once. The declared weight and configuration digests must appear in
  the attested window; any other file the serving account read is an undeclared digest.
- `gpu/verify_completeness.py` adds: quote key equals MAA's `HCLAkPub`; MAA's attested PCR 0 to 7 values equal
  the quoted ones; the quoted boot registers equal the regulator's reference values; the log's boot_aggregate
  equals sha256 over the quoted PCRs 0 to 9 (confirmed on hardware: PCRs 0 to 7 alone do not match); secure
  boot on and debugging off; the verdict recomputed off-box and compared with the disclosure's.

## Runs

**TDX dev box** (`tdx-dev-box/`): `Standard_DC2es_v6`, Intel TDX confidential VM, West US, kernel
6.8.0-1064-azure-fde, no GPU. Used because this subscription has no AMD confidential CPU quota. Baseline 222
entries, declaration 50 digests. All five cases **17/17** with the expected verdict.

**H100 box, run 1** (`h100-run1/`): `Standard_NCC40ads_H100_v5`, AMD SEV-SNP, East US 2, H100 NVL, driver
595.71.05, kernel 6.8.0-1064-azure-fde, torch 2.13.0+cu130, NRAS v4 through `nv-attestation-sdk` 2.7.3.
Baseline 1,186 entries (1,058 distinct digests); declaration 915 digests, almost all of them the serving
account's reads of the Python and torch libraries. accept, tampered, config-changed, undeclared-import and
undeclared-exec: **23/23** each, with the expected verdict. The undeclared-GPU case rejected, and not because
of the GPU work: between the accept case and that run the provider's in-guest security agent read a changed
copy of its own configuration file (`/etc/azsec/auoms_desired_state.json`), which lands in every later window.
The GPU work itself (23,769 multiplications of two 4096-square bf16 matrices in a 9.9 second run) added no
undeclared entry. Each later reject case's own entry is visible in its verifier output.

**H100 box, run 2** (`h100-run2/`): same machine, rebooted, a fresh policy load, and only two cases, the
undeclared GPU work first and accept second. Baseline 766 entries (719 distinct digests); declaration 943
digests. **Both 23/23 and both accept.** The undeclared-GPU case ran 22,089 hidden multiplications of two
4096-square bf16 matrices, 137,438,953,472 operations each, so about 3.0 quadrillion operations in a 9.9
second run (about 300 trillion a second averaged over the run), and the accept case after it covered exactly
the same 1,725-entry window: the hidden work left no trace in the measurement at all.

The five-probe adversarial pass (`h100-run2/adversarial-newpolicy.json`) was rerun under the new policy after
both cases were quoted: execution of a binary that exists only in memory (memfd) caught, an edit to the
approved program after approval caught, undeclared code run as another user caught, code written into
anonymous memory and jumped to not caught. The fifth probe, trimming the log, errored because the probe script
assumes a PCR-10-only quote; the forgery above demonstrates the same property against the fixed verifier.

Environment (`h100-env.txt`): H100 NVL, driver 595.71.05, VBIOS 96.00.88.00.11, CC on, DevTools off, torch
2.13.0+cu130, `nv-attestation-sdk` 2.7.3 (NRAS v4 endpoint), Ubuntu 22.04.5, kernel 6.8.0-1064-azure-fde. The
vTPM key at `0x81000003` reports `fixedtpm|fixedparent|sensitivedataorigin|userwithauth|noda|restricted|sign`.

## Reproduce

```bash
./tier1_sequence.sh eastus2 300                                   # on the box; CCV_GPU=1 CCV_VENV=~/venv on the H100
CCV_CASES="undeclared-gpu --undeclared-gpu|accept" ./tier1_sequence.sh eastus2 300   # the clean pair, after a reboot
CCV_GPU=1 bash verify_all.sh <ip> <evidence-dir> sevsnpvm         # off the box, while the tokens are fresh
python forge_quote_demo.py --bundle <reject-bundle> --approved <approved-dir> --out <dir>
```

Cost of this session: about 25 minutes of one H100 box (roughly $3 to $4) and under an hour of a small TDX
box (about 10 cents). The platform token lasts eight hours and the GPU token one hour, so the verifier runs
straight after the pull; the raw HCL report and AK certificate are archived beside them.

## What this still does not establish

- **Which IMA policy was loaded.** It is written once after boot and that write is not measured; the kernel
  command line sits inside a distribution-signed kernel image. A company that loads a narrower policy gets a
  genuine, correctly signed receipt that accepts.
- **Work an already-declared process submits to the accelerator**, which run 2 shows directly.
- **Code that never arrives as a file**, which the anonymous-memory probe shows.
- **A live cloud host's own agents** can put an undeclared digest into the window at any time, as run 1 shows;
  a real deployment has to either declare the provider's agent configuration or quiesce it.

## Two more holes, found by a cold review of the fixed verifier, and run 3

Two fresh reviewers were given the fixed verifier and told to break it. Both succeeded, in ways the hardware
runs above could not have shown.

1. **The platform issuer was taken from the token itself.** `check_maa` read the `iss` claim and fetched the
   signing keys from that URL. An operator can sign its own token, publish a matching key set at its own
   address, and put a **software key in `HCLAkPub`**, which is the one claim the whole chain hangs on. A
   reviewer demonstrated a bundle assembled with no confidential hardware at all: **17/17, accept**. The GPU
   path had always pinned NVIDIA's issuer; the platform path had not. Fixed: the issuer must be one on the
   verifier's own allowlist of Microsoft-operated shared attestation endpoints (`TRUSTED_MAA_ISSUERS`),
   checked before any key is fetched, and the token algorithm must be asymmetric. A pattern match on
   `*.attest.azure.net` is deliberately not used, since any Azure customer can create a provider with that
   suffix and a custom policy; a self-issued token, or a token from a customer-created provider, now fails
   that check offline. A production regulator would instead pin its own dedicated provider by policy hash.
   The full reproduction of this "no confidential hardware" forgery is retained in
   `forged-issuer-nohardware/` (a 127.0.0.1 self-issued software key: pre-pin verifier 17/17 accept, current
   verifier rejects at the issuer check); see its README.
2. **Entries IMA could not measure were treated as approved.** When a file is written while it is being read,
   IMA records a **violation**: an all-zero digest in the log and all-ones into the register, whatever the file
   held. The all-zero digest reached the approved sets, and since every violation produces that same digest,
   approving one approved every unmeasurable file. A reviewer rewrote an existing violation entry's path to
   `/root/undeclared_miner.py` without touching the register (a violation's extend value does not depend on its
   contents) and the verifier still returned **accept, 23/23**. Fixed: a violation is now its own rejection
   reason (`measurement_violation`), and `capture_set.py` never puts the all-zero digest into a baseline or a
   declaration.

Fixing (2) rejected the earlier accept runs, for a reason that had nothing to do with the workload: the
provider's own agents read `/var/log/auth.log`, `/var/log/syslog` and a waagent results file while they were
being written, three or four violations per boot, all through the administrator-read rule inherited from the
kernel's `tcb` table. **That rule is now dropped.** The scheme's coverage comes from the serving account's
reads instead, and the cost is stated: a file read by the administrator rather than by the serving account is
no longer covered.

**H100 box, run 3** (`h100-run3/`), the current results: same instance type, driver 595.71.05, VBIOS
96.00.9F.00.04, kernel 6.8.0-1064-azure-fde, policy in `h100-env-and-policy.txt` (11 rules, no
administrator-read rule). Baseline 270 entries / 269 digests; declaration 1,045 digests. **All six cases
24/24 with the expected verdict, in one boot, no violations:** undeclared-GPU accept (21,739 hidden
4096-square bf16 matmuls, about 3 PFLOP in 9.9 s), clean accept over the same 1,315-entry window, then
tampered, config-changed, undeclared-import and undeclared-exec. Because the log only grows in a boot, each
of those later windows carries the earlier changes' fingerprints too; each case adds exactly one of its own,
which is the fingerprint named in its verifier output. Probes rerun under this policy: memfd exec, post-approval edit and other-user caught; JIT into
anonymous memory not; the trim probe still assumes a PCR-10-only quote. About 17 minutes of H100 time, $2 to
$3.

**A precondition the code cannot enforce**, raised by the same review: the challenge must be the regulator's
own and unpredictable to the operator. The ceremony writes one into the bundle for convenience, and a
regulator that feeds that file back in gets no freshness, since the operator chose it.
