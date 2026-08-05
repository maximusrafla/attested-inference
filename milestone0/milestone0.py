"""
Milestone 0: does re-execution separate a real model from a fake one?

Same-hardware leg (CPU). Builds the measurement harness and answers the
adversarially-close question: how close can a fake get before its logprobs
slip under the re-run noise floor.

Design:
  - "prover" logs, per prompt, the greedily-generated token sequence and the
    logprob it assigned to each realized token (teacher-forced).
  - each "verifier variant" re-computes, teacher-forced, the logprob IT assigns
    to that same realized sequence. Divergence = mean |logprob_prover - logprob_variant|
    over the generated positions (in nats).
  - variants:
      rerun      : same real model            -> the noise floor
      fp16       : real model in half precision -> precision-change fake
      perturb_*  : real weights + Gaussian noise at increasing strength -> subtle tampering ladder
      different  : a genuinely different (bigger) model -> gross substitution
  - output: per-variant divergence stats (JSON), a separation plot (PNG), and a
    printed tolerance T with false-accept / false-reject.

No em dashes in output text.
"""

import os, json, copy, time
import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

torch.set_num_threads(os.cpu_count() or 8)
torch.manual_seed(0)
np.random.seed(0)

OUT = os.path.dirname(os.path.abspath(__file__))
REAL = "Qwen/Qwen2.5-0.5B-Instruct"
DIFFERENT = "HuggingFaceTB/SmolLM2-360M-Instruct"  # different family, small, memory-light
PERTURB_EPS = [1e-4, 3e-4, 1e-3, 3e-3, 1e-2]
MAX_NEW = 64

PROMPTS = [
    "Explain why the sky is blue in two sentences.",
    "Write a haiku about winter.",
    "What is the capital of Australia?",
    "Give three tips for writing clean code.",
    "Summarize the plot of Romeo and Juliet in one sentence.",
    "What causes ocean tides?",
    "Translate 'good morning, how are you' into French.",
    "List the first five prime numbers.",
    "Describe how a bicycle works.",
    "What is photosynthesis?",
    "Give a simple recipe for pancakes.",
    "Explain recursion to a beginner.",
    "What are the primary colors?",
    "Write one sentence about the moon landing.",
    "How does a refrigerator keep food cold?",
    "Name three renewable energy sources.",
    "What is the Pythagorean theorem?",
    "Describe the water cycle briefly.",
    "Why do leaves change color in autumn?",
    "Give two reasons exercise is good for you.",
]


def load(model_name, dtype):
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=dtype)
    model.eval()
    return tok, model


def build_records(tok, model):
    """Prover side: greedily generate, then teacher-forced logprob of each realized token."""
    records = []
    for i, p in enumerate(PROMPTS):
        msgs = [{"role": "user", "content": p}]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(text, return_tensors="pt")
        plen = enc.input_ids.shape[1]
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        full = gen[0]  # prompt + generated
        gen_ids = full[plen:]
        lp = teacher_forced_logprobs(model, full.unsqueeze(0), plen)
        records.append({"prompt": p, "full_ids": full.tolist(), "plen": plen,
                        "gen_ids": gen_ids.tolist(), "prover_logprobs": lp.tolist()})
        print(f"  record {i+1}/{len(PROMPTS)}  gen_len={len(gen_ids)}")
    return records


def teacher_forced_logprobs(model, full_ids, plen):
    """logprob assigned to each realized generated token (positions plen..end)."""
    with torch.no_grad():
        out = model(full_ids)
        logits = out.logits[0].float()          # [seq, vocab]
        logprobs = torch.log_softmax(logits, dim=-1)
        # token at position t is predicted by logits at t-1
        idx = full_ids[0]
        gen_positions = range(plen, full_ids.shape[1])
        vals = []
        for t in gen_positions:
            vals.append(logprobs[t - 1, idx[t]].item())
    return torch.tensor(vals)


def variant_divergences(records, tok, model, label):
    """mean |prover_lp - variant_lp| per record, teacher-forced on the stored sequence."""
    divs = []
    for r in records:
        full = torch.tensor(r["full_ids"]).unsqueeze(0)
        lp = teacher_forced_logprobs(model, full, r["plen"]).numpy()
        prover = np.array(r["prover_logprobs"])
        n = min(len(lp), len(prover))
        divs.append(float(np.mean(np.abs(prover[:n] - lp[:n]))))
    print(f"  variant {label}: median div = {np.median(divs):.5f} nats")
    return divs


