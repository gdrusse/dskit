"""Walk-forward evaluation (ADR-0027): the spec grammar, the embargoed
splits, and the driver's per-fold execution + summary."""

from __future__ import annotations

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
    WalkForwardSpec,
)
from dskit.pipeline.driver import aggregate_folds, _fold_splits, run_walk_forward
from dskit.pipeline.node import Node
from dskit.pipeline.split_policy import EventBounds

DAY = 24 * 60 * 60 * 1000
ASOF = "2026-01-01"


class SplitProbe(Node):
    """Echoes the fold's materialized cuts: the score IS train_end_ms, so
    every fold's objective is distinct and exactly predictable."""

    role = "transform"
    outputs = ("score", "cuts")

    def run(self, ctx, inputs):
        return {"score": float(ctx.splits_info["train_end_ms"]), "cuts": dict(ctx.splits_info)}


class LateFoldGate(Node):
    """NO-GO once the fold's train cut passes ``halt_after_ms`` — how the
    halted-fold path is exercised."""

    role = "gate"
    outputs = ("verdict",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= {"halt_after_ms"} else ["unknown params"]

    def run(self, ctx, inputs):
        late = ctx.splits_info["train_end_ms"] > self.params["halt_after_ms"]
        return {"verdict": "NO-GO" if late else "GO"}


class BoundedEvents(Node):
    """A source that can answer the ADR-0024 question: a few records plus
    each event's observed extent, both handed in literally
    (``bounds`` = ``{cluster: [open_ms, close_ms]}``) so a test pins
    extents spanning whichever fold ranges it needs."""

    role = "data"
    outputs = ("events",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= {"bounds"} else ["unknown params"]

    def event_bounds(self):
        return {
            cluster: EventBounds(open_ms, close_ms)
            for cluster, (open_ms, close_ms) in self.params["bounds"].items()
        }

    def run(self, ctx, inputs):
        return {
            "events": [
                {"cluster": cluster, "asof_ms": open_ms}
                for cluster, (open_ms, _close_ms) in self.params["bounds"].items()
            ]
        }


class PolicyProbe(Node):
    """SplitProbe plus the ADR-0031 evidence: the policy this fold's
    ``splits_info`` carries, and where the fold's split object PUT a
    straddler pinned relative to the fold's own cuts — asof one day
    inside the embargo band, cluster named for the fold's cutoff instant.
    Embargoed folds only (it reads ``val_start_ms``)."""

    role = "transform"
    outputs = ("score", "policy", "assigned")

    def run(self, ctx, inputs):
        cut = ctx.splits_info["val_start_ms"]
        straddler = SimpleNamespace(asof_ms=cut - DAY, cluster=str(cut))
        return {
            "score": float(ctx.splits_info["train_end_ms"]),
            "policy": ctx.splits_info.get("policy", "record"),
            "assigned": ctx.splits.split_of(straddler),
        }


def probe_doc(tmp_path, wf, *, gate_after=None):
    pipeline = {
        "events": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthEvents",
            params={"n_events": 4},
        ),
    }
    probe_inputs = {"events": "$events.events"}
    if gate_after is not None:
        pipeline["gate"] = NodeSpec(
            uses="tests.pipeline.test_walkforward:LateFoldGate",
            inputs={"events": "$events.events"},
            params={"halt_after_ms": gate_after},
        )
        probe_inputs["verdict"] = "$gate.verdict"
    pipeline["probe"] = NodeSpec(
        uses="tests.pipeline.test_walkforward:SplitProbe", inputs=probe_inputs
    )
    return PipelineDocument(
        name="wfdemo",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
        walkforward=wf,
    )


def wf_spec(**overrides):
    base = {
        "objective": "$probe.score",
        "val_days": 7,
        "folds": ["2025-01-01", "2025-02-01"],
    }
    base.update(overrides)
    return WalkForwardSpec(**base)


# -- the spec grammar ----------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"objective": ""}, "objective"),
        ({"objective": "no-dollar"}, "objective"),
        ({"val_days": 0}, "val_days"),
        ({"embargo_days": -1}, "embargo_days"),
        ({"select": "best"}, "select"),
        ({"folds": []}, "folds"),
        ({"folds": ["01-01-2025"]}, "folds"),
        ({"folds": ["2025-02-01", "2025-01-01"]}, "ascending"),
        ({"folds": ["2025-01-01", "2025-01-01"]}, "ascending"),
        ({"folds": None}, "no folds declared"),
        ({"first": "2025-01-01", "step_days": 7, "count": 2}, "both"),
        ({"folds": None, "first": "bad", "step_days": 7, "count": 2}, "first"),
        ({"folds": None, "first": "2025-01-01", "count": 2}, "step_days"),
        ({"folds": None, "first": "2025-01-01", "step_days": 7}, "count"),
    ],
)
def test_spec_validation_refuses_by_name(overrides, needle):
    with pytest.raises(ConfigError, match=needle):
        wf_spec(**overrides)


def test_fold_cutoffs_explicit_and_generated():
    assert wf_spec().fold_cutoffs() == ("2025-01-01", "2025-02-01")
    generated = wf_spec(folds=None, first="2025-01-01", step_days=7, count=3)
    assert generated.fold_cutoffs() == ("2025-01-01", "2025-01-08", "2025-01-15")


def test_spec_round_trip_emits_only_the_active_declaration():
    explicit = wf_spec().to_obj()
    assert "folds" in explicit and "first" not in explicit
    assert "train_days" not in explicit
    assert "weight_halflife_folds" not in explicit
    assert WalkForwardSpec.from_obj(explicit).fold_cutoffs() == (
        "2025-01-01",
        "2025-02-01",
    )
    generated = wf_spec(folds=None, first="2025-01-01", step_days=7, count=2)
    obj = generated.to_obj()
    assert "folds" not in obj and obj["count"] == 2
    with pytest.raises(ConfigError, match="every_fold"):
        WalkForwardSpec.from_obj({**explicit, "every_fold": True})


# -- the document integration --------------------------------------------------


