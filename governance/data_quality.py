"""
Data quality gate and pipeline observability for the health-authority extracts.

Two things a reporting pipeline owes its consumers, neither of which is a
dashboard:

  1. **A gate.** Bad data must not reach a published report. Not "be flagged in
     a report nobody opens" — *not reach it*. Critical failures halt the run
     with a non-zero exit code, which is the signal any scheduler (Fabric
     pipeline, Airflow, SQL Agent, Power Automate) turns into a blocked refresh
     and a page. Warnings are recorded and let the run proceed, because halting
     a month-end refresh over three malformed postal codes trades a data
     problem for an availability problem.

  2. **A trail.** Every run appends structured JSONL events — run id, rule,
     status, rows scanned, duration, severity — to an ops log that Datadog or
     Azure Monitor can tail without transformation. One run id reconstructs any
     run end to end, including the runs that failed.

Rules are declarative data, not code. That matters more than it sounds: adding
a check to a governed dataset should be a reviewed one-line change that a data
steward can read, not a pull request against a Python module that only the
author understands.

Usage:
    python governance/data_quality.py
    python governance/data_quality.py --inject-failure   # prove the gate closes
"""

import argparse
import csv
import json
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

CRITICAL, WARNING = "critical", "warning"

# Freshness service level: how stale the newest discharge is allowed to be
# before the extract is considered late. Expressed against the snapshot date
# the whole repository is anchored to.
SNAPSHOT_DATE = date(2026, 7, 1)
FRESHNESS_SLA_DAYS = 45

