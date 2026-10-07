"""ParquetSeries (ADR-0240): many day-named parquet files of one onboarded stream as a time series.

Fixtures are real acquisitions (``localblobs``) read through ``payload_files``, so the manifest,
the snapshot hash and the verified-bytes rule are the platform's own. Nothing here names a venue:
the files are ticks with an integer epoch-ms instant ``t`` and a float value ``v``.
"""

import ast
import io
import itertools
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, payload_files, run_acquisition  # noqa: E402
from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.conformance import NodeProbe, conformance_suite  # noqa: E402
from dskit.pipeline.libs import parquet_series as pack  # noqa: E402
from dskit.pipeline.libs.parquet_series import (  # noqa: E402
    AGE_COLUMN,
    NODE_KINDS,
    ParquetSeries,
    StreamManifests,
    manifest_entry,
    prior_index,
    register,
    stream_problems,
)
from dskit.pipeline.node import NodeKindRegistry  # noqa: E402

DAY = 86_400_000
SOURCE, STREAM = "tape", "files"
COLUMNS = ["t", "v"]


def ms(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp()) * 1000


def day_of(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def parquet_bytes(rows, time_type=None):
    """One parquet file of ``(t, v)`` rows, in the order given."""
    times = pa.array([r[0] for r in rows], type=time_type or pa.int64())
    table = pa.table({"t": times, "v": pa.array([r[1] for r in rows], type=pa.float64())})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def day_files(rows, template="{day}.parquet"):
    """Group ascending ``(t, v)`` rows by UTC day into ``{relpath: bytes}``, order kept."""
    by_day = {}
    for t, v in rows:
        by_day.setdefault(day_of(t), []).append((t, v))
    return {template.replace("{day}", day): parquet_bytes(part) for day, part in by_day.items()}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        acquire_module, "utc_now",
        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"),
    )


class Store:
    """A tmp onboarding root with one ``localblobs`` source; files are laid, then acquired."""

    def __init__(self, tmp_path):
        self.blobs = tmp_path / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.onboarding = OnboardingRoot.create(str(tmp_path / "ob"))
        self.registry = self.onboarding.registry()
        vid = self.registry.register("source_config", {
            "name": SOURCE, "catalog_source": "x", "connector": "localblobs",
            "config": {"path": str(self.blobs), "as_of": "2026-01-01T00:00:00+00:00"},
        }, origin="test")
        self.registry.transition(vid, "active", origin="test")
        self.root = self.onboarding.root

    def lay(self, files):
        for relpath, body in files.items():
            target = self.blobs / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return self

    def acquire(self):
        run_acquisition(self.onboarding, self.registry, SOURCE, STREAM, "backfill")
        return self

    def file(self, relpath):
        """The acquired payload file (what the reader opens), for tampering."""
        return payload_files(self.root, SOURCE, STREAM)["files"][relpath]


def store_of(tmp_path, rows, extra=None, template="{day}.parquet"):
    return Store(tmp_path).lay({**day_files(rows, template), **(extra or {})}).acquire()


def series(store, template="{day}.parquet", time_column="t", columns=None):
    return ParquetSeries(store.root, SOURCE, STREAM, template, time_column, COLUMNS if columns is None else columns)


T0 = ms(2026, 9, 2, 1, 0, 0)


# -- construction ------------------------------------------------------------------------


def test_days_lists_only_the_files_the_template_names(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0), (T0 + DAY, 2.0)], extra={"_raw/2026-09-02.zip": b"not parquet"})
    assert series(store).days() == ["2026-09-02", "2026-09-03"], "the raw zip is not a day file"


def test_a_template_with_a_directory_is_matched_on_the_whole_relpath(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)], template="ticks/{day}.parquet",
                     extra={"other/2026-09-02.parquet": b"not read"})
    assert series(store, template="ticks/{day}.parquet").days() == ["2026-09-02"]


@pytest.mark.parametrize("template, why", [
    ("bars.parquet", "no day"), ("{day}-{day}.parquet", "two days"), ("{day}-{x}.parquet", "another placeholder"),
    ("{day}.parquet}", "a stray brace"), ("", "empty"),
])
def test_a_template_must_carry_exactly_one_day_and_no_other_brace(tmp_path, template, why):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match=r"\{day\}"):
        series(store, template=template)


