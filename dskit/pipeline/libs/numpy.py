"""The tier-2 numpy pack — generic array feature engineering (docs/25 §2).

Two doorways, both SUBCLASS HOOKS on the ``PyomoSolve`` pattern: a
project implements ``apply(arrays, params)`` and the base does the rest —
envelope→array lifting, per-instrument grouping, the writeback (or the
feature rows), and the CAUSALITY GUARD. Raw functions are never
referenceable (D-145), and the ``"fn": "pkg.mod:function"`` variant was
ruled OUT for this pack (I-228, resolved subclass-only): a function named
by path is code OUTSIDE the run identity — edit the math, and the hash
says nothing changed.

* :class:`ArrayMap` (role ``transform``, outputs ``("records",)``) —
  ``apply``'s columns are rewritten ENVELOPE numeric fields; the base
  rebuilds each record immutably (``dataclasses.replace`` / dict copy)
  and returns the stream in its original order.
* :class:`ArrayFeatures` (role ``tensor``, outputs ``("rows",
  "metrics")``) — the same lifting, but ``apply``'s columns become
  FEATURE ROWS (one dict per record) for downstream fit/train nodes.

**Wiring is by import path — deliberately.** :data:`NODE_KINDS` is empty
and nothing here registers: the two bases are abstract (a registry or an
import-path resolve refuses them by name), and a user project's subclass
needs no registration either — ``"uses": "their.module:TheirSubclass"``
resolves directly. Subclassing and referencing by path IS the wiring.
:class:`LogMid` and :class:`TrailingReturns` are the concrete reference
subclasses documents, examples and tests point at.

**The causality guard is a mechanical SCREEN, not a proof** (knob
``causality_check``, default ``True``). A window may look BACKWARD only:
a global z-score or a centered rolling mean silently reads the future —
the classic leak. This generalizes the stage-list ruling (the stream
kinds are all pointwise; "a future windowed kind must preserve that
guarantee explicitly") to arbitrary array code, by PREFIX CONSISTENCY:
after ``apply`` runs on the full arrays, the base re-runs it on
truncated prefixes at deterministic cut points — the quarter, half and
three-quarter points AND ``n - 1``, no RNG; the tail cut is what
catches a leak confined to the last rows, the S1 #2 blind spot of the
old two-point grid — and REFUSES when any overlapping output moved.
What a causal ``apply`` computes at ``t`` cannot depend on rows after
``t``, so removing the future must not change it. Outputs must match
exactly or nan-equal — leading warm-up NaNs from trailing windows are
legal and compare equal. A ``cuts`` knob (a list of distinct ints
``>= 1``) REPLACES the default grid; whatever the grid, each cut is
kept to a strict prefix ``1 <= cut < n`` per instrument, and a cut past
a group's length checks nothing there. Honesty about the bar: the
guard can only refuse drift its finitely many cut points expose — a
transform keyed to lengths no cut probes slips through — so it is a
NECESSARY screen for the classic leaks, never a sufficient proof of
causality: passing means "not caught here", and the subclass's code
stays the thing to review. Three consequences worth knowing: the guard
needs a strict prefix, so a one-record instrument gives it no leverage;
``apply`` must handle ANY prefix of its input — emit warm-up NaNs for
short arrays instead of refusing them; and setting
``causality_check: false`` (huge arrays, twice-per-cut cost) is a
DECISION the document owns: the guard is the only mechanical check on
the subclass's code, and off means unchecked.

**Missing-field policy — pass through, never crash mid-stream.** A
record that cannot be lifted (no ``instrument``, or a non-int
``asof_ms``) passes through UNTOUCHED in :class:`ArrayMap` and yields NO
row in :class:`ArrayFeatures`; a missing or non-numeric numeric field
lifts as NaN; an ``apply`` output the envelope cannot hold (NaN/inf, a
non-positive price, a ``lead_frac`` outside (0, 1), a field the record
type lacks) leaves that record's field UNCHANGED. Everything is counted
and logged; none of it raises. A row without a ``contract`` id is
skipped (a feature row with no identity is unusable downstream) — the
record still participates in the arrays, so later trailing windows see
it.

Subclass knobs: extend ``_PARAMS`` (which keeps the default-deny and the
conformance fuzz covering the new names) and override
``validate_params``, calling ``super().validate_params(params)`` first —
:class:`TrailingReturns` is the worked example.

Import cost: stdlib only — numpy lives strictly inside the helpers
``run()`` calls at execute time (``tests/pipeline/test_purity.py``
enforces this twice, statically and with numpy blocked from a fresh
interpreter).
"""

