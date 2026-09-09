"""Materialize policy-free, deterministic release-rotation time contracts.

A caller provides every temporal choice explicitly, then pins the calendar
and its bounded manifest before another layer consumes it. The value objects
keep UTC normalization, half-open boundaries, zero-embargo representation,
and canonical identity in one stdlib-only home without training, data access,
release promotion, deployment, market-calendar, or wall-clock behavior.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dskit.pipeline.stages import is_sha256hex

__all__ = [
    "HalfOpenInterval",
    "ReleaseRotationCalendar",
    "ReleaseRotationWindow",
]

_SCHEMA_VERSION = 1
_UTC = timezone.utc


def _utc_instant(value, name):
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime, got {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    try:
        return value.astimezone(_UTC)
    except OverflowError as error:
        raise ValueError(f"{name} cannot be normalized to UTC") from error


def _duration_us(value, name, *, positive):
    if not isinstance(value, timedelta):
        raise ValueError(f"{name} must be a timedelta, got {value!r}")
    if positive and value <= timedelta(0):
        raise ValueError(f"{name} must be positive")
    if not positive and value < timedelta(0):
        raise ValueError(f"{name} must not be negative")
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


def _canonical_digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _instant_text(value):
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class HalfOpenInterval:
    """One non-empty, timezone-aware half-open interval in normalized UTC.

    Parameters
    ----------
    start : datetime
        Inclusive timezone-aware boundary.
    end : datetime
        Exclusive timezone-aware boundary, strictly after start.

    Raises
    ------
    ValueError
        If either boundary is naive, not a datetime, or the interval is empty
        or reversed.

    Examples
    --------
    Construct one UTC interval::

        interval = HalfOpenInterval(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        interval.to_obj()["end_exclusive"]  # "2026-01-02T00:00:00Z"
    """

    start: datetime
    end: datetime

    def __post_init__(self):
        """Normalize interval endpoints and enforce their half-open ordering.

        Raises
        ------
        ValueError
            If normalization fails or end is not strictly after start.
        """
        start = _utc_instant(self.start, "start")
        end = _utc_instant(self.end, "end")
        if end <= start:
            raise ValueError("interval end must be after start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_obj(self):
        """Return the canonical JSON-ready half-open representation.

        Returns
        -------
        dict
            Inclusive start and exclusive end strings in normalized UTC.
        """
        return {"start": _instant_text(self.start), "end_exclusive": _instant_text(self.end)}


@dataclass(frozen=True, slots=True)
class ReleaseRotationWindow:
    """One pinned release instant with joined training and embargo intervals.

    Parameters
    ----------
    index : int
        Non-negative release index within its calendar.
    calendar_sha256 : str
        Exact lowercase hexadecimal SHA-256 digest of the calendar contract.
    release_at : datetime
        Timezone-aware release instant.
    training : HalfOpenInterval
        Non-empty training interval.
    embargo : HalfOpenInterval or None
        Positive embargo interval ending at release_at, or None for zero
        embargo when training ends at release_at.

    Raises
    ------
    ValueError
        If identity, interval types, or interval joins are invalid.

    Examples
    --------
    Construct a zero-embargo window::

        training = HalfOpenInterval(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        window = ReleaseRotationWindow(0, "a" * 64, training.end, training, None)
        window.embargo is None  # True
    """

    index: int
    calendar_sha256: str
    release_at: datetime
    training: HalfOpenInterval
    embargo: HalfOpenInterval | None

    def __post_init__(self):
        """Normalize release_at and enforce the window identity and joins.

        Raises
        ------
        ValueError
            If index, digest, boundaries, or zero-embargo representation is
            invalid.
        """
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("window index must be a non-negative int")
        if not is_sha256hex(self.calendar_sha256):
            raise ValueError("calendar_sha256 must be a lowercase SHA-256 string")
        release_at = _utc_instant(self.release_at, "release_at")
        if not isinstance(self.training, HalfOpenInterval):
            raise ValueError("training must be a HalfOpenInterval value")
        if self.embargo is not None and not isinstance(self.embargo, HalfOpenInterval):
            raise ValueError("embargo must be a HalfOpenInterval value or None")
        if self.embargo is None and self.training.end != release_at:
            raise ValueError("training must end at a zero-embargo release instant")
        if self.embargo is not None and (
            self.training.end != self.embargo.start or self.embargo.end != release_at
        ):
            raise ValueError("window intervals must join at the release instant")
        object.__setattr__(self, "release_at", release_at)

    @property
    def id(self):
        """Return this window stable, calendar-bound identifier.

        Returns
        -------
        str
            Index-prefixed identifier with a digest-derived suffix.
        """
        suffix = _canonical_digest(self._identity_obj())[:16]
        return f"rotation-{self.index:06d}-{suffix}"

    def _identity_obj(self):
        return {
            "calendar_sha256": self.calendar_sha256,
            "index": self.index,
            "release_at": _instant_text(self.release_at),
            "training": self.training.to_obj(),
            "embargo": None if self.embargo is None else self.embargo.to_obj(),
        }

    def to_obj(self):
        """Return the JSON-ready window representation with its stable id.

        Returns
        -------
        dict
            Window identity, UTC boundaries, and null when embargo is zero.
        """
        return {"id": self.id, **self._identity_obj()}


@dataclass(frozen=True, slots=True)
class ReleaseRotationCalendar:
    """Materialize an explicit, bounded cadence of non-overlapping windows.

    Parameters
    ----------
    anchor : datetime
        Timezone-aware release instant for index zero.
    cadence : timedelta
        Strictly positive distance between releases.
    trailing_training_span : timedelta
        Strictly positive training duration before each embargo.
    embargo : timedelta
        Non-negative duration immediately before each release.

    Raises
    ------
    ValueError
        If instants or durations are invalid, rotation windows overlap, or the
        first window underflows datetime.

    Examples
    --------
    Construct a weekly calendar with one day of embargo::

        calendar = ReleaseRotationCalendar(
            anchor=datetime(2026, 1, 8, tzinfo=timezone.utc),
            cadence=timedelta(days=7),
            trailing_training_span=timedelta(days=5),
            embargo=timedelta(days=1),
        )
        calendar.to_obj()["embargo_us"]  # 86400000000
    """

    anchor: datetime
    cadence: timedelta
    trailing_training_span: timedelta
    embargo: timedelta

    def __post_init__(self):
        """Normalize anchor and validate the immutable calendar contract.

        Raises
        ------
        ValueError
            If an instant or duration is invalid, rotations overlap, or the
            first training interval underflows datetime.
        """
        anchor = _utc_instant(self.anchor, "anchor")
        cadence_us = _duration_us(self.cadence, "cadence", positive=True)
        span_us = _duration_us(self.trailing_training_span, "trailing_training_span", positive=True)
        embargo_us = _duration_us(self.embargo, "embargo", positive=False)
        if span_us + embargo_us > cadence_us:
            raise ValueError("trailing_training_span plus embargo would overlap rotations")
        try:
            anchor - self.trailing_training_span - self.embargo
        except OverflowError as error:
            raise ValueError("anchor underflows its first window") from error
        object.__setattr__(self, "anchor", anchor)

    def to_obj(self):
        """Return JSON-ready identity material for this calendar.

        Returns
        -------
        dict
            Schema version, normalized anchor, and exact duration counts.
        """
        return {
            "schema_version": _SCHEMA_VERSION,
            "anchor": _instant_text(self.anchor),
            "cadence_us": _duration_us(self.cadence, "cadence", positive=True),
            "trailing_training_span_us": _duration_us(
                self.trailing_training_span, "trailing_training_span", positive=True
            ),
            "embargo_us": _duration_us(self.embargo, "embargo", positive=False),
        }

    @property
    def digest(self):
        """Return the stable SHA-256 identity of this calendar contract.

        Returns
        -------
        str
            Lowercase hexadecimal SHA-256 digest of to_obj output.
        """
        return _canonical_digest(self.to_obj())

    def materialize(self, start, end_exclusive, *, max_windows):
        """Return every release in one bounded half-open request range.

        Parameters
        ----------
        start : datetime
            Inclusive timezone-aware release-range boundary at or after anchor.
        end_exclusive : datetime
            Exclusive timezone-aware release-range boundary after start.
        max_windows : int
            Positive cap on materialized windows; booleans are refused.

        Returns
        -------
        tuple of ReleaseRotationWindow
            Ordered windows whose release instants lie in [start, end_exclusive).

        Raises
        ------
        ValueError
            If request boundaries or cap are invalid, count exceeds the cap, or
            datetime arithmetic overflows.
        """
        start, end, first, count = self._request(start, end_exclusive, max_windows)
        del start, end
        return tuple(self._window(index) for index in range(first, first + count))

    def manifest(self, start, end_exclusive, *, max_windows):
        """Return a JSON-ready, digest-pinned rendering of one request.

        Parameters
        ----------
        start : datetime
            Inclusive timezone-aware release-range boundary at or after anchor.
        end_exclusive : datetime
            Exclusive timezone-aware release-range boundary after start.
        max_windows : int
            Positive cap on materialized windows; booleans are refused.

        Returns
        -------
        dict
            Calendar digest, request bounds, JSON-ready windows, and manifest
            digest.

        Raises
        ------
        ValueError
            If the equivalent materialization request is invalid.
        """
        start, end, first, count = self._request(start, end_exclusive, max_windows)
        windows = [self._window(index).to_obj() for index in range(first, first + count)]
        manifest = {
            "schema_version": _SCHEMA_VERSION,
            "calendar_sha256": self.digest,
            "start": _instant_text(start),
            "end_exclusive": _instant_text(end),
            "windows": windows,
        }
        return {**manifest, "manifest_sha256": _canonical_digest(manifest)}

    def _request(self, start, end_exclusive, max_windows):
        start = _utc_instant(start, "start")
        end = _utc_instant(end_exclusive, "end_exclusive")
        if start < self.anchor:
            raise ValueError("start must not precede the anchor")
        if end <= start:
            raise ValueError("end_exclusive must be after start")
        if isinstance(max_windows, bool) or not isinstance(max_windows, int) or max_windows < 1:
            raise ValueError("max_windows must be a positive int")
        cadence_us = _duration_us(self.cadence, "cadence", positive=True)
        start_us = _duration_us(start - self.anchor, "start offset", positive=False)
        end_us = _duration_us(end - self.anchor, "end offset", positive=False)
        first = (start_us + cadence_us - 1) // cadence_us
        last = (end_us - 1) // cadence_us
        count = max(0, last - first + 1)
        if count > max_windows:
            raise ValueError(f"materialization count {count} exceeds max_windows {max_windows}")
        return start, end, first, count

    def _window(self, index):
        try:
            release_at = self.anchor + self.cadence * index
            training_end = release_at - self.embargo
            training_start = training_end - self.trailing_training_span
        except OverflowError as error:
            raise ValueError("materialization overflows datetime") from error
        training = HalfOpenInterval(training_start, training_end)
        embargo = (
            None if self.embargo == timedelta(0) else HalfOpenInterval(training_end, release_at)
        )
        return ReleaseRotationWindow(index, self.digest, release_at, training, embargo)
