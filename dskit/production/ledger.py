"""The append-only chain and the serve-root layout (plan §5.8, §6, D13, D15).

A serve series is one hash chain, written as ``ledger.NNNN.jsonl``
segments under ``<root>/<series_id>/``, and everything a process knows is
a fold over it (§5.8.1). This module owns three things and nothing else:
WHERE the files live (:class:`ServeRoot` is the only object that knows the
directory shape — no other module builds a serve path by concatenation),
HOW a record lands (:class:`JsonlLedger`: one ``O_APPEND`` write per
record, a graded fsync policy, the ``serve.lock`` writer lock, torn-tail
recovery and segment rotation), and WHAT a head-bound cache must prove
before it is trusted (:class:`Checkpoint`).

The envelope nests the body. A caller hands over exactly
``{"kind", "id", "body"}`` and the ledger writes twelve fields: those
three plus ``payload_digest``, ``seq``, ``series_id``, ``process_id``,
``release_hash``, ``recorded_at_ms``, ``schema_version``, ``prev_hash``
and ``hash``. The nesting is not cosmetic: a §6 body legitimately carries
its own ``kind``, ``series_id`` or ``release_hash`` (a ``tick_start``
written during recovery names a release that is not the writing
process's), and a flat merge would silently overwrite one with the other.
``payload_digest`` is :func:`~dskit.production.base.canonical_hash` over
the caller's three fields only, which makes idempotency a property of the
caller's content — the same ``id`` with the same digest returns the prior
``seq`` and writes nothing, the same ``id`` with a different digest
refuses — and ``hash`` is :func:`~dskit.production.base.record_hash` over
the envelope without ``hash``, chained from a ``prev_hash`` of sixty-four
zeros. The attached ``SeriesState`` is folded from the line as it was
written and decoded, never from the caller's in-memory objects, so a live
fold and a replay from disk see byte-identical input.

Durability is graded by ``durability.fsync`` (``every`` / ``batch`` /
``none``) but :meth:`Ledger.barrier` always reaches the platter: D13's
safety records cross it regardless of policy, and a segment roll seals the
old segment and fsyncs the directory so a new file cannot vanish with the
power. Money never touches float: a ``float`` under any
:data:`~dskit.production.vocab.MONEY_FIELDS` name, at any depth of the
body (a list under a money name inherits it), refuses before anything is
written, while a ratio under any other name stays a float.

``verify()`` locates damage rather than merely detecting it: it walks
every segment and returns the ``seq`` the walk EXPECTED at the first
position that fails — an edited line reports its own ``seq``; a deletion,
an insertion or a reorder reports the ``seq`` that should have stood
there.

Import cost: stdlib plus :mod:`dskit.onboarding.base` (the maildir write
the caches and the genesis use) and the package's own ``base``, ``vocab``
and ``redact``.
"""

import fcntl
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields

from dskit.onboarding.base import _fsync_dir, durable_write_json
from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    GENESIS_HASH,
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_bytes,
    canonical_hash,
    now_ms,
    record_hash,
    reject_unknown_params,
)
from dskit.production.redact import get_logger
from dskit.production.vocab import (
    FSYNC_MODES,
    MONEY_FIELDS,
    RECORD_KINDS,
    ROTATE_BY,
)

__all__ = [
    "Checkpoint",
    "DEFAULT_FSYNC",
    "DEFAULT_ROTATE_BY",
    "DEFAULT_SNAPSHOT_EVERY",
    "JsonlLedger",
    "Ledger",
    "SCHEMA_VERSION",
    "ServeRoot",
]

#: The envelope schema this writer emits. A reader tolerates unknown
#: fields and upcasts an older version (§5.8); the genesis carries it too.
SCHEMA_VERSION = 1

#: ``durability.fsync`` when none is given: the safest grade.
DEFAULT_FSYNC = "every"

#: ``placement.rotate.by`` when no rotation is given.
DEFAULT_ROTATE_BY = "day"

#: Auto-snapshot cadence when none is given: off. A stateless ledger must
#: construct, which it could not if the default asked for a projection
#: there is no ``state`` to supply.
DEFAULT_SNAPSHOT_EVERY = None

_log = get_logger("ledger")

#: What a caller supplies; every other envelope field is assigned here.
_CALLER_KEYS = ("kind", "id", "body")

#: The twelve envelope fields and the type a well-formed line carries.
_ENVELOPE_TYPES = {
    "kind": str,
    "id": str,
    "body": dict,
    "payload_digest": str,
    "seq": int,
    "series_id": str,
    "process_id": str,
    "release_hash": str,
    "recorded_at_ms": int,
    "schema_version": int,
    "prev_hash": str,
    "hash": str,
}

#: The one record kind the ledger itself produces (§6 ``snapshot``).
_SNAPSHOT_KIND = "snapshot"

#: The two answers a cache validation can give — members of
#: :data:`~dskit.production.vocab.CACHE_STATES`, pinned by test.
_CURRENT, _STALE = "current", "stale"

_MS_PER_DAY = 86_400_000
_SEGMENT_NAME = "ledger.{index:04d}.jsonl"
_SEGMENT = re.compile(r"^ledger\.(\d+)\.jsonl\Z")


# ---------------------------------------------------------------------------
# The layout — one owner (§5.8 serve-root tree, D15 genesis)
# ---------------------------------------------------------------------------