from __future__ import annotations

import math
from abc import abstractmethod
from dataclasses import is_dataclass, replace

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import Node

__all__ = [
    "ArrayFeatures",
    "ArrayMap",
    "LogMid",
    "NODE_KINDS",
    "TrailingReturns",
]

#: Sentinel for "the record carries no such field" — distinct from every
#: real value a field could hold (``None`` included).
_MISSING = object()

#: Envelope numeric fields lifted into every per-instrument array set,
#: alongside ``asof_ms``. Missing/non-numeric values lift as NaN so the
#: arrays stay aligned one-entry-per-record.
_LIFTED_FIELDS = ("bid", "ask", "mid", "lead_frac")

#: Row keys :class:`ArrayFeatures` owns — a feature column may not take
#: one of these names (it would silently clobber the row's identity).
_ROW_IDENTITY = ("instrument", "contract", "asof_ms", "group")


def _price_ok(value) -> bool:
    """The envelope's price rule: finite and strictly positive."""
    return math.isfinite(value) and value > 0.0


def _lead_ok(value) -> bool:
    """The envelope's ``lead_frac`` rule: finite, strictly in (0, 1)."""
    return math.isfinite(value) and 0.0 < value < 1.0


#: Envelope fields :class:`ArrayMap` may write back, each with the
#: acceptance rule the envelope itself enforces — a value that fails it
#: leaves the record's field UNCHANGED (the pass-through policy) instead
#: of exploding inside ``dataclasses.replace``.
_WRITEBACK = {
    "bid": _price_ok,
    "ask": _price_ok,
    "mid": _price_ok,
    "lead_frac": _lead_ok,
}


def _field(record, name):
    """THE attr-or-key accessor: ``record[name]`` for mappings,
    ``record.name`` for objects, :data:`_MISSING` when absent — dicts and
    ``MarketRecord``-like envelopes are interchangeable by construction
    (the ``kinds_flow`` convention)."""
    if isinstance(record, dict):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


def _num(value) -> float:
    """One lifted cell: a real finite number as ``float``, anything else
    (absent, ``None``, bool, string, inf) as NaN — missing is data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("nan")
    out = float(value)
    return out if math.isfinite(out) else float("nan")


def _lift(records):
    """Group by instrument, sort ascending ``asof_ms``, lift to arrays.

    Returns ``(groups, unlifted)`` where ``groups`` maps instrument to
    ``(original_indices, arrays)`` — indices into ``records`` in time
    order (ties broken by stream position, so the sort is deterministic)
    and ``arrays`` per the ``apply`` contract — and ``unlifted`` lists
    the indices of records that could not be lifted (no ``instrument``
    string or no int ``asof_ms``): the pass-through set.

    numpy is imported HERE, at execute time — never at module level
    (tier-2 purity, D-146).
    """
    import numpy as np

    by_instrument = {}
    unlifted = []
    for idx, record in enumerate(records):
        instrument = _field(record, "instrument")
        asof_ms = _field(record, "asof_ms")
        if (
            not isinstance(instrument, str)
            or not instrument
            or isinstance(asof_ms, bool)
            or not isinstance(asof_ms, int)
        ):
            unlifted.append(idx)
            continue
        by_instrument.setdefault(instrument, []).append((asof_ms, idx))
    groups = {}
    for instrument, pairs in by_instrument.items():
        pairs.sort()
        indices = [idx for _asof, idx in pairs]
        arrays = {"asof_ms": np.asarray([asof for asof, _ in pairs], dtype=np.int64)}
        for name in _LIFTED_FIELDS:
            arrays[name] = np.asarray(
                [_num(_field(records[idx], name)) for idx in indices],
                dtype=np.float64,
            )
        groups[instrument] = (indices, arrays)
    return groups, unlifted


def _copies(arrays):
    """Fresh copies for one ``apply`` call — a subclass that mutates its
    input must not corrupt the arrays the guard's later cuts re-read."""
    return {name: arr.copy() for name, arr in arrays.items()}


