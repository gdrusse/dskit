"""The expensive scramble: whole sessions donate labels (ADR-0074).

ADR-0069 built both ends of the tier-2 seam and left the middle out.
These are the tests of the middle. What must hold, and why each one
would silently invalidate the null if it did not:

- a session moves WHOLE, so the h-minute label overlap and the
  within-day shape ride along with it;
- EVERY symbol gets the same permutation, or the cross-stock
  correlation at a minute is destroyed too and the null is no longer
  the null we mean;
- the training and the validation windows are permuted
  INDEPENDENTLY, or a scrambled walk is scored on the sessions it
  trained on;
- a row whose donor session lacks its minute is REFUSED, never given
  an invented label;
- half sessions leave the donor pool, since permuting one against a
  full session changes the row count rather than the labels.
"""

import numpy as np
import pytest

from intraday_equities.nodes import (
    _DAY_MS,
    NoInformationScan,
    _DayScramble,
    _session_offsets,
    _scan_fold_stamped,
    _walk_no_information_series,
)

MINUTE = 60_000


def _session(day, rows, first=0):
    """Stamps for one session: ``rows`` one-minute bars from ``first``."""
    return np.asarray(
        [day * _DAY_MS + (first + k) * MINUTE for k in range(rows)],
        dtype=np.int64,
    )


def _tape(days, rows=5, first=0):
    """One symbol's stamps over ``days``, ``rows`` bars each."""
    return np.concatenate([_session(d, rows, first) for d in days])


def _prepared(symbol, stamps, prices=None):
    """A ``prepared`` item shaped as :func:`_scan_aligned` returns one."""
    n = stamps.size
    px = (
        np.asarray(prices, dtype=np.float64)
        if prices is not None
        else np.linspace(100.0, 101.0, n)
    )
    x = np.arange(n, dtype=np.float64).reshape(n, 1)
    loc = np.arange(n, dtype=np.int64)
    return (symbol, stamps, x, loc, np.ones(n, dtype=bool), stamps, px)


def test_from_prepared_reads_the_calendar_and_drops_the_half_days():
    """Half sessions leave the pool; the calendar is the symbol union."""
    full = _tape([0, 1, 2, 3])
    short = _session(4, 2)
    one = _prepared("LLY", np.concatenate([full, short]))
    other = _prepared("XOM", _tape([0, 1, 2, 3, 5]))
    scramble = _DayScramble.from_prepared(7, [one, other])
    assert scramble.calendar == (0, 1, 2, 3, 4, 5), (
        "the calendar is the union over symbols, so a name missing a "
        "session cannot shrink the donor pool"
    )
    assert scramble.short == frozenset({4}), (
        "a 2-row session against a 5-row median is a half day and must "
        "leave the pool: permuting it would change the row count"
    )
    assert 4 not in scramble._days_in(None, 10 * _DAY_MS)
    assert scramble.describe() == "seed:7 sessions:6 short-dropped:1"


def test_a_whole_session_moves_at_once():
    """Every row of a session takes its label from ONE donor session."""
    stamps = _tape([0, 1, 2, 3])
    y = np.arange(stamps.size, dtype=np.float64)
    scramble = _DayScramble.from_prepared(3, [_prepared("LLY", stamps)])
    mask = np.ones(stamps.size, dtype=bool)
    out = scramble.apply(
        stamps,
        y,
        (("w", mask, None, 4 * _DAY_MS - 1),),
    )
    assert np.all(np.isfinite(out)), (
        "every session is full and in-window, so no row may be refused"
    )
    assert sorted(out.tolist()) == sorted(y.tolist()), (
        "the labels are dealt around, never invented or dropped"
    )
    donors = scramble._donor_map("w", scramble._days_in(None, 4 * _DAY_MS - 1))
    day = stamps // _DAY_MS
    for row in range(stamps.size):
        gave = donors[int(day[row])]
        offset = stamps[row] - stamps[day == day[row]].min()
        want = y[(day == gave) & ((stamps - gave * _DAY_MS) == offset)]
        assert out[row] == pytest.approx(want[0]), (
            "row {0} took its label from somewhere other than its donor "
            "session at the same minute".format(row)
        )


