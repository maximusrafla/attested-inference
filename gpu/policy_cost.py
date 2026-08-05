#!/usr/bin/env python3
"""What the widened measurement policy costs the OPERATOR, in time rather than in log entries.

994 measured entries against 1 is a verifier-side burden. A regulator will also ask what the
policy costs the party being regulated, because that is the number that decides whether anyone
would agree to run it. This measures latency and throughput on the same workloads under the
default policy and under the widened one.

The shape of the cost matters as much as the size. IMA hashes a file once per boot and caches the
result, so the price is paid on FIRST touch and is proportional to the bytes hashed, not to how
often the file is used afterwards. So each workload is run cold (fresh, never-measured files) and
warm (the same files again), and the difference between those two is the real story.

Run once per boot under each policy, since the policy is write-once per boot:
    sudo ./policy_cost.py --label default   --out cost-default.json      # before any policy write
    (set the widened policy, reboot is not needed if it has not been written yet)
    sudo ./policy_cost.py --label widened   --out cost-widened.json
"""

import argparse
import json
import os
import shutil
import subprocess
import statistics
import tempfile
import time
from pathlib import Path


def timed(fn, repeats=5):
    xs = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - t0)
    return {"median_s": statistics.median(xs), "min_s": min(xs), "runs": xs}


def make_execs(d, n, payload_kb):
    """n unique executables, so every exec is a first touch and must be hashed."""
    paths = []
    filler = "#" + "x" * (payload_kb * 1024)
    for i in range(n):
        p = Path(d) / f"e{i}.sh"
        p.write_text(f"#!/bin/sh\nexit 0\n{filler}{i}\n")
        p.chmod(0o755)
        paths.append(str(p))
    return paths


def make_files(d, n, size_kb):
    paths = []
    for i in range(n):
        p = Path(d) / f"f{i}.bin"
        p.write_bytes(os.urandom(size_kb * 1024))
        paths.append(str(p))
    return paths


def exec_all(paths):
    for p in paths:
        subprocess.run([p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_all(paths):
    for p in paths:
        with open(p, "rb") as f:
            while f.read(1 << 20):
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-exec", type=int, default=200)
    ap.add_argument("--exec-kb", type=int, default=64)
    ap.add_argument("--n-read", type=int, default=100)
    ap.add_argument("--read-mb", type=int, default=4)
    args = ap.parse_args()

    policy = ""
    try:
        policy = subprocess.run(["cat", "/sys/kernel/security/ima/policy"],
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        pass
    n_entries_before = int(subprocess.run(
        ["cat", "/sys/kernel/security/ima/runtime_measurements_count"],
        capture_output=True, text=True).stdout.strip() or 0)

    d = tempfile.mkdtemp(prefix="imacost-")
    os.chmod(d, 0o755)
    results = {"label": args.label, "policy_in_force": policy or "(not readable)",
               "entries_before": n_entries_before}

    # exec workload: unique binaries, so cold means every one has to be hashed
    execs = make_execs(d, args.n_exec, args.exec_kb)
    results["exec_cold"] = timed(lambda: exec_all(execs), repeats=1)
    results["exec_warm"] = timed(lambda: exec_all(execs), repeats=5)
    results["exec_bytes"] = args.n_exec * args.exec_kb * 1024

    # read workload as root: the FILE_CHECK euid=0 rule is what the widened policy adds
    reads = make_files(d, args.n_read, args.read_mb * 1024)
    results["read_cold"] = timed(lambda: read_all(reads), repeats=1)
    results["read_warm"] = timed(lambda: read_all(reads), repeats=3)
    results["read_bytes"] = args.n_read * args.read_mb * 1024 * 1024

    # a realistic proxy for operator latency: interpreter startup, which touches many libraries
    results["python_startup"] = timed(
        lambda: subprocess.run(["python3", "-c", "pass"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        repeats=10)

    results["entries_after"] = int(subprocess.run(
        ["cat", "/sys/kernel/security/ima/runtime_measurements_count"],
        capture_output=True, text=True).stdout.strip() or 0)
    results["entries_added"] = results["entries_after"] - n_entries_before
    shutil.rmtree(d, ignore_errors=True)

    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
