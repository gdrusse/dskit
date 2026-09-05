"""Validate the helper documents/run dirs the decider tests build."""
import dataclasses, json, os
import pytest
from dskit.pipeline.planner import plan
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.production.document import ServeDocument
from tests.production.conftest import BASE_PASS_NODE, ENTRY_NODE, HEAD, TRAINABLE_NODE, UNIVERSE

NO_REPLAY = {}


def serving_document(*a, **k):
    raise AssertionError("not used here")

def variant(training_document, changes=None, drop=(), **overrides):
    """The conftest training document with nodes added, replaced or dropped."""
    pipeline = {k: v for k, v in training_document.pipeline.items() if k not in drop}
    pipeline.update(changes or {})
    fields = {
        "name": training_document.name,
        "pipeline": pipeline,
        "splits": training_document.splits,
    }
    fields.update(overrides)
    return PipelineDocument(**fields)


def fake_run_dir(tmp_path, document, records=None, carry=None, artifacts=()):
    """A run dir with the driver's own layout: config/nodes/carry/artifacts."""
    run = tmp_path / "run"
    (run / "nodes").mkdir(parents=True, exist_ok=True)
    (run / "config.json").write_text(json.dumps(document.to_obj()), encoding="utf-8")
    for i, (key, spec) in enumerate(document.expanded.items(), start=1):
        record = {
            "node": key,
            "uses": spec.uses,
            "role": None,
            "status": "ok",
            "seconds": 0.0,
            "outputs": {},
        }
        record.update((records or {}).get(key, {}))
        (run / "nodes" / f"{i:02d}-{key}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    (run / "carry.json").write_text(json.dumps(carry or {}), encoding="utf-8")
    for key in artifacts:
        (run / "artifacts" / key).mkdir(parents=True, exist_ok=True)
    return str(run)


def derived(training_document, run_dir, heads=(HEAD,), replay=NO_REPLAY):
    """`serving_document` over the conftest training run."""
    return serving_document(training_document, run_dir, list(heads), replay)


def gate_node(inputs=None):
    """A `gate`-role node (`eligibility`) — the classic replayed verdict."""
    return NodeSpec(
        uses="eligibility",
        inputs=inputs or {"banked": f"${BASE_PASS_NODE}.table"},
        params={"min_events": 1},
    )


def gated_document(training_document):
    """The training document with a gate between `weights` and the head."""
    head = training_document.pipeline[HEAD]
    return variant(
        training_document,
        changes={
            "family": gate_node(),
            HEAD: dataclasses.replace(
                head, inputs={"records": "$scored.records", "weight": "$family.instruments"}
            ),
        },
    )


GATE_OUTPUTS = {"instruments": list(UNIVERSE), "verdict": "GO"}


def searched_document(training_document):
    """The training document plus a search node that tuned `grid.offset_ms`."""
    return variant(
        training_document,
        changes={
            "tune": NodeSpec(
                uses="hpo-grid",
                params={
                    "objective": f"${TRAINABLE_NODE}.metrics.n_rows",
                    "direction": "max",
                    "space": {"grid.offset_ms": [0, 1]},
                },
            )
        },
    )



def test_gated_document_plans(training_document, tmp_path):
    doc = gated_document(training_document)
    p = plan(doc)
    assert "family" in p.order
    assert p.role_of("family") == "gate"
    assert "family" in p.ancestors(HEAD)
    run = fake_run_dir(tmp_path, doc, carry={"family": dict(GATE_OUTPUTS)},
                       artifacts=[TRAINABLE_NODE])
    assert os.path.isfile(os.path.join(run, "config.json"))
    assert sorted(os.listdir(os.path.join(run, "nodes")))


def test_searched_document_constructs(training_document):
    doc = searched_document(training_document)
    assert "tune" in doc.expanded
    with pytest.raises(Exception):
        plan(doc)   # documented: a searched doc is derived from, never re-planned


def test_prev_variant_constructs(training_document):
    head = training_document.pipeline[HEAD]
    doc = variant(training_document, changes={
        HEAD: dataclasses.replace(head, params={**head.params,
              "how": {"$prev": "picks.how", "default": "strict"}})})
    plan(doc)


def test_tail_variant_constructs(training_document):
    from dskit.pipeline.document import NodeSpec
    doc = variant(training_document, changes={"tail": NodeSpec(
        uses="filter", inputs={"records": f"${HEAD}.records"},
        params={"where": [{"field": "value", "op": ">",
                           "value": {"$prev": "tail.bar", "default": 0}}]})})
    plan(doc)


def test_two_entry_variant_constructs(training_document):
    doc = variant(training_document, changes={
        "shadow_bars": dataclasses.replace(training_document.pipeline[ENTRY_NODE]),
        HEAD: dataclasses.replace(training_document.pipeline[HEAD],
              inputs={"records": "$scored.records", "weight": "$shadow_bars.records"})})
    p = plan(doc)
    assert {"shadow_bars", ENTRY_NODE} <= p.ancestors(HEAD)


def test_weights_only_variant_constructs(training_document):
    doc = variant(training_document, drop=[ENTRY_NODE, "usable", "grid",
                                           TRAINABLE_NODE, "scored", HEAD])
    plan(doc)
    assert set(doc.expanded) == {BASE_PASS_NODE}


def test_serve_document_round_trips_with_serving_overrides(serve_document, run_dir):
    obj = serve_document.to_obj()
    obj["serving"].update({"heads": [BASE_PASS_NODE]})
    doc = ServeDocument.from_obj(obj)
    assert list(doc.serving.heads) == [BASE_PASS_NODE]
    assert doc.serving.entry.node == ENTRY_NODE
    assert doc.serving.entry.param == "since_ms"
    assert doc.serving.entry.window_ms
    assert list(doc.serving.required_universe) == ["INS1", "INS2"]
