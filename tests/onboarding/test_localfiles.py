"""libs/localfiles.py: the reference connector, driven through the contract."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.libs import localfiles
from dskit.onboarding.libs.localfiles import LocalFilesConnector


@pytest.fixture
def conn():
    return LocalFilesConnector()


@pytest.fixture
def config(data_dir):
    return {"path": data_dir, "effective_field": "date"}


def _read(conn, config, streams, state=None, mode="live"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def test_spec_passes_its_own_gate(conn, config):
    check_config(conn, config)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**config, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"effective_field": "date"})


def test_check_fails_fast_on_bad_or_empty_paths(conn, tmp_path, config):
    conn.check(config)  # data exists — fine
    with pytest.raises(AssetError, match="not a directory"):
        conn.check({"path": str(tmp_path / "nope"), "effective_field": "date"})
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssetError, match="no \\*\\.csv"):
        conn.check({"path": str(empty), "effective_field": "date"})


def test_default_encoding_is_one_named_constant(conn, config, monkeypatch):
    # discover() and read() must both fall back to the SAME module constant
    # rather than each hardcoding their own "utf-8" literal. Rebind the
    # constant to a sentinel value: a call site that hardcoded "utf-8"
    # instead of reading the constant would keep seeing "utf-8" here and
    # the test would fail.
    assert localfiles._DEFAULT_ENCODING == "utf-8"
    monkeypatch.setattr(localfiles, "_DEFAULT_ENCODING", "utf-8-sig")

    seen = []
    real_rows = LocalFilesConnector._rows

    def _spy(self, path, encoding):
        seen.append(encoding)
        return real_rows(self, path, encoding)

    monkeypatch.setattr(LocalFilesConnector, "_rows", _spy)
    conn.discover(config)
    list(conn.read(config, ["prices"], {}, "live"))
    assert seen
    assert all(enc == "utf-8-sig" for enc in seen)


def test_encoding_default_pinned_in_prose(conn):
    # The default is stated in prose in both the module docstring and the
    # spec() note; pin both to the constant so a later change to the
    # constant cannot silently leave the prose saying the old value. The
    # trailing period terminates the needle, so a drifted "utf-8-sig"
    # cannot pass on the shared prefix.
    expected = f"default {localfiles._DEFAULT_ENCODING}."
    assert expected in localfiles.__doc__
    assert expected in conn.spec()["params"]["encoding"]["notes"]


def test_discover_one_stream_per_file(conn, config):
    streams = conn.discover(config)
    assert [s["stream"] for s in streams] == ["outlook", "prices"]
    assert streams[1]["schema"] == {"fields": ["close", "date"]}


def test_read_sorts_rows_and_emits_schema_then_state(conn, config):
    msgs = _read(conn, config, ["prices"])
    types = [m["type"] for m in msgs]
    assert types == ["SCHEMA", "RECORD", "RECORD", "RECORD", "STATE"]
    # The CSV was deliberately unsorted; emission is effective-date order.
    effs = [m["effective_date"] for m in msgs if m["type"] == "RECORD"]
    assert effs == ["2026-01-02", "2026-01-04", "2026-01-05"]
    assert msgs[-1]["state"] == {"prices": {"cursor": "2026-01-05"}}


def test_cursor_filters_already_durable_rows(conn, config):
    state = {"prices": {"cursor": "2026-01-04"}}
    records = [m for m in _read(conn, config, ["prices"], state)
               if m["type"] == "RECORD"]
    assert [m["effective_date"] for m in records] == ["2026-01-05"]


def test_caught_up_stream_keeps_its_cursor(conn, config):
    state = {"prices": {"cursor": "2026-01-05"}}
    msgs = _read(conn, config, ["prices"], state)
    assert [m["type"] for m in msgs if m["type"] == "RECORD"] == []
    assert msgs[-1]["state"] == state  # cursor never regresses on empty


def test_forecast_streams_declared(conn, config):
    config = {**config, "forecast_streams": ["outlook"]}
    records = [m for m in _read(conn, config, ["outlook"])
               if m["type"] == "RECORD"]
    assert all(m["kind"] == "forecast" for m in records)


def test_csv_values_are_strings(conn, config):
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert records[0]["data"]["close"] == "10.5"  # stdlib csv does not guess


def test_unknown_stream_named(conn, config):
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(config, ["ghost"], {}, "live"))


def test_row_missing_effective_field_named_with_line(conn, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "s.jsonl").write_text('{"date": "2026-01-01"}\n{"other": 1}\n')
    with pytest.raises(AssetError, match=":2"):
        list(conn.read({"path": str(bad), "effective_field": "date"},
                       ["s"], {}, "live"))


def test_duplicate_stem_refused(conn, tmp_path):
    d = tmp_path / "dup"
    d.mkdir()
    (d / "s.csv").write_text("date\n2026-01-01\n")
    (d / "s.jsonl").write_text('{"date": "2026-01-01"}\n')
    with pytest.raises(AssetError, match="both csv and jsonl"):
        conn.check({"path": str(d), "effective_field": "date"})
