"""The serve document: the §4.1 grammar as a table, default-deny at every level, one identity.

A serve document declares the whole process, so a typo anywhere in it must
be an error and never a silent default. Rather than a class per section
(forty of them, each restating the same three checks), the grammar is a
TABLE of small shape objects — :class:`_Fixed` for an object with known
keys, :class:`_Named` for a map of author-chosen names, :class:`_ListOf`,
the scalar shapes, and :class:`_Opaque` for a ``params`` block — walked
once by :class:`ServeDocument`. The walk validates and builds the read
views in the same pass, accumulating every problem into ONE
:class:`~dskit.production.base.ProductionError` whose text names each
offending key. ``notes`` is legal on every object and never graded.

Two copies of the document exist on purpose. The raw deep copy is what
``to_obj()`` hands back (fresh each call, so the round trip is exact and
an absent optional key is never materialised — a rendered default would
move the identity of every document that omits it) and what ``doc_hash``
grades. The views are what code reads: fixed keys by attribute
(``doc.schedule.max_staleness_ms``), author-named maps as read-only
mappings (``doc.guards["size"].uses``), lists as tuples, a ``params`` block
as a read-only mapping the seam class's own ``validate_params`` judges, and
an absent optional key as ``None`` — the owning module's named default
then applies (``cadence.DEFAULT_OVERRUN_POLICY``, never a literal here).
``coordination.scope`` IS an :class:`~dskit.production.records.ExecutionScope`,
so compose hands it to the manifest unchanged.

``params`` blocks are the one place default-deny stops: the document
cannot know a family it must not import. The exception is D9, which reads
``guards.*.params.measure`` and ``params.window`` because a document whose
executor can reach a venue must declare a per-proposal size limit, a
period loss limit and a non-``paper`` accounting strategy, and §5.6 adds a
child approval verifier. Those rules and §5.8's "``fsync: none`` only at
``shadow``" are rung-dependent, and D2 forbids a rung branch outside
``compose.py``: they live in :data:`_RUNG_RULES`, a table keyed by rung
and pinned to ``vocab.RUNGS`` at import.

Identity is the pipeline recipe unchanged (D24): ``doc_hash =
config_hash(document, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)``. The
grammar is partitioned to suit that recipe — it drops WHOLE top-level
sections — which is why ``alert_endpoints``, ``heartbeat``, ``placement``
and ``env`` are top level and everything else is graded, ``name`` included.

Later modules import from here rather than restating: the bounds the
document owns (:data:`SHUTDOWN_GRACE_S_BOUNDS`, :data:`GROUP_WAIT_S_BOUNDS`,
:data:`REPEAT_INTERVAL_S_BOUNDS`, :data:`MIN_HEARTBEAT_EVERY_S`,
:data:`MIN_VALID_FOR_S`, :data:`RENEWAL_BUDGET_FACTOR`), the key sets of
the structured non-``params`` objects (:data:`OVERRUN_KEYS`,
:data:`RETRY_KEYS`, :data:`RETRY_BUDGET_KEYS`, :data:`BREAKER_KEYS`,
:data:`LIMITER_LANE_KEYS`, :data:`RATE_LIMIT_KEYS`, :data:`ROTATE_KEYS`,
:data:`FSYNC_BATCH_KEYS`), :func:`check_fsync`, and the D9 names.

Import cost: stdlib plus ``dskit.pipeline.base`` (``config_hash``),
``dskit.pipeline.node`` (``check_int_param``), ``dskit.production.base``,
``records``, ``release`` (``parse_iso_duration``) and ``vocab``.
"""

import copy
import json
import math
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType

from dskit.pipeline.base import config_hash
from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
)
from dskit.production.records import ExecutionScope
from dskit.production.release import parse_iso_duration
from dskit.production.vocab import (
    ESCALATION_LEVELS,
    FSYNC_MODES,
    JITTER_MODES,
    ON_MISMATCH,
    OVERRUN_POLICIES,
    PROBE_SCOPES,
    RETRY_AFTER_MODES,
    RETRY_WRITE_MODES,
    ROTATE_BY,
    RUNGS,
    SEVERITIES,
)

__all__ = [
    "BREAKER_KEYS",
    "DENY_ALL_APPROVAL_KIND",
    "FSYNC_BATCH_KEYS",
    "FSYNC_NONE",
    "GRADED_SECTIONS",
    "GROUP_WAIT_S_BOUNDS",
    "LIMITER_LANE_KEYS",
    "LOSS_MEASURE",
    "MAX_ACK_S_BOUNDS",
    "MAX_OUTCOME_COVERAGE",
    "MAX_SILENCE_S_BOUNDS",
    "MIN_HEARTBEAT_EVERY_S",
    "MIN_REPORT_BINS",
    "MIN_VALID_FOR_S",
    "OVERRUN_KEYS",
    "PAPER_ACCOUNTING_KIND",
    "PRODUCTION_NON_IDENTITY_SECTIONS",
    "RATE_LIMIT_KEYS",
    "RENEWAL_BUDGET_FACTOR",
    "REPEAT_INTERVAL_S_BOUNDS",
    "RETRY_BUDGET_KEYS",
    "RETRY_KEYS",
    "ROTATE_KEYS",
    "SHUTDOWN_GRACE_S_BOUNDS",
    "SIZE_MEASURES",
    "ServeDocument",
    "check_fsync",
]

#: The top-level sections ``config_hash`` drops (§4.2, D24): where alerts
#: go, how the process heartbeats, where storage lives, where credentials
#: come from. Everything else the grammar declares is graded.
PRODUCTION_NON_IDENTITY_SECTIONS = ("alert_endpoints", "heartbeat", "placement", "env")

