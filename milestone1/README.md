# Milestone 1: VM + verified attestation quote, emitting the regulator-facing disclosure

The base rung of the build ladder. Done = one MAA-verified SEV-SNP attestation quote, end to end, no ML.

What M1 establishes for the paper: a regulator who does not own the machine, does not trust the
operator, and is not on the premises learns that the approved code, and no other code, ran inside
hardware-verified isolation, while the operator's weights and customer data never leave the
enclave. Everything later in the ladder (timing, re-execution, code review) hangs off this link.

## The disclosure discipline (build requirement from M1 onward)

The PoC's mechanics are setting-neutral: SEV-SNP attestation works the same anywhere. What makes
it a demo of the *domestic scheme* rather than a generic TEE demo is what it emits and what it
withholds. So every rung emits a disclosure carrying only:

| Field | Why the regulator gets it |
|---|---|
| `verdict` | accept/reject, the regulator-facing answer |
| `code_sha256` | what actually ran, checkable against the approved declaration |
| `declared_code_sha256`, `code_matches_declaration` | the comparison, shown not asserted |
| `output_commitment` | pins the run's output so it can be opened under legal compulsion |
| `schema`, `commitment_scheme` | so the regulator can parse and audit the format |

Withheld, and never copied off the machine: `secret_input.json` (stands in for weights and
customer data), the workload's output, and the commitment salt.

The allowlist is enforced twice on purpose. `enclave_job.py` refuses to write a disclosure with
non-allowlisted fields, so a later edit cannot quietly widen it; `verify_token.py` enforces its
own copy, because the regulator should not rely on the operator's enforcement.

Honest caveat on the commitment: sha256(salt||output) with the salt kept inside is a pinning
device, not a confidentiality guarantee. If the salt ever leaks, a low-entropy output is
brute-forceable.

## What M1 does not establish

That any *AI* computation happened inside the boundary. An SEV-SNP report covers the CPU package.
Work offloaded to a GPU sits outside it until a GPU-CC attestation covers it too, and the
CPU milestones do not bind the accelerator. `verify_token.py` prints this on every run.
CPU-first is the build order, not the scope; the GPU phase in `../gpu/` closes it.

## Files

| File | Where it runs | What it does |
|---|---|---|
| `provision.ps1` | Windows | picks a region with `Standard_DC2as_v5`, creates the SEV-SNP CVM |
| `vm-setup.sh` | the CVM | installs `azguestattestation1`, builds the sample `AttestationClient` |
| `declared_code.json` | both | the declaration: the code hash the regulator approved in advance |
| `workload.py` | the CVM | the declared program, trivial, reads the secret input |
| `secret_input.json` | the CVM only | weights / customer-data stand-in, never leaves |
| `enclave_job.py` | the CVM | hashes the code, runs it if approved, emits `disclosure.json` |
| `attest.sh` | the CVM | requests an MAA token with nonce = sha256(disclosure) |
| `verify_token.py` | Windows (regulator) | 8 checks: hardware, binding, verdict, code match, minimality |

The nonce binds the *disclosure*, not the run record, so the operator cannot attest one artifact
and hand over another.

## Runbook

```powershell
az login                                   # interactive, do this yourself
az account show                            # confirm the right subscription
.\provision.ps1                            # creates ccverify-m1-rg, ~$0.09/hr
```

```bash
IP=$(cat vm-ip.txt)
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'mkdir -p ~/m1'
scp -i ~/.ssh/ccverify_m1 vm-setup.sh attest.sh azureuser@$IP:~/
scp -i ~/.ssh/ccverify_m1 workload.py enclave_job.py declared_code.json secret_input.json \
    azureuser@$IP:~/m1/
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP 'chmod +x *.sh && ./vm-setup.sh'
ssh -i ~/.ssh/ccverify_m1 azureuser@$IP './attest.sh eastus2'
mkdir -p out && scp -i ~/.ssh/ccverify_m1 azureuser@$IP:~/m1-out/* ./out/
```

That `scp` fetches `~/m1-out` only, which is exactly the regulator's view. The secret input and
the workload output live in `~/m1` and are never fetched.

```powershell
python verify_token.py --token out\maa-token.jwt --disclosure out\disclosure.json `
                       --declared declared_code.json --dump-claims
```

## Teardown (do not leave it running)

```powershell
az group delete --name ccverify-m1-rg --yes --no-wait
```

## Status: M1 COMPLETE (2026-07-26)

- [x] build assets written
- [x] disclosure path tested locally: accept on approved code, reject on an undeclared edit
- [x] `az login` + subscription confirmed (ebadc6ba..., "Azure subscription 1")
- [x] VM provisioned: `Standard_DC2as_v5`, eastus, zone 2, ConfidentialVM + vTPM + secure boot
- [x] attestation client built (azguestattestation1 1.1.2 + Microsoft sample AttestationClient, clean build)
- [x] token obtained (7198 B) and bound to the disclosure (nonce = sha256(disclosure.json))
- [x] **disclosure verified off-machine: 9/9 checks pass** on a real AMD Milan SEV-SNP report
- [x] VM torn down (resource group deleted)

Verified evidence: AMD SEV-SNP, chip family Milan, `x-ms-compliance-status=azure-compliant-cvm`,
`is-debuggable=false`, launch measurement `5b0ce64a...`, nonce peels to the disclosure hash `64276bb5...`.

## Integration friction log (what building surfaced that desk research does not)

1. **Fresh subscription has all resource providers unregistered.** `Microsoft.Compute/Network/Attestation/Storage`
   were `NotRegistered`, so every quota/usage query returned empty until registered (~2-3 min async). A first-time
   regulator hits this. Not documented in the obvious CVM quickstarts.
2. **Regional SKU availability, not quota, was the real gate.** `Standard_DC2as_v5` is not offered to this
   subscription in eastus2 (0 SKUs) despite a quota line existing there. Had to scan regions; usable US regions were
   eastus and westus. Default-region tutorials would have failed opaquely.
3. **Azure CVM tokens nest the SEV-SNP evidence under `x-ms-isolation-tee`;** the top-level `x-ms-attestation-type`
   is a generic `azurevm` envelope. A verifier reading top-level claims sees "azurevm" and wrongly rejects. Real
   SEV-SNP type/compliance/debuggable live in the nested TEE claim.
4. **The client nonce round-trips double-base64-encoded** through the HCL/MAA path. Binding is intact but the verifier
   must peel base64 layers to recover the hex it committed. (`attest.sh` base64s the hash before passing it; the
   verifier now peels, so it is robust either way.)
5. **No `/dev/sev-guest`;** Azure CVMs attest via the vTPM path (the guest attestation library), not the direct SNP
   guest device. The sample client uses the vTPM path, which is correct here.

These are cheap-to-fix but real, and each is a data point for the "doable within months" honesty and the paper's
feasibility section. Substrate note: M3 will use Kettle's native AMD-root attestation, not this MAA path, so
`verify_token.py` is M1-specific; the friction findings 1-2 and 5 carry forward regardless.