def test_document_carries_and_hashes_the_section(tmp_path):
    doc = probe_doc(tmp_path, wf_spec())
    obj = doc.to_obj()
    assert obj["walkforward"]["objective"] == "$probe.score"
    round_tripped = PipelineDocument.from_obj(obj)
    assert round_tripped.hash == doc.hash
    # The section is IDENTITY (unlike schedule): a different fold plan is
    # a different experiment.
    other = probe_doc(tmp_path, wf_spec(folds=["2025-01-01", "2025-03-01"]))
    assert other.hash != doc.hash


def test_documents_without_the_section_do_not_emit_the_key(tmp_path):
    doc = probe_doc(tmp_path, wf_spec())
    plain = PipelineDocument(
        name="plain", pipeline=dict(doc.pipeline), outputs=doc.outputs
    )
    assert "walkforward" not in plain.to_obj()  # pre-ADR-0027 hashes must not move


def test_objective_must_reference_a_declared_node(tmp_path):
    with pytest.raises(ConfigError, match="DECLARED node"):
        probe_doc(tmp_path, wf_spec(objective="$nope.score"))


# -- embargoed time splits (the split-level half of ADR-0027) ------------------


class _Rec:
    def __init__(self, t):
        self.asof_ms = t


def test_val_start_opens_an_embargo_band():
    cuts = TimeSplitConfig(
        train_end_ms=10 * DAY,
        val_start_ms=12 * DAY,
        val_end_ms=20 * DAY,
        test_end_ms=25 * DAY,
    )
    assert cuts.split_of(_Rec(10 * DAY)) == "train"
    assert cuts.split_of(_Rec(11 * DAY)) is None  # embargoed: NO split
    assert cuts.split_of(_Rec(12 * DAY)) == "val"
    assert cuts.split_of(_Rec(20 * DAY)) == "val"
    assert cuts.split_of(_Rec(21 * DAY)) == "test"
    assert cuts.split_of(_Rec(26 * DAY)) is None


def test_val_start_invariants_and_identity_omission():
    with pytest.raises(ConfigError, match="val_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, val_start_ms=10, val_end_ms=20, test_end_ms=30
        )
    with pytest.raises(ConfigError, match="val_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, val_start_ms=25, val_end_ms=20, test_end_ms=30
        )
    plain = TimeSplitConfig(train_end_ms=10, val_end_ms=20, test_end_ms=30)
    assert "val_start_ms" not in plain.to_obj()  # existing identities must not move
    banded = TimeSplitConfig(
        train_end_ms=10, val_start_ms=15, val_end_ms=20, test_end_ms=30
    )
    assert TimeSplitConfig.from_obj(banded.to_obj()) == banded


def test_trailing_embargo_materializes_the_band():
    spec = TrailingSplitSpec(test_days=5, val_days=10, embargo_days=3)
    cuts = spec.materialize(100 * DAY)
    assert cuts.test_end_ms == 100 * DAY
    assert cuts.val_end_ms == 95 * DAY
    assert cuts.val_start_ms == 85 * DAY
    assert cuts.train_end_ms == 82 * DAY  # the embargo came out of TRAIN's tail
    assert "embargo_days" not in TrailingSplitSpec(test_days=5, val_days=10).to_obj()
    assert TrailingSplitSpec.from_obj(spec.to_obj()) == spec
    no_embargo = TrailingSplitSpec(test_days=5, val_days=10).materialize(100 * DAY)
    assert no_embargo.val_start_ms is None


# -- the calibration band (ADR-0034) -------------------------------------------


def test_cal_start_opens_a_cal_band():
    cuts = TimeSplitConfig(
        train_end_ms=10 * DAY,
        val_start_ms=12 * DAY,
        cal_start_ms=16 * DAY,
        val_end_ms=20 * DAY,
        test_end_ms=25 * DAY,
    )
    assert cuts.split_of(_Rec(10 * DAY)) == "train"
    assert cuts.split_of(_Rec(11 * DAY)) is None  # embargoed: NO split
    assert cuts.split_of(_Rec(12 * DAY)) == "val"
    assert cuts.split_of(_Rec(15 * DAY)) == "val"
    assert cuts.split_of(_Rec(16 * DAY)) == "cal"  # inclusive start
    assert cuts.split_of(_Rec(20 * DAY)) == "cal"  # inclusive end (= val_end)
    assert cuts.split_of(_Rec(21 * DAY)) == "test"
    assert cuts.split_of(_Rec(26 * DAY)) is None
    # Without an embargo, the band floor is train_end.
    plain = TimeSplitConfig(
        train_end_ms=10 * DAY,
        cal_start_ms=16 * DAY,
        val_end_ms=20 * DAY,
        test_end_ms=25 * DAY,
    )
    assert plain.split_of(_Rec(11 * DAY)) == "val"
    assert plain.split_of(_Rec(16 * DAY)) == "cal"


def test_cal_start_invariants_and_identity_omission():
    # cal must sit strictly after the val floor and inside the val window
    with pytest.raises(ConfigError, match="cal_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, cal_start_ms=10, val_end_ms=20, test_end_ms=30
        )
    with pytest.raises(ConfigError, match="cal_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, cal_start_ms=25, val_end_ms=20, test_end_ms=30
        )
    with pytest.raises(ConfigError, match="cal_start_ms"):
        TimeSplitConfig(
            train_end_ms=10,
            val_start_ms=15,
            cal_start_ms=15,  # == val_start: val would be empty
            val_end_ms=20,
            test_end_ms=30,
        )
    plain = TimeSplitConfig(train_end_ms=10, val_end_ms=20, test_end_ms=30)
    assert "cal_start_ms" not in plain.to_obj()  # existing identities must not move
    banded = TimeSplitConfig(
        train_end_ms=10, cal_start_ms=17, val_end_ms=20, test_end_ms=30
    )
    assert TimeSplitConfig.from_obj(banded.to_obj()) == banded


