"""
Synthetic acute-care inpatient activity for a Canadian health authority.

Shaped like a CIHI **Discharge Abstract Database (DAD)** extract — the record
every Canadian acute hospital submits for every inpatient discharge, and the
substrate almost all health-authority decision support is built on:

    abstract -> CMG+ case mix group -> RIW (resource intensity weight)
             -> expected LOS (ELOS) -> acute days + ALC days -> disposition

Why this exists alongside the US revenue-cycle data in `data/`: in a
single-payer system there are no payers, no denials, and no bad-debt reserve.
A BC health authority does not ask "what is our denial rate" — it asks what a
weighted case costs, whether patients are staying longer than their case mix
predicts, how many beds are occupied by patients who no longer need acute care,
and whether this month's readmission rate is a signal or just noise.

Synthetic only: no PHI, no real patients, physicians, or facilities. Facility
names are invented. Fixed seed so CI and every published number are reproducible.

Usage:
    python canadian/generate_activity_data.py
"""

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(1867)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
OUT.mkdir(parents=True, exist_ok=True)

AS_OF = date(2026, 7, 1)          # same snapshot date as the revenue-cycle data
MONTHS = 24                       # 2024-07 .. 2026-06

# Volume is calibrated so the authority is internally consistent: ~20,000
# discharges a year against 490 staffed acute beds works out to roughly 85%
# occupancy at the modelled length of stay. That coherence matters — a bed-day
# business case built on a dataset whose occupancy is physically impossible is
# a business case nobody senior will read twice.
N_ABSTRACTS = 40000

# Target cost per weighted case for the authority as a whole, before facility
# variation. Calibrated to the order of magnitude CIHI publishes for Canadian
# acute inpatient care; the point of the model is the *variation* between sites,
# not the absolute level.
TARGET_CPWC = 7200.0

# An ALC day still consumes a staffed acute bed, but costs less than an acute
# day: the patient is medically stable, so the diagnostic, OR, and acute nursing
# intensity is gone. Modelled as a flat per-diem on top of the case's acute cost.
ALC_PER_DIEM = 1150.0

# facility_id, name, site type, staffed acute beds, cost factor, LOS factor,
# ALC odds factor, acuity
#
# Four separate site effects, because in a real authority they are separate
# problems with separate owners:
#   * cost factor — structural cost position per weighted case (teaching and
#     quaternary sites carry higher overhead). Owned by finance.
#   * LOS factor  — how long the same case mix stays. Owned by the clinical program.
#   * ALC factor  — how hard it is to discharge a stable patient, which is really
#     a measure of the *community's* residential and home-support capacity, not
#     the hospital's. Owned by nobody, which is why ALC is everyone's crisis.
#   * acuity      — how sick the patients are before anyone treats them. Owned by
#     no one at all, and the reason crude comparisons between sites are
#     worthless: a tertiary centre that takes every transfer and every complex
#     case will post a worse crude readmission rate than a community hospital
#     while doing better work. Risk adjustment exists to separate these two,
#     so the generator has to create the confound for the engine to remove.
FACILITIES = [
    (1, "Riverbend Regional Hospital",   "Tertiary",      165, 1.14, 1.03, 1.15,  1.00),
    (2, "Cedar Valley General Hospital", "Community",     100, 0.97, 0.98, 0.92,  0.00),
    (3, "Harbourview Hospital",          "Community",      78, 1.02, 1.06, 1.45, -0.15),
    (4, "Mount Ashton General Hospital", "Community",      55, 0.93, 0.95, 0.80, -0.35),
    (5, "Two Rivers Health Centre",      "Rural",          22, 1.09, 1.01, 1.30, -0.70),
    (6, "Fernwood Memorial Hospital",    "Community",      70, 0.99, 0.99, 0.95, -0.10),
]
FACILITY_WEIGHTS = [0.30, 0.20, 0.16, 0.13, 0.05, 0.16]

