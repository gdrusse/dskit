"""ADR-0283: every name in study.json ``report.run_report.required`` is rendered by the report document.

The mapping below is explicit on purpose: adding a required name to
study.json without saying which part of the document satisfies it fails
``test_the_mapping_covers_exactly_the_required_list``. The document
(``configs/mio-report-limited36-available-size-20261029.json``) is read as
JSON only; no pipeline runs here.
"""

import json
import os
from pathlib import Path

import pytest

CHILD = Path(__file__).resolve().parents[1]
REPO = CHILD.parents[1]
STUDY = CHILD / "docs/plans/mio-json/study.json"
DOCUMENT = CHILD / "configs/mio-report-limited36-available-size-20261029.json"

EVENTS_KIND = "dskit.evaluation.nodes:RowsToEvents"
REPORT_KIND = "dskit.evaluation.nodes:EvaluationReport"
LINKS_KIND = "dskit.evaluation.links:ArtifactLinks"

RUN_DOCUMENT = CHILD / "configs/mio-2019-limited36-available-size-20261029.json"
#: The v4 stress output root is not named anywhere yet (state.json ``stress`` is null), so the
#: report document carries this exact placeholder until the owner supplies the absolute path.
ROOT_PLACEHOLDER = "<V4_STRESS_OUTPUT_ROOT - not named in state.json; supply before running>"
COHORT = "limited36"

#: The charts the standard shows: P&L and equity, cash, distributions, periods.
CHART_SECTIONS = {"equity", "cash", "distributions", "periods"}


def _run_report():
    return json.loads(STUDY.read_text())["report"]["run_report"]


@pytest.fixture(scope="module")
def document():
    if not DOCUMENT.is_file():
        pytest.fail(f"the report document is not there yet: {DOCUMENT.relative_to(REPO)}")
    return json.loads(DOCUMENT.read_text())


def _nodes(document, kind):
    return [(key, spec) for key, spec in document["pipeline"].items() if spec.get("uses") == kind]


def _event_ports(document):
    """Every port the document maps to events, with its field map."""
    ports = {}
    for _key, spec in _nodes(document, EVENTS_KIND):
        ports.update(spec["params"]["map"])
    return ports


def _sections(document):
    """The sections every EvaluationReport node renders (summary and provenance always do)."""
    names = {"summary", "provenance"}
    for _key, spec in _nodes(document, REPORT_KIND):
        names |= set(spec["params"].get("sections", []))
    return names


def _links(document):
    """The shared ArtifactLinks params: every report's own links node declares the same ones."""
    nodes = _nodes(document, LINKS_KIND)
    assert nodes, "an ArtifactLinks node is expected"
    first = nodes[0][1]["params"]
    assert all(spec["params"] == first for _key, spec in nodes), "the links nodes must agree"
    return first


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _table_names(document):
    """Names of every table the report shows: pipeline node keys and each node's ``tables`` keys."""
    names = list(document["pipeline"])
    for spec in document["pipeline"].values():
        tables = spec.get("params", {}).get("tables")
        if isinstance(tables, dict):
            names += list(tables)
        elif isinstance(tables, list):
            names += [t.get("anchor", "") for t in tables if isinstance(t, dict)]
    return [n.lower() for n in names]


# --- the mapping: required name -> an assertion over the document ------------------------


def _day_by_day_ledger(document):
    ports = _event_ports(document)
    assert {"fills", "cashflows", "refusals"} <= set(ports), sorted(ports)
    assert {"decisions", "trades"} <= _sections(document)


def _refusal_reasons(document):
    refusals = _event_ports(document)["refusals"]["fields"]
    assert "reason" in refusals
    assert "decisions" in _sections(document)      # the decision log summarises (action, reason)


def _ticker_dte(document):
    names = _table_names(document)
    assert any("ticker" in n and "dte" in n for n in names), names


def _optimizer_certificates(document):
    solves = _event_ports(document)["solves"]["fields"]
    assert {"status", "solver"} <= set(solves), sorted(solves)
    assert "optimizer" in _sections(document)


