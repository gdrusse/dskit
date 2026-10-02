"""index_options.datafiles: a data entry is a store reference or a legacy path (ADR-0225).

A reference resolves through ``payload_files`` against a real ``localblobs`` store (the
``blob_store`` fixture); a plain string keeps reading the bytes it names.
"""

import hashlib
from pathlib import Path

import pytest

from dskit.onboarding import payload_files
from index_options import datafiles
from index_options.datafiles import (DataFiles, DataSourceError, DataTree, archive_relpath,
                                     clear_snapshot_cache, entry_problems)

ARCHIVE = {
    "qqq/options_2012.parquet": b"PAR1 qqq 2012",
    "qqq/options_2013.parquet": b"PAR1 qqq 2013 ...",
    "qqq/underlying_prices.parquet": b"PAR1 prices",
}
TABLES = {"surface.parquet": b"PAR1 surface", "life.parquet": b"PAR1 life",
          "surface.parquet.sources.json": b"{}"}


def _write(directory, files):
    for rel, data in files.items():
        (directory / rel).parent.mkdir(parents=True, exist_ok=True)
        (directory / rel).write_bytes(data)
    return directory


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def ref(source, relpath=None, **extra):
    entry = {"source": source, "stream": "files", **extra}
    if relpath is not None:
        entry["relpath"] = relpath
    return entry


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


@pytest.fixture
def store(tmp_path, blob_store):
    blob_store.archive_dir = _write(tmp_path / "archive-src", ARCHIVE)
    blob_store.tables_dir = _write(tmp_path / "tables-src", TABLES)
    blob_store.add("archive", blob_store.archive_dir)
    blob_store.add("tables", blob_store.tables_dir)
    return blob_store


@pytest.fixture
def spy(monkeypatch):
    """Count the ``payload_files`` calls the module makes."""
    calls = []
    real = datafiles.payload_files

    def counting(root, source, stream, **kwargs):
        calls.append((root, source, stream))
        return real(root, source, stream, **kwargs)

    monkeypatch.setattr(datafiles, "payload_files", counting)
    return calls


# -- the shape of an entry ------------------------------------------------------------------

@pytest.mark.parametrize("entry, tree", [
    ("some/file.parquet", False), ("some/dir", True),
    (ref("s", "a/b.parquet"), False), (ref("s"), True),
    (ref("s", "a.parquet", manifest_sha256="ab" * 32), False),
])
def test_a_path_or_a_well_formed_reference_has_no_problems(entry, tree):
    assert entry_problems("surface", entry, tree=tree) == []


@pytest.mark.parametrize("entry, tree, match", [
    ("", False, "empty path"),
    (3, False, "path string or a store reference"),
    ({"source": "s", "relpath": "a"}, False, "stream"),
    ({"stream": "files", "relpath": "a"}, False, "source"),
    ({"source": "", "stream": "files", "relpath": "a"}, False, "source"),
    (ref("s"), False, "needs a relpath"),
    (ref("s", "a.parquet"), True, "takes no relpath"),
    (ref("s", "/abs.parquet"), False, "relative POSIX path"),
    (ref("s", "../up.parquet"), False, "relative POSIX path"),
    (ref("s", "a.parquet", pin="x"), False, "unknown key"),
    (ref("s", "a.parquet", manifest_sha256="ABC"), False, "64 lowercase hex"),
    (ref("s", "a.parquet", manifest_sha256=7), False, "64 lowercase hex"),
])
def test_entry_problems_names_what_is_wrong(entry, tree, match):
    problems = entry_problems("surface", entry, tree=tree)
    assert problems and any(match in p and p.startswith("surface") for p in problems), problems


def test_every_problem_is_reported_at_once():
    problems = entry_problems("x", {"source": "", "extra": 1, "relpath": "../a"}, tree=False)
    assert len(problems) >= 4


def test_archive_relpath_is_the_philippdubach_layout():
    assert archive_relpath("QQQ", 2012) == "qqq/options_2012.parquet"
    assert archive_relpath("spy", "2020") == "spy/options_2020.parquet"


# -- a store reference -----------------------------------------------------------------------

def test_a_file_reference_resolves_to_the_snapshots_file_and_manifest_digest(store):
    files = DataFiles(store.path)
    path = files.path(ref("tables", "surface.parquet"))
    assert isinstance(path, Path) and path.read_bytes() == TABLES["surface.parquet"]
    got = payload_files(store.path, "tables", "files")
    assert path == got["files"]["surface.parquet"]
    assert files.sha256(ref("tables", "surface.parquet")) == _sha(TABLES["surface.parquet"])
    assert files.sha256(ref("tables", "surface.parquet")) == got["sha256"]["surface.parquet"]