# A step increase in ALC risk part-way through the series. This is planted on
# purpose: a control chart you have never watched detect a real shift is
# decoration, so the data contains one shift, at a known date, and the SPC
# tests assert the u-chart finds it. (Post-pandemic erosion of residential-care
# and home-support capacity produced exactly this shape in real Canadian data.)
ALC_SHIFT_START = date(2026, 1, 1)
ALC_SHIFT_ODDS = 1.60

# cmg_code, cmg_name, program, RIW, expected LOS (ELOS, acute days)
# CMG codes are illustrative, not the real CIHI grouper's assignments.
CMGS = [
    ("139", "Chronic Obstructive Pulmonary Disease", "Medicine",           0.95,  5.5),
    ("140", "Pneumonia",                             "Medicine",           1.05,  6.2),
    ("145", "Urinary Tract Infection",               "Medicine",           0.70,  4.4),
    ("152", "Gastrointestinal Hemorrhage",           "Medicine",           0.88,  4.0),
    ("196", "Heart Failure",                         "Cardiac Sciences",   1.20,  7.0),
    ("202", "Acute Myocardial Infarction",           "Cardiac Sciences",   1.35,  4.8),
    ("208", "Coronary Artery Bypass Graft",          "Cardiac Sciences",   4.10,  8.5),
    ("025", "Stroke, Ischemic",                      "Neurosciences",      1.85,  9.4),
    ("032", "Seizure Disorder",                      "Neurosciences",      0.80,  3.6),
    ("305", "Hip Replacement",                       "Surgery",            2.05,  4.2),
    ("306", "Knee Replacement",                      "Surgery",            1.90,  3.8),
    ("284", "Bowel Resection",                       "Surgery",            2.60,  8.0),
    ("291", "Cholecystectomy",                       "Surgery",            1.10,  2.5),
    ("560", "Vaginal Delivery",                      "Maternity",          0.55,  1.9),
    ("563", "Cesarean Section",                      "Maternity",          0.90,  3.1),
    ("675", "Schizophrenia / Psychotic Disorder",    "Mental Health",      1.15, 14.0),
    ("680", "Substance-Related Disorder",            "Mental Health",      0.70,  5.5),
]
CMG_WEIGHTS = [0.085, 0.090, 0.070, 0.045, 0.080, 0.055, 0.020,
               0.055, 0.035, 0.060, 0.060, 0.030, 0.045,
               0.115, 0.055, 0.050, 0.050]

AGE_BANDS = ["0-17", "18-44", "45-64", "65-74", "75-84", "85+"]
# Maternity and mental health skew young; medicine and neurosciences skew old.
AGE_WEIGHTS_BY_PROGRAM = {
    "Medicine":         [0.02, 0.08, 0.20, 0.24, 0.28, 0.18],
    "Cardiac Sciences": [0.00, 0.05, 0.25, 0.30, 0.28, 0.12],
    "Neurosciences":    [0.02, 0.09, 0.22, 0.25, 0.27, 0.15],
    "Surgery":          [0.03, 0.16, 0.30, 0.26, 0.19, 0.06],
    "Maternity":        [0.04, 0.88, 0.08, 0.00, 0.00, 0.00],
    "Mental Health":    [0.06, 0.46, 0.30, 0.11, 0.05, 0.02],
}
# Older patients carry more comorbidity, which drives both LOS and ALC risk.
AGE_COMORBIDITY_SHIFT = {"0-17": -1.4, "18-44": -0.9, "45-64": -0.2,
                         "65-74": 0.4, "75-84": 0.9, "85+": 1.4}

ENTRY_VIA_ED = {"Medicine": 0.92, "Cardiac Sciences": 0.74, "Neurosciences": 0.85,
                "Surgery": 0.31, "Maternity": 0.12, "Mental Health": 0.78}