#: The pipeline's documentation key, legal on every object, never graded.
_NOTES = "notes"

# --- bounds the document owns (§5.7.2, §5.11) — read by the run alike ----
#: ``lifecycle.shutdown_grace_s`` must sit under the supervisor's grace.
SHUTDOWN_GRACE_S_BOUNDS = (1, 300)
#: ``alerting.group_wait_s``.
GROUP_WAIT_S_BOUNDS = (0, 600)
#: ``alerting.repeat_interval_s``.
REPEAT_INTERVAL_S_BOUNDS = (60, 86400)
#: ``alerting.max_silence_s`` and ``alerting.max_ack_s`` (§5.11.2): the
#: longest suppression an operator may ask for. A minute is the shortest
#: window worth writing down; a week is the longest an unattended
#: suppression may outlive the shift that created it, and an UNBOUNDED one
#: is how a page is lost forever, which is why neither knob may be absent
#: from the code even when it is absent from the document.
MAX_SILENCE_S_BOUNDS = (60, 604800)
MAX_ACK_S_BOUNDS = (60, 604800)
#: ``heartbeat.every_s``: at least one second.
MIN_HEARTBEAT_EVERY_S = 1
#: ``readiness.valid_for_s``: a GO that expires immediately is no GO.
MIN_VALID_FOR_S = 1
#: ``reporting.bins`` (§5.13.3): the equal-width partition ECE is taken
#: over. One bin is no partition at all — every forecast lands in it and
#: the error is identically zero — so two is the smallest that measures
#: anything.
MIN_REPORT_BINS = 2
#: ``readiness.min_outcome_coverage`` (§5.13.4): a FRACTION of the decided
#: legs in the window, so 1 is "every one" and there is nothing above it.
MAX_OUTCOME_COVERAGE = 1
#: ``coordination.ttl_ms > RENEWAL_BUDGET_FACTOR * (renew_every_ms +
#: renew_timeout_ms)`` — a missed renewal deadline can never be mistaken
#: for a still-valid permit.
RENEWAL_BUDGET_FACTOR = 2

# --- the core kind names D9 and §5.6 reason about ------------------------
#: The two ``MEASURE_KINDS`` a per-proposal size limit measures.
SIZE_MEASURES = ("quantity", "notional")
#: The measure a period loss limit is over.
LOSS_MEASURE = "pnl"
#: The accounting strategy that cannot back a live-capable document.
PAPER_ACCOUNTING_KIND = "paper"
#: The shadow/paper approval default a live-capable document may not keep.
DENY_ALL_APPROVAL_KIND = "deny-all"
#: The ``durability.fsync`` grade legal only where the rung table says so.
FSYNC_NONE = "none"


def _pinned(table, vocabulary, what):
    """Return ``table`` after refusing a key set that disagrees with ``vocabulary``."""
    if set(table) != set(vocabulary):
        raise ProductionError(
            [f"document.py's {what} table keys {sorted(table)} != vocab {sorted(vocabulary)}"]
        )
    return table


def _join(where, name):
    """Spell a nested path."""
    return f"{where}.{name}" if where else name


def _numeric(value):
    """Say whether ``value`` is a number the int checks accepted (bool is not one)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# The read views
# ---------------------------------------------------------------------------


class _Section:
    """Read-only attribute view of one fixed-key object; an absent optional key reads as None."""

    __slots__ = ("_values",)

    def __init__(self, values):
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name):
        """Read a declared key; anything else is an AttributeError."""
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(f"no key {name!r} in this section") from None

    def __setattr__(self, name, value):
        """Refuse: a document section is read-only."""
        raise AttributeError("a serve document section is read-only")

    def __repr__(self):
        """Name the keys that are declared."""
        declared = sorted(k for k, v in self._values.items() if v is not None)
        return f"Section({declared})"


# ---------------------------------------------------------------------------
# The shapes — one grammar node each
# ---------------------------------------------------------------------------


class _Shape(ABC):
    """One node of the grammar: validate a value at a path and return its read view."""

    @abstractmethod
    def check(self, problems, where, value):
        """Append every problem with ``value`` at ``where``; return the view (None if malformed)."""


class _Str(_Shape):
    """A non-empty string."""

    def check(self, problems, where, value):
        """Refuse anything but a non-empty string."""
        _check_str(problems, where, value)
        return value


class _Bool(_Shape):
    """A JSON boolean."""

    def check(self, problems, where, value):
        """Refuse anything but ``true``/``false`` — ``"true"`` is a string."""
        if not isinstance(value, bool):
            problems.append(f"{where} must be true or false, got {value!r}")
        return value


class _Int(_Shape):
    """An int within ``[ge, le]`` (``le`` optional); bool refused."""

    def __init__(self, ge, le=None):
        self.ge, self.le = ge, le

    def check(self, problems, where, value):
        """Apply the pipeline's int check, then the upper bound."""
        check_int_param(problems, where, value, ge=self.ge)
        if self.le is not None and _numeric(value) and value > self.le:
            problems.append(f"{where} must be an int <= {self.le}, got {value!r}")
        return value


class _Number(_Shape):
    """A finite int or float, strictly above ``gt`` and at most ``le`` when given."""

    def __init__(self, gt=None, le=None):
        self.gt = gt
        self.le = le

    def check(self, problems, where, value):
        """Refuse NaN/Infinity here, so no refusal a caller sees comes from the hash."""
        if not _numeric(value) or not math.isfinite(value):
            problems.append(f"{where} must be a finite number, got {value!r}")
        elif self.gt is not None and value <= self.gt:
            problems.append(f"{where} must be > {self.gt}, got {value!r}")
        elif self.le is not None and value > self.le:
            problems.append(f"{where} must be <= {self.le}, got {value!r}")
        return value


