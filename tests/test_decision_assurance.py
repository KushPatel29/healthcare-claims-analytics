"""The intervention packet must be an evidence gate, not polished prose."""

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "governance"))

import decision_assurance as da


@pytest.fixture(scope="module")
def built():
    return da.build()


def rows(name):
    with (ROOT / "output" / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_release_is_honestly_review_required(built):
    assert built["overall_status"] == "REVIEW REQUIRED"
    assert built["gates_pass"] == 8
    assert built["gates_review"] == 2
    assert built["gates_block"] == 0


def test_policy_has_a_versioned_decision_boundary():
    policy = da.load_policy()
    assert policy["policy_id"] == "HSD-REL-01"
    assert policy["policy_version"] == "1.0.0"
    assert policy["decision_id"]
    assert "Synthetic" in policy["evidence_boundary"]
    assert "not an official CIHI" in policy["evidence_boundary"]


def test_measure_ids_are_unique_and_definitions_are_operational(built):
    measures = rows("health_measure_register.csv")
    assert len(measures) == built["measures"] == 6
    assert len({m["measure_id"] for m in measures}) == len(measures)
    for measure in measures:
        assert measure["numerator"]
        assert measure["denominator"]
        assert measure["grain"]
        assert measure["owner"]
        assert measure["official_standard_claim"] == "false"


def test_missing_implementation_baselines_are_not_hidden():
    pending = [m for m in rows("health_measure_register.csv") if m["baseline_status"] == "PENDING"]
    assert {m["measure_id"] for m in pending} == {"HSD-M05", "HSD-M06"}
    gate = next(g for g in rows("evidence_release_gates.csv") if g["gate_id"] == "BASE-01")
    assert gate["status"] == "REVIEW"
    assert "HSD-M05" in gate["evidence"] and "HSD-M06" in gate["evidence"]


def test_no_human_approval_is_fabricated(built):
    assert built["open_approvals"] == 2
    gate = next(g for g in rows("evidence_release_gates.csv") if g["gate_id"] == "APP-01")
    assert gate["status"] == "REVIEW"


def test_every_required_source_is_fingerprinted():
    policy = da.load_policy()
    manifest = json.loads((ROOT / "output" / "health_decision_manifest.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in manifest["files"]}
    assert set(policy["required_sources"]) <= paths
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert len({item["path"] for item in manifest["files"]}) == len(manifest["files"])


def test_manifest_hashes_recompute_exactly():
    manifest = json.loads((ROOT / "output" / "health_decision_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        assert da.canonical_sha256(ROOT / item["path"]) == item["sha256"]


def test_activity_control_total_reconciles():
    gate = next(g for g in rows("evidence_release_gates.csv") if g["gate_id"] == "REC-01")
    assert gate["status"] == "PASS"
    assert "39,567" in gate["evidence"]


def test_spc_gate_finds_the_first_shift_month():
    gate = next(g for g in rows("evidence_release_gates.csv") if g["gate_id"] == "SPC-01")
    assert gate["status"] == "PASS"
    assert gate["evidence"].endswith("2026-01")


def test_both_costing_perspectives_are_a_release_condition():
    gate = next(g for g in rows("evidence_release_gates.csv") if g["gate_id"] == "ECON-01")
    assert gate["status"] == "PASS"
    assert "A_opportunity_cost" in gate["evidence"]
    assert "B_cash_releasing" in gate["evidence"]


def test_equity_output_covers_every_facility_and_age_band(built):
    equity = rows("equity_monitoring.csv")
    assert len(equity) == built["equity_cells"] == 36
    assert len({r["facility"] for r in equity}) == 6
    assert len({r["age_band"] for r in equity}) == 6


def test_small_cell_policy_never_publishes_a_thin_cell():
    for row in rows("equity_monitoring.csv"):
        if int(row["discharges"]) < int(row["small_cell_threshold"]):
            assert row["publication_status"] == "SUPPRESSED"
            assert row["alc_stay_rate"] == ""
            assert row["readmission_rate"] == ""
        else:
            assert row["publication_status"] == "PUBLISH"


def test_equity_rates_are_bounded():
    for row in rows("equity_monitoring.csv"):
        for field in ("alc_stay_rate", "readmission_rate"):
            if row[field]:
                assert 0 <= float(row[field]) <= 1


def test_intervention_docket_has_four_distinct_options(built):
    options = rows("intervention_options_docket.csv")
    assert len(options) == built["options"] == 4
    assert len({o["option_id"] for o in options}) == 4
    recommended = [o for o in options if o["recommendation"] == "RECOMMENDED WITH CONDITIONS"]
    assert [o["option_id"] for o in recommended] == ["OPT-02"]


def test_recommended_option_does_not_claim_a_cash_saving():
    option = next(o for o in rows("intervention_options_docket.csv") if o["option_id"] == "OPT-02")
    assert "Not a booked saving" in option["cash_case"]
    assert option["readiness"] == "CONDITIONAL"


def test_reverification_drill_blocks_without_mutating_source():
    drill = json.loads((ROOT / "output" / "health_reverification_evidence.json").read_text(encoding="utf-8"))
    assert drill["baseline_status"] == "REVIEW REQUIRED"
    assert drill["reverified_status"] == "BLOCKED"
    assert drill["source_mutated"] is False
    assert drill["observed_result"] == drill["expected_result"]


def test_overall_status_prioritises_blocks_over_reviews():
    assert da.overall_status([{"status": "PASS"}]) == "READY"
    assert da.overall_status([{"status": "REVIEW"}, {"status": "PASS"}]) == "REVIEW REQUIRED"
    assert da.overall_status([{"status": "REVIEW"}, {"status": "BLOCK"}]) == "BLOCKED"


def test_release_gate_ids_are_unique():
    gates = rows("evidence_release_gates.csv")
    assert len(gates) == 10
    assert len({gate["gate_id"] for gate in gates}) == len(gates)


def test_decision_packet_carries_the_conditions_and_boundary():
    packet = (ROOT / "output" / "health_intervention_decision_packet.md").read_text(encoding="utf-8")
    assert "**REVIEW REQUIRED**" in packet
    assert "approve with conditions" in packet.lower()
    assert "never fabricates human approval" in packet
    assert "not an official CIHI" in packet


def test_summary_is_machine_readable_and_matches_outputs(built):
    summary = json.loads((ROOT / "output" / "health_decision_summary.json").read_text(encoding="utf-8"))
    assert summary == built
    assert summary["recommendation"] == "APPROVE WITH CONDITIONS"


def test_canonical_hash_is_newline_stable(tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"one\ntwo\n")
    crlf.write_bytes(b"one\r\ntwo\r\n")
    assert da.canonical_sha256(lf) == da.canonical_sha256(crlf)
