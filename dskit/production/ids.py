"""Deterministic identifiers, allocated before a tick starts (§5.13, §6, D20).

Every identifier a serve process writes — the tick id, each leg's id and
plan id, the client reference a venue sees — is a function of *semantic*
inputs: the release hash, the tick instant, the leg index, the attempt.
Never the wall clock, never a counter, never the ledger's ``seq``, never
the order the process happened to ask. That property is what lets
``replay`` re-derive the same ids from the tape (D20) and what lets a
recovering process query a client reference it never stored (D12).

The seam is :class:`IdSource`: four abstract allocation hooks, so an
incomplete source refuses to construct (§5.15), and one CONCRETE method,
``flatten_client_ref``, because D12's formula ``H("flatten-v1",
release_hash, reduction_request_id, index, reduction_intent_digest)`` has
exactly one owner — a live source, a replaying source and a child's own
subclass must all answer the same reference for the same reduction. Core
ships two sources: :class:`ReleaseIdSource` derives ids from the release
it was built with, and :class:`RecordedIdSource` replays a recorded tape
entry by entry, refusing the first call that diverges from the recording.

Each derivation is ``base.canonical_hash`` over a tagged tuple. The tag
(``tick-v1``, ``leg-v1``, ``plan-v1``, ``client-v1``, ``flatten-v1``) is
the first term, so the same numbers under two methods never collide, and
the release hash is the second, so two releases never share an id. This
module imports no clock, no random source and no process token — an AST
test pins that, and a behavioural one asks for every id again with the
wall clock made to explode.
"""

from abc import ABC, abstractmethod

from dskit.production.base import ProductionError, canonical_hash

__all__ = ["IdSource", "RecordedIdSource", "ReleaseIdSource"]

#: The first term of every derivation: one tag per recipe, so the same
#: numbers under two methods never produce one id. ``flatten-v1`` is D12's
#: and is part of the contract a recovering process re-derives.
_TICK_TAG = "tick-v1"
_LEG_TAG = "leg-v1"
_PLAN_TAG = "plan-v1"
_CLIENT_TAG = "client-v1"
_FLATTEN_TAG = "flatten-v1"


def _text(value, what):
    """Return ``value`` if it is a non-empty str, else refuse."""
    if not isinstance(value, str) or not value:
        raise ProductionError([f"{what} must be a non-empty str, got {value!r}"])
    return value


