"""``records-write-run`` (ADR-0247): a table writer whose file, and so whose published stream, is named by the run.

Three layers: the node on its own (expansion, stamping, refusals), the plan-time refusals through the
real planner and driver, and the whole point of the kind, end to end through a real ``localtables``
publication: a changed document publishes a SECOND stream and the first still reads back unchanged.
"""

import itertools
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_run_write import (
    NODE_KINDS,
    RUN_ID_FIELD,
    RUN_PLACEHOLDER,
    RecordsWriteRun,
    register,
    run_name_problems,
)
from dskit.pipeline.kinds_table import RecordsWrite
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry
from dskit.pipeline.planner import plan

RUN = "doc-2026-10-06-aaaaaaaa"


def ctx(tmp_path, run=RUN):
    return NodeContext(name="doc", asof="2026-10-06", run_dir=str(pathlib.Path(tmp_path) / "runs" / run))


def node(tmp_path, **over):
    params = {"path": str(pathlib.Path(tmp_path) / "table-{run}.jsonl"), "source": "test rows", **over}
    return RecordsWriteRun("write", params)


def lines(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines()]


ROWS = [{"ticker": "A", "decision_ms": 1}, {"ticker": "B", "decision_ms": 2}]


# -- the node ----------------------------------------------------------------------------------


def test_the_path_names_the_run_and_every_row_says_which_run_it_came_from(tmp_path):
    out = node(tmp_path).run(ctx(tmp_path), {"records": ROWS})
    expected = tmp_path / f"table-{RUN}.jsonl"
    assert out["path"] == str(expected) and out["provenance"]["path"] == str(expected)
    assert lines(expected) == [{**row, RUN_ID_FIELD: RUN} for row in ROWS]
    assert ROWS == [{"ticker": "A", "decision_ms": 1}, {"ticker": "B", "decision_ms": 2}], "inputs are not mutated"


def test_the_field_and_the_placeholder_are_the_public_constants():
    assert (RUN_ID_FIELD, RUN_PLACEHOLDER) == ("run_id", "{run}")


def test_two_runs_write_two_files(tmp_path):
    for suffix in ("aaaaaaaa", "bbbbbbbb"):
        node(tmp_path).run(ctx(tmp_path, f"doc-2026-10-06-{suffix}"), {"records": [{"x": 1}]})
    assert sorted(os.listdir(tmp_path)) == ["table-doc-2026-10-06-aaaaaaaa.jsonl", "table-doc-2026-10-06-bbbbbbbb.jsonl"]


def test_a_trailing_separator_on_the_run_dir_does_not_change_the_name(tmp_path):
    frame = NodeContext(name="doc", asof="2026-10-06", run_dir=str(tmp_path / "runs" / RUN) + os.sep)
    out = node(tmp_path).run(frame, {"records": ROWS})
    assert out["path"].endswith(f"table-{RUN}.jsonl")


def test_it_is_a_records_write_so_every_guarantee_is_kept(tmp_path):
    assert issubclass(RecordsWriteRun, RecordsWrite) and RecordsWriteRun.outputs == RecordsWrite.outputs
    run = ctx(tmp_path)
    first = node(tmp_path).run(run, {"records": ROWS})
    assert set(first) == {"path", "provenance", "metrics"} and first["metrics"]["rows"] == 2
    with pytest.raises(FileExistsError):
        node(tmp_path).run(run, {"records": ROWS})  # overwrite is not declared
    node(tmp_path, overwrite=True).run(run, {"records": ROWS})
    with pytest.raises(ValueError, match="NaN|finite|JSON"):
        node(tmp_path, overwrite=True).run(run, {"records": [{"x": float("nan")}]})
    with pytest.raises(ValueError, match="row 1"):
        node(tmp_path, overwrite=True).run(run, {"records": [{"x": 1}, "not a mapping"]})
    with pytest.raises(ValueError, match="expects 3"):
        node(tmp_path, overwrite=True, expect=3).run(run, {"records": ROWS})
    assert node(tmp_path, overwrite=True, expect=2).run(run, {"records": ROWS})["metrics"]["rows"] == 2