def test_every_symbol_gets_the_same_permutation():
    """The cross-stock correlation at a minute survives only if it does."""
    stamps = _tape([0, 1, 2, 3])
    scramble = _DayScramble.from_prepared(
        11,
        [_prepared("LLY", stamps), _prepared("XOM", stamps)],
    )
    mask = np.ones(stamps.size, dtype=bool)
    bucket = (("w", mask, None, 4 * _DAY_MS - 1),)
    lly = scramble.apply(stamps, np.arange(stamps.size, dtype=np.float64) * 1.0, bucket)
    xom = scramble.apply(
        stamps, np.arange(stamps.size, dtype=np.float64) * 10.0, bucket
    )
    assert np.allclose(lly * 10.0, xom), (
        "two names handed the same stamps must be re-labelled by the "
        "same session map, or the scramble destroys the cross-stock "
        "correlation as well as the signal"
    )


def test_the_training_and_validation_windows_are_drawn_apart():
    """One shuffle for both would score a walk on what it trained on."""
    stamps = _tape([0, 1, 2, 3, 4, 5, 6])
    y = np.arange(stamps.size, dtype=np.float64)
    scramble = _DayScramble.from_prepared(5, [_prepared("LLY", stamps)])
    day = stamps // _DAY_MS
    train, val = day <= 3, day >= 4
    scramble.apply(
        stamps,
        y,
        (
            ("train|None|{0}".format(4 * _DAY_MS - 1), train, None, 4 * _DAY_MS - 1),
            (
                "val|{0}|{1}".format(4 * _DAY_MS, 7 * _DAY_MS - 1),
                val,
                4 * _DAY_MS,
                7 * _DAY_MS - 1,
            ),
        ),
    )
    keys = sorted(scramble._donors)
    assert len(keys) == 2, "each window draws its own permutation"
    assert set(scramble._donors[keys[0]]).isdisjoint(set(scramble._donors[keys[1]])), (
        "a validation session must never appear in the training pool, or "
        "the scrambled walk trains on the sessions it is scored on"
    )


def test_a_donor_without_that_minute_refuses_the_row():
    """An invented label is the one thing this test exists to rule out."""
    stamps = np.concatenate([_session(0, 5), _session(1, 5, first=60)])
    y = np.arange(stamps.size, dtype=np.float64)
    scramble = _DayScramble(0, {0: 5, 1: 5})
    mask = np.ones(stamps.size, dtype=bool)
    # Session 1 starts an hour later on the clock, but the key is
    # minutes from each session's OWN first row, so the two align and
    # nothing is refused — this is the daylight-saving case.
    out = scramble.apply(stamps, y, (("w", mask, None, 2 * _DAY_MS - 1),))
    assert np.all(np.isfinite(out))
    # Now make the donor genuinely shorter than the row it must serve.
    ragged = np.concatenate([_session(0, 5), _session(1, 2)])
    y2 = np.arange(ragged.size, dtype=np.float64)
    forced = _DayScramble(0, {0: 5, 1: 2})
    forced._donors["w"] = {0: 1, 1: 0}
    out2 = forced.apply(
        ragged, y2, (("w", np.ones(ragged.size, bool), None, 2 * _DAY_MS - 1),)
    )
    assert np.count_nonzero(np.isnan(out2[:5])) == 3, (
        "session 0's last three minutes have no counterpart in a 2-row "
        "donor and must be refused, not filled"
    )


