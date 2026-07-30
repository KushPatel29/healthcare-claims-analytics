"""
Economic evaluation of a proposed ALC-reduction program.

The activity engine establishes that alternate-level-of-care days are the
authority's largest recoverable capacity problem and that they concentrate at
one site. This module answers the question a Vice President actually asks next:
*should we fund the fix, and how confident are you?*

It is a standard health-technology-assessment structure:

  1. **Base case** — incremental cost, incremental QALYs, **ICER**, and net
     monetary benefit at a willingness-to-pay threshold.
  2. **One-way sensitivity analysis** — every parameter moved to its plausible
     extremes, ranked by how much the answer swings (a tornado).
  3. **Probabilistic sensitivity analysis** — every parameter drawn from a
     distribution simultaneously, 10,000 times, producing a
     **cost-effectiveness acceptability curve**: the probability the program is
     worth funding at each threshold.

Two costing perspectives, and the difference between them is the point
-----------------------------------------------------------------------
Almost every hospital business case ever written values an avoided bed day at
the fully absorbed per-diem and books the whole thing as a saving. That is
usually wrong. Unless the bed is actually closed and the staffing removed, the
fixed cost does not go anywhere — the ward, the nurse, the heat, and the
overhead are all still there. What you have created is *capacity*, not cash.

So the model reports both, explicitly:

  * **Perspective A — opportunity cost.** Value the freed bed day at the full
    per-diem, on the argument that it is immediately backfilled by a patient
    currently boarding in Emergency or waiting for a surgical slot. Defensible
    when the site is at capacity, which it demonstrably is. This is the version
    that makes the program look dominant.
  * **Perspective B — cash-releasing.** Value only the genuinely variable share
    of the per-diem, because that is the only money the CFO can actually
    redeploy this fiscal year. This is the version the CFO will believe.

The recommendation is written against Perspective B. If a program only works
under Perspective A, it is a capacity argument wearing a savings costume, and
it should be argued on capacity.

Usage:
    python engine/health_economics.py
"""

import csv
import math
import random
from pathlib import Path

random.seed(2024)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# The program is scoped authority-wide, not to the single worst site. ALC is a
# community-capacity problem, and a site-only program mostly relocates the
# bottleneck: patients who cannot be discharged from Harbourview are not
# Harbourview's patients, they are the region's.
LEAD_SITE = "Harbourview Hospital"     # highest ALC rate — phase 1 of the rollout
MONTHS_OF_DATA = 24
PSA_ITERATIONS = 10000
WTP_THRESHOLD = 50000.0                # $/QALY, the conventional Canadian anchor
CEAC_GRID = [0, 10000, 20000, 30000, 40000, 50000, 75000, 100000, 150000]

# name: (base, low, high, distribution, note)
# Low/high are the plausible bounds a reviewer would defend, not confidence
# intervals — that is what a one-way sensitivity analysis is for.
PARAMS = {
    "alc_reduction": (
        0.22, 0.10, 0.35, "beta",
        "Relative reduction in ALC days. Published transitional-care and "
        "early-discharge-planning programs report 10-35%; 22% is mid-range."),
    "program_cost_annual": (
        1_450_000.0, 1_100_000.0, 1_900_000.0, "gamma",
        "8.0 FTE (5 care coordinators, 2 OT/PT, 1 manager) plus purchased "
        "interim community capacity and program overhead."),
    "alc_per_diem": (
        1150.0, 950.0, 1350.0, "gamma",
        "Fully absorbed cost of a staffed acute bed-day occupied by an ALC "
        "patient."),
    "variable_share": (
        0.32, 0.20, 0.45, "beta",
        "Share of the per-diem that is genuinely cash-releasing within the "
        "fiscal year (supplies, drugs, food, premium/agency nursing)."),
    "community_per_diem": (
        195.0, 140.0, 260.0, "gamma",
        "Cost of the home-support or interim residential day the patient "
        "receives instead. The saving is a difference, never the full per-diem."),
    "qaly_per_alc_day_avoided": (
        0.00075, 0.00020, 0.00150, "gamma",
        "Health gain per avoided ALC day: less deconditioning, delirium, and "
        "hospital-acquired infection. Small per day, meaningful over thousands."),
}


def beta_from(mean, sd):
    """Method-of-moments Beta. Guards against an sd too large to be a Beta."""
    mean = min(max(mean, 1e-6), 1 - 1e-6)
    max_sd = math.sqrt(mean * (1 - mean)) * 0.99
    sd = min(sd, max_sd)
    common = mean * (1 - mean) / (sd ** 2) - 1
    return max(common * mean, 1e-3), max(common * (1 - mean), 1e-3)


