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

import csv
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
    """Parse the TMDL into {table: {columns}} and a set of measure names.

    The table declaration is found anywhere in the file, not only at byte zero.
    TMDL allows a `///` description block above an object, and the Canadian
    tables use it to record why they exist — so anchoring this to the start of
    the file made a documented table unparseable and took the whole fixture down
    with an AttributeError rather than a useful message.
    """
    tables = {}
    measures = set()
    for tmdl in (MODEL / "tables").glob("*.tmdl"):
        text = tmdl.read_text(encoding="utf-8")
        m = re.search(r"^table\s+('([^']+)'|(\S+))", text, re.M)
        assert m, f"{tmdl.name}: no table declaration found"
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
        m = re.search(r"^table\s+('([^']+)'|(\S+))", text, re.M)
        assert m, f"{tmdl.name}: no table declaration found"
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


# --------------------------------------------------------------------------
# The Canadian decision-support layer
# --------------------------------------------------------------------------

def test_every_activity_month_exists_in_its_date_dimension():
    """The failure this model's second date table exists to prevent.

    `dim_month` is generated for the claims period and starts at 2025-07. The
    inpatient abstracts start twelve months earlier. Point discharge_month at
    dim_month and Power BI does not complain — it puts every unmatched row in a
    blank member, and every month-sliced visual quietly reports half the
    authority's activity with nothing on the canvas to say so.

    So this asserts the join actually covers the data: every discharge month in
    the fact table must exist in the calendar it is related to. If someone
    re-points the relationship, or the generator's date range moves, this fails
    loudly instead of the dashboard failing silently.
    """
    rel = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    m = re.search(
        r"fromColumn:\s*fact_inpatient_abstracts\.discharge_month\s*\n\s*toColumn:\s*(\S+)\.(\S+)",
        rel)
    assert m, "discharge_month is not related to any date table"
    dim_table, dim_col = m.group(1), m.group(2)
    assert dim_table == "dim_activity_month", (
        f"discharge_month is joined to {dim_table}, which does not span the "
        f"abstracts' 24 months — half the activity would land in a blank row"
    )

    tmdl = (MODEL / "tables" / f"{dim_table}.tmdl").read_text(encoding="utf-8")
    start = re.search(r"#date\((\d{4}),\s*(\d+),\s*\d+\)", tmdl)
    span = re.search(r"\{0\.\.(\d+)\}", tmdl)
    assert start and span, f"{dim_table}: cannot read its generated range"

    y, mo, n = int(start.group(1)), int(start.group(2)), int(span.group(1))
    covered = set()
    for i in range(n + 1):
        yy, mm = divmod((y * 12 + mo - 1) + i, 12)
        covered.add(f"{yy}-{mm + 1:02d}")

    with open(ROOT / "data" / "fact_inpatient_abstracts.csv", encoding="utf-8") as f:
        actual = {r["discharge_month"] for r in csv.DictReader(f)}

    missing = sorted(actual - covered)
    assert not missing, (
        f"{len(missing)} discharge month(s) fall outside {dim_table} and would "
        f"land in a blank row: {missing[:6]}"
    )


def test_the_canadian_pages_lead_the_report():
    """A health authority reader should not have to page past four screens of US
    revenue cycle to reach the work aimed at them. Page order is a claim the
    README makes, so it gets a test."""
    meta = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    assert meta["pageOrder"][:2] == ["section_acute_activity", "section_alc_flow"]
    assert meta["activePageName"] == "section_acute_activity"


