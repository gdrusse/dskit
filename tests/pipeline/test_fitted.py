"""The fitted-transform family (ADR-0040 part 6): the sibling of the
pure-transform family, for a node that LEARNS state from a declared
split and applies it elsewhere.

Leakage is the whole subject. A transform fitted on validation rows
leaks invisibly — nothing fails, the scores just come out better — so
what is tested hardest here is which rows the fit SAW, that the
declaration is refused at plan when it cannot be honoured, and that the
purity screen catches an ``apply_state`` that recomputes a statistic
over the rows it was handed.

Fixture data only; the standardizing scaler is the family's first member
and stands in for every later one.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from dskit.pipeline import DEFAULT_NODE_KINDS
from dskit.pipeline.base import (
    ConfigError,
    OutputsConfig,
    RandomSplitConfig,
    TimeSplitConfig,
)
from dskit.pipeline.document import NodeSpec, PipelineDocument, WalkForwardSpec
from dskit.pipeline.driver import run_document
from dskit.pipeline.fitted import (
    SIDECAR_NAME,
    ApplyTransform,
    FittedTransform,
    Standardize,
    _assigns_by_cluster,
)
from dskit.pipeline.node import NodeContext, resolve_uses
from dskit.pipeline.planner import plan
from dskit.pipeline.records import MarketRecord

DAY = 24 * 60 * 60 * 1000
ASOF = "2026-01-01"


def rows(n, *, start=0, value=1.0, split_day=1):
    """``n`` rows inside one split day, ``value`` climbing by one."""
    return [
        {
            "contract": f"C-{start + i}",
            "asof_ms": split_day * DAY + i,
            "x": value + i,
            "keep": "yes",
        }
        for i in range(n)
    ]


#: Train rows sit on day 1, val rows on day 15 — and the two series are
#: deliberately far apart, so a scaler that saw the val rows produces
#: visibly different numbers.
TRAIN_ROWS = rows(4, value=1.0, split_day=1)
VAL_ROWS = rows(4, start=100, value=1000.0, split_day=15)


@pytest.fixture
def split_ctx(tmp_path):
    splits = TimeSplitConfig(
        train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY
    )
    return NodeContext(
        name="fitted",
        asof=ASOF,
        run_dir=str(tmp_path),
        splits=splits,
        splits_info=splits.to_obj(),
    )


def _document(params, *, splits=None, mode=None, artifact="", run_root="",
              walkforward=None):
    return PipelineDocument(
        name="fitted-doc",
        pipeline={
            "dataset": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 8, "n_instruments": 1, "seed": 3},
            ),
            "scaler": NodeSpec(
                uses="standardize",
                inputs={"rows": "$dataset.events"},
                params=params,
                mode=mode,
                artifact=artifact,
            ),
        },
        splits=splits,
        walkforward=walkforward,
        outputs=OutputsConfig(run_root=run_root),
    )


TIME_SPLITS = TimeSplitConfig(
    train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY
)


class TestTheFamilyShape:
    def test_the_base_is_abstract_and_refused_at_resolve(self):
        with pytest.raises(ValueError, match="abstract"):
            resolve_uses("dskit.pipeline.fitted:FittedTransform")

    def test_the_two_kinds_are_registered(self):
        assert "standardize" in DEFAULT_NODE_KINDS
        assert "apply-transform" in DEFAULT_NODE_KINDS

    def test_the_role_and_contracts_are_declared(self):
        assert Standardize.role == "fitted_transform"
        assert Standardize.outputs == ("transform", "rows", "metrics")
        assert ApplyTransform.role == "transform"
        assert ApplyTransform.outputs == ("rows",)

    def test_the_family_inherits_the_mode_dispatch_it_does_not_write(self):
        """ADR-0038's bar, structurally: neither template method is
        overridden, so no member of this family owns an opinion about
        ``mode``."""
        from dskit.pipeline.node import TrainableNode

        assert issubclass(FittedTransform, TrainableNode)
        for cls in (FittedTransform, Standardize):
            assert cls.run is TrainableNode.run
            assert cls.validate_inputs is TrainableNode.validate_inputs

    def test_unknown_knobs_are_refused_by_name(self):
        assert any(
            "bogus" in p
            for p in Standardize.validate_params(
                {"fit_split": "train", "features": ["x"], "bogus": 1}
            )
        )

    def test_features_are_required_and_checked(self):
        assert any("features" in p
                   for p in Standardize.validate_params({"fit_split": "train"}))
        for bad in ([], "x", [""], [1]):
            assert any(
                "features" in p
                for p in Standardize.validate_params(
                    {"fit_split": "train", "features": bad}
                )
            ), bad


class TestLeakageIsRefused:
    def test_a_train_fit_never_sees_the_val_rows(self, split_ctx):
        """THE leak: the state must be a function of the fit split alone.

        The val rows are three orders of magnitude away, so a scaler
        that saw them cannot possibly answer the train-only numbers.
        """
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        out = node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        state = out["transform"].state
        assert state["mean"]["x"] == pytest.approx(2.5)  # 1,2,3,4
        assert out["metrics"]["n_fit_rows"] == len(TRAIN_ROWS)

    def test_every_row_is_emitted_transformed_not_just_the_fit_split(
        self, split_ctx
    ):
        """``fit_split`` governs what is LEARNED, never what is emitted —
        a scaler that emitted only its fit slice would silently truncate
        the stream its downstream reads."""
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        out = node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        assert len(out["rows"]) == len(TRAIN_ROWS) + len(VAL_ROWS)
        # The val rows are transformed BY THE TRAIN STATE — that is the
        # required behaviour; the leak would be fitting on them.
        state = out["transform"].state
        expect = (VAL_ROWS[0]["x"] - state["mean"]["x"]) / state["std"]["x"]
        assert out["rows"][len(TRAIN_ROWS)]["x"] == pytest.approx(expect)
        assert out["rows"][0]["keep"] == "yes"  # untouched columns ride

    def test_a_declared_fit_split_with_no_splits_refuses_at_plan(self):
        with pytest.raises(ConfigError, match="declares none"):
            plan(_document({"fit_split": "train", "features": ["x"]}))

    def test_a_walk_forward_document_needs_no_splits_section(self):
        """Folds REPLACE the splits section wholesale, so a walk-forward
        document carries none BY DESIGN — the same exemption the
        document grammar already grants a ``split`` param. Without it
        this family could never appear in a rolling-origin study at all,
        while the reason given ("would fit on EVERYTHING") is untrue:
        every fold materializes its own cuts before anything is fit.
        """
        document = _document(
            {"fit_split": "train", "features": ["x"]},
            walkforward=WalkForwardSpec(
                objective="$scaler.metrics.n_rows", val_days=21,
                first="1973-01-01", step_days=7, count=2,
            ),
        )
        assert plan(document).role_of("scaler") == "fitted_transform"

    def test_an_undeclared_fit_split_refuses_at_plan_under_train_mode(self):
        with pytest.raises(ConfigError, match="fit_split"):
            plan(_document({"features": ["x"]}, splits=TIME_SPLITS))

    def test_a_fit_split_that_is_not_a_split_name_refuses_at_plan(self):
        with pytest.raises(ConfigError, match="fit_split"):
            plan(_document({"fit_split": "holdout", "features": ["x"]},
                           splits=TIME_SPLITS))

    def test_a_declared_split_plans_clean(self):
        resolved = plan(_document({"fit_split": "train", "features": ["x"]},
                                  splits=TIME_SPLITS))
        assert resolved.role_of("scaler") == "fitted_transform"

    def test_fitting_with_no_materialized_splits_refuses_at_run(self, tmp_path):
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        ctx = NodeContext(name="f", asof=ASOF, run_dir=str(tmp_path))
        with pytest.raises(ValueError, match="fit_split"):
            node.run(ctx, {"rows": TRAIN_ROWS})

    def test_a_fit_split_that_matches_no_row_refuses_rather_than_fitting_on_none(
        self, split_ctx
    ):
        node = Standardize("scaler", {"fit_split": "test", "features": ["x"]})
        with pytest.raises(ValueError, match="no row"):
            node.run(split_ctx, {"rows": TRAIN_ROWS})

    def test_identity_less_rows_under_a_cluster_keyed_split_refuse(
        self, tmp_path
    ):
        """THE silent leak: a random cut assigns by hashing the row's
        CLUSTER, so rows carrying none all hash the same string and land
        in ONE bucket. When that bucket is ``fit_split`` the scaler fits
        on the entire stream — val and test included — and reports it as
        an ordinary fit, with the right metrics and no refusal anywhere.

        The row shape is the one the README's own example wires: a
        window node's output, keyed on ``symbol``/``asof_ms``.
        """
        rows = [{"symbol": "AAPL", "asof_ms": i, "x": float(i)}
                for i in range(100)]
        ctx = NodeContext(
            name="f", asof=ASOF, run_dir=str(tmp_path),
            splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1),
        )
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        with pytest.raises(ValueError, match="no usable split identity"):
            node.run(ctx, {"rows": rows})

    def test_identity_less_rows_are_fine_when_the_split_cuts_ON_TIME(
        self, split_ctx
    ):
        """The refusal is about the split that READS an identity, not
        about identity — a time cut reads the instant, so the window
        rows the README wires must keep planning and running."""
        rows = [{"symbol": "AAPL", "asof_ms": DAY + i, "x": float(i)}
                for i in range(4)]
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            split_ctx, {"rows": rows}
        )
        assert out["metrics"]["n_fit_rows"] == 4

    def test_the_split_instant_is_read_from_the_declared_order_field(
        self, split_ctx
    ):
        """ADR-0040's other half exists so a foreign-vocabulary stream
        can enter. A fitted transform that read a hardcoded ``asof_ms``
        would put every such row in NO split and then blame the split
        bounds — a refusal naming the wrong cause sends the operator to
        the wrong file.
        """
        rows = [{"contract": f"C-{i}", "t": DAY + i, "x": float(1 + i)}
                for i in range(4)]
        rows += [{"contract": f"V-{i}", "t": 15 * DAY + i, "x": 1000.0 + i}
                 for i in range(4)]
        out = Standardize("s", {"fit_split": "train", "features": ["x"],
                                "order_field": "t"}).run(
            split_ctx, {"rows": rows}
        )
        assert out["metrics"]["n_fit_rows"] == 4
        assert out["transform"].state["mean"]["x"] == pytest.approx(2.5)

    def test_a_carried_cluster_satisfies_a_random_cut(self, tmp_path):
        """And the same rows WITH an identity assign per cluster, which
        is what a random cut promised in the first place."""
        rows = [{"cluster": f"day-{i}", "asof_ms": i, "x": float(i)}
                for i in range(100)]
        ctx = NodeContext(
            name="f", asof=ASOF, run_dir=str(tmp_path),
            splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1),
        )
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            ctx, {"rows": rows}
        )
        assert 0 < out["metrics"]["n_fit_rows"] < len(rows)

    def test_the_cluster_a_TOOLKIT_row_actually_carries_is_the_one_read(self):
        """The vocabulary the toolkit EMITS, not the one a fixture picks.

        No dskit node emits a row key spelled ``cluster`` — that name is
        a *property* on the envelope. ``ArrayFeatures`` carries the
        cluster under ``records.CLUSTER_FIELD`` (``group``), so a
        ``frame_of`` reading only ``cluster`` falls through to
        ``contract``, which is PER ROW: every event straddles the fit
        boundary and the scaler fits on the very clusters that supply
        the val rows. Four events x three contracts, each event wholly
        one cluster — the fit must see whole events or none.
        """
        node = Standardize("s", {"fit_split": "train", "features": ["y"]})
        splits = RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1)
        seen = {}
        for event in range(4):
            for contract in range(3):
                row = {"instrument": f"I-{contract}",
                       "contract": f"C-{contract}",
                       "group": f"event-{event}",
                       "asof_ms": 1_000 * event, "y": 1.0}
                assert node.frame_of(row).cluster == f"event-{event}"
                seen.setdefault(f"event-{event}", set()).add(
                    splits.split_of(node.frame_of(row))
                )
        assert all(len(s) == 1 for s in seen.values()), seen

    def test_an_envelope_still_assigns_by_the_property_it_publishes(self):
        """And the envelope's own answer is unmoved: ``MarketRecord``
        publishes ``cluster`` as a property doing the group-or-contract
        fallback, and that is what a record stream must still be cut by.
        """
        node = Standardize("s", {"fit_split": "train", "features": ["y"]})
        envelope = dict(instrument="I", contract="C", venue="V", asof_ms=5,
                        usable=True, reason="ok")
        grouped = MarketRecord(**envelope, group="EV-1")
        assert node.frame_of(grouped).cluster == grouped.cluster == "EV-1"
        bare = MarketRecord(**envelope)
        assert node.frame_of(bare).cluster == bare.cluster == "C"

    def test_an_EMPTY_identity_is_refused_and_not_hashed_as_one(
        self, tmp_path
    ):
        """``""`` hashes to the same ``f"{seed}:"`` for every row exactly
        as ``None`` does, so a check testing ``is not None`` lets the
        whole-stream leak through with ordinary-looking metrics. The bar
        is a USABLE identity — the envelope's own ``cluster_ok`` — not a
        present one. Empty identity columns are ordinary in
        CSV/table-sourced streams.
        """
        rows = [{"contract": "", "asof_ms": i, "x": float(i)}
                for i in range(100)]
        ctx = NodeContext(
            name="f", asof=ASOF, run_dir=str(tmp_path),
            splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1),
        )
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        with pytest.raises(ValueError, match="no usable split identity"):
            node.run(ctx, {"rows": rows})

    def test_an_unusable_identity_is_refused_by_the_ENVELOPE_rule(
        self, tmp_path
    ):
        """An integer cluster id is not one the envelope can hold
        (``cluster_ok`` — a non-empty string), and the pack already
        lands such a value as ABSENT on the rows it carries. The refusal
        must therefore fire, and must name the real cause: the old
        message said the row carried no identity at all, which sent the
        operator looking for a field that was right there.
        """
        rows = [{"cluster": i % 3, "asof_ms": i, "x": float(i)}
                for i in range(100)]
        ctx = NodeContext(
            name="f", asof=ASOF, run_dir=str(tmp_path),
            splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1),
        )
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        with pytest.raises(ValueError, match="non-empty string"):
            node.run(ctx, {"rows": rows})


class TestWhichSplitsReadAnIdentity:
    """``_assigns_by_cluster`` decides when the identity refusal bites,
    and it names one fact the split configs own ("the random family
    hashes the cluster"). The pin DERIVES the answer from the configs
    themselves rather than restating it, so the day a config changes
    what it reads, the tuple is caught rather than the leak."""

    @staticmethod
    def _really_reads_a_cluster(config):
        """Derived: does this config's answer move with the cluster
        alone? A config that RAISES on an unknown one read it too."""
        answers = set()
        for i in range(32):
            frame = SimpleNamespace(asof_ms=5 * DAY, cluster=f"probe-{i}")
            try:
                answers.add(config.split_of(frame))
            except Exception:
                return True
        return len(answers) > 1

    @pytest.mark.parametrize("config", [
        TIME_SPLITS,
        TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY,
                        test_end_ms=30 * DAY, policy="event-close"),
        RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1),
    ], ids=["time-record", "time-event-close", "random"])
    def test_the_predicate_agrees_with_what_the_config_actually_does(
        self, config
    ):
        assert _assigns_by_cluster(config) is self._really_reads_a_cluster(
            config
        )


class TestThePurityScreen:
    def test_a_state_recomputed_over_the_handed_rows_is_caught(self, split_ctx):
        """The family's classic leak: ``apply_state`` that looks at the
        rows it was given instead of only at the state."""

        class _Peeker(Standardize):
            def apply_state(self, state, rows, params):
                shift = sum(row["x"] for row in rows) / max(len(rows), 1)
                return [{**row, "x": row["x"] - shift} for row in rows]

        node = _Peeker("peek", {"fit_split": "train", "features": ["x"]})
        with pytest.raises(ValueError, match="row-independent"):
            node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

    def test_the_screen_is_a_decision_the_document_owns(self, split_ctx):
        class _Peeker(Standardize):
            def apply_state(self, state, rows, params):
                shift = sum(row["x"] for row in rows) / max(len(rows), 1)
                return [{**row, "x": row["x"] - shift} for row in rows]

        node = _Peeker("peek", {"fit_split": "train", "features": ["x"],
                                "purity_check": False})
        assert len(node.run(split_ctx, {"rows": TRAIN_ROWS})["rows"]) == 4

    def test_a_member_emitting_nan_is_not_mistaken_for_row_dependence(
        self, split_ctx
    ):
        """NaN-as-absent is this repo's own convention, and the screen
        must not read it as drift.

        ``float('nan') != float('nan')``, so a plain ``==`` over the
        transformed rows refuses a member that marks an unusable value
        the way ``libs/numpy.py`` marks a warm-up one — pure and
        row-independent by construction, refused anyway, with a message
        naming the wrong cause and no escape but turning the family's one
        mechanical check off.
        """

        class _Nanner(FittedTransform):
            def fit(self, rows, params):
                return {"k": 1.0}

            def apply_state(self, state, rows, params):
                return [{**row, "y": float("nan")} for row in rows]

        out = _Nanner("nan", {"fit_split": "train"}).run(
            split_ctx, {"rows": TRAIN_ROWS}
        )
        assert len(out["rows"]) == len(TRAIN_ROWS)
        assert all(row["y"] != row["y"] for row in out["rows"])

    def test_row_dependence_is_still_caught_through_a_nan_column(
        self, split_ctx
    ):
        """The nan-equal path widens what compares equal, never what
        passes: a member that reads the rows it was handed is refused
        even while it also emits a NaN."""

        class _NanPeeker(FittedTransform):
            def fit(self, rows, params):
                return {"k": 1.0}

            def apply_state(self, state, rows, params):
                shift = sum(row["x"] for row in rows) / max(len(rows), 1)
                return [{**row, "x": row["x"] - shift, "y": float("nan")}
                        for row in rows]

        node = _NanPeeker("peek", {"fit_split": "train"})
        with pytest.raises(ValueError, match="row-independent"):
            node.run(split_ctx, {"rows": TRAIN_ROWS})

    def test_a_row_count_that_moves_is_refused_even_unscreened(self, split_ctx):
        class _Dropper(Standardize):
            def apply_state(self, state, rows, params):
                return list(rows)[:1]

        node = _Dropper("drop", {"fit_split": "train", "features": ["x"],
                                 "purity_check": False})
        with pytest.raises(ValueError, match="one row out per row in"):
            node.run(split_ctx, {"rows": TRAIN_ROWS})


class TestLoadMode:
    def _fit(self, ctx):
        node = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        out = node.run(ctx, {"rows": TRAIN_ROWS + VAL_ROWS})
        return node, out, os.path.join(node.artifact_dir(ctx), SIDECAR_NAME)

    def test_a_restored_state_is_the_fitted_one_and_nothing_refits(
        self, split_ctx
    ):
        _fitted, trained, sidecar = self._fit(split_ctx)

        served = Standardize("scaler", {"features": ["x"]},
                             mode="load", artifact=sidecar)
        # No splits at all in the serving context: a node that refitted
        # would have nothing to fit on and would refuse.
        bare = NodeContext(name="f", asof=ASOF, run_dir=split_ctx.run_dir)
        out = served.run(bare, {"rows": VAL_ROWS})

        assert out["transform"].state == trained["transform"].state
        assert out["metrics"]["n_fit_rows"] == 0
        assert out["rows"][0]["x"] == pytest.approx(trained["rows"][4]["x"])

    def test_the_artifact_directory_is_accepted_too(self, split_ctx):
        _fitted, _trained, sidecar = self._fit(split_ctx)
        served = Standardize("scaler", {"features": ["x"]},
                             mode="load", artifact=os.path.dirname(sidecar))
        bare = NodeContext(name="f", asof=ASOF, run_dir=split_ctx.run_dir)
        assert served.run(bare, {"rows": VAL_ROWS})["rows"]

    def test_a_fit_split_that_contradicts_the_sidecar_refuses(self, split_ctx):
        _fitted, _trained, sidecar = self._fit(split_ctx)
        served = Standardize("scaler", {"features": ["x"], "fit_split": "val"},
                             mode="load", artifact=sidecar)
        bare = NodeContext(name="f", asof=ASOF, run_dir=split_ctx.run_dir)
        with pytest.raises(ValueError, match="fit_split"):
            served.run(bare, {"rows": VAL_ROWS})

    def test_a_sidecar_from_another_class_refuses(self, split_ctx, tmp_path):
        _fitted, _trained, sidecar = self._fit(split_ctx)
        payload = json.loads(open(sidecar, encoding="utf-8").read())
        payload["node_class"] = "somepkg.other:Scaler"
        forged = tmp_path / "forged.json"
        forged.write_text(json.dumps(payload), encoding="utf-8")

        served = Standardize("scaler", {"features": ["x"]},
                             mode="load", artifact=str(forged))
        bare = NodeContext(name="f", asof=ASOF, run_dir=split_ctx.run_dir)
        with pytest.raises(ValueError, match="node_class"):
            served.run(bare, {"rows": VAL_ROWS})

    def test_load_without_a_pin_refuses_by_name(self, split_ctx):
        served = Standardize("scaler", {"features": ["x"]})
        served.mode = "load"
        with pytest.raises(ValueError, match="artifact"):
            served.run(split_ctx, {"rows": VAL_ROWS})

    def test_load_does_not_need_a_fit_split_at_plan(self, tmp_path):
        resolved = plan(_document({"features": ["x"]}, splits=TIME_SPLITS,
                                  mode="load", artifact=str(tmp_path / "s.json")))
        assert resolved.role_of("scaler") == "fitted_transform"

    def test_a_serving_document_may_restate_fit_split_with_no_splits(
        self, tmp_path
    ):
        """The serving shape: no splits section, a restated ``fit_split``.

        A document that never fits declares no splits — that IS what a
        serving document looks like — and the no-splits refusal exists
        to stop a FIT from seeing everything. Refusing here would put
        the sidecar cross-check ("may restate, never misdescribe") out
        of reach of exactly the documents it was written for.
        """
        resolved = plan(_document({"features": ["x"], "fit_split": "train"},
                                  mode="load",
                                  artifact=str(tmp_path / "s.json")))
        assert resolved.role_of("scaler") == "fitted_transform"

    def test_the_restored_state_must_cover_the_features_the_document_names(
        self, split_ctx
    ):
        """A serving document may restate what a state is, never
        misdescribe it — the same rule ``node_class`` and ``fit_split``
        already carry, on the knob that says WHAT was scaled.

        Unchecked, a retuned or typo'd feature list feeds the model an
        unscaled column forever: ``apply_state`` iterates the STATE, so
        the declared name is simply never read and nothing differs.
        """
        _fitted, _trained, sidecar = self._fit(split_ctx)
        served = Standardize("scaler", {"features": ["other"]},
                             mode="load", artifact=sidecar)
        bare = NodeContext(name="f", asof=ASOF, run_dir=split_ctx.run_dir)
        with pytest.raises(ValueError, match="features"):
            served.run(bare, {"rows": VAL_ROWS})


class TestApplyTransform:
    def test_a_second_stream_rides_through_the_wired_carrier(self, split_ctx):
        fitted = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        carrier = fitted.run(split_ctx, {"rows": TRAIN_ROWS})["transform"]

        out = ApplyTransform("apply", {}).run(
            split_ctx, {"transform": carrier, "rows": VAL_ROWS}
        )
        expect = (VAL_ROWS[0]["x"] - carrier.state["mean"]["x"]) / \
            carrier.state["std"]["x"]
        assert out["rows"][0]["x"] == pytest.approx(expect)
        assert len(out["rows"]) == len(VAL_ROWS)

    def test_a_missing_carrier_is_refused_by_name(self):
        problems = ApplyTransform("apply", {}).validate_inputs(
            {"rows": VAL_ROWS, "transform": object()}
        )
        assert any("transform" in p for p in problems), problems

    def test_the_members_own_row_rule_reaches_the_SECOND_stream(
        self, split_ctx
    ):
        """The kind exists to project a second stream, so the member's
        own input rule must hold on it.

        ``Standardize`` refuses a non-mapping row by index when it runs
        itself; through this kind the same stream used to validate clean
        and die at execute with a bare ``TypeError`` out of ``dict(row)``
        — the validate-clean-die-at-execute shape ADR-0040 names.
        """
        fitted = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        carrier = fitted.run(split_ctx, {"rows": TRAIN_ROWS})["transform"]
        strangers = [SimpleNamespace(contract="D-0", asof_ms=DAY, x=1.0)]

        problems = ApplyTransform("apply", {}).validate_inputs(
            {"transform": carrier, "rows": strangers}
        )
        assert any("rows[0]" in p and "Standardize" in p for p in problems), \
            problems

    def test_a_second_stream_the_member_accepts_still_validates_clean(
        self, split_ctx
    ):
        fitted = Standardize("scaler", {"fit_split": "train", "features": ["x"]})
        carrier = fitted.run(split_ctx, {"rows": TRAIN_ROWS})["transform"]
        assert ApplyTransform("apply", {}).validate_inputs(
            {"transform": carrier, "rows": VAL_ROWS}
        ) == []


class TestTheScalerItself:
    def test_a_zero_variance_column_does_not_divide_by_zero(self, split_ctx):
        flat = [{"contract": f"F-{i}", "asof_ms": DAY + i, "x": 7.0}
                for i in range(4)]
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            split_ctx, {"rows": flat}
        )
        assert out["transform"].state["std"]["x"] == 1.0
        assert [row["x"] for row in out["rows"]] == [0.0] * 4

    def test_a_row_missing_the_feature_keeps_its_absence(self, split_ctx):
        mixed = TRAIN_ROWS + [{"contract": "N-0", "asof_ms": DAY + 9, "x": None}]
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            split_ctx, {"rows": mixed}
        )
        assert out["rows"][-1]["x"] is None
        # It was IN the fit split — it just carried nothing to learn from.
        assert out["metrics"]["n_fit_rows"] == len(TRAIN_ROWS) + 1
        assert out["transform"].state["mean"]["x"] == pytest.approx(2.5)

    def test_a_feature_no_fit_row_carries_refuses_by_name(self, split_ctx):
        """A typo is an error, not a silent identity transform.

        Learning mean 0 / std 1 for a name nothing carries makes a
        typo'd feature a clean, successful, WRONG run: the real column
        rides through unscaled, ``n_features`` counts the phantom, and
        the load-mode cross-check agrees with itself because declared
        and covered are the same wrong name. This is the fit-time twin
        of the numpy half's all-unlifted refusal.
        """
        rows = [{"contract": f"C-{i}", "asof_ms": DAY + i, "ret_lag_0": float(i)}
                for i in range(4)]
        node = Standardize("s", {"fit_split": "train",
                                 "features": ["ret_lag_00"]})
        with pytest.raises(ValueError, match="ret_lag_00"):
            node.run(split_ctx, {"rows": rows})

    def test_a_feature_some_fit_row_carries_is_learned_as_before(
        self, split_ctx
    ):
        """The refusal is about a feature with NO usable value in the
        whole fit split; per-row absence is a different case and keeps
        its documented policy."""
        mixed = TRAIN_ROWS + [{"contract": "N-0", "asof_ms": DAY + 9, "x": None}]
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            split_ctx, {"rows": mixed}
        )
        assert out["transform"].state["mean"]["x"] == pytest.approx(2.5)

    def test_the_state_is_json_able_by_construction(self, split_ctx):
        out = Standardize("s", {"fit_split": "train", "features": ["x"]}).run(
            split_ctx, {"rows": TRAIN_ROWS}
        )
        assert json.loads(json.dumps(out["transform"].state))


class TestThroughTheDriver:
    def test_a_document_with_a_fitted_transform_runs(self, tmp_path):
        # SynthEvents lays one event per day from day 1000; the cut puts
        # four in train and four beyond it.
        splits = TimeSplitConfig(
            train_end_ms=1004 * DAY, val_end_ms=1006 * DAY,
            test_end_ms=1010 * DAY,
        )
        document = _document(
            {"fit_split": "train", "features": ["mid"]},
            splits=splits,
            run_root=str(tmp_path / "runs"),
        )
        result = run_document(document, asof=ASOF)
        assert result.state == "ran" and result.exit_code == 0, result.error
        metrics = result.outputs["scaler"]["metrics"]
        assert metrics["n_rows"] == 8 and metrics["n_fit_rows"] == 5
        # The state landed in the run dir, where a serving load reads it.
        assert os.path.exists(
            os.path.join(result.run_dir, "artifacts", "scaler", SIDECAR_NAME)
        )
