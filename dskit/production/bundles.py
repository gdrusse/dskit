"""The seven collaborator bundles the loop, the tick and the leg are built from (§5.13, §5.16).

``ServeLoop``, ``Tick`` and ``LegPipeline`` take two values plus seven
frozen bundles rather than thirty positional arguments. The bundles are
their own module because ``LegPipeline`` takes six of them while
``compose.py`` builds all seven — putting them in either module would make
the §10 build order cyclic.

That cycle is why a bundle validates PRESENCE ONLY. Checking that
``Safety.breaker`` is a ``Breaker`` would import ``breaker.py``, and doing
that for every member would import most of the package back into the
module that exists to break the cycle. So a member is refused only when it
is ``None`` — a falsy collaborator such as an empty guard chain is present
— every absent member is reported in one raise, and the only production
module imported here is ``base``. Type conformance is proved where the
collaborators are built (``compose.py``) and where they are used.

The member ORDER is the constructor contract: ``compose.bundles_for``
returns the seven positionally and ``LegPipeline`` takes six of them, so
a member that moved would silently swap two collaborators. The order is
§5.16's table, restated independently by ``tests/production/test_bundles.py``.

:class:`Invocation` is the one value object here — the frozen ``{armed,
env_release_hash, once, max_ticks}`` that ``__main__`` builds from
``--armed``, ``DSKIT_PRODUCTION_ARM``, ``--once`` and ``--max-ticks`` and
that ``Safety`` carries, so ``Arming.check_conjunction`` (§5.6) can see
all three of its inputs. Its knobs are stdlib-typed, so it does check
them.
"""

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from types import MappingProxyType

from dskit.pipeline.event_wire import (
    DATASET_AUTHORIZATION_EVENT_SCHEMAS,
    RAW_EVENT_FIELDS,
)
from dskit.production.base import (
    ProductionError,
    canonical_bytes as _canonical_bytes,
    canonical_hash as _canonical_hash,
    check_digest,
)

__all__ = [
    "CapturedReplayTape",
    "Data",
    "Decision",
    "Execution",
    "Invocation",
    "Observability",
    "Recording",
    "ReplayTape",
    "Safety",
    "Schedule",
    "compose_replay_tape",
    "verify_causal_order",
]

CAPTURED_REPLAY_TAPE_SCHEMA = "dskit.captured-replay-tape/v1"
CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA = "dskit.event-envelope/v2"

_PLACEHOLDER = "0" * 64
_FIELDS = (
    "schema_version",
    "event_envelope_schema",
    "data_capture_root",
    "data_captured_receipt",
    "source_rank_policy_sha256",
    "envelope_count",
    "ordered_envelope_digests",
    "ordered_envelopes_sha256",
    "tape_digest",
)
_DIGEST_FIELDS = (
    "data_capture_root",
    "data_captured_receipt",
    "source_rank_policy_sha256",
)


def _is_digest(value):
    """Return True only for a non-placeholder 64-hex digest (via base.check_digest)."""
    if not isinstance(value, str) or value == _PLACEHOLDER or value == "self":
        return False
    problems = []
    check_digest(problems, "digest", value)
    return not problems


