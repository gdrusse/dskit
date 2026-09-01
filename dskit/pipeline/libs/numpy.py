"""The tier-2 numpy pack — generic array feature engineering (docs/25 §2).

Two doorways, both SUBCLASS HOOKS on the ``PyomoSolve`` pattern: a
project implements ``apply(arrays, params)`` and the base does the rest —
record→array lifting, grouping, ordering, GAP-AWARE framing, the
writeback (or the feature rows), and the CAUSALITY GUARD. Raw functions
are never referenceable (D-145), and the ``"fn": "pkg.mod:function"``
variant was ruled OUT for this pack (I-228, resolved subclass-only): a
function named by path is code OUTSIDE the run identity — edit the math,
and the hash says nothing changed.

* :class:`ArrayMap` (role ``transform``, outputs ``("records",)``) —
  ``apply``'s columns are rewritten ENVELOPE numeric fields; the base
  rebuilds each record immutably (``dataclasses.replace`` / dict copy)
  and returns the stream in its original order.
* :class:`ArrayFeatures` (role ``tensor``, outputs ``("rows",
  "metrics")``) — the same lifting, but ``apply``'s columns become
  FEATURE ROWS (one dict per record) for downstream fit/train nodes.
* :class:`ReturnWindows` — the concrete windowing member built on the
  ops below: one-step returns, ``lookback`` lags, and a forward label.

**Wiring is by import path — deliberately.** :data:`NODE_KINDS` is empty
and nothing here registers: the two bases are abstract (a registry or an
import-path resolve refuses them by name), and a user project's subclass
needs no registration either — ``"uses": "their.module:TheirSubclass"``
resolves directly. Subclassing and referencing by path IS the wiring.
:class:`LogMid` and :class:`TrailingReturns` are the concrete reference
subclasses documents, examples and tests point at.

**Every lifting field is DECLARED, and read through an accessor**
(ADR-0040). ``group_field``, ``order_field``, ``fields`` and ``max_gap``
say what the stream is keyed on, ordered by, lifted from, and framed
with; :class:`ArrayFeatures` adds ``carry_fields``, ``require_fields``
and ``drop_incomplete`` for the row it emits, and the guard's own
``causality_check``/``cuts`` read the same way. Each is read through a
one-line public accessor, so a project whose documents speak different
SPELLINGS overrides the accessor instead of bending its vocabulary to
this pack's. Every default is a module constant named ONCE — read by the
knob gate AND by the run path, never a literal in either — and
reproduces the pre-ADR-0040 behaviour exactly.

**An accessor override NARROWS ``_PARAMS``, and hardcoding IS an
override.** A subclass that answers a knob from its own vocabulary must
DROP that knob (:func:`narrow_params`), or default-deny would accept a
value the run discards — the shape that let a document write
``"fields": ["bid"]`` at :class:`LogMid`, validate clean, and die at
execute with a bare ``KeyError``. The rule is not merely tested: it is a
runtime refusal (:func:`accessor_narrowing_problems`, reported through
``validate_params``). Because ``validate_params`` is a classmethod it
cannot evaluate an accessor, so **every per-knob check is guarded by
``if "<knob>" in cls._PARAMS``** — without that guard a narrowed
subclass would be refused for omitting a knob it does not have.

**Tier-1 truth is IMPORTED, never restated.** The envelope's price and
``lead_frac`` rules come from :mod:`dskit.pipeline.records`; a pack that
re-derives a core validator drifts the moment core loosens a bound
(audit HIGH-2, where the private copies failed silently through the
writeback pass-through).

**Gap-aware framing.** ``max_gap`` splits each ordered group into
SEGMENTS before any offset arithmetic, so no lag, lead or return ever
spans a session boundary: ``apply`` sees one segment at a time, and the
columns are concatenated back. Absent ``max_gap`` there is exactly one
segment per group — today's behaviour, unchanged.

**The causality guard is a mechanical SCREEN, not a proof** (knob
``causality_check``, default ``True``). A window may look BACKWARD only
— unless the class DECLARES how far forward a column reads
(``lookahead_columns``): a supervised label is legitimately forward, and
the honest treatment is to declare the horizon and hold the column to
it, never to exempt it. This generalizes the stage-list ruling (the
stream kinds are all pointwise; "a future windowed kind must preserve
that guarantee explicitly") to arbitrary array code, by PREFIX
CONSISTENCY: after ``apply`` runs on the full arrays, the base re-runs it
on truncated prefixes at deterministic cut points — the quarter, half and
three-quarter points AND ``n - 1``, no RNG; the tail cut is what catches
a leak confined to the last rows, the S1 #2 blind spot of the old
two-point grid — and REFUSES when any overlapping output moved. What a
causal ``apply`` computes at ``t`` cannot depend on rows after ``t``
(after ``t + h`` for a column declaring horizon ``h``), so removing the
future must not change it. Outputs must match exactly or nan-equal —
leading warm-up NaNs from trailing windows are legal and compare equal. A
``cuts`` knob (a list of distinct ints ``>= 1``) REPLACES the default
grid; whatever the grid, each cut is kept to a strict prefix
``1 <= cut < n`` per SEGMENT, and a cut past a segment's length checks
nothing there. Honesty about the bar: the guard can only refuse drift its
finitely many cut points expose — a transform keyed to lengths no cut
probes slips through — so it is a NECESSARY screen for the classic leaks,
never a sufficient proof of causality: passing means "not caught here",
and the subclass's code stays the thing to review. Three consequences
worth knowing: the guard needs a strict prefix, so a one-record segment
gives it no leverage; ``apply`` must handle ANY prefix of its input —
emit warm-up NaNs for short arrays instead of refusing them; and setting
``causality_check: false`` (huge arrays, twice-per-cut cost) is a
DECISION the document owns: the guard is the only mechanical check on the
subclass's code, and off means unchecked.

**Missing-field policy — pass through, never crash mid-stream.** A
record that cannot be lifted (no group string, no usable order value)
passes through UNTOUCHED in :class:`ArrayMap` and yields NO row in
:class:`ArrayFeatures`; a missing or non-numeric lifted field becomes
NaN; an ``apply`` output the envelope cannot hold (NaN/inf, a
non-positive price, a ``lead_frac`` outside (0, 1), a field the record
type lacks) leaves that record's field UNCHANGED. Everything is counted
and logged; none of it raises. The ONE exception is a non-empty input
whose records were ALL unlifted: a declared field matching nothing would
emit zero rows and exit 0, so that refuses by name. A row missing a
``require_fields`` id is skipped (a feature row with no identity is
unusable downstream) — the record still participates in the arrays, so
later trailing windows see it. A carried CLUSTER the envelope could not
hold rides as absent, because a dict record and an envelope are
interchangeable here and a random split buckets on that value. Those
three questions — the GROUP key a record is lifted under, a
``require_fields`` id, and the carried cluster — are one question ("is
this a usable identity") and they read one imported answer,
:func:`~dskit.pipeline.records.cluster_ok`: a private copy of it is how
one record gets three answers the day the envelope's rule widens.
Which positions participate at all is the
``keep_mask`` hook: the base keeps every lifted record, and a subclass
whose domain says otherwise ("a bar with no usable price is not a bar")
answers with a vectorized mask and the base compacts around it. Those
rejections are counted BEFORE compaction destroys the evidence and
reported as ``n_dropped`` and in the log line — a domain rule quietly
eating a stream (a vendor outage zeroing prices) must be visible
somewhere.

Subclass knobs: extend ``_PARAMS`` (which keeps the default-deny and the
conformance fuzz covering the new names) and override ``validate_params``,
calling ``super().validate_params(params)`` first —
:class:`TrailingReturns` is the worked example.

Import cost: stdlib only — numpy lives strictly inside the helpers
``run()`` calls at execute time (``tests/pipeline/test_purity.py``
enforces this twice, statically and with numpy blocked from a fresh
interpreter).
"""

from __future__ import annotations

import math
from abc import abstractmethod
from collections import deque, namedtuple
from dataclasses import is_dataclass, replace

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import (
    ASOF_FIELD,
    CLUSTER_FIELD,
    CONTRACT_FIELD,
    cluster_ok,
    lead_frac_ok,
    number_ok,
    price_ok,
)

__all__ = [
    "ArrayFeatures",
    "ArrayMap",
    "DEFAULT_CARRY_FIELDS",
    "DEFAULT_CAUSALITY_CHECK",
    "DEFAULT_CUTS",
    "DEFAULT_DROP_INCOMPLETE",
    "DEFAULT_FIELDS",
    "DEFAULT_GROUP_FIELD",
    "DEFAULT_LABEL_LEAD",
    "DEFAULT_LABEL_NAME",
    "DEFAULT_LAG_PREFIX",
    "DEFAULT_MAX_GAP",
    "DEFAULT_ORDER_FIELD",
    "DEFAULT_REQUIRE_FIELDS",
    "DEFAULT_RETURN_KIND",
    "LogMid",
    "NODE_KINDS",
    "RETURN_KINDS",
    "ReturnWindows",
    "TrailingReturns",
    "accessor_narrowing_problems",
    "lag",
    "lead",
    "log_return",
    "narrow_params",
    "pct_return",
    "rolling_max",
    "rolling_min",
    "rolling_std",
    "rolling_sum",
]

