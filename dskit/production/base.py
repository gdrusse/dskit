"""Shared mechanics every ``dskit.production`` module reuses (plan §5.0).

One error type, one registry shape, one canonical-bytes recipe, one set of
time helpers — each defined exactly once so that a digest computed in
``records.py`` and a chain hash computed in ``ledger.py`` cannot disagree,
and so that every ``uses`` site in the serve document resolves the same
way. Four rules the module enforces for the whole package:

1. **Errors accumulate.** :class:`ProductionError` carries a LIST of
   problems; validation appends every problem it finds and raises once.
   The re-exported checkers (``_check_str``, ``_check_dict``,
   ``_check_unknown``, ``_raise_if``) come from :mod:`dskit.assets.base`
   by identity — the same idiom :mod:`dskit.onboarding.base` uses — so the
   three packages share one checker vocabulary. Note that ``_raise_if``
   raises ``AssetError``; a production refusal raises
   :class:`ProductionError` itself.
2. **Default-deny has one owner.** :func:`reject_unknown_params` is the
   pipeline's own function, re-exported, never copied.
3. **Identity is canonical bytes.** :func:`canonical_bytes` renders an
   object as sorted-key, compact, ASCII JSON with ``Decimal`` as its string
   and tuples as lists, refusing NaN/Infinity and any other type;
   :func:`canonical_hash` and :func:`record_hash` (the §6 chain link) are
   built on it and nowhere else. Unlike the assets recipe it does NOT strip
   ``notes``: a record is not a config, and two records that differ in any
   field are different records.
4. **Money never touches float.** :func:`reject_money_floats` is the one
   walk of that rule — ``records.py`` validating an opaque venue payload
   and ``ledger.py`` validating a record body call the SAME function, so
   the two cannot disagree about what a money name is or how deep the
   rule reaches.

Instants are epoch-millisecond ``int``s everywhere; :func:`utc_iso` and
:func:`parse_utc_ms` convert to and from ISO-8601 and refuse a naive
stamp, because a stamp with no zone is a guess.

Import cost: stdlib, ``dskit.production.vocab``, and the tier-1 cores of
``dskit.pipeline`` and ``dskit.assets``.
"""

import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Re-exported for sibling modules (not exported): one checker idiom across
# assets, onboarding and production.
from dskit.assets.base import (  # noqa: F401
    check_dict as _check_dict,
    check_str as _check_str,
    check_unknown as _check_unknown,
    raise_if as _raise_if,
)
from dskit.pipeline.base import import_ref, is_class_ref
from dskit.pipeline.node import reject_unknown_params  # noqa: F401  (re-export)
from dskit.production.vocab import MONEY_FIELDS

__all__ = [
    "CREDENTIAL_CHECKS",
    "GENESIS_HASH",
    "ProductionError",
    "Registry",
    "canonical_bytes",
    "canonical_hash",
    "check_credentials",
    "check_digest",
    "now_ms",
    "parse_utc_ms",
    "pin_members",
    "record_hash",
    "reject_money_floats",
    "reject_unknown_params",
    "utc_iso",
]

#: The ``prev_hash`` of the first record of every series (§6): 64 zeros,
#: never a hash. Named once so the ledger, the fold and the checkpoint
#: cannot each spell it.
GENESIS_HASH = "0" * 64

#: A hex sha256 digest, which is what every chain link and record hash is.
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}\Z")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


class ProductionError(ValueError):
    """Every problem an operation has, raised once.

    The same shape as ``dskit.pipeline.base.ConfigError`` and
    ``dskit.assets.base.AssetError`` — a ``ValueError`` carrying the raw
    list — so a caller that already catches ``ValueError`` at a boundary
    keeps working. Validation ACCUMULATES: one raise carrying three
    problems, never three runs discovering one problem each.

    Parameters
    ----------
    problems : list of str
        The individual problems. A lone string is taken as one problem.

    Attributes
    ----------
    problems : list of str
        The problems as given; ``str(err)`` joins them with ``"; "``.

    Examples
    --------
    Accumulate, then raise once::

        problems = ["qty must be a Decimal", "tif must be one of ['ioc', ...]"]
        err = ProductionError(problems)
        str(err)  # 'qty must be a Decimal; tif must be one of [...]'
        err.problems[1]  # "tif must be one of ['ioc', ...]"
    """

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        self.problems = [str(p) for p in problems]
        super().__init__("; ".join(self.problems))