class _Choice(_Shape):
    """A member of a closed vocabulary from ``vocab.py``."""

    def __init__(self, members):
        self.members = members

    def check(self, problems, where, value):
        """Refuse a non-member, naming the members."""
        if value not in self.members:
            problems.append(f"{where} must be one of {list(self.members)}, got {value!r}")
        return value


class _Uuid(_Shape):
    """A canonical (lowercase, hyphenated) UUID string — it names a directory."""

    def check(self, problems, where, value):
        """Refuse a malformed UUID or a non-canonical spelling of one."""
        _check_str(problems, where, value)
        if isinstance(value, str) and value and _canonical_uuid(value) != value:
            problems.append(f"{where} must be a canonical lowercase hyphenated UUID, got {value!r}")
        return value


def _canonical_uuid(text):
    """Spell ``text`` as a canonical UUID, or return None when it is not one."""
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


class _Duration(_Shape):
    """An ISO-8601 day/time duration, kept as its string; ``release.parse_iso_duration`` judges it."""

    def check(self, problems, where, value):
        """Refuse what ``parse_iso_duration`` refuses, under this path."""
        try:
            parse_iso_duration(value)
        except ProductionError as exc:
            problems.extend(f"{where}: {p}" for p in exc.problems)
        return value


class _Opaque(_Shape):
    """A ``params`` block: any plain-JSON object, judged by the seam class, not here."""

    def check(self, problems, where, value):
        """Require a JSON object with finite numbers; keep it verbatim, read-only."""
        _check_dict(problems, where, value)
        if not isinstance(value, dict):
            return None
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            problems.append(f"{where} is not plain JSON: {exc}")
        return MappingProxyType(copy.deepcopy(value))


class _ListOf(_Shape):
    """A JSON list of ``item``-shaped values, viewed as a tuple."""

    def __init__(self, item, min_len=0):
        self.item, self.min_len = item, min_len

    def check(self, problems, where, value):
        """Refuse a non-list or a list shorter than ``min_len``; check every item."""
        if not isinstance(value, list):
            problems.append(f"{where} must be a list, got {value!r}")
            return None
        if len(value) < self.min_len:
            problems.append(f"{where} needs at least {self.min_len} item(s), got {value!r}")
        return tuple(self.item.check(problems, f"{where}[{i}]", v) for i, v in enumerate(value))


class _Nullable(_Shape):
    """``inner``, or an explicit ``null`` — declared-null and absent are different documents."""

    def __init__(self, inner):
        self.inner = inner

    def check(self, problems, where, value):
        """Pass ``null`` through; otherwise defer to ``inner``."""
        return None if value is None else self.inner.check(problems, where, value)


class _Universe(_Shape):
    """``serving.required_universe``: a path to a JSON key list, or the list inline (§5.2)."""

    def check(self, problems, where, value):
        """Accept a non-empty path string or a list of key strings."""
        if isinstance(value, list):
            return _KEY_LIST.check(problems, where, value)
        if not isinstance(value, str) or not value:
            problems.append(
                f"{where} must be a path to a JSON key list or an inline list of keys, got {value!r}"
            )
        return value


class _Record(_Shape):
    """A value object from ``records.py``, built by its own default-deny ``from_obj``."""

    def __init__(self, record):
        self.record = record

    def check(self, problems, where, value):
        """Strip ``notes`` and let the record validate itself, under this path."""
        if not isinstance(value, dict):
            names = [f.name for f in fields(self.record)]
            problems.append(f"{where} must be an object with {names}, got {value!r}")
            return None
        try:
            return self.record.from_obj({k: v for k, v in value.items() if k != _NOTES})
        except ProductionError as exc:
            problems.extend(f"{where}: {p}" for p in exc.problems)
            return None


class _Fixed(_Shape):
    """An object with known keys: default-deny, ``required`` keys present, ``notes`` allowed."""

    def __init__(self, shapes, required=()):
        self.shapes = dict(shapes)
        self.required = frozenset(required)
        undeclared = self.required - set(self.shapes)
        if undeclared:
            raise ProductionError([f"required keys {sorted(undeclared)} are not in the grammar"])

    @property
    def keys(self):
        """The declared keys, in grammar order."""
        return tuple(self.shapes)

    def check(self, problems, where, value):
        """Refuse unknown or missing keys; check every declared one; build the section view."""
        if not isinstance(value, dict):
            problems.append(f"{where or 'the document'} must be an object (dict), got {value!r}")
            return None
        _check_unknown(problems, value, self.keys + (_NOTES,), where=where or "document")
        if _NOTES in value and not isinstance(value[_NOTES], str):
            problems.append(f"{_join(where, _NOTES)} must be a string")
        views = {_NOTES: value.get(_NOTES)}
        for name, shape in self.shapes.items():
            if name in value:
                views[name] = shape.check(problems, _join(where, name), value[name])
            else:
                views[name] = None
                if name in self.required:
                    problems.append(f"{_join(where, name)} is required")
        return _Section(views)


class _Named(_Shape):
    """A map of author-chosen names to ``entry``-shaped objects (guards, monitors, sinks, …)."""

    def __init__(self, entry):
        self.entry = entry

    def check(self, problems, where, value):
        """Check every entry under its name; a ``notes`` string is documentation, not an entry."""
        if not isinstance(value, dict):
            problems.append(f"{where} must be a mapping of names to objects, got {value!r}")
            return None
        views = {}
        for name, entry in value.items():
            if name == _NOTES and isinstance(entry, str):
                continue
            views[name] = self.entry.check(problems, _join(where, name), entry)
        return MappingProxyType(views)