#: Sentinel for "the record carries no such field" — distinct from every
#: real value a field could hold (``None`` included).
_MISSING = object()

#: What a stream is keyed on when the document does not say: the
#: envelope's instrument id.
DEFAULT_GROUP_FIELD = "instrument"

#: What a stream is ordered by when the document does not say: the
#: envelope's decision instant, read from the envelope's own name
#: (:data:`~dskit.pipeline.records.ASOF_FIELD`) rather than retyped —
#: ``fitted.py`` defaults to the same field for the same rows.
DEFAULT_ORDER_FIELD = ASOF_FIELD

#: Numeric fields lifted into every per-group array set alongside the
#: order array. Missing/non-numeric values lift as NaN so the arrays stay
#: aligned one-entry-per-record.
DEFAULT_FIELDS = ("bid", "ask", "mid", "lead_frac")

#: No gap bound unless the document declares one — one segment per
#: group, which is the pre-ADR-0040 framing exactly.
DEFAULT_MAX_GAP = None

#: Record fields :class:`ArrayFeatures` copies onto every row. A feature
#: column may not take one of these names (it would silently clobber the
#: row's identity).
DEFAULT_CARRY_FIELDS = (
    DEFAULT_GROUP_FIELD, CONTRACT_FIELD, DEFAULT_ORDER_FIELD, CLUSTER_FIELD,
)

#: Identity fields a row must carry to be emitted at all — a row with no
#: identity is unusable downstream. The envelope's own name again
#: (:data:`~dskit.pipeline.records.CONTRACT_FIELD`): a fitted transform
#: cuts on the identity these rows carry, so the two sides are renamed
#: together or not at all.
DEFAULT_REQUIRE_FIELDS = (CONTRACT_FIELD,)

#: Keep every row, warm-up NaNs included; a supervised windowing document
#: turns this on to drop the rows whose window or label is incomplete.
#: A TRAINING emission knob only — :meth:`ArrayFeatures.latest_rows`
#: requires completeness unconditionally, because a serving vector that
#: is half warm-up is not the truth about now.
DEFAULT_DROP_INCOMPLETE = False

#: The causality guard runs unless the document turns it off, and the
#: default grid decides its own cut points. Both are named ONCE: the
#: knob gate and the run path read these, never a bare literal.
DEFAULT_CAUSALITY_CHECK = True
DEFAULT_CUTS = None

#: :class:`ReturnWindows` defaults: one-step LOG returns, a one-step
#: forward label, and neutral column spellings a domain subclass renames.
DEFAULT_RETURN_KIND = "log"
DEFAULT_LABEL_LEAD = 1
DEFAULT_LAG_PREFIX = "lag_"
DEFAULT_LABEL_NAME = "label"


# ---------------------------------------------------------------------------
# The ops — vectorized, composable, and what a subclass's apply() calls
# ---------------------------------------------------------------------------


def lag(values, n):
    """Shift a series BACKWARD by ``n`` positions (look at the past).

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    n : int
        How many positions back to read; ``>= 0``. Position ``t`` of the
        result is ``values[t - n]``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``, NaN for the first
        ``n`` positions (they have no such past).

    Raises
    ------
    ValueError
        When ``n`` is negative — a backward shift by a negative amount is
        :func:`lead`, and spelling it this way would hide a lookahead.
    """
    import numpy as np

    values = np.asarray(values, dtype=np.float64)
    if n < 0:
        raise ValueError(f"lag needs n >= 0, got {n!r} — a forward read is lead()")
    if n == 0:
        return values.copy()
    out = np.full(values.shape, np.nan)
    if n < len(values):
        out[n:] = values[:-n]
    return out


def lead(values, n):
    """Shift a series FORWARD by ``n`` positions (look at the future).

    The label op: position ``t`` of the result is ``values[t + n]``. A
    column built with this reads ahead BY CONSTRUCTION, so the class
    emitting it must declare the horizon through ``lookahead_columns``
    — the causality guard holds it to that horizon and nothing more.

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    n : int
        How many positions forward to read; ``>= 0``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``, NaN for the last
        ``n`` positions (they have no such future).

    Raises
    ------
    ValueError
        When ``n`` is negative.
    """
    import numpy as np

    values = np.asarray(values, dtype=np.float64)
    if n < 0:
        raise ValueError(f"lead needs n >= 0, got {n!r} — a backward read is lag()")
    if n == 0:
        return values.copy()
    out = np.full(values.shape, np.nan)
    if n < len(values):
        out[:-n] = values[n:]
    return out


