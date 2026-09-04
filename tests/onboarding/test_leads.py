"""leads.py: the generic lead-fraction capture grid (ADR-0075 part 2).

The declaration below is restated here on purpose — a test that read its
expectation from the subject would assert nothing. It is one child's grid
(21 fractions, a 48/168/744 h cap ladder, a 60 s floor), which is exactly
what dskit must NOT carry as a default: every knob is required.
"""

import math

import pytest

from dskit.onboarding import LeadGrid
from dskit.onboarding.leads import LEAD_ROUND_DP, lead_key

H_MS = 3_600_000
DAY_MS = 24 * H_MS

FRACS = (
    0.98, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50,
    0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10, 0.05, 0.02,
)
CAPS_H = (48, 168, 744)
FLOOR_S = 60

#: JSON-shaped wrong-typed values — ``problems`` is the plan-time half a
#: node's ``validate_params`` calls, so it must RETURN on every one.
FUZZ = ("1,000", "", -1, 0, 1e308, float("nan"), True, False, None, [], [1],
        {}, {"nested": 1})


def _grid(fracs=FRACS, caps=CAPS_H, floor=FLOOR_S):
    return LeadGrid(fracs, caps, floor)


# ---------------------------------------------------------------------------
# Declaration — every knob required, every problem accumulated
# ---------------------------------------------------------------------------


class TestDeclaration:
    def test_every_knob_is_required_no_project_default_lives_here(self):
        with pytest.raises(TypeError):
            LeadGrid()
        with pytest.raises(TypeError):
            LeadGrid(FRACS)
        with pytest.raises(TypeError):
            LeadGrid(FRACS, CAPS_H)

    def test_a_valid_declaration_has_no_problems_and_is_held_as_floats(self):
        assert LeadGrid.problems(FRACS, CAPS_H, FLOOR_S) == []
        grid = _grid()
        assert grid.lead_fracs == FRACS
        assert grid.dur_caps_h == (48.0, 168.0, 744.0)
        assert grid.min_abs_lead_s == 60.0
        assert all(type(f) is float for f in grid.lead_fracs)
        assert all(type(c) is float for c in grid.dur_caps_h)

    @pytest.mark.parametrize(
        ("fracs", "caps", "floor", "needle"),
        [
            ((), CAPS_H, FLOOR_S, "lead_fracs"),  # empty
            ("0.98", CAPS_H, FLOOR_S, "lead_fracs"),  # not a list
            ((0.5, 0.9), CAPS_H, FLOOR_S, "strictly decreasing"),
            ((0.9, 0.9), CAPS_H, FLOOR_S, "repeat"),
            ((0.9800001, 0.98), CAPS_H, FLOOR_S, "repeat"),  # one key at 6 dp
            ((0.9, 0.0), CAPS_H, FLOOR_S, "(0, 1)"),
            ((1.0, 0.5), CAPS_H, FLOOR_S, "(0, 1)"),
            ((0.9, "0.5"), CAPS_H, FLOOR_S, "(0, 1)"),
            ((0.9, True), CAPS_H, FLOOR_S, "(0, 1)"),
            ((0.9, float("nan")), CAPS_H, FLOOR_S, "(0, 1)"),
            (FRACS, (), FLOOR_S, "dur_caps_h"),  # empty
            (FRACS, (168, 48), FLOOR_S, "ascending"),
            (FRACS, (48, 48), FLOOR_S, "repeat"),
            (FRACS, (0, 48), FLOOR_S, "dur_caps_h"),
            (FRACS, (48, float("inf")), FLOOR_S, "dur_caps_h"),
            (FRACS, (48, "168"), FLOOR_S, "dur_caps_h"),
            (FRACS, CAPS_H, -1, "min_abs_lead_s"),
            (FRACS, CAPS_H, float("nan"), "min_abs_lead_s"),
            (FRACS, CAPS_H, True, "min_abs_lead_s"),
            (FRACS, CAPS_H, "60", "min_abs_lead_s"),
        ],
    )
    def test_refusals_name_the_knob_at_plan_and_at_construction(
        self, fracs, caps, floor, needle
    ):
        problems = LeadGrid.problems(fracs, caps, floor)
        assert any(needle in p for p in problems), problems
        with pytest.raises(ValueError) as excinfo:
            LeadGrid(fracs, caps, floor)
        assert needle in str(excinfo.value)

    def test_problems_accumulate_one_per_knob(self):
        problems = LeadGrid.problems((), (), -1)
        assert len(problems) == 3
        assert [p.split()[0] for p in problems] == [
            "lead_fracs", "dur_caps_h", "min_abs_lead_s"
        ]

    @pytest.mark.parametrize("value", FUZZ)
    def test_problems_returns_never_raises(self, value):
        # Totality, not refusal: some fuzz values ARE legal in some slot
        # (0 is "no floor", 1e308 a finite cap) — what must never happen
        # is an exception escaping a plan-time validator.
        for trial in (
            (value, CAPS_H, FLOOR_S),
            (FRACS, value, FLOOR_S),
            (FRACS, CAPS_H, value),
            ((0.9, value), CAPS_H, FLOOR_S),
            (FRACS, (48, value), FLOOR_S),
        ):
            problems = LeadGrid.problems(*trial)
            assert isinstance(problems, list), trial
            assert all(isinstance(p, str) for p in problems), trial

    def test_a_zero_floor_is_a_legal_declaration(self):
        # >= 0: "no floor" is a decision a caller may take, spelled as 0.
        assert LeadGrid.problems(FRACS, CAPS_H, 0) == []
        assert _grid(floor=0).epochs(0, 1_000) == [
            (f, int(round(1_000 - f * 1_000))) for f in FRACS
        ]