def test_the_digest_is_of_the_stamped_bytes_so_it_pins_the_run(tmp_path):
    one = node(tmp_path).run(ctx(tmp_path, "doc-2026-10-06-aaaaaaaa"), {"records": ROWS})
    two = node(tmp_path).run(ctx(tmp_path, "doc-2026-10-06-bbbbbbbb"), {"records": ROWS})
    assert one["metrics"]["sha256"] != two["metrics"]["sha256"]


def test_the_template_survives_a_run_and_a_failed_run(tmp_path):
    writer = node(tmp_path)
    template = writer.params["path"]
    writer.run(ctx(tmp_path), {"records": ROWS})
    assert writer.params["path"] == template
    with pytest.raises(FileExistsError):
        writer.run(ctx(tmp_path), {"records": ROWS})
    assert writer.params["path"] == template, "a refusal leaves the template in place too"


def test_a_row_that_already_names_another_run_is_refused_not_overwritten(tmp_path):
    with pytest.raises(ValueError, match=r"row 1.*run_id.*'other'"):
        node(tmp_path).run(ctx(tmp_path), {"records": [{"x": 1}, {"x": 2, RUN_ID_FIELD: "other"}]})
    assert not (tmp_path / f"table-{RUN}.jsonl").exists(), "nothing reaches disk"
    with pytest.raises(ValueError, match="run_id"):
        node(tmp_path).run(ctx(tmp_path), {"records": [{"x": 1, RUN_ID_FIELD: None}]})
    out = node(tmp_path).run(ctx(tmp_path), {"records": [{"x": 1, RUN_ID_FIELD: RUN}]})
    assert lines(out["path"]) == [{"x": 1, RUN_ID_FIELD: RUN}], "a row already stamped with this run is idempotent"


def test_a_run_name_that_cannot_be_a_stream_name_is_refused_by_name_before_any_byte_lands(tmp_path):
    for bad in ("Doc-2026-10-06-aaaaaaaa", "doc.v2-2026-10-06-aaaaaaaa", "doc v2-2026-10-06-aaaaaaaa",
                "_doc-2026-10-06-aaaaaaaa", "-doc-2026-10-06-aaaaaaaa", "dóc-2026-10-06-aaaaaaaa"):
        with pytest.raises(ValueError, match="run name") as caught:
            node(tmp_path).run(ctx(tmp_path, bad), {"records": ROWS})
        assert bad in str(caught.value)
    assert os.listdir(tmp_path) == [], "nothing was written for any of them"


def test_there_must_be_a_run_frame(tmp_path):
    for frame in (None, NodeContext(name="d", asof="2026-10-06", run_dir="")):
        with pytest.raises(ValueError, match="run frame"):
            node(tmp_path).run(frame, {"records": ROWS})
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("name, ok", [
    ("doc-2026-10-06-aaaaaaaa", True), ("a", True), ("0", True), ("a_b-c", True), ("a" * 200, True),
    ("", False), ("A", False), ("a.b", False), ("a b", False), ("_a", False), ("-a", False), ("a\n", False),
    ("a/b", False), ("é", False),
])
def test_run_name_problems_is_the_one_rule_and_run_name_applies_it(tmp_path, name, ok):
    assert (run_name_problems(name) == []) is ok
    if ok:
        assert RecordsWriteRun.run_name(ctx(tmp_path, name)) == name
    elif "/" not in name:  # a path separator makes the run dir's BASE name something else
        with pytest.raises(ValueError, match="run name"):
            RecordsWriteRun.run_name(ctx(tmp_path, name or "x/ "))


def test_run_name_problems_agrees_with_the_platforms_stream_rule(tmp_path):
    """The platform's segment rule is private to onboarding, so it is restated here and PINNED against
    its public doorway: ``payload_files`` refuses a stream name the platform will not accept."""
    from dskit.onboarding import OnboardingRoot, payload_files

    store = OnboardingRoot.create(str(tmp_path / "ob"))
    names = ["a", "a-b_c", "0x", "_a", "-a", "A", "a.b", "a b", "a\n", "a/b", "é", "doc-2026-10-06-aaaaaaaa",
             "a" * 80, "-", "_", "a--b", "a__b"]
    for name in names:
        try:
            payload_files(store.root, "no-such-source", name)
        except Exception as err:
            platform_accepts = "filesystem-safe" not in str(err)
        else:
            platform_accepts = True
        assert (run_name_problems(name) == []) is platform_accepts, name