class Registry:
    """The open doorway behind one ``uses`` family (§4.3).

    Every seam module defines its registry at module bottom and registers
    its core kinds on import (``CLOCK_KINDS = Registry("clock", Clock)``),
    so import IS registration. A serve document's ``uses`` is either a
    registered name or a ``pkg.module:Class`` reference — how a child
    supplies its own implementation without editing the package — and
    both must be subclasses of the family's ABC.

    Parameters
    ----------
    family : str
        The family name, used in every refusal (``"clock"``).
    abc : type
        The seam ABC every registered or referenced class must subclass.

    Attributes
    ----------
    family : str
        As given.
    abc : type
        As given.

    Examples
    --------
    A family with one core kind, resolved by name and by reference::

        from abc import ABC, abstractmethod

        class Clock(ABC):
            @abstractmethod
            def now_ms(self): ...

        class WallClock(Clock):
            def now_ms(self):
                return 0

        CLOCK_KINDS = Registry("clock", Clock)
        CLOCK_KINDS.register("wall", WallClock)
        CLOCK_KINDS.resolve("wall") is WallClock  # True
        CLOCK_KINDS.kinds()  # ('wall',)
        "wall" in CLOCK_KINDS  # True
        CLOCK_KINDS.resolve("mypkg.clocks:GpsClock")  # imports and checks the subclass
    """

    def __init__(self, family, abc):
        problems = []
        _check_str(problems, "family", family)
        if not isinstance(abc, type):
            problems.append(f"registry abc must be a class, got {abc!r}")
        if problems:
            raise ProductionError(problems)
        self.family = family
        self.abc = abc
        self._kinds = {}

    def register(self, name, cls):
        """Register ``cls`` under ``name``; a duplicate or a non-subclass refuses.

        Parameters
        ----------
        name : str
            The kind name a document's ``uses`` will spell.
        cls : type
            A subclass of the family's ABC.

        Raises
        ------
        ProductionError
            If ``name`` is not a non-empty string, is already registered,
            or ``cls`` is not a subclass of ``abc``.
        """
        problems = []
        _check_str(problems, f"{self.family} kind name", name)
        if name in self._kinds:
            problems.append(f"{self.family}: kind {name!r} is already registered")
        if not self._in_family(cls):
            problems.append(
                f"{self.family}: {cls!r} is not a subclass of {self.abc.__name__}"
            )
        if problems:
            raise ProductionError(problems)
        self._kinds[name] = cls

    def resolve(self, uses):
        """Return the class a ``uses`` value names.

        Parameters
        ----------
        uses : str
            A registered kind name, or a ``pkg.module:Class`` reference.

        Returns
        -------
        type
            The registered or imported class, a subclass of ``abc``.

        Raises
        ------
        ProductionError
            If the name is unknown, the reference cannot be imported, or
            the referenced object is not a subclass of ``abc``.
        """
        if isinstance(uses, str) and uses in self._kinds:
            return self._kinds[uses]
        if is_class_ref(uses):
            try:
                cls = import_ref(uses)
            except ValueError as exc:
                raise ProductionError([f"{self.family}: {exc}"]) from exc
            if not self._in_family(cls):
                raise ProductionError(
                    [
                        f"{self.family}: {uses!r} is not a subclass of "
                        f"{self.abc.__name__}"
                    ]
                )
            return cls
        raise ProductionError(
            [
                f"{self.family}: unknown kind {uses!r} — registered: "
                f"{list(self.kinds())}; or use a pkg.module:Class reference"
            ]
        )

    def kinds(self):
        """Return the registered kind names as a sorted tuple.

        Returns
        -------
        tuple of str
            Sorted, so a listing is stable across import orders.
        """
        return tuple(sorted(self._kinds))

    def __contains__(self, name):
        """Say whether ``name`` is a registered kind (references never are)."""
        return isinstance(name, str) and name in self._kinds

    def __repr__(self):
        """Render the family and its registered kinds."""
        return f"Registry({self.family!r}, kinds={list(self.kinds())})"

    def _in_family(self, cls):
        return isinstance(cls, type) and issubclass(cls, self.abc)


