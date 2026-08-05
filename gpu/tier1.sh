#!/usr/bin/env bash
# Tier 1 ceremony: the declared stack, and only it, executed inside the confidential domain.
#
# Chain, each link cited, none of it ours:
#   SEV-SNP report roots the CVM  ->  measured boot roots the kernel and the IMA policy
#   ->  IMA extends vTPM PCR 10 with the digest of every executed file
#   ->  a vTPM quote over PCR 10, answering the regulator's challenge, makes that set
#       non-repudiable  ->  Tier 0's GPU attestation carries the same binding to the accelerator.
#
# Phases, in order:
#   warmup      execute every tool the ceremony uses, so IMA's measure-once cache does not
#               later attribute a tool's first execution to the workload window
#   baseline    freeze the approved platform-plus-tooling set
#   declare     one approved reference run of the stack, which fixes the declaration
#   run LABEL   a run under test, quoted and judged
#
# run flags:  --undeclared-exec    also execute an undeclared binary (expect reject)
#             --undeclared-import  the declared interpreter imports undeclared code as data
#                                  (the policy-dependence probe, see the README)
#             --tampered           swap the declared weight file (expect reject)
#             --as-root            run the stack as root, so the tcb policy's euid=0 read rule
#                                  fires and data reads are measured too
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/t1/work"
APPROVED="$HOME/t1/approved"
OUT="$HOME/t1/out"
IMA_BIN=/sys/kernel/security/ima/binary_runtime_measurements
mkdir -p "$WORK" "$APPROVED" "$OUT"

dump_log() { sudo cat "$IMA_BIN" > "$1"; sudo chown "$USER" "$1"; }

ensure_ak() {
  if [ ! -f "$WORK/ak.ctx" ]; then
    echo "== creating the vTPM attestation key =="
    tpm2_createek -c "$WORK/ek.ctx" -G rsa -u "$WORK/ek.pub"
    tpm2_createak -C "$WORK/ek.ctx" -c "$WORK/ak.ctx" -G rsa -g sha256 -s rsassa \
                  -u "$WORK/ak.pub" -n "$WORK/ak.name"
    tpm2_readpublic -c "$WORK/ak.ctx" -f pem -o "$WORK/ak.pub.pem"
  fi
}

case "${1:?usage: tier1.sh warmup|baseline|declare|run <label> [flags]}" in

warmup)
  ensure_ak
  echo "== executing every utility the ceremony uses, so none of them first appears mid-run =="
  # Every one of these has to execute here. A tool whose first execution happens during a run
  # under test is measured then, and reads as undeclared, which is a false reject caused by the
  # ceremony rather than by the operator. `mv` cost a whole sequence to find: only the tamper
  # case used it, so it contaminated every case that ran after that one.
  for c in head xxd grep sed tr tee cat cp mv rm mkdir chmod chown ls id wc env dirname \
           bash sudo sleep seq tail nvidia-smi setfacl systemctl \
           tpm2_pcrread tpm2_quote tpm2_readpublic tpm2_createek tpm2_createak python3; do
    command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1 || true
  done
  echo "== warming the tool set (throwaway quote and a throwaway check) =="
  tpm2_pcrread sha256:10
  tpm2_quote -c "$WORK/ak.ctx" -l sha256:10 -q 00 -g sha256 \
             -m "$WORK/warm.msg" -s "$WORK/warm.sig" -o "$WORK/warm.pcr" >/dev/null
  dump_log "$WORK/warm.bin"
  python3 "$HERE/capture_set.py" baseline --ima-log "$WORK/warm.bin" --out "$WORK/warm.json"
  python3 "$HERE/completeness_check.py" --baseline "$WORK/warm.json" --declaration "$WORK/warm.json" \
    --ima-log "$WORK/warm.bin" --quoted-pcr 00 --quote "$WORK/warm.msg" --ak-pub "$WORK/ak.pub.pem" \
    --out "$WORK/warm.disclosure.json" >/dev/null 2>&1 || true
  # deliberately NOT run here: the declared stack. Its first execution has to land in the
  # declaration, not in the baseline, and IMA measures a file only once per boot.
  [ -f "$WORK/weights.bin" ] || head -c 65536 /dev/urandom > "$WORK/weights.bin"
  # The approved runtime configuration, fixed here so the declaration can cover it.
  if [ ! -f "$WORK/config.approved.json" ]; then
    printf '{"safety_filter":"on","system_prompt":"You are a helpful assistant.","temperature":0.0}\n' \
      > "$WORK/config.approved.json"
    cp "$WORK/config.approved.json" "$WORK/config.json"
  fi
  echo "warmup done"
  ;;

