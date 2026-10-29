"""Solver certificates and a ticker x DTE table reach the report (ADR-0283).

Three contracts, none of them MIO-specific:

* ``RowsToEvents`` explodes a row's nested ``certificates`` list into one
  ``solve`` event each, and a solver time limit stays visible in the
  optimizer section;
* the existing ``derive`` + ``groupby`` kinds build a (key, bucket) table
  from flat rows, the bucket edges being params;
* :class:`~dskit.evaluation.sections.TableSection` shows such a table in
  ``report.html`` and writes it as a CSV beside the report.
"""

import csv
import io
import os

import pytest

from dskit.evaluation.events import EventLog
from dskit.evaluation.mapping import EventMapping
from dskit.evaluation.report import FILENAMES, BacktestReport
from dskit.evaluation.sections import DEFAULT_SECTIONS, OptimizerSection, Section, TableSection
from dskit.evaluation.statistics import SolveSummary
from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_flow import Derive, GroupBy
from dskit.pipeline.node import NodeContext
from tests.evaluation.conftest import DAY1, DAY2

RUN_START = {"run_id": "r1", "tz": "UTC", "title": "Tables"}

#: The ``map.solves`` block a document declares: one solve per certificate.
SOLVES_MAP = {"explode": "certificates", "fields": {
    "solver": "solver", "status": "status", "termination": "termination",
    "objective": "objective", "bound": "bound", "gap": "gap", "seconds": "seconds",
    "variables": "variables", "constraints": "constraints"}}


def _certificate(**changes):
    return {"solver": "appsi_highs", "status": "ok", "termination": "optimal",
            "objective": 100.0, "bound": 101.0, "gap": 0.01, "seconds": 3.5,
            "variables": 4311, "constraints": 1615, **changes}


def _decisions():
    """Three decisions: one solve, a timeout with an incumbent, a timeout with none."""
    return [
        {"asof_ms": DAY1, "status": "selected", "certificates": [_certificate()]},
        {"asof_ms": DAY2, "status": "timeout_skipped",
         "certificates": [_certificate(termination="maxTimeLimit", seconds=60.0, gap=0.4),
                          _certificate(seconds=1.0)]},
        {"asof_ms": DAY2 + 86_400_000, "status": "timeout_skipped",
         "certificates": [_certificate(termination="maxTimeLimit", objective=None, gap=None,
                                       bound=None, seconds=60.0)]},
        {"asof_ms": DAY2 + 2 * 86_400_000, "status": "no_eligible_candidates",
         "certificates": []},
    ]


def _log():
    mapping = EventMapping(RUN_START, {"solves": SOLVES_MAP})
    return EventLog(mapping.events({"solves": _decisions()}))


def test_each_certificate_becomes_one_solve_event():
    solves = _log().of_kind("solve")
    assert len(solves) == 4  # 1 + 2 + 1 + 0
    assert [e.ts_ms for e in solves] == [DAY1, DAY2, DAY2, DAY2 + 86_400_000]
    # The certificate's own status wins over the decision row's status of the same name.
    assert {e.get("status") for e in solves} == {"ok"}
    assert solves[3].get("objective") is None and solves[3].get("gap") is None


def test_a_time_limit_is_visible_in_the_optimizer_section(tmp_path):
    log = _log()
    outcomes = {(r["status"], r["termination"]): r["solves"]
                for r in SolveSummary(log).outcomes()}
    assert outcomes == {("ok", "optimal"): 2, ("ok", "maxTimeLimit"): 2}
    report = BacktestReport(log, sections=[OptimizerSection])
    assert "maxTimeLimit" in report.html()
    assert "ok/maxTimeLimit 2" in report.summary_markdown()


def test_the_solves_map_is_accepted_at_plan_time():
    assert EventMapping.problems(RUN_START, {"solves": SOLVES_MAP}) == []


# ---------------------------------------------------------------------------
# ticker x DTE through the existing derive + groupby kinds
# ---------------------------------------------------------------------------

#: DTE bucket edges are params: (label, low, high) inclusive.
BANDS = (("1-7", 1, 7), ("8-14", 8, 14), ("15-21", 15, 21))


