"""Tier 1, regulator side: check the completeness disclosure from a separate machine.

The regulator holds its own copies of the approved baseline, the approved declaration and, optionally,
reference values for the boot registers. It receives the disclosure, the quote and its signature, the
quoted boot register values, the exported measurement log, and the platform and accelerator tokens. It
recomputes the verdict itself. Nothing on this side depends on the operator's pre-check having run, and
nothing is taken from the disclosure except the claims it checks.

What gets checked:

  the quote, and that it came from the attested vTPM
  1. the quote is a TPM 2.0 quote structure answering the regulator's challenge,
  2. its signature verifies under the attestation key the bundle names,
  3. that key is the vTPM attestation key the platform token attests (MAA's HCLAkPub). Without this
     check a software key signs anything, including a structure that starts with TPM_GENERATED_VALUE;
     the magic value only restricts what a TPM restricted key will sign,
  4. the disclosure's hashes of the quote and the key match,

  the platform (MAA token)
  5. the token's issuer is one on the verifier's own list of Microsoft-operated shared endpoints (not a
     pattern accepted from the token, because any customer can create a *.attest.azure.net provider with
     a custom policy), it verifies against that endpoint's published keys under an asymmetric algorithm,
     and reports a compliant confidential VM,
  6. its nonce is sha256 of this disclosure,
  7. secure boot is on and boot and kernel debugging are off,
  8. the boot register values MAA attests (PCRs 0 to 7) equal the values the quote covers, and equal the
     regulator's reference values when those are supplied. On Azure's confidential Ubuntu image the kernel
     is a signed unified kernel image with its command line built in, so PCR 4 pins both,

  the measurement log
  9. some prefix of the exported log, together with the quoted boot registers, reproduces the quote's
     pcrDigest, so the log is the one the vTPM sealed and nothing was trimmed,
  10. the log's first entry, IMA's boot_aggregate, equals sha256 over the quoted PCRs 0 to 9, which ties
      the log to the boot the platform attested,
  11. the replayed PCR 10 value is the one in the disclosure,

  the verdict, recomputed here
  12. the verifier's own verdict over the window (every digest in the baseline or the declaration, the
      declared weights and configuration present by digest, and no entry IMA failed to measure) is the
      one expected, and
  13. it agrees with the disclosure's verdict, reasons and counts,

  the accelerator (NRAS token), and the content
  14 onward. the GPU token is NVIDIA-issued, verifies, is bound to this disclosure, reports matching
      measurements, secure boot and debug disabled; the declaration and baseline the operator used are the
      approved ones; the disclosure carries nothing beyond the allowlist.

History, stated because it matters. Before 2026-09-14 this verifier had neither check 3 nor checks 8,
10 and 12. The ceremony quoted with a key it created itself, so a bundle signed by a software key over a
log with the undeclared entry deleted passed every check (see forge_quote_demo.py). The weight and
configuration verdicts were read from the disclosure, which the operator's own code wrote.

A precondition the code cannot enforce: the challenge must be the regulator's own and unpredictable to
the operator. The ceremony writes one into the bundle for convenience, and a regulator that feeds that
file back in gets no freshness at all, since the operator chose it.

What the checks still cannot establish: which IMA policy was loaded. On this image the policy is written
once after boot, and that write is not measured, so the verdict is exactly as wide as the policy the
operator chose to load.

Usage:
  python verify_completeness.py --disclosure disclosure.json --quote quote.msg --signature quote.sig \
      --ak-pub ak.pub.pem --challenge <hex> --pcr-values boot-pcrs.json --ima-log ima.bin \
      --declaration declaration.json --baseline baseline.json --maa-token maa-token.jwt \
      [--nras-token nras-token.json] [--reference-pcrs reference-pcrs.json] [--platform sevsnpvm|tdxvm]
      [--allow-expired-tokens] [--expect accept|reject]
"""

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_ima import parse_binary_log  # noqa: E402
from lib_tpm import (TPM_GENERATED_VALUE, TPM_ST_ATTEST_QUOTE, ALG_RSASSA, ALG_RSAPSS,  # noqa: E402
                     ALG_ECDSA, parse_attest, parse_signature, selected_pcrs)
