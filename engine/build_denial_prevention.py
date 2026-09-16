"""
Denial prevention: the denials that should never have been submitted.

`build_rcm_metrics.py` measures the claim as the payer left it, and
`build_revenue_integrity.py` measures what can still be recovered from it. Both
start after the claim has gone out of the door. This measures what happened
before it did, which is where a denial is cheapest to stop:

1. ROOT CAUSE, not reason code. A CARC code says what the payer objected to; it
   does not say who could have prevented it. A missing authorization and an
   unverified eligibility are front-desk failures; a medical-necessity denial is a
   clinical disagreement and a bundling denial is the payer's own edit. Ranking
   denials by code sends the team at whichever code is loudest; ranking them by
   preventable dollars sends them at the ones that can actually be stopped.

2. THE FUNNEL. A claim can fail three different ways - rejected by the clearing
   house before the payer sees it, denied after adjudication, or paid short. They
   are usually reported as one "clean claim rate", which measures none of them.
   The funnel keeps them separate: created -> accepted first time -> adjudicated ->
   paid first pass -> recovered on appeal.

3. A DATE, NOT A DRIFT. When a payer changes a rule, the denial rate moves on the
   day it lands. A month-over-month table reports that as three bad months in a row
   and argues about each one. A control chart on the affected payer x service line
   names the month it started and holds the limits it was set with - the same
   Laney p-prime chart the activity side uses, because the maths does not care
   whether the proportion is a readmission or a denial.

4. THE PATIENT AS A PAYER. Patient responsibility is now the largest single
   "payer" in the book and it collects like none of them. Tracking it beside the
   insured book is the difference between a net collection rate that means
   something and one that quietly averages two different businesses.

No third-party dependencies, by design: this repo runs on a bare Python.

Outputs (output/):
    denial_root_cause.csv     month x category: denials, dollars, preventable
    payer_scorecard.csv       one row per payer, every measure a payer meeting asks for
    patient_collections.csv   month x payer type: responsibility, collected, rate
    claim_funnel.csv          created -> accepted -> adjudicated -> paid -> recovered
    auth_denial_pchart.csv    the authorization denial rate, charted with its limits
    denial_prevention.json    the headline figures
    denial_prevention.txt     the same, readable

Usage:
    python engine/build_denial_prevention.py
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spc import p_chart, western_electric

DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# The control chart needs a phase I baseline: a stretch of months that describes
# the process before whatever is being watched for. Six months is the shortest
# stretch that gives a usable centre line here.
BASELINE_MONTHS = 6
# The cell the authorization rule landed on. Charting the whole book would bury a
# rule change on one payer's cardiology in everyone else's ordinary variation.
WATCHED_CELL = {"payer_name": "UnitedHealthcare", "service_line": "Cardiology"}


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def write(name, rows, fieldnames=None):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def month_of(claim, field="submitted_date"):
    return claim[field][:7]


# ---------------------------------------------------------------------------
# 1. Root cause
# ---------------------------------------------------------------------------

def root_cause(claims):
    """Denials by month and root-cause category, with the preventable share."""
    cells = defaultdict(lambda: {"denials": 0, "denied_billed": 0.0, "preventable": 0,
                                 "preventable_billed": 0.0, "recovered": 0.0})
    for claim in claims:
        if claim["status"] != "Denied":
            continue
        cell = cells[(month_of(claim), claim["denial_category"], claim["denial_stage"])]
        cell["denials"] += 1
        cell["denied_billed"] += num(claim["submitted_amount"])
        cell["recovered"] += num(claim["recovered_amount"])
        if claim["denial_preventable"] == "1":
            cell["preventable"] += 1
            # What the claim would have been worth had it been paid: the size of
            # the prize for stopping it, not the billed charge nobody ever collects.
            cell["preventable_billed"] += num(claim["expected_allowed"])
    rows = []
    for (month, category, stage), cell in sorted(cells.items()):
        rows.append({
            "month": month, "denial_category": category, "denial_stage": stage,
            "denials": cell["denials"], "denied_billed": round(cell["denied_billed"], 2),
            "preventable_denials": cell["preventable"],
            "preventable_contract_value": round(cell["preventable_billed"], 2),
            "recovered": round(cell["recovered"], 2),
        })
    return rows


# ---------------------------------------------------------------------------
# 2. The funnel
# ---------------------------------------------------------------------------

def funnel(claims):
    """Where claims and dollars leave the cycle, stage by stage.

    Every stage is a subset of the one above it, so the drop between two rows is
    the loss at that step and nothing else. That is not automatic: a claim the
    clearing house rejected is corrected and resubmitted, so it still reaches
    adjudication, and a funnel that counts "accepted first time" and "adjudicated"
    from the whole book shows more claims adjudicated than accepted. Recovery on
    appeal is the one thing that moves the other way, so it is reported as a
    recovery rather than smuggled into the ladder.

    Dollars are the contracted value at risk - the expected allowed amount -
    because billed charges overstate every loss by the contractual adjustment
    nobody was ever going to collect.
    """
    created = claims
    accepted = [c for c in created if c["clearinghouse_rejected"] == "0"]
    adjudicated = [c for c in accepted if c["status"] != "Pending"]
    paid_first = [c for c in adjudicated if c["status"] == "Paid"]
    denied = [c for c in adjudicated if c["status"] == "Denied"]
    overturned = [c for c in denied if c["appeal_outcome"] == "Overturned"]

    def value(rows, field="expected_allowed"):
        return round(sum(num(r[field]) for r in rows), 2)

    stages = [
        ("1. Claims created", created, "ladder", "Every claim the hospital billed"),
        ("2. Accepted by the clearing house first time", accepted, "ladder",
         "The rest were rejected on a scrubber edit, corrected and resubmitted"),
        ("3. Adjudicated by the payer", adjudicated, "ladder", "The rest are still open AR"),
        ("4. Paid on first pass", paid_first, "ladder", "Adjudicated without a denial"),
        ("5. Recovered on appeal", overturned, "recovery",
         "Denied, appealed and overturned - money that came back after the ladder"),
    ]
    rows = []
    for stage, records, kind, note in stages:
        rows.append({
            "stage": stage, "kind": kind, "claims": len(records),
            "share_of_created": round(len(records) / len(created), 4),
            "contract_value": value(records), "note": note,
        })
    return rows


# ---------------------------------------------------------------------------
# 3. The payer scorecard
# ---------------------------------------------------------------------------

def payer_scorecard(claims, payer_name, payer_type):
    """One row per payer: the measures a payer meeting actually argues about."""
    cells = defaultdict(lambda: {"claims": 0, "billed": 0.0, "adjudicated": 0, "denied": 0,
                                 "preventable": 0, "auth_denials": 0, "rejected": 0, "lag": 0.0,
                                 "lag_n": 0, "allowed": 0.0, "paid": 0.0, "patient": 0.0,
                                 "patient_paid": 0.0, "underpaid": 0.0, "open_ar": 0.0,
                                 "first_pass": 0, "touches": 0})
    for claim in claims:
        cell = cells[claim["payer_id"]]
        cell["claims"] += 1
        cell["billed"] += num(claim["submitted_amount"])
        cell["touches"] += int(num(claim["follow_up_touches"]))
        cell["rejected"] += int(claim["clearinghouse_rejected"] == "1")
        if claim["status"] == "Pending":
            cell["open_ar"] += num(claim["submitted_amount"])
            continue
        cell["adjudicated"] += 1
        cell["lag"] += num(claim["days_to_adjudicate"])
        cell["lag_n"] += 1
        if claim["status"] == "Denied":
            cell["denied"] += 1
            cell["preventable"] += int(claim["denial_preventable"] == "1")
            cell["auth_denials"] += int(claim["denial_category"] == "Authorization")
        else:
            cell["first_pass"] += 1
            cell["allowed"] += num(claim["allowed_amount"])
            cell["paid"] += num(claim["paid_amount"])
            cell["patient"] += num(claim["patient_responsibility"])
            cell["patient_paid"] += num(claim["patient_paid_amount"])
            cell["underpaid"] += num(claim["underpaid_amount"])

    rows = []
    for payer_id, cell in cells.items():
        adjudicated = max(cell["adjudicated"], 1)
        rows.append({
            "payer_name": payer_name[payer_id], "payer_type": payer_type[payer_id],
            "claims": cell["claims"], "billed": round(cell["billed"], 2),
            "denial_rate": round(cell["denied"] / adjudicated, 4),
            "preventable_denial_rate": round(cell["preventable"] / adjudicated, 4),
            "authorization_denial_rate": round(cell["auth_denials"] / adjudicated, 4),
            "clean_claim_rate": round(1 - cell["rejected"] / max(cell["claims"], 1), 4),
            "first_pass_rate": round(cell["first_pass"] / adjudicated, 4),
            "avg_days_to_pay": round(cell["lag"] / max(cell["lag_n"], 1), 1),
            "net_collection_rate": round(cell["paid"] / cell["allowed"], 4) if cell["allowed"] else 0.0,
            "patient_share_of_allowed": round(cell["patient"] / cell["allowed"], 4) if cell["allowed"] else 0.0,
            "patient_collection_rate": round(cell["patient_paid"] / cell["patient"], 4) if cell["patient"] else 0.0,
            "underpaid_amount": round(cell["underpaid"], 2),
            "open_ar": round(cell["open_ar"], 2),
            "touches_per_claim": round(cell["touches"] / max(cell["claims"], 1), 2),
        })
    rows.sort(key=lambda r: -r["billed"])
    return rows


# ---------------------------------------------------------------------------
# 4. Patient responsibility
# ---------------------------------------------------------------------------

def patient_collections(claims, payer_type):
    """Month by payer type: what the patient owed, and what arrived.

    Deductibles reset in January, so the patient's share of the book is seasonal.
    An annual average hides both the January spike and the collection rate."""
    cells = defaultdict(lambda: {"claims": 0, "responsibility": 0.0, "collected": 0.0,
                                 "allowed": 0.0, "bad_debt": 0.0, "charity": 0.0})
    for claim in claims:
        if claim["status"] != "Paid":
            continue
        cell = cells[(month_of(claim, "service_date"), payer_type[claim["payer_id"]])]
        cell["claims"] += 1
        cell["responsibility"] += num(claim["patient_responsibility"])
        cell["collected"] += num(claim["patient_paid_amount"])
        cell["allowed"] += num(claim["allowed_amount"])
        # The patient's own unpaid remainder, not the whole write-off: the payer's
        # share of a short payment is a contractual matter, not bad debt.
        if claim["write_off_category"] == "Patient bad debt":
            cell["bad_debt"] += num(claim["patient_write_off"])
        elif claim["write_off_category"] == "Charity care":
            cell["charity"] += num(claim["patient_write_off"])
    rows = []
    for (month, ptype), cell in sorted(cells.items()):
        rows.append({
            "month": month, "payer_type": ptype, "claims": cell["claims"],
            "allowed": round(cell["allowed"], 2),
            "patient_responsibility": round(cell["responsibility"], 2),
            "patient_collected": round(cell["collected"], 2),
            "patient_collection_rate": round(cell["collected"] / cell["responsibility"], 4)
                                       if cell["responsibility"] else 0.0,
            "patient_share_of_allowed": round(cell["responsibility"] / cell["allowed"], 4)
                                        if cell["allowed"] else 0.0,
            "bad_debt": round(cell["bad_debt"], 2), "charity_care": round(cell["charity"], 2),
        })
    return rows


# ---------------------------------------------------------------------------
# 5. The rule change, on a chart
# ---------------------------------------------------------------------------

def authorization_chart(claims, payer_name, sl_name):
    """The authorization denial rate for the watched payer x service line, with
    control limits set from the months before anything changed."""
    monthly = defaultdict(lambda: {"claims": 0, "auth_denials": 0})
    for claim in claims:
        if claim["status"] == "Pending":
            continue
        if payer_name[claim["payer_id"]] != WATCHED_CELL["payer_name"]:
            continue
        if sl_name[claim["service_line_id"]] != WATCHED_CELL["service_line"]:
            continue
        cell = monthly[month_of(claim)]
        cell["claims"] += 1
        cell["auth_denials"] += int(claim["denial_category"] == "Authorization")

    points = [(month, cell["auth_denials"], cell["claims"]) for month, cell in sorted(monthly.items())]
    chart = p_chart(points, laney=True, baseline=BASELINE_MONTHS)
    signals = western_electric(chart)
    flagged = {signal["label"] for signal in signals}
    rows = []
    for point in chart:
        rows.append({
            "month": point["label"], "claims": point["denominator"],
            "authorization_denials": point["numerator"],
            "rate": round(point["value"], 4) if point["value"] is not None else "",
            "centre": round(point["centre"], 4),
            "lcl": round(point["lcl"], 4) if point["lcl"] is not None else "",
            "ucl": round(point["ucl"], 4) if point["ucl"] is not None else "",
            "signal": int(point["label"] in flagged),
        })
    return rows, signals


# ---------------------------------------------------------------------------

def build():
    claims = load("fact_claims.csv")
    payer_name = {p["payer_id"]: p["payer_name"] for p in load("dim_payer.csv")}
    payer_type = {p["payer_id"]: p["payer_type"] for p in load("dim_payer.csv")}
    sl_name = {s["service_line_id"]: s["service_line"] for s in load("dim_service_line.csv")}

    causes = root_cause(claims)
    stages = funnel(claims)
    scorecard = payer_scorecard(claims, payer_name, payer_type)
    patients = patient_collections(claims, payer_type)
    chart, signals = authorization_chart(claims, payer_name, sl_name)

    denied = [c for c in claims if c["status"] == "Denied"]
    adjudicated = [c for c in claims if c["status"] != "Pending"]
    preventable = [c for c in denied if c["denial_preventable"] == "1"]
    rejected = [c for c in claims if c["clearinghouse_rejected"] == "1"]
    paid = [c for c in claims if c["status"] == "Paid"]

    by_category = defaultdict(lambda: {"denials": 0, "value": 0.0, "preventable": 0})
    for claim in denied:
        cell = by_category[claim["denial_category"]]
        cell["denials"] += 1
        cell["value"] += num(claim["expected_allowed"])
        cell["preventable"] += int(claim["denial_preventable"] == "1")
    worst = max(by_category.items(), key=lambda kv: kv[1]["value"] if kv[1]["preventable"] else 0.0)

    # Four Western Electric rules can each fire on the same month, so counting
    # signals overstates what happened. What a reader needs is which months were
    # flagged and whether they ran together: one flagged month is ordinary
    # variation showing its face, a run of them is a process that changed.
    flagged = sorted({signal["label"] for signal in signals})
    months_charted = [row["month"] for row in chart]
    longest, run, run_start, best_start = 0, 0, "", ""
    for month in months_charted:
        if month in flagged:
            run_start = run_start or month
            run += 1
            if run > longest:
                longest, best_start = run, run_start
        else:
            run, run_start = 0, ""
    months = sorted({month_of(c) for c in claims})
    half = len(months) // 2
    early = [c for c in claims if month_of(c) < months[half]]
    late = [c for c in claims if month_of(c) >= months[half]]

    def reject_rate(rows):
        return sum(1 for r in rows if r["clearinghouse_rejected"] == "1") / max(len(rows), 1)

    summary = {
        "claims": len(claims),
        "months": len(months),
        "first_month": months[0], "last_month": months[-1],
        "denials": len(denied),
        "denial_rate": round(len(denied) / len(adjudicated), 4),
        "preventable_denials": len(preventable),
        "preventable_share": round(len(preventable) / max(len(denied), 1), 4),
        "preventable_contract_value": round(sum(num(c["expected_allowed"]) for c in preventable), 2),
        "biggest_preventable_category": worst[0],
        "biggest_preventable_value": round(worst[1]["value"], 2),
        "clean_claim_rate": round(1 - len(rejected) / len(claims), 4),
        "clean_claim_rate_first_half": round(1 - reject_rate(early), 4),
        "clean_claim_rate_second_half": round(1 - reject_rate(late), 4),
        "rejections": len(rejected),
        "authorization_denials": sum(1 for c in denied if c["denial_category"] == "Authorization"),
        "authorization_flagged_months": ", ".join(flagged),
        "authorization_first_signal": flagged[0] if flagged else "",
        "authorization_longest_run": longest,
        "authorization_run_from": best_start,
        "authorization_baseline_rate": round(chart[0]["centre"], 4),
        "authorization_peak_rate": round(max((row["rate"] for row in chart if row["rate"] != ""), default=0.0), 4),
        "authorization_peak_month": max((row for row in chart if row["rate"] != ""),
                                        key=lambda row: row["rate"])["month"],
        "watched_cell": f"{WATCHED_CELL['payer_name']} / {WATCHED_CELL['service_line']}",
        "patient_responsibility": round(sum(num(c["patient_responsibility"]) for c in paid), 2),
        "patient_collected": round(sum(num(c["patient_paid_amount"]) for c in paid), 2),
        "patient_share_of_allowed": round(
            sum(num(c["patient_responsibility"]) for c in paid) / sum(num(c["allowed_amount"]) for c in paid), 4),
        "bad_debt": round(sum(num(c["patient_write_off"]) for c in paid
                              if c["write_off_category"] == "Patient bad debt"), 2),
        "charity_care": round(sum(num(c["patient_write_off"]) for c in paid
                                  if c["write_off_category"] == "Charity care"), 2),
        "denial_write_offs": round(sum(num(c["write_off_amount"]) for c in denied), 2),
    }
    summary["patient_collection_rate"] = round(
        summary["patient_collected"] / summary["patient_responsibility"], 4)

    write("denial_root_cause.csv", causes)
    write("payer_scorecard.csv", scorecard)
    write("patient_collections.csv", patients)
    write("claim_funnel.csv", stages)
    write("auth_denial_pchart.csv", chart)
    (OUT / "denial_prevention.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary, causes, scorecard, stages, chart, signals


def main():
    s, _causes, scorecard, stages, _chart, _signals = build()
    worst_payer = max(scorecard, key=lambda r: r["preventable_denial_rate"])
    lines = [
        "DENIAL PREVENTION",
        "=" * 64,
        (f"Claims                        {s['claims']:>10,}   {s['months']} months, "
         f"{s['first_month']} to {s['last_month']}"),
        f"Denial rate                   {s['denial_rate']:>10.1%}   {s['denials']:,} denials",
        (f"Preventable                   {s['preventable_share']:>10.1%}   "
         f"{s['preventable_denials']:,} denials worth "
         f"${s['preventable_contract_value']:,.0f} at contract"),
        (f"Biggest preventable cause     {s['biggest_preventable_category']}"
         f"  ${s['biggest_preventable_value']:,.0f}"),
        (f"Worst payer for prevention    {worst_payer['payer_name']}"
         f"  {worst_payer['preventable_denial_rate']:.1%} of its adjudicated claims"),
        "-" * 64,
        "THE FUNNEL",
    ]
    for stage in stages:
        lines.append(f"  {stage['stage']:<46s} {stage['claims']:>7,}  {stage['share_of_created']:>6.1%}")
    lines += [
        "-" * 64,
        "CLEAN CLAIM RATE (accepted by the clearing house first time)",
        (f"  overall {s['clean_claim_rate']:.1%}   "
         f"first half {s['clean_claim_rate_first_half']:.1%}"
         f"   second half {s['clean_claim_rate_second_half']:.1%}"),
        "-" * 64,
        f"AUTHORIZATION DENIALS ({s['watched_cell']})",
        (f"  {s['authorization_denials']:,} across the book. On the watched cell the rate runs at "
         f"{s['authorization_baseline_rate']:.1%}, then holds above its limits for "
         f"{s['authorization_longest_run']} months from {s['authorization_run_from']}, peaking at "
         f"{s['authorization_peak_rate']:.0%} in {s['authorization_peak_month']}"),
        f"  flagged months: {s['authorization_flagged_months']}",
        "-" * 64,
        "THE PATIENT AS A PAYER",
        (f"  Responsibility              ${s['patient_responsibility']:>12,.0f}"
         f"   {s['patient_share_of_allowed']:.1%} of allowed dollars"),
        f"  Collected                   ${s['patient_collected']:>12,.0f}   {s['patient_collection_rate']:.1%}",
        f"  Written off as bad debt     ${s['bad_debt']:>12,.0f}",
        f"  Charity care                ${s['charity_care']:>12,.0f}",
        f"  Denial write-offs           ${s['denial_write_offs']:>12,.0f}",
        "=" * 64,
    ]
    report = "\n".join(lines)
    (OUT / "denial_prevention.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote 7 files to {OUT}")


if __name__ == "__main__":
    main()
