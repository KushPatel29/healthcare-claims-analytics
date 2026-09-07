"""
Revenue integrity: the three questions a denial rate cannot answer.

`build_rcm_metrics.py` measures the claim as the payer left it - denied or
paid, fast or slow, collected or aging. This measures what the revenue cycle
can still do about it, which is a different set of questions:

1. CONTRACT VARIANCE. A claim can be paid, clean, and still short. The payer
   pays what it pays; the contract says what it owes. Comparing the allowed
   amount to the fee schedule per payer x service line is what contract
   management is, and it is unmeasurable without a schedule to compare to -
   which is why `dim_payer_contract` exists as a dimension rather than being
   re-derived here from the claims themselves. A variance report that learns
   the contract from the payments will always conclude the payer paid
   correctly.

   A materiality band is applied: adjudication moves every allowed amount a few
   percent either way, so a report that flags every dollar below contract flags
   half the book and gets ignored.

2. APPEAL YIELD. `resubmitted` says a denial was worked. It does not say
   whether the money came back. Split by reason, the two behave nothing alike:
   a missing-information denial is a clerical fix that mostly comes back, a
   timely-filing denial is money that is gone. Ranking denial reasons by
   VOLUME sends the team at the wrong ones; ranking them by recoverable
   dollars - overturn rate times what is being denied - sends them at the
   right ones. The gap between those two rankings is the finding.

3. PRICE, VOLUME AND MIX. Net revenue moved; that is not a finding. Whether it
   moved because we did more cases, because each case pays more, or because the
   case mix shifted toward the ones that pay better - those are three different
   conversations with three different owners. The decomposition is exact by
   construction (volume + mix + rate reconciles to the change to the cent), and
   the reconciliation is asserted rather than assumed.

Plus the operating metrics that sit underneath days-in-AR: charge lag (the half
of it the hospital owns, before the payer sees the claim), first-pass
resolution, and follow-up touches - the revenue cycle's actual capacity
constraint.

No third-party dependencies, by design: this repo runs on a bare Python.

Outputs (output/):
    contract_variance.csv    payer x service line against the fee schedule
    denial_appeals.csv       by denial reason: appeal rate, overturn, recovery
    revenue_bridge.csv       price / volume / mix between two mature periods
    revenue_bridge_summary.csv the same figures as one row, for the cards
    rcm_operations.csv       charge lag, first-pass, touches by service line
    revenue_integrity.txt    the headline figures

Usage:
    python engine/build_revenue_integrity.py
"""

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

AS_OF = date(2026, 7, 1)
# A claim must be under contract by more than this before it is an
# underpayment rather than adjudication noise. Same band the generator uses,
# stated here too because this is the report that acts on it.
MATERIALITY = 0.05
# Days in AR is AR divided by average daily net revenue. Ninety days is the
# conventional denominator: short enough to reflect the current run rate,
# long enough not to swing on one big week.
DAILY_REVENUE_WINDOW = 90
# Claims submitted recently are still adjudicating, so the last weeks of any
# window are systematically short of paid dollars. A bridge that runs to the
# snapshot date therefore reports a volume collapse that is really the
# adjudication lag, and it does it every single period. Both windows end far
# enough back that they are mature.
BRIDGE_MATURITY_DAYS = 75


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def write(name, rows, fieldnames=None):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames or list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# 1. Contract variance
# ---------------------------------------------------------------------------

