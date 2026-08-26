"""The toolkit-owned kinds: the real ``stat_test`` and ``validate``."""

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_stats import (
    MIN_CLUSTERS,
    StatTest,
    Validate,
    _belief,
    _field,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.planner import plan
from dskit.pipeline.records import MarketRecord
from dskit.pipeline.synthetic_nodes import (
    SynthEvents,
    SynthLabels,
    SynthMarketSignal,
    SynthTrain,
)
from tests.pipeline.dochelpers import BANKING_SPLITS, DAY

ASOF = "2026-01-01"


def _decision(out):
    """The stat test's DECISION fields only.

    ``evidence`` (I-232) is reporting, not decision: pinning it inside a
    whole-dict equality would make every future reporting field a test
    break, and would let a reader mistake the ledger for the verdict. The
    decision is still asserted exactly — just on its own three keys.
    """
    return {k: v for k, v in out.items() if k != "evidence"}


@pytest.fixture
def ctx(tmp_path):
    """No splits section — everything is in-sample."""
    return NodeContext(name="kinds", asof=ASOF, run_dir=str(tmp_path))


@pytest.fixture
def split_ctx(tmp_path):
    splits = TimeSplitConfig(
        train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY
    )
    return NodeContext(
        name="kinds",
        asof=ASOF,
        run_dir=str(tmp_path),
        splits=splits,
        splits_info=splits.to_obj(),
    )


def mrec(contract, asof_ms, group=None):
    """A MarketRecord — the attribute-bearing record shape."""
    return MarketRecord(
        venue="test",
        instrument=contract.split("-")[0],
        contract=contract,
        asof_ms=asof_ms,
        usable=True,
        reason="ok",
        group=group,
    )


def drec(contract, asof_ms, cluster):
    """A plain dict — the key-bearing record shape."""
    return {
        "instrument": contract.split("-")[0],
        "contract": contract,
        "cluster": cluster,
        "asof_ms": asof_ms,
    }


def scoring_case():
    """Four hand-checkable records: half MarketRecord, half dict, two
    clusters. Brier: model losses .04/.09/.16/.16 (mean .1125), baseline
    .25 each; improvements c0 (.21+.16)/2=.185, c1 (.09+.09)/2=.09."""
    records = [
        mrec("AAA-1", 15 * DAY, group="AAA:c0"),
        mrec("AAA-2", 15 * DAY, group="AAA:c0"),
        drec("AAA-3", 15 * DAY, cluster="AAA:c1"),
        drec("AAA-4", 15 * DAY, cluster="AAA:c1"),
    ]
    outcomes = {"AAA-1": True, "AAA-2": False, "AAA-3": True, "AAA-4": False}
    signal = {"AAA-1": 0.8, "AAA-2": 0.3, "AAA-3": 0.6, "AAA-4": 0.4}
    baseline = {c: 0.5 for c in signal}
    return records, outcomes, signal, baseline


# ---------------------------------------------------------------------------
# StatTest
# ---------------------------------------------------------------------------


class TestStatTestParams:
    def test_defaults_are_valid(self):
        assert StatTest.validate_params({}) == []

    def test_explicit_good_params(self):
        params = {"alpha": 0.01, "n_boot": 1000, "seed": 3, "correction": "none"}
        assert StatTest.validate_params(params) == []

    def test_alpha_one_refused_matching_stats_bound(self):
        # stats._check_pvalues enforces (0, 1) — the kind matches it, so a
        # bad alpha fails at PLAN, never mid-run inside the correction
        # (integration ruling on the K1 boundary-mismatch flag).
        problems = StatTest.validate_params({"alpha": 1.0})
        assert len(problems) == 1 and "alpha" in problems[0]

    @pytest.mark.parametrize("alpha", [0, 0.0, -0.1, 1.5, True, "0.05", None])
    def test_bad_alpha(self, alpha):
        problems = StatTest.validate_params({"alpha": alpha})
        assert len(problems) == 1 and "alpha" in problems[0]

    @pytest.mark.parametrize("n_boot", [999, 0, -5, 2000.0, True, "many"])
    def test_bad_n_boot(self, n_boot):
        problems = StatTest.validate_params({"n_boot": n_boot})
        assert len(problems) == 1 and "n_boot must be an int >= 1000" in problems[0]

    @pytest.mark.parametrize("seed", [-1, 0.5, True, "7"])
    def test_bad_seed(self, seed):
        problems = StatTest.validate_params({"seed": seed})
        assert len(problems) == 1 and "seed" in problems[0]

    def test_unknown_correction_names_the_known_ones(self):
        (problem,) = StatTest.validate_params({"correction": "sidak"})
        for known in ("bh", "bonferroni", "none"):
            assert known in problem
        assert "sidak" in problem

    def test_problems_accumulate_never_raise(self):
        problems = StatTest.validate_params(
            {
                "alpha": 2.0,
                "n_boot": 1,
                "seed": -1,
                "correction": "holm",
                "method": "percentile",
            }
        )
        assert len(problems) == 5

    def test_construction_refuses_bad_params(self):
        with pytest.raises(ConfigError, match="n_boot"):
            StatTest("edge", {"n_boot": 10})

    @pytest.mark.parametrize("method", ["plain", "studentized"])
    def test_good_methods(self, method):
        assert StatTest.validate_params({"method": method}) == []

    @pytest.mark.parametrize("method", ["percentile", True, None, "", 3])
    def test_bad_method_names_the_known_ones(self, method):
        (problem,) = StatTest.validate_params({"method": method})
        assert "method" in problem
        for known in ("plain", "studentized"):
            assert known in problem


class TestStatTestInputs:
    def good(self):
        return {"scores": {"AAA": {"c0": 0.1, "c1": -0.2}, "BBB": {"c0": 0.3}}}

    def test_good_shape_passes(self):
        assert StatTest("t").validate_inputs(self.good()) == []

    @pytest.mark.parametrize("scores", [None, [], "scores", 3])
    def test_scores_must_be_a_dict(self, scores):
        problems = StatTest("t").validate_inputs({"scores": scores})
        assert len(problems) == 1 and "scores must be a dict" in problems[0]

    def test_missing_scores_port_is_a_problem_not_a_raise(self):
        assert StatTest("t").validate_inputs({}) != []

    def test_instrument_value_must_be_a_cluster_dict(self):
        problems = StatTest("t").validate_inputs({"scores": {"AAA": [0.1]}})
        assert len(problems) == 1 and "scores['AAA']" in problems[0]

    def test_non_string_instrument_key(self):
        problems = StatTest("t").validate_inputs({"scores": {7: {"c0": 0.1}}})
        assert len(problems) == 1 and "instrument keys" in problems[0]

    def test_non_string_cluster_key(self):
        problems = StatTest("t").validate_inputs({"scores": {"AAA": {3: 0.1}}})
        assert any("cluster keys" in p for p in problems)

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf"), True, "0.1", None]
    )
    def test_non_finite_or_non_numeric_values_refused(self, value):
        # A NaN would ride the bootstrap as maximal significance — refused.
        problems = StatTest("t").validate_inputs({"scores": {"AAA": {"c0": value}}})
        assert any("finite number" in p for p in problems)

    def test_problems_accumulate_across_instruments(self):
        problems = StatTest("t").validate_inputs(
            {"scores": {"AAA": [0.1], "BBB": {"c0": float("nan")}}}
        )
        assert len(problems) == 2


