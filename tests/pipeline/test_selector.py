"""The feature-selection seam (ADR-0042): one member of the fitted family.

Leakage is the subject, exactly as it is for the family's first member: a
selector that sees validation rows leaks invisibly — nothing fails, the
scores just come out better — so the fixtures below make the leak VISIBLE
by putting the val rows' ranking upside down relative to the train rows'.
A fit that saw both selects a different column set, and every test that
matters here reads that set.

The member used throughout is a toy ``TopMeans`` defined in this file: the
seam's whole extension contract is ONE hook returning the surviving names,
so a test member is three lines and the base owns everything a real pack
member would inherit — the split selection, the projection, the sidecar,
the metrics and the ``features`` output.
"""

from __future__ import annotations

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.fitted import (
    SIDECAR_NAME,
    ApplyTransform,
    FeatureSelector,
    FittedTransform,
)
from dskit.pipeline.node import NodeContext, TrainableNode, resolve_uses
from dskit.pipeline.planner import plan

DAY = 24 * 60 * 60 * 1000
ASOF = "2026-01-01"

#: The candidate columns every document below declares, in the order it
#: declares them — the order the surviving list is canonicalized to.
CANDIDATES = ["a", "b", "c"]


def _row(i, *, day, a, b, c):
    return {
        "contract": f"C-{i}",
        "asof_ms": day * DAY + i,
        "a": a,
        "b": b,
        "c": c,
        "label": 0.5,
    }


#: Train rows rank a > b > c; val rows rank c far above both. A top-2 fit
#: on train answers ['a', 'b'], and a fit that ALSO saw the val rows
#: answers ['a', 'c'] — so the leak is readable off the selected list.
TRAIN_ROWS = [_row(i, day=1, a=10.0, b=5.0, c=1.0) for i in range(4)]
VAL_ROWS = [_row(100 + i, day=15, a=0.0, b=0.0, c=1000.0) for i in range(4)]

TIME_SPLITS = TimeSplitConfig(
    train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY
)


class TopMeans(FeatureSelector):
    """Keep the ``n`` candidates with the largest mean — a toy rule.

    Records every fit it was handed in :attr:`fits`, which is how the
    leakage tests read WHICH rows the hook saw rather than inferring it
    from the answer alone.
    """

    _PARAMS = FeatureSelector._PARAMS + ("n",)

    fits = None

    def surviving_features(self, rows, params):
        if self.fits is None:
            self.fits = []
        self.fits.append(list(rows))
        means = {
            name: sum(row[name] for row in rows) / len(rows)
            for name in self.features()
        }
        ranked = sorted(means, key=lambda name: (-means[name], name))
        return ranked[: params["n"]]


class Answers(FeatureSelector):
    """Answers a canned list — the base's contract checks are the subject."""

    _PARAMS = FeatureSelector._PARAMS + ("answer",)

    def surviving_features(self, rows, params):
        return params["answer"]


@pytest.fixture
def split_ctx(tmp_path):
    return NodeContext(
        name="selector",
        asof=ASOF,
        run_dir=str(tmp_path),
        splits=TIME_SPLITS,
        splits_info=TIME_SPLITS.to_obj(),
    )


def _node(cls=TopMeans, key="select", mode=None, artifact="", **params):
    """One selector node; a ``None`` param means "do not declare it"."""
    declared = {"fit_split": "train", "features": list(CANDIDATES), "n": 2}
    declared.update(params)
    return cls(
        key,
        {k: v for k, v in declared.items() if v is not None},
        mode=mode,
        artifact=artifact,
    )


