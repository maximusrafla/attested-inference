# The exported measurement log, replayed on the verifier's side (2026-09-14)

An external review of the write-up pointed out a hole in the July design: the measurement log stayed inside
the enclave, the regulator received a verdict plus the quoted PCR 10 value, and nothing the regulator held
let it interpret that value or identify the code that produced the verdict. Check 4 bound the disclosure to
the quote; it did not bind the verdict to the log. The remedy is the one deployed attestation systems use:
the attester sends the measurement list out with the quote and the verifier replays it.

`gpu/verify_completeness.py` now takes `--ima-log` and adds four checks (10 to 13 in its docstring): the
exported log replays to the register value the quote signed; that value equals the disclosure's; the
verifier's own recount of the attested window (every measured digest checked against the baseline and the
declaration it holds) agrees with the disclosure's counts; and the verifier's own finding on undeclared
execution agrees with the verdict. `gpu/completeness_check.py` is retained as the operator's pre-check and
nothing on the regulator's side depends on it any more.

No new hardware was rented. Every July ceremony already dumped the log after the quote (`gpu/tier1.sh`
`run`), so the retained bundles carry the exact evidence the exported-log verifier needs. The verifier was
rerun off-box on those bundles, with the expired MAA and NRAS tokens omitted:

| bundle | expect | log prefix covered | verifier's own count | disclosure's count | result |
|---|---|---|---|---|---|
| `gpu/gpuout/collect/tier1/accept` | accept | 2426 of 2446 | 2426 measured, 0 undeclared | 2426, 0 | 14/14 |
| `gpu/gpuout/collect/tier1/undeclared-exec` | reject | 2698 of 2718 | 2698, 1 (`undeclared_exec.sh`) | 2698, 1 | 14/14 |
| `gpu/gpuout/collect/tier1/undeclared-import` | accept (the limit) | 2503 of 2523 | 2503, 0 | 2503, 0 | 14/14 |
| `gpu/gpuout/collect/tier1/import-as-root` | reject | 3802 of 3822 | 3802, 994 | 3802, 994 | 14/14 |
| `gpu/gpuout/collect/tier1/tampered` | reject (`payload_identity`) | 2590 of 2610 | 2590, 0 | 2590, 0 | 14/14 |
| `gpu/devout2/tier1/accept` (cheap vTPM box) | accept | 574 of 582 | 574, 0 | 574, 0 | 14/14 |

The per-case verifier output is in `gpu-<case>.txt` and `cheapbox-accept.txt` here. The twenty-entry tail
outside each prefix is the provider's in-guest agent still writing after the quote, which is the liveness
behaviour recorded in the run log; the verifier ignores those entries rather than counting them.

What the regulator now holds that it did not before: the file names and digests of everything the machine
loaded in the attested window. It still never holds the weights, the outputs or the prompts. What it no
longer has to take on the operator's word: the counts and the verdict.