class _Fsync(_Shape):
    """``durability.fsync`` — see :func:`check_fsync`."""

    def check(self, problems, where, value):
        """Validate the grade grammar; view a knob object read-only."""
        check_fsync(problems, value, where)
        return MappingProxyType(copy.deepcopy(value)) if isinstance(value, dict) else value


# ---------------------------------------------------------------------------
# The grammar (§4.1)
# ---------------------------------------------------------------------------

_STR, _BOOL, _OPAQUE = _Str(), _Bool(), _Opaque()
_POSITIVE_INT = _Int(ge=1)
_COUNT = _Int(ge=0)
_POSITIVE = _Number(gt=0)
_KEY_LIST = _ListOf(_STR)

#: Every ``uses`` site: a registered kind or a ``pkg.module:Class`` reference, plus its knobs.
_SELECTOR = _Fixed({"uses": _STR, "params": _OPAQUE}, required=("uses",))

_SERVING = _Fixed(
    {
        "run_dir": _STR,
        "adapter": _STR,
        "entry": _Fixed(
            {"node": _STR, "param": _STR, "window_ms": _POSITIVE_INT},
            required=("node", "param", "window_ms"),
        ),
        "heads": _ListOf(_STR, min_len=1),
        "required_universe": _Universe(),
        "proposer": _SELECTOR,
        "replay": _Named(_STR),
        "max_artifact_age": _Duration(),
    },
    required=("run_dir", "adapter", "entry", "heads", "required_universe", "proposer"),
)
_OVERRUN = _Fixed({"policy": _Choice(OVERRUN_POLICIES), "max_lag_ms": _COUNT})
_SCHEDULE = _Fixed(
    {
        "clock": _SELECTOR,
        "calendar": _SELECTOR,
        "cadence": _SELECTOR,
        "overrun": _OVERRUN,
        "dead_after_ms": _POSITIVE_INT,
        "max_staleness_ms": _POSITIVE_INT,
        "max_quote_age_ms": _POSITIVE_INT,
        "max_venue_skew_ms": _Nullable(_COUNT),
    },
    required=(
        "clock",
        "calendar",
        "cadence",
        "dead_after_ms",
        "max_staleness_ms",
        "max_quote_age_ms",
    ),
)
_EXECUTION = _Fixed(
    {
        "uses": _STR,
        "params": _OPAQUE,
        "submit_timeout_ms": _POSITIVE_INT,
        "on_halt": _Fixed({"cancel_open": _BOOL}, required=("cancel_open",)),
        # [phase 2] §5.12.1's request signer. OPTIONAL inside a section
        # that is already graded, so a document that does not sign hashes
        # exactly as it does today; declaring one changes what leaves the
        # process, so it changes identity. A child's `LiveExecutor` calls
        # it — core never does, because core ships no venue.
        "signer": _SELECTOR,
    },
    required=("uses", "submit_timeout_ms"),
)
_ACCOUNTING = _Fixed(
    {"uses": _STR, "params": _OPAQUE, "max_valuation_age_ms": _POSITIVE_INT},
    required=("uses", "max_valuation_age_ms"),
)
_ARMING = _Fixed(
    {"max_duration_s": _POSITIVE_INT, "approval": _SELECTOR},
    required=("max_duration_s", "approval"),
)
_COORDINATION = _Fixed(
    {
        "scope": _Record(ExecutionScope),
        "lease": _SELECTOR,
        "ttl_ms": _POSITIVE_INT,
        "renew_every_ms": _POSITIVE_INT,
        "renew_timeout_ms": _POSITIVE_INT,
    },
    required=("scope", "lease", "ttl_ms", "renew_every_ms", "renew_timeout_ms"),
)
_RECONCILE = _Fixed(
    {
        "on_start": _BOOL,
        "every_s": _POSITIVE_INT,
        "on_mismatch": _Choice(ON_MISMATCH),
        "lookback_ms": _POSITIVE_INT,
    },
    required=("on_start", "every_s", "on_mismatch", "lookback_ms"),
)
_PROBE = _Fixed(
    {"uses": _STR, "params": _OPAQUE, "scope": _Choice(PROBE_SCOPES), "timeout_s": _POSITIVE},
    required=("uses",),
)
_HEALTH = _Fixed(
    {
        "failure_threshold": _POSITIVE_INT,
        "success_threshold": _POSITIVE_INT,
        "timeout_s": _POSITIVE,
        "probes": _Named(_PROBE),
    },
    required=("failure_threshold", "success_threshold", "timeout_s", "probes"),
)
#: [phase 2] §5.8.2's store selector beside the grade. `fsync` says how
#: often the writer commits; `ledger` says WHERE the chain lives, and is
#: OPTIONAL so every phase-1 document keeps both its identity and its
#: existing `jsonl` chain.
_DURABILITY = _Fixed({"fsync": _Fsync(), "ledger": _SELECTOR}, required=("fsync",))
_RETRY_BUDGET = _Fixed(
    {"capacity": _COUNT, "transient_cost": _COUNT, "throttle_cost": _COUNT, "refund": _COUNT}
)
_RETRY = _Fixed(
    {
        "max_attempts": _POSITIVE_INT,
        "base_s": _POSITIVE,
        "throttle_base_s": _POSITIVE,
        "cap_s": _POSITIVE,
        "jitter": _Choice(JITTER_MODES),
        "retry_after": _Choice(RETRY_AFTER_MODES),
        "retry_writes": _Choice(RETRY_WRITE_MODES),
        "budget": _RETRY_BUDGET,
    }
)
_BREAKER = _Fixed({"min_calls": _POSITIVE_INT, "failure_rate": _POSITIVE, "open_s": _POSITIVE})
_LANE = _Fixed(
    {"rate_per_s": _POSITIVE, "burst": _POSITIVE_INT, "max_in_flight": _POSITIVE_INT, "reserved": _BOOL}
)
_LIMITER = _Fixed({"submit": _LANE, "cancel": _LANE}, required=("submit", "cancel"))
_RESILIENCE = _Fixed(
    {"retry": _RETRY, "breaker": _BREAKER, "limiter": _LIMITER, "transport": _SELECTOR},
    required=("retry", "breaker", "limiter", "transport"),
)
_LIFECYCLE = _Fixed(
    {"cooling_off_s": _COUNT, "shutdown_grace_s": _Int(*SHUTDOWN_GRACE_S_BOUNDS)},
    required=("cooling_off_s", "shutdown_grace_s"),
)
#: [phase 2] §5.13.4's four knobs are OPTIONAL and GRADED: absent, they
#: are absent from the hash material, so a phase-1 document keeps its
#: identity; present, they change what a GO means and are therefore
#: graded. Each is required only when a checklist item CITES the evidence
#: name that reads it (`readiness.py` refuses by name when one is
#: missing), because §4.1 rules that code holds no threshold and a
#: default minimum coverage would be exactly that.
_READINESS = _Fixed(
    {
        "checklist": _STR,
        "waivers": _KEY_LIST,
        "valid_for_s": _Int(ge=MIN_VALID_FOR_S),
        "outcome_window": _Duration(),
        "min_outcome_coverage": _Number(gt=0, le=MAX_OUTCOME_COVERAGE),
        "max_outcome_age": _Duration(),
        "calibration_monitor": _STR,
    },
    required=("checklist", "waivers", "valid_for_s"),
)
#: [phase 2] §5.13.2's outcome sources: an ordered map of author-chosen
#: names to ``OUTCOME_SOURCE_KINDS`` selectors. OPTIONAL and GRADED (§4.2) —
#: an absent key is absent from the hash material, so a document written
#: against phase 1 keeps its identity, while a document that declares one
#: changes numbers someone acts on and therefore changes identity.
_OUTCOMES = _Fixed({"sources": _Named(_SELECTOR)}, required=("sources",))
#: [phase 2, §5.13.3] The report's four knobs, every one OPTIONAL and
#: each defaulted by ONE named constant in ``report.py`` — §4.1's "code
#: holds no threshold" applies to a report exactly as it does to a guard.
#: ``scoring`` is a registered ``dskit.pipeline.metrics`` NAME rather than
#: a closed choice: the registry is open by design (a child registers its
#: own rule), so the grammar takes a string and ``report.py`` resolves it
#: through the same lookup unit 3 gave the two scored monitors.
_REPORTING = _Fixed(
    {
        "bins": _Int(ge=MIN_REPORT_BINS),
        "markouts_ms": _ListOf(_POSITIVE_INT),
        "markout_tolerance_ms": _COUNT,
        "scoring": _STR,
    }
)
_HEARTBEAT = _Fixed(
    {
        "every_s": _Int(ge=MIN_HEARTBEAT_EVERY_S),
        "in_degraded": _BOOL,
        "emitters": _Named(_SELECTOR),
    },
    required=("every_s", "emitters"),
)
_ROUTE = _Fixed(
    {"severity": _Choice(SEVERITIES), "sinks": _ListOf(_STR, min_len=1)},
    required=("severity", "sinks"),
)
_RATE_LIMIT = _Fixed(
    {"max_per_hour": _POSITIVE_INT, "burst": _POSITIVE_INT}, required=("max_per_hour", "burst")
)
#: [phase 2] §5.11.2's label matchers: label NAME -> the value it must
#: equal. A map rather than a list of expressions, because exact equality
#: is the whole matcher language — a regex here would be a second grammar
#: with its own escaping rules and no test could enumerate what it matches.
_MATCHERS = _Named(_STR)
#: [phase 2] One ``alerting.inhibit`` rule. ``equal`` is optional: a rule
#: with no shared label inhibits on the matchers alone.
_INHIBIT = _Fixed(
    {"source": _MATCHERS, "target": _MATCHERS, "equal": _KEY_LIST},
    required=("source", "target"),
)
#: [phase 2] One rung of ``alerting.escalation``.
_ESCALATION_LEVEL = _Fixed(
    {"after_s": _POSITIVE_INT, "sinks": _ListOf(_STR, min_len=1)},
    required=("after_s", "sinks"),
)
#: [phase 2] The ladder, keyed by ``ESCALATION_LEVELS`` and default-deny
#: over it, so an operator cannot invent a fourth rung whose delivery no
#: test covers (§5.11.2). Every level is optional; a document may declare
#: one rung or all three.
_ESCALATION = _Fixed({level: _ESCALATION_LEVEL for level in ESCALATION_LEVELS})
_ALERTING = _Fixed(
    {
        "sinks": _Named(_SELECTOR),
        "routes": _ListOf(_ROUTE),
        "group_wait_s": _Int(*GROUP_WAIT_S_BOUNDS),
        "repeat_interval_s": _Int(*REPEAT_INTERVAL_S_BOUNDS),
        "rate_limit": _RATE_LIMIT,
        "inhibit": _ListOf(_INHIBIT),
        "escalation": _ESCALATION,
        "max_silence_s": _Int(*MAX_SILENCE_S_BOUNDS),
        "max_ack_s": _Int(*MAX_ACK_S_BOUNDS),
    },
    required=("sinks", "routes"),
)
_ENDPOINT = _Fixed({"url_env": _STR, "template": _STR, "timeout_s": _POSITIVE}, required=("url_env",))
_ROTATE = _Fixed({"by": _Choice(ROTATE_BY), "max_bytes": _POSITIVE_INT})
#: [phase 3] Metric exporters live HERE, not in ``alerting`` (§5.11.3):
#: §4.2 grades alert sinks because emptying them silences a safety
#: control, and a metric is explicitly never an input to a decision, a
#: guard or a record (§5.11.1) — so adding or removing an exporter is a
#: placement change and must not mint a new release.
_PLACEMENT = _Fixed(
    {
        "ledger_root": _STR,
        "rotate": _ROTATE,
        "log_dir": _STR,
        "metric_sinks": _Named(_SELECTOR),
    },
    required=("ledger_root",),
)
_ENV = _Fixed({"env_file": _STR, "require": _KEY_LIST})