def test_trailing_cal_materializes_the_band():
    spec = TrailingSplitSpec(test_days=5, val_days=10, cal_days=3, embargo_days=2)
    cuts = spec.materialize(100 * DAY)
    assert cuts.test_end_ms == 100 * DAY
    assert cuts.val_end_ms == 95 * DAY
    # +1: the cal band is inclusive-left, so the shifted cut keeps the
    # midnight stamp at 92D in VAL — cal = stamps {93D, 94D, 95D},
    # exactly cal_days of daily stamps, none stolen from val.
    assert cuts.cal_start_ms == 92 * DAY + 1
    assert cuts.val_start_ms == 82 * DAY + 1  # val counts back from the band
    assert cuts.train_end_ms == 80 * DAY + 1
    # Boundary-by-boundary on daily midnight stamps:
    assert cuts.split_of(_Rec(92 * DAY)) == "val"
    assert cuts.split_of(_Rec(93 * DAY)) == "cal"
    assert cuts.split_of(_Rec(95 * DAY)) == "cal"
    assert cuts.split_of(_Rec(96 * DAY)) == "test"
    # Ten daily stamps in val, as declared:
    val_stamps = [d for d in range(80, 101) if cuts.split_of(_Rec(d * DAY)) == "val"]
    assert len(val_stamps) == 10
    assert "cal_days" not in TrailingSplitSpec(test_days=5, val_days=10).to_obj()
    assert TrailingSplitSpec.from_obj(spec.to_obj()) == spec
    no_cal = TrailingSplitSpec(test_days=5, val_days=10).materialize(100 * DAY)
    assert no_cal.cal_start_ms is None


def test_walkforward_refuses_a_parent_cal_band(tmp_path):
    doc = probe_doc(tmp_path, wf_spec())
    with_cal = PipelineDocument(
        name="cal-wf",
        pipeline=dict(doc.pipeline),
        outputs=doc.outputs,
        walkforward=doc.walkforward,
        splits=TimeSplitConfig(
            train_end_ms=10 * DAY,
            cal_start_ms=16 * DAY,
            val_end_ms=20 * DAY,
            test_end_ms=25 * DAY,
        ),
    )
    with pytest.raises(ConfigError, match="cannot carry a cal band"):
        run_walk_forward(with_cal, asof="2026-01-01")


# -- the driver: one run per fold + a summary ----------------------------------