def _natural(value, what):
    """Return ``value`` if it is a non-negative int (a bool is not one), else refuse."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProductionError([f"{what} must be a non-negative int, got {value!r}"])
    return value


class IdSource(ABC):
    """The id seam: four allocations a tick makes, plus D12's flatten reference (§5.13).

    A source answers the tick id for an instant, and the leg id, plan id
    and client reference for a leg of a tick, from semantic inputs alone
    (D20). The four allocation hooks are abstract; ``flatten_client_ref``
    is concrete because its recipe has one owner, whatever source is
    injected.

    Examples
    --------
    The smallest complete source — one that spells its ids rather than
    hashing them — constructs, and inherits the flatten reference::

        class Spelled(IdSource):
            def next_tick_id(self, tick_at_ms):
                return f"tick-{tick_at_ms}"

            def leg_id(self, tick_id, index):
                return f"leg-{tick_id}-{index}"

            def plan_id(self, tick_id, leg_index):
                return f"plan-{tick_id}-{leg_index}"

            def client_ref(self, tick_id, leg_index, attempt):
                return f"ref-{tick_id}-{leg_index}-{attempt}"

        source = Spelled()
        source.next_tick_id(1_767_268_800_000)  # "tick-1767268800000"
        len(source.flatten_client_ref("a" * 64, "req-1", 0, "e" * 64))  # 64
    """

    @abstractmethod
    def next_tick_id(self, tick_at_ms):
        """Return the id of the tick due at ``tick_at_ms``.

        Parameters
        ----------
        tick_at_ms : int
            The tick instant, epoch milliseconds (D6's ``tick_at``).

        Returns
        -------
        str
            The tick id; the same instant always answers the same id.
        """

    @abstractmethod
    def leg_id(self, tick_id, index):
        """Return the id of leg ``index`` of tick ``tick_id``.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            The leg id.
        """

    @abstractmethod
    def plan_id(self, tick_id, leg_index):
        """Return the ``decision_plan`` id of leg ``leg_index`` of tick ``tick_id``.

        Parameters
        ----------
        tick_id : str
            The tick the plan belongs to.
        leg_index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            The plan id.
        """

    @abstractmethod
    def client_ref(self, tick_id, leg_index, attempt):
        """Return the client reference of one submit attempt of a model leg.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        leg_index : int
            The leg's zero-based position within the tick.
        attempt : int
            The zero-based attempt; a retry is a new attempt and therefore
            a new reference, never a blind resend of the same one (D20).

        Returns
        -------
        str
            The client reference the venue will see.
        """

    def flatten_client_ref(self, release_hash, request_id, index, reduction_intent_digest):
        """Return D12's client reference for one intent of a reduction plan.

        ``canonical_hash(("flatten-v1", release_hash, request_id, index,
        reduction_intent_digest))`` — the release is the reduction plan's,
        an argument rather than anything this source holds, because a
        recovering process (possibly a different release) must re-derive
        the reference from the ledger row alone before it may query or
        resume the reserved intent.

        Parameters
        ----------
        release_hash : str
            The release the ``ReductionPlan`` is bound to.
        request_id : str
            The ``reduction_request_id`` of the maker-signed flatten request.
        index : int
            The intent's zero-based position within the plan.
        reduction_intent_digest : str
            The signed intent's digest (§5.4).

        Returns
        -------
        str
            64 lowercase hex characters.

        Raises
        ------
        ProductionError
            If a term is not the type the recipe hashes — an empty or
            non-str hash, id or digest, or a non-int index.
        """
        return canonical_hash(
            (
                _FLATTEN_TAG,
                _text(release_hash, "release_hash"),
                _text(request_id, "request_id"),
                _natural(index, "index"),
                _text(reduction_intent_digest, "reduction_intent_digest"),
            )
        )


class ReleaseIdSource(IdSource):
    """The live source: ids derived from the release, the instant, the index and the attempt (D20).

    Parameters
    ----------
    release_hash : str
        The release every id is bound to; two releases never share an id.

    Raises
    ------
    ProductionError
        If ``release_hash`` is not a non-empty str.

    Examples
    --------
    The four allocations one tick makes, each answered the same way however
    often or in whatever order it is asked::

        source = ReleaseIdSource("a" * 64)
        tick_id = source.next_tick_id(1_767_268_800_000)
        leg_id = source.leg_id(tick_id, 0)
        plan_id = source.plan_id(tick_id, 0)
        client_ref = source.client_ref(tick_id, 0, 0)
        len(tick_id)  # 64
        source.next_tick_id(1_767_268_800_000) == tick_id  # True
    """

    def __init__(self, release_hash):
        self._release_hash = _text(release_hash, "release_hash")

    def next_tick_id(self, tick_at_ms):
        """Return ``H("tick-v1", release_hash, tick_at_ms)``.

        Parameters
        ----------
        tick_at_ms : int
            The tick instant, epoch milliseconds.

        Returns
        -------
        str
            64 lowercase hex characters.

        Raises
        ------
        ProductionError
            If ``tick_at_ms`` is not a non-negative int.
        """
        return canonical_hash((_TICK_TAG, self._release_hash, _natural(tick_at_ms, "tick_at_ms")))

    def leg_id(self, tick_id, index):
        """Return ``H("leg-v1", release_hash, tick_id, index)``.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            64 lowercase hex characters.

        Raises
        ------
        ProductionError
            If ``tick_id`` is not a non-empty str or ``index`` not a
            non-negative int.
        """
        return canonical_hash(
            (_LEG_TAG, self._release_hash, _text(tick_id, "tick_id"), _natural(index, "index"))
        )

    def plan_id(self, tick_id, leg_index):
        """Return ``H("plan-v1", release_hash, tick_id, leg_index)``.

        Parameters
        ----------
        tick_id : str
            The tick the plan belongs to.
        leg_index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            64 lowercase hex characters.

        Raises
        ------
        ProductionError
            If ``tick_id`` is not a non-empty str or ``leg_index`` not a
            non-negative int.
        """
        return canonical_hash(
            (
                _PLAN_TAG,
                self._release_hash,
                _text(tick_id, "tick_id"),
                _natural(leg_index, "leg_index"),
            )
        )

    def client_ref(self, tick_id, leg_index, attempt):
        """Return ``H("client-v1", release_hash, tick_id, leg_index, attempt)``.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        leg_index : int
            The leg's zero-based position within the tick.
        attempt : int
            The zero-based submit attempt.

        Returns
        -------
        str
            64 lowercase hex characters.

        Raises
        ------
        ProductionError
            If ``tick_id`` is not a non-empty str or an index is not a
            non-negative int.
        """
        return canonical_hash(
            (
                _CLIENT_TAG,
                self._release_hash,
                _text(tick_id, "tick_id"),
                _natural(leg_index, "leg_index"),
                _natural(attempt, "attempt"),
            )
        )


#: The tape may record only the four allocation hooks — never the flatten
#: reference, which every source derives rather than allocates.
_ALLOCATIONS = frozenset(IdSource.__abstractmethods__)


def _checked_tape(tape):
    """Return ``tape`` as a tuple of ``(method, args, id)`` triples, refusing a broken recording."""
    if isinstance(tape, (str, bytes)) or not isinstance(tape, (list, tuple)):
        raise ProductionError(
            [f"a tape is a sequence of (method, args, id) triples, got {type(tape).__name__}"]
        )
    problems, entries = [], []
    for position, entry in enumerate(tape):
        where = f"tape[{position}]"
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            problems.append(f"{where}: expected a (method, args, id) triple, got {entry!r}")
            continue
        method, args, value = entry
        before = len(problems)
        if not isinstance(method, str) or method not in _ALLOCATIONS:
            problems.append(f"{where}: {method!r} is not one of {sorted(_ALLOCATIONS)}")
        if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
            problems.append(f"{where}: args must be a sequence, got {args!r}")
        if not isinstance(value, str) or not value:
            problems.append(f"{where}: the recorded id must be a non-empty str, got {value!r}")
        if len(problems) == before:
            entries.append((method, tuple(args), value))
    if problems:
        raise ProductionError(problems)
    return tuple(entries)


class RecordedIdSource(IdSource):
    """The replay source: the recorded tape's ids, in the recorded order (D20).

    Replay allocates nothing new. Each allocation consumes the next tape
    entry and must ask exactly what the recording asked — the same method
    with the same positional arguments — or the replay has diverged, and
    D20's parity claim is precisely that it did not. A call past the end
    of the tape refuses for the same reason. ``flatten_client_ref`` is
    derived, not recorded, so it consumes no entry.

    Parameters
    ----------
    tape : sequence of (str, sequence, str)
        The ``(method, args, id)`` triples a recorded ledger yields, in
        order. ``args`` compares positionally, so a tape read back from
        JSON (lists) replays a tape recorded from tuples.

    Raises
    ------
    ProductionError
        At construction, if any entry is not a triple, names a method that
        is not one of the four allocations, carries non-sequence args, or
        records an id that is not a non-empty str.

    Examples
    --------
    A one-entry tape replays its tick id, then refuses a second ask::

        tape = (("next_tick_id", (1_767_268_800_000,), "c" * 64),)
        source = RecordedIdSource(tape)
        source.next_tick_id(1_767_268_800_000)  # "cccc…" (the recorded id)
        source.next_tick_id(1_767_268_800_000)
        # -> ProductionError: the tape is exhausted
    """

    def __init__(self, tape):
        self._tape = _checked_tape(tape)
        self._cursor = 0

    def _replay(self, method, args):
        """Return the next recorded id, refusing any divergence from the tape."""
        position = self._cursor
        if position >= len(self._tape):
            raise ProductionError(
                [f"replay asked {method}{args!r} but the tape is exhausted after {position} entries"]
            )
        recorded_method, recorded_args, value = self._tape[position]
        if (recorded_method, recorded_args) != (method, tuple(args)):
            raise ProductionError(
                [
                    f"replay asked {method}{args!r} at entry {position} but the tape "
                    f"recorded {recorded_method}{recorded_args!r}"
                ]
            )
        self._cursor = position + 1
        return value

    def next_tick_id(self, tick_at_ms):
        """Return the recorded tick id for ``tick_at_ms``.

        Parameters
        ----------
        tick_at_ms : int
            The tick instant the recording asked about.

        Returns
        -------
        str
            The recorded id.

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("next_tick_id", (tick_at_ms,))

    def leg_id(self, tick_id, index):
        """Return the recorded leg id.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            The recorded id.

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("leg_id", (tick_id, index))

    def plan_id(self, tick_id, leg_index):
        """Return the recorded plan id.

        Parameters
        ----------
        tick_id : str
            The tick the plan belongs to.
        leg_index : int
            The leg's zero-based position within the tick.

        Returns
        -------
        str
            The recorded id.

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("plan_id", (tick_id, leg_index))

    def client_ref(self, tick_id, leg_index, attempt):
        """Return the recorded client reference.

        Parameters
        ----------
        tick_id : str
            The tick the leg belongs to.
        leg_index : int
            The leg's zero-based position within the tick.
        attempt : int
            The zero-based submit attempt.

        Returns
        -------
        str
            The recorded reference.

        Raises
        ------
        ProductionError
            If the next tape entry is not this call, or the tape is exhausted.
        """
        return self._replay("client_ref", (tick_id, leg_index, attempt))
