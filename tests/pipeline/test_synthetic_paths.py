"""The seeded GJR-GARCH test-bed path (ADR-0168)."""

import math
import statistics

import pytest

from dskit.pipeline.node import ConfigError
from dskit.pipeline.synthetic_paths import GJR_DEFAULTS, SynthGjrPaths


def _returns(records):
    closes = [r["close"] for r in records]
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def test_path_is_deterministic_per_seed_and_moves_with_it():
    a = SynthGjrPaths("m", {"n_days": 300, "seed": 4}).run(None, {})["records"]
    b = SynthGjrPaths("m", {"n_days": 300, "seed": 4}).run(None, {})["records"]
    c = SynthGjrPaths("m", {"n_days": 300, "seed": 5}).run(None, {})["records"]
    assert a == b and a != c
    assert [r["asof_ms"] for r in a] == sorted({r["asof_ms"] for r in a})
    assert len({r["contract"] for r in a}) == 300


def test_path_has_declared_scale_fat_tails_and_leverage():
    records = SynthGjrPaths("m", {"n_days": 20_000, "seed": 1}).run(None, {})["records"]
    rets = _returns(records)
    d = GJR_DEFAULTS
    uncond = math.sqrt(d["omega"] / (1 - d["alpha"] - d["gamma"] / 2 - d["beta"]))
    assert statistics.pstdev(rets) == pytest.approx(uncond, rel=0.15)
    z = [r / rec["cond_vol"] for r, rec in zip(rets, records[1:])]
    kurt = statistics.fmean([v ** 4 for v in z]) / statistics.fmean([v ** 2 for v in z]) ** 2
    assert kurt > 3.5
    # rets[i] is day i+1's shock; the variance it drives is day i+2's
    after_down = [records[i + 2]["cond_vol"] for i, r in enumerate(rets[:-1]) if r < -0.01]
    after_up = [records[i + 2]["cond_vol"] for i, r in enumerate(rets[:-1]) if r > 0.01]
    assert statistics.fmean(after_down) > statistics.fmean(after_up)


def test_day_zero_is_the_start_level_at_unconditional_variance():
    first = SynthGjrPaths("m", {"n_days": 3, "s0": 50.0}).run(None, {})["records"][0]
    d = GJR_DEFAULTS
    assert first["close"] == 50.0
    assert first["cond_vol"] == pytest.approx(
        math.sqrt(d["omega"] / (1 - d["alpha"] - d["gamma"] / 2 - d["beta"])))


def test_fingerprint_and_edge_cover_every_knob():
    node = SynthGjrPaths("m", {"n_days": 10})
    assert set(node.fingerprint()) == set(GJR_DEFAULTS) | {"kind"}
    assert node.data_edge() == node.run(None, {})["records"][-1]["asof_ms"]


@pytest.mark.parametrize("bad", [
    {"beta": 0.99}, {"nu": 2}, {"omega": 0}, {"n_days": 1}, {"alpha": -0.1},
    {"s0": float("nan")}, {"instrument": ""}, {"typo": 1}, {"seed": True},
])
def test_invalid_params_refuse(bad):
    with pytest.raises(ConfigError):
        SynthGjrPaths("m", bad)