class TestStatTestWeightsInput:
    """The weights-port rules: demanded by a needs_weights correction,
    refused otherwise, shaped when present, and covering EVERY instrument
    (degraded ones enter the family at p = 1.0, so they weigh too)."""

    SCORES = {"AAA": {"c0": 0.1, "c1": -0.2}, "BBB": {"c0": 0.3}}

    def node(self, **params):
        return StatTest("t", {"correction": "weighted-bh", **params})

    def test_weighted_correction_without_weights_refuses(self):
        problems = self.node().validate_inputs({"scores": dict(self.SCORES)})
        assert any("wire a weights input" in p for p in problems)

    def test_unweighted_correction_with_weights_refuses(self):
        problems = StatTest("t").validate_inputs(
            {"scores": dict(self.SCORES), "weights": {"AAA": 1.0, "BBB": 1.0}}
        )
        assert any("does not use weights" in p for p in problems)

    def test_good_weights_pass(self):
        problems = self.node().validate_inputs(
            {"scores": dict(self.SCORES), "weights": {"AAA": 2.0, "BBB": 0.5}}
        )
        assert problems == []

    @pytest.mark.parametrize("w", [0.0, -1.0, True, float("nan"), "2", None])
    def test_bad_weight_values_refuse(self, w):
        problems = self.node().validate_inputs(
            {"scores": dict(self.SCORES), "weights": {"AAA": w, "BBB": 1.0}}
        )
        assert any("finite number > 0" in p for p in problems)

    def test_weights_must_be_a_dict(self):
        problems = self.node().validate_inputs(
            {"scores": dict(self.SCORES), "weights": [1.0, 2.0]}
        )
        assert any("weights must be a dict" in p for p in problems)

    def test_every_instrument_needs_a_weight_including_degraded(self):
        scores = {"AAA": {"c0": 0.1, "c1": 0.2}, "TINY": {"c0": 0.5}}
        problems = self.node().validate_inputs(
            {"scores": scores, "weights": {"AAA": 1.0}}
        )
        assert any("no weight for instrument 'TINY'" in p for p in problems)

    def test_extra_weight_keys_are_fine(self):
        problems = self.node().validate_inputs(
            {
                "scores": dict(self.SCORES),
                "weights": {"AAA": 1.0, "BBB": 1.0, "unrelated": 3.0},
            }
        )
        assert problems == []