from lib_verdict import boot_aggregate_matches, find_window, judge  # noqa: E402

ALLOWED_DISCLOSURE_FIELDS = {
    "schema", "verdict", "declaration_sha256", "baseline_sha256", "pcr10_sha256",
    "ima_entries_checked", "undeclared_entries", "weights_sha256", "config_sha256",
    "weights_in_measured_window", "config_in_measured_window",
    "quote_sha256", "ak_pub_sha256", "rejected_by",
    # v3 fields, accepted so that pre-2026-09-14 bundles can still be checked and fail honestly
    "weights_match_declaration", "config_matches_declaration",
}

results = []


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, detail))
    return ok


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def b64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_quote_signature(quote_bytes, sig_blob, ak_pem):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, utils
    key = serialization.load_pem_public_key(ak_pem)
    sig_alg, _hash_alg, sig = parse_signature(sig_blob)
    try:
        if sig_alg == ALG_RSASSA:
            key.verify(sig, quote_bytes, padding.PKCS1v15(), hashes.SHA256())
        elif sig_alg == ALG_RSAPSS:
            key.verify(sig, quote_bytes,
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=padding.PSS.DIGEST_LENGTH),
                       hashes.SHA256())
        else:
            r, s = sig
            der = utils.encode_dss_signature(int.from_bytes(r, "big"), int.from_bytes(s, "big"))
            key.verify(der, quote_bytes, ec.ECDSA(hashes.SHA256()))
        return True, {ALG_RSASSA: "RSASSA", ALG_RSAPSS: "RSAPSS", ALG_ECDSA: "ECDSA"}[sig_alg]
    except Exception as e:
        return False, f"{type(e).__name__}"


def load_boot_values(path):
    """{index: 32 raw bytes} from the ceremony's boot-pcrs.json ({"sha256": {"0": "hex", ...}})."""
    if not path:
        return {}
    doc = json.loads(Path(path).read_text())
    bank = doc.get("sha256", doc)
    return {int(k): bytes.fromhex(v.lower().removeprefix("0x")) for k, v in bank.items()}


# The platform token says who issued it. Taking that at its word is fatal: an operator can stand up
# its own attestation provider, or a local key server, name it in the token, and put a software key in
# the HCLAkPub claim, which is the only thing tying the quote to hardware. Pinning by pattern to
# *.attest.azure.net is not enough: any Azure customer can create a provider at <name>.<region>.attest
# .azure.net and give it a custom policy, and whether such a policy can issue the isolation-tee claims
# as constants is not something this build establishes. So the issuer must be one of Microsoft's shared,
# Microsoft-operated endpoints, which run the fixed default policy and no customer can reconfigure; this
# is the verifier's own list, not a pattern read off the token. The shared endpoint also does the work
# that makes HCLAkPub trustworthy at all: it validates that the SNP report's report_data commits to the
# runtime block carrying that key. A production regulator would instead pin its own dedicated provider by
# its policy hash (x-ms-policy-hash); the four below are the regions this ceremony's bind step uses.
TRUSTED_MAA_ISSUERS = frozenset({
    "https://sharedeus2.eus2.attest.azure.net",   # East US 2
    "https://sharedeus.eus.attest.azure.net",     # East US
    "https://sharedwus.wus.attest.azure.net",     # West US
    "https://sharedcus.cus.attest.azure.net",     # Central US
})
ASYMMETRIC_ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]


def decode_jwt(token, issuer_certs, allow_expired, algs=None):
    import jwt
    from jwt import PyJWKClient
    header = json.loads(b64u(token.split(".")[0]))
    allowed = algs or ASYMMETRIC_ALGS
    if header.get("alg") not in allowed:
        raise ValueError(f"token algorithm {header.get('alg')} is not an allowed asymmetric algorithm")
    key = PyJWKClient(issuer_certs).get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=allowed,
                      options={"verify_aud": False, "verify_exp": not allow_expired})