def _bucket_cases():
    cases = [{"when": [{"field": "dte", "op": ">=", "value": low},
                       {"field": "dte", "op": "<=", "value": high}], "value": label}
             for label, low, high in BANDS]
    return [*cases, {"when": [], "value": "other"}]


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="t", asof="2019-01-02", run_dir=str(tmp_path))


def _table(ctx, rows, keys, aggregates):
    banded = Derive("band", {"field": "dte_band", "cases": _bucket_cases()}).run(
        ctx, {"records": rows})["records"]
    return GroupBy("by", {"keys": keys, "aggregates": aggregates}).run(
        ctx, {"records": banded})["records"]


FILLED = [
    {"symbol": "AAA", "dte": 3, "contracts": 2, "fees": 1.0, "pnl": 10.0},
    {"symbol": "AAA", "dte": 5, "contracts": 3, "fees": 1.5, "pnl": -4.0},
    {"symbol": "AAA", "dte": 9, "contracts": 1, "fees": 0.5, "pnl": 2.0},
    {"symbol": "BBB", "dte": 20, "contracts": 4, "fees": 2.0, "pnl": 7.5},
    {"symbol": "BBB", "dte": 80, "contracts": 1, "fees": 0.5, "pnl": 1.0},
]
REFUSED = [
    {"symbol": "AAA", "dte": 4, "reason": "missing_fill_quote"},
    {"symbol": "AAA", "dte": 6, "reason": "missing_fill_quote"},
    {"symbol": "AAA", "dte": 6, "reason": "insufficient_fill_size"},
    {"symbol": "BBB", "dte": 10, "reason": "missing_fill_quote"},
]


def test_filled_books_by_symbol_and_dte_band(ctx):
    rows = _table(ctx, FILLED, ["symbol", "dte_band"], {
        "books": {"op": "count"}, "contracts": {"op": "sum", "field": "contracts"},
        "fees": {"op": "sum", "field": "fees"}, "net_pnl": {"op": "sum", "field": "pnl"}})
    assert rows == [
        {"symbol": "AAA", "dte_band": "1-7", "books": 2, "contracts": 5, "fees": 2.5,
         "net_pnl": 6.0},
        {"symbol": "AAA", "dte_band": "8-14", "books": 1, "contracts": 1, "fees": 0.5,
         "net_pnl": 2.0},
        {"symbol": "BBB", "dte_band": "15-21", "books": 1, "contracts": 4, "fees": 2.0,
         "net_pnl": 7.5},
        {"symbol": "BBB", "dte_band": "other", "books": 1, "contracts": 1, "fees": 0.5,
         "net_pnl": 1.0},
    ]


def test_refusals_by_symbol_dte_band_and_reason(ctx):
    rows = _table(ctx, REFUSED, ["symbol", "dte_band", "reason"], {"refusals": {"op": "count"}})
    assert [(r["symbol"], r["dte_band"], r["reason"], r["refusals"]) for r in rows] == [
        ("AAA", "1-7", "insufficient_fill_size", 1),
        ("AAA", "1-7", "missing_fill_quote", 2),
        ("BBB", "8-14", "missing_fill_quote", 1)]


def test_exact_dte_needs_no_bucket(ctx):
    rows = GroupBy("by", {"keys": ["symbol", "dte"], "aggregates": {
        "books": {"op": "count"}}}).run(ctx, {"records": FILLED})["records"]
    assert [(r["symbol"], r["dte"]) for r in rows][:3] == [("AAA", 3), ("AAA", 5), ("AAA", 9)]


def test_a_dte_no_band_covers_is_refused_without_a_catch_all(ctx):
    cases = _bucket_cases()[:-1]
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        Derive("band", {"field": "dte_band", "cases": cases}).run(ctx, {"records": FILLED})


# ---------------------------------------------------------------------------
# TableSection
# ---------------------------------------------------------------------------

