"""
Synthetic healthcare claims dataset for revenue-cycle analytics.

Models the claim lifecycle a hospital revenue-cycle team manages, end to end:

    service -> coding -> scrubber -> submission -> adjudication
            -> paid (payer share + patient share) / denied (CARC reason) / appealed
            -> pending AR / written off

Payer behaviour is deliberately differentiated (fee schedules by service line,
denial rates, appeal-overturn rates by reason, adjudication lag, collection rates,
patient cost-sharing) so the dashboard has real signal to show. Five things are
planted on purpose, because a dashboard that finds nothing is not worth opening:

1. three payer x service-line combinations that systematically pay under their own
   contracted rate - what contract management exists to catch;
2. a Medicare fee-schedule cut part-way through, so the revenue bridge has a rate
   effect to separate from mix;
3. a book drifting from commercial plans toward Medicare Advantage, so it has a mix
   effect too;
4. a payer that starts requiring prior authorization for imaging, so authorization
   denials spike from a date a control chart can find;
5. a claim-scrubber rule update that halves clearinghouse rejections, so the clean
   claim rate has a real improvement in it rather than noise.

Synthetic only: no PHI, no real patients, providers, or payer contracts. Fixed seed
so CI and the Power BI screenshots are reproducible.

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

AS_OF = date(2026, 7, 1)        # AR aging is measured as of this date
HISTORY_DAYS = 730              # two fiscal years, so the report can compare them
N_CLAIMS = 30000

# payer_id, name, type, contractual factor (allowed/submitted),
# denial rate, adjudication lag mean days, net collection rate on the PAYER's share.
#
# Net collection rate is where payer economics diverge most. Government and
# commercial payers pay ~97-99% of the portion of the allowed amount that is theirs;
# what they do not pay is the patient's share, and that is collected separately and
# far less completely (see PATIENT_SHARE below). Self-Pay is billed full charges
# (contractual factor 1.0) and the whole allowed amount is the patient's, which is
# why a dollar of Self-Pay AR is worth a fraction of a dollar of Medicare AR.
PAYERS = [
    (1, "Medicare", "Medicare", 0.52, 0.055, 14, 0.985),
    (2, "Medicaid", "Medicaid", 0.45, 0.120, 32, 0.975),
    (3, "Blue Cross Blue Shield", "Commercial", 0.68, 0.085, 21, 0.985),
    (4, "UnitedHealthcare", "Commercial", 0.65, 0.095, 24, 0.980),
    (5, "Aetna", "Commercial", 0.66, 0.080, 20, 0.985),
    (6, "Cigna", "Commercial", 0.64, 0.090, 22, 0.980),
    (7, "Humana Medicare Advantage", "Medicare Advantage", 0.55, 0.100, 26, 0.980),
    (8, "Self-Pay", "Self-Pay", 1.00, 0.040, 45, 0.900),
    (9, "Molina Healthcare", "Medicaid Managed Care", 0.47, 0.135, 34, 0.970),
    (10, "TRICARE", "Military", 0.58, 0.070, 19, 0.985),
    (11, "State Workers' Compensation", "Workers' Comp", 0.82, 0.110, 38, 0.975),
]

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

FACILITIES = ["Main Campus", "North Clinic", "South Clinic", "Ambulatory Surgery Center", "Telehealth"]
FIRST = ["Sarah", "James", "Maria", "David", "Jennifer", "Michael", "Priya",
         "Robert", "Aisha", "Daniel", "Emily", "Ahmed", "Laura", "Kevin",
         "Sofia", "Brian", "Grace", "Omar", "Rachel", "Thomas", "Nadia", "Victor",
         "Hannah", "Marcus", "Elena", "Jonah", "Clara", "Andre", "Leah", "Felix",
         "Rosa", "Dmitri", "Naomi", "Carlos", "Ingrid", "Tobias", "Yara", "Simon",
         "Maya", "Patrick"]
LAST = ["Chen", "Patel", "Nguyen", "Garcia", "Smith", "Johnson", "Kim",
        "Brown", "Singh", "Martinez", "Lee", "Wilson", "Ali", "Taylor",
        "Lopez", "Davis", "Okafor", "Clark", "Ivanov", "Murphy", "Haddad", "Rossi",
        "Silva", "Novak", "Dubois", "Meyer", "Fischer", "Larsen", "Keller", "Moreau",
        "Adeyemi", "Sato", "Petrov", "Cruz", "Weber", "Bianchi", "Hansen", "Osei",
        "Reyes", "Bauer"]

# The procedure a claim was billed for. Each is (code, description, charge multiplier
# against the service line's own scale) - a CT head and a CBC do not cost the same
# thing, and a denial report that cannot name the procedure cannot be worked.
PROCEDURES = {
    1: [("99283", "ED visit, moderate severity", 0.7), ("99284", "ED visit, high severity", 1.0),
        ("99285", "ED visit, high severity with threat", 1.5)],
    2: [("93000", "Electrocardiogram, routine", 0.3), ("93306", "Echocardiogram, complete", 1.0),
        ("92928", "Coronary stent placement", 2.6)],
    3: [("20610", "Major joint injection", 0.3), ("29881", "Knee arthroscopy with meniscectomy", 1.0),
        ("27447", "Total knee arthroplasty", 2.4)],
    4: [("96372", "Therapeutic injection", 0.2), ("96413", "Chemotherapy infusion, first hour", 1.0),
        ("77427", "Radiation treatment management", 1.3)],
    5: [("71046", "Chest x-ray, two views", 0.3), ("70450", "CT head without contrast", 1.0),
        ("72148", "MRI lumbar spine without contrast", 1.9)],
    6: [("85025", "Complete blood count with differential", 0.6), ("80053", "Comprehensive metabolic panel", 1.0),
        ("83036", "Hemoglobin A1c", 0.8)],
    7: [("49505", "Inguinal hernia repair", 0.7), ("47562", "Laparoscopic cholecystectomy", 1.0),
        ("44970", "Laparoscopic appendectomy", 1.2)],
    8: [("99213", "Office visit, established patient, low", 0.7), ("99214", "Office visit, established, moderate", 1.0),
        ("99396", "Preventive visit, 40-64 years", 1.1)],
    9: [("76805", "Obstetric ultrasound", 0.4), ("59400", "Obstetric care, vaginal delivery", 1.0),
        ("59510", "Obstetric care, caesarean delivery", 1.6)],
    10: [("90791", "Psychiatric diagnostic evaluation", 1.0), ("90834", "Psychotherapy, 45 minutes", 0.5),
         ("90837", "Psychotherapy, 60 minutes", 0.7)],
}

# Where the service happened. It drives who bills (institutional vs professional)
# and, with the payer, whether prior authorization applies.
ENCOUNTER_TYPES = {
    1: [("Emergency", 1.0)],
    2: [("Outpatient", 0.8), ("Inpatient", 0.2)],
    3: [("Outpatient", 0.65), ("Inpatient", 0.35)],
    4: [("Outpatient", 0.9), ("Inpatient", 0.1)],
    5: [("Outpatient", 1.0)],
    6: [("Outpatient", 1.0)],
    7: [("Inpatient", 0.6), ("Outpatient", 0.4)],
    8: [("Office", 0.92), ("Telehealth", 0.08)],
    9: [("Inpatient", 0.55), ("Outpatient", 0.45)],
    10: [("Office", 0.45), ("Telehealth", 0.55)],
}

# CARC-style denial reasons: (code and text, share of the ordinary denial mix,
# root-cause category, the part of the cycle that owns it, preventable).
#
# "Preventable" is the distinction a denial-prevention programme is built on. A
# missing authorization or an unverified eligibility is a front-desk failure and
# should never have been submitted; a medical-necessity denial is a clinical
# disagreement, and a bundling denial is the payer applying its own edits. Sorting
# denials by CARC code alone cannot tell those apart, so the category is carried in
# the data and published as a dimension.
DENIAL_REASONS = [
    ("CO-16 Missing or invalid information", 0.20, "Registration & data entry", "Front end", 1),
    ("CO-97 Service bundled/included", 0.14, "Bundling & payer edits", "Back end", 0),
    ("CO-11 Diagnosis inconsistent with procedure", 0.13, "Coding & documentation", "Mid cycle", 1),
    ("CO-50 Not medically necessary", 0.12, "Medical necessity", "Mid cycle", 0),
    ("CO-45 Exceeds fee schedule", 0.10, "Bundling & payer edits", "Back end", 0),
    ("CO-29 Timely filing limit expired", 0.09, "Timely filing", "Back end", 1),
    ("CO-18 Duplicate claim or service", 0.08, "Registration & data entry", "Front end", 1),
    ("CO-4 Modifier missing or inconsistent", 0.07, "Coding & documentation", "Mid cycle", 1),
    ("PR-1 Deductible amount", 0.07, "Patient responsibility", "Patient", 0),
]
# The two denials that are caused rather than drawn: they happen when the front end
# failed to do something specific, which is what makes them preventable by name.
AUTH_DENIAL = ("CO-197 Precertification/authorization absent", "Authorization", "Front end", 1)
ELIGIBILITY_DENIAL = ("CO-27 Coverage terminated before service", "Eligibility & registration", "Front end", 1)

# Probability a denial is appealed, and probability the appeal is overturned, by
# reason. These differ enormously and the difference is the whole finding: a
# missing-information denial is a clerical fix that almost always comes back, a
# timely-filing denial is money that is gone.
APPEAL_BEHAVIOUR = {
    "CO-16 Missing or invalid information":        (0.78, 0.82),
    "CO-97 Service bundled/included":              (0.42, 0.35),
    "CO-11 Diagnosis inconsistent with procedure": (0.62, 0.66),
    "CO-50 Not medically necessary":               (0.58, 0.44),
    "CO-45 Exceeds fee schedule":                  (0.48, 0.52),
    "CO-29 Timely filing limit expired":           (0.22, 0.06),
    "CO-18 Duplicate claim or service":            (0.55, 0.74),
    "CO-4 Modifier missing or inconsistent":       (0.71, 0.79),
    "PR-1 Deductible amount":                      (0.10, 0.12),
    AUTH_DENIAL[0]:                                (0.66, 0.38),
    ELIGIBILITY_DENIAL[0]:                         (0.35, 0.24),
}

AR_BUCKETS = [(30, "0-30"), (60, "31-60"), (90, "61-90"), (120, "91-120"), (10**6, "120+")]
BUCKET_SORT = {"0-30": 1, "31-60": 2, "61-90": 3, "91-120": 4, "120+": 5}


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

# A claim must be under contract by more than this before it counts as an
# underpayment. Adjudication rounds every allowed amount a few percent either
# way; without a materiality band the variance report flags half the book.
UNDERPAYMENT_MATERIALITY = 0.05

# Payer x service-line combinations where the payer systematically pays under
# its own contracted rate. This is what contract management exists to catch,
# and it is invisible without a fee schedule to compare against.
# The three shortfalls are deliberately different shapes, because "worst" is
# ambiguous the moment a small contract is badly wrong and a large one is slightly
# wrong: Surgery is the biggest cell in the book and is 13% short, Behavioral Health
# is one of the smallest and is 20% short. A report that publishes one "worst"
# quietly means whichever the code sorted by, so this one names both.
UNDERPAYING = {
    (3, 7): 0.87,     # BCBS on Surgery              - the most dollars
    (4, 4): 0.90,     # UnitedHealthcare on Oncology
    (7, 10): 0.80,    # Humana MA on Behavioral Health - the deepest rate, on a small cell
}


# ---------------------------------------------------------------------------
# Patient responsibility
# ---------------------------------------------------------------------------

# The patient's share of the allowed amount, by payer type, and how much of that
# share is ever collected. Patient responsibility has grown into the single
# largest "payer" in a US hospital's book and it collects like none of them:
# a commercial deductible is a real receivable, and roughly half of it arrives.
PATIENT_SHARE = {
    "Medicare": (0.18, 0.62), "Medicare Advantage": (0.16, 0.58), "Commercial": (0.19, 0.55),
    "Medicaid": (0.02, 0.45), "Medicaid Managed Care": (0.02, 0.45), "Military": (0.05, 0.70),
    "Workers' Comp": (0.00, 0.80), "Self-Pay": (1.00, 0.20),
}
# Deductibles reset on 1 January, so the patient's share of the first quarter is
# larger than the rest of the year - the seasonal swing in cash that every
# revenue-cycle director recognises and no annual average shows.
DEDUCTIBLE_RESET_MONTHS = {1: 1.55, 2: 1.40, 3: 1.20}
SMALL_BALANCE = 25.0


# ---------------------------------------------------------------------------
# The shape of the two years
# ---------------------------------------------------------------------------

VOLUME_GROWTH = 0.15        # the last month runs this much hotter than the first

# Payer mix drifts toward Medicare Advantage as the commercial book erodes. MA pays
# a materially lower share of billed than commercial does, so the mix effect works
# against revenue per case even when nothing about a single contract has changed.
# Weights are (start-of-history, end-of-history) pairs.
PAYER_WEIGHT_DRIFT = [
    # Medicare's share is deliberately flat. Its rates were cut mid-history, and a
    # payer whose share also moved would blend the two effects the bridge exists to
    # separate - so the fee cut arrives as rate, and the drift to Medicare Advantage
    # arrives as mix, and each can be read on its own.
    (0.22, 0.22),   # Medicare
    (0.12, 0.10),   # Medicaid
    (0.16, 0.115),  # Blue Cross Blue Shield    <- losing share
    (0.14, 0.105),  # UnitedHealthcare          <- losing share
    (0.10, 0.08),   # Aetna
    (0.08, 0.07),   # Cigna
    (0.06, 0.16),   # Humana Medicare Advantage <- taking it
    (0.04, 0.04),   # Self-Pay
    (0.05, 0.07),   # Molina Healthcare
    (0.02, 0.03),   # TRICARE
    (0.01, 0.01),   # State Workers' Compensation
]

# Medicare's fee schedule update. A real one lands on a date and applies to
# everything after it, which is what makes it a RATE effect rather than a mix one -
# and why a bridge that cannot separate the two is not much use in the year a payer
# cuts its rates.
# Lands on the boundary between the two mature windows the revenue bridge
# compares, so its effect is a clean rate movement rather than half of one.
FEE_SCHEDULE_CUT_DAY = 410          # days before AS_OF
FEE_SCHEDULE_CUT_PAYERS = {1: 0.94}

# Prior authorization. Required by plan type for the service lines where payers
# actually require it; the front end obtains it most of the time.
AUTH_REQUIRED_LINES = {3, 4, 5, 7}
AUTH_REQUIRED_TYPES = {"Commercial", "Medicare Advantage", "Medicaid Managed Care", "Workers' Comp"}
# Payers require authorization for the expensive end of every service line, and for
# most of the lines where the procedure is scheduled rather than urgent. A stent
# needs one wherever it is done; a routine ECG does not.
AUTH_EXPENSIVE_PROCEDURE = 1.5
AUTH_OBTAINED = 0.94
# The planted policy change: UnitedHealthcare extends its authorization requirement
# to all of Cardiology on this date, where before only the expensive procedures
# needed one. For the first quarter afterwards the front end is still
# learning the rule, so authorisations go missing and CO-197 denials spike - a real
# shift, on a date, for the control chart to find.
NEW_AUTH_RULE = {"payer_id": 4, "service_line_id": 2,
                 "from": AS_OF - timedelta(days=243), "learning_days": 90,
                 "obtained_while_learning": 0.55}

# Eligibility verification at registration. Unverified coverage is where
# terminated-coverage denials come from.
ELIGIBILITY_VERIFIED = 0.965

# The claim scrubber: edits caught before the payer ever sees the claim. A
# rejection is not a denial - it never reached adjudication - and conflating the
# two is how a "clean claim rate" ends up measuring the wrong thing.
SCRUBBER_REJECT_BEFORE = 0.092
SCRUBBER_REJECT_AFTER = 0.048
SCRUBBER_FIX_DAY = AS_OF - timedelta(days=182)      # the rule update
REJECTION_REASONS = [
    ("Subscriber ID not on file", 0.27),
    ("Referring provider NPI missing", 0.22),
    ("Diagnosis code invalid for date of service", 0.20),
    ("Service date after statement date", 0.17),
    ("Duplicate control number", 0.14),
]


def year_position(submitted_date):
    """0.0 at the start of the generated history, 1.0 at the snapshot."""
    return 1.0 - (AS_OF - submitted_date).days / HISTORY_DAYS


def draw_submitted_date():
    """A submission date, with more of them later in the history."""
    # Two uniforms and a keep-the-later-one bias give a clean linear ramp without
    # pulling in a distribution the rest of this file does not use.
    a = random.randint(1, HISTORY_DAYS)
    if random.random() < VOLUME_GROWTH:
        a = min(a, random.randint(1, HISTORY_DAYS))
    return AS_OF - timedelta(days=a)


def draw_payer(submitted_date):
    t = year_position(submitted_date)
    weights = [s + (e - s) * t for s, e in PAYER_WEIGHT_DRIFT]
    return random.choices(PAYERS, weights=weights)[0]


def weighted(options):
    """Pick from [(value, weight), ...]."""
    values = [v for v, _ in options]
    return random.choices(values, weights=[w for _, w in options])[0]


def contracted_rate(payer_id, contract_factor, service_line_id):
    """Expected allowed-to-billed rate for this payer and service line."""
    rate = contract_factor * CONTRACT_MULTIPLIER[service_line_id]
    return round(min(rate, 1.0), 4)


def charge_lag(service_line_id):
    mean, sd = CODING_LAG[service_line_id]
    return max(0, int(round(random.gauss(mean, sd))))


def pick_denial_reason():
    r, acc = random.random(), 0.0
    for reason, w, category, stage, preventable in DENIAL_REASONS:
        acc += w
        if r < acc:
            return reason, category, stage, preventable
    reason, _, category, stage, preventable = DENIAL_REASONS[-1]
    return reason, category, stage, preventable


def pick_rejection_reason():
    r, acc = random.random(), 0.0
    for reason, w in REJECTION_REASONS:
        acc += w
        if r < acc:
            return reason
    return REJECTION_REASONS[-1][0]


def ar_bucket(days):
    for limit, name in AR_BUCKETS:
        if days <= limit:
            return name
    return "120+"


def authorization(payer, service_line_id, service_date, charge_weight):
    """Was an authorization required for this claim, and was one obtained?"""
    payer_id, _, payer_type, *_ = payer
    rule = NEW_AUTH_RULE
    new_rule_applies = (payer_id == rule["payer_id"] and service_line_id == rule["service_line_id"]
                        and service_date >= rule["from"])
    standard = payer_type in AUTH_REQUIRED_TYPES and (
        service_line_id in AUTH_REQUIRED_LINES or charge_weight >= AUTH_EXPENSIVE_PROCEDURE)
    required = new_rule_applies or standard
    if not required:
        return 0, 0
    learning = new_rule_applies and (service_date - rule["from"]).days < rule["learning_days"]
    obtained = random.random() < (rule["obtained_while_learning"] if learning else AUTH_OBTAINED)
    return 1, int(obtained)


def patient_split(payer_type, allowed, service_month):
    """Split an allowed amount into the payer's share and the patient's, and say
    how much of the patient's share is ever collected."""
    share, collection = PATIENT_SHARE[payer_type]
    share *= DEDUCTIBLE_RESET_MONTHS.get(service_month, 1.0) if share < 1.0 else 1.0
    share = min(share, 1.0)
    responsibility = round(allowed * share, 2)
    collected = round(responsibility * min(1.0, max(0.0, random.gauss(collection, collection * 0.25))), 2)
    return responsibility, collected