class TestStatTestRun:
    def test_empty_scores_is_nogo_not_a_crash(self, ctx):
        out = StatTest("t").run(ctx, {"scores": {}})
        assert _decision(out) == {
            "survivors": [],
            "pvalues": {},
            "verdict": "NO-GO",
        }
        assert out["evidence"]["totals"]["family_size"] == 0

    def test_all_positive_clusters_hit_the_addone_floor_exactly(self, ctx):
        scores = {"AAA": {f"c{i}": 1.0 for i in range(8)}}
        out = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": scores})
        # Every resample mean > 0: p = (1 + 0) / (n_boot + 1), exactly.
        assert out["pvalues"]["AAA"] == 1 / 1001
        assert out["survivors"] == ["AAA"] and out["verdict"] == "GO"

    def test_all_zero_clusters_give_p_one_nogo(self, ctx):
        scores = {"AAA": {f"c{i}": 0.0 for i in range(6)}}
        out = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": scores})
        assert _decision(out) == {
            "survivors": [],
            "pvalues": {"AAA": 1.0},
            "verdict": "NO-GO",
        }

    def test_all_negative_clusters_give_p_one(self, ctx):
        scores = {"AAA": {f"c{i}": -0.5 for i in range(6)}}
        out = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": scores})
        assert out["pvalues"]["AAA"] == 1.0

    def test_per_instrument_decisions_localize_the_edge(self, ctx):
        # One real edge, one null: the family correction keeps each
        # instrument's own reject/accept — never an aggregate.
        scores = {
            "EDGE": {f"c{i}": 1.0 for i in range(8)},
            "NULL": {f"c{i}": 0.0 for i in range(8)},
        }
        out = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": scores})
        assert out["survivors"] == ["EDGE"] and out["verdict"] == "GO"
        assert out["pvalues"]["NULL"] == 1.0
        assert set(out["pvalues"]) == {"EDGE", "NULL"}

    @pytest.mark.parametrize("clusters", [{}, {"c0": 5.0}])
    def test_below_min_clusters_degrades_to_p_one_not_a_crash(self, ctx, clusters):
        assert len(clusters) < MIN_CLUSTERS
        out = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": {"AAA": clusters}})
        # One lucky cluster must NOT masquerade as maximal significance.
        assert out["pvalues"]["AAA"] == 1.0
        assert _decision(out) == {
            "survivors": [],
            "pvalues": {"AAA": 1.0},
            "verdict": "NO-GO",
        }
        # The degradation must be SAID, not merely scored as p=1.0.
        row = out["evidence"]["instruments"]["AAA"]
        assert row["tested"] is False and "MIN_CLUSTERS" in row["reason"]

    def test_correction_dispatch_changes_the_family_decision(self, ctx):
        # Exact p-values: EDGE = 1/1001, NULL = 1.0. At alpha = 0.0015 the
        # uncorrected test rejects EDGE (p < alpha) but bonferroni
        # (bound = alpha/2 = 0.00075 < p) and BH (same first threshold)
        # both refuse — dispatch is observable, deterministically.
        scores = {
            "EDGE": {f"c{i}": 1.0 for i in range(8)},
            "NULL": {f"c{i}": 0.0 for i in range(8)},
        }
        results = {
            corr: StatTest(
                "t", {"alpha": 0.0015, "n_boot": 1000, "correction": corr}
            ).run(ctx, {"scores": scores})
            for corr in ("none", "bonferroni", "bh")
        }
        assert results["none"]["survivors"] == ["EDGE"]
        assert results["bonferroni"]["survivors"] == []
        assert results["bh"]["survivors"] == []
        assert results["none"]["verdict"] == "GO"
        assert results["bh"]["verdict"] == "NO-GO"

    def test_same_seed_same_result_different_seed_can_differ(self, ctx):
        # Mixed signs make the bootstrap p genuinely resampling-dependent.
        scores = {"AAA": {"c0": 1.0, "c1": 1.0, "c2": 1.0, "c3": 1.0, "c4": -0.5}}

        def run(seed):
            return StatTest("t", {"n_boot": 1000, "seed": seed}).run(
                ctx, {"scores": scores}
            )

        assert run(0) == run(0)
        p0, p1 = run(0)["pvalues"]["AAA"], run(1)["pvalues"]["AAA"]
        assert p0 != p1  # pinned seeds, sha256 streams — deterministic forever
        assert 0 < p0 < 0.05 and 0 < p1 < 0.05

    def test_outputs_match_the_declared_contract(self, ctx):
        node = StatTest("t", {"n_boot": 1000})
        out = node.run(ctx, {"scores": {"AAA": {"c0": 1.0, "c1": 1.0}}})
        assert node.validate_outputs(out) == []

    def test_method_plain_is_default_and_identical(self, ctx):
        # Full-output equality, evidence included: an undeclared method IS
        # the plain method, byte-for-byte.
        scores = {
            "EDGE": {f"c{i}": 1.0 + 0.1 * i for i in range(8)},
            "MIXED": {"c0": 1.0, "c1": -1.0, "c2": 0.4},
        }
        base = StatTest("t", {"n_boot": 1000}).run(ctx, {"scores": scores})
        explicit = StatTest("t", {"n_boot": 1000, "method": "plain"}).run(
            ctx, {"scores": scores}
        )
        assert base == explicit

    def test_studentized_oracle_and_degradation(self, ctx):
        # Identical positives are the signed-degenerate case: exact add-one
        # floor. Identical zeros: exact 1.0. Below MIN_CLUSTERS degrades
        # exactly as the plain method does.
        scores = {
            "EDGE": {f"c{i}": 1.0 for i in range(8)},
            "NULL": {f"c{i}": 0.0 for i in range(8)},
            "TINY": {"c0": 5.0},
        }
        out = StatTest("t", {"n_boot": 1000, "method": "studentized"}).run(
            ctx, {"scores": scores}
        )
        assert out["pvalues"]["EDGE"] == 1 / 1001
        assert out["pvalues"]["NULL"] == 1.0
        assert out["pvalues"]["TINY"] == 1.0
        assert out["survivors"] == ["EDGE"] and out["verdict"] == "GO"
        row = out["evidence"]["instruments"]["TINY"]
        assert row["tested"] is False and "MIN_CLUSTERS" in row["reason"]

    @pytest.mark.parametrize("method", ["plain", "studentized"])
    def test_evidence_self_describes_the_test(self, ctx, method):
        # TODO-1 / ADR-0033: the stage says what ran; the report renders
        # these instead of a fallback description.
        out = StatTest("t", {"n_boot": 1000, "method": method, "seed": 3}).run(
            ctx, {"scores": {"AAA": {f"c{i}": 0.5 + 0.1 * i for i in range(6)}}}
        )
        totals = out["evidence"]["totals"]
        assert totals["method"] == method
        assert totals["n_boot"] == 1000
        assert totals["seed"] == 3
        assert totals["independence_unit"] == "cluster (the record's dependence group)"
        if method == "plain":
            assert totals["test"] == (
                "one-sided cluster bootstrap on the paired improvement "
                "(H0: mean improvement <= 0)"
            )
        else:
            assert "studentized recentered cluster bootstrap-t" in totals["test"]
        assert "statistic" in totals

    def test_studentized_rows_carry_se_and_ci(self, ctx):
        out = StatTest("t", {"n_boot": 1000, "method": "studentized"}).run(
            ctx,
            {
                "scores": {
                    "AAA": {f"c{i}": 0.5 + 0.05 * (i % 7) for i in range(40)},
                    "FLAT": {f"c{i}": 0.5 for i in range(6)},
                }
            },
        )
        row = out["evidence"]["instruments"]["AAA"]
        assert row["ci_low"] <= row["mean_improvement"] <= row["ci_high"]
        assert row["se"] > 0 and row["t"] > 0
        # Degenerate bounds are OMITTED, never null (allow_nan JSON safety
        # + the report's disclaimer probe keys off presence).
        flat = out["evidence"]["instruments"]["FLAT"]
        assert flat["se"] == 0.0
        assert "ci_low" not in flat and "ci_high" not in flat and "t" not in flat
        # Plain rows carry none of the studentized columns.
        plain = StatTest("t", {"n_boot": 1000}).run(
            ctx, {"scores": {"AAA": {"c0": 1.0, "c1": 1.0}}}
        )
        assert "se" not in plain["evidence"]["instruments"]["AAA"]

    def test_weighted_bh_end_to_end_flip(self, ctx):
        # Exact p-values: EDGE = 1/1001, NULL = 1.0. With a tiny weight on
        # EDGE, its q = p/w blows past the BH threshold; with unit weights
        # it survives — the weights change the family decision, nothing
        # else.
        scores = {
            "EDGE": {f"c{i}": 1.0 for i in range(8)},
            "NULL": {f"c{i}": 0.0 for i in range(8)},
        }
        node = StatTest("t", {"n_boot": 1000, "correction": "weighted-bh"})
        up = node.run(
            ctx, {"scores": scores, "weights": {"EDGE": 1.0, "NULL": 1.0}}
        )
        down = node.run(
            ctx, {"scores": scores, "weights": {"EDGE": 0.01, "NULL": 1.0}}
        )
        assert up["survivors"] == ["EDGE"] and up["verdict"] == "GO"
        assert down["survivors"] == [] and down["verdict"] == "NO-GO"
        assert up["pvalues"] == down["pvalues"] == {"EDGE": 1 / 1001, "NULL": 1.0}


