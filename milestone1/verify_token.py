"""M1 verifier side: what a regulator can check without owning or trusting the machine.

The regulator holds two things: the declaration it approved earlier (a code hash), and what
the operator just handed over (a disclosure plus an attestation token). It checks:

  hardware and binding
  1. the token is signed by Microsoft Azure Attestation (JWKS, x5c chain),
  2. MAA says the platform is an AMD SEV-SNP confidential VM, not a plain VM,
  3. MAA says the platform passed its compliance checks (secure boot, vTPM, not debuggable),
  4. the token's nonce equals sha256 of THIS disclosure, so the attested thing and the
     handed-over thing are the same artifact,

  the domestic scheme's actual content
  5. the disclosure's verdict is accept,
  6. the code hash in the disclosure equals the hash the regulator approved,
  7. the disclosure carries nothing beyond the allowlist, so the operator's weights and
     customer data did not cross the boundary.

Checks 4 and 6 together are the point: the regulator learns that the approved code, and no
other code, ran inside hardware-verified isolation. Check 7 is the other half, the withholding.
A scheme that proves 6 by shipping the regulator everything is not the scheme.

Scope, stated on purpose: an SEV-SNP token covers the CPU package. It says nothing about an
accelerator. AI computation offloaded to a GPU runs outside this boundary until a separate
GPU-CC attestation covers it, so this checker names that as an explicit uncovered slot rather
than letting the omission pass silently. CPU-first is the build order, not the scope.

Usage: python verify_token.py --token out/maa-token.jwt --disclosure out/disclosure.json \
                              --declared declared_code.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import jwt
from jwt import PyJWKClient

# Must match ALLOWED_FIELDS in enclave_job.py. Duplicated on purpose: the regulator
# enforces its own copy rather than trusting the operator's.
ALLOWED_DISCLOSURE_FIELDS = {
    "schema",
    "verdict",
    "code_sha256",
    "declared_code_sha256",
    "code_matches_declaration",
    "output_commitment",
    "commitment_scheme",
}

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((PASS if ok else FAIL, name, detail))
    return ok


def _peel_base64(s, rounds=3):
    """Azure's guest-attestation path round-trips the client nonce base64-encoded,
    sometimes twice. Return every layer so a hex nonce underneath is recoverable."""
    import base64

    seen = {s}
    cur = s
    for _ in range(rounds):
        try:
            cur = base64.b64decode(cur + "=" * (-len(cur) % 4)).decode()
            seen.add(cur)
        except Exception:
            break
    return seen


def find_nonce(claims):
    """MAA/HCL put the client nonce under x-ms-runtime.client-payload, and encode it.
    Collect the raw values and every base64-peeled layer, so an expected hex nonce matches."""
    raw = []
    runtime = claims.get("x-ms-runtime") or {}
    payload = runtime.get("client-payload") or {}
    for key in ("nonce", "user-data", "userdata"):
        for src in (payload, runtime, claims):
            if isinstance(src, dict) and isinstance(src.get(key), str):
                raw.append(src[key])
    normalized = set()
    for r in raw:
        normalized |= _peel_base64(r)
    return normalized


def sevsnp_claims(claims):
    """Azure CVM tokens wrap the SEV-SNP evidence under x-ms-isolation-tee; the top
    level is a generic 'azurevm' envelope. Read the TEE view, fall back to top level."""
    return claims.get("x-ms-isolation-tee") or claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--disclosure", required=True)
    ap.add_argument("--declared", required=True, help="the code hash the regulator approved")
    ap.add_argument("--dump-claims", action="store_true")
    args = ap.parse_args()

    token = Path(args.token).read_text().strip()
    disclosure_bytes = Path(args.disclosure).read_bytes()
    disclosure = json.loads(disclosure_bytes)
    declared = json.loads(Path(args.declared).read_text())
    expected_nonce = hashlib.sha256(disclosure_bytes).hexdigest()

    unverified = jwt.decode(token, options={"verify_signature": False})
    issuer = unverified.get("iss", "")
    print(f"token issuer (unverified): {issuer}")

    # --- hardware and binding ---
    jwks_url = issuer.rstrip("/") + "/certs"
    signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
    claims = jwt.decode(token, signing_key.key, algorithms=["RS256"],
                        options={"verify_aud": False})
    check("token signature verifies against MAA JWKS", True, jwks_url)
    check("issuer is an attest.azure.net endpoint",
          issuer.startswith("https://") and issuer.endswith(".attest.azure.net"), issuer)

    tee = sevsnp_claims(claims)
    att_type = tee.get("x-ms-attestation-type")
    check("attestation type is sevsnpvm", att_type == "sevsnpvm", str(att_type))
    check("MAA compliance status is azure-compliant-cvm",
          tee.get("x-ms-compliance-status") == "azure-compliant-cvm",
          str(tee.get("x-ms-compliance-status")))
    debuggable = tee.get("x-ms-sevsnpvm-is-debuggable")
    check("SEV-SNP guest is not debuggable", debuggable is False, str(debuggable))

    nonces = find_nonce(claims)
    check("token nonce equals sha256 of this disclosure", expected_nonce in nonces,
          f"expected {expected_nonce[:16]}..., token carried {nonces}")

    # --- the domestic scheme's content ---
    check("disclosure verdict is accept", disclosure.get("verdict") == "accept",
          str(disclosure.get("verdict")))
    check("code that ran matches the approved declaration",
          disclosure.get("code_sha256") == declared.get("declared_sha256")
          and disclosure.get("code_sha256") is not None,
          f"ran {str(disclosure.get('code_sha256'))[:16]}..., "
          f"approved {str(declared.get('declared_sha256'))[:16]}...")

    extra = set(disclosure) - ALLOWED_DISCLOSURE_FIELDS
    check("disclosure carries no fields beyond the allowlist", not extra,
          f"unexpected fields: {sorted(extra)}" if extra else "weights and customer data withheld")

    print()
    for status, name, detail in results:
        print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))

    print("\nwhat the regulator now knows:")
    print("  - the approved code, and no other code, ran inside an AMD SEV-SNP enclave")
    print("  - one commitment pinning that run's output, openable under legal compulsion")
    print("what it does not know: the weights, the customer data, the output itself")

    print("\nnot covered by this evidence:")
    print("  - computation offloaded to an accelerator (GPU/TPU). An SEV-SNP report covers")
    print("    the CPU package only. Needs a separate GPU-CC attestation.")
    print("  - physical interposition by an operator with sustained access to the host.")

    if args.dump_claims:
        Path("claims.json").write_text(json.dumps(claims, indent=2, sort_keys=True))
        print("\nMAA platform claims written to claims.json")

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
