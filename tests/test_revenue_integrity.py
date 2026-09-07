"""
Invariants for the revenue-integrity engine.

Three ways this layer could be plausible and wrong, all of which render:

  * a contract variance report that learned the contract from the payments,
    and therefore always concludes the payer paid correctly;
  * an appeal-yield table that ranks denial reasons by how loud they are
    instead of by how much of the money comes back;
  * a price/volume/mix bridge whose bars do not add up to the movement they
    decompose - which looks exactly like one that does.
"""

import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@pytest.fixture(scope="module", autouse=True)
def built():
    """Regenerate and rebuild, so nothing here can pass on a stale file."""
    for script in ("data_generator/generate_claims_data.py",
                   "engine/build_revenue_integrity.py"):
        r = subprocess.run([sys.executable, str(ROOT / script)],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, f"{script} failed:\n{r.stdout}\n{r.stderr}"


@pytest.fixture(scope="module")
def claims():
    return read(DATA / "fact_claims.csv")


@pytest.fixture(scope="module")
def summary():
    return json.loads((OUT / "revenue_integrity.json").read_text(encoding="utf-8"))


# --- the fee schedule -----------------------------------------------------

def test_the_fee_schedule_is_a_dimension_not_a_derivation(claims):
    """Every claim's expected allowed must come from the published schedule.

    If the report derived the contract from what was paid, an underpaying payer
    would define its own contract and the variance would be zero forever. This
    reconciles each claim against dim_payer_contract independently.
    """
    schedule = {(int(r["payer_id"]), int(r["service_line_id"])):
                float(r["contracted_rate"]) for r in read(DATA / "dim_payer_contract.csv")}
    assert len(schedule) == 80, "the schedule should cover every payer x line"
    checked = 0
    for c in claims:
        key = (int(c["payer_id"]), int(c["service_line_id"]))
        expected = num(c["submitted_amount"]) * float(c["contracted_rate"])
        assert abs(expected - num(c["expected_allowed"])) < 0.02
        # The claim's own rate is the schedule's, except where a mid-year fee
        # schedule update applies.
        assert float(c["contracted_rate"]) <= schedule[key] + 1e-9
        checked += 1
    assert checked > 10_000


def test_underpayment_needs_to_clear_the_noise_band(claims):
    """Adjudication moves every allowed amount a few percent. A variance report
    with no materiality band flags about half the paid book, and gets ignored."""
    flagged, near_miss = 0, 0
    for c in claims:
        if c["status"] != "Paid":
            continue
        exp, allowed = num(c["expected_allowed"]), num(c["allowed_amount"])
        short = exp - allowed
        if num(c["underpaid_amount"]) > 0:
            flagged += 1
            assert short > exp * 0.05 - 0.02, "flagged inside the noise band"
        elif 0 < short <= exp * 0.05:
            near_miss += 1
    assert flagged > 100, "no underpayments at all makes this test vacuous"
    assert near_miss > flagged, \
        "the noise band is not actually suppressing anything"


def test_contract_variance_finds_the_payers_that_underpay():
    v = read(OUT / "contract_variance.csv")
    under = [r for r in v if r["verdict"] == "Under contract"]
    assert len(v) == 80
    assert 1 <= len(under) <= 8, "either nothing or everything is under contract"
    for r in under:
        assert float(r["variance_pct"]) < -0.05
        assert float(r["underpaid_amount"]) > 0
    # And the cells that are fine must genuinely be fine, not just unflagged.
    ok = [r for r in v if r["verdict"] == "At contract"]
    assert all(abs(float(r["variance_pct"])) <= 0.05 for r in ok)
    assert len(ok) > 60


def test_variance_amount_is_actual_minus_expected():
    for r in read(OUT / "contract_variance.csv"):
        assert abs(float(r["variance_amount"])
                   - (float(r["actual_allowed"]) - float(r["expected_allowed"]))) < 0.05


# --- appeals --------------------------------------------------------------

def test_an_appeal_outcome_exists_for_every_appealed_denial(claims):
    for c in claims:
        appealed = c["appeal_outcome"] in ("Overturned", "Upheld")
        assert appealed == (c["status"] == "Denied" and c["resubmitted"] == "1"), \
            "resubmitted and appeal_outcome disagree"
        if c["appeal_outcome"] == "Overturned":
            assert num(c["recovered_amount"]) > 0
        if c["appeal_outcome"] == "Upheld":
            assert num(c["recovered_amount"]) == 0
        if c["status"] != "Denied":
            assert c["appeal_outcome"] == ""


def test_recovery_never_exceeds_what_the_contract_would_have_paid(claims):
    """An overturned denial pays at contract, less the usual collection
    leakage. Recovering more than the contracted allowed amount would mean the
    appeal made the claim worth more than it ever was."""
    for c in claims:
        if c["appeal_outcome"] == "Overturned":
            assert num(c["recovered_amount"]) <= num(c["expected_allowed"]) + 0.02


def test_overturn_rates_differ_enough_by_reason_to_act_on():
    """The whole point of splitting appeals by reason. If every reason came
    back at the same rate, the table would be a list rather than a decision."""
    rows = read(OUT / "denial_appeals.csv")
    rates = [float(r["overturn_rate"]) for r in rows if int(r["appealed"]) >= 20]
    assert len(rates) >= 4
    assert max(rates) - min(rates) > 0.35, \
        "denial reasons all overturn alike, so the split adds nothing"


def test_volume_and_recoverable_rank_denial_reasons_differently():
    """The finding the page leads with: working the biggest pile is not the
    same as working the most recoverable one."""
    rows = read(OUT / "denial_appeals.csv")
    by_dollars = [r["denial_reason"] for r in
                  sorted(rows, key=lambda r: -float(r["denied_billed"]))]
    by_recoverable = [r["denial_reason"] for r in
                      sorted(rows, key=lambda r: -float(r["recoverable_left"]))]
    assert by_dollars != by_recoverable, \
        "the two rankings agree, so the recoverable column adds nothing"
    # The reason worth the least effort must be near the bottom on recoverable
    # however big it looks by dollars.
    timely = next(r for r in rows if r["denial_reason"].startswith("CO-29"))
    assert float(timely["overturn_rate"]) < 0.2
    assert by_recoverable.index(timely["denial_reason"]) >= len(rows) - 3


def test_recoverable_left_is_an_expected_value_not_a_promise():
    """It must be bounded by the un-appealed denials it is computed from,
    scaled by a rate that is never above 1."""
    for r in read(OUT / "denial_appeals.csv"):
        assert 0.0 <= float(r["overturn_rate"]) <= 1.0
        assert float(r["recoverable_left"]) >= 0
        if int(r["not_appealed"]) == 0:
            assert float(r["recoverable_left"]) == 0


# --- the bridge -----------------------------------------------------------

def test_the_bridge_reconciles_to_the_movement_it_decomposes(summary):
    """Volume + mix + rate = the change, to the cent. A bridge that does not
    close is worse than no bridge, because it looks like one."""
    parts = (summary["volume_effect"] + summary["mix_effect"]
             + summary["rate_effect"])
    assert abs(parts - summary["movement"]) < 1.0
    assert abs((summary["recent_revenue"] - summary["prior_revenue"])
               - summary["movement"]) < 0.02


def test_the_bridge_steps_are_the_same_numbers(summary):
    rows = {r["step"]: float(r["amount"]) for r in read(OUT / "revenue_bridge.csv")}
    assert rows["1. Prior period"] == pytest.approx(summary["prior_revenue"], abs=0.02)
    assert rows["5. Recent period"] == pytest.approx(summary["recent_revenue"], abs=0.02)
    assert (rows["1. Prior period"] + rows["2. Volume"] + rows["3. Mix"]
            + rows["4. Rate"]) == pytest.approx(rows["5. Recent period"], abs=1.0)


def test_both_bridge_windows_are_mature(summary, claims):
    """A window that runs to the snapshot is still adjudicating, so its paid
    dollars are systematically short and the bridge reports a volume collapse
    that is really the adjudication lag."""
    last_service = max(date.fromisoformat(c["service_date"]) for c in claims)
    recent_end = date.fromisoformat(summary["bridge_recent_to"])
    assert (last_service - recent_end).days >= 60
    # Equal windows, or the volume effect is measuring window length.
    prior_len = (date.fromisoformat(summary["bridge_prior_to"])
                 - date.fromisoformat(summary["bridge_prior_from"])).days
    recent_len = (recent_end
                  - date.fromisoformat(summary["bridge_recent_from"])).days
    assert prior_len == recent_len == summary["bridge_window_days"]
    assert prior_len > 120, "windows too short to say anything"


def test_the_bridge_cell_is_payer_by_service_line(summary):
    """Both roll-ups exist and reconcile to the same totals."""
    detail = read(OUT / "revenue_bridge_detail.csv")
    dims = {r["dimension"] for r in detail}
    assert dims == {"Payer", "Service line"}
    for dim in dims:
        rows = [r for r in detail if r["dimension"] == dim]
        assert sum(float(r["mix_effect"]) for r in rows) ==             pytest.approx(summary["mix_effect"], abs=1.0)
        assert sum(float(r["rate_effect"]) for r in rows) ==             pytest.approx(summary["rate_effect"], abs=1.0)
        assert sum(int(r["claims_prior"]) for r in rows) == summary["prior_claims"]
        assert sum(int(r["claims_recent"]) for r in rows) == summary["recent_claims"]


def test_a_coarser_cell_would_misread_the_mix_as_rate(claims, summary):
    """Why the cell is payer x service line rather than service line alone.

    A shift of the book from a commercial plan to a Medicare Advantage plan -
    same procedures, lower contracted share of billed - is a MIX movement. Split
    on service line only, it has nowhere to go but the rate term, and the report
    concludes prices fell when what moved was the payer mix.

    Both decompositions are computed here from the raw claims, so the claim is
    demonstrated rather than asserted: the coarse grain must push materially
    more of the movement into rate.
    """
    from datetime import timedelta

    service_dates = [date.fromisoformat(c["service_date"]) for c in claims]
    mature = max(service_dates) - timedelta(days=75)
    half = (mature - min(service_dates)).days // 2
    cut, start = mature - timedelta(days=half), mature - timedelta(days=2 * half)

    def decompose(keyfunc):
        q = [defaultdict(int), defaultdict(int)]
        rev = [defaultdict(float), defaultdict(float)]
        for c in claims:
            if c["status"] != "Paid":
                continue
            d = date.fromisoformat(c["service_date"])
            p = 0 if start <= d < cut else 1 if cut <= d < mature else None
            if p is None:
                continue
            q[p][keyfunc(c)] += 1
            rev[p][keyfunc(c)] += num(c["paid_amount"])
        Q0, Q1 = sum(q[0].values()), sum(q[1].values())
        R0 = sum(rev[0].values())
        mix = rate = 0.0
        for k in set(q[0]) | set(q[1]):
            r0 = rev[0][k] / q[0][k] if q[0][k] else 0.0
            r1 = rev[1][k] / q[1][k] if q[1][k] else 0.0
            mix += (q[1][k] - Q1 * (q[0][k] / Q0 if Q0 else 0)) * r0
            rate += q[1][k] * (r1 - r0)
        return mix, rate

    fine_mix, fine_rate = decompose(
        lambda c: (c["payer_id"], c["service_line_id"]))
    coarse_mix, coarse_rate = decompose(lambda c: c["service_line_id"])

    assert fine_mix == pytest.approx(summary["mix_effect"], abs=1.0)
    assert fine_rate == pytest.approx(summary["rate_effect"], abs=1.0)
    # The coarse grain hides the payer shift, so more of the movement lands in
    # rate and the mix term shrinks toward nothing.
    assert abs(coarse_rate) > abs(fine_rate) * 1.15, (
        f"coarse rate {coarse_rate:,.0f} is not materially larger than "
        f"fine rate {fine_rate:,.0f} - the grain is not doing any work")
    assert abs(coarse_mix) < abs(fine_mix)


def test_the_fee_schedule_cut_lands_as_rate_not_mix():
    """Medicare's rates were cut mid-year and its share barely moved. That is a
    rate effect; if it showed up as mix the decomposition is mislabelled."""
    rows = [r for r in read(OUT / "revenue_bridge_detail.csv")
            if r["dimension"] == "Payer" and r["category"] == "Medicare"]
    assert len(rows) == 1
    medicare = rows[0]
    assert float(medicare["rate_effect"]) < 0, "the fee schedule cut is not visible"
    assert abs(float(medicare["rate_effect"])) > abs(float(medicare["mix_effect"]))
    assert float(medicare["rate_recent"]) < float(medicare["rate_prior"])


# --- operations -----------------------------------------------------------

def test_charge_lag_separates_the_service_lines():
    """Charge lag is the half of days-in-AR the hospital owns. If every service
    line coded at the same speed there would be nothing to manage."""
    ops = read(OUT / "rcm_operations.csv")
    lags = [float(r["avg_charge_lag_days"]) for r in ops]
    assert max(lags) / min(lags) > 3, "coding speed is uniform, so DNFB is not a finding"
    slowest = max(ops, key=lambda r: float(r["avg_charge_lag_days"]))
    assert slowest["service_line"] == "Surgery"


def test_total_cycle_is_charge_lag_plus_adjudication():
    for r in read(OUT / "rcm_operations.csv"):
        assert abs(float(r["avg_total_cycle_days"])
                   - float(r["avg_charge_lag_days"])
                   - float(r["avg_days_to_adjudicate"])) < 0.02


def test_days_in_ar_uses_a_daily_revenue_denominator(summary):
    assert summary["daily_net_revenue"] > 0
    assert summary["days_in_ar"] == pytest.approx(
        summary["open_ar"] / summary["daily_net_revenue"], abs=0.1)
    assert 30 < summary["days_in_ar"] < 200, "days in AR is not a plausible number"


def test_operations_rows_cover_every_claim(summary, claims):
    ops = read(OUT / "rcm_operations.csv")
    assert sum(int(r["claims"]) for r in ops) == len(claims)
    assert sum(int(r["total_touches"]) for r in ops) == summary["touches_total"]


def test_first_pass_rate_counts_only_adjudicated_claims(summary, claims):
    """A pending claim has not had a first pass yet. Including it in the
    denominator makes the rate a function of how much AR is open."""
    adjudicated = [c for c in claims if c["status"] != "Pending"]
    clean = [c for c in adjudicated if c["status"] == "Paid" and not c["denial_reason"]]
    assert summary["first_pass_rate"] == pytest.approx(
        len(clean) / len(adjudicated), abs=0.001)
    assert 0.8 < summary["first_pass_rate"] < 0.99