# ---------------------------------------------------------------------------
# Canonical bytes — the one sha256-canonical idiom (§5.0, §6)
# ---------------------------------------------------------------------------


def _plain(value, path):
    """Return ``value`` in JSON-ready form, or raise naming what is not."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProductionError([f"{path}: non-finite number {value!r} is not JSON"])
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ProductionError([f"{path}: non-finite Decimal {value} is not JSON"])
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProductionError([f"{path}: key {key!r} is not a string"])
            out[key] = _plain(item, f"{path}.{key}")
        return out
    raise ProductionError(
        [f"{path}: {type(value).__name__} is not canonically serializable"]
    )


def canonical_bytes(obj):
    """Render ``obj`` as canonical JSON bytes.

    Sorted keys, ``(",", ":")`` separators, ASCII escapes, NaN/Infinity
    refused; ``Decimal`` as its ``str()`` (so ``Decimal("1.50")`` stays
    ``"1.50"``), tuples as lists. Anything else — a set, a datetime, bytes
    — refuses rather than being guessed at, because a hash some writers
    can produce and others cannot is not an identity. ``notes`` is NOT
    stripped: this is the record recipe, not the config recipe.

    Parameters
    ----------
    obj : dict or list or tuple or scalar
        The object to render; nested containers are rendered recursively.

    Returns
    -------
    bytes
        ASCII bytes of the canonical JSON.

    Raises
    ------
    ProductionError
        Naming the path of the first value that is not canonically
        serializable.
    """
    return json.dumps(
        _plain(obj, "$"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def canonical_hash(obj):
    """Return the hex sha256 of :func:`canonical_bytes` of ``obj``.

    Every ``*_digest`` in the package is this function over a record's
    ``to_obj()`` (or a stated subset) — one recipe, defined once.

    Parameters
    ----------
    obj : dict or list or tuple or scalar
        As for :func:`canonical_bytes`.

    Returns
    -------
    str
        64 lowercase hex characters.

    Raises
    ------
    ProductionError
        As for :func:`canonical_bytes`.
    """
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def check_digest(problems, name, value):
    """Append a problem unless ``value`` is a lowercase hex sha256 digest.

    The one owner of "is this a digest?" — every record field the plan
    spells ``*_digest`` or ``*_hash`` is 64 lowercase hex characters, and
    a module that re-spelled the rule would drift the moment the recipe
    widened. It accumulates rather than raising so a caller can report
    every malformed member of a value object at once.

    Parameters
    ----------
    problems : list of str
        The accumulator the caller raises from.
    name : str
        What the value is, as the refusal should name it.
    value : object
        The candidate digest.

    Returns
    -------
    None
        ``problems`` gains one entry when ``value`` is not 64 lowercase
        hex characters.
    """
    if not isinstance(value, str) or not _HEX_DIGEST.match(value):
        problems.append(f"{name} must be a 64-hex sha256 digest, got {value!r}")


#: The three ids an authenticated control act carries, and the check each
#: owes (D11, D12, §5.5.1). The two digests are 64-hex through
#: :func:`check_digest`, because a record stores DIGESTS: a raw proof
#: handed in where a digest belongs must refuse rather than be written to
#: the chain. One table, because a breaker transition, an adoption and a
#: released hold are the same act with different consequences, and three
#: modules spelling it three ways is how one of them loosens.
CREDENTIAL_CHECKS = {
    "control_request_id": _check_str,
    "principal_digest": check_digest,
    "proof_digest": check_digest,
}


def check_credentials(problems, credentials, where=None, required=True):
    """Append a problem per malformed credential; per missing one too when required.

    Parameters
    ----------
    problems : list of str
        The accumulator the caller raises from.
    credentials : mapping
        Any subset of :data:`CREDENTIAL_CHECKS`; a name it does not carry
        reads as absent.
    where : str or None
        A prefix for the refusals (``"adopt"`` gives ``"adopt:
        proof_digest ..."``); None names the credential alone.
    required : bool
        Whether an absent credential is itself a problem — an
        authenticated act says yes, and the one transition that may be
        the process's own says no.

    Returns
    -------
    None
        ``problems`` gains one entry per credential that is absent when
        required, or present and malformed.
    """
    for name, checked in CREDENTIAL_CHECKS.items():
        spelled = name if where is None else f"{where}: {name}"
        value = credentials.get(name)
        if value is None:
            if required:
                problems.append(f"{spelled} is required: this is an authenticated act (D12)")
        else:
            checked(problems, spelled, value)


def pin_members(what, members, vocabulary, *, exact=False):
    """Refuse at IMPORT when a module's own names stray from a closed vocabulary.

    Closed vocabularies live only in ``vocab.py`` (§5.0), so every module
    that spells one of their members for itself — a dispatch table's keys,
    a tuple of literals — owes a pin that the spelling is still a member.
    One owner, because four modules had written the same two lines and a
    fifth would have written them differently.

    Parameters
    ----------
    what : str
        What is being pinned, as the refusal should name it (``"breaker.py's
        CAUSE_TARGETS values"``).
    members : iterable
        The names the module spells. A mapping contributes its keys.
    vocabulary : iterable
        The closed set from ``vocab.py`` they must lie within.
    exact : bool, optional
        When True the two sets must be EQUAL — the pin a dispatch table
        keyed by a vocabulary owes, since a missing key is a silently
        unhandled member. Default False: membership only.

    Returns
    -------
    object
        ``members``, so a table can be pinned where it is bound.

    Raises
    ------
    ProductionError
        Naming the strays, or both differences under ``exact``.
    """
    spelled, closed = set(members), set(vocabulary)
    stray = sorted(spelled - closed)
    missing = sorted(closed - spelled) if exact else []
    faults = []
    if stray:
        faults.append(f"{what} strays outside its vocabulary: {stray}")
    if missing:
        faults.append(f"{what} does not cover {missing}")
    if faults:
        raise ProductionError(faults)
    return members


def record_hash(prev_hash, envelope):
    """Return the §6 chain link ``sha256(prev_hash + canonical(envelope − hash))``.

    Any ``hash`` key already on ``envelope`` is excluded, so a record read
    back from the ledger re-hashes to the value it carries — which is what
    lets ``verify()`` be right. The genesis link is :data:`GENESIS_HASH`.

    Parameters
    ----------
    prev_hash : str
        The previous record's hash, or :data:`GENESIS_HASH` — 64 hex chars.
    envelope : dict
        The full envelope (body plus the ledger-assigned fields), with or
        without its ``hash``.

    Returns
    -------
    str
        64 lowercase hex characters.

    Raises
    ------
    ProductionError
        If ``prev_hash`` is not a hex sha256, ``envelope`` is not a dict,
        or the envelope is not canonically serializable.
    """
    problems = []
    if not isinstance(prev_hash, str) or not _HEX_DIGEST.match(prev_hash):
        problems.append(f"prev_hash must be a hex sha256 digest, got {prev_hash!r}")
    _check_dict(problems, "envelope", envelope)
    if problems:
        raise ProductionError(problems)
    body = {key: value for key, value in envelope.items() if key != "hash"}
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_bytes(body)).hexdigest()


# ---------------------------------------------------------------------------
# Money — one rule, one owner (§5.4, §5.8)
# ---------------------------------------------------------------------------


def reject_money_floats(problems, value, path, money=False):
    """Append a problem for every float under a money name in ``value``.

    The one owner of the money rule: a ``float`` under any
    :data:`~dskit.production.vocab.MONEY_FIELDS` name is refused at any
    depth of an opaque payload, and a list under such a name INHERITS it
    — ``{"price": [1.0]}`` is a price that is a float. Every other float
    is a dimensionless ratio (``confidence``, a monitor ``statistic``)
    and stays legal. ``records.py`` and ``ledger.py`` both call this
    rather than each walking a payload, so a payload a value object
    accepts is a payload the chain accepts.

    Parameters
    ----------
    problems : list of str
        Accumulator; one entry per offending float, naming its path.
    value : object
        The payload to walk: a dict, a sequence, or a scalar.
    path : str
        Where ``value`` sits, for the messages (``"record.body"``).
    money : bool
        Whether ``value`` itself already sits under a money name. The
        recursion sets it; a caller starting at a payload root leaves it
        false.

    Returns
    -------
    None
        Problems are appended; the caller raises once.
    """
    if isinstance(value, float):
        if money:
            problems.append(f"{path}: money never touches float, got {value!r}")
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_money_floats(problems, item, f"{path}.{key}", key in MONEY_FIELDS)
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            reject_money_floats(problems, item, f"{path}[{position}]", money)


# ---------------------------------------------------------------------------
# Instants — epoch milliseconds, UTC, never naive
# ---------------------------------------------------------------------------


def now_ms():
    """Return the wall clock as epoch milliseconds.

    Every class needing time takes an injected ``Clock`` and never calls
    this; it exists for the places that stamp provenance outside the tick
    loop (a series genesis file, a release manifest).

    Returns
    -------
    int
        Milliseconds since the Unix epoch, truncated.
    """
    return int(time.time() * 1000)


def utc_iso(ms):
    """Render an epoch-millisecond instant as an ISO-8601 UTC string.

    Parameters
    ----------
    ms : int
        Milliseconds since the Unix epoch. Never a float or a bool.

    Returns
    -------
    str
        e.g. ``"2026-09-05T00:00:00.123+00:00"`` — millisecond precision,
        explicit zero offset, so :func:`parse_utc_ms` round-trips it.

    Raises
    ------
    ProductionError
        If ``ms`` is not an int, or is outside the datetime range.
    """
    if isinstance(ms, bool) or not isinstance(ms, int):
        raise ProductionError([f"an instant is an epoch-ms int, got {ms!r}"])
    try:
        stamp = _EPOCH + ms * _ONE_MS
    except OverflowError as exc:
        raise ProductionError([f"{ms} ms is outside the representable range"]) from exc
    return stamp.isoformat(timespec="milliseconds")


def parse_utc_ms(text):
    """Parse an ISO-8601 instant WITH a zone into epoch milliseconds.

    A stamp with no zone is a guess, and a guess in a ledger is a lie — a
    naive stamp refuses rather than being read as UTC. Any explicit
    offset is accepted and normalised (``+01:00`` and ``Z`` both work).

    Parameters
    ----------
    text : str
        An ISO-8601 date-time with an offset or ``Z``.

    Returns
    -------
    int
        Milliseconds since the Unix epoch, floored.

    Raises
    ------
    ProductionError
        If ``text`` is not a string, does not parse, or carries no zone.
    """
    if not isinstance(text, str) or not text:
        raise ProductionError([f"expected an ISO-8601 instant string, got {text!r}"])
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProductionError([f"{text!r} is not an ISO-8601 instant: {exc}"]) from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ProductionError(
            [f"{text!r} carries no zone — an instant must state its offset or Z"]
        )
    return (stamp - _EPOCH) // _ONE_MS
