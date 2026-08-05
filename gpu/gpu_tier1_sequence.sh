#!/usr/bin/env bash
# The Tier 1 evidence sequence, run on a freshly booted GPU box, in one go.
#
# Order is not arbitrary and each step is there for a reason found the hard way:
#
#   quiesce      a stock cloud image executes things on timers and on every SSH login, and each
#                one is a file execution IMA measures. Left alone they land after the baseline
#                is frozen and read as undeclared, so the box false-rejects on nothing.
#   settle       Azure's own in-guest security agent (azsec-monitor / auoms) runs on a cycle of
#                its own. Waiting for a cycle before freezing the baseline is what puts the
#                CLOUD PROVIDER'S agents inside the approved platform set, where they belong.
#                That they have to be there at all is a finding: a regulator checking a
#                cloud-hosted operator inherits the provider's in-guest agents into the set it
#                is approving, and the operator does not control that set.
#   warmup       IMA measures a file once per boot, so any tool whose first execution happens
#                mid-run would be attributed to the workload. Run them all first.
#   declare      the reference run that fixes the declaration, which must happen AFTER the
#                baseline is frozen so the stack's own files land in the declaration.
#   cases        accept first, then the ones that poison the domain. PCR 10 is cumulative and
#                monotonic, so once something undeclared has run, no later run in the same boot
#                can come back clean. That is the correct semantics, and it means ordering the
#                cases wrong destroys the experiment.
set -euo pipefail
SETTLE="${1:-300}"

cd ~/t1/bin

echo "== vTPM access and platform quiesce =="
sudo setfacl -m u:"$USER":rw /dev/tpmrm0 /dev/tpm0
for t in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service \
         update-notifier-download.timer update-notifier-motd.timer motd-news.timer \
         sysstat-collect.timer sysstat-summary.timer man-db.timer fstrim.timer \
         e2scrub_all.timer logrotate.timer dpkg-db-backup.timer ua-timer.timer; do
  sudo systemctl stop "$t" 2>/dev/null || true
  sudo systemctl disable "$t" 2>/dev/null || true
done
sudo systemctl stop cron 2>/dev/null || true
sudo chmod -x /etc/update-motd.d/* 2>/dev/null || true

echo "== GPU ready state =="
sudo nvidia-smi conf-compute -srs 1
nvidia-smi conf-compute -f

echo "== IMA policy (runtime write: this image has no /etc/default/grub, the kernel command"
echo "   line is managed by the snapd FDE boot chain, so the boot-pinned route is unavailable) =="
printf 'measure func=POLICY_CHECK\nmeasure func=BPRM_CHECK mask=MAY_EXEC\nmeasure func=MMAP_CHECK mask=MAY_EXEC\nmeasure func=FILE_CHECK mask=^MAY_READ euid=0\nmeasure func=MODULE_CHECK\n' \
  | sudo tee /sys/kernel/security/ima/policy >/dev/null
echo "policy loaded; second write: $(printf 'measure func=BPRM_CHECK mask=MAY_EXEC\n' | sudo tee /sys/kernel/security/ima/policy >/dev/null 2>&1 && echo ACCEPTED || echo DENIED)"

rm -rf ~/t1/work ~/t1/out ~/t1/approved
export CCV_GPU=1

echo "== warmup =="
./tier1.sh warmup 2>&1 | tail -3

echo "== settling ${SETTLE}s so the provider's in-guest agents cycle into the platform set =="
sleep "$SETTLE"

echo "== baseline =="
./tier1.sh baseline

echo "== declaration (approved reference run, real GPU work) =="
./tier1.sh declare 2>&1 | tail -3

# Case order matters twice over. PCR 10 is cumulative, so a case that runs something undeclared
# contaminates every case after it in the same boot. And the tamper case uses tools the pure
# completeness cases do not, so it goes after them.
# The as-root case goes LAST because the tcb read rule makes a root-run interpreter measure its
# whole data footprint, roughly a thousand entries. That is the point of that case, but it swamps
# the count for anything after it, and the undeclared-exec case is the paper's reject figure, so
# it needs to show its one entry and not inherit a thousand.
for c in "accept" "undeclared-import --undeclared-import" "tampered --tampered" \
         "undeclared-exec --undeclared-exec" "import-as-root --undeclared-import --as-root"; do
  echo "########## case: $c"
  ./tier1.sh run $c 2>&1 | grep -E '"verdict"|"undeclared_entries"|"ima_entries_checked"|"weights_match_declaration"|"rejected_by"|log replay' || true
done

echo "== binding the accept and reject cases to both hardware roots =="
./tier1.sh bind accept eastus2 2>&1 | tail -6
./tier1.sh bind undeclared-exec eastus2 2>&1 | tail -6
