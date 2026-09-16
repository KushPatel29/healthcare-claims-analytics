"""Invariants for the front-end denial-prevention and patient-collection layer."""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def read(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def claims():
    return read(DATA / "fact_claims.csv")


@pytest.fixture(scope="module")
def summary():
    return json.loads((OUT / "denial_prevention.json").read_text(encoding="utf-8"))


def test_claim_book_is_two_years_and_unique(claims):
    assert len(claims) == 30_000
    assert len({row["claim_id"] for row in claims}) == len(claims)
    # A rolling 730-day service window touches 25 calendar-month labels because
    # the first and last months are partial. Submission-month reporting remains
    # the intended 24 complete months.
    assert len({row["service_date"][:7] for row in claims}) == 25


def test_every_claim_has_workable_procedure_and_encounter_context(claims):
    for row in claims:
        assert row["cpt_code"] and row["procedure"]
        assert row["encounter_type"] and row["facility"]


def test_paid_amount_reconciles_to_payer_and_patient_cash(claims):
    for row in claims:
        if row["status"] == "Paid":
            assert float(row["paid_amount"]) == pytest.approx(
                float(row["payer_paid_amount"]) + float(row["patient_paid_amount"]), abs=0.011
            )


def test_denials_have_a_root_cause_and_owner(claims):
    denied = [row for row in claims if row["status"] == "Denied"]
    assert denied
    for row in denied:
        assert row["denial_reason"] and row["denial_category"]
        assert row["denial_stage"] in {"Front end", "Mid cycle", "Back end", "Patient"}
        assert row["denial_preventable"] in {"0", "1"}


def test_prior_authorization_denials_require_a_missing_authorization(claims):
    auth = [row for row in claims if row["denial_category"] == "Authorization"]
    assert auth
    assert all(row["prior_auth_required"] == "1" for row in auth)
    assert all(row["prior_auth_obtained"] == "0" for row in auth)


def test_preventable_denials_are_material_but_not_every_denial(summary):
    assert summary["preventable_denials"] == 2321
    assert summary["preventable_share"] == pytest.approx(0.6863)
    assert summary["preventable_contract_value"] == pytest.approx(4_844_248.21)
    assert 0 < summary["preventable_denials"] < summary["denials"]


def test_funnel_ladder_is_monotonic():
    ladder = [row for row in read(OUT / "claim_funnel.csv") if row["kind"] == "ladder"]
    assert [int(row["claims"]) for row in ladder] == sorted(
        (int(row["claims"]) for row in ladder), reverse=True
    )
    assert [float(row["contract_value"]) for row in ladder] == sorted(
        (float(row["contract_value"]) for row in ladder), reverse=True
    )


def test_funnel_starts_at_the_claim_control_total(claims):
    first = read(OUT / "claim_funnel.csv")[0]
    assert int(first["claims"]) == len(claims)
    assert float(first["share_of_created"]) == 1.0


def test_clean_claim_rate_improves_after_the_planted_scrubber_change(summary):
    assert summary["clean_claim_rate_second_half"] > summary["clean_claim_rate_first_half"]
    assert summary["clean_claim_rate_second_half"] - summary["clean_claim_rate_first_half"] > 0.02


def test_authorization_chart_uses_a_fixed_baseline():
    chart = read(OUT / "auth_denial_pchart.csv")
    assert len(chart) == 24
    assert len({row["centre"] for row in chart}) == 1
    assert float(chart[0]["centre"]) == pytest.approx(0.0267)


def test_authorization_rule_change_is_localised_and_detected(summary):
    assert summary["watched_cell"] == "UnitedHealthcare / Cardiology"
    assert summary["authorization_first_signal"] == "2025-10"
    assert summary["authorization_longest_run"] == 6


def test_flagged_months_are_exactly_the_chart_flags(summary):
    flagged = [row["month"] for row in read(OUT / "auth_denial_pchart.csv") if row["signal"] == "1"]
    assert ", ".join(flagged) == summary["authorization_flagged_months"]


def test_payer_scorecard_has_one_row_per_payer():
    scorecard = read(OUT / "payer_scorecard.csv")
    payers = read(DATA / "dim_payer.csv")
    assert len(scorecard) == len(payers) == 11
    assert {row["payer_name"] for row in scorecard} == {row["payer_name"] for row in payers}


def test_payer_scorecard_rates_are_bounded():
    fields = ["denial_rate", "preventable_denial_rate", "authorization_denial_rate",
              "clean_claim_rate", "first_pass_rate", "net_collection_rate",
              "patient_share_of_allowed", "patient_collection_rate"]
    for row in read(OUT / "payer_scorecard.csv"):
        for field in fields:
            assert 0 <= float(row[field]) <= 1, (row["payer_name"], field)


def test_patient_collection_summary_reconciles_to_claims(claims, summary):
    paid = [row for row in claims if row["status"] == "Paid"]
    owed = sum(float(row["patient_responsibility"]) for row in paid)
    collected = sum(float(row["patient_paid_amount"]) for row in paid)
    assert summary["patient_responsibility"] == pytest.approx(owed, abs=0.02)
    assert summary["patient_collected"] == pytest.approx(collected, abs=0.02)
    assert summary["patient_collection_rate"] == pytest.approx(collected / owed, abs=0.0001)


def test_patient_collection_rollup_reconciles_to_summary(summary):
    rows = read(OUT / "patient_collections.csv")
    assert sum(float(row["patient_responsibility"]) for row in rows) == pytest.approx(
        summary["patient_responsibility"], abs=0.05
    )
    assert sum(float(row["patient_collected"]) for row in rows) == pytest.approx(
        summary["patient_collected"], abs=0.05
    )


def test_root_cause_rollup_reconciles_to_denial_count(summary):
    rows = read(OUT / "denial_root_cause.csv")
    assert sum(int(row["denials"]) for row in rows) == summary["denials"]
    assert sum(int(row["preventable_denials"]) for row in rows) == summary["preventable_denials"]


def test_authorization_is_the_largest_preventable_category(summary):
    assert summary["biggest_preventable_category"] == "Authorization"
    assert summary["biggest_preventable_value"] == pytest.approx(1_549_968.73)


def test_rejection_reason_is_present_only_when_rejected(claims):
    for row in claims:
        if row["clearinghouse_rejected"] == "1":
            assert row["rejection_reason"] and int(row["days_to_correct"]) > 0
        else:
            assert row["rejection_reason"] == ""


def test_patient_writeoffs_do_not_exceed_patient_responsibility(claims):
    for row in claims:
        assert float(row["patient_write_off"] or 0) <= float(row["patient_responsibility"] or 0) + 0.011


def test_follow_up_touches_are_nonnegative_integers(claims):
    assert all(int(row["follow_up_touches"]) >= 0 for row in claims)


def test_every_denial_category_appears_in_the_root_cause_output(claims):
    expected = {row["denial_category"] for row in claims if row["status"] == "Denied"}
    actual = {row["denial_category"] for row in read(OUT / "denial_root_cause.csv")}
    assert actual == expected


def test_procedure_dimension_covers_every_claim(claims):
    procedures = {row["cpt_code"] for row in read(DATA / "dim_procedure.csv")}
    assert procedures == {row["cpt_code"] for row in claims}


def test_denial_reason_dimension_covers_every_denied_claim(claims):
    reasons = {row["denial_reason"] for row in read(DATA / "dim_denial_reason.csv")}
    claim_reasons = {row["denial_reason"] for row in claims if row["status"] == "Denied"}
    assert claim_reasons <= reasons


def test_root_cause_grain_is_unique():
    rows = read(OUT / "denial_root_cause.csv")
    keys = [(row["month"], row["denial_category"], row["denial_stage"]) for row in rows]
    assert len(keys) == len(set(keys))


def test_claim_status_control_total_is_complete(claims):
    counts = Counter(row["status"] for row in claims)
    assert set(counts) == {"Paid", "Denied", "Pending"}
    assert sum(counts.values()) == 30_000
