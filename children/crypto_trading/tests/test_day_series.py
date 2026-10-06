"""The many-file reader: daily parquet files of one onboarded stream as a time series.

INTERIM (PROPOSED ADR-0240): a generic reader would live in dskit; these tests move over.
Fixtures are real acquisitions (``localblobs``), read through ``payload_files``.
"""

import numpy as np
import pytest
from synthetic import DAY_MS, Store, bvol_days, kline_days, ms, utc, walk

from crypto_trading.day_series import ParquetDaySeries, StreamManifests, prior_index

KLINE_COLUMNS = ["open_time_ms", "close_time_ms", "close"]
START = ms(utc(2026, 9, 1, 23, 0))


def klines_store(tmp_path, monkeypatch, bars=None, source="btc-1m"):
    store = Store(tmp_path, monkeypatch)
    bars = bars if bars is not None else walk(START, 180)  # 23:00 on the 1st to 02:00 on the 2nd
    files = kline_days(bars)
    store.blobs(source, {**files, "_raw/2026-09-01.zip": b"not parquet"})
    return store, bars


def series(store, source="btc-1m", time_column="close_time_ms", columns=None, template="{day}.parquet"):
    return ParquetDaySeries(store.path, source, "files", template, time_column,
                            columns or KLINE_COLUMNS)