DISPOSITIONS = ["Home", "Home with Support Services", "Residential Care",
                "Transfer to Other Facility", "Died"]


def choose(options, weights):
    return random.choices(options, weights=weights)[0]


def logistic(x):
    return 1.0 / (1.0 + math.exp(-x))


def alc_probability(age_band, comorbidity, program, entry_via_ed,
                    facility_alc_factor, discharge_after_shift):
    """P(this stay accrues at least one alternate-level-of-care day).

    ALC is overwhelmingly a discharge-destination problem, not a medical one:
    the patient is stable but there is nowhere appropriate to send them. It
    concentrates in old, comorbid, medically complex patients who came in
    through emergency and cannot go home to the situation they came from.

    Site and period effects enter as log-odds shifts, so "Harbourview is 1.45x"
    means 1.45x the *odds*, not 1.45x the rate — the distinction matters once a
    baseline risk is already high.
    """
    z = (-3.95
         + 1.05 * AGE_COMORBIDITY_SHIFT[age_band]
         + 0.42 * comorbidity
         + (0.55 if program in ("Medicine", "Neurosciences") else 0.0)
         + (0.55 if program == "Mental Health" else 0.0)
         + (0.30 if entry_via_ed else 0.0)
         + math.log(facility_alc_factor)
         + (math.log(ALC_SHIFT_ODDS) if discharge_after_shift else 0.0))
    return logistic(z)


def readmission_probability(age_band, comorbidity, program, had_alc):
    """P(unplanned readmission to any site within 30 days of discharge).

    Deliberately driven by patient risk factors the engine can also observe, so
    the risk-adjusted (observed/expected) readmission ratio has something real
    to recover — an unadjusted rate mostly measures who your patients are, not
    how well you cared for them.
    """
    z = (-3.05
         + 0.30 * AGE_COMORBIDITY_SHIFT[age_band]
         + 0.34 * comorbidity
         + (0.35 if program in ("Medicine", "Cardiac Sciences") else 0.0)
         + (0.45 if program == "Mental Health" else 0.0)
         - (0.60 if program == "Maternity" else 0.0)
         + (0.28 if had_alc else 0.0))
    return logistic(z)