def gamma_from(mean, sd):
    """Method-of-moments Gamma (shape, scale). Costs are right-skewed and
    non-negative, which is exactly what a Gamma is for."""
    shape = (mean / sd) ** 2
    scale = (sd ** 2) / mean
    return max(shape, 1e-3), scale


def sd_from_bounds(low, high):
    """Treat the plausible low/high as roughly a 95% span."""
    return max((high - low) / 3.92, 1e-9)


def load_alc_days():
    """Annualised ALC days, authority-wide and at the phase-1 lead site.

    Read from the activity engine's own output rather than recomputed here, so
    the business case and the operational report can never quietly disagree
    about the number they are both built on.
    """
    path = OUT / "activity_by_facility.csv"
    if not path.exists():
        raise SystemExit("run engine/build_activity_metrics.py first")
    with open(path, encoding="utf-8") as f:
        rows = {r["facility_name"]: r for r in csv.DictReader(f)}
    years = MONTHS_OF_DATA / 12.0
    total = sum(int(r["alc_days"]) for r in rows.values()) / years
    lead = int(rows[LEAD_SITE]["alc_days"]) / years if LEAD_SITE in rows else 0.0
    return total, lead


def evaluate(alc_days_annual, p):
    """One deterministic run of the model at parameter set `p`.

    Returns both costing perspectives. Cost is incremental *to the health
    system*: what the program costs, minus what avoiding the bed days is worth,
    plus what the community placement costs instead.
    """
    days_avoided = alc_days_annual * p["alc_reduction"]
    qalys = days_avoided * p["qaly_per_alc_day_avoided"]
    community_cost = days_avoided * p["community_per_diem"]

    out = {}
    for label, per_diem in (
        ("A_opportunity_cost", p["alc_per_diem"]),
        ("B_cash_releasing", p["alc_per_diem"] * p["variable_share"]),
    ):
        avoided = days_avoided * per_diem
        inc_cost = p["program_cost_annual"] + community_cost - avoided
        out[label] = {
            "alc_days_avoided": days_avoided,
            "incremental_cost": inc_cost,
            "incremental_qalys": qalys,
            # A negative ICER is not "very cost-effective" — it is ambiguous,
            # because it can mean dominant (cheaper and better) or dominated
            # (dearer and worse). Report the quadrant, never the bare ratio.
            "icer": (inc_cost / qalys) if qalys > 0 else None,
            "dominant": inc_cost < 0 and qalys > 0,
            "nmb": qalys * WTP_THRESHOLD - inc_cost,
        }
    return out


def base_params():
    return {k: v[0] for k, v in PARAMS.items()}


def draw_params():
    p = {}
    for name, (base, low, high, dist, _) in PARAMS.items():
        sd = sd_from_bounds(low, high)
        if dist == "beta":
            a, b = beta_from(base, sd)
            p[name] = random.betavariate(a, b)
        else:
            shape, scale = gamma_from(base, sd)
            p[name] = random.gammavariate(shape, scale)
    return p