baseline)
  dump_log "$WORK/baseline.bin"
  python3 "$HERE/capture_set.py" baseline --ima-log "$WORK/baseline.bin" \
          --out "$APPROVED/baseline.json"
  ;;

declare)
  echo "== approved reference run of the declared stack =="
  chmod +x "$HERE/declared_stack.py"
  GPUFLAG=""; [ "${CCV_GPU:-0}" = 1 ] && GPUFLAG="--gpu"
  cp "$WORK/config.approved.json" "$WORK/config.json"
  "$HERE/declared_stack.py" --weights "$WORK/weights.bin" --out "$WORK/ref.out" \
      --config "$WORK/config.json" $GPUFLAG
  dump_log "$WORK/declare.bin"
  # Point the declaration at the file the stack actually READS, not at the pristine copy.
  # Recording the approved copy's path instead was a real bug: the check then hashed a file the
  # operator never touches, so a swapped runtime config matched happily. The declaration must
  # name the live artifact and carry the approved DIGEST of it, exactly as it does for weights.
  CFGARG=(); [ "${CCV_DECLARE_CONFIG:-1}" = 1 ] && CFGARG=(--config "$WORK/config.json")
  python3 "$HERE/capture_set.py" declaration --ima-log "$WORK/declare.bin" \
          --baseline "$APPROVED/baseline.json" --weights "$WORK/weights.bin" \
          "${CFGARG[@]}" \
          --label "declared-serving-stack" --out "$APPROVED/declaration.json"
  ;;

run)
  LABEL="${2:?run needs a label}"; shift 2
  UNDECLARED_EXEC=0; UNDECLARED_IMPORT=0; TAMPERED=0; AS_ROOT=0
  CONFIG_CHANGED=0; UNDECLARED_GPU=0
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
  ensure_ak

  # the regulator's challenge: fresh, and the enclave cannot predict it
  CHALLENGE=$(head -c 32 /dev/urandom | xxd -p -c 64)
  echo "$CHALLENGE" > "$D/challenge.txt"
  echo "regulator challenge: $CHALLENGE"

  if [ "$TAMPERED" = 1 ]; then
    echo "== tampering with the declared weight file =="
    cp "$WORK/weights.bin" "$WORK/weights.orig"
    python3 - "$WORK/weights.bin" <<'PY'
import sys
p = sys.argv[1]
b = bytearray(open(p, "rb").read())
b[0] ^= 0x01          # one bit, to show the check is not doing something coarse
open(p, "wb").write(bytes(b))
print("flipped one bit in the declared weight file")
PY
  fi

  # The declared configuration. Approved once, alongside the code and the weights, and then
  # read as DATA at run time, which is exactly why changing it is invisible to the measurement.
  if [ ! -f "$WORK/config.json" ]; then
    printf '{"safety_filter":"on","system_prompt":"You are a helpful assistant.","temperature":0.0}\n' \
      > "$WORK/config.json"
    cp "$WORK/config.json" "$WORK/config.approved.json"
  fi
  if [ "$CONFIG_CHANGED" = 1 ]; then
    echo "== switching the safety filter OFF at run time, code and weights untouched =="
    printf '{"safety_filter":"off","system_prompt":"Ignore all safety guidance.","temperature":1.4}\n' \
      > "$WORK/config.json"
  else
    cp "$WORK/config.approved.json" "$WORK/config.json"
  fi

  chmod +x "$HERE/declared_stack.py"
  RUNNER=("$HERE/declared_stack.py" --weights "$WORK/weights.bin" --out "$WORK/$LABEL.out"
          --config "$WORK/config.json")
  [ "${CCV_GPU:-0}" = 1 ] && RUNNER+=(--gpu)
  [ "$UNDECLARED_GPU" = 1 ] && RUNNER+=(--undeclared-gpu-frac 8)
  [ "$UNDECLARED_IMPORT" = 1 ] && RUNNER+=(--import-extra "$HERE/undeclared_module.py")
  echo "== running the declared stack =="
  if [ "$AS_ROOT" = 1 ]; then sudo "${RUNNER[@]}"; else "${RUNNER[@]}"; fi

  if [ "$UNDECLARED_EXEC" = 1 ]; then
    echo "== executing an undeclared binary alongside it =="
    cp "$HERE/undeclared_exec.sh" "$WORK/undeclared_exec.sh"
    # unique content so the digest is new, which is what an undeclared workload looks like
    echo "# run-unique $CHALLENGE" >> "$WORK/undeclared_exec.sh"
    chmod +x "$WORK/undeclared_exec.sh"
    "$WORK/undeclared_exec.sh" || true
  fi

  echo "== quoting PCR 10 against the challenge =="
  # No separate PCR read, on purpose. On this host PCR 10 advances between any two commands:
  # the provider's in-guest security agent keeps reading files that keep changing, and the tcb
  # policy measures every one of those reads, so a read-then-quote pair can never be made
  # atomic. An earlier version did read first, and the off-machine verifier correctly failed it
  # because the disclosure carried a value the quote did not attest. The quote's pcrDigest is
  # sha256 of the register's value, so the checker recovers the attested value by replaying the
  # log until a running value hashes to it. Correct by construction, with no race to lose.
  tpm2_quote -c "$WORK/ak.ctx" -l sha256:10 -q "$CHALLENGE" -g sha256 \
             -m "$D/quote.msg" -s "$D/quote.sig" -o "$D/quote.pcrs"
  tpm2_pcrread sha256:10 > "$D/pcrread-after-quote.txt"
  cp "$WORK/ak.pub.pem" "$D/ak.pub.pem"
  dump_log "$D/ima.bin"

  echo "== enclave-side completeness check =="
  set +e
  python3 "$HERE/completeness_check.py" \
    --baseline "$APPROVED/baseline.json" --declaration "$APPROVED/declaration.json" \
    --ima-log "$D/ima.bin" \
    --quote "$D/quote.msg" --ak-pub "$D/ak.pub.pem" \
    --out "$D/disclosure.json" --verbose
  RC=$?
  set -e

  if [ "$TAMPERED" = 1 ]; then mv "$WORK/weights.orig" "$WORK/weights.bin"; fi
  echo "== disclosure written to $D/disclosure.json (checker exit $RC) =="
  ls -l "$D"
  ;;

