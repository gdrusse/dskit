"""lineage.py: one global DAG — layered phases, end-to-end queries."""

import pytest

from dskit.assets import AssetError, Lineage


@pytest.fixture
def chain(registry):
    """source -> dataset -> dataset_version -> run -> output, both phases."""
    lin = Lineage(registry)
    src = registry.register("source", {"name": "vendor-x"})
    ds = registry.register("dataset", {"name": "prices"}, refs={"source": src})
    dv = registry.register("dataset_version", {"name": "prices-v1"},
                           refs={"dataset": ds})
    run = registry.register("run_observation", {"name": "run-1"})
    out = registry.register("output", {"name": "signal"}, refs={"run": run})
    for s, d, rel, ph in [(src, ds, "registered_from", "onboarding"),
                          (ds, dv, "version_of", "onboarding"),
                          (dv, run, "input", "execution"),
                          (run, out, "produced", "execution")]:
        assert lin.add(s, d, relation=rel, phase=ph, origin="test")
    return lin, [src, ds, dv, run, out]


# -- normal ----------------------------------------------------------------


def test_end_to_end_queries(chain):
    lin, (src, ds, dv, run, out) = chain
    assert lin.ancestors(out) == sorted([src, ds, dv, run])
    assert lin.descendants(src) == sorted([ds, dv, run, out])
    assert lin.parents(dv) == [ds] and lin.children(dv) == [run]


def test_edges_carry_provenance_and_filter(chain):
    lin, (_src, _ds, dv, _run, _out) = chain
    assert len(lin.edges()) == 4 and len(lin.edges(dv)) == 2
    edge = lin.edges(dv)[0]
    assert edge["phase"] == "onboarding" and edge["origin"] == "test" and edge["at"]


def test_both_phases_compose_in_one_graph(chain):
    lin, _vids = chain
    assert {e["phase"] for e in lin.edges()} == {"onboarding", "execution"}


# -- edge ------------------------------------------------------------------


def test_duplicate_edge_is_idempotent(chain):
    lin, (src, ds, *_rest) = chain
    assert lin.add(src, ds, relation="registered_from", phase="onboarding") is False
    assert len(lin.edges()) == 4


# -- failure ---------------------------------------------------------------


def test_cycles_refused_shallow_and_deep(chain):
    lin, (src, _ds, _dv, _run, out) = chain
    with pytest.raises(AssetError, match="itself"):
        lin.add(src, src, relation="x", phase="execution")
    with pytest.raises(AssetError, match="cycle"):
        lin.add(out, src, relation="loops", phase="execution")


def test_dangling_endpoint_refused(chain):
    lin, (_src, _ds, _dv, _run, out) = chain
    with pytest.raises(AssetError, match="no record"):
        lin.add("0" * 64, out, relation="x", phase="execution")