def _check_tape(value):
    """Accumulate every refusal for a ``CapturedReplayTape.v1`` object."""
    problems = []
    unknown = set(value) - set(_FIELDS)
    missing = set(_FIELDS) - set(value)
    for name in sorted(unknown):
        problems.append(f"unknown captured-replay-tape field {name!r}")
    for name in sorted(missing):
        problems.append(f"missing captured-replay-tape field {name!r}")
    if value.get("schema_version") != CAPTURED_REPLAY_TAPE_SCHEMA:
        problems.append(
            f"schema_version must be {CAPTURED_REPLAY_TAPE_SCHEMA!r}"
        )
    if value.get("event_envelope_schema") != CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA:
        problems.append(
            f"event_envelope_schema must be {CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA!r}"
        )
    for name in _DIGEST_FIELDS:
        if not _is_digest(value.get(name)):
            problems.append(f"{name} must be a 64-hex sha256 digest, not a placeholder")
    count = value.get("envelope_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        problems.append("envelope_count must be a non-negative int")
    envelopes = value.get("ordered_envelope_digests")
    if not isinstance(envelopes, list):
        problems.append("ordered_envelope_digests must be a list")
    else:
        for index, digest in enumerate(envelopes):
            if not _is_digest(digest):
                problems.append(
                    f"ordered_envelope_digests[{index}] must be a 64-hex sha256 digest"
                )
        if isinstance(count, int) and not isinstance(count, bool) and count != len(envelopes):
            problems.append("envelope_count must equal len(ordered_envelope_digests)")
        if value.get("ordered_envelopes_sha256") != _canonical_hash(envelopes):
            problems.append("ordered_envelopes_sha256 does not match the digest list")
    try:
        source = {key: item for key, item in value.items() if key != "tape_digest"}
        if value.get("tape_digest") != _canonical_hash(source):
            problems.append("tape_digest does not match the recomputed digest")
    except ProductionError:
        # A non-serializable member is already reported above; the digest
        # cannot be recomputed over it, so there is nothing more to say here.
        pass
    return problems


class CapturedReplayTape:
    """The default-deny ``dskit.captured-replay-tape/v1`` inner-manifest codec.

    The bytes a ``ReplayTapeManifestProducer`` publishes are one exact
    nine-field object. This value pins it: the two derived digests
    (``ordered_envelopes_sha256`` and ``tape_digest``) are recomputed from
    canonical bytes, so the replay consumer later trusts only bytes that
    verify — never a caller-supplied digest string. It has no public
    constructor; ``parse`` and the private factory are the only mints.

    Examples
    --------
    A value is parsed, never constructed::

        tape = CapturedReplayTape.parse(canonical_bytes)
        tape.tape_digest  # 64 lowercase hex characters
    """

    __slots__ = ("_value", "__weakref__")

    def __new__(cls, *args, **kwargs):
        """Refuse direct construction: parse the bytes or use the factory."""
        raise TypeError("CapturedReplayTape is parsed, never constructed")

    def __setattr__(self, name, value):
        """Refuse mutation: a captured tape is immutable."""
        raise AttributeError("CapturedReplayTape is frozen")

    def __copy__(self):
        """Refuse a shallow copy of the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __deepcopy__(self, memo):
        """Refuse a deep copy of the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __getstate__(self):
        """Refuse pickle state for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __reduce__(self):
        """Refuse pickle reduction for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    def __reduce_ex__(self, protocol):
        """Refuse protocol pickle reduction for the opaque value."""
        raise TypeError("CapturedReplayTape is opaque")

    @classmethod
    def _from_value(cls, value):
        """Build an immutable instance from an already-validated object."""
        frozen = MappingProxyType(
            dict(value, ordered_envelope_digests=tuple(value["ordered_envelope_digests"]))
        )
        self = object.__new__(cls)
        object.__setattr__(self, "_value", frozen)
        return self

    @classmethod
    def parse(cls, raw):
        """Parse canonical ``CapturedReplayTape.v1`` bytes, refusing everything else.

        Parameters
        ----------
        raw : bytes
            The exact canonical JSON bytes of the nine-field object.

        Returns
        -------
        CapturedReplayTape
            The immutable, verified value.

        Raises
        ------
        ProductionError
            Naming every field that is unknown, missing, mistyped, a
            placeholder digest, or a mismatched recomputed digest, and any
            bytes that are not the canonical form.
        """
        if not isinstance(raw, bytes):
            raise ProductionError(["CapturedReplayTape.parse takes canonical bytes"])
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            raise ProductionError(["CapturedReplayTape bytes are not canonical JSON"])
        if not isinstance(value, dict):
            raise ProductionError(["CapturedReplayTape must decode to an object"])
        problems = _check_tape(value)
        if not problems and _canonical_bytes(value) != raw:
            problems = ["CapturedReplayTape bytes are not canonical"]
        if problems:
            raise ProductionError(problems)
        return cls._from_value(value)

    @classmethod
    def _build(cls, data_capture_root, data_captured_receipt,
               source_rank_policy_sha256, ordered_envelope_digests):
        """Derive the canonical value from its five supplied inputs."""
        if not isinstance(ordered_envelope_digests, (list, tuple)):
            raise ProductionError(["ordered_envelope_digests must be a list"])
        envelopes = list(ordered_envelope_digests)
        value = {
            "schema_version": CAPTURED_REPLAY_TAPE_SCHEMA,
            "event_envelope_schema": CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA,
            "data_capture_root": data_capture_root,
            "data_captured_receipt": data_captured_receipt,
            "source_rank_policy_sha256": source_rank_policy_sha256,
            "envelope_count": len(envelopes),
            "ordered_envelope_digests": envelopes,
        }
        value["ordered_envelopes_sha256"] = _canonical_hash(envelopes)
        source = {key: item for key, item in value.items() if key != "tape_digest"}
        value["tape_digest"] = _canonical_hash(source)
        problems = _check_tape(value)
        if problems:
            raise ProductionError(problems)
        return cls._from_value(value)

    @property
    def schema_version(self):
        """The fixed ``dskit.captured-replay-tape/v1`` literal."""
        return self._value["schema_version"]

    @property
    def event_envelope_schema(self):
        """The fixed ``dskit.event-envelope/v2`` literal."""
        return self._value["event_envelope_schema"]

    @property
    def data_capture_root(self):
        """The parent data capture root digest."""
        return self._value["data_capture_root"]

    @property
    def data_captured_receipt(self):
        """The manifest producer's parent CAPTURED receipt digest."""
        return self._value["data_captured_receipt"]

    @property
    def source_rank_policy_sha256(self):
        """The pre-document source-rank policy digest."""
        return self._value["source_rank_policy_sha256"]

    @property
    def envelope_count(self):
        """The number of ordered envelopes."""
        return self._value["envelope_count"]

    @property
    def ordered_envelope_digests(self):
        """The ordered envelope digests, as an immutable tuple."""
        return tuple(self._value["ordered_envelope_digests"])

    @property
    def ordered_envelopes_sha256(self):
        """The recomputed digest of the ordered envelope digest array."""
        return self._value["ordered_envelopes_sha256"]

    @property
    def tape_digest(self):
        """The recomputed digest of the object with ``tape_digest`` omitted."""
        return self._value["tape_digest"]

    def to_obj(self):
        """Return a plain copy of the nine-field object."""
        value = dict(self._value)
        value["ordered_envelope_digests"] = list(value["ordered_envelope_digests"])
        return value

    def canonical_bytes(self):
        """Return the exact canonical JSON bytes of the value."""
        return _canonical_bytes(self.to_obj())


# ---------------------------------------------------------------------------
# ADR-0145 -- bounded synthetic `dskit.event-envelope/v2` causal-order
# verification (P7 EventEnvelope gate).
#
# This is a DIFFERENT "envelope" than `dskit/pipeline/trust.py`'s existing
# G1/G2-grant preflight envelope concept (`_hs_validate_envelope`,
# `_HS_ENVELOPES`, `_verify_one_signed`'s `envelope` parameter -- a signed
# issuer_role/key_usage/not_before_ms object). The two share only the
# English word; they have no shared fields, no shared code path, and this
# module never imports `trust.py`.
#
# The 15 keys below are ADR-0130 Decision point 4's four-field per-envelope
# projection (`schema_version`, `source_id`, `source_rank`,
# `source_rank_policy_sha256`) UNION ADR-0132's six-field `dskit.raw-event/v1`
# shape (`schema_version`, `source_id`, `event_id`, `source_sequence`,
# `availability_ms`, `payload_sha256`) -- an 8-field union after the 2-field
# overlap -- PLUS 7 new fields (`exchange_ms`, `receive_ms`,
# `source_provenance_tag`, `source_timezone_tag`, `correction_position`,
# `corrects_event_id`, `prior_envelope_sha256`). ADR-0130's own four-field
# projection has no prior implementation anywhere in this codebase (only its
# schema-name constant, above, existed before this ADR): the parser below is
# that projection's first implementation, not an extension of working code.
# ---------------------------------------------------------------------------

_EVENT_ENVELOPE_FIELDS = (
    "schema_version",
    "source_id",
    "event_id",
    "source_sequence",
    "payload_sha256",
    "availability_ms",
    "exchange_ms",
    "receive_ms",
    "source_provenance_tag",
    "source_timezone_tag",
    "source_rank",
    "source_rank_policy_sha256",
    "correction_position",
    "corrects_event_id",
    "prior_envelope_sha256",
)


def _check_event_envelope(value):
    """Accumulate every refusal for one closed ``dskit.event-envelope/v2`` object.

    Mirrors ``_check_tape``'s unknown/missing accumulation style (ADR-0145
    Decision point 1). ``source_id``/``event_id``/``source_sequence``/
    ``payload_sha256``/``availability_ms``/``source_rank``/
    ``source_rank_policy_sha256`` are carried verbatim from ADR-0130/0132;
    ``exchange_ms``/``receive_ms``/``source_provenance_tag``/
    ``source_timezone_tag``/``correction_position``/``corrects_event_id``/
    ``prior_envelope_sha256`` are new to this ADR.
    """
    if not isinstance(value, dict):
        return ["event-envelope/v2 must decode to an object"]
    problems = []
    unknown = set(value) - set(_EVENT_ENVELOPE_FIELDS)
    missing = set(_EVENT_ENVELOPE_FIELDS) - set(value)
    for name in sorted(unknown):
        problems.append(f"unknown event-envelope field {name!r}")
    for name in sorted(missing):
        problems.append(f"missing event-envelope field {name!r}")
    if value.get("schema_version") != CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA:
        problems.append(
            f"schema_version must be {CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA!r}"
        )
    source_id = value.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        problems.append("source_id must be a nonempty str")
    event_id = value.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        problems.append("event_id must be a nonempty str")
    source_sequence = value.get("source_sequence")
    if (
        isinstance(source_sequence, bool)
        or not isinstance(source_sequence, int)
        or source_sequence < 0
    ):
        problems.append("source_sequence must be a non-negative int")
    check_digest(problems, "payload_sha256", value.get("payload_sha256"))
    availability_ms = value.get("availability_ms")
    if isinstance(availability_ms, bool) or not isinstance(availability_ms, int):
        problems.append("availability_ms must be an int")
    exchange_ms = value.get("exchange_ms")
    if (
        isinstance(exchange_ms, bool)
        or not isinstance(exchange_ms, int)
        or exchange_ms < 0
    ):
        problems.append("exchange_ms must be a non-negative int")
    receive_ms = value.get("receive_ms")
    if (
        isinstance(receive_ms, bool)
        or not isinstance(receive_ms, int)
        or receive_ms < 0
    ):
        problems.append("receive_ms must be a non-negative int")
    if (
        isinstance(exchange_ms, int) and not isinstance(exchange_ms, bool)
        and isinstance(receive_ms, int) and not isinstance(receive_ms, bool)
        and receive_ms < exchange_ms
    ):
        problems.append("receive_ms must be >= exchange_ms")
    provenance = value.get("source_provenance_tag")
    if not isinstance(provenance, str) or not provenance:
        problems.append("source_provenance_tag must be a nonempty str")
    timezone_tag = value.get("source_timezone_tag")
    if not isinstance(timezone_tag, str) or not timezone_tag:
        problems.append("source_timezone_tag must be a nonempty str")
    source_rank = value.get("source_rank")
    if (
        isinstance(source_rank, bool)
        or not isinstance(source_rank, int)
        or source_rank < 0
    ):
        problems.append("source_rank must be a non-negative int")
    check_digest(problems, "source_rank_policy_sha256", value.get("source_rank_policy_sha256"))
    correction_position = value.get("correction_position")
    position_ok = (
        isinstance(correction_position, int)
        and not isinstance(correction_position, bool)
        and correction_position >= 0
    )
    if not position_ok:
        problems.append("correction_position must be a non-negative int")
    corrects_event_id = value.get("corrects_event_id")
    prior_envelope_sha256 = value.get("prior_envelope_sha256")
    if position_ok and correction_position == 0:
        if corrects_event_id is not None:
            problems.append("corrects_event_id must be null when correction_position == 0")
        if prior_envelope_sha256 is not None:
            problems.append("prior_envelope_sha256 must be null when correction_position == 0")
    elif position_ok:
        if not isinstance(corrects_event_id, str) or not corrects_event_id:
            problems.append(
                "corrects_event_id must be a nonempty str when correction_position > 0"
            )
        if prior_envelope_sha256 is None:
            problems.append(
                "prior_envelope_sha256 must be a sha256 digest when correction_position > 0"
            )
        else:
            check_digest(problems, "prior_envelope_sha256", prior_envelope_sha256)
    else:
        if corrects_event_id is not None and not isinstance(corrects_event_id, str):
            problems.append("corrects_event_id must be a str or null")
        if prior_envelope_sha256 is not None:
            check_digest(problems, "prior_envelope_sha256", prior_envelope_sha256)
    return problems


def _parse_event_envelope(raw):
    """Parse one closed ``dskit.event-envelope/v2`` object, refusing everything else.

    Not shipped as a caller-facing minting API beyond fixture/test
    construction (ADR-0145 Decision point 4): production callers reach this
    shape only through :func:`verify_causal_order`'s per-position parse
    step.

    Parameters
    ----------
    raw : bytes
        JSON bytes of one candidate envelope object.

    Returns
    -------
    dict
        The parsed, fully validated 15-field object.

    Raises
    ------
    ProductionError
        Naming every field that is unknown, missing or mistyped.
    """
    if not isinstance(raw, bytes):
        raise ProductionError(["event-envelope/v2 parse takes bytes"])
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise ProductionError(["event-envelope/v2 bytes are not JSON"])
    problems = _check_event_envelope(value)
    if problems:
        raise ProductionError(problems)
    return value


def _event_envelope_order_key(envelope):
    """Return the ADR-0145 Decision point 2 order key, verbatim.

    Availability, derived source rank, source sequence, correction
    position, payload digest, event ID -- exactly this 6-tuple, this
    order, no more terms. ``exchange_ms``/``receive_ms`` are deliberately
    absent: deriving order from them is real F1/F2 semantics this ADR does
    not claim (Decision point 1).
    """
    return (
        envelope["availability_ms"],
        envelope["source_rank"],
        envelope["source_sequence"],
        envelope["correction_position"],
        envelope["payload_sha256"],
        envelope["event_id"],
    )


def _project_v2_event_envelopes(events, source_rank_policy, /):
    """Purely project closed raw-event/v2 values into canonical envelopes.

    This private transform recognizes no authority.  Its caller must already
    have verified the raw fixture, authorization scope, and synthetic
    environment identity.  The deliberately narrow inputs leave every
    envelope field either event-derived or policy-derived (ADR-0171).
    """
    problems = []
    raw_schema = DATASET_AUTHORIZATION_EVENT_SCHEMAS[
        "dskit.dataset-capture-authorization/v2"
    ]
    raw_fields = RAW_EVENT_FIELDS[raw_schema]
    policy_schema = "dskit.source-rank-policy/v1"
    policy_fields = {"schema_version", "sources", "policy_sha256"}
    source_fields = {"source_id", "rank"}

    if type(events) is not tuple:
        problems.append("events must be an exact tuple")
        event_values = ()
    elif not events:
        problems.append("events must be nonempty")
        event_values = ()
    else:
        event_values = events

    ranks = {}
    policy_digest = None
    if type(source_rank_policy) is not MappingProxyType:
        problems.append("source_rank_policy must be an exact mappingproxy")
        sources = ()
    else:
        unknown = set(source_rank_policy) - policy_fields
        missing = policy_fields - set(source_rank_policy)
        for name in sorted(name for name in unknown if type(name) is str):
            problems.append(f"source_rank_policy has unknown field {name!r}")
        for name_type in sorted(
            type(name).__name__ for name in unknown if type(name) is not str
        ):
            problems.append(
                "source_rank_policy has a non-string field name of type "
                f"{name_type}"
            )
        for name in sorted(missing):
            problems.append(f"source_rank_policy is missing field {name!r}")
        policy_version = source_rank_policy.get("schema_version")
        if type(policy_version) is not str or policy_version != policy_schema:
            problems.append(f"source_rank_policy.schema_version must be {policy_schema!r}")
        sources = source_rank_policy.get("sources")
        if type(sources) is not tuple:
            problems.append("source_rank_policy.sources must be an exact tuple")
            sources = ()
        elif not sources:
            problems.append("source_rank_policy.sources must be nonempty")
        policy_digest = source_rank_policy.get("policy_sha256")
        if type(policy_digest) is not str:
            problems.append(
                "source_rank_policy.policy_sha256 must be an exact str"
            )
        else:
            digest_problems = []
            check_digest(digest_problems, "policy_sha256", policy_digest)
            problems.extend(
                f"source_rank_policy.{problem}" for problem in digest_problems
            )

    source_preimage = []
    previous_source_id = None
    for index, item in enumerate(sources):
        prefix = f"source_rank_policy.sources[{index}]"
        if type(item) is not MappingProxyType:
            problems.append(f"{prefix} must be an exact mappingproxy")
            continue
        unknown = set(item) - source_fields
        missing = source_fields - set(item)
        for name in sorted(name for name in unknown if type(name) is str):
            problems.append(f"{prefix} has unknown field {name!r}")
        for name_type in sorted(
            type(name).__name__ for name in unknown if type(name) is not str
        ):
            problems.append(
                f"{prefix} has a non-string field name of type {name_type}"
            )
        for name in sorted(missing):
            problems.append(f"{prefix} is missing field {name!r}")
        source_id = item.get("source_id")
        rank = item.get("rank")
        source_ok = type(source_id) is str and bool(source_id)
        rank_ok = type(rank) is int and rank >= 0
        if not source_ok:
            problems.append(f"{prefix}.source_id must be an exact nonempty str")
        if not rank_ok:
            problems.append(f"{prefix}.rank must be an exact non-negative int")
        elif rank != index:
            problems.append(f"{prefix}.rank must equal its tuple position {index}")
        if source_ok:
            if previous_source_id is not None and source_id <= previous_source_id:
                problems.append(
                    f"{prefix}.source_id must be strictly sorted and unique"
                )
            previous_source_id = source_id
        if source_ok and rank_ok and not unknown and not missing:
            source_preimage.append({"source_id": source_id, "rank": rank})
            if source_id not in ranks:
                ranks[source_id] = rank

    if (
        type(source_rank_policy) is MappingProxyType
        and type(source_rank_policy.get("sources")) is tuple
        and len(source_preimage) == len(sources)
        and type(policy_digest) is str
    ):
        expected_policy_digest = hashlib.sha256(_canonical_bytes({
            "schema_version": policy_schema,
            "sources": source_preimage,
        })).hexdigest()
        if policy_digest != expected_policy_digest:
            problems.append(
                "source_rank_policy.policy_sha256 does not match canonical policy"
            )

    projected = []
    seen_event_ids = set()
    copied_event_fields = tuple(
        name for name in raw_fields if name != "schema_version"
    )
    for index, event in enumerate(event_values):
        prefix = f"event[{index}]"
        if type(event) is not MappingProxyType:
            problems.append(f"{prefix} must be an exact mappingproxy")
            continue
        unknown = set(event) - set(raw_fields)
        missing = set(raw_fields) - set(event)
        for name in sorted(name for name in unknown if type(name) is str):
            problems.append(f"{prefix} has unknown raw-event field {name!r}")
        for name_type in sorted(
            type(name).__name__ for name in unknown if type(name) is not str
        ):
            problems.append(
                f"{prefix} has a non-string raw-event field name of type "
                f"{name_type}"
            )
        for name in sorted(missing):
            problems.append(f"{prefix} is missing raw-event field {name!r}")
        event_schema = event.get("schema_version")
        if type(event_schema) is not str or event_schema != raw_schema:
            problems.append(f"{prefix}.schema_version must be {raw_schema!r}")

        for name in ("source_id", "event_id", "source_provenance_tag",
                     "source_timezone_tag"):
            if type(event.get(name)) is not str or not event.get(name):
                problems.append(f"{prefix}.{name} must be an exact nonempty str")
        for name in ("source_sequence", "availability_ms", "exchange_ms",
                     "receive_ms", "correction_position"):
            value = event.get(name)
            if type(value) is not int or value < 0:
                problems.append(f"{prefix}.{name} must be an exact non-negative int")

        exchange_ms = event.get("exchange_ms")
        receive_ms = event.get("receive_ms")
        if (
            type(exchange_ms) is int
            and type(receive_ms) is int
            and receive_ms < exchange_ms
        ):
            problems.append(f"{prefix}.receive_ms must be >= exchange_ms")
        payload_digest = event.get("payload_sha256")
        if type(payload_digest) is not str:
            problems.append(f"{prefix}.payload_sha256 must be an exact str")
        else:
            digest_problems = []
            check_digest(digest_problems, "payload_sha256", payload_digest)
            problems.extend(f"{prefix}.{problem}" for problem in digest_problems)

        event_id = event.get("event_id")
        if type(event_id) is str and event_id:
            if event_id in seen_event_ids:
                problems.append(f"{prefix}.event_id {event_id!r} is a duplicate")
            seen_event_ids.add(event_id)
        source_id = event.get("source_id")
        if type(source_id) is str and source_id and source_id not in ranks:
            problems.append(f"{prefix}.source_id {source_id!r} is absent from policy")

        correction_position = event.get("correction_position")
        corrects_event_id = event.get("corrects_event_id")
        if type(correction_position) is int and correction_position >= 0:
            if correction_position == 0:
                if corrects_event_id is not None:
                    problems.append(
                        f"{prefix}.corrects_event_id must be null at position zero"
                    )
            else:
                if type(corrects_event_id) is not str or not corrects_event_id:
                    problems.append(
                        f"{prefix}.corrects_event_id must be an exact nonempty str"
                    )
                elif corrects_event_id == event_id:
                    problems.append(f"{prefix}.corrects_event_id cannot be self")

        if (
            not unknown
            and not missing
            and type(source_id) is str
            and source_id in ranks
        ):
            envelope = {
                "schema_version": CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA,
                **{name: event[name] for name in copied_event_fields},
                "source_rank": ranks[source_id],
                "source_rank_policy_sha256": policy_digest,
            }
            projected.append(envelope)

    if problems:
        raise ProductionError(problems)

    projected.sort(key=_event_envelope_order_key)
    output = []
    emitted = {}
    for index, envelope in enumerate(projected):
        correction_position = envelope["correction_position"]
        target_id = envelope["corrects_event_id"]
        if correction_position == 0:
            envelope["prior_envelope_sha256"] = None
        else:
            target = emitted.get(target_id)
            if target is None:
                problems.append(
                    f"envelope[{index}].corrects_event_id {target_id!r} "
                    "does not identify an earlier envelope"
                )
                continue
            target_position, target_bytes = target
            if target_position != correction_position - 1:
                problems.append(
                    f"envelope[{index}] correction position does not immediately "
                    "follow its target"
                )
                continue
            envelope["prior_envelope_sha256"] = hashlib.sha256(
                target_bytes
            ).hexdigest()

        envelope_problems = _check_event_envelope(envelope)
        if envelope_problems:
            problems.extend(
                f"envelope[{index}]: {problem}" for problem in envelope_problems
            )
            continue
        raw = _canonical_bytes(envelope)
        try:
            parsed = _parse_event_envelope(raw)
        except ProductionError as exc:
            problems.append(f"envelope[{index}] failed canonical round trip: {exc}")
            continue
        output.append(raw)
        emitted[parsed["event_id"]] = (parsed["correction_position"], raw)

    reparsed = []
    for index, raw in enumerate(output):
        try:
            reparsed.append(_parse_event_envelope(raw))
        except ProductionError as exc:
            problems.append(f"output[{index}] failed reparse: {exc}")
    verified_ids = {}
    for index, envelope in enumerate(reparsed):
        event_id = envelope["event_id"]
        if event_id in verified_ids:
            problems.append(f"output[{index}] duplicates event_id {event_id!r}")
        if index and _event_envelope_order_key(reparsed[index - 1]) > (
            _event_envelope_order_key(envelope)
        ):
            problems.append(f"output[{index}] is out of canonical order")
        if envelope["correction_position"] > 0:
            target_index = verified_ids.get(envelope["corrects_event_id"])
            if target_index is None:
                problems.append(f"output[{index}] correction target is not earlier")
            else:
                target = reparsed[target_index]
                expected = hashlib.sha256(output[target_index]).hexdigest()
                if target["correction_position"] != envelope["correction_position"] - 1:
                    problems.append(f"output[{index}] correction position is not contiguous")
                if envelope["prior_envelope_sha256"] != expected:
                    problems.append(f"output[{index}] prior envelope digest differs")
        verified_ids[event_id] = index

    if problems:
        raise ProductionError(problems)
    return tuple(output)


def verify_causal_order(tape, ordered_envelope_bytes):
    """Verify a resolved envelope-bytes sequence is causally ordered for ``tape``.

    Pure and read-only (ADR-0145 Decision point 3): performs no F4, P4,
    broker, signer or ledger operation and mutates neither argument.
    Resolving ``ordered_envelope_bytes`` from a capture/session/broker is
    composed-tape's job, deliberately out of scope here (Decision point 6).

    Parameters
    ----------
    tape : CapturedReplayTape
        An already-parsed ``dskit.captured-replay-tape/v1`` value.
    ordered_envelope_bytes : list or tuple of bytes
        The caller-supplied, already-resolved raw envelope member bytes,
        one per tape position, in tape order.

    Returns
    -------
    None

    Raises
    ------
    ProductionError
        Naming every violation found: a length mismatch against
        ``tape.envelope_count``; a per-position digest mismatch or a
        malformed envelope shape (position included); a
        ``source_rank_policy_sha256`` that disagrees with the tape's own
        field; a duplicate ``event_id`` anywhere on the tape; a correction
        chain that forward-references, gaps, or never bottoms out at
        ``correction_position == 0``; or a decreasing order key between two
        adjacent positions.
    """
    if not isinstance(ordered_envelope_bytes, (list, tuple)):
        raise ProductionError(["ordered_envelope_bytes must be a list or tuple of bytes"])
    ordered_envelope_bytes = list(ordered_envelope_bytes)
    if len(ordered_envelope_bytes) != tape.envelope_count:
        raise ProductionError([
            f"ordered_envelope_bytes has {len(ordered_envelope_bytes)} entries, "
            f"tape.envelope_count is {tape.envelope_count}"
        ])

    digests = tape.ordered_envelope_digests
    problems = []
    envelopes = [None] * len(ordered_envelope_bytes)
    for index, raw in enumerate(ordered_envelope_bytes):
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digests[index]:
            problems.append(
                f"envelope[{index}] bytes do not match "
                f"tape.ordered_envelope_digests[{index}]"
            )
            continue
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            problems.append(f"envelope[{index}] bytes are not JSON")
            continue
        field_problems = _check_event_envelope(value)
        if field_problems:
            problems.extend(f"envelope[{index}]: {item}" for item in field_problems)
            continue
        envelopes[index] = value
    if problems:
        raise ProductionError(problems)

    problems = []
    policy = tape.source_rank_policy_sha256
    for index, envelope in enumerate(envelopes):
        if envelope["source_rank_policy_sha256"] != policy:
            problems.append(
                f"envelope[{index}]: source_rank_policy_sha256 does not match "
                "tape.source_rank_policy_sha256"
            )

    first_index = {}
    for index, envelope in enumerate(envelopes):
        event_id = envelope["event_id"]
        if event_id in first_index:
            problems.append(
                f"envelope[{index}]: duplicate event_id {event_id!r}, "
                f"first seen at position {first_index[event_id]}"
            )
        else:
            first_index[event_id] = index

    for index, envelope in enumerate(envelopes):
        position = envelope["correction_position"]
        if position == 0:
            continue
        parent_index = first_index.get(envelope["corrects_event_id"])
        if parent_index is None or parent_index >= index:
            problems.append(
                f"envelope[{index}]: corrects_event_id does not name an envelope "
                "at a strictly lower tape position"
            )
            continue
        parent = envelopes[parent_index]
        if parent["correction_position"] != position - 1:
            problems.append(
                f"envelope[{index}]: corrects an envelope whose correction_position "
                "is not exactly one less"
            )
        if envelope["prior_envelope_sha256"] != digests[parent_index]:
            problems.append(
                f"envelope[{index}]: prior_envelope_sha256 does not match the "
                f"corrected envelope's digest at position {parent_index}"
            )

    for index in range(len(envelopes) - 1):
        if _event_envelope_order_key(envelopes[index]) > _event_envelope_order_key(
            envelopes[index + 1]
        ):
            problems.append(
                f"envelope[{index}] and envelope[{index + 1}] are out of causal order"
            )

    if problems:
        raise ProductionError(problems)


# ---------------------------------------------------------------------------
# ADR-0146 -- bounded synthetic composed-tape verification (P7 closure
# gate). `compose_replay_tape` resolves envelope bytes from an
# already-committed capture/session/publication and composes them into a
# `verify_causal_order`-verified `CapturedReplayTape`, end-to-end. It
# imports no symbol from `dskit.pipeline.trust` (Decision point 10): it
# reaches `record`/`session`/`published` only through the public methods/
# attributes ADR-0129 already exposes (`record.read_member_bytes`,
# `record.lifecycle_captured_receipt_sha256`, `published.sealed.digests`,
# `published.sealed.prepared.stream_id`), duck-typed on whatever the
# caller hands it.
# ---------------------------------------------------------------------------

_COMPOSED_TAPE_ROSTER_SCHEMA = "dskit.composed-tape-roster-fixture/v1"
# ^ Test-only/placeholder fixture shape (ADR-0146 Decision point 3): NOT a
# real `SourceRosterCapture.v1` and not a claim toward ADR-0130's
# undelivered broker.
_RAW_EVENT_V1_SCHEMA = DATASET_AUTHORIZATION_EVENT_SCHEMAS[
    "dskit.dataset-capture-authorization/v1"
]
_RAW_EVENT_V1_FIELDS = RAW_EVENT_FIELDS[_RAW_EVENT_V1_SCHEMA]
_RAW_EVENT_MEMBERS_ENTRY_FIELDS = (
    "relative_path",
    "exchange_ms",
    "receive_ms",
    "source_provenance_tag",
    "source_timezone_tag",
    "correction_position",
    "corrects_event_id",
    "prior_envelope_sha256",
)


def _check_composed_tape_roster_fixture(value):
    """Accumulate every refusal for one ``dskit.composed-tape-roster-fixture/v1`` object.

    ``sources`` must be a list of ``{source_id, rank}`` objects with unique
    ``source_id`` values and ``rank`` equal to the item's own list
    position exactly (0..len(sources)-1, in order) -- already rank-sorted,
    no gap, no duplicate, no out-of-position value (ADR-0146 Decision
    point 3/7 step 3).
    """
    if not isinstance(value, dict):
        return ["composed-tape-roster-fixture must decode to an object"]
    problems = []
    known = {"schema_version", "sources"}
    unknown = set(value) - known
    missing = known - set(value)
    for name in sorted(unknown):
        problems.append(f"unknown composed-tape-roster-fixture field {name!r}")
    for name in sorted(missing):
        problems.append(f"missing composed-tape-roster-fixture field {name!r}")
    if value.get("schema_version") != _COMPOSED_TAPE_ROSTER_SCHEMA:
        problems.append(f"schema_version must be {_COMPOSED_TAPE_ROSTER_SCHEMA!r}")
    sources = value.get("sources")
    if not isinstance(sources, list):
        problems.append("sources must be a list")
        return problems
    if not sources:
        problems.append("sources must be a nonempty list")
        return problems
    seen_ids = set()
    source_known = {"source_id", "rank"}
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            problems.append(f"sources[{index}] must be an object")
            continue
        item_unknown = set(item) - source_known
        item_missing = source_known - set(item)
        for name in sorted(item_unknown):
            problems.append(f"unknown sources[{index}] field {name!r}")
        for name in sorted(item_missing):
            problems.append(f"missing sources[{index}] field {name!r}")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            problems.append(f"sources[{index}].source_id must be a nonempty str")
        elif source_id in seen_ids:
            problems.append(f"sources[{index}].source_id {source_id!r} is a duplicate")
        else:
            seen_ids.add(source_id)
        rank = item.get("rank")
        rank_ok = isinstance(rank, int) and not isinstance(rank, bool) and rank >= 0
        if not rank_ok:
            problems.append(f"sources[{index}].rank must be a non-negative int")
        elif rank != index:
            problems.append(
                f"sources[{index}].rank must equal its list position {index}, got {rank}"
            )
    return problems


def _check_raw_event_member(value):
    """Accumulate every refusal for one closed ``dskit.raw-event/v1`` six-key object.

    The v1 schema and field tuple come from the dependency-free shared
    ``dskit.pipeline.event_wire`` owner. This module still does not import
    ``dskit.pipeline.trust`` (Decision point 10's own layering boundary).
    """
    if not isinstance(value, dict):
        return ["raw-event/v1 must decode to an object"]
    problems = []
    unknown = set(value) - set(_RAW_EVENT_V1_FIELDS)
    missing = set(_RAW_EVENT_V1_FIELDS) - set(value)
    for name in sorted(unknown):
        problems.append(f"unknown raw-event field {name!r}")
    for name in sorted(missing):
        problems.append(f"missing raw-event field {name!r}")
    if value.get("schema_version") != _RAW_EVENT_V1_SCHEMA:
        problems.append(f"schema_version must be {_RAW_EVENT_V1_SCHEMA!r}")
    source_id = value.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        problems.append("source_id must be a nonempty str")
    event_id = value.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        problems.append("event_id must be a nonempty str")
    source_sequence = value.get("source_sequence")
    if (
        isinstance(source_sequence, bool)
        or not isinstance(source_sequence, int)
        or source_sequence < 0
    ):
        problems.append("source_sequence must be a non-negative int")
    availability_ms = value.get("availability_ms")
    if isinstance(availability_ms, bool) or not isinstance(availability_ms, int):
        problems.append("availability_ms must be an int")
    check_digest(problems, "payload_sha256", value.get("payload_sha256"))
    return problems


def compose_replay_tape(
    record, session, published, consumer_document_sha256,
    roster_relative_path, raw_event_members,
):
    """Resolve one committed P4 capture into a verified ``CapturedReplayTape``.

    Bounded synthetic/test use only; not for real replay operations.

    Reads a ``dskit.composed-tape-roster-fixture/v1`` roster member and an
    ordered sequence of ``dskit.raw-event/v1`` members from the exact
    ``record``/``session``/``published``/``consumer_document_sha256``
    capture a caller already holds (ADR-0146 Decision point 7), projects
    each raw event plus its caller-supplied metadata into a closed
    ``dskit.event-envelope/v2`` object, then builds, round-trips and
    verifies the resulting tape via the unedited ``CapturedReplayTape``
    codec and :func:`verify_causal_order`. ``record``/``session``/
    ``published`` are the exact values one ``authorize_capture_set`` call
    already returned (ADR-0129's own shapes); this function calls only
    their existing public methods/attributes and imports no symbol from
    ``dskit.pipeline.trust``, so it works identically for a fixed or a
    dynamic authority's capture without inspecting which one issued it.

    Parameters
    ----------
    record : CapturedAuthorizationRecord
        The exact record one ``authorize_capture_set`` call returned.
    session : LaunchSession
        The exact session returned alongside ``record``.
    published : _Published
        The exact publication token the caller captured from the same F4
        lifecycle that fed ``authorize_capture_set``'s ``captures`` tuple.
    consumer_document_sha256 : str
        The frozen consumer document's own digest.
    roster_relative_path : str
        The ``source_roster.json`` member's relative path in the sealed
        manifest.
    raw_event_members : list or tuple
        An ordered sequence (never a one-shot iterator), one item per
        envelope, in the exact intended final tape order -- position ``i``
        becomes tape position ``i``; no sorting is performed here. Each
        item is a mapping with exactly eight keys: ``relative_path`` (str),
        ``exchange_ms``/``receive_ms`` (non-negative int),
        ``source_provenance_tag``/``source_timezone_tag`` (nonempty str),
        ``correction_position`` (non-negative int), ``corrects_event_id``
        (str or None), ``prior_envelope_sha256`` (sha256 digest or None).

    Returns
    -------
    CapturedReplayTape
        The verified, reparsed tape.

    Raises
    ------
    ProductionError
        Naming every problem found: an unauthorized or mismatched
        record/session/published/consumer_document_sha256 combination
        (the underlying accessor's own ``ValueError`` wrapped for a single
        consistent exception type across this function's whole contract);
        a malformed roster or raw-event member; a raw-event member whose
        source_id is not in the roster; a malformed or unrecognized
        ``raw_event_members`` entry; or any ``verify_causal_order``
        violation, propagated unchanged.
    """
    if not isinstance(raw_event_members, (list, tuple)):
        raise ProductionError(["raw_event_members must be a list or tuple"])

    try:
        stream = published.sealed.prepared.stream_id
    except AttributeError as exc:
        raise ProductionError([f"published token is malformed: {exc}"]) from exc

    try:
        roster_bytes = record.read_member_bytes(
            session, published, consumer_document_sha256, roster_relative_path
        )
    except ValueError as exc:
        raise ProductionError([str(exc)]) from exc

    try:
        roster_value = json.loads(roster_bytes.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise ProductionError(["composed-tape-roster-fixture bytes are not JSON"])
    roster_problems = _check_composed_tape_roster_fixture(roster_value)
    if roster_problems:
        raise ProductionError(roster_problems)

    source_rank_policy_sha256 = hashlib.sha256(roster_bytes).hexdigest()
    source_rank_by_id = {
        item["source_id"]: item["rank"] for item in roster_value["sources"]
    }

    problems = []
    ordered_envelope_bytes = []
    ordered_envelope_digests = []
    for position, entry in enumerate(raw_event_members):
        if not isinstance(entry, dict):
            problems.append(f"raw_event_members[{position}] must be an object")
            continue
        entry_unknown = set(entry) - set(_RAW_EVENT_MEMBERS_ENTRY_FIELDS)
        entry_missing = set(_RAW_EVENT_MEMBERS_ENTRY_FIELDS) - set(entry)
        for name in sorted(entry_unknown):
            problems.append(f"raw_event_members[{position}]: unknown field {name!r}")
        for name in sorted(entry_missing):
            problems.append(f"raw_event_members[{position}]: missing field {name!r}")
        if entry_unknown or entry_missing:
            continue
        relative_path = entry["relative_path"]
        try:
            raw_bytes = record.read_member_bytes(
                session, published, consumer_document_sha256, relative_path
            )
        except ValueError as exc:
            problems.append(f"raw_event_members[{position}]: {exc}")
            continue
        try:
            raw_value = json.loads(raw_bytes.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            problems.append(f"raw_event_members[{position}]: raw-event bytes are not JSON")
            continue
        raw_problems = _check_raw_event_member(raw_value)
        if raw_problems:
            problems.extend(f"raw_event_members[{position}]: {item}" for item in raw_problems)
            continue
        source_id = raw_value["source_id"]
        if source_id not in source_rank_by_id:
            problems.append(
                f"raw_event_members[{position}]: source_id {source_id!r} is not in the roster"
            )
            continue
        envelope = {
            "schema_version": CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA,
            "source_id": source_id,
            "event_id": raw_value["event_id"],
            "source_sequence": raw_value["source_sequence"],
            "payload_sha256": raw_value["payload_sha256"],
            "availability_ms": raw_value["availability_ms"],
            "source_rank": source_rank_by_id[source_id],
            "source_rank_policy_sha256": source_rank_policy_sha256,
            "exchange_ms": entry["exchange_ms"],
            "receive_ms": entry["receive_ms"],
            "source_provenance_tag": entry["source_provenance_tag"],
            "source_timezone_tag": entry["source_timezone_tag"],
            "correction_position": entry["correction_position"],
            "corrects_event_id": entry["corrects_event_id"],
            "prior_envelope_sha256": entry["prior_envelope_sha256"],
        }
        envelope_problems = _check_event_envelope(envelope)
        if envelope_problems:
            problems.extend(
                f"raw_event_members[{position}]: {item}" for item in envelope_problems
            )
            continue
        envelope_bytes = _canonical_bytes(envelope)
        ordered_envelope_bytes.append(envelope_bytes)
        ordered_envelope_digests.append(hashlib.sha256(envelope_bytes).hexdigest())
    if problems:
        raise ProductionError(problems)

    # Computed fresh, here, from the live `published` token that already
    # survived the binding checks inside every `read_member_bytes` call
    # above -- never accepted as an argument (Decision point 7 step 7;
    # Phase 0 matrix row 6's tamper-resistance requirement). Matches
    # `trust.py`'s own `_manifest_digest` formula (`_digest(_canonical_bytes(
    # [dict(item) for item in digests]))`) without reading the private
    # `_member_manifest_sha256` attribute.
    data_capture_root = _canonical_hash([dict(item) for item in published.sealed.digests])

    try:
        data_captured_receipt = record.lifecycle_captured_receipt_sha256(
            stream, consumer_document_sha256
        )
    except ValueError as exc:
        raise ProductionError([str(exc)]) from exc

    tape = CapturedReplayTape._build(
        data_capture_root, data_captured_receipt,
        source_rank_policy_sha256, ordered_envelope_digests,
    )
    reparsed = CapturedReplayTape.parse(tape.canonical_bytes())
    verify_causal_order(reparsed, ordered_envelope_bytes)
    return reparsed


class ReplayTape(ABC):
    """What a replay hands the composition root, so it can select D20's objects.

    ``compose.bundles_for(..., tape=tape)`` builds the replay collaborators
    from these three answers; the tape supplies DATA and never an object,
    which is what keeps "the rungs differ only by which objects were
    injected" (§5.15) a fact about ``compose.py`` rather than about
    whoever produced the tape. The ABC lives here, beside the bundles, for
    the same reason they do: ``report.py`` builds tapes and ``compose.py``
    consumes them, and a declaration in either would make §10's build order
    cyclic.

    Examples
    --------
    A tape that replays one tick of one leg::

        class OneTick(ReplayTape):
            def start_ms(self):
                return 1_767_268_800_000

            def feed_results(self):
                return (result,)

            def id_allocations(self):
                return (("next_tick_id", (1_767_268_800_000,), "tick-1"),)

        OneTick().start_ms()
        # -> 1767268800000
    """

    @abstractmethod
    def start_ms(self):
        """Return the instant the replay clock starts at.

        Returns
        -------
        int
            Epoch milliseconds — the recording's own first instant, so the
            cadence grid the replay walks is the grid it walked.
        """

    @abstractmethod
    def feed_results(self):
        """Return the recorded pulls, in tick order.

        Returns
        -------
        tuple of FeedResult
            One per recorded tick.
        """

    @abstractmethod
    def id_allocations(self):
        """Return the recorded id allocations, in the order they were asked.

        Returns
        -------
        tuple of tuple
            ``(method, args, id)`` triples, as ``RecordedIdSource`` takes
            them — and it refuses any call that is not the recorded one,
            which is what makes a replay that decided differently a
            refusal rather than a quiet re-derivation.
        """


class _Bundle:
    """Presence-only validation every bundle shares: a ``None`` member is absent, nothing else is."""

    def __post_init__(self):
        """Refuse every absent member in one raise, naming each."""
        absent = [field.name for field in fields(self) if getattr(self, field.name) is None]
        if absent:
            raise ProductionError(
                [f"{type(self).__name__}.{name} is absent (None)" for name in absent]
            )


@dataclass(frozen=True)
class Schedule(_Bundle):
    """When the loop ticks: the time source and the calendar, cadence and overrun policies (§5.1).

    Parameters
    ----------
    clock : Clock
        The injected time source; nothing else reads the wall clock.
    calendar : Calendar
        Open/closed sessions and the windows guards and cadences anchor on.
    cadence : Cadence
        The tick grid — the next due instant after a given one.
    overrun : Overrun
        What happens to ticks that fell due while one was running.

    Examples
    --------
    Bind the four collaborators ``compose.bundles_for`` resolved::

        schedule = Schedule(clock=clock, calendar=calendar, cadence=cadence, overrun=overrun)
        schedule.clock is clock  # True
    """

    clock: object
    calendar: object
    cadence: object
    overrun: object


@dataclass(frozen=True)
class Data(_Bundle):
    """Where a tick's rows and proposals come from (§5.2, §5.3).

    Parameters
    ----------
    feed : Feed
        Acquires and reads the entry batch.
    decider : Decider
        Runs the decision nodes and owns the configured ``Proposer``, which
        is how ``Tick.candidates`` / ``quotes`` / ``propose`` reach it.

    Examples
    --------
    ::

        data = Data(feed=feed, decider=decider)
        data.decider is decider  # True
    """

    feed: object
    decider: object


@dataclass(frozen=True)
class Decision(_Bundle):
    """What judges a proposal and what watches the stream of decisions (§5.5, §5.10).

    Parameters
    ----------
    guards : GuardChain
        The ordinary guards, run at leg steps (1) and (2).
    monitors : Mapping
        The configured monitors, observed after each tick.

    Examples
    --------
    ::

        decision = Decision(guards=guards, monitors=monitors)
        decision.guards is guards  # True
    """

    guards: object
    monitors: object


@dataclass(frozen=True)
class Safety(_Bundle):
    """Everything that may say no (§5.6, §5.13, §5.13.1, §5.14).

    Parameters
    ----------
    breaker : Breaker
        The series breaker — ``active | reducing | halted``.
    arming : Arming
        The maker-checker arming fold and scope application (D11).
    authorities : AuthorityTable
        ``for_origin(origin, breaker)`` — the ``Authority`` that mints a permit.
    readiness : Readiness
        The GO / NO-GO checklist evaluator.
    invocation : Invocation
        The ``--armed`` / env-hash / ``--once`` / ``--max-ticks`` values.
    action_policy : ActionPolicy
        Who may act — D10's matrix.
    transition_policy : TransitionPolicy
        How the breaker may move — D10's transitions.
    submission_verifier : SubmissionVerifier
        The final verify-and-call gate before native I/O.

    Examples
    --------
    ::

        safety = Safety(
            breaker=breaker, arming=arming, authorities=authorities, readiness=readiness,
            invocation=Invocation(armed=False, env_release_hash=None, once=False, max_ticks=None),
            action_policy=action_policy, transition_policy=transition_policy,
            submission_verifier=submission_verifier,
        )
        safety.invocation.armed  # False
    """

    breaker: object
    arming: object
    authorities: object
    readiness: object
    invocation: object
    action_policy: object
    transition_policy: object
    submission_verifier: object


@dataclass(frozen=True)
class Execution(_Bundle):
    """The venue side: the executor, its accounting, the lease and the resilience policies (§5.7, §5.12).

    Parameters
    ----------
    executor : Executor
        Read, query and cancel — and, for a ``SubmittingExecutor``, submit.
    accounting : Accounting
        Snapshots, valuation and the ``risk_effect`` classification.
    lease : Lease
        Single-writer coordination over the venue/account scope.
    resilience : ResiliencePolicies
        The ``Retry`` / ``CircuitBreakers`` / ``RateLimiter`` / ``Transport`` set.

    Examples
    --------
    ::

        execution = Execution(
            executor=executor, accounting=accounting, lease=lease, resilience=resilience
        )
        execution.executor is executor  # True
    """

    executor: object
    accounting: object
    lease: object
    resilience: object


@dataclass(frozen=True)
class Recording(_Bundle):
    """The durable side: the ledger, its fold, the control inbox, reconciliation and ids (§5.8, §5.9, §5.13).

    Parameters
    ----------
    ledger : Ledger
        The append-only, barriered series ledger.
    state : SeriesState
        The sole fold of that ledger; ``snapshot()`` is every ``state_view``.
    inbox : ControlInbox
        The durable spool of queued control commands.
    reconciler : Reconciler
        Startup and periodic reconciliation against the venue.
    checkpoint : Checkpoint
        The projection written last after each tick.
    journal_hook : callable
        Writes D22's one journal row per completed process.
    id_source : IdSource
        Allocates tick, leg, plan and client ids before ``tick_start``.

    Examples
    --------
    ::

        recording = Recording(
            ledger=ledger, state=state, inbox=inbox, reconciler=reconciler,
            checkpoint=checkpoint, journal_hook=journal_hook, id_source=id_source,
        )
        recording.id_source is id_source  # True
    """

    ledger: object
    state: object
    inbox: object
    reconciler: object
    checkpoint: object
    journal_hook: object
    id_source: object


@dataclass(frozen=True)
class Observability(_Bundle):
    """What the process reports about itself (§5.11, §5.11.1).

    Parameters
    ----------
    metrics : Metrics
        Counters, gauges and histograms flushed per tick.
    alerts : AlertRouter
        Routes alerts to the configured sinks.
    health : Health
        The health state machine the action policy reads.
    heartbeat : HeartbeatEmitter
        The liveness signal.

    Examples
    --------
    ::

        observability = Observability(
            metrics=metrics, alerts=alerts, health=health, heartbeat=heartbeat
        )
        observability.health is health  # True
    """

    metrics: object
    alerts: object
    health: object
    heartbeat: object


@dataclass(frozen=True)
class Invocation:
    """How this process was invoked: the four knobs ``__main__`` reads (§5.6, §5.13).

    Parameters
    ----------
    armed : bool
        ``--armed`` was given.
    env_release_hash : str or None
        The value of ``DSKIT_PRODUCTION_ARM``, or ``None`` when unset.
    once : bool
        ``--once`` was given: run one tick.
    max_ticks : int or None
        ``--max-ticks N`` (at least 1), or ``None`` for an unbounded serve.

    Raises
    ------
    ProductionError
        Naming every knob that is not the type above — ``--max-ticks 0``
        must refuse rather than serve forever or stop at once depending on
        how the loop reads it.

    Examples
    --------
    An unarmed, unbounded serve — the normal shadow case — and an armed one::

        Invocation(armed=False, env_release_hash=None, once=False, max_ticks=None)
        armed = Invocation(armed=True, env_release_hash="a" * 64, once=True, max_ticks=1)
        armed.once  # True
    """

    armed: bool
    env_release_hash: str | None
    once: bool
    max_ticks: int | None

    def __post_init__(self):
        """Refuse every knob of the wrong type in one raise."""
        problems = []
        if not isinstance(self.armed, bool):
            problems.append(f"Invocation.armed must be a bool, got {self.armed!r}")
        if self.env_release_hash is not None and not isinstance(self.env_release_hash, str):
            problems.append(
                f"Invocation.env_release_hash must be a str or None, got {self.env_release_hash!r}"
            )
        if not isinstance(self.once, bool):
            problems.append(f"Invocation.once must be a bool, got {self.once!r}")
        if self.max_ticks is not None and (
            isinstance(self.max_ticks, bool)
            or not isinstance(self.max_ticks, int)
            or self.max_ticks < 1
        ):
            problems.append(
                f"Invocation.max_ticks must be an int of at least 1 or None, got {self.max_ticks!r}"
            )
        if problems:
            raise ProductionError(problems)
