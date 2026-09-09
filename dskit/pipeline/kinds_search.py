"""The ``hpo-grid`` search kind — spec §10 step 3's first owned-kind build.

The grid lives in the document (docs/24 §8): ``space`` maps
``"node.param.path"`` override targets to value lists, ``objective``
is a ``$``-reference into a val-split score node, and the DRIVER owns
re-execution — this class never touches the DAG itself. ``run`` refuses
to execute without ``ctx.rerun``, the driver-injected subgraph seam
(:class:`dskit.pipeline.driver._SearchSeam`): each ``rerun(overrides)``
call re-executes the minimal dirty subgraph and returns the objective as
a float. The winning overrides are returned as ``best_params``; the
driver then applies them one final time so every downstream node
consumes the winner pass.

Determinism rules (all sha256, no RNG state — the ``testing.py``
discipline):

* **Enumeration.** The exhaustive grid is ``itertools.product`` over the
  space's SORTED keys, each key's values in their given order — the last
  sorted key varies fastest. Ties on score go to the FIRST trial in this
  enumeration order.
* **Subsampling.** When ``n_trials`` is present and smaller than the
  grid, each trial is ranked by
  ``sha256("{seed}:{canonical-json(overrides)}")`` (the node's own
  ``seed`` param, default 0; canonical = sorted keys, compact
  separators); the ``n_trials`` lexicographically SMALLEST digests
  survive, and the survivors are evaluated in enumeration order.
* **Seeds ensemble** (I-222 reading of §8 "per-trial seed ensemble"):
  when ``seeds`` is present, every trial is evaluated once per seed with
  an ADDITIONAL override setting the top-level ``"seed"`` param of every
  train-role node inside the re-execution subgraph THAT ALREADY DECLARES
  one (the driver surfaces those paths as ``ctx.rerun.seed_targets``;
  params that do not exist are never created), and the trial's score is
  the MEAN across seeds. ``best_params`` carries only the grid
  overrides — the final winner pass runs under the document's own pinned
  seeds, never a trial seed.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import types
import threading
from collections.abc import Mapping

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import parse_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "CandidateInventory",
    "HpoGrid",
    "OneStandardErrorSelector",
    "SelectionRecord",
    "TopTrials",
    "TrialLedger",
    "register",
]

#: A space key: a node key, a dot, then one or more param path segments.
_SPACE_KEY_OK = r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$"


_MAX_JSON_INT = 10**4096 - 1
_DEFAULT_MAX_CANDIDATES = 4096


class _EvidenceRefusal(ValueError):
    """A trusted static reason for refusing canonical evidence."""


def _evidence_message(error) -> str:
    """Return a safe static message from an internal evidence refusal."""
    if (
        type(error) is _EvidenceRefusal
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        return error.args[0]
    return "trial evidence cannot be frozen safely"


def _json_int_ok(value) -> bool:
    """Report whether an exact builtin integer has a portable JSON encoding."""
    return type(value) is int and -_MAX_JSON_INT <= value <= _MAX_JSON_INT


def _is_json_scalar(value) -> bool:
    """Report whether value is one admitted canonical JSON scalar."""
    if value is None or type(value) in (bool, str):
        return True
    if type(value) is int:
        return _json_int_ok(value)
    return type(value) is float and math.isfinite(value)


def _bounded_candidate_count(space, max_candidates) -> int:
    """Return the Cartesian count or refuse it before materialization."""
    count = 1
    for values in space.values():
        count *= len(values)
        if count > max_candidates:
            raise ValueError(
                f"candidate count {count} exceeds max_candidates {max_candidates}"
            )
    return count


def _grid(space) -> list:
    """The exhaustive grid, deterministically enumerated: sorted space
    keys, each key's values in their given order (the last sorted key
    varies fastest)."""
    keys = sorted(space)
    return [
        dict(zip(keys, combo)) for combo in itertools.product(*(space[k] for k in keys))
    ]


def _finite_objective(key, overrides, score) -> float:
    """The objective one rerun reported, or a refusal NAMING the trial.

    NaN defeats every strict comparison — ``x < nan`` and ``x > nan`` are
    both False — so a first-reported NaN would silently stick as "best"
    and the driver would then re-apply its overrides as the winner; an
    inf outranks every real trial the same way. A search cannot rank what
    the score node could not measure, so the trial fails loudly at the
    instant it reports, carrying its exact overrides and the value.
    Shared with the optuna pack: one rule, never two drifting ones.
    """
    if not math.isfinite(score):
        raise ValueError(
            f"{key}: trial {overrides!r} reported a non-finite objective "
            f"({score!r}) — NaN/inf cannot rank trials and must never win a "
            "search; fix the score node (or drop the space region that "
            "produces it) so every trial reports a finite number"
        )
    return score


def _trial_digest(seed, overrides) -> str:
    """The subsample rank of one trial: sha256 over the seed and the
    trial's canonical JSON (sorted keys, compact separators)."""
    canon = json.dumps(overrides, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{seed}:{canon}".encode()).hexdigest()


def _canonical_combo_key(snapshot) -> str:
    """Return canonical JSON for one already-copied overrides mapping."""
    if any(type(key) is not str for key in snapshot):
        raise ValueError("overrides keys must be exact builtin strings")
    try:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    except Exception as exc:
        raise ValueError(
            "overrides must contain only canonical JSON-scalar values"
        ) from exc


def _combo_key(overrides) -> str:
    """Return canonical JSON for one safe one-shot overrides snapshot."""
    if not isinstance(overrides, Mapping):
        raise ValueError("overrides must be a mapping")
    try:
        snapshot = dict(overrides)
    except Exception:
        raise ValueError("overrides could not be copied safely") from None
    return _canonical_combo_key(snapshot)


def _snapshot_evidence_fields(evidence_fields) -> tuple:
    """Return one validated immutable required-field contract."""
    if not isinstance(evidence_fields, (tuple, list)):
        raise ValueError(
            "evidence_fields must be a non-empty duplicate-free tuple/list of strings"
        )
    try:
        fields = tuple(field for field in evidence_fields)
    except Exception:
        raise ValueError(
            "evidence_fields could not be snapshotted safely"
        ) from None
    if (
        not fields
        or any(type(field) is not str or not field for field in fields)
        or len(set(fields)) != len(fields)
    ):
        raise ValueError(
            "evidence_fields must be a non-empty duplicate-free tuple/list of strings"
        )
    return fields


def _snapshot_overrides(overrides) -> tuple:
    """Return one canonical scalar-only overrides copy and its key."""
    if not isinstance(overrides, Mapping):
        raise ValueError("overrides must be a mapping")
    try:
        copied = dict(overrides)
    except Exception:
        raise ValueError("overrides could not be copied safely") from None
    try:
        snapshot = dict(_frozen_json(copied))
    except Exception as exc:
        raise ValueError(
            "overrides must use exact builtin JSON values"
        ) from exc
    return snapshot, _canonical_combo_key(snapshot)


def _freeze(value, _ancestors=frozenset(), *, strict=False):
    """Recursively make ``value`` immutable at rest: ``dict`` ->
    ``types.MappingProxyType`` (nested values frozen the same way),
    ``list``/``tuple`` -> ``tuple`` (elements frozen the same way,
    including a ``namedtuple`` — which loses its named-field access in
    exchange, a deliberate immutability-over-convenience tradeoff),
    ``set``/``frozenset`` -> ``frozenset`` (a set's own elements must
    already be hashable, i.e. already immutable by Python's own rules,
    so no further per-element recursion is needed there — and a
    hashable value can never itself be a cyclic dict/list/tuple).
    Anything else (a scalar, or a caller's own opaque object) is
    returned unchanged.

    Used by :meth:`TrialLedger.record` so caller-supplied ``**extra``
    fields (``cuts``, ``diagnostics``, ...) — explicitly part of what
    that ledger's own docstring says it records — get the same
    protection as ``overrides``/``score``, not just the row's own top
    level (skeptic review round 11, Major) or only dict/list/tuple
    shapes (round 12, Major — a set, the natural shape for this plan's
    own "boundary flags" diagnostic vocabulary, fell through unfrozen).

    ``_ancestors`` tracks the ``id()`` of every open dict/list/tuple on
    the CURRENT recursion path (a fresh frozenset per branch, not one
    shared mutable set — so the same object appearing twice in separate
    branches is not mistaken for a cycle). Unlike ``_combo_key``, which
    delegates circular-reference detection to ``json.dumps``'s
    C-accelerated encoder, this is hand-rolled Python recursion with no
    such protection built in — a circular ``**extra`` value used to
    crash with a bare, unnamed ``RecursionError`` (skeptic review round
    18, Major), and so did a merely very deep (non-circular) one, at a
    far shallower depth than the analogous ``overrides``/``json.dumps``
    path tolerates."""
    if isinstance(value, (Mapping, list, tuple)):
        marker = id(value)
        if marker in _ancestors:
            raise ValueError(
                "cannot freeze a circular reference — TrialLedger rows "
                "cannot store cyclic structures"
            )
        _ancestors = _ancestors | {marker}
    if isinstance(value, Mapping):
        return types.MappingProxyType(
            {k: _freeze(v, _ancestors, strict=strict) for k, v in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v, _ancestors, strict=strict) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(value)
    if strict and not isinstance(value, (type(None), bool, int, float, str)):
        raise ValueError("evidence values must be immutable JSON-like values")
    return value


def _frozen_json(value, ancestors=frozenset()):
    """Return an immutable, canonical-JSON-safe evidence snapshot."""
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in ancestors:
            raise _EvidenceRefusal("evidence cannot contain circular references")
        items = tuple(value.items())
        keys = tuple(key for key, _ in items)
        if any(type(key) is not str for key in keys):
            raise _EvidenceRefusal("evidence mapping keys must be strings")
        if len(set(keys)) != len(keys):
            raise _EvidenceRefusal("evidence mapping keys must be unique")
        next_ancestors = ancestors | {marker}
        return types.MappingProxyType(
            {key: _frozen_json(item, next_ancestors) for key, item in items}
        )
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in ancestors:
            raise _EvidenceRefusal("evidence cannot contain circular references")
        next_ancestors = ancestors | {marker}
        return tuple(_frozen_json(item, next_ancestors) for item in value)
    if isinstance(value, (set, frozenset)):
        raise _EvidenceRefusal("evidence sets are not canonical JSON values")
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        if not _json_int_ok(value):
            raise _EvidenceRefusal("evidence integers must have at most 4096 decimal digits")
        return value
    if type(value) is float and math.isfinite(value):
        return 0.0 if value == 0 else value
    if type(value) is float:
        raise _EvidenceRefusal("evidence numbers must be finite")
    raise _EvidenceRefusal("evidence values must be immutable JSON values")


def _json_obj(value):
    """Return a JSON-serializable copy of frozen evidence."""
    if isinstance(value, Mapping):
        return {key: _json_obj(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_obj(item) for item in value]
    return value


def _frozen_number(value, label):
    """Return one finite builtin score or standard-error number."""
    if type(value) is int:
        if _json_int_ok(value):
            return value
        raise _EvidenceRefusal(f"{label} integers must have at most 4096 decimal digits")
    if type(value) is float and math.isfinite(value):
        return 0.0 if value == 0 else value
    raise _EvidenceRefusal(
        f"{label} must be a finite builtin int or float"
    )



def _frozen_standard_error(value):
    """Return one finite nonnegative standard error with canonical zero."""
    number = _frozen_number(value, "se")
    if number < 0:
        raise _EvidenceRefusal("se must be non-negative")
    if type(number) is float and number == 0:
        return 0.0
    return number


def _simplicity_value(value, ancestors=frozenset(), depth=0):
    """Return one finite, acyclic, totally-orderable simplicity key."""
    if depth > 64:
        raise ValueError("simplicity_key nesting exceeds 64 levels")
    if type(value) in (str, int):
        if type(value) is int and not _json_int_ok(value):
            raise ValueError(
                "simplicity_key integers must have at most 4096 decimal digits"
            )
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("simplicity_key numbers must be finite")
        return value
    if type(value) is tuple:
        marker = id(value)
        if marker in ancestors:
            raise ValueError("simplicity_key cannot contain circular references")
        next_ancestors = ancestors | {marker}
        return tuple(
            _simplicity_value(item, next_ancestors, depth + 1) for item in value
        )
    raise ValueError(
        "simplicity_key must be a number, string, or tuple of those"
    )

def _contains_nan(value) -> bool:
    """True if ``value`` is NaN itself, or a ``tuple``/``list`` that
    contains a NaN anywhere, recursively.

    NOT the same as ``value != value``: CPython's tuple/list ``!=``
    shortcuts an object compared to ITSELF via per-element IDENTITY
    (``PyObject_RichCompareBool``), so it never actually invokes float
    NaN semantics on an element nested inside a container being compared
    to itself — a composite key like ``(1, float("nan"))`` silently
    passes ``key != key`` even though it plainly contains a NaN (skeptic
    review round 14, Critical). This walks the structure explicitly
    instead."""
    if isinstance(value, (tuple, list)):
        return any(_contains_nan(v) for v in value)
    try:
        return math.isnan(value)
    except TypeError:
        return False


def _contains_unordered_set(value) -> bool:
    """True if ``value`` is a ``set``/``frozenset``, or a ``tuple``/
    ``list`` that contains one anywhere, recursively.

    Python's ``<`` on sets is a proper-subset test — a PARTIAL order
    that never raises ``TypeError`` for two unrelated sets, it just
    silently returns ``False`` in BOTH directions. That defeats the
    "refuse incomparable simplicity_key types" guard (which relies on
    ``<`` raising for a genuinely incomparable pair) and makes two
    distinct, unrelated sets look TIED instead of refused — the
    strict-compare winner loop then silently keeps whichever candidate
    was recorded first, regardless of true simplicity (skeptic review
    round 15, Major)."""
    if isinstance(value, (set, frozenset)):
        return True
    if isinstance(value, (tuple, list)):
        return any(_contains_unordered_set(v) for v in value)
    return False


def _subsample(trials, n_trials, seed) -> list:
    """The ``n_trials`` trials with the smallest :func:`_trial_digest`,
    kept in enumeration order (module docstring rule)."""
    ranked = sorted(
        range(len(trials)), key=lambda i: (_trial_digest(seed, trials[i]), i)
    )
    keep = set(ranked[:n_trials])
    return [t for i, t in enumerate(trials) if i in keep]


class CandidateInventory:
    """One frozen, deterministically ordered set of hyperparameter
    combinations, built once and handed unchanged to every caller.

    Not a :class:`Node` — a plain object a domain caller (e.g. final-model
    per-lead HPO,
    Phase 1, ADR-0113) builds once and passes to N independent selections,
    so every selection receives byte-identical candidates. Reuses
    :func:`_grid`'s enumeration order (sorted space keys, last sorted key
    varies fastest) and :func:`_subsample`'s pinned sha256 selection, so
    dskit has one canonical way to freeze-and-hash a grid rather than a
    second one that could silently disagree with :class:`HpoGrid`.

    Parameters
    ----------
    space : dict
        ``"name"`` -> non-empty list of canonical JSON-scalar values.
        Builtin integers have at most 4096 decimal digits. Unlike
        ``HpoGrid.space``, keys are plain parameter names a domain caller
        applies directly to its own estimator, not ``"node.param.path"``
        DAG override targets.
    max_candidates : int, optional
        Positive exact-builtin cap on the full Cartesian product before it is
        materialized (default: 4096). The accepted cap is part of this
        inventory's digest, so callers can audit both its candidates and
        resource authority. n_trials never bypasses this cap.
    n_trials : int, optional
        Subsample bar. Absent means exhaustive.
    seed : int
        Subsample rank seed (default 0); unused when ``n_trials`` is
        absent or not smaller than the full grid.

    Attributes
    ----------
    combinations : tuple[types.MappingProxyType]
        The frozen, ordered combinations. Each is read-only: an attempted
        in-place mutation — one lead's code accidentally corrupting a
        combination shared with every other lead — raises ``TypeError``
        immediately instead of silently drifting one lead's evidence from
        another's.
    max_candidates : int
        The immutable caller-visible full-grid materialization cap.
    digest : str
        sha256 over canonical JSON of max_candidates and combinations — a
        caller can audit both candidate identity and the bound that authorized
        eager materialization.
    Examples
    --------
    >>> inventory = CandidateInventory({"depth": [1, 2]}, max_candidates=2)
    >>> inventory.combinations[0]["depth"]
    1
    """


    __slots__ = ("_combinations", "_digest", "_keys", "_max_candidates", "_sealed")

    def __setattr__(self, name, value):
        """Set construction state once and refuse later mutation."""
        if getattr(self, "_sealed", False):
            raise AttributeError("CandidateInventory is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        space,
        *,
        max_candidates=_DEFAULT_MAX_CANDIDATES,
        n_trials=None,
        seed=0,
    ):
        problems = []
        space_snapshot = {}
        if not isinstance(space, dict):
            problems.append("space must be a non-empty dict of name -> non-empty value list")
        else:
            try:
                entries = tuple(space.items())
            except Exception:
                raise ValueError(
                    "space could not be snapshotted safely"
                ) from None
            if not entries:
                problems.append("space must be a non-empty dict of name -> non-empty value list")
            for index, entry in enumerate(entries):
                try:
                    name, supplied_values = entry
                except Exception as exc:
                    raise ValueError(
                        f"space entry {index} must be a name/value pair"
                    ) from exc
                if type(name) is not str or not name:
                    problems.append("space keys must be non-empty strings")
                    continue
                if name in space_snapshot:
                    problems.append(f"space must not repeat key {name!r}")
                    continue
                if isinstance(supplied_values, (list, tuple)):
                    try:
                        space_snapshot[name] = tuple(
                            0.0 if type(value) is float and value == 0 else value
                            for value in supplied_values
                        )
                    except Exception as exc:
                        raise ValueError(
                            f"space[{name!r}] values could not be "
                            "snapshotted safely"
                        ) from exc
                else:
                    space_snapshot[name] = supplied_values
            for name, values in space_snapshot.items():
                if type(name) is not str or not name:
                    problems.append(f"space key {name!r} must be a non-empty string")
                if not isinstance(values, tuple) or not values:
                    problems.append(
                        f"space[{name!r}] must be a non-empty list of JSON "
                        "scalars"
                    )
                    continue
                oversized = [
                    value for value in values
                    if type(value) is int and not _json_int_ok(value)
                ]
                bad = [value for value in values if not _is_json_scalar(value)]
                if oversized:
                    problems.append(
                        f"space[{name!r}] integers must have at most "
                        "4096 decimal digits"
                    )
                elif bad:
                    problems.append(
                        f"space[{name!r}] values must be canonical JSON "
                        "scalars (null/bool/number/string)"
                    )
                else:
                    encoded = [json.dumps(value) for value in values]
                    if len(set(encoded)) != len(encoded):
                        problems.append(
                            f"space[{name!r}] must not contain duplicate values"
                        )
        if type(max_candidates) is int and not _json_int_ok(max_candidates):
            problems.append(
                "max_candidates integers must have at most 4096 decimal digits"
            )
        elif type(max_candidates) is not int or max_candidates < 1:
            problems.append("max_candidates must be a positive int")
        if type(n_trials) is int and not _json_int_ok(n_trials):
            problems.append("n_trials integers must have at most 4096 decimal digits")
        elif n_trials is not None and (type(n_trials) is not int or n_trials < 1):
            problems.append("n_trials must be an int >= 1")
        if type(seed) is int and not _json_int_ok(seed):
            problems.append("seed integers must have at most 4096 decimal digits")
        elif type(seed) is not int or seed < 0:
            problems.append("seed must be an int >= 0")
        if problems:
            raise ValueError("; ".join(problems))

        _bounded_candidate_count(space_snapshot, max_candidates)
        trials = _grid(space_snapshot)
        if n_trials is not None and n_trials < len(trials):
            trials = _subsample(trials, n_trials, seed)
        self._max_candidates = max_candidates
        self._combinations = tuple(types.MappingProxyType(dict(t)) for t in trials)
        self._keys = frozenset(_combo_key(t) for t in trials)
        canon = json.dumps(
            {"max_candidates": max_candidates, "combinations": trials},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._digest = hashlib.sha256(canon.encode()).hexdigest()
        self._sealed = True

    @property
    def combinations(self) -> tuple:
        """Return the immutable ordered candidate combinations."""
        return self._combinations

    @property
    def max_candidates(self) -> int:
        """Return the immutable full-grid materialization cap.

        Examples
        --------
        >>> CandidateInventory({"depth": [1]}, max_candidates=1).max_candidates
        1
        """
        return self._max_candidates

    @property
    def digest(self) -> str:
        """Return the deterministic candidate inventory digest."""
        return self._digest

    def contains(self, overrides) -> bool:
        """Report membership from one canonical caller-mapping snapshot."""
        _, key = _snapshot_overrides(overrides)
        return key in self._keys


class TrialLedger:
    """A complete, append-only record of every trial evaluated against one
    :class:`CandidateInventory`.

    Plan §6 Phase 1.5: "the ledger records every candidate, score
    components, fit seed, cuts, row counts, parameters, diagnostics,
    selected winner, and inventory digest. A score-only output must fail
    the contract." This object is generic: a row is
    ``{"overrides": dict, "score": float, **extra}``. Scores and an
    ``se`` extra must be a finite nonnegative builtin ``int`` or ``float``; negative zero is
    canonicalized to positive ``0.0``. Builtin integers have at most
    4096 decimal digits, and other evidence is canonical
    JSON data. It never inspects what ``extra``
    means (fit seed, cuts, row counts, per-component
    scores, boundary flags, ...) — only that every row supplies the
    caller-declared ``required_fields``, and that every recorded
    ``overrides`` is an actual member of the inventory, recorded at most
    once.

    Parameters
    ----------
    inventory : CandidateInventory
        An exact frozen CandidateInventory value; subclasses are refused so no
        overridable property can alter this ledger's bound candidate identity.
    evidence_fields : tuple[str]
        Non-empty immutable names every row must carry. Reserved row fields
        overrides and score are refused.
    Examples
    --------
    ledger = TrialLedger(CandidateInventory({"depth": [1]}), evidence_fields=("se",))
    ledger.record({"depth": 1}, score=0.25, se=0.05)
    # -> ledger.is_complete() is True
    """


    __slots__ = ("_inventory", "_evidence_fields", "_state", "_lock", "_sealed")

    #: record()'s own named params — never captured into **extra, so
    #: naming either here would make every row unconditionally refuse
    #: (skeptic review round 4, Major).
    _RESERVED_FIELDS = frozenset({"overrides", "score"})

    def __setattr__(self, name, value):
        """Seal ledger identity and state after construction."""
        if getattr(self, "_sealed", False) and name in self.__slots__:
            raise AttributeError("TrialLedger is immutable outside record transitions")
        object.__setattr__(self, name, value)

    def __init__(self, inventory, *, evidence_fields):
        if type(inventory) is not CandidateInventory:
            raise TypeError("inventory must be an exact CandidateInventory")
        evidence_fields = _snapshot_evidence_fields(evidence_fields)
        reserved = self._RESERVED_FIELDS.intersection(evidence_fields)
        if reserved:
            raise ValueError(
                f"evidence_fields must not name reserved fields {sorted(reserved)!r} — "
                "'overrides'/'score' are record()'s own params, never "
                "part of **extra, so requiring either would make every "
                "row unconditionally refuse"
            )
        self._inventory = inventory
        self._evidence_fields = evidence_fields
        self._state = ((), frozenset(), types.MappingProxyType({}))
        self._lock = threading.RLock()
        self._sealed = True

    @property
    def evidence_fields(self) -> tuple:
        """Return the immutable exact per-row evidence contract."""
        return self._evidence_fields

    @property
    def inventory_digest(self) -> str:
        return self._inventory.digest

    @property
    def expected_count(self) -> int:
        return len(self._inventory.combinations)

    @property
    def rows(self) -> tuple:
        with self._lock:
            rows, _, _ = self._state
            rows_by_key = {_combo_key(row["overrides"]): row for row in rows}
            return tuple(
                rows_by_key[key]
                for key in (_combo_key(item) for item in self._inventory.combinations)
                if key in rows_by_key
            )

    def is_complete(self) -> bool:
        with self._lock:
            rows, _, _ = self._state
            return len(rows) == self.expected_count

    def to_obj(self) -> dict:
        """Return a lock-backed complete canonical-JSON-safe ledger snapshot."""
        with self._lock:
            rows, _, _ = self._state
            if len(rows) != self.expected_count:
                raise ValueError("cannot serialize an incomplete TrialLedger")
            rows_by_key = {_combo_key(row["overrides"]): row for row in rows}
            rows = tuple(
                rows_by_key[key]
                for key in (_combo_key(item) for item in self._inventory.combinations)
            )
        return {
            "inventory_digest": self.inventory_digest,
            "evidence_fields": list(self._evidence_fields),
            "rows": [_json_obj(row) for row in rows],
        }

    @property
    def digest(self) -> str:
        """Return the SHA-256 digest of complete canonical ledger data."""
        canonical = json.dumps(
            self.to_obj(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def record(self, overrides, score, **extra) -> None:
        """Prepare then atomically commit one immutable trial row."""
        snapshot, key = self._snapshot_trial(overrides)
        self._validate_trial(key, snapshot, extra)
        token = object()
        try:
            self._reserve(key, snapshot, token)
            row = self._freeze_trial(snapshot, score, extra)
            self._commit(key, row, token)
        except BaseException:
            self._release_if_uncommitted(key, token)
            raise

    def _reserve(self, key, snapshot, token):
        """Atomically reserve one candidate for one admission token."""
        with self._lock:
            rows, seen, pending = self._state
            if key in seen or key in pending:
                raise ValueError(
                    f"{snapshot!r} has already recorded or is recording a trial "
                    "in this ledger"
                )
            next_pending = dict(pending)
            next_pending[key] = token
            self._replace_state(rows, seen, next_pending)

    def _commit(self, key, row, token):
        """Atomically replace this token's reservation with one committed row."""
        with self._lock:
            rows, seen, pending = self._state
            if pending.get(key) is not token:
                raise ValueError("trial reservation is no longer owned by this record")
            next_pending = dict(pending)
            del next_pending[key]
            self._replace_state(rows + (row,), seen | {key}, next_pending)

    def _release_if_uncommitted(self, key, token):
        """Release only this admission's uncommitted reservation."""
        with self._lock:
            rows, seen, pending = self._state
            if key not in seen and pending.get(key) is token:
                next_pending = dict(pending)
                del next_pending[key]
                self._replace_state(rows, seen, next_pending)

    def _replace_state(self, rows, seen, pending):
        """Install one immutable state snapshot from a transition method."""
        object.__setattr__(
            self, "_state", (rows, seen, types.MappingProxyType(pending))
        )

    def _snapshot_trial(self, overrides):
        """Return the caller's one validated overrides snapshot and key."""
        try:
            return _snapshot_overrides(overrides)
        except ValueError:
            raise
        except Exception:
            raise ValueError(
                "trial overrides evidence cannot be snapshotted"
            ) from None

    def _validate_trial(self, key, snapshot, extra):
        """Refuse a non-member or incomplete trial before reservation."""
        if key not in self._inventory._keys:
            raise ValueError(
                f"{snapshot!r} is not a member of this ledger's CandidateInventory"
            )
        missing = [
            field for field in self._evidence_fields
            if field not in extra or extra[field] is None
        ]
        if missing:
            raise ValueError(
                f"trial {snapshot!r} is missing evidence field(s) {missing!r}"
            )

    def _freeze_trial(self, snapshot, score, extra):
        """Freeze one already-reserved trial outside the admission lock."""
        try:
            frozen_score = _frozen_number(score, "score")
            frozen_extra = {
                name: _frozen_standard_error(value) if name == "se"
                else _frozen_json(value)
                for name, value in extra.items()
            }
        except _EvidenceRefusal as exc:
            raise ValueError(_evidence_message(exc)) from None
        except RecursionError:
            raise ValueError(
                f"trial {snapshot!r}: score/extra nesting too deep to freeze safely"
            ) from None
        except Exception:
            raise ValueError(
                f"trial {snapshot!r} evidence cannot be frozen"
            ) from None
        return types.MappingProxyType(
            {
                "overrides": types.MappingProxyType(snapshot),
                "score": frozen_score,
                **frozen_extra,
            }
        )


class SelectionRecord:
    """Immutable JSON-safe evidence for one completed selection."""

    __slots__ = ("_payload", "_digest", "_sealed")

    def __setattr__(self, name, value):
        """Refuse mutation after the canonical record is constructed."""
        if getattr(self, "_sealed", False):
            raise AttributeError("SelectionRecord is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, payload):
        try:
            frozen = _frozen_json(payload)
            canonical = json.dumps(
                _json_obj(frozen), sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except _EvidenceRefusal as exc:
            raise ValueError(_evidence_message(exc)) from None
        except Exception:
            raise ValueError("selection evidence cannot be frozen") from None
        self._payload = frozen
        self._digest = hashlib.sha256(canonical.encode()).hexdigest()
        self._sealed = True

    def to_obj(self) -> dict:
        """Return an independent canonical JSON-safe selection snapshot."""
        return _json_obj(self._payload)

    @property
    def digest(self) -> str:
        """Return the deterministic selection-record digest."""
        return self._digest


    @property
    def ledger_digest(self) -> str:
        """Return the exact completed ledger digest bound by this record."""
        return self._payload["ledger_digest"]


    @property
    def best_score(self):
        """Return the best score under the recorded direction."""
        return self._payload["best_score"]


    @property
    def threshold(self):
        """Return the recorded one-standard-error eligibility threshold."""
        return self._payload["threshold"]


    @property
    def selected_candidate(self) -> dict:
        """Return an independent selected-candidate snapshot."""
        return _json_obj(self._payload["selected_candidate"])


class OneStandardErrorSelector:
    """A conservative one-standard-error selection rule over a completed
    :class:`TrialLedger`.

    Plan §2: "Use a conservative one-standard-error simplicity rule rather
    than raw argmax." This class owns the selection SHAPE only: find the
    best score, widen by ONE caller-supplied per-trial ``se``, then take
    the simplest surviving candidate. It never invents the statistical
    UNIT behind ``se`` — that is a locked-open owner decision (plan
    §11.1) this class refuses to guess; the caller supplies ``se`` in
    whatever consistent unit the owner eventually rules. Scores and standard
    errors are finite nonnegative builtin ``int`` or ``float`` values; ledger
    admission canonicalizes negative zero to positive ``0.0``. Ties in simplicity
    go to the first candidate in canonical inventory order.

    Parameters
    ----------
    select : str
        ``"min"`` or ``"max"`` — which score direction is "better."
    simplicity_key : callable
        ``row -> orderable value``; the LOWEST value wins among eligible
        rows (e.g. ``lambda row: row["overrides"]["num_leaves"]``).
    Examples
    --------
    selector = OneStandardErrorSelector(
        select="min", simplicity_key=lambda row: row["overrides"]["depth"]
    )
    """


    __slots__ = ("_select", "_simplicity_key")

    def __init__(self, *, select, simplicity_key):
        if type(select) is not str or select not in ("min", "max"):
            raise ValueError("select must be the exact string 'min' or 'max'")
        if not callable(simplicity_key):
            raise ValueError("simplicity_key must be callable")
        self._select = select
        self._simplicity_key = simplicity_key

    def select(self, ledger: TrialLedger) -> SelectionRecord:
        if type(ledger) is not TrialLedger:
            raise TypeError("ledger must be an exact TrialLedger")
        if not ledger.is_complete():
            raise ValueError(
                f"cannot select from an incomplete ledger "
                f"({len(ledger.rows)}/{ledger.expected_count} trials recorded)"
            )
        def require_finite(value, label, overrides):
            """Return a stored finite builtin number or name the bad trial."""
            if type(value) not in (int, float) or (
                type(value) is float and not math.isfinite(value)
            ):
                raise ValueError(
                    f"trial {overrides!r} reported an invalid {label} "
                    f"({value!r}) — cannot select"
                )
            return value

        def compare(a, b, op, context):
            # require_finite only proves score/se are each INDIVIDUALLY
            # finite-ish (duck-typed via math.isfinite, which
            # decimal.Decimal satisfies fine) — never that two rows'
            # values are mutually COMPARABLE. decimal.Decimal < float
            # (and Decimal + float, below) raises TypeError even though
            # each alone passes require_finite fine; every comparison in
            # this method must refuse by name on failure like everything
            # else here (skeptic review round 17, Critical).
            try:
                if op == "<":
                    return bool(a < b)
                if op == ">":
                    return bool(a > b)
                if op == "<=":
                    return bool(a <= b)
                return bool(a >= b)
            except Exception:
                # Catch broadly, not a (TypeError, ValueError) allowlist:
                # a custom __lt__/__gt__/__le__/__ge__ can raise ANYTHING
                # (round 18, Major reopened exactly the class of bug
                # round 10 already fixed in require_finite for a hostile
                # __float__ — the same broad catch belongs here too).
                raise ValueError(
                    f"{context}: cannot compare stored values"
                ) from None

        def combine(a, b, op, context):
            try:
                return a + b if op == "+" else a - b
            except Exception:
                raise ValueError(
                    f"{context}: cannot compute stored values"
                ) from None

        rows = ledger.rows
        for row in rows:
            if "se" not in row:
                raise ValueError(
                    f"trial {row['overrides']!r} has no 'se' field — "
                    "OneStandardErrorSelector requires every row to carry "
                    "its own standard error"
                )
            require_finite(row["score"], "score", row["overrides"])
            se = require_finite(row["se"], "se", row["overrides"])
            if compare(se, 0, "<", f"trial {row['overrides']!r} se"):
                raise ValueError(
                    f"trial {row['overrides']!r} reported a negative se "
                    f"({se!r}) — a standard error cannot be negative"
                )

        best = rows[0]
        for row in rows[1:]:  # strict compare: ties go to the FIRST recorded
            op = "<" if self._select == "min" else ">"
            if compare(row["score"], best["score"], op, "comparing scores"):
                best = row
        if self._select == "min":
            threshold = combine(
                best["score"], best["se"], "+", "computing the eligibility threshold"
            )
        else:
            threshold = combine(
                best["score"], best["se"], "-", "computing the eligibility threshold"
            )
        require_finite(threshold, "eligibility threshold", best["overrides"])
        op = "<=" if self._select == "min" else ">="
        eligible = [
            r for r in rows if compare(r["score"], threshold, op, "checking eligibility")
        ]

        if not eligible:
            raise ValueError(
                f"trial {best['overrides']!r} is not eligible under its own "
                "one-standard-error threshold"
            )

        def simplicity(row):
            try:
                key = self._simplicity_key(row)
            except Exception:
                raise ValueError(
                    f"simplicity_key raised on trial {row['overrides']!r}"
                ) from None
            try:
                return _simplicity_value(key)
            except Exception:
                raise ValueError(
                    f"simplicity_key returned an invalid key for trial "
                    f"{row['overrides']!r}"
                ) from None

        all_keyed_rows = [(row, simplicity(row)) for row in rows]
        eligible_ids = {id(row) for row in eligible}
        keyed_rows = [
            (row, value) for row, value in all_keyed_rows
            if id(row) in eligible_ids
        ]
        winner, winner_key = keyed_rows[0]
        for row, row_key in keyed_rows[1:]:  # canonical order breaks ties
            try:
                # bool(...) forces the truth-value check to happen INSIDE
                # this try block. A multi-element numpy array's `<`
                # doesn't raise at the comparison itself — it returns an
                # elementwise boolean array — so without the explicit
                # bool() the crash would happen later, unguarded, at
                # `if is_simpler:`, as numpy's own opaque "truth value of
                # an array is ambiguous" ValueError instead of this
                # class's named, trial-naming refusal (skeptic review
                # round 16, Major).
                is_simpler = bool(row_key < winner_key)
            except Exception:
                # simplicity_key returning mutually-incomparable types
                # across rows must refuse by name without rendering a
                # caller-controlled comparison failure.
                raise ValueError(
                    f"simplicity_key returned incomparable values for "
                    f"trials {winner['overrides']!r} and {row['overrides']!r}"
                ) from None
            if is_simpler:
                winner, winner_key = row, row_key
        return SelectionRecord(
            {
                "inventory_digest": ledger.inventory_digest,
                "ledger_digest": ledger.digest,
                "direction": self._select,
                "best_score": best["score"],
                "threshold": threshold,
                "eligible_candidates": [
                    _json_obj(row["overrides"]) for row in eligible
                ],
                "simplicity_order": [
                    {"candidate": _json_obj(row["overrides"]), "key": key}
                    for row, key in all_keyed_rows
                ],
                "selected_candidate": _json_obj(winner["overrides"]),
            }
        )


class HpoGrid(Node):
    """Exhaustive (or subsampled) grid search over document params
    (role ``search``, registered kind ``hpo-grid``).

    Params: ``space`` (required — ``"node.param.path"`` -> non-empty
    value list), ``objective`` (required — a ``$``-reference into a
    val-split score node's output), ``select`` (``min``/``max``, default
    ``min``), ``n_trials`` (optional int >= 1: subsample bar; absent =
    exhaustive), ``seeds`` (optional non-empty int list: per-trial seed
    ensemble), ``seed`` (optional int >= 0, default 0: the subsample
    rank seed). Outputs: ``best_params`` (the winning overrides),
    ``best_score`` (its mean score), ``trials`` (every evaluated trial as
    ``{"overrides", "score"[, "seed_scores"]}``).

    A trial reporting a NON-FINITE objective (NaN/±inf) fails the search
    loudly, named by its overrides (:func:`_finite_objective`) — the
    strict-compare winner loop would otherwise let a first-trial NaN
    stick as "best" and hand the driver its overrides as the winner.
    """

    role = "search"
    outputs = ("best_params", "best_score", "trials")

    #: The class's own knobs — anything else is refused by name. Denying
    #: KEYS only: ``objective``'s VALUE is a ``$``-reference string by
    #: design, and stays legal.
    _PARAMS = ("n_trials", "objective", "seed", "seeds", "select", "space")

    @classmethod
    def validate_params(cls, params):
        """Return stable configuration problems for one hpo-grid request."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        space = params.get("space")
        if not isinstance(space, dict) or not space:
            problems.append(
                "space must be a non-empty dict of 'node.param.path' -> "
                "non-empty value list"
            )
            entries = ()
        else:
            try:
                entries = tuple(space.items())
            except Exception:
                problems.append("space could not be snapshotted safely")
                entries = ()
        for index, entry in enumerate(entries):
            try:
                target, grid = entry
            except Exception:
                problems.append(f"space entry {index} must be a name/value pair")
                continue
            if type(target) is not str or not re.match(_SPACE_KEY_OK, target):
                problems.append(
                    "space key must be '<node>.<param.path>' (e.g. 'train.lr')"
                )
                continue
            if not isinstance(grid, (list, tuple)):
                problems.append(
                    f"space[{target!r}] must be a non-empty list of JSON scalars"
                )
                continue
            try:
                values = tuple(value for value in grid)
            except Exception:
                problems.append(f"space[{target!r}] values could not be snapshotted safely")
                continue
            if not values:
                problems.append(
                    f"space[{target!r}] must be a non-empty list of JSON scalars"
                )
            elif any(not _is_json_scalar(value) for value in values):
                problems.append(
                    f"space[{target!r}] values must be JSON scalars "
                    "(null/bool/number/string)"
                )
        if "objective" not in params:
            problems.append(
                "objective is required (a $-reference to a score node's "
                "val-split output, e.g. '$validate.metrics.loss')"
            )
        elif type(params["objective"]) is str:
            try:
                parse_node_ref(params["objective"])
            except ConfigError:
                problems.append("objective must be a '$node.path' reference")
        select = params.get("select", "min")
        if type(select) is not str or select not in ("min", "max"):
            problems.append("select must be 'min' or 'max'")
        n_trials = params.get("n_trials")
        if type(n_trials) is int and not _json_int_ok(n_trials):
            problems.append("n_trials integers must have at most 4096 decimal digits")
        elif n_trials is not None and (type(n_trials) is not int or n_trials < 1):
            problems.append("n_trials must be an int >= 1")
        seed = params.get("seed", 0)
        if type(seed) is int and not _json_int_ok(seed):
            problems.append("seed integers must have at most 4096 decimal digits")
        elif type(seed) is not int or seed < 0:
            problems.append("seed must be an int >= 0")
        seeds = params.get("seeds")
        if seeds is not None and not isinstance(seeds, (list, tuple)):
            problems.append("seeds must be a non-empty list of builtin ints")
        elif seeds is not None:
            try:
                seed_values = tuple(item for item in seeds)
            except Exception:
                problems.append("seeds could not be snapshotted safely")
            else:
                if not seed_values or any(type(item) is not int for item in seed_values):
                    problems.append("seeds must be a non-empty list of builtin ints")
                elif any(not _json_int_ok(item) for item in seed_values):
                    problems.append("seeds integers must have at most 4096 decimal digits")
        return problems

    def run(self, ctx, inputs):
        if ctx.rerun is None:
            raise RuntimeError("hpo-grid runs under the driver")
        rerun = ctx.rerun
        space = self.params["space"]
        select = self.params.get("select", "min")
        seeds = self.params.get("seeds")
        trials = _grid(space)
        grid_size = len(trials)
        n_trials = self.params.get("n_trials")
        if n_trials is not None and n_trials < grid_size:
            trials = _subsample(trials, n_trials, self.params.get("seed", 0))
        seed_targets = tuple(getattr(rerun, "seed_targets", ()) or ())
        results = []
        for overrides in trials:
            if seeds:
                seed_scores = []
                for s in seeds:
                    trial = dict(overrides)
                    for target in seed_targets:
                        trial[target] = s
                    # Refused per REPORT, naming the exact rerun call (grid
                    # overrides plus the seed override) — the seed is part
                    # of what failed.
                    seed_scores.append(_finite_objective(self.key, trial, rerun(trial)))
                results.append(
                    {
                        "overrides": overrides,
                        "score": sum(seed_scores) / len(seed_scores),
                        "seed_scores": seed_scores,
                    }
                )
            else:
                results.append(
                    {
                        "overrides": overrides,
                        "score": _finite_objective(
                            self.key, overrides, rerun(dict(overrides))
                        ),
                    }
                )
        best = None
        for trial in results:  # strict compare: ties go to the FIRST enumerated
            if (
                best is None
                or (select == "min" and trial["score"] < best["score"])
                or (select == "max" and trial["score"] > best["score"])
            ):
                best = trial
        self.log.info(
            "hpo-grid: evaluated %d/%d trial(s)%s — best score %s at %s",
            len(results),
            grid_size,
            f" x {len(seeds)} seed(s)" if seeds else "",
            best["score"],
            best["overrides"],
        )
        return {
            "best_params": dict(best["overrides"]),
            "best_score": best["score"],
            "trials": results,
        }


class TopTrials(Node):
    """Pick an ensemble pool from a search ledger (kind ``top-trials``).

    Rank ``trials`` by score, keep the top ``frac``, sample ``size``
    members with replacement, assign distinct seeds (ADR-0052).

    Parameters
    ----------
    params : dict
        ``frac`` (float in (0, 1]), ``size`` (int >= 1), ``seed``
        (int >= 0), optional ``select`` (``min``/``max``, default
        ``min``).

    Examples
    --------
    Draw 5 members from the top tenth::

        node = TopTrials("ensemble", {"frac": 0.1, "size": 5, "seed": 1})
        node.params["size"]  # 5
    """

    role = "transform"
    outputs = ("members", "metrics")
    _PARAMS = ("frac", "seed", "select", "size")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        frac = params.get("frac")
        if (
            isinstance(frac, bool)
            or not isinstance(frac, (int, float))
            or not (0.0 < float(frac) <= 1.0)
        ):
            problems.append(f"frac must be in (0, 1], got {frac!r}")
        size = params.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            problems.append(f"size must be an int >= 1, got {size!r}")
        seed = params.get("seed")
        if type(seed) is int and not _json_int_ok(seed):
            problems.append("seed integers must have at most 4096 decimal digits")
        elif type(seed) is not int or seed < 0:
            problems.append("seed must be an int >= 0")
        if params.get("select", "min") not in ("min", "max"):
            problems.append(
                f"select must be 'min' or 'max', got {params.get('select')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require a list of ``{overrides, score}`` trial rows."""
        trials = inputs.get("trials")
        if not isinstance(trials, list) or not trials:
            return [f"trials must be a non-empty list, got {trials!r}"]
        problems = []
        for i, row in enumerate(trials):
            if not isinstance(row, dict) or "score" not in row:
                problems.append(f"trials[{i}] must be an object with score")
        return problems

    def run(self, ctx, inputs):
        """Rank, keep the top fraction, sample members.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused.
        inputs : dict
            ``trials`` from a search node.

        Returns
        -------
        dict
            ``members`` and ``metrics``.
        """
        trials = list(inputs["trials"])
        select = self.params.get("select", "min")
        reverse = select == "max"
        ranked = sorted(trials, key=lambda row: row["score"], reverse=reverse)
        keep = max(1, math.ceil(float(self.params["frac"]) * len(ranked)))
        pool = ranked[:keep]
        size = int(self.params["size"])
        seed = int(self.params["seed"])
        members = []
        for i in range(size):
            digest = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
            picked = pool[int(digest, 16) % len(pool)]
            members.append({
                "overrides": dict(picked.get("overrides") or {}),
                "score": picked["score"],
                "seed": seed + i,
            })
        self.log.info(
            "top-trials: %d of %d trials -> %d members",
            keep, len(ranked), size,
        )
        return {
            "members": members,
            "metrics": {
                "n_trials": float(len(ranked)),
                "n_pool": float(keep),
                "n_members": float(size),
            },
        }


def register(registry=None) -> None:
    """Register ``hpo-grid`` and ``top-trials`` into ``registry``."""
    target = DEFAULT_NODE_KINDS if registry is None else registry
    if "hpo-grid" not in target:
        target.register("hpo-grid", HpoGrid, owned=False)
    if "top-trials" not in target:
        target.register("top-trials", TopTrials, owned=False)