def test_the_sibling_suffix_stays_in_the_same_stream(store):
    files = DataFiles(store.path)
    entry = ref("tables", "surface.parquet")
    assert files.has(entry, ".sources.json") and not files.has(entry, ".nope")
    assert files.path(entry, ".sources.json").read_bytes() == b"{}"
    assert files.sha256(entry, ".sources.json") == _sha(b"{}")


def test_a_tree_reference_reaches_files_by_relpath(store):
    tree = DataFiles(store.path).tree(ref("archive"))
    assert isinstance(tree, DataTree)
    rel = archive_relpath("QQQ", 2012)
    assert tree.has(rel) and not tree.has(archive_relpath("QQQ", 1999))
    assert tree.path(rel).read_bytes() == ARCHIVE[rel]
    assert tree.sha256(rel) == _sha(ARCHIVE[rel])
    assert tree.path("qqq/underlying_prices.parquet").read_bytes() == b"PAR1 prices"
    assert tree.label(rel) == "store:archive/files/qqq/options_2012.parquet"
    snapshot = payload_files(store.path, "archive", "files")
    assert tree.store == {"source": "archive", "stream": "files", "snapshot": snapshot["snapshot"],
                          "manifest_sha256": snapshot["manifest_sha256"]}


def test_a_missing_relpath_refuses_by_name(store):
    files = DataFiles(store.path)
    with pytest.raises(DataSourceError, match=r"tables/files.*'nope\.parquet'"):
        files.path(ref("tables", "nope.parquet"))
    with pytest.raises(DataSourceError, match=r"archive/files.*'qqq/options_1999\.parquet'"):
        files.tree(ref("archive")).path(archive_relpath("QQQ", 1999))
    assert not files.has(ref("tables", "nope.parquet"))


def test_an_unknown_source_or_stream_refuses_by_name(store):
    files = DataFiles(store.path)
    with pytest.raises(DataSourceError, match="no-such-source/files"):
        files.path(ref("no-such-source", "a.parquet"))
    with pytest.raises(DataSourceError, match="tables/other"):
        files.path({"source": "tables", "stream": "other", "relpath": "surface.parquet"})


def test_a_reference_needs_a_store_root(store):
    with pytest.raises(DataSourceError, match="needs a store root"):
        DataFiles().path(ref("tables", "surface.parquet"))
    with pytest.raises(DataSourceError, match="needs a store root"):
        DataFiles(None).tree(ref("archive"))


def test_a_malformed_reference_is_refused_before_the_store_is_touched(store, spy):
    files = DataFiles(store.path)
    with pytest.raises(DataSourceError, match="needs a relpath"):
        files.path(ref("tables"))
    with pytest.raises(DataSourceError, match="takes no relpath"):
        files.tree(ref("archive", "qqq/options_2012.parquet"))
    assert spy == []


def test_the_pin_must_match_the_current_snapshot(store):
    files = DataFiles(store.path)
    manifest = payload_files(store.path, "tables", "files")["manifest_sha256"]
    ok = ref("tables", "surface.parquet", manifest_sha256=manifest)
    assert files.path(ok).read_bytes() == TABLES["surface.parquet"]
    bad = ref("tables", "surface.parquet", manifest_sha256="0" * 64)
    with pytest.raises(DataSourceError, match=r"pinned manifest_sha256 0{64}.*" + manifest):
        files.path(bad)
    with pytest.raises(DataSourceError, match="pinned manifest_sha256"):
        files.tree(ref("tables", manifest_sha256="1" * 64))


def test_a_pin_taken_before_a_newer_snapshot_refuses_once_the_cache_is_cleared(store):
    entry = ref("tables", "surface.parquet",
                manifest_sha256=payload_files(store.path, "tables", "files")["manifest_sha256"])
    assert DataFiles(store.path).path(entry)
    (store.tables_dir / "surface.parquet").write_bytes(b"PAR1 surface v2")
    store.add("tables", store.tables_dir)
    clear_snapshot_cache()
    with pytest.raises(DataSourceError, match="pinned manifest_sha256"):
        DataFiles(store.path).path(entry)


# -- a legacy path ----------------------------------------------------------------------------

def test_a_legacy_file_path_is_read_in_place(tmp_path, spy):
    _write(tmp_path / "legacy", TABLES)
    file = tmp_path / "legacy" / "surface.parquet"
    files = DataFiles()
    assert files.path(str(file)) == file and files.path(file) == file
    assert files.has(str(file)) and not files.has(str(tmp_path / "legacy" / "nope"))
    assert files.sha256(str(file)) == _sha(TABLES["surface.parquet"])
    assert files.path(str(file), ".sources.json") == Path(str(file) + ".sources.json")
    assert files.sha256(str(file), ".sources.json") == _sha(b"{}")
    assert files.provenance() == [] and spy == []


def test_a_legacy_relative_path_keeps_its_spelling():
    assert DataFiles().path("surface.parquet") == Path("surface.parquet")
    assert str(DataFiles().path("a/b/surface.parquet")) == "a/b/surface.parquet"


