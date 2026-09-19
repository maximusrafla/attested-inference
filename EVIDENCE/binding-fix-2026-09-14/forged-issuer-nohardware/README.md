# The platform-issuer forgery, reproduced with no confidential hardware

Before commit `accc0ef`, `verify_completeness.py` read the issuer out of the platform (MAA) token and
fetched the signing keys from that address, so whoever wrote the token also chose the keys it was checked
against. A company can sign its own token, serve the matching key set itself, and put a software key it
controls in `HCLAkPub`, which is the one claim that ties the quote key to the vTPM. Every other binding then
lines up with a forged quote. A bundle assembled this way was found to pass the pre-pin verifier 17/17, but
only an 89-byte stub token was retained from that check. This folder is the full, reproducible artifact.

Nothing here touches confidential hardware. Every key and token is software-generated off the box.

## What was done

1. **Forged bundle** (`bundle/`). `gpu/forge_quote_demo.py` was run on the genuine run-3 reject bundle
   `h100-run3/undeclared-exec`: it deletes the four undeclared entries (`weights.tampered.bin`,
   `config.json`, `undeclared_module.py`, `undeclared_exec.sh`), replays the log, splices the regulator's
   challenge and the new register value into the real quote structure, signs it with a fresh 2048-bit
   software RSA key, and writes an accept disclosure. The window returns to 1315 entries, 0 undeclared. A
   forger needs the approved image's boot register values anyway, since they must equal the regulator's
   reference values, and any genuine run of that image supplies them.

2. **Self-issued platform token** (`maa-token.jwt`, keys in `jwks.json`). Built on the claim shape of the
   genuine run-3 token, with `iss` set to `http://127.0.0.1:8731`, `HCLAkPub` set to the forged bundle's
   software key, the attested PCRs 0 to 7 set to the forged bundle's boot values, and the nonce set to
   sha256 of the forged disclosure. It is signed RS256 with a second software key, whose public half is
   `jwks.json`, served at `/certs`. Neither signing key was retained.

## The two runs

| Verifier | How it was run | Result | Output |
|---|---|---|---|
| Pre-pin, from git `accc0ef` (no issuer pin at all) | with the self-issued token, no `--nras-token`, `--reference-pcrs` from `h100-run3/approved` | **17/17, ACCEPT** | `verify-prepin-ACCEPT.txt` |
| Current (working tree, `TRUSTED_MAA_ISSUERS` allowlist) | same bundle and token | **REJECT, 10/11**, failing only `platform token was issued by Microsoft's attestation service (http://127.0.0.1:8731)` | `verify-current-REJECT.txt` |

The pre-pin accept above is that forgery reproduced with no confidential hardware anywhere. The current verifier stops
it at the issuer check, before any key is fetched.

## The customer-created-provider variant (stated, not run end to end)

A token from a real Azure customer-created provider at `<name>.<region>.attest.azure.net` passes the
intermediate `d7e8052` regex but is rejected by the current allowlist. It cannot be run end to end off the
box, because there is no way to serve JWKS at a real `*.attest.azure.net` host one does not own. Its crux is
shown offline in `issuer-check-regex-vs-allowlist.txt`: the `d7e8052` regex accepts
`https://evil.eus2.attest.azure.net`, the current allowlist rejects it, and only a genuine shared endpoint
passes both.

## Files

- `bundle/` the forged SEV-SNP bundle (quote, signature, software AK, edited log, accept disclosure, boot PCRs, challenge)
- `maa-token.jwt` the self-issued platform token; `jwks.json` its signing key set (served at `/certs`)
- `verify-prepin-ACCEPT.txt`, `verify-current-REJECT.txt` the two runs above
- `issuer-check-regex-vs-allowlist.txt` the offline regex-versus-allowlist crux

## Reproduce

The pre-pin verifier must come from git, since the working tree is the fixed one:

```
git -C poc show accc0ef:gpu/verify_completeness.py > /tmp/prepin/verify_completeness.py
git -C poc show accc0ef:gpu/lib_ima.py   > /tmp/prepin/lib_ima.py
git -C poc show accc0ef:gpu/lib_tpm.py   > /tmp/prepin/lib_tpm.py
git -C poc show accc0ef:gpu/lib_verdict.py > /tmp/prepin/lib_verdict.py
```

Serve the retained key set (the pre-pin verifier fetches it; the current verifier rejects before fetching,
so it needs no server):

```
python - <<'PY'
import http.server, json
from pathlib import Path
jwks = Path("jwks.json").read_text()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(jwks.encode())
    def log_message(self,*a): pass
http.server.HTTPServer(("127.0.0.1", 8731), H).serve_forever()
PY
```

Then, from `poc/gpu`, run each verifier with `--disclosure ../<this>/bundle/disclosure.json` and the rest of
the bundle, `--maa-token ../<this>/maa-token.jwt`, `--reference-pcrs
../EVIDENCE/binding-fix-2026-09-14/h100-run3/approved/reference-pcrs.json`, `--platform sevsnpvm`, no
`--nras-token`. The retained token's `exp` is far in the future, so no `--allow-expired-tokens` is needed;
rebuilding from scratch would regenerate every key, so the bundle and token change byte for byte while the
two verdicts must not.

## Provenance note

A truncated, unparseable `build_and_verify.py` was found in this folder during the build, origin
undetermined (it is untracked, in no commit, was never run, and predates this build's writes by about a
minute). It was moved to the session scratchpad rather than deleted, in case its origin matters later. The
files above were produced by hand in this session, off the box, and independently re-verified by a second
session.
