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
| TDX dev box undeclared-exec, forged, with a **genuine** MAA token requested over the forged disclosure's hash (`forged-tdx-with-genuine-token/`) | fixed verifier | **16/17: only the key-binding check stops it** (`forged-tdx-vs-new-verifier.txt`) |

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