def contract_variance(claims, payer_name, sl_name, schedule):
    cells = defaultdict(lambda: {"claims": 0, "billed": 0.0, "expected": 0.0,
                                 "allowed": 0.0, "under_claims": 0,
                                 "under_amount": 0.0})
    for c in claims:
        if c["status"] != "Paid":
            continue
        key = (int(c["payer_id"]), int(c["service_line_id"]))
        cell = cells[key]
        cell["claims"] += 1
        cell["billed"] += num(c["submitted_amount"])
        cell["expected"] += num(c["expected_allowed"])
        cell["allowed"] += num(c["allowed_amount"])
        if num(c["underpaid_amount"]) > 0:
            cell["under_claims"] += 1
            cell["under_amount"] += num(c["underpaid_amount"])

    rows = []
    for (pid, slid), cell in sorted(cells.items(), key=lambda kv: -kv[1]["under_amount"]):
        realised = cell["allowed"] / cell["billed"] if cell["billed"] else 0.0
        variance = (cell["allowed"] / cell["expected"] - 1.0) if cell["expected"] else 0.0
        rows.append({
            # The cell, spelled out. A contract is negotiated per payer AND
            # service line, so the payer alone is the wrong axis: one line
            # paying 12% under nets away against nine that are fine, and the
            # chart shows nothing wrong anywhere.
            "cell": f"{payer_name[pid]} / {sl_name[slid]}",
            "payer": payer_name[pid],
            "service_line": sl_name[slid],
            "contracted_rate": schedule[(pid, slid)],
            "realised_rate": round(realised, 4),
            "paid_claims": cell["claims"],
            "billed": round(cell["billed"], 2),
            "expected_allowed": round(cell["expected"], 2),
            "actual_allowed": round(cell["allowed"], 2),
            "variance_amount": round(cell["allowed"] - cell["expected"], 2),
            "variance_pct": round(variance, 4),
            "underpaid_claims": cell["under_claims"],
            "underpaid_amount": round(cell["under_amount"], 2),
            # A cell is only "under contract" once it is past the noise band at
            # the aggregate level too, not just on individual claims.
            "verdict": ("Under contract" if variance < -MATERIALITY
                        else "Over contract" if variance > MATERIALITY
                        else "At contract"),
        })
    return rows


# ---------------------------------------------------------------------------
# 2. Appeal yield
# ---------------------------------------------------------------------------

def denial_appeals(claims):
    by_reason = defaultdict(lambda: {"denials": 0, "denied_amount": 0.0,
                                     "appealed": 0, "overturned": 0,
                                     "recovered": 0.0, "appeal_days": [],
                                     "not_appealed_amount": 0.0})
    for c in claims:
        if c["status"] != "Denied":
            continue
        r = by_reason[c["denial_reason"]]
        r["denials"] += 1
        r["denied_amount"] += num(c["submitted_amount"])
        if c["appeal_outcome"] in ("Overturned", "Upheld"):
            r["appealed"] += 1
            r["appeal_days"].append(num(c["appeal_days"]))
            if c["appeal_outcome"] == "Overturned":
                r["overturned"] += 1
                r["recovered"] += num(c["recovered_amount"])
        else:
            r["not_appealed_amount"] += num(c["expected_allowed"])

    rows = []
    for reason, r in by_reason.items():
        overturn = r["overturned"] / r["appealed"] if r["appealed"] else 0.0
        rows.append({
            "denial_reason": reason,
            "denials": r["denials"],
            "denied_billed": round(r["denied_amount"], 2),
            "appealed": r["appealed"],
            "appeal_rate": round(r["appealed"] / r["denials"], 4),
            "overturned": r["overturned"],
            "overturn_rate": round(overturn, 4),
            "recovered": round(r["recovered"], 2),
            "avg_appeal_days": round(sum(r["appeal_days"]) / len(r["appeal_days"]), 1)
                               if r["appeal_days"] else 0.0,
            "not_appealed": r["denials"] - r["appealed"],
            # What the un-appealed denials would be worth at this reason's own
            # historical overturn rate. Not a promise - an expected value, and
            # the only honest way to rank where the next hour of appeal work
            # should go.
            "recoverable_left": round(r["not_appealed_amount"] * overturn, 2),
        })
    rows.sort(key=lambda x: -x["recoverable_left"])
    return rows


# ---------------------------------------------------------------------------
# 3. Price / volume / mix
# ---------------------------------------------------------------------------

