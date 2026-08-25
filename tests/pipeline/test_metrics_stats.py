"""Scoring rules + the cluster bootstrap / multiplicity machinery."""

import math

import pytest

from dskit.pipeline.metrics import (
    CLIP,
    METRICS,
    absolute_error,
    brier,
    logloss,
    register_metric,
    squared_error,
)
from dskit.pipeline.stats import (
    CORRECTIONS,
    benjamini_hochberg,
    bonferroni,
    cluster_bootstrap_pvalue,
    no_correction,
)


class TestMetrics:
    def test_logloss_values(self):
        assert logloss(0.8, 1.0) == pytest.approx(-math.log(0.8))
        assert logloss(0.8, 0.0) == pytest.approx(-math.log(0.2))

    def test_logloss_clips_hard_beliefs(self):
        assert logloss(0.0, 1.0) == pytest.approx(-math.log(CLIP))
        assert math.isfinite(logloss(1.0, 0.0))

    def test_brier_values(self):
        assert brier(0.8, 1.0) == pytest.approx(0.04)
        assert brier(0.8, 0.0) == pytest.approx(0.64)

    def test_domain_errors(self):
        with pytest.raises(ValueError, match="q must lie"):
            logloss(1.2, 1.0)
        with pytest.raises(ValueError, match="y must be 0.0 or 1.0"):
            brier(0.5, 0.7)
        with pytest.raises(ValueError, match="finite"):
            logloss(float("nan"), 1.0)
        with pytest.raises(ValueError, match="number"):
            brier(True, 1.0)

    def test_regression_rules_take_unbounded_values(self):
        # ADR-0025: the mark-to-market pair has no [0, 1] frame.
        assert squared_error(-2.5, 1.5) == pytest.approx(16.0)
        assert absolute_error(-2.5, 1.5) == pytest.approx(4.0)
        assert squared_error(3.0, 3.0) == 0.0

    def test_regression_rules_still_refuse_non_finite_values(self):
        with pytest.raises(ValueError, match="finite"):
            squared_error(float("inf"), 1.0)
        with pytest.raises(ValueError, match="finite"):
            absolute_error(0.0, float("nan"))
        with pytest.raises(ValueError, match="number"):
            squared_error(True, 1.0)

    def test_registry_and_registration(self):
        assert set(METRICS) >= {
            "logloss",
            "brier",
            "squared_error",
            "absolute_error",
        }
        with pytest.raises(ValueError, match="already registered"):
            register_metric("logloss", lambda q, y: 0.0)
        with pytest.raises(ValueError, match="non-empty"):
            register_metric("", lambda q, y: 0.0)
        with pytest.raises(ValueError, match="callable"):
            register_metric("new", "nope")


class TestClusterBootstrap:
    def test_clear_edge_gets_small_p(self):
        scores = {f"ev{i}": [0.5 + 0.01 * (i % 3)] for i in range(40)}
        assert cluster_bootstrap_pvalue(scores, 200, 0, label="X") < 0.01

    def test_no_edge_gets_large_p(self):
        scores = {f"ev{i}": [-0.5] for i in range(40)}
        assert cluster_bootstrap_pvalue(scores, 200, 0, label="X") > 0.99

    def test_deterministic_and_label_seeded(self):
        scores = {f"ev{i}": [(-1) ** i * 0.3, 0.05] for i in range(20)}
        p1 = cluster_bootstrap_pvalue(scores, 300, 7, label="A")
        p2 = cluster_bootstrap_pvalue(scores, 300, 7, label="A")
        p3 = cluster_bootstrap_pvalue(scores, 300, 7, label="B")
        assert p1 == p2
        assert p1 != p3  # per-instrument streams are independent

    def test_never_exactly_zero_or_one(self):
        p = cluster_bootstrap_pvalue({"e": [1.0]}, 100, 0)
        assert 0.0 < p < 1.0

    def test_empty_inputs_fail_loud(self):
        with pytest.raises(ValueError, match="empty"):
            cluster_bootstrap_pvalue({}, 10, 0)
        with pytest.raises(ValueError, match="no scores"):
            cluster_bootstrap_pvalue({"e": []}, 10, 0)


class TestCorrections:
    def test_bh_known_example(self):
        pvals = {"a": 0.001, "b": 0.02, "c": 0.03, "d": 0.9}
        rejected = benjamini_hochberg(pvals, alpha=0.05)
        assert rejected == {"a": True, "b": True, "c": True, "d": False}

    def test_bh_rejects_nothing_when_nothing_clears(self):
        assert benjamini_hochberg({"a": 0.9, "b": 0.8}, 0.05) == {
            "a": False,
            "b": False,
        }

    def test_bonferroni_is_stricter(self):
        pvals = {"a": 0.03, "b": 0.04}
        assert bonferroni(pvals, 0.05) == {"a": False, "b": False}
        assert no_correction(pvals, 0.05) == {"a": True, "b": True}

    def test_per_instrument_decisions_preserved(self):
        # the deliverable IS the localization: each name keeps its own call
        rejected = benjamini_hochberg({"a": 0.001, "z": 0.99}, 0.05)
        assert rejected["a"] and not rejected["z"]

    def test_input_validation(self):
        with pytest.raises(ValueError, match="nothing to test"):
            benjamini_hochberg({}, 0.05)
        with pytest.raises(ValueError, match="p-value"):
            bonferroni({"a": 0.0}, 0.05)
        with pytest.raises(ValueError, match="alpha"):
            no_correction({"a": 0.5}, 1.5)

    def test_registry(self):
        assert set(CORRECTIONS) == {"bh", "bonferroni", "none"}
