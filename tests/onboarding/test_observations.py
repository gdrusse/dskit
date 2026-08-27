"""observations.py: the read seam (ADR-0037) — bitemporal dedup,
codec-aware resolution, loud refusals, digest parity, and the
single-copy memory contract."""

import hashlib
import json
import os
import tracemalloc

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding.codec import open_text_writer
from dskit.onboarding.observations import scan_stream, stream_digest

ACQUIRED = "2026-01-06T00:00:00+00:00"


def _row(symbol, ts, close, acquired=ACQUIRED):
    return {
        "stream": "bars", "mode": "backfill", "kind": "observation",
        "effective_date": ts, "acquired_at": acquired,
        "data": {"symbol": symbol, "ts": ts, "close": close},
    }


def _write(root, acq, rows, codec="none", stream="bars"):
    directory = os.path.join(root, "observations", "alpaca", acq)
    os.makedirs(directory, exist_ok=True)
    suffix = ".gz" if codec == "gzip" else ""
    path = os.path.join(directory, f"{stream}.jsonl{suffix}")
    with open_text_writer(path, codec) as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def _scan(root, **over):
    kwargs = dict(source="alpaca", stream="bars",
                  key_fields=("symbol", "ts"), ts_field="ts")
    kwargs.update(over)
    return scan_stream(root, **kwargs)


