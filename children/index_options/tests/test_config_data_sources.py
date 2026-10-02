"""Guard: the shipped run-step documents read data only through the onboarding store.

Owner rule ("Data enters through onboarding"): a run names the onboarded source and stream it
reads, never a directory somewhere on the machine. In a ``configs/run-step*.json`` document

- ``root`` (the onboarding store, ``data.root`` and every ``pipeline.*.params.root``) is the one
  key that may hold an absolute path, and only a store in ``STORE_ROOTS``;
- every other string value is path-free: no leading ``/`` or ``~`` and no ``/home/`` anywhere,
  notes included. Output and hand-off locations (``pipeline_runs/...``) are RELATIVE, resolved
  from the working directory, and so pass;
- ``data.archive_root``, ``data.surface``, ``data.lifecycle``, ``data.chain_features`` and
  ``data.decision_regions.archive_root`` are store references, never plain strings.

The origin path of a file belongs in its registration config (``configs/source-store-*.json``),
once.
"""

import json
from pathlib import Path

import pytest

from index_options.datafiles import entry_problems

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
RUN_STEPS = sorted(CONFIGS.glob("run-step*.json"))

#: The onboarding stores a run-step document may name as ``root`` by absolute path.
STORE_ROOTS = {"/home/russell/data/index_options/ob"}

#: Explicit exemptions, ``(file name, JSON pointer) -> reason``. None today: add one only for a
#: string that must look like a path and is neither a ``root`` nor a data entry.
ALLOWED = {}

#: ``data`` entries that name files (``relpath`` required) and the one that names a tree.
FILE_ENTRIES = ("surface", "lifecycle", "chain_features")
TREE_ENTRIES = ("archive_root",)


def _path_like(value):
    return value.startswith(("/", "~")) or "/home/" in value