#: The document: every top-level key of §4.1, in plan order.
_GRAMMAR = _Fixed(
    {
        "name": _STR,
        "series_id": _Uuid(),
        "rung": _Choice(RUNGS),
        "serving": _SERVING,
        "feed": _SELECTOR,
        "schedule": _SCHEDULE,
        "guards": _Named(_SELECTOR),
        "execution": _EXECUTION,
        "accounting": _ACCOUNTING,
        "arming": _ARMING,
        "coordination": _COORDINATION,
        "reconcile": _RECONCILE,
        "monitors": _Named(_SELECTOR),
        "health": _HEALTH,
        "durability": _DURABILITY,
        "resilience": _RESILIENCE,
        "lifecycle": _LIFECYCLE,
        "readiness": _READINESS,
        "outcomes": _OUTCOMES,
        "reporting": _REPORTING,
        "alerting": _ALERTING,
        "alert_endpoints": _Named(_ENDPOINT),
        "heartbeat": _HEARTBEAT,
        "placement": _PLACEMENT,
        "env": _ENV,
    },
    required=(
        "name",
        "series_id",
        "rung",
        "serving",
        "feed",
        "schedule",
        "guards",
        "execution",
        "accounting",
        "arming",
        "coordination",
        "reconcile",
        "monitors",
        "health",
        "durability",
        "resilience",
        "lifecycle",
        "readiness",
        "alerting",
        "placement",
    ),
)

