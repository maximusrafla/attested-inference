"""Tier 0.5, verifier side: the party that owns the clock.

Runs off the box. Stamps t0, issues the nonce, waits for the signed result, stamps t1, and
computes the occupancy bound over ITS OWN bracket. Nothing inside the enclave is trusted to say
how long anything took, because a confidential VM has no trusted clock: SGX's trusted time is
deprecated and SEV-SNP offers none.

Two runs, both needed for an honest number:
  --noop     a round trip that does no GPU work, which sizes the orchestration slack inside the
             bracket. That slack is charged against the workload, so it makes the reported
             occupancy an understatement.
  the real run, whose op count is known exactly and whose checksum depends on the nonce.

Usage:
  python tier05_verifier.py --host <ip> --key <sshkey> --seconds 20 --out gpuout/tier05
"""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path


def ssh(host, key, cmd):
    return subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-i", key,
                           f"azureuser@{host}", cmd], capture_output=True, text=True)


def scp_back(host, key, remote, local):
    return subprocess.run(["scp", "-i", key, f"azureuser@{host}:{remote}", local],
                          capture_output=True, text=True)


def one_round(host, key, out_dir, seconds, size, noop, ceiling=None, ceiling_only=False):
    nonce = secrets.token_hex(32)
    tag = "ceiling" if ceiling_only else ("noop" if noop else "work")
    remote = f"/home/azureuser/t05-{tag}.json"
    flag = " --noop" if noop else ""
    if ceiling_only:
        flag = " --ceiling-only"
    elif ceiling:
        flag = f" --ceiling {ceiling}"
    t0 = time.time()
    r = ssh(host, key,
            f"cd ~/t1/bin && ./tier05_occupancy.py --nonce {nonce} --seconds {seconds} "
            f"--size {size} --out {remote}{flag}")
    scp_back(host, key, remote, str(Path(out_dir) / f"tier05-{tag}.json"))
    t1 = time.time()
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
        raise SystemExit(f"{tag} round failed with exit {r.returncode}")
    result = json.loads((Path(out_dir) / f"tier05-{tag}.json").read_text())
    result["verifier_t0"] = t0
    result["verifier_t1"] = t1
    result["verifier_bracket_s"] = round(t1 - t0, 4)
    return nonce, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--size", type=int, default=8192)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)

    print("== device ceiling, measured once and OUTSIDE the bracket ==")
    _, ceil_run = one_round(args.host, args.key, args.out, 0, args.size, False, ceiling_only=True)
    ceiling = ceil_run["measured_ceiling_tflops"]
    print(f"ceiling on an unconstrained bf16 GEMM stream: {ceiling} TFLOP/s")

    print("\n== slack probe: a round trip that does no GPU work ==")
    _, noop = one_round(args.host, args.key, args.out, 0, args.size, True)
    slack = noop["verifier_bracket_s"]
    print(f"orchestration slack inside the bracket: {slack:.2f}s")

    print("\n== the challenge round ==")
    nonce, work = one_round(args.host, args.key, args.out, args.seconds, args.size, False,
                            ceiling=ceiling)
    bracket = work["verifier_bracket_s"]
    tflops_bracket = work["total_flops"] / bracket / 1e12
    ceiling = work["measured_ceiling_tflops"]

    # everything between t0 and t1 is charged to the workload, including process startup, CUDA
    # context creation and the network round trip. That is the conservative rule: it can only
    # understate how occupied the device was.
    summary = {
        "schema": "ccverify.tier05.verifier.v1",
        "nonce_issued": nonce,
        "device": work["device"],
        "verifier_bracket_s": bracket,
        "orchestration_slack_s": slack,
        "total_flops": work["total_flops"],
        "tflops_over_full_bracket": round(tflops_bracket, 2),
        "measured_ceiling_tflops": ceiling,
        "occupancy_over_full_bracket": round(tflops_bracket / ceiling, 4) if ceiling else None,
        "enclave_self_reported_tflops_UNTRUSTED": work["enclave_tflops_UNTRUSTED"],
        "enclave_self_reported_occupancy_UNTRUSTED": work["occupancy_vs_measured_ceiling_UNTRUSTED"],
        "final_state_sha256": work["final_state_sha256"],
    }
    Path(args.out, "tier05-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2, sort_keys=True))

    occ = summary["occupancy_over_full_bracket"]
    print("\nwhat this supports, stated at the strength it actually has:")
    print(f"  across a {bracket:.1f}s interval the verifier itself bracketed, the device sustained")
    print(f"  {summary['tflops_over_full_bracket']} TFLOP/s of bf16 work whose result depends on the")
    print(f"  verifier's nonce, which is {occ:.1%} of the ceiling this same device reached on an")
    print(f"  unconstrained GEMM stream. Arithmetic headroom left for undeclared work in that")
    print(f"  interval: at most about {1 - occ:.1%}, and that figure is charged the full")
    print(f"  {slack:.1f}s of orchestration slack, so it is an overstatement of the headroom.")
    print("\nwhat it does not support:")
    print("  a bound on memory-bound or low-occupancy undeclared work, which can hide in the gap")
    print("  anything outside the bracketed interval")
    print("  a hardware guarantee: the mechanism is SM contention, which is an argument, not a seal")


if __name__ == "__main__":
    main()