COLUMNS = ("symbol", "dte_band", "books", "net_pnl")
TABLE_ROWS = [{"symbol": "AAA", "dte_band": "1-7", "books": 2, "net_pnl": 6.0},
              {"symbol": "B<B", "dte_band": "8-14", "books": 1, "net_pnl": -2.5}]


def _section(**changes):
    spec = {"title": "Ticker x DTE", "columns": list(COLUMNS), "rows": TABLE_ROWS,
            "formats": {"books": "count", "net_pnl": "pnl"}, **changes}
    return TableSection.from_obj("ticker-dte", spec)


def _report(*sections):
    return BacktestReport(_log(), sections=list(sections))


def test_the_table_renders_escaped_and_formatted_in_the_html():
    page = _report(_section()).html()
    assert '<section id="ticker-dte"><h2>Ticker x DTE</h2>' in page
    assert "B&lt;B" in page and "B<B" not in page
    assert "-2.50" in page and "6.00" in page
    assert 'href="#ticker-dte"' in page
    assert "http" not in page.lower()


def test_the_csv_lands_beside_the_report_with_raw_values(tmp_path):
    paths = _report(_section()).write(str(tmp_path / "out"))
    assert set(FILENAMES) <= set(paths) and "ticker-dte.csv" in paths
    assert os.path.dirname(paths["ticker-dte.csv"]) == os.path.dirname(paths["html"])
    with open(paths["ticker-dte.csv"], newline="", encoding="utf-8") as handle:
        got = list(csv.DictReader(handle))
    assert got[1] == {"symbol": "B<B", "dte_band": "8-14", "books": "1", "net_pnl": "-2.5"}


def test_summary_points_at_the_csv_and_does_not_dump_the_table():
    text = _report(_section()).summary_markdown()
    assert "Ticker x DTE" in text and "2 rows" in text and "ticker-dte.csv" in text
    assert "B<B" not in text and "B&lt;B" not in text


def test_an_empty_table_says_so_and_still_writes_a_header(tmp_path):
    section = _section(rows=[])
    assert "No rows." in _report(section).html()
    paths = _report(section).write(str(tmp_path / "out"))
    assert open(paths["ticker-dte.csv"], encoding="utf-8").read().splitlines() == [
        ",".join(COLUMNS)]


def test_default_sections_write_exactly_the_five_files(tmp_path):
    paths = BacktestReport(_log()).write(str(tmp_path / "out"))
    assert set(paths) == set(FILENAMES)


def test_every_problem_in_a_table_spec_is_listed():
    with pytest.raises(ConfigError) as info:
        TableSection.from_obj("Bad Anchor", {"title": "", "columns": ["a", "a"],
                                              "rows": [{"a": 1}, 3], "formats": {"z": "money"},
                                              "colour": "red"})
    text = str(info.value)
    for needle in ("anchor", "title", "distinct", "rows[1]", "formats", "colour"):
        assert needle in text


def test_a_table_file_may_not_overwrite_a_report_file(tmp_path):
    section = TableSection.from_obj("decisions", {"title": "E", "columns": ["a"], "rows": []})
    out = tmp_path / "out"
    with pytest.raises(ConfigError, match="decisions.csv.*collides"):
        _report(section).write(str(out))
    assert not out.exists()


def test_two_tables_with_one_anchor_are_refused_up_front():
    with pytest.raises(ConfigError, match="ticker-dte.*used twice"):
        _report(_section(), _section())


def test_a_table_may_not_reuse_a_default_sections_anchor():
    section = TableSection.from_obj("equity", {"title": "E", "columns": ["a"], "rows": []})
    with pytest.raises(ConfigError, match="equity.*used twice"):
        BacktestReport(_log(), sections=[*DEFAULT_SECTIONS, section])


class _Files(Section):
    """A section that writes the named files."""

    title, anchor = "Files", "files"

    def __init__(self, *names):
        self.names = names

    def html(self, context):
        return ""

    def files(self, context):
        return {name: "x\n" for name in self.names}


@pytest.mark.parametrize("name", [*FILENAMES, *FILENAMES.values()])
def test_a_section_file_may_not_reuse_a_report_name(tmp_path, name):
    out = tmp_path / "out"
    with pytest.raises(ConfigError, match="collides"):
        _report(_Files(name)).write(str(out))
    assert not out.exists()


