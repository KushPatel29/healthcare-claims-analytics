"""
Invariants for the predictive yield / NRV worklist.

The NRV numbers drive a bad-debt reserve on a CFO scorecard and the order a
follow-up team works its accounts. If the math is wrong, the reserve is
mis-stated and the worklist points people at the wrong claims — so CI proves
the arithmetic before Power BI ever opens the file.
"""

import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOL = 0.02  # rounding slack (engine rounds to cents / 4 dp)


@pytest.fixture(scope="module")
def data():
    read = lambda p: list(csv.DictReader(open(p, encoding="utf-8")))
    return {
        "claims": read(ROOT / "data" / "fact_claims.csv"),
        "pred": read(ROOT / "output" / "ar_yield_predictions.csv"),
        "rates": read(ROOT / "output" / "payer_yield_rates.csv"),
    }


def test_nrv_ceiling(data):
    """Expected NRV can never exceed what was billed, nor the expected allowed
    amount — you cannot forecast collecting more than the claim is worth."""
    for r in data["pred"]:
        nrv = float(r["expected_nrv"])
        assert nrv <= float(r["billed_amount"]) + TOL, r["claim_id"]
        assert nrv <= float(r["expected_allowed"]) + TOL, r["claim_id"]
        assert nrv >= 0.0, r["claim_id"]


def test_probability_bounds(data):
    """Every denial propensity is a genuine probability, strictly inside (0, 1).
    Empirical-Bayes shrinkage guarantees this even for thin payer cells."""
    for src in ("pred", "rates"):
        for r in data[src]:
            p = float(r["denial_propensity"])
            assert 0.0 < p < 1.0, f"{src}: denial propensity {p} out of (0,1)"


def test_yield_factors_bounded(data):
    """Each factor of the yield decomposition stays in its natural range, so the
    product is a fraction of the billed dollar — never an inflation of it."""
    for src in ("pred", "rates"):
        for r in data[src]:
            contract = float(r["contractual_factor"])
            ncr = float(r["net_collection_rate"])
            yr = float(r["expected_yield_rate"])
            assert 0.0 < contract <= 1.0 + TOL, contract
            assert 0.0 < ncr <= 1.0 + TOL, ncr
            assert 0.0 < yr < 1.0, yr


def test_yield_decomposition_identity(data):
    """yield = contract x ncr x (1 - denial), and NRV = billed x yield. Proving
    the identity means the exported columns are internally consistent, not three
    numbers that happen to sit in the same row. Tolerance scales with the billed
    amount because the columns are rounded (yield to 4 dp, dollars to cents)."""
    for r in data["pred"]:
        expected_yr = (float(r["contractual_factor"]) * float(r["net_collection_rate"])
                       * (1 - float(r["denial_propensity"])))
        assert abs(expected_yr - float(r["expected_yield_rate"])) < 0.001, r["claim_id"]
        billed = float(r["billed_amount"])
        assert abs(billed * float(r["expected_yield_rate"])
                   - float(r["expected_nrv"])) < 0.01 + billed * 1e-4, r["claim_id"]


def test_priority_score_and_ranking(data):
    """Priority = Expected NRV x (days in AR / 30), and the file is sorted by it
    descending with a dense 1..N rank — the worklist a team reads top-down."""
    pred = data["pred"]
    for r in pred:
        nrv = float(r["expected_nrv"])
        age = int(r["ar_age_days"])
        assert age >= 1, f"{r['claim_id']} open AR with non-positive age"
        age_factor = age / 30.0
        assert abs(nrv * age_factor - float(r["priority_score"])) < 0.01 + age_factor * 0.01, r["claim_id"]
    scores = [float(r["priority_score"]) for r in pred]
    assert scores == sorted(scores, reverse=True), "predictions not sorted by priority"
    ranks = [int(r["priority_rank"]) for r in pred]
    assert ranks == list(range(1, len(pred) + 1)), "priority_rank is not a dense 1..N"


def test_yield_control_total(data):
    """Total Expected NRV ties to the open-AR control total and is strictly less
    than it (the model always reserves *some* bad debt) — the same control-total
    discipline as a GL reconciliation, now on a predicted number."""
    open_ar = sum(float(c["submitted_amount"]) for c in data["claims"]
                  if c["status"] == "Pending")
    pred_billed = sum(float(r["billed_amount"]) for r in data["pred"])
    assert abs(open_ar - pred_billed) < 1.0, "worklist billed total drifts from open AR"
    total_nrv = sum(float(r["expected_nrv"]) for r in data["pred"])
    assert 0.0 < total_nrv < open_ar, "NRV must be positive and below gross AR"


def test_every_open_claim_is_scored(data):
    """Exactly one prediction per open claim — no pending dollar left unscored,
    none double-counted."""
    pending = {c["claim_id"] for c in data["claims"] if c["status"] == "Pending"}
    scored = [r["claim_id"] for r in data["pred"]]
    assert len(scored) == len(set(scored)), "duplicate claim in worklist"
    assert set(scored) == pending, "worklist does not cover the open AR exactly"


def test_reserve_is_not_theatrical(data):
    """Overall realization must land in a believable band. A model that reserves
    ~everything or ~nothing would pass the ceiling tests but be useless."""
    open_ar = sum(float(r["billed_amount"]) for r in data["pred"])
    nrv = sum(float(r["expected_nrv"]) for r in data["pred"])
    realization = nrv / open_ar
    assert 0.20 <= realization <= 0.85, f"realization {realization:.1%} implausible"


def test_self_pay_yields_below_insured(data):
    """The domain check: a Self-Pay dollar of AR must be worth materially less
    than an insured dollar. If the engine ever stops seeing that, the NRV story
    is broken regardless of whether the arithmetic still balances."""
    def avg_ncr(payer_type):
        vals = [float(r["net_collection_rate"]) for r in data["rates"]
                if r["payer_type"] == payer_type]
        return sum(vals) / len(vals)

    self_pay = avg_ncr("Self-Pay")
    for insured in ("Medicare", "Medicaid", "Commercial", "Medicare Advantage"):
        assert self_pay < avg_ncr(insured) - 0.20, (
            f"Self-Pay NCR {self_pay:.2f} not far below {insured}")