def read_json(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return json.load(fh)


def probe_outputs(run_dir):
    """The probe node's recorded outputs (small strings/scalars survive
    the per-node record whole)."""
    nodes_dir = os.path.join(run_dir, "nodes")
    (name,) = [f for f in os.listdir(nodes_dir) if f.endswith("-probe.json")]
    return read_json(nodes_dir, name)["outputs"]


def test_a_policyless_splits_parent_runs_folds_under_record(tmp_path):
    """The getattr fallback, pinned (review M2): a splits family with no
    policy field at all — random — must leave every fold on 'record':
    no policy key in the fold's resolved splits, no bounds demand."""
    from dataclasses import replace

    doc = replace(
        probe_doc(tmp_path, wf_spec()),
        splits=RandomSplitSpec(train_frac=0.8, val_frac=0.2, seed=7),
    )
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "ran"
    for fold in result.folds:
        splits = read_json(fold["run_dir"], "resolved.json")["splits"]
        assert "policy" not in splits


def test_a_declared_event_policy_rides_every_fold(tmp_path):
    """ADR-0031 (rewrites the old walkforward-door refusal): the parent
    document's declared policy is STAMPED on every fold's pinned cuts and
    HONORED there — each fold's run binds bounds from the fold's data
    nodes (ADR-0024) and the embargo band applies to the policy-selected
    instant. The straddler pins the direction test_split_policy.py pins:
    its own asof sits INSIDE the embargo band (record policy: NO split),
    its event closes inside val, and event-close carries the whole event
    FORWARD into val."""
    from dataclasses import replace

    from dskit.pipeline.driver import _cutoff_ms

    cutoffs = ["2025-01-01", "2025-02-01"]
    bounds = {}
    for cut in (_cutoff_ms(c) for c in cutoffs):
        bounds[str(cut)] = [cut - 2 * DAY, cut + DAY]
    doc = replace(
        PipelineDocument(
            name="wfpolicy",
            pipeline={
                "events": NodeSpec(
                    uses="tests.pipeline.test_walkforward:BoundedEvents",
                    params={"bounds": bounds},
                ),
                "probe": NodeSpec(
                    uses="tests.pipeline.test_walkforward:PolicyProbe",
                    inputs={"events": "$events.events"},
                ),
            },
            outputs=OutputsConfig(run_root=str(tmp_path)),
            walkforward=wf_spec(embargo_days=3),
        ),
        splits=TimeSplitConfig(
            train_end_ms=1_000,
            val_end_ms=2_000,
            test_end_ms=3_000,
            policy="event-close",
        ),
    )
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "ran"
    assert [f["cutoff"] for f in result.folds] == cutoffs
    for fold in result.folds:
        seen = probe_outputs(fold["run_dir"])
        assert seen["policy"] == "event-close"  # ctx.splits_info, EVERY fold
        assert seen["assigned"] == "val"  # honored, not merely stamped


def test_a_fold_bounds_refusal_propagates_out_of_run_walk_forward(tmp_path):
    """ADR-0031's other half: with the policy carried, a fold whose data
    nodes cannot answer event_bounds() must refuse OUT of
    run_walk_forward — the per-fold ADR-0024 ConfigError propagates,
    never swallowed into a silent record fallback nor buried as one
    fold's recorded "error". Nothing lands on disk: no fold run dir, no
    summary."""
    from dataclasses import replace

    doc = replace(
        probe_doc(tmp_path, wf_spec()),
        splits=TimeSplitConfig(
            train_end_ms=1_000,
            val_end_ms=2_000,
            test_end_ms=3_000,
            policy="event-close",
        ),
    )
    with pytest.raises(ConfigError, match="do not implement event_bounds"):
        run_walk_forward(doc, asof=ASOF)
    assert not any(tmp_path.iterdir())


def test_walk_forward_runs_each_fold_and_aggregates(tmp_path):
    result = run_walk_forward(probe_doc(tmp_path, wf_spec()), asof=ASOF)
    assert result.state == "ran"
    assert result.exit_code == 0
    assert [f["cutoff"] for f in result.folds] == ["2025-01-01", "2025-02-01"]
    # The probe's score IS each fold's train_end_ms — pinned arithmetic:
    # half-open cuts, so train ends 1ms BEFORE the cutoff instant (the
    # cutoff day validates, never trains).
    from dskit.pipeline.driver import _cutoff_ms

    expected = [float(_cutoff_ms(c) - 1) for c in ("2025-01-01", "2025-02-01")]
    assert [f["score"] for f in result.folds] == expected
    assert result.aggregate["n_scored"] == 2
    assert result.aggregate["best_cutoff"] == "2025-01-01"  # select=min
    # Every fold is an ordinary run dir of its own series.
    for fold in result.folds:
        assert os.path.basename(fold["run_dir"]).startswith(
            f"wfdemo-wf-{fold['cutoff']}-{ASOF}"
        )
        assert os.path.isfile(os.path.join(fold["run_dir"], "result.json"))
    summary = read_json(result.summary_dir, "walkforward.json")
    assert summary["state"] == "ran"
    assert summary["aggregate"]["mean"] == pytest.approx(sum(expected) / 2)
    assert os.path.isfile(os.path.join(result.summary_dir, "report.md"))


def test_walk_forward_generated_schedule_and_embargo_cuts(tmp_path):
    wf = wf_spec(
        folds=None, first="2025-01-01", step_days=7, count=2, embargo_days=3
    )
    result = run_walk_forward(probe_doc(tmp_path, wf), asof=ASOF)
    assert [f["cutoff"] for f in result.folds] == ["2025-01-01", "2025-01-08"]
    from dskit.pipeline.driver import _cutoff_ms

    cuts = read_json(result.folds[0]["run_dir"], "resolved.json")["splits"]
    c = _cutoff_ms("2025-01-01")
    assert cuts["val_start_ms"] == c
    # Half-open both ways: a 3-day embargo excludes exactly 3 midnight
    # stamps, a 7-day val window holds exactly 7.
    assert cuts["train_end_ms"] == c - 3 * DAY - 1
    assert cuts["val_end_ms"] == c + 7 * DAY - 1


def test_a_halted_fold_is_a_result_and_later_folds_still_run(tmp_path):
    from dskit.pipeline.driver import _cutoff_ms

    gate_after = _cutoff_ms("2025-01-15")  # fold 1 passes, fold 2 halts
    doc = probe_doc(tmp_path, wf_spec(), gate_after=gate_after)
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "halted"
    assert result.exit_code == 3
    assert result.folds[0]["state"] == "ran"
    assert result.folds[0]["score"] is not None
    assert result.folds[1]["state"] == "halted"
    assert result.folds[1]["score"] is None
    assert result.aggregate["n_scored"] == 1


def test_an_unreadable_objective_is_a_fold_error_that_stops_the_plan(tmp_path):
    doc = probe_doc(tmp_path, wf_spec(objective="$probe.no_such_output"))
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "error"
    assert result.exit_code == 1
    assert result.folds[0]["state"] == "error"
    assert len(result.folds) == 1  # the plan stopped at the erroring fold


def test_fold_boundary_membership_is_embargo_invariant():
    """The skeptic finding: the record stamped AT the cutoff instant must
    land in val with and without an embargo, and a val_days window must
    hold exactly val_days midnight stamps either way."""
    from dskit.pipeline.driver import _cutoff_ms, _fold_splits

    c = _cutoff_ms("2025-01-01")
    plain = _fold_splits(wf_spec(), "2025-01-01")
    banded = _fold_splits(wf_spec(embargo_days=3), "2025-01-01")
    for cuts in (plain, banded):
        assert cuts.split_of(_Rec(c)) == "val"  # the cutoff day validates
        assert cuts.split_of(_Rec(c + 7 * DAY - 1)) == "val"
        assert cuts.split_of(_Rec(c + 7 * DAY)) != "val"  # half-open window
    assert plain.split_of(_Rec(c - 1)) == "train"
    # The 3-day embargo excludes exactly the 3 midnight stamps before it.
    for d in (1, 2, 3):
        assert banded.split_of(_Rec(c - d * DAY)) is None
    assert banded.split_of(_Rec(c - 4 * DAY)) == "train"


def test_impossible_calendar_dates_refuse_at_validate():
    with pytest.raises(ConfigError, match="REAL"):
        wf_spec(folds=["2026-01-15", "2026-02-30"])
    with pytest.raises(ConfigError, match="REAL"):
        wf_spec(folds=None, first="2026-02-30", step_days=7, count=2)


def test_the_spec_never_shares_its_folds_list():
    handed = ["2025-01-01", "2025-02-01"]
    spec = wf_spec(folds=handed)
    handed.append("2025-03-01")  # the builder's list is not the spec's
    assert spec.fold_cutoffs() == ("2025-01-01", "2025-02-01")
    obj = spec.to_obj()
    obj["folds"].append("2025-04-01")  # nor is to_obj's
    assert spec.fold_cutoffs() == ("2025-01-01", "2025-02-01")


def test_a_refused_fold_is_recorded_and_the_summary_still_lands(tmp_path):
    """The skeptic finding: run_document RAISES pre-flight refusals (an
    occupied fold run dir); the loop must record the fold and write the
    summary rather than discard every completed fold's score."""
    import shutil

    doc = probe_doc(tmp_path, wf_spec())
    first = run_walk_forward(doc, asof=ASOF)
    shutil.rmtree(first.summary_dir)  # the folds stay occupied
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "error"
    assert result.exit_code == 1
    assert result.folds[0]["state"] == "error"
    assert "already happened" in result.folds[0]["error"]
    summary = read_json(result.summary_dir, "walkforward.json")
    assert summary["state"] == "error"
    assert os.path.isfile(os.path.join(result.summary_dir, "report.md"))


def test_missing_section_and_occupied_summary_refuse(tmp_path):
    plain = PipelineDocument(
        name="plain",
        pipeline={
            "events": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 4},
            )
        },
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    with pytest.raises(ConfigError, match="no walkforward section"):
        run_walk_forward(plain, asof=ASOF)
    doc = probe_doc(tmp_path, wf_spec())
    run_walk_forward(doc, asof=ASOF)
    with pytest.raises(ValueError, match="already happened"):
        run_walk_forward(doc, asof=ASOF)