# ---------------------------------------------------------------------------
# The rule set. This is the artefact a data steward reviews.
# ---------------------------------------------------------------------------
RULES = [
    # --- structural: if these fail nothing downstream is meaningful ---------
    {"id": "DQ-001", "dataset": "fact_inpatient_abstracts.csv", "type": "not_null",
     "column": "abstract_id", "severity": CRITICAL,
     "rationale": "The grain of the fact table. A null grain key means rows "
                  "cannot be counted once."},
    {"id": "DQ-002", "dataset": "fact_inpatient_abstracts.csv", "type": "unique",
     "column": "abstract_id", "severity": CRITICAL,
     "rationale": "A duplicated abstract double-counts a discharge, its cost, "
                  "and its bed days — the single most expensive silent error "
                  "in activity reporting."},
    {"id": "DQ-003", "dataset": "fact_inpatient_abstracts.csv",
     "type": "referential", "column": "facility_id",
     "parent": "dim_facility.csv", "parent_column": "facility_id",
     "severity": CRITICAL,
     "rationale": "An orphan facility key drops the row out of every "
                  "site-level report while leaving the authority total intact "
                  "— the totals still tie, so nobody notices."},
    {"id": "DQ-004", "dataset": "fact_inpatient_abstracts.csv",
     "type": "referential", "column": "cmg_code",
     "parent": "dim_cmg.csv", "parent_column": "cmg_code", "severity": CRITICAL,
     "rationale": "An unmapped CMG has no RIW, so the case silently carries "
                  "zero weight in cost per weighted case."},

    # --- clinical and business logic ---------------------------------------
    {"id": "DQ-010", "dataset": "fact_inpatient_abstracts.csv", "type": "range",
     "column": "acute_los_days", "min": 1, "max": 365, "severity": CRITICAL,
     "rationale": "A stay of zero or negative days is a data-entry or "
                  "timezone error, not a real admission."},
    {"id": "DQ-011", "dataset": "fact_inpatient_abstracts.csv", "type": "range",
     "column": "alc_days", "min": 0, "max": 365, "severity": CRITICAL,
     "rationale": "ALC days are a subset of the stay and cannot be negative."},
    {"id": "DQ-012", "dataset": "fact_inpatient_abstracts.csv", "type": "range",
     "column": "riw", "min": 0.0001, "max": 60.0, "severity": CRITICAL,
     "rationale": "A zero or absent RIW makes cost per weighted case divide "
                  "by something meaningless."},
    {"id": "DQ-013", "dataset": "fact_inpatient_abstracts.csv", "type": "domain",
     "column": "discharge_disposition",
     "allowed": ["Home", "Home with Support Services", "Residential Care",
                 "Transfer to Other Facility", "Died"], "severity": WARNING,
     "rationale": "New disposition codes appear legitimately when a source "
                  "system is upgraded. Worth knowing about, not worth halting "
                  "month-end for."},
    {"id": "DQ-014", "dataset": "fact_inpatient_abstracts.csv", "type": "domain",
     "column": "readmit_30d", "allowed": ["0", "1"], "severity": CRITICAL,
     "rationale": "A flag with a third value breaks every rate that divides "
                  "by it."},
    {"id": "DQ-015", "dataset": "fact_inpatient_abstracts.csv",
     "type": "expression", "severity": CRITICAL,
     "expression": "total_los_days == acute_los_days + alc_days",
     "rationale": "The decomposition every bed-day figure depends on."},
    {"id": "DQ-016", "dataset": "fact_inpatient_abstracts.csv",
     "type": "expression", "severity": CRITICAL,
     "expression": "admit_date < discharge_date",
     "rationale": "Discharged before admitted. Rare, and always a real defect."},

    # --- timeliness ---------------------------------------------------------
    {"id": "DQ-020", "dataset": "fact_inpatient_abstracts.csv",
     "type": "freshness", "column": "discharge_date",
     "max_age_days": FRESHNESS_SLA_DAYS, "severity": WARNING,
     "rationale": "Coding lag is normal in acute care; a stale extract is a "
                  "service-level conversation with Health Information "
                  "Management, not a pipeline defect."},

    # --- the revenue-cycle datasets, held to the same standard --------------
    {"id": "DQ-030", "dataset": "fact_claims.csv", "type": "unique",
     "column": "claim_id", "severity": CRITICAL,
     "rationale": "Duplicate claims double-count both revenue and AR."},
    {"id": "DQ-031", "dataset": "fact_claims.csv", "type": "referential",
     "column": "payer_id", "parent": "dim_payer.csv",
     "parent_column": "payer_id", "severity": CRITICAL,
     "rationale": "An orphan payer key hides a claim from every payer cut."},
    {"id": "DQ-032", "dataset": "fact_claims.csv", "type": "domain",
     "column": "status", "allowed": ["Paid", "Denied", "Pending"],
     "severity": CRITICAL,
     "rationale": "Status drives the entire adjudication and AR split."},
]


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check(rule, rows, datasets):
    """Run one rule. Returns (violations, rows_scanned, detail)."""
    kind = rule["type"]

    if kind == "not_null":
        bad = [r for r in rows if not (r.get(rule["column"]) or "").strip()]
        return len(bad), len(rows), f"{len(bad)} null/blank"

    if kind == "unique":
        seen, dupes = set(), 0
        for r in rows:
            v = r.get(rule["column"])
            if v in seen:
                dupes += 1
            seen.add(v)
        return dupes, len(rows), f"{dupes} duplicate key(s)"

    if kind == "referential":
        parent = {r[rule["parent_column"]] for r in datasets[rule["parent"]]}
        bad = [r for r in rows if r.get(rule["column"]) not in parent]
        return len(bad), len(rows), f"{len(bad)} orphan(s) vs {rule['parent']}"

    if kind == "range":
        bad = 0
        for r in rows:
            v = _num(r.get(rule["column"]))
            if v is None or v < rule["min"] or v > rule["max"]:
                bad += 1
        return bad, len(rows), f"{bad} outside [{rule['min']}, {rule['max']}]"

    if kind == "domain":
        allowed = set(rule["allowed"])
        offenders = sorted({r.get(rule["column"]) for r in rows
                            if r.get(rule["column"]) not in allowed})
        bad = sum(1 for r in rows if r.get(rule["column"]) not in allowed)
        return bad, len(rows), (f"{bad} row(s), unexpected value(s): "
                                f"{offenders[:5]}" if bad else "all values in domain")

    if kind == "expression":
        # A deliberately tiny expression language: three named forms rather
        # than eval(). A rule file is configuration, and configuration that can
        # execute arbitrary Python is a supply-chain vulnerability wearing a
        # YAML hat.
        expr = rule["expression"]
        if expr == "total_los_days == acute_los_days + alc_days":
            bad = sum(1 for r in rows
                      if int(r["total_los_days"]) != int(r["acute_los_days"])
                      + int(r["alc_days"]))
        elif expr == "admit_date < discharge_date":
            bad = sum(1 for r in rows if not r["admit_date"] < r["discharge_date"])
        else:
            raise ValueError(f"unsupported expression rule: {expr}")
        return bad, len(rows), f"{bad} row(s) violate: {expr}"

    if kind == "freshness":
        newest = max(r[rule["column"]] for r in rows)
        age = (SNAPSHOT_DATE - date.fromisoformat(newest)).days
        breach = 1 if age > rule["max_age_days"] else 0
        return breach, len(rows), (f"newest {rule['column']} is {newest}, "
                                   f"{age}d old (SLA {rule['max_age_days']}d)")

    raise ValueError(f"unknown rule type: {kind}")