class ServeRoot:
    """The serve-series root: the one object that knows the directory shape.

    ``<root>/<series_id>/`` is created on first use together with the
    ``series.json`` genesis that binds the series id (D15), ``ledger/``
    and the three ``commands/`` queues; a later ``ServeRoot`` over the
    same directory with a different ``series_id`` refuses. Every other
    path of the §5.8 tree is an accessor here. The ``HALT`` sentinel is
    never created — absent means not halted by file.

    Parameters
    ----------
    root : str
        ``placement.ledger_root``: the directory holding one subdirectory
        per series. Used verbatim, never normalised.
    series_id : str
        The operator-issued series UUID — a non-blank single path segment.

    Attributes
    ----------
    series_id : str
        The id, as validated against the genesis.
    series_path : str
        ``<root>/<series_id>``.

    Raises
    ------
    ProductionError
        If ``root`` or ``series_id`` is malformed, the genesis on disk is
        not JSON, or it binds a different series id.

    Examples
    --------
    Bind a series and hand its paths to the writers::

        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        serve.lock_path  # './serve/018f0f4e-…/serve.lock'
        serve.segment_path(1)  # './serve/018f0f4e-…/ledger/ledger.0001.jsonl'
        serve.process_base_dir("a" * 64, "proc-1")
        # -> './serve/018f0f4e-…/releases/aaaa…/process-proc-1/base'
    """

    def __init__(self, root, series_id):
        problems = []
        _check_str(problems, "root", root)
        _check_str(problems, "series_id", series_id)
        if isinstance(series_id, str) and (
            not series_id.strip()
            or os.path.basename(series_id) != series_id
            or series_id in (os.curdir, os.pardir)
        ):
            problems.append(
                f"series_id must be a non-blank single path segment, got {series_id!r}"
            )
        if problems:
            raise ProductionError(problems)
        self._series_id = series_id
        self._series_path = os.path.join(root, series_id)
        for directory in (
            self._series_path,
            self.ledger_dir,
            self.commands_inbox,
            self.commands_applied,
            self.commands_rejected,
        ):
            os.makedirs(directory, exist_ok=True)
        self._bind_genesis()

    def _bind_genesis(self):
        """Write the genesis on first use; refuse one that binds another series."""
        path = self.genesis_path
        if not os.path.exists(path):
            durable_write_json(
                path,
                {
                    "series_id": self._series_id,
                    "created_ms": now_ms(),
                    "schema_version": SCHEMA_VERSION,
                },
            )
            return
        try:
            with open(path, encoding="utf-8") as fh:
                genesis = json.load(fh)
        except ValueError as exc:
            raise ProductionError([f"{path}: genesis is not JSON ({exc})"]) from exc
        bound = genesis.get("series_id") if isinstance(genesis, dict) else None
        if bound != self._series_id:
            raise ProductionError(
                [f"{path} binds series_id {bound!r}, not {self._series_id!r}"]
            )

    @property
    def series_id(self):
        """The series id this root is bound to."""
        return self._series_id

    @property
    def series_path(self):
        """``<root>/<series_id>``, the stable serve-series root."""
        return self._series_path

    @property
    def genesis_path(self):
        """``series.json``: the immutable genesis binding."""
        return os.path.join(self._series_path, "series.json")

    @property
    def arming_cache(self):
        """``arming.json``: an optional head-bound cache of the arming fold."""
        return os.path.join(self._series_path, "arming.json")

    @property
    def breaker_cache(self):
        """``breaker.json``: an optional head-bound cache of the breaker fold."""
        return os.path.join(self._series_path, "breaker.json")

    @property
    def checkpoint_cache(self):
        """``checkpoint.json``: the :class:`Checkpoint` cache."""
        return os.path.join(self._series_path, "checkpoint.json")

    @property
    def halt_sentinel(self):
        """``HALT``: the cross-release kill switch; absent means not halted by file."""
        return os.path.join(self._series_path, "HALT")

    @property
    def lock_path(self):
        """``serve.lock``: the same-filesystem, cross-release writer lock."""
        return os.path.join(self._series_path, "serve.lock")

    @property
    def commands_inbox(self):
        """``commands/inbox``: fsynced caller-UUID control requests."""
        return os.path.join(self._series_path, "commands", "inbox")

    @property
    def commands_applied(self):
        """``commands/applied``: terminal accepted command receipts."""
        return os.path.join(self._series_path, "commands", "applied")

    @property
    def commands_rejected(self):
        """``commands/rejected``: terminal refused command receipts."""
        return os.path.join(self._series_path, "commands", "rejected")

    @property
    def heartbeat_path(self):
        """``heartbeat.json``: the file heartbeat."""
        return os.path.join(self._series_path, "heartbeat.json")

    @property
    def ledger_dir(self):
        """``ledger/``: the segments of the one chain across releases."""
        return os.path.join(self._series_path, "ledger")

    def release_dir(self, release_hash):
        """Return ``releases/<release_hash>``, a release's frozen home.

        Parameters
        ----------
        release_hash : str
            The release's identity hash.

        Returns
        -------
        str
            The directory that holds ``document.json``, ``release.json``
            and the per-process base-pass run dirs. Not created here.
        """
        return os.path.join(self._series_path, "releases", release_hash)

    def process_base_dir(self, release_hash, process_id):
        """Return ``releases/<release_hash>/process-<process_id>/base``.

        Parameters
        ----------
        release_hash : str
            The release's identity hash.
        process_id : str
            The serving process's id.

        Returns
        -------
        str
            The process's base-pass run dir (config/plan/resolved/nodes).
            Not created here.
        """
        return os.path.join(self.release_dir(release_hash), f"process-{process_id}", "base")

    def segment_path(self, index):
        """Return the path of ledger segment ``index`` (``ledger.NNNN.jsonl``).

        Parameters
        ----------
        index : int
            The 1-based segment number.

        Returns
        -------
        str
            ``ledger/ledger.0001.jsonl`` for ``index`` 1; four digits with
            zero padding, more when the count outgrows them.
        """
        return os.path.join(self.ledger_dir, _SEGMENT_NAME.format(index=index))

    def segment_paths(self):
        """Return every existing segment as ``(index, path)``, in chain order.

        Returns
        -------
        list of tuple
            ``(index, path)`` pairs sorted by the numeric index — never by
            name, which would misorder segment 10000 before 9999. Files
            that are not segments are ignored.
        """
        found = []
        for name in os.listdir(self.ledger_dir):
            match = _SEGMENT.match(name)
            if match:
                found.append((int(match.group(1)), os.path.join(self.ledger_dir, name)))
        return sorted(found)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class Ledger(ABC):
    """The append-only chain every producer records into (§5.8, D15).

    A subclass persists envelopes somewhere; this seam fixes what a
    producer may rely on: dense 1-based ``seq``, idempotency by the
    caller's ``id`` plus ``payload_digest``, the fold kept level with the
    chain (``append`` calls ``SeriesState.apply``), and a ``barrier`` that
    D13's safety records cross regardless of the durability grade.

    Examples
    --------
    The one shipped implementation, seen through the seam::

        from dskit.production.clock import WallClock

        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        ledger = JsonlLedger(serve, "proc-1", "a" * 64, clock=WallClock())
        isinstance(ledger, Ledger)  # True
        ledger.append({"kind": "tick_start", "id": "tick-1", "body": {}})  # 1
        ledger.barrier()
        ledger.close()
    """

    @abstractmethod
    def append(self, record):
        """Append one record, or return the seq it already holds.

        Parameters
        ----------
        record : dict
            Exactly ``{"kind", "id", "body"}``: a ``kind`` from
            ``vocab.RECORD_KINDS``, the caller's stable ``id``, and a
            dict body with no float under a money name.

        Returns
        -------
        int
            The record's dense 1-based ``seq`` — the prior one when the
            same ``id`` was appended before with the same payload digest.

        Raises
        ------
        ProductionError
            On a malformed record, a float under a money name, or an
            ``id`` already appended with a different payload.
        """

    @abstractmethod
    def append_many(self, records):
        """Append records in order after validating every one of them.

        Parameters
        ----------
        records : iterable of dict
            As for :meth:`append`. Nothing is written if any record is
            malformed.

        Returns
        -------
        tuple of int
            One ``seq`` per record, duplicates resolved as :meth:`append`
            resolves them.

        Raises
        ------
        ProductionError
            As for :meth:`append`, every problem accumulated.
        """

    @abstractmethod
    def barrier(self):
        """Make every appended record durable, regardless of the fsync grade.

        Returns
        -------
        None
            Returns only once the platter holds the head.
        """

    @abstractmethod
    def scan(self, kind=None, since_seq=0):
        """Yield envelopes in ``seq`` order.

        Parameters
        ----------
        kind : str or None
            Keep only this record kind; ``None`` keeps every kind.
        since_seq : int
            EXCLUSIVE lower bound: ``since_seq=N`` yields from ``N + 1``,
            so a snapshot's ``at_seq`` replays forward without repeating
            what the snapshot already holds.

        Returns
        -------
        iterator of dict
            The full twelve-field envelopes.
        """

    @abstractmethod
    def head(self):
        """Return the chain head.

        Returns
        -------
        tuple
            ``(seq, hash)`` — ``(0, GENESIS_HASH)`` for an empty chain.
        """

    @abstractmethod
    def verify(self):
        """Walk the whole chain and locate the first damage.

        Returns
        -------
        int or None
            ``None`` when every link holds; otherwise the ``seq`` the
            walk expected at the first position that fails.
        """

    @abstractmethod
    def snapshot(self, payload):
        """Append a ``snapshot`` record projecting the current head.

        Parameters
        ----------
        payload : dict
            The JSON-able state (``SeriesState.to_snapshot_obj()``).

        Returns
        -------
        int
            The snapshot record's ``seq``; its body is ``{at_seq,
            state_digest, state}`` with ``at_seq`` the head it projects.
        """

    @abstractmethod
    def latest_snapshot(self):
        """Return the most recent ``snapshot`` envelope.

        Returns
        -------
        dict or None
            The envelope, or ``None`` when no snapshot was ever appended.
        """

    @abstractmethod
    def close(self):
        """Release the writer lock and every file handle; idempotent.

        Returns
        -------
        None
            A closed ledger refuses every further write.
        """