def test_shipped_example_loads_hashes_and_runs(tmp_path):
    import pathlib

    from dskit.pipeline.document import load_document

    example = (
        pathlib.Path(__file__).parents[2]
        / "examples"
        / "pipeline"
        / "walk-forward.json"
    )
    doc = load_document(str(example))
    assert doc.walkforward is not None
    assert doc.walkforward.fold_cutoffs() == (
        "1973-03-01",
        "1973-03-31",
        "1973-04-30",
    )
    assert doc.hash == load_document(str(example)).hash
    pytest.importorskip("torch")
    obj = json.loads(example.read_text(encoding="utf-8"))
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_walk_forward(PipelineDocument.from_obj(obj), asof="1973-08-01")
    assert result.state == "ran"
    assert result.aggregate["n_scored"] == 3
    resolved = read_json(result.folds[0]["run_dir"], "resolved.json")
    assert "val_start_ms" in resolved["splits"]  # the embargo band materialized


def test_cli_verb_prints_the_summary_and_reports_exit(tmp_path, capsys):
    from dskit.pipeline.__main__ import main
    from dskit.pipeline.document import save_document

    doc = probe_doc(tmp_path / "runs", wf_spec())
    path = str(tmp_path / "wf.json")
    save_document(doc, path)
    code = main(["walkforward", path, "--asof", ASOF])
    out = capsys.readouterr().out
    assert code == 0
    assert "WALK-FORWARD RAN" in out
    assert "summary dir:" in out


# -- HPO inside walk-forward (ADR-0043) ----------------------------------------


class ThetaKnob(Node):
    """One tunable knob (role ``train``): the value IS ``theta``, so a
    search over it moves the objective by exactly the declared amount.
    ``kernel`` is inert — a free-form STRING a search can win, because
    the winner is the one report cell that prints a user's own value."""

    role = "train"
    outputs = ("value",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= {"theta", "kernel"} else ["unknown params"]

    def run(self, ctx, inputs):
        return {"value": float(self.params["theta"])}


class MovingTargetScore(Node):
    """A val-split score whose optimum MOVES with the fold: the loss is
    ``(value - late)^2`` once the fold's train cut passes ``switch_ms``
    and ``(value - early)^2`` before it — so the folds genuinely
    disagree about which theta wins, which is the whole diagnostic."""

    role = "score"
    outputs = ("metrics",)

    @classmethod
    def validate_params(cls, params):
        known = {"split", "switch_ms", "early", "late"}
        return [] if set(params) <= known else ["unknown params"]

    def run(self, ctx, inputs):
        late = ctx.splits_info["train_end_ms"] > self.params["switch_ms"]
        target = self.params["late" if late else "early"]
        return {"metrics": {"loss": (inputs["value"] - target) ** 2}}


class ThetaGate(Node):
    """GO iff ``value <= bar`` (role ``gate``), sitting between the knob
    and the score — the winner-pass verdict-flip refusal's subject."""

    role = "gate"
    outputs = ("value", "verdict")

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= {"bar"} else ["unknown params"]

    def run(self, ctx, inputs):
        value = inputs["value"]
        return {
            "value": value,
            "verdict": "GO" if value <= self.params["bar"] else "NO-GO",
        }


class PinnedSearch(Node):
    """A search kind whose winner STATE is declared, not discovered
    (role ``search``).

    It drives the seam once, then emits exactly the winner its params
    pin: a JSON value (``winner``), no ``best_params`` key at all
    (``emit_winner: false``), or a value JSON cannot hold
    (``infinite_winner: true``, or ``infinite_after_ms`` for the folds
    past that train cut only) — the three states ADR-0043 asks the run
    result to tell apart.
    """

    role = "search"

    @classmethod
    def validate_params(cls, params):
        known = {
            "boom",
            "emit_winner",
            "infinite_after_ms",
            "infinite_winner",
            "objective",
            "space",
            "winner",
        }
        return [] if set(params) <= known else ["unknown params"]

    def _drops(self, ctx):
        after = self.params.get("infinite_after_ms")
        return bool(self.params.get("infinite_winner")) or (
            after is not None and ctx.splits_info["train_end_ms"] > after
        )

    def run(self, ctx, inputs):
        probe = {"theta.theta": 3.0}
        score = ctx.rerun(dict(probe))
        if self.params.get("boom"):
            raise RuntimeError("the search itself failed")
        out = {"best_score": score, "trials": [{"overrides": probe, "score": score}]}
        if self._drops(ctx):
            out["best_params"] = {"theta.theta": float("inf")}
        elif self.params.get("emit_winner", True):
            out["best_params"] = self.params.get("winner")
        return out


def hpo_pipeline(switch_ms, *, uses="hpo-grid", search_params=None, suffix="", theta=9.0):
    """One search chain — knob, fold-dependent val score, search node."""
    params = {
        "space": {f"theta{suffix}.theta": [1.0, 2.0]},
        "objective": f"$val{suffix}.metrics.loss",
    }
    params.update(search_params or {})
    return {
        f"theta{suffix}": NodeSpec(
            uses="tests.pipeline.test_walkforward:ThetaKnob",
            params={"theta": theta, "kernel": "base"},
        ),
        f"val{suffix}": NodeSpec(
            uses="tests.pipeline.test_walkforward:MovingTargetScore",
            inputs={"value": f"$theta{suffix}.value"},
            params={"split": "val", "switch_ms": switch_ms, "early": 1.0, "late": 2.0},
        ),
        f"tune{suffix}": NodeSpec(uses=uses, params=params),
    }


def hpo_doc(tmp_path, pipeline, *, objective="$val.metrics.loss", folds=None):
    return PipelineDocument(
        name="wfhpo",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
        walkforward=wf_spec(
            objective=objective,
            folds=list(folds or ["2025-01-01", "2025-02-01", "2025-03-01"]),
        ),
    )


def switch_between_folds():
    """A ``switch_ms`` that lands after fold 1's train cut and before the
    rest — folds 2 and 3 then share a winner fold 1 does not."""
    from dskit.pipeline.driver import _cutoff_ms

    return _cutoff_ms("2025-01-15")


def test_per_fold_winners_that_disagree_are_counted_and_printed(tmp_path):
    """The point of ADR-0043: three folds, two winners, and a reader of
    the summary can SEE the folds disagreed."""
    doc = hpo_doc(tmp_path, hpo_pipeline(switch_between_folds()))
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "ran"
    assert [f["search"]["tune"]["winner"] for f in result.folds] == [
        {"theta.theta": 1.0},
        {"theta.theta": 2.0},
        {"theta.theta": 2.0},
    ]
    assert [f["search"]["tune"]["trials_executed"] for f in result.folds] == [2, 2, 2]
    assert [f["search"]["tune"]["winner_score"] for f in result.folds] == [0.0] * 3
    assert result.aggregate["search"] == {
        "tune": {"n_folds_with_winner": 3, "n_distinct_winners": 2}
    }
    summary = read_json(result.summary_dir, "walkforward.json")
    assert summary["aggregate"]["search"]["tune"]["n_distinct_winners"] == 2
    assert summary["folds"][0]["search"]["tune"]["winner_reran"] == ["theta", "val"]
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    assert "| tune | 3 | 2 | 0 |" in report  # the aggregate row, reconciled
    assert '| 2025-01-01 | tune | 2 | `{"theta.theta": 1.0}` |' in report
    assert '| 2025-02-01 | tune | 2 | `{"theta.theta": 2.0}` |' in report
    assert "6 trial(s) executed" in report  # counted, never predicted


def test_two_search_nodes_stay_distinguishable(tmp_path):
    """Node-keyed, so K>1 searches never collapse into one tally: the
    second chain's score never switches, so its folds all agree."""
    switch = switch_between_folds()
    pipeline = hpo_pipeline(switch)
    pipeline.update(hpo_pipeline(switch * 10, suffix="_b"))
    result = run_walk_forward(hpo_doc(tmp_path, pipeline), asof=ASOF)
    assert result.state == "ran"
    assert result.aggregate["search"] == {
        "tune": {"n_folds_with_winner": 3, "n_distinct_winners": 2},
        "tune_b": {"n_folds_with_winner": 3, "n_distinct_winners": 1},
    }
    assert result.folds[2]["search"]["tune_b"]["winner"] == {"theta_b.theta": 1.0}


def test_a_search_that_produces_no_winner_reports_none(tmp_path):
    """Presence, not value: a kind that emits no ``best_params`` at all
    records no winner, and the section still says the search RAN."""
    pipeline = hpo_pipeline(
        switch_between_folds(),
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"emit_winner": False},
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, objective="$tune.best_score", folds=["2025-01-01"]),
        asof=ASOF,
    )
    assert result.state == "ran"
    meta = result.folds[0]["search"]["tune"]
    assert meta["trials_executed"] == 1
    assert "winner" not in meta
    assert "winner_dropped" not in meta
    assert meta["winner_score"] == 4.0  # (3 - 1)^2 on the probe trial
    assert result.aggregate["search"] == {
        "tune": {"n_folds_with_winner": 0, "n_distinct_winners": 0}
    }
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        assert "| 2025-01-01 | tune | 1 | — |" in fh.read()


