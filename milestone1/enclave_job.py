"""Runs inside the enclave. Produces the regulator-facing disclosure and nothing else.

This is the piece that makes the PoC a demo of the domestic scheme rather than a generic
TEE demo. The enclave sees everything: the code, the weights stand-in, the customer-data
stand-in, the output. What it hands the regulator is:

  - a verdict (accept/reject),
  - the hash of the code that actually ran,
  - a commitment to the output.

Nothing else crosses the boundary. The disclosure is checked against an allowlist before
it is written, so a future edit cannot quietly widen what the regulator receives.

The output commitment is sha256(salt || output) with the salt kept inside. The regulator
cannot read the output from it. Its purpose is to pin the operator to one specific output
so it can be opened later if a legal instrument compels it, which is the domestic handoff.
Honest caveat: for a low-entropy output the commitment is brute-forceable if the salt ever
leaks, so it is a pinning device, not a confidentiality guarantee.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent

# Everything the regulator is allowed to receive. Anything else is a leak.
ALLOWED_FIELDS = {
    "schema",
    "verdict",
    "code_sha256",
    "declared_code_sha256",
    "code_matches_declaration",
    "output_commitment",
    "commitment_scheme",
}

WITHHELD = ("secret_input.json", "workload output", "commitment salt")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    declared = json.loads((HERE / "declared_code.json").read_text())
    declared_hash = declared["declared_sha256"]

    # 1. Hash the code that is about to run, inside the enclave, before running it.
    actual_hash = sha256_file(HERE / declared["declared_program"])
    matches = actual_hash == declared_hash

    # 2. Run the declared program only if it is the approved one.
    if matches:
        sys.path.insert(0, str(HERE))
        import workload

        output = workload.run()
        salt = os.urandom(32)
        commitment = hashlib.sha256(
            salt + json.dumps(output, sort_keys=True).encode()
        ).hexdigest()
        verdict = "accept"
    else:
        # Refused. The regulator learns the refusal and the hash mismatch, nothing more.
        output, commitment, verdict = None, None, "reject"

    disclosure = {
        "schema": "ccverify.disclosure.v1",
        "verdict": verdict,
        "code_sha256": actual_hash,
        "declared_code_sha256": declared_hash,
        "code_matches_declaration": matches,
        "output_commitment": commitment,
        "commitment_scheme": "sha256(salt||output), salt retained in enclave",
    }

    leaked = set(disclosure) - ALLOWED_FIELDS
    if leaked:
        raise SystemExit(f"refusing to emit: disclosure carries non-allowlisted fields {leaked}")

    out = HERE / "disclosure.json"
    out.write_text(json.dumps(disclosure, indent=2, sort_keys=True) + "\n")
    print(json.dumps(disclosure, indent=2, sort_keys=True))
    print(f"\nwithheld from the regulator, never leaves this machine: {', '.join(WITHHELD)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