class TestStatTestProperties:
    SHAPES = {
        "all_positive": {f"c{i}": 0.5 + 0.1 * i for i in range(6)},
        "all_negative": {f"c{i}": -0.5 - 0.1 * i for i in range(6)},
        "all_zero": {f"c{i}": 0.0 for i in range(6)},
        "mixed": {"c0": 1.0, "c1": -1.0, "c2": 0.4, "c3": -0.2, "c4": 0.1},
        "single": {"c0": 2.0},
        "empty": {},
    }

    @pytest.mark.parametrize("method", ["plain", "studentized"])
    @pytest.mark.parametrize("correction", ["bh", "bonferroni", "none"])
    @pytest.mark.parametrize("seed", [0, 1])
    def test_invariants_hold_across_shapes_seeds_corrections(
        self, ctx, correction, seed, method
    ):
        params = {
            "n_boot": 1000,
            "seed": seed,
            "correction": correction,
            "method": method,
        }
        out = StatTest("t", params).run(ctx, {"scores": dict(self.SHAPES)})
        assert set(out["pvalues"]) == set(self.SHAPES)
        assert all(0 < p <= 1 for p in out["pvalues"].values())
        assert out["survivors"] == sorted(out["survivors"])
        assert set(out["survivors"]) <= set(self.SHAPES)
        assert (out["verdict"] == "GO") == bool(out["survivors"])
        # Degenerate instruments degrade honestly; extremes are exact
        # where the method makes them exact.
        assert out["pvalues"]["single"] == 1.0
        assert out["pvalues"]["empty"] == 1.0
        assert out["pvalues"]["all_zero"] == 1.0
        if method == "plain":
            # every resample mean is <= 0 / > 0 respectively — exact
            assert out["pvalues"]["all_negative"] == 1.0
            assert out["pvalues"]["all_positive"] == 1 / 1001
        else:
            # varied values: the studentized pivot resamples for real
            assert out["pvalues"]["all_negative"] >= 0.5
            assert out["pvalues"]["all_positive"] <= 0.05


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestValidateParams:
    def test_split_is_required_and_constrained(self):
        (problem,) = Validate.validate_params({})
        assert "split" in problem
        assert Validate.validate_params({"split": "holdout"}) != []
        for split in ("train", "val", "test"):
            assert Validate.validate_params({"split": split}) == []

    def test_unknown_metric_names_the_known_ones(self):
        (problem,) = Validate.validate_params({"split": "val", "metric": "auc"})
        assert "brier" in problem and "logloss" in problem and "auc" in problem

    @pytest.mark.parametrize("min_events", [0, -3, 2.5, True])
    def test_bad_min_events(self, min_events):
        problems = Validate.validate_params({"split": "val", "min_events": min_events})
        assert len(problems) == 1 and "min_events" in problems[0]

    def test_problems_accumulate_never_raise(self):
        problems = Validate.validate_params({"metric": "auc", "min_events": 0})
        assert len(problems) == 3


