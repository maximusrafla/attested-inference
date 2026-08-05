#!/usr/bin/env bash
# M1 step 3: run the declared program in the enclave, emit the regulator-facing
# disclosure, and get an MAA attestation token bound to that disclosure.
#
# The nonce is sha256 of the DISCLOSURE, not of the run. That is deliberate: it ties the
# hardware report to exactly the artifact the regulator receives, so the operator cannot
# attest one thing and hand over another.
#
# Usage: ./attest.sh <azure-region>      e.g. ./attest.sh eastus2
set -euo pipefail

REGION="${1:?usage: attest.sh <azure-region>}"

case "$REGION" in
  eastus2)     MAA="https://sharedeus2.eus2.attest.azure.net" ;;
  eastus)      MAA="https://sharedeus.eus.attest.azure.net" ;;
  westeurope)  MAA="https://sharedweu.weu.attest.azure.net" ;;
  northeurope) MAA="https://sharedneu.neu.attest.azure.net" ;;
  westus)      MAA="https://sharedwus.wus.attest.azure.net" ;;
  centralus)   MAA="https://sharedcus.cus.attest.azure.net" ;;
  *) echo "unknown region $REGION, pass the MAA url as \$2" >&2; MAA="${2:?}" ;;
esac
echo "MAA endpoint: $MAA"

WORK="$HOME/m1"
OUT="$HOME/m1-out"          # ONLY this directory is copied to the verifier.
mkdir -p "$OUT"

echo "== running the declared program inside the enclave =="
python3 "$WORK/enclave_job.py"

echo "== binding the attestation to the disclosure =="
cp "$WORK/disclosure.json" "$OUT/disclosure.json"
DISCLOSURE_HASH=$(sha256sum "$OUT/disclosure.json" | cut -d' ' -f1)
NONCE=$(printf '%s' "$DISCLOSURE_HASH" | base64 -w0)
echo "disclosure sha256: $DISCLOSURE_HASH"

echo "== requesting attestation token =="
sudo AttestationClient -a "$MAA" -n "$NONCE" -o token > "$OUT/maa-token.jwt"
echo "token bytes: $(wc -c < "$OUT/maa-token.jwt")"

cp "$WORK/declared_code.json" "$OUT/declared_code.json"   # regulator already holds this
echo "$MAA" > "$OUT/maa-endpoint.txt"

echo "== what the regulator receives =="
ls -l "$OUT"
echo "== what stays here: secret_input.json, the workload output, the commitment salt =="
