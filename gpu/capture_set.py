"""Provision the two approved artifacts Tier 1 checks against: the baseline and the declaration.

Both are produced BEFORE the run under test and are what the regulator holds. This mirrors how
allowlist-based integrity schemes are actually provisioned: an approved reference run fixes the
set, and every later run is checked against it.

  baseline.json     the approved platform image plus the verifier tooling. Captured once, after
                    boot and after a warm-up that executes every tool the run will use, so that
                    IMA's measure-once cache does not later attribute a tool's first execution
                    to the workload window. Records how many log entries it covers, which is
                    where the checked window starts.

  declaration.json  the declared stack: the files an approved reference run of the stack caused
                    IMA to measure, plus the declared weight file's digest carried explicitly
                    (IMA under the tcb policy does not measure a data file read by a non-root
                    process, so the weight blob rides along the way M1 carried its code hash).

Usage:
  python3 capture_set.py baseline --ima-log ima.bin --out baseline.json
  python3 capture_set.py declaration --ima-log ima.bin --baseline baseline.json \
      --weights model.bin --label qwen-serving-stack --out declaration.json
"""

import argparse
import hashlib
import json
from pathlib import Path

from lib_ima import parse_binary_log


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digests(entries, start=0):
    """Approved digests. Violation entries are skipped: IMA logs an all-zero digest when it could
    not measure a file, the same value for every such file, so approving one approves them all."""
    out = []
    for e in entries[start:]:
        if e.is_violation:
            continue
        d, _path = e.file_digest_and_path()
        if d is not None:
            out.append(d)
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("baseline")
    b.add_argument("--ima-log", required=True)
    b.add_argument("--out", required=True)

    d = sub.add_parser("declaration")
    d.add_argument("--ima-log", required=True)
    d.add_argument("--baseline", required=True)
    d.add_argument("--weights")
    d.add_argument("--config", help="the approved runtime configuration")
    d.add_argument("--label", default="declared-stack")
    d.add_argument("--out", required=True)

    args = ap.parse_args()
    entries = parse_binary_log(Path(args.ima_log).read_bytes())

    if args.mode == "baseline":
        doc = {
            "schema": "ccverify.baseline.v1",
            "entry_count": len(entries),
            "digests": digests(entries),
        }
        Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True))
        print(f"baseline: {len(entries)} log entries, {len(doc['digests'])} distinct file digests")
        return

    baseline = json.loads(Path(args.baseline).read_text())
    start = int(baseline["entry_count"])
    new = [x for x in digests(entries, start) if x not in set(baseline["digests"])]
    doc = {
        "schema": "ccverify.declaration.v1",
        "label": args.label,
        "digests": new,
        "weights_path": args.weights,
        "weights_sha256": sha256_file(args.weights) if args.weights else None,
        # Configuration is declared explicitly, for the same reason the weights are: it is read
        # as data at run time, so no measurement covers it. Behaviour is code times weights
        # times config, and the statute's live hook is about applied safeguards, which live
        # here and not in the code hash. Leaving it out was a real gap, demonstrated before it
        # was closed: switching the safety filter off produced an identical accept.
        "config_path": args.config,
        "config_sha256": sha256_file(args.config) if args.config else None,
    }
    Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True))
    print(f"declaration '{args.label}': {len(new)} newly measured file digests since baseline "
          f"(log entries {start} to {len(entries)})")
    if args.weights:
        print(f"declared weights: {args.weights}  sha256 {doc['weights_sha256'][:16]}...")


if __name__ == "__main__":
    main()