class TestValidateInputs:
    def good(self):
        records, outcomes, signal, baseline = scoring_case()
        return {
            "records": records,
            "signal": signal,
            "outcomes": outcomes,
            "baseline": baseline,
        }

    def node(self):
        return Validate("val", {"split": "val"})

    def test_good_shape_passes(self):
        assert self.node().validate_inputs(self.good()) == []

    def test_baseline_is_optional(self):
        inputs = self.good()
        del inputs["baseline"]
        assert self.node().validate_inputs(inputs) == []

    @pytest.mark.parametrize("records", [None, 3, "records", {"a": 1}, b"x"])
    def test_records_must_be_a_non_mapping_iterable(self, records):
        inputs = self.good()
        inputs["records"] = records
        problems = self.node().validate_inputs(inputs)
        assert len(problems) == 1 and "records" in problems[0]

    @pytest.mark.parametrize("bad", [None, 3, [0.5], object()])
    def test_signal_must_be_mapping_or_predictor(self, bad):
        inputs = self.good()
        inputs["signal"] = bad
        problems = self.node().validate_inputs(inputs)
        assert len(problems) == 1 and "signal" in problems[0]

    def test_baseline_when_present_is_checked_like_signal(self):
        inputs = self.good()
        inputs["baseline"] = 7
        problems = self.node().validate_inputs(inputs)
        assert len(problems) == 1 and "baseline" in problems[0]

    def test_predict_object_is_accepted_for_both(self):
        class Model:
            def predict(self, record):
                return 0.5

        inputs = self.good()
        inputs["signal"], inputs["baseline"] = Model(), Model()
        assert self.node().validate_inputs(inputs) == []

    def test_outcomes_must_be_a_dict(self):
        inputs = self.good()
        inputs["outcomes"] = [("AAA-1", True)]
        problems = self.node().validate_inputs(inputs)
        assert len(problems) == 1 and "outcomes" in problems[0]

    def test_everything_wrong_accumulates(self):
        problems = self.node().validate_inputs({})
        assert len(problems) == 3  # records, signal, outcomes (baseline optional)


