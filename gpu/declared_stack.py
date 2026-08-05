#!/usr/bin/env python3
"""The declared stack: the program the operator declared and the regulator approved.

Executed directly, never as `python3 declared_stack.py`, and that detail is load-bearing: IMA's
BPRM_CHECK rule measures the file being executed, so running it as a script gives the regulator
a measurement of THIS file. Handing the same code to an already-measured interpreter as data
would measure nothing, which is the limit the --import-extra probe exists to show.


Deliberately small. Tier 1 is about what the measurement chain can say about which code ran,
not about the code being interesting. On the GPU box this is the same script with torch work
added (see --gpu), so the same declaration mechanism covers a real accelerator workload.

--import-extra exists to probe a policy limit rather than to hide anything: it loads a Python
module as DATA through the already-declared interpreter. Under the IMA tcb policy a non-root
read is not measured, so that code never reaches PCR 10. Running the same thing under --as-root
makes the tcb euid=0 read rule fire. The difference between those two runs is the finding.
"""

import argparse
import hashlib
import json
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--import-extra", help="load an extra module as data through this interpreter")
    ap.add_argument("--gpu", action="store_true", help="also do real accelerator work")
    ap.add_argument("--config", help="runtime configuration, read as data")
    ap.add_argument("--undeclared-gpu-frac", type=float, default=0.0,
                    help="fraction of a second per iteration of UNDECLARED accelerator work, "
                         "submitted by this already-declared process")
    args = ap.parse_args()

    t0 = time.time()
    blob = open(args.weights, "rb").read()
    digest = hashlib.sha256(blob).hexdigest()
    result = {"weights_sha256": digest, "weights_bytes": len(blob), "pid": os.getpid(),
              "euid": os.geteuid()}

    # Runtime configuration, read as DATA. This is the 4.2 case: the statute's live hook is
    # promise versus practice on applied safeguards, and safeguards live in configuration, not
    # in the code hash. A non-root read is not measured, so switching a safety flag here leaves
    # every attested value identical.
    if args.config:
        cfg = json.load(open(args.config))
        result["config_effect"] = {
            "safety_filter": cfg.get("safety_filter"),
            "system_prompt_sha256": hashlib.sha256(
                cfg.get("system_prompt", "").encode()).hexdigest()[:16],
            "sampling_temperature": cfg.get("temperature"),
        }
        result["config_sha256"] = hashlib.sha256(
            open(args.config, "rb").read()).hexdigest()

    if args.import_extra:
        import importlib.util
        spec = importlib.util.spec_from_file_location("undeclared_module", args.import_extra)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result["extra"] = mod.run()

    if args.gpu:
        import torch
        dev = torch.device("cuda")
        n = 4096
        g = torch.Generator(device="cuda").manual_seed(int(digest[:8], 16))
        a = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        b = torch.randn(n, n, generator=g, device=dev, dtype=torch.bfloat16)
        c = (a @ b).float()
        torch.cuda.synchronize()
        result["gpu"] = {"device": torch.cuda.get_device_name(0),
                         "checksum": float(c.abs().sum().item())}

        # UNDECLARED accelerator work, submitted by this already-declared process. Nothing new
        # executes as a file, so the completeness measurement never sees it. This is the
        # accelerator-level form of the same limit, and it is the one that matters for AI
        # compute rather than for software generally.
        if args.undeclared_gpu_frac > 0:
            import time as _t
            u = torch.randn(4096, 4096, device=dev, dtype=torch.bfloat16)
            v = torch.randn(4096, 4096, device=dev, dtype=torch.bfloat16)
            t_end = _t.time() + args.undeclared_gpu_frac
            n = 0
            while _t.time() < t_end:
                u = torch.nn.functional.normalize(u @ v, dim=1)
                n += 1
            torch.cuda.synchronize()
            result["undeclared_gpu_iterations"] = n

    result["elapsed_s"] = round(time.time() - t0, 3)
    # the output stays on the box: this is the withheld side of the disclosure discipline
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(f"declared stack ran: weights {digest[:16]}... euid={result['euid']} "
          f"elapsed {result['elapsed_s']}s", file=sys.stderr)


if __name__ == "__main__":
    main()
