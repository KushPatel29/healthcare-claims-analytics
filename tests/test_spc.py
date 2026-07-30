"""Tests for the SPC library and the charts it produces.

A control chart you have never watched succeed *and* fail is decoration. These
tests do both: they plant a shift and demand the rules find it, and they hand
the rules a stable process and demand silence. The Western Electric rules are
also each exercised on a series constructed to trip exactly that rule.
"""

import csv
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from spc import (  # noqa: E402
    p_chart, u_chart, western_electric, dispersion_factor,
)

OUT = ROOT / "output"


def read(name):
    with open(OUT / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rules_fired(chart):
    return {s["rule"] for s in western_electric(chart)}


# --------------------------------------------------------------------------
# Chart mathematics
# --------------------------------------------------------------------------

def test_p_chart_centre_is_pooled_not_mean_of_rates():
    """Two months: 5/10 and 100/1000. The mean of the two *rates* is 0.30; the
    pooled proportion is 105/1010 = 0.104. Averaging rates would let a
    ten-patient month outvote a thousand-patient one, which is how a small site
    quietly hijacks a region-wide centre line.

    The denominators have to differ *and* the rates have to differ for this test
    to mean anything. An earlier version used 1/10 and 100/1000 — both 0.10 —
    where the pooled proportion and the mean of the rates are identical, so it
    would have passed just as happily against the bug it exists to catch."""
    chart = p_chart([("m1", 5, 10), ("m2", 100, 1000)])
    mean_of_rates = (5 / 10 + 100 / 1000) / 2
    assert mean_of_rates == pytest.approx(0.30)
    assert chart[0]["centre"] == pytest.approx(105 / 1010)
    assert chart[0]["centre"] != pytest.approx(mean_of_rates)


def test_p_chart_limits_are_wider_for_smaller_denominators():
    chart = p_chart([("small", 5, 50), ("large", 100, 1000)])
    small, large = chart
    assert small["sigma"] > large["sigma"]
    assert (small["ucl"] - small["lcl"]) > (large["ucl"] - large["lcl"])


def test_p_chart_limits_never_leave_the_unit_interval():
    """A rare event with 3-sigma limits will happily produce a negative lower
    limit if nothing clamps it. A negative rate on a published chart is the
    fastest way to lose a clinical audience."""
    chart = p_chart([("m", 1, 40)] * 6)
    for p in chart:
        assert p["lcl"] >= 0.0
        assert p["ucl"] <= 1.0


def test_u_chart_sigma_follows_poisson():
    chart = u_chart([("a", 40, 10.0), ("b", 60, 20.0)])
    centre = 100 / 30.0
    assert chart[0]["centre"] == pytest.approx(centre)
    assert chart[0]["sigma"] == pytest.approx((centre / 10.0) ** 0.5)


def test_zero_denominator_is_carried_not_crashed():
    chart = p_chart([("m1", 5, 100), ("gap", 0, 0), ("m3", 5, 100)])
    assert chart[1]["z"] is None
    assert chart[1]["value"] is None


def test_empty_exposure_is_rejected():
    with pytest.raises(ValueError):
        u_chart([("a", 0, 0.0)])


# --------------------------------------------------------------------------
# Western Electric rules — each one, on purpose
# --------------------------------------------------------------------------

def make_chart(z_values):
    """A minimal chart object carrying pre-set z-scores, so rule logic can be
    tested independently of how the z-scores were derived."""
    return [{"label": f"p{i}", "z": z} for i, z in enumerate(z_values)]


def test_rule_1_single_point_outside_limits():
    assert 1 in rules_fired(make_chart([0.1, -0.2, 3.4]))
    assert 1 not in rules_fired(make_chart([0.1, -0.2, 2.9]))


def test_rule_2_two_of_three_beyond_two_sigma():
    assert 2 in rules_fired(make_chart([0.0, 2.3, 0.4, 2.5]))
    # opposite sides must not count as a pattern
    assert 2 not in rules_fired(make_chart([0.0, 2.3, 0.4, -2.5]))


def test_rule_3_four_of_five_beyond_one_sigma():
    assert 3 in rules_fired(make_chart([0.0, 1.2, 1.4, 0.2, 1.3, 1.5]))
    assert 3 not in rules_fired(make_chart([0.0, 1.2, 1.4, 0.2, 0.3, 0.5]))


def test_rule_4_eight_consecutive_on_one_side():
    """The rule that earns its keep: not one of these eight points is
    remarkable, and a threshold alert never fires — but the process has
    unmistakably moved."""
    assert 4 in rules_fired(make_chart([0.3] * 8))
    assert 4 not in rules_fired(make_chart([0.3] * 7 + [-0.1]))


def test_a_missing_period_breaks_a_run_rather_than_bridging_it():
    """Eight points on one side, with a gap in the middle, is not eight
    consecutive points. Letting an absent month bridge the run manufactures
    signals out of missing data."""
    assert 4 in rules_fired(make_chart([0.3] * 8))
    assert 4 not in rules_fired(make_chart([0.3] * 4 + [None] + [0.3] * 4))


def test_a_missing_period_breaks_the_counting_rules_too():
    """Rule 4 cannot actually prove the gap logic works.

    `all(side(j) == s)` already fails on an absent point, because side() returns
    0 for None — so the rule-4 case above passes whether or not `window()`
    rejects windows containing a gap. It is the *counting* rules that depend on
    the guard: rules 2 and 3 tally qualifying points and would happily count
    across a hole, treating two months either side of a gap as consecutive.

    Each case here is the one the guard decides. Remove the None-check from
    window() and both of these fire."""
    # Rule 2 — two points beyond 2 sigma with a missing month between them are
    # not "2 of 3 consecutive".
    assert 2 not in rules_fired(make_chart([2.5, None, 2.5]))
    assert 2 in rules_fired(make_chart([2.5, 0.1, 2.5])), "sanity: fires without the gap"

    # Rule 3 — four points beyond 1 sigma spanning a gap are not "4 of 5
    # consecutive".
    assert 3 not in rules_fired(make_chart([1.2, 1.4, None, 1.3, 1.5]))
    assert 3 in rules_fired(make_chart([1.2, 1.4, 0.1, 1.3, 1.5])), \
        "sanity: fires without the gap"


def test_stable_process_produces_no_signals():
    """The negative control. Random binomial data from a genuinely stable
    process must stay silent; a rule set that fires on noise is worse than no
    rule set, because people stop reading it."""
    rng = random.Random(7)
    points = [(f"m{i}", sum(1 for _ in range(600) if rng.random() < 0.10), 600)
              for i in range(24)]
    assert western_electric(p_chart(points)) == []


def test_planted_shift_is_detected():
    """A shift has to be found, and found where it is.

    Note what is *not* asserted: that no rule ever fires before the shift. With
    24 points and four rules, the family-wise false-alarm rate is a few percent
    per chart — that is the documented cost of the Western Electric rule set,
    not a bug. The honest claim is that signals concentrate after the change
    point and that the strongest one lands there."""
    rng = random.Random(11)
    points = []
    for i in range(24):
        rate = 0.10 if i < 16 else 0.16
        points.append((f"m{i}", sum(1 for _ in range(600) if rng.random() < rate), 600))
    signals = western_electric(p_chart(points, baseline=16))
    assert signals, "a 6-point rate shift must produce a signal"

    after = [s for s in signals if s["index"] >= 16]
    before = [s for s in signals if s["index"] < 16]
    assert len(after) > len(before)
    assert all(s["direction"] == "above" for s in after)
    strongest = max(signals, key=lambda s: abs(s["z"]))
    assert strongest["index"] >= 16


def test_including_the_shift_in_the_baseline_corrupts_the_chart():
    """The error this library's `baseline=` parameter exists to prevent, shown
    happening. With the centre line computed over the whole series, the shift
    pulls it upward and the *stable* months before it get flagged as a downward
    special cause — a chart that reports a problem in the period where nothing
    happened."""
    rng = random.Random(11)
    points = []
    for i in range(24):
        rate = 0.10 if i < 16 else 0.16
        points.append((f"m{i}", sum(1 for _ in range(600) if rng.random() < rate), 600))

    contaminated = western_electric(p_chart(points))
    correct = western_electric(p_chart(points, baseline=16))

    false_alarms = [s for s in contaminated if s["index"] < 16]
    assert false_alarms, "expected the contaminated chart to misfire"
    assert all(s["direction"] == "below" for s in false_alarms)
    assert len([s for s in correct if s["index"] < 16]) < len(false_alarms)


# --------------------------------------------------------------------------
# Overdispersion and Laney's correction
# --------------------------------------------------------------------------

def test_dispersion_is_about_one_for_genuinely_binomial_data():
    rng = random.Random(3)
    points = [(f"m{i}", sum(1 for _ in range(800) if rng.random() < 0.12), 800)
              for i in range(30)]
    assert 0.7 < dispersion_factor(p_chart(points)) < 1.4


def test_clustered_counts_are_detected_as_overdispersed():
    """ALC days in miniature: each 'event' drags a random cluster of days with
    it, so the variance far exceeds Poisson. The dispersion factor has to see
    that, because it is the trigger for correcting the limits."""
    rng = random.Random(5)
    points = []
    for i in range(30):
        days = sum(int(rng.expovariate(1 / 10.0)) + 1
                   for _ in range(rng.randint(8, 20)))
        points.append((f"m{i}", days, 40.0))
    assert dispersion_factor(u_chart(points)) > 1.5


def test_laney_widens_limits_and_suppresses_false_alarms():
    rng = random.Random(5)
    points = []
    for i in range(30):
        days = sum(int(rng.expovariate(1 / 10.0)) + 1
                   for _ in range(rng.randint(8, 20)))
        points.append((f"m{i}", days, 40.0))

    naive = u_chart(points)
    corrected = u_chart(points, laney=True)

    assert corrected[0]["sigma"] > naive[0]["sigma"]
    assert len(western_electric(corrected)) < len(western_electric(naive))


def test_laney_stays_near_nominal_on_well_behaved_data_but_is_not_free():
    """What the correction actually does to binomial data — stated honestly.

    It is tempting to call this a no-op and wave `laney=True` on by default. It
    is not. On this seed sigma_z is 0.79, so the corrected limits come out ~21%
    *tighter* than the uncorrected ones, which makes the chart marginally more
    trigger-happy rather than less. The factor is not clamped at 1.0 because
    Laney's published method does not clamp it (see the module docstring).

    The assertion is therefore a band, not an equality, and the band is tight
    enough to fail if the estimator ever drifts badly — the previous version
    allowed 45% either way, which is wide enough to hide the very behaviour this
    test is supposed to document."""
    rng = random.Random(3)
    points = [(f"m{i}", sum(1 for _ in range(800) if rng.random() < 0.12), 800)
              for i in range(30)]
    naive = p_chart(points)
    corrected = p_chart(points, laney=True)
    ratio = corrected[0]["sigma"] / naive[0]["sigma"]

    assert 0.70 < ratio < 1.30, f"sigma_z drifted out of the nominal band: {ratio:.3f}"
    assert ratio < 1.0, (
        "documented behaviour: this seed is mildly underdispersed, so Laney "
        "tightens rather than widens. If this ever flips, the docstring in "
        "spc.py that warns the correction is not free needs rewriting too."
    )


# --------------------------------------------------------------------------
# The published charts
# --------------------------------------------------------------------------

def test_alc_day_counts_really_are_overdispersed_in_the_data():
    """The finding that justifies publishing the stay-level chart: measured on
    the actual data, ALC *days* disperse far more than Poisson allows."""
    rows = read("spc_alc_uchart.csv")
    assert float(rows[0]["dispersion_factor"]) > 1.5


def test_stay_level_chart_is_the_better_unit_of_analysis(cap=1.35):
    """One observation per patient removes the clustering at source rather than
    correcting for it afterwards, so the stay-level chart should be close to
    nominal dispersion."""
    stays = read("spc_alc_stay_pchart.csv")
    days = read("spc_alc_uchart.csv")
    assert float(stays[0]["dispersion_factor"]) < cap
    assert float(stays[0]["dispersion_factor"]) < float(days[0]["dispersion_factor"])


def test_the_planted_alc_shift_is_found_at_the_right_time():
    """The generator steps ALC risk up from 2026-01. The stay-level chart has
    to signal after that date, in the right direction, and promptly.

    Signals before the change point are not asserted away: some months genuinely
    ran below the centre line, and a low outlier in a stable period is a real
    special cause, not a false positive. What must be true is that everything
    after the shift points upward and that detection is not slow."""
    signals = [s for s in read("spc_signals.csv")
               if s["indicator"].startswith("Stays with any ALC")]
    assert signals, "the planted ALC shift was not detected at all"

    after = [s for s in signals if s["month"] >= "2026-01"]
    assert after, "no signal after the planted shift"
    assert all(s["direction"] == "above" for s in after)
    # Detection lag: the shift starts 2026-01, and the chart may need a couple
    # of periods of evidence before a rule closes. More than four months of
    # silence would make the chart useless operationally.
    assert min(s["month"] for s in after) <= "2026-04"
    assert len(after) > len([s for s in signals if s["month"] < "2026-01"])


def test_readmission_was_left_stable_and_the_chart_agrees():
    """No shift was planted in readmission. A chart that finds one anyway is
    reporting its own limits, not the process."""
    signals = [s for s in read("spc_signals.csv")
               if s["indicator"].startswith("30-day readmission")]
    assert len(signals) <= 2