def revenue_bridge(claims, sl_name, payer_name):
    """Exact decomposition of the change in net revenue between two periods.

        volume = (Q1 - Q0) * r0_bar
        mix    = sum_i (q1_i - Q1 * s0_i) * r0_i
        rate   = sum_i q1_i * (r1_i - r0_i)

    which sums to R1 - R0 identically. The reconciliation is asserted at the
    end of this function rather than trusted: a bridge whose bars do not add up
    to the movement it decomposes is worse than no bridge, because it looks
    like one.

    The cell is payer x service line, not service line alone. Split by service
    line only, a shift of the book from a commercial plan to a Medicare
    Advantage plan - same procedures, lower contracted share of billed - shows
    up as a RATE effect, and the report says prices fell when what actually
    happened is that the payer mix moved. Cell-level effects are additive, so
    the same arithmetic rolls up to either view.
    """
    # Windows are derived from the data, not hard-coded: two equal halves of
    # whatever mature history exists. A fixed six months either side silently
    # runs off the front of the dataset and reports the shortfall as a volume
    # collapse.
    service_dates = [date.fromisoformat(c["service_date"]) for c in claims]
    mature = max(service_dates) - timedelta(days=BRIDGE_MATURITY_DAYS)
    half = (mature - min(service_dates)).days // 2
    cut = mature - timedelta(days=half)
    start = cut - timedelta(days=half)

    def period_of(c):
        d = date.fromisoformat(c["service_date"])
        if start <= d < cut:
            return 0
        if cut <= d < mature:
            return 1
        return None

    q = [defaultdict(int), defaultdict(int)]
    rev = [defaultdict(float), defaultdict(float)]
    for c in claims:
        if c["status"] != "Paid":
            continue
        p = period_of(c)
        if p is None:
            continue
        key = (int(c["payer_id"]), int(c["service_line_id"]))
        q[p][key] += 1
        rev[p][key] += num(c["paid_amount"])

    cells = sorted(set(q[0]) | set(q[1]))
    Q0, Q1 = sum(q[0].values()), sum(q[1].values())
    R0, R1 = sum(rev[0].values()), sum(rev[1].values())
    r0_bar = R0 / Q0 if Q0 else 0.0

    volume = (Q1 - Q0) * r0_bar
    mix = rate = 0.0
    by_payer = defaultdict(lambda: {"mix": 0.0, "rate": 0.0, "q0": 0, "q1": 0,
                                    "r0": 0.0, "r1": 0.0})
    by_line = defaultdict(lambda: {"mix": 0.0, "rate": 0.0, "q0": 0, "q1": 0,
                                   "r0": 0.0, "r1": 0.0})
    for key in cells:
        pid, slid = key
        r0 = rev[0][key] / q[0][key] if q[0][key] else 0.0
        r1 = rev[1][key] / q[1][key] if q[1][key] else 0.0
        s0 = q[0][key] / Q0 if Q0 else 0.0
        mix_i = (q[1][key] - Q1 * s0) * r0
        rate_i = q[1][key] * (r1 - r0)
        mix += mix_i
        rate += rate_i
        for bucket, name in ((by_payer, payer_name[pid]), (by_line, sl_name[slid])):
            b = bucket[name]
            b["mix"] += mix_i
            b["rate"] += rate_i
            b["q0"] += q[0][key]
            b["q1"] += q[1][key]
            b["r0"] += rev[0][key]
            b["r1"] += rev[1][key]

    assert abs((volume + mix + rate) - (R1 - R0)) < 1.0,         "price/volume/mix does not reconcile to the movement it decomposes"

    def detail_rows(bucket, label):
        out = []
        for name, b in sorted(bucket.items(), key=lambda kv: kv[1]["mix"] + kv[1]["rate"]):
            out.append({
                "dimension": label,
                "category": name,
                "claims_prior": b["q0"],
                "claims_recent": b["q1"],
                "revenue_prior": round(b["r0"], 2),
                "revenue_recent": round(b["r1"], 2),
                "rate_prior": round(b["r0"] / b["q0"], 2) if b["q0"] else 0.0,
                "rate_recent": round(b["r1"] / b["q1"], 2) if b["q1"] else 0.0,
                "mix_effect": round(b["mix"], 2),
                "rate_effect": round(b["rate"], 2),
                "total_effect": round(b["mix"] + b["rate"], 2),
            })
        return out

    detail = detail_rows(by_payer, "Payer") + detail_rows(by_line, "Service line")

    bridge = [
        {"step": "1. Prior period", "amount": round(R0, 2), "kind": "Total"},
        {"step": "2. Volume", "amount": round(volume, 2), "kind": "Change"},
        {"step": "3. Mix", "amount": round(mix, 2), "kind": "Change"},
        {"step": "4. Rate", "amount": round(rate, 2), "kind": "Change"},
        {"step": "5. Recent period", "amount": round(R1, 2), "kind": "Total"},
    ]
    summary = {
        "bridge_prior_from": start.isoformat(), "bridge_prior_to": cut.isoformat(),
        "bridge_recent_from": cut.isoformat(), "bridge_recent_to": mature.isoformat(),
        "bridge_window_days": half,
        "prior_revenue": round(R0, 2), "recent_revenue": round(R1, 2),
        "movement": round(R1 - R0, 2),
        "volume_effect": round(volume, 2), "mix_effect": round(mix, 2),
        "rate_effect": round(rate, 2),
        "prior_claims": Q0, "recent_claims": Q1,
        "prior_rate": round(r0_bar, 2),
        "recent_rate": round(R1 / Q1, 2) if Q1 else 0.0,
    }
    return bridge, detail, summary