def test_a_scrambled_fold_keeps_the_real_folds_geometry():
    """The masks come from the stamps, so the fold shape is unchanged.

    Eight sessions, not seven: the validation pool is sessions 4-6 and
    the eighth is there only so session 6's last label exists, which is
    what makes every donor complete. When one is NOT complete the row is
    refused instead — see the ragged case above.
    """
    stamps = _tape([0, 1, 2, 3, 4, 5, 6, 7])
    # Prices that MOVE differently from session to session: a tape whose
    # returns are all but identical would pass this test whether or not
    # anything was permuted.
    steps = np.random.default_rng(0).normal(0.0, 0.01, stamps.size)
    item = _prepared("LLY", stamps, 100.0 * np.exp(np.cumsum(steps)))
    cuts = dict(
        lead=1,
        train_end=4 * _DAY_MS - 1,
        val_start=4 * _DAY_MS,
        val_end=7 * _DAY_MS - 1,
        train_start=0,
    )
    plain = _scan_fold_stamped([item], **cuts)
    scramble = _DayScramble.from_prepared(6, [item])
    shuffled = _scan_fold_stamped([item], scramble=scramble, **cuts)
    assert shuffled[0].shape == plain[0].shape, (
        "a scrambled walk must train on the same number of rows as the "
        "real one, or its skill is not comparable with the real one's"
    )
    assert np.array_equal(shuffled[0], plain[0]), (
        "scrambling labels must not move or rebuild the training features"
    )
    assert shuffled[2].shape == plain[2].shape
    assert np.array_equal(shuffled[2], plain[2]), (
        "scrambling labels must not move or rebuild the validation features"
    )
    assert np.array_equal(shuffled[4], plain[4]), (
        "the scored instants are the real walk's instants"
    )
    assert not np.allclose(shuffled[3], plain[3]), (
        "the validation labels must actually have moved"
    )


def test_session_offsets_handle_rows_that_are_not_day_sorted():
    """The uncommon fallback remains correct without rescanning each day."""
    stamps = np.asarray(
        [
            _DAY_MS + 2 * MINUTE,
            3 * MINUTE,
            _DAY_MS + 5 * MINUTE,
            7 * MINUTE,
        ],
        dtype=np.int64,
    )
    days = stamps // _DAY_MS
    assert np.array_equal(
        _session_offsets(stamps, days),
        np.asarray([0, 0, 3 * MINUTE, 4 * MINUTE], dtype=np.int64),
    )


def test_pooled_fold_parts_avoid_a_second_symbol_scramble(monkeypatch):
    """Scoring reuses the pooled fold arrays instead of rebuilding them."""
    stamps = _tape([0, 1, 2, 3, 4, 5, 6, 7])
    steps = np.random.default_rng(1).normal(0.0, 0.01, stamps.size)
    item = _prepared("LLY", stamps, 100.0 * np.exp(np.cumsum(steps)))
    cuts = dict(
        train_end=4 * _DAY_MS - 1,
        val_start=4 * _DAY_MS,
        val_end=7 * _DAY_MS - 1,
        train_start=0,
    )
    scramble = _DayScramble.from_prepared(6, [item])
    original = _DayScramble.apply
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    class ZeroModel:
        calls = 0

        @staticmethod
        def predict(x):
            ZeroModel.calls += 1
            return np.zeros(x.shape[0], dtype=np.float64)

    monkeypatch.setattr(_DayScramble, "apply", counted)
    parts = {}
    _scan_fold_stamped([item], lead=1, scramble=scramble, parts=parts, **cuts)
    assert len(calls) == 1
    key = ("LLY", 1)
    predictions = {key: np.zeros(parts[key][1].shape[0], dtype=np.float64)}
    _walk_no_information_series(
        item,
        ZeroModel(),
        [1],
        period_minutes=1,
        scramble=scramble,
        parts=parts,
        predictions=predictions,
        **cuts,
    )
    assert len(calls) == 1, "the symbol scorer must reuse the pooled build"
    assert ZeroModel.calls == 0, "the scorer must reuse the pooled prediction"


def test_the_knob_is_declared_and_checked():
    """A knob a document may set and nothing honours is worse than none."""
    base = {
        "split": "val",
        "train_end_ms": 10,
        "val_start_ms": 11,
        "val_end_ms": 20,
    }
    assert NoInformationScan.validate_params(dict(base, label_scramble_seed=0)) == []
    assert any(
        "label_scramble_seed" in p
        for p in NoInformationScan.validate_params(dict(base, label_scramble_seed=-1))
    ), "a negative seed is not a seed"
    assert any(
        "label_scramble_seed" in p
        for p in NoInformationScan.validate_params(dict(base, label_scramble_seed=None))
    ), (
        "a null scramble knob must be refused rather than read as 'off' — "
        "the same rule the label and lead knobs follow"
    )