class TestValidateRun:
    def test_hand_checked_brier_scores_and_cluster_improvements(self, ctx):
        records, outcomes, signal, baseline = scoring_case()
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx,
            {
                "records": records,
                "signal": signal,
                "outcomes": outcomes,
                "baseline": baseline,
            },
        )
        assert out["metrics"]["n"] == 4
        assert out["metrics"]["loss"] == pytest.approx(0.1125)
        assert out["metrics"]["baseline_loss"] == pytest.approx(0.25)
        assert out["metrics"]["beats_baseline"] is True
        assert out["cluster_scores"] == {
            "AAA": {
                "AAA:c0": pytest.approx(0.185),
                "AAA:c1": pytest.approx(0.09),
            }
        }

    def test_absolute_mode_without_baseline(self, ctx):
        # The plain-ML case (docs/24 §5): no beats-gate, no cluster scores.
        records, outcomes, signal, _ = scoring_case()
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx, {"records": records, "signal": signal, "outcomes": outcomes}
        )
        assert set(out["metrics"]) == {"loss", "n"}
        assert out["metrics"]["loss"] == pytest.approx(0.1125)
        assert out["cluster_scores"] == {}

    def test_only_the_declared_split_is_scored(self, split_ctx):
        outcomes = {"AAA-1": True, "AAA-2": True, "AAA-3": True}
        signal = {"AAA-1": 0.9, "AAA-2": 0.9, "AAA-3": 0.9}
        records = [
            mrec("AAA-1", 5 * DAY, group="AAA:c0"),  # train
            drec("AAA-2", 15 * DAY, cluster="AAA:c1"),  # val
            mrec("AAA-3", 25 * DAY, group="AAA:c2"),  # test
        ]
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            split_ctx, {"records": records, "signal": signal, "outcomes": outcomes}
        )
        assert out["metrics"]["n"] == 1

    def test_no_splits_section_means_everything_in_sample(self, ctx):
        records, outcomes, signal, _ = scoring_case()
        out = Validate("val", {"split": "test", "metric": "brier"}).run(
            ctx, {"records": records, "signal": signal, "outcomes": outcomes}
        )
        assert out["metrics"]["n"] == len(records)

    def test_unknown_and_unsettled_outcomes_are_skipped(self, ctx):
        records, outcomes, signal, _ = scoring_case()
        outcomes = dict(outcomes)
        del outcomes["AAA-1"]  # unknown
        outcomes["AAA-2"] = None  # unsettled — not an error
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx, {"records": records, "signal": signal, "outcomes": outcomes}
        )
        assert out["metrics"]["n"] == 2

    def test_records_without_signal_coverage_are_skipped(self, ctx):
        records, outcomes, signal, baseline = scoring_case()
        signal = dict(signal)
        del signal["AAA-3"]
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx,
            {
                "records": records,
                "signal": signal,
                "outcomes": outcomes,
                "baseline": baseline,
            },
        )
        assert out["metrics"]["n"] == 3

    def test_paired_scoring_needs_baseline_coverage_too(self, ctx):
        records, outcomes, signal, baseline = scoring_case()
        baseline = dict(baseline)
        del baseline["AAA-4"]
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx,
            {
                "records": records,
                "signal": signal,
                "outcomes": outcomes,
                "baseline": baseline,
            },
        )
        # AAA-4 dropped from BOTH sides — losses stay paired.
        assert out["metrics"]["n"] == 3
        assert out["cluster_scores"]["AAA"]["AAA:c1"] == pytest.approx(0.09)

    def test_predict_objects_receive_the_record(self, ctx):
        records, outcomes, _, _ = scoring_case()

        class PerContract:
            """Proves predict() sees the record — answers by its contract."""

            def __init__(self, table):
                self.table = table

            def predict(self, record):
                return self.table[_field(record, "contract")]

        _, _, signal_map, baseline_map = scoring_case()
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx,
            {
                "records": records,
                "signal": PerContract(signal_map),
                "outcomes": outcomes,
                "baseline": PerContract(baseline_map),
            },
        )
        assert out["metrics"]["loss"] == pytest.approx(0.1125)
        assert out["metrics"]["baseline_loss"] == pytest.approx(0.25)

    def test_mapping_signal_with_predict_baseline_mix(self, ctx):
        records, outcomes, signal, _ = scoring_case()

        class Flat:
            def predict(self, record):
                return 0.5

        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx,
            {
                "records": records,
                "signal": signal,
                "outcomes": outcomes,
                "baseline": Flat(),
            },
        )
        assert out["metrics"]["baseline_loss"] == pytest.approx(0.25)

    def test_predict_returning_none_declines_coverage(self, ctx):
        records, outcomes, _, _ = scoring_case()

        class OnlyFirst:
            def predict(self, record):
                return 0.8 if _field(record, "contract") == "AAA-1" else None

        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx, {"records": records, "signal": OnlyFirst(), "outcomes": outcomes}
        )
        assert out["metrics"]["n"] == 1

    def test_metric_dispatch_uses_the_registered_rule(self, ctx):
        records, outcomes, signal, _ = scoring_case()
        inputs = {"records": records, "signal": signal, "outcomes": outcomes}
        brier = Validate("val", {"split": "val", "metric": "brier"}).run(ctx, inputs)
        logloss = Validate("val", {"split": "val"}).run(ctx, inputs)  # default
        assert brier["metrics"]["loss"] != logloss["metrics"]["loss"]

    def test_below_min_events_raises_naming_split_and_floor(self, ctx):
        records, outcomes, signal, _ = scoring_case()
        with pytest.raises(ValueError, match=r"'val' split — below min_events=10"):
            Validate("val", {"split": "val", "min_events": 10}).run(
                ctx, {"records": records, "signal": signal, "outcomes": outcomes}
            )

    def test_empty_records_hit_the_floor(self, ctx):
        with pytest.raises(ValueError, match="min_events=1"):
            Validate("val", {"split": "val"}).run(
                ctx, {"records": [], "signal": {}, "outcomes": {}}
            )

    def test_records_may_be_a_single_pass_generator(self, ctx):
        records, outcomes, signal, _ = scoring_case()
        out = Validate("val", {"split": "val", "metric": "brier"}).run(
            ctx, {"records": iter(records), "signal": signal, "outcomes": outcomes}
        )
        assert out["metrics"]["n"] == 4

    def test_deterministic_and_contract_clean(self, ctx):
        records, outcomes, signal, baseline = scoring_case()
        node = Validate("val", {"split": "val", "metric": "brier"})
        inputs = {
            "records": records,
            "signal": signal,
            "outcomes": outcomes,
            "baseline": baseline,
        }
        first, second = node.run(ctx, inputs), node.run(ctx, inputs)
        assert first == second
        assert node.validate_outputs(first) == []

    def test_record_missing_a_field_fails_loudly_by_name(self, ctx):
        bad = {"contract": "AAA-1", "asof_ms": 15 * DAY, "cluster": "AAA:c0"}
        with pytest.raises(ValueError, match="instrument"):
            Validate("val", {"split": "val", "metric": "brier"}).run(
                ctx,
                {
                    "records": [bad],
                    "signal": {"AAA-1": 0.8},
                    "outcomes": {"AAA-1": True},
                    "baseline": {"AAA-1": 0.5},
                },
            )


