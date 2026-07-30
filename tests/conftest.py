"""Regenerate the data and rebuild every engine output once per test session,
before any test runs. This guarantees all tests — including the Power BI
integrity checks that read output/ directly — see freshly built, consistent
artifacts rather than whatever happens to be committed."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def build_outputs():
    for script in (
        # US revenue cycle
        ROOT / "data_generator" / "generate_claims_data.py",
        ROOT / "engine" / "build_rcm_metrics.py",
        # Canadian acute-care activity, SPC, and the economic evaluation
        # (order matters: the HTA model reads the activity engine's output)
        ROOT / "canadian" / "generate_activity_data.py",
        ROOT / "engine" / "build_activity_metrics.py",
        ROOT / "engine" / "health_economics.py",
        # governance layer
        ROOT / "governance" / "deidentify.py",
        ROOT / "governance" / "data_quality.py",
    ):
        subprocess.run([sys.executable, str(script)], check=True)


def read_csv(name):
    """Read an engine output CSV as a list of dicts."""
    import csv
    with open(ROOT / "output" / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))