def test_a_winner_of_none_is_not_a_missing_winner(tmp_path):
    """The other side of the presence rule: a kind CAN choose ``None``,
    and that is a winner the summary counts."""
    pipeline = hpo_pipeline(
        switch_between_folds(),
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"emit_winner": True},
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, objective="$tune.best_score", folds=["2025-01-01"]),
        asof=ASOF,
    )
    meta = result.folds[0]["search"]["tune"]
    assert "winner" in meta and meta["winner"] is None
    assert "winner_reran" not in meta  # a falsy winner is never applied
    assert result.aggregate["search"] == {
        "tune": {"n_folds_with_winner": 1, "n_distinct_winners": 1}
    }


def test_a_winner_json_cannot_hold_is_dropped_never_coerced(tmp_path):
    """An infinite override is applied to the run but NOT invented into
    the record: the fold reports a winner it cannot print."""
    pipeline = hpo_pipeline(
        switch_between_folds(),
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"infinite_winner": True},
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, objective="$tune.best_score", folds=["2025-01-01"]),
        asof=ASOF,
    )
    assert result.state == "ran"
    meta = result.folds[0]["search"]["tune"]
    assert "winner" not in meta
    assert meta["winner_dropped"] == ["best_params"]
    assert meta["winner_reran"] == ["theta", "val"]  # applied all the same
    assert result.aggregate["search"] == {
        "tune": {
            "n_folds_with_winner": 1,
            "n_distinct_winners": 0,
            "n_folds_dropped": 1,
        }
    }
    read_json(result.summary_dir, "walkforward.json")  # the record stayed writable
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        assert "dropped (not JSON-legal)" in fh.read()


def test_the_winner_that_caused_a_flip_refusal_is_still_reported(tmp_path, monkeypatch):
    """Population happens BEFORE the winner is applied, so the fold that
    refused to ride a stale GO still names the winner that caused it.

    The ORDER needs a witness of its own, because no VALUE below can be
    one: ``_execute_plan``'s except-handler rebuilds the record from the
    same seam and the same ``attempt.outputs``, so writing it AFTER
    ``apply_winner`` leaves every assertion on the fold row, the summary
    and the report unchanged — they would pin the guarantee while the
    clause that delivers it was gone. What only the ordering can produce
    is the record standing on ``run`` ALREADY as the raise leaves
    ``_run_one_node``, which is what makes the caller's rebuild
    redundancy rather than the mechanism. So that is what is asserted.
    """
    import copy
    from dataclasses import replace

    from dskit.pipeline import driver

    escaped = []
    run_one_node = driver._run_one_node

    def witness(attempt, key, spec, the_plan, ctx, run, instances):
        """Snapshot the run's search records as a node's raise escapes."""
        try:
            return run_one_node(attempt, key, spec, the_plan, ctx, run, instances)
        except Exception:
            escaped.append((key, copy.deepcopy(run.search_meta)))
            raise

    monkeypatch.setattr(driver, "_run_one_node", witness)

    pipeline = hpo_pipeline(
        switch_between_folds(), search_params={"select": "max"}, theta=1.0
    )
    pipeline["gate"] = NodeSpec(
        uses="tests.pipeline.test_walkforward:ThetaGate",
        inputs={"value": "$theta.value"},
        params={"bar": 1.5},
    )
    pipeline["val"] = replace(pipeline["val"], inputs={"value": "$gate.value"})
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, folds=["2025-01-01"]), asof=ASOF
    )
    assert result.state == "error"
    meta = result.folds[0]["search"]["tune"]
    assert meta["winner"] == {"theta.theta": 2.0}  # the winner that flipped the gate
    assert "winner_reran" not in meta  # the apply never completed
    # ...and it survives into the artifact the refusing fold leaves.
    nodes_dir = os.path.join(result.folds[0]["run_dir"], "nodes")
    tune_record = next(f for f in sorted(os.listdir(nodes_dir)) if f.endswith("-tune.json"))
    node_record = read_json(nodes_dir, tune_record)
    assert node_record["status"] == "error"
    assert node_record["winner"] == {"theta.theta": 2.0}
    # The ordering clause itself. The search node is the node whose raise
    # escaped, and its record was already written when it did.
    assert [key for key, _ in escaped] == ["tune"]
    at_raise = escaped[0][1]
    assert "tune" in at_raise, "the winner was recorded only AFTER apply_winner"
    assert at_raise["tune"] == meta