# ---------------------------------------------------------------------------
# Cap selection and the observable span
# ---------------------------------------------------------------------------


class TestCapAndSpan:
    @pytest.mark.parametrize(
        ("life_ms", "cap_h"),
        [
            (DAY_MS, 48),  # a daily life takes the daily cap
            (48 * H_MS, 48),  # exactly AT a cap: that cap
            (int(48.1 * H_MS), 168),  # just over: the next rung
            (168 * H_MS, 168),
            (168 * H_MS + 1, 744),
            (744 * H_MS, 744),
            (1_000 * H_MS, 744),  # beyond the largest: the largest
        ],
    )
    def test_cap_is_the_smallest_at_or_above_the_life_else_the_largest(
        self, life_ms, cap_h
    ):
        grid = _grid()
        open_ms = 7_000_000  # a non-zero open, so the life is close - open
        assert grid.cap_ms(open_ms, open_ms + life_ms) == cap_h * H_MS

    def test_span_is_the_whole_life_when_the_cap_does_not_bind(self):
        assert _grid().span(0, DAY_MS) == (0, DAY_MS)
        assert _grid().span(5_000, 5_000 + 48 * H_MS) == (5_000, 5_000 + 48 * H_MS)

    def test_span_is_the_cap_when_the_instrument_is_listed_early(self):
        close = 1_000 * H_MS
        assert _grid().span(0, close) == (close - 744 * H_MS, close)

    @pytest.mark.parametrize("close", [0, -1, 5_000])
    def test_span_refuses_a_close_at_or_before_the_open(self, close):
        with pytest.raises(ValueError, match="close after it opens"):
            _grid().span(5_000, close)
        with pytest.raises(ValueError, match="close after it opens"):
            _grid().epochs(5_000, close)


# ---------------------------------------------------------------------------
# Epochs — the arithmetic the child pins, restated
# ---------------------------------------------------------------------------


def _expected(grid, open_ms, close_ms):
    """The semantics, spelled independently of the implementation."""
    life_h = (close_ms - open_ms) / H_MS
    cap_h = next((c for c in grid.dur_caps_h if life_h <= c), grid.dur_caps_h[-1])
    start = max(open_ms, close_ms - int(round(cap_h * H_MS)))
    span = close_ms - start
    out = []
    for f in grid.lead_fracs:
        instant = int(round(close_ms - max(f * span, grid.min_abs_lead_s * 1000)))
        if instant > open_ms:
            out.append((f, instant))
    return out