#: The graded sections (§4.2): the grammar minus the four excluded
#: sections, ``name`` (graded, but not a section) and ``notes``. Eighteen
#: in phase 1, plus phase 2's OPTIONAL ``outcomes`` and ``reporting``.
GRADED_SECTIONS = tuple(
    key for key in _GRAMMAR.keys if key not in PRODUCTION_NON_IDENTITY_SECTIONS + ("name",)
)

#: The key sets of the structured objects that are NOT ``params`` blocks,
#: for the modules that consume them (``cadence.Overrun``, ``resilience``,
#: ``alerts``, ``ledger``) to use as their ``_PARAMS`` — one owner.
OVERRUN_KEYS = _OVERRUN.keys
RETRY_KEYS = _RETRY.keys
RETRY_BUDGET_KEYS = _RETRY_BUDGET.keys
BREAKER_KEYS = _BREAKER.keys
LIMITER_LANE_KEYS = _LANE.keys
RATE_LIMIT_KEYS = _RATE_LIMIT.keys
ROTATE_KEYS = _ROTATE.keys

# ---------------------------------------------------------------------------
# durability.fsync (§5.8)
# ---------------------------------------------------------------------------

_FSYNC_BATCH = _Fixed({"n": _POSITIVE_INT, "ms": _COUNT}, required=("n", "ms"))
_NO_KNOBS = _Fixed({})
#: The knob object each grade takes: only ``batch`` has knobs, and needs both.
_FSYNC_KNOBS = _pinned(
    {"every": _NO_KNOBS, "batch": _FSYNC_BATCH, "none": _NO_KNOBS}, FSYNC_MODES, "fsync"
)
#: ``{"batch": {"n", "ms"}}`` — the knobs a batched fsync must declare.
FSYNC_BATCH_KEYS = _FSYNC_BATCH.keys


def check_fsync(problems, fsync, where="durability.fsync"):
    """Validate a ``durability.fsync`` declaration, accumulating.

    The grammar the ledger reads: a grade name from ``vocab.FSYNC_MODES``,
    or a one-key object ``{grade: knobs}`` whose knobs are default-deny per
    grade — ``batch`` requires ``n`` (int >= 1) and ``ms`` (int >= 0), the
    other grades take none. A bare ``"batch"`` refuses for the missing
    knobs, because a batch with no bound is ``none`` in disguise.

    Parameters
    ----------
    problems : list of str
        The accumulator. Appended to in place.
    fsync : str or dict
        The declared value.
    where : str
        The path used in messages.

    Returns
    -------
    None
        Problems are appended to ``problems``.
    """
    if isinstance(fsync, str):
        grade, knobs = fsync, {}
    elif isinstance(fsync, dict) and len(fsync) == 1:
        ((grade, knobs),) = fsync.items()
    else:
        problems.append(
            f"{where} must be one of {list(FSYNC_MODES)} or "
            f"{{'batch': {{'n', 'ms'}}}}, got {fsync!r}"
        )
        return
    if grade not in FSYNC_MODES:
        problems.append(f"{where} grade must be one of {list(FSYNC_MODES)}, got {grade!r}")
        return
    _FSYNC_KNOBS[grade].check(problems, f"{where}.{grade}", knobs)