def _as_columns(key, instrument, value, n):
    """``apply``'s return held to its contract: a dict of non-empty
    string names to 1-D arrays of exactly ``n`` values (one per input
    row). Returns ``{name: np.ndarray}``; raises ``ValueError`` naming
    the node, the column and the instrument on any violation."""
    import numpy as np

    if not isinstance(value, dict):
        raise ValueError(
            f"{key}: apply() must return a dict of column name -> array, got "
            f"{type(value).__name__} (instrument {instrument!r})"
        )
    if not value:
        raise ValueError(
            f"{key}: apply() returned no columns (instrument {instrument!r}) — "
            "a transform that computes nothing is a wiring bug, not a no-op"
        )
    columns = {}
    for name, data in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{key}: apply() returned a non-string column name {name!r} "
                f"(instrument {instrument!r})"
            )
        arr = np.asarray(data)
        if arr.ndim != 1:
            raise ValueError(
                f"{key}: column {name!r} must be 1-D (one value per record), "
                f"got shape {arr.shape} (instrument {instrument!r})"
            )
        if len(arr) != n:
            raise ValueError(
                f"{key}: column {name!r} has {len(arr)} value(s) for {n} "
                f"record(s) (instrument {instrument!r}) — apply() must return "
                "one value per input row"
            )
        columns[name] = arr
    return columns


def _check_schema(key, schema, columns, instrument):
    """One column schema per node: ``apply`` must return the SAME column
    set for every instrument — a data-dependent schema hands downstream
    consumers ragged rows. Returns the (possibly newly-pinned) schema."""
    names = frozenset(columns)
    if schema is None or schema == names:
        return names
    raise ValueError(
        f"{key}: apply() returned column(s) {sorted(names)} for "
        f"{instrument!r} but {sorted(schema)} for an earlier instrument — "
        "the column schema must not depend on the data"
    )


