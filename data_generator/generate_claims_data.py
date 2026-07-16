"""
Synthetic healthcare claims dataset for revenue-cycle analytics.

Models the claim lifecycle a hospital revenue-cycle team manages:
submission -> adjudication -> paid / denied (CARC reason) / pending AR.
Payer behavior is deliberately differentiated (contractual rates, denial
rates, adjudication lag) so the dashboard has real signal to show.

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

AR_BUCKETS = [(30, "0-30"), (60, "31-60"), (90, "61-90"), (120, "91-120"), (10**6, "120+")]
BUCKET_SORT = {"0-30": 1, "31-60": 2, "61-90": 3, "91-120": 4, "120+": 5}


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
        payer = random.choices(PAYERS, weights=PAYER_WEIGHTS)[0]
        sl = random.choices(SERVICE_LINES, weights=SL_WEIGHTS)[0]
        provider = random.choice(providers)
        # Anchor to the snapshot: a claim can only be on the books if it was
        # submitted on or before the as-of date. Generating the submission date
        # first (then working back to the service date) guarantees AR age >= 1
        # day — a snapshot must never contain a claim submitted in its future.
        submitted_date = AS_OF - timedelta(days=random.randint(1, 365))
        service_date = submitted_date - timedelta(days=random.randint(1, 14))
        submitted = round(random.lognormvariate(sl[2], 0.55) + 40, 2)

        payer_id, _, _, contract, denial_rate, lag_mean, collect_mean = payer

        # Recent submissions are disproportionately still pending (real AR shape).
        days_since_submit = (AS_OF - submitted_date).days
        pending_prob = 0.85 if days_since_submit < 20 else (
            0.30 if days_since_submit < 45 else 0.06)

        if random.random() < pending_prob:
            status, allowed, paid = "Pending", "", ""
            adjudicated_date, days_adj, reason, resubmitted = "", "", "", 0
            age = days_since_submit
            bucket, bucket_sort = ar_bucket(age), BUCKET_SORT[ar_bucket(age)]
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
                resubmitted = 1 if random.random() < 0.40 else 0
            else:
                status = "Paid"
                allowed = round(submitted * contract * random.uniform(0.92, 1.05), 2)
                allowed = min(allowed, submitted)
                collect = min(1.0, max(0.03, random.gauss(collect_mean, collect_mean * 0.20)))
                paid = round(allowed * collect, 2)
                reason, resubmitted = "", 0

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
        })

    datasets = [
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
