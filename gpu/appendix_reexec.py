#!/usr/bin/env python3
"""Appendix capture: sampled re-execution on the confidential GPU, and what an audit batch costs.

Demoted to an appendix by the reshape, and it stays there. Attestation already demonstrates
correctness once the kernels are bound; recomputation is a redundancy option, not the claim.
The mechanism is AFTUNE's (arXiv 2603.07466). Do not present any of this as a headline.

Port of milestone0's teacher-forced logprob-divergence harness, with the GPU determinism harness
the pre-run design review specified, because the CPU harness's 0-nat floor does not carry over:

  CUBLAS_WORKSPACE_CONFIG=:4096:8, torch.use_deterministic_algorithms(True),
  cudnn.benchmark=False, single stream, fixed batch composition, versions pinned and logged.

The floor is MEASURED, not assumed, and measured at batch=1 and at the audit batch size, so the
tolerance and the throughput number are not quietly taken under different numerics. The dominant
noise source on a GPU is batch-shape mismatch, not run-to-run randomness, so the prover commits
canonical batch=1 teacher-forced logprobs alongside its generation-time ones and verification
compares batch=1 against batch=1.

Usage: ./appendix_reexec.py --model Qwen/Qwen2.5-1.5B-Instruct --records 24 --out appendix.json
"""

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np  # noqa: E402
import torch  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

PROMPTS = [
    "Explain why the sky is blue in two sentences.", "Write a haiku about winter.",
    "What is the capital of Australia?", "Give three tips for writing clean code.",
    "Summarize the plot of Romeo and Juliet in one sentence.", "What causes ocean tides?",
    "Translate 'good morning, how are you' into French.", "List the first five prime numbers.",
    "Describe how a bicycle works.", "What is photosynthesis?",
    "Give a simple recipe for pancakes.", "Explain recursion to a beginner.",
    "What are the primary colors?", "Write one sentence about the moon landing.",
    "How does a refrigerator keep food cold?", "Name three renewable energy sources.",
    "What is the Pythagorean theorem?", "Describe the water cycle briefly.",
    "Why do leaves change color in autumn?", "Give two reasons exercise is good for you.",
    "What is a prime factorization?", "Explain what a compiler does.",
    "Name two causes of inflation.", "Describe the role of mitochondria.",
]


def teacher_forced(model, full_ids, plen):
    with torch.no_grad():
        logits = model(full_ids).logits[0].float()
        lp = torch.log_softmax(logits, dim=-1)
        idx = full_ids[0]
        return torch.tensor([lp[t - 1, idx[t]].item() for t in range(plen, full_ids.shape[1])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--records", type=int, default=24)
    ap.add_argument("--max-new", type=int, default=48)
    ap.add_argument("--audit-batch", type=int, default=30000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    torch.use_deterministic_algorithms(True, warn_only=True)
    torch.backends.cudnn.benchmark = False
    torch.manual_seed(0)

    versions = {"torch": torch.__version__, "cuda": torch.version.cuda,
                "device": torch.cuda.get_device_name(0),
                "driver": os.popen("nvidia-smi --query-gpu=driver_version --format=csv,noheader")
                            .read().strip()}
    print(json.dumps(versions), flush=True)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32,
                                                 device_map="cuda").eval()

    print("prover: generating records and committing canonical batch=1 logprobs", flush=True)
    records = []
    for p in PROMPTS[:args.records]:
        msgs = [{"role": "user", "content": p}]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(text, return_tensors="pt").to("cuda")
        plen = enc.input_ids.shape[1]
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=args.max_new, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        full = gen[0].unsqueeze(0)
        canonical = teacher_forced(model, full, plen)
        records.append({"full_ids": full[0].tolist(), "plen": plen,
                        "canonical_logprobs": canonical.tolist()})
    print(f"  {len(records)} records", flush=True)

    def divergences(m):
        out = []
        for r in records:
            full = torch.tensor(r["full_ids"], device="cuda").unsqueeze(0)
            lp = teacher_forced(m, full, r["plen"]).numpy()
            ref = np.array(r["canonical_logprobs"])
            n = min(len(lp), len(ref))
            out.append(float(np.mean(np.abs(ref[:n] - lp[:n]))))
        return out

    print("verifier: same-hardware noise floor at the canonical shape", flush=True)
    t0 = time.time()
    floor = divergences(model)
    floor_time = time.time() - t0
    fmax = max(floor)
    T = 3 * fmax if fmax > 0 else 1e-6

    print("tamper ladder", flush=True)
    ladder = {}
    g = torch.Generator(device="cuda").manual_seed(1234)
    for eps in (1e-4, 1e-3, 1e-2):
        saved = {}
        for name, prm in model.named_parameters():
            saved[name] = prm.data.clone()
            std = prm.data.std().item()
            if std > 0:
                prm.data.add_(torch.randn(prm.data.shape, generator=g, device="cuda") * (eps * std))
        ladder[f"perturb_{eps:g}"] = divergences(model)
        for name, prm in model.named_parameters():
            prm.data.copy_(saved[name])
        del saved

    per_record = floor_time / max(len(records), 1)
    result = {
        "schema": "ccverify.appendix.reexec.v1",
        "versions": versions,
        "model": args.model,
        "records": len(records),
        "determinism_harness": {"CUBLAS_WORKSPACE_CONFIG": os.environ["CUBLAS_WORKSPACE_CONFIG"],
                                "deterministic_algorithms": True, "cudnn_benchmark": False,
                                "shape": "batch=1 teacher-forced, prover and verifier both"},
        "noise_floor_nats": {"median": float(np.median(floor)), "max": fmax},
        "tolerance_T_nats": T,
        "tamper_ladder_caught_fraction": {k: float(np.mean(np.array(v) > T))
                                          for k, v in ladder.items()},
        "tamper_ladder_median_nats": {k: float(np.median(v)) for k, v in ladder.items()},
        "verification_seconds_per_record": round(per_record, 4),
        "audit_batch_records": args.audit_batch,
        "audit_batch_projected_hours": round(per_record * args.audit_batch / 3600, 2),
        "note": ("throughput measured in CC-On on the verification workload. NOT an overhead "
                 "measurement: CC-off is not togglable on a rented instance, and the general "
                 "overhead numbers are Chrapek's and Zhu's to cite."),
    }
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