def test_the_rule_is_pinned_to_the_driver_that_names_the_run(tmp_path):
    """``run_name_problems(document_name)`` stands in for the whole run name because the rest of it
    (``-<as-of>-<hash8>``) is always safe: pinned against what the driver actually names a run dir."""
    for document_name in ("feat", "feat.v2", "a_b-c", "0abc"):
        registry = NodeKindRegistry()
        registry.register("rows", Rows)
        document = PipelineDocument.from_obj({
            "name": document_name, "outputs": {"run_root": str(tmp_path / "runs")},
            "pipeline": {"rows": {"uses": "rows", "params": {"rows": ROWS}}}})
        result = run_document(document, asof="2026-10-06", registry=registry, journal=False)
        assert (run_name_problems(os.path.basename(result.run_dir)) == []) == (run_name_problems(document_name) == [])


# -- plan time ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path, match", [
    ("out/table.jsonl", r"\{run\}"),
    ("out/{run}-{run}.jsonl", r"\{run\}"),
    ("out/{run}/table.jsonl", "file name"),
    ("out/table.{run}.jsonl", "stem"),
    ("out/Table-{run}.jsonl", "stream name"),
    ("out/table {run}.jsonl", "stream name"),
    ("out/table-{run}-{x}.jsonl", "stream name"),
    ("out/_table-{run}.jsonl", "stream name"),
    ("", "path"),
])
def test_a_path_that_cannot_make_one_stream_per_run_is_refused_at_validation(path, match):
    problems = RecordsWriteRun.validate_params({"path": path, "source": "x"})
    assert problems and any(re.search(match, p) for p in problems), problems
    with pytest.raises(ConfigError, match=match):
        RecordsWriteRun("write", {"path": path, "source": "x"})


@pytest.mark.parametrize("path", ["out/table.{run}", "table.{run}", "out/t.x.{run}"])
def test_a_run_placeholder_in_the_extension_is_refused_or_every_run_would_write_one_stem(path):
    """B1-05 (ADR-0247 F2): the stream is named by the stem, so ``table`` + ``.{run}`` would be one stream for every run."""
    problems = RecordsWriteRun.validate_params({"path": path, "source": "x"})
    assert any("before the extension" in p for p in problems), problems
    with pytest.raises(ConfigError, match="before the extension"):
        RecordsWriteRun("write", {"path": path, "source": "x"})


@pytest.mark.parametrize("path", [
    "out/table-{run}.jsonl", "{run}.jsonl", "out/{run}", "~/features/t_{run}-x.ndjson",
    "$tables.path", "out/dir.v2/Cap/table-{run}.jsonl",
])
def test_a_path_with_the_run_in_a_safe_file_stem_is_legal(path):
    assert RecordsWriteRun.validate_params({"path": path, "source": "x"}) == []


def test_validation_is_total_over_wrong_typed_paths():
    for junk in (None, 5, 1.5, True, [], {}, ["{run}"], b"{run}"):
        assert isinstance(RecordsWriteRun.validate_params({"path": junk, "source": "x"}), list)


def test_default_deny_is_inherited(tmp_path):
    with pytest.raises(ConfigError, match="surprise"):
        node(tmp_path, surprise=1)
    assert RecordsWriteRun._PARAMS == RecordsWrite._PARAMS, "no knob was added: the identity grammar is unchanged"


def test_the_planner_refuses_a_bad_path_before_anything_runs(tmp_path):
    registry = NodeKindRegistry()
    registry.register("rows", Rows)
    register(registry)
    document = PipelineDocument.from_obj({
        "name": "feat", "outputs": {"run_root": str(tmp_path / "runs")},
        "pipeline": {
            "rows": {"uses": "rows", "params": {"rows": ROWS}},
            "write": {"uses": "records-write-run", "inputs": {"records": "$rows.records"},
                      "params": {"path": str(tmp_path / "table.jsonl"), "source": "x"}}}})
    with pytest.raises(ConfigError, match=r"pipeline\.write.*\{run\}"):
        plan(document, registry)
    with pytest.raises(ConfigError, match=r"\{run\}"):
        run_document(document, asof="2026-10-06", registry=registry, journal=False)
    assert not (tmp_path / "runs").exists(), "no run directory was claimed"


