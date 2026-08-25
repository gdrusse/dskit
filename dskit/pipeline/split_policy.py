"""Split ASSIGNMENT POLICY — WHICH INSTANT a record is cut on.

A split config answers *where the cuts are* (``train_end_ms``,
``val_end_ms``, ``test_end_ms``). That is only half the question. The other
half — *which instant does a given record get compared against those cuts* —
was hardcoded for the whole life of the toolkit: the record's own
``asof_ms``. This module makes it a DECLARED POLICY with a registry, exactly
as ``kind`` is a declared tag with a registry (:data:`SPLIT_KINDS`).

Why it matters
--------------
Project doctrine is that **the event is the independence unit** — every
score groups by ``record.cluster`` (the event), the stat test bootstraps
over clusters, and ``min_events`` counts clusters. But under the
record-instant rule a long-lived event whose leads span a cut contributes
records to TWO splits. When that cut is ``val_end_ms`` the same event both
selects the model and scores it. Measured on one project's real store, a
long-horizon series (events ~165h long) straddled on 1 of 7 events — 14%.

Note that :class:`~dskit.pipeline.base.RandomSplitConfig` never had this
defect: it hashes ``record.cluster``, so it is event-atomic by construction.
Only the TIME family assigns per record, which is why the policy lands on
the time family's instant selection.

The policies
------------
A policy is one pure function, ``instant_of(frame, bounds)``, returning the
epoch-ms instant the cuts are applied to (or ``None`` to drop the record).
``frame`` is the duck-typed split frame every call site already builds
(``asof_ms`` + ``cluster``); ``bounds`` is the resolved
``cluster -> EventBounds`` map, or ``None`` when the run never resolved one.

``record``
    The record's own ``asof_ms``. The historical behaviour and the DEFAULT,
    so no existing run changes. Leaks across cuts by construction.
``event-close``
    Every record of an event is cut on the event's LAST observed instant, so
    an event that opens in train and closes in val lands WHOLLY IN VAL.
``event-open``
    Every record of an event is cut on the event's FIRST observed instant, so
    that same event lands WHOLLY IN TRAIN.

Why ``event-close`` is the event-atomic default (and ``event-open`` is not)
--------------------------------------------------------------------------
Both make an event atomic, so both close the straddle. They differ in which
direction the event moves, and the two directions are NOT symmetric:

* ``event-close`` only ever WITHHOLDS data from the earlier split. The model
  is fit on train; an event promoted to val is simply not fit on. Nothing
  from after a cut enters the block before it.
* ``event-open`` DRAGS THE FUTURE BACKWARD. An event demoted to train keeps
  its post-cut leads, so the fit consumes instants from inside the selection
  (or test) window. That is a causality violation — strictly worse than the
  straddle it fixes.

So ``event-close`` is the safe direction and ``event-open`` is registered
for completeness (walk-forward studies where the open is the decision
instant), never as a recommendation. This also agrees with event-atomic
splitters an application may already run, which assign on the event's close
instant — the engine and its siblings cut the same way.

What this does NOT do
---------------------
Making an event atomic removes the straddle. It does not PURGE: a test event
that opened before the test cut is still wholly in test while carrying leads
observed during the val window. A stricter remedy drops such an event
outright (a purged test set). That is a different axis (drop vs move) and a
future registration here — the seam is built for it, this module does not
claim it.

Adding a policy
---------------
:func:`register_split_policy` — the same shape as
:func:`~dskit.pipeline.base.register_transform_kind`: a name, the
behaviour, duplicates raise. A third policy is a registration, not a
rewrite.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "DEFAULT_SPLIT_POLICY",
    "EVENT_ATOMIC_POLICIES",
    "EventBounds",
    "SPLIT_POLICIES",
    "event_bounds_from_records",
    "merge_event_bounds",
    "policy_instant",
    "register_split_policy",
    "split_policy",
    "straddle_report",
]

#: The policy every split carries unless the DOCUMENT declares otherwise.
#: Historical behaviour, kept as the default deliberately: moving it would
#: silently re-cut every run that ever ran (see ``TimeSplitConfig.to_obj``).
DEFAULT_SPLIT_POLICY = "record"


@dataclass(frozen=True, slots=True)
class EventBounds:
    """One event's OBSERVED extent: first and last instant it was seen.

    Observed, never assumed. One project learned this the hard way — a
    fixed duration cap under-purged events built with 200h windows, so the
    honest key is the min/max actually present in the store.
    """

    open_ms: int
    close_ms: int

    def __post_init__(self):
        for name in ("open_ms", "close_ms"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"EventBounds.{name} must be an int, got {v!r}")
        if self.close_ms < self.open_ms:
            raise ValueError(
                f"EventBounds must not run backward: open_ms={self.open_ms} > "
                f"close_ms={self.close_ms}"
            )

    @property
    def span_ms(self) -> int:
        return self.close_ms - self.open_ms


#: ``name -> {"instant_of": fn, "needs_bounds": bool, "doc": str}``. The
#: toolkit ships three; anything else registers. Mirrors
#: :data:`~dskit.pipeline.base.TRANSFORM_KINDS`.
SPLIT_POLICIES: dict = {}


def register_split_policy(name, instant_of, *, needs_bounds, doc="") -> None:
    """Bind a policy ``name`` to its ``instant_of(frame, bounds)``.

    ``needs_bounds`` declares whether the policy reads the event-bounds map;
    a split validating itself uses it to refuse an unresolvable run EARLY,
    rather than at the first record. Duplicates raise, as everywhere else in
    the toolkit."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"split policy name must be a non-empty string, got {name!r}")
    if not callable(instant_of):
        raise ValueError(
            f"instant_of for {name!r} must be callable, got {instant_of!r}"
        )
    if not isinstance(needs_bounds, bool):
        raise ValueError(
            f"needs_bounds for {name!r} must be a bool, got {needs_bounds!r}"
        )
    if name in SPLIT_POLICIES:
        raise ValueError(f"split policy {name!r} is already registered")
    SPLIT_POLICIES[name] = {
        "instant_of": instant_of,
        "needs_bounds": needs_bounds,
        "doc": doc,
    }