def check_maa(token_path, disclosure_bytes, ak_pem, boot_values, reference, platform, allow_expired):
    from cryptography.hazmat.primitives import serialization
    token = Path(token_path).read_text().strip()
    issuer = json.loads(b64u(token.split(".")[1])).get("iss", "")
    if not check("platform token was issued by Microsoft's attestation service",
                 issuer.rstrip("/") in TRUSTED_MAA_ISSUERS, issuer or "no issuer in the token"):
        return
    try:
        claims = decode_jwt(token, issuer.rstrip("/") + "/certs", allow_expired)
    except Exception as e:
        check("MAA token verifies against the issuer's published keys", False, f"{type(e).__name__}: {e}")
        return
    check("MAA token verifies against the issuer's published keys", True,
          issuer + (" (expiry not enforced: archival check)" if allow_expired else ""))

    tee = claims.get("x-ms-isolation-tee") or {}
    check(f"MAA reports a compliant {platform} confidential VM",
          tee.get("x-ms-attestation-type") == platform
          and tee.get("x-ms-compliance-status") == "azure-compliant-cvm",
          f"{tee.get('x-ms-attestation-type')}, {tee.get('x-ms-compliance-status')}")

    expected = hashlib.sha256(disclosure_bytes).hexdigest()
    seen = set()
    for src in ((claims.get("x-ms-runtime") or {}).get("client-payload") or {}, claims.get("x-ms-runtime") or {}):
        for key in ("nonce", "user-data"):
            cur = src.get(key) if isinstance(src, dict) else None
            for _ in range(4):
                if not isinstance(cur, str):
                    break
                seen.add(cur.lower())
                try:
                    cur = base64.b64decode(cur + "=" * (-len(cur) % 4)).decode()
                except Exception:
                    break
    check("MAA nonce equals sha256 of this disclosure", expected in seen, f"expected {expected[:16]}...")

    runtime_keys = (tee.get("x-ms-runtime") or {}).get("keys") or []
    hcl = next((k for k in runtime_keys if k.get("kid") == "HCLAkPub"), None)
    ak = serialization.load_pem_public_key(ak_pem).public_numbers()
    bound = (hcl is not None
             and int.from_bytes(b64u(hcl["n"]), "big") == ak.n
             and int.from_bytes(b64u(hcl["e"]), "big") == ak.e)
    check("quote key is the vTPM attestation key the platform attests (HCLAkPub)", bound,
          "matches" if bound else ("no HCLAkPub in token" if hcl is None else "a different key signed the quote"))

    vmcfg = (tee.get("x-ms-runtime") or {}).get("vm-configuration") or {}
    check("secure boot on; boot and kernel debugging off",
          claims.get("secureboot") is True and vmcfg.get("secure-boot") is True
          and claims.get("x-ms-azurevm-bootdebug-enabled") is False
          and claims.get("x-ms-azurevm-kerneldebug-enabled") is False)

    attested = claims.get("x-ms-azurevm-attested-pcr-values") or {}
    att_vals = {int(k.removeprefix("pcr")): base64.b64decode(v) for k, v in attested.items()}
    same = bool(att_vals) and all(i in boot_values and boot_values[i] == v for i, v in att_vals.items())
    check("boot registers MAA attests equal the ones the quote covers",
          same, f"MAA attests PCRs {sorted(att_vals)}" if att_vals else "token carries no PCR values")
    # Compared against the quote-covered values, which equal MAA's for PCRs 0 to 7 (checked above)
    # and are bound to the quote's pcrDigest for all ten (checked by the log replay). Without them,
    # PCRs 8 and 9, where the kernel and its command line land, are pinned by nothing outside the
    # bundle, so a missing reference file is a failure rather than a skipped check.
    ref = load_boot_values(reference) if reference else {}
    check("quoted boot registers equal the regulator's reference values",
          bool(ref) and all(boot_values.get(i) == v for i, v in ref.items()),
          f"reference covers PCRs {sorted(ref)}" if ref else "no reference values supplied")