def _charts(document):
    assert CHART_SECTIONS <= _sections(document), sorted(CHART_SECTIONS - _sections(document))


def _glossary(document):
    glossary = _run_report()["glossary"]
    assert (REPO / glossary).is_file()
    assert any(glossary in text for text in _walk_strings(document)), \
        "the document must name the glossary path"


def _v4_scenarios():
    """The seven v4 case names, from the stress module that owns them."""
    from index_options import mio_execution_stress as stress
    return [s["id"] for s in stress._approved_scenarios(stress._AVAILABLE_SIZE_SCHEMA)]


def _run_methods():
    """The four method ids of the limited36 run document."""
    study = json.loads(RUN_DOCUMENT.read_text())["pipeline"]["study"]["params"]["study"]
    return [m["id"] for m in study["domain"]["params"]["methods"]]


def _links_table(document):
    params = _links(document)
    table = params["table"]
    assert table["ok"] == {"column": "status", "equals": "complete"}
    assert table["where"] == {"cohort": COHORT}
    assert table["case"] == "scenario" and table["unit"] == "method"
    assert table["file"] == "summary.csv"
    assert table["cases"] == _v4_scenarios()
    assert sorted(table["units"]) == sorted(_run_methods()) and len(table["units"]) == 4
    root = params["root"]
    assert root == ROOT_PLACEHOLDER or os.path.isabs(root), root
    return params, table


def _stress_gate0(document):
    _params, table = _links_table(document)
    gate = {name: cases for name, cases in table["checks"].items() if len(cases) == 1}
    assert gate, "a one-case check (the zero-shock control) is Gate 0"
    assert {c for cases in gate.values() for c in cases} == {"control"}
    # one cohort holds 4 of the 8 controls: the label must not claim all eight
    assert all("4_of_8" in name for name in gate), sorted(gate)


def _stress_grid(document):
    params, table = _links_table(document)
    assert len(table["cases"]) == 7
    assert any(sorted(cases) == sorted(table["cases"]) for cases in table["checks"].values())
    ids = {entry["id"] for entry in params["files"]}
    assert len(ids) == len(params["files"]) >= 4      # report, summary, pins, daybooks
    books = [e for e in params["files"] if "axes" in e]
    assert books and set(books[0]["axes"]["scenario"]) == set(table["cases"])
    assert books[0]["path"].endswith(".md")
    assert params["pins"]["file"].endswith("pins.json")
    assert any(e["path"].endswith("report.md") for e in params["files"])
    assert any(e["path"] == table["file"] for e in params["files"])


MAPPING = {
    "day_by_day_ledger": _day_by_day_ledger,
    "refusal_reasons": _refusal_reasons,
    "ticker_dte": _ticker_dte,
    "optimizer_certificates": _optimizer_certificates,
    "charts": _charts,
    "glossary": _glossary,
    "stress_gate0": _stress_gate0,
    "stress_grid": _stress_grid,
}


def test_the_mapping_covers_exactly_the_required_list():
    assert set(MAPPING) == set(_run_report()["required"])
    assert len(_run_report()["required"]) == len(set(_run_report()["required"]))


@pytest.mark.parametrize("name", sorted(MAPPING))
def test_each_required_name_is_rendered_by_the_report_document(name, document):
    MAPPING[name](document)


# --- the stress links never write into a stress root ------------------------------------


def test_every_report_has_exactly_one_links_node_fed_by_its_paths_alone(document):
    reporters = {key for key, _spec in _nodes(document, REPORT_KIND)}
    fed = []
    for key, spec in _nodes(document, LINKS_KIND):
        source = spec["inputs"]["paths"]
        assert set(spec["inputs"]) == {"paths"}
        assert source.startswith("$") and source.endswith(".paths")
        reporter = source[1:].rsplit(".", 1)[0]
        assert reporter in reporters, "paths must be a report node's"
        fed.append(reporter)
    assert sorted(fed) == sorted(reporters), "one ArtifactLinks per EvaluationReport node"


