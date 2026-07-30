"""Every number quoted in prose must still be the number the engines produce.

This file exists because the repository states a rule on its front page — *no
claim without a number, and no number without a test that fails if it stops being
true* — and, until it was written, the rule did not apply to the front page
itself. Nothing pinned the headline figures, so when the activity generator was
last changed the README kept quoting the previous run: 39,573 discharges against
an actual 39,567, and 56,768.2 weighted cases against an actual 58,727.7. The
suite stayed green throughout, because none of it was looking.

The stale weighted-case figure is the instructive one. It was not merely out of
date, it was *internally inconsistent with the line beneath it*: 496,166,175 /
56,768.2 is 8,740, not the 8,449 the README printed directly below as cost per
weighted case. Anyone who divided the two published numbers would have caught it.
That is the failure mode this file removes — a reader with a calculator should
never be the first line of defence.

Two things are checked, and both matter:

  1. The engine still produces the value. Guards against a generator or method
     change silently moving a published number.
  2. The prose still quotes it, formatted exactly as a reader sees it. Guards
     against the code moving and the documents being forgotten — which is what
     actually happened.

When a figure legitimately changes, this file fails and names the document that
needs editing. That is the intended cost: the numbers and the words move
together, or the build stops.
"""

import csv
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

README = ROOT / "README.md"
BRIEFING = ROOT / "docs" / "BRIEFING_NOTE.md"
BUSINESS = ROOT / "docs" / "BUSINESS_CASE.md"


def text(path):
    return path.read_text(encoding="utf-8")


def summary_value(path, label, cast=str):
    """Pull a labelled value out of one of the engines' summary artefacts."""
    for line in text(path).splitlines():
        if line.strip().startswith(label):
            raw = line.split(label, 1)[1].strip()
            token = raw.split()[0] if raw.split() else ""
            return cast(token.replace(",", "").replace("%", "").replace("x", ""))
    raise AssertionError(f"{path.name}: no line labelled {label!r}")


@pytest.fixture(scope="module")
def activity():
    return OUT / "activity_summary.txt"


@pytest.fixture(scope="module")
def deid():
    return OUT / "deid_summary.txt"


@pytest.fixture(scope="module")
def hta():
    return OUT / "hta_summary.txt"