def split_policy(name) -> dict:
    """The registered policy, failing loudly and naming the family."""
    entry = SPLIT_POLICIES.get(name)
    if entry is None:
        raise ValueError(
            f"splits.policy: unknown policy {name!r} — known policies: "
            f"{sorted(SPLIT_POLICIES)}"
        )
    return entry


def policy_instant(name, frame, bounds):
    """The instant ``frame`` is cut on under policy ``name``."""
    return split_policy(name)["instant_of"](frame, bounds)


# --- the three shipped policies -------------------------------------------


def _instant_record(frame, bounds):
    """The record's own decision instant. Bounds unused."""
    return frame.asof_ms


def _event_instant(frame, bounds, edge):
    if bounds is None:
        raise ValueError(
            f"splits.policy 'event-{edge}' assigns by the EVENT's boundary, but "
            "no event-bounds map was resolved for this run. Bind one with "
            "TimeSplitConfig.with_event_bounds(...) (the driver does this from "
            "the data nodes' event_bounds() hook). Refusing rather than falling "
            "back to the record instant, which is the very leak the policy exists "
            "to close"
        )
    got = bounds.get(frame.cluster)
    if got is None:
        raise ValueError(
            f"splits.policy 'event-{edge}': no bounds for cluster "
            f"{frame.cluster!r}. The bounds map must cover every event the run "
            "assigns; a missing event silently reverting to its record instant "
            "would reintroduce the straddle for exactly the events nobody "
            "measured"
        )
    return got.close_ms if edge == "close" else got.open_ms


def _instant_event_close(frame, bounds):
    """The event's LAST observed instant — an event straddling a cut moves
    WHOLLY FORWARD into the later split. Withholds data from the earlier
    split; never drags post-cut instants backward."""
    return _event_instant(frame, bounds, "close")


def _instant_event_open(frame, bounds):
    """The event's FIRST observed instant — an event straddling a cut moves
    WHOLLY BACKWARD into the earlier split.

    Closes the straddle but pulls post-cut leads into the earlier block, so
    a fit consumes instants from the selection window. Registered because
    walk-forward designs where the OPEN is the decision instant need it;
    NOT recommended for train/val/test."""
    return _event_instant(frame, bounds, "open")


register_split_policy(
    "record",
    _instant_record,
    needs_bounds=False,
    doc="assign by the record's own asof_ms (historical behaviour; leaks)",
)
register_split_policy(
    "event-close",
    _instant_event_close,
    needs_bounds=True,
    doc="assign every record of an event by the event's last observed instant",
)
register_split_policy(
    "event-open",
    _instant_event_open,
    needs_bounds=True,
    doc="assign every record of an event by the event's first observed instant",
)

#: Policies that make an event atomic — the set a straddle audit expects to
#: report zero for. A new event-atomic policy adds itself here.
EVENT_ATOMIC_POLICIES = frozenset({"event-close", "event-open"})


# --- deriving the bounds ---------------------------------------------------


