"""
Revenue-cycle metrics engine: reads the generated claims and produces the
control outputs a revenue-cycle director reviews — denial summary by payer
and month, an AR aging snapshot, a one-page KPI summary, and a predictive
Net Realizable Value (NRV) / yield worklist for the open AR.

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

# Empirical-Bayes shrinkage strength. Thin payer x service-line cells (a payer
# that only shows up in a handful of Oncology claims) would otherwise produce
# wild yield rates — 0% or 100% denial off two claims. K acts as a pseudo-count
# of "prior" observations pulling every cell toward the portfolio-wide rate;
# a cell needs real volume before it moves the estimate. This is the standard
# fix for sparse-cell rate estimation and it also guarantees every probability
# lands strictly inside (0, 1), which the invariant tests then prove.
SHRINKAGE_K = 20.0


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_yield_predictions(claims, payer_name, payer_type, service_line_name):
    """Predictive yield + NRV worklist for every open (pending) claim.

    A pending claim has only a *billed* (submitted) amount — it has not been
    adjudicated, so it has no allowed or paid amount yet. To estimate the cash
    we will actually realize, we decompose the billed dollar through the three
    things that historically happen to it, learned per payer x service line
    from adjudicated claims only:

        expected_yield_rate = contractual_factor   # allowed / billed  (paid claims)
                            x net_collection_rate  # paid / allowed    (paid claims)
                            x (1 - denial_propensity)  # P(adjudicates as paid)

        Expected_NRV = billed_amount x expected_yield_rate

    That is why $100k of Medicare AR is close to cash while $100k of Self-Pay AR
    is worth pennies: the same billed dollar carries a very different yield rate.

    Priority score ranks the follow-up worklist by expected recoverable dollars
    weighted by aging urgency:

        Priority_Score = Expected_NRV x (ar_age_days / 30)

    Note on the priority formula: the naive "Expected_NRV x (1 - denial) x age"
    double-counts denial, because Expected_NRV already nets out denial
    probability. We drop the redundant factor so a claim is not penalized for
    denial risk twice. (See README "Deliberate deviations from the brief".)
    """
    adjudicated = [c for c in claims if c["status"] in ("Paid", "Denied")]
    paid = [c for c in adjudicated if c["status"] == "Paid"]

    # Three quantities are learned from history, each a fraction in [0, 1]:
    #   denial   = P(claim adjudicates as denied)
    #   contract = allowed / billed  (contractual write-down, paid claims)
    #   ncr      = paid / allowed    (net collection rate, paid claims)
    def denial_flag(c):
        return 1.0 if c["status"] == "Denied" else 0.0

    def contract_obs(c):
        s = float(c["submitted_amount"])
        return (float(c["allowed_amount"]) / s) if s > 0 else None

    def ncr_obs(c):
        a = float(c["allowed_amount"])
        return (float(c["paid_amount"]) / a) if a > 0 else None

    def tally(records, fn):
        s = n = 0.0
        for c in records:
            v = fn(c)
            if v is not None:
                s += v
                n += 1
        return s, n

    def shrink(cell_sum, cell_n, prior, k):
        """Empirical-Bayes posterior mean: cell evidence blended with the prior,
        weighted by k pseudo-observations of the prior."""
        return (cell_sum + k * prior) / (cell_n + k)

    # Level 0 — portfolio-wide priors (the ultimate backstop).
    g_denial = tally(adjudicated, denial_flag)[0] / len(adjudicated)
    cs, cn = tally(paid, contract_obs)
    g_contract = cs / cn
    ns, nn = tally(paid, ncr_obs)
    g_ncr = ns / nn

    # Level 1 — payer-level priors. Hierarchical shrinkage is what keeps the
    # yield model honest: a thin Oncology/Self-Pay cell borrows strength from
    # *all* Self-Pay claims (which really do collect ~20 cents on the dollar),
    # not from the global average that Medicare and commercial payers dominate.
    # Payers have hundreds of claims each, so their estimates barely move; the
    # small global pseudo-count only guards a degenerate payer.
    by_payer = defaultdict(list)
    for c in adjudicated:
        by_payer[c["payer_id"]].append(c)
    payer_prior = {}
    for pid, recs in by_payer.items():
        pd = tally(recs, denial_flag)
        pc = tally([c for c in recs if c["status"] == "Paid"], contract_obs)
        pn = tally([c for c in recs if c["status"] == "Paid"], ncr_obs)
        payer_prior[pid] = (
            shrink(pd[0], pd[1], g_denial, SHRINKAGE_K),
            shrink(pc[0], pc[1], g_contract, SHRINKAGE_K),
            shrink(pn[0], pn[1], g_ncr, SHRINKAGE_K),
        )

    # Level 2 — (payer, service_line) cell tallies.
    n_adj = defaultdict(int)
    den_s = defaultdict(float)
    con_s, con_n = defaultdict(float), defaultdict(int)
    ncr_s, ncr_n = defaultdict(float), defaultdict(int)
    for c in adjudicated:
        key = (c["payer_id"], c["service_line_id"])
        n_adj[key] += 1
        den_s[key] += denial_flag(c)
        cv = contract_obs(c)
        if c["status"] == "Paid" and cv is not None:
            con_s[key] += cv
            con_n[key] += 1
        nv = ncr_obs(c)
        if c["status"] == "Paid" and nv is not None:
            ncr_s[key] += nv
            ncr_n[key] += 1

    def rates(key):
        pid = key[0]
        p_denial, p_contract, p_ncr = payer_prior.get(
            pid, (g_denial, g_contract, g_ncr))
        denial = shrink(den_s[key], n_adj[key], p_denial, SHRINKAGE_K)
        contract = shrink(con_s[key], con_n[key], p_contract, SHRINKAGE_K)
        ncr = shrink(ncr_s[key], ncr_n[key], p_ncr, SHRINKAGE_K)
        return denial, contract, ncr

    # ---- cell-level yield table (feeds the CFO scorecard + full transparency)
    cells = sorted({(c["payer_id"], c["service_line_id"]) for c in claims})
    with open(OUT / "payer_yield_rates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["payer_name", "payer_type", "service_line", "adjudicated_claims",
                    "denial_propensity", "contractual_factor", "net_collection_rate",
                    "expected_yield_rate"])
        for key in cells:
            denial, contract, ncr = rates(key)
            yr = contract * ncr * (1 - denial)
            w.writerow([payer_name[key[0]], payer_type[key[0]], service_line_name[key[1]],
                        n_adj[key], round(denial, 4), round(contract, 4),
                        round(ncr, 4), round(yr, 4)])

    # ---- claim-level NRV predictions for every open-AR claim
    rows = []
    for c in claims:
        if c["status"] != "Pending":
            continue
        key = (c["payer_id"], c["service_line_id"])
        denial, contract, ncr = rates(key)
        billed = float(c["submitted_amount"])
        yield_rate = contract * ncr * (1 - denial)
        expected_allowed = billed * contract
        expected_nrv = billed * yield_rate
        age = int(c["ar_age_days"])
        priority = expected_nrv * (age / 30.0)
        rows.append({
            "claim_id": c["claim_id"],
            "payer_name": payer_name[c["payer_id"]],
            "payer_type": payer_type[c["payer_id"]],
            "service_line": service_line_name[c["service_line_id"]],
            "submitted_date": c["submitted_date"],
            "ar_age_days": age,
            "ar_bucket": c["ar_bucket"],
            "ar_bucket_sort": c["ar_bucket_sort"],
            "billed_amount": round(billed, 2),
            "contractual_factor": round(contract, 4),
            "net_collection_rate": round(ncr, 4),
            "denial_propensity": round(denial, 4),
            "expected_yield_rate": round(yield_rate, 4),
            "expected_allowed": round(expected_allowed, 2),
            "expected_nrv": round(expected_nrv, 2),
            "priority_score": round(priority, 2),
        })

    rows.sort(key=lambda r: -r["priority_score"])
    for rank, r in enumerate(rows, start=1):
        r["priority_rank"] = rank

    fields = ["priority_rank", "claim_id", "payer_name", "payer_type", "service_line",
              "submitted_date", "ar_age_days", "ar_bucket", "ar_bucket_sort",
              "billed_amount", "contractual_factor", "net_collection_rate",
              "denial_propensity", "expected_yield_rate", "expected_allowed",
              "expected_nrv", "priority_score"]
    with open(OUT / "ar_yield_predictions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    return rows


def main():
    claims = load("fact_claims.csv")
    payers = {r["payer_id"]: r["payer_name"] for r in load("dim_payer.csv")}
    payer_name = payers
    payer_type = {r["payer_id"]: r["payer_type"] for r in load("dim_payer.csv")}
    service_line_name = {r["service_line_id"]: r["service_line"]
                         for r in load("dim_service_line.csv")}

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

    # ---- predictive yield / NRV worklist
    yield_rows = build_yield_predictions(claims, payer_name, payer_type, service_line_name)
    expected_nrv = sum(r["expected_nrv"] for r in yield_rows)
    reserve = open_ar - expected_nrv

    lines = [
        "REVENUE CYCLE KPI SUMMARY",
        "=" * 40,
        f"Total claims:            {len(claims):>10,}",
        f"Adjudicated:             {len(adjudicated):>10,}",
        f"Denial rate:             {denial_rate:>10.1%}",
        f"Clean claim rate:        {clean_rate:>10.1%}",
        f"Net collection rate:     {collected / allowed:>10.1%}",
        f"Avg days to adjudicate:  {avg_days:>10.1f}",
        f"Open AR (gross):         {open_ar:>10,.0f}",
        f"AR > 90 days:            {ar_over_90:>10,.0f} ({ar_over_90 / open_ar:.1%} of AR)",
        "-" * 40,
        "PREDICTIVE YIELD (open AR)",
        f"Expected NRV:            {expected_nrv:>10,.0f} ({expected_nrv / open_ar:.1%} of gross)",
        f"Bad-debt reserve:        {reserve:>10,.0f} ({reserve / open_ar:.1%} of gross)",
        f"Worklist claims:        {len(yield_rows):>10,}",
    ]
    (OUT / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