@pytest.mark.parametrize("columns, match", [
    (["t", "t"], "duplicate"), (["v"], "time_column"), ([], "columns"), ("tv", "columns"),
    (["t", "v", AGE_COLUMN], AGE_COLUMN), (["t", 5], "columns"),
])
def test_the_columns_and_time_column_are_checked_before_anything_is_read(tmp_path, columns, match):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match=match):
        series(store, columns=columns)


def test_a_file_the_template_matches_but_no_calendar_day_names_is_refused_not_ignored(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)], extra={"2026-13-45.parquet": parquet_bytes([(T0, 1.0)])})
    with pytest.raises(ValueError, match="2026-13-45.parquet"):
        series(store)


def test_a_stream_the_store_lacks_is_refused_by_the_platform(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(Exception, match="nope"):
        ParquetSeries(store.root, SOURCE, "nope", "{day}.parquet", "t", COLUMNS)


# -- load_span ---------------------------------------------------------------------------


def test_load_span_returns_whole_day_files_ascending_in_native_dtypes(tmp_path):
    rows = [(ms(2026, 9, 1, 23, 0, 0) + 60_000 * k, float(k)) for k in range(180)]  # crosses midnight
    store = store_of(tmp_path, rows)
    got = series(store).load_span(rows[0][0], rows[0][0] + 3_600_000)
    assert got["t"].dtype == np.int64 and got["v"].dtype == np.float64
    assert len(got["t"]) == 180, "whole day files, not a cut at the span bounds"
    assert np.all(np.diff(got["t"]) > 0) and got["v"][0] == 0.0
    only_second_day = series(store).load_span(ms(2026, 9, 2, 0, 30), ms(2026, 9, 2, 1, 0))
    assert len(only_second_day["t"]) == 120, "the first day is not read"


def test_a_span_over_days_the_store_lacks_is_empty_not_an_error(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    got = series(store).load_span(ms(2026, 9, 9), ms(2026, 9, 10))
    assert len(got["t"]) == 0 and set(got) == {"t", "v"}


def test_a_huge_span_costs_the_files_that_exist_not_the_days_it_covers(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    got = series(store).load_span(0, 1 << 60)
    assert list(got["v"]) == [1.0]


@pytest.mark.parametrize("first, last", [(5, 4), (1.5, 9), (True, 9), ("1", 9), (None, 9)])
def test_load_span_refuses_bounds_that_are_not_ordered_integers(tmp_path, first, last):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match="first_ms"):
        series(store).load_span(first, last)


def test_load_span_accepts_numpy_integers(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    assert len(series(store).load_span(np.int64(T0), np.int64(T0))["t"]) == 1


# -- verified bytes, shape and order ---------------------------------------------------------


def test_each_file_is_verified_against_the_manifest_before_it_is_parsed(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0), (T0 + DAY, 2.0)])
    victim = store.file("2026-09-03.parquet")
    victim.write_bytes(victim.read_bytes() + b"\0")
    with pytest.raises(ValueError, match=r"2026-09-03\.parquet.*manifest"):
        series(store).load_span(T0, T0 + DAY)


def test_a_replacement_that_is_valid_parquet_still_refuses(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    store.file("2026-09-02.parquet").write_bytes(parquet_bytes([(T0, 9.0)]))
    with pytest.raises(ValueError, match="manifest"):
        series(store).load_span(T0, T0)


def test_a_file_the_manifest_lists_but_that_is_not_parquet_is_refused_by_name(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)], extra={"2026-09-03.parquet": b"definitely not parquet"})
    with pytest.raises(ValueError, match=r"2026-09-03\.parquet"):
        series(store).load_span(T0, T0 + DAY)


def test_a_declared_column_the_file_lacks_is_refused_by_name(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match="no_such_column"):
        series(store, columns=["t", "no_such_column"]).load_span(T0, T0)


def test_instants_that_run_backwards_inside_a_file_are_refused(tmp_path):
    store = store_of(tmp_path, [(T0 + 2000, 1.0), (T0 + 1000, 2.0)])
    with pytest.raises(ValueError, match=r"2026-09-02\.parquet.*not ascending"):
        series(store).load_span(T0, T0)


