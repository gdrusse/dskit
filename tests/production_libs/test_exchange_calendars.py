"""`libs/exchange_calendars.py` — a published exchange schedule as a session list (§5.1.1).

`WeeklySessions` makes a document restate an exchange's schedule by hand.
This pack reads the published one instead, and its whole job is turning a
library into the session list phase 1 already consumes — which is why the
tests below assert two things and not three:

* **Nothing is re-derived.** `is_open`, `next_open`, `next_close`,
  `window(kind, at_ms)` and `tz_name` are answered by the `WeeklySessions`
  the pack materialises, so the half-open bounds, the buffers, the
  DST-gap refusal and the `CALENDAR_WINDOWS` dispatch are tested once, in
  `tests/production/test_sessions.py`, and asserted here only as
  DELEGATION.
* **What cannot be expressed refuses.** A weekly session list plus
  holidays plus early closes cannot express a late open, a session that
  crosses midnight, or a weekday whose days disagree on their structure.
  Materialising one of those silently would put the loop in the market at
  an hour the exchange was shut, so each refuses and names the dates. The
  one deviation it CAN express — an earlier close — is materialised
  exactly, which is why a longer close becomes the weekday's pattern
  rather than a refusal.
* **The window is the whole fact.** The list is a fact about `bounds` and
  nothing else, so outside them the calendar is shut rather than
  extrapolated: a weekly pattern with no holidays left would trade on the
  first unlisted Christmas.

The schedule read is the one method that names the library, so every test
here but the live one drives the pack through a subclass that supplies
canned sessions — the same seam a child would use, and the reason the
materialisation is testable on a host where the library is not installed.
The live test asks for the library through `pytest.importorskip` and
skips when it is absent, as every pack test does.

`data_fingerprint()` is asserted by what MOVES it: the pack's answer
changes when a holiday moves and does not change when the code is merely
rebuilt, which is the whole reason a code fingerprint alone would not
catch a library upgrade.
"""

import pytest

from dskit.production.base import ProductionError, canonical_hash
from dskit.production.libs.exchange_calendars import ExchangeCalendar
from dskit.production.sessions import CALENDAR_KINDS, AlwaysOpen, Calendar, WeeklySessions

# ---------------------------------------------------------------------------
# A canned schedule: the one method that names the library, overridden
# ---------------------------------------------------------------------------

#: Five ordinary weekdays of a 09:30-16:00 exchange, one holiday (the 15th,
#: a Thursday, absent from the schedule) and one early close (the 16th).
WEEK = {
    "2026-01-12": (("09:30", "16:00"),),
    "2026-01-13": (("09:30", "16:00"),),
    "2026-01-14": (("09:30", "16:00"),),
    "2026-01-16": (("09:30", "13:00"),),
    "2026-01-19": (("09:30", "16:00"),),
    "2026-01-20": (("09:30", "16:00"),),
    "2026-01-21": (("09:30", "16:00"),),
    "2026-01-22": (("09:30", "16:00"),),
    "2026-01-23": (("09:30", "16:00"),),
}

BOUNDS = {"from": "2026-01-12", "until": "2026-01-23"}


def canned(rows, tz="America/New_York"):
    """Return an `ExchangeCalendar` subclass whose schedule is `rows`."""

    class Canned(ExchangeCalendar):
        def _materialise(self, params):
            return tz, dict(rows)

    return Canned


def a_calendar(rows=None, tz="America/New_York", **params):
    """Build the pack over canned rows with the illustration's bounds."""
    site = {"exchange": "XNYS", "bounds": dict(BOUNDS)}
    site.update(params)
    return canned(WEEK if rows is None else rows, tz)(site)