def _walk(node, pointer="", key=None):
    """Yield ``(pointer, key, string)`` for every string in a JSON value."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _walk(value, f"{pointer}/{name}", name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{pointer}/{index}", key)
    elif isinstance(node, str):
        yield pointer, key, node


def stray_paths(name, document):
    """List the strings of ``document`` that break the rule (``name`` is its file name)."""
    found = []
    for pointer, key, value in _walk(document):
        if (name, pointer) in ALLOWED or not _path_like(value):
            continue
        if key == "root":
            if value not in STORE_ROOTS:
                found.append(f"{pointer}: root {value!r} is not a registered store {sorted(STORE_ROOTS)}")
        else:
            found.append(f"{pointer}: {value[:80]!r} looks like a filesystem path")
    return found


def plain_data_entries(document):
    """List the ``data`` entries of ``document`` that are not store references."""
    data = document.get("data")
    if not isinstance(data, dict):
        return []
    entries = [(f"/data/{k}", data[k], k in TREE_ENTRIES) for k in FILE_ENTRIES + TREE_ENTRIES
               if k in data]
    regions = data.get("decision_regions")
    if isinstance(regions, dict) and "archive_root" in regions:
        entries.append(("/data/decision_regions/archive_root", regions["archive_root"], True))
    found = []
    for pointer, entry, tree in entries:
        if isinstance(entry, str):
            found.append(f"{pointer}: plain path {entry[:60]!r}; use a store reference")
        else:
            found += [f"{pointer}: {p}" for p in entry_problems(pointer, entry, tree=tree)]
    return found


def test_there_are_run_step_documents_to_guard():
    names = {p.name for p in RUN_STEPS}
    assert {"run-step1-expiry-coverage.json", "run-step1b-feature-engineering.json",
            "run-step4-feature-selection.json", "run-step5-model-zoo.json",
            "run-step6-hpo.json", "run-step4-feature-selection-iwm.json",
            "run-step5-model-zoo-iwm.json", "run-step6-hpo-iwm.json"} <= names


@pytest.mark.parametrize("path", RUN_STEPS, ids=lambda p: p.name)
def test_a_run_step_document_names_no_data_path_outside_the_store(path):
    assert stray_paths(path.name, json.loads(path.read_text())) == []


@pytest.mark.parametrize("path", RUN_STEPS, ids=lambda p: p.name)
def test_a_run_step_documents_data_entries_are_store_references(path):
    assert plain_data_entries(json.loads(path.read_text())) == []


@pytest.mark.parametrize("name", ["run-step1-expiry-coverage.json",
                                  "run-step1b-feature-engineering.json"])
def test_steps_1_and_1b_read_the_panel_from_the_store(name):
    params = json.loads((CONFIGS / name).read_text())["pipeline"]["source"]["params"]
    assert (params["root"], params["source"], params["stream"]) == (
        "/home/russell/data/index_options/ob", "cdf-horizon-panel", "input_panel")


@pytest.mark.parametrize("name", ["run-step4-feature-selection.json", "run-step5-model-zoo.json",
                                  "run-step6-hpo.json", "run-step4-feature-selection-iwm.json",
                                  "run-step5-model-zoo-iwm.json", "run-step6-hpo-iwm.json"])
def test_the_study_documents_resolve_every_reference_against_data_root(name):
    data = json.loads((CONFIGS / name).read_text())["data"]
    assert data["root"] in STORE_ROOTS
    assert {k: (data[k]["source"], data[k]["relpath"]) for k in FILE_ENTRIES} == {
        "surface": ("exact-expiry-tables", "exact_expiry_surface.parquet"),
        "lifecycle": ("exact-expiry-tables", "date_expiry_lifecycle.parquet"),
        "chain_features": ("raw-chain-features", "raw_chain_features.parquet")}
    assert data["archive_root"] == data["decision_regions"]["archive_root"] == {
        "source": "philippdubach-options", "stream": "files"}


def test_step_1b_reads_the_same_tables_as_the_studies():
    step = json.loads((CONFIGS / "run-step1b-feature-engineering.json").read_text())
    study = json.loads((CONFIGS / "run-step4-feature-selection.json").read_text())["data"]
    panel = step["pipeline"]["panel"]["params"]
    assert {k: panel[k] for k in ("root", *FILE_ENTRIES)} == {
        k: study[k] for k in ("root", *FILE_ENTRIES)}


# -- the guard itself bites -----------------------------------------------------------------------

@pytest.mark.parametrize("document, expected", [
    ({"data": {"root": "/home/russell/data/index_options/ob"}}, 0),
    ({"data": {"root": "/home/russell/other/ob"}}, 1),
    ({"pipeline": {"source": {"params": {"root": "~/ob"}}}}, 1),
    ({"data": {"root": "./pipeline_runs/step1"}}, 0),
    ({"outputs": {"run_root": "./pipeline_runs/x/runs"}, "x": "pipeline_runs/y"}, 0),
    ({"data": {"surface": "/home/russell/a.parquet"}}, 1),
    ({"data": {"surface": "/mnt/data/a.parquet"}}, 1),
    ({"notes": "read it from /home/russell/data"}, 1),
    ({"list": ["ok", "~/x", {"deep": "/etc/passwd"}]}, 2),
    ({"data": {"symbols": {"QQQ": "VXN"}, "formula": "|log K/S| <= 0.1"}}, 0),
])
def test_the_path_detector_flags_exactly_the_stray_paths(document, expected):
    assert len(stray_paths("doc.json", document)) == expected


def test_the_allowlist_exempts_one_pointer_in_one_file(monkeypatch):
    document = {"notes": "see /home/russell/x"}
    assert stray_paths("doc.json", document)
    monkeypatch.setitem(ALLOWED, ("doc.json", "/notes"), "illustration")
    assert stray_paths("doc.json", document) == []
    assert stray_paths("other.json", document)


def test_a_plain_string_or_malformed_data_entry_is_flagged():
    ref = {"source": "s", "stream": "files", "relpath": "a.parquet"}
    tree = {"source": "s", "stream": "files"}
    good = {"data": {"surface": ref, "lifecycle": ref, "chain_features": ref,
                     "archive_root": tree, "decision_regions": {"archive_root": tree}}}
    assert plain_data_entries(good) == []
    assert len(plain_data_entries({"data": {**good["data"], "surface": "a.parquet"}})) == 1
    assert len(plain_data_entries({"data": {**good["data"], "archive_root": "/x"}})) == 1
    assert len(plain_data_entries(
        {"data": {**good["data"], "decision_regions": {"archive_root": "/x"}}})) == 1
    assert plain_data_entries({"data": {**good["data"], "archive_root": ref}})   # a file, not a tree
    assert plain_data_entries({"data": {**good["data"], "surface": tree}})      # a tree, not a file
    assert plain_data_entries({"pipeline": {}}) == []