class TestTheSeamShape:
    def test_the_base_is_abstract_and_refused_at_resolve(self):
        """A selector with no rule must not construct, let alone run."""
        with pytest.raises(ValueError, match="abstract"):
            resolve_uses("dskit.pipeline.fitted:FeatureSelector")

    def test_it_is_a_member_of_the_fitted_family_not_a_second_seam(self):
        assert issubclass(FeatureSelector, FittedTransform)
        assert FeatureSelector.role == "fitted_transform"

    def test_the_features_output_joins_the_family_three(self):
        assert FeatureSelector.outputs == (
            "transform",
            "rows",
            "metrics",
            "features",
        )

    def test_no_member_writes_the_mode_dispatch_or_the_two_family_hooks(self):
        """ADR-0038's bar plus ADR-0042's: ONE hook is the contract.

        Neither template method is overridden (so ``mode`` is handled in
        exactly one place), and ``fit``/``apply_state`` belong to the
        selector base — a member that had to write either would be a
        second seam wearing the family's name.
        """
        for cls in (FeatureSelector, TopMeans, Answers):
            assert cls.run is TrainableNode.run
            assert cls.validate_inputs is TrainableNode.validate_inputs
            assert cls.fit is FeatureSelector.fit
            assert cls.apply_state is FeatureSelector.apply_state

    def test_the_base_knobs_are_inherited_not_restated(self):
        """The leakage knobs come from the family, in the family's order."""
        head = FeatureSelector._PARAMS[: len(FittedTransform._PARAMS)]
        assert head == FittedTransform._PARAMS
        assert "features" in FeatureSelector._PARAMS

    def test_unknown_knobs_are_refused_by_name(self):
        assert any(
            "bogus" in problem
            for problem in TopMeans.validate_params(
                {"fit_split": "train", "features": ["a"], "n": 1, "bogus": 1}
            )
        )

    def test_candidates_are_required_and_checked(self):
        assert any(
            "features" in problem
            for problem in Answers.validate_params({"fit_split": "train"})
        )
        for bad in ([], "a", [""], [1], ["a", "a"]):
            assert any(
                "features" in problem
                for problem in Answers.validate_params(
                    {"fit_split": "train", "features": bad}
                )
            ), bad


class TestLeakageIsRefused:
    def test_the_hook_sees_the_fit_split_and_nothing_else(self, split_ctx):
        """THE leak, read off the rows the hook was handed.

        The val rows rank ``c`` a thousand times above everything, so a
        hook that saw them cannot answer the train-only pair.
        """
        node = _node()
        out = node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        assert [dict(row) for row in node.fits[0]] == TRAIN_ROWS
        assert out["features"] == ["a", "b"]
        assert out["metrics"]["n_fit_rows"] == len(TRAIN_ROWS)

    def test_wired_rows_is_the_fit_split_not_the_uncut_port(self, split_ctx):
        """``wired("rows")`` must not bypass the cut (C6 leakage)."""

        class ReadsWiredRows(FeatureSelector):
            _PARAMS = FeatureSelector._PARAMS
            seen = None

            def surviving_features(self, rows, params):
                wired = self.wired("rows")
                self.seen = [dict(row) for row in wired]
                return ["a", "b"]

        node = _node(cls=ReadsWiredRows, n=None)
        node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})
        assert node.seen == TRAIN_ROWS
        assert all(row["contract"] != "C-100" for row in node.seen)

    def test_a_val_fit_split_selects_the_val_answer(self, split_ctx):
        """The knob is read, not assumed: declaring ``val`` fits on val.

        Without this the leakage test above could pass on a base that
        ignored ``fit_split`` and happened to fit on the first rows.
        """
        node = _node(fit_split="val")
        out = node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        assert [dict(row) for row in node.fits[0]] == VAL_ROWS
        assert out["features"] == ["a", "c"]

    def test_a_document_with_no_splits_is_refused_at_plan(self):
        """Inherited, not restated: the planner's family rule fires.

        No score node here — a ``split`` param demands a splits section
        of its own, and that refusal would mask the one under test.
        """
        with pytest.raises(ConfigError, match="fit_split"):
            plan(_document({"fit_split": "train", "features": CANDIDATES,
                            "n": 2}, score=False))

    def test_an_undeclared_fit_split_is_refused_at_plan(self):
        with pytest.raises(ConfigError, match="must declare which split"):
            plan(_document({"features": CANDIDATES, "n": 2}, splits=TIME_SPLITS))

    def test_a_members_own_knob_is_searchable_so_flow_2_plans(self):
        """ADR-0044: a member knob changes what the rule DECIDES.

        ``select.n`` is not ``fit_split`` / ``purity_check`` / ``order_field``.
        A space over it is owner flow 2 and must plan.
        """
        pipeline = _pipeline({"fit_split": "train", "features": CANDIDATES,
                              "n": 2})
        pipeline["search"] = NodeSpec(
            uses="hpo-grid",
            params={
                "space": {"select.n": [1, 2]},
                "objective": "$score.metrics.loss",
                "select": "min",
            },
        )
        the_plan = plan(
            PipelineDocument(
                name="selector-doc", pipeline=pipeline, splits=TIME_SPLITS
            )
        )
        assert the_plan.role_of("select") == "fitted_transform"
        assert the_plan.role_of("search") == "search"

    def test_a_base_knob_on_a_fitted_transform_is_still_refused(self):
        """The ADR-0040 rationale, now keyed on FittedTransform._PARAMS.

        ``fit_split.x`` is refused too: the head param is the leakage knob.
        """
        from dskit.pipeline.fitted import FittedTransform

        pipeline = _pipeline({"fit_split": "train", "features": CANDIDATES,
                              "n": 2})
        pipeline["search"] = NodeSpec(
            uses="hpo-grid",
            params={
                "space": {"select.fit_split": ["train", "val"]},
                "objective": "$score.metrics.loss",
                "select": "min",
            },
        )
        with pytest.raises(ConfigError, match="may not address 'select.fit_split'"):
            plan(
                PipelineDocument(
                    name="selector-doc", pipeline=pipeline, splits=TIME_SPLITS
                )
            )
        pipeline["search"].params["space"] = {"select.fit_split.x": ["train"]}
        with pytest.raises(ConfigError, match="may not address 'select.fit_split"):
            plan(
                PipelineDocument(
                    name="selector-doc", pipeline=pipeline, splits=TIME_SPLITS
                )
            )
        assert FittedTransform._PARAMS == (
            "fit_split", "order_field", "purity_check"
        )


