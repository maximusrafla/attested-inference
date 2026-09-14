"""Recheck every headline E1 figure straight from the raw run files, not via the collector."""
import json, statistics
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "EVIDENCE" / "e1-adaptive-adversary"
L = lambda n: json.loads((D / n).read_text())
fails = []


def check(label, got, want, tol):
    ok = abs(got - want) <= tol
    print(("  OK  " if ok else "  FAIL") + f" {label}: got {got:.3f}, claimed {want}")
    if not ok:
        fails.append(label)


print("Part A, separate process, from rows[].drop_pct")
a1, a2 = L("A1-sep-nonadaptive.json"), L("A2-sep-adaptive.json")
for r, want in zip(a1["rows"], (11.4, 11.9, 13.5)):
    check(f"A1 duty {r['duty']}", r["drop_pct"], want, 0.06)
for r, want in zip(a2["rows"], (0.12, 0.23, 0.10)):
    check(f"A2 duty {r['duty']}", r["drop_pct"], want, 0.02)

print("\nAttribution controls")
for r, want in zip(L("A1b-sep-nonadaptive-noreadywait.json")["rows"], (11.2, 11.7, 13.6)):
    check(f"A1b duty {r['duty']}", r["drop_pct"], want, 0.06)
a1c = L("A1c-sep-nonadaptive-unbounded.json")
for r, want in zip(a1c["rows"], (34.9, 44.0, 49.7)):
    check(f"A1c duty {r['duty']}", r["drop_pct"], want, 0.06)
sh = [h["device_share_taken"] for h in a1c["hidden_work_counters"]]
print(f"  A1c device shares: 2% -> {min(sh[:4]):.3f}-{max(sh[:4]):.3f}, "
      f"5% -> {min(sh[4:8]):.3f}-{max(sh[4:8]):.3f}, 25% -> {min(sh[8:]):.3f}-{max(sh[8:]):.3f}")
check("A1c 2% share low", min(sh[:4]), 0.64, 0.01)
check("A1c 2% share high", max(sh[:4]), 0.66, 0.01)

print("\nAdaptive against an unbounded queue")
a2c = L("A2c-sep-adaptive-unbounded.json")
for r in a2c["rows"]:
    det = (1 - r["mean_ratio"]) > 3 * r["stdev_ratio"]
    print(f"  duty {r['duty']}: drop {r['drop_pct']:.2f}%  detected {det}")
check("A2c duty 0.25 drop", a2c["rows"][2]["drop_pct"], 5.4, 0.06)
drains = [1000 * x for h in a2c["hidden_work_counters"] for x in h["reaction_latency_s"]]
print(f"  drains ms: {min(drains[:4]):.0f}-{max(drains[:4]):.0f}, "
      f"{min(drains[4:8]):.0f}-{max(drains[4:8]):.0f}, {min(drains[8:]):.0f}-{max(drains[8:]):.0f}")

print("\nThe bound")
b = L("A34-bound.json")
check("clean mean", b["clean_mean_tflops"], 364.4, 0.1)
check("paired sigma pct", 100 * b["null_sigma_paired"], 1.00, 0.01)
check("unpaired sigma pct", 100 * b["null_sigma_unpaired"], 0.74, 0.01)
check("paired hideable pct", 100 * b["modes"]["nonadaptive"]["hideable_duty_paired"], 5.6, 0.05)
check("unpaired hideable pct", 100 * b["modes"]["nonadaptive"]["hideable_duty_unpaired"], 4.0, 0.05)
for r, want in zip(b["modes"]["nonadaptive"]["duty_response"], (2.74, 7.19, 14.05)):
    check(f"nonadaptive duty {r['duty']}", 100 * r["drop"], want, 0.02)
zeros = [n for r in b["modes"]["adaptive"]["duty_response"]
         for n in r["undeclared_iterations_in_window"]]
print(f"  adaptive undeclared-in-window values: {set(zeros)} over {len(zeros)} pairs")
if set(zeros) != {0}:
    fails.append("adaptive in-window iterations not all zero")
assert b["modes"]["adaptive"]["no_tested_duty_detectable_paired"]
assert b["modes"]["adaptive"]["no_tested_duty_detectable_unpaired"]
print("  OK   adaptive: no tested duty detectable, both protocols")

print("\nPart B, hidden work kept (rate ratio) and detection")
for name, wants in (("B2r-inline-adaptive.json", (1.0, 0.941, 0.884, 0.718, 0.464)),
                    ("B1r-sep-adaptive.json", (1.0, 0.720, 0.468))):
    f = L(name)
    ceil = next(p["summary"]["hidden_iterations_per_second"] for p in f["points"] if p["duty"] == 0)
    for p, want in zip(f["points"], wants):
        got = p["summary"]["hidden_iterations_per_second"] / ceil
        check(f"{name[:3]} duty {p['duty']} kept", got, want, 0.001)
        if p["duty"] > 0 and p["summary"]["detected_at_3sigma"]:
            fails.append(f"{name} duty {p['duty']} unexpectedly detected")

print("\nPositive control")
b3 = L("B3-sep-nonadaptive.json")
p = b3["points"][1]
check("B3 in-window drop", p["summary"]["in_window_drop_pct"], 47.61, 0.02)
check("B3 null sigma", p["summary"]["in_window_null_sigma_pct"], 0.13, 0.01)
assert p["summary"]["detected_at_3sigma"] is True
print("  OK   B3 detected at 3 sigma")

print("\nPart C, reaction deadline (and the arithmetic prediction)")
full = 47.8
for r, want in ((0, 0.00), (25, 1.43), (50, 2.51), (100, 4.94), (250, 11.99), (500, 23.54)):
    f = L(f"C-sep-react{r}.json")
    s = f["points"][0]["summary"]
    hc = f["points"][0]["contended"]["hidden_counters"]
    lat = [1000 * x for x in hc["reaction_latency_s"]]
    pred = full * (sum(lat) / len(lat)) / 1000
    check(f"C sep {r}ms drop", s["in_window_drop_pct"], want, 0.02)
    print(f"       measured reaction {sum(lat)/len(lat):.1f} ms (max {max(lat):.1f}), "
          f"predicted drop {pred:.2f}%, detected {s['detected_at_3sigma']}, "
          f"3 sigma {3*s['in_window_null_sigma_pct']:.2f}%")
for r, want in ((0, 0.07), (100, 4.82), (250, 9.35)):
    s = L(f"C-inline-react{r}.json")["points"][0]["summary"]
    check(f"C inline {r}ms drop", s["in_window_drop_pct"], want, 0.02)

print("\nThe confound, and that it did not touch the kept figures")
bx = L("B1x-sep-adaptive-CONFOUNDED-settle-asymmetry.json")
for p in bx["points"]:
    if p["duty"] > 0:
        print(f"  duty {p['duty']}: in-window {p['summary']['in_window_drop_pct']:.2f}% "
              f"(the artefact), kept {p['summary']['hidden_yield_vs_ceiling']:.4f}")
check("confounded kept at 0.25", bx["points"][3]["summary"]["hidden_yield_vs_ceiling"], 0.721, 0.001)
check("confounded kept at 0.50", bx["points"][4]["summary"]["hidden_yield_vs_ceiling"], 0.469, 0.001)

print("\n" + ("ALL HEADLINE FIGURES CHECK OUT" if not fails else f"FAILURES: {fails}"))
