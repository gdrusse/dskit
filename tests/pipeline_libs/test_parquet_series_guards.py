"""ParquetSeries guard pins (ADR-0243): each ordering, range and window rule has a case that only IT refuses or answers.

``test_parquet_series.py`` pins the contract; a mutation pass over ``parquet_series.py`` found 22 single-token changes in
these guards that every test still passed (a weaker ordering check, a loosened bound, a window cut a day early). Each case
here is a file, a query or a boundary that one of them gets wrong, so a regression of the guard is a failing test. The
fixtures are the contract file's own helpers (real ``localblobs`` acquisitions, ticks with an integer ``t`` and float ``v``).
"""

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")

from .test_parquet_series import (  # noqa: E402,F401  (clock is an autouse fixture: importing it applies it here)
    AGE_COLUMN,
    DAY,
    T0,
    Store,
    clock,
    ms,
    parquet_bytes,
    series,
    store_of,
)

LIMIT = 1 << 62  # the module's _LIMIT_MS: instants and ages stay strictly inside it, so instant - age never wraps
MIDNIGHT = ms(2026, 9, 3, 0, 0, 0)
FILE = r"^2026-09-02\.parquet: "  # a refusal opens with the file's relpath (anchored: the absolute path also contains it)


def lone(tmp_path, body):
    """A store whose only file is the day file ``2026-09-02.parquet`` holding ``body`` bytes."""
    return Store(tmp_path).lay({"2026-09-02.parquet": body}).acquire()


# -- ascending order: any one step backwards refuses, wherever it sits ------------------------------


@pytest.mark.parametrize("order", [
    pytest.param([1000, 3000, 2000], id="last-step"),
    pytest.param([3000, 1000, 2000], id="first-step"),
    pytest.param([1000, 4000, 2000, 5000], id="middle-step"),
    pytest.param([1000, 2000, 3000, 2999], id="one-ms-back"),
])
def test_a_file_that_steps_backwards_anywhere_is_refused_not_only_one_that_is_reversed_throughout(tmp_path, order):
    store = lone(tmp_path, parquet_bytes([(T0 + k, 1.0) for k in order]))
    with pytest.raises(ValueError, match=FILE + "t is not ascending"):
        series(store).load_span(T0, T0)


def test_a_partly_unsorted_day_never_answers_prior_from_the_wrong_row(tmp_path):
    """The shipped code refuses [T0, T0+3000, T0+2000]; a check that let it through would answer prior(T0+2500) with T0's row."""
    store = lone(tmp_path, parquet_bytes([(T0, 1.0), (T0 + 3000, 2.0), (T0 + 2000, 3.0)]))
    with pytest.raises(ValueError, match="not ascending"):
        series(store).prior(np.array([T0 + 2500]), 10_000)


def test_ties_and_a_strictly_ascending_day_are_accepted_so_the_guard_is_not_simply_always_on(tmp_path):
    store = lone(tmp_path, parquet_bytes([(T0, 1.0), (T0, 2.0), (T0 + 1, 3.0), (T0 + 5, 4.0)]))
    assert list(series(store).load_span(T0, T0)["v"]) == [1.0, 2.0, 3.0, 4.0]


# -- the UTC day a file names: first and last millisecond in, the neighbours out --------------------


def test_the_first_and_last_millisecond_of_the_day_are_inside_it(tmp_path):
    first = ms(2026, 9, 2, 0, 0, 0)
    store = lone(tmp_path, parquet_bytes([(first, 1.0), (first + DAY - 1, 2.0)]))
    assert list(series(store).load_span(first, first)["v"]) == [1.0, 2.0]


def test_a_row_at_the_next_midnight_is_refused(tmp_path):
    store = lone(tmp_path, parquet_bytes([(T0, 1.0), (ms(2026, 9, 3, 0, 0, 0), 2.0)]))
    with pytest.raises(ValueError, match=FILE + "t holds instants outside the UTC day"):
        series(store).load_span(T0, T0)