def log_return(values, n=1):
    """Take the ``n``-step LOG return of a positive series.

    Parameters
    ----------
    values : array-like of float
        One segment's prices, ordered ascending.
    n : int
        The step, ``>= 1``. Position ``t`` is
        ``log(values[t] / values[t - n])``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``, NaN for the first
        ``n`` positions and wherever either end of the ratio is not a
        positive number.
    """
    import numpy as np

    values = np.asarray(values, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.log(values / lag(values, n))
    return np.where(np.isfinite(out), out, np.nan)


def pct_return(values, n=1):
    """Take the ``n``-step SIMPLE return of a series.

    Parameters
    ----------
    values : array-like of float
        One segment's prices, ordered ascending.
    n : int
        The step, ``>= 1``. Position ``t`` is
        ``values[t] / values[t - n] - 1``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``, NaN for the first
        ``n`` positions and wherever the ratio is not finite.
    """
    import numpy as np

    values = np.asarray(values, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = values / lag(values, n) - 1.0
    return np.where(np.isfinite(out), out, np.nan)


def _rolling_width(width):
    """Refuse a non-positive window the same way ``lag`` refuses n < 0."""
    if isinstance(width, bool) or not isinstance(width, int) or width < 1:
        raise ValueError(f"rolling width must be an int >= 1, got {width!r}")
    return width


def rolling_sum(values, width):
    """Causal window sum; NaN wherever the window contains a NaN.

    Matches ``numpy.sum`` on a sliding window, not ``nansum``. The first
    ``width - 1`` positions are NaN (the window is not full). Extra RAM
    is O(n), not O(n * width).

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    width : int
        Window length, ``>= 1``. Position ``t`` sums
        ``values[t - width + 1 : t + 1]``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``.

    Raises
    ------
    ValueError
        When ``width`` is not an int ``>= 1``.

    Examples
    --------
    A 3-wide sum of ``[1, 2, 3, 4]``::

        rolling_sum([1.0, 2.0, 3.0, 4.0], 3)
        # -> array([nan, nan, 6.0, 9.0])
    """
    import numpy as np

    width = _rolling_width(width)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    n = values.size
    out = np.full(n, np.nan)
    if n < width:
        return out
    nans = np.isnan(values)
    filled = np.where(nans, 0.0, values)
    count = np.concatenate(([0], np.cumsum(nans, dtype=np.int64)))
    total = np.concatenate(([0.0], np.cumsum(filled)))
    end = np.arange(width, n + 1)
    start = end - width
    tail = total[end] - total[start]
    tail[count[end] - count[start] > 0] = np.nan
    out[width - 1:] = tail
    return out


def rolling_std(values, width, ddof=0):
    """Causal window standard deviation, NaNs skipped (``nanstd``).

    Two-pass via cumsums of count, sum, and sum-of-squares so a 1170-wide
    window on a million-bar tape stays O(n) RAM. Matches
    ``numpy.nanstd(..., ddof=ddof)`` on each full window.

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    width : int
        Window length, ``>= 1``.
    ddof : int, optional
        Delta degrees of freedom, default 0 (population). A window
        with ``count <= ddof`` is NaN.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``. The first
        ``width - 1`` positions are NaN.

    Raises
    ------
    ValueError
        When ``width`` is not an int ``>= 1``, or ``ddof`` is negative.

    Examples
    --------
    Population std of three 1s is 0::

        rolling_std([1.0, 1.0, 1.0], 3)
        # -> array([nan, nan, 0.0])
    """
    import numpy as np

    width = _rolling_width(width)
    if isinstance(ddof, bool) or not isinstance(ddof, int) or ddof < 0:
        raise ValueError(f"ddof must be an int >= 0, got {ddof!r}")
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    n = values.size
    out = np.full(n, np.nan)
    if n < width:
        return out
    valid = ~np.isnan(values)
    filled = np.where(valid, values, 0.0)
    count = np.concatenate(([0], np.cumsum(valid, dtype=np.int64)))
    total = np.concatenate(([0.0], np.cumsum(filled)))
    total_sq = np.concatenate(([0.0], np.cumsum(filled * filled)))
    end = np.arange(width, n + 1)
    start = end - width
    cnt = count[end] - count[start]
    s = total[end] - total[start]
    s2 = total_sq[end] - total_sq[start]
    ok = cnt > ddof
    var = np.full(cnt.shape, np.nan)
    var[ok] = (s2[ok] - s[ok] * s[ok] / cnt[ok]) / (cnt[ok] - ddof)
    var[ok] = np.maximum(var[ok], 0.0)
    tail = np.full(cnt.shape, np.nan)
    tail[ok] = np.sqrt(var[ok])
    out[width - 1:] = tail
    return out


def rolling_max(values, width):
    """Causal window maximum; NaN wherever the window contains a NaN.

    Matches ``numpy.max`` on a sliding window. Extra RAM is O(width).

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    width : int
        Window length, ``>= 1``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``. The first
        ``width - 1`` positions are NaN.

    Raises
    ------
    ValueError
        When ``width`` is not an int ``>= 1``.

    Examples
    --------
    A 2-wide max::

        rolling_max([1.0, 3.0, 2.0], 2)
        # -> array([nan, 3.0, 3.0])
    """
    return _rolling_extrema(values, width, direction=1)


def rolling_min(values, width):
    """Causal window minimum; NaN wherever the window contains a NaN.

    Matches ``numpy.min`` on a sliding window. Extra RAM is O(width).

    Parameters
    ----------
    values : array-like of float
        One segment's values, ordered ascending.
    width : int
        Window length, ``>= 1``.

    Returns
    -------
    numpy.ndarray
        A float64 array the same length as ``values``. The first
        ``width - 1`` positions are NaN.

    Raises
    ------
    ValueError
        When ``width`` is not an int ``>= 1``.

    Examples
    --------
    A 2-wide min::

        rolling_min([1.0, 3.0, 2.0], 2)
        # -> array([nan, 1.0, 2.0])
    """
    return _rolling_extrema(values, width, direction=-1)


def _rolling_extrema(values, width, direction):
    """Monotonic-deque max (``direction=1``) or min (``direction=-1``)."""
    import numpy as np

    width = _rolling_width(width)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    n = values.size
    out = np.full(n, np.nan)
    if n < width:
        return out
    q = deque()
    nan_count = 0
    for i in range(n):
        v = values[i]
        if np.isnan(v):
            nan_count += 1
        else:
            while q and direction * values[q[-1]] <= direction * v:
                q.pop()
            q.append(i)
        if i >= width:
            if np.isnan(values[i - width]):
                nan_count -= 1
        while q and q[0] <= i - width:
            q.popleft()
        if i >= width - 1:
            if nan_count:
                out[i] = np.nan
            elif q:
                out[i] = values[q[0]]
    return out


#: The return vocabulary, a REGISTRY rather than an ``if kind ==`` chain:
#: :class:`ReturnWindows` looks its ``return_kind`` up here, and a pack
#: that grows a third convention adds an entry, not a branch.
RETURN_KINDS = {"log": log_return, "pct": pct_return}


# ---------------------------------------------------------------------------
# The accessor-narrowing rule
# ---------------------------------------------------------------------------


def narrow_params(params, *knobs):
    """Drop ``knobs`` from a ``_PARAMS`` tuple — the narrowing spelling.

    A subclass that overrides an accessor must drop that accessor's knob
    (module docstring); this is how, so the surviving tuple is derived
    from the base's rather than retyped and left to drift.

    Parameters
    ----------
    params : tuple of str
        The base class's ``_PARAMS``.
    *knobs : str
        Names to remove. A name that is not there is refused — it means
        the base moved and this call is stale.

    Returns
    -------
    tuple of str
        ``params`` without ``knobs``, order preserved.

    Raises
    ------
    ValueError
        When a named knob is not in ``params``.
    """
    missing = [k for k in knobs if k not in params]
    if missing:
        raise ValueError(
            f"cannot narrow away {missing} — not in {list(params)}; the base's "
            "knob set moved and this narrowing is stale"
        )
    return tuple(p for p in params if p not in knobs)


def _accessor_owner(cls, knob):
    """Name the most-base class in ``cls``'s MRO defining a callable ``knob``."""
    for base in reversed(cls.__mro__):
        if callable(vars(base).get(knob)):
            return base
    return None


def accessor_narrowing_problems(cls):
    """Why ``cls`` breaks the accessor-narrowing rule — empty when it does not.

    Reported through ``validate_params``, so the rule is a REFUSAL and
    not merely a convention: a class that answers a knob from its own
    vocabulary while still advertising the knob would let a document set
    a value the run discards.

    The knob set is DERIVED from the classes, never listed: each
    declared knob is looked up in the MRO, and the class that first
    defined its accessor is the owner to compare against. A listed table
    would cover exactly the knobs someone remembered to add — a knob
    that gained an accessor without an entry would get no refusal at
    all, and a SUBCLASS's own knobs (the shape every child writes) would
    never be covered by a pack-side table in the first place.

    Parameters
    ----------
    cls : type
        An :class:`_ArrayApply` subclass.

    Returns
    -------
    list of str
        One problem per knob that is both overridden and still declared.
    """
    problems = []
    for knob in cls._PARAMS:
        owner = _accessor_owner(cls, knob)
        if owner is None:
            continue  # a knob with no accessor — nothing to narrow
        if getattr(cls, knob) is getattr(owner, knob):
            continue  # the accessor is the owner's — the knob is live
        problems.append(
            f"{cls.__name__} overrides the {knob}() accessor but still "
            f"declares {knob!r} in _PARAMS — an overridden accessor "
            "NARROWS the knob set (numpy pack, ADR-0040): drop it with "
            "narrow_params(), or the document may set a value the run "
            "discards"
        )
    return problems


# ---------------------------------------------------------------------------
# Lifting, framing, and the apply contract
# ---------------------------------------------------------------------------

#: Envelope fields :class:`ArrayMap` may write back, each with the
#: acceptance rule the envelope itself enforces — IMPORTED from
#: ``records.py``, never restated (HIGH-2). A value that fails one leaves
#: the record's field UNCHANGED (the pass-through policy) instead of
#: exploding inside ``dataclasses.replace``.
_WRITEBACK = {
    "bid": price_ok,
    "ask": price_ok,
    "mid": price_ok,
    "lead_frac": lead_frac_ok,
}


def _field(record, name):
    """Read one field of a record, attr-or-key.

    THE accessor: ``record[name]`` for mappings, ``record.name`` for
    objects, :data:`_MISSING` when absent — dicts and
    ``MarketRecord``-like envelopes are interchangeable by construction
    (the ``kinds_flow`` convention).
    """
    if isinstance(record, dict):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


def _num(value) -> float:
    """Lift one cell to ``float`` by the envelope's own number rule.

    A real finite number rides as ``float``; anything else (absent,
    ``None``, bool, string, inf) becomes NaN — missing is data.
    """
    return float(value) if number_ok(value) else float("nan")


def _order_value(value):
    """Read the order key of one record, or ``None`` when it has none.

    The SAME rule :func:`_num` lifts a cell by
    (:func:`~dskit.pipeline.records.number_ok`), keeping the value's own
    type: an int order key stays an int, so a stream ordered on epoch
    milliseconds never rounds through a float on the way in.
    """
    return value if number_ok(value) else None


def _carried_column(name, values):
    """One carried field's COLUMN of row values, the envelope's rule applied.

    The CLUSTER field is normalized by :func:`cluster_ok` — imported,
    never restated: a dict record is interchangeable with an envelope
    everywhere else here, so a ``group`` the envelope would refuse must
    land the way the envelope would have had it (absent), not ride into
    a random split as a bucket of its own. Every other field rides as
    it is. A column, not a value, because the rule is decided once per
    FIELD where a per-value call decides it once per cell.
    """
    if name == CLUSTER_FIELD:
        return [v if cluster_ok(v) else None for v in values]
    return values


def _lift(records, group_field, order_field, fields):
    """Group, order and lift a record stream into per-group arrays.

    Parameters
    ----------
    records : list
        The input stream, dicts or attribute-bearing envelopes.
    group_field : str
        The field records are keyed on; a record whose value is not a
        non-empty string cannot be placed and is unlifted.
    order_field : str
        The field records are ordered by; the lifted array rides under
        THIS name, whatever it is. A record with no usable order value
        (:func:`_order_value`) is unlifted.
    fields : sequence of str
        Numeric fields lifted alongside; missing or non-numeric values
        become NaN.

    Returns
    -------
    tuple of (dict, list)
        ``groups`` maps the group value to ``(original_indices, arrays)``
        — indices into ``records`` in order (ties broken by stream
        position, so the sort is deterministic) and ``arrays`` per the
        ``apply`` contract — and ``unlifted`` lists the indices of the
        records that could not be placed: the pass-through set.
    """
    import numpy as np

    by_group = {}
    unlifted = []
    for idx, record in enumerate(records):
        # The two reads are spelled out rather than routed through
        # `_field`: this loop runs once per record on the 2M-bar
        # benchmark, and two function calls per record is the whole
        # difference between the port and the loop it replaced.
        if isinstance(record, dict):
            group = record.get(group_field)
            order = record.get(order_field)
        else:
            group = getattr(record, group_field, None)
            order = getattr(record, order_field, None)
        if type(order) is not int:  # the common case first; bool is not one
            order = _order_value(order)
        # `cluster_ok` decides what a usable identity IS — imported, never
        # restated (the tier rule): a group key, a `require_fields` id and
        # a carried cluster are the same question, and a private copy here
        # would answer it differently the day the envelope's rule moves.
        if order is None or not cluster_ok(group):
            unlifted.append(idx)
            continue
        by_group.setdefault(group, []).append((order, idx))
    groups = {}
    for group, pairs in by_group.items():
        pairs.sort()
        indices = [idx for _order, idx in pairs]
        arrays = {}
        for name in fields:
            # Spelled out for the same reason as the loop above: one
            # field of one record is the innermost step of the whole
            # pack, run once per (record, declared field).
            arrays[name] = np.asarray(
                [_num(records[idx].get(name)) if isinstance(records[idx], dict)
                 else _num(getattr(records[idx], name, _MISSING))
                 for idx in indices],
                dtype=np.float64,
            )
        # The order array is written LAST and therefore always wins: the
        # framing reads it, so a `fields` entry naming it must not shadow
        # it with a float64 restatement.
        arrays[order_field] = np.asarray([order for order, _ in pairs])
        groups[group] = (indices, arrays)
    return groups, unlifted


#: One group's built state: the surviving stream indices, the columns
#: ``apply`` answered for them, and whether the group's NEWEST lifted
#: position survived ``keep_mask``. The flag rides here because it is the
#: one fact compaction destroys — afterwards ``indices[-1]`` is the
#: newest SURVIVOR, and a serving call cannot tell that apart from the
#: newest position (which is how a stale row gets served as current).
_Group = namedtuple("_Group", ("indices", "columns", "newest_kept"))

#: One stream's built state: the surviving groups, the indices no group
#: could take, how many lifted positions ``keep_mask`` rejected, and the
#: pinned column schema. ``dropped`` rides HERE because compaction
#: destroys it and the module's promise is that everything is counted —
#: a bar the domain rule rejected (a vendor outage zeroing prices) would
#: otherwise appear in no counter and no log line at all.
_Built = namedtuple("_Built", ("groups", "unlifted", "dropped", "schema"))


def _compact(indices, arrays, mask):
    """Drop the masked-out positions from indices and arrays together."""
    import numpy as np

    if bool(mask.all()):
        return indices, arrays
    keep = np.flatnonzero(mask)
    return (
        [indices[i] for i in keep.tolist()],
        {name: arr[keep] for name, arr in arrays.items()},
    )


def _segments(order, max_gap):
    """Contiguous ``(start, stop)`` ranges no more than ``max_gap`` apart.

    Parameters
    ----------
    order : numpy.ndarray
        One group's order values, ascending.
    max_gap : int, float or None
        The largest step two adjacent positions may sit apart, in the
        ORDER field's own units. ``None`` = no bound, which yields the
        single whole-group segment (today's framing exactly).

    Returns
    -------
    tuple of tuple
        Half-open index ranges covering the group in order.
    """
    import numpy as np

    n = len(order)
    if max_gap is None or n < 2:
        return ((0, n),)
    edges = (0, *(np.flatnonzero(np.diff(order) > max_gap) + 1).tolist(), n)
    return tuple(zip(edges, edges[1:]))


def _copies(arrays):
    """Copy the arrays for one ``apply`` call.

    A subclass that mutates its input must not corrupt the arrays the
    guard's later cuts re-read.
    """
    return {name: arr.copy() for name, arr in arrays.items()}


def _as_columns(key, group, value, n):
    """Hold ``apply``'s return to its contract.

    A dict of non-empty string names to 1-D arrays of exactly ``n``
    values, one per input row. Returns ``{name: np.ndarray}``; raises
    ``ValueError`` naming the node, the column and the group on any
    violation.
    """
    import numpy as np

    if not isinstance(value, dict):
        raise ValueError(
            f"{key}: apply() must return a dict of column name -> array, got "
            f"{type(value).__name__} (group {group!r})"
        )
    if not value:
        raise ValueError(
            f"{key}: apply() returned no columns (group {group!r}) — "
            "a transform that computes nothing is a wiring bug, not a no-op"
        )
    columns = {}
    for name, data in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{key}: apply() returned a non-string column name {name!r} "
                f"(group {group!r})"
            )
        arr = np.asarray(data)
        if arr.ndim != 1:
            raise ValueError(
                f"{key}: column {name!r} must be 1-D (one value per record), "
                f"got shape {arr.shape} (group {group!r})"
            )
        if len(arr) != n:
            raise ValueError(
                f"{key}: column {name!r} has {len(arr)} value(s) for {n} "
                f"record(s) (group {group!r}) — apply() must return "
                "one value per input row"
            )
        columns[name] = arr
    return columns


def _check_schema(key, schema, columns, group):
    """Pin one column schema per node.

    ``apply`` must return the SAME column set for every group and every
    segment — a data-dependent schema hands downstream consumers ragged
    rows. Returns the (possibly newly-pinned) schema.
    """
    names = frozenset(columns)
    if schema is None or schema == names:
        return names
    raise ValueError(
        f"{key}: apply() returned column(s) {sorted(names)} for "
        f"{group!r} but {sorted(schema)} for an earlier group or segment — "
        "the column schema must not depend on the data"
    )


def _cut_points(n, declared=None):
    """Choose the guard's deterministic truncation points.

    Kept to strict prefixes (1 <= cut < n) and deduped. No RNG — the
    guard must fail the same way on every run.

    Default grid: the quarter, half and three-quarter points PLUS
    ``n - 1`` — the tail cut is the one that holds the last comparable
    position against a future-free prefix, catching leaks confined to
    the array's end (S1 #2). A ``declared`` list (the ``cuts`` knob)
    REPLACES the grid; cuts past a segment's length simply drop, so a
    short segment may see fewer — or no — checks (the documented
    no-leverage limit).
    """
    pool = declared if declared else (n // 4, n // 2, (3 * n) // 4, n - 1)
    return sorted({cut for cut in pool if 1 <= cut < n})


def _prefix_equal(a, b) -> bool:
    """Compare two column prefixes exactly, or nan-equally.

    ``equal_nan`` chokes on non-numeric dtypes (strings), where plain
    equality is the right bar anyway. The float path is spelled out
    rather than delegated to ``array_equal(equal_nan=True)``: this runs
    once per (column, cut, segment) — tens of thousands of times on the
    benchmark stream — and ``array_equal``'s NaN branch pays for two
    boolean-indexed COPIES where a mask does not.
    """
    import numpy as np

    if a.shape != b.shape:
        return False
    if a.dtype.kind == "f" and b.dtype.kind == "f":
        same = a == b
        if bool(same.all()):
            return True
        return bool((same | (np.isnan(a) & np.isnan(b))).all())
    try:
        return bool(np.array_equal(a, b, equal_nan=True))
    except (TypeError, ValueError):
        return bool(np.array_equal(a, b))


def _row_cells(columns, names, n):
    """Columns as plain row values, WHOLE-ARRAY at a time.

    The row-emission hot path: ``tolist()`` converts a column in C and
    ``isfinite`` finds the absences vectorized, where a per-cell
    ``hasattr``/``isfinite`` pair would run once per value — 62 million
    of them on the 2M-bar benchmark's 31 columns.

    Parameters
    ----------
    columns : dict of str -> numpy.ndarray
        One group's computed columns.
    names : list of str
        The columns to emit, in order.
    n : int
        How many positions the group has.

    Returns
    -------
    tuple of (dict, list)
        ``cells`` maps each name to a list of plain Python values, a
        non-finite float rendered as ``None`` — an absent belief (the
        warm-up NaN), never a number some reader cannot parse — and
        ``complete`` says, per position, whether every emitted value is
        present.
    """
    import numpy as np

    cells = {}
    complete = np.ones(n, dtype=bool)
    for name in names:
        column = columns[name]
        if column.dtype.kind != "f":
            cells[name] = column.tolist()
            continue
        present = np.isfinite(column)
        values = column.tolist()
        if not bool(present.all()):
            complete &= present
            # Only the ABSENCES are touched from Python — a warm-up NaN
            # is a handful of positions, where a per-value branch over
            # the whole column is one Python step per cell.
            for i in np.flatnonzero(~present).tolist():
                values[i] = None
        cells[name] = values
    return cells, complete.tolist()


class _ArrayApply(Node):
    """The machinery both doorways stand on.

    Lifting, framing, the apply contract, the causality guard,
    default-deny params. Abstract — never referenceable itself;
    :class:`ArrayMap` and :class:`ArrayFeatures` are the two public
    shapes.
    """

    #: The base's own knobs. Subclasses EXTEND this tuple with theirs —
    #: that keeps ``validate_params``'s default-deny and the conformance
    #: suite's per-knob fuzz covering the new names — and NARROW it
    #: (:func:`narrow_params`) for every accessor they override.
    _PARAMS = ("causality_check", "cuts", "fields", "group_field", "max_gap",
               "order_field")

    # -- the declared-field accessors (ADR-0040) ---------------------------
    #
    # One line each, and each the ONLY reader of its default. Override to
    # speak a different vocabulary — and narrow the knob away when you do.

    def group_field(self):
        """Name the field this stream is keyed on (str)."""
        return self.params.get("group_field", DEFAULT_GROUP_FIELD)

    def order_field(self):
        """Name the field this stream is ordered by (str)."""
        return self.params.get("order_field", DEFAULT_ORDER_FIELD)

    def fields(self):
        """Name the numeric fields lifted into the arrays (tuple of str)."""
        return tuple(self.params.get("fields", DEFAULT_FIELDS))

    def max_gap(self):
        """Give the gap bound, in the order field's own units.

        ``None`` means no bound — one segment per group.
        """
        return self.params.get("max_gap", DEFAULT_MAX_GAP)

    def causality_check(self):
        """Say whether the causality guard runs (bool)."""
        return bool(self.params.get("causality_check", DEFAULT_CAUSALITY_CHECK))

    def cuts(self):
        """Give the guard's declared cut points, or ``None`` for the grid."""
        return self.params.get("cuts", DEFAULT_CUTS)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Default-deny against ``cls._PARAMS``, then the base's own knobs.
        **Every per-knob check is guarded by membership in ``_PARAMS``**,
        because a subclass that overrides the accessor has narrowed the
        knob away and must not be refused for omitting it. Subclasses
        override, call ``super().validate_params(params)`` first, and
        append their own checks (see :class:`TrailingReturns`).

        Parameters
        ----------
        params : dict
            The node's declared params, straight from the document.

        Returns
        -------
        list of str
            One problem per broken knob; empty when the params are usable.
        """
        problems = accessor_narrowing_problems(cls)
        reject_unknown_params(problems, params, cls._PARAMS)
        if "causality_check" in cls._PARAMS:
            flag = params.get("causality_check", DEFAULT_CAUSALITY_CHECK)
            if not is_node_ref(flag) and not isinstance(flag, bool):
                problems.append(f"causality_check must be a bool, got {flag!r}")
        cls._cut_problems(problems, params)
        for knob in ("group_field", "order_field"):
            if knob not in cls._PARAMS or knob not in params:
                continue
            value = params[knob]
            if not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        if "fields" in cls._PARAMS and "fields" in params:
            cls._fields_problems(problems, params["fields"])
        if "max_gap" in cls._PARAMS and params.get("max_gap") is not None:
            gap = params["max_gap"]
            if not is_node_ref(gap) and (
                isinstance(gap, bool)
                or not isinstance(gap, (int, float))
                or not math.isfinite(gap)
                or gap <= 0
            ):
                problems.append(
                    f"max_gap must be a positive finite number in the order "
                    f"field's units when present, got {gap!r}"
                )
        return problems

    @classmethod
    def _fields_problems(cls, problems, fields):
        """Check ``fields``: a non-empty sequence of distinct names."""
        if is_node_ref(fields):
            return
        if not isinstance(fields, (list, tuple)) or not fields:
            problems.append(
                f"fields must be a non-empty list of record field names, "
                f"got {fields!r}"
            )
            return
        bad = [f for f in fields if not isinstance(f, str) or not f]
        if bad:
            problems.append(f"fields entries must be non-empty strings, got {bad!r}")
        dupes = sorted({f for f in fields if list(fields).count(f) > 1})
        if dupes:
            problems.append(f"fields repeats {dupes} — declare each field once")

    @classmethod
    def _cut_problems(cls, problems, params):
        """Check ``cuts``: distinct ints >= 1, or absent."""
        if "cuts" not in cls._PARAMS:
            return
        cuts = params.get("cuts", DEFAULT_CUTS)
        if cuts is None or is_node_ref(cuts):
            return
        # ints and >= 1 are checkable at PLAN; the upper bound is each
        # segment's own length, only knowable at run (_cut_points keeps
        # declared cuts to strict prefixes there).
        if not isinstance(cuts, (list, tuple)) or not cuts:
            problems.append(
                f"cuts must be a non-empty list of ints >= 1 when present, "
                f"got {cuts!r}"
            )
            return
        bad = [c for c in cuts if isinstance(c, bool) or not isinstance(c, int) or c < 1]
        if bad:
            problems.append(f"cuts entries must be ints >= 1, got {bad!r}")
        good = [c for c in cuts if not isinstance(c, bool) and isinstance(c, int) and c >= 1]
        dupes = sorted({c for c in good if good.count(c) > 1})
        if dupes:
            problems.append(f"cuts repeats {dupes} — declare each cut once")

    def validate_inputs(self, inputs):
        """Require ``records`` to be a LIST.

        A one-shot iterable is refused by name — validation must never
        consume what ``run()`` reads (the F-220 #6 shape), and refusing
        loudly is the lawful alternative.
        """
        records = inputs.get("records")
        if not isinstance(records, list):
            return [
                "records must be a list of records (a one-shot iterable is "
                f"refused, never consumed by validation), got "
                f"{type(records).__name__}"
            ]
        return []

    @abstractmethod
    def apply(self, arrays, params):
        """Compute this group's columns — the ONLY code a project writes.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            ONE group's records for ONE gap-free segment, aligned and
            sorted ascending by the declared order field: that field's
            own array under its own name, plus one float64 array per
            declared lifted field, NaN where the record carries no value.
            The arrays are fresh copies — mutate freely.
        params : dict
            ``self.params``, passed through for convenience.

        Returns
        -------
        dict of str -> array-like
            One 1-D column per name, exactly one value per input row.
            Position ``t`` may depend on rows ``<= t`` ONLY — unless the
            class declares a forward horizon for that column through
            :meth:`lookahead_columns` — and the method must accept ANY
            prefix of its input (emit warm-up NaNs, never refuse short
            arrays), because the guard re-runs it on prefixes.
        """
        raise NotImplementedError

    # -- the framing hooks --------------------------------------------------

    def keep_mask(self, arrays):
        """Which lifted positions participate in the series.

        The seam for a domain rule the pack cannot know — "a bar with no
        usable price is not a bar" — kept VECTORIZED on purpose: the
        alternative is a per-record Python predicate over millions of
        rows. The base keeps everything, which is today's behaviour.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One group's arrays, before segmentation.

        Returns
        -------
        numpy.ndarray
            A bool array, one entry per position. Dropped positions are
            compacted out INDICES AND ALL, so the surviving records
            become adjacent and the gap bound then judges them.
        """
        import numpy as np

        return np.ones(len(arrays[self.order_field()]), dtype=bool)

    def lookahead_columns(self):
        """Columns that read FORWARD, mapped to how far, ``{}`` by default.

        A supervised label is legitimately forward-looking, and the
        honest treatment is to DECLARE the horizon: the causality guard
        then holds the column to "position ``t`` depends on rows
        ``<= t + h``" instead of exempting it. An undeclared column is
        held to ``h = 0`` — strictly backward, the pre-ADR-0040 rule.

        Returns
        -------
        dict of str -> int
            Column name to forward horizon, in positions.
        """
        return {}

    # -- shared plumbing ----------------------------------------------------

    def _refuse_columns(self, names):
        """Class-specific column-name rule; the base accepts any name."""

    def _grouped_columns(self, records):
        """Build one input stream's :class:`_Built` state."""
        groups, unlifted = _lift(
            records, self.group_field(), self.order_field(), self.fields()
        )
        if records and not groups:
            raise ValueError(
                f"{self.key}: every one of the {len(records)} input record(s) "
                f"was unlifted — nothing carries a usable {self.group_field()!r} "
                f"and {self.order_field()!r} pair. A declared field matching "
                "NOTHING would emit zero rows and exit 0, so it refuses here "
                "instead"
            )
        out = {}
        schema = None
        dropped = 0
        for group in sorted(groups):
            indices, arrays = groups[group]
            # Read the flag BEFORE compaction destroys the evidence:
            # `indices` arrives newest-last, so mask[-1] is that position.
            mask = self.keep_mask(arrays)
            newest_kept = bool(len(mask)) and bool(mask[-1])
            kept, arrays = _compact(indices, arrays, mask)
            dropped += len(indices) - len(kept)
            if not kept:
                continue
            columns = self._segmented_columns(group, arrays, schema)
            schema = _check_schema(self.key, schema, columns, group)
            out[group] = _Group(kept, columns, newest_kept)
        return _Built(out, unlifted, dropped, schema)

    def _segmented_columns(self, group, arrays, schema):
        """Run one group through ``apply``, a gap-free segment at a time.

        The segments' columns are concatenated back in order.
        """
        import numpy as np

        pieces = []
        for start, stop in _segments(arrays[self.order_field()], self.max_gap()):
            segment = {name: arr[start:stop] for name, arr in arrays.items()}
            columns = self._columns_for(group, segment, stop - start)
            schema = _check_schema(self.key, schema, columns, group)
            pieces.append(columns)
        if len(pieces) == 1:
            return pieces[0]
        return {
            name: np.concatenate([piece[name] for piece in pieces])
            for name in pieces[0]
        }

    def _columns_for(self, group, arrays, n):
        """Run one segment through ``apply``, held to the whole contract.

        Shape-checked, class-legal names, and — unless the document said
        otherwise — causal.
        """
        full = _as_columns(
            self.key, group, self.apply(_copies(arrays), self.params), n
        )
        self._refuse_columns(sorted(full))
        if self.causality_check():
            self._assert_causal(group, arrays, full, n)
        return full

    def _assert_causal(self, group, arrays, full, n):
        """Run the prefix-consistency guard (module docstring).

        Re-run ``apply`` on each truncated prefix and refuse, by name,
        any overlapping output that moved. A column declaring a forward
        horizon is compared only where BOTH answers are determined. A
        NECESSARY mechanical screen, not a sufficiency proof — only
        drift the cut points expose can be caught, so "no refusal" means
        "not caught here", never "proven causal".
        """
        ahead = self.lookahead_columns()
        for cut in _cut_points(n, self.cuts()):
            try:
                trial = self.apply({k: v[:cut].copy() for k, v in arrays.items()},
                                   self.params)
            except Exception as exc:
                raise ValueError(
                    f"{self.key}: apply() failed on a truncated prefix "
                    f"({cut}/{n} rows, group {group!r}) — the "
                    "causality guard re-runs apply() on prefixes, so it must "
                    "handle any prefix of its input (emit warm-up NaNs "
                    f"instead of refusing short arrays): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            trial = _as_columns(self.key, group, trial, cut)
            if set(trial) != set(full):
                raise ValueError(
                    f"{self.key}: apply() is not causal — the column set "
                    f"changed when the future was removed ({sorted(full)} "
                    f"became {sorted(trial)} at cut {cut}/{n}, group "
                    f"{group!r})"
                )
            for name in sorted(full):
                end = cut - int(ahead.get(name, 0))
                if end <= 0:
                    continue  # the declared horizon eats the whole prefix
                if not _prefix_equal(full[name][:end], trial[name][:end]):
                    raise ValueError(
                        f"{self.key}: apply() is not causal — output at t "
                        "changed when the future was removed (column "
                        f"{name!r}, group {group!r}, cut "
                        f"{cut}/{n}, declared lookahead "
                        f"{int(ahead.get(name, 0))}). A window may look "
                        "BACKWARD only: global statistics and centered "
                        "windows read rows after t. Fix apply(), or declare "
                        "the horizon in lookahead_columns(); "
                        "causality_check=false skips this guard, and turning "
                        "it off is a decision the document owns."
                    )


class ArrayMap(_ArrayApply):
    """Rewrite envelope numeric fields with array code (role ``transform``).

    The generic feature-engineering doorway, subclass form (I-228).

    Subclass, implement :meth:`~_ArrayApply.apply`, and reference the
    subclass by import path. The base lifts the stream per group, splits
    it on the declared gap bound, calls ``apply``, runs the causality
    guard, and writes the returned columns back onto the records —
    ``dataclasses.replace`` for dataclass envelopes, a shallow copy for
    dict records — returning the stream in its ORIGINAL order.

    ``apply`` may name only rewritable envelope fields
    (``bid``/``ask``/``mid``/``lead_frac``); any other column is refused
    by name — a derived feature belongs in :class:`ArrayFeatures` rows,
    not on the envelope. The writeback table does NOT follow ``fields``:
    ``fields`` governs what the base READS, never what this class writes,
    because a foreign column name has no acceptance predicate the pack
    could honestly supply (ADR-0040, deferred). A value the envelope
    cannot hold (NaN/inf, non-positive price, ``lead_frac`` outside
    (0, 1), a field the record type lacks) leaves that record's field
    unchanged; unliftable records pass through untouched. Counted and
    logged, never raised (module docstring, missing-field policy).

    Parameters
    ----------
    params : dict
        The base knobs — ``causality_check``, ``cuts``, ``fields``,
        ``group_field``, ``order_field``, ``max_gap`` — plus whatever the
        subclass declares.

    Examples
    --------
    A subclass and the node a document builds from it::

        class Halve(ArrayMap):
            def apply(self, arrays, params):
                return {"mid": arrays["mid"] / 2.0}

        node = Halve("halve", {"causality_check": True})
        out = node.run(ctx, {"records": records})
        # -> {"records": [...]}
    """

    role = "transform"
    outputs = ("records",)

    def validate_inputs(self, inputs):
        """Problems with ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs; ``records`` is the stream.

        Returns
        -------
        list of str
            The base's problems, plus one naming the first record that
            is neither a mapping nor a dataclass envelope — this class
            rebuilds records immutably and can hold nothing else.
        """
        problems = super().validate_inputs(inputs)
        if problems:
            return problems
        for i, record in enumerate(inputs["records"]):
            if isinstance(record, dict):
                continue
            if is_dataclass(record) and not isinstance(record, type):
                continue
            problems.append(
                f"records[{i}] is a {type(record).__name__} — ArrayMap "
                "rebuilds records immutably, so every record must be a "
                "mapping or a dataclass envelope"
            )
            break
        return problems

    def _refuse_columns(self, names):
        unknown = sorted(set(names) - set(_WRITEBACK))
        if unknown:
            raise ValueError(
                f"{self.key}: apply() returned column(s) {unknown} that are "
                "not rewritable envelope fields — ArrayMap writes only "
                f"{sorted(_WRITEBACK)}; a derived feature belongs in "
                "ArrayFeatures rows, not on the envelope"
            )

    def run(self, ctx, inputs):
        """Rewrite the stream's envelope fields and return it in order.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — this node reads only its inputs.
        inputs : dict
            ``records``, the stream to rewrite.

        Returns
        -------
        dict
            ``{"records": [...]}`` — the stream in its ORIGINAL order,
            rebuilt immutably where a value was written.
        """
        records = inputs["records"]
        built = self._grouped_columns(records)
        grouped = built.groups
        rebuilt = list(records)
        written = unchanged = 0
        for group in sorted(grouped):
            indices, columns = grouped[group].indices, grouped[group].columns
            for name in sorted(columns):
                accept = _WRITEBACK[name]
                values = columns[name]
                for j, idx in enumerate(indices):
                    try:
                        value = float(values[j])
                    except (TypeError, ValueError):
                        unchanged += 1
                        continue
                    if not accept(value):
                        unchanged += 1
                        continue
                    record = rebuilt[idx]
                    if isinstance(record, dict):
                        rebuilt[idx] = {**record, name: value}
                    elif hasattr(record, name):
                        rebuilt[idx] = replace(record, **{name: value})
                    else:
                        unchanged += 1
                        continue
                    written += 1
        self.log.info(
            "array map wrote %d value(s) across %d group(s); "
            "%d value(s) left unchanged (unwritable), %d record(s) passed "
            "through unlifted, %d dropped by keep_mask",
            written,
            len(grouped),
            unchanged,
            len(built.unlifted),
            built.dropped,
        )
        return {"records": rebuilt}


class ArrayFeatures(_ArrayApply):
    """Derive feature rows with array code (role ``tensor``).

    The same lifting, framing and guard as :class:`ArrayMap`, but
    ``apply``'s columns become FEATURE ROWS for downstream fit/train
    nodes: one dict per lifted record, carrying the declared
    ``carry_fields`` plus every column, in the stream's original order.

    A non-finite value lands as ``None`` — an absent belief (the warm-up
    NaN of a trailing window), matching the signal convention that no
    coverage is ``None``, never a fabricated number; ``drop_incomplete``
    turns those rows into no row at all, which is what a supervised
    window wants. A record missing a ``require_fields`` id yields no row
    (a row with no identity is unusable downstream) but still
    participates in the arrays, so later windows see it. A feature column
    may not take a carried field's name — refused by name.

    ``metrics`` carries the numeric summary (``n_rows``, ``n_records``,
    ``n_instruments`` — the group count — ``n_columns``, and
    ``n_dropped``, the positions ``keep_mask`` rejected) for the sinks;
    the bulk rows ride under ``rows``, never under ``metrics``.

    Parameters
    ----------
    params : dict
        :class:`ArrayMap`'s base knobs plus ``carry_fields``,
        ``require_fields`` and ``drop_incomplete``.

    Examples
    --------
    A subclass emitting one column, and the node a document builds::

        class Doubled(ArrayFeatures):
            def apply(self, arrays, params):
                return {"twice_mid": arrays["mid"] * 2.0}

        node = Doubled("doubled", {"drop_incomplete": True})
        out = node.run(ctx, {"records": records})
        # -> {"rows": [...], "metrics": {...}}
    """

    role = "tensor"
    outputs = ("rows", "metrics")

    _PARAMS = _ArrayApply._PARAMS + (
        "carry_fields",
        "drop_incomplete",
        "require_fields",
    )

    def carry_fields(self):
        """Record fields copied onto every row (tuple of str)."""
        return tuple(self.params.get("carry_fields", DEFAULT_CARRY_FIELDS))

    def require_fields(self):
        """Identity fields a row must carry to be emitted (tuple of str).

        "Carry" means hold a value :func:`~dskit.pipeline.records.
        cluster_ok` accepts — the same bar the group key and the carried
        cluster are held to.
        """
        return tuple(self.params.get("require_fields", DEFAULT_REQUIRE_FIELDS))

    def drop_incomplete(self):
        """Whether a row with any absent column value is dropped (bool)."""
        return bool(self.params.get("drop_incomplete", DEFAULT_DROP_INCOMPLETE))

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        The base's knobs plus this class's three, each guarded by
        ``_PARAMS`` membership so a narrowing subclass is not refused
        for omitting one.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        for knob in ("carry_fields", "require_fields"):
            if knob not in cls._PARAMS or knob not in params:
                continue
            value = params[knob]
            if is_node_ref(value):
                continue
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(f, str) or not f for f in value
            ):
                problems.append(
                    f"{knob} must be a list of non-empty record field names, "
                    f"got {value!r}"
                )
        if "drop_incomplete" in cls._PARAMS and "drop_incomplete" in params:
            flag = params["drop_incomplete"]
            if not is_node_ref(flag) and not isinstance(flag, bool):
                problems.append(f"drop_incomplete must be a bool, got {flag!r}")
        return problems

    # -- the emission hooks -------------------------------------------------

    def sort_rows(self, rows):
        """Order the rows for emission; the base keeps the stream's."""
        return rows

    def emit(self, rows, metrics):
        """Package the built rows as THIS class's declared outputs.

        The seam a subclass with a different output contract overrides —
        a ``transform``-role window node emitting ``records`` overrides
        this and its ``outputs``, and inherits every line of the chain
        semantics unchanged.

        Parameters
        ----------
        rows : list of dict
            The built feature rows, already ordered.
        metrics : dict
            The numeric summary.

        Returns
        -------
        dict
            The node's named outputs.
        """
        return {"rows": rows, "metrics": metrics}

    def _refuse_columns(self, names):
        clash = sorted(set(names) & set(self.carry_fields()))
        if clash:
            raise ValueError(
                f"{self.key}: apply() returned column(s) {clash} that collide "
                f"with the carried fields {list(self.carry_fields())} — rename "
                "the feature columns"
            )

    def _carried_columns(self, records, indices, carry):
        """Read the carried identity fields as COLUMNS, the envelope's rule applied.

        A column at a time, not a row at a time: the field's rule is
        resolved once per FIELD instead of once per (row, field), which
        is the same reason :func:`_row_cells` converts whole arrays.
        """
        return [
            _carried_column(name, [
                record.get(name) if isinstance(record, dict)
                else getattr(record, name, None)
                for record in map(records.__getitem__, indices)
            ])
            for name in carry
        ]

    def _feature_rows(self, records, drop=(), complete_only=False):
        """Build every emittable row, keyed by its input index.

        Answers ``(built, rows_by_index, no_row)`` and is shared by
        :meth:`run` and :meth:`latest_rows`; ``drop`` names columns to
        leave OUT of the emitted rows, and ``complete_only`` requires
        completeness whatever the knob says — the serving caller's
        unconditional rule.
        """
        built = self._grouped_columns(records)
        carry = tuple(self.carry_fields())
        required = tuple(self.require_fields())
        incomplete_is_no_row = complete_only or self.drop_incomplete()
        rows_by_index = {}
        no_row = 0
        for group in built.groups.values():
            indices = group.indices
            names = [name for name in sorted(group.columns) if name not in drop]
            cells, complete = _row_cells(group.columns, names, len(indices))
            keys = carry + tuple(names)
            values = self._carried_columns(records, indices, carry)
            values += [cells[name] for name in names]
            for idx, cell_row, whole in zip(indices, zip(*values), complete):
                if incomplete_is_no_row and not whole:
                    no_row += 1
                    continue
                if required and any(
                    not cluster_ok(_field(records[idx], name))
                    for name in required
                ):
                    no_row += 1
                    continue
                rows_by_index[idx] = dict(zip(keys, cell_row))
        return built, rows_by_index, no_row

    def run(self, ctx, inputs):
        """Build the feature rows and their numeric summary.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — this node reads only its inputs.
        inputs : dict
            ``records``, the stream to window.

        Returns
        -------
        dict
            Whatever :meth:`emit` packages — by default
            ``{"rows": [...], "metrics": {...}}``.
        """
        records = inputs["records"]
        built, rows_by_index, no_row = self._feature_rows(records)
        rows = [rows_by_index[idx] for idx in range(len(records)) if idx in rows_by_index]
        metrics = {
            "n_rows": len(rows),
            "n_records": len(records),
            "n_instruments": len(built.groups),
            "n_columns": len(built.schema or ()),
            "n_dropped": built.dropped,
        }
        self.log.info(
            "array features: %d row(s) x %d column(s) from %d record(s) "
            "(%d unlifted, %d dropped by keep_mask, %d without a usable row)",
            metrics["n_rows"],
            metrics["n_columns"],
            metrics["n_records"],
            len(built.unlifted),
            metrics["n_dropped"],
            no_row,
        )
        return self.emit(self.sort_rows(rows), metrics)

    def latest_rows(self, records):
        """Build the NEWEST row per group, without the forward columns.

        The serving call: a live loop holds the last few bars and wants
        the feature vector at the newest one, whose label does not exist
        yet. Same lifting, same gap bound, same guard, same ``apply`` —
        so a serving row and the training row for the same
        ``(group, order)`` agree by construction rather than by a second
        implementation agreeing with the first.

        **The newest LIFTED position is taken as-is, mask included.** It
        is absent from the answer when its row is incomplete AND when
        ``keep_mask`` dropped it — because compaction makes the newest
        SURVIVOR adjacent, and serving that survivor would hand a live
        loop a stale feature vector wearing a stale stamp with nothing
        marking it stale. Training WANTS the survivors to chain; serving
        wants the truth about now. (A record that could not be lifted at
        all belongs to no group, so it cannot make one stale.)

        **Both halves of that rule are UNCONDITIONAL.**
        ``drop_incomplete`` is a TRAINING emission knob — it decides
        whether :meth:`run` keeps warm-up rows — and a serving call that
        read it would publish a half-warm feature vector to any document
        that left the knob at its default. So completeness is required
        here whatever the knob says.

        Parameters
        ----------
        records : list
            Recent records for one or more groups, any order.

        Returns
        -------
        dict
            Group value -> the newest complete row, with every column the
            class declares in ``lookahead_columns`` left out. A group
            whose newest position is dropped or incomplete is ABSENT.
        """
        built, rows_by_index, _no_row = self._feature_rows(
            records, drop=tuple(self.lookahead_columns()), complete_only=True
        )
        latest = {}
        for group, group_state in built.groups.items():
            if not group_state.newest_kept:
                continue
            row = rows_by_index.get(group_state.indices[-1])
            if row is not None:
                latest[group] = row
        return latest


# ---------------------------------------------------------------------------
# Reference subclasses — what a project's own subclass looks like. These
# are the import-path targets the example document and the conformance
# probes use; they are deliberately tiny and backward-only by construction.
# ---------------------------------------------------------------------------


class LogMid(ArrayMap):
    """Reference :class:`ArrayMap`: rewrite ``mid`` as ``log1p(mid)``.

    ``log1p`` keeps every positive price positive (``log`` alone would
    turn a sub-1.0 price negative and the envelope would refuse it), so
    the rewritten ``mid`` is always a legal envelope price. Pointwise,
    therefore backward-only by construction — the guard passes it at
    every cut. It reads ``mid`` by NAME, which is why it overrides
    ``fields()`` and narrows the knob away: a document writing
    ``"fields": ["bid"]`` used to validate clean and die at execute with
    a bare ``KeyError``.

    Parameters
    ----------
    params : dict
        :class:`ArrayMap`'s knobs less ``fields``.

    Examples
    --------
    The node the shipped example builds::

        node = LogMid("log_mid", {})
        out = node.run(ctx, {"records": records})
        # -> {"records": [...]}
    """

    _PARAMS = narrow_params(ArrayMap._PARAMS, "fields")

    def fields(self):
        """``mid`` and nothing else — this class indexes it by name."""
        return ("mid",)

    def apply(self, arrays, params):
        """Rewrite ``mid`` as ``log1p(mid)``.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One segment's arrays; only ``mid`` is read.
        params : dict
            This node's params; unused.

        Returns
        -------
        dict of str -> numpy.ndarray
            ``{"mid": log1p(mid)}`` — pointwise, therefore causal.
        """
        import numpy as np

        return {"mid": np.log1p(arrays["mid"])}


class TrailingReturns(ArrayFeatures):
    """Reference :class:`ArrayFeatures`: the trailing return of ``mid``.

    ``mid[t] / mid[t - window] - 1``, NaN (→ row ``None``) for the first
    ``window`` entries. Position ``t`` reads ``t`` and ``t - window``
    only: backward-only by construction, and the worked example of the
    subclass-knob pattern (extend ``_PARAMS``, override
    ``validate_params`` calling ``super()`` first, and narrow away the
    accessor you override).

    Parameters
    ----------
    params : dict
        ``window`` (int >= 1, required) plus :class:`ArrayFeatures`'s
        knobs less ``fields``.

    Examples
    --------
    The node the shipped example builds::

        node = TrailingReturns("features", {"window": 2})
        out = node.run(ctx, {"records": records})
        # -> {"rows": [...], "metrics": {...}}
    """

    _PARAMS = narrow_params(ArrayFeatures._PARAMS, "fields") + ("window",)

    def fields(self):
        """``mid`` and nothing else — this class indexes it by name."""
        return ("mid",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        The base's, plus the required ``window``.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        if "window" not in params:
            problems.append(
                "window is required — the trailing lookback must be stated, "
                "there is no default"
            )
            return problems
        window = params["window"]
        if is_node_ref(window):
            return problems  # a $-reference — construction re-validates
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            problems.append(f"window must be an int >= 1, got {window!r}")
        return problems

    def apply(self, arrays, params):
        """Take the ``window``-step trailing return of ``mid``.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One segment's arrays; only ``mid`` is read.
        params : dict
            This node's params; ``window`` is the step.

        Returns
        -------
        dict of str -> numpy.ndarray
            ``{"trailing_return": ...}``, NaN for the warm-up positions.
        """
        return {"trailing_return": pct_return(arrays["mid"], params["window"])}


def _lag_name(prefix, step):
    """Spell the column one lag rides under — the ONE owner of the format."""
    return f"{prefix}{step}"


def _lag_index(name, prefix):
    """Invert :func:`_lag_name`: the step digits in ``name``, or ``None`` for none."""
    if not name.startswith(prefix):
        return None
    step = name[len(prefix):]
    if not (step.isascii() and step.isdigit()):
        return None
    # ``str(n)`` is unpadded, so a zero-padded name is nobody's lag column.
    return step if step == "0" or step[0] != "0" else None


class ReturnWindows(ArrayFeatures):
    """Lagged one-step returns with a forward label — the ops, composed.

    The concrete windowing member of the pack: order the group, split it
    on the gap bound, take one-step returns of the declared price field,
    and emit ``lookback`` lags of them plus a ``label_lead``-step forward
    label. Every op is vectorized (:func:`lag`, :func:`lead`,
    :func:`log_return`, :func:`pct_return`) and every one is screened —
    the label by DECLARED horizon, never by exemption.

    A domain subclass overrides the accessors it wants to spell its own
    way (``price_field`` instead of ``fields``, minutes instead of order
    units) and narrows those knobs away; it writes no chain semantics of
    its own.

    **The label may never take a lag column's NAME.** Lags and the label
    are written into ONE dict, so ``label_name`` matching
    ``f"{lag_prefix}{step}"`` for a ``step`` inside ``lookback`` would put
    a FORWARD value in a PAST column — silently, because the row simply
    loses a column and the guard then holds the overwritten name to the
    horizon :meth:`lookahead_columns` declares for it. The combination is
    refused at plan (``validate_params``), never merely discouraged.

    Parameters
    ----------
    params : dict
        ``fields`` (exactly one price field, required), ``lookback``
        (int >= ``min_lookback``, required), ``return_kind`` (a
        :data:`RETURN_KINDS` name, default ``"log"``), ``label_lead``
        (int >= 1, default 1), ``lag_prefix`` (str, default ``"lag_"``),
        ``label_name`` (str, default ``"label"``), plus
        :class:`ArrayFeatures`'s knobs.

    Examples
    --------
    Ten lags of one-minute log returns that never bridge a five-minute
    hole, labelled with the next return::

        node = ReturnWindows("windows", {
            "fields": ["close"], "lookback": 10,
            "max_gap": 5 * 60_000, "drop_incomplete": True,
        })
        out = node.run(ctx, {"records": bars})
        # -> {"rows": [{"lag_0": ..., "label": ...}, ...], "metrics": {...}}
    """

    #: The smallest window this class accepts. A domain subclass whose
    #: model needs more raises it, and the refusal message follows —
    #: one bound, one message, one name.
    min_lookback = 1

    _PARAMS = ArrayFeatures._PARAMS + (
        "label_lead",
        "label_name",
        "lag_prefix",
        "lookback",
        "n_ahead",
        "return_kind",
    )

    def lookback(self):
        """How many lagged returns each row carries (int)."""
        return self.params["lookback"]

    def return_kind(self):
        """Which return convention to take (a :data:`RETURN_KINDS` name)."""
        return self.params.get("return_kind", DEFAULT_RETURN_KIND)

    def label_lead(self):
        """How many steps forward the label reads (int >= 1)."""
        return self.params.get("label_lead", DEFAULT_LABEL_LEAD)

    def n_ahead(self):
        """How many forward steps to emit (int >= 1). Default 1."""
        return self.params.get("n_ahead", 1)

    def lag_prefix(self):
        """Name the prefix the emitted lag columns share (str)."""
        return self.params.get("lag_prefix", DEFAULT_LAG_PREFIX)

    def label_name(self):
        """Name the emitted label column (str)."""
        return self.params.get("label_name", DEFAULT_LABEL_NAME)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        The base's, plus this class's five, each guarded by ``_PARAMS``
        membership — and the one CROSS-knob rule: ``label_name`` may not
        name a lag column ``lag_prefix`` and ``lookback`` produce.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        if "fields" in cls._PARAMS:
            fields = params.get("fields")
            if not is_node_ref(fields) and (
                not isinstance(fields, (list, tuple)) or len(fields) != 1
            ):
                problems.append(
                    f"fields must name EXACTLY ONE price field — a window is "
                    f"taken of one series, got {fields!r}"
                )
        cls._window_problems(problems, params)
        return problems

    @classmethod
    def _window_problems(cls, problems, params):
        """Check the windowing knobs: lookback, kind, lead, names, collision."""
        if "lookback" in cls._PARAMS:
            lookback = params.get("lookback")
            if "lookback" not in params:
                problems.append(
                    "lookback is required — the window width must be stated, "
                    "there is no default"
                )
            elif not is_node_ref(lookback) and (
                isinstance(lookback, bool)
                or not isinstance(lookback, int)
                or lookback < cls.min_lookback
            ):
                problems.append(
                    f"lookback must be an int >= {cls.min_lookback}, "
                    f"got {lookback!r}"
                )
        if "return_kind" in cls._PARAMS and "return_kind" in params:
            kind = params["return_kind"]
            if not is_node_ref(kind) and kind not in RETURN_KINDS:
                problems.append(
                    f"return_kind must be one of {sorted(RETURN_KINDS)}, "
                    f"got {kind!r}"
                )
        if "label_lead" in cls._PARAMS and "label_lead" in params:
            step = params["label_lead"]
            if not is_node_ref(step) and (
                isinstance(step, bool) or not isinstance(step, int) or step < 1
            ):
                problems.append(f"label_lead must be an int >= 1, got {step!r}")
        if "n_ahead" in cls._PARAMS and "n_ahead" in params:
            ahead = params["n_ahead"]
            if not is_node_ref(ahead) and (
                isinstance(ahead, bool)
                or not isinstance(ahead, int)
                or ahead < 1
            ):
                problems.append(f"n_ahead must be an int >= 1, got {ahead!r}")
        for knob in ("lag_prefix", "label_name"):
            if knob not in cls._PARAMS or knob not in params:
                continue
            value = params[knob]
            if not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        cls._collision_problems(problems, params)

    @classmethod
    def _collision_problems(cls, problems, params):
        """Refuse a label whose NAME is one of the lag columns beside it."""
        if not {"label_name", "lag_prefix", "lookback"}.issubset(cls._PARAMS):
            return  # a narrowed class hardcodes them; nothing here to read
        label = params.get("label_name", DEFAULT_LABEL_NAME)
        prefix = params.get("lag_prefix", DEFAULT_LAG_PREFIX)
        lookback = params.get("lookback")
        if not isinstance(label, str) or not isinstance(prefix, str):
            return  # refused by the per-knob checks, or a $-ref that defers
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
            return  # no window, no lag columns — and its own check refuses it
        step = _lag_index(label, prefix)
        # Compared as canonical decimals: a column NAME is not an int, and
        # converting a five-thousand-digit one would explode a validator
        # whose bar is to RETURN problems.
        if step is None or (len(step), step) >= (len(f"{lookback}"), f"{lookback}"):
            return
        problems.append(
            f"label_name {label!r} IS the lag column lag_prefix {prefix!r} "
            f"emits for step {step}, which lookback={lookback} asks for — the "
            "label is a FORWARD value and a lag is a PAST one, so the label "
            "would silently overwrite that feature with the next bar's "
            "return, drop a column, and pass the causality guard (which holds "
            "a declared-forward name to a forward horizon). Rename label_name, "
            "or move lag_prefix off it"
        )

    def lookahead_columns(self):
        """Declare the label column(s) and how far forward they read."""
        step = self.label_lead()
        ahead = self.n_ahead()
        if ahead == 1:
            return {self.label_name(): step}
        return {f"y_ahead_{k}": k * step for k in range(1, ahead + 1)}

    def apply(self, arrays, params):
        """Build the lag columns and the forward label.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One segment's arrays; the declared price field is read.
        params : dict
            This node's params; the knobs are read through accessors.

        Returns
        -------
        dict of str -> numpy.ndarray
            ``lookback`` lag columns plus the label, NaN where a
            position has no such past or future.
        """
        returns = RETURN_KINDS[self.return_kind()](arrays[self.fields()[0]])
        prefix = self.lag_prefix()
        columns = {
            _lag_name(prefix, step): lag(returns, step)
            for step in range(self.lookback())
        }
        step = self.label_lead()
        ahead = self.n_ahead()
        if ahead == 1:
            columns[self.label_name()] = lead(returns, step)
        else:
            for k in range(1, ahead + 1):
                columns[f"y_ahead_{k}"] = lead(returns, k * step)
        return columns



#: Deliberately EMPTY — this pack registers nothing. The two bases are
#: abstract (``node_class_errors`` refuses them at registration and at
#: import-path resolve alike), and the wiring story needs no registry:
#: user projects subclass and reference by import path, exactly as the
#: reference subclasses above are referenced by the shipped example
#: (``examples/pipeline/numpy-features.json``).
NODE_KINDS = ()
