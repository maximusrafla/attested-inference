"""The domestic disclosure wrapper: compose the two layers, emit only what the regulator gets.

Two cited layers:
  - review  = deterministic source review (review_scan.py; mechanism 1c, AI variant cited to Agentic Witnessing)
  - attest  = Kettle build+attest+verify (source -> artifact binding; cited, MIT)

Overall verdict = accept ONLY if BOTH pass. The xz point: A is caught by review, B by attest; either failing
rejects the whole. The regulator learns the verdict, the attested provenance digest, and which layer objected.
WITHHELD (same allowlist discipline as M1): source contents, weights, customer data, the artifact itself.

Usage:
  # real, on the SEV-SNP VM after `kettle attest`:
  python disclose.py --source <src-dir> --kettle-build <kettle-build-dir>
  # local logic test (no Kettle): supply the attest outcome directly:
  python disclose.py --source <src-dir> --attest pass|fail [--provenance-digest <hex>]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ALLOWED = {"schema", "verdict", "review", "attestation", "provenance_digest", "rejected_by"}
WITHHELD = ("source contents", "weights", "customer data", "the built artifact")


def run_review(src):
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "review_scan.py"), src],
                       capture_output=True, text=True)
    return ("pass" if r.returncode == 0 else "fail"), r.stdout.strip()


def run_kettle_verify(build_dir):
    r = subprocess.run(["kettle", "verify", build_dir], capture_output=True, text=True)
    passed = r.returncode == 0 and "PASSED" in (r.stdout + r.stderr)
    return "pass" if passed else "fail"


def provenance_digest(build_dir):
    import hashlib
    p = Path(build_dir) / "provenance.json"
    if p.exists():
        return hashlib.sha256(p.read_bytes()).hexdigest()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--kettle-build")
    ap.add_argument("--attest", choices=["pass", "fail"], help="override for local logic testing")
    ap.add_argument("--provenance-digest")
    args = ap.parse_args()

    review_verdict, review_detail = run_review(args.source)

    if args.attest:
        attest_verdict = args.attest
        prov = args.provenance_digest
    elif args.kettle_build:
        attest_verdict = run_kettle_verify(args.kettle_build)
        prov = provenance_digest(args.kettle_build)
    else:
        print("need --kettle-build (real) or --attest (test)", file=sys.stderr)
        return 2

    rejected_by = [name for name, v in (("review", review_verdict), ("attestation", attest_verdict)) if v == "fail"]
    verdict = "accept" if not rejected_by else "reject"

    disclosure = {
        "schema": "ccverify.disclosure.v2",
        "verdict": verdict,
        "review": review_verdict,
        "attestation": attest_verdict,
        "provenance_digest": prov,
        "rejected_by": rejected_by,
    }
    leaked = set(disclosure) - ALLOWED
    if leaked:
        raise SystemExit(f"refusing to emit: non-allowlisted fields {leaked}")

    print(json.dumps(disclosure, indent=2, sort_keys=True))
    print(f"\nwithheld from the regulator: {', '.join(WITHHELD)}", file=sys.stderr)
    return 0 if verdict == "accept" else 1


if __name__ == "__main__":
    sys.exit(main())