def test_a_row_one_millisecond_before_the_day_is_refused(tmp_path):
    store = lone(tmp_path, parquet_bytes([(ms(2026, 9, 2, 0, 0, 0) - 1, 1.0), (T0, 2.0)]))
    with pytest.raises(ValueError, match=FILE + "t holds instants outside the UTC day"):
        series(store).load_span(T0, T0)


# -- unsigned time columns -----------------------------------------------------------------------


def test_an_unsigned_time_column_in_range_is_read_as_int64(tmp_path):
    store = lone(tmp_path, parquet_bytes([(T0, 1.0), (T0 + 1, 2.0)], pa.uint64()))
    got = series(store).load_span(T0, T0)
    assert got["t"].dtype == np.int64 and list(got["t"]) == [T0, T0 + 1] and list(got["v"]) == [1.0, 2.0]


@pytest.mark.parametrize("rows", [
    pytest.param([(T0, 1.0), (LIMIT, 2.0)], id="at-the-limit"),
    pytest.param([(T0, 1.0), (1 << 63, 2.0)], id="past-int64"),
    pytest.param([(LIMIT, 1.0)], id="only-row"),
])
def test_an_unsigned_instant_at_or_over_the_limit_is_refused_as_not_epoch_ms_not_wrapped(tmp_path, rows):
    store = lone(tmp_path, parquet_bytes(rows, pa.uint64()))
    with pytest.raises(ValueError, match=FILE + r"t must be an integer epoch-ms column, got uint64"):
        series(store).load_span(T0, T0)


def test_a_signed_instant_over_the_limit_is_refused_by_its_day_not_as_a_dtype(tmp_path):
    store = lone(tmp_path, parquet_bytes([(T0, 1.0), (LIMIT + 5, 2.0)]))
    with pytest.raises(ValueError, match=FILE + "t holds instants outside the UTC day"):
        series(store).load_span(T0, T0)


def test_the_other_refusals_name_the_file_by_its_relpath_first(tmp_path):
    floats = lone(tmp_path / "f", parquet_bytes([(float(T0), 1.0)], pa.float64()))
    with pytest.raises(ValueError, match=FILE + "t must be an integer epoch-ms column, got float64"):
        series(floats).load_span(T0, T0)
    junk = lone(tmp_path / "j", b"definitely not parquet")
    with pytest.raises(ValueError, match=FILE + "cannot be read as parquet"):
        series(junk).load_span(T0, T0)
    short = lone(tmp_path / "s", parquet_bytes([(T0, 1.0)]))
    with pytest.raises(ValueError, match=r"^2026-09-02\.parquet has no column\(s\) \['gone'\]"):
        series(short, columns=["t", "gone"]).load_span(T0, T0)


# -- spans and windows that start or end inside a day -------------------------------------------------


def test_an_empty_store_span_has_a_typed_empty_answer(tmp_path):
    got = series(store_of(tmp_path, [(T0, 1.0)])).load_span(T0 + 10 * DAY, T0 + 11 * DAY)
    assert got["t"].dtype == np.int64 and got["v"].dtype == np.float64 and len(got["t"]) == len(got["v"]) == 0


def test_a_span_whose_bounds_are_mid_day_returns_the_whole_day_files_it_touches(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0), (T0 + DAY, 2.0), (T0 + 2 * DAY, 3.0)])
    assert list(series(store).load_span(T0 + 5, T0 + 5)["v"]) == [1.0], "one instant, one day"
    assert list(series(store).load_span(T0 + 5, T0 + DAY + 5)["v"]) == [1.0, 2.0], "first bound mid-day, last mid-next-day"


def test_a_day_before_1970_is_read_and_answered_like_any_other(tmp_path):
    """Day indexes below zero: floor division reaches the file; truncation toward zero would look one day late."""
    store = Store(tmp_path).lay({"1969-12-31.parquet": parquet_bytes([(-1000, 1.0), (-500, 2.0)])}).acquire()
    assert list(series(store).load_span(-300, -300)["v"]) == [1.0, 2.0]
    got = series(store).prior(np.array([-250]), 10_000)
    assert got["v"][0] == 2.0 and got[AGE_COLUMN][0] == 250


