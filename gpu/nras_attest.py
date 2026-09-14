"""Get an NVIDIA-issued (NRAS) attestation token for this GPU, with a caller-chosen nonce.

The Azure confidential-GPU image ships a local verifier whose tokens are HS256 self-assertions a third
party cannot check. The remote NVIDIA Remote Attestation Service signs ES384 tokens against a published
key set, which a regulator can verify. This uses NVIDIA's Python attestation SDK (nv-attestation-sdk),
which NVIDIA deprecated in March 2026 with end of support on 15 September 2026; the C++ SDK and the
nvattest command-line tool replace it with the same token format.

Usage (as root, from a venv with nv-attestation-sdk installed):
  python nras_attest.py <64-hex nonce> <out.json>
"""

import json
import sys

from nv_attestation_sdk import attestation

ENDPOINTS = [("https://nras.attestation.nvidia.com/v4/attest/gpu", "3.0"),
             ("https://nras.attestation.nvidia.com/v3/attest/gpu", "2.0")]


def main():
    nonce, out = sys.argv[1], sys.argv[2]
    if len(nonce) != 64:
        raise SystemExit("nonce must be 32 bytes as 64 hex characters")
    for url, claims in ENDPOINTS:
        client = attestation.Attestation()
        client.reset()
        client.set_name("ccverify-node")
        client.set_nonce(nonce)
        client.set_claims_version(claims)
        client.add_verifier(attestation.Devices.GPU, attestation.Environment.REMOTE, url, "")
        ok = client.attest(client.get_evidence())
        token = client.get_token()
        if ok and token:
            break
        print(f"attest via {url} (claims {claims}) did not succeed, trying the next endpoint")
    with open(out, "w") as f:
        json.dump({"attest_ok": bool(ok), "endpoint": url, "claims_version": claims, "token": token}, f, indent=2)
    print(f"NRAS attest {'succeeded' if ok else 'FAILED'} via {url}; token written to {out}")


if __name__ == "__main__":
    main()
