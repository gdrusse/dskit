"""The trailing-split materialization seam (I-223): ``Node.data_edge`` +
``driver._materialize_splits``.

``TrailingSplitSpec.materialize`` was pure, tested, and called from
nowhere — the driver refused trailing documents outright. The seam closed
here is the one question a venue-neutral toolkit may ask a source: *where
does your data end*. Every ``data``-role node is asked during RESOLVE
(alongside ``fingerprint``, and for the same reason — a source's params
are fully literal, so it is complete before anything runs), and the
trailing windows are counted backward from the one answer.

The properties this file pins:

* the base ``data_edge`` declines, an override is honored;
* non-trailing splits pass through ``_materialize_splits`` untouched;
* one edge materializes to the exact backward arithmetic;
* zero edges and two edges REFUSE — a guessed anchor silently moves
  every cut, which is the whole reason the seam refuses rather than picks;
* end to end, a trailing run is reconstructible after the fact:
  ``config.json`` keeps the trailing spec, ``resolved.json`` carries the
  pinned ``"time"`` cuts, and ``$splits.*`` references resolve against
  those materialized cuts.

PURE-TOOLKIT suite (``test_purity.py`` spirit): stdlib plus
``dskit.pipeline`` only — no adapter package, no numpy, no pandas.
"""

import json
import os
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import (
    NodeSpec,
    PipelineDocument,
    RandomSplitSpec,
    TrailingSplitSpec,
)
from dskit.pipeline.driver import _materialize_splits, run_document
from dskit.pipeline.node import Node

ASOF = "2026-01-01"
DAY = 24 * 60 * 60 * 1000
#: The ledger's edge in the fixtures — an arbitrary but deep instant, so
#: the backward windows land comfortably above zero.
NEWEST_MS = 1_000 * DAY

#: Lifecycle calls the fixture source recorded, in order:
#: ``(stage, node key, run_root listing at that moment)``.
CALLS = []


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


class PlainNode(Node):
    """A node with no edge to report — the base defaults, untouched."""

    role = "transform"

    def run(self, ctx, inputs):
        return {"ok": True}


class EdgeSource(Node):
    """A source that knows where its ledger ends (role ``data``).

    ``newest_ms`` is that edge; ``n_events`` daily events are emitted at
    ``newest_ms - i*DAY`` so a downstream cut is observable. Every
    lifecycle call appends to :data:`CALLS` together with the ``run_root``
    listing at that instant — which is how the ordering property (edges
    are read BEFORE the run dir exists) is asserted.
    """

    role = "data"
    outputs = ("events", "newest_ms")

    @classmethod
    def validate_params(cls, params):
        newest = params.get("newest_ms")
        # floats are accepted here on purpose: an edge of the wrong TYPE
        # must be refused by the seam, not hidden by the fixture.
        if isinstance(newest, bool) or not isinstance(newest, (int, float)):
            return [f"newest_ms must be a number, got {newest!r}"]
        return []

    def _snapshot(self):
        root = self.params.get("run_root", "")
        return sorted(os.listdir(root)) if root and os.path.isdir(root) else None

    def fingerprint(self):
        CALLS.append(("fingerprint", self.key, self._snapshot()))
        return {"newest_ms": self.params["newest_ms"]}

    def data_edge(self):
        CALLS.append(("data_edge", self.key, self._snapshot()))
        return self.params["newest_ms"]

    def run(self, ctx, inputs):
        CALLS.append(("run", self.key, self._snapshot()))
        newest = self.params["newest_ms"]
        instrument = self.params.get("instrument", "SYNA")
        events = [
            {
                "instrument": instrument,
                "contract": f"{instrument}-{i:03d}",
                "asof_ms": newest - i * DAY,
            }
            for i in range(self.params.get("n_events", 10))
        ]
        return {"events": events, "newest_ms": newest}


class EchoSplits(Node):
    """Classifies its events under ``ctx.splits`` — proof the pinned cuts
    reach the NODES as a working split object, not only ``resolved.json``."""

    role = "transform"
    outputs = ("splits_info", "by_split")

    def run(self, ctx, inputs):
        by_split = {}
        for event in inputs["events"]:
            name = ctx.splits.split_of(
                SimpleNamespace(asof_ms=event["asof_ms"], cluster=event["contract"])
            )
            by_split[name] = by_split.get(name, 0) + 1
        return {"splits_info": ctx.splits_info, "by_split": by_split}


REF = "tests.pipeline.test_trailing_seam:EdgeSource"