# -- the kind and its registration --------------------------------------------------------------


def test_the_kind_is_named_by_the_module_and_claimed_only_by_register():
    assert NODE_KINDS == (("records-write-run", RecordsWriteRun),)
    registry = NodeKindRegistry()
    assert "records-write-run" not in registry
    register(registry)
    register(registry)  # idempotent
    assert registry.get("records-write-run")[0] is RecordsWriteRun


def test_register_without_a_registry_claims_the_toolkit_default(monkeypatch):
    """B1-05: ``register()`` means the toolkit's registry, which a test swaps for a fresh one so nothing leaks."""
    fresh = NodeKindRegistry()
    monkeypatch.setattr(sys.modules[RecordsWriteRun.__module__], "DEFAULT_NODE_KINDS", fresh)
    assert register() is None
    register()  # idempotent
    assert fresh.get("records-write-run")[0] is RecordsWriteRun


def test_register_leaves_an_already_claimed_name_alone():
    class Mine(RecordsWriteRun):
        pass

    registry = NodeKindRegistry()
    registry.register("records-write-run", Mine)
    register(registry)
    assert registry.get("records-write-run")[0] is Mine


def test_importing_the_module_registers_nothing_into_the_toolkit_registry():
    import subprocess

    script = ("from dskit.pipeline.node import DEFAULT_NODE_KINDS as K; before = set(K.kinds()); "
              "import dskit.pipeline.kinds_run_write; "
              "assert set(K.kinds()) == before and 'records-write-run' not in K.kinds(), K.kinds()")
    repo = str(pathlib.Path(sys.modules[RecordsWriteRun.__module__].__file__).resolve().parents[2])  # THIS tree
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, cwd=repo)
    assert done.returncode == 0, done.stderr


def test_a_document_names_the_kind_by_import_path(tmp_path):
    registry = NodeKindRegistry()
    registry.register("rows", Rows)
    document = PipelineDocument.from_obj({
        "name": "feat", "outputs": {"run_root": str(tmp_path / "runs")},
        "pipeline": {
            "rows": {"uses": "rows", "params": {"rows": ROWS}},
            "write": {"uses": "dskit.pipeline.kinds_run_write:RecordsWriteRun",
                      "inputs": {"records": "$rows.records"},
                      "params": {"path": str(tmp_path / "table-{run}.jsonl"), "source": "x"}}}})
    result = run_document(document, asof="2026-10-06", registry=registry, journal=False)
    assert result.state == "ran"
    written = result.outputs["write"]["path"]
    assert os.path.basename(written) == f"table-{os.path.basename(result.run_dir)}.jsonl"
    assert [r[RUN_ID_FIELD] for r in lines(written)] == [os.path.basename(result.run_dir)] * 2


# -- the platform bar -----------------------------------------------------------------------------


def _probes(tmp_path):
    (tmp_path / "produced").mkdir(exist_ok=True)
    records = [{"ticker": "A", "decision_ms": 1}, {"ticker": "B", "decision_ms": 2}, {"ticker": "C", "decision_ms": 3}]
    return {"records-write-run": NodeProbe(
        params={"path": str(tmp_path / "produced" / "records-{run}.jsonl"),
                "source": "the conformance run's rows", "expect": 3},
        required=("path", "source"), inputs={"records": records}, stream_ports=("records",), runnable=True)}


TestRecordsWriteRunConformance = conformance_suite(
    registry=NODE_KINDS, probes=_probes, expected_roles={"records-write-run": "report"},
    name="TestRecordsWriteRunConformance")


# -- end to end: one published stream per run -------------------------------------------------------


