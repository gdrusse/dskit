"""observations.py: the read seam (ADR-0037) — bitemporal dedup,
codec-aware resolution, loud refusals, digest parity, and the
single-copy memory contract."""

import hashlib
import json
import os
import tracemalloc
from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding.codec import open_text_writer
from dskit.onboarding.observations import (
    scan_stream,
    stream_digest,
    stream_dir,
)

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

    def test_glob_metacharacters_in_root_still_read(self, tmp_path):
        # Adversarial finding: a well-formed dir name containing glob
        # metacharacters must not silently scan as an empty store.
        root = str(tmp_path / "data [archive 2026]")
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        assert len(_scan(root)) == 1

    def test_equal_acquired_at_identical_data_dedups(self, tmp_path):
        # At-least-once re-pulls: the same row re-acquired at the same
        # instant with the same content is a duplicate, not a conflict.
        root = str(tmp_path)
        row = _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)
        _write(root, "acq-0001", [row])
        _write(root, "acq-0002", [row])
        assert len(_scan(root)) == 1

    def test_equal_acquired_at_differing_data_refuses(self, tmp_path):
        # A tie with DIFFERENT content AT THE WINNING acquired_at is
        # ambiguous — refusing makes the winner scan-order-independent
        # (no directory ordering may pick silently).
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        _write(root, "acq-0002",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 555.0)])
        with pytest.raises(AssetError, match="acquired_at"):
            _scan(root)

    def test_superseded_tie_does_not_refuse(self, tmp_path):
        # A conflict at an OLDER acquired_at is history, not ambiguity:
        # a later corrective acquisition decides the key, in EVERY
        # directory arrangement — the round-2 adversarial finding was
        # that the tie fired against the running max, bricking a stream
        # no future acquisition could ever repair.
        t1, t2 = "2026-01-06T00:00:00+00:00", "2026-01-07T00:00:00+00:00"
        ts = "2026-01-05T14:30:00+00:00"
        for i, arrangement in enumerate((
            # tie pair scans BEFORE the winner
            (("acq-0001", 100.0, t1), ("acq-0002", 100.5, t1),
             ("acq-0003", 101.0, t2)),
            # winner scans FIRST
            (("acq-0001", 101.0, t2), ("acq-0002", 100.0, t1),
             ("acq-0003", 100.5, t1)),
        )):
            root = str(tmp_path / f"case-{i}")
            for acq, close, acquired in arrangement:
                _write(root, acq, [_row("AAPL", ts, close,
                                        acquired=acquired)])
            records = _scan(root)
            assert [r["close"] for r in records] == [101.0]

    def test_type_respelled_tie_refuses(self, tmp_path):
        # Python == coerces 100 == 100.0 == True; such a tie must refuse
        # (they serialize differently, so a quiet dedup would emit a
        # scan-order-picked value and digest).
        ts = "2026-01-05T14:30:00+00:00"
        for a, b in ((100, 100.0), (1, True)):
            root = str(tmp_path / f"case-{a!r}-{b!r}")
            row_a = _row("AAPL", ts, a)
            row_b = _row("AAPL", ts, b)
            _write(root, "acq-0001", [row_a])
            _write(root, "acq-0002", [row_b])
            with pytest.raises(AssetError, match="acquired_at"):
                _scan(root)

    def test_nan_key_field_refuses(self, tmp_path):
        # NaN breaks total order WITHOUT raising — Timsort silently
        # falls back to scan order, making record order and digest
        # arrangement-dependent. Refuse at intake, by path and line.
        root = str(tmp_path)
        row = _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)
        row["data"]["symbol"] = float("nan")
        _write(root, "acq-0001", [row])
        with pytest.raises(AssetError, match="NaN"):
            _scan(root)

    def test_superseded_ties_on_mixed_type_keys_scan_clean(self, tmp_path):
        # Round-3 finding: the conflicts adjudication sorted RAW key
        # tuples before the winning-level filter, so heterogeneous key
        # types crashed a store whose winners are unambiguous.
        t1, t2 = "2026-01-06T00:00:00+00:00", "2026-01-07T00:00:00+00:00"
        root = str(tmp_path)
        for i, symbol in enumerate((1, "a")):
            ts = f"2026-01-05T14:3{i}:00+00:00"
            _write(root, f"acq-000{3 * i + 1}",
                   [_row(symbol, ts, 100.0, acquired=t1)])
            _write(root, f"acq-000{3 * i + 2}",
                   [_row(symbol, ts, 100.5, acquired=t1)])
            _write(root, f"acq-000{3 * i + 3}",
                   [_row(symbol, ts, 101.0, acquired=t2)])
        records = _scan(root)
        assert [r["close"] for r in records] == [101.0, 101.0]

    def test_winning_ties_on_mixed_type_keys_accumulate(self, tmp_path):
        # Winning-level conflicts on heterogeneous keys must refuse as
        # ONE AssetError naming every key — never a raw TypeError.
        t1 = "2026-01-06T00:00:00+00:00"
        root = str(tmp_path)
        for i, symbol in enumerate((1, "a")):
            ts = f"2026-01-05T14:3{i}:00+00:00"
            _write(root, f"acq-000{2 * i + 1}",
                   [_row(symbol, ts, 100.0, acquired=t1)])
            _write(root, f"acq-000{2 * i + 2}",
                   [_row(symbol, ts, 100.5, acquired=t1)])
        with pytest.raises(AssetError) as err:
            _scan(root)
        # Both keys named, both problems accumulated — "1" alone would
        # be satisfied by any path:line material (round-7 hygiene fix).
        text = str(err.value)
        assert "[1," in text and "['a'," in text
        assert text.count("share the winning") == 2

    def test_acquired_at_adjudicates_chronologically(self, tmp_path):
        # "2026-01-06T23:00:00-05:00" is a LATER instant than
        # "2026-01-07T00:00:00+00:00" but string-sorts below it — the
        # winner is the instant, never the spelling.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", ts, 999.0,
                     acquired="2026-01-06T23:00:00-05:00")])
        _write(root, "acq-0002",
               [_row("AAPL", ts, 100.0,
                     acquired="2026-01-07T00:00:00+00:00")])
        records = _scan(root)
        assert [r["close"] for r in records] == [999.0]

    def test_same_instant_acquired_spellings_tie(self, tmp_path):
        # "Z" and "+00:00" are one instant: differing data at that
        # instant has no bitemporal winner — spelling must not dodge
        # the tie refusal.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", ts, 100.0,
                     acquired="2026-01-06T00:00:00Z")])
        _write(root, "acq-0002",
               [_row("AAPL", ts, 555.0,
                     acquired="2026-01-06T00:00:00+00:00")])
        with pytest.raises(AssetError, match="acquired_at"):
            _scan(root)

    def test_unparseable_acquired_at_refuses(self, tmp_path):
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0,
                     acquired="not-a-stamp")])
        with pytest.raises(AssetError, match="not-a-stamp"):
            _scan(root)

    def test_boundary_year_offset_stamps_refuse_typed(self, tmp_path):
        # "9999-12-31T23:00:00-05:00" parses as ISO but its UTC
        # conversion leaves datetime's range — the seam must refuse
        # typed on BOTH stamp paths, never leak a raw OverflowError.
        ts = "2026-01-05T14:30:00+00:00"
        root_a = str(tmp_path / "a")
        _write(root_a, "acq-0001",
               [_row("AAPL", ts, 100.0,
                     acquired="9999-12-31T23:00:00-05:00")])
        with pytest.raises(AssetError, match="9999"):
            _scan(root_a)
        root_b = str(tmp_path / "b")
        _write(root_b, "acq-0001",
               [_row("AAPL", "0001-01-01T00:00:00+05:45", 100.0)])
        with pytest.raises(AssetError, match="0001"):
            _scan(root_b)

    def test_respelled_keys_are_distinct_across_instants(self, tmp_path):
        # Key identity is CANONICAL, not coercing ==: 1, 1.0, and true
        # are three keys — a later acquisition must never silently
        # supersede records it never keyed (round-4 finding).
        t1, t2, t3 = ("2026-01-06T00:00:00+00:00",
                      "2026-01-07T00:00:00+00:00",
                      "2026-01-08T00:00:00+00:00")
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        for acq, ident, close, acquired in (
            ("acq-0001", 1, 100.0, t1),
            ("acq-0002", 1.0, 200.0, t2),
            ("acq-0003", True, 300.0, t3),
        ):
            row = _row("AAPL", ts, close, acquired=acquired)
            row["data"]["ident"] = ident
            _write(root, acq, [row])
        records = _scan(root, key_fields=("ident", "ts"))
        assert sorted(r["close"] for r in records) == [100.0, 200.0, 300.0]

    def test_stream_named_directory_squat_refuses(self, tmp_path):
        # A DIRECTORY squatting the stream spelling at source level must
        # refuse like the file spelling — never scan as an empty acq dir.
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        os.makedirs(os.path.join(root, "observations", "alpaca",
                                 "bars.jsonl"))
        with pytest.raises(AssetError, match="bars.jsonl"):
            _scan(root)

    @pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                        reason="chmod-based denial is inert for root")
    def test_unreadable_acquisition_dir_refuses(self, tmp_path):
        # os.path.isdir/isfile swallow EACCES: a mode-000 acquisition
        # dir silently vanished from the dedup, serving the SUPERSEDED
        # row as winner (round-9 finding). Denial must refuse loudly.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", ts, 100.0,
                     acquired="2026-01-06T00:00:00+00:00")])
        _write(root, "acq-0002",
               [_row("AAPL", ts, 555.0,
                     acquired="2026-01-07T00:00:00+00:00")])
        blocked = os.path.join(root, "observations", "alpaca", "acq-0002")
        os.chmod(blocked, 0)
        try:
            with pytest.raises(AssetError, match="acq-0002"):
                _scan(root)
        finally:
            os.chmod(blocked, 0o755)

    @pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                        reason="chmod-based denial is inert for root")
    def test_untraversable_source_dir_refuses(self, tmp_path):
        # A readable-but-untraversable source dir (mode r--) listed
        # fine but every per-entry stat failed silently — a CORRECT
        # root scanned as an empty store.
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        source_dir = os.path.join(root, "observations", "alpaca")
        os.chmod(source_dir, 0o400)
        try:
            with pytest.raises(AssetError, match="cannot stat"):
                _scan(root)
        finally:
            os.chmod(source_dir, 0o755)

    def test_empty_string_acquired_at_refuses(self, tmp_path):
        # A present-but-EMPTY stamp is writer-impossible and
        # unparseable — it must refuse like every other bad spelling;
        # only true ABSENCE reads as the earliest instant (round-8).
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0,
                     acquired="")])
        with pytest.raises(AssetError, match="acquired_at"):
            _scan(root)

    def test_missing_acquired_at_loses_to_any_stamp(self, tmp_path):
        # An absent acquired_at reads as the earliest possible instant.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        bare = _row("AAPL", ts, 100.0)
        del bare["acquired_at"]
        _write(root, "acq-0001", [bare])
        _write(root, "acq-0002",
               [_row("AAPL", ts, 999.0,
                     acquired="1971-01-01T00:00:00+00:00")])
        records = _scan(root)
        assert [r["close"] for r in records] == [999.0]

    def test_misplaced_gz_stream_spelling_refuses(self, tmp_path):
        # The tamper-shaped refusal covers BOTH spellings.
        root = str(tmp_path)
        directory = os.path.join(root, "observations", "alpaca")
        os.makedirs(directory)
        with open_text_writer(os.path.join(directory, "bars.jsonl.gz"),
                              "gzip") as fh:
            fh.write(json.dumps(
                _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)) + "\n")
        with pytest.raises(AssetError, match="acquisition"):
            _scan(root)

    def test_same_instant_spellings_order_key_determined(self, tmp_path):
        # Two ts spellings of one instant are distinct keys; their order
        # is determined by the ts STRING, never by scan order — the
        # deliberate divergence from the retired stable-sort tiebreak
        # (ADR-0037 review amendments).
        root = str(tmp_path)
        _write(root, "acq-0001", [
            _row("AAPL", "2026-01-05T14:30:00+00:00", 1.0),
            _row("AAPL", "2026-01-05T14:30:00", 2.0),
        ])
        records = _scan(root)
        assert [r["ts"] for r in records] == [
            "2026-01-05T14:30:00", "2026-01-05T14:30:00+00:00",
        ]
        assert len({r["asof_ms"] for r in records}) == 1

    def test_deeply_nested_json_line_refuses_loudly(self, tmp_path):
        # A ~200 KB hostile line of nested arrays must cross the seam as
        # AssetError, never a raw RecursionError.
        root = str(tmp_path)
        path = _write(root, "acq-0001",
                      [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("[" * 100_000 + "]" * 100_000 + "\n")
        with pytest.raises(AssetError, match=r":2"):
            _scan(root)

    def test_zero_byte_gz_member_refuses(self, tmp_path):
        # A written member always carries header + trailer (~20 bytes);
        # 0 bytes is corrupt-shaped (partial copy) — and observations/
        # has no manifest, so this seam is its only detector.
        root = str(tmp_path)
        directory = os.path.join(root, "observations", "alpaca", "acq-0001")
        os.makedirs(directory)
        open(os.path.join(directory, "bars.jsonl.gz"), "wb").close()
        with pytest.raises(AssetError, match="0-byte"):
            _scan(root)

    def test_stream_file_outside_an_acquisition_dir_refuses(self, tmp_path):
        # Tamper-shaped: the writer only ever puts the stream inside an
        # acquisition dir — a misplaced spelling must refuse, not vanish.
        root = str(tmp_path)
        directory = os.path.join(root, "observations", "alpaca")
        os.makedirs(directory)
        with open(os.path.join(directory, "bars.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(
                _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)) + "\n")
        with pytest.raises(AssetError, match="acquisition"):
            _scan(root)

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

    def test_unparseable_ts_refuses_naming_the_key(self, tmp_path):
        root = str(tmp_path)
        _write(root, "acq-0001", [_row("AAPL", "not-a-stamp", 100.0)])
        with pytest.raises(AssetError, match="not-a-stamp"):
            _scan(root)
        with pytest.raises(AssetError, match="AAPL"):
            _scan(root)

    def test_ts_out_collision_refuses(self, tmp_path):
        root = str(tmp_path)
        row = _row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)
        row["data"]["asof_ms"] = 1  # would be silently clobbered
        _write(root, "acq-0001", [row])
        with pytest.raises(AssetError, match="asof_ms"):
            _scan(root)

    def test_epoch_ms_is_exact_integer_arithmetic(self, tmp_path):
        # int(timestamp() * 1000) compounds two float roundings: ~1-2.5%
        # of millisecond-precision stamps landed one ms off in affected
        # decades, with counterexamples from 1970 on (rounds 6-7). The
        # expected stamps are built via exact timedelta arithmetic,
        # independent of the implementation.
        for true_ms in (2163892205864, -2177167649680):
            stamp = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                     + timedelta(milliseconds=true_ms)).isoformat()
            root = str(tmp_path / f"case{abs(true_ms)}")
            _write(root, "acq-0001", [_row("AAPL", stamp, 100.0)])
            records = _scan(root)
            assert records[0]["asof_ms"] == true_ms

    def test_one_ms_apart_stamps_supersede(self, tmp_path):
        # The same float defect collapsed acquired_at stamps a FULL
        # millisecond apart into one instant, spuriously refusing a
        # valid supersede — permanently, since observations/ is
        # append-only.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", ts, 100.0,
                     acquired="2038-10-20T17:12:31.817000+00:00")])
        _write(root, "acq-0002",
               [_row("AAPL", ts, 200.0,
                     acquired="2038-10-20T17:12:31.818000+00:00")])
        records = _scan(root)
        assert [r["close"] for r in records] == [200.0]

    def test_sub_ms_remainders_floor_in_every_era(self, tmp_path):
        # Round-10 mutation evidence: floor->round survived the whole
        # suite. Pin the documented FLOOR: +0.5 ms floors down, and a
        # pre-1970 -0.5 ms floors to -1 (true floor, never
        # trunc-toward-zero).
        exact = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc) \
            - datetime(1970, 1, 1, tzinfo=timezone.utc)
        exact_ms = (exact.days * 86400 + exact.seconds) * 1000
        for stamp, expected in (
            ("2026-01-05T14:30:00.000500+00:00", exact_ms),
            ("1969-12-31T23:59:59.999500+00:00", -1),
        ):
            root = str(tmp_path / stamp.replace(":", "-"))
            _write(root, "acq-0001", [_row("AAPL", stamp, 100.0)])
            records = _scan(root)
            assert records[0]["asof_ms"] == expected

    def test_negative_zero_key_stays_distinct(self, tmp_path):
        # Round-10 mutation evidence: dropping the repr tiebreak let a
        # later 0.0 acquisition silently supersede a -0.0-keyed record.
        # Pin: they are two keys, both emitted.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        for acq, level, close, acquired in (
            ("acq-0001", -0.0, 100.0, "2026-01-06T00:00:00+00:00"),
            ("acq-0002", 0.0, 200.0, "2026-01-07T00:00:00+00:00"),
        ):
            row = _row("AAPL", ts, close, acquired=acquired)
            row["data"]["level"] = level
            _write(root, acq, [row])
        records = _scan(root, key_fields=("level", "ts"))
        assert sorted(r["close"] for r in records) == [100.0, 200.0]
        assert sorted(repr(r["level"]) for r in records) == ["-0.0", "0.0"]

    def test_float_keys_order_numerically(self, tmp_path):
        # Round-5 finding: the ("f", repr) tag sorted float keys as
        # repr STRINGS ([-1.0, -2.0, 10.0, 2.5]) — the order freezes at
        # merge, so it must be numeric (repr as the -0.0/0.0 tiebreak).
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        rows = []
        for level in (2.5, 10.0, -1.0, -2.0):
            row = _row("AAPL", ts, 100.0)
            row["data"]["level"] = level
            rows.append(row)
        _write(root, "acq-0001", rows)
        records = _scan(root, key_fields=("level", "ts"))
        assert [r["level"] for r in records] == [-2.0, -1.0, 2.5, 10.0]

    def test_float_key_refusal_shows_the_float(self, tmp_path):
        # The tie message must show the float 1.0, not the string '1.0'
        # — the type distinction is the point of canonical identity.
        ts = "2026-01-05T14:30:00+00:00"
        root = str(tmp_path)
        for acq, close in (("acq-0001", 100.0), ("acq-0002", 555.0)):
            row = _row("AAPL", ts, close)
            row["data"]["level"] = 1.0
            _write(root, acq, [row])
        with pytest.raises(AssetError, match=r"\[1\.0,"):
            _scan(root, key_fields=("level", "ts"))

    def test_unsafe_source_and_stream_spellings_refuse(self, tmp_path):
        # Round-5 finding: the writer only mints segment-safe names, but
        # the reader accepted any string — path traversal read files
        # OUTSIDE the store, "alpaca/../other" read the wrong source,
        # and writer-impossible typos ("Bars ") scanned silently empty.
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        for source, stream in (
            ("alpaca", "../../../secrets"),
            ("alpaca/../polygon", "bars"),
            ("alpaca", "Bars "),
            ("Alpaca", "bars"),
        ):
            with pytest.raises(AssetError, match="filesystem-safe"):
                _scan(root, source=source, stream=stream)

    def test_zero_byte_plain_member_refuses(self, tmp_path):
        # The committed writer lazy-opens on the first record, so a
        # committed member always has >= 1 line — 0 bytes is a partial
        # copy, corrupt-shaped for the plain spelling exactly as for gz.
        root = str(tmp_path)
        _write(root, "acq-0001",
               [_row("AAPL", "2026-01-05T14:30:00+00:00", 100.0)])
        directory = os.path.join(root, "observations", "alpaca", "acq-0002")
        os.makedirs(directory)
        open(os.path.join(directory, "bars.jsonl"), "w").close()
        with pytest.raises(AssetError, match="0-byte"):
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

    def test_deep_record_refuses_loudly(self):
        # json.dumps recursion on a pathological record must cross the
        # seam as AssetError, never a raw RecursionError.
        deep = current = []
        for _ in range(100_000):
            nxt = []
            current.append(nxt)
            current = nxt
        with pytest.raises(AssetError, match="record 0"):
            stream_digest([{"x": deep}])


