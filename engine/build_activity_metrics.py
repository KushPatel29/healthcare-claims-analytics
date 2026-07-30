"""
Acute-care activity and funding metrics for a Canadian health authority.

Produces the four indicators a decision-support office reports every month, and
the control charts that say whether any of them actually moved:

  * **Cost per weighted case (CPWC)** — total cost / sum of RIW. The only fair
    way to compare a tertiary site against a community site, because it prices
    the case mix instead of the case count.
  * **LOS index** — actual acute days / expected acute days, indirectly
    standardised. Above 1.00 means patients stay longer than their case mix,
    age, and comorbidity predict.
  * **ALC rate and conservable bed days** — the share of patient days spent by
    patients who no longer need acute care, expressed in the unit an executive
    can act on: how many staffed beds that is, every day, all year.
  * **Risk-adjusted 30-day readmission** — observed over expected, where
    expected comes from the authority's own stratum rates.

Every output is a CSV the Power BI model reads and the test suite re-derives,
so no number on a slide exists only inside a visual.

Usage:
    python engine/build_activity_metrics.py
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spc import p_chart, u_chart, western_electric, dispersion_factor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# Empirical-Bayes pseudo-count for indirect standardisation. A stratum with 6
# discharges cannot carry its own rate; without shrinkage a single readmission
# in a thin (age x comorbidity x program) cell produces a 17% "expected" rate
# and every facility that treats those patients is scored against noise. Same
# technique the NRV yield engine uses on thin payer x service-line cells.
SHRINKAGE_K = 25.0

# What a staffed acute bed-day costs when it is occupied by an ALC patient, and
# how much of that is genuinely variable (see docs/BUSINESS_CASE.md — the
# distinction between an avoided bed day and a released dollar is the single
# most common error in health-system business cases).
ALC_PER_DIEM = 1150.0


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def shrink(cell_sum, cell_n, prior, k=SHRINKAGE_K):
    return (cell_sum + k * prior) / (cell_n + k)


def indirect_expectation(records, strata_fn, value_fn):
    """Indirect standardisation: give every record the authority-wide rate for
    its own stratum, so a facility's expected value reflects the patients it
    actually treats.

    Returns (expected_by_record, stratum_rate). By construction the sum of the
    expectations equals the sum of the observations across the whole authority
    — the authority-wide ratio is exactly 1.00, and each site's ratio is read
    relative to it. That identity is asserted in the test suite; if it ever
    breaks, the standardisation is wrong.
    """
    grand_sum = sum(value_fn(r) for r in records)
    grand_n = len(records)
    grand_rate = grand_sum / grand_n if grand_n else 0.0

    cell_sum = defaultdict(float)
    cell_n = defaultdict(int)
    for r in records:
        key = strata_fn(r)
        cell_sum[key] += value_fn(r)
        cell_n[key] += 1

    rate = {k: shrink(cell_sum[k], cell_n[k], grand_rate) for k in cell_sum}

    # Shrinkage pulls thin cells toward the grand rate, which leaves the total
    # expectation slightly off the total observation. Rescaling restores the
    # identity without disturbing the *relative* stratum rates that do the work.
    raw_total = sum(rate[strata_fn(r)] for r in records)
    scale = (grand_sum / raw_total) if raw_total > 0 else 1.0
    rate = {k: v * scale for k, v in rate.items()}

    return {id(r): rate[strata_fn(r)] for r in records}, rate


def summarise(records, key_fn, expected_readmit, expected_los):
    """Roll activity up to whatever grain `key_fn` returns."""
    agg = defaultdict(lambda: {
        "cases": 0, "weighted_cases": 0.0, "total_cost": 0.0,
        "acute_days": 0, "alc_days": 0, "patient_days": 0, "alc_stays": 0,
        "obs_readmit": 0, "exp_readmit": 0.0, "exp_acute_days": 0.0,
    })
    for r in records:
        a = agg[key_fn(r)]
        a["cases"] += 1
        a["weighted_cases"] += float(r["riw"])
        a["total_cost"] += float(r["total_cost"])
        a["acute_days"] += int(r["acute_los_days"])
        a["alc_days"] += int(r["alc_days"])
        a["patient_days"] += int(r["total_los_days"])
        a["alc_stays"] += 1 if int(r["alc_days"]) > 0 else 0
        a["obs_readmit"] += int(r["readmit_30d"])
        a["exp_readmit"] += expected_readmit[id(r)]
        a["exp_acute_days"] += expected_los[id(r)]
    return agg


def rows_from(agg, label):
    out = []
    for key in sorted(agg):
        a = agg[key]
        out.append({
            label: key,
            "discharges": a["cases"],
            "weighted_cases": round(a["weighted_cases"], 2),
            "total_cost": round(a["total_cost"], 2),
            "cost_per_weighted_case": round(a["total_cost"] / a["weighted_cases"], 2),
            # Splitting ALC out of CPWC is not cosmetic. ALC cost is not a
            # measure of how efficiently the site treats its cases — it is the
            # price of a discharge destination that does not exist. Leaving it
            # inside CPWC makes the site with the weakest community capacity
            # look like the site with the worst cost control.
            "cost_per_weighted_case_ex_alc": round(
                (a["total_cost"] - a["alc_days"] * ALC_PER_DIEM) / a["weighted_cases"], 2),
            "acute_days": a["acute_days"],
            "expected_acute_days": round(a["exp_acute_days"], 1),
            "los_index": round(a["acute_days"] / a["exp_acute_days"], 4),
            "alc_days": a["alc_days"],
            "alc_stays": a["alc_stays"],
            "patient_days": a["patient_days"],
            "alc_rate": round(a["alc_days"] / a["patient_days"], 4),
            "alc_stay_rate": round(a["alc_stays"] / a["cases"], 4),
            "alc_bed_equivalents": round(a["alc_days"] / 730.0, 2),
            "alc_cost": round(a["alc_days"] * ALC_PER_DIEM, 2),
            "observed_readmits": a["obs_readmit"],
            "expected_readmits": round(a["exp_readmit"], 2),
            "readmit_rate": round(a["obs_readmit"] / a["cases"], 4),
            "readmit_oe_ratio": round(a["obs_readmit"] / a["exp_readmit"], 4)
            if a["exp_readmit"] > 0 else None,
        })
    return out


def write_csv(name, rows):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def main():
    abstracts = load("fact_inpatient_abstracts.csv")
    facility_name = {r["facility_id"]: r["facility_name"]
                     for r in load("dim_facility.csv")}
    cmg_name = {r["cmg_code"]: r["cmg_name"] for r in load("dim_cmg.csv")}

    # ---- risk adjustment -------------------------------------------------
    # Readmission is standardised on the clinical drivers a facility does not
    # choose (who walks in the door); LOS is standardised on case mix and
    # comorbidity. Neither adjusts for facility, which is the whole point:
    # whatever is left after standardisation is the facility's to explain.
    expected_readmit, _ = indirect_expectation(
        abstracts,
        lambda r: (r["program"], r["age_band"], r["comorbidity_level"]),
        lambda r: int(r["readmit_30d"]),
    )
    expected_los, _ = indirect_expectation(
        abstracts,
        lambda r: (r["cmg_code"], r["comorbidity_level"]),
        lambda r: int(r["acute_los_days"]),
    )

    by_facility = summarise(abstracts, lambda r: facility_name[r["facility_id"]],
                            expected_readmit, expected_los)
    by_program = summarise(abstracts, lambda r: r["program"],
                           expected_readmit, expected_los)
    by_cmg = summarise(abstracts, lambda r: cmg_name[r["cmg_code"]],
                       expected_readmit, expected_los)
    by_month = summarise(abstracts, lambda r: r["discharge_month"],
                         expected_readmit, expected_los)

    write_csv("activity_by_facility.csv", rows_from(by_facility, "facility_name"))
    write_csv("activity_by_program.csv", rows_from(by_program, "program"))
    write_csv("activity_by_cmg.csv", rows_from(by_cmg, "cmg_name"))
    monthly = rows_from(by_month, "discharge_month")
    write_csv("activity_monthly.csv", monthly)

    # ---- statistical process control -------------------------------------
    # Three charts, and the third one exists because the second one lies.
    #
    #  1. Readmission rate — a p-chart. One independent observation per
    #     discharge, so the binomial model holds and plain limits are correct.
    #
    #  2. ALC days per 100 patient days — a u-chart. This is the number
    #     executives ask for, and it is badly overdispersed: one patient waiting
    #     60 days contributes 60 correlated days, not 60 Poisson events. Run
    #     naively it signals in most months, which is not a finding, it is a
    #     broken chart. Laney's u-prime correction is applied, and the naive
    #     version is computed alongside it purely to show the difference.
    #
    #  3. Share of stays with any ALC day — a p-chart. Changing the unit of
    #     analysis from days to patients removes the clustering at the source,
    #     which is a better fix than correcting for it after the fact.
    # Limits are set from the first 18 months and extended over the 6 months
    # being monitored. The baseline is chosen by a standing rule — the period
    # preceding the current monitoring window — not by looking at the data and
    # picking the split that makes the answer come out. Setting limits from the
    # full series would let a late shift drag the centre line up and flag every
    # quiet month before it as a *downward* special cause.
    BASELINE_MONTHS = 18

    readmit_chart = p_chart(
        ((m["discharge_month"], m["observed_readmits"], m["discharges"])
         for m in monthly), laney=True, baseline=BASELINE_MONTHS)

    alc_points = [(m["discharge_month"], m["alc_days"], m["patient_days"] / 100.0)
                  for m in monthly]
    # kept only for the comparison in the summary
    alc_naive = u_chart(alc_points, baseline=BASELINE_MONTHS)
    alc_chart = u_chart(alc_points, laney=True, baseline=BASELINE_MONTHS)

    alc_stay_chart = p_chart(
        ((m["discharge_month"], m["alc_stays"], m["discharges"])
         for m in monthly), laney=True, baseline=BASELINE_MONTHS)

    write_csv("spc_readmission_pchart.csv", [
        {"month": p["label"], "readmits": p["numerator"], "discharges": p["denominator"],
         "rate": round(p["value"], 5), "centre": round(p["centre"], 5),
         "lcl": round(p["lcl"], 5), "ucl": round(p["ucl"], 5),
         "sigma": round(p["sigma"], 6), "z": round(p["z"], 3),
         "dispersion_factor": p["dispersion_factor"]}
        for p in readmit_chart])

    write_csv("spc_alc_uchart.csv", [
        {"month": p["label"], "alc_days": p["count"],
         "patient_days_per_100": round(p["exposure"], 2),
         "alc_days_per_100_patient_days": round(p["value"], 4),
         "centre": round(p["centre"], 4), "lcl": round(p["lcl"], 4),
         "ucl": round(p["ucl"], 4), "sigma": round(p["sigma"], 5),
         "z": round(p["z"], 3), "dispersion_factor": p["dispersion_factor"]}
        for p in alc_chart])

    write_csv("spc_alc_stay_pchart.csv", [
        {"month": p["label"], "stays_with_alc": p["numerator"],
         "discharges": p["denominator"], "rate": round(p["value"], 5),
         "centre": round(p["centre"], 5), "lcl": round(p["lcl"], 5),
         "ucl": round(p["ucl"], 5), "sigma": round(p["sigma"], 6),
         "z": round(p["z"], 3), "dispersion_factor": p["dispersion_factor"]}
        for p in alc_stay_chart])

    charts = [
        ("30-day readmission rate (p-prime)", readmit_chart),
        ("ALC days per 100 patient days (u-prime)", alc_chart),
        ("Stays with any ALC day (p-prime)", alc_stay_chart),
    ]
    signals = []
    for name, ch in charts:
        signals += [dict(s, indicator=name) for s in western_electric(ch)]

    naive_alc_signals = len(western_electric(alc_naive))
    alc_dispersion = dispersion_factor(alc_naive)

    if signals:
        write_csv("spc_signals.csv", [
            {"indicator": s["indicator"], "month": s["label"], "rule": s["rule"],
             "direction": s["direction"], "z": round(s["z"], 3),
             "description": s["description"]}
            for s in sorted(signals, key=lambda s: (s["indicator"], s["label"], s["rule"]))])
    else:
        write_csv("spc_signals.csv", [{"indicator": "", "month": "", "rule": "",
                                       "direction": "", "z": "", "description": ""}])

    # ---- authority-level summary ----------------------------------------
    cases = len(abstracts)
    wc = sum(float(r["riw"]) for r in abstracts)
    cost = sum(float(r["total_cost"]) for r in abstracts)
    acute = sum(int(r["acute_los_days"]) for r in abstracts)
    alc = sum(int(r["alc_days"]) for r in abstracts)
    pdays = acute + alc
    readmits = sum(int(r["readmit_30d"]) for r in abstracts)
    alc_stays = sum(1 for r in abstracts if int(r["alc_days"]) > 0)

    fac_rows = rows_from(by_facility, "facility_name")
    worst_alc = max(fac_rows, key=lambda r: r["alc_rate"])
    worst_cpwc = max(fac_rows, key=lambda r: r["cost_per_weighted_case_ex_alc"])
    worst_los = max(fac_rows, key=lambda r: r["los_index"])
    alc_cost = alc * ALC_PER_DIEM

    lines = [
        "ACUTE CARE ACTIVITY & FUNDING SUMMARY",
        "=" * 58,
        f"Discharges:                        {cases:>12,}",
        f"Weighted cases (sum RIW):          {wc:>12,.1f}",
        f"Total cost:                        {cost:>12,.0f}",
        f"Cost per weighted case:            {cost / wc:>12,.0f}",
        f"  ...excluding ALC:                {(cost - alc_cost) / wc:>12,.0f}",
        f"  ...ALC component:                {alc_cost / wc:>12,.0f}",
        f"Acute patient days:                {acute:>12,}",
        f"ALC days:                          {alc:>12,}",
        f"ALC rate (% of patient days):      {alc / pdays:>12.1%}",
        f"Stays with any ALC day:            {alc_stays:>12,} ({alc_stays / cases:.1%})",
        f"ALC bed equivalents (24 mo):       {alc / 730.0:>12,.1f} beds",
        f"ALC cost @ ${ALC_PER_DIEM:,.0f}/day:            {alc_cost:>12,.0f}",
        f"30-day readmission rate:           {readmits / cases:>12.1%}",
        "-" * 58,
        "SITE VARIATION (risk-adjusted where noted)",
        f"Highest cost/weighted case ex-ALC: {worst_cpwc['facility_name']}"
        f" (${worst_cpwc['cost_per_weighted_case_ex_alc']:,.0f})",
        f"Highest LOS index:                 {worst_los['facility_name']}"
        f" ({worst_los['los_index']:.3f})",
        f"Highest ALC rate:                  {worst_alc['facility_name']}"
        f" ({worst_alc['alc_rate']:.1%} of patient days)",
        "-" * 58,
        "STATISTICAL PROCESS CONTROL",
        f"Special-cause signals (published charts): {len(signals)}",
        f"ALC overdispersion factor (Laney sigma_z): {alc_dispersion:.2f}x",
        f"  naive u-chart signals:      {naive_alc_signals:>3}  <- mostly false alarms",
        f"  u-prime corrected signals:  "
        f"{len([s for s in signals if s['indicator'].startswith('ALC')]):>3}",
    ]
    (OUT / "activity_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
