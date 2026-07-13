"""
Invariants for the synthetic claims dataset and the RCM metrics engine.

If any of these break, the dashboard lies — so CI regenerates the data,
rebuilds the metrics, and re-proves them on every push.
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dataset():
    subprocess.run([sys.executable, str(ROOT / "data_generator" / "generate_claims_data.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "engine" / "build_rcm_metrics.py")], check=True)
    read = lambda p: list(csv.DictReader(open(p, encoding="utf-8")))
    return {
        "claims": read(ROOT / "data" / "fact_claims.csv"),
        "payers": read(ROOT / "data" / "dim_payer.csv"),
        "providers": read(ROOT / "data" / "dim_provider.csv"),
        "service_lines": read(ROOT / "data" / "dim_service_line.csv"),
        "ar": read(ROOT / "output" / "ar_aging.csv"),
    }


def test_referential_integrity(dataset):
    payer_ids = {r["payer_id"] for r in dataset["payers"]}
    provider_ids = {r["provider_id"] for r in dataset["providers"]}
    sl_ids = {r["service_line_id"] for r in dataset["service_lines"]}
    for c in dataset["claims"]:
        assert c["payer_id"] in payer_ids
        assert c["provider_id"] in provider_ids
        assert c["service_line_id"] in sl_ids


def test_financial_ordering(dataset):
    """paid <= allowed <= submitted on every paid claim — the arithmetic a
    payment-posting audit checks."""
    for c in dataset["claims"]:
        if c["status"] == "Paid":
            s, a, p = (float(c["submitted_amount"]), float(c["allowed_amount"]),
                       float(c["paid_amount"]))
            assert p <= a + 0.01 and a <= s + 0.01, c["claim_id"]


def test_status_consistency(dataset):
    for c in dataset["claims"]:
        if c["status"] == "Denied":
            assert c["denial_reason"], f"{c['claim_id']} denied without a CARC reason"
            assert float(c["paid_amount"]) == 0.0
        elif c["status"] == "Paid":
            assert not c["denial_reason"]
        elif c["status"] == "Pending":
            assert not c["adjudicated_date"]
            assert c["ar_bucket"], f"{c['claim_id']} pending without an AR bucket"


def test_denial_rate_plausible(dataset):
    adjudicated = [c for c in dataset["claims"] if c["status"] in ("Paid", "Denied")]
    denied = [c for c in adjudicated if c["status"] == "Denied"]
    rate = len(denied) / len(adjudicated)
    assert 0.05 <= rate <= 0.15, f"denial rate {rate:.1%} outside industry-plausible band"


def test_ar_snapshot_ties_to_claims(dataset):
    """Every dollar in the AR aging output must tie back to a pending claim —
    control-total thinking applied to AR."""
    pending_total = sum(float(c["submitted_amount"]) for c in dataset["claims"]
                        if c["status"] == "Pending")
    ar_total = sum(float(r["submitted_amount"]) for r in dataset["ar"])
    assert abs(pending_total - ar_total) < 0.01
