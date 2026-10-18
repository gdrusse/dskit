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
