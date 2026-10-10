"""TapeRows: the day-file Binance tapes as the row lists MinuteFeatures takes, over a synthetic onboarded store.

Expected values are restated from the fixture bars (a deliberate independent restatement). A moved store must refuse
by name, and the rows must run through the real MinuteFeatures node, which is the only consumer.
"""

import json
import os

import pytest
from synthetic import KLINE_COLUMNS, Store, bvol_days, kline_days, ms, utc, walk

from crypto_trading.minute_features import MinuteFeatures
from crypto_trading.tape_rows import TapeRows

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = ms(utc(2026, 1, 5, 23, 0))  # an hour before midnight, so the bars span two UTC day files
BARS = 130


def params(store_root, **over):
    base = {
        "root": store_root, "day_relpath_template": "{day}.parquet",
        "assets": {"AAA": {"klines": {"source": "k-aaa", "stream": "files"},
                           "bvol": {"source": "b-aaa", "stream": "files"}},
                   "BBB": {"klines": {"source": "k-bbb", "stream": "files"},
                           "bvol": {"source": "b-bbb", "stream": "files"}}},
        "columns": {"klines": {"time": "open_time_ms", "fields": list(KLINE_COLUMNS)},
                    "bvol": {"time": "calc_time_ms", "fields": ["calc_time_ms", "index_value"]}},
    }
    base.update(over)
    return base


@pytest.fixture
def world(tmp_path, monkeypatch):
    store = Store(tmp_path / "world", monkeypatch)
    store.bars = {"AAA": walk(START, BARS, price=100.0, seed=1), "BBB": walk(START, BARS, price=50.0, seed=2)}
    stamps = [(START + 60_000 * k + 500, 40.0 + k) for k in range(0, BARS, 10)]
    store.blobs("k-aaa", kline_days(store.bars["AAA"]))
    store.blobs("k-bbb", kline_days(store.bars["BBB"]))
    store.blobs("b-aaa", bvol_days(stamps))
    store.blobs("b-bbb", bvol_days(stamps, symbol="BBB"))
    store.stamps = stamps
    return store


def manifests(root, p):
    from dskit.pipeline.libs.parquet_series import StreamManifests

    streams = {f"{a}_{tape}": spec for a, tapes in p["assets"].items() for tape, spec in tapes.items()}
    return StreamManifests("streams", {"root": root, "streams": streams}).run(None, {})["manifests"]


def test_every_bar_of_every_asset_becomes_one_row_with_the_asset_and_the_declared_fields(world):
    p = params(world.path)
    out = TapeRows("tapes", p).run(None, {"manifests": manifests(world.path, p)})
    assert len(out["klines"]) == 2 * BARS
    for asset in ("AAA", "BBB"):
        got = sorted((r for r in out["klines"] if r["asset"] == asset), key=lambda r: r["open_time_ms"])
        assert [r["open_time_ms"] for r in got] == [b["open_time_ms"] for b in world.bars[asset]]
        assert got[3]["close"] == world.bars[asset][3]["close"]
        assert set(got[0]) == {"asset", *KLINE_COLUMNS}
    assert len(out["bvol"]) == 2 * len(world.stamps)
    assert {r["asset"] for r in out["bvol"]} == {"AAA", "BBB"}
    assert set(out["bvol"][0]) == {"asset", "calc_time_ms", "index_value"}


def test_the_rows_run_through_the_real_feature_node_with_no_adapter_in_between(world):
    p = params(world.path)
    rows = TapeRows("tapes", p).run(None, {"manifests": manifests(world.path, p)})
    small = {"assets": ["AAA", "BBB"], "scales": [2, 5], "ewma_half_lives": [3], "ewma_lookback_multiple": 3,
             "range_windows": [4], "flow_window": 6, "taker_windows": [3], "scale_window": 10,
             "shock_window": 5, "shock_max_minutes": 8, "history_minutes": 13, "max_horizon": 3,
             "families": ["ret", "vol", "flow", "clock", "xasset"]}
    out = MinuteFeatures("features", small).run(None, {"klines": rows["klines"], "bvol": rows["bvol"]})
    assert out["records"] and out["provenance"]["assets"]["AAA"]["bars"] == BARS


def test_a_store_that_moved_since_the_manifest_was_taken_is_refused_by_name(world):
    p = params(world.path)
    held = manifests(world.path, p)
    held["AAA_klines"] = {**held["AAA_klines"], "manifest_sha256": "0" * 64}  # the snapshot the manifest saw is gone
    with pytest.raises(ValueError, match="AAA_klines"):
        TapeRows("tapes", p).run(None, {"manifests": held})


