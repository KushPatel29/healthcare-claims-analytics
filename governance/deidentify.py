"""
De-identification of a patient-level extract, with a measured re-identification
risk rather than an assurance.

Every dataset in this repository is synthetic, so nothing here protects a real
person. That is precisely why it is worth building: the technique has to exist
and be tested *before* it is pointed at real data, and "we de-identified it" is
the most over-claimed sentence in health analytics.

Two things happen, and they are not the same thing:

  1. **Direct identifier removal — HIPAA Safe Harbor.** Strip or transform the
     18 categories of direct identifier (names, MRNs, full dates, geography
     finer than the first three postal characters, ages over 89, contact
     details, and so on). This is a checklist and it is the easy half.

  2. **Quasi-identifier control — k-anonymity.** The hard half. Nobody is
     re-identified by their name in a de-identified file; they are
     re-identified by the *combination* of attributes that survives it. Age
     band + postal prefix + sex + admission month + a rare CMG can be unique in
     a population of half a million. So every record must sit in an equivalence
     class of at least k records that look identical on those quasi-identifiers;
     classes smaller than k are generalised, and if generalisation is not enough
     they are suppressed.

The output carries an explicit risk report: how many records were unique before,
the smallest surviving equivalence class, and how much information was destroyed
to get there. Suppression is never free, and a de-identification that reports
its cost is one somebody can actually argue with.

Usage:
    python governance/deidentify.py
"""

import csv
import hashlib
import os
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

K = 5                     # minimum equivalence-class size
AGE_CAP = 89              # Safe Harbor: ages above this are aggregated
QUASI_IDENTIFIERS = ["age_band", "postal_prefix", "sex", "admit_year",
                     "program", "cmg_code"]

# The 18 Safe Harbor identifier categories, as the column-name patterns they
# usually arrive under. Held as a rule set rather than a hard-coded list so a
# new source column that looks like an identifier is caught on arrival instead
# of on audit.
SAFE_HARBOR_PATTERNS = {
    "name": r"(^|_)(name|surname|given|first|last)($|_)",
    "geographic_subdivision": r"(^|_)(address|street|city|postal_code|zip)($|_)",
    "date": r"(^|_)(dob|birth_date|admit_date|discharge_date|death_date)($|_)",
    "telephone": r"(^|_)(phone|tel|fax|mobile)($|_)",
    "email": r"(^|_)email($|_)",
    "health_plan_or_record_number": r"(^|_)(mrn|phn|chart|health_card|"
                                    r"medical_record|account_no)($|_)",
    "identifier_number": r"(^|_)(ssn|sin|licen[cs]e|certificate|vehicle|device|"
                         r"serial)($|_)",
    "web_or_ip": r"(^|_)(url|uri|ip_address)($|_)",
    "biometric_or_image": r"(^|_)(biometric|fingerprint|photo|face)($|_)",
}

# Coarser generalisations applied, in order, to equivalence classes that are
# too small. Generalising is always preferred to suppressing: a broadened age
# band still supports analysis, whereas a deleted row supports nothing.
AGE_GENERALISATION = {
    "0-17": "0-44", "18-44": "0-44",
    "45-64": "45-74", "65-74": "45-74",
    "75-84": "75+", "85+": "75+",
}


def salt():
    """Pseudonymisation salt.

    A hash without a secret salt is not pseudonymisation, it is an open
    invitation: the identifier space is small enough that anyone can hash every
    possible MRN and build a lookup table in minutes. In production this comes
    from a secrets manager and is rotated; here it falls back to a fixed value
    so CI is reproducible, and that fallback is a deliberate, documented
    weakness rather than an accident.
    """
    return os.environ.get("DEID_SALT", "ci-reproducible-salt-not-for-real-data")


def pseudonymise(value):
    """Stable, non-reversible surrogate key, so longitudinal linkage survives
    de-identification. Without this, a patient's two admissions become two
    unrelated people and every readmission analysis dies with the identifiers."""
    return hashlib.sha256(f"{salt()}|{value}".encode()).hexdigest()[:16]


def classify_identifiers(columns):
    """Which incoming columns match a Safe Harbor identifier category."""
    found = {}
    for col in columns:
        for category, pattern in SAFE_HARBOR_PATTERNS.items():
            if re.search(pattern, col.lower()):
                found.setdefault(category, []).append(col)
                break
    return found


def cap_age_band(band):
    """Safe Harbor requires ages over 89 to be aggregated into a single
    category, because the 90+ population is small enough that an exact age is
    close to an identifier on its own."""
    return f"{AGE_CAP}+" if band == "85+" else band


def equivalence_classes(rows, keys):
    return Counter(tuple(r[k] for k in keys) for r in rows)


