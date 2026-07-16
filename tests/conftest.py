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
    subprocess.run([sys.executable, str(ROOT / "data_generator" / "generate_claims_data.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "engine" / "build_rcm_metrics.py")], check=True)