def main():
    facilities = [{"facility_id": f[0], "facility_name": f[1], "site_type": f[2],
                   "staffed_acute_beds": f[3]} for f in FACILITIES]
    facility_cost_factor = {f[0]: f[4] for f in FACILITIES}
    facility_los_factor = {f[0]: f[5] for f in FACILITIES}
    facility_alc_factor = {f[0]: f[6] for f in FACILITIES}
    facility_acuity = {f[0]: f[7] for f in FACILITIES}

    # Case mix follows acuity: a tertiary site sees proportionally more of the
    # heavy CMGs. Implemented by tilting the CMG weights toward high-RIW groups
    # in proportion to the site's acuity, then renormalising.
    cmg_weights_by_facility = {}
    for f in FACILITIES:
        tilt = [w * (c[3] ** (0.55 * f[7])) for w, c in zip(CMG_WEIGHTS, CMGS)]
        total = sum(tilt)
        cmg_weights_by_facility[f[0]] = [t / total for t in tilt]

    cmg_rows = [{"cmg_code": c[0], "cmg_name": c[1], "program": c[2],
                 "riw": c[3], "elos_days": c[4]} for c in CMGS]

    start = date(AS_OF.year, AS_OF.month, 1) - timedelta(days=1)
    # first day of the month MONTHS ago
    m = AS_OF.month - MONTHS
    y = AS_OF.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    window_start = date(y, m, 1)
    window_days = (start - window_start).days

    abstracts = []
    for i in range(1, N_ABSTRACTS + 1):
        fac = choose(FACILITIES, FACILITY_WEIGHTS)
        acuity = facility_acuity[fac[0]]
        cmg = choose(CMGS, cmg_weights_by_facility[fac[0]])
        cmg_code, cmg_name, program, riw_base, elos = cmg

        age_band = choose(AGE_BANDS, AGE_WEIGHTS_BY_PROGRAM[program])
        comorbidity = max(0, min(4, int(round(
            random.gauss(1.4 + AGE_COMORBIDITY_SHIFT[age_band] * 0.55
                         + 0.45 * acuity, 0.9)))))
        entry_via_ed = random.random() < ENTRY_VIA_ED[program]

        admit = window_start + timedelta(days=random.randint(0, max(1, window_days)))

        # Acute length of stay: gamma-ish around ELOS, inflated by comorbidity.
        # A stay is never shorter than a day.
        shape = 2.6
        scale = (elos * (1.0 + 0.14 * comorbidity)
                 * facility_los_factor[fac[0]]) / shape
        acute_los = max(1, int(round(random.gammavariate(shape, scale))))

        # ALC days sit on top of the acute stay and are the long tail that
        # wrecks a bed-day budget: most stays have none, a few have dozens.
        provisional_discharge = admit + timedelta(days=acute_los)
        after_shift = provisional_discharge >= ALC_SHIFT_START
        if random.random() < alc_probability(age_band, comorbidity, program,
                                             entry_via_ed,
                                             facility_alc_factor[fac[0]],
                                             after_shift):
            alc_days = max(1, int(round(random.expovariate(1 / 9.5))))
            alc_days = min(alc_days, 180)
        else:
            alc_days = 0

        total_los = acute_los + alc_days
        discharge = admit + timedelta(days=total_los)
        if discharge >= AS_OF:                    # keep the snapshot clean:
            continue                              # only completed stays count

        # RIW is assigned by the grouper from the case, not from what the site
        # actually spent — that is the whole point of a weighted case. It varies
        # with comorbidity level, which is exactly how CMG+ works.
        riw = round(riw_base * (1.0 + 0.11 * comorbidity)
                    * random.uniform(0.90, 1.12), 4)

        # Cost is where site performance lives: same weighted case, different
        # spend. Facility cost factor + noise; ALC days priced separately.
        acute_cost = TARGET_CPWC * riw * facility_cost_factor[fac[0]] \
            * random.lognormvariate(0.0, 0.20)
        total_cost = round(acute_cost + alc_days * ALC_PER_DIEM, 2)

        if random.random() < 0.021 + 0.012 * comorbidity:
            disposition = "Died"
        elif alc_days > 0 and random.random() < 0.42:
            disposition = "Residential Care"
        elif random.random() < 0.10:
            disposition = "Transfer to Other Facility"
        elif random.random() < 0.28:
            disposition = "Home with Support Services"
        else:
            disposition = "Home"

        if disposition == "Died":
            readmit = 0
        else:
            readmit = 1 if random.random() < readmission_probability(
                age_band, comorbidity, program, alc_days > 0) else 0

        abstracts.append({
            "abstract_id": f"DAD-{i:06d}",
            "facility_id": fac[0],
            "cmg_code": cmg_code,
            "program": program,
            "admit_date": admit.isoformat(),
            "discharge_date": discharge.isoformat(),
            "discharge_month": discharge.isoformat()[:7],
            "age_band": age_band,
            "comorbidity_level": comorbidity,
            "entry_via_ed": 1 if entry_via_ed else 0,
            "acute_los_days": acute_los,
            "alc_days": alc_days,
            "total_los_days": total_los,
            "elos_days": elos,
            "riw": riw,
            "total_cost": total_cost,
            "discharge_disposition": disposition,
            "readmit_30d": readmit,
        })

    datasets = [
        ("dim_facility.csv", facilities),
        ("dim_cmg.csv", cmg_rows),
        ("fact_inpatient_abstracts.csv", abstracts),
    ]
    for fname, rows in datasets:
        with open(OUT / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows):6d} rows -> {fname}")


if __name__ == "__main__":
    main()