def write_off_category(status, responsibility, collected, payer_type):
    """What the uncollected remainder is called on the books. The name matters:
    charity care and bad debt are reported differently and reserved differently."""
    if status == "Denied":
        return "Denial write-off"
    remainder = responsibility - collected
    if remainder <= 0.01:
        return ""
    if remainder < SMALL_BALANCE:
        return "Small balance"
    if payer_type == "Self-Pay" and random.random() < 0.35:
        return "Charity care"
    return "Patient bad debt"


def main():
    providers = []
    for pid in range(1, 41):
        sl = SERVICE_LINES[(pid - 1) % len(SERVICE_LINES)]
        providers.append({
            "provider_id": pid,
            "provider_name": f"Dr. {FIRST[pid - 1]} {LAST[pid - 1]}",
            "npi": f"1{700000000 + pid * 37:09d}",     # NPI-shaped, synthetic
            "specialty": sl[1],
            "facility": random.choice(FACILITIES),
        })

    claims = []
    for i in range(1, N_CLAIMS + 1):
        submitted_date = draw_submitted_date()
        payer = draw_payer(submitted_date)
        sl = random.choices(SERVICE_LINES, weights=SL_WEIGHTS)[0]
        provider = random.choice(providers)
        payer_id, _, payer_type, contract, denial_rate, lag_mean, collect_mean = payer

        # Anchor to the snapshot: a claim can only be on the books if it was
        # submitted on or before the as-of date. Generating the submission date
        # first (then working back to the service date) guarantees AR age >= 1 day -
        # a snapshot must never contain a claim submitted in its future. Charge lag
        # is service-line driven, not uniform noise: it is the DNFB half of
        # days-in-AR and the half the hospital owns.
        lag_days = charge_lag(sl[0])
        service_date = submitted_date - timedelta(days=lag_days)
        encounter_type = weighted(ENCOUNTER_TYPES[sl[0]])
        cpt_code, procedure, charge_weight = weighted([(p, p[2]) for p in PROCEDURES[sl[0]]])
        submitted = round(random.lognormvariate(sl[2], 0.45) * charge_weight + 40, 2)

        # The claim scrubber runs before the payer sees anything. A rejected claim is
        # corrected and resubmitted, which costs days and a touch but is not a denial.
        reject_rate = SCRUBBER_REJECT_BEFORE if submitted_date < SCRUBBER_FIX_DAY else SCRUBBER_REJECT_AFTER
        rejected = random.random() < reject_rate
        rejection_reason = pick_rejection_reason() if rejected else ""
        days_to_correct = max(1, int(round(random.gauss(3.0, 1.5)))) if rejected else 0
        first_submission = submitted_date - timedelta(days=days_to_correct)

        auth_required, auth_obtained = authorization(payer, sl[0], service_date, charge_weight)
        eligibility_verified = int(random.random() < ELIGIBILITY_VERIFIED)

        rate = contracted_rate(payer_id, contract, sl[0])
        cut_from = AS_OF - timedelta(days=FEE_SCHEDULE_CUT_DAY)
        if payer_id in FEE_SCHEDULE_CUT_PAYERS and submitted_date >= cut_from:
            rate = round(rate * FEE_SCHEDULE_CUT_PAYERS[payer_id], 4)
        expected_allowed = round(submitted * rate, 2)

        # Recent submissions are disproportionately still pending, and the tail thins
        # fast: a claim that is still open two years after submission is a write-off,
        # not a receivable. A flat old-claim probability over a two-year history
        # accumulates an AR book that is mostly ancient, which no hospital's is.
        days_since_submit = (AS_OF - submitted_date).days
        pending_prob = (0.85 if days_since_submit < 20 else
                        0.34 if days_since_submit < 45 else
                        0.12 if days_since_submit < 90 else
                        0.035 if days_since_submit < 180 else 0.004)

        appeal_outcome, recovered, appeal_days = "", "", ""
        underpaid = ""
        denial_category, denial_stage, preventable = "", "", ""
        responsibility = patient_paid = payer_paid = write_off = ""
        patient_write_off = ""
        write_off_kind = ""

        if random.random() < pending_prob:
            status, allowed, paid = "Pending", "", ""
            adjudicated_date, days_adj, reason, resubmitted = "", "", "", 0
            age = days_since_submit
            bucket, bucket_sort = ar_bucket(age), BUCKET_SORT[ar_bucket(age)]
            # An open claim still consumes follow-up: the older it is, the more.
            touches = 1 + int(age // 45) + (1 if rejected else 0)
        else:
            lag = max(3, int(random.gauss(lag_mean, lag_mean * 0.3)))
            adj = submitted_date + timedelta(days=lag)
            adjudicated_date = min(adj, AS_OF - timedelta(days=1)).isoformat()
            days_adj = lag
            age, bucket, bucket_sort = "", "", ""

            # A missing authorization or an unverified eligibility does not merely
            # raise the odds of a denial - it causes a specific one. Everything else
            # is drawn from the payer's ordinary denial rate.
            missing_auth = auth_required and not auth_obtained
            coverage_gap = not eligibility_verified and payer_type != "Self-Pay"
            denied = (missing_auth and random.random() < 0.82) \
                or (coverage_gap and random.random() < 0.55) \
                or random.random() < denial_rate

            if denied:
                status = "Denied"
                allowed, paid = 0.0, 0.0
                if missing_auth and random.random() < 0.9:
                    reason, denial_category, denial_stage, preventable = AUTH_DENIAL[0], *AUTH_DENIAL[1:]
                elif coverage_gap and random.random() < 0.8:
                    reason, denial_category, denial_stage, preventable = ELIGIBILITY_DENIAL[0], *ELIGIBILITY_DENIAL[1:]
                else:
                    reason, denial_category, denial_stage, preventable = pick_denial_reason()
                appeal_p, overturn_p = APPEAL_BEHAVIOUR[reason]
                resubmitted = 1 if random.random() < appeal_p else 0
                if resubmitted:
                    appeal_days = max(7, int(random.gauss(38, 14)))
                    if random.random() < overturn_p:
                        appeal_outcome = "Overturned"
                        # An overturned denial pays at contract, less the collection
                        # leakage every paid claim carries.
                        collect = min(1.0, max(0.03, random.gauss(collect_mean, collect_mean * 0.08)))
                        recovered = round(expected_allowed * collect, 2)
                    else:
                        appeal_outcome = "Upheld"
                        recovered = 0.0
                else:
                    appeal_outcome = "Not appealed"
                    recovered = 0.0
                write_off = round(expected_allowed - float(recovered or 0.0), 2)
                patient_write_off = 0.0
                write_off_kind = "Denial write-off"
                touches = 3 + (4 if resubmitted else 0) + (2 if missing_auth else 0) + random.randint(0, 3)
            else:
                status = "Paid"
                # Paid at the contracted rate, with the usual adjudication noise -
                # except on the payer x service-line combinations that systematically
                # pay under contract.
                shortfall = UNDERPAYING.get((payer_id, sl[0]), 1.0)
                allowed = round(expected_allowed * shortfall * random.uniform(0.97, 1.03), 2)
                allowed = min(allowed, submitted)

                # The allowed amount splits into the payer's share and the patient's,
                # and the two collect nothing like each other.
                responsibility, patient_paid = patient_split(payer_type, allowed, service_date.month)
                payer_share = round(allowed - responsibility, 2)
                collect = min(1.0, max(0.5, random.gauss(collect_mean, collect_mean * 0.02)))
                payer_paid = round(payer_share * collect, 2)
                paid = round(payer_paid + patient_paid, 2)
                write_off = round(allowed - paid, 2)
                patient_write_off = round(responsibility - patient_paid, 2)
                write_off_kind = write_off_category(status, responsibility, patient_paid, payer_type)
                reason, resubmitted = "", 0
                appeal_outcome, recovered = "", ""

                # Materiality: adjudication noise moves the allowed amount a few
                # percent either way on every claim, so a variance report that flags
                # every dollar below contract flags half the book. Only a shortfall
                # past the noise band is an underpayment.
                shortfall_amt = expected_allowed - allowed
                underpaid = (round(shortfall_amt, 2)
                             if shortfall_amt > expected_allowed * UNDERPAYMENT_MATERIALITY else 0.0)
                touches = (1 if underpaid < 1.0 else 2) + (1 if rejected else 0) \
                    + (1 if responsibility > SMALL_BALANCE else 0)

        claims.append({
            "claim_id": f"CLM-{i:06d}",
            "service_date": service_date.isoformat(),
            "first_submission_date": first_submission.isoformat(),
            "submitted_date": submitted_date.isoformat(),
            "adjudicated_date": adjudicated_date,
            "payer_id": payer_id,
            "provider_id": provider["provider_id"],
            "service_line_id": sl[0],
            "cpt_code": cpt_code,
            "procedure": procedure,
            "encounter_type": encounter_type,
            "facility": provider["facility"],
            "status": status,
            "submitted_amount": submitted,
            "allowed_amount": allowed,
            "paid_amount": paid,
            "payer_paid_amount": payer_paid,
            "patient_responsibility": responsibility,
            "patient_paid_amount": patient_paid,
            "write_off_amount": write_off,
            "patient_write_off": patient_write_off,
            "write_off_category": write_off_kind,
            "denial_reason": reason,
            "denial_category": denial_category,
            "denial_stage": denial_stage,
            "denial_preventable": preventable,
            "resubmitted": resubmitted,
            "days_to_adjudicate": days_adj,
            "ar_age_days": age,
            "ar_bucket": bucket,
            "ar_bucket_sort": bucket_sort,
            "charge_lag_days": lag_days,
            "clearinghouse_rejected": int(rejected),
            "rejection_reason": rejection_reason,
            "days_to_correct": days_to_correct,
            "prior_auth_required": auth_required,
            "prior_auth_obtained": auth_obtained,
            "eligibility_verified": eligibility_verified,
            "contracted_rate": rate,
            "expected_allowed": expected_allowed,
            "underpaid_amount": underpaid,
            "appeal_outcome": appeal_outcome,
            "appeal_days": appeal_days,
            "recovered_amount": recovered,
            "follow_up_touches": touches,
        })

    # The fee schedule, published as a dimension so the variance report reads the
    # contract rather than re-deriving it - which is how a contract report ends up
    # agreeing with itself no matter what the payer did.
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
                "update_effective": (AS_OF - timedelta(days=FEE_SCHEDULE_CUT_DAY)).isoformat()
                                    if payer_id in FEE_SCHEDULE_CUT_PAYERS else "",
            })

    # Denial reasons as a dimension: the root cause and who owns it, so the report
    # groups denials by the thing that can be fixed rather than by the code.
    reason_rows = []
    for reason, _w, category, stage, preventable in DENIAL_REASONS:
        reason_rows.append({"denial_reason": reason, "denial_category": category,
                            "denial_stage": stage, "preventable": preventable})
    for reason, category, stage, preventable in (AUTH_DENIAL, ELIGIBILITY_DENIAL):
        reason_rows.append({"denial_reason": reason, "denial_category": category,
                            "denial_stage": stage, "preventable": preventable})

    procedures = [{"cpt_code": code, "procedure": description, "service_line_id": sl_id,
                   "service_line": dict((s[0], s[1]) for s in SERVICE_LINES)[sl_id],
                   "charge_weight": weight}
                  for sl_id, items in PROCEDURES.items() for code, description, weight in items]

    datasets = [
        ("dim_payer_contract.csv", contracts),
        ("dim_payer.csv", [{"payer_id": p[0], "payer_name": p[1], "payer_type": p[2]} for p in PAYERS]),
        ("dim_provider.csv", providers),
        ("dim_service_line.csv", [{"service_line_id": s[0], "service_line": s[1]} for s in SERVICE_LINES]),
        ("dim_denial_reason.csv", reason_rows),
        ("dim_procedure.csv", procedures),
        ("fact_claims.csv", claims),
    ]
    for fname, rows in datasets:
        with open(OUT / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows):6d} rows -> {fname}")


if __name__ == "__main__":
    main()