def check_gpu(nras_path, disclosure_bytes, allow_expired):
    """The accelerator root: an NVIDIA-signed attestation of this GPU, bound to this disclosure. The
    Azure CGPU image's shipped local verifier emits an HS256 token a regulator cannot verify, so only
    tokens issued by nras.attestation.nvidia.com are accepted."""
    bundle = json.loads(Path(nras_path).read_text())

    def find_jwts(o, acc):
        if isinstance(o, str) and o.strip()[:1] in ("[", "{"):
            try:
                return find_jwts(json.loads(o), acc)
            except ValueError:
                pass
        if isinstance(o, str) and o.count(".") == 2:
            acc.append(o)
        elif isinstance(o, list):
            for x in o:
                find_jwts(x, acc)
        elif isinstance(o, dict):
            for x in o.values():
                find_jwts(x, acc)
        return acc

    nras = []
    for t in find_jwts(bundle, []):
        try:
            if json.loads(b64u(t.split(".")[1])).get("iss", "").startswith("https://nras.attestation.nvidia.com"):
                nras.append(t)
        except Exception:
            pass
    check("bundle contains an NVIDIA-issued GPU attestation", bool(nras))
    if not nras:
        return
    device = None
    try:
        for t in nras:
            c = decode_jwt(t, "https://nras.attestation.nvidia.com/.well-known/jwks.json", allow_expired)
            if "hwmodel" in c:
                device = c
    except Exception as e:
        check("GPU attestation verifies against NVIDIA's published keys", False, f"{type(e).__name__}: {e}")
        return
    check("GPU attestation verifies against NVIDIA's published keys", device is not None)
    if device is None:
        return
    expected = hashlib.sha256(disclosure_bytes).hexdigest()
    check("GPU attestation nonce equals sha256 of this disclosure", device.get("eat_nonce") == expected)
    check("NVIDIA's verifier reports the GPU measurements matched reference values",
          device.get("measres") == "success", str(device.get("measres")))
    check("GPU reports secure boot on and debug disabled",
          device.get("secboot") is True and device.get("dbgstat") == "disabled")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosure", required=True)
    ap.add_argument("--quote", required=True)
    ap.add_argument("--signature", required=True)
    ap.add_argument("--ak-pub", required=True)
    ap.add_argument("--challenge", required=True, help="the hex challenge the regulator issued")
    ap.add_argument("--declaration", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--ima-log", required=True, help="the measurement log exported with the receipt")
    ap.add_argument("--pcr-values", help="boot-pcrs.json: the quoted PCR 0 to 9 values")
    ap.add_argument("--maa-token")
    ap.add_argument("--reference-pcrs", help="regulator-held reference values for the attested boot PCRs")
    ap.add_argument("--platform", default="sevsnpvm", choices=["sevsnpvm", "tdxvm"])
    ap.add_argument("--nras-token")
    ap.add_argument("--cc-mode", help="nvidia-smi conf-compute capture (on-box, the operator's word)")
    ap.add_argument("--allow-expired-tokens", action="store_true",
                    help="verify token signatures but not expiry, for re-checking archived bundles")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--expect", choices=["accept", "reject"], default="accept")
    args = ap.parse_args()

    disclosure_bytes = Path(args.disclosure).read_bytes()
    disclosure = json.loads(disclosure_bytes)
    quote_bytes = Path(args.quote).read_bytes()
    ak_pem = Path(args.ak_pub).read_bytes()
    att = parse_attest(quote_bytes)
    boot_values = load_boot_values(args.pcr_values)

    check("quote is a TPM 2.0 quote answering the regulator's challenge",
          att["magic"] == TPM_GENERATED_VALUE and att["type"] == TPM_ST_ATTEST_QUOTE
          and att["extra_data"].hex() == args.challenge.lower(),
          f"PCRs selected {selected_pcrs(att['selections'])}")
    ok, how = verify_quote_signature(quote_bytes, Path(args.signature).read_bytes(), ak_pem)
    check("quote signature verifies under the key the bundle names", ok, how)
    check("disclosure's quote and key hashes match",
          hashlib.sha256(quote_bytes).hexdigest() == disclosure.get("quote_sha256")
          and hashlib.sha256(ak_pem).hexdigest() == disclosure.get("ak_pub_sha256"))

    if args.maa_token:
        check_maa(args.maa_token, disclosure_bytes, ak_pem, boot_values, args.reference_pcrs,
                  args.platform, args.allow_expired_tokens)
    else:
        check("quote key is the vTPM attestation key the platform attests (HCLAkPub)", False,
              "no platform token supplied, so nothing ties the quote key to hardware")

    entries = parse_binary_log(Path(args.ima_log).read_bytes())
    covered, pcr10 = find_window(entries, att, boot_values)
    check("exported log and quoted boot registers reproduce the quote's pcrDigest", covered is not None,
          f"prefix {covered} of {len(entries)} entries" if covered is not None
          else "no prefix matches (log edited, or boot register values missing or wrong)")
    agg = boot_aggregate_matches(entries, boot_values)
    check("log's boot_aggregate equals sha256 over the quoted PCRs 0 to 9", agg is True,
          {None: "quote does not cover PCRs 0 to 9", False: "mismatch", True: "matches"}[agg])
    check("replayed PCR 10 value is the one in the disclosure", pcr10 is not None and pcr10 == disclosure.get("pcr10_sha256"))

    baseline = json.loads(Path(args.baseline).read_text())
    declaration = json.loads(Path(args.declaration).read_text())
    mine = judge(entries, covered, baseline, declaration)
    check(f"verifier's own verdict is {args.expect}", mine["verdict"] == args.expect,
          f"{mine['verdict']}, reasons {mine['rejected_by']}")
    check("disclosure agrees with the verifier's own verdict, reasons and counts",
          disclosure.get("verdict") == mine["verdict"]
          and sorted(disclosure.get("rejected_by") or []) == sorted(mine["rejected_by"])
          and disclosure.get("ima_entries_checked") == mine["ima_entries_checked"]
          and disclosure.get("undeclared_entries") == mine["undeclared_entries"],
          f"disclosure {disclosure.get('verdict')} {disclosure.get('rejected_by')} "
          f"{disclosure.get('ima_entries_checked')}/{disclosure.get('undeclared_entries')}; "
          f"verifier {mine['ima_entries_checked']}/{mine['undeclared_entries']}")

    if args.nras_token:
        check_gpu(args.nras_token, disclosure_bytes, args.allow_expired_tokens)
    if args.cc_mode:
        txt = Path(args.cc_mode).read_text()
        check("GPU CC mode ON and DevTools OFF (on-box capture, not signed evidence)",
              "CC status: ON" in txt and "DevTools Mode: OFF" in txt)

    check("declaration and baseline the operator used are the approved ones",
          sha256_file(args.declaration) == disclosure.get("declaration_sha256")
          and sha256_file(args.baseline) == disclosure.get("baseline_sha256"))
    extra = set(disclosure) - ALLOWED_DISCLOSURE_FIELDS
    check("disclosure carries no fields beyond the allowlist", not extra, f"unexpected: {sorted(extra)}" if extra else "")

    print()
    for status, name, detail in results:
        print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if mine["undeclared"] and args.verbose:
        print("\nundeclared measurements in the window:")
        for d, p in mine["undeclared"][:20]:
            print(f"  {p}  {d[:24]}...")

    print("\nwhat the regulator established for itself:")
    print(f"  - {mine['ima_entries_checked']} measured entries in the attested window, "
          f"{mine['undeclared_entries']} outside the approved sets")
    print(f"  - declared weights present by digest: {mine['weights_in_measured_window']}; "
          f"declared configuration present by digest: {mine['config_in_measured_window']}")
    print("  - it holds the file names and digests behind those counts, never the weights, outputs or prompts")
    print("not covered: which IMA policy was loaded (a single unmeasured write on this image); work an")
    print("already-declared process submits to the accelerator; code that never arrives as a file.")

    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