# ---------------------------------------------------------------------------
# 4. Operations under days-in-AR
# ---------------------------------------------------------------------------

def operations(claims, sl_name):
    cells = defaultdict(lambda: {"claims": 0, "lag": 0, "adj": 0.0, "adj_n": 0,
                                 "touches": 0, "first_pass": 0, "billed": 0.0,
                                 "denied": 0, "adjudicated": 0})
    for c in claims:
        slid = int(c["service_line_id"])
        cell = cells[slid]
        cell["claims"] += 1
        cell["lag"] += int(num(c["charge_lag_days"]))
        cell["touches"] += int(num(c["follow_up_touches"]))
        cell["billed"] += num(c["submitted_amount"])
        if c["status"] != "Pending":
            cell["adjudicated"] += 1
        if c["status"] == "Paid" and not c["denial_reason"]:
            cell["first_pass"] += 1
        if c["status"] == "Denied":
            cell["denied"] += 1
        if c["days_to_adjudicate"]:
            cell["adj"] += num(c["days_to_adjudicate"])
            cell["adj_n"] += 1

    rows = []
    for slid, cell in sorted(cells.items(), key=lambda kv: -kv[1]["lag"] / max(kv[1]["claims"], 1)):
        n = cell["claims"]
        rows.append({
            "service_line": sl_name[slid],
            "claims": n,
            "billed": round(cell["billed"], 2),
            "avg_charge_lag_days": round(cell["lag"] / n, 2),
            "avg_days_to_adjudicate": round(cell["adj"] / cell["adj_n"], 2)
                                      if cell["adj_n"] else 0.0,
            # Charge lag is the part of the cycle that runs before the payer
            # has even seen the claim, and it is the part the hospital owns.
            "avg_total_cycle_days": round(cell["lag"] / n
                                          + (cell["adj"] / cell["adj_n"]
                                             if cell["adj_n"] else 0.0), 2),
            "adjudicated": cell["adjudicated"],
            "first_pass_rate": round(cell["first_pass"]
                                     / max(cell["adjudicated"], 1), 4),
            "denial_rate": round(cell["denied"] / max(cell["adjudicated"], 1), 4),
            "touches_per_claim": round(cell["touches"] / n, 2),
            "total_touches": cell["touches"],
        })
    return rows