def emit(path, event):
    """Append one structured event to the ops log."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-failure", action="store_true",
                    help="corrupt a row in memory to prove the gate closes")
    args = ap.parse_args()

    run_id = uuid.uuid4().hex[:12]
    log = OUT / "dq_events.jsonl"
    started = time.time()

    needed = {r["dataset"] for r in RULES} | {r["parent"] for r in RULES
                                              if "parent" in r}
    datasets = {name: load(name) for name in sorted(needed)}

    if args.inject_failure:
        # A gate you have never seen close is decoration. Duplicating a key is
        # the exact failure mode DQ-002 exists for, so it is the one to inject.
        victim = dict(datasets["fact_inpatient_abstracts.csv"][0])
        datasets["fact_inpatient_abstracts.csv"].append(victim)

    emit(log, {"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
               "event": "run_started", "rules": len(RULES),
               "datasets": {k: len(v) for k, v in datasets.items()},
               "inject_failure": args.inject_failure})

    results, critical_failures = [], 0
    for rule in RULES:
        t0 = time.time()
        violations, scanned, detail = check(rule, datasets[rule["dataset"]], datasets)
        duration_ms = round((time.time() - t0) * 1000, 2)
        status = "pass" if violations == 0 else (
            "fail" if rule["severity"] == CRITICAL else "warn")
        if status == "fail":
            critical_failures += 1

        # The results CSV is the *report*: what was checked and what it found.
        # Run id and timings are telemetry — they belong in the JSONL event log,
        # which is where an observability tool reads them from. Keeping
        # nondeterministic values out of the committed artefact also means a
        # clean run produces no diff, so a real change to the data stands out
        # in review instead of hiding among churn.
        row = {"rule_id": rule["id"], "dataset": rule["dataset"],
               "type": rule["type"], "column": rule.get("column", ""),
               "severity": rule["severity"], "status": status,
               "violations": violations, "rows_scanned": scanned,
               "detail": detail, "rationale": rule["rationale"]}
        results.append(row)
        emit(log, {"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                   "event": "rule_evaluated", "duration_ms": duration_ms,
                   **{k: row[k] for k in ("rule_id", "status", "severity",
                                          "violations", "rows_scanned")}})

    passed = sum(1 for r in results if r["status"] == "pass")
    warned = sum(1 for r in results if r["status"] == "warn")
    verdict = "BLOCKED" if critical_failures else "PUBLISH"

    emit(log, {"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
               "event": "run_finished", "verdict": verdict,
               "passed": passed, "warned": warned, "failed": critical_failures,
               "duration_ms": round((time.time() - started) * 1000, 2)})

    lines = [
        "DATA QUALITY GATE",
        "=" * 46,
        f"Rules evaluated:   {len(results)}",
        f"  passed:          {passed}",
        f"  warnings:        {warned}",
        f"  critical fails:  {critical_failures}",
        f"Verdict:           {verdict}",
    ]
    for r in results:
        if r["status"] != "pass":
            lines.append(f"  [{r['status'].upper():4}] {r['rule_id']} "
                         f"{r['dataset']}.{r['column']} — {r['detail']}")
    # A sabotage drill must not overwrite the published evidence. The injected
    # run still emits its full telemetry to the event log — that is how the
    # test suite proves the gate closed — but the artefacts downstream
    # consumers read continue to describe the last *real* run.
    if not args.inject_failure:
        with open(OUT / "dq_results.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)
        (OUT / "dq_summary.txt").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")

    # The run id goes to the console and the event log, not into the committed
    # summary — it is how you find this run in telemetry, not a finding.
    print("\n".join(lines))
    print(f"\nrun_id {run_id} — full trail in output/dq_events.jsonl")

    # Exit code is the interface to the scheduler: 0 publish, 2 blocked.
    if critical_failures:
        print("\nCritical data-quality failure — downstream refresh must not run.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