def test_no_other_node_names_a_path_inside_the_stress_root(document):
    root = os.path.normpath(_links(document)["root"])
    for key, spec in document["pipeline"].items():
        if spec.get("uses") == LINKS_KIND:
            continue
        for text in _walk_strings(spec.get("params", {})):
            assert not (text == root or text.startswith(root + os.sep)), (key, text)


def test_the_report_directory_is_not_inside_or_above_the_stress_root(document):
    root = os.path.normpath(_links(document)["root"])
    for key, spec in _nodes(document, REPORT_KIND):
        out = os.path.normpath(os.path.expanduser(spec["params"]["out_dir"]))
        if os.path.isabs(out):
            assert not (out == root or out.startswith(root + os.sep)
                        or root.startswith(out + os.sep)), (key, out)


# --- the gate wiring, tolerances and the report's basis disclosure ------------------------

#: The accepted cent-level discrepancy in dollars: float summation order differs, so exact
#: equality is not asked of money; counts are exact.
MONEY_TOLERANCE = 0.005
COUNT_CHECKS = {"fill_count", "refusal_count", "decision_count"}
#: Every report discloses these in its own words (substring, case-insensitive).
CAVEAT_NEEDLES = ("at cost", "not broker liquidity")
AT_COST_STATS = {"twr", "max_drawdown", "max_drawdown_pct", "daily_sharpe"}
DECISION_STATS = {"refusal_rate", "skip_rate"}


def _methods(document):
    return [key[:-len("_report")] for key, _spec in _nodes(document, REPORT_KIND)]


def test_each_report_reads_the_events_its_own_gate_passed_on(document):
    methods = _methods(document)
    assert len(methods) == 4
    for method in methods:
        assert document["pipeline"][f"{method}_report"]["inputs"] == {
            "events": f"${method}_gate.events"}


def test_gate_tolerances_are_zero_for_counts_and_half_a_cent_for_money(document):
    for method in _methods(document):
        checks = document["pipeline"][f"{method}_gate"]["params"]["checks"]
        assert {name for name, c in checks.items() if name in COUNT_CHECKS} == COUNT_CHECKS
        for name, check in checks.items():
            want = 0 if name in COUNT_CHECKS else MONEY_TOLERANCE
            assert check["tolerance"] == want, (method, name)


def test_the_shaper_tolerance_is_the_same_half_cent(document):
    for method in _methods(document):
        assert document["pipeline"][f"{method}_rows"]["params"]["tolerance"] == MONEY_TOLERANCE


def test_every_report_states_its_basis_and_drops_the_driver_sentence(document):
    from dskit.evaluation.statistics import STAT_NAMES
    for method in _methods(document):
        params = document["pipeline"][f"{method}_report"]["params"]
        assert params["narrative_driver"] is False, method
        caveats = params["caveats"]
        assert caveats and all(isinstance(c, str) and c for c in caveats), method
        for needle in CAVEAT_NEEDLES:
            assert any(needle in c.lower() for c in caveats), (method, needle)
        assert any(_run_report()["glossary"] in c for c in caveats), method
        labels = params["statistic_labels"]
        assert set(labels) <= set(STAT_NAMES), sorted(set(labels) - set(STAT_NAMES))
        for name in AT_COST_STATS:
            assert labels[name].endswith("(at cost)"), (method, name)
        for name in DECISION_STATS:
            assert labels[name].endswith("(decisions)"), (method, name)
        # a label that says "at cost" belongs to an at-cost statistic: nothing else is tagged
        assert {n for n, v in labels.items() if v.endswith("(at cost)")} | DECISION_STATS \
            == set(labels)


def test_the_four_reports_carry_identical_basis_params(document):
    keys = ("caveats", "statistic_labels", "narrative_driver")
    seen = [{k: document["pipeline"][f"{m}_report"]["params"].get(k) for k in keys}
            for m in _methods(document)]
    assert all(entry == seen[0] for entry in seen)