def test_a_search_that_failed_still_reports_the_trials_it_burned(tmp_path):
    """The other winner-failure path: the search node itself raised, so
    there is no winner to report — but the trials it executed were paid
    for, and the fold row says how many."""
    pipeline = hpo_pipeline(
        switch_between_folds(),
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"boom": True},
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, objective="$tune.best_score", folds=["2025-01-01"]),
        asof=ASOF,
    )
    assert result.state == "error"
    assert result.folds[0]["search"] == {"tune": {"trials_executed": 1}}
    assert result.aggregate["search"] == {
        "tune": {"n_folds_with_winner": 0, "n_distinct_winners": 0}
    }
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    # The cost line COUNTS (ADR-0043 §1): this fold never reached a
    # winner pass, so the report must not bill one.
    assert (
        "Cost, counted: 1 fold(s) searched, 1 trial(s) executed, "
        "0 winner pass(es) applied." in report
    )


def test_the_cost_line_counts_only_the_folds_that_searched(tmp_path):
    """Counted, never predicted (ADR-0043 §1): a fold that HALTED before
    it reached the search node bought no trials and no winner pass, so
    the cost line must not bill it — ``n_folds`` is the wrong number."""
    from dataclasses import replace

    switch = switch_between_folds()
    pipeline = hpo_pipeline(switch)
    pipeline["gate"] = NodeSpec(
        uses="tests.pipeline.test_walkforward:LateFoldGate",
        inputs={"value": "$theta.value"},
        params={"halt_after_ms": switch},
    )
    pipeline["val"] = replace(
        pipeline["val"], inputs={"value": "$theta.value", "verdict": "$gate.verdict"}
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, folds=["2025-01-01", "2025-02-01"]), asof=ASOF
    )
    assert result.state == "halted"
    assert "search" not in result.folds[1]  # it never reached the node
    assert result.aggregate["n_folds"] == 2
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    assert (
        "Cost, counted: 1 fold(s) searched, 2 trial(s) executed, "
        "1 winner pass(es) applied." in report
    )


def test_the_aggregate_row_reconciles_the_winners_it_could_not_compare(tmp_path):
    """One fold's winner printable and the next fold's not JSON-legal:
    ``2 folds with a winner, 1 distinct`` reads exactly like two folds
    that AGREED. The dropped count is what makes the row add up, and
    seeing the disagreement is the point of the ADR."""
    switch = switch_between_folds()
    pipeline = hpo_pipeline(
        switch,
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"winner": {"theta.theta": 1.0}, "infinite_after_ms": switch},
    )
    result = run_walk_forward(
        hpo_doc(
            tmp_path,
            pipeline,
            objective="$tune.best_score",
            folds=["2025-01-01", "2025-02-01"],
        ),
        asof=ASOF,
    )
    assert result.state == "ran"
    assert result.aggregate["search"] == {
        "tune": {
            "n_folds_with_winner": 2,
            "n_distinct_winners": 1,
            "n_folds_dropped": 1,
        }
    }
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    assert (
        "| search node | folds with a winner | distinct winners | dropped |" in report
    )
    assert "| tune | 2 | 1 | 1 |" in report


def test_a_winner_carrying_a_pipe_keeps_its_table_row_intact(tmp_path):
    """The winner is the one report cell that prints a free-form value,
    and a raw ``|`` ENDS a markdown cell — an unescaped one would give
    the row a fifth column and misalign the whole table."""
    import re

    pipeline = hpo_pipeline(
        switch_between_folds(),
        uses="tests.pipeline.test_walkforward:PinnedSearch",
        search_params={"winner": {"theta.kernel": "rbf|linear"}},
    )
    result = run_walk_forward(
        hpo_doc(tmp_path, pipeline, objective="$tune.best_score", folds=["2025-01-01"]),
        asof=ASOF,
    )
    assert result.state == "ran"
    assert result.folds[0]["search"]["tune"]["winner"] == {"theta.kernel": "rbf|linear"}
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    row = next(
        line for line in report.splitlines() if line.startswith("| 2025-01-01 | tune |")
    )
    assert row == r'| 2025-01-01 | tune | 1 | `{"theta.kernel": "rbf\|linear"}` |'
    assert len(re.split(r"(?<!\\)\|", row)[1:-1]) == 4  # four cells, still


def test_the_winner_s_recorded_name_has_a_single_owner(monkeypatch):
    """``_SEARCH_WINNER_FIELDS`` names the produced -> recorded mapping
    once, so the record's WRITER and both of the summary's READERS must
    take the names from it. Rename it and everything follows; a reader
    that re-spelled ``winner`` would report every fold as winner-less
    and print agreement where the folds disagreed."""
    from dskit.pipeline import driver

    monkeypatch.setattr(
        driver,
        "_SEARCH_WINNER_FIELDS",
        (("best_params", "winner_params"), ("best_score", "winner_score")),
    )
    meta = driver._search_record(SimpleNamespace(calls=2), {"best_params": {"a": 1}})
    assert meta == {"trials_executed": 2, "winner_params": {"a": 1}}
    assert driver._winner_identity(meta) == (True, '{"a": 1}')
    assert driver._winner_cell(meta) == '`{"a": 1}`'
    dropped = driver._search_record(SimpleNamespace(calls=1), {"best_params": {1, 2}})
    assert dropped == {"trials_executed": 1, "winner_dropped": ["best_params"]}
    assert driver._winner_identity(dropped) == (True, None)
    assert driver._winner_cell(dropped) == "dropped (not JSON-legal)"


