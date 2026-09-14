#!/usr/bin/env bash
# Pull every case from a Tier 1 sequence run and verify each one off the box.
#
#   verify_all.sh <ip> <evidence-dir> <platform sevsnpvm|tdxvm>      CCV_GPU=1 adds the GPU token check
#
# Expected verdicts: accept and undeclared-gpu are accept; every other case is reject. Each reject case is
# printed with the undeclared paths in its window, so the entry that case added is visible.
set -euo pipefail
IP="${1:?ip}"; EV="${2:?evidence dir}"; PLATFORM="${3:-sevsnpvm}"
HERE="$(cd "$(dirname "$0")" && pwd)"
KEY="${CCV_SSH_KEY:-$HOME/.ssh/ccverify_m1}"
PY="${CCV_PYTHON:-python}"
mkdir -p "$EV"
scp -q -r -i "$KEY" "azureuser@$IP:t1/out/." "$EV/"
scp -q -r -i "$KEY" "azureuser@$IP:t1/approved" "$EV/approved"

for D in "$EV"/*/; do
  L=$(basename "$D")
  [ "$L" = approved ] && continue
  [ -f "$D/disclosure.json" ] || continue
  case "$L" in accept|undeclared-gpu) EXP=accept ;; *) EXP=reject ;; esac
  GPU=(); [ "${CCV_GPU:-0}" = 1 ] && [ -f "$D/nras-token.json" ] && GPU=(--nras-token "$D/nras-token.json" --cc-mode "$D/cc-mode.txt")
  set +e
  "$PY" "$HERE/verify_completeness.py" --disclosure "$D/disclosure.json" --quote "$D/quote.msg" \
    --signature "$D/quote.sig" --ak-pub "$D/ak.pub.pem" --challenge "$(cat "$D/challenge.txt")" \
    --pcr-values "$D/boot-pcrs.json" --ima-log "$D/ima.bin" --maa-token "$D/maa-token.jwt" \
    --reference-pcrs "$EV/approved/reference-pcrs.json" --platform "$PLATFORM" \
    --declaration "$EV/approved/declaration.json" --baseline "$EV/approved/baseline.json" \
    "${GPU[@]}" --expect "$EXP" --verbose > "$EV/verify-$L.txt" 2>&1
  set -e
  echo "== $L (expect $EXP): $(tail -1 "$EV/verify-$L.txt")"
  grep -E "^\[FAIL\]" "$EV/verify-$L.txt" || true
  sed -n '/undeclared measurements in the window/,/^$/p' "$EV/verify-$L.txt" | head -8
done