class TestTheProjection:
    def test_the_rejected_candidates_are_dropped_and_the_rest_ride_along(
        self, split_ctx
    ):
        """A row is projected to the SURVIVING candidates plus everything
        that was never a candidate — the label and the split identity are
        not features, and a row stripped to its features could neither be
        trained on nor cut."""
        out = _node().run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        assert sorted(out["rows"][0]) == ["a", "asof_ms", "b", "contract", "label"]
        assert out["rows"][0]["a"] == TRAIN_ROWS[0]["a"]

    def test_every_row_is_emitted_not_just_the_fit_split(self, split_ctx):
        out = _node().run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})

        assert len(out["rows"]) == len(TRAIN_ROWS) + len(VAL_ROWS)
        assert all("c" not in row for row in out["rows"])

    def test_the_surviving_list_is_in_the_declared_candidate_order(
        self, split_ctx
    ):
        """Canonical, not the rule's ranking: two candidates that tie on
        importance would otherwise order by whatever the library returned,
        and the artifact a serving run restores must be reproducible."""
        node = _node(cls=Answers, answer=["c", "a"], n=None)
        out = node.run(split_ctx, {"rows": TRAIN_ROWS})

        assert out["features"] == ["a", "c"]
        assert out["transform"].state["features"] == ["a", "c"]

    def test_the_carrier_projects_a_second_stream_identically(self, split_ctx):
        """The family's apply kind serves this member for free."""
        carrier = _node().run(split_ctx, {"rows": TRAIN_ROWS})["transform"]
        served = ApplyTransform("serve", {}).run(
            split_ctx, {"transform": carrier, "rows": VAL_ROWS}
        )

        assert sorted(served["rows"][0]) == [
            "a", "asof_ms", "b", "contract", "label"
        ]

    def test_metrics_count_the_candidates_and_the_survivors(self, split_ctx):
        metrics = _node().run(split_ctx, {"rows": TRAIN_ROWS})["metrics"]

        assert metrics["n_candidates"] == len(CANDIDATES)
        assert metrics["n_selected"] == 2


class TestTheContractOnTheHook:
    @pytest.mark.parametrize(
        "answer, expected",
        [
            (["a", "zzz"], "not a candidate"),
            ([], "selected nothing"),
            ("a", "list of candidate"),
            ([1], "list of candidate"),
        ],
    )
    def test_a_broken_answer_is_refused_by_name(
        self, split_ctx, answer, expected
    ):
        node = _node(cls=Answers, answer=answer, n=None)
        with pytest.raises(ValueError, match=expected):
            node.run(split_ctx, {"rows": TRAIN_ROWS})

    def test_a_non_mapping_row_is_refused_before_anything_runs(self):
        node = _node()
        assert any(
            "mapping" in problem
            for problem in node.validate_common_inputs({"rows": [object()]})
        )

    def test_a_candidate_no_row_carries_is_refused_by_name(self):
        node = _node(features=[*CANDIDATES, "typo"])
        problems = node.validate_common_inputs({"rows": TRAIN_ROWS})
        assert any("typo" in problem for problem in problems)