@pytest.mark.parametrize("name", ["", ".", "..", "sub/x.csv", "../x.csv", "/abs.csv"])
def test_a_section_file_name_must_be_a_bare_name(tmp_path, name):
    out = tmp_path / "out"
    with pytest.raises(ConfigError, match="bare file name"):
        _report(_Files(name)).write(str(out))
    assert not out.exists()


def test_a_report_without_a_table_writes_exactly_its_five_files_unchanged(tmp_path):
    report = BacktestReport(_log())
    paths = report.write(str(tmp_path / "out"))
    assert sorted(os.listdir(tmp_path / "out")) == sorted(FILENAMES.values())
    expected = {"events": report.log.to_jsonl(), "summary": report.summary_markdown().encode(),
                "html": report.html().encode(), "decisions": report.decisions_csv().encode(),
                "trades": report.trades_csv().encode()}
    for name, raw in expected.items():
        with open(paths[name], "rb") as handle:
            assert handle.read() == raw


def test_csv_text_is_the_pipeline_one():
    section = _section()
    (name, raw), = section.files(_report(section).context).items()
    assert name == "ticker-dte.csv"
    assert list(csv.reader(io.StringIO(raw)))[0] == list(COLUMNS)


# ---------------------------------------------------------------------------
# a decision with no certificates (no eligible candidates) never reaches the map
# ---------------------------------------------------------------------------

SOLVE_ROW_WHERE = [{"field": "certificates", "op": "!=", "value": []}]


def test_a_decision_without_certificates_is_filtered_before_the_map(ctx):
    from dskit.evaluation.events import EvaluationError
    from dskit.pipeline.kinds_flow import Filter

    rows = [*_decisions(), {"asof_ms": DAY1, "status": "no_eligible_candidates"}]
    mapping = EventMapping(RUN_START, {"solves": SOLVES_MAP})
    with pytest.raises(EvaluationError, match="no field 'certificates'"):
        mapping.events({"solves": rows})
    kept = Filter("solve_rows", {"where": SOLVE_ROW_WHERE}).run(ctx, {"records": rows})["records"]
    assert len(kept) == 3  # the key-less row and the empty-list row are both dropped
    assert len(EventLog(mapping.events({"solves": kept})).of_kind("solve")) == 4


# ---------------------------------------------------------------------------
# the EvaluationReport node's `tables` param
# ---------------------------------------------------------------------------

TABLES = {"by-symbol": {"title": "By symbol", "columns": ["symbol", "net_pnl"],
                        "rows": [{"symbol": "AAA", "net_pnl": 6.0}],
                        "formats": {"net_pnl": "pnl"}, "note": "from groupby"}}


def test_the_report_node_renders_each_table_param_before_provenance(tmp_path, scenario):
    import re

    from dskit.evaluation.nodes import EvaluationReport

    params = {"out_dir": "r", "sections": ["equity"], "tables": TABLES}
    assert EvaluationReport.validate_params(params) == []
    ctx = NodeContext(name="t", asof="2026-09-02", run_dir=str(tmp_path))
    out = EvaluationReport("report", params).run(ctx, {"events": scenario})
    assert os.path.basename(out["paths"]["by-symbol.csv"]) == "by-symbol.csv"
    page = (tmp_path / "r" / "report.html").read_text(encoding="utf-8")
    assert re.findall(r'<section id="([a-z-]+)"', page) == [
        "summary", "equity", "by-symbol", "provenance"]
    assert "from groupby" in page
    bare = EvaluationReport("report", {"out_dir": "r2"}).run(ctx, {"events": scenario})
    assert "by-symbol.csv" not in bare["paths"]