# ---------------------------------------------------------------------------
# Durability and rotation — strategy objects keyed by the vocabularies
# ---------------------------------------------------------------------------


class _Durability(ABC):
    """When an appended line must reach the platter (``durability.fsync``)."""

    _PARAMS = ("notes",)

    def __init__(self, problems, knobs, clock):
        reject_unknown_params(problems, knobs, self._PARAMS)
        self._clock = clock
        self._configure(problems, knobs)

    def _configure(self, problems, knobs):
        """Read this grade's knobs; the default has none."""

    @abstractmethod
    def due(self):
        """Say whether the line just written must be fsynced now."""

    def reset(self):
        """Forget any pending batch — a barrier or a roll just synced."""


class _SyncEvery(_Durability):
    """Every append is fsynced before it returns."""

    def due(self):
        """Sync every append."""
        return True


class _SyncNever(_Durability):
    """No append fsyncs; only ``barrier()`` does (legal at ``shadow`` only)."""

    def due(self):
        """Sync nothing on append; only a barrier or a roll does."""
        return False


class _SyncBatch(_Durability):
    """Fsync after ``n`` pending lines or ``ms`` since the first pending one."""

    _PARAMS = ("n", "ms", "notes")

    def _configure(self, problems, knobs):
        """Require both knobs: a batch with no bound is ``none`` in disguise."""
        check_int_param(problems, "fsync.batch.n", knobs.get("n"), ge=1)
        check_int_param(problems, "fsync.batch.ms", knobs.get("ms"), ge=0)
        self._n, self._ms = knobs.get("n"), knobs.get("ms")
        self._count, self._since = 0, None

    def due(self):
        """Count the line; due at ``n`` lines or once ``ms`` have elapsed."""
        now = self._clock.monotonic()
        if self._since is None:
            self._since = now
        self._count += 1
        return self._count >= self._n or (now - self._since) * 1000.0 >= self._ms

    def reset(self):
        """Start the next batch from the next append."""
        self._count, self._since = 0, None