class Rows(Node):
    """A source of literal rows (role ``data``), so a document can change what it publishes."""

    role = "data"
    outputs = ("records",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) == {"rows"} else [f"rows is the only param, got {sorted(params)}"]

    def fingerprint(self):
        return {"kind": "Rows", "rows": self.params["rows"]}

    def run(self, ctx, inputs):
        return {"records": [dict(r) for r in self.params["rows"]]}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    import dskit.onboarding.acquire as acquire_module

    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(acquire_module, "utc_now",
                        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"))


def publish_run(tmp_path, table_dir, rows, document_name="feat"):
    """Run one document that writes ``rows`` as a run-identified table; return the run's name."""
    registry = NodeKindRegistry()
    registry.register("rows", Rows)
    register(registry)
    document = PipelineDocument.from_obj({
        "name": document_name, "outputs": {"run_root": str(tmp_path / "runs")},
        "pipeline": {
            "rows": {"uses": "rows", "params": {"rows": rows}},
            "write": {"uses": "records-write-run", "inputs": {"records": "$rows.records"},
                      "params": {"path": str(table_dir / "table-{run}.jsonl"), "source": "published rows"}}}})
    result = run_document(document, asof="2026-10-06", registry=registry, journal=False)
    assert result.state == "ran", result.error
    return os.path.basename(result.run_dir)


def test_a_changed_document_publishes_a_second_stream_and_the_first_still_reads_back_unchanged(tmp_path):
    from dskit.onboarding import OnboardingRoot, run_acquisition, scan_stream

    table_dir = tmp_path / "tables"
    table_dir.mkdir()
    store = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = store.registry()
    vid = registry.register("source_config", {
        "name": "feat", "catalog_source": "x", "connector": "localtables",
        "config": {"path": str(table_dir), "layout": "file", "effective_field": "decision_ms",
                   "effective_unit": "ms"}}, origin="test")
    registry.transition(vid, "active", origin="test")

    def read(run):
        got = scan_stream(store.root, "feat", f"table-{run}", key_fields=("ticker", "decision_ms"))
        return sorted(((r["ticker"], r["decision_ms"], r["value"], r[RUN_ID_FIELD]) for r in got))

    rows_a = [{"ticker": "A", "decision_ms": 1000, "value": 1.0}, {"ticker": "B", "decision_ms": 2000, "value": 2.0}]
    run_a = publish_run(tmp_path, table_dir, rows_a)
    first = run_acquisition(store, registry, "feat", f"table-{run_a}", "backfill")
    assert first["records"] == 2 and first["snapshot"]
    assert read(run_a) == [("A", 1000, 1.0, run_a), ("B", 2000, 2.0, run_a)]

    again = run_acquisition(store, registry, "feat", f"table-{run_a}", "backfill")
    assert again["snapshot"] is None and again["records"] == 0, "an unchanged table re-acquired makes no snapshot"

    rows_b = [{**rows_a[0], "value": 10.0}, {**rows_a[1], "value": 20.0}]  # the same keys, new values
    run_b = publish_run(tmp_path, table_dir, rows_b)
    assert run_b != run_a, "a changed document is a different run"
    second = run_acquisition(store, registry, "feat", f"table-{run_b}", "backfill")
    assert second["records"] == 2 and second["snapshot"]
    assert read(run_b) == [("A", 1000, 10.0, run_b), ("B", 2000, 20.0, run_b)], "the new table is not shadowed"
    assert read(run_a) == [("A", 1000, 1.0, run_a), ("B", 2000, 2.0, run_a)], "the first table is unchanged"
    assert sorted(os.listdir(table_dir)) == sorted([f"table-{run_a}.jsonl", f"table-{run_b}.jsonl"])


def test_a_document_whose_name_cannot_make_a_stream_name_fails_the_run_and_writes_nothing(tmp_path):
    table_dir = tmp_path / "tables"
    table_dir.mkdir()
    with pytest.raises(AssertionError, match="feat.v2"):
        publish_run(tmp_path, table_dir, ROWS, document_name="feat.v2")
    assert os.listdir(table_dir) == [], "the refused run left no table behind"
    assert run_name_problems("feat.v2") and not run_name_problems("feat"), "and the pre-flight said so"