class TestRecordAccessAndBeliefs:
    def test_field_prefers_attributes_and_covers_the_cluster_property(self):
        record = mrec("AAA-1", 5 * DAY, group="AAA:c0")
        assert _field(record, "instrument") == "AAA"
        assert _field(record, "cluster") == "AAA:c0"  # the property, not a key
        assert _field(mrec("AAA-2", 5 * DAY), "cluster") == "AAA-2"  # group=None

    def test_field_falls_back_to_mapping_keys(self):
        record = drec("AAA-1", 5 * DAY, cluster="AAA:c0")
        assert _field(record, "asof_ms") == 5 * DAY
        assert _field(record, "cluster") == "AAA:c0"

    def test_field_missing_everywhere_raises_by_name(self):
        with pytest.raises(ValueError, match="'cluster'"):
            _field({"contract": "AAA-1"}, "cluster")

    def test_belief_mapping_and_predictor_forms(self):
        assert _belief({"AAA-1": 0.7}, {}, "AAA-1") == 0.7
        assert _belief({"AAA-1": 0.7}, {}, "AAA-9") is None  # no coverage

        class Model:
            def predict(self, record):
                return record["mid"]

        assert _belief(Model(), {"mid": 0.42}, "AAA-1") == 0.42


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_both_kinds_owned(self):
        reg = NodeKindRegistry()
        register(reg)
        assert reg.get("stat_test") == (StatTest, True)
        assert reg.get("validate") == (Validate, True)

    def test_idempotent_second_call_is_a_no_op(self):
        reg = NodeKindRegistry()
        register(reg)
        register(reg)  # the registry raises on duplicates — skipping avoids it
        assert reg.get("stat_test") == (StatTest, True)

    def test_present_names_are_skipped_never_shadowed(self):
        reg = NodeKindRegistry()
        reg.register("stat_test", StatTest)  # squatter, owned=False
        register(reg)
        assert reg.get("stat_test") == (StatTest, False)  # untouched
        assert reg.get("validate") == (Validate, True)  # still added

    def test_default_registry_path(self, monkeypatch):
        import dskit.pipeline.kinds_stats as mod

        private = NodeKindRegistry()
        monkeypatch.setattr(mod, "DEFAULT_NODE_KINDS", private)
        register()
        assert private.get("stat_test") == (StatTest, True)
        assert private.get("validate") == (Validate, True)