def _field(record, name):
    """Attr-or-key access, so venue records and plain dicts share one path."""
    if hasattr(record, name):
        return getattr(record, name)
    if isinstance(record, Mapping) and name in record:
        return record[name]
    raise ValueError(f"record {record!r} carries no {name!r} (attribute or key)")


def event_bounds_from_records(records) -> dict:
    """``cluster -> EventBounds`` from a record stream: min/max ``asof_ms``.

    OBSERVED extent, computed from the same records the run will assign, so
    the bounds can never disagree with the data being cut. One pass, no
    store read, no new data path."""
    lo: dict = {}
    hi: dict = {}
    for record in records:
        cluster = _field(record, "cluster")
        t = _field(record, "asof_ms")
        if isinstance(t, bool) or not isinstance(t, int):
            raise ValueError(
                f"event bounds need an int asof_ms, got {t!r} for cluster "
                f"{cluster!r}"
            )
        if cluster not in lo or t < lo[cluster]:
            lo[cluster] = t
        if cluster not in hi or t > hi[cluster]:
            hi[cluster] = t
    return {c: EventBounds(open_ms=lo[c], close_ms=hi[c]) for c in lo}


def merge_event_bounds(*maps) -> dict:
    """Union of several bounds maps, widening any cluster seen in more than
    one.

    Unlike a trailing split's ANCHOR — where two sources offering an edge is
    an ambiguity the driver refuses to resolve — two sources offering bounds
    is not ambiguous: the keys are event tickers, so the union is exact, and
    a cluster genuinely present in both is widened to cover both views
    rather than arbitrarily taking one."""
    out: dict = {}
    for m in maps:
        if not m:
            continue
        for cluster, b in m.items():
            prev = out.get(cluster)
            out[cluster] = (
                b
                if prev is None
                else EventBounds(
                    open_ms=min(prev.open_ms, b.open_ms),
                    close_ms=max(prev.close_ms, b.close_ms),
                )
            )
    return out


# --- the audit -------------------------------------------------------------


def straddle_report(splits, records, *, bounds=None) -> dict:
    """Count the events that land in more than one split — the leak, as a
    number.

    A straddle count of ZERO is the proof an event-atomic policy worked; a
    non-zero count under ``record`` is exactly the leak the owner should
    see. ``boundaries`` names WHICH cut each event straddles, because
    ``val|test`` is the one that puts an event in both selection and
    scoring, and is therefore the serious one.

    ``rows_by_split`` is reported alongside, because moving events between
    splits moves training rows with them: a policy that fixes the leak while
    emptying train is not a fix, and only the row counts show that.

    Returns a plain JSON-safe dict, so any report sink can carry it.
    """
    records = list(records)
    if bounds is None:
        bounds = getattr(splits, "event_bounds", None)
    if bounds is None:
        bounds = event_bounds_from_records(records)

    policy = getattr(splits, "policy", DEFAULT_SPLIT_POLICY)
    per_event: dict = {}
    rows_by_split: dict = {}
    unassigned = 0
    for record in records:
        cluster = _field(record, "cluster")
        frame = _SplitFrame(_field(record, "asof_ms"), cluster)
        split = splits.split_of(frame)
        if split is None:
            unassigned += 1
            continue
        per_event.setdefault(cluster, set()).add(split)
        rows_by_split[split] = rows_by_split.get(split, 0) + 1

    boundaries: dict = {}
    straddlers = []
    for cluster, splits_seen in per_event.items():
        if len(splits_seen) < 2:
            continue
        straddlers.append(cluster)
        ordered = [s for s in ("train", "val", "test") if s in splits_seen]
        for a, b in zip(ordered, ordered[1:]):
            key = f"{a}|{b}"
            boundaries[key] = boundaries.get(key, 0) + 1

    return {
        "policy": policy,
        "event_atomic": policy in EVENT_ATOMIC_POLICIES,
        "events": len(per_event),
        "events_straddling": len(straddlers),
        "boundaries": boundaries,
        "straddling_events": sorted(straddlers)[:20],
        "rows_by_split": rows_by_split,
        "rows_unassigned": unassigned,
        "bounds_events": len(bounds),
    }


class _SplitFrame:
    """The duck-typed frame every ``split_of`` reads: an instant and a
    cluster. A real class so a missing field fails as an AttributeError,
    not a silent ``None`` — the same shape the venue adapters build."""

    __slots__ = ("asof_ms", "cluster")

    def __init__(self, asof_ms, cluster):
        self.asof_ms = asof_ms
        self.cluster = cluster
