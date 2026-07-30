"""Tests for the privacy and data-quality layer.

Two claims are being defended here, and they are the two claims most often made
without evidence in health analytics:

  * "The data is de-identified."  -> then show me the smallest equivalence
    class, and show me that the pseudonyms still support linkage.
  * "We have data quality checks." -> then show me the gate closing.
"""

import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "governance"))

from deidentify import (  # noqa: E402
    K, QUASI_IDENTIFIERS, SAFE_HARBOR_PATTERNS, build_identified_extract,
    cap_age_band, classify_identifiers, deidentify, enforce_k_anonymity,
    pseudonymise,
)
import data_quality as dq  # noqa: E402


def read(name):
    with open(OUT / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# De-identification: direct identifiers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def deid_rows():
    return read("deidentified_abstracts.csv")


def test_no_direct_identifier_survives(deid_rows):
    """The published extract must not carry a column matching any Safe Harbor
    identifier pattern. Checked against the pattern set rather than a hard-coded
    list, so a newly added identifier column fails this test on arrival."""
    assert classify_identifiers(deid_rows[0].keys()) == {}


def test_the_source_extract_actually_contained_identifiers():
    """The control that stops the previous test from passing vacuously: if the
    input never had identifiers, removing them proves nothing."""
    found = classify_identifiers(build_identified_extract()[0].keys())
    assert {"name", "health_plan_or_record_number", "geographic_subdivision",
            "date"} <= set(found)


def test_full_dates_are_reduced_to_month(deid_rows):
    for r in deid_rows[:500]:
        assert len(r["admit_month"]) == 7
        assert "discharge_date" not in r


def test_postal_geography_is_generalised(deid_rows):
    for r in deid_rows[:500]:
        assert len(r["postal_prefix"]) <= 3


def test_ages_over_89_are_aggregated():
    assert cap_age_band("85+") == "89+"
    assert cap_age_band("45-64") == "45-64"


# --------------------------------------------------------------------------
# De-identification: pseudonymisation
# --------------------------------------------------------------------------

def test_pseudonyms_are_stable_and_distinct():
    """Stability is what preserves longitudinal linkage. Without it a patient's
    two admissions become two people and every readmission metric is wrong."""
    assert pseudonymise("MRN7000001") == pseudonymise("MRN7000001")
    assert pseudonymise("MRN7000001") != pseudonymise("MRN7000002")


def test_pseudonym_is_not_a_bare_hash_of_the_identifier():
    """An unsalted hash is not pseudonymisation. The MRN space is small enough
    to enumerate, so anyone can build a rainbow table and reverse the whole
    file in minutes."""
    import hashlib
    naive = hashlib.sha256(b"MRN7000001").hexdigest()[:16]
    assert pseudonymise("MRN7000001") != naive


def test_different_salts_produce_different_pseudonyms(monkeypatch):
    a = pseudonymise("MRN7000001")
    monkeypatch.setenv("DEID_SALT", "a-completely-different-salt")
    assert pseudonymise("MRN7000001") != a


# --------------------------------------------------------------------------
# De-identification: k-anonymity, which is the part that actually matters
# --------------------------------------------------------------------------

def test_every_equivalence_class_meets_k(deid_rows):
    classes = Counter(tuple(r[q] for q in QUASI_IDENTIFIERS) for r in deid_rows)
    assert min(classes.values()) >= K


def test_a_uniquely_identifying_combination_is_removed():
    """The concrete attack: one record with a combination nobody else shares.
    Direct-identifier removal does nothing about it; k-anonymity must."""
    rows = [{"age_band": "45-64", "postal_prefix": "V3T", "sex": "F",
             "admit_year": "2025", "program": "Medicine", "cmg_code": "140"}
            for _ in range(20)]
    rows.append({"age_band": "85+", "postal_prefix": "V9Z", "sex": "M",
                 "admit_year": "2025", "program": "Neurosciences",
                 "cmg_code": "025"})
    kept, suppressed, report = enforce_k_anonymity(rows, QUASI_IDENTIFIERS)
    assert report["unique_records_before"] == 1
    assert len(suppressed) == 1
    assert suppressed[0]["cmg_code"] == "025"


def test_generalisation_is_preferred_to_suppression(deid_rows):
    """Suppression destroys a record; generalisation keeps it and costs only
    precision. A de-identifier that reaches for deletion first quietly biases
    the dataset toward the common and the ordinary."""
    report = {r["metric"]: r["value"] for r in read("deid_risk_report.csv")}
    assert int(report["records_generalised"]) > int(report["records_suppressed"])


def test_suppression_cost_is_reported_and_bounded(deid_rows):
    """Suppression is never free, and a de-identification that does not report
    its cost cannot be argued with. It also must not be so aggressive that the
    surviving dataset no longer represents the population."""
    report = {r["metric"]: r["value"] for r in read("deid_risk_report.csv")}
    assert 0 < float(report["suppression_rate"]) < 0.15
    assert int(report["records_out"]) + int(report["records_suppressed"]) \
        == int(report["records_in"])


def test_analysis_survives_deidentification(deid_rows):
    """The failure mode nobody talks about: privacy work that leaves a dataset
    too damaged to answer the question it exists for. The de-identified extract
    must still reproduce the authority's ALC share within a small tolerance."""
    with open(ROOT / "data" / "fact_inpatient_abstracts.csv", encoding="utf-8") as f:
        source = list(csv.DictReader(f))
    src_alc = (sum(int(r["alc_days"]) for r in source)
               / sum(int(r["total_los_days"]) for r in source))
    deid_alc = (sum(int(r["alc_days"]) for r in deid_rows)
                / sum(int(r["alc_days"]) + int(r["acute_los_days"])
                      for r in deid_rows))
    assert abs(src_alc - deid_alc) < 0.02


def test_the_risk_report_leaks_nothing():
    """An audit table that records *what* was suppressed defeats the
    suppression. This one records only counts and column names."""
    text = (OUT / "deid_risk_report.csv").read_text(encoding="utf-8")
    assert "MRN" not in text
    assert "Patient " not in text


# --------------------------------------------------------------------------
# Data quality gate
# --------------------------------------------------------------------------

def test_clean_data_passes_the_gate():
    r = subprocess.run([sys.executable, str(ROOT / "governance" / "data_quality.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PUBLISH" in r.stdout


def test_the_gate_closes_on_a_duplicate_key():
    """The sabotage proof. A duplicated abstract double-counts a discharge, its
    cost, and its bed days, and the totals still look plausible — which is
    exactly why the gate has to catch it rather than a human."""
    r = subprocess.run([sys.executable, str(ROOT / "governance" / "data_quality.py"),
                        "--inject-failure"], capture_output=True, text=True)
    assert r.returncode == 2, "a critical failure must block the refresh"
    assert "BLOCKED" in r.stdout
    assert "DQ-002" in r.stdout


def test_every_rule_carries_a_severity_and_a_rationale():
    """A rule nobody can explain gets deleted the first time it fires
    inconveniently. The rationale is what makes it survivable."""
    ids = [r["id"] for r in dq.RULES]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    for rule in dq.RULES:
        assert rule["severity"] in (dq.CRITICAL, dq.WARNING)
        assert len(rule["rationale"]) > 30, rule["id"]


def test_warnings_do_not_block_the_run():
    """Halting a month-end refresh over a handful of unexpected disposition
    codes trades a data problem for an availability problem."""
    results = read("dq_results.csv")
    for r in results:
        if r["status"] == "warn":
            assert r["severity"] == dq.WARNING


def test_rule_types_are_all_implemented():
    datasets = {name: dq.load(name) for name in
                {r["dataset"] for r in dq.RULES} | {r["parent"] for r in dq.RULES
                                                    if "parent" in r}}
    for rule in dq.RULES:
        violations, scanned, detail = dq.check(rule, datasets[rule["dataset"]], datasets)
        assert isinstance(violations, int)
        assert scanned > 0
        assert detail


def test_expression_rules_do_not_execute_arbitrary_code():
    """Configuration that can run arbitrary Python is a vulnerability, not a
    feature. An unknown expression must be refused, never evaluated."""
    rule = {"id": "X", "dataset": "fact_inpatient_abstracts.csv",
            "type": "expression", "severity": dq.CRITICAL,
            "expression": "__import__('os').system('echo pwned')",
            "rationale": "x" * 40}
    with pytest.raises(ValueError):
        dq.check(rule, dq.load("fact_inpatient_abstracts.csv"), {})


# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------

def test_every_run_leaves_a_reconstructable_trail():
    """One run id must reconstruct a run end to end — start, every rule, and a
    verdict. This is the difference between a log and telemetry."""
    events = [json.loads(line) for line in
              (OUT / "dq_events.jsonl").read_text(encoding="utf-8").splitlines()]
    by_run = {}
    for e in events:
        by_run.setdefault(e["run_id"], []).append(e)

    complete = [evts for evts in by_run.values()
                if any(e["event"] == "run_finished" for e in evts)]
    assert complete, "no completed run recorded"

    for evts in complete:
        kinds = [e["event"] for e in evts]
        assert kinds[0] == "run_started"
        assert kinds[-1] == "run_finished"
        assert kinds.count("rule_evaluated") == len(dq.RULES)
        for e in evts:
            assert e["ts"] and e["run_id"]


def test_failed_runs_are_logged_too():
    """The runs worth having telemetry for are the ones that failed. A pipeline
    that only logs its successes is telling you what you already assumed."""
    events = [json.loads(line) for line in
              (OUT / "dq_events.jsonl").read_text(encoding="utf-8").splitlines()]
    verdicts = {e.get("verdict") for e in events if e["event"] == "run_finished"}
    assert "BLOCKED" in verdicts
