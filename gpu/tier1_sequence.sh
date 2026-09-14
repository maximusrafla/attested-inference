#!/usr/bin/env bash
# The Tier 1 evidence sequence (2026-09-14 revision), run once on a freshly booted confidential VM.
#
#   tier1_sequence.sh <region> [settle-seconds]      CCV_GPU=1 on the H100 box
#
# Order matters. PCR 10 and the measurement log are cumulative since boot, and every case below except
# the first two puts an undeclared digest into the log, so every later window inherits it. Each reject
# case is therefore reported with the undeclared entry it ADDED, which the verifier lists by path.
# Binding to the platform and GPU roots happens after every quote, so the attestation client's own
# activity lands outside every attested window.
set -euo pipefail
REGION="${1:?region, e.g. eastus2}"
SETTLE="${2:-120}"
SVC="${CCV_SVC:-ccvsvc}"
cd "$(dirname "$0")"

echo "== vTPM access, serving account, platform quiesce =="
sudo setfacl -m u:"$USER":rw /dev/tpmrm0 /dev/tpm0
id "$SVC" >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC"
SVC_UID=$(id -u "$SVC")
for t in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service \
         update-notifier-download.timer update-notifier-motd.timer motd-news.timer \
         sysstat-collect.timer sysstat-summary.timer man-db.timer fstrim.timer \
         e2scrub_all.timer logrotate.timer dpkg-db-backup.timer ua-timer.timer; do
  sudo systemctl stop "$t" 2>/dev/null || true
  sudo systemctl disable "$t" 2>/dev/null || true
done
sudo systemctl stop cron 2>/dev/null || true
sudo chmod -x /etc/update-motd.d/* 2>/dev/null || true

if [ "${CCV_GPU:-0}" = 1 ]; then
  echo "== GPU ready state =="
  sudo nvidia-smi conf-compute -srs 1
  nvidia-smi conf-compute -f
fi

echo "== IMA policy: written once, after boot (this image's kernel command line is inside a signed UKI) =="
# The first rules keep pseudo-filesystem reads out of FILE_CHECK only, so program launches from
# in-memory files (BPRM_CHECK) are still measured. The serving-account read rule is what makes the
# weight and configuration files, and any other file the declared stack reads, part of the log.
POLICY=$(printf '%s\n' \
  'dont_measure fsmagic=0x9fa0 func=FILE_CHECK' \
  'dont_measure fsmagic=0x62656572 func=FILE_CHECK' \
  'dont_measure fsmagic=0x73636673 func=FILE_CHECK' \
  'dont_measure fsmagic=0x63677270 func=FILE_CHECK' \
  'dont_measure fsmagic=0x27e0eb func=FILE_CHECK' \
  'dont_measure fsmagic=0xde5e81e4 func=FILE_CHECK' \
  'measure func=POLICY_CHECK' \
  'measure func=BPRM_CHECK mask=MAY_EXEC' \
  'measure func=MMAP_CHECK mask=MAY_EXEC' \
  'measure func=FILE_CHECK mask=^MAY_READ euid=0' \
  "measure func=FILE_CHECK mask=^MAY_READ uid=$SVC_UID" \
  'measure func=MODULE_CHECK')
echo "$POLICY" > ~/ima-policy-in-force.txt
printf '%s\n' "$POLICY" | sudo tee /sys/kernel/security/ima/policy >/dev/null
echo "policy loaded ($(wc -l < ~/ima-policy-in-force.txt) rules); second write: $(printf 'measure func=BPRM_CHECK mask=MAY_EXEC\n' | sudo tee /sys/kernel/security/ima/policy >/dev/null 2>&1 && echo ACCEPTED || echo DENIED)"

rm -rf ~/t1/work ~/t1/out ~/t1/approved

echo "== warmup =="
./tier1.sh warmup 2>&1 | tail -3
echo "== settling ${SETTLE}s so the provider's in-guest agents cycle into the platform set =="
sleep "$SETTLE"
echo "== baseline =="
./tier1.sh baseline
echo "== declaration (approved reference run, as $SVC) =="
./tier1.sh declare 2>&1 | tail -3

CASES=("accept")
[ "${CCV_GPU:-0}" = 1 ] && CASES+=("undeclared-gpu --undeclared-gpu")
CASES+=("tampered --tampered" "config-changed --config-changed" \
        "undeclared-import --undeclared-import" "undeclared-exec --undeclared-exec")
# CCV_CASES overrides the list, e.g. "undeclared-gpu --undeclared-gpu|accept" for a clean pair.
if [ -n "${CCV_CASES:-}" ]; then IFS='|' read -r -a CASES <<< "$CCV_CASES"; fi
for c in "${CASES[@]}"; do
  echo "########## case: $c"
  ./tier1.sh run $c 2>&1 | grep -E '"verdict"|"undeclared_entries"|"ima_entries_checked"|"weights_in|"config_in|"rejected_by"|undeclared measurements|  /|log window' || true
done

echo "== binding every case to the platform and accelerator roots =="
for c in "${CASES[@]}"; do
  ./tier1.sh bind "${c%% *}" "$REGION" 2>&1 | grep -E "token bytes|not readable|NRAS|error" || true
done
cp ~/ima-policy-in-force.txt ~/t1/out/
echo "== sequence done =="