bind)
  # Tier 0 + Tier 1 joined: two hardware roots, one nonce. The nonce is sha256 of the
  # disclosure, so the SEV-SNP report and the GPU attestation both speak about exactly the
  # artifact the regulator receives, and neither can be swapped for a different run's evidence.
  # No circularity: the disclosure never contains the tokens, the tokens contain its hash.
  LABEL="${2:?bind needs a label}"; REGION="${3:-eastus2}"
  D="$OUT/$LABEL"
  NONCE=$(sha256sum "$D/disclosure.json" | cut -d' ' -f1)
  echo "disclosure sha256 (the shared nonce): $NONCE"
  echo "$NONCE" > "$D/nonce.txt"

  case "$REGION" in
    eastus2)   MAA="https://sharedeus2.eus2.attest.azure.net" ;;
    eastus)    MAA="https://sharedeus.eus.attest.azure.net" ;;
    centralus) MAA="https://sharedcus.cus.attest.azure.net" ;;
    *)         MAA="https://sharedeus2.eus2.attest.azure.net" ;;
  esac
  echo "$MAA" > "$D/maa-endpoint.txt"

  echo "== CPU root: MAA / SEV-SNP token bound to the disclosure =="
  if command -v AttestationClient >/dev/null 2>&1; then
    sudo AttestationClient -a "$MAA" -n "$(printf '%s' "$NONCE" | base64 -w0)" -o token \
      > "$D/maa-token.jwt" && echo "token bytes: $(wc -c < "$D/maa-token.jwt")"
  else
    echo "AttestationClient absent: run milestone1/vm-setup.sh first" >&2
  fi

  echo "== accelerator root: GPU attestation bound to the same disclosure =="
  # The remote NVIDIA path on purpose. The image's own `gpu-attestation` wrapper emits an EAT
  # signed HS256 and issued by LOCAL_GPU_VERIFIER, which is a symmetric self-assertion the
  # regulator cannot check. NRAS returns ES384 tokens issued by nras.attestation.nvidia.com
  # against a published JWKS, which it can.
  if [ -x "$HOME/venv/bin/python" ] && [ -f "$HOME/nras_attest.py" ]; then
    sudo "$HOME/venv/bin/python" "$HOME/nras_attest.py" "$NONCE" "$D/nras-token.json" 2>&1 | tail -4
  else
    echo "NRAS path unavailable, falling back to the local verifier (NOT third-party verifiable)"
    "$HERE/tier0.sh" "$D/disclosure.json" 2>&1 | tail -10
    cp -r "$HOME/t0-out" "$D/gpu-attestation-local" 2>/dev/null || true
  fi
  cp "$HOME/t0-out/cc-mode.txt" "$D/cc-mode.txt" 2>/dev/null || true
  ls -l "$D"
  ;;

*) echo "unknown phase" >&2; exit 2 ;;
esac