def test_a_legacy_directory_is_a_tree_of_files(tmp_path, spy):
    archive = _write(tmp_path / "legacy", ARCHIVE)
    tree = DataFiles("/ignored/store/root").tree(str(archive))
    rel = archive_relpath("QQQ", 2013)
    assert tree.has(rel) and not tree.has(archive_relpath("QQQ", 1999))
    assert tree.path(rel) == archive / "qqq" / "options_2013.parquet"
    assert tree.path(archive_relpath("QQQ", 1999)) == archive / "qqq" / "options_1999.parquet"
    assert tree.sha256(rel) == _sha(ARCHIVE[rel])
    assert tree.label(rel) == str(archive / "qqq" / "options_2013.parquet")
    assert tree.store is None and spy == []


def test_an_empty_legacy_path_refuses():
    with pytest.raises(DataSourceError, match="empty path"):
        DataFiles().path("")
    with pytest.raises(DataSourceError, match="empty path"):
        DataFiles().tree("")


def test_a_store_and_a_legacy_read_agree_on_bytes_digest_and_path_shape(store, tmp_path):
    files = DataFiles(store.path)
    legacy = DataFiles()
    for rel, data in ARCHIVE.items():
        in_store = files.tree(ref("archive")).path(rel)
        in_place = legacy.tree(str(store.archive_dir)).path(rel)
        assert in_store.read_bytes() == in_place.read_bytes() == data
        assert files.tree(ref("archive")).sha256(rel) == legacy.tree(
            str(store.archive_dir)).sha256(rel)


# -- provenance ------------------------------------------------------------------------------

def test_provenance_records_each_resolved_snapshot_once(store):
    files = DataFiles(store.path)
    files.path(ref("tables", "surface.parquet"))
    files.path(ref("tables", "life.parquet"))
    files.sha256(ref("tables", "surface.parquet"))
    files.tree(ref("archive"))
    tables = payload_files(store.path, "tables", "files")
    archive = payload_files(store.path, "archive", "files")
    assert files.provenance() == [
        {"source": "archive", "stream": "files", "snapshot": archive["snapshot"],
         "manifest_sha256": archive["manifest_sha256"], "files": len(ARCHIVE)},
        {"source": "tables", "stream": "files", "snapshot": tables["snapshot"],
         "manifest_sha256": tables["manifest_sha256"], "files": len(TABLES)}]


def test_provenance_is_a_copy_and_empty_for_legacy_only_reads(tmp_path):
    assert DataFiles().provenance() == []
    _write(tmp_path / "legacy", TABLES)
    files = DataFiles()
    files.path(str(tmp_path / "legacy" / "surface.parquet"))
    assert files.provenance() == []


def test_a_failed_resolution_records_nothing(store):
    files = DataFiles(store.path)
    with pytest.raises(DataSourceError):
        files.path(ref("tables", "surface.parquet", manifest_sha256="0" * 64))
    with pytest.raises(DataSourceError):
        files.path(ref("no-such-source", "a"))
    assert files.provenance() == []
    files.provenance().append("junk")
    assert files.provenance() == []


# -- the in-process cache --------------------------------------------------------------------

def test_payload_files_is_called_once_per_root_source_and_stream(store, spy):
    one, two = DataFiles(store.path), DataFiles(store.path)
    for files in (one, two):
        files.path(ref("tables", "surface.parquet"))
        files.path(ref("tables", "life.parquet"))
        files.sha256(ref("tables", "life.parquet"))
        files.tree(ref("archive"))
        files.tree(ref("archive"))
    assert sorted(call[1] for call in spy) == ["archive", "tables"]


def test_the_cache_holds_one_snapshot_until_it_is_cleared(store, spy):
    files = DataFiles(store.path)
    first = files.path(ref("tables", "surface.parquet"))
    (store.tables_dir / "surface.parquet").write_bytes(b"PAR1 surface v2")
    store.add("tables", store.tables_dir)
    assert DataFiles(store.path).path(ref("tables", "surface.parquet")) == first
    clear_snapshot_cache()
    newest = DataFiles(store.path).path(ref("tables", "surface.parquet"))
    assert newest != first and newest.read_bytes() == b"PAR1 surface v2"
    assert len(spy) == 2


def test_a_refused_resolution_is_not_cached(store, spy):
    files = DataFiles(store.path)
    with pytest.raises(DataSourceError):
        files.path(ref("no-such-source", "a"))
    with pytest.raises(DataSourceError):
        files.path(ref("no-such-source", "a"))
    assert len(spy) == 2


def test_two_stores_do_not_share_cache_entries(store, tmp_path):
    other = tmp_path / "other-root"
    other.mkdir()
    with pytest.raises(DataSourceError, match="tables/files"):
        DataFiles(other).path(ref("tables", "surface.parquet"))
    assert DataFiles(store.path).path(ref("tables", "surface.parquet")).is_file()
