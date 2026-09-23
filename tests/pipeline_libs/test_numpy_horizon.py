"""Horizon label and realized-volatility features in the numpy pack (ADR-0168)."""

import math

import pytest

np = pytest.importorskip("numpy")

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.numpy import (
    ForwardRealizedVol,
    HorizonLogReturn,
    RealizedVolFeatures,
    ReturnWindows,
    _accessor_owner,
)
from dskit.pipeline.node import NodeContext

DAY = 86_400_000
CLOSES = [100.0, 101.0, 99.0, 102.0, 103.0, 101.5, 104.0]


def records(closes=CLOSES, name="SYN"):
    return [
        {"instrument": name, "contract": f"{name}-{i}", "group": f"{name}:{i}",
         "asof_ms": 1000 * DAY + i * DAY, "close": c}
        for i, c in enumerate(closes)
    ]


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


def test_horizon_label_is_the_cumulative_forward_log_return(tmp_path):
    rows = HorizonLogReturn("y", {"fields": ["close"], "horizon": 3}).run(
        ctx(tmp_path), {"records": records()})["rows"]
    for t, row in enumerate(rows):
        if t + 3 < len(CLOSES):
            assert row["label"] == pytest.approx(math.log(CLOSES[t + 3] / CLOSES[t]))
        else:
            assert row["label"] is None


def test_horizon_label_passes_the_causality_guard_at_its_declared_reach(tmp_path):
    node = HorizonLogReturn("y", {"fields": ["close"], "horizon": 2, "label_name": "fwd",
                                  "carry_fields": ["contract", "close"]})
    assert node.lookahead_columns() == {"fwd": 2}
    rows = node.run(ctx(tmp_path), {"records": records()})["rows"]
    assert set(rows[0]) == {"contract", "close", "fwd"}


def test_forward_realized_vol_reads_exactly_the_next_horizon(tmp_path):
    node = ForwardRealizedVol("f", {"fields": ["close"], "horizon": 3})
    assert node.lookahead_columns() == {"rv_fwd": 3}
    rows = node.run(ctx(tmp_path), {"records": records()})["rows"]
    rets = [math.log(b / a) for a, b in zip(CLOSES, CLOSES[1:])]
    for t, row in enumerate(rows):
        if t + 3 < len(CLOSES):
            assert row["rv_fwd"] == pytest.approx(math.sqrt(sum(r * r for r in rets[t:t + 3]) / 3))
        else:
            assert row["rv_fwd"] is None


def test_realized_vol_matches_the_definition(tmp_path):
    rows = RealizedVolFeatures("rv", {"fields": ["close"], "windows": [1, 3]}).run(
        ctx(tmp_path), {"records": records()})["rows"]
    rets = [math.log(b / a) for a, b in zip(CLOSES, CLOSES[1:])]
    assert rows[0]["rv_1"] is None and rows[2]["rv_3"] is None
    assert rows[1]["rv_1"] == pytest.approx(abs(rets[0]))
    assert rows[5]["rv_3"] == pytest.approx(math.sqrt(sum(r * r for r in rets[2:5]) / 3))


def test_features_then_label_chain_carries_features_forward(tmp_path):
    rv = RealizedVolFeatures("rv", {
        "fields": ["close"], "windows": [2], "drop_incomplete": True,
        "carry_fields": ["instrument", "contract", "asof_ms", "group", "close"],
    }).run(ctx(tmp_path), {"records": records()})["rows"]
    out = HorizonLogReturn("y", {
        "fields": ["close"], "horizon": 2, "drop_incomplete": True,
        "carry_fields": ["instrument", "contract", "asof_ms", "group", "rv_2"],
    }).run(ctx(tmp_path), {"records": rv})["rows"]
    assert len(out) == len(CLOSES) - 2 - 2  # rv warm-up, then label reach
    assert all(row["rv_2"] is not None and row["label"] is not None for row in out)


@pytest.mark.parametrize("cls,bad", [
    (HorizonLogReturn, {"fields": ["close"]}),
    (HorizonLogReturn, {"fields": ["close"], "horizon": 0}),
    (HorizonLogReturn, {"fields": ["a", "b"], "horizon": 2}),
    (HorizonLogReturn, {"fields": ["close"], "horizon": 2, "label_name": ""}),
    (ForwardRealizedVol, {"fields": ["close"]}),
    (ForwardRealizedVol, {"fields": ["close"], "horizon": 0}),
    (ForwardRealizedVol, {"fields": ["close"], "horizon": 2, "label_name": ""}),
    (RealizedVolFeatures, {"fields": ["close"]}),
    (RealizedVolFeatures, {"fields": ["close"], "windows": [2, 2]}),
    (RealizedVolFeatures, {"fields": ["close"], "windows": [0]}),
    (RealizedVolFeatures, {"fields": ["close"], "windows": [2], "vol_prefix": ""}),
    (RealizedVolFeatures, {"fields": ["close"], "windows": [2], "typo": 1}),
])
def test_invalid_params_refuse(cls, bad):
    with pytest.raises(ConfigError):
        cls("n", bad)


def test_shared_one_field_rule_still_governs_return_windows():
    with pytest.raises(ConfigError, match="EXACTLY ONE"):
        ReturnWindows("w", {"fields": ["a", "b"], "lookback": 2})


def test_every_new_knob_with_an_accessor_is_covered():
    for cls in (HorizonLogReturn, RealizedVolFeatures, ForwardRealizedVol):
        for knob in cls._PARAMS:
            if callable(getattr(cls, knob, None)):
                assert _accessor_owner(cls, knob) is not None, (cls, knob)


def _probes(tmp_path):
    return {
        "numpy-horizon-log-return": NodeProbe(
            params={"fields": ["close"], "horizon": 2}, required=("fields", "horizon"),
            inputs={"records": records()}, stream_ports=("records",), runnable=True,
        ),
        "numpy-forward-realized-vol": NodeProbe(
            params={"fields": ["close"], "horizon": 2}, required=("fields", "horizon"),
            inputs={"records": records()}, stream_ports=("records",), runnable=True,
        ),
        "numpy-realized-vol": NodeProbe(
            params={"fields": ["close"], "windows": [2]}, required=("fields", "windows"),
            inputs={"records": records()}, stream_ports=("records",), runnable=True,
        ),
    }


TestHorizonPackConformance = conformance_suite(
    registry=(
        ("numpy-horizon-log-return", HorizonLogReturn),
        ("numpy-realized-vol", RealizedVolFeatures),
        ("numpy-forward-realized-vol", ForwardRealizedVol),
    ),
    module="dskit.pipeline.libs.numpy",
    probes=_probes,
    expected_roles={"numpy-horizon-log-return": "tensor", "numpy-realized-vol": "tensor",
                    "numpy-forward-realized-vol": "tensor"},
    name="TestHorizonPackConformance",
)
