"""ArtifactLinks: a read-only manifest of another run's outputs, shown in a report (ADR-0283).

The node names each expected artifact by path with its SHA-256 and byte
count when the file exists and says ``pending`` when it does not; it reads a
declared grid table for status and cross-checks a pins file, and it writes
only beside the report, never into the linked root.
"""

import csv
import hashlib
import os
import stat

import pytest

from dskit.evaluation.links import ArtifactLinks
from dskit.pipeline.base import ConfigError

CASES = ("control", "shock")
UNITS = ("a", "b")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _tree(root):
    """Every file under ``root`` with its bytes and mtime, to prove nothing moved."""
    out = {}
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[path] = (fh.read(), os.stat(path).st_mtime_ns)
    return out


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "evidence"
    for case in CASES:
        (root / "cohort" / case).mkdir(parents=True)
        for unit in UNITS:
            (root / "cohort" / case / f"{unit}.md").write_bytes(f"{case}-{unit}".encode())
    (root / "report.md").write_bytes(b"# stress report\n")
    return root


def _summary(root, rows):
    with open(root / "summary.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["cohort", "scenario", "method", "status"])
        writer.writeheader()
        writer.writerows(rows)


def _full_rows(status="complete"):
    return [{"cohort": "cohort", "scenario": case, "method": unit, "status": status}
            for case in CASES for unit in UNITS]


@pytest.fixture
def report_dir(tmp_path):
    out = tmp_path / "report"
    out.mkdir()
    (out / "summary.md").write_text("# Title\n\n## Provenance\n\n", encoding="utf-8")
    (out / "report.html").write_text(
        "<!DOCTYPE html><html><body><h1>T</h1><main><section id=a></section></main></body></html>\n",
        encoding="utf-8")
    return out


def _params(root, **extra):
    params = {
        "root": str(root),
        "files": [
            {"id": "report", "path": "report.md"},
            {"id": "daybook", "path": "cohort/{scenario}/{method}.md",
             "axes": {"scenario": list(CASES), "method": list(UNITS)}},
            {"id": "summary", "path": "summary.csv"},
            {"id": "pins", "path": "pins.json"},
        ],
    }
    params.update(extra)
    return params


def _run(root, report_dir, **extra):
    paths = {"summary": str(report_dir / "summary.md"), "html": str(report_dir / "report.html")}
    return ArtifactLinks("links", _params(root, **extra)).run(None, {"paths": paths})


def _by_path(rows):
    return {row["path"]: row for row in rows}


def test_a_present_file_carries_its_hash_and_size_and_an_absent_one_is_pending(root, report_dir):
    out = _run(root, report_dir)
    rows = _by_path(out["rows"])
    report = rows[str(root / "report.md")]
    assert report["status"] == "present"
    assert report["sha256"] == _sha(b"# stress report\n")
    assert report["bytes"] == len(b"# stress report\n")
    missing = rows[str(root / "pins.json")]
    assert missing["status"] == "pending"
    assert missing["sha256"] is None and missing["bytes"] is None


def test_axes_expand_to_the_full_product_in_declared_order(root, report_dir):
    out = _run(root, report_dir)
    books = [row for row in out["rows"] if row["id"] == "daybook"]
    assert [row["key"] for row in books] == [
        "scenario=control,method=a", "scenario=control,method=b",
        "scenario=shock,method=a", "scenario=shock,method=b"]
    assert all(row["status"] == "present" for row in books)
    assert books[0]["path"] == str(root / "cohort" / "control" / "a.md")


def test_nothing_is_fabricated_when_the_whole_root_is_absent(tmp_path, report_dir):
    out = _run(tmp_path / "not-there", report_dir)
    assert {row["status"] for row in out["rows"]} == {"pending"}
    assert all(row["sha256"] is None for row in out["rows"])


def test_the_pins_file_cross_checks_each_link(root, report_dir):
    import json
    raw = (root / "cohort" / "control" / "a.md").read_bytes()
    pins = {"files": {
        "cohort/control/a.md": {"sha256": _sha(raw), "bytes": len(raw)},
        "cohort/control/b.md": {"sha256": "0" * 64, "bytes": 1},
        "report.md": {"sha256": _sha(b"# stress report\n"), "bytes": 16}}}
    (root / "pins.json").write_text(json.dumps(pins))
    out = _run(root, report_dir, pins={"file": "pins.json", "files_key": "files"})
    rows = _by_path(out["rows"])
    assert rows[str(root / "cohort" / "control" / "a.md")]["pinned"] == "match"
    assert rows[str(root / "cohort" / "control" / "b.md")]["pinned"] == "mismatch"
    assert rows[str(root / "cohort" / "shock" / "a.md")]["pinned"] == "unpinned"
    assert rows[str(root / "pins.json")]["pinned"] == "n/a"


def test_without_a_pins_file_present_the_pin_column_says_pending(root, report_dir):
    out = _run(root, report_dir, pins={"file": "pins.json", "files_key": "files"})
    assert {row["pinned"] for row in out["rows"] if row["id"] != "pins"} == {"pending"}


TABLE = {"file": "summary.csv", "where": {"cohort": "cohort"}, "case": "scenario",
         "unit": "method", "cases": list(CASES), "units": list(UNITS),
         "ok": {"column": "status", "equals": "complete"},
         "checks": {"gate": ["control"], "grid": list(CASES)}}


def _checks(out):
    return {row["check"]: row for row in out["checks"]}


def test_checks_are_pending_until_the_table_exists(root, report_dir):
    out = _run(root, report_dir, table=TABLE)
    assert {row["status"] for row in out["checks"]} == {"pending"}
    assert {row["status"] for row in out["cells"]} == {"pending"}


def test_checks_complete_when_every_expected_cell_is_ok(root, report_dir):
    _summary(root, _full_rows())
    out = _run(root, report_dir, table=TABLE)
    checks = _checks(out)
    assert checks["gate"]["status"] == "complete" and checks["gate"]["found"] == 2
    assert checks["grid"]["status"] == "complete" and checks["grid"]["expected"] == 4


def test_a_failed_cell_or_a_missing_cell_makes_the_check_incomplete(root, report_dir):
    rows = _full_rows()
    rows[0]["status"] = "unsupported"      # control/a
    rows.pop()                              # shock/b absent
    rows.append({"cohort": "other", "scenario": "shock", "method": "b",
                 "status": "complete"})     # another cohort must not fill it
    _summary(root, rows)
    out = _run(root, report_dir, table=TABLE)
    checks = _checks(out)
    assert checks["gate"]["status"] == "incomplete" and checks["gate"]["found"] == 1
    assert checks["grid"]["status"] == "incomplete" and checks["grid"]["found"] == 2
    cells = {(c["case"], c["unit"]): c["status"] for c in out["cells"]}
    assert cells[("control", "a")] == "not ok (unsupported)"
    assert cells[("shock", "b")] == "missing"
    assert cells[("control", "b")] == "ok"


def test_the_csv_and_the_report_text_carry_the_links(root, report_dir):
    out = _run(root, report_dir, table=TABLE, title="Stress links")
    csv_path = report_dir / "artifact-links.csv"
    assert out["paths"]["csv"] == str(csv_path)
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("id,key,path,status,sha256,bytes")
    assert len(lines) == 1 + len(out["rows"])
    summary = (report_dir / "summary.md").read_text()
    assert "## Stress links" in summary and str(root / "report.md") in summary
    assert "pending" in summary
    page = (report_dir / "report.html").read_text()
    assert '<section id="artifact-links">' in page and "Stress links" in page
    assert page.index("artifact-links") < page.index("</main>")
    assert "http" not in page.split('id="artifact-links"', 1)[1]


def test_a_rerun_replaces_the_block_instead_of_stacking_it(root, report_dir):
    _run(root, report_dir, title="Stress links")
    (root / "pins.json").write_bytes(b"{}")
    _run(root, report_dir, title="Stress links")
    assert (report_dir / "summary.md").read_text().count("## Stress links") == 1
    assert (report_dir / "report.html").read_text().count('id="artifact-links"') == 1
    pins_row = [row for row in (report_dir / "summary.md").read_text().splitlines()
                if "pins.json" in row][0]
    assert "present" in pins_row and "pending" not in pins_row


def test_the_linked_root_is_never_written(root, report_dir):
    _summary(root, _full_rows())
    before = _tree(root)
    for path in (root, *root.rglob("*")):                 # a write would now raise
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    try:
        _run(root, report_dir, table=TABLE)
        assert _tree(root) == before
    finally:
        for path in (root, *root.rglob("*")):
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


def test_a_report_directory_inside_the_linked_root_is_refused(root):
    out = root / "report"
    out.mkdir()
    (out / "summary.md").write_text("# T\n")
    (out / "report.html").write_text("<main></main>")
    paths = {"summary": str(out / "summary.md"), "html": str(out / "report.html")}
    before = _tree(root)
    with pytest.raises(ValueError, match="inside"):
        ArtifactLinks("links", _params(root)).run(None, {"paths": paths})
    assert _tree(root) == before


def test_a_linked_root_inside_the_report_directory_is_refused(tmp_path):
    out = tmp_path / "report"
    nested = out / "evidence"
    nested.mkdir(parents=True)
    (out / "summary.md").write_text("# T\n")
    (out / "report.html").write_text("<main></main>")
    paths = {"summary": str(out / "summary.md"), "html": str(out / "report.html")}
    with pytest.raises(ValueError, match="inside"):
        ArtifactLinks("links", _params(nested)).run(None, {"paths": paths})


@pytest.mark.parametrize("bad", [
    {"nope": 1},
    {"root": ""},
    {"files": []},
    {"files": [{"id": "x", "path": "/abs/path"}]},
    {"files": [{"id": "x", "path": "../escape"}]},
    {"files": [{"id": "x", "path": "{a}.md"}]},                       # axis not declared
    {"files": [{"id": "x", "path": "f.md", "axes": {"a": ["1"]}}]},   # axis unused
    {"files": [{"id": "x", "path": "a"}, {"id": "x", "path": "b"}]},  # duplicate id
    {"table": {"file": "s.csv"}},                                      # incomplete table
    {"pins": {"file": "pins.json"}},
    {"csv_name": "../evidence/summary.csv"},                           # must be a bare name
    {"csv_name": "/abs/links.csv"},
    {"csv_name": "sub/links.csv"},
    {"csv_name": ".."},
    {"csv_name": "links.txt"},
    {"files": [{"id": "x", "path": "{}.md", "axes": {"": ["a"]}}]},   # empty placeholder
    {"files": [{"id": "x", "path": "{0}.md", "axes": {"0": ["a"]}}]}, # numeric placeholder
    {"files": [{"id": "x", "path": "{a!r}.md", "axes": {"a": ["a"]}}]},   # conversion
    {"files": [{"id": "x", "path": "{a:>5}.md", "axes": {"a": ["a"]}}]},  # format spec
    {"files": [{"id": "x", "path": "{a.b}.md", "axes": {"a.b": ["a"]}}]},
    {"files": [{"id": "x", "path": "{a}.md", "axes": {"a": ["."]}}]},
    {"files": [{"id": "x", "path": "{a}.md", "axes": {"a": [".."]}}]},
])
def test_unusable_params_are_refused(root, bad):
    with pytest.raises(ConfigError):
        ArtifactLinks("links", {**_params(root), **bad})


def test_the_inputs_must_name_the_report_files(root):
    node = ArtifactLinks("links", _params(root))
    assert node.validate_inputs({"paths": {"summary": "x"}})
    assert not node.validate_inputs({"paths": {"summary": "x", "html": "y"}})


def test_the_node_class_is_well_formed():
    from dskit.pipeline.node import node_class_errors
    assert node_class_errors(ArtifactLinks, "report") == []


def test_a_document_wires_it_after_the_report_node(tmp_path, root):
    from dskit.pipeline.document import NodeSpec, OutputsConfig, PipelineDocument
    from dskit.pipeline.driver import run_document
    document = PipelineDocument(
        name="links-doorway",
        pipeline={
            "events": NodeSpec(uses="tests.evaluation.test_report:ScenarioEvents"),
            "report": NodeSpec(uses="dskit.evaluation.nodes:EvaluationReport",
                               inputs={"events": "$events.events"},
                               params={"out_dir": "evaluation"}),
            "links": NodeSpec(uses="dskit.evaluation.links:ArtifactLinks",
                              inputs={"paths": "$report.paths"},
                              params=_params(root, title="Stress links")),
        },
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    )
    before = _tree(root)
    result = run_document(document, asof="2026-09-02", journal=False)
    assert result.state == "ran", result
    paths = result.outputs["report"]["paths"]
    assert "## Stress links" in open(paths["summary"], encoding="utf-8").read()
    assert os.path.isfile(result.outputs["links"]["paths"]["csv"])
    assert _tree(root) == before


# --- review round: csv_name containment, pin-gated checks, symlinks ------------------------


@pytest.mark.parametrize("name", ["decisions.csv", "trades.csv"])
def test_a_csv_name_that_collides_with_a_report_file_is_refused(root, report_dir, name):
    paths = {"summary": str(report_dir / "summary.md"), "html": str(report_dir / "report.html"),
             "decisions": str(report_dir / "decisions.csv"), "trades": str(report_dir / "trades.csv")}
    with pytest.raises(ValueError, match="collides"):
        ArtifactLinks("links", _params(root, csv_name=name)).run(None, {"paths": paths})


def test_the_csvs_are_written_last_and_only_beside_the_report(root, report_dir, monkeypatch):
    import dskit.evaluation.links as links
    order = []
    real = links.atomic_write
    monkeypatch.setattr(links, "atomic_write", lambda path, raw: (order.append(os.path.basename(path)),
                                                                   real(path, raw)))
    _run(root, report_dir, table=TABLE)
    assert order == ["summary.md", "report.html", "artifact-links.csv", "artifact-links-cells.csv"]


def _pin_table(root, report_dir, pinned=True, summary_text=None, **extra):
    import json
    _summary(root, _full_rows())
    raw = (root / "summary.csv").read_bytes()
    entry = {"sha256": _sha(raw if summary_text is None else summary_text), "bytes": len(raw)}
    files = {"summary.csv": entry} if pinned else {}
    (root / "pins.json").write_text(json.dumps({"files": files}))
    return _run(root, report_dir, table=TABLE,
                pins={"file": "pins.json", "files_key": "files"}, **extra)


def test_checks_are_complete_only_when_the_pins_confirm_the_table(root, report_dir):
    out = _pin_table(root, report_dir)
    assert {c["status"] for c in out["checks"]} == {"complete"}
    assert {c["pinned"] for c in out["checks"]} == {"match"}


def test_a_pin_mismatch_or_an_unpinned_table_is_unverified(root, report_dir):
    out = _pin_table(root, report_dir, summary_text=b"tampered")
    assert {c["status"] for c in out["checks"]} == {"unverified"}
    out = _pin_table(root, report_dir, pinned=False)
    assert {c["status"] for c in out["checks"]} == {"unverified"}


def test_a_table_without_its_pins_file_is_pending_not_complete(root, report_dir):
    _summary(root, _full_rows())        # execute() writes summary.csv, then may fail before pins.json
    out = _run(root, report_dir, table=TABLE, pins={"file": "pins.json", "files_key": "files"})
    assert {c["status"] for c in out["checks"]} == {"pending"}


def test_without_pins_declared_a_check_says_so(root, report_dir):
    _summary(root, _full_rows())
    out = _run(root, report_dir, table=TABLE)
    assert {c["pinned"] for c in out["checks"]} == {"n/a"}
    assert {c["status"] for c in out["checks"]} == {"complete"}


def test_a_broken_symlink_inside_the_root_is_pending(root, report_dir):
    os.symlink(str(root / "nowhere.md"), str(root / "report.md.link"))
    out = _run(root, report_dir, files=[{"id": "x", "path": "report.md.link"}])
    assert out["rows"][0]["status"] == "pending"


@pytest.mark.parametrize("where", ["link", "pins", "table"])
def test_a_symlink_escaping_the_root_is_refused(root, report_dir, tmp_path, where):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    name = {"link": "escape.md", "pins": "pins.json", "table": "summary.csv"}[where]
    os.symlink(str(outside), str(root / name))
    extra = dict(files=[{"id": "x", "path": "escape.md"}]) if where == "link" else {}
    if where == "pins":
        extra["pins"] = {"file": "pins.json", "files_key": "files"}
    if where == "table":
        extra["table"] = TABLE
    with pytest.raises(ValueError, match="outside"):
        _run(root, report_dir, **extra)


# --- review round 2: one read per file, symlinked outputs --------------------------------


@pytest.mark.parametrize("name", ["artifact-links.csv", "artifact-links-cells.csv"])
def test_an_existing_csv_symlinked_into_the_root_is_refused(root, report_dir, name):
    (root / "victim.txt").write_text("keep")
    os.symlink(str(root / "victim.txt"), str(report_dir / name))
    before = _tree(root)
    with pytest.raises(ValueError, match="directly in the report"):
        _run(root, report_dir, table=TABLE)
    assert _tree(root) == before


def test_a_symlinked_report_directory_resolving_into_the_root_is_refused(root, tmp_path):
    (root / "inner").mkdir()
    (root / "inner" / "summary.md").write_text("# T\n")
    (root / "inner" / "report.html").write_text("<main></main>")
    link = tmp_path / "report-link"
    os.symlink(str(root / "inner"), str(link))
    paths = {"summary": str(link / "summary.md"), "html": str(link / "report.html")}
    before = _tree(root)
    with pytest.raises(ValueError, match="inside"):
        ArtifactLinks("links", _params(root)).run(None, {"paths": paths})
    assert _tree(root) == before


def test_a_symlinked_pins_file_outside_the_root_is_refused(root, report_dir, tmp_path):
    outside = tmp_path / "outside-pins.json"
    outside.write_text('{"files": {}}')
    os.symlink(str(outside), str(root / "pins.json"))
    with pytest.raises(ValueError, match="outside"):
        _run(root, report_dir, pins={"file": "pins.json", "files_key": "files"})


def test_a_table_rewritten_after_it_was_read_cannot_turn_a_check_complete(
        root, report_dir, monkeypatch):
    """The checked cells and the pinned digest come from one read of the bytes."""
    rows = _full_rows()
    rows[0]["status"] = "unsupported"
    _summary(root, rows)
    import json
    raw = (root / "summary.csv").read_bytes()
    (root / "pins.json").write_text(json.dumps(
        {"files": {"summary.csv": {"sha256": _sha(raw), "bytes": len(raw)}}}))
    real = ArtifactLinks._load

    def load_then_rewrite(self, root_dir, relative, *args, **kwargs):
        out = real(self, root_dir, relative, *args, **kwargs)
        if relative == "summary.csv":
            _summary(root, _full_rows())       # every cell now ok, bytes differ from the pin
        return out

    monkeypatch.setattr(ArtifactLinks, "_load", load_then_rewrite)
    out = _run(root, report_dir, table=TABLE, pins={"file": "pins.json", "files_key": "files"})
    checks = _checks(out)
    assert checks["gate"]["status"] == "incomplete"          # the pinned bytes, not the rewrite
    assert checks["grid"]["status"] == "incomplete"
    assert {c["pinned"] for c in out["checks"]} == {"match"}


def test_the_rows_and_the_checks_agree_on_the_tables_pin(root, report_dir):
    out = _pin_table(root, report_dir, summary_text=b"tampered")
    row = _by_path(out["rows"])[str(root / "summary.csv")]
    assert row["pinned"] == "mismatch"
    assert {c["pinned"] for c in out["checks"]} == {row["pinned"]}
