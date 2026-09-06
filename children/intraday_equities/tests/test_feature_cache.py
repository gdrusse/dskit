"""Content-verified, memory-mapped feature-cache tests."""

from __future__ import annotations

import numpy as np

from intraday_equities.feature_cache import SessionFeatureCache, write_feature_cache


def _outputs():
    records = []
    tape = []
    for index, symbol in enumerate(("AAA", "BBB")):
        records.append(
            {
                "symbol": symbol,
                "asof_ms": np.array([1, 2], dtype=np.int64),
                "close": np.array([2.0, 3.0], dtype=np.float32),
                "names": ["ret_lag_0"],
                "X": np.array([[index], [index + 1]], dtype=np.float32),
            }
        )
        tape.append(
            {
                "symbol": symbol,
                "asof_ms": np.array([0, 1, 2], dtype=np.int64),
                "close": np.array([1.0, 2.0, 3.0], dtype=np.float32),
                "price_field": "close",
            }
        )
    return {"records": records, "tape": tape}


def test_cache_round_trip_is_memory_mapped_and_content_verified(tmp_path):
    path = tmp_path / "features"
    digest = write_feature_cache(str(path), _outputs(), {"study": "p10"})
    node = SessionFeatureCache("cached", {"path": str(path), "manifest_sha256": digest})
    assert node.validate_params(node.params) == []
    assert node.fingerprint()["symbols"] == ["AAA", "BBB"]
    out = node.run(None, {})
    assert [row["symbol"] for row in out["records"]] == ["AAA", "BBB"]
    assert isinstance(out["records"][0]["X"], np.memmap)
    np.testing.assert_array_equal(
        out["records"][1]["X"], np.array([[1], [2]], dtype=np.float32)
    )


def test_cache_refuses_manifest_drift(tmp_path):
    path = tmp_path / "features"
    digest = write_feature_cache(str(path), _outputs(), {"study": "p10"})
    manifest = path / "manifest.json"
    manifest.write_text(manifest.read_text().replace('"version": 1', '"version": 2'))
    node = SessionFeatureCache("cached", {"path": str(path), "manifest_sha256": digest})
    try:
        node.fingerprint()
    except ValueError as exc:
        assert "manifest digest changed" in str(exc)
    else:
        raise AssertionError("manifest drift was accepted")


def test_cache_v2_round_trips_kline_frames(tmp_path):
    outputs = _outputs()
    outputs["klines"] = [
        {
            "symbol": symbol,
            "asof_ms": np.array([1, 2], dtype=np.int64),
            "session": np.array([10, 10], dtype=np.int32),
            "names": ["open", "high", "low", "close", "volume", "amount"],
            "X": np.ones((2, 6), dtype=np.float32),
        }
        for symbol in ("AAA", "BBB")
    ]
    path = tmp_path / "features"
    digest = write_feature_cache(str(path), outputs, {"study": "kronos"})
    out = SessionFeatureCache(
        "cached", {"path": str(path), "manifest_sha256": digest}
    ).run(None, {})
    assert [row["symbol"] for row in out["klines"]] == ["AAA", "BBB"]
    assert isinstance(out["klines"][0]["X"], np.memmap)
    assert out["klines"][0]["names"][-1] == "amount"


def test_cache_v3_round_trips_one_minute_sequence_frames(tmp_path):
    outputs = _outputs()
    outputs["sequences"] = [
        {
            "symbol": symbol,
            "asof_ms": np.array([2], dtype=np.int64),
            "names": ["ohlcv_t000_open", "ohlcv_t000_close"],
            "X": np.array([[10.0, 10.5]], dtype=np.float32),
        }
        for symbol in ("AAA", "BBB")
    ]
    path = tmp_path / "features"
    digest = write_feature_cache(str(path), outputs, {"study": "recurrent"})
    out = SessionFeatureCache(
        "cached", {"path": str(path), "manifest_sha256": digest}
    ).run(None, {})
    assert [row["symbol"] for row in out["sequences"]] == ["AAA", "BBB"]
    assert isinstance(out["sequences"][0]["X"], np.memmap)
    assert out["sequences"][0]["names"][-1] == "ohlcv_t000_close"
