"""Invariants for the Canadian acute-care activity layer.

Same discipline as the revenue-cycle suite: every number the dashboard and the
business case rest on is re-derived here from the source abstracts, and any
identity that must hold is asserted rather than assumed.
"""

import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

ALC_PER_DIEM = 1150.0
CENT = 0.01


def read(directory, name):
    with open(directory / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def abstracts():
    return read(DATA, "fact_inpatient_abstracts.csv")


@pytest.fixture(scope="module")
def by_facility():
    return read(OUT, "activity_by_facility.csv")


@pytest.fixture(scope="module")
def monthly():
    return read(OUT, "activity_monthly.csv")


# --------------------------------------------------------------------------
# Source data integrity
# --------------------------------------------------------------------------

def test_length_of_stay_decomposes(abstracts):
    """Total LOS is exactly acute days plus ALC days — no third bucket, no
    rounding drift. If this ever fails, every bed-day number downstream is
    wrong and the business case is built on sand."""
    for a in abstracts:
        assert int(a["total_los_days"]) == int(a["acute_los_days"]) + int(a["alc_days"])


def test_stays_are_complete_and_in_window(abstracts):
    """A discharge abstract only exists once the patient has left. A snapshot
    containing a discharge in its own future is the activity-data equivalent of
    a claim submitted tomorrow."""
    for a in abstracts:
        assert a["admit_date"] < a["discharge_date"]
        assert a["discharge_date"] < "2026-07-01"
        assert int(a["acute_los_days"]) >= 1
        assert int(a["alc_days"]) >= 0


def test_riw_and_cost_are_positive(abstracts):
    for a in abstracts:
        assert float(a["riw"]) > 0
        assert float(a["total_cost"]) > 0


def test_deceased_patients_are_never_readmitted(abstracts):
    """The kind of check that catches a join error instantly, and the kind of
    error that destroys credibility with a clinical audience faster than any
    amount of methodology will rebuild it."""
    for a in abstracts:
        if a["discharge_disposition"] == "Died":
            assert int(a["readmit_30d"]) == 0


# --------------------------------------------------------------------------
# Control totals — the engine must tie to the source, to the penny
# --------------------------------------------------------------------------

def test_facility_rollup_ties_to_abstracts(abstracts, by_facility):
    assert sum(int(r["discharges"]) for r in by_facility) == len(abstracts)
    assert sum(int(r["alc_days"]) for r in by_facility) == \
        sum(int(a["alc_days"]) for a in abstracts)
    assert sum(int(r["acute_days"]) for r in by_facility) == \
        sum(int(a["acute_los_days"]) for a in abstracts)
    assert abs(sum(float(r["total_cost"]) for r in by_facility)
               - sum(float(a["total_cost"]) for a in abstracts)) < CENT * len(by_facility)


def test_monthly_rollup_ties_to_abstracts(abstracts, monthly):
    assert sum(int(r["discharges"]) for r in monthly) == len(abstracts)
    assert sum(int(r["observed_readmits"]) for r in monthly) == \
        sum(int(a["readmit_30d"]) for a in abstracts)


def test_cost_per_weighted_case_is_cost_over_riw(by_facility):
    for r in by_facility:
        expected = float(r["total_cost"]) / float(r["weighted_cases"])
        assert abs(float(r["cost_per_weighted_case"]) - expected) < CENT


def test_cpwc_ex_alc_removes_exactly_the_alc_cost(by_facility):
    """The ex-ALC figure must differ from the headline by precisely the ALC
    per-diem cost — otherwise the split is a second, unreconciled model."""
    for r in by_facility:
        removed = int(r["alc_days"]) * ALC_PER_DIEM
        expected = (float(r["total_cost"]) - removed) / float(r["weighted_cases"])
        assert abs(float(r["cost_per_weighted_case_ex_alc"]) - expected) < CENT
        assert float(r["cost_per_weighted_case_ex_alc"]) \
            <= float(r["cost_per_weighted_case"])


# --------------------------------------------------------------------------
# Risk adjustment
# --------------------------------------------------------------------------

def test_indirect_standardisation_sums_to_observed(abstracts, by_facility):
    """The defining identity of indirect standardisation: across the whole
    population, expected equals observed, so the authority-wide O/E ratio is
    1.00 and each site is read relative to its peers. If the totals drift, the
    ratios are measuring the standardisation, not the sites."""
    obs = sum(int(r["observed_readmits"]) for r in by_facility)
    exp = sum(float(r["expected_readmits"]) for r in by_facility)
    assert abs(obs - exp) / obs < 0.001

    acute = sum(int(r["acute_days"]) for r in by_facility)
    exp_acute = sum(float(r["expected_acute_days"]) for r in by_facility)
    assert abs(acute - exp_acute) / acute < 0.001


def test_risk_adjustment_moves_the_ranking_the_way_case_mix_predicts(by_facility):
    """A risk adjustment that never changes anything is decoration — but
    "the two rankings differ" is far too weak a way to say so.

    With six sites, any single adjacent swap satisfies it, including one caused
    by two sites sitting a ten-thousandth apart. Worse, a *broken* adjustment
    passes it more reliably than a correct one, because random noise reorders
    more readily than a real signal does. It is an assertion that gets easier
    the less the code works.

    So this asserts the mechanism instead. Rank 1 is the worst performer. A site
    treating the heaviest case mix should look *better* once you account for the
    patients it actually treats; a site with the lightest case mix should look
    *worse*, because its low crude rate was partly a gift from its patients.
    Both are directional claims a broken adjustment fails."""
    exp_rate = lambda r: float(r["expected_readmits"]) / int(r["discharges"])
    crude = [r["facility_name"] for r in
             sorted(by_facility, key=lambda r: -float(r["readmit_rate"]))]
    adjusted = [r["facility_name"] for r in
                sorted(by_facility, key=lambda r: -float(r["readmit_oe_ratio"]))]

    assert crude != adjusted, "adjustment reordered nothing at all"

    # A material move, not a tie-break flip.
    moves = {n: crude.index(n) - adjusted.index(n) for n in crude}
    assert max(abs(m) for m in moves.values()) >= 2, \
        f"only tie-break-sized movement, nothing reordered materially: {moves}"

    heaviest = max(by_facility, key=exp_rate)["facility_name"]
    lightest = min(by_facility, key=exp_rate)["facility_name"]
    assert heaviest != lightest

    assert adjusted.index(heaviest) > crude.index(heaviest), (
        f"{heaviest} treats the heaviest case mix "
        f"(expected {exp_rate(max(by_facility, key=exp_rate)):.4f}) and must rank "
        f"better once adjusted, not worse"
    )
    assert adjusted.index(lightest) < crude.index(lightest), (
        f"{lightest} treats the lightest case mix and must rank worse once its "
        f"easy case mix is accounted for"
    )


def test_los_index_is_acute_over_expected(by_facility):
    for r in by_facility:
        expected = int(r["acute_days"]) / float(r["expected_acute_days"])
        assert abs(float(r["los_index"]) - expected) < 1e-4


# --------------------------------------------------------------------------
# Plausibility bands — a generator that drifts out of these is broken
# --------------------------------------------------------------------------

def test_indicators_stay_in_plausible_bands(abstracts, by_facility):
    alc = sum(int(a["alc_days"]) for a in abstracts)
    pdays = sum(int(a["total_los_days"]) for a in abstracts)
    readmit = sum(int(a["readmit_30d"]) for a in abstracts) / len(abstracts)
    assert 0.05 < alc / pdays < 0.30, "ALC share of patient days outside plausible range"
    assert 0.05 < readmit < 0.20, "30-day readmission rate outside plausible range"

    for r in by_facility:
        assert 0.7 < float(r["los_index"]) < 1.4


def test_the_known_alc_outlier_site_is_detected(by_facility):
    """The generator gives Harbourview materially worse discharge-destination
    capacity. The engine has to find it — a site-variation report that cannot
    recover a planted difference will not find a real one either."""
    worst = max(by_facility, key=lambda r: float(r["alc_rate"]))
    assert worst["facility_name"] == "Harbourview Hospital"
    best = min(by_facility, key=lambda r: float(r["alc_rate"]))
    # The generator plants a 1.45x *odds* ratio. The observed *rate* ratio is
    # smaller, because odds and rates diverge once the baseline risk is
    # non-trivial — asserting 1.45 here would be asserting a misreading of the
    # generator's own parameter.
    assert float(worst["alc_rate"]) > 1.25 * float(best["alc_rate"])
