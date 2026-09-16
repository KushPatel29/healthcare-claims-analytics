# Decision assurance runbook

This runbook governs the synthetic Transitional Care and Community Bridging decision packet. It demonstrates a production-style release control; it does not represent an official CIHI submission, a clinical recommendation, or approval by a real health authority.

## Rebuild

```bash
python canadian/generate_activity_data.py
python engine/build_activity_metrics.py
python engine/health_economics.py
python governance/deidentify.py
python governance/data_quality.py
python governance/decision_assurance.py
pytest tests/test_decision_assurance.py -v
```

Expected release decision: **REVIEW REQUIRED**. Eight evidence gates pass; the two reviews are intentional:

- ED boarding and surgical-postponement baselines must be established before implementation.
- Executive sponsor and released-capacity backfill approvals must be recorded by humans.

## Release artefacts

| Artefact | Purpose |
|---|---|
| `health_intervention_decision_packet.md` | Board-facing recommendation, options, gates, and conditions |
| `health_measure_register.csv` | Versioned numerator, denominator, grain, exclusions, owner, and baseline state |
| `intervention_options_docket.csv` | Comparable options with readiness, cost, capacity, and equity considerations |
| `equity_monitoring.csv` | Facility × age-band outcomes with the small-cell publication rule applied |
| `evidence_release_gates.csv` | Machine-readable PASS / REVIEW / BLOCK decisions |
| `health_decision_manifest.json` | Canonical SHA-256 fingerprints for every required input and release output |
| `health_reverification_evidence.json` | Failure drill proving a critical DQ defect changes the release to BLOCKED |

## Production hand-off

1. Replace synthetic extracts with governed source views and complete privacy review.
2. Reconcile local field definitions against the organisation's licensed CIHI specifications; this portfolio crosswalk is illustrative only.
3. Confirm the small-cell threshold with the privacy office and apply complementary suppression if required.
4. Establish the two pending operational baselines before go-live.
5. Record approvals in the organisation's system of record, not by editing this repository.
6. Release phase 2 only against a pre-registered, fixed-baseline evaluation plan.

## Incident rule

Any BLOCK result stops publication. REVIEW REQUIRED can circulate for decision discussion but cannot be labelled implementation-ready. Only a fully passing gate set can become READY.