def test_risk_adjusted_measures_read_the_engine_rather_than_recomputing():
    """The repo's standing rule, enforced on the measures that would be easiest
    to get wrong. LOS index and readmission O/E are indirectly standardised with
    empirical-Bayes shrinkage in Python; a DAX reimplementation would produce a
    second, subtly different answer with no test behind it."""
    tmdl = (MODEL / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    for name in ("LOS Index", "Readmission O/E"):
        m = re.search(rf"measure '{re.escape(name)}' = (.+)", tmdl)
        assert m, f"measure '{name}' is missing"
        expr = m.group(1)
        assert "activity_by_facility" in expr, (
            f"'{name}' no longer reads the engine output — it must not be "
            f"recomputed from the fact table in DAX"
        )


def test_no_chart_plots_a_column_it_cannot_aggregate():
    """The bug this test was written for shipped, and it shipped silently.

    The p' control chart originally put spc_alc_stay[rate], [centre], [lcl] and
    [ucl] straight onto the Y axis. Every one of those columns is
    `summarizeBy: none` — correct, because summing a control limit is nonsense —
    but a chart has to aggregate what it plots. Power BI does not raise: it
    renders the visual empty. Field references all resolved, so the existing
    integrity test passed, and the page looked finished until someone opened it.

    A column belongs on a value axis only if the model says how to aggregate it.
    Anything else goes through a measure, which is what the rest of this report
    already does. Cards, tables and slicers are exempt — none of them aggregate.
    """
    NUMERIC = {"double", "int64", "decimal"}
    VALUE_WELLS = {"Y", "Y2", "Values", "Size", "X"}
    EXEMPT = {"slicer", "tableEx", "pivotTable", "card", "multiRowCard"}

    no_agg = {}
    for tmdl in (MODEL / "tables").glob("*.tmdl"):
        text = tmdl.read_text(encoding="utf-8")
        m = re.search(r"^table\s+('([^']+)'|(\S+))", text, re.M)
        table = m.group(2) or m.group(3)
        for col in re.finditer(
                r"^\tcolumn\s+(\S+)\n(?:\t\t.*\n)*?\t\tsummarizeBy:\s*none", text, re.M):
            block = text[col.start():col.end()]
            dtype = re.search(r"dataType:\s*(\S+)", block)
            if dtype and dtype.group(1) in NUMERIC:
                no_agg.setdefault(table, set()).add(col.group(1))

    problems = []
    for vf in sorted(REPORT.glob("pages/*/visuals/*/visual.json")):
        tree = json.loads(vf.read_text(encoding="utf-8"))
        vis = tree.get("visual", {})
        if vis.get("visualType") in EXEMPT:
            continue
        page = vf.parent.parent.parent.name
        for well, spec in vis.get("query", {}).get("queryState", {}).items():
            if well not in VALUE_WELLS:
                continue
            for proj in spec.get("projections", []):
                col = proj.get("field", {}).get("Column")
                if not col:
                    continue
                entity = col.get("Expression", {}).get("SourceRef", {}).get("Entity")
                prop = col.get("Property")
                if prop in no_agg.get(entity, ()):
                    problems.append(
                        f"{page}/{tree['name']} ({vis.get('visualType')}): "
                        f"{entity}[{prop}] sits on '{well}' but is summarizeBy:none "
                        f"— this renders an empty visual, not an error"
                    )

    assert not problems, "charts that will render blank:\n" + "\n".join(problems)


def test_the_control_chart_reads_its_limits_from_the_engine():
    """The p' chart's four series, and where each one legitimately comes from.

    The observed rate is recomputed from the fact table; the three limits are
    read from the engine. That split is the point — if the limits ever became
    DAX, they would be recomputed inside filter context and would no longer be
    the phase I baseline limits the chart claims to show.
    """
    vf = (REPORT / "pages" / "section_alc_flow" / "visuals" / "visualD06" / "visual.json")
    tree = json.loads(vf.read_text(encoding="utf-8"))
    qs = tree["visual"]["query"]["queryState"]

    axis = qs["Category"]["projections"][0]["queryRef"]
    assert axis == "dim_activity_month.month", (
        f"axis is {axis}; it must be the conformed dimension, or the fact-based "
        f"observed rate is not filtered by the month on the axis"
    )

    series = {p["queryRef"] for p in qs["Y"]["projections"]}
    assert series == {
        "_Measures.ALC Stay Rate",
        "_Measures.p-prime Centre Line",
        "_Measures.p-prime Lower Limit",
        "_Measures.p-prime Upper Limit",
    }, f"control chart series changed: {sorted(series)}"

    tmdl = (MODEL / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    for limit in ("p-prime Centre Line", "p-prime Lower Limit", "p-prime Upper Limit"):
        expr = re.search(rf"measure '{re.escape(limit)}' = (.+)", tmdl)
        assert expr and "spc_alc_stay" in expr.group(1), (
            f"'{limit}' no longer reads the engine's chart output"
        )