def _fsync_grade(fsync):
    """Name the grade a validated ``fsync`` view declares."""
    if isinstance(fsync, str):
        return fsync
    return next(iter(fsync), None) if isinstance(fsync, Mapping) else None


# ---------------------------------------------------------------------------
# The rung table (D9, §5.6, §5.8) — the rung is read, never compared
# ---------------------------------------------------------------------------


def _refuse_unsynced(problems, view):
    """Refuse ``fsync: none`` for a rung whose row forbids it."""
    if view.durability is not None and _fsync_grade(view.durability.fsync) == FSYNC_NONE:
        legal = [name for name, rules in _RUNG_RULES.items() if rules.fsync_none]
        problems.append(
            f"durability.fsync: {FSYNC_NONE!r} is legal only at rung(s) {legal} (§5.8)"
        )


def _require_live_declarations(problems, view):
    """D9/§5.6: a document that can reach a venue declares its limits and its verifiers."""
    guards = view.guards if view.guards is not None else {}
    measures = {
        name: (guard.params or {}).get("measure")
        for name, guard in guards.items()
        if guard is not None
    }
    if not any(measure in SIZE_MEASURES for measure in measures.values()):
        problems.append(
            "guards: a live-capable document must declare a per-proposal size limit — "
            f"a guard whose params.measure is one of {list(SIZE_MEASURES)} (D9)"
        )
    losses = [name for name, measure in measures.items() if measure == LOSS_MEASURE]
    if not losses:
        problems.append(
            "guards: a live-capable document must declare a period loss limit — "
            f"a guard whose params.measure is {LOSS_MEASURE!r} with a window (D9)"
        )
    elif not any("window" in (guards[name].params or {}) for name in losses):
        problems.append(
            f"guards: the {LOSS_MEASURE!r} loss limit(s) {losses} must declare a window — "
            "a period loss limit without a period is none (D9)"
        )
    if view.accounting is not None and view.accounting.uses == PAPER_ACCOUNTING_KIND:
        problems.append(
            f"accounting.uses: {PAPER_ACCOUNTING_KIND!r} accounting cannot back a live-capable "
            "document — name a child Accounting class (D9)"
        )
    approval = view.arming.approval if view.arming is not None else None
    if approval is not None and approval.uses == DENY_ALL_APPROVAL_KIND:
        problems.append(
            f"arming.approval.uses: {DENY_ALL_APPROVAL_KIND!r} is the shadow/paper default — "
            "a live-capable document must name a child ApprovalVerifier class (§5.6)"
        )


@dataclass(frozen=True)
class _RungRules:
    """What one rung demands of a document beyond the grammar."""

    live_capable: bool
    fsync_none: bool

    def check(self, problems, view):
        """Apply this rung's rules to a parsed document view."""
        if not self.fsync_none:
            _refuse_unsynced(problems, view)
        if self.live_capable:
            _require_live_declarations(problems, view)


#: One row per ``vocab.RUNGS`` member, pinned at import: ``fsync: none``
#: only at shadow; D9's live default-deny at the two live rungs.
_RUNG_RULES = _pinned(
    {
        "shadow": _RungRules(live_capable=False, fsync_none=True),
        "paper": _RungRules(live_capable=False, fsync_none=False),
        "live_limited": _RungRules(live_capable=True, fsync_none=False),
        "live": _RungRules(live_capable=True, fsync_none=False),
    },
    RUNGS,
    "rung",
)
if FSYNC_NONE not in FSYNC_MODES:
    raise ProductionError([f"document.py's FSYNC_NONE {FSYNC_NONE!r} is not in vocab.FSYNC_MODES"])


# ---------------------------------------------------------------------------
# Cross-field rules
# ---------------------------------------------------------------------------


def _check_lease_budget(problems, view):
    """§5.7.2: ``ttl_ms > RENEWAL_BUDGET_FACTOR * (renew_every_ms + renew_timeout_ms)``."""
    lease = view.coordination
    if lease is None:
        return
    knobs = (lease.ttl_ms, lease.renew_every_ms, lease.renew_timeout_ms)
    if not all(_numeric(knob) for knob in knobs):
        return
    budget = RENEWAL_BUDGET_FACTOR * (lease.renew_every_ms + lease.renew_timeout_ms)
    if lease.ttl_ms <= budget:
        problems.append(
            f"coordination.ttl_ms must exceed {RENEWAL_BUDGET_FACTOR} * (renew_every_ms + "
            f"renew_timeout_ms) = {budget}, got {lease.ttl_ms} (§5.7.2)"
        )


def _check_routes(problems, view):
    """Every route names only declared sinks."""
    alerting = view.alerting
    if alerting is None or alerting.sinks is None or alerting.routes is None:
        return
    for index, route in enumerate(alerting.routes):
        for sink in route.sinks if route is not None and route.sinks is not None else ():
            if sink not in alerting.sinks:
                problems.append(
                    f"alerting.routes[{index}].sinks names {sink!r}, "
                    "which alerting.sinks does not declare"
                )


def _check_escalation(problems, view):
    """Every escalation level names only declared sinks (§5.11.2)."""
    alerting = view.alerting
    if alerting is None or alerting.sinks is None or alerting.escalation is None:
        return
    for level in ESCALATION_LEVELS:
        declared = getattr(alerting.escalation, level)
        for sink in declared.sinks if declared is not None and declared.sinks is not None else ():
            if sink not in alerting.sinks:
                problems.append(
                    f"alerting.escalation.{level}.sinks names {sink!r}, "
                    "which alerting.sinks does not declare"
                )