def test_an_hpo_free_summary_is_byte_identical(tmp_path):
    """The hard invariant: a walk-forward document with no search node
    must produce exactly the summary it produced before ADR-0043 — no
    empty ``search`` key on a fold row, none in the aggregate, and no
    section in the report. Spelled out here independently of the
    formatter, so an unconditional emission cannot pass."""
    import statistics

    from dskit.pipeline.driver import _cutoff_ms

    doc = probe_doc(tmp_path, wf_spec())
    result = run_walk_forward(doc, asof=ASOF)
    summary = read_json(result.summary_dir, "walkforward.json")
    assert set(summary) == {
        "name",
        "asof",
        "document_hash",
        "objective",
        "select",
        "state",
        "folds",
        "aggregate",
    }
    assert [sorted(f) for f in summary["folds"]] == [
        ["cutoff", "run_dir", "score", "state"]
    ] * 2
    assert sorted(summary["aggregate"]) == [
        "best_cutoff",
        "best_score",
        "max",
        "mean",
        "min",
        "n_folds",
        "n_scored",
        "std",
    ]
    cutoffs = ["2025-01-01", "2025-02-01"]
    scores = [float(_cutoff_ms(c) - 1) for c in cutoffs]
    expected = [
        "**WALK-FORWARD RAN** — 2/2 fold(s) scored on `$probe.score`",
        "",
        f"- document hash: `{doc.hash[:16]}…`",
        f"- mean {statistics.fmean(scores):.6g} · "
        f"std {statistics.pstdev(scores):.6g} · "
        f"best (min) {min(scores):.6g} at {cutoffs[0]}",
        "",
        "| fold cutoff | state | score | run |",
        "|---|---|---|---|",
    ] + [
        f"| {fold['cutoff']} | ran | {fold['score']:.6g} | "
        f"`{os.path.basename(fold['run_dir'])}` |"
        for fold in summary["folds"]
    ]
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        assert fh.read() == "\n".join(expected) + "\n"


def test_fold_splits_stamp_train_start_when_train_days_is_set():
    cuts = _fold_splits(wf_spec(train_days=30, embargo_days=3), "2025-06-01")
    assert cuts.train_start_ms == cuts.train_end_ms - 30 * DAY + 1
    assert _fold_splits(wf_spec(), "2025-06-01").train_start_ms is None


def test_weighted_mean_uses_recency_and_is_omitted_when_unset():
    folds = [
        {"cutoff": "2025-01-01", "score": 4.0, "state": "ran", "run_dir": ""},
        {"cutoff": "2025-04-01", "score": 2.0, "state": "ran", "run_dir": ""},
    ]
    plain = aggregate_folds(folds, "min")
    assert "weighted_mean" not in plain
    weighted = aggregate_folds(folds, "min", weight_halflife_folds=1)
    assert weighted["weighted_mean"] < plain["mean"]
    assert weighted["weighted_mean"] == pytest.approx(
        (0.5 * 4.0 + 1.0 * 2.0) / 1.5
    )


# -- ADR-0093: the fold-row shape has one owner ---------------------------------


def test_the_fold_row_shape_has_one_owner():
    from dskit.pipeline import driver

    assert driver.FOLD_FIELDS == ("cutoff", "run_dir", "state", "score")
    assert driver.FOLD_OPTIONAL_FIELDS == ("search", "error")
    for name in (
        "FOLD_FIELDS",
        "FOLD_OPTIONAL_FIELDS",
        "aggregate_folds",
        "write_walkforward_summary",
    ):
        assert name in driver.__all__, name
    assert not hasattr(driver, "_aggregate_folds")
    assert not hasattr(driver, "_write_walkforward_summary")


def _row(**overrides):
    row = {"cutoff": "2025-01-01", "run_dir": "", "state": "ran", "score": 1.0}
    row.update(overrides)
    return row


def test_aggregate_folds_refuses_a_row_missing_a_required_key():
    row = _row()
    del row["score"]
    with pytest.raises(ValueError, match="score"):
        aggregate_folds([row], "max")


def test_aggregate_folds_refuses_a_key_outside_the_union():
    # Default-deny pins the optional half too: a per-fold memory reading
    # is exactly the key ADR-0093 removes from the record.
    with pytest.raises(ValueError, match="peak_rss_bytes"):
        aggregate_folds([_row(peak_rss_bytes=5)], "max")


def test_aggregate_folds_accepts_the_optional_keys():
    folds = [
        _row(search={"tune": {"trials_executed": 1, "winner": {"a": 1}}}),
        _row(cutoff="2025-02-01", state="error", score=None, error="boom"),
    ]
    out = aggregate_folds(folds, "max")
    assert out["n_folds"] == 2
    assert out["n_scored"] == 1


def test_the_rows_the_driver_writes_are_the_rows_the_readers_read(tmp_path):
    """The round trip ADR-0093 pins: a written summary read back through
    ``walk_fold_dirs``, ``single_fold_row`` and ``aggregate_folds`` is the
    summary the driver wrote."""
    from dskit.pipeline.driver import FOLD_FIELDS
    from dskit.pipeline.runs import single_fold_row, walk_fold_dirs

    doc = probe_doc(tmp_path, wf_spec(folds=["2025-01-01"]))
    result = run_walk_forward(doc, asof=ASOF)
    summary = read_json(result.summary_dir, "walkforward.json")
    assert [sorted(f) for f in summary["folds"]] == [sorted(FOLD_FIELDS)]
    assert walk_fold_dirs(result.summary_dir) == [summary["folds"][0]["run_dir"]]
    assert single_fold_row(result.summary_dir, "2025-01-01") == summary["folds"][0]
    assert aggregate_folds(summary["folds"], summary["select"]) == summary["aggregate"]