class TestTheArtifactIsTheColumns:
    def test_the_state_is_written_to_the_sidecar(self, split_ctx):
        node = _node()
        node.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})
        with open(
            os.path.join(node.artifact_dir(split_ctx), SIDECAR_NAME),
            encoding="utf-8",
        ) as fh:
            payload = json.load(fh)

        assert payload["state"]["features"] == ["a", "b"]
        assert payload["state"]["candidates"] == CANDIDATES
        assert payload["fit_split"] == "train"

    def test_serving_restores_the_identical_columns_and_never_fits(
        self, split_ctx
    ):
        """The whole point of persisting the list: a serving run consumes
        the columns TRAINING chose, in training's order, and the rule that
        chose them is never consulted again.

        The serving node is the SAME class (the sidecar records which one
        fitted, and a foreign class is already refused), so "never
        consulted" is read off the rule's own record of every fit it saw.
        """
        fitted = _node()
        fitted.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})
        sidecar = os.path.join(fitted.artifact_dir(split_ctx), SIDECAR_NAME)

        serving = _node(mode="load", artifact=sidecar, fit_split=None, n=None)
        out = serving.run(split_ctx, {"rows": VAL_ROWS})

        assert serving.fits is None
        assert out["features"] == ["a", "b"]
        assert out["metrics"]["n_fit_rows"] == 0
        assert all("c" not in row for row in out["rows"])

    @pytest.mark.parametrize(
        "restated", [["a", "b"], ["a", "b", "c", "d"], ["b", "a", "c"]]
    )
    def test_a_serving_document_that_restates_other_candidates_refuses(
        self, split_ctx, restated
    ):
        """A document may restate what a state is, never misdescribe it.

        ``apply_state`` drops the candidates the STATE records, so a
        serving node naming a different candidate SET would project a
        different column set while every document claimed the trained
        one — the train/serve skew this family exists to make impossible.

        The third case is a REORDERING, same set: refused too, because
        the candidate order is what canonicalizes the surviving list, so
        two documents claiming one state would disagree about the feature
        vector's order the moment either re-fits.
        """
        fitted = _node()
        fitted.run(split_ctx, {"rows": TRAIN_ROWS + VAL_ROWS})
        sidecar = os.path.join(fitted.artifact_dir(split_ctx), SIDECAR_NAME)

        serving = _node(mode="load", artifact=sidecar, fit_split=None, n=None,
                        features=restated)
        with pytest.raises(ValueError, match="candidates"):
            serving.run(split_ctx, {"rows": VAL_ROWS})


class TestTheWiredSeam:
    """A rule that needs more than rows asks for a wired port BY NAME."""

    class NeedsAPort(FeatureSelector):
        def surviving_features(self, rows, params):
            return list(self.wired("ranking"))

    def test_a_member_reads_a_wired_port_through_the_seam(self, split_ctx):
        node = self.NeedsAPort(
            "select", {"fit_split": "train", "features": list(CANDIDATES)}
        )
        out = node.run(
            split_ctx, {"rows": TRAIN_ROWS, "ranking": ["b", "a"]}
        )

        assert out["features"] == ["a", "b"]

    def test_an_unwired_port_refuses_by_name(self, split_ctx):
        node = self.NeedsAPort(
            "select", {"fit_split": "train", "features": list(CANDIDATES)}
        )
        with pytest.raises(ValueError, match="ranking"):
            node.run(split_ctx, {"rows": TRAIN_ROWS})


# ---------------------------------------------------------------------------
# Document helpers — the plan-time rules need a whole document
# ---------------------------------------------------------------------------


def _pipeline(params, *, score=True):
    """The selector between a data node and a val-split score node.

    Every kind is named by IMPORT PATH (the ``test_fitted.py`` idiom), so
    these documents plan against the default registry with nothing
    registered for them. ``score=False`` drops the score node: its
    ``split`` param demands a splits section on its own, which would mask
    the selector's own no-splits refusal.
    """
    pipeline = {
        "events": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthEvents",
            params={"n_events": 8, "n_instruments": 1, "seed": 3},
        ),
        "select": NodeSpec(
            uses="tests.pipeline.test_selector:TopMeans",
            inputs={"rows": "$events.events"},
            params=params,
        ),
    }
    if score:
        pipeline["score"] = NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthScore",
            inputs={"events": "$select.rows"},
            params={"split": "val"},
        )
    return pipeline


def _document(params, *, splits=None, score=True):
    return PipelineDocument(
        name="selector-doc",
        pipeline=_pipeline(params, score=score),
        splits=splits,
        outputs=OutputsConfig(run_root=""),
    )
