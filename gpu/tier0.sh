#!/usr/bin/env bash
# Tier 0: get a real H100 confidential-computing attestation and bind it to the CPU enclave's
# SEV-SNP report under one nonce, plus the three riders.
#
# The claim this tier moves: the attested boundary spans the model computation. Every CPU
# milestone so far attests host-side code while the AI work runs on an accelerator outside the
# enclave. This is the link that closes that gap, and without it the scheme does not bind AI
# compute at all.
#
# Usage: ./tier0.sh <disclosure.json>     (the nonce is sha256 of that file)
set -uo pipefail

OUT="$HOME/t0-out"; mkdir -p "$OUT"
DISCLOSURE="${1:-}"
if [ -n "$DISCLOSURE" ]; then
  NONCE=$(sha256sum "$DISCLOSURE" | cut -d' ' -f1)
else
  NONCE=$(head -c 32 /dev/urandom | xxd -p -c 64)
fi
echo "nonce (64 hex = 32 bytes, what every tool in this path accepts): $NONCE"
echo "$NONCE" > "$OUT/nonce.txt"

echo "=================== platform and tool versions ==================="
{
  echo "date: $(date -Is)"
  echo "kernel: $(uname -r)"
  echo "cmdline: $(cat /proc/cmdline)"
  nvidia-smi --query-gpu=name,driver_version,vbios_version,serial,memory.total --format=csv
  nvcc --version 2>/dev/null | tail -2
  python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.version.cuda)" 2>/dev/null
} 2>&1 | tee "$OUT/platform.txt"

echo "=================== CPU side: SEV-SNP confirmation ==================="
sudo dmesg 2>/dev/null | grep -i -E "sev|snp" | head -5
ls -l /dev/tpm0 /dev/tpmrm0 2>/dev/null
[ -e /dev/sev-guest ] && echo "/dev/sev-guest present" || echo "no /dev/sev-guest (vTPM attestation path, as in M1)"

echo "=================== GPU side: confidential-compute mode ==================="
{
  echo "--- conf-compute -f (CC status) ---";      nvidia-smi conf-compute -f
  echo "--- conf-compute -q (full state) ---";     nvidia-smi conf-compute -q
  echo "--- conf-compute -d (DevTools mode) ---";  nvidia-smi conf-compute -d
  echo "--- conf-compute -grs (ready state) ---";  nvidia-smi conf-compute -grs
} 2>&1 | tee "$OUT/cc-mode.txt"

echo "=================== setting the GPU ready state ==================="
sudo nvidia-smi conf-compute -srs 1 2>&1 | tee -a "$OUT/cc-mode.txt"

echo "=================== GPU attestation ==================="
# The VMI ships a wrapper; the current supported tooling is the nvattest CLI / NVAT SDK, since
# the Python nv-attestation-sdk is deprecated (2026-03-15, EOL 2026-09-15) and the standalone
# local verifier is no longer supported. Record which one is actually present.
{
  echo "--- which tools exist ---"
  for t in nvattest gpu-attestation cc-admin; do
    command -v "$t" >/dev/null 2>&1 && echo "present: $t ($($t --version 2>&1 | head -1))" || echo "absent: $t"
  done
  python3 -c "import nv_attestation_sdk, os; print('python nv-attestation-sdk present:', os.path.dirname(nv_attestation_sdk.__file__))" 2>/dev/null \
    || echo "python nv-attestation-sdk: not importable"
} 2>&1 | tee "$OUT/attest-tooling.txt"

echo "--- smoke test: the VMI's own wrappers ---"
sudo cpu-attestation 2>&1 | tail -5 | tee "$OUT/cpu-attestation.txt"
sudo gpu-attestation 2>&1 | tail -20 | tee "$OUT/gpu-attestation-smoke.txt"

echo "--- nonce-bound attestation ---"
if command -v nvattest >/dev/null 2>&1; then
  nvattest attest --nonce "$NONCE" -o "$OUT/gpu-eat.json" 2>&1 | tee "$OUT/nvattest.txt" \
    || nvattest --nonce "$NONCE" 2>&1 | tee -a "$OUT/nvattest.txt"
else
  python3 - "$NONCE" "$OUT" <<'PY' 2>&1 | tee "$OUT/nvattest.txt"
import json, sys
nonce, out = sys.argv[1], sys.argv[2]
try:
    from nv_attestation_sdk import attestation
except Exception as e:
    print("no python SDK either:", e); raise SystemExit(3)
c = attestation.Attestation()
c.set_name("ccverify"); c.set_nonce(nonce)
c.add_verifier(attestation.Devices.GPU, attestation.Environment.REMOTE,
               "https://nras.attestation.nvidia.com/v3/attest/gpu", "")
ev = c.get_evidence()
tok = c.attest(ev)
print("attest returned:", tok)
open(f"{out}/gpu-eat.json", "w").write(json.dumps(c.get_token(), indent=2))
PY
fi

echo "=================== riders (all three, results are the point) ==================="
echo "--- rider a: clocks in CC-On ---" | tee "$OUT/riders.txt"
{
  echo "### nvidia-smi -q -d CLOCK"
  nvidia-smi -q -d CLOCK
  echo "### attempt to lock graphics clocks"
  sudo nvidia-smi -lgc 1200,1200; echo "lgc exit: $?"
  echo "### re-read after the lock attempt"
  nvidia-smi -q -d CLOCK | grep -A6 -i "clocks event\|applications clocks\|sm clock" | head -30
  echo "### is the locked state externally queryable"
  nvidia-smi --query-gpu=clocks.applications.graphics,clocks.max.graphics,clocks.gr --format=csv
  echo "### release"
  sudo nvidia-smi -rgc; echo "rgc exit: $?"
} 2>&1 | tee -a "$OUT/riders.txt"

echo "--- rider b(i): CUPTI / device profiling counters (whitepaper says disabled in CC-On) ---" | tee -a "$OUT/riders.txt"
{
  if command -v ncu >/dev/null 2>&1; then
    ncu --version | head -2
    ncu --metrics sm__cycles_elapsed.avg --target-processes all python3 -c "import torch;torch.randn(64,64,device='cuda')@torch.randn(64,64,device='cuda');torch.cuda.synchronize()"
    echo "ncu exit: $?"
  else
    echo "ncu (Nsight Compute) not installed on the VMI"
  fi
  if command -v nsys >/dev/null 2>&1; then
    nsys profile --gpu-metrics-devices=all -o /tmp/nsysprof python3 -c "import torch;torch.randn(64,64,device='cuda')@torch.randn(64,64,device='cuda');torch.cuda.synchronize()" 2>&1 | tail -20
    echo "nsys exit: $?"
  else
    echo "nsys (Nsight Systems) not installed on the VMI"
  fi
} 2>&1 | tee -a "$OUT/riders.txt"

echo "--- rider b(ii): NVML card-health telemetry, a DIFFERENT path, may still read ---" | tee -a "$OUT/riders.txt"
{
  nvidia-smi -q -d UTILIZATION | head -25
  nvidia-smi --query-gpu=utilization.gpu,utilization.memory,power.draw,temperature.gpu --format=csv
} 2>&1 | tee -a "$OUT/riders.txt"

echo "--- rider c: mode reporting, so a verifier can tell CC-On from CC-DevTools ---" | tee -a "$OUT/riders.txt"
nvidia-smi conf-compute -d 2>&1 | tee -a "$OUT/riders.txt"

echo "=================== tier 0 artifacts ==================="
ls -l "$OUT"
