"""Build the board decision docket and its auditable release decision.

This layer does not invent a stronger recommendation. It assembles the existing
activity, SPC, health-economics, privacy, and data-quality evidence; makes the
measure definitions and missing baselines explicit; and refuses to represent a
human approval that has not happened.

Pure standard library by design. Run from any working directory:

    python governance/decision_assurance.py
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
POLICY_PATH = ROOT / "governance" / "decision_policy.json"


def load_policy(path: Path = POLICY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows and not fieldnames:
        raise ValueError(f"cannot infer columns for empty output: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def canonical_sha256(path: Path) -> str:
    """Hash text evidence independent of CRLF/LF checkout behaviour."""
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def measure_register(policy: dict) -> list[dict]:
    rows = []
    for measure in policy["measures"]:
        rows.append({
            **measure,
            "policy_id": policy["policy_id"],
            "policy_version": policy["policy_version"],
            "official_standard_claim": "false",
        })
    return rows


def equity_monitoring(policy: dict) -> list[dict]:
    """Publish facility x age-band outcomes with a production small-cell rule."""
    abstracts = read_csv(ROOT / "data" / "fact_inpatient_abstracts.csv")
    facilities = {
        row["facility_id"]: row["facility_name"]
        for row in read_csv(ROOT / "data" / "dim_facility.csv")
    }
    threshold = int(policy["small_cell_threshold"])
    cells: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"discharges": 0, "alc_stays": 0, "readmissions": 0}
    )
    for row in abstracts:
        cell = cells[(facilities[row["facility_id"]], row["age_band"])]
        cell["discharges"] += 1
        cell["alc_stays"] += int(row["alc_days"]) > 0
        cell["readmissions"] += int(row["readmit_30d"])

    rows = []
    for (facility, age_band), cell in sorted(cells.items()):
        suppressed = cell["discharges"] < threshold
        rows.append({
            "facility": facility,
            "age_band": age_band,
            "discharges": cell["discharges"],
            "alc_stay_rate": "" if suppressed else round(cell["alc_stays"] / cell["discharges"], 4),
            "readmission_rate": "" if suppressed else round(cell["readmissions"] / cell["discharges"], 4),
            "small_cell_threshold": threshold,
            "publication_status": "SUPPRESSED" if suppressed else "PUBLISH",
            "interpretation": "Descriptive synthetic monitoring only; no causal or clinical inference.",
        })
    return rows


def intervention_docket() -> list[dict]:
    return [
        {
            "option_id": "OPT-01", "option": "Status quo", "annual_cost": 0,
            "capacity_released_beds": 0, "cash_case": "No investment; upward ALC shift remains",
            "equity_consideration": "Existing access pressure persists", "readiness": "READY",
            "recommendation": "NOT RECOMMENDED",
        },
        {
            "option_id": "OPT-02", "option": "Authority-wide transitional care",
            "annual_cost": 1450000, "capacity_released_beds": 12.5,
            "cash_case": "Not a booked saving without a bed-closure decision",
            "equity_consideration": "Authority-wide access with facility and age-band monitoring",
            "readiness": "CONDITIONAL", "recommendation": "RECOMMENDED WITH CONDITIONS",
        },
        {
            "option_id": "OPT-03", "option": "Harbourview phase only", "annual_cost": 400000,
            "capacity_released_beds": 2.3, "cash_case": "Lower exposure; captures only part of the opportunity",
            "equity_consideration": "May shift pressure to neighbouring sites", "readiness": "READY",
            "recommendation": "PHASE-1 ALTERNATIVE",
        },
        {
            "option_id": "OPT-04", "option": "Purchase residential capacity", "annual_cost": "NOT COSTED",
            "capacity_released_beds": "NOT MODELLED", "cash_case": "Requires a separate commissioning case",
            "equity_consideration": "Placement access and geography require assessment", "readiness": "NOT READY",
            "recommendation": "LONG-TERM PATH",
        },
    ]


def release_gates(policy: dict, *, inject_dq_failure: bool = False) -> list[dict]:
    required = [ROOT / path for path in policy["required_sources"]]
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.exists()]
    dq = read_csv(OUT / "dq_results.csv")
    critical_failures = sum(
        row["severity"] == "critical" and row["status"] != "pass" for row in dq
    ) + int(inject_dq_failure)
    risk = {row["metric"]: row["value"] for row in read_csv(OUT / "deid_risk_report.csv")}
    abstracts = read_csv(ROOT / "data" / "fact_inpatient_abstracts.csv")
    facilities = read_csv(OUT / "activity_by_facility.csv")
    spc = read_csv(OUT / "spc_alc_stay_pchart.csv")
    first_shift = next(
        (row["month"] for row in spc if row["rate"] and float(row["rate"]) > float(row["ucl"])),
        "NONE",
    )
    economics = read_csv(OUT / "hta_base_case.csv")
    perspectives = {row["perspective"] for row in economics}
    ceac = read_csv(OUT / "hta_psa_ceac.csv")
    wtp_50k = next((row for row in ceac if row["willingness_to_pay"] == "50000"), None)
    equity = equity_monitoring(policy)
    bad_equity_cells = [
        row for row in equity
        if row["publication_status"] == "PUBLISH"
        and row["discharges"] < int(policy["small_cell_threshold"])
    ]
    pending_measures = [m["measure_id"] for m in policy["measures"] if m["baseline_status"] != "ESTABLISHED"]
    open_approvals = [a["approval"] for a in policy["approvals"] if a["status"] != "APPROVED"]

    gates = [
        ("SRC-01", "Evidence inventory complete", "BLOCK" if missing else "PASS",
         f"{len(required) - len(missing)}/{len(required)} required sources present" + (f"; missing: {', '.join(missing)}" if missing else "")),
        ("DQ-01", "Critical data-quality rules pass", "BLOCK" if critical_failures else "PASS",
         f"{critical_failures} critical failure(s); injected drill={inject_dq_failure}"),
        ("PRIV-01", "Privacy risk remains within policy", "PASS" if int(risk["min_equivalence_class_after"]) >= int(risk["k_target"]) and float(risk["suppression_rate"]) <= 0.05 else "BLOCK",
         f"k={risk['min_equivalence_class_after']}; suppression={float(risk['suppression_rate']):.2%}"),
        ("REC-01", "Activity control total reconciles", "PASS" if sum(int(row["discharges"]) for row in facilities) == len(abstracts) else "BLOCK",
         f"facility rollup={sum(int(row['discharges']) for row in facilities):,}; abstracts={len(abstracts):,}"),
        ("SPC-01", "Planted ALC shift is detected prospectively", "PASS" if first_shift == "2026-01" else "BLOCK",
         f"first month above the fixed upper limit: {first_shift}"),
        ("ECON-01", "Both economic perspectives are published", "PASS" if perspectives == {"A_opportunity_cost", "B_cash_releasing"} else "BLOCK",
         f"perspectives={', '.join(sorted(perspectives))}"),
        ("UNC-01", "Decision uncertainty is quantified", "PASS" if wtp_50k else "BLOCK",
         "10,000-iteration PSA includes the $50,000/QALY decision point" if wtp_50k else "$50,000/QALY decision point missing"),
        ("EQ-01", "Equity cells apply the small-cell rule", "BLOCK" if bad_equity_cells else "PASS",
         f"{len(equity)} facility x age-band cells; threshold={policy['small_cell_threshold']}; {sum(r['publication_status'] == 'SUPPRESSED' for r in equity)} suppressed"),
        ("BASE-01", "Implementation baselines are established", "PASS" if not pending_measures else "REVIEW",
         f"pending production baselines: {', '.join(pending_measures) if pending_measures else 'none'}"),
        ("APP-01", "Required human approvals are recorded", "PASS" if not open_approvals else "REVIEW",
         f"open: {', '.join(open_approvals) if open_approvals else 'none'}"),
    ]
    return [
        {"gate_id": gate_id, "gate": name, "status": status, "evidence": evidence}
        for gate_id, name, status, evidence in gates
    ]


def overall_status(gates: list[dict]) -> str:
    statuses = {gate["status"] for gate in gates}
    if "BLOCK" in statuses:
        return "BLOCKED"
    if "REVIEW" in statuses:
        return "REVIEW REQUIRED"
    return "READY"


def render_packet(policy: dict, summary: dict, gates: list[dict], options: list[dict]) -> str:
    lines = [
        "# Health System Intervention Decision Packet",
        "",
        f"**Decision:** {policy['decision_title']}  ",
        f"**Decision ID:** `{policy['decision_id']}`  ",
        f"**Policy:** `{policy['policy_id']} v{policy['policy_version']}`  ",
        f"**Evidence as of:** {policy['as_of_date']}  ",
        f"**Release decision:** **{summary['overall_status']}**",
        "",
        f"> {policy['evidence_boundary']}",
        "",
        "## Executive decision",
        "",
        "The analytical recommendation remains **approve with conditions**: staged authority-wide transitional care, presented as a capacity-and-access initiative. The evidence is not released as implementation-ready because two outcome baselines and two human approvals are intentionally still open.",
        "",
        "## Release gates",
        "",
        "| Gate | Status | Evidence |",
        "|---|---|---|",
    ]
    lines.extend(f"| {g['gate_id']} — {g['gate']} | **{g['status']}** | {g['evidence']} |" for g in gates)
    lines.extend(["", "## Options docket", "", "| Option | Annual cost | Capacity | Readiness | Recommendation |", "|---|---:|---:|---|---|"])
    for option in options:
        cost = f"${option['annual_cost']:,.0f}" if isinstance(option["annual_cost"], (int, float)) else option["annual_cost"]
        capacity = f"{option['capacity_released_beds']} beds" if isinstance(option["capacity_released_beds"], (int, float)) else option["capacity_released_beds"]
        lines.append(f"| {option['option_id']} — {option['option']} | {cost} | {capacity} | {option['readiness']} | **{option['recommendation']}** |")
    lines.extend([
        "", "## Conditions before implementation", "",
        "1. Record the executive sponsor decision; this repository never fabricates human approval.",
        "2. Sign the released-capacity backfill commitment with Clinical Operations and Finance.",
        "3. Establish ED boarding-hours and surgical-postponement baselines before go-live.",
        "4. Continue facility × age-band monitoring under the documented small-cell rule.",
        "5. Release phase 2 only after the fixed-baseline SPC evaluation shows a sustained improvement.",
        "", "## Audit boundary", "",
        "Every gate is machine-readable in `evidence_release_gates.csv`; the measure definitions are in `health_measure_register.csv`; source and output fingerprints are in `health_decision_manifest.json`. The re-verification drill proves that a critical data-quality failure changes the decision to **BLOCKED** without editing source evidence.",
        "",
    ])
    return "\n".join(lines)


def build() -> dict:
    OUT.mkdir(exist_ok=True)
    policy = load_policy()
    measures = measure_register(policy)
    equity = equity_monitoring(policy)
    options = intervention_docket()
    gates = release_gates(policy)
    status = overall_status(gates)
    summary = {
        "decision_id": policy["decision_id"],
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "as_of_date": policy["as_of_date"],
        "overall_status": status,
        "gates_pass": sum(g["status"] == "PASS" for g in gates),
        "gates_review": sum(g["status"] == "REVIEW" for g in gates),
        "gates_block": sum(g["status"] == "BLOCK" for g in gates),
        "measures": len(measures),
        "pending_measure_baselines": sum(m["baseline_status"] != "ESTABLISHED" for m in measures),
        "options": len(options),
        "equity_cells": len(equity),
        "small_cell_threshold": policy["small_cell_threshold"],
        "open_approvals": sum(a["status"] != "APPROVED" for a in policy["approvals"]),
        "recommendation": "APPROVE WITH CONDITIONS",
    }

    write_csv(OUT / "health_measure_register.csv", measures)
    write_csv(OUT / "equity_monitoring.csv", equity)
    write_csv(OUT / "intervention_options_docket.csv", options)
    write_csv(OUT / "evidence_release_gates.csv", gates)
    (OUT / "health_decision_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (OUT / "health_intervention_decision_packet.md").write_text(
        render_packet(policy, summary, gates, options), encoding="utf-8"
    )

    injected = release_gates(policy, inject_dq_failure=True)
    drill = {
        "scenario": "A duplicate grain key triggers one critical DQ failure in memory",
        "source_mutated": False,
        "baseline_status": status,
        "reverified_status": overall_status(injected),
        "changed_gate": "DQ-01",
        "expected_result": "BLOCKED",
        "observed_result": overall_status(injected),
    }
    (OUT / "health_reverification_evidence.json").write_text(json.dumps(drill, indent=2) + "\n", encoding="utf-8")

    manifest_paths = [ROOT / path for path in policy["required_sources"]] + [
        POLICY_PATH,
        OUT / "health_measure_register.csv",
        OUT / "equity_monitoring.csv",
        OUT / "intervention_options_docket.csv",
        OUT / "evidence_release_gates.csv",
        OUT / "health_decision_summary.json",
        OUT / "health_intervention_decision_packet.md",
        OUT / "health_reverification_evidence.json",
    ]
    manifest = {
        "decision_id": policy["decision_id"],
        "policy_version": policy["policy_version"],
        "hash_algorithm": "SHA-256 after CRLF-to-LF normalization",
        "files": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": canonical_sha256(path), "bytes": path.stat().st_size}
            for path in manifest_paths
        ],
    }
    (OUT / "health_decision_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    result = build()
    print(f"{result['overall_status']}: {result['gates_pass']} pass / {result['gates_review']} review / {result['gates_block']} block")
