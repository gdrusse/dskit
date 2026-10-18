"""Pin the focused tuning scope and reuse the proven chronological checks."""
import json
from pathlib import Path

import pytest

from test_warmup20 import test_stage_boundaries_and_features as _check_boundaries

ROOT = Path(__file__).parents[1] / "configs"
PATHS = sorted(ROOT.glob("run-pocket-tuning-*.json"))


def test_complete_tuning_pairs():
    assert len(PATHS) == 40


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_aligned_and_label_invariant(path):
    _check_boundaries(path)
    doc = json.loads(path.read_text())
    spec = doc["notes"].split("Parent method ")[1].split(";")[0]
    parent = int(spec)
    stage = "validation" if "-validation-" in path.name else "warmup"
    previous = json.loads(next(ROOT.glob(
        f"run-warmup20-{stage}-{parent:02d}-*.json")).read_text())
    nodes = doc["pipeline"]
    for name in ["prior_ard", "prior_newton", "calibration_baseline", "data"]:
        assert nodes[name] == previous["pipeline"][name]
    # Selection can change only the named candidate knobs, never comparators.
    assert nodes["fit"]["params"]["features"] == previous["pipeline"]["fit"]["params"]["features"]
    assert nodes["fit"]["params"]["label"] == "standard_residual"


def test_four_exact_reference_controls():
    for stage in ["warmup", "validation"]:
        for method, parent in [(2, 24), (5, 48), (8, 44), (11, 38)]:
            paths = list(ROOT.glob(f"run-pocket-tuning-{stage}-{method:02d}-*.json"))
            assert len(paths) == 1
            doc = json.loads(paths[0].read_text())
            old = json.loads(next(ROOT.glob(f"run-warmup20-{stage}-{parent:02d}-*.json")).read_text())
            for node in ["fit", "probability", "group"]:
                assert doc["pipeline"].get(node) == old["pipeline"].get(node)


@pytest.mark.parametrize("method", range(1, 13))
def test_correction_strength_matches_declared_damping(method):
    import math

    from dskit.pipeline.kinds_compute import ComputeFields

    strength = [.25, .5, .75][(method - 1) % 3]
    path = next(ROOT.glob(f"run-pocket-tuning-validation-{method:02d}-*.json"))
    doc = json.loads(path.read_text())
    expr = doc["pipeline"]["probability"]["params"]["fields"]["prediction"]
    rows = [{"mid": p, "raw_prediction": raw}
            for p in [.01, .15, .5, .85, .99] for raw in [-2., -.02, .02, 2.]]
    result = ComputeFields("p", {"fields": {"prediction": expr}}).run(
        None, {"records": rows})["records"]
    for row in result:
        p = row["mid"]
        delta = max(-1., min(1., row["raw_prediction"] / math.sqrt(max(p * (1-p), .0001))))
        expected = 1 / (1 + math.exp(-(math.log(p / (1-p)) + strength * delta)))
        assert row["prediction"] == pytest.approx(expected, abs=1e-12)


def test_pruning_and_regime_perturbations_are_candidate_only():
    import math

    for method in range(13, 21):
        path = next(ROOT.glob(f"run-pocket-tuning-validation-{method:02d}-*.json"))
        nodes = json.loads(path.read_text())["pipeline"]
        if method <= 18:
            expected = 1000. if method % 2 else 100000.
            assert nodes["fit"]["params"]["estimator_params"]["threshold_lambda"] == expected
        else:
            expected = math.log(.5 if method == 19 else 2.)
            assert nodes["group"]["params"]["cases"][0]["when"][0]["value"] == expected
        assert "threshold_lambda" not in nodes["prior_newton"]["params"]["estimator_params"]
