#!/usr/bin/env bash
# Tier 1 substrate setup, run once on the confidential VM (CPU dev box or GPU box, same script).
#
# Two things get turned on:
#   1. tpm2-tools plus non-root access to the vTPM resource manager, so the workload user can
#      take a quote without being root (root matters: the IMA tcb policy measures files READ by
#      euid 0, which would make every disclosure write show up in the log as noise).
#   2. Linux IMA with the tcb policy, so PCR 10 accumulates the digest of every executed file.
#      Prefer the runtime policy write if the kernel allows it. Otherwise set the kernel command
#      line and reboot, which is the guaranteed path.
#
# Exit code 10 means "IMA needs a reboot, run me again after it comes back".
set -euo pipefail

echo "== platform =="
uname -r
sudo dmesg 2>/dev/null | grep -i -E "sev|snp" | head -5 || true
ls -l /dev/tpm0 /dev/tpmrm0 2>/dev/null || echo "WARNING: no vTPM device"

echo "== packages =="
sudo apt-get update -q
sudo apt-get install -y -q tpm2-tools acl jq python3 python3-pip >/dev/null
tpm2_getcap --version || true

echo "== vTPM access for $USER (no root needed for quoting) =="
sudo usermod -aG tss "$USER" || true
sudo setfacl -m u:"$USER":rw /dev/tpmrm0 || true
sudo setfacl -m u:"$USER":rw /dev/tpm0 || true

echo "== quiescing the platform's own background activity =="
# A live general-purpose host keeps executing things on timers: apt, unattended upgrades,
# sysstat, update-notifier, motd. Every one of those is a file execution IMA measures, so on a
# stock image they land in the log after the baseline is frozen and read as undeclared. A real
# serving host runs a minimal image; here we quiet the stock one and say that we did. This IS a
# feasibility finding, not housekeeping: an allowlist-based completeness check on a live host
# has to cover the host's own periodic activity or it produces false rejects.
for t in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service \
         update-notifier-download.timer update-notifier-motd.timer motd-news.timer \
         sysstat-collect.timer sysstat-summary.timer man-db.timer fstrim.timer \
         e2scrub_all.timer logrotate.timer dpkg-db-backup.timer; do
  sudo systemctl stop "$t" 2>/dev/null || true
  sudo systemctl disable "$t" 2>/dev/null || true
done
sudo systemctl stop cron 2>/dev/null || true
# Every SSH login runs the MOTD pipeline (ubuntu-distro-info, cloud-id, the ubuntu-advantage
# python modules). Those executions land in the log after the baseline is frozen and read as
# undeclared, so a stock image false-rejects on nothing more than an administrator logging in.
sudo chmod -x /etc/update-motd.d/* 2>/dev/null || true
sudo systemctl stop ua-timer.timer ubuntu-advantage.service esm-cache.service 2>/dev/null || true
sudo systemctl disable ua-timer.timer 2>/dev/null || true
echo "timers and the motd pipeline stopped"
sleep 5

echo "== IMA =="
# A log existing is not enough. Ubuntu with secure boot ships the architecture policy, which
# measures kernel modules and firmware only, so executables never reach PCR 10. Tier 1 needs
# a policy that measures execution, which is what tcb adds.
if grep -q 'ima_policy=tcb' /proc/cmdline; then
  echo "IMA tcb policy active"
  echo "current kernel cmdline: $(cat /proc/cmdline)"
else
  echo "IMA is not measuring executions yet (cmdline: $(cat /proc/cmdline))"
  if [ "${1:-}" = "--dev" ] && sudo test -w /sys/kernel/security/ima/policy; then
    # Dev convenience only. For an evidence run prefer the kernel command line, so the policy
    # itself is fixed by measured boot rather than by a write the operator could have made
    # differently. A runtime-writable policy is a real weakness of this route and gets said so
    # in the write-up. The rules below mirror what ima_policy=tcb installs.
    echo "kernel allows a runtime policy write, using it (no reboot)"
    printf '%s\n' \
      'measure func=BPRM_CHECK mask=MAY_EXEC' \
      'measure func=MMAP_CHECK mask=MAY_EXEC' \
      'measure func=FILE_CHECK mask=^MAY_READ euid=0' \
      'measure func=MODULE_CHECK' \
      | sudo tee /sys/kernel/security/ima/policy >/dev/null
  else
    # Friction finding 11: editing /etc/default/grub does nothing on an Azure Ubuntu cloud
    # image. /etc/default/grub.d/50-cloudimg-settings.cfg is sourced afterwards and overwrites
    # GRUB_CMDLINE_LINUX_DEFAULT outright. The append has to land in a file that sorts last.
    echo "setting ima_policy=tcb on the kernel command line and rebooting"
    printf '%s\n' \
      'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT ima_policy=tcb"' \
      | sudo tee /etc/default/grub.d/99-ima.cfg >/dev/null
    sudo update-grub 2>&1 | tail -3
    grep -o 'ima_policy=tcb' /boot/grub/grub.cfg | head -1 || echo "WARNING: ima_policy not in grub.cfg"
    echo "REBOOT REQUIRED"
    exit 10
  fi
fi

echo "== IMA log head =="
sudo head -3 /sys/kernel/security/ima/ascii_runtime_measurements || true
echo "entries: $(sudo cat /sys/kernel/security/ima/ascii_runtime_measurements | wc -l)"
echo "executables measured: $(sudo cat /sys/kernel/security/ima/ascii_runtime_measurements | grep -c '/usr/bin\|/bin/\|/usr/sbin' || true)"
echo "== setup done =="
