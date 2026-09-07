"""
Synthetic healthcare claims dataset for revenue-cycle analytics.

Models the claim lifecycle a hospital revenue-cycle team manages:
service -> coding -> submission -> adjudication -> paid / denied (CARC reason)
/ appealed / pending AR. Payer behavior is deliberately differentiated
(fee schedules by service line, denial rates, appeal-overturn rates by reason,
adjudication lag, collection rates) so the dashboard has real signal to show -
including three payer x service-line combinations that systematically pay under
their own contracted rate, which is what contract management exists to catch.

Synthetic only: no PHI, no real patients, providers, or payer contracts.
Fixed seed so CI and the Power BI screenshots are reproducible.

Usage:
    python generate_claims_data.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

AS_OF = date(2026, 7, 1)  # AR aging is measured as of this date
N_CLAIMS = 12000

# payer_id, name, type, contractual factor (allowed/submitted),
# denial rate, adjudication lag mean days, net collection rate (paid/allowed).
#
# Net collection rate is where payer economics diverge most. Government and
# commercial payers pay ~93-97% of the allowed (contracted) amount on paid
# claims. Self-Pay is billed full charges (contractual factor 1.0) but collects
# only ~20% of it — the rest ages into patient bad debt. This is exactly why a
# dollar of Self-Pay AR is worth a fraction of a dollar of Medicare AR, and the
# yield engine has to see it in the data to model it.
PAYERS = [
    (1, "Medicare", "Medicare", 0.52, 0.055, 14, 0.97),
    (2, "Medicaid", "Medicaid", 0.45, 0.120, 32, 0.93),
    (3, "Blue Cross Blue Shield", "Commercial", 0.68, 0.085, 21, 0.96),
    (4, "UnitedHealthcare", "Commercial", 0.65, 0.095, 24, 0.95),
    (5, "Aetna", "Commercial", 0.66, 0.080, 20, 0.96),
    (6, "Cigna", "Commercial", 0.64, 0.090, 22, 0.95),
    (7, "Humana Medicare Advantage", "Medicare Advantage", 0.55, 0.100, 26, 0.95),
    (8, "Self-Pay", "Self-Pay", 1.00, 0.040, 45, 0.20),
]
PAYER_WEIGHTS = [0.24, 0.14, 0.16, 0.14, 0.10, 0.09, 0.09, 0.04]

# service_line_id, name, charge scale (lognormal mu)
SERVICE_LINES = [
    (1, "Emergency Department", 7.2),
    (2, "Cardiology", 8.1),
    (3, "Orthopedics", 8.4),
    (4, "Oncology", 8.6),
    (5, "Imaging", 6.8),
    (6, "Laboratory", 5.6),
    (7, "Surgery", 8.9),
    (8, "Primary Care", 5.9),
    (9, "OB/GYN", 7.6),
    (10, "Behavioral Health", 6.4),
]
SL_WEIGHTS = [0.16, 0.10, 0.09, 0.07, 0.14, 0.18, 0.07, 0.11, 0.05, 0.03]

FACILITIES = ["Main Campus", "North Clinic", "South Clinic", "Telehealth"]
FIRST = ["Sarah", "James", "Maria", "David", "Jennifer", "Michael", "Priya",
         "Robert", "Aisha", "Daniel", "Emily", "Ahmed", "Laura", "Kevin",
         "Sofia", "Brian", "Grace", "Omar", "Rachel", "Thomas"]
LAST = ["Chen", "Patel", "Nguyen", "Garcia", "Smith", "Johnson", "Kim",
        "Brown", "Singh", "Martinez", "Lee", "Wilson", "Ali", "Taylor",
        "Lopez", "Davis", "Okafor", "Clark", "Ivanov", "Murphy"]

# CARC-style denial reasons with realistic mix
DENIAL_REASONS = [
    ("CO-16 Missing or invalid information", 0.25),
    ("CO-97 Service bundled/included", 0.15),
    ("CO-11 Diagnosis inconsistent with procedure", 0.15),
    ("CO-50 Not medically necessary", 0.14),
    ("CO-45 Exceeds fee schedule", 0.12),
    ("CO-29 Timely filing limit expired", 0.11),
    ("PR-1 Deductible amount", 0.08),
]

# ---------------------------------------------------------------------------
# Contract management, appeals and operations
# ---------------------------------------------------------------------------

# Coding complexity per service line: the DNFB driver. A surgical case waits on
# an operative note and a coder; a lab result posts itself. Charge lag is
# service-to-submission, and it is the one part of days-in-AR the revenue cycle
# controls without the payer's cooperation.
CODING_LAG = {
    1: (2.0, 1.2),    # Emergency Department
    2: (4.5, 2.0),    # Cardiology
    3: (6.0, 2.6),    # Orthopedics
    4: (7.5, 3.0),    # Oncology
    5: (1.5, 0.8),    # Imaging
    6: (1.0, 0.5),    # Laboratory
    7: (9.0, 3.5),    # Surgery  <- the DNFB problem
    8: (2.0, 1.0),    # Primary Care
    9: (4.0, 1.8),    # OB/GYN
    10: (3.0, 1.5),   # Behavioral Health
}

# Per-service-line multiplier on the payer's headline contractual factor. A
# contract is a fee schedule, not a single rate: the same payer pays 0.7x of
# billed on imaging and 0.55x on surgery, and a variance report that compares
# every claim to one blended number finds underpayments that are not there and
# misses the ones that are.
CONTRACT_MULTIPLIER = {
    1: 1.05, 2: 0.98, 3: 0.94, 4: 0.92, 5: 1.10,
    6: 1.14, 7: 0.90, 8: 1.08, 9: 1.00, 10: 1.04,
}

# Payer x service-line combinations where the payer systematically pays under
# its own contracted rate. This is what contract management exists to catch,
# and it is invisible without a fee schedule to compare against.
# A claim must be under contract by more than this before it counts as an
# underpayment. Adjudication rounds every allowed amount a few percent either
# way; without a materiality band the variance report flags half the book.
UNDERPAYMENT_MATERIALITY = 0.05

UNDERPAYING = {
    (3, 7): 0.88,     # BCBS on Surgery
    (4, 4): 0.90,     # UnitedHealthcare on Oncology
    (7, 2): 0.86,     # Humana MA on Cardiology
}

# Probability a denial is appealed, and probability the appeal is overturned,
# by denial reason. These differ enormously and the difference is the whole
# finding: a missing-information denial is a clerical fix that almost always
# comes back, a timely-filing denial is money that is gone.
APPEAL_BEHAVIOUR = {
    "CO-16 Missing or invalid information":        (0.78, 0.82),
    "CO-97 Service bundled/included":              (0.42, 0.35),
    "CO-11 Diagnosis inconsistent with procedure": (0.62, 0.66),
    "CO-50 Not medically necessary":               (0.58, 0.44),
    "CO-45 Exceeds fee schedule":                  (0.48, 0.52),
    "CO-29 Timely filing limit expired":           (0.22, 0.06),
    "PR-1 Deductible amount":                      (0.10, 0.12),
}

AR_BUCKETS = [(30, "0-30"), (60, "31-60"), (90, "61-90"), (120, "91-120"), (10**6, "120+")]
BUCKET_SORT = {"0-30": 1, "31-60": 2, "61-90": 3, "91-120": 4, "120+": 5}


# ---------------------------------------------------------------------------
# The shape of the year
# ---------------------------------------------------------------------------

# Volume grows through the year. Drawn by weighting the submission date rather
# than by generating months separately, so every other distribution in the file
# stays exactly as it was.
VOLUME_GROWTH = 0.15        # last month runs this much hotter than the first

# Payer mix drifts toward Medicare Advantage as the commercial book erodes.
# MA pays a materially lower share of billed than commercial does, so the mix
# effect works against revenue per case even when nothing about a single
# contract has changed. Weights are (start-of-year, end-of-year) pairs.
PAYER_WEIGHT_DRIFT = [
    (0.24, 0.23),   # Medicare
    (0.14, 0.14),   # Medicaid
    (0.17, 0.13),   # Blue Cross Blue Shield   <- losing share
    (0.15, 0.12),   # UnitedHealthcare         <- losing share
    (0.11, 0.09),   # Aetna
    (0.09, 0.08),   # Cigna
    (0.06, 0.17),   # Humana Medicare Advantage <- taking it
    (0.04, 0.04),   # Self-Pay
]

# Medicare's fee schedule update. A real one lands on a date and applies to
# everything after it, which is what makes it a RATE effect rather than a mix
# one - and why a bridge that cannot separate the two is not much use in the
# year a payer cuts its rates.
FEE_SCHEDULE_CUT_DAY = 200          # days before AS_OF
FEE_SCHEDULE_CUT_PAYERS = {1: 0.955}


def year_position(submitted_date):
    """0.0 at the start of the generated year, 1.0 at the snapshot."""
    return 1.0 - (AS_OF - submitted_date).days / 365.0


def draw_submitted_date():
    """A submission date, with more of them later in the year."""
    # Two uniforms and a keep-the-later-one bias give a clean linear ramp
    # without pulling in a distribution the rest of this file does not use.
    a = random.randint(1, 365)
    if random.random() < VOLUME_GROWTH:
        a = min(a, random.randint(1, 365))
    return AS_OF - timedelta(days=a)


def draw_payer(submitted_date):
    t = year_position(submitted_date)
    weights = [s + (e - s) * t for s, e in PAYER_WEIGHT_DRIFT]
    return random.choices(PAYERS, weights=weights)[0]


def contracted_rate(payer_id, contract_factor, service_line_id):
    """Expected allowed-to-billed rate for this payer and service line."""
    rate = contract_factor * CONTRACT_MULTIPLIER[service_line_id]
    return round(min(rate, 1.0), 4)


def charge_lag(service_line_id):
    mean, sd = CODING_LAG[service_line_id]
    return max(0, int(round(random.gauss(mean, sd))))


def pick_denial_reason():
    r = random.random()
    acc = 0.0
    for reason, w in DENIAL_REASONS:
        acc += w
        if r < acc:
            return reason
    return DENIAL_REASONS[-1][0]


def ar_bucket(days):
    for limit, name in AR_BUCKETS:
        if days <= limit:
            return name
    return "120+"


def main():
    providers = []
    for pid in range(1, 21):
        sl = SERVICE_LINES[(pid - 1) % len(SERVICE_LINES)]
        providers.append({
            "provider_id": pid,
            "provider_name": f"Dr. {FIRST[pid - 1]} {LAST[pid - 1]}",
            "specialty": sl[1],
            "facility": random.choice(FACILITIES),
        })

    claims = []
    for i in range(1, N_CLAIMS + 1):
        submitted_date = draw_submitted_date()
        payer = draw_payer(submitted_date)
        sl = random.choices(SERVICE_LINES, weights=SL_WEIGHTS)[0]
        provider = random.choice(providers)
        # Anchor to the snapshot: a claim can only be on the books if it was
        # submitted on or before the as-of date. Generating the submission date
        # first (then working back to the service date) guarantees AR age >= 1
        # day — a snapshot must never contain a claim submitted in its future.
        # Charge lag is service-line driven, not uniform noise: it is the DNFB
        # half of days-in-AR and the half the hospital owns.
        lag_days = charge_lag(sl[0])
        service_date = submitted_date - timedelta(days=lag_days)
        submitted = round(random.lognormvariate(sl[2], 0.55) + 40, 2)

        payer_id, _, _, contract, denial_rate, lag_mean, collect_mean = payer
        rate = contracted_rate(payer_id, contract, sl[0])
        # The fee-schedule update applies from its effective date onward.
        cut_from = AS_OF - timedelta(days=FEE_SCHEDULE_CUT_DAY)
        if payer_id in FEE_SCHEDULE_CUT_PAYERS and submitted_date >= cut_from:
            rate = round(rate * FEE_SCHEDULE_CUT_PAYERS[payer_id], 4)
        expected_allowed = round(submitted * rate, 2)

        # Recent submissions are disproportionately still pending (real AR shape).
        days_since_submit = (AS_OF - submitted_date).days
        pending_prob = 0.85 if days_since_submit < 20 else (
            0.30 if days_since_submit < 45 else 0.06)

        appeal_outcome, recovered, appeal_days = "", "", ""
        underpaid = ""

        if random.random() < pending_prob:
            status, allowed, paid = "Pending", "", ""
            adjudicated_date, days_adj, reason, resubmitted = "", "", "", 0
            age = days_since_submit
            bucket, bucket_sort = ar_bucket(age), BUCKET_SORT[ar_bucket(age)]
            # An open claim still consumes follow-up: the older it is, the more.
            touches = 1 + int(age // 45)
        else:
            lag = max(3, int(random.gauss(lag_mean, lag_mean * 0.3)))
            adj = submitted_date + timedelta(days=lag)
            adjudicated_date = min(adj, AS_OF - timedelta(days=1)).isoformat()
            days_adj = lag
            age, bucket, bucket_sort = "", "", ""
            if random.random() < denial_rate:
                status = "Denied"
                allowed, paid = 0.0, 0.0
                reason = pick_denial_reason()
                appeal_p, overturn_p = APPEAL_BEHAVIOUR[reason]
                resubmitted = 1 if random.random() < appeal_p else 0
                if resubmitted:
                    appeal_days = max(7, int(random.gauss(38, 14)))
                    if random.random() < overturn_p:
                        appeal_outcome = "Overturned"
                        # An overturned denial pays at contract, less the
                        # collection leakage every paid claim carries.
                        collect = min(1.0, max(0.03, random.gauss(
                            collect_mean, collect_mean * 0.20)))
                        recovered = round(expected_allowed * collect, 2)
                    else:
                        appeal_outcome = "Upheld"
                        recovered = 0.0
                else:
                    appeal_outcome = "Not appealed"
                    recovered = 0.0
                touches = 3 + (4 if resubmitted else 0) + random.randint(0, 3)
            else:
                status = "Paid"
                # Paid at the contracted rate, with the usual adjudication
                # noise - except on the payer x service-line combinations that
                # systematically pay under contract.
                shortfall = UNDERPAYING.get((payer_id, sl[0]), 1.0)
                allowed = round(expected_allowed * shortfall
                                * random.uniform(0.97, 1.03), 2)
                allowed = min(allowed, submitted)
                collect = min(1.0, max(0.03, random.gauss(collect_mean, collect_mean * 0.20)))
                paid = round(allowed * collect, 2)
                reason, resubmitted = "", 0
                appeal_outcome, recovered = "", ""
                # Materiality: adjudication noise moves the allowed amount a
                # few percent either way on every claim, so a variance report
                # that flags every dollar below contract flags half the book.
                # Only a shortfall past the noise band is an underpayment.
                shortfall_amt = expected_allowed - allowed
                underpaid = round(shortfall_amt, 2)                     if shortfall_amt > expected_allowed * UNDERPAYMENT_MATERIALITY else 0.0
                touches = 1 if underpaid < 1.0 else 2

        claims.append({
            "claim_id": f"CLM-{i:06d}",
            "service_date": service_date.isoformat(),
            "submitted_date": submitted_date.isoformat(),
            "adjudicated_date": adjudicated_date,
            "payer_id": payer_id,
            "provider_id": provider["provider_id"],
            "service_line_id": sl[0],
            "status": status,
            "submitted_amount": submitted,
            "allowed_amount": allowed,
            "paid_amount": paid,
            "denial_reason": reason,
            "resubmitted": resubmitted,
            "days_to_adjudicate": days_adj,
            "ar_age_days": age,
            "ar_bucket": bucket,
            "ar_bucket_sort": bucket_sort,
            "charge_lag_days": lag_days,
            "contracted_rate": rate,
            "expected_allowed": expected_allowed,
            "underpaid_amount": underpaid,
            "appeal_outcome": appeal_outcome,
            "appeal_days": appeal_days,
            "recovered_amount": recovered,
            "follow_up_touches": touches,
        })

    # The fee schedule, published as a dimension so the variance report reads
    # the contract rather than re-deriving it - which is how a contract report
    # ends up agreeing with itself no matter what the payer did.
    contracts = []
    for payer_id, name, ptype, contract, _dr, _lag, _cr in PAYERS:
        for sl_id, sl_name, _mu in SERVICE_LINES:
            contracts.append({
                "payer_id": payer_id,
                "payer_name": name,
                "service_line_id": sl_id,
                "service_line": sl_name,
                "contracted_rate": contracted_rate(payer_id, contract, sl_id),
                "fee_schedule_update": FEE_SCHEDULE_CUT_PAYERS.get(payer_id, 1.0),
                "update_effective": (AS_OF - timedelta(days=FEE_SCHEDULE_CUT_DAY)
                                     ).isoformat() if payer_id in FEE_SCHEDULE_CUT_PAYERS
                                    else "",
            })

    datasets = [
        ("dim_payer_contract.csv", contracts),
        ("dim_payer.csv", [{"payer_id": p[0], "payer_name": p[1], "payer_type": p[2]} for p in PAYERS]),
        ("dim_provider.csv", providers),
        ("dim_service_line.csv", [{"service_line_id": s[0], "service_line": s[1]} for s in SERVICE_LINES]),
        ("fact_claims.csv", claims),
    ]
    for fname, rows in datasets:
        with open(OUT / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows):5d} rows -> {fname}")


if __name__ == "__main__":
    main()