def test_prior_reads_the_days_its_age_window_starts_and_ends_in_when_both_are_mid_day(tmp_path):
    rows = [(MIDNIGHT - DAY + 6 * 3_600_000, 1.0), (MIDNIGHT + 3_600_000, 2.0), (MIDNIGHT + DAY + 3_600_000, 3.0)]
    store = store_of(tmp_path, rows)
    at = MIDNIGHT + DAY + 6 * 3_600_000  # 06:00 on day 3, a cap of 1.5 days starts the window at 18:00 on day 1
    got = series(store).prior(np.array([at]), 3 * DAY // 2)
    assert got["v"][0] == 3.0 and got[AGE_COLUMN][0] == 5 * 3_600_000, "the nearest row wins; days 1 to 3 are all in reach"
    far = series(store).prior(np.array([at]), 5 * 3_600_000 - 1)
    assert np.isnan(far["v"][0]), "a cap one ms short of the nearest row finds none even though older rows are in reach"
    old = series(store).prior(np.array([MIDNIGHT - DAY + 7 * 3_600_000]), 3_600_000)
    assert old["v"][0] == 1.0, "the first day alone, the window starting inside it"


def test_instants_needing_different_day_windows_in_one_call_are_each_answered_from_their_own(tmp_path):
    rows = [(T0, 10.0), (T0 + DAY, 20.0), (T0 + DAY + 3_600_000, 30.0)]
    store = store_of(tmp_path, rows)
    asked = np.array([T0 + DAY + 7_200_000, T0 + 5_000, T0 + DAY + 3_600_100, T0 + 1_000])
    got = series(store).prior(asked, 10_000)
    assert np.isnan(got["v"][0]), "two hours after the last row: past the cap"
    assert list(got["v"][1:]) == [10.0, 30.0, 10.0], "each instant sees the row of its own day, the groups cut in the right place"
    assert list(got[AGE_COLUMN][1:]) == [5_000, 100, 1_000]


# -- the representable range of instants and caps ---------------------------------------------------------


def test_an_instant_just_inside_the_range_is_accepted_on_either_side(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    assert np.isnan(series(store).prior(np.array([LIMIT - 1], dtype=np.int64), 1000)["v"][0])
    assert np.isnan(series(store).prior(np.array([-LIMIT + 1], dtype=np.int64), 1000)["v"][0])
    assert np.isnan(series(store).prior(np.array([5, 7], dtype=np.uint64), 1000)["v"]).all(), "an unsigned query has no lower bound to breach"


@pytest.mark.parametrize("instants", [
    pytest.param([LIMIT], id="max-at-limit"),
    pytest.param([5, LIMIT], id="max-at-limit-after-small"),
    pytest.param([-LIMIT], id="min-at-limit"),
    pytest.param([-LIMIT, 5], id="min-at-limit-before-small"),
])
def test_an_instant_on_or_past_the_range_edge_is_refused_wherever_it_sits_in_the_query(tmp_path, instants):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match=r"instants_ms must lie strictly within"):
        series(store).prior(np.array(instants, dtype=np.int64), 1000)


def test_an_unsigned_instant_at_the_limit_is_refused(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    with pytest.raises(ValueError, match=r"instants_ms must lie strictly within"):
        series(store).prior(np.array([5, LIMIT], dtype=np.uint64), 1000)


def test_the_age_cap_may_equal_the_limit_and_not_exceed_it(tmp_path):
    store = store_of(tmp_path, [(T0, 1.0)])
    assert series(store).prior(np.array([T0 + 1]), LIMIT)["v"][0] == 1.0, "a cap at the limit reaches every row, without wrapping"
    with pytest.raises(ValueError, match="max_age_ms"):
        series(store).prior(np.array([T0 + 1]), LIMIT + 1)