# ---------------------------------------------------------------------------

def build():
    claims = load("fact_claims.csv")
    payer_name = {int(p["payer_id"]): p["payer_name"] for p in load("dim_payer.csv")}
    sl_name = {int(s["service_line_id"]): s["service_line"]
               for s in load("dim_service_line.csv")}
    schedule = {(int(r["payer_id"]), int(r["service_line_id"])):
                float(r["contracted_rate"]) for r in load("dim_payer_contract.csv")}

    variance = contract_variance(claims, payer_name, sl_name, schedule)
    appeals = denial_appeals(claims)
    bridge, bridge_detail, bridge_summary = revenue_bridge(claims, sl_name, payer_name)
    ops = operations(claims, sl_name)

    # --- days in AR -------------------------------------------------------
    window_start = AS_OF - timedelta(days=DAILY_REVENUE_WINDOW)
    recent_paid = sum(num(c["paid_amount"]) for c in claims
                      if c["adjudicated_date"]
                      and date.fromisoformat(c["adjudicated_date"]) >= window_start)
    daily_revenue = recent_paid / DAILY_REVENUE_WINDOW
    open_ar = sum(num(c["submitted_amount"]) for c in claims if c["status"] == "Pending")

    total_touches = sum(int(num(c["follow_up_touches"])) for c in claims)
    first_pass = sum(1 for c in claims if c["status"] == "Paid" and not c["denial_reason"])
    adjudicated = sum(1 for c in claims if c["status"] != "Pending")

    under = [v for v in variance if v["verdict"] == "Under contract"]
    summary = {
        "claims": len(claims),
        "days_in_ar": round(open_ar / daily_revenue, 1) if daily_revenue else 0.0,
        "open_ar": round(open_ar, 2),
        "daily_net_revenue": round(daily_revenue, 2),
        "first_pass_rate": round(first_pass / adjudicated, 4) if adjudicated else 0.0,
        "avg_charge_lag_days": round(
            sum(int(num(c["charge_lag_days"])) for c in claims) / len(claims), 2),
        "slowest_to_code": ops[0]["service_line"],
        "slowest_charge_lag": ops[0]["avg_charge_lag_days"],
        "touches_total": total_touches,
        "touches_per_claim": round(total_touches / len(claims), 2),
        "contract_cells": len(variance),
        "cells_under_contract": len(under),
        "underpaid_claims": sum(v["underpaid_claims"] for v in variance),
        "underpaid_amount": round(sum(v["underpaid_amount"] for v in variance), 2),
        # Two different "worst" questions, named separately rather than one
        # label quietly meaning whichever the code happened to sort by.
        "worst_contract_by_dollars": under[0]["cell"] if under else "",
        "worst_contract_dollars": under[0]["underpaid_amount"] if under else 0.0,
        "worst_contract_by_rate": (min(under, key=lambda r: r["variance_pct"])["cell"]
                                   if under else ""),
        "worst_contract_variance": (min(under, key=lambda r: r["variance_pct"])
                                    ["variance_pct"] if under else 0.0),
        "denials": sum(a["denials"] for a in appeals),
        "appealed": sum(a["appealed"] for a in appeals),
        "overturned": sum(a["overturned"] for a in appeals),
        "recovered": round(sum(a["recovered"] for a in appeals), 2),
        "recoverable_left": round(sum(a["recoverable_left"] for a in appeals), 2),
        "best_appeal_reason": appeals[0]["denial_reason"],
        "best_appeal_overturn": appeals[0]["overturn_rate"],
        "biggest_denial_reason": max(appeals, key=lambda a: a["denials"])["denial_reason"],
    }
    summary["overturn_rate"] = round(
        summary["overturned"] / summary["appealed"], 4) if summary["appealed"] else 0.0
    summary["appeal_rate"] = round(
        summary["appealed"] / summary["denials"], 4) if summary["denials"] else 0.0
    summary.update(bridge_summary)

    write("revenue_bridge_summary.csv", [{
        k: summary[k] for k in (
            "prior_revenue", "recent_revenue", "movement", "volume_effect",
            "mix_effect", "rate_effect", "prior_claims", "recent_claims",
            "prior_rate", "recent_rate", "bridge_window_days",
            "bridge_prior_from", "bridge_recent_to", "days_in_ar",
            "first_pass_rate", "avg_charge_lag_days", "touches_total",
            "touches_per_claim")}])
    write("contract_variance.csv", variance)
    write("denial_appeals.csv", appeals)
    write("revenue_bridge.csv", bridge)
    write("revenue_bridge_detail.csv", bridge_detail)
    write("rcm_operations.csv", ops)
    (OUT / "revenue_integrity.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary, variance, appeals, bridge, ops


def main():
    s, variance, appeals, bridge, ops = build()
    lines = [
        "REVENUE INTEGRITY",
        "=" * 62,
        f"Days in AR                    {s['days_in_ar']:>10.1f}"
        f"   (${s['open_ar']:,.0f} AR / ${s['daily_net_revenue']:,.0f} a day)",
        f"First-pass resolution rate    {s['first_pass_rate']:>10.1%}",
        f"Charge lag (service->bill)    {s['avg_charge_lag_days']:>10.1f} days"
        f"   slowest: {s['slowest_to_code']} at {s['slowest_charge_lag']:.1f}",
        f"Follow-up touches             {s['touches_total']:>10,}"
        f"   ({s['touches_per_claim']:.2f} per claim)",
        "-" * 62,
        "CONTRACT VARIANCE",
        f"Cells under contract          {s['cells_under_contract']:>10,}"
        f" of {s['contract_cells']}",
        f"Underpaid claims              {s['underpaid_claims']:>10,}",
        f"Underpaid amount              ${s['underpaid_amount']:>9,.0f}",
        f"Worst by rate                 {s['worst_contract_by_rate']}"
        f"  at {s['worst_contract_variance']:.1%}",
        f"Worst by dollars              {s['worst_contract_by_dollars']}"
        f"  ${s['worst_contract_dollars']:,.0f}",
        "-" * 62,
        "APPEAL YIELD",
        f"Denials                       {s['denials']:>10,}",
        f"Appealed                      {s['appealed']:>10,}  ({s['appeal_rate']:.0%})",
        f"Overturned                    {s['overturned']:>10,}  ({s['overturn_rate']:.0%})",
        f"Recovered                     ${s['recovered']:>9,.0f}",
        f"Left on the table             ${s['recoverable_left']:>9,.0f}"
        "   un-appealed denials at their own overturn rate",
        f"Biggest by volume             {s['biggest_denial_reason']}",
        f"Biggest by recoverable        {s['best_appeal_reason']}",
        "-" * 62,
        f"PRICE / VOLUME / MIX   ({s['bridge_recent_from']} to {s['bridge_recent_to']}"
        f" vs the {s['bridge_window_days']} days before)",
        f"                              both windows end {BRIDGE_MATURITY_DAYS} days back,"
        " so neither is still adjudicating",
        f"Prior net revenue             ${s['prior_revenue']:>9,.0f}"
        f"   {s['prior_claims']:,} claims at ${s['prior_rate']:,.0f}",
        f"  volume                      ${s['volume_effect']:>+9,.0f}",
        f"  mix                         ${s['mix_effect']:>+9,.0f}",
        f"  rate                        ${s['rate_effect']:>+9,.0f}",
        f"Recent net revenue            ${s['recent_revenue']:>9,.0f}"
        f"   {s['recent_claims']:,} claims at ${s['recent_rate']:,.0f}",
        "=" * 62,
    ]
    report = "\n".join(lines)
    (OUT / "revenue_integrity.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote 6 files to {OUT}")


if __name__ == "__main__":
    main()