def _check_endpoints(problems, view):
    """Every endpoint belongs to a declared sink and names an env var the document requires."""
    endpoints = view.alert_endpoints
    if endpoints is None:
        return
    alerting = view.alerting
    sinks = alerting.sinks if alerting is not None and alerting.sinks is not None else {}
    required = tuple(view.env.require or ()) if view.env is not None else ()
    for name, endpoint in endpoints.items():
        if name not in sinks:
            problems.append(f"alert_endpoints.{name}: alerting.sinks declares no sink named {name!r}")
        if endpoint is None or not isinstance(endpoint.url_env, str):
            continue
        if endpoint.url_env not in required:
            problems.append(
                f"alert_endpoints.{name}.url_env: {endpoint.url_env!r} must be listed in "
                "env.require (§5.11)"
            )


def _check_calibration_monitor(problems, view):
    """`readiness.calibration_monitor` names a declared monitor (§5.13.4)."""
    readiness = view.readiness
    named = None if readiness is None else readiness.calibration_monitor
    if named is None:
        return
    declared = view.monitors or {}
    if named not in declared:
        problems.append(
            f"readiness.calibration_monitor names {named!r}, which monitors does not declare "
            "— its verdict would be one nothing writes"
        )


def _check_rung(problems, view):
    """Apply the rung's row of the table, when the rung parsed."""
    rules = _RUNG_RULES.get(view.rung) if isinstance(view.rung, str) else None
    if rules is not None:
        rules.check(problems, view)


def _strip_notes(obj):
    """Drop the ``notes`` key at every depth — the pipeline's rule, restated and pinned by test."""
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if k != _NOTES}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class ServeDocument:
    """A validated serve document (§4.1): its grammar, its identity, and read views.

    Construction validates the whole §4.1 grammar — default-deny at every
    level but ``params``, every problem accumulated — then the cross-field
    rules (lease budget, routes and endpoints against declared sinks and
    ``env.require``) and the rung table (``fsync: none`` only at shadow;
    D9's live default-deny). The caller's object is never mutated.

    Parameters
    ----------
    obj : dict
        The document as ``json.load`` returns it.

    Attributes
    ----------
    name, series_id, rung : str
        The required identity scalars.
    serving, feed, schedule, guards, … : view
        Every top-level section by attribute: fixed keys by attribute,
        author-named maps (``guards``, ``monitors``, ``health.probes``,
        ``alerting.sinks``, ``alert_endpoints``, ``heartbeat.emitters``,
        ``serving.replay``) as read-only mappings, lists as tuples,
        ``params`` as read-only mappings, ``coordination.scope`` as an
        ``ExecutionScope``, and an absent optional key or section as None.
    doc_hash : str
        ``config_hash(self, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)``.

    Raises
    ------
    ProductionError
        Every problem at once, each naming its key.

    Examples
    --------
    Load a document and read it::

        doc = ServeDocument.load("configs/serve.json")
        doc.rung  # 'paper'
        doc.schedule.max_staleness_ms  # 120000
        doc.guards["size"].params["measure"]  # 'quantity'
        doc.schedule.overrun  # None when omitted — cadence's named default applies
        doc.to_obj() == json.load(open("configs/serve.json"))  # True: the exact round trip
        doc.doc_hash == config_hash(doc, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)  # True
    """

    def __init__(self, obj):
        if not isinstance(obj, dict):
            raise ProductionError([f"a serve document is a JSON object (dict), got {obj!r}"])
        obj = copy.deepcopy(obj)
        problems = []
        view = _GRAMMAR.check(problems, "", obj)
        for rule in (_check_lease_budget, _check_routes, _check_escalation,
                     _check_endpoints, _check_calibration_monitor, _check_rung):
            rule(problems, view)
        if problems:
            raise ProductionError(problems)
        self._obj = obj
        self._view = view

    @classmethod
    def from_obj(cls, obj):
        """Validate a document object.

        Parameters
        ----------
        obj : dict
            As ``json.load`` returns it; deep-copied, never mutated.

        Returns
        -------
        ServeDocument

        Raises
        ------
        ProductionError
            Every problem at once.
        """
        return cls(obj)

    @classmethod
    def load(cls, path):
        """Read and validate a JSON document file.

        Parameters
        ----------
        path : str or pathlib.Path
            A UTF-8 JSON file holding one document object.

        Returns
        -------
        ServeDocument

        Raises
        ------
        ProductionError
            If the file cannot be read or parsed, or the document refuses.
        """
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ProductionError([f"cannot read serve document {str(path)!r}: {exc}"]) from exc
        return cls(obj)

    def to_obj(self):
        """Return the document exactly as declared — a fresh deep copy each call.

        Returns
        -------
        dict
            The validated object with every optional key exactly as
            present or absent; mutating it changes nothing here.
        """
        return copy.deepcopy(self._obj)

    def identity_obj(self):
        """Return the hash material: ``notes`` gone everywhere, the excluded sections dropped.

        Returns
        -------
        dict
            What ``config_hash`` canonicalises — for a golden test or a
            human diff of two identities.
        """
        return {
            key: _strip_notes(value)
            for key, value in self._obj.items()
            if key != _NOTES and key not in PRODUCTION_NON_IDENTITY_SECTIONS
        }

    @property
    def doc_hash(self):
        """The identity (§4.2): the pipeline recipe with the production exclusions, 64 hex chars."""
        return config_hash(self, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)

    def __getattr__(self, name):
        """Read a top-level section or scalar by attribute."""
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._view, name)

    def __repr__(self):
        """Name the document, its series, its rung and the head of its identity."""
        return (
            f"ServeDocument({self.name!r}, series_id={self.series_id!r}, "
            f"rung={self.rung!r}, doc_hash={self.doc_hash[:12]!r}...)"
        )
