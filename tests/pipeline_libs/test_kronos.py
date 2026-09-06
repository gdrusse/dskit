"""Frozen Kronos library-pack contract tests."""

from __future__ import annotations

import numpy as np
import pytest

from dskit.pipeline.libs.kronos import (
    KronosHiddenState,
    _copy_final_hidden,
    _prefix_normalize,
)


def _params(tmp_path):
    return {
        "source_root": str(tmp_path / "source"),
        "source_revision": "a" * 40,
        "onboarding_root": str(tmp_path / "ob"),
        "tokenizer_snapshot": "b" * 64,
        "model_snapshot": "c" * 64,
        "cache_dir": str(tmp_path / "cache"),
        "input_identity": ["d" * 64],
        "score_period_ms": 1_800_000,
        "batch_size": 8,
        "device": "cpu",
        "dtype": "float16",
        "timezone": "America/New_York",
        "encoder_contract": "upstream-prefix-mean-std-final-hidden-v1",
    }


def test_kronos_params_are_closed_and_content_pinned(tmp_path):
    params = _params(tmp_path)
    assert KronosHiddenState.validate_params(params) == []
    params["surprise"] = True
    assert "unknown" in " ".join(KronosHiddenState.validate_params(params))


def test_upstream_normalization_is_applied_to_causal_prefixes():
    values = np.arange(36, dtype=np.float32).reshape(6, 6) + 1.0
    prefix = _prefix_normalize(values[:4])
    changed = values.copy()
    changed[4:] *= 1000.0
    perturbed = _prefix_normalize(changed[:4])
    np.testing.assert_allclose(prefix, perturbed, rtol=0.0, atol=0.0)
    expected = (values[:4] - values[:4].mean(axis=0)) / (
        values[:4].std(axis=0) + 1e-5
    )
    np.testing.assert_allclose(prefix, expected, rtol=1e-6)
    assert np.isfinite(prefix).all()


def test_final_hidden_copy_does_not_retain_padded_batch():
    hidden = np.arange(4 * 78 * 512, dtype=np.float32).reshape(4, 78, 512)
    final = _copy_final_hidden(hidden, 2, 17)
    assert final.shape == (1, 512)
    assert final.flags.owndata
    assert not np.shares_memory(final, hidden)
    np.testing.assert_array_equal(final[0], hidden[2, 16])


@pytest.mark.slow
def test_real_pinned_kronos_gpu_smoke(tmp_path, monkeypatch):
    """Opt-in proof that the official source and both snapshots interoperate."""
    if not __import__("os").environ.get("DSKIT_KRONOS_SMOKE"):
        pytest.skip("set DSKIT_KRONOS_SMOKE=1 for the pinned-weight smoke")
    root = "/home/russell/dskit"
    monkeypatch.chdir(root)
    params = {
        "source_root": root + "/third_party/Kronos",
        "source_revision": "67b630e67f6a18c9e9be918d9b4337c960db1e9a",
        "onboarding_root": (
            "/home/russell/.local/share/intraday_equities/onboarding"
        ),
        "tokenizer_snapshot": (
            "c101158cfee9ab7424a21acbf92963f714a7d32ca3e0f34721fe514ebf67ed87"
        ),
        "model_snapshot": (
            "bb1bb0b2cede75c5bf72c9411d30eb3d55e762314b5513cea07b3a1b6d400b55"
        ),
        "cache_dir": str(tmp_path / "cache"),
        "input_identity": ["d" * 64],
        "score_period_ms": 1_800_000,
        "batch_size": 8,
        "device": "cuda",
        "dtype": "float16",
        "timezone": "America/New_York",
        "encoder_contract": "upstream-prefix-mean-std-final-hidden-v1",
    }
    sessions = int(__import__("os").environ.get("DSKIT_KRONOS_SESSIONS", "1"))
    start = 1_767_623_400_000
    one_ms = start + np.arange(78, dtype=np.int64) * 300_000
    ms = np.concatenate([one_ms + day * 86_400_000 for day in range(sessions)])
    one_price = 100 + np.linspace(0, 2, 78, dtype=np.float32)
    price = np.tile(one_price, sessions)
    values = np.column_stack(
        [
            price - 0.05,
            price + 0.1,
            price - 0.1,
            price,
            np.full(len(ms), 10_000, dtype=np.float32),
            price * 10_000,
        ]
    ).astype(np.float32)
    frame = {
        "symbol": "AAPL",
        "asof_ms": ms,
        "session": np.repeat(
            np.arange(739_621, 739_621 + sessions, dtype=np.int32), 78
        ),
        "names": ["open", "high", "low", "close", "volume", "amount"],
        "X": values,
    }
    node = KronosHiddenState("kronos", params)
    out = node.run(None, {"records": [frame]})
    again = node.run(None, {"records": [frame]})
    assert out["records"][0]["X"].shape[1] == 512
    assert out["records"][0]["X"].dtype == np.float16
    assert isinstance(again["records"][0]["X"], np.memmap)
