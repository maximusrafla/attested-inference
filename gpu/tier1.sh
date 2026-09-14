#!/usr/bin/env bash
# Tier 1 ceremony: the declared stack, and only it, executed inside the confidential domain.
#
# Chain:
#   the SEV-SNP (or TDX) report roots the paravisor and vTPM, and binds the vTPM's attestation key
#   (HCLAkPub) into the platform token  ->  secure boot and the signed kernel image land in PCRs 0 to 9
#   ->  IMA's boot_aggregate hashes PCRs 0 to 9 into the first log entry  ->  IMA extends PCR 10 with
#   every measured file  ->  a quote by THAT attestation key over PCRs 0 to 10, answering the regulator's
#   challenge  ->  the platform and GPU tokens both carry sha256 of the disclosure as their nonce.
#
# Changed 2026-09-14. The quote used to be signed by a key this script created with tpm2_createak, which
# nothing tied to the attested vTPM, and it covered PCR 10 only. The declared stack used to run as the
# ceremony user, whose reads are not measured, so the weight and configuration verdicts came from the
# checker hashing files on disk. Now: the quote is by the vTPM's own attestation key at 0x81000003, over
# PCRs 0 to 10, and the declared stack runs as a dedicated serving account whose every read is measured.
#
# Phases:  warmup | baseline | declare | run LABEL [flags] | bind LABEL [region]
# run flags:  --undeclared-exec    also execute an undeclared script (expect reject)
#             --undeclared-import  the declared interpreter loads undeclared code as data (expect reject:
#                                  the serving account's reads are measured)
#             --tampered           flip one bit in the declared weight file (expect reject)
#             --config-changed     switch the safety filter off in the runtime configuration (expect reject)
#             --undeclared-gpu     the declared process also submits undeclared GPU work (expect ACCEPT:
#                                  the limit of what a measurement of files can see)
#             --as-root            run the stack as root instead of the serving account
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/t1/work"
APPROVED="$HOME/t1/approved"
OUT="$HOME/t1/out"
SVC="${CCV_SVC:-ccvsvc}"
AK_HANDLE="${CCV_AK_HANDLE:-0x81000003}"
IMA_BIN=/sys/kernel/security/ima/binary_runtime_measurements
BOOT_PCRS="sha256:0,1,2,3,4,5,6,7,8,9"
QUOTE_PCRS="sha256:0,1,2,3,4,5,6,7,8,9,10"
mkdir -p "$WORK" "$APPROVED" "$OUT"

dump_log() { sudo cat "$IMA_BIN" > "$1"; sudo chown "$USER" "$1"; }

export_ak() {
  # The vTPM's own attestation key, the one the platform token names as HCLAkPub.
  [ -f "$WORK/ak.pub.pem" ] || tpm2_readpublic -c "$AK_HANDLE" -f pem -o "$WORK/ak.pub.pem" >/dev/null
}

boot_pcrs_json() {
  tpm2_pcrread "$BOOT_PCRS" | python3 -c '
import json, re, sys
vals = {m.group(1): m.group(2).lower() for m in re.finditer(r"^\s*(\d+)\s*:\s*0x([0-9A-Fa-f]{64})\s*$", sys.stdin.read(), re.M)}
assert len(vals) == 10, vals
json.dump({"sha256": vals}, open(sys.argv[1], "w"), indent=2, sort_keys=True)' "$1"
}

quote() {  # quote <challenge-hex> <dir>
  boot_pcrs_json "$2/boot-pcrs.json"
  tpm2_quote -c "$AK_HANDLE" -l "$QUOTE_PCRS" -q "$1" -g sha256 \
             -m "$2/quote.msg" -s "$2/quote.sig" -o "$2/quote.pcrs" >/dev/null
  cp "$WORK/ak.pub.pem" "$2/ak.pub.pem"
}

run_stack() {  # run_stack <as-root 0|1> <args...>
  # sudo resets PATH, so the interpreter the declared script's shebang finds is chosen explicitly:
  # CCV_VENV (the GPU box's torch venv) when set, the system python otherwise.
  local root="$1"; shift
  local path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  [ -n "${CCV_VENV:-}" ] && path="$CCV_VENV/bin:$path"
  if [ "$root" = 1 ]; then sudo env PATH="$path" "$@"; else sudo -u "$SVC" env PATH="$path" "$@"; fi
}

case "${1:?usage: tier1.sh warmup|baseline|declare|run <label> [flags]|bind <label> [region]}" in