def perturb_(model, eps):
    """add Gaussian noise scaled per-tensor to each weight; returns saved originals."""
    saved = {}
    g = torch.Generator().manual_seed(1234)
    for name, prm in model.named_parameters():
        saved[name] = prm.data.clone()
        std = prm.data.std().item()
        if std > 0:
            noise = torch.randn(prm.data.shape, generator=g) * (eps * std)
            prm.data.add_(noise)
    return saved


def restore_(model, saved):
    for name, prm in model.named_parameters():
        prm.data.copy_(saved[name])


def save_results(results):
    json.dump(results, open(os.path.join(OUT, "divergences.json"), "w"), indent=2)


def main():
    t0 = time.time()
    rec_path = os.path.join(OUT, "records.json")
    print("loading real model:", REAL)
    tok, real = load(REAL, torch.float32)

    if os.path.exists(rec_path):
        print("reusing saved prover records")
        records = json.load(open(rec_path))
    else:
        print("building prover records (greedy generate + teacher-forced logprobs)...")
        records = build_records(tok, real)
        json.dump(records, open(rec_path, "w"))

    results = {}

    print("variant: rerun (noise floor)")
    results["rerun"] = variant_divergences(records, tok, real, "rerun")
    save_results(results)

    print("variant: fp16")
    _, real16 = load(REAL, torch.float16)
    results["fp16"] = variant_divergences(records, tok, real16, "fp16")
    del real16
    save_results(results)

    for eps in PERTURB_EPS:
        print(f"variant: perturb eps={eps}")
        saved = perturb_(real, eps)
        results[f"perturb_{eps:g}"] = variant_divergences(records, tok, real, f"perturb_{eps:g}")
        restore_(real, saved)
        del saved
        save_results(results)

    try:
        print("variant: different model:", DIFFERENT)
        del real
        tok2, diff = load(DIFFERENT, torch.float32)
        results["different"] = variant_divergences_retok(records, tok, tok2, diff)
        del diff
        save_results(results)
    except Exception as e:
        print("different-model variant failed (non-fatal):", repr(e))

    summarize(results)
    plot(results)
    print(f"done in {time.time()-t0:.0f}s")


def variant_divergences_retok(records, tok_real, tok_var, model):
    """for a different-family model: rebuild the sequence from decoded text so token ids are valid."""
    divs = []
    for r in records:
        gen_text = tok_real.decode(r["gen_ids"], skip_special_tokens=True)
        msgs = [{"role": "user", "content": r["prompt"]}]
        ptext = tok_var.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc_p = tok_var(ptext, return_tensors="pt")
        plen = enc_p.input_ids.shape[1]
        full = tok_var(ptext + gen_text, return_tensors="pt").input_ids
        lp = teacher_forced_logprobs(model, full, plen).numpy()
        prover = np.array(r["prover_logprobs"])
        n = min(len(lp), len(prover))
        if n == 0:
            continue
        divs.append(float(np.mean(np.abs(prover[:n] - lp[:n]))))
    print(f"  variant different: median div = {np.median(divs):.5f} nats")
    return divs


def summarize(results):
    real = np.array(results["rerun"])
    T = real.max() * 3 if real.max() > 0 else 1e-3
    print("\n=== SEPARATION SUMMARY ===")
    print(f"noise floor (rerun): median={np.median(real):.5f} max={real.max():.5f} nats")
    print(f"proposed tolerance T = 3x max noise = {T:.5f} nats")
    print(f"{'variant':<16}{'median':>10}{'min':>10}{'% caught (>T)':>16}")
    for k, v in results.items():
        v = np.array(v)
        caught = 100.0 * np.mean(v > T)
        print(f"{k:<16}{np.median(v):>10.5f}{v.min():>10.5f}{caught:>15.0f}%")


def plot(results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = list(results.keys())
    data = [np.array(results[k]) for k in labels]
    fig, ax = plt.subplots(figsize=(11, 6))
    for i, (k, d) in enumerate(zip(labels, data)):
        x = np.random.normal(i, 0.06, size=len(d))
        ax.scatter(x, np.clip(d, 1e-6, None), s=22, alpha=0.6)
    real = np.array(results["rerun"])
    T = real.max() * 3 if real.max() > 0 else 1e-3
    ax.axhline(T, color="red", ls="--", lw=1, label=f"tolerance T={T:.4f}")
    ax.set_yscale("log")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("mean |logprob divergence| per record (nats, log)")
    ax.set_title("Milestone 0: re-execution separation, real vs fake (same hardware, CPU)")
    ax.legend()
    fig.tight_layout()
    p = os.path.join(OUT, "separation.png")
    fig.savefig(p, dpi=130)
    print("plot saved:", p)


if __name__ == "__main__":
    main()
