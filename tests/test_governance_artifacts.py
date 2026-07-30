"""
The published artefacts must describe the last real run, and must not churn.

Two small properties, both learned the boring way — by watching a clean rebuild
produce a diff and having to work out whether anything had actually changed.

  * **Determinism.** A committed artefact that changes on every run buries a
    real data change in noise, and review stops being useful. Run ids and
    timings are telemetry: they belong in the JSONL event log, not in the CSV.
  * **A sabotage drill must not overwrite production evidence.** The
    `--inject-failure` run deliberately corrupts the data to prove the gate
    closes. If it also rewrote the results file, the repository's committed
    quality report would describe a failure that never happened to real data.
"""

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
GATE = ROOT / "governance" / "data_quality.py"


def run(*args):
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True)


def test_repeated_clean_runs_produce_identical_artifacts():
    run()
    first_csv = (OUT / "dq_results.csv").read_bytes()
    first_txt = (OUT / "dq_summary.txt").read_bytes()

    run()
    assert (OUT / "dq_results.csv").read_bytes() == first_csv
    assert (OUT / "dq_summary.txt").read_bytes() == first_txt


def test_no_nondeterministic_field_reaches_the_committed_results():
    with open(OUT / "dq_results.csv", encoding="utf-8") as f:
        columns = set(csv.DictReader(f).fieldnames)
    assert "duration_ms" not in columns
    assert "run_id" not in columns
    assert "ts" not in columns


def test_run_id_is_not_written_into_the_summary():
    assert "Run id" not in (OUT / "dq_summary.txt").read_text(encoding="utf-8")


def test_the_sabotage_drill_leaves_the_published_artifacts_alone():
    run()
    clean_csv = (OUT / "dq_results.csv").read_bytes()
    clean_txt = (OUT / "dq_summary.txt").read_bytes()

    corrupted = run("--inject-failure")
    assert corrupted.returncode == 2, "the drill must still block"
    assert "BLOCKED" in corrupted.stdout

    assert (OUT / "dq_results.csv").read_bytes() == clean_csv
    assert (OUT / "dq_summary.txt").read_bytes() == clean_txt
    assert "PUBLISH" in (OUT / "dq_summary.txt").read_text(encoding="utf-8")


def test_the_drill_still_leaves_a_telemetry_trail():
    """Suppressing the artefact write must not suppress the evidence. The
    blocked run has to be reconstructable from the event log, or the drill
    proves nothing to anyone who was not watching the terminal."""
    import json
    run("--inject-failure")
    events = [json.loads(line) for line in
              (OUT / "dq_events.jsonl").read_text(encoding="utf-8").splitlines()]
    blocked = [e for e in events
               if e.get("event") == "run_finished" and e.get("verdict") == "BLOCKED"]
    assert blocked, "the blocked run left no trail"
    assert all(e["run_id"] and e["ts"] for e in blocked)