def test_a_wired_reference_for_rows_defers_and_a_malformed_table_is_refused():
    from dskit.evaluation.nodes import EvaluationReport

    wired = {"t": dict(TABLES["by-symbol"], rows="$groups.records")}
    assert EvaluationReport.validate_params({"out_dir": "r", "tables": wired}) == []
    assert EvaluationReport.validate_params({"out_dir": "r", "tables": "$spec.tables"}) == []
    for bad in ({"Bad Anchor": TABLES["by-symbol"]}, [], {"t": dict(TABLES["by-symbol"],
                                                                     rows="nope")},
                {"t": {"title": "x"}}):
        assert EvaluationReport.validate_params({"out_dir": "r", "tables": bad}), bad


# ---------------------------------------------------------------------------
# review round 3: section-vs-section collisions, no directory on a refusal, anchors, specs
# ---------------------------------------------------------------------------


class _Named(_Files):
    """A section with its own anchor, so two of them can sit in one report."""

    def __init__(self, anchor, *names):
        super().__init__(*names)
        self.anchor = anchor


def test_two_sections_writing_one_file_name_collide_and_nothing_is_created(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ConfigError, match="collides"):
        _report(_Named("one", "shared.csv"), _Named("two", "shared.csv")).write(str(out))
    assert not out.exists()


def test_two_sections_with_distinct_file_names_both_land(tmp_path):
    paths = _report(_Named("one", "a.csv"), _Named("two", "b.csv")).write(str(tmp_path / "out"))
    assert {"a.csv", "b.csv"} <= set(paths)


@pytest.mark.parametrize("name", ["a\\b.csv", "dir/x.csv", ""])
def test_a_refused_write_creates_no_directory_even_when_nested(tmp_path, name):
    out = tmp_path / "deep" / "out"
    with pytest.raises(ConfigError):
        _report(_Files(name)).write(str(out))
    assert not (tmp_path / "deep").exists()


@pytest.mark.parametrize("anchor", ["a b", "A", "a/b", "-a", "a.b", "a\n", "", "ab "])
def test_an_anchor_must_fullmatch_the_pattern(anchor):
    assert TableSection.problems(anchor, {"title": "T", "columns": ["a"], "rows": []})
    with pytest.raises(ConfigError, match="anchor"):
        TableSection(anchor=anchor, title="T", columns=["a"], rows=[])


@pytest.mark.parametrize("anchor", ["a", "a-b_c", "0x", "ticker-dte"])
def test_a_conforming_anchor_is_accepted(anchor):
    assert TableSection.problems(anchor, {"title": "T", "columns": ["a"], "rows": []}) == []


def _spec(**changes):
    return {"title": "T", "columns": ["a", "b"], "rows": [], **changes}


def test_a_format_naming_a_column_not_in_columns_is_a_problem():
    problems = TableSection.problems("t", _spec(formats={"z": "money"}))
    assert len(problems) == 1 and "'z'" in problems[0] and "formats" in problems[0]


def test_an_unknown_format_kind_is_a_problem():
    problems = TableSection.problems("t", _spec(formats={"a": "furlongs"}))
    assert len(problems) == 1 and "'furlongs'" in problems[0]


@pytest.mark.parametrize("title", ["", "   ", None, 3])
def test_a_blank_or_non_string_title_is_a_problem(title):
    problems = TableSection.problems("t", _spec(title=title))
    assert len(problems) == 1 and "title" in problems[0]


def test_a_valid_format_is_not_a_problem():
    assert TableSection.problems("t", _spec(formats={"a": "money", "b": "text"})) == []


# ---------------------------------------------------------------------------
# the one owner of the bare-file-name rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, ok", [
    ("a.csv", True), ("a b.csv", True), ("..x", True),
    ("", False), (".", False), ("..", False), ("a/b", False), ("/a", False), ("../a", False),
    ("a\\b", False), ("a/", False), (None, False), (3, False),
])
def test_is_bare_file_name(name, ok):
    from dskit.evaluation.names import is_bare_file_name
    assert is_bare_file_name(name) is ok


def test_reports_and_links_share_the_one_rule():
    import dskit.evaluation.links as links
    import dskit.evaluation.report as report
    from dskit.evaluation import names
    assert links.is_bare_file_name is names.is_bare_file_name
    assert report.is_bare_file_name is names.is_bare_file_name