def test_a_manifests_port_without_a_tape_the_document_reads_is_refused(world):
    p = params(world.path)
    held = manifests(world.path, p)
    del held["BBB_bvol"]
    with pytest.raises(ValueError, match="BBB_bvol"):
        TapeRows("tapes", p).run(None, {"manifests": held})


def test_bvol_is_optional_per_asset(world):
    p = params(world.path)
    del p["assets"]["BBB"]["bvol"]
    out = TapeRows("tapes", p).run(None, {"manifests": manifests(world.path, p)})
    assert {r["asset"] for r in out["bvol"]} == {"AAA"}


@pytest.mark.parametrize("bad, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"root": ""}, "root"),
    ({"assets": {}}, "assets"),
    ({"day_relpath_template": "x.parquet"}, "day_relpath_template"),
    ({"columns": {"klines": {"time": "open_time_ms", "fields": ["close"]}}}, "columns"),
    ({"columns": {"klines": {"time": "nope", "fields": ["open_time_ms"]},
                  "bvol": {"time": "calc_time_ms", "fields": ["calc_time_ms"]}}}, "time"),
])
def test_default_deny_params(bad, fragment):
    problems = TapeRows.validate_params(params("/x", **bad))
    assert any(fragment in p for p in problems), problems


def test_the_shipped_document_wires_this_node_to_the_node_that_consumes_it():
    with open(os.path.join(CHILD_ROOT, "configs", "run-panel-build.json"), encoding="utf-8") as handle:
        document = json.load(handle)
    nodes = document["pipeline"]
    assert nodes["tapes"]["uses"] == "crypto_trading.tape_rows:TapeRows"
    assert nodes["features"]["inputs"] == {"klines": "$tapes.klines", "bvol": "$tapes.bvol"}


def test_start_and_end_cut_the_rows_and_the_day_files_read(world):
    cut = START + 60_000 * 90  # inside the second UTC day file
    p = params(world.path, start_ms=START + 60_000 * 10, end_ms=cut)
    out = TapeRows("tapes", p).run(None, {"manifests": manifests(world.path, p)})
    times = sorted(r["open_time_ms"] for r in out["klines"] if r["asset"] == "AAA")
    assert times == [t["open_time_ms"] for t in world.bars["AAA"] if START + 60_000 * 10 <= t["open_time_ms"] < cut]
    assert all(r["calc_time_ms"] < cut for r in out["bvol"])


def test_a_window_that_holds_no_day_reads_no_rows(world):
    p = params(world.path, start_ms=ms(utc(2020, 1, 1)), end_ms=ms(utc(2020, 1, 2)))
    out = TapeRows("tapes", p).run(None, {"manifests": manifests(world.path, p)})
    assert out["klines"] == [] and out["bvol"] == []


@pytest.mark.parametrize("bad", [{"start_ms": 5, "end_ms": 5}, {"end_ms": -1}, {"start_ms": 1.5}])
def test_window_bounds_are_ordered_non_negative_ints(bad):
    assert any("_ms" in p for p in TapeRows.validate_params(params("/x", **bad)))


def test_bucket_last_keeps_the_newest_finite_reading_per_bucket_and_features_are_unchanged(tmp_path, monkeypatch):
    """Exactness: the BVOL columns from the reduced tape equal those from the full tape, and the rows shrink."""
    raw = [(START + 60_000 * k + off, 40.0 + k + off / 1000) for k in range(BARS) for off in (100, 7_000, 59_000)]
    world = Store(tmp_path / "dense", monkeypatch)
    world.bars = {"AAA": walk(START, BARS, price=100.0, seed=1), "BBB": walk(START, BARS, price=50.0, seed=2)}
    world.blobs("k-aaa", kline_days(world.bars["AAA"]))
    world.blobs("k-bbb", kline_days(world.bars["BBB"]))
    world.blobs("b-aaa", bvol_days(raw))
    world.blobs("b-bbb", bvol_days(raw, symbol="BBB"))
    full_p, cut_p = params(world.path), params(world.path, bucket_last_ms={"bvol": 60_000})
    full = TapeRows("tapes", full_p).run(None, {"manifests": manifests(world.path, full_p)})
    cut = TapeRows("tapes", cut_p).run(None, {"manifests": manifests(world.path, cut_p)})
    assert len(full["bvol"]) == 2 * len(raw) and len(cut["bvol"]) == 2 * BARS
    assert cut["bvol"][0] == {"asset": "AAA", "calc_time_ms": START + 59_000, "index_value": 40.0 + 59.0}
    small = {"assets": ["AAA", "BBB"], "scales": [2, 5], "ewma_half_lives": [3], "ewma_lookback_multiple": 3,
             "range_windows": [4], "flow_window": 6, "taker_windows": [3], "scale_window": 10,
             "shock_window": 5, "shock_max_minutes": 8, "history_minutes": 13, "max_horizon": 3,
             "families": ["ret", "bvol"], "bvol_max_age_ms": 150_000, "bvol_change_minutes": 3}
    a = MinuteFeatures("f", small).run(None, {"klines": full["klines"], "bvol": full["bvol"]})["records"]
    b = MinuteFeatures("f", small).run(None, {"klines": cut["klines"], "bvol": cut["bvol"]})["records"]
    assert [(r["bvol__level"], r["bvol__change"]) for r in a] == [(r["bvol__level"], r["bvol__change"]) for r in b]