def source_spec(tmp_path, **params):
    """An ``EdgeSource`` node spec pinned to ``tmp_path`` as its run root."""
    base = {"newest_ms": NEWEST_MS, "n_events": 10, "run_root": str(tmp_path)}
    base.update(params)
    return NodeSpec(uses=REF, params=base)


def trailing_doc(tmp_path, pipeline=None, **overrides):
    """A document over ``tmp_path`` — trailing splits unless overridden."""
    base = {
        "name": "trailing-doc",
        "pipeline": pipeline or {"ladder": source_spec(tmp_path)},
        "splits": TrailingSplitSpec(test_days=2, val_days=3),
        "outputs": OutputsConfig(run_root=str(tmp_path)),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def read_json(run_dir, name):
    with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


class TestDataEdgeSeam:
    """The Node-side half: the question, and who answers it."""

    def test_base_default_declines(self):
        node = PlainNode("plain")
        assert node.data_edge() is None
        # the sibling identity seam is unchanged by the addition
        assert node.fingerprint() is None

    def test_subclass_override_is_honored(self):
        node = EdgeSource("ladder", {"newest_ms": NEWEST_MS})
        assert node.data_edge() == NEWEST_MS
        assert [stage for stage, _key, _snap in CALLS] == ["data_edge"]

    def test_every_data_node_is_asked_at_resolve(self, tmp_path):
        # Pinned cuts: the edges are still collected (the driver asks
        # unconditionally), they simply have nothing to materialize.
        doc = trailing_doc(
            tmp_path,
            splits=TimeSplitConfig(
                train_end_ms=NEWEST_MS - 5 * DAY,
                val_end_ms=NEWEST_MS - 2 * DAY,
                test_end_ms=NEWEST_MS,
            ),
        )
        result = run_document(doc, asof=ASOF)
        assert result.state == "ran"
        assert [stage for stage, _key, _snap in CALLS] == [
            "fingerprint",
            "data_edge",
            "run",
        ]


class TestMaterializeSplits:
    """The driver-side half, exercised directly."""

    def test_pinned_time_cuts_pass_through_identically(self):
        cuts = TimeSplitConfig(
            train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY
        )
        # Same object back, whether or not sources offered edges.
        assert _materialize_splits(cuts, {}, []) is cuts
        assert _materialize_splits(cuts, {"ladder": NEWEST_MS}, ["ladder"]) is cuts

    def test_random_splits_pass_through_identically(self):
        spec = RandomSplitSpec(train_frac=0.8, val_frac=0.2, seed=7)
        assert _materialize_splits(spec, {}, []) is spec
        assert _materialize_splits(spec, {"ladder": NEWEST_MS}, ["ladder"]) is spec

    def test_absent_splits_pass_through(self):
        assert _materialize_splits(None, {}, []) is None
        assert _materialize_splits(None, {"ladder": NEWEST_MS}, ["ladder"]) is None

    def test_one_edge_materializes_the_backward_arithmetic(self):
        spec = TrailingSplitSpec(test_days=14, val_days=28)
        cuts = _materialize_splits(spec, {"ladder": NEWEST_MS}, ["ladder"])
        assert isinstance(cuts, TimeSplitConfig) and cuts.kind == "time"
        assert cuts.test_end_ms == NEWEST_MS
        assert cuts.val_end_ms == cuts.test_end_ms - 14 * DAY
        assert cuts.train_end_ms == cuts.val_end_ms - 28 * DAY

    def test_the_spec_itself_is_not_mutated(self):
        spec = TrailingSplitSpec(test_days=14, val_days=28)
        _materialize_splits(spec, {"ladder": NEWEST_MS}, ["ladder"])
        assert spec.to_obj() == {
            "kind": "trailing",
            "test_days": 14,
            "val_days": 28,
            "train_days": "all-prior",
            "notes": "",
        }


class TestRefusals:
    """Four refusals. None of them guesses."""

    def spec(self):
        return TrailingSplitSpec(test_days=2, val_days=3)

    # -- 1: nobody offered an edge -------------------------------------

    def test_no_edge_names_the_data_nodes_and_the_advice(self):
        with pytest.raises(ConfigError, match="none was supplied") as exc:
            _materialize_splits(self.spec(), {}, ["ladder", "settlement"])
        message = str(exc.value)
        assert "'ladder'" in message and "'settlement'" in message
        assert "Pin time cuts" in message

    def test_no_data_node_at_all_says_so(self):
        with pytest.raises(ConfigError, match="declares no data node") as exc:
            _materialize_splits(self.spec(), {}, [])
        assert "Pin time cuts" in str(exc.value)

    def test_driver_refuses_a_document_whose_sources_decline(self, tmp_path):
        # A data node that never overrode data_edge: the base declines.
        pipeline = {
            "events": NodeSpec(uses="dskit.pipeline.synthetic_nodes:SynthEvents")
        }
        with pytest.raises(ConfigError, match="none was supplied") as exc:
            run_document(trailing_doc(tmp_path, pipeline=pipeline), asof=ASOF)
        assert "'events'" in str(exc.value)
        assert os.listdir(tmp_path) == []  # refused before any run dir

    def test_driver_refuses_a_document_with_no_data_node(self, tmp_path):
        pipeline = {
            "step": NodeSpec(uses="tests.pipeline.test_trailing_seam:PlainNode")
        }
        with pytest.raises(ConfigError, match="declares no data node"):
            run_document(trailing_doc(tmp_path, pipeline=pipeline), asof=ASOF)

    # -- 2: more than one offered ---------------------------------------

    def test_two_edges_refuse_to_pick_and_name_both(self):
        with pytest.raises(ConfigError, match="needs ONE anchor") as exc:
            _materialize_splits(
                self.spec(),
                {"ladder": NEWEST_MS, "poly": NEWEST_MS - DAY},
                ["ladder", "poly"],
            )
        message = str(exc.value)
        assert "'ladder'" in message and "'poly'" in message
        assert "refusing to pick" in message
        # ... and it says WHY, which is the point of the refusal
        assert "silently moves every cut" in message

    def test_driver_refuses_two_edge_supplying_sources(self, tmp_path):
        pipeline = {
            "ladder": source_spec(tmp_path),
            "poly": source_spec(tmp_path, newest_ms=NEWEST_MS - 30 * DAY),
        }
        with pytest.raises(ConfigError, match="needs ONE anchor") as exc:
            run_document(trailing_doc(tmp_path, pipeline=pipeline), asof=ASOF)
        message = str(exc.value)
        assert "'ladder'" in message and "'poly'" in message
        # Both sources WERE asked — the ambiguity is real, not a short circuit.
        assert [key for stage, key, _snap in CALLS if stage == "data_edge"] == [
            "ladder",
            "poly",
        ]
        assert os.listdir(tmp_path) == []

    # -- 3: a bounded train window stamps train_start_ms (ADR-0050) -----

    def test_bounded_train_days_stamps_train_start(self):
        bounded = TrailingSplitSpec(test_days=14, val_days=28, train_days=30)
        cuts = _materialize_splits(bounded, {"ladder": NEWEST_MS}, ["ladder"])
        train_end = NEWEST_MS - (14 + 28) * DAY
        train_start = train_end - 30 * DAY + 1
        assert cuts.train_end_ms == train_end
        assert cuts.train_start_ms == train_start
        rec = SimpleNamespace(asof_ms=train_start, cluster="c")
        assert cuts.split_of(rec) == "train"
        rec.asof_ms = train_start - 1
        assert cuts.split_of(rec) is None

    def test_driver_materializes_bounded_train(self, tmp_path):
        doc = trailing_doc(
            tmp_path, splits=TrailingSplitSpec(test_days=2, val_days=3, train_days=30)
        )
        result = run_document(doc, asof=ASOF)
        assert result.state == "ran"
        resolved = os.path.join(result.run_dir, "resolved.json")
        with open(resolved, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert "train_start_ms" in payload["splits"]
        assert payload["splits"]["train_start_ms"] == (
            payload["splits"]["train_end_ms"] - 30 * DAY + 1
        )

    # -- 4: the edge cannot carry the windows ---------------------------

    def test_shallow_edge_surfaces_as_a_config_error(self):
        spec = TrailingSplitSpec(test_days=14, val_days=28)
        with pytest.raises(ConfigError, match="deep enough for the windows") as exc:
            _materialize_splits(spec, {"ladder": 5 * DAY}, ["ladder"])
        message = str(exc.value)
        assert "anchored on 'ladder'" in message
        assert f"newest_ms={5 * DAY}" in message

    def test_driver_surfaces_the_shallow_edge_refusal(self, tmp_path):
        pipeline = {"ladder": source_spec(tmp_path, newest_ms=DAY)}
        with pytest.raises(ConfigError, match="deep enough for the windows") as exc:
            run_document(trailing_doc(tmp_path, pipeline=pipeline), asof=ASOF)
        assert "anchored on 'ladder'" in str(exc.value)

    def test_non_integer_edge_refuses_rather_than_rounds(self, tmp_path):
        spec = TrailingSplitSpec(test_days=2, val_days=3)
        with pytest.raises(ConfigError, match="must be an int") as exc:
            _materialize_splits(spec, {"ladder": float(NEWEST_MS)}, ["ladder"])
        assert "anchored on 'ladder'" in str(exc.value)
        # ... and the same through the driver, from a source's own answer
        pipeline = {"ladder": source_spec(tmp_path, newest_ms=float(NEWEST_MS))}
        with pytest.raises(ConfigError, match="must be an int"):
            run_document(trailing_doc(tmp_path, pipeline=pipeline), asof=ASOF)


class TestEndToEnd:
    """A trailing document that RUNS — and stays reconstructible."""

    #: test = the newest 2 days, val = the 3 before that (trailing_doc).
    TRAIN_END = NEWEST_MS - 5 * DAY
    VAL_END = NEWEST_MS - 2 * DAY

    def doc(self, tmp_path):
        pipeline = {
            "ladder": source_spec(tmp_path),
            "bank": NodeSpec(
                uses="event-bank",
                inputs={"events": "$ladder.events"},
                params={"count": "all", "strictly_before": "$splits.train_end_ms"},
            ),
            "frame": NodeSpec(
                uses="tests.pipeline.test_trailing_seam:EchoSplits",
                inputs={"events": "$ladder.events"},
            ),
        }
        return trailing_doc(tmp_path, pipeline=pipeline)

    def test_a_trailing_document_runs(self, tmp_path):
        result = run_document(self.doc(tmp_path), asof=ASOF)
        assert result.state == "ran" and result.exit_code == 0
        assert set(result.node_states.values()) == {"ok"}

    def test_resolved_json_carries_the_materialized_cuts(self, tmp_path):
        result = run_document(self.doc(tmp_path), asof=ASOF)
        splits = read_json(result.run_dir, "resolved.json")["splits"]
        assert splits == {
            "kind": "time",
            "train_end_ms": self.TRAIN_END,
            "val_end_ms": self.VAL_END,
            "test_end_ms": NEWEST_MS,
            "notes": "",
        }

    def test_the_document_still_says_trailing(self, tmp_path):
        # Provenance: WHAT was declared and WHAT it became are both on
        # disk, so a rolled window is reconstructible after the fact.
        result = run_document(self.doc(tmp_path), asof=ASOF)
        assert read_json(result.run_dir, "config.json")["splits"] == {
            "kind": "trailing",
            "test_days": 2,
            "val_days": 3,
            "train_days": "all-prior",
            "notes": "",
        }

    def test_splits_references_resolve_against_the_materialized_cuts(self, tmp_path):
        result = run_document(self.doc(tmp_path), asof=ASOF)
        # 10 daily events at NEWEST_MS - i*DAY; strictly_before train_end
        # (= NEWEST_MS - 5*DAY) admits i = 6..9, and nothing else.
        assert result.outputs["bank"]["counts"] == {"SYNA": 4}
        assert result.outputs["bank"]["extents"]["SYNA"] == {
            "first_ms": NEWEST_MS - 9 * DAY,
            "last_ms": NEWEST_MS - 6 * DAY,
        }

    def test_the_cuts_reach_the_nodes_as_a_split_object(self, tmp_path):
        result = run_document(self.doc(tmp_path), asof=ASOF)
        frame = result.outputs["frame"]
        assert frame["splits_info"]["kind"] == "time"
        assert frame["splits_info"]["train_end_ms"] == self.TRAIN_END
        # 10 daily events: i=0,1 sit past val_end (test), i=2..4 in val,
        # i=5..9 at or before train_end — the object CLASSIFIES, it is not
        # a JSON echo of the cuts.
        assert frame["by_split"] == {"train": 5, "val": 3, "test": 2}

    def test_edges_are_read_before_the_run_dir_exists(self, tmp_path):
        result = run_document(self.doc(tmp_path), asof=ASOF)
        stages = [stage for stage, _key, _snap in CALLS]
        assert stages == ["fingerprint", "data_edge", "run"]
        # The run root was still EMPTY when identity and the edge were
        # asked — RESOLVE precedes the run dir, which precedes EXECUTE.
        assert CALLS[0][2] == [] and CALLS[1][2] == []
        assert CALLS[2][2] == [os.path.basename(result.run_dir)]