def test_duplicate_instants_are_allowed(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0), (T0, 2.0), (T0 + 1, 3.0)])
    assert list(series(store).load_span(T0, T0)["v"]) == [1.0, 2.0, 3.0]


def test_a_row_outside_the_day_its_file_names_is_refused(tmp_path):
    spill = parquet_bytes([(T0, 1.0), (T0 + DAY, 2.0)])  # the second row belongs to the next day
    store = Store(tmp_path).lay({"2026-09-02.parquet": spill}).acquire()
    with pytest.raises(ValueError, match=r"2026-09-02\.parquet.*UTC day"):
        series(store).load_span(T0, T0)


def test_a_time_column_that_is_not_integer_epoch_ms_is_refused(tmp_path):
    floats = Store(tmp_path / "f").lay({"2026-09-02.parquet": parquet_bytes([(float(T0), 1.0)], pa.float64())}).acquire()
    with pytest.raises(ValueError, match="integer"):
        series(floats).load_span(T0, T0)
    stamps = Store(tmp_path / "s").lay({"2026-09-02.parquet": parquet_bytes([(T0, 1.0)], pa.timestamp("ms"))}).acquire()
    with pytest.raises(ValueError, match="integer"):
        series(stamps).load_span(T0, T0)


def test_a_missing_instant_is_refused_not_read_as_a_float(tmp_path):
    body = parquet_bytes([(T0, 1.0), (None, 2.0)])
    store = Store(tmp_path).lay({"2026-09-02.parquet": body}).acquire()
    with pytest.raises(ValueError, match="integer"):
        series(store).load_span(T0, T0)


def test_an_empty_file_is_a_day_with_no_rows(tmp_path):
    store = Store(tmp_path).lay({"2026-09-02.parquet": parquet_bytes([])}).acquire()
    assert len(series(store).load_span(T0, T0)["t"]) == 0
    got = series(store).prior(np.array([T0]), 1000)
    assert np.isnan(got["v"][0])


# -- prior_index: the one home of the strictly-before rule -------------------------------------


def test_prior_index_is_strictly_before_the_instant():
    times = np.array([10, 20, 30])
    assert list(prior_index(times, np.array([5, 10, 11, 20, 21, 31]))) == [-1, -1, 0, 0, 1, 2]


def test_prior_index_answers_the_last_of_equal_instants_and_an_empty_series():
    times = np.array([10, 20, 20, 20, 30])
    assert list(prior_index(times, np.array([20, 21, 31]))) == [0, 3, 4]
    assert list(prior_index(np.array([], dtype=np.int64), np.array([1, 2]))) == [-1, -1]


def test_prior_index_works_on_any_query_order_and_does_not_mutate_its_inputs():
    times, instants = np.array([10, 20, 30]), np.array([31, 5, 21])
    before = instants.copy()
    assert list(prior_index(times, instants)) == [2, -1, 1]
    assert (instants == before).all()


# -- prior -------------------------------------------------------------------------------------


def second_rows(first, count, base=50.0):
    return [(first + 1000 * k, base + k) for k in range(count)]


def test_prior_takes_the_last_row_strictly_before_each_instant_with_its_age(tmp_path):
    rows = [(T0 + 1000 * k, 50.0 + k) for k in range(-5, 6)]  # one row a second around T0
    store = store_of(tmp_path, rows)
    got = series(store).prior(np.array([T0]), max_age_ms=5000)
    assert got["v"][0] == 49.0, "the row AT the instant (value 50) is not yet known"
    assert got[AGE_COLUMN][0] == 1000 and got["t"][0] == T0 - 1000
    got = series(store).prior(np.array([T0 + 1]), max_age_ms=5000)
    assert got["v"][0] == 50.0 and got[AGE_COLUMN][0] == 1


def test_a_row_older_than_the_age_cap_is_missing_not_stale(tmp_path):
    store = store_of(tmp_path, [(T0 - 61_000, 50.0)])
    got = series(store).prior(np.array([T0]), max_age_ms=60_000)
    assert np.isnan(got["v"][0]) and np.isnan(got[AGE_COLUMN][0]) and np.isnan(got["t"][0])
    assert series(store).prior(np.array([T0]), max_age_ms=61_000)["v"][0] == 50.0


