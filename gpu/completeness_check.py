"""Tier 1, enclave side: decide whether the declared stack, and only it, executed.

Runs INSIDE the confidential VM, as part of the declared stack, so its own code is measured
before the quote is taken. It reads the IMA measurement log, replays it against the PCR value
the vTPM just signed, checks every measured file against what the regulator approved, and emits
a disclosure carrying the answer and nothing else.

Why the checker is in here and not on the regulator's desk: the log names every file path the
operator executed, which is exactly the kind of thing the domestic scheme withholds. The
regulator gets a verdict from attested code instead of a directory listing. Same discipline as
M1 and M3.

Three separate things get decided, and the disclosure keeps them separate:
  1. log integrity   replay of the log must reproduce the quoted PCR 10 value, else the log
                     has been trimmed or the window is wrong,
  2. completeness    every file measured in the window must be in the approved baseline or the
                     approved declaration, else something undeclared executed,
  3. payload identity the declared weight file must hash to what the regulator approved
                     (IMA does not measure a data file read by a non-root process, so this is
                     carried explicitly, the same way M1 carried the code hash).

Usage:
  python3 completeness_check.py --baseline baseline.json --declaration declaration.json \
      --ima-log ima.bin --quoted-pcr <hex> --quote quote.msg --ak-pub ak.pub.pem \
      --out disclosure.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lib_ima import parse_binary_log
from lib_tpm import parse_attest

ALLOWED_DISCLOSURE_FIELDS = {
    "schema",
    "verdict",
    "declaration_sha256",
    "baseline_sha256",
    "pcr10_sha256",
    "ima_entries_checked",
    "undeclared_entries",
    "weights_sha256",
    "weights_match_declaration",
    "config_sha256",
    "config_matches_declaration",
    "quote_sha256",
    "ak_pub_sha256",
    "rejected_by",
}

WITHHELD = ("the measurement log and every path in it", "the model weights",
            "customer data", "the stack's output")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prefix_matching_quote(entries, pcr_digest_hex):
    """Find the log prefix the quote actually covers, and the PCR value it attests.

    With one PCR selected, the quote's pcrDigest is sha256 of that register's value. So replay
    the log entry by entry and hash each running value: the prefix whose hash equals pcrDigest
    is the one the vTPM signed. Entries after it were logged after the quote was taken and are
    outside the attested window, so they are excluded rather than silently counted.

    This also removes a race that is unavoidable on this host. Reading PCR 10 and then quoting
    it cannot be made atomic on an Azure confidential-GPU guest, because the provider's in-guest
    security agent keeps reading files that keep changing and the tcb policy measures every one,
    so the register advances between any two commands. Deriving the value from the quote is
    correct by construction instead.

    Returns (entries_covered, pcr_hex_or_None).
    """
    pcr = bytes(32)
    if hashlib.sha256(pcr).hexdigest() == pcr_digest_hex:
        return 0, pcr.hex()
    for i, e in enumerate(entries):
        if e.pcr != 10:
            continue
        pcr = hashlib.sha256(pcr + e.bank_digest("sha256")).digest()
        if hashlib.sha256(pcr).hexdigest() == pcr_digest_hex:
            return i + 1, pcr.hex()
    return len(entries), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="approved platform + tooling measured set")
    ap.add_argument("--declaration", required=True, help="the approved declared stack")
    ap.add_argument("--ima-log", required=True, help="binary_runtime_measurements, captured after the quote")
    ap.add_argument("--quote", required=True, help="quote.msg: the PCR value is derived from it")
    ap.add_argument("--ak-pub", required=True, help="ak.pub.pem, hashed into the disclosure")
    ap.add_argument("--out", required=True)
    ap.add_argument("--verbose", action="store_true", help="print undeclared paths locally, never disclosed")
    args = ap.parse_args()

    baseline = json.loads(Path(args.baseline).read_text())
    declaration = json.loads(Path(args.declaration).read_text())

    attest = parse_attest(Path(args.quote).read_bytes())
    entries = parse_binary_log(Path(args.ima_log).read_bytes())
    covered, quoted_pcr = prefix_matching_quote(entries, attest["pcr_digest"].hex())
    log_ok = quoted_pcr is not None

    # Every entry the quote covers gets checked, not just the ones since the run started. IMA
    # measures each file once per boot, so a second run of the declared stack adds nothing to
    # the log, and a check scoped to "entries since the run began" would pass on an empty set.
    # PCR 10 is cumulative since boot, so the honest claim is cumulative too: since this domain
    # booted, every file that executed was one the regulator approved.
    approved = set(baseline["digests"]) | set(declaration["digests"])
    # The declared weight file is approved by its own digest below, but under the tcb policy a
    # root-run stack also causes IMA to measure it as a read, so its digest has to be approved
    # in the measured set too or the declared payload reads as undeclared.
    if declaration.get("weights_sha256"):
        approved.add("sha256::" + declaration["weights_sha256"])

    undeclared = []
    checked = 0
    for e in entries[:covered]:
        digest, path = e.file_digest_and_path()
        if digest is None:
            continue
        checked += 1
        if digest not in approved:
            undeclared.append((digest, path))

    weights_sha = sha256_file(declaration["weights_path"]) if declaration.get("weights_path") else None
    weights_ok = (weights_sha == declaration.get("weights_sha256")) if weights_sha else None

    config_sha = sha256_file(declaration["config_path"]) if declaration.get("config_path") else None
    config_ok = (config_sha == declaration.get("config_sha256")) if config_sha else None

    rejected_by = []
    if not log_ok:
        rejected_by.append("log_integrity")
    if undeclared:
        rejected_by.append("undeclared_execution")
    if weights_ok is False:
        rejected_by.append("payload_identity")
    if config_ok is False:
        rejected_by.append("configuration_identity")

    disclosure = {
        "schema": "ccverify.disclosure.v3",
        "verdict": "accept" if not rejected_by else "reject",
        "declaration_sha256": sha256_file(args.declaration),
        "baseline_sha256": sha256_file(args.baseline),
        "pcr10_sha256": quoted_pcr or "",
        "ima_entries_checked": checked,
        "undeclared_entries": len(undeclared),
        "weights_sha256": weights_sha,
        "weights_match_declaration": weights_ok,
        "config_sha256": config_sha,
        "config_matches_declaration": config_ok,
        "quote_sha256": sha256_file(args.quote),
        "ak_pub_sha256": sha256_file(args.ak_pub),
        "rejected_by": rejected_by,
    }

    leaked = set(disclosure) - ALLOWED_DISCLOSURE_FIELDS
    if leaked:
        raise SystemExit(f"refusing to emit: non-allowlisted fields {sorted(leaked)}")

    Path(args.out).write_text(json.dumps(disclosure, indent=2, sort_keys=True))
    print(json.dumps(disclosure, indent=2, sort_keys=True))
    print(f"\nlog replay covered {covered} of {len(entries)} entries (match={log_ok}); "
          f"baseline froze the first {baseline['entry_count']}", file=sys.stderr)
    print(f"withheld from the regulator: {', '.join(WITHHELD)}", file=sys.stderr)
    if args.verbose and undeclared:
        print("\nundeclared executions (local view only, NOT disclosed):", file=sys.stderr)
        for d, p in undeclared[:20]:
            print(f"  {p}  {d[:24]}...", file=sys.stderr)
    return 0 if disclosure["verdict"] == "accept" else 1


if __name__ == "__main__":
    sys.exit(main())