warmup)
  export_ak
  echo "== serving account $SVC and file access =="
  id "$SVC" >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC"
  chmod o+x "$HOME" "$HOME/t1" "$WORK"
  chmod -R o+rX "$HERE"; chmod +x "$HERE/declared_stack.py"
  [ -n "${CCV_VENV:-}" ] && chmod -R o+rX "$CCV_VENV"
  sudo install -d -o "$SVC" -m 0755 "$WORK/svc-out"
  echo "== executing every utility the ceremony uses, so none of them first appears mid-run =="
  for c in head xxd grep sed tr tee cat cp mv rm mkdir chmod chown install ls id wc env dirname \
           bash sudo sleep seq tail nvidia-smi setfacl systemctl useradd \
           tpm2_pcrread tpm2_quote tpm2_readpublic tpm2_nvread python3; do
    command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1 || true
  done
  echo "== warming the tool set (throwaway quote and a throwaway check) =="
  mkdir -p "$WORK/warm"
  quote 00 "$WORK/warm"
  dump_log "$WORK/warm/ima.bin"
  python3 "$HERE/capture_set.py" baseline --ima-log "$WORK/warm/ima.bin" --out "$WORK/warm/set.json" >/dev/null
  python3 "$HERE/completeness_check.py" --baseline "$WORK/warm/set.json" --declaration "$WORK/warm/set.json" \
    --ima-log "$WORK/warm/ima.bin" --quote "$WORK/warm/quote.msg" --ak-pub "$WORK/ak.pub.pem" \
    --pcr-values "$WORK/warm/boot-pcrs.json" --out "$WORK/warm/disclosure.json" >/dev/null 2>&1 || true
  # Deliberately NOT run here: the declared stack. Its first measurement belongs in the declaration.
  [ -f "$WORK/weights.bin" ] || head -c 65536 /dev/urandom > "$WORK/weights.bin"
  [ -f "$WORK/config.approved.json" ] || \
    printf '{"safety_filter":"on","system_prompt":"You are a helpful assistant.","temperature":0.0}\n' \
      > "$WORK/config.approved.json"
  cp "$WORK/config.approved.json" "$WORK/config.json"
  chmod o+r "$WORK/weights.bin" "$WORK/config.json" "$WORK/config.approved.json"
  echo "warmup done"
  ;;

baseline)
  dump_log "$WORK/baseline.bin"
  python3 "$HERE/capture_set.py" baseline --ima-log "$WORK/baseline.bin" --out "$APPROVED/baseline.json"
  boot_pcrs_json "$APPROVED/reference-pcrs.json"
  ;;

declare)
  echo "== approved reference run of the declared stack, as $SVC =="
  GPUFLAG=(); [ "${CCV_GPU:-0}" = 1 ] && GPUFLAG=(--gpu)
  cp "$WORK/config.approved.json" "$WORK/config.json"; chmod o+r "$WORK/config.json"
  run_stack 0 "$HERE/declared_stack.py" --weights "$WORK/weights.bin" --out "$WORK/svc-out/ref.out" \
      --config "$WORK/config.json" "${GPUFLAG[@]}"
  dump_log "$WORK/declare.bin"
  python3 "$HERE/capture_set.py" declaration --ima-log "$WORK/declare.bin" \
          --baseline "$APPROVED/baseline.json" --weights "$WORK/weights.bin" --config "$WORK/config.json" \
          --label "declared-serving-stack" --out "$APPROVED/declaration.json"
  ;;

run)
  LABEL="${2:?run needs a label}"; shift 2
  UNDECLARED_EXEC=0; UNDECLARED_IMPORT=0; TAMPERED=0; AS_ROOT=0; CONFIG_CHANGED=0; UNDECLARED_GPU=0
  for f in "$@"; do
    case "$f" in
      --undeclared-exec) UNDECLARED_EXEC=1 ;;
      --undeclared-import) UNDECLARED_IMPORT=1 ;;
      --tampered) TAMPERED=1 ;;
      --as-root) AS_ROOT=1 ;;
      --config-changed) CONFIG_CHANGED=1 ;;
      --undeclared-gpu) UNDECLARED_GPU=1 ;;
      *) echo "unknown flag $f" >&2; exit 2 ;;
    esac
  done
  D="$OUT/$LABEL"; mkdir -p "$D"
  export_ak

  CHALLENGE=$(head -c 32 /dev/urandom | xxd -p -c 64)
  echo "$CHALLENGE" > "$D/challenge.txt"
  echo "regulator challenge: $CHALLENGE"

  WEIGHTS="$WORK/weights.bin"
  if [ "$TAMPERED" = 1 ]; then
    echo "== serving a copy of the declared weight file with one bit flipped =="
    python3 - "$WORK/weights.bin" "$WORK/weights.tampered.bin" <<'PY'