def test_the_age_cap_is_inclusive_to_the_millisecond(tmp_path):
    store = store_of(tmp_path, [(T0 - 60_000, 50.0)])
    at_cap = series(store).prior(np.array([T0]), max_age_ms=60_000)
    assert at_cap["v"][0] == 50.0 and at_cap[AGE_COLUMN][0] == 60_000
    assert np.isnan(series(store).prior(np.array([T0 + 1]), max_age_ms=60_000)["v"][0])


def test_a_zero_cap_answers_nothing_because_a_strictly_prior_row_is_at_least_a_millisecond_old(tmp_path):
    store = store_of(tmp_path, [(T0 - 1, 50.0)])
    assert np.isnan(series(store).prior(np.array([T0]), max_age_ms=0)["v"][0])


def test_prior_crosses_midnight_and_does_not_reach_across_a_missing_day(tmp_path):
    midnight = ms(2026, 9, 2, 0, 0, 0)
    store = store_of(tmp_path, [(midnight - 3 * DAY, 40.0), (midnight - 1000, 51.0)])
    got = series(store).prior(np.array([midnight + 500, midnight + DAY]), max_age_ms=5000)
    assert got["v"][0] == 51.0 and got[AGE_COLUMN][0] == 1500
    assert np.isnan(got["v"][1]), "a day with no file contributes nothing; the cap decides the reach"


def test_a_cap_longer_than_a_day_reaches_over_a_missing_day_and_reports_the_age(tmp_path):
    midnight = ms(2026, 9, 2, 0, 0, 0)
    store = store_of(tmp_path, [(midnight - 1000, 51.0)])
    got = series(store).prior(np.array([midnight + 2 * DAY]), max_age_ms=3 * DAY)
    assert got["v"][0] == 51.0 and got[AGE_COLUMN][0] == 2 * DAY + 1000


def test_prior_answers_many_instants_in_any_order_aligned_to_the_input(tmp_path):
    store = store_of(tmp_path, [(T0 + 1000 * k, float(k)) for k in range(100)])
    instants = np.array([T0 + 50_500, T0 + 10_500, T0 + 99_999, T0 + 10_500])
    got = series(store).prior(instants, max_age_ms=10_000)
    assert list(got["v"]) == [50.0, 10.0, 99.0, 10.0]
    assert list(instants) == [T0 + 50_500, T0 + 10_500, T0 + 99_999, T0 + 10_500], "the input is not reordered"


def test_prior_accepts_a_plain_list_and_an_empty_query(tmp_path):
    store = store_of(tmp_path, [(T0 - 1000, 7.0)])
    assert series(store).prior([T0], 5000)["v"][0] == 7.0
    got = series(store).prior([], 5000)
    assert all(len(got[c]) == 0 for c in ("t", "v", AGE_COLUMN))


@pytest.mark.parametrize("instants", [[1.5], np.array([1.0]), np.array([["1"]]), np.array([[1, 2]]), 5, [True]])
def test_instants_must_be_a_one_dimensional_integer_array(tmp_path, instants):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match="instants_ms"):
        series(store).prior(instants, 1000)


@pytest.mark.parametrize("cap", [-1, 1.5, True, "5", None, 1 << 63])
def test_the_cap_must_be_a_non_negative_integer_within_range(tmp_path, cap):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match="max_age_ms"):
        series(store).prior(np.array([T0]), cap)


def test_an_instant_outside_the_representable_range_is_refused_not_wrapped(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match="instants_ms"):
        series(store).prior(np.array([np.iinfo(np.int64).min + 5]), 1000)


def test_prior_needs_numeric_columns_and_says_which(tmp_path):
    table = pa.table({"t": pa.array([T0], pa.int64()), "tag": pa.array(["a"])})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    store = Store(tmp_path).lay({"2026-09-02.parquet": buffer.getvalue()}).acquire()
    reader = series(store, columns=["t", "tag"])
    assert list(reader.load_span(T0, T0)["tag"]) == ["a"], "load_span returns any column as stored"
    with pytest.raises(ValueError, match="tag"):
        reader.prior(np.array([T0 + 1]), 1000)