@pytest.fixture(scope="module")
def abstracts():
    with open(ROOT / "data" / "fact_inpatient_abstracts.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def assert_quoted(figure, *documents):
    """The figure must appear, character for character, in every named document."""
    for doc in documents:
        assert figure in text(doc), (
            f"{doc.name} no longer quotes {figure!r}. The engine output moved and "
            f"the prose did not follow — fix the document, not this test."
        )


# --------------------------------------------------------------------------
# Activity and funding — the four indicators the README leads with
# --------------------------------------------------------------------------

def test_discharge_count_is_what_the_documents_claim(activity, abstracts):
    n = summary_value(activity, "Discharges:", int)
    assert n == len(abstracts), "summary and source data disagree on discharge count"
    assert n == 39_567
    assert_quoted("39,567", README, BRIEFING)


def test_weighted_cases_reconcile_against_cost_per_weighted_case(activity, abstracts):
    """The identity that would have caught the stale figure on its own.

    Cost per weighted case is total cost over weighted cases. If the README
    quotes all three, dividing the first two has to give the third — otherwise
    at least one of them is from a different run."""
    wc = summary_value(activity, "Weighted cases (sum RIW):", float)
    cost = summary_value(activity, "Total cost:", float)
    cpwc = summary_value(activity, "Cost per weighted case:", float)

    assert wc == pytest.approx(sum(float(r["riw"]) for r in abstracts), rel=1e-6)
    assert cost / wc == pytest.approx(cpwc, rel=5e-4), (
        f"published CPWC {cpwc:,.0f} does not reconcile to {cost:,.0f} / {wc:,.1f} "
        f"= {cost / wc:,.0f} — the three figures are not from the same run"
    )
    assert wc == pytest.approx(58_727.7, abs=0.05)
    assert_quoted("58,727.7", README)
    assert_quoted("8,449", README)


def test_cost_per_weighted_case_ex_alc_is_quoted(activity):
    ex_alc = summary_value(activity, "...excluding ALC:", float)
    assert ex_alc == pytest.approx(7_635, abs=0.5)
    assert_quoted("7,635", README)


def test_alc_and_readmission_headlines_are_quoted(activity, abstracts):
    alc_rate = summary_value(activity, "ALC rate (% of patient days):", float)
    beds = summary_value(activity, "ALC bed equivalents (24 mo):", float)
    readmit = summary_value(activity, "30-day readmission rate:", float)

    alc_days = sum(int(r["alc_days"]) for r in abstracts)
    pdays = sum(int(r["total_los_days"]) for r in abstracts)
    assert alc_rate == pytest.approx(100 * alc_days / pdays, abs=0.05)
    assert alc_rate == pytest.approx(13.8, abs=0.05)
    assert beds == pytest.approx(56.9, abs=0.05)
    assert readmit == pytest.approx(9.9, abs=0.05)

    assert_quoted("13.8%", README)
    assert_quoted("56.9", README, BRIEFING)
    assert_quoted("9.9%", README)


# --------------------------------------------------------------------------
# SPC — the overdispersion finding
# --------------------------------------------------------------------------

def test_the_dispersion_the_readme_quotes_is_the_one_the_chart_applied():
    """The README says 4.6x. The chart has to have actually used 4.6x.

    These were two different numbers: the summary reported the whole-series
    estimate (4.78) while the published chart corrected by the baseline-only
    figure (4.60). Both are defensible quantities; quoting one beside a chart
    drawn with the other is not."""
    with open(OUT / "spc_alc_uchart.csv", encoding="utf-8") as f:
        applied = float(next(csv.DictReader(f))["dispersion_factor"])
    assert applied == pytest.approx(4.60, abs=0.01)
    assert "**4.6×**" in text(README) or "4.6×" in text(README), \
        "README no longer quotes the applied dispersion factor"


def test_signal_counts_and_month_counts_are_quoted():
    """Both numbers, because one without the other is misleading.

    41 point-signals is a count of rule firings, not of months in trouble: a
    point beyond 3 sigma also trips the 2-sigma and 1-sigma rules. The number an
    analyst works is the month count."""
    with open(OUT / "spc_signals.csv", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["indicator"]]

    uprime = [r for r in rows if r["indicator"].startswith("ALC days")]
    assert len(uprime) == 10
    assert len({r["month"] for r in uprime}) == 4

    body = text(README)
    assert "41 point-signals" in body and "19 of 24 months" in body
    assert "10 signals in 4 months" in body


def test_stay_level_dispersion_is_quoted_as_measured():
    """The README claimed 1.05 for a long time. It is 1.18."""
    with open(OUT / "spc_alc_stay_pchart.csv", encoding="utf-8") as f:
        stays = float(next(csv.DictReader(f))["dispersion_factor"])
    assert stays == pytest.approx(1.18, abs=0.01)
    assert "1.18" in text(README)


def test_readmission_chart_is_still_silent():
    """No shift was planted in readmission; the README says the chart says
    nothing. If that stops being true the claim has to change."""
    with open(OUT / "spc_signals.csv", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["indicator"].startswith("30-day")]
    assert rows == []


# --------------------------------------------------------------------------
# Health economics — the two perspectives
# --------------------------------------------------------------------------

def test_both_costing_perspectives_are_quoted(hta):
    with open(OUT / "hta_base_case.csv", encoding="utf-8") as f:
        base = {r["perspective"]: r for r in csv.DictReader(f)}

    a = float(base["A_opportunity_cost"]["incremental_cost"])
    b = float(base["B_cash_releasing"]["incremental_cost"])
    icer_b = float(base["B_cash_releasing"]["icer"])

    assert a == pytest.approx(-2_916_613, abs=1)
    assert b == pytest.approx(658_980, abs=1)
    assert icer_b == pytest.approx(192_163, abs=1)
    assert base["A_opportunity_cost"]["dominant"] == "True"
    assert base["B_cash_releasing"]["dominant"] == "False", \
        "perspective B must not be dominant — the whole argument rests on it not being"

    assert_quoted("2,916,613", README, BUSINESS)
    assert_quoted("658,980", README, BUSINESS)
    assert_quoted("192,163", README, BUSINESS)


def test_psa_probabilities_are_quoted(hta):
    p_a = summary_value(hta, "P(cost-effective @ $50,000/QALY), perspective A:", float)
    p_b = summary_value(hta, "P(cost-effective @ $50,000/QALY), perspective B:", float)
    assert p_a == pytest.approx(99.7, abs=0.05)
    assert p_b == pytest.approx(16.9, abs=0.05)
    assert_quoted("99.7", README, BRIEFING, BUSINESS)
    assert_quoted("16.9", README, BUSINESS)


# --------------------------------------------------------------------------
# Governance — the de-identification cost
# --------------------------------------------------------------------------

def test_deidentification_figures_are_quoted(deid):
    unique = summary_value(deid, "Unique on quasi-identifiers:", int)
    generalised = summary_value(deid, "Records generalised:", int)
    suppressed = summary_value(deid, "Records suppressed:", int)
    smallest = summary_value(deid, "Smallest equivalence class:", int)

    assert (unique, generalised, suppressed) == (449, 3_390, 1_405)
    assert smallest == 5, "k-anonymity target breached"

    body = text(README)
    for figure in ("449 (1.14%)", "3,390", "1,405 (3.55%)"):
        assert figure in body, f"README no longer quotes {figure!r}"


def test_suppression_percentages_match_their_own_counts(deid):
    """A percentage quoted beside a count has to be that count over the total."""
    records_in = summary_value(deid, "Records in:", int)
    suppressed = summary_value(deid, "Records suppressed:", int)
    pct = re.search(r"Records suppressed:\s+[\d,]+\s+\(([\d.]+)%\)", text(deid))
    assert pct, "suppression line no longer carries a percentage"
    assert float(pct.group(1)) == pytest.approx(100 * suppressed / records_in, abs=0.02)


# --------------------------------------------------------------------------
# The badge
# --------------------------------------------------------------------------

def test_the_test_count_on_the_badge_is_the_real_test_count():
    """A badge claiming a number of tests is a claim like any other."""
    badge = re.search(r"tests-(\d+)%20passing", text(README))
    assert badge, "README no longer carries a test-count badge"
    claimed = int(badge.group(1))

    actual = sum(
        len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M))
        for p in sorted((ROOT / "tests").glob("test_*.py"))
    )
    assert claimed == actual, (
        f"badge claims {claimed} tests, the suite defines {actual}. "
        f"Update the badge in README.md."
    )