def main():
    alc_days_annual, lead_site_days = load_alc_days()
    base = evaluate(alc_days_annual, base_params())

    # ---- base case -------------------------------------------------------
    with open(OUT / "hta_base_case.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["perspective", "alc_days_avoided_annual", "incremental_cost",
                    "incremental_qalys", "icer", "dominant", "net_monetary_benefit"])
        for label, r in base.items():
            # QALYs are carried at full precision on purpose: rounding a
            # denominator to three decimals moves the ICER by hundreds of
            # dollars and stops the published ICER reconciling to its own
            # inputs, which is the first thing a reviewer checks.
            w.writerow([label, round(r["alc_days_avoided"], 1),
                        round(r["incremental_cost"], 2), round(r["incremental_qalys"], 6),
                        round(r["icer"], 2) if r["icer"] is not None else "",
                        r["dominant"], round(r["nmb"], 2)])

    # ---- one-way sensitivity (tornado), on the conservative perspective ---
    tornado = []
    for name, (_, low, high, _, note) in PARAMS.items():
        lo_p, hi_p = base_params(), base_params()
        lo_p[name], hi_p[name] = low, high
        lo = evaluate(alc_days_annual, lo_p)["B_cash_releasing"]["nmb"]
        hi = evaluate(alc_days_annual, hi_p)["B_cash_releasing"]["nmb"]
        tornado.append({
            "parameter": name, "low_value": low, "high_value": high,
            "nmb_at_low": round(lo, 2), "nmb_at_high": round(hi, 2),
            "swing": round(abs(hi - lo), 2),
            "flips_decision": (lo > 0) != (hi > 0),
            "note": note,
        })
    tornado.sort(key=lambda r: -r["swing"])
    with open(OUT / "hta_tornado.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=tornado[0].keys())
        w.writeheader()
        w.writerows(tornado)

    # ---- probabilistic sensitivity analysis ------------------------------
    # Every parameter varies at once, which is the only honest way to state
    # confidence: a one-way analysis holds five things still while moving one,
    # and reality never does that.
    sims = {"A_opportunity_cost": [], "B_cash_releasing": []}
    for _ in range(PSA_ITERATIONS):
        r = evaluate(alc_days_annual, draw_params())
        for k in sims:
            sims[k].append((r[k]["incremental_cost"], r[k]["incremental_qalys"]))

    ceac = []
    for wtp in CEAC_GRID:
        row = {"willingness_to_pay": wtp}
        for k, runs in sims.items():
            row[f"p_cost_effective_{k}"] = round(
                sum(1 for c, q in runs if q * wtp - c > 0) / len(runs), 4)
        ceac.append(row)
    with open(OUT / "hta_psa_ceac.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ceac[0].keys())
        w.writeheader()
        w.writerows(ceac)

    def pct(runs, fn):
        return sum(1 for c, q in runs if fn(c, q)) / len(runs)

    p_saving_b = pct(sims["B_cash_releasing"], lambda c, q: c < 0)
    p_ce_b = pct(sims["B_cash_releasing"], lambda c, q: q * WTP_THRESHOLD - c > 0)
    p_ce_a = pct(sims["A_opportunity_cost"], lambda c, q: q * WTP_THRESHOLD - c > 0)
    mean_cost_b = sum(c for c, _ in sims["B_cash_releasing"]) / PSA_ITERATIONS

    b, a = base["B_cash_releasing"], base["A_opportunity_cost"]
    lines = [
        "ECONOMIC EVALUATION — TRANSITIONAL CARE / ALC REDUCTION",
        "=" * 62,
        f"Scope:                           authority-wide "
        f"(phase 1: {LEAD_SITE})",
        f"ALC days (annualised, authority):{alc_days_annual:>12,.0f}"
        f"  = {alc_days_annual / 365:,.0f} beds",
        f"  of which {LEAD_SITE[:20]}: {lead_site_days:>12,.0f}",
        f"ALC days avoided at base case:   {b['alc_days_avoided']:>12,.0f}"
        f"  ({PARAMS['alc_reduction'][0]:.0%} reduction"
        f" = {b['alc_days_avoided'] / 365:,.1f} beds)",
        f"QALYs gained:                    {b['incremental_qalys']:>12,.1f}",
        "-" * 62,
        "PERSPECTIVE A — opportunity cost (bed day valued in full)",
        f"  Incremental cost:              {a['incremental_cost']:>12,.0f}",
        f"  Result:                        "
        f"{'DOMINANT (cost-saving and health-improving)' if a['dominant'] else 'ICER ' + format(a['icer'], ',.0f') + ' /QALY'}",
        f"  Net monetary benefit @ ${WTP_THRESHOLD:,.0f}/QALY: {a['nmb']:>12,.0f}",
        "-" * 62,
        "PERSPECTIVE B — cash-releasing (variable cost only)  <- the decision case",
        f"  Incremental cost:              {b['incremental_cost']:>12,.0f}",
        f"  Result:                        "
        f"{'DOMINANT (cost-saving and health-improving)' if b['dominant'] else 'ICER ' + format(b['icer'], ',.0f') + ' /QALY'}",
        f"  Net monetary benefit @ ${WTP_THRESHOLD:,.0f}/QALY: {b['nmb']:>12,.0f}",
        "-" * 62,
        f"PROBABILISTIC SENSITIVITY ({PSA_ITERATIONS:,} iterations)",
        f"  P(cost-effective @ ${WTP_THRESHOLD:,.0f}/QALY), perspective A: {p_ce_a:>7.1%}",
        f"  P(cost-effective @ ${WTP_THRESHOLD:,.0f}/QALY), perspective B: {p_ce_b:>7.1%}",
        f"  P(cost-saving outright), perspective B:            {p_saving_b:>7.1%}",
        f"  Mean incremental cost, perspective B:      {mean_cost_b:>12,.0f}",
        "-" * 62,
        "MOST INFLUENTIAL PARAMETERS (one-way, perspective B)",
    ]
    for t in tornado[:3]:
        flip = "  ** flips the decision **" if t["flips_decision"] else ""
        lines.append(f"  {t['parameter']:<26} swing ${t['swing']:>12,.0f}{flip}")

    (OUT / "hta_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
