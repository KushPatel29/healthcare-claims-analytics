"""
Power BI report/model integrity — proven without opening Power BI.

The report is hand-authored (TMDL semantic model + PBIR report definition). A
mistyped column or a measure that no longer exists does not fail loudly; it
renders a blank visual that a screenshot might not reveal. So CI parses the
model and asserts that every field a visual references actually resolves:

  * every visual column/measure projection exists in the model,
  * every sort field resolves,
  * every relationship and sortByColumn points at a real column.

If any of these break, the dashboard would open with broken visuals — caught
here first.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "powerbi" / "pbip" / "RevenueCycleAnalytics.SemanticModel" / "definition"
REPORT = ROOT / "powerbi" / "pbip" / "RevenueCycleAnalytics.Report" / "definition"

COLUMN_RE = re.compile(r"^\tcolumn\s+('([^']+)'|(\S+))", re.MULTILINE)
MEASURE_RE = re.compile(r"^\tmeasure\s+('([^']+)'|(\S+?))\s*=", re.MULTILINE)
SORTBY_RE = re.compile(r"sortByColumn:\s*(\S+)")


def _name(match):
    return match.group(2) or match.group(3)


@pytest.fixture(scope="module")
def model():
    """Parse the TMDL into {table: {columns}} and a set of measure names."""
    tables = {}
    measures = set()
    for tmdl in (MODEL / "tables").glob("*.tmdl"):
        text = tmdl.read_text(encoding="utf-8")
        m = re.match(r"table\s+('([^']+)'|(\S+))", text)
        table = m.group(2) or m.group(3)
        tables[table] = {_name(c) for c in COLUMN_RE.finditer(text)}
        measures.update(_name(x) for x in MEASURE_RE.finditer(text))
    return {"tables": tables, "measures": measures}


def _iter_field_refs(node):
    """Yield ('Column'|'Measure', entity, property) for every field ref in a
    visual.json tree, wherever it appears (projections, sort definitions...)."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and isinstance(node[kind], dict):
                inner = node[kind]
                entity = inner.get("Expression", {}).get("SourceRef", {}).get("Entity")
                prop = inner.get("Property")
                if entity and prop:
                    yield kind, entity, prop
        for v in node.values():
            yield from _iter_field_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_field_refs(v)


def test_visual_field_refs_resolve(model):
    tables, measures = model["tables"], model["measures"]
    visuals = list(REPORT.glob("pages/*/visuals/*/visual.json"))
    assert visuals, "no visuals found — report definition missing?"
    problems = []
    for vf in visuals:
        tree = json.loads(vf.read_text(encoding="utf-8"))
        vid = f"{vf.parent.parent.parent.name}/{vf.parent.name}"
        for kind, entity, prop in _iter_field_refs(tree):
            if kind == "Measure":
                if prop not in measures:
                    problems.append(f"{vid}: measure '{prop}' not in model")
            else:  # Column
                if entity not in tables:
                    problems.append(f"{vid}: unknown table '{entity}'")
                elif prop not in tables[entity]:
                    problems.append(f"{vid}: column '{entity}'[{prop}] not in model")
    assert not problems, "unresolved field references:\n" + "\n".join(problems)


def test_relationships_reference_real_columns(model):
    tables = model["tables"]
    text = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    for side in re.findall(r"(?:from|to)Column:\s*(\S+)\.(\S+)", text):
        table, column = side
        assert table in tables, f"relationship references unknown table {table}"
        assert column in tables[table], f"relationship references {table}[{column}] which is missing"


def test_sortby_columns_exist(model):
    tables = model["tables"]
    for tmdl in (MODEL / "tables").glob("*.tmdl"):
        text = tmdl.read_text(encoding="utf-8")
        m = re.match(r"table\s+('([^']+)'|(\S+))", text)
        table = m.group(2) or m.group(3)
        for sort_col in SORTBY_RE.findall(text):
            assert sort_col in tables[table], f"{table}: sortByColumn '{sort_col}' missing"


def test_new_page_registered():
    meta = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    assert "section_yield" in meta["pageOrder"], "Predictive Yield page not in pageOrder"
    for page in meta["pageOrder"]:
        assert (REPORT / "pages" / page / "page.json").exists(), f"{page} missing page.json"


def test_yield_columns_backed_by_engine_output():
    """Every column the yield table declares must be a real header in the CSV
    the engine writes — the model and the engine cannot drift apart."""
    tmdl = (MODEL / "tables" / "ar_yield_predictions.tmdl").read_text(encoding="utf-8")
    declared = {_name(c) for c in COLUMN_RE.finditer(tmdl)}
    header = (ROOT / "output" / "ar_yield_predictions.csv").read_text(
        encoding="utf-8").splitlines()[0].split(",")
    missing = declared - set(header)
    assert not missing, f"model columns not produced by engine: {missing}"
