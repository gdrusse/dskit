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

import pytest

from dskit.pipeline import DEFAULT_NODE_KINDS
from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.fitted import (
    SIDECAR_NAME,
    ApplyTransform,
    FittedTransform,
    Standardize,
)
from dskit.pipeline.node import NodeContext, resolve_uses
from dskit.pipeline.planner import plan

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


def _document(params, *, splits=None, mode=None, artifact="", run_root=""):
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
