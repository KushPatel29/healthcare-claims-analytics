# Health System Intervention Decision Packet

**Decision:** Transitional care and community bridging program  
**Decision ID:** `ALC-TRANSITION-2026-01`  
**Policy:** `HSD-REL-01 v1.0.0`  
**Evidence as of:** 2026-07-30  
**Release decision:** **REVIEW REQUIRED**

> Synthetic portfolio evidence only. This is a CIHI-inspired decision-support model, not an official CIHI specification, submission, certification, or clinical recommendation.

## Executive decision

The analytical recommendation remains **approve with conditions**: staged authority-wide transitional care, presented as a capacity-and-access initiative. The evidence is not released as implementation-ready because two outcome baselines and two human approvals are intentionally still open.

## Release gates

| Gate | Status | Evidence |
|---|---|---|
| SRC-01 — Evidence inventory complete | **PASS** | 11/11 required sources present |
| DQ-01 — Critical data-quality rules pass | **PASS** | 0 critical failure(s); injected drill=False |
| PRIV-01 — Privacy risk remains within policy | **PASS** | k=5; suppression=3.55% |
| REC-01 — Activity control total reconciles | **PASS** | facility rollup=39,567; abstracts=39,567 |
| SPC-01 — Planted ALC shift is detected prospectively | **PASS** | first month above the fixed upper limit: 2026-01 |
| ECON-01 — Both economic perspectives are published | **PASS** | perspectives=A_opportunity_cost, B_cash_releasing |
| UNC-01 — Decision uncertainty is quantified | **PASS** | 10,000-iteration PSA includes the $50,000/QALY decision point |
| EQ-01 — Equity cells apply the small-cell rule | **PASS** | 36 facility x age-band cells; threshold=20; 0 suppressed |
| BASE-01 — Implementation baselines are established | **REVIEW** | pending production baselines: HSD-M05, HSD-M06 |
| APP-01 — Required human approvals are recorded | **REVIEW** | open: Executive sponsor decision, Released-capacity backfill commitment |

## Options docket

| Option | Annual cost | Capacity | Readiness | Recommendation |
|---|---:|---:|---|---|
| OPT-01 — Status quo | $0 | 0 beds | READY | **NOT RECOMMENDED** |
| OPT-02 — Authority-wide transitional care | $1,450,000 | 12.5 beds | CONDITIONAL | **RECOMMENDED WITH CONDITIONS** |
| OPT-03 — Harbourview phase only | $400,000 | 2.3 beds | READY | **PHASE-1 ALTERNATIVE** |
| OPT-04 — Purchase residential capacity | NOT COSTED | NOT MODELLED | NOT READY | **LONG-TERM PATH** |

## Conditions before implementation

1. Record the executive sponsor decision; this repository never fabricates human approval.
2. Sign the released-capacity backfill commitment with Clinical Operations and Finance.
3. Establish ED boarding-hours and surgical-postponement baselines before go-live.
4. Continue facility × age-band monitoring under the documented small-cell rule.
5. Release phase 2 only after the fixed-baseline SPC evaluation shows a sustained improvement.

## Audit boundary

Every gate is machine-readable in `evidence_release_gates.csv`; the measure definitions are in `health_measure_register.csv`; source and output fingerprints are in `health_decision_manifest.json`. The re-verification drill proves that a critical data-quality failure changes the decision to **BLOCKED** without editing source evidence.