_DURABILITIES = {"every": _SyncEvery, "batch": _SyncBatch, "none": _SyncNever}


def _durability(problems, fsync, clock):
    """Resolve ``fsync`` (a mode name or ``{mode: knobs}``) to a policy."""
    if isinstance(fsync, str):
        name, knobs = fsync, {}
    elif isinstance(fsync, dict) and len(fsync) == 1:
        ((name, knobs),) = fsync.items()
    else:
        problems.append(
            f"fsync must be one of {list(FSYNC_MODES)} or {{'batch': {{'n', 'ms'}}}}, "
            f"got {fsync!r}"
        )
        return None
    policy = _DURABILITIES.get(name)
    if policy is None:
        problems.append(f"fsync mode must be one of {list(FSYNC_MODES)}, got {name!r}")
        return None
    if not isinstance(knobs, dict):
        problems.append(f"fsync.{name} knobs must be a dict, got {knobs!r}")
        return None
    return policy(problems, knobs, clock)


class _Cursor:
    """The open segment: index, fd, byte size, last stamp, and who created it."""

    __slots__ = ("index", "fd", "size", "last_ms", "opened_here")

    def __init__(self, index, fd, size, last_ms, opened_here):
        self.index = index
        self.fd = fd
        self.size = size
        self.last_ms = last_ms
        self.opened_here = opened_here


class _Rotation(ABC):
    """When an append opens a new segment (``placement.rotate``).

    ``max_bytes`` is an optional cap in every mode and the only trigger
    for ``size`` — so the document's ``{"by": "day", "max_bytes": …}``
    means daily segments that never outgrow the cap.
    """

    _PARAMS = ("by", "max_bytes", "notes")
    _cap_required = False

    def __init__(self, problems, spec):
        reject_unknown_params(problems, spec, self._PARAMS)
        self._max_bytes = spec.get("max_bytes")
        if self._max_bytes is not None or self._cap_required:
            check_int_param(problems, "rotate.max_bytes", self._max_bytes, ge=1)

    def rolls(self, cursor, line_len, at_ms):
        """Say whether ``line_len`` more bytes at ``at_ms`` start a new segment."""
        if (
            self._max_bytes is not None
            and cursor.size > 0
            and cursor.size + line_len > self._max_bytes
        ):
            return True
        return self._boundary(cursor, at_ms)

    @abstractmethod
    def _boundary(self, cursor, at_ms):
        """Report this mode's own boundary, the cap aside."""


class _RotateBySize(_Rotation):
    """Only the byte cap rolls."""

    _cap_required = True

    def _boundary(self, cursor, at_ms):
        return False


class _RotateByDay(_Rotation):
    """Roll when the injected clock's UTC day differs from the last record's."""

    def _boundary(self, cursor, at_ms):
        return cursor.last_ms is not None and (
            at_ms // _MS_PER_DAY != cursor.last_ms // _MS_PER_DAY
        )


class _RotateByProcess(_Rotation):
    """Each open writes into a segment of its own: roll off an inherited one."""

    def _boundary(self, cursor, at_ms):
        return not cursor.opened_here


_ROTATIONS = {"size": _RotateBySize, "day": _RotateByDay, "process": _RotateByProcess}


def _rotation(problems, rotate):
    """Resolve ``placement.rotate`` (``None`` = the named default) to a policy."""
    spec = {"by": DEFAULT_ROTATE_BY} if rotate is None else rotate
    _check_dict(problems, "rotate", spec)
    if not isinstance(spec, dict):
        return None
    by = spec.get("by")
    policy = _ROTATIONS.get(by)
    if policy is None:
        problems.append(f"rotate.by must be one of {list(ROTATE_BY)}, got {by!r}")
        return None
    return policy(problems, spec)


# ---------------------------------------------------------------------------
# Record checks and line codec
# ---------------------------------------------------------------------------