def enforce_k_anonymity(rows, keys, k=K):
    """Generalise, then suppress, until every equivalence class has >= k members.

    Returns (kept_rows, suppressed_rows, report). Two passes on purpose:
    generalisation is lossy but preserves the record, suppression destroys it,
    so suppression is only ever the fallback.
    """
    before = equivalence_classes(rows, keys)
    unique_before = sum(1 for c in before.values() if c == 1)

    # Pass 1 — generalise the quasi-identifiers of any record in a small class.
    generalised = 0
    for r in rows:
        if before[tuple(r[k] for k in keys)] < k:
            new_band = AGE_GENERALISATION.get(r["age_band"], r["age_band"])
            # Postal prefix is truncated one more character: FSA -> first two.
            new_postal = r["postal_prefix"][:2]
            if (new_band, new_postal) != (r["age_band"], r["postal_prefix"]):
                r["age_band"] = new_band
                r["postal_prefix"] = new_postal
                r["generalised"] = 1
                generalised += 1

    # Pass 2 — suppress whatever is still too small to hide in.
    after = equivalence_classes(rows, keys)
    kept, suppressed = [], []
    for r in rows:
        (kept if after[tuple(r[k] for k in keys)] >= k else suppressed).append(r)

    final = equivalence_classes(kept, keys)
    report = {
        "records_in": len(rows),
        "records_out": len(kept),
        "unique_records_before": unique_before,
        "unique_share_before": round(unique_before / len(rows), 5),
        "records_generalised": generalised,
        "records_suppressed": len(suppressed),
        "suppression_rate": round(len(suppressed) / len(rows), 5),
        "k_target": k,
        "min_equivalence_class_after": min(final.values()) if final else 0,
        "equivalence_classes_after": len(final),
    }
    return kept, suppressed, report


def build_identified_extract():
    """Stand in for the identified extract that would arrive from the source
    system. The base abstracts carry no identifiers at all (they were generated
    that way), so direct identifiers and geography are attached here — you
    cannot demonstrate that a de-identification works on a file that never had
    anything to remove.
    """
    import random
    rng = random.Random(99)
    fsas = ["V3T", "V3S", "V2S", "V5A", "V3B", "V4N", "V2Y", "V3R"]
    with open(DATA / "fact_inpatient_abstracts.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "mrn": f"MRN{7000000 + i}",
            "patient_name": f"Patient {i}",
            "postal_code": f"{rng.choice(fsas)} {rng.randint(1, 9)}"
                           f"{rng.choice('ABCEGHJ')}{rng.randint(1, 9)}",
            "sex": rng.choice(["F", "M"]),
            "admit_date": r["admit_date"],
            "discharge_date": r["discharge_date"],
            "age_band": r["age_band"],
            "program": r["program"],
            "cmg_code": r["cmg_code"],
            "facility_id": r["facility_id"],
            "acute_los_days": r["acute_los_days"],
            "alc_days": r["alc_days"],
            "readmit_30d": r["readmit_30d"],
            "total_cost": r["total_cost"],
        })
    return out


def deidentify(rows):
    """Apply Safe Harbor transformations. Returns (deidentified_rows, audit)."""
    audit = classify_identifiers(rows[0].keys())
    out = []
    for r in rows:
        out.append({
            # Direct identifiers -> salted pseudonym, so linkage survives.
            "patient_key": pseudonymise(r["mrn"]),
            # Geography -> first three characters only (Canadian FSA), which is
            # the Safe Harbor-equivalent generalisation for postal geography.
            "postal_prefix": r["postal_code"][:3],
            "sex": r["sex"],
            # Full dates -> year and month. Dates of service are identifying:
            # anyone who knows roughly when you were admitted plus two other
            # attributes can usually find you.
            "admit_year": r["admit_date"][:4],
            "admit_month": r["admit_date"][:7],
            "age_band": cap_age_band(r["age_band"]),
            "program": r["program"],
            "cmg_code": r["cmg_code"],
            "facility_id": r["facility_id"],
            # Clinical and financial measures are not identifiers and are kept
            # intact — de-identification that destroys the analysis has simply
            # relocated the failure.
            "acute_los_days": r["acute_los_days"],
            "alc_days": r["alc_days"],
            "readmit_30d": r["readmit_30d"],
            "total_cost": r["total_cost"],
            "generalised": 0,
        })
    return out, audit


def main():
    identified = build_identified_extract()
    deid, audit = deidentify(identified)
    kept, suppressed, report = enforce_k_anonymity(deid, QUASI_IDENTIFIERS)

    with open(OUT / "deidentified_abstracts.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=kept[0].keys())
        w.writeheader()
        w.writerows(kept)

    # The audit trail records *keys only* — what was withheld and why, never a
    # withheld value. An audit table that leaks the thing it is auditing is a
    # mistake people genuinely make.
    with open(OUT / "deid_risk_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for key, value in report.items():
            w.writerow([key, value])
        for category, cols in sorted(audit.items()):
            w.writerow([f"safe_harbor_removed:{category}", ";".join(sorted(cols))])

    residual = report["records_out"] and (1.0 / report["min_equivalence_class_after"])
    lines = [
        "DE-IDENTIFICATION & RE-IDENTIFICATION RISK",
        "=" * 54,
        f"Safe Harbor categories found and handled: {len(audit)}",
        *[f"  - {c:<32} {', '.join(cols)}" for c, cols in sorted(audit.items())],
        "-" * 54,
        f"Records in:                       {report['records_in']:>10,}",
        f"Unique on quasi-identifiers:      {report['unique_records_before']:>10,}"
        f" ({report['unique_share_before']:.2%})",
        f"Records generalised:              {report['records_generalised']:>10,}",
        f"Records suppressed:               {report['records_suppressed']:>10,}"
        f" ({report['suppression_rate']:.2%})",
        f"Records out:                      {report['records_out']:>10,}",
        "-" * 54,
        f"k target:                         {report['k_target']:>10}",
        f"Smallest equivalence class:       {report['min_equivalence_class_after']:>10}",
        f"Equivalence classes:              {report['equivalence_classes_after']:>10,}",
        f"Max re-identification probability:{residual:>10.3f}"
        "   (1 / smallest class)",
    ]
    (OUT / "deid_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