@pytest.mark.parametrize("bad", [{"bvol": 0}, {"bvol": 1.5}, {"nope": 60_000}, 60_000])
def test_bucket_last_ms_is_a_map_of_known_tapes_to_positive_ints(bad):
    assert TapeRows.validate_params({**params("/x"), "bucket_last_ms": bad})


@pytest.mark.parametrize("bucket", [None, {"bvol": 60_000}])
def test_reading_in_one_day_spans_gives_the_same_rows_as_one_read(world, bucket):
    extra = {} if bucket is None else {"bucket_last_ms": bucket}
    one, many = params(world.path, **extra), params(world.path, span_days=1, **extra)
    a = TapeRows("tapes", one).run(None, {"manifests": manifests(world.path, one)})
    b = TapeRows("tapes", many).run(None, {"manifests": manifests(world.path, many)})
    assert a["klines"] == b["klines"] and a["bvol"] == b["bvol"]


@pytest.mark.parametrize("bad", [{"span_days": 0}, {"span_days": 1.5}, {"bucket_last_ms": {"bvol": 7_000}}])
def test_span_days_is_a_positive_int_and_a_bucket_must_divide_a_day(bad):
    assert TapeRows.validate_params({**params("/x"), **bad})


@pytest.mark.parametrize("bucket", [None, {"bvol": 60_000}])
def test_arrays_output_drives_the_feature_node_to_the_same_columns_as_rows(world, bucket):
    """The columnar path is the row path minus the dicts: same refusals, same arrays, same provenance."""
    import numpy as np

    extra = {} if bucket is None else {"bucket_last_ms": bucket}
    rows_p, arr_p = params(world.path, **extra), params(world.path, output="arrays", **extra)
    rows = TapeRows("tapes", rows_p).run(None, {"manifests": manifests(world.path, rows_p)})
    arrs = TapeRows("tapes", arr_p).run(None, {"manifests": manifests(world.path, arr_p)})
    assert set(arrs["klines"]) == {"AAA", "BBB"} and arrs["klines"]["AAA"]["close"].shape == (BARS,)
    assert {k: v["rows"] for k, v in arrs["provenance"].items()} == {k: v["rows"] for k, v in rows["provenance"].items()}
    small = {"assets": ["AAA", "BBB"], "scales": [2, 5], "ewma_half_lives": [3], "ewma_lookback_multiple": 3,
             "range_windows": [4], "flow_window": 6, "taker_windows": [3], "scale_window": 10,
             "shock_window": 5, "shock_max_minutes": 8, "history_minutes": 13, "max_horizon": 3,
             "bvol_max_age_ms": 150_000, "bvol_change_minutes": 3, "emit": ["columns"]}
    a = MinuteFeatures("f", small).run(None, {"klines": rows["klines"], "bvol": rows["bvol"]})
    b = MinuteFeatures("f", small).run(None, {"klines": arrs["klines"], "bvol": arrs["bvol"]})
    assert a["provenance"] == b["provenance"] and set(a["columns"]) == set(b["columns"])
    for name, column in a["columns"].items():
        assert np.array_equal(column, b["columns"][name], equal_nan=True), name


def test_arrays_output_is_one_of_the_known_shapes():
    assert TapeRows.validate_params({**params("/x"), "output": "frames"})