class TestEpochs:
    def test_a_daily_instrument_yields_every_fraction(self):
        epochs = _grid().epochs(0, DAY_MS)
        assert [f for f, _t in epochs] == list(FRACS)
        assert epochs[0] == (0.98, 1_728_000)
        assert epochs[-1] == (0.02, DAY_MS - 1_728_000)
        instants = [t for _f, t in epochs]
        assert instants == sorted(instants)  # chronological
        assert len(set(instants)) == len(instants)  # and distinct here
        assert all(type(t) is int for t in instants)

    @pytest.mark.parametrize(
        ("open_ms", "close_ms"),
        [
            (0, DAY_MS),
            (5_000_000, 5_000_000 + 30 * H_MS),
            (123_456, 123_456 + 100 * H_MS),  # the weekly cap
            (0, 1_000 * H_MS),  # the cap binds: span is 744 h
            (0, 90_000),  # the floor bites
            (40_000, 130_000),
        ],
    )
    def test_the_arithmetic_is_the_pinned_one(self, open_ms, close_ms):
        grid = _grid()
        assert grid.epochs(open_ms, close_ms) == _expected(grid, open_ms, close_ms)

    def test_a_ninety_second_instrument_floors_the_late_fractions(self):
        # 90 s of life, a 60 s floor: every fraction whose proportional
        # lead is under 60 s is floored onto ONE instant (close - 60 s);
        # nothing is dropped, because that instant is still after the open.
        epochs = _grid().epochs(0, 90_000)
        assert len(epochs) == len(FRACS)
        floored = [t for f, t in epochs if f * 90_000 < 60_000]
        assert floored and set(floored) == {30_000}
        proportional = [t for f, t in epochs if f * 90_000 >= 60_000]
        assert proportional == [int(round(90_000 - f * 90_000)) for f in FRACS[:7]]
        instants = [t for _f, t in epochs]
        assert instants == sorted(instants)  # still chronological

    @pytest.mark.parametrize("life_ms", [60_000, 45_000, 1])
    def test_an_instrument_no_longer_than_the_floor_drops_every_instant(
        self, life_ms
    ):
        # The floor pushes each instant AT or before the raw open — a
        # state that does not exist yet cannot be read — so nothing is due.
        assert _grid().epochs(10_000, 10_000 + life_ms) == []
        assert _grid().due_periods(10_000, 10_000 + life_ms) == []

    def test_instants_are_strictly_after_the_raw_open_never_the_capped_start(self):
        # Listed early: the span starts at close - cap, well after the open,
        # and every instant lies inside that window.
        close = 1_000 * H_MS
        start, _close = _grid().span(0, close)
        for _f, t in _grid().epochs(0, close):
            assert start < t < close

    def test_fractions_stay_strictly_decreasing_and_time_chronological(self):
        for open_ms, close_ms in ((0, DAY_MS), (0, 90_000), (0, 1_000 * H_MS)):
            epochs = _grid().epochs(open_ms, close_ms)
            fracs = [f for f, _t in epochs]
            assert fracs == sorted(fracs, reverse=True)
            assert len(set(fracs)) == len(fracs)
            instants = [t for _f, t in epochs]
            assert instants == sorted(instants)

    def test_determinism_pin_same_inputs_identical_lists(self):
        grid = _grid()
        for open_ms, close_ms in ((0, DAY_MS), (0, 90_000), (7, 7 + 500 * H_MS)):
            assert grid.epochs(open_ms, close_ms) == grid.epochs(open_ms, close_ms)
            assert grid.due_periods(open_ms, close_ms) == grid.due_periods(
                open_ms, close_ms
            )
            # Two grids from one declaration are one grid.
            assert _grid().epochs(open_ms, close_ms) == grid.epochs(open_ms, close_ms)

    def test_int_and_float_declarations_agree(self):
        # A JSON document may spell 48 or 48.0, 60 or 60.0 — same grid.
        a = LeadGrid(FRACS, CAPS_H, FLOOR_S)
        b = LeadGrid(FRACS, tuple(float(c) for c in CAPS_H), float(FLOOR_S))
        assert a.epochs(0, DAY_MS) == b.epochs(0, DAY_MS)
        assert a.epochs(0, 90_000) == b.epochs(0, 90_000)