# ---------------------------------------------------------------------------
# Integration: the planted edge through run_document
# ---------------------------------------------------------------------------


def private_registry():
    """The synthetic classes registered INDIVIDUALLY (never via
    register_synthetic_nodes — it would squat the owned ``stat_test``
    name) plus the toolkit-owned kinds under their real names."""
    reg = NodeKindRegistry()
    reg.register("synth-events", SynthEvents)
    reg.register("synth-labels", SynthLabels)
    reg.register("synth-market", SynthMarketSignal)
    reg.register("synth-train", SynthTrain)
    register(reg)
    return reg


def edge_pipeline(signal_ref="$qhat.signal", with_qhat=True):
    """The 432-event / seed-4 planted-edge market wired through the REAL
    owned kinds: brier scoring on the val split (8 clusters per
    instrument under BANKING_SPLITS), then the cluster bootstrap."""
    pipeline = {
        "events": NodeSpec(
            uses="synth-events",
            params={"n_events": 432, "n_instruments": 2, "seed": 4},
        ),
        "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.events"}),
        "market": NodeSpec(uses="synth-market", inputs={"events": "$events.events"}),
        "val": NodeSpec(
            uses="validate",
            inputs={
                "records": "$events.events",
                "signal": signal_ref,
                "baseline": "$market.signal",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val", "metric": "brier", "min_events": 10},
        ),
        "edge": NodeSpec(
            uses="stat_test",
            inputs={"scores": "$val.cluster_scores"},
            params={"alpha": 0.05, "n_boot": 2000},
        ),
    }
    if with_qhat:
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$events.events"},
            params={"min_train": 5},
        )
    return pipeline


def edge_document(tmp_path, pipeline, name):
    return PipelineDocument(
        name=name,
        pipeline=pipeline,
        splits=TimeSplitConfig(**BANKING_SPLITS),
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class TestIntegrationPlantedEdge:
    def test_planted_edge_deploys_through_run_document(self, tmp_path):
        doc = edge_document(tmp_path, edge_pipeline(), "kinds-edge")
        result = run_document(doc, asof=ASOF, registry=private_registry())
        assert result.state == "ran" and result.exit_code == 0
        out = result.outputs["edge"]
        assert out["survivors"] == ["SYNA", "SYNB"] and out["verdict"] == "GO"
        # 8/8 positive val clusters per instrument: the add-one floor,
        # exactly — maximal deterministic margin under alpha=0.05 BH.
        assert all(p == 1 / 2001 for p in out["pvalues"].values())
        metrics = result.outputs["val"]["metrics"]
        assert metrics["beats_baseline"] is True
        assert metrics["loss"] < metrics["baseline_loss"]
        assert metrics["n"] == 384  # 192 val events x 2 instruments

    def test_market_against_itself_null_is_nogo_halt(self, tmp_path):
        pipeline = edge_pipeline(signal_ref="$market.signal", with_qhat=False)
        doc = edge_document(tmp_path, pipeline, "kinds-null")
        result = run_document(doc, asof=ASOF, registry=private_registry())
        assert result.state == "halted" and result.exit_code == 3
        assert result.halted_at == "edge"
        assert _decision(result.outputs["edge"]) == {
            "survivors": [],
            "pvalues": {"SYNA": 1.0, "SYNB": 1.0},
            "verdict": "NO-GO",
        }

    def test_import_path_stat_test_still_refuses_to_plan(self, tmp_path):
        # The doctrine holds for OUR class too: only the owned registration
        # satisfies role stat_test — an import path never can.
        pipeline = edge_pipeline()
        pipeline["edge"] = NodeSpec(
            uses="dskit.pipeline.kinds_stats:StatTest",
            inputs={"scores": "$val.cluster_scores"},
            params={"alpha": 0.05, "n_boot": 2000},
        )
        doc = edge_document(tmp_path, pipeline, "kinds-doctrine")
        with pytest.raises(ConfigError, match="toolkit-owned"):
            plan(doc, private_registry())
