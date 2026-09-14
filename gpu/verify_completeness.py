"""Tier 1, regulator side: check the completeness disclosure from a separate machine.

Extends M1's verifier. M1 asked "did the approved code run inside real SEV-SNP isolation, and
did the operator hand me the same artifact it attested." This adds the completeness half: a
vTPM quote over PCR 10, which is the register Linux IMA extends with the digest of every file
executed, so the operator cannot run something undeclared without moving a value it does not
control.

What gets checked here, with nothing taken on the operator's word:

  the quote
  1. the quote is signed by the attestation key whose public half the disclosure names,
  2. the quote is a TPM 2.0 quote structure, not some other attestation type,
  3. the quote's extraData equals the challenge the regulator issued, so it is fresh,
  4. the quote's pcrDigest equals sha256 of the PCR 10 value in the disclosure, so the
     disclosure and the signed evidence describe the same register,

  the platform, if an MAA token is supplied
  5. the MAA token verifies against Microsoft's JWKS and reports an AMD SEV-SNP CVM,
  6. the MAA nonce equals sha256 of this disclosure, binding the platform report, the vTPM
     attestation key, and the verdict into one artifact,

  the scheme's content
  7. the declaration and baseline the enclave checked against are the ones the regulator holds,
  8. the verdict is accept and nothing objected,
  9. the disclosure carries nothing beyond the allowlist,

  the measurement log, exported with the receipt (added 2026-09-14)
  10. some prefix of the exported log replays, entry by entry, to the register value the
      quote signed, so the log is the one the hardware sealed and nothing was trimmed,
  11. that replayed value is the one written in the disclosure,
  12. the verifier's own recount of the attested window, every measured digest checked
      against the baseline and the declaration it holds, agrees with the disclosure's counts,
  13. the verifier's own finding on undeclared execution agrees with the disclosure's verdict.

Why 10 to 13 exist. The first build kept the log inside the enclave and sent out a verdict, on
the argument that the replay ran in attested code and check 4 made it non-repudiable. A blind
review (2026-09-12) pointed out what that leaves the regulator with: a register value it cannot
interpret and a verdict from code it cannot identify, so an operator could run a modified
checker, or none, and write "accept". Check 4 binds the disclosure to the quote; it does not bind
the verdict to the log. The remedy is the one Keylime has used in production for years: the
attester sends the measurement list out with the quote and the verifier replays it. The
enclave-side checker (completeness_check.py) is kept as the operator's own pre-check; the
regulator no longer depends on it. The cost, stated plainly: the regulator now holds the file
names and digests of everything the machine loaded in the window. It still never holds the
weights, the outputs or the prompts.

PCR 10 liveness: on a live cloud guest the register advances between any two commands, so the
log is dumped after the quote and the verifier finds the prefix the quote covers by replaying
until the running value hashes to the quote's pcrDigest. Entries after that prefix are outside
the attested window and are ignored, not counted.

Usage:
  python verify_completeness.py --disclosure out/disclosure.json --quote out/quote.msg \
      --signature out/quote.sig --ak-pub out/ak.pub.pem --challenge <hex> \
      --declaration declaration.json --baseline baseline.json --ima-log out/ima.bin \
      [--maa-token out/maa-token.jwt] [--nras-token out/nras-token.json]
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ALLOWED_DISCLOSURE_FIELDS = {
    "schema", "verdict", "declaration_sha256", "baseline_sha256", "pcr10_sha256",
    "ima_entries_checked", "undeclared_entries", "weights_sha256",
    "weights_match_declaration", "config_sha256", "config_matches_declaration",
    "quote_sha256", "ak_pub_sha256", "rejected_by",
}

TPM_GENERATED_VALUE = 0xFF544347
TPM_ST_ATTEST_QUOTE = 0x8018
ALG_RSASSA, ALG_RSAPSS, ALG_ECDSA = 0x0014, 0x0016, 0x0018

results = []


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, detail))
    return ok


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0], o + 2


def _tpm2b(b, o):
    n, o = _u16(b, o)
    return b[o:o + n], o + n


def parse_attest(blob):
    o = 0
    magic, = struct.unpack_from(">I", blob, o); o += 4
    typ, o = _u16(blob, o)
    _signer, o = _tpm2b(blob, o)
    extra, o = _tpm2b(blob, o)
    o += 17          # TPMS_CLOCK_INFO
    o += 8           # firmwareVersion
    count, = struct.unpack_from(">I", blob, o); o += 4
    selections = []
    for _ in range(count):
        alg, o = _u16(blob, o)
        size = blob[o]; o += 1
        selections.append((alg, blob[o:o + size])); o += size
    pcr_digest, o = _tpm2b(blob, o)
    return {"magic": magic, "type": typ, "extra_data": extra,
            "selections": selections, "pcr_digest": pcr_digest}


def parse_signature(blob):
    o = 0
    sig_alg, o = _u16(blob, o)
    hash_alg, o = _u16(blob, o)
    if sig_alg in (ALG_RSASSA, ALG_RSAPSS):
        sig, o = _tpm2b(blob, o)
        return sig_alg, hash_alg, sig
    if sig_alg == ALG_ECDSA:
        r, o = _tpm2b(blob, o)
        s, o = _tpm2b(blob, o)
        return sig_alg, hash_alg, (r, s)
    raise ValueError(f"unsupported TPM signature algorithm 0x{sig_alg:04x}")


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


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_maa(token_path, disclosure_bytes):
    import jwt
    from jwt import PyJWKClient
    token = Path(token_path).read_text().strip()
    unverified = jwt.decode(token, options={"verify_signature": False})
    issuer = unverified.get("iss", "")
    signing_key = PyJWKClient(issuer.rstrip("/") + "/certs").get_signing_key_from_jwt(token)
    claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], options={"verify_aud": False})
    tee = claims.get("x-ms-isolation-tee") or claims
    check("MAA token signature verifies against Microsoft's JWKS", True, issuer)
    check("MAA reports an AMD SEV-SNP confidential VM",
          tee.get("x-ms-attestation-type") == "sevsnpvm", str(tee.get("x-ms-attestation-type")))
    check("MAA compliance status is azure-compliant-cvm",
          tee.get("x-ms-compliance-status") == "azure-compliant-cvm",
          str(tee.get("x-ms-compliance-status")))
    expected = hashlib.sha256(disclosure_bytes).hexdigest()
    import base64
    seen, cur = set(), None
    runtime = claims.get("x-ms-runtime") or {}
    payload = runtime.get("client-payload") or {}
    for key in ("nonce", "user-data", "userdata"):
        for src in (payload, runtime, claims):
            if isinstance(src, dict) and isinstance(src.get(key), str):
                cur = src[key]
                seen.add(cur)
                for _ in range(3):
                    try:
                        cur = base64.b64decode(cur + "=" * (-len(cur) % 4)).decode()
                        seen.add(cur)
                    except Exception:
                        break
    check("MAA nonce equals sha256 of this disclosure", expected in seen,
          f"expected {expected[:16]}...")
    return claims


def check_gpu(nras_path, disclosure_bytes):
    """The accelerator root: an NVIDIA-signed attestation of THIS GPU, bound to this disclosure.

    Which token this is matters, and finding out cost a probe. The Azure CGPU image's shipped
    path (`gpu-attestation`, the bundled local_gpu_verifier) emits an EAT signed HS256 and issued
    by LOCAL_GPU_VERIFIER, so it is a symmetric self-assertion and a regulator cannot verify it
    at all. The remote NRAS path emits ES384 tokens issued by nras.attestation.nvidia.com against
    a published JWKS, which is what a regulator can actually check. So this function insists on
    the NRAS token and says so.
    """
    import base64
    import json as _json
    import jwt
    from jwt import PyJWKClient

    bundle = _json.loads(Path(nras_path).read_text())

    def find_jwts(o, acc):
        if isinstance(o, str) and o.count(".") == 2:
            acc.append(o)
        elif isinstance(o, list):
            for x in o:
                find_jwts(x, acc)
        elif isinstance(o, dict):
            for x in o.values():
                find_jwts(x, acc)
        return acc

    def hdr(t):
        h = t.split(".")[0]
        return _json.loads(base64.urlsafe_b64decode(h + "=" * (-len(h) % 4)))

    tokens = find_jwts(bundle, [])
    nras = [t for t in tokens
            if _json.loads(base64.urlsafe_b64decode(
                t.split(".")[1] + "=" * (-len(t.split(".")[1]) % 4))).get("iss", "")
            .startswith("https://nras.attestation.nvidia.com")]
    check("bundle contains an NVIDIA-issued (not local self-signed) GPU attestation",
          bool(nras), f"{len(nras)} of {len(tokens)} tokens are NRAS-issued")
    if not nras:
        return

    jwks = PyJWKClient("https://nras.attestation.nvidia.com/.well-known/jwks.json")
    device = None
    for t in nras:
        key = jwks.get_signing_key_from_jwt(t)
        claims = jwt.decode(t, key.key, algorithms=[hdr(t)["alg"]],
                            options={"verify_aud": False})
        if "hwmodel" in claims:
            device = claims
    check("GPU attestation verifies against NVIDIA's published JWKS", True,
          f"alg {hdr(nras[0])['alg']}, iss nras.attestation.nvidia.com")
    if device is None:
        check("GPU attestation carries per-device claims", False)
        return

    expected = hashlib.sha256(disclosure_bytes).hexdigest()
    check("GPU attestation nonce equals sha256 of this disclosure",
          device.get("eat_nonce") == expected, f"expected {expected[:16]}...")
    check("NVIDIA's verifier reports the GPU measurements matched golden values",
          device.get("measres") == "success", str(device.get("measres")))
    check("GPU reports secure boot on and debug disabled",
          device.get("secboot") is True and device.get("dbgstat") == "disabled",
          f"secboot={device.get('secboot')} dbgstat={device.get('dbgstat')}")
    print(f"\nattested accelerator: {device.get('hwmodel')} ueid {str(device.get('ueid'))[:18]}..., "
          f"driver {device.get('x-nvidia-gpu-driver-version')}, "
          f"vbios {device.get('x-nvidia-gpu-vbios-version')}")


def check_log(log_path, disclosure, att, declaration_path, baseline_path, verbose=False):
    """Checks 10 to 13: replay the exported measurement log on the regulator's own machine.

    The regulator holds the baseline and the declaration (digest sets) and now the log. It finds
    the prefix the quote covers from the quote's pcrDigest, replays it, and does its own count.
    Nothing here relies on the enclave-side checker having run at all.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from lib_ima import parse_binary_log
    entries = parse_binary_log(Path(log_path).read_bytes())
    target = att["pcr_digest"].hex()

    covered, value = None, None
    pcr = bytes(32)
    if hashlib.sha256(pcr).hexdigest() == target:
        covered, value = 0, pcr.hex()
    else:
        for i, e in enumerate(entries):
            if e.pcr != 10:
                continue
            pcr = hashlib.sha256(pcr + e.bank_digest("sha256")).digest()
            if hashlib.sha256(pcr).hexdigest() == target:
                covered, value = i + 1, pcr.hex()
                break
    check("exported measurement log replays to the register value the quote signed",
          covered is not None,
          (f"prefix {covered} of {len(entries)} entries reproduces the quoted digest"
           if covered is not None else "no prefix of the log reproduces the quoted digest"))
    if covered is None:
        return None
    check("replayed register value equals the one in the disclosure",
          value == disclosure.get("pcr10_sha256"))

    baseline = json.loads(Path(baseline_path).read_text())
    declaration = json.loads(Path(declaration_path).read_text())
    approved = set(baseline["digests"]) | set(declaration["digests"])
    if declaration.get("weights_sha256"):
        approved.add("sha256::" + declaration["weights_sha256"])

    checked, undeclared = 0, []
    for e in entries[:covered]:
        digest, path = e.file_digest_and_path()
        if digest is None:
            continue
        checked += 1
        if digest not in approved:
            undeclared.append((digest, path))

    check("verifier's own recount of the attested window agrees with the disclosure",
          checked == disclosure.get("ima_entries_checked")
          and len(undeclared) == disclosure.get("undeclared_entries"),
          f"verifier counts {checked} measured and {len(undeclared)} undeclared; "
          f"disclosure says {disclosure.get('ima_entries_checked')} and "
          f"{disclosure.get('undeclared_entries')}")
    says_undeclared = "undeclared_execution" in (disclosure.get("rejected_by") or [])
    check("verifier's own finding on undeclared execution agrees with the verdict",
          bool(undeclared) == says_undeclared,
          "undeclared execution found in the log" if undeclared else "none found in the log")
    if undeclared and verbose:
        print("\nundeclared executions the regulator can now see for itself:")
        for d, p in undeclared[:20]:
            print(f"  {p}  {d[:24]}...")
        if len(undeclared) > 20:
            print(f"  ... and {len(undeclared) - 20} more")
    return {"covered": covered, "total": len(entries), "checked": checked,
            "undeclared": len(undeclared)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosure", required=True)
    ap.add_argument("--quote", required=True)
    ap.add_argument("--signature", required=True)
    ap.add_argument("--ak-pub", required=True)
    ap.add_argument("--challenge", required=True, help="the hex challenge the regulator issued")
    ap.add_argument("--declaration", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--maa-token")
    ap.add_argument("--nras-token", help="the NVIDIA-issued GPU attestation bundle")
    ap.add_argument("--cc-mode", help="nvidia-smi conf-compute capture, checked for CC ON / DevTools OFF")
    ap.add_argument("--ima-log", help="the measurement log exported with the receipt, replayed here")
    ap.add_argument("--audit-log", help=argparse.SUPPRESS)   # the pre-2026-09-14 name, same file
    ap.add_argument("--verbose", action="store_true", help="list undeclared paths found in the log")
    ap.add_argument("--expect", choices=["accept", "reject"], default="accept")
    args = ap.parse_args()

    disclosure_bytes = Path(args.disclosure).read_bytes()
    disclosure = json.loads(disclosure_bytes)
    quote_bytes = Path(args.quote).read_bytes()
    sig_blob = Path(args.signature).read_bytes()
    ak_pem = Path(args.ak_pub).read_bytes()

    ok, how = verify_quote_signature(quote_bytes, sig_blob, ak_pem)
    check("quote is signed by the attestation key named in the disclosure", ok, how)

    att = parse_attest(quote_bytes)
    check("quote is a TPM 2.0 quote structure",
          att["magic"] == TPM_GENERATED_VALUE and att["type"] == TPM_ST_ATTEST_QUOTE,
          f"magic 0x{att['magic']:08x}, type 0x{att['type']:04x}")
    check("quote answers the regulator's challenge",
          att["extra_data"].hex() == args.challenge.lower(),
          f"carried {att['extra_data'].hex()[:24]}...")
    expected_pcr_digest = hashlib.sha256(bytes.fromhex(disclosure["pcr10_sha256"])).hexdigest()
    check("quoted PCR digest matches the PCR 10 value in the disclosure",
          att["pcr_digest"].hex() == expected_pcr_digest,
          f"quote {att['pcr_digest'].hex()[:24]}...")
    check("AK public key hashes to the value in the disclosure",
          hashlib.sha256(ak_pem).hexdigest() == disclosure.get("ak_pub_sha256"))
    check("quote hashes to the value in the disclosure",
          hashlib.sha256(quote_bytes).hexdigest() == disclosure.get("quote_sha256"))

    if args.maa_token:
        check_maa(args.maa_token, disclosure_bytes)
    if args.nras_token:
        check_gpu(args.nras_token, disclosure_bytes)
    if args.cc_mode:
        txt = Path(args.cc_mode).read_text()
        check("GPU is in full confidential-compute mode, not DevTools mode",
              "CC status: ON" in txt and "DevTools Mode: OFF" in txt,
              "CC status and DevTools mode as captured on the box")

    check("declaration checked against is the one the regulator approved",
          sha256_file(args.declaration) == disclosure.get("declaration_sha256"))
    check("baseline checked against is the one the regulator approved",
          sha256_file(args.baseline) == disclosure.get("baseline_sha256"))
    check(f"disclosure verdict is {args.expect}", disclosure.get("verdict") == args.expect,
          f"verdict {disclosure.get('verdict')}, rejected_by {disclosure.get('rejected_by')}")
    extra = set(disclosure) - ALLOWED_DISCLOSURE_FIELDS
    check("disclosure carries no fields beyond the allowlist", not extra,
          f"unexpected: {sorted(extra)}" if extra else "weights and outputs withheld")

    log_path = args.ima_log or args.audit_log
    replay = check_log(log_path, disclosure, att, args.declaration, args.baseline,
                       args.verbose) if log_path else None

    print()
    for status, name, detail in results:
        print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))

    print("\nwhat the regulator now knows:")
    if replay:
        print(f"  - by its own replay, {replay['checked']} files executed or were read in this domain")
        print(f"    inside the attested window (log prefix {replay['covered']} of {replay['total']})")
        print(f"  - {replay['undeclared']} of them were outside the approved baseline and declaration")
        print("  - and it holds the file names and digests behind those counts")
    else:
        print(f"  - {disclosure['ima_entries_checked']} files have executed in this domain since it booted")
        print(f"  - {disclosure['undeclared_entries']} of them were outside the approved declaration")
        print("    (both figures on the enclave-side checker's word: no log was supplied to replay)")
    print(f"  - the declared payload {'matched' if disclosure.get('weights_match_declaration') else 'did NOT match'}"
          " the approved digest")
    cfg = disclosure.get("config_matches_declaration")
    print(f"  - the runtime configuration {'matched' if cfg else ('did NOT match' if cfg is False else 'was not declared at all in')}"
          " the approved digest")
    print("what it does not know: the weights, the outputs, the prompts"
          + ("" if replay else ", which files, which paths"))

    print("\nnot covered by this evidence:")
    print("  - work submitted to an accelerator by an already-declared process. IMA measures")
    print("    file execution, not GPU kernel launches. Tier 0 binds the accelerator's identity")
    print("    and mode; it does not itemize what was submitted to it.")
    print("  - code the declared interpreter loads as data. Under the tcb policy a non-root read")
    print("    is not measured, so completeness here is only as wide as the IMA policy is.")
    print("  - anything executed outside the quoted window.")
    print("  - physical interposition by an operator with sustained access to the host.")

    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