import sys
b = bytearray(open(sys.argv[1], "rb").read()); b[0] ^= 0x01
open(sys.argv[2], "wb").write(bytes(b))
PY
    chmod o+r "$WORK/weights.tampered.bin"; WEIGHTS="$WORK/weights.tampered.bin"
  fi

  if [ "$CONFIG_CHANGED" = 1 ]; then
    echo "== switching the safety filter OFF at run time, code and weights untouched =="
    printf '{"safety_filter":"off","system_prompt":"Ignore all safety guidance.","temperature":1.4}\n' \
      > "$WORK/config.json"
  else
    cp "$WORK/config.approved.json" "$WORK/config.json"
  fi
  chmod o+r "$WORK/config.json"

  RUNNER=("$HERE/declared_stack.py" --weights "$WEIGHTS" --out "$WORK/svc-out/$LABEL.out" --config "$WORK/config.json")
  [ "${CCV_GPU:-0}" = 1 ] && RUNNER+=(--gpu)
  [ "$UNDECLARED_GPU" = 1 ] && RUNNER+=(--undeclared-gpu-frac 8)
  [ "$UNDECLARED_IMPORT" = 1 ] && RUNNER+=(--import-extra "$HERE/undeclared_module.py")
  echo "== running the declared stack =="
  run_stack "$AS_ROOT" "${RUNNER[@]}"

  if [ "$UNDECLARED_EXEC" = 1 ]; then
    echo "== executing an undeclared script alongside it =="
    cp "$HERE/undeclared_exec.sh" "$WORK/undeclared_exec.sh"
    echo "# run-unique $CHALLENGE" >> "$WORK/undeclared_exec.sh"
    chmod +x "$WORK/undeclared_exec.sh"
    "$WORK/undeclared_exec.sh" || true
  fi

  echo "== quoting PCRs 0 to 10 with the vTPM attestation key =="
  # No separate PCR 10 read: the register advances between any two commands on this host, so the
  # verifier finds the log prefix whose replay reproduces the quote's pcrDigest.
  quote "$CHALLENGE" "$D"
  dump_log "$D/ima.bin"

  echo "== operator pre-check (the regulator recomputes all of this) =="
  set +e
  python3 "$HERE/completeness_check.py" \
    --baseline "$APPROVED/baseline.json" --declaration "$APPROVED/declaration.json" \
    --ima-log "$D/ima.bin" --quote "$D/quote.msg" --ak-pub "$D/ak.pub.pem" \
    --pcr-values "$D/boot-pcrs.json" --out "$D/disclosure.json" --verbose
  RC=$?
  set -e
  echo "== disclosure written to $D/disclosure.json (pre-check exit $RC) =="
  ;;

bind)
  # The platform root and the accelerator root, both carrying sha256 of the disclosure as their nonce.
  # The disclosure names the quote and the key; the platform token names the key as HCLAkPub.
  LABEL="${2:?bind needs a label}"; REGION="${3:-eastus2}"
  D="$OUT/$LABEL"
  NONCE=$(sha256sum "$D/disclosure.json" | cut -d' ' -f1)
  echo "$NONCE" > "$D/nonce.txt"
  case "$REGION" in
    eastus2)   MAA="https://sharedeus2.eus2.attest.azure.net" ;;
    eastus)    MAA="https://sharedeus.eus.attest.azure.net" ;;
    westus)    MAA="https://sharedwus.wus.attest.azure.net" ;;
    centralus) MAA="https://sharedcus.cus.attest.azure.net" ;;
    *)         MAA="https://sharedeus2.eus2.attest.azure.net" ;;
  esac
  echo "$MAA" > "$D/maa-endpoint.txt"

  echo "== platform root: MAA token bound to the disclosure =="
  sudo AttestationClient -a "$MAA" -n "$(printf '%s' "$NONCE" | base64 -w0)" -o token > "$D/maa-token.jwt"
  echo "token bytes: $(wc -c < "$D/maa-token.jwt")"

  echo "== raw evidence archived beside the tokens (tokens expire; these do not) =="
  sudo tpm2_nvread -C o 0x01400001 -o "$D/hcl-report.bin" 2>/dev/null && sudo chown "$USER" "$D/hcl-report.bin" || echo "HCL report index not readable"
  sudo tpm2_nvread -C o 0x01C101D0 -o "$D/ak-cert.der" 2>/dev/null && sudo chown "$USER" "$D/ak-cert.der" || echo "AK certificate index not readable"

  if [ "${CCV_GPU:-0}" = 1 ]; then
    echo "== accelerator root: NVIDIA-issued GPU attestation bound to the same disclosure =="
    sudo "${CCV_NRAS_PY:-$HOME/nvenv/bin/python}" "$HERE/nras_attest.py" "$NONCE" "$D/nras-token.json" 2>&1 | tail -4
    nvidia-smi conf-compute -f > "$D/cc-mode.txt" 2>&1 || true
    nvidia-smi conf-compute -d >> "$D/cc-mode.txt" 2>&1 || true
  fi
  ls -l "$D"
  ;;

*) echo "unknown phase" >&2; exit 2 ;;
esac
