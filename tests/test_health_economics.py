"""Invariants for the economic evaluation.

An economic model is a persuasion device. That makes it exactly the kind of
artefact that most needs its arithmetic pinned down by tests: it is very easy
to build a model that produces the answer you wanted and very hard to notice
you have done it. These tests fix the identities, force the model to change its
mind when the inputs say it should, and assert the honest-labelling rules that
stop a negative ICER from being reported as a bargain.
"""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from health_economics import (  # noqa: E402
    PARAMS, WTP_THRESHOLD, base_params, beta_from, evaluate, gamma_from,
    sd_from_bounds,
)

OUT = ROOT / "output"


def read(name):
    with open(OUT / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def base_case():
    return {r["perspective"]: r for r in read("hta_base_case.csv")}


# --------------------------------------------------------------------------
# Model identities
# --------------------------------------------------------------------------

def test_incremental_cost_decomposes_exactly():
    """Incremental cost = program + community placement - value of avoided bed
    days. Recomputed by hand here, because this single line is the entire
    financial claim."""
    p = base_params()
    alc = 20000.0
    r = evaluate(alc, p)["B_cash_releasing"]
    avoided = alc * p["alc_reduction"]
    expected = (p["program_cost_annual"]
                + avoided * p["community_per_diem"]
                - avoided * p["alc_per_diem"] * p["variable_share"])
    assert r["incremental_cost"] == pytest.approx(expected)
    assert r["alc_days_avoided"] == pytest.approx(avoided)


def test_net_monetary_benefit_matches_its_definition(base_case):
    for r in base_case.values():
        nmb = float(r["incremental_qalys"]) * WTP_THRESHOLD - float(r["incremental_cost"])
        assert float(r["net_monetary_benefit"]) == pytest.approx(nmb, abs=1.0)


def test_icer_is_cost_over_qalys(base_case):
    for r in base_case.values():
        if r["icer"]:
            assert float(r["icer"]) == pytest.approx(
                float(r["incremental_cost"]) / float(r["incremental_qalys"]), rel=1e-4)


def test_cash_releasing_never_looks_better_than_opportunity_cost(base_case):
    """Perspective B values the same avoided day at a fraction of Perspective
    A's per-diem, so B can only ever cost more. If B ever came out cheaper, the
    two perspectives would be crossed somewhere."""
    a = float(base_case["A_opportunity_cost"]["incremental_cost"])
    b = float(base_case["B_cash_releasing"]["incremental_cost"])
    assert b > a


def test_qalys_are_identical_across_perspectives(base_case):
    """A costing perspective changes what a bed day is worth in dollars. It
    cannot change how much health the patient gained."""
    assert float(base_case["A_opportunity_cost"]["incremental_qalys"]) == \
        pytest.approx(float(base_case["B_cash_releasing"]["incremental_qalys"]))


def test_dominance_is_labelled_not_left_as_a_negative_ratio(base_case):
    """A negative ICER is ambiguous — it means dominant *or* dominated, and the
    ratio cannot tell you which. Wherever incremental cost is negative and
    QALYs positive, the model must say 'dominant' in words."""
    for r in base_case.values():
        cost = float(r["incremental_cost"])
        qalys = float(r["incremental_qalys"])
        if cost < 0 and qalys > 0:
            assert r["dominant"] == "True"


# --------------------------------------------------------------------------
# The model has to be able to change its mind
# --------------------------------------------------------------------------

def test_more_effective_program_is_never_worse():
    """Monotonicity. Doubling effectiveness while holding cost fixed cannot
    reduce net monetary benefit — a model that fails this has a sign error
    somewhere and will happily argue against its own intervention."""
    alc = 20000.0
    weak, strong = base_params(), base_params()
    weak["alc_reduction"], strong["alc_reduction"] = 0.10, 0.30
    for perspective in ("A_opportunity_cost", "B_cash_releasing"):
        assert evaluate(alc, strong)[perspective]["nmb"] > \
            evaluate(alc, weak)[perspective]["nmb"]


def test_an_expensive_enough_program_stops_being_worth_it():
    alc = 20000.0
    p = base_params()
    p["program_cost_annual"] = 50_000_000.0
    r = evaluate(alc, p)["A_opportunity_cost"]
    assert r["nmb"] < 0
    assert not r["dominant"]


def test_a_program_that_does_nothing_is_pure_cost():
    alc = 20000.0
    p = base_params()
    p["alc_reduction"] = 0.0
    r = evaluate(alc, p)["B_cash_releasing"]
    assert r["incremental_qalys"] == 0
    assert r["incremental_cost"] == pytest.approx(p["program_cost_annual"])
    assert r["icer"] is None, "an ICER with a zero denominator must not be reported"


# --------------------------------------------------------------------------
# Sensitivity analysis
# --------------------------------------------------------------------------

def test_tornado_is_sorted_and_every_parameter_appears():
    rows = read("hta_tornado.csv")
    assert {r["parameter"] for r in rows} == set(PARAMS)
    swings = [float(r["swing"]) for r in rows]
    assert swings == sorted(swings, reverse=True)
    assert all(s >= 0 for s in swings)


def test_at_least_one_parameter_flips_the_decision():
    """If no plausible movement of any single input changes the recommendation,
    the sensitivity analysis is theatre and the base case should just be stated
    as a certainty. Here the decision genuinely is contested, which is the
    finding."""
    rows = read("hta_tornado.csv")
    assert any(r["flips_decision"] == "True" for r in rows)


def test_ceac_is_monotonic_and_bounded():
    """A cost-effectiveness acceptability curve can only rise: raising what you
    are willing to pay per QALY cannot make an intervention less acceptable."""
    rows = read("hta_psa_ceac.csv")
    for col in ("p_cost_effective_A_opportunity_cost",
                "p_cost_effective_B_cash_releasing"):
        values = [float(r[col]) for r in rows]
        assert all(0.0 <= v <= 1.0 for v in values)
        assert values == sorted(values)


def test_psa_disagrees_between_perspectives():
    """The whole point of running both perspectives: at the conventional
    threshold they must reach materially different conclusions, or the
    distinction was not worth drawing."""
    at_threshold = next(r for r in read("hta_psa_ceac.csv")
                        if int(r["willingness_to_pay"]) == int(WTP_THRESHOLD))
    a = float(at_threshold["p_cost_effective_A_opportunity_cost"])
    b = float(at_threshold["p_cost_effective_B_cash_releasing"])
    assert a - b > 0.3


# --------------------------------------------------------------------------
# Distribution fitting
# --------------------------------------------------------------------------

def test_beta_and_gamma_recover_their_moments():
    a, b = beta_from(0.22, 0.05)
    assert a / (a + b) == pytest.approx(0.22, rel=1e-6)

    shape, scale = gamma_from(1_450_000.0, 200_000.0)
    assert shape * scale == pytest.approx(1_450_000.0, rel=1e-6)
    assert (shape * scale ** 2) ** 0.5 == pytest.approx(200_000.0, rel=1e-6)


def test_beta_fitting_survives_an_impossible_standard_deviation():
    """A Beta cannot have arbitrary spread for a given mean. The fitter has to
    clamp rather than emit negative shape parameters that crash the sampler
    3,000 iterations into a run."""
    a, b = beta_from(0.05, 0.9)
    assert a > 0 and b > 0


def test_every_parameter_has_bounds_that_bracket_its_base_case():
    for name, (base, low, high, dist, note) in PARAMS.items():
        assert low <= base <= high, name
        assert low < high, name
        assert dist in ("beta", "gamma"), name
        assert note.strip(), f"{name} has no documented justification"
        assert sd_from_bounds(low, high) > 0
