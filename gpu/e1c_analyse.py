#!/usr/bin/env python3
"""E1c (2026-09-04): the same-context bound below 4 percent of a device, recomputed from raw values.

Separate from tier05_bound.py on purpose. Everything here is rebuilt from the retained clean runs,
the per-pair undeclared iteration counts, the per-pair shares (from which iterations per second are
recovered as share x in-run solo rate), and the two sustained calibrations taken beside the run.
Nothing is taken from the harness's own summary fields except to cross-check them.
"""
import argparse, json, math, statistics
from pathlib import Path


def pct(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}%"


def null_stats(clean):
    mean = statistics.mean(clean)
    unpaired = statistics.stdev(clean) / mean
    ratios = [clean[i + 1] / clean[i] for i in range(0, len(clean) - 1, 2)]
    paired = statistics.stdev(ratios) if len(ratios) > 1 else None
    return mean, unpaired, paired, ratios


def interp_measured(pts, target):
    """Smallest x whose y reaches target, interpolated between MEASURED points only. Returns
    (x, 'measured') or (x, 'extrapolated below smallest load') or (None, 'above largest load')."""
    pts = sorted(pts)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 <= target <= y1:
            return (x0 if y1 == y0 else x0 + (x1 - x0) * (target - y0) / (y1 - y0)), "measured"
    if target < pts[0][1]:
        return pts[0][0] * target / pts[0][1], "EXTRAPOLATED below smallest load"
    return None, "above largest load"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dir", required=True); a = ap.parse_args()
    d = Path(a.dir)
    b = json.loads((d / "BOUND-lowload.json").read_text())
    cals = {}
    for name in ("CAL-before", "CAL-after"):
        p = d / f"{name}.json"
        if p.exists():
            cals[name] = json.loads(p.read_text())
    print("=" * 92)
    print("E1c. Same-context bound below 4 percent of a device. Recomputed from raw values.")
    print("=" * 92)

    solo_inrun = b["solo_calibration"]["iterations_per_second"]
    print(f"\nDENOMINATORS (matmul/s, 8192 bf16)")
    print(f"  in-run short calibration (3 s, sync every 4)      : {solo_inrun:8.1f}")
    dens = {"in-run": solo_inrun}
    for name, c in cals.items():
        print(f"  {name:<12} short cal {c['calibration']['solo_iterations_per_second']:8.1f}  "
              f"sustained 20 s {c['iterations_per_second']:8.1f}  "
              f"(sustained/short {c['iterations_per_second']/c['calibration']['solo_iterations_per_second']:.3f})")
        dens[name] = c["iterations_per_second"]
    if "CAL-before" in cals and "CAL-after" in cals:
        drift = cals["CAL-after"]["iterations_per_second"] / cals["CAL-before"]["iterations_per_second"] - 1
        print(f"  session drift, sustained after/before - 1          : {100*drift:+.2f}%")

    clean = b["clean_runs"]
    print(f"\nNULL from {len(clean)} retained clean runs: " + ", ".join(f"{x:.1f}" for x in clean))
    for label, cl in (("all runs", clean), ("dropping the first run", clean[1:]),
                      ("dropping the first two runs", clean[2:])):
        m, su, sp, ratios = null_stats(cl)
        print(f"  {label:<28} mean {m:6.1f}  unpaired sigma {100*su:5.2f}%  paired sigma "
              f"{pct(100*sp if sp else None)}  (n pairs {len(ratios)})  paired threshold 3s {pct(300*sp if sp else None)}")
    # cross-check the harness's own figures
    print(f"  harness reports: paired {100*b['null_sigma_paired']:.2f}%  unpaired {100*b['null_sigma_unpaired']:.2f}%  "
          f"excl warmup paired {100*b['null_sigma_paired_excl_warmup']:.2f}%")

    mode = b["modes"]["nonadaptive"]
    rows = mode["duty_response"]
    print(f"\nDUTY RESPONSE, six pairs per point")
    hdr = f"  {'knob':>6}{'drop':>8}{'sd':>7}{'its/pair':>10}" + "".join(f"{('share vs '+k):>18}" for k in dens) + f"{'drop/share(in-run)':>20}"
    print(hdr)
    pts = {k: [] for k in dens}
    for r in rows:
        ips = [s * solo_inrun for s in r["device_share_per_pair"]]   # recover iterations/s per pair
        mean_ips = statistics.mean(ips)
        line = f"  {r['duty_knob']:>6}{100*r['drop']:>7.2f}%{100*r['stdev_ratio']:>6.2f}%{r['undeclared_iterations_in_window'][0]:>10}"
        for k, den in dens.items():
            sh = mean_ips / den
            pts[k].append((sh, r["drop"]))
            line += f"{100*sh:>17.2f}%"
        line += f"{r['drop']/(mean_ips/solo_inrun):>20.2f}"
        print(line)
    print("  drop/share above 1 means the declared chain loses more than the undeclared kernels' own device time.")

    print(f"\nWIDTH: smallest device share whose drop reaches the threshold, between measured points")
    for nlabel, cl in (("all runs", clean), ("dropping the first run", clean[1:])):
        _, su, sp, _ = null_stats(cl)
        for tlabel, thr in (("paired 3s", 3 * sp), ("stored-ceiling 3s", 3 * su)):
            line = f"  null {nlabel:<22} {tlabel:<18} threshold {100*thr:5.2f}% :"
            for k in dens:
                x, how = interp_measured(pts[k], thr)
                line += f"  vs {k} {pct(100*x if x is not None else None)} [{how}]"
            print(line)
    print(f"  harness's own: hideable paired {pct(100*mode['hideable_device_share_paired'])}  "
          f"unpaired {pct(100*mode['hideable_device_share_unpaired'])} (both against the in-run denominator)")

    print(f"\nBOTTOM-END LINEARITY: drop per unit share (in-run denominator) by knob")
    for (sh, dr), r in zip(pts["in-run"], rows):
        print(f"  knob {r['duty_knob']:<5} share {100*sh:5.2f}%  drop {100*dr:5.2f}%  ratio {dr/sh:.3f}")
    print("  v2 for comparison (BOUND-nonadaptive, in-run denominator): knob 0.05 share 4.33 drop 4.50 ratio 1.04; "
          "knob 0.10 share 8.27 drop 8.52 ratio 1.03")


if __name__ == "__main__":
    main()
