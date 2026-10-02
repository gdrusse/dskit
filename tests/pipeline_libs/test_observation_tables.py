"""ObservationTables: keyed onboarded tables attached onto a stream, exact and as-of."""

import json

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.pipeline.libs.observation_tables import (
    AGE_SUFFIX, MISSING_SUFFIX, NODE_KINDS, ObservationTables, register)
from dskit.pipeline.node import NodeContext, NodeKindRegistry

SOURCE, STREAM = "src", "daily"
ROWS = (
    {"sym": "A", "date": "2026-01-02", "px": 1.0, "vol": 10.0},
    {"sym": "A", "date": "2026-01-05", "px": 2.0, "vol": None},
    {"sym": "B", "date": "2026-01-02", "px": 7.0, "vol": 70.0},
)


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with open(data / f"{STREAM}.jsonl", "w", encoding="utf-8") as fh:
        for row in ROWS:
            fh.write(json.dumps(row) + "\n")
    store = OnboardingRoot.create(str(tmp_path / "ob"))
    reg = store.registry()
    vid = reg.register("source_config", {
        "name": SOURCE, "catalog_source": "x", "connector": "localfiles",
        "config": {"path": str(data), "effective_field": "date"}}, origin="test")
    reg.transition(vid, "active", origin="test")
    run_acquisition(store, reg, SOURCE, STREAM, "backfill")
    return store.root


def _node(root, tables, **over):
    return ObservationTables("t", {"root": root, "key": ["sym", "date"], "tables": tables, **over})


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-10", run_dir=str(tmp_path / "run"))


STREAM_ROWS = [{"sym": "A", "date": "2026-01-02"}, {"sym": "A", "date": "2026-01-04"},
               {"sym": "A", "date": "2026-01-05"}, {"sym": "B", "date": "2026-01-03"}]


def test_exact_table_matches_the_key_and_leaves_the_rest_null(root, tmp_path):
    node = _node(root, {"f": {"source": SOURCE, "stream": STREAM, "columns": {"px": "f_px"}}})
    out = node.run(_ctx(tmp_path), {"records": STREAM_ROWS})
    assert [r["f_px"] for r in out["records"]] == [1.0, None, 2.0, None]
    assert out["provenance"]["tables"]["f"]["matched"] == 2
    assert out["provenance"]["tables"]["f"]["unmatched"] == 2
    assert STREAM_ROWS[0] == {"sym": "A", "date": "2026-01-02"}   # input untouched


def test_asof_table_is_strictly_prior_within_the_age_with_companions(root, tmp_path):
    node = _node(root, {"f": {"source": SOURCE, "stream": STREAM, "columns": {"px": "f_px"},
                              "max_age_days": 2}})
    rows = node.run(_ctx(tmp_path), {"records": STREAM_ROWS})["records"]
    got = [(r["f_px"], r["f_px" + AGE_SUFFIX], r["f_px" + MISSING_SUFFIX]) for r in rows]
    # d2: nothing strictly prior; d4: d2 is 2 days old; d5: d2 is 3 days old, too stale;
    # B d3: B's d2 is 1 day old.
    assert got == [(None, None, 1), (1.0, 2, 0), (None, None, 1), (7.0, 1, 0)]


def test_asof_same_day_allowed_when_not_strict_and_null_value_counts_missing(root, tmp_path):
    node = _node(root, {"f": {"source": SOURCE, "stream": STREAM, "columns": {"vol": "f_v"},
                              "max_age_days": 3, "strict_prior": False}})
    rows = node.run(_ctx(tmp_path), {"records": STREAM_ROWS})["records"]
    assert [(r["f_v"], r["f_v" + MISSING_SUFFIX]) for r in rows] == [
        (10.0, 0), (10.0, 0), (None, 1), (70.0, 0)]


def test_no_tables_is_a_pass_through(root, tmp_path):
    out = _node(root, {}).run(_ctx(tmp_path), {"records": STREAM_ROWS})
    assert out["records"] == STREAM_ROWS and out["provenance"] == {"tables": {}}


def test_collisions_and_missing_key_fields_refuse(root, tmp_path):
    spec = {"source": SOURCE, "stream": STREAM, "columns": {"px": "date"}}
    with pytest.raises(ValueError, match="already exists"):
        _node(root, {"f": spec}).run(_ctx(tmp_path), {"records": STREAM_ROWS})
    twice = {"f": {"source": SOURCE, "stream": STREAM, "columns": {"px": "x"}},
             "g": {"source": SOURCE, "stream": STREAM, "columns": {"vol": "x"}}}
    with pytest.raises(ValueError, match="already exists"):
        _node(root, twice).run(_ctx(tmp_path), {"records": STREAM_ROWS})
    with pytest.raises(ValueError, match="lack key"):
        _node(root, {}).run(_ctx(tmp_path), {"records": [{"sym": "A"}]})


@pytest.mark.parametrize("bad", [
    {"typo": 1}, {"source": "s"}, {"source": "s", "stream": "t", "columns": {}},
    {"source": "s", "stream": "t", "columns": {"a": "x"}, "key_fields": ["only_one"]},
    {"source": "s", "stream": "t", "columns": {"a": "x"}, "max_age_days": -1},
    {"source": "s", "stream": "t", "columns": {"a": "x"}, "strict_prior": "yes"},
    {"source": "s", "stream": "t", "columns": {"a": "x", "b": "x"}},
])
def test_a_malformed_table_declaration_is_refused(bad):
    params = {"root": "r", "key": ["sym", "date"], "tables": {"f": bad}}
    assert ObservationTables.validate_params(params)


def test_unknown_param_and_missing_required_are_refused():
    assert ObservationTables.validate_params({"typo": 1})
    assert ObservationTables.validate_params({"root": "r", "key": ["a"], "tables": {}}) == []


def test_registers_idempotently():
    reg = NodeKindRegistry()
    register(reg)
    register(reg)
    assert NODE_KINDS[0][0] in reg


def test_a_table_may_name_its_own_root_and_needs_one_somewhere(root, tmp_path):
    spec = {"root": root, "source": SOURCE, "stream": STREAM, "columns": {"px": "f_px"}}
    node = ObservationTables("t", {"key": ["sym", "date"], "tables": {"f": spec}})
    assert node.run(_ctx(tmp_path), {"records": STREAM_ROWS})["records"][0]["f_px"] == 1.0
    bare = {k: v for k, v in spec.items() if k != "root"}
    assert ObservationTables.validate_params({"key": ["sym", "date"], "tables": {"f": bare}})