class TestScan:
    def test_latest_acquired_wins_per_key(self, tmp_path):
        root = str(tmp_path)
        _write(root, "acq-0001", [
            _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0),
            _row("AAPL", "2026-01-05T14:31:00+00:00", 101.0),
        ])
        _write(root, "acq-0002", [
            _row("AAPL", "2026-01-05T14:31:00+00:00", 555.0,
                 acquired="2026-01-07T00:00:00+00:00"),
        ])
        records = _scan(root)
        assert len(records) == 2  # superseded, not duplicated
        assert [r["close"] for r in records] == [100.0, 555.0]

    def test_ts_field_flattens_to_epoch_ms_and_orders(self, tmp_path):
        root = str(tmp_path)
        # Written out of order; naive stamps are UTC (parse_utc).
        _write(root, "acq-0001", [
            _row("MSFT", "2026-01-05T14:31:00", 201.0),
            _row("AAPL", "2026-01-05T14:31:00+00:00", 101.0),
            _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0),
        ])
        records = _scan(root)
        assert [(r["symbol"], r["asof_ms"]) for r in records] == [
            ("AAPL", 1767623400000),
            ("AAPL", 1767623460000),
            ("MSFT", 1767623460000),
        ]

    def test_without_ts_field_orders_by_key_fields(self, tmp_path):
        root = str(tmp_path)
        _write(root, "acq-0001", [
            _row("MSFT", "2026-01-05T14:31:00+00:00", 201.0),
            _row("AAPL", "2026-01-05T14:31:00+00:00", 101.0),
        ])
        records = _scan(root, ts_field=None)
        assert [r["symbol"] for r in records] == ["AAPL", "MSFT"]
        assert all("asof_ms" not in r for r in records)

    def test_reads_gzip_members(self, tmp_path):
        plain, gz = str(tmp_path / "plain"), str(tmp_path / "gz")
        rows = [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0),
                _row("AAPL", "2026-01-05T14:31:00+00:00", 101.0)]
        _write(plain, "acq-0001", rows)
        _write(gz, "acq-0001", rows, codec="gzip")
        assert _scan(gz) == _scan(plain)

    def test_refuses_ambiguous_spellings(self, tmp_path):
        root = str(tmp_path)
        rows = [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)]
        _write(root, "acq-0001", rows)
        _write(root, "acq-0001", rows, codec="gzip")
        with pytest.raises(AssetError, match="both"):
            _scan(root)

    def test_missing_source_dir_refuses(self, tmp_path):
        # A typo'd root must not read as an empty store (default-deny).
        with pytest.raises(AssetError, match="observations"):
            _scan(str(tmp_path))

    def test_empty_source_dir_is_truthfully_empty(self, tmp_path):
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "observations", "alpaca"))
        assert _scan(root) == []

    def test_shared_fields_collapse_to_one_canonical_string(self, tmp_path):
        # The memory contract observable: every record's dict keys and
        # every declared shared field value are the SAME object.
        root = str(tmp_path)
        _write(root, "acq-0001", [
            _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0),
            _row("AAPL", "2026-01-05T14:31:00+00:00", 101.0),
        ])
        a, b = _scan(root, shared_fields=("symbol",))
        assert a["symbol"] is b["symbol"]
        for ka, kb in zip(sorted(a, key=str), sorted(b, key=str)):
            assert ka is kb

    def test_row_missing_a_key_field_refuses(self, tmp_path):
        root = str(tmp_path)
        bad = _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)
        del bad["data"]["symbol"]
        _write(root, "acq-0001", [bad])
        with pytest.raises(AssetError, match="symbol"):
            _scan(root)

    def test_bad_json_line_refuses_by_path_and_line(self, tmp_path):
        root = str(tmp_path)
        path = _write(root, "acq-0001",
                      [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        with pytest.raises(AssetError, match=r":2"):
            _scan(root)

    def test_unparseable_ts_refuses(self, tmp_path):
        root = str(tmp_path)
        _write(root, "acq-0001", [_row("AAPL", "not-a-stamp", 100.0)])
        with pytest.raises(AssetError, match="not-a-stamp"):
            _scan(root)

    def test_ts_out_collision_refuses(self, tmp_path):
        root = str(tmp_path)
        row = _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)
        row["data"]["asof_ms"] = 1  # would be silently clobbered
        _write(root, "acq-0001", [row])
        with pytest.raises(AssetError, match="asof_ms"):
            _scan(root)

    def test_param_problems_accumulate(self, tmp_path):
        with pytest.raises(AssetError) as err:
            scan_stream("", "", "", key_fields=())
        text = str(err.value)
        for name in ("root", "source", "stream", "key_fields"):
            assert name in text


class TestDigest:
    def test_matches_the_whole_snapshot_dump(self, tmp_path):
        # FROZEN recipe: sha256(json.dumps(records, sort_keys=True)) —
        # byte parity is what keeps existing callers' identities still.
        root = str(tmp_path)
        _write(root, "acq-0001", [
            _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0),
            _row("MSFT", "2026-01-05T14:30:00+00:00", 200.0),
        ])
        records = _scan(root)
        expected = hashlib.sha256(
            json.dumps(records, sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert stream_digest(records) == expected

    def test_empty_snapshot(self):
        expected = hashlib.sha256(b"[]").hexdigest()
        assert stream_digest([]) == expected


class TestPeak:
    def test_scan_and_digest_hold_one_copy(self, tmp_path):
        """The OOM regression pin (14.3 GB on 2M bars, ADR-0037): scan +
        digest must hold ONE copy of the snapshot — no second records
        list, no whole-snapshot JSON string, no per-row duplicate key
        strings. Budget sits between the single-copy cost and the
        measured multi-copy defect (~1550 B/row at this row shape)."""
        root = str(tmp_path)
        rows = []
        for symbol in ("AAPL", "MSFT"):
            base = 100.0 if symbol == "AAPL" else 200.0
            for i in range(5000):
                ts = f"2026-01-05T{14 + i // 3600:02d}:" \
                     f"{(i // 60) % 60:02d}:{i % 60:02d}+00:00"
                row = _row(symbol, ts, base + (i % 97) / 100.0)
                row["data"].update(open=base, high=base, low=base,
                                   volume=100.0, trade_count=5, vwap=base)
                rows.append(row)
        _write(root, "acq-0001", rows)
        n_rows = len(rows)
        del rows

        tracemalloc.start()
        try:
            records = _scan(root, shared_fields=("symbol",))
            stream_digest(records)
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert len(records) == n_rows
        assert peak / n_rows < 1000, f"peak {peak / n_rows:.0f} B/row"
        assert current / n_rows < 900, f"resident {current / n_rows:.0f} B/row"