def _check_money(problems, value, path, money):
    """Refuse a float under a money name at any depth; a list inherits the name."""
    if isinstance(value, float):
        if money:
            problems.append(f"{path}: money never touches float, got {value!r}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_money(problems, item, f"{path}.{key}", key in MONEY_FIELDS)
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            _check_money(problems, item, f"{path}[{position}]", money)


def _read(path):
    """Yield ``(lineno, raw_bytes)`` for every line; the last may lack its newline."""
    with open(path, "rb") as fh:
        for lineno, raw in enumerate(fh, 1):
            yield lineno, raw


def _decode(path, lineno, raw):
    """Parse one written line into its envelope, refusing anything else."""
    try:
        envelope = json.loads(raw)
    except ValueError as exc:
        raise ProductionError([f"{path} line {lineno}: not JSON ({exc})"]) from exc
    if not isinstance(envelope, dict) or any(
        not isinstance(envelope.get(name), expected)
        for name, expected in _ENVELOPE_TYPES.items()
    ):
        raise ProductionError([f"{path} line {lineno}: not a ledger envelope"])
    return envelope


# ---------------------------------------------------------------------------
# JSONL — the shipped implementation
# ---------------------------------------------------------------------------


class JsonlLedger(Ledger):
    """The JSONL chain: one ``O_APPEND`` write per record, locked, verifiable.

    Opening takes ``serve.lock`` (``flock``, exclusive, non-blocking) for
    the object's lifetime, walks every segment to recover the head, the
    idempotency index and the latest snapshot, and truncates a torn final
    line — a line without its newline is a write the crash cut short,
    never a record. Segments are ``ledger.NNNN.jsonl``, created lazily on
    the first write and rolled by the rotation policy; a roll seals the
    old segment with an fsync and fsyncs the directory, and the first
    record of the new segment chains from the old one's tail, so the
    chain is continuous across files.

    Parameters
    ----------
    serve_root : ServeRoot
        The layout; ``series_id`` comes from it.
    process_id : str
        The writing process's id, stamped on every envelope.
    release_hash : str
        The writing process's release, stamped on every envelope.
    clock : Clock
        Injected; ``now_ms()`` stamps ``recorded_at_ms`` and drives day
        rotation, ``monotonic()`` bounds a batch's age.
    fsync : str or dict
        ``"every"`` (default), ``"none"``, or ``{"batch": {"n": int >= 1,
        "ms": int >= 0}}`` — fsync after ``n`` pending lines or ``ms``
        since the first pending one. Members of ``vocab.FSYNC_MODES``.
    rotate : dict or None
        ``{"by": one of vocab.ROTATE_BY, "max_bytes": int >= 1}``;
        ``max_bytes`` is required for ``size`` and an optional cap
        otherwise. ``None`` means ``{"by": DEFAULT_ROTATE_BY}``.
    state : SeriesState or None
        The fold; every appended envelope is handed to ``state.apply``
        exactly as it will be read back (a deduplicated append is not).
    snapshot_every : int or None
        Append a ``snapshot`` of ``state.to_snapshot_obj()`` after this
        many records since the last one; ``None`` (the default) never
        does. Requires ``state``.

    Raises
    ------
    ProductionError
        On a malformed argument, when another writer holds
        ``serve.lock``, or when a sealed segment is damaged.

    Examples
    --------
    Open the series' sole writer, record a tick start and make it durable
    before any work::

        from dskit.production.clock import WallClock

        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        ledger = JsonlLedger(serve, "proc-1", "a" * 64, clock=WallClock())
        ledger.append({"kind": "tick_start", "id": "tick-1",
                       "body": {"tick_id": "tick-1", "tick_at_ms": 0}})  # 1
        ledger.barrier()
        ledger.head()  # (1, '<sha256 hex>')
        ledger.close()
    """

    def __init__(
        self,
        serve_root,
        process_id,
        release_hash,
        *,
        clock,
        fsync=DEFAULT_FSYNC,
        rotate=None,
        state=None,
        snapshot_every=DEFAULT_SNAPSHOT_EVERY,
    ):
        problems = []
        if not isinstance(serve_root, ServeRoot):
            problems.append(f"serve_root must be a ServeRoot, got {serve_root!r}")
        _check_str(problems, "process_id", process_id)
        _check_str(problems, "release_hash", release_hash)
        for method in ("now_ms", "monotonic"):
            if not callable(getattr(clock, method, None)):
                problems.append(f"clock must provide {method}(), got {clock!r}")
        durability = _durability(problems, fsync, clock)
        rotation = _rotation(problems, rotate)
        if state is not None and not callable(getattr(state, "apply", None)):
            problems.append(f"state must provide apply(envelope), got {state!r}")
        if snapshot_every is not None:
            check_int_param(problems, "snapshot_every", snapshot_every, ge=1)
            if not callable(getattr(state, "to_snapshot_obj", None)):
                problems.append(
                    "snapshot_every needs a state with to_snapshot_obj() to project"
                )
        if problems:
            raise ProductionError(problems)
        self._root = serve_root
        self._process_id = process_id
        self._release_hash = release_hash
        self._clock = clock
        self._durability = durability
        self._rotation = rotation
        self._state = state
        self._snapshot_every = snapshot_every
        self._seq, self._head = 0, GENESIS_HASH
        self._index = {}
        self._latest_snapshot_seq = None
        self._since_snapshot = 0
        self._cursor = None
        self._closed = False
        self._lock_fd = self._acquire_lock()
        try:
            self._recover()
        except BaseException:
            self._release_lock()
            raise

    # -- open / close -------------------------------------------------------

    def _acquire_lock(self):
        """Take ``serve.lock`` exclusively; another holder refuses at once."""
        path = self._root.lock_path
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise ProductionError(
                [f"serve.lock at {path} is held by another writer ({exc})"]
            ) from exc
        return fd

    def _release_lock(self):
        """Unlock and close ``serve.lock``."""
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)

    def _recover(self):
        """Walk every segment: head, index, snapshot cadence, and the torn tail."""
        segments = self._root.segment_paths()
        last_ms = None
        for position, (index, path) in enumerate(segments):
            is_tail = position == len(segments) - 1
            complete = 0
            for lineno, raw in _read(path):
                if not raw.endswith(b"\n"):
                    if not is_tail:
                        raise ProductionError(
                            [f"{path} line {lineno}: torn line inside a sealed segment"]
                        )
                    break
                envelope = _decode(path, lineno, raw)
                self._seq, self._head = envelope["seq"], envelope["hash"]
                self._index[envelope["id"]] = (envelope["payload_digest"], self._seq)
                self._note_kind(envelope["kind"], self._seq)
                last_ms = envelope["recorded_at_ms"]
                complete += len(raw)
            if is_tail:
                self._cursor = self._reopen(index, path, complete, last_ms)

    def _reopen(self, index, path, complete, last_ms):
        """Open the tail segment for append, dropping bytes past the last newline."""
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        size = os.fstat(fd).st_size
        if size != complete:
            os.ftruncate(fd, complete)
            os.fsync(fd)
            _log.warning("discarded a torn tail of %d bytes in %s", size - complete, path)
        return _Cursor(index, fd, complete, last_ms, opened_here=False)

    def _create_segment(self, index, last_ms):
        """Create segment ``index`` exclusively and make its directory entry durable."""
        fd = os.open(
            self._root.segment_path(index),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        _fsync_dir(self._root.ledger_dir)
        return _Cursor(index, fd, 0, last_ms, opened_here=True)

    def _roll(self):
        """Open the next segment, then seal and close the current one."""
        old = self._cursor
        new = self._create_segment(old.index + 1, old.last_ms)
        os.fsync(old.fd)
        os.close(old.fd)
        self._cursor = new
        self._durability.reset()

    def _open_check(self):
        """Refuse any write after ``close()``."""
        if self._closed:
            raise ProductionError(["the ledger is closed"])

    def close(self):
        """Seal the open segment, release ``serve.lock`` and close every fd.

        Returns
        -------
        None
            Idempotent; a second call does nothing.
        """
        if self._closed:
            return
        try:
            if self._cursor is not None:
                os.fsync(self._cursor.fd)
                os.close(self._cursor.fd)
                self._cursor = None
        finally:
            self._closed = True
            self._release_lock()

    # -- the write path -----------------------------------------------------

    def _prepare(self, record, where):
        """Validate a caller record; return it as a plain dict with its digest."""
        problems = []
        _check_dict(problems, where, record)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, record, _CALLER_KEYS, where)
        record_kind = record.get("kind")
        if record_kind not in RECORD_KINDS:
            problems.append(
                f"{where}.kind must be one of {list(RECORD_KINDS)}, got {record_kind!r}"
            )
        _check_str(problems, f"{where}.id", record.get("id"))
        body = record.get("body")
        _check_dict(problems, f"{where}.body", body)
        if isinstance(body, dict):
            _check_money(problems, body, f"{where}.body", False)
        if problems:
            raise ProductionError(problems)
        caller = {"kind": record_kind, "id": record["id"], "body": body}
        return caller, canonical_hash(caller)

    def _commit(self, record, digest):
        """Deduplicate, assign the envelope, write one line, fold; return the seq."""
        self._open_check()
        prior = self._index.get(record["id"])
        if prior is not None:
            if prior[0] == digest:
                return prior[1]
            raise ProductionError(
                [
                    f"record id {record['id']!r} was already appended at seq "
                    f"{prior[1]} with a different payload"
                ]
            )
        at_ms = self._clock.now_ms()
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            raise ProductionError([f"clock.now_ms() must return an int, got {at_ms!r}"])
        seq = self._seq + 1
        envelope = dict(record)
        envelope.update(
            payload_digest=digest,
            seq=seq,
            series_id=self._root.series_id,
            process_id=self._process_id,
            release_hash=self._release_hash,
            recorded_at_ms=at_ms,
            schema_version=SCHEMA_VERSION,
            prev_hash=self._head,
        )
        envelope["hash"] = record_hash(self._head, envelope)
        line = canonical_bytes(envelope) + b"\n"
        self._write(line, at_ms)
        self._seq, self._head = seq, envelope["hash"]
        self._index[record["id"]] = (digest, seq)
        self._note_kind(record["kind"], seq)
        if self._state is not None:
            self._state.apply(json.loads(line))
        return seq

    def _write(self, line, at_ms):
        """Land one line with a single ``write`` on the right segment."""
        if self._cursor is None:
            self._cursor = self._create_segment(1, None)
        elif self._rotation.rolls(self._cursor, len(line), at_ms):
            self._roll()
        cursor = self._cursor
        written = os.write(cursor.fd, line)
        if written != len(line):
            os.ftruncate(cursor.fd, cursor.size)
            raise ProductionError(
                [
                    f"short write to {self._root.segment_path(cursor.index)}: "
                    f"{written} of {len(line)} bytes; the tail was truncated back"
                ]
            )
        cursor.size += written
        cursor.last_ms = at_ms
        if self._durability.due():
            os.fsync(cursor.fd)

    def _note_kind(self, record_kind, seq):
        """Track the latest snapshot and the records since it."""
        if record_kind == _SNAPSHOT_KIND:
            self._latest_snapshot_seq = seq
            self._since_snapshot = 0
        else:
            self._since_snapshot += 1

    def _auto_snapshot(self):
        """Project the state once the cadence is reached."""
        if self._snapshot_every is not None and self._since_snapshot >= self._snapshot_every:
            self.snapshot(self._state.to_snapshot_obj())

    def append(self, record):
        """Append one record (see :meth:`Ledger.append`); one write, one fold."""
        caller, digest = self._prepare(record, "record")
        seq = self._commit(caller, digest)
        self._auto_snapshot()
        return seq

    def append_many(self, records):
        """Append records in order (see :meth:`Ledger.append_many`).

        Every record is validated before the first is written, so a
        malformed third record leaves the first two unwritten.
        """
        problems, prepared = [], []
        for position, record in enumerate(records):
            try:
                prepared.append(self._prepare(record, f"records[{position}]"))
            except ProductionError as exc:
                problems.extend(exc.problems)
        if problems:
            raise ProductionError(problems)
        seqs = []
        for caller, digest in prepared:
            seqs.append(self._commit(caller, digest))
            self._auto_snapshot()
        return tuple(seqs)

    def barrier(self):
        """Fsync the open segment whatever the grade (see :meth:`Ledger.barrier`)."""
        self._open_check()
        if self._cursor is not None:
            os.fsync(self._cursor.fd)
        self._durability.reset()

    def snapshot(self, payload):
        """Append a ``snapshot`` of ``payload`` at the head (see :meth:`Ledger.snapshot`)."""
        at_seq = self._seq
        record = {
            "kind": _SNAPSHOT_KIND,
            "id": f"snapshot-{at_seq}",
            "body": {
                "at_seq": at_seq,
                "state_digest": canonical_hash(payload),
                "state": payload,
            },
        }
        caller, digest = self._prepare(record, "snapshot")
        return self._commit(caller, digest)

    # -- the read path ------------------------------------------------------

    def _lines(self):
        """Yield ``(path, lineno, raw)`` across every segment in chain order."""
        for _index, path in self._root.segment_paths():
            for lineno, raw in _read(path):
                yield path, lineno, raw

    def head(self):
        """Return ``(seq, hash)`` of the last record (see :meth:`Ledger.head`)."""
        return (self._seq, self._head)

    def scan(self, kind=None, since_seq=0):
        """Yield envelopes from disk in seq order (see :meth:`Ledger.scan`)."""
        wanted = kind
        for path, lineno, raw in self._lines():
            envelope = _decode(path, lineno, raw)
            if envelope["seq"] <= since_seq:
                continue
            if wanted is not None and envelope["kind"] != wanted:
                continue
            yield envelope

    def latest_snapshot(self):
        """Return the last ``snapshot`` envelope or ``None`` (see the seam)."""
        if self._latest_snapshot_seq is None:
            return None
        for envelope in self.scan(kind=_SNAPSHOT_KIND, since_seq=self._latest_snapshot_seq - 1):
            return envelope
        return None

    def verify(self):
        """Locate the first bad link across every segment (see :meth:`Ledger.verify`)."""
        expected, prev = 1, GENESIS_HASH
        for path, lineno, raw in self._lines():
            try:
                envelope = _decode(path, lineno, raw)
                intact = (
                    envelope["seq"] == expected
                    and envelope["prev_hash"] == prev
                    and record_hash(prev, envelope) == envelope["hash"]
                )
            except ProductionError:
                intact = False
            if not intact:
                return expected
            expected, prev = expected + 1, envelope["hash"]
        return None