class TestPeak:
    def test_scan_and_digest_hold_one_copy(self, tmp_path):
        """The OOM regression pin (14.3 GB on 2M bars, ADR-0037): scan +
        digest must hold ONE copy of the snapshot — no second records
        list, no whole-snapshot JSON string, no per-row duplicate key
        strings. Budget sits between the single-copy cost and the
        measured multi-copy defect (~1550 B/row at this row shape) —
        and tight enough to catch a whole-dump digest regression alone
        (~930 B/row measured), not just the full defect."""
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
        assert peak / n_rows < 800, f"peak {peak / n_rows:.0f} B/row"
        assert current / n_rows < 700, f"resident {current / n_rows:.0f} B/row"


def test_stream_dir_is_where_scan_stream_reads(tmp_path):
    """The read side spells ``observations/<source>`` ONCE.

    A caller memoizing a scan digests this directory to know whether its
    snapshot is stale; if the helper and the reader ever disagreed, the
    cache would be keyed on a tree nobody reads.
    """
    root = str(tmp_path)
    _write(root, "acq-1", [_row("AAPL", "2026-01-05T14:31:00+00:00", 10.0)])
    assert stream_dir(root, "alpaca") == os.path.join(root, "observations", "alpaca")
    assert os.path.isdir(stream_dir(root, "alpaca"))
    with pytest.raises(AssetError, match="no observations directory"):
        scan_stream(root, "nobody", "bars", key_fields=("symbol", "ts"))
