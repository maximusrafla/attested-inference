"""Tier 1, operator side: rehearse the regulator's verdict before handing over the disclosure.

The regulator recomputes everything in verify_completeness.py from the exported log, the quote and the
approved sets, so nothing on its side depends on this script. It exists so the operator can see the
verdict the regulator will reach, and so the disclosure carries the operator's own claim for the
regulator to compare against. The verdict itself is defined once, in lib_verdict.py.

History. Until 2026-09-14 this script also decided the weight and configuration verdicts by hashing the
declared files on disk, and the regulator took those answers from the disclosure. They are now judged
from the measurement log on both sides (see lib_verdict.py).

Usage:
  python3 completeness_check.py --baseline baseline.json --declaration declaration.json \
      --ima-log ima.bin --quote quote.msg --ak-pub ak.pub.pem --pcr-values boot-pcrs.json \
      --out disclosure.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lib_ima import parse_binary_log
from lib_tpm import parse_attest
from lib_verdict import find_window, judge

ALLOWED_DISCLOSURE_FIELDS = {
    "schema", "verdict", "declaration_sha256", "baseline_sha256", "pcr10_sha256",
    "ima_entries_checked", "undeclared_entries", "weights_sha256", "config_sha256",
    "weights_in_measured_window", "config_in_measured_window",
    "quote_sha256", "ak_pub_sha256", "rejected_by",
}


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--declaration", required=True)
    ap.add_argument("--ima-log", required=True, help="binary_runtime_measurements, captured after the quote")
    ap.add_argument("--quote", required=True)
    ap.add_argument("--ak-pub", required=True)
    ap.add_argument("--pcr-values", help="boot-pcrs.json, the quoted PCR 0 to 9 values")
    ap.add_argument("--out", required=True)
    ap.add_argument("--verbose", action="store_true", help="print undeclared paths locally")
    args = ap.parse_args()

    baseline = json.loads(Path(args.baseline).read_text())
    declaration = json.loads(Path(args.declaration).read_text())
    att = parse_attest(Path(args.quote).read_bytes())
    entries = parse_binary_log(Path(args.ima_log).read_bytes())
    boot_values = {}
    if args.pcr_values:
        bank = json.loads(Path(args.pcr_values).read_text())["sha256"]
        boot_values = {int(k): bytes.fromhex(v) for k, v in bank.items()}

    covered, pcr10 = find_window(entries, att, boot_values)
    verdict = judge(entries, covered, baseline, declaration)

    disclosure = {
        "schema": "ccverify.disclosure.v4",
        "verdict": verdict["verdict"],
        "rejected_by": verdict["rejected_by"],
        "declaration_sha256": sha256_file(args.declaration),
        "baseline_sha256": sha256_file(args.baseline),
        "pcr10_sha256": pcr10 or "",
        "ima_entries_checked": verdict["ima_entries_checked"],
        "undeclared_entries": verdict["undeclared_entries"],
        "weights_sha256": declaration.get("weights_sha256"),
        "config_sha256": declaration.get("config_sha256"),
        "weights_in_measured_window": verdict["weights_in_measured_window"],
        "config_in_measured_window": verdict["config_in_measured_window"],
        "quote_sha256": sha256_file(args.quote),
        "ak_pub_sha256": sha256_file(args.ak_pub),
    }
    leaked = set(disclosure) - ALLOWED_DISCLOSURE_FIELDS
    if leaked:
        raise SystemExit(f"refusing to emit: non-allowlisted fields {sorted(leaked)}")

    Path(args.out).write_text(json.dumps(disclosure, indent=2, sort_keys=True))
    print(json.dumps(disclosure, indent=2, sort_keys=True))
    print(f"\nlog window {covered} of {len(entries)} entries; baseline froze the first {baseline['entry_count']}",
          file=sys.stderr)
    if args.verbose and verdict["undeclared"]:
        print("\nundeclared measurements (local view):", file=sys.stderr)
        for d, p in verdict["undeclared"][:20]:
            print(f"  {p}  {d[:24]}...", file=sys.stderr)
    return 0 if disclosure["verdict"] == "accept" else 1


if __name__ == "__main__":
    sys.exit(main())