# ---------------------------------------------------------------------------
# Position — on-grid vs off-grid, compared at LEAD_ROUND_DP
# ---------------------------------------------------------------------------


class TestPosition:
    def test_on_grid_fractions_locate_chronologically(self):
        grid = _grid()
        assert grid.position(0.98) == 0
        assert grid.position(0.50) == 10
        assert grid.position(0.02) == 20
        assert [grid.position(f) for f in FRACS] == list(range(len(FRACS)))

    def test_a_fraction_within_rounding_is_on_grid(self):
        grid = _grid()
        assert grid.position(0.98000004) == 0
        assert grid.position(0.9799996) == 0
        assert grid.position("0.98") == 0  # a period string read back

    @pytest.mark.parametrize("frac", [0.97, 0.9800006, 0.0, 1.0, 0.5000006])
    def test_off_grid_answers_none_so_a_loader_skips_never_aborts(self, frac):
        assert _grid().position(frac) is None


# ---------------------------------------------------------------------------
# Due periods — the coverage-ledger spelling
# ---------------------------------------------------------------------------


class TestDuePeriods:
    def test_the_rounding_places_are_pinned(self):
        # Ledgers PERSIST the spelling: moving this orphans every marked
        # period, so it changes with a migration, never silently.
        assert LEAD_ROUND_DP == 6

    def test_periods_are_six_decimal_strings_paired_with_the_instants(self):
        periods = _grid().due_periods(0, DAY_MS)
        assert periods[0] == ("0.980000", 1_728_000)
        assert periods[-1] == ("0.020000", DAY_MS - 1_728_000)
        for period, instant in periods:
            whole, frac = period.split(".")
            assert whole == "0" and len(frac) == LEAD_ROUND_DP
            assert type(instant) is int

    def test_due_periods_are_epochs_respelled_nothing_else(self):
        grid = _grid()
        for open_ms, close_ms in ((0, DAY_MS), (0, 90_000), (0, 1_000 * H_MS)):
            assert grid.due_periods(open_ms, close_ms) == [
                (LeadGrid.normalize(f), t) for f, t in grid.epochs(open_ms, close_ms)
            ]

    def test_a_period_reads_back_onto_the_grid(self):
        grid = _grid()
        for i, (period, _t) in enumerate(grid.due_periods(0, DAY_MS)):
            assert float(period) == lead_key(FRACS[i])
            assert grid.position(float(period)) == i
            assert LeadGrid.normalize(period) == period  # idempotent

    def test_normalize_spells_one_fraction_one_way(self):
        assert LeadGrid.normalize(0.5) == "0.500000"
        assert LeadGrid.normalize(0.98) == "0.980000"
        assert LeadGrid.normalize("0.98") == "0.980000"
        assert LeadGrid.normalize(0.9800004) == "0.980000"  # lead_key rounding
        assert LeadGrid.normalize(0.02) == "0.020000"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "not-a-number", None])
    def test_normalize_refuses_what_cannot_be_a_period(self, bad):
        with pytest.raises((ValueError, TypeError)):
            LeadGrid.normalize(bad)

    def test_lead_key_rounds_at_the_declared_places(self):
        assert lead_key(0.98000004) == 0.98
        assert lead_key(0.9800006) == 0.980001
        assert lead_key("0.5") == 0.5
        assert math.isnan(lead_key(float("nan")))  # keys do not judge; normalize does