def test_days_lists_only_files_the_template_names(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    assert series(store).days() == ["2026-09-01", "2026-09-02"], "the _raw zip is not a day file"


def test_load_span_returns_ascending_arrays_over_the_days_the_span_touches(tmp_path, monkeypatch):
    store, bars = klines_store(tmp_path, monkeypatch)
    got = series(store).load_span(START, START + 2 * 3_600_000)
    assert got["close_time_ms"].dtype == np.int64
    assert len(got["close"]) == 180, "whole day files, not a cut at the span"
    assert np.all(np.diff(got["close_time_ms"]) > 0)
    assert got["close"][0] == pytest.approx(bars[0]["close"])
    only_day_two = series(store).load_span(ms(utc(2026, 9, 2, 0, 30)), ms(utc(2026, 9, 2, 1, 0)))
    assert len(only_day_two["close"]) == 120, "the first day is not read"


def test_a_span_over_days_the_store_lacks_is_empty_not_an_error(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    got = series(store).load_span(ms(utc(2026, 9, 9)), ms(utc(2026, 9, 10)))
    assert len(got["close"]) == 0


def test_each_file_is_verified_against_the_manifest_before_it_is_parsed(tmp_path, monkeypatch):
    from dskit.onboarding import payload_files

    store, _ = klines_store(tmp_path, monkeypatch)
    victim = payload_files(store.path, "btc-1m", "files")["files"]["2026-09-02.parquet"]
    victim.write_bytes(victim.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="2026-09-02.parquet.*manifest"):
        series(store).load_span(START, START + DAY_MS)


def test_a_declared_column_the_file_lacks_is_refused_by_name(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="no_such_column"):
        series(store, columns=["close_time_ms", "no_such_column"]).load_span(START, START + 1)


def test_instants_that_run_backwards_inside_a_file_are_refused(tmp_path, monkeypatch):
    bars = walk(START, 5)
    bars[2], bars[3] = bars[3], bars[2]
    store, _ = klines_store(tmp_path, monkeypatch, bars=bars)
    with pytest.raises(ValueError, match="not ascending"):
        series(store).load_span(START, START + 1)


def test_the_template_must_carry_a_day_placeholder(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=r"\{day\}"):
        series(store, template="bars.parquet")


def test_prior_index_is_strictly_before_the_instant():
    times = np.array([10, 20, 30])
    assert list(prior_index(times, np.array([5, 10, 11, 20, 21, 31]))) == [-1, -1, 0, 0, 1, 2]


def bvol_store(tmp_path, monkeypatch, rows, source="btc-bvol"):
    store = Store(tmp_path, monkeypatch)
    store.blobs(source, bvol_days(rows))
    return store


def bvol_series(store, source="btc-bvol"):
    return ParquetDaySeries(store.path, source, "files", "{day}.parquet", "calc_time_ms",
                            ["calc_time_ms", "index_value"])


def test_prior_takes_the_last_row_strictly_before_each_instant_with_its_age(tmp_path, monkeypatch):
    t0 = ms(utc(2026, 9, 2, 1, 0))
    rows = [(t0 + 1000 * k, 50.0 + k) for k in range(-5, 6)]  # one row a second around t0
    store = bvol_store(tmp_path, monkeypatch, rows)
    got = bvol_series(store).prior(np.array([t0]), max_age_ms=5000)
    assert got["index_value"][0] == 49.0, "the row AT the instant (value 50) is not yet known"
    assert got["age_ms"][0] == 1000
    got = bvol_series(store).prior(np.array([t0 + 1]), max_age_ms=5000)
    assert got["index_value"][0] == 50.0 and got["age_ms"][0] == 1


def test_a_row_older_than_the_age_cap_is_missing_not_stale(tmp_path, monkeypatch):
    t0 = ms(utc(2026, 9, 2, 1, 0))
    store = bvol_store(tmp_path, monkeypatch, [(t0 - 61_000, 50.0)])
    got = bvol_series(store).prior(np.array([t0]), max_age_ms=60_000)
    assert np.isnan(got["index_value"][0]) and np.isnan(got["age_ms"][0])
    got = bvol_series(store).prior(np.array([t0]), max_age_ms=61_000)
    assert got["index_value"][0] == 50.0


def test_prior_crosses_midnight_and_does_not_bridge_a_missing_day(tmp_path, monkeypatch):
    midnight = ms(utc(2026, 9, 2, 0, 0))
    store = bvol_store(tmp_path, monkeypatch,
                       [(midnight - 1000, 51.0), (midnight - 3 * DAY_MS, 40.0)])
    got = bvol_series(store).prior(np.array([midnight + 500, midnight + DAY_MS]), max_age_ms=5000)
    assert got["index_value"][0] == 51.0 and got["age_ms"][0] == 1500
    assert np.isnan(got["index_value"][1]), "a day with no file is a gap, never carried across"


def test_prior_answers_many_instants_in_any_order(tmp_path, monkeypatch):
    t0 = ms(utc(2026, 9, 2, 1, 0))
    rows = [(t0 + 1000 * k, float(k)) for k in range(0, 100)]
    store = bvol_store(tmp_path, monkeypatch, rows)
    instants = np.array([t0 + 50_500, t0 + 10_500, t0 + 99_999])
    got = bvol_series(store).prior(instants, max_age_ms=10_000)
    assert list(got["index_value"]) == [50.0, 10.0, 99.0]


def test_the_manifest_node_fingerprints_the_streams_it_names(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    params = {"root": store.path, "streams": {"btc_klines": {"source": "btc-1m", "stream": "files"}}}
    node = StreamManifests("manifests", params)
    out = node.run(None, {})
    entry = out["manifests"]["btc_klines"]
    assert set(entry) == {"source", "stream", "snapshot", "manifest_sha256", "files"}
    assert entry["files"] == 3 and len(entry["manifest_sha256"]) == 64, "two day files and the raw zip"
    assert node.fingerprint() == {"kind": "StreamManifests", "manifests": {
        "btc_klines": entry["manifest_sha256"]}}

    other, _ = klines_store(tmp_path / "other", monkeypatch, bars=walk(START, 10))
    moved = StreamManifests("manifests", {**params, "root": other.path})
    assert moved.fingerprint() != node.fingerprint(), "a changed store moves the run identity"


def test_the_manifest_node_refuses_a_missing_stream_and_unknown_params(tmp_path, monkeypatch):
    store, _ = klines_store(tmp_path, monkeypatch)
    node = StreamManifests("m", {"root": store.path,
                                 "streams": {"x": {"source": "no-such-source", "stream": "files"}}})
    with pytest.raises(Exception):
        node.run(None, {})
    with pytest.raises(Exception, match="surprise"):
        StreamManifests("m", {"root": "r", "streams": {}, "surprise": 1})
