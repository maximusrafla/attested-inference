"""The declared program: the code the operator told the regulator it would run.

Deliberately trivial, no ML. M1 is about the plumbing. What matters is that this
file has a hash, that hash was approved in advance, and the enclave can prove the
approved code is what actually ran.

It reads a secret input standing in for model weights and customer data. That input
and this program's output stay inside the enclave. Neither appears in the disclosure.
"""

import json
from pathlib import Path

SECRET_INPUT = Path(__file__).parent / "secret_input.json"


def run():
    payload = json.loads(SECRET_INPUT.read_text())
    return {"total": sum(payload["values"]), "n": len(payload["values"])}


if __name__ == "__main__":
    print(json.dumps(run()))