def test_only_the_days_an_age_cap_can_reach_are_read(tmp_path):
    rows = [(ms(2026, 9, d, 12, 0, 0), float(d)) for d in (1, 2, 3)]
    store = store_of(tmp_path, rows)
    victim = store.file("2026-09-01.parquet")
    victim.write_bytes(victim.read_bytes() + b"\0")
    noon = ms(2026, 9, 3, 12, 0, 1)
    assert series(store).prior(np.array([noon]), 3_600_000)["v"][0] == 3.0, "day 1 is out of reach, so unread"
    with pytest.raises(ValueError, match=r"2026-09-01\.parquet.*manifest"):
        series(store).prior(np.array([noon]), 3 * DAY)


def tamper(store, day):
    victim = store.file(f"{day}.parquet")
    victim.write_bytes(victim.read_bytes() + b"\0")


def test_the_cap_reaches_back_exactly_as_far_as_the_day_it_lands_in(tmp_path):
    midnight = ms(2026, 9, 3, 0, 0, 0)
    store = store_of(tmp_path, [(midnight - 5000, 1.0), (midnight + 1000, 2.0)])
    tamper(store, "2026-09-02")
    half_past = midnight + 1_800_000
    assert series(store).prior(np.array([half_past]), 1_800_000)["v"][0] == 2.0, "the cap stops at midnight"
    with pytest.raises(ValueError, match=r"2026-09-02\.parquet"):
        series(store).prior(np.array([half_past]), 1_800_001)  # one millisecond more reaches the day before


def test_an_instant_at_the_first_millisecond_of_a_day_does_not_read_that_day(tmp_path):
    midnight = ms(2026, 9, 3, 0, 0, 0)
    store = store_of(tmp_path, [(midnight - 5000, 1.0), (midnight + 1000, 2.0)])
    tamper(store, "2026-09-03")
    assert series(store).prior(np.array([midnight]), 5000)["v"][0] == 1.0, "no row of that day is before it"
    with pytest.raises(ValueError, match=r"2026-09-03\.parquet"):
        series(store).prior(np.array([midnight + 1]), 5000)


def test_a_drifted_file_refuses_prior_too(tmp_path):
    store = store_of(tmp_path, [(T0 - 1000, 7.0)])
    victim = store.file("2026-09-02.parquet")
    victim.write_bytes(victim.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="manifest"):
        series(store).prior(np.array([T0]), 5000)


def brute_force(rows, instant, cap):
    """The last row (in file order) with time < instant and age <= cap, or None."""
    best = None
    for t, v in rows:
        if t < instant and instant - t <= cap:
            best = (t, v)
    return best


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_prior_agrees_with_a_brute_force_scan_on_random_data_gaps_and_duplicates(tmp_path, seed):
    rng = random.Random(seed)
    base = ms(2026, 9, 1, 0, 0, 0)
    times = sorted(base + rng.randrange(0, 5 * DAY) for _ in range(300))
    times += [times[10], times[10], times[200]]  # duplicated instants
    times.sort()
    rows = [(t, float(i)) for i, t in enumerate(times) if day_of(t) != "2026-09-03"]  # a missing day
    store = store_of(tmp_path, rows)
    reader = series(store)
    instants = [base + rng.randrange(-DAY // 2, 5 * DAY + DAY // 2) for _ in range(60)]
    instants += [t for t, _ in rows[::25]] + [base + k * DAY for k in range(7)] + [times[10] + 1]
    for cap in (0, 1, 999, 60_000, 3_600_000, DAY, DAY + 1, 2 * DAY, 4 * DAY):
        got = reader.prior(np.array(instants), cap)
        for position, instant in enumerate(instants):
            want = brute_force(rows, instant, cap)
            if want is None:
                assert np.isnan(got["v"][position]) and np.isnan(got[AGE_COLUMN][position]), (cap, instant)
            else:
                assert (got["t"][position], got["v"][position]) == want, (cap, instant)
                assert got[AGE_COLUMN][position] == instant - want[0]


# -- the manifest ------------------------------------------------------------------------------


def test_manifest_names_the_snapshot_being_read(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)], extra={"_raw/x.zip": b"zip"})
    entry = series(store).manifest()
    assert set(entry) == {"source", "stream", "snapshot", "manifest_sha256", "files"}
    assert (entry["source"], entry["stream"], entry["files"]) == (SOURCE, STREAM, 2)
    assert len(entry["manifest_sha256"]) == 64
    assert entry == manifest_entry(payload_files(store.root, SOURCE, STREAM))
    entry["files"] = 99
    assert series(store).manifest()["files"] == 2, "a copy, not the held dict"