def _cut_points(n, declared=None):
    """The guard's deterministic truncation points, kept to strict
    prefixes (1 <= cut < n) and deduped. No RNG — the guard must fail
    the same way on every run.

    Default grid: the quarter, half and three-quarter points PLUS
    ``n - 1`` — the tail cut is the one that holds the last comparable
    position against a future-free prefix, catching leaks confined to
    the array's end (S1 #2). A ``declared`` list (the ``cuts`` knob)
    REPLACES the grid; cuts past a group's length simply drop, so a
    short instrument may see fewer — or no — checks (the documented
    no-leverage limit)."""
    pool = declared if declared else (n // 4, n // 2, (3 * n) // 4, n - 1)
    return sorted({cut for cut in pool if 1 <= cut < n})


def _prefix_equal(a, b) -> bool:
    """Exact or nan-equal match of two column prefixes. ``equal_nan``
    chokes on non-numeric dtypes (strings), where plain equality is the
    right bar anyway."""
    import numpy as np

    try:
        return bool(np.array_equal(a, b, equal_nan=True))
    except (TypeError, ValueError):
        return bool(np.array_equal(a, b))


def _row_value(value):
    """One array cell as a plain row value: numpy scalars become Python
    scalars, and a non-finite float becomes ``None`` — an absent belief
    (warm-up NaN), never a number some reader cannot parse."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _ArrayApply(Node):
    """The shared machinery both doorways stand on: lifting, the apply
    contract, the causality guard, default-deny params. Abstract — never
    referenceable itself; :class:`ArrayMap` and :class:`ArrayFeatures`
    are the two public shapes."""

    #: The base's own knobs. Subclasses EXTEND this tuple with theirs —
    #: that keeps ``validate_params``'s default-deny and the conformance
    #: suite's per-knob fuzz covering the new names.
    _PARAMS = ("causality_check", "cuts")

    @classmethod
    def validate_params(cls, params):
        """Default-deny against ``cls._PARAMS``, then the base's own
        knobs. Subclasses override, call ``super().validate_params(params)``
        first, and append their own checks (see
        :class:`TrailingReturns`)."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        flag = params.get("causality_check", True)
        if not is_node_ref(flag) and not isinstance(flag, bool):
            problems.append(f"causality_check must be a bool, got {flag!r}")
        cuts = params.get("cuts")
        if cuts is not None and not is_node_ref(cuts):
            # ints and >= 1 are checkable at PLAN; the upper bound is each
            # instrument's own length, only knowable at run (_cut_points
            # keeps declared cuts to strict prefixes there).
            if not isinstance(cuts, (list, tuple)) or not cuts:
                problems.append(
                    f"cuts must be a non-empty list of ints >= 1 when "
                    f"present, got {cuts!r}"
                )
            else:
                bad = [
                    c
                    for c in cuts
                    if isinstance(c, bool) or not isinstance(c, int) or c < 1
                ]
                if bad:
                    problems.append(f"cuts entries must be ints >= 1, got {bad!r}")
                good = [
                    c
                    for c in cuts
                    if not isinstance(c, bool) and isinstance(c, int) and c >= 1
                ]
                dupes = sorted({c for c in good if good.count(c) > 1})
                if dupes:
                    problems.append(f"cuts repeats {dupes} — declare each cut once")
        return problems

    def validate_inputs(self, inputs):
        """``records`` must be a LIST. A one-shot iterable is refused by
        name — validation must never consume what ``run()`` reads (the
        F-220 #6 shape), and refusing loudly is the lawful alternative."""
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
        """The subclass hook — the ONLY code a project writes.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            ONE instrument's records, aligned and sorted ascending by
            ``asof_ms``: key ``"asof_ms"`` (int64) plus one float64 array
            per envelope numeric field (``"bid"``, ``"ask"``, ``"mid"``,
            ``"lead_frac"``), NaN where the record carries no value. The
            arrays are fresh copies — mutate freely.
        params : dict
            ``self.params``, passed through for convenience.

        Returns
        -------
        dict of str -> array-like
            One 1-D column per name, exactly one value per input row.
            Position ``t`` may depend on rows ``<= t`` ONLY — the guard
            re-runs this method on prefixes and refuses any drift — and
            the method must therefore accept any prefix of its input
            (emit warm-up NaNs, never refuse short arrays).
        """
        raise NotImplementedError

    # -- shared plumbing ----------------------------------------------------

    def _refuse_columns(self, names):
        """Class-specific column-name rule; the base accepts any name."""

    def _columns_for(self, instrument, arrays, n):
        """One instrument through ``apply``, held to the whole contract:
        shape-checked, class-legal names, and — unless the document said
        otherwise — causal."""
        full = _as_columns(
            self.key, instrument, self.apply(_copies(arrays), self.params), n
        )
        self._refuse_columns(sorted(full))
        if self.params.get("causality_check", True):
            self._assert_causal(instrument, arrays, full, n)
        return full

    def _assert_causal(self, instrument, arrays, full, n):
        """The prefix-consistency guard (module docstring): re-run
        ``apply`` on each truncated prefix and refuse, by name, any
        overlapping output that moved. A NECESSARY mechanical screen,
        not a sufficiency proof — only drift the cut points expose can
        be caught, so "no refusal" means "not caught here", never
        "proven causal"."""
        declared = self.params.get("cuts")
        for cut in _cut_points(n, declared):
            prefix = {name: arr[:cut].copy() for name, arr in arrays.items()}
            try:
                trial = self.apply(prefix, self.params)
            except Exception as exc:
                raise ValueError(
                    f"{self.key}: apply() failed on a truncated prefix "
                    f"({cut}/{n} rows, instrument {instrument!r}) — the "
                    "causality guard re-runs apply() on prefixes, so it must "
                    "handle any prefix of its input (emit warm-up NaNs "
                    f"instead of refusing short arrays): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            trial = _as_columns(self.key, instrument, trial, cut)
            if set(trial) != set(full):
                raise ValueError(
                    f"{self.key}: apply() is not causal — the column set "
                    f"changed when the future was removed ({sorted(full)} "
                    f"became {sorted(trial)} at cut {cut}/{n}, instrument "
                    f"{instrument!r})"
                )
            for name in sorted(full):
                if not _prefix_equal(full[name][:cut], trial[name]):
                    raise ValueError(
                        f"{self.key}: apply() is not causal — output at t "
                        "changed when the future was removed (column "
                        f"{name!r}, instrument {instrument!r}, cut "
                        f"{cut}/{n}). A window may look BACKWARD only: "
                        "global statistics and centered windows read rows "
                        "after t. Fix apply(); causality_check=false skips "
                        "this guard, and turning it off is a decision the "
                        "document owns."
                    )


class ArrayMap(_ArrayApply):
    """Rewrite envelope numeric fields with array code (role
    ``transform``) — the generic feature-engineering doorway, subclass
    form (I-228).

    Subclass, implement :meth:`~_ArrayApply.apply`, and reference the
    subclass by import path. The base lifts the stream per instrument,
    calls ``apply``, runs the causality guard, and writes the returned
    columns back onto the records — ``dataclasses.replace`` for dataclass
    envelopes, a shallow copy for dict records — returning the stream in
    its ORIGINAL order.

    ``apply`` may name only rewritable envelope fields
    (``bid``/``ask``/``mid``/``lead_frac``); any other column is refused
    by name — a derived feature belongs in :class:`ArrayFeatures` rows,
    not on the envelope. A value the envelope cannot hold (NaN/inf,
    non-positive price, ``lead_frac`` outside (0, 1), a field the record
    type lacks) leaves that record's field unchanged; unliftable records
    pass through untouched. Counted and logged, never raised (module
    docstring, missing-field policy).
    """

    role = "transform"
    outputs = ("records",)

    def validate_inputs(self, inputs):
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
        records = inputs["records"]
        groups, unlifted = _lift(records)
        rebuilt = list(records)
        schema = None
        written = unchanged = 0
        for instrument in sorted(groups):
            indices, arrays = groups[instrument]
            columns = self._columns_for(instrument, arrays, len(indices))
            schema = _check_schema(self.key, schema, columns, instrument)
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
            "array map wrote %d value(s) across %d instrument(s); "
            "%d value(s) left unchanged (unwritable), %d record(s) passed "
            "through unlifted",
            written,
            len(groups),
            unchanged,
            len(unlifted),
        )
        return {"records": rebuilt}


class ArrayFeatures(_ArrayApply):
    """Derive feature rows with array code (role ``tensor``) — the same
    lifting and guard as :class:`ArrayMap`, but ``apply``'s columns
    become FEATURE ROWS for downstream fit/train nodes: one
    ``{"instrument", "contract", "asof_ms", "group", <column>: value,
    ...}`` dict per lifted record, in the stream's original order.

    A non-finite value lands as ``None`` — an absent belief (the warm-up
    NaN of a trailing window), matching the signal convention that no
    coverage is ``None``, never a fabricated number. A record without a
    ``contract`` id yields no row (a row with no identity is unusable
    downstream) but still participates in the arrays, so later windows
    see it. A feature column may not take a row-identity name — refused
    by name.

    ``metrics`` carries the numeric summary (``n_rows``, ``n_records``,
    ``n_instruments``, ``n_columns``) for the sinks; the bulk rows ride
    under ``rows``, never under ``metrics``.
    """

    role = "tensor"
    outputs = ("rows", "metrics")

    def _refuse_columns(self, names):
        clash = sorted(set(names) & set(_ROW_IDENTITY))
        if clash:
            raise ValueError(
                f"{self.key}: apply() returned column(s) {clash} that collide "
                f"with the row identity fields {list(_ROW_IDENTITY)} — rename "
                "the feature columns"
            )

    def run(self, ctx, inputs):
        records = inputs["records"]
        groups, unlifted = _lift(records)
        schema = None
        values_by_index = {}
        for instrument in sorted(groups):
            indices, arrays = groups[instrument]
            columns = self._columns_for(instrument, arrays, len(indices))
            schema = _check_schema(self.key, schema, columns, instrument)
            names = sorted(columns)
            for j, idx in enumerate(indices):
                values_by_index[idx] = {
                    name: _row_value(columns[name][j]) for name in names
                }
        rows = []
        no_contract = 0
        for idx, record in enumerate(records):
            values = values_by_index.get(idx)
            if values is None:
                continue  # never lifted — counted in unlifted
            contract = _field(record, "contract")
            if not isinstance(contract, str) or not contract:
                no_contract += 1
                continue
            group = _field(record, "group")
            rows.append(
                {
                    "instrument": _field(record, "instrument"),
                    "contract": contract,
                    "asof_ms": _field(record, "asof_ms"),
                    "group": group if isinstance(group, str) else None,
                    **values,
                }
            )
        metrics = {
            "n_rows": len(rows),
            "n_records": len(records),
            "n_instruments": len(groups),
            "n_columns": len(schema or ()),
        }
        self.log.info(
            "array features: %d row(s) x %d column(s) from %d record(s) "
            "(%d unlifted, %d without a contract id)",
            metrics["n_rows"],
            metrics["n_columns"],
            metrics["n_records"],
            len(unlifted),
            no_contract,
        )
        return {"rows": rows, "metrics": metrics}


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
    every cut.
    """

    def apply(self, arrays, params):
        import numpy as np

        return {"mid": np.log1p(arrays["mid"])}


class TrailingReturns(ArrayFeatures):
    """Reference :class:`ArrayFeatures`: the ``window``-step trailing
    return of ``mid`` — ``mid[t] / mid[t - window] - 1``, NaN (→ row
    ``None``) for the first ``window`` entries. Position ``t`` reads
    ``t`` and ``t - window`` only: backward-only by construction, and
    the worked example of the subclass-knob pattern (extend ``_PARAMS``,
    override ``validate_params`` calling ``super()`` first).
    """

    _PARAMS = ArrayFeatures._PARAMS + ("window",)

    @classmethod
    def validate_params(cls, params):
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
        import numpy as np

        mid = arrays["mid"]
        window = params["window"]
        out = np.full(mid.shape, np.nan)
        if len(mid) > window:
            with np.errstate(divide="ignore", invalid="ignore"):
                out[window:] = mid[window:] / mid[:-window] - 1.0
        return {"trailing_return": out}


#: Deliberately EMPTY — this pack registers nothing. The two bases are
#: abstract (``node_class_errors`` refuses them at registration and at
#: import-path resolve alike), and the wiring story needs no registry:
#: user projects subclass and reference by import path, exactly as the
#: reference subclasses above are referenced by the shipped example
#: (``examples/pipeline/numpy-features.json``).
NODE_KINDS = ()
