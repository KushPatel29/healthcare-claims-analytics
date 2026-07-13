"""
Revenue-cycle metrics engine: reads the generated claims and produces the
control outputs a revenue-cycle director reviews — denial summary by payer
and month, an AR aging snapshot, and a one-page KPI summary.

These outputs exist so the numbers on the Power BI dashboard can be
independently reproduced (and CI-verified) outside Power BI.

Usage:
    python build_rcm_metrics.py
"""

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    claims = load("fact_claims.csv")
    payers = {r["payer_id"]: r["payer_name"] for r in load("dim_payer.csv")}

    adjudicated = [c for c in claims if c["status"] in ("Paid", "Denied")]
    denied = [c for c in claims if c["status"] == "Denied"]
    paid = [c for c in claims if c["status"] == "Paid"]
    pending = [c for c in claims if c["status"] == "Pending"]

    # ---- denial summary: payer x month
    grp = defaultdict(lambda: {"claims": 0, "denied": 0, "denied_amount": 0.0})
    for c in adjudicated:
        key = (payers[c["payer_id"]], c["submitted_date"][:7])
        grp[key]["claims"] += 1
        if c["status"] == "Denied":
            grp[key]["denied"] += 1
            grp[key]["denied_amount"] += float(c["submitted_amount"])
    with open(OUT / "denial_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["payer_name", "month", "adjudicated_claims", "denied_claims",
                    "denial_rate", "denied_amount"])
        for (payer, month), v in sorted(grp.items()):
            w.writerow([payer, month, v["claims"], v["denied"],
                        round(v["denied"] / v["claims"], 4), round(v["denied_amount"], 2)])

    # ---- AR aging snapshot
    with open(OUT / "ar_aging.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["claim_id", "payer_name", "submitted_date", "ar_age_days",
                    "ar_bucket", "submitted_amount"])
        for c in sorted(pending, key=lambda c: -int(c["ar_age_days"])):
            w.writerow([c["claim_id"], payers[c["payer_id"]], c["submitted_date"],
                        c["ar_age_days"], c["ar_bucket"], c["submitted_amount"]])

    # ---- KPI summary
    denial_rate = len(denied) / len(adjudicated)
    clean = [c for c in paid if c["resubmitted"] == "0"]
    clean_rate = len(clean) / len(adjudicated)
    allowed = sum(float(c["allowed_amount"]) for c in paid)
    collected = sum(float(c["paid_amount"]) for c in paid)
    open_ar = sum(float(c["submitted_amount"]) for c in pending)
    ar_over_90 = sum(float(c["submitted_amount"]) for c in pending
                     if c["ar_bucket"] in ("91-120", "120+"))
    avg_days = (sum(int(c["days_to_adjudicate"]) for c in adjudicated)
                / len(adjudicated))

    lines = [
        "REVENUE CYCLE KPI SUMMARY",
        "=" * 40,
        f"Total claims:            {len(claims):>10,}",
        f"Adjudicated:             {len(adjudicated):>10,}",
        f"Denial rate:             {denial_rate:>10.1%}",
        f"Clean claim rate:        {clean_rate:>10.1%}",
        f"Net collection rate:     {collected / allowed:>10.1%}",
        f"Avg days to adjudicate:  {avg_days:>10.1f}",
        f"Open AR:                 {open_ar:>10,.0f}",
        f"AR > 90 days:            {ar_over_90:>10,.0f} ({ar_over_90 / open_ar:.1%} of AR)",
    ]
    (OUT / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