def test_require_manifest_passes_what_was_fingerprinted_and_refuses_a_store_that_moved(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    held = series(store).manifest()
    series(store).require_manifest(held)  # no error
    store.lay(day_files([(T0 + DAY, 2.0)])).acquire()
    with pytest.raises(ValueError, match="moved"):
        series(store).require_manifest(held)


def test_require_manifest_refuses_another_stream_a_missing_entry_and_a_malformed_one(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    reader = series(store)
    held = reader.manifest()
    with pytest.raises(ValueError, match="other"):
        reader.require_manifest({**held, "source": "other"}, label="other-tape")
    for bad in (None, "x", {}, {**held, "manifest_sha256": 5}):
        with pytest.raises(ValueError, match="manifest"):
            reader.require_manifest(bad)


def test_stream_problems_wants_exactly_two_nonempty_strings():
    assert stream_problems("s", {"source": "a", "stream": "b"}) == []
    for bad in (None, "x", {}, {"source": "a"}, {"source": "a", "stream": ""}, {"source": "a", "stream": 3},
                {"source": "a", "stream": "b", "extra": "c"}):
        assert len(stream_problems("streams['x']", bad)) == 1
        assert "streams['x']" in stream_problems("streams['x']", bad)[0]


# -- StreamManifests ---------------------------------------------------------------------------


def manifests_node(store, **streams):
    spec = streams or {"tape": {"source": SOURCE, "stream": STREAM}}
    return StreamManifests("manifests", {"root": store.root, "streams": spec})


def test_the_manifest_node_emits_each_streams_entry_and_fingerprints_the_snapshot_hashes(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    node = manifests_node(store)
    out = node.run(None, {})
    entry = out["manifests"]["tape"]
    assert entry == series(store).manifest()
    assert node.fingerprint() == {"kind": "StreamManifests", "manifests": {"tape": entry["manifest_sha256"]}}
    assert node.role == "data" and node.outputs == ("manifests",)


def test_the_fingerprint_moves_with_the_store(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    before = manifests_node(store).fingerprint()
    store.lay(day_files([(T0 + DAY, 2.0)])).acquire()
    assert manifests_node(store).fingerprint() != before, "a changed store moves the run identity"


def test_resolve_and_execute_see_one_snapshot_per_instance(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    node = manifests_node(store)
    fingerprinted = node.fingerprint()["manifests"]["tape"]
    store.lay(day_files([(T0 + DAY, 2.0)])).acquire()  # the store moves between resolve and execute
    assert node.run(None, {})["manifests"]["tape"]["manifest_sha256"] == fingerprinted


def test_the_run_output_is_a_copy_the_next_run_cannot_corrupt(tmp_path):
    node = manifests_node(store_of(tmp_path, [(T0, 1.0)]))
    first = node.run(None, {})
    first["manifests"]["tape"]["files"] = 99
    assert node.run(None, {})["manifests"]["tape"]["files"] == 1


def test_a_stream_that_is_not_in_the_store_refuses_the_run(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    node = manifests_node(store, tape={"source": "no-such-source", "stream": STREAM})
    with pytest.raises(Exception, match="no-such-source"):
        node.fingerprint()


@pytest.mark.parametrize("params, match", [
    ({"root": "r", "streams": {}, "surprise": 1}, "surprise"),
    ({"streams": {}}, "root"), ({"root": "", "streams": {}}, "root"), ({"root": 5, "streams": {}}, "root"),
    ({"root": "r"}, "streams"), ({"root": "r", "streams": []}, "streams"),
    ({"root": "r", "streams": {"x": {"source": "a"}}}, "streams"),
])
def test_the_manifest_node_is_default_deny_and_total(params, match):
    with pytest.raises(ConfigError, match=match):
        StreamManifests("m", params)


def test_the_kind_is_named_by_the_pack_and_claimed_only_by_register():
    assert NODE_KINDS == (("stream-manifests", StreamManifests),)
    registry = NodeKindRegistry()
    assert "stream-manifests" not in registry
    register(registry)
    register(registry)  # idempotent
    assert registry.get("stream-manifests")[0] is StreamManifests


def test_register_does_not_displace_a_kind_already_claimed():
    class Mine(StreamManifests):
        pass

    registry = NodeKindRegistry()
    registry.register("stream-manifests", Mine)
    register(registry)
    assert registry.get("stream-manifests")[0] is Mine


# -- the platform bar ---------------------------------------------------------------------------


_CONFORMANCE_STORES = {}


def _conformance_store(tmp_path):
    """One store per test directory: the suite asks for the probes several times."""
    key = str(tmp_path)
    if key not in _CONFORMANCE_STORES:
        _CONFORMANCE_STORES[key] = store_of(tmp_path / "conf", [(T0, 1.0), (T0 + 1000, 2.0)])
    return _CONFORMANCE_STORES[key]


def _probes(tmp_path):
    store = _conformance_store(tmp_path)
    params = {"root": store.root, "streams": {"tape": {"source": SOURCE, "stream": STREAM}}}

    def move():
        """Rewrite the one day file's content in place, same shape."""
        store.lay(day_files([(T0, 7.0), (T0 + 1000, 8.0)])).acquire()

    def grow():
        """Land a new day file."""
        store.lay(day_files([(T0 + DAY, 3.0)])).acquire()

    return {"stream-manifests": NodeProbe(
        params=params, required=("root", "streams"), inputs={}, stream_ports=(), runnable=True,
        make=lambda: StreamManifests("manifests", params), move=move, grow=grow,
        size=lambda out: sum(entry["files"] for entry in out["manifests"].values()),
    )}


TestStreamManifestsConformance = conformance_suite(
    registry=NODE_KINDS, module="dskit.pipeline.libs.parquet_series", probes=_probes,
    expected_roles={"stream-manifests": "data"}, name="TestStreamManifestsConformance",
)


# -- end to end through the driver ----------------------------------------------------------------


def _document(store, tmp_path, note):
    from dskit.pipeline.document import PipelineDocument

    return PipelineDocument.from_obj({
        "name": "tape-manifest",
        "outputs": {"run_root": str(tmp_path / "runs")},
        "pipeline": {"streams": {
            "uses": "dskit.pipeline.libs.parquet_series:StreamManifests",
            "params": {"root": store.root, "streams": {"tape": {"source": SOURCE, "stream": STREAM}}},
            "notes": note,
        }},
    })


def test_a_run_through_the_driver_changes_identity_when_the_store_moves(tmp_path):
    from dskit.pipeline.driver import run_document

    store = store_of(tmp_path, [(T0, 1.0)])
    first = run_document(_document(store, tmp_path, "one"), asof="2026-10-06", journal=False)
    again = _document(store, tmp_path, "notes never change identity")
    store.lay(day_files([(T0 + DAY, 2.0)])).acquire()
    second = run_document(again, asof="2026-10-06", journal=False)
    assert first.state == second.state == "ran"
    assert first.run_hash != second.run_hash, "the same document over a moved store is a different run"
    out = second.outputs["streams"]["manifests"]["tape"]
    assert out["manifest_sha256"] == payload_files(store.root, SOURCE, STREAM)["manifest_sha256"]


# -- the tier-2 rules, pinned for this module -------------------------------------------------------


def _top_level_import_modules():
    tree = ast.parse(Path(pack.__file__).read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def test_numpy_pyarrow_and_onboarding_are_imported_only_inside_methods():
    heavy = [m for m in _top_level_import_modules() if m.split(".")[0] in {"numpy", "pyarrow", "dskit"} and
             not m.startswith("dskit.pipeline")]
    assert heavy == []


def test_importing_the_module_pulls_in_neither_the_libraries_nor_onboarding():
    import subprocess

    script = ("import sys; import dskit.pipeline.libs.parquet_series as m; "
              "bad = [n for n in ('numpy', 'pyarrow', 'dskit.onboarding') if n in sys.modules]; "
              "print(bad); raise SystemExit(1 if bad else 0)")
    repo = str(Path(pack.__file__).resolve().parents[3])  # import THIS tree, whatever the caller's cwd
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, cwd=repo)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_public_surface_is_declared_and_no_underscore_name_leaks():
    assert sorted(pack.__all__) == sorted([
        "AGE_COLUMN", "NODE_KINDS", "ParquetSeries", "StreamManifests", "manifest_entry",
        "prior_index", "register", "stream_problems"])
    assert not [n for n in pack.__all__ if n.startswith("_")]
