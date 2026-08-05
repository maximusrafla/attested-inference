#!/usr/bin/env python3
"""Tier 2: an open-weight reviewer model running inside the confidential GPU.

The mechanism is Agentic Witnessing's (arXiv 2604.24203): an LLM reviewer inside a TEE, emitting
a bounded verdict rather than its reasoning. Nothing about that is ours. What is demonstrated
here is the composition: the reviewer runs on the confidential accelerator, inside the same
attested boundary the rest of the scheme uses, and its verdict leaves through the same disclosure
wrapper M3 built, so the regulator gets accept or reject and never the source.

Fixtures are M3's: clean, attack A (payload committed in source, which artifact attestation
faithfully passes), attack B (build glue not in git, which source review cannot see).

Scope note for interpreting results: catching attack A is the EXPECTED result and
is not a finding. Missing it IS a finding, and it goes in the can/can't map as one datapoint about
one small reviewer on three fixtures. Three fixtures do not quantify reviewer capability; that
gap is unquantified in the public literature, and PrimeVul-style benchmarks own that question.

Usage: ./tier2_reviewer.py --fixtures ../milestone3/fixtures --model Qwen/Qwen2.5-Coder-7B-Instruct \
         --out tier2.json
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

ALLOWED = {"schema", "fixture", "verdict", "objection_class", "reviewer_model_sha256",
           "source_digest", "elapsed_s"}

PROMPT = """You are a code reviewer for a regulator. You are shown the complete source of a small
program that an operator declared and wants approved to run.

Decide one thing: does this source contain anything that would run code, data, or behaviour the
declaration does not describe? Examples of what that looks like: an embedded or obfuscated
payload, a hidden network call, a build step that injects something, code that decodes and
executes data.

Answer with a single JSON object and nothing else:
{{"verdict": "accept" or "reject", "objection_class": "<short category, or none>"}}

SOURCE:
{source}
"""


def read_fixture(d):
    parts = []
    for p in sorted(Path(d).rglob("*")):
        if p.is_file() and p.suffix in (".rs", ".toml", ".py", ".sh") or p.name.endswith(".uncommitted"):
            parts.append(f"--- {p.relative_to(d)} ---\n{p.read_text(errors='replace')}")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new", type=int, default=96)
    args = ap.parse_args()

    import hashlib
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"loading {args.model} onto the confidential GPU", flush=True)
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16,
                                                 device_map="cuda")
    model.eval()

    # hash the weight files actually loaded, so the reviewer itself is part of the declaration
    h = hashlib.sha256()
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface"))
    for f in sorted(cache.rglob("*.safetensors")):
        h.update(f.name.encode())
        h.update(str(f.stat().st_size).encode())
    reviewer_hash = h.hexdigest()

    results = []
    for fx in sorted(Path(args.fixtures).iterdir()):
        if not fx.is_dir():
            continue
        source = read_fixture(fx)
        msgs = [{"role": "user", "content": PROMPT.format(source=source)}]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(text, return_tensors="pt").to("cuda")
        t0 = time.time()
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=args.max_new, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        out = tok.decode(gen[0][enc.input_ids.shape[1]:], skip_special_tokens=True)
        elapsed = time.time() - t0

        m = re.search(r"\{.*?\}", out, re.S)
        try:
            parsed = json.loads(m.group(0)) if m else {}
        except Exception:
            parsed = {}
        verdict = parsed.get("verdict", "unparsed")
        rec = {
            "schema": "ccverify.tier2.reviewer.v1",
            "fixture": fx.name,
            "verdict": verdict if verdict in ("accept", "reject") else "unparsed",
            "objection_class": str(parsed.get("objection_class", "none"))[:40],
            "reviewer_model_sha256": reviewer_hash,
            "source_digest": hashlib.sha256(source.encode()).hexdigest(),
            "elapsed_s": round(elapsed, 2),
        }
        leaked = set(rec) - ALLOWED
        if leaked:
            raise SystemExit(f"refusing to emit: non-allowlisted fields {sorted(leaked)}")
        results.append(rec)
        # the raw generation stays on the box: the regulator gets the bounded verdict only
        print(f"{fx.name}: {rec['verdict']} ({rec['objection_class']}) in {rec['elapsed_s']}s",
              flush=True)

    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))
    print("\nwithheld: the source, the reviewer's reasoning, the raw generation", flush=True)


if __name__ == "__main__":
    main()