def ms(day, wall, tz="America/New_York"):
    """Epoch ms of a local wall time, through the phase-1 owner's own arithmetic."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.fromisoformat(f"{day}T{wall}:00").replace(tzinfo=ZoneInfo(tz))
    return int(stamp.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Registration and the family
# ---------------------------------------------------------------------------


def test_the_pack_registers_exchange_into_the_calendar_family():
    # §4.3: import is registration, and the family is the phase-1 seam —
    # a document says `{"uses": "exchange"}` and nothing else changes.
    assert CALENDAR_KINDS.resolve("exchange") is ExchangeCalendar
    assert issubclass(ExchangeCalendar, Calendar)


def test_a_document_selects_it_through_the_registry_like_any_other_calendar():
    assert "exchange" in CALENDAR_KINDS


# ---------------------------------------------------------------------------
# Params — default-deny, and every refusal before the library is reached
# ---------------------------------------------------------------------------


class TestParams:
    def test_an_unknown_knob_refuses(self):
        problems = ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": dict(BOUNDS), "sessions": []}
        )
        assert any("sessions" in p for p in problems)

    def test_notes_is_allowed_beside_the_knobs(self):
        assert ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": dict(BOUNDS), "notes": "why"}
        ) == []

    def test_the_exchange_code_is_required(self):
        problems = ExchangeCalendar.validate_params({"bounds": dict(BOUNDS)})
        assert any("exchange" in p for p in problems)

    def test_bounds_are_required_because_an_unbounded_query_is_not_reproducible(self):
        # §5.1.1: "an unbounded library query is not reproducible and would
        # answer differently as the library's own horizon moves".
        problems = ExchangeCalendar.validate_params({"exchange": "XNYS"})
        assert any("bounds" in p for p in problems)

    @pytest.mark.parametrize(
        "bounds",
        [
            {"from": "2026-01-12"},
            {"until": "2026-01-23"},
            {"from": "2026-01-12", "until": "2026-01-23", "through": "x"},
            {"from": "not-a-date", "until": "2026-01-23"},
            {"from": "2026-01-23", "until": "2026-01-12"},
            {"from": "2026-01-12", "until": "2026-01-12"},
            "2026",
        ],
        ids=["no-until", "no-from", "unknown-key", "malformed", "reversed", "empty", "not-an-object"],
    )
    def test_a_malformed_bound_refuses(self, bounds):
        problems = ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": bounds}
        )
        assert problems

    def test_an_unknown_zone_override_refuses(self):
        problems = ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": dict(BOUNDS), "tz": "Mars/Olympus"}
        )
        assert any("Mars/Olympus" in p for p in problems)

    def test_a_negative_buffer_refuses(self):
        problems = ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": dict(BOUNDS), "after_open_s": -1}
        )
        assert any("after_open_s" in p for p in problems)

    def test_a_malformed_blackout_refuses_through_the_phase_one_checker(self):
        problems = ExchangeCalendar.validate_params(
            {"exchange": "XNYS", "bounds": dict(BOUNDS),
             "blackouts": [{"from": "2026-01-12T15:00:00Z"}]}
        )
        assert problems

    def test_a_refusal_never_reaches_the_library(self):
        # The library is absent on most hosts and the params are checked
        # before it is asked for anything, so a malformed document refuses
        # identically whether or not it is installed.
        class Exploding(ExchangeCalendar):
            def _materialise(self, params):
                raise AssertionError("the schedule was read despite bad params")

        with pytest.raises(ProductionError):
            Exploding({"exchange": "XNYS"})


# ---------------------------------------------------------------------------
# Materialisation — the pack's whole job
# ---------------------------------------------------------------------------


class TestMaterialisation:
    def test_the_weekly_pattern_comes_from_the_published_sessions(self):
        cal = a_calendar()
        assert cal.is_open(ms("2026-01-12", "10:00")) is True
        assert cal.is_open(ms("2026-01-12", "09:00")) is False
        assert cal.is_open(ms("2026-01-12", "16:00")) is False  # half-open

    def test_a_day_the_exchange_did_not_publish_becomes_a_holiday(self):
        cal = a_calendar()
        assert cal.is_open(ms("2026-01-15", "10:00")) is False
        assert cal.is_open(ms("2026-01-22", "10:00")) is True

    def test_an_early_close_becomes_a_special_close(self):
        cal = a_calendar()
        assert cal.is_open(ms("2026-01-16", "12:00")) is True
        assert cal.is_open(ms("2026-01-16", "14:00")) is False
        assert cal.is_open(ms("2026-01-23", "14:00")) is True

    def test_a_weekend_the_exchange_never_trades_is_simply_not_a_session_day(self):
        cal = a_calendar()
        assert cal.is_open(ms("2026-01-17", "12:00")) is False
        assert cal.next_open(ms("2026-01-16", "14:00")) == ms("2026-01-19", "09:30")

    def test_a_lunch_break_becomes_two_sessions_on_the_same_day(self):
        rows = {
            "2026-01-12": (("09:00", "11:30"), ("12:30", "15:00")),
            "2026-01-13": (("09:00", "11:30"), ("12:30", "15:00")),
        }
        cal = a_calendar(rows, tz="Asia/Tokyo",
                         bounds={"from": "2026-01-12", "until": "2026-01-13"})
        assert cal.is_open(ms("2026-01-12", "10:00", "Asia/Tokyo")) is True
        assert cal.is_open(ms("2026-01-12", "12:00", "Asia/Tokyo")) is False
        assert cal.is_open(ms("2026-01-12", "13:00", "Asia/Tokyo")) is True

    def test_a_weekday_the_exchange_trades_only_sometimes_is_a_day_with_holidays(self):
        # A rare Saturday session: the weekday joins the weekly pattern and
        # every other Saturday in the bounds is a holiday. Refusing here
        # would make the pack useless for an exchange that has ever opened
        # on one.
        rows = dict(WEEK)
        rows["2026-01-17"] = (("09:30", "12:00"),)
        cal = a_calendar(rows)
        assert cal.is_open(ms("2026-01-17", "10:00")) is True
        assert cal.is_open(ms("2026-01-10", "10:00")) is False

    def test_the_buffers_reach_the_materialised_sessions(self):
        cal = a_calendar(after_open_s=60, before_close_s=120)
        assert cal.is_open(ms("2026-01-12", "09:30")) is False
        assert cal.is_open(ms("2026-01-12", "09:31")) is True
        assert cal.is_open(ms("2026-01-12", "15:58")) is False

    def test_a_blackout_shuts_an_interval_inside_a_session(self):
        cal = a_calendar(
            blackouts=[{"from": "2026-01-12T15:00:00Z", "until": "2026-01-12T16:00:00Z"}]
        )
        assert cal.is_open(ms("2026-01-12", "10:30")) is False  # 15:30Z
        assert cal.is_open(ms("2026-01-12", "11:30")) is True

    def test_the_calendar_is_shut_outside_the_window_it_was_read_over(self):
        # The materialised list is a fact about `bounds` and nothing else:
        # past `until` the weekly pattern would keep opening the market
        # with no holidays left to stop it, so the loop would trade on the
        # first unlisted Christmas. Shut is the safe stop; the operator
        # re-plans against a longer window.
        cal = a_calendar()
        assert cal.is_open(ms("2026-01-23", "10:00")) is True
        assert cal.is_open(ms("2026-01-26", "10:00")) is False
        assert cal.is_open(ms("2026-01-09", "10:00")) is False
        assert cal.next_open(ms("2026-01-23", "16:00")) is None

    def test_the_zone_is_the_librarys_unless_the_document_overrides_it(self):
        assert a_calendar().tz_name == "America/New_York"
        assert a_calendar(tz="UTC").tz_name == "UTC"

    def test_an_override_zone_reinterprets_the_published_wall_times(self):
        # The override is a `tz` for the SESSION LIST, so the same
        # `09:30-16:00` becomes a UTC session — which is why it is an
        # override and not a translation.
        cal = a_calendar(tz="UTC")
        assert cal.is_open(ms("2026-01-12", "10:00", "UTC")) is True
        assert cal.is_open(ms("2026-01-12", "15:30", "America/New_York")) is False


# ---------------------------------------------------------------------------
# What a weekly session list cannot express
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_an_empty_schedule_refuses_rather_than_serving_a_shut_calendar(self):
        with pytest.raises(ProductionError, match="no session"):
            a_calendar({})

    def test_a_session_crossing_midnight_refuses(self):
        rows = {"2026-01-12": (("18:00", "17:00"),), "2026-01-13": (("18:00", "17:00"),)}
        with pytest.raises(ProductionError, match="midnight"):
            a_calendar(rows, bounds={"from": "2026-01-12", "until": "2026-01-13"})

    def test_a_late_open_refuses_because_a_special_close_cannot_express_it(self):
        rows = dict(WEEK)
        rows["2026-01-20"] = (("11:00", "16:00"),)
        with pytest.raises(ProductionError, match="2026-01-20"):
            a_calendar(rows)

    def test_a_longer_close_becomes_the_pattern_and_the_short_days_close_early(self):
        # The one deviation a session list CAN express is an earlier close,
        # so the weekday's pattern is its latest close and every other date
        # of that weekday becomes a `special_close`. Refusing instead would
        # discard a schedule the shape reproduces exactly.
        rows = dict(WEEK)
        rows["2026-01-20"] = (("09:30", "18:00"),)
        cal = a_calendar(rows)
        assert cal.is_open(ms("2026-01-20", "17:00")) is True
        assert cal.is_open(ms("2026-01-13", "17:00")) is False
        assert cal.is_open(ms("2026-01-13", "15:00")) is True

    def test_a_refusal_names_every_date_it_cannot_express(self):
        rows = dict(WEEK)
        rows["2026-01-20"] = (("11:00", "16:00"),)   # a Tuesday that opened late
        rows["2026-01-21"] = (("11:00", "16:00"),)   # and a Wednesday
        with pytest.raises(ProductionError) as caught:
            a_calendar(rows)
        joined = " ".join(caught.value.problems)
        assert "2026-01-20" in joined and "2026-01-21" in joined

    def test_a_boundary_that_is_not_a_wall_minute_refuses(self):
        rows = dict(WEEK)
        rows["2026-01-20"] = (("09:30:30", "16:00"),)
        with pytest.raises(ProductionError):
            a_calendar(rows)

    def test_a_published_day_outside_the_bounds_is_not_materialised(self):
        rows = dict(WEEK)
        rows["2026-02-02"] = (("11:00", "16:00"),)  # unexpressible, but outside
        cal = a_calendar(rows)
        assert cal.is_open(ms("2026-01-12", "10:00")) is True


# ---------------------------------------------------------------------------
# Delegation — the phase-1 owner answers every question
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_every_question_is_answered_by_a_weekly_sessions(self):
        cal = a_calendar()
        twin = WeeklySessions({
            "tz": "America/New_York",
            "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"],
                          "open": "09:30", "close": "16:00"}],
            "holidays": ["2026-01-15"],
            "special_closes": [{"date": "2026-01-16", "close": "13:00"}],
        })
        at = ms("2026-01-12", "10:00")
        assert cal.is_open(at) == twin.is_open(at)
        assert cal.next_open(at) == twin.next_open(at)
        assert cal.next_close(at) == twin.next_close(at)
        assert cal.window("session", at) == twin.window("session", at)
        assert cal.window("day", at) == twin.window("day", at)
        assert cal.tz_name == twin.tz_name

    def test_an_unknown_window_kind_refuses_through_the_same_owner(self):
        with pytest.raises(ProductionError, match="unknown window kind"):
            a_calendar().window("fortnight", ms("2026-01-12", "10:00"))

    def test_a_calendar_with_no_event_window_refuses_for_one(self):
        with pytest.raises(ProductionError, match="event"):
            a_calendar().window("event", ms("2026-01-12", "10:00"))


# ---------------------------------------------------------------------------
# The data fingerprint — the fact a code digest cannot carry
# ---------------------------------------------------------------------------


class TestDataFingerprint:
    def test_the_base_calendar_answers_none(self):
        # The hook is concrete on `Calendar` so `plan` can ask every
        # calendar and write nothing for the ones that have no data.
        assert AlwaysOpen({}).data_fingerprint() is None
        assert WeeklySessions({
            "tz": "UTC",
            "sessions": [{"days": ["mon"], "open": "09:30", "close": "16:00"}],
        }).data_fingerprint() is None

    def test_the_pack_answers_a_digest(self):
        digest = a_calendar().data_fingerprint()
        assert isinstance(digest, str) and len(digest) == 64

    def test_the_same_schedule_answers_the_same_digest(self):
        assert a_calendar().data_fingerprint() == a_calendar().data_fingerprint()

    def test_a_moved_holiday_moves_the_digest(self):
        # The whole reason for the hook: the CODE is identical across a
        # library upgrade and the DATA is not.
        moved = {day: hours for day, hours in WEEK.items() if day != "2026-01-14"}
        moved["2026-01-15"] = (("09:30", "16:00"),)
        assert a_calendar(moved).data_fingerprint() != a_calendar().data_fingerprint()

    def test_a_different_early_close_moves_the_digest(self):
        moved = dict(WEEK)
        moved["2026-01-16"] = (("09:30", "12:00"),)
        assert a_calendar(moved).data_fingerprint() != a_calendar().data_fingerprint()

    def test_the_buffers_and_the_blackouts_are_in_the_digest(self):
        base = a_calendar().data_fingerprint()
        assert a_calendar(after_open_s=60).data_fingerprint() != base
        assert a_calendar(
            blackouts=[{"from": "2026-01-12T15:00:00Z", "until": "2026-01-12T16:00:00Z"}]
        ).data_fingerprint() != base

    def test_the_digest_is_the_canonical_hash_of_the_materialised_session_list(self):
        # Deliberate independent restatement: the expectation is built
        # here from the §4.1 shape, not read back out of the object.
        cal = a_calendar()
        assert cal.data_fingerprint() == canonical_hash({
            "tz": "America/New_York",
            "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"],
                          "open": "09:30", "close": "16:00"}],
            "holidays": ["2026-01-15"],
            "special_closes": [{"date": "2026-01-16", "close": "13:00"}],
            "blackouts": [],
            "after_open_s": 0,
            "before_close_s": 0,
        })


# ---------------------------------------------------------------------------
# The library itself, when the host has it
# ---------------------------------------------------------------------------


class TestTheRealLibrary:
    @pytest.fixture
    def library(self):
        return pytest.importorskip("exchange_calendars")

    def test_a_published_exchange_materialises_and_answers(self, library):
        cal = ExchangeCalendar({
            "exchange": "XNYS",
            "bounds": {"from": "2026-01-02", "until": "2026-03-31"},
        })
        assert cal.tz_name == "America/New_York"
        assert cal.is_open(ms("2026-01-02", "10:00")) is True
        assert cal.is_open(ms("2026-01-02", "08:00")) is False
        assert cal.is_open(ms("2026-01-01", "10:00")) is False  # New Year's Day
        assert isinstance(cal.data_fingerprint(), str)

    def test_an_unknown_exchange_code_refuses_by_name(self, library):
        with pytest.raises(ProductionError, match="NOSUCH"):
            ExchangeCalendar({
                "exchange": "NOSUCH",
                "bounds": {"from": "2026-01-02", "until": "2026-01-09"},
            })

    def test_bounds_the_library_cannot_cover_refuse(self, library):
        with pytest.raises(ProductionError):
            ExchangeCalendar({
                "exchange": "XNYS",
                "bounds": {"from": "1600-01-02", "until": "1600-03-31"},
            })