# ---------------------------------------------------------------------------
# Checkpoint — the head-bound cache (§5.8, D15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoint:
    """The atomic cache written last in a tick, bound to the head it projects.

    A cache is never an authority — the ledger fold is — so before it is
    used it must prove its place in the chain: a checkpoint at the ledger
    head is ``current``; a verified ancestor is ``stale`` and gets
    rebuilt; one that is ahead of the ledger or names a hash the chain
    never held refuses, because it was written against a history this
    ledger does not have. Crashing between the ledger barrier and the
    cache replacement therefore leaves a stale cache, which is normal.

    Parameters
    ----------
    release_hash : str
        The release the checkpoint was written under.
    last_tick_at : int or None
        Epoch ms of the last tick started; ``None`` before the first.
    last_completed_tick_at : int or None
        Epoch ms of the last tick that reached a terminal record.
    pending : tuple of str
        Client refs with no terminal order event yet.
    positions_snapshot_at : int or None
        Epoch ms of the position snapshot the checkpoint reflects.
    schema_version : int
        The checkpoint schema, ``>= 1``.
    head_seq : int
        The ledger ``seq`` the checkpoint projects, ``>= 0``.
    head_hash : str
        That record's ``hash`` (``GENESIS_HASH`` when ``head_seq`` is 0).

    Raises
    ------
    ProductionError
        On a malformed field.

    Examples
    --------
    Write the cache after the tick's barrier, and trust it only once the
    ledger vouches for it::

        cp = Checkpoint(release_hash="a" * 64, last_tick_at=1_700_000_000_000,
                        last_completed_tick_at=1_700_000_000_000, pending=["ref-1"],
                        positions_snapshot_at=1_700_000_000_000, schema_version=1,
                        head_seq=3, head_hash=ledger.head()[1])
        cp.write(serve.checkpoint_cache)
        Checkpoint.load(serve.checkpoint_cache).validate_against(ledger)  # 'current'
    """

    release_hash: str
    last_tick_at: int | None
    last_completed_tick_at: int | None
    pending: tuple
    positions_snapshot_at: int | None
    schema_version: int
    head_seq: int
    head_hash: str

    def __post_init__(self):
        """Validate every field, accumulating, and freeze ``pending`` as a tuple."""
        problems = []
        _check_str(problems, "release_hash", self.release_hash)
        for name in ("last_tick_at", "last_completed_tick_at", "positions_snapshot_at"):
            value = getattr(self, name)
            if value is not None:
                check_int_param(problems, name, value, ge=0)
        if isinstance(self.pending, (list, tuple)):
            for position, ref in enumerate(self.pending):
                _check_str(problems, f"pending[{position}]", ref)
            object.__setattr__(self, "pending", tuple(self.pending))
        else:
            problems.append(f"pending must be a list of client refs, got {self.pending!r}")
        check_int_param(problems, "schema_version", self.schema_version, ge=1)
        check_int_param(problems, "head_seq", self.head_seq, ge=0)
        _check_str(problems, "head_hash", self.head_hash)
        if problems:
            raise ProductionError(problems)

    def to_obj(self):
        """Return the JSON-ready form, ``pending`` as a list.

        Returns
        -------
        dict
            The eight fields under their own names.
        """
        obj = {name: getattr(self, name) for name in _CHECKPOINT_KEYS}
        obj["pending"] = list(self.pending)
        return obj

    @classmethod
    def from_obj(cls, obj):
        """Build a checkpoint from its JSON form, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the eight fields of :meth:`to_obj`.

        Returns
        -------
        Checkpoint
            The validated value.

        Raises
        ------
        ProductionError
            On a non-dict, an unknown or missing key, or a malformed field.
        """
        problems = []
        _check_dict(problems, "checkpoint", obj)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, obj, _CHECKPOINT_KEYS, "checkpoint")
        missing = [name for name in _CHECKPOINT_KEYS if name not in obj]
        if missing:
            problems.append(f"checkpoint: missing key(s) {missing}")
        if problems:
            raise ProductionError(problems)
        return cls(**obj)

    def write(self, path):
        """Replace the cache at ``path`` atomically and durably.

        Stage, fsync, rename, fsync the directory: a crash before the
        rename leaves the previous cache readable and no torn file.

        Parameters
        ----------
        path : str
            ``ServeRoot.checkpoint_cache``; its directory must exist.

        Returns
        -------
        None
            Returns once the new cache is durable.
        """
        durable_write_json(path, self.to_obj())

    @classmethod
    def load(cls, path):
        """Read the cache at ``path``.

        Parameters
        ----------
        path : str
            ``ServeRoot.checkpoint_cache``.

        Returns
        -------
        Checkpoint or None
            ``None`` when no cache exists yet — a fresh series is a
            state, not an error.

        Raises
        ------
        ProductionError
            When the file exists but is not JSON or not a checkpoint.
        """
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except ValueError as exc:
            raise ProductionError([f"{path}: checkpoint is not JSON ({exc})"]) from exc
        return cls.from_obj(obj)

    def validate_against(self, ledger):
        """Place this checkpoint in the ledger's chain.

        Parameters
        ----------
        ledger : Ledger
            The chain that is the authority.

        Returns
        -------
        str
            A member of ``vocab.CACHE_STATES``: ``"current"`` at the
            ledger head, ``"stale"`` for a verified ancestor.

        Raises
        ------
        ProductionError
            When the checkpoint is ahead of the ledger head, or its
            ``head_hash`` is not the hash the chain holds at ``head_seq``.
        """
        seq, head = ledger.head()
        if self.head_seq > seq:
            raise ProductionError(
                [f"checkpoint head_seq {self.head_seq} is ahead of the ledger head {seq}"]
            )
        if self.head_seq == seq:
            if self.head_hash == head:
                return _CURRENT
            raise ProductionError(
                [
                    f"checkpoint head_hash {self.head_hash} diverges from the "
                    f"ledger hash {head} at seq {seq}"
                ]
            )
        if self._ancestor_hash(ledger) == self.head_hash:
            return _STALE
        raise ProductionError(
            [
                f"checkpoint head {self.head_seq}/{self.head_hash} is not an "
                f"ancestor of the ledger head {seq}/{head}"
            ]
        )

    def _ancestor_hash(self, ledger):
        """Return the chain's hash at ``head_seq``, or None when no such record exists."""
        if self.head_seq == 0:
            return GENESIS_HASH
        for envelope in ledger.scan(since_seq=self.head_seq - 1):
            return envelope["hash"] if envelope["seq"] == self.head_seq else None
        return None


_CHECKPOINT_KEYS = tuple(field.name for field in fields(Checkpoint))
