"""Opaque capture handles and the append-only WORM lifecycle (ADR-0123 F4).

Application code cannot construct a trust-root, provider, keyring, clock,
runtime verifier, lifecycle authority, launch session, capture, receipt, or
binding resolver from data. A development broker exists only for focused
synthetic tests and always stamps ``deployment_eligible=false``.

The compare-and-set chain is ``PRODUCED -> SEALED -> PUBLISHED -> CAPTURED
-> CONSUMED``. Handles expose verified bytes and audit, never a path,
reopen, provider, or JSON reconstruction API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from functools import wraps
from threading import RLock
from types import MappingProxyType
from weakref import WeakKeyDictionary, WeakSet, ref as weakref_ref

from dskit.pipeline.event_wire import (
    AUTHORIZATION_SCOPE_FIELDS,
    DATASET_AUTHORIZATION_EVENT_SCHEMAS,
    RAW_EVENT_FIELDS,
    ROSTER_AUTHORIZATION_EVENT_SCHEMAS,
)
from dskit.pipeline.node import Node, reject_unknown_params

__all__ = [
    "CapturedAuthorizationAuthority",
    "CapturedAuthorizationRecord",
    "CapturedBindings",
    "CapturedPortSet",
    "CapturedJsonArtifact",
    "CapturedLifecyclePort",
    "CapturedMemberHandle",
    "CapturedRelease",
    "HistoricalStudyEnvelopePreflight",
    "HistoricalStudyRevocations",
    "ImmutableSnapshotProvider",
    "LaunchSession",
    "LifecycleAuthority",
    "NonAuthorizingAdr0125StructuralSignaturePreflight",
    "NonAuthorizingSyntheticGrantVerifier",
    "NonAuthorizingSyntheticFixtureVerifier",
    "NonAuthorizingRosterBootstrapVerifier",
    "NonAuthorizingRosterRootProof",
    "NonAuthorizingRawRootProof",
    "NonAuthorizingSyntheticRootPisProof",
    "NonAuthorizingDynamicRootGraph",
    "ReleaseKeyring",
    "ReplayRun",
    "TerminalArtifactVerifier",
    "TrustedClock",
    "TrustedRuntimeVerifier",
    "VerifiedCapture",
    "VerifiedExternalArtifactAnchor",
]

_MAKE = object()
_P4_PORT_STATE_MUTATE = object()
_DEV_KEY = b"dskit.lifecycle-dev/v1"
_HEX64 = 64
_PLACEHOLDER = "0" * _HEX64
_MEMBER_KEYS = frozenset(
    {"relative_path", "media_type", "bytes", "file_type", "link_count"}
)
_DESCRIPTOR_KEYS = frozenset(
    {
        "document_sha256",
        "node",
        "output",
        "purpose",
        "root_ref",
        "snapshot_version",
    }
)
_EVENTS = ("PRODUCED", "SEALED", "PUBLISHED", "CAPTURED", "CONSUMED")


def _canonical_bytes(value):
    """Return canonical JSON bytes; refuse NaN and non-ASCII."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _digest(data):
    """Return lowercase SHA-256 hex of ``data``."""
    return hashlib.sha256(data).hexdigest()


def _sign(body):
    """Return a development HMAC over canonical receipt body bytes."""
    return hmac.new(_DEV_KEY, _canonical_bytes(body), hashlib.sha256).hexdigest()


def _freeze_json(value):
    """Return an immutable JSON view."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _load_canonical_json(raw):
    """Decode retained member bytes as exact canonical JSON."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("canonical JSON member bytes are required")
    data = bytes(raw)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("UTF-8 JSON member is required") from exc

    def _reject_constant(token):
        raise ValueError("finite JSON numbers are required")

    def _reject_duplicates(pairs):
        keys = [key for key, _unused in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate JSON keys are refused")
        return dict(pairs)

    try:
        parsed = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicates,
        )
    except ValueError as exc:
        raise ValueError("canonical JSON member is required") from exc
    canonical = _canonical_bytes(parsed)
    if canonical != data:
        raise ValueError("canonical JSON member is required")
    return parsed, canonical


def _path_error(path):
    """Return a path error message, or None when the relative POSIX path is legal."""
    if not isinstance(path, str) or path in ("", "."):
        return "path"
    if path.startswith("/") or "\\" in path or "\x00" in path:
        return "path"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "path"
    return None


def _require_sha256(value, label):
    """Refuse a non-lowercase SHA-256 hex string or a placeholder digest."""
    if value == "self":
        raise ValueError("%s placeholder is refused" % label)
    if not isinstance(value, str) or len(value) != _HEX64:
        raise ValueError("%s SHA-256 is required" % label)
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError("%s SHA-256 is required" % label)
    if value == _PLACEHOLDER:
        raise ValueError("%s placeholder is refused" % label)
    return value


class _Opaque:
    """Block copy, pickle, mapping, and filesystem reconstruction."""

    __slots__ = ()

    def __copy__(self):
        raise TypeError("opaque capture handle")

    def __deepcopy__(self, memo):
        raise TypeError("opaque capture handle")

    def __getstate__(self):
        raise TypeError("opaque capture handle")

    def __reduce__(self):
        raise TypeError("opaque capture handle")

    def __reduce_ex__(self, protocol):
        raise TypeError("opaque capture handle")


class ImmutableSnapshotProvider(ABC):
    """Read-only view of one immutable snapshot version.

    Parameters
    ----------
    None
        Public construction is refused. A broker supplies the live provider.

    Examples
    --------
    A test double names the two abstract hooks::

        class MemoryProvider(ImmutableSnapshotProvider):
            def describe(self, root_ref, snapshot_version):
                return {"root_ref": root_ref, "snapshot_version": snapshot_version}

            def open_member(self, snapshot, relative_path):
                return b"{}"

        provider = MemoryProvider()
        provider.describe("capture://root", "1")["snapshot_version"]  # '1'
    """

    @abstractmethod
    def describe(self, root_ref, snapshot_version):
        """Return the snapshot identity for ``root_ref`` / ``snapshot_version``."""

    @abstractmethod
    def open_member(self, snapshot, relative_path):
        """Return the exact retained bytes of one snapshot member."""


class ReleaseKeyring(ABC):
    """Verify one external signature.

    Examples
    --------
    A development keyring always reports a boolean::

        class Always(ReleaseKeyring):
            def verify(self, key_id, key_version, issued_at_ms, message, signature):
                return True

        Always().verify("k", 1, 0, b"m", "s")  # True
    """

    @abstractmethod
    def verify(self, key_id, key_version, issued_at_ms, message, signature):
        """Return True when ``signature`` is valid for ``message``."""


class TrustedClock(ABC):
    """Integer epoch-ms clock.

    Examples
    --------
    A pinned clock returns one instant::

        class Pinned(TrustedClock):
            def now_ms(self):
                return 1

        Pinned().now_ms()  # 1
    """

    @abstractmethod
    def now_ms(self):
        """Return the trusted instant as integer epoch milliseconds."""


class TrustedRuntimeVerifier(ABC):
    """Check a launch measurement.

    Examples
    --------
    A verifier that accepts every measurement::

        class Accept(TrustedRuntimeVerifier):
            def verify(self, measurement):
                return True

        Accept().verify({})  # True
    """

    @abstractmethod
    def verify(self, measurement):
        """Return True when ``measurement`` matches the launch admission."""


class LifecycleAuthority(ABC):
    """Broker-owned WORM lifecycle writer.

    Examples
    --------
    Public construction of the ABC is refused::

        try:
            LifecycleAuthority()
        except TypeError:
            refused = True
        refused  # True
    """

    @abstractmethod
    def produce(self, session, **kwargs):
        """Record PRODUCED for a completed planned producer run."""

    @abstractmethod
    def seal(self, session, prepared, **kwargs):
        """Record SEALED for the exact staged member bytes."""

    @abstractmethod
    def publish(self, session, sealed, **kwargs):
        """Record PUBLISHED after copying sealed bytes into WORM storage."""

    @abstractmethod
    def capture(self, published, frozen, port, **kwargs):
        """Record CAPTURED for one frozen consumer document and port."""

    @abstractmethod
    def open_capture(self, session, captured):
        """Return a verified capture bound to ``session``."""


class CapturedAuthorizationAuthority(LifecycleAuthority):
    """Broker-issued capability for the versioned capture-set doorway.

    The fixed nondeployment broker verifies the complete held artifact closure
    and atomically commits admission consumption, the exact signed batch and
    one opaque session. It grants no member access. Ordinary v1 authorities
    retain their original abstract method set.

    Parameters
    ----------
    None
        The public ABC cannot be constructed; only a private broker issues it.

    Examples
    --------
    Direct capability construction refuses::

        try:
            CapturedAuthorizationAuthority()
        except TypeError:
            refused = True
        refused  # True
    """

    @abstractmethod
    def authorize_capture_set(
        self, captures, admission_ref, *, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonces,
    ):
        """Authorize one complete capture set in the held authority's ledger.

        Parameters
        ----------
        captures : tuple
            Nonempty exact tuples of published handle, frozen document and port.
        admission_ref : dict
            Exact action-execution or final-replay IssuanceBasisRef.v1.
        consumer_run_identity : str
            Nonempty run identity distinct from every producer run.
        process_measurement_sha256 : str
            Nonplaceholder lowercase SHA-256 of the consumer measurement.
        runtime_sha256 : str
            Nonplaceholder lowercase SHA-256 of the consumer runtime.
        transition_nonces : tuple
            One unique, unused nonempty string per capture.

        Returns
        -------
        tuple
            The opaque CapturedAuthorizationRecord and P4 LaunchSession.
            An identical retry resolves these same committed objects.

        Raises
        ------
        TypeError
            The receiver is not an exact broker-issued capability or an input
            has a forbidden type.
        ValueError
            Request identity, complete artifact closure or atomic admission
            preconditions fail. No partial capture or member access is granted.
        """
        return _p4_checked_dispatch(
            self, captures, admission_ref,
            consumer_run_identity=consumer_run_identity,
            process_measurement_sha256=process_measurement_sha256,
            runtime_sha256=runtime_sha256,
            transition_nonces=transition_nonces,
        )

    def captured_port_set(self, record, session):
        """Mint the sole named tape-port view over one committed replay batch."""
        if _p4_checked_port_set_dispatch is not _P4_PORT_SET_DISPATCH:
            raise TypeError("P4 port-set dispatch integrity refused")
        return _P4_PORT_SET_DISPATCH(self, record, session)

    def inspect_capture_admission(
        self, captures, admission_ref, *, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonces,
    ):
        """Read-only verification of one already-issued ``admission_ref`` (ADR-0147).

        Requires an issued, non-revoked authority and a live resolver
        snapshot (the existing ``_p4_require_issued_authority``); validates
        ``admission_ref`` and the request/nonce triple through the SAME
        checked machinery ``authorize_capture_set`` already uses
        (``_p4_reference_bytes``, ``_P4_REQUEST_CHECK``); resolves the exact
        selected admission through the existing
        ``_P4_CLOSE_ADMISSION``/``_dynamic_p4_close_admission`` closure walk
        (via ``_p4_close_admission_once``, dispatched on the resolver type
        exactly as ``commit_p4_batch`` already does); and returns ONLY the
        immutable canonical bytes ``_p4_reference_bytes`` itself already
        computed for the validated ``admission_ref`` — never a second,
        possibly-divergent recomputation, and never a session, plan
        projection or transferable token.

        Performs no write, no session and no ledger effect: every step
        above is an existing read-only check or closure-verification
        reused from ``authorize_capture_set``'s own preflight, never the
        commit path (``_LifecycleAuthorizationLedger.commit_p4_batch``) that
        actually spends. Calling this twice with identical arguments
        returns byte-identical bytes and changes no observable state, which
        is what lets ``HistoricalStudyVerifier.capture`` call it once before
        reserving durably and once more immediately after, as a recheck,
        with no effect of its own either time.

        Parameters
        ----------
        captures : tuple
            Exact ordered published/frozen/derived-port tuples, as
            ``authorize_capture_set`` takes them.
        admission_ref : dict
            Exact action or replay admission reference.
        consumer_run_identity : str
            Consumer run distinct from the producer runs.
        process_measurement_sha256 : str
            Exact consumer process measurement digest.
        runtime_sha256 : str
            Exact consumer runtime digest.
        transition_nonces : tuple
            One distinct unused nonce per capture — a metadata-freshness
            check on THIS call only; no process/run/nonce identity of any
            kind enters the durable spend-right this feeds (ADR-0147
            Decision point 4).

        Returns
        -------
        bytes
            The verified ``admission_ref``'s immutable canonical bytes.

        Raises
        ------
        TypeError
            The receiver is not an exact broker-issued capability, or an
            input has a forbidden type.
        ValueError
            Request identity or complete artifact closure preconditions
            fail.
        """
        _p4_require_issued_authority(self)
        resolver = self._p4_resolver
        snapshot = resolver.snapshot()
        admission_bytes = _p4_reference_bytes(admission_ref, resolver)
        runtime = {
            "consumer_run_identity": consumer_run_identity,
            "process_measurement_sha256": process_measurement_sha256,
            "runtime_sha256": runtime_sha256,
        }
        _P4_REQUEST_CHECK(self, captures, runtime, transition_nonces)
        _p4_close_admission_once(
            resolver, snapshot, admission_ref, (self, captures, runtime), [None],
        )
        return admission_bytes


class LaunchSession(_Opaque):
    """Process-bound launch capability. Not constructible from data.

    Examples
    --------
    Direct construction is refused::

        try:
            LaunchSession()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = (
        "_kind",
        "_run_identity",
        "_ended",
        "_plan_sha256",
        "_runtime",
        "_stream_id",
        "_locked",
    )

    def __init__(self, token, kind, run_identity, plan_sha256, runtime):
        if token is not _MAKE:
            raise TypeError("LaunchSession is opaque")
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_run_identity", run_identity)
        object.__setattr__(self, "_ended", False)
        object.__setattr__(self, "_plan_sha256", plan_sha256)
        object.__setattr__(self, "_runtime", MappingProxyType(dict(runtime)))
        object.__setattr__(self, "_stream_id", None)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Refuse attribute writes after construction."""
        if getattr(self, "_locked", False):
            raise AttributeError("launch session is frozen")
        object.__setattr__(self, name, value)


def _captured_view_transition(method):
    """Arbitrate the entire v1 member/require operation, including local flags."""
    @wraps(method)
    def locked(view, *args, **kwargs):
        identity = _LIFECYCLE_VIEWS.get(view)
        if identity is None:
            raise ValueError("broker-registered legacy captured view required")
        authority, stream = identity
        ledger = _LIFECYCLE_LEDGERS.get(authority)
        if ledger is None:
            raise ValueError("captured view lifecycle ledger required")
        with ledger._lock:
            ledger._check()
            ledger._legacy_gate((view,))
            ledger._require_unclaimed(stream)
            record = authority._view_record(view)
            if record[1] in ("verified", "member") and stream not in authority._consumed_streams:
                _DevelopmentBroker._validate_bound_v1_effect(authority, view=view)
            return method(view, *args, **kwargs)
    return locked


def _retained_view(view):
    """Resolve a view only through its authenticated lifecycle owner."""
    identity = _LIFECYCLE_VIEWS.get(view)
    if identity is None:
        raise ValueError("broker-registered captured view required")
    return identity[0]._view_record(view)


class VerifiedCapture(_Opaque):
    """Verified retained members for one captured root.

    Examples
    --------
    Direct construction is refused::

        try:
            VerifiedCapture()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_members", "_published", "_port", "_frozen", "_session", "_retained", "_locked", "__weakref__")

    def __init__(self, token, members, published, port, frozen, session, retained):
        if token is not _MAKE:
            raise TypeError("VerifiedCapture is opaque")
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "_members", members)
        object.__setattr__(self, "_published", published)
        object.__setattr__(self, "_port", MappingProxyType(dict(port)))
        object.__setattr__(self, "_frozen", frozen)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_retained", retained)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Refuse attribute writes after construction."""
        if getattr(self, "_locked", False):
            raise AttributeError("verified capture is frozen")
        object.__setattr__(self, name, value)

    @_captured_view_transition
    def member(self, relative_path):
        """Return the one-shot handle for a retained member."""
        members = _retained_view(self)[4]
        if relative_path not in members:
            raise ValueError("missing captured member")
        member = members[relative_path]
        if self._members.get(relative_path) is not member:
            raise ValueError("captured member parent mismatch")
        if _retained_view(member)[3] is not self:
            raise ValueError("captured member parent mismatch")
        return member


class CapturedMemberHandle(_Opaque):
    """Single-read verified member bytes.

    Examples
    --------
    Direct construction is refused::

        try:
            CapturedMemberHandle()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_bytes", "_read", "__weakref__")

    def __init__(self, token, data):
        if token is not _MAKE:
            raise TypeError("CapturedMemberHandle is opaque")
        self._bytes = data
        self._read = False

    @_captured_view_transition
    def read_bytes(self):
        """Return the retained bytes exactly once."""
        authority, _stream = _LIFECYCLE_VIEWS[self]
        record = authority._view_record(self)
        if self._read or record[5]:
            raise ValueError("member bytes already consumed")
        authority._store_interned(
            authority._view_pins, authority._view_intern, id(self),
            (*record[:5], True),
        )
        self._read = True
        return record[4][1]

    @_captured_view_transition
    def read_text(self):
        """Return the retained UTF-8 text exactly once."""
        return self.read_bytes().decode("utf-8")


class CapturedJsonArtifact(_Opaque):
    """Immutable JSON value plus audit for one captured artifact.

    Examples
    --------
    Direct construction is refused::

        try:
            CapturedJsonArtifact()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_value", "_audit", "__weakref__")

    def __init__(self, token, value, audit):
        if token is not _MAKE:
            raise TypeError("CapturedJsonArtifact is opaque")
        self._value = value
        self._audit = audit

    @property
    @_captured_view_transition
    def value(self):
        """Frozen JSON object decoded from retained bytes."""
        return _retained_view(self)[4][0]

    @property
    @_captured_view_transition
    def audit(self):
        """Frozen audit mapping with no filesystem fields."""
        return _retained_view(self)[4][1]


class CapturedLifecyclePort(_Opaque):
    """One authorized consumer input bound to a verified artifact.

    Examples
    --------
    Direct construction is refused::

        try:
            CapturedLifecyclePort()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_artifact", "_audit", "__weakref__")

    def __init__(self, token, artifact, audit):
        if token is not _MAKE:
            raise TypeError("CapturedLifecyclePort is opaque")
        self._artifact = artifact
        self._audit = audit

    @property
    @_captured_view_transition
    def artifact(self):
        """The captured JSON artifact for this port."""
        return _retained_view(self)[4][0]

    @property
    @_captured_view_transition
    def audit(self):
        """Frozen port audit, including ``consumer_port``."""
        return _retained_view(self)[4][1]


class CapturedBindings(_Opaque):
    """Non-enumerable per-node view of authorized captured inputs.

    Examples
    --------
    Direct construction is refused::

        try:
            CapturedBindings()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_ports", "_used", "_broker", "_session", "_stream_id", "_locked", "__weakref__")

    def __init__(self, token, ports, broker, session, stream_id):
        if token is not _MAKE:
            raise TypeError("CapturedBindings is opaque")
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "_ports", MappingProxyType(dict(ports)))
        object.__setattr__(self, "_used", set())
        object.__setattr__(self, "_broker", broker)
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_stream_id", str(stream_id))
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Refuse attribute writes after construction."""
        if getattr(self, "_locked", False):
            raise AttributeError("captured bindings are frozen")
        object.__setattr__(self, name, value)

    @_captured_view_transition
    def require(self, input_name):
        """Return the exact declared input once, then refuse replay."""
        if input_name not in self._ports:
            raise ValueError("missing captured input")
        if input_name in self._used:
            raise ValueError("captured input already consumed")
        port = self._broker._consume_binding(self, input_name)
        self._used.add(input_name)
        return port


class CapturedRelease(_Opaque):
    """Opaque post-consumption result returned to the driver.

    Examples
    --------
    Direct construction is refused::

        try:
            CapturedRelease()
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_stream_id",)

    def __init__(self, token, stream_id):
        if token is not _MAKE:
            raise TypeError("CapturedRelease is opaque")
        self._stream_id = stream_id


class _MemoryProvider(ImmutableSnapshotProvider):
    """Development snapshot map keyed by root, version, and relative path."""

    def __init__(self, storage, events, worm):
        self._storage = storage
        self._events = events
        self._worm = worm

    def describe(self, root_ref, snapshot_version):
        return {"root_ref": root_ref, "snapshot_version": snapshot_version}

    def open_member(self, snapshot, relative_path):
        key = (snapshot["root_ref"], snapshot["snapshot_version"], relative_path)
        self._events.append(key)
        if key not in self._storage:
            raise ValueError("missing snapshot member")
        return self._storage[key]

    def write_member(self, root_ref, snapshot_version, relative_path, data):
        if not self._worm:
            raise ValueError("WORM immutable snapshot is required")
        key = (root_ref, snapshot_version, relative_path)
        if key in self._storage:
            raise ValueError("WORM snapshot member already published")
        self._storage[key] = bytes(data)


class _PinnedClock(TrustedClock):
    """Deterministic millisecond clock that advances on each read."""

    def __init__(self, start_ms):
        self._now = int(start_ms)

    def now_ms(self):
        instant = self._now
        self._now += 1
        return instant


class _HmacKeyring(ReleaseKeyring):
    """Development HMAC keyring with a fixed key."""

    def verify(self, key_id, key_version, issued_at_ms, message, signature):
        expected = hmac.new(_DEV_KEY, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class _AcceptRuntime(TrustedRuntimeVerifier):
    """Development runtime verifier that accepts the supplied measurement."""

    def verify(self, measurement):
        return isinstance(measurement, dict)


class _ReceiptSubject:
    """Immutable stream identity for receipt append. Not exported."""

    __slots__ = (
        "stream_id",
        "producer",
        "root",
        "session_run_identity",
        "output_member",
        "purpose",
        "_locked",
    )

    def __init__(
        self,
        stream_id,
        producer,
        root,
        session_run_identity,
        output_member,
        purpose,
    ):
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "stream_id", stream_id)
        object.__setattr__(self, "producer", MappingProxyType(dict(producer)))
        object.__setattr__(self, "root", MappingProxyType(dict(root)))
        object.__setattr__(self, "session_run_identity", session_run_identity)
        object.__setattr__(self, "output_member", output_member)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Keep the construction-owned publisher binding frozen."""
        if getattr(self, "_locked", False):
            raise AttributeError("receipt subject is frozen")
        object.__setattr__(self, name, value)


class _WormReceiptStore:
    """Append-only view of a receipt log; truncation is refused."""

    def __init__(self, data):
        self._data = data

    def __setitem__(self, key, value):
        new = tuple(value)
        old = self._data.get(key)
        if old is not None and len(new) < len(tuple(old)):
            raise ValueError("WORM receipt store is append-only")
        self._data[key] = new

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def items(self):
        return self._data.items()

    def __iter__(self):
        return iter(self._data)


class _Prepared:
    """Driver-internal PRODUCED token. Not exported."""

    __slots__ = (
        "stream_id",
        "members",
        "output_member",
        "purpose",
        "root",
        "producer",
        "session_run_identity",
        "_locked",
    )

    def __init__(
        self,
        stream_id,
        members,
        output_member,
        purpose,
        root,
        producer,
        session_run_identity,
    ):
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "stream_id", stream_id)
        object.__setattr__(self, "members", tuple(members))
        object.__setattr__(self, "output_member", output_member)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "producer", producer)
        object.__setattr__(self, "session_run_identity", session_run_identity)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("PRODUCED token is frozen")
        object.__setattr__(self, name, value)


class _Sealed:
    """Driver-internal SEALED token. Not exported."""

    __slots__ = (
        "prepared",
        "digests",
        "parsed",
        "_digests",
        "_member_manifest_sha256",
        "_locked",
    )

    def __init__(self, prepared, digests, parsed, member_manifest_sha256):
        frozen = tuple(MappingProxyType(dict(item)) for item in digests)
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "prepared", prepared)
        object.__setattr__(self, "digests", frozen)
        object.__setattr__(self, "parsed", parsed)
        object.__setattr__(self, "_digests", frozen)
        object.__setattr__(self, "_member_manifest_sha256", member_manifest_sha256)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("SEALED token is frozen")
        object.__setattr__(self, name, value)


class _Published:
    """Driver-internal PUBLISHED token. Not exported."""

    __slots__ = ("sealed", "descriptor", "_sealed", "_locked")

    def __init__(self, sealed, descriptor):
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "sealed", sealed)
        object.__setattr__(self, "descriptor", dict(descriptor))
        object.__setattr__(self, "_sealed", sealed)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("PUBLISHED token is frozen")
        object.__setattr__(self, name, value)


class _Frozen:
    """Frozen consumer document identity. Not exported."""

    __slots__ = (
        "source",
        "source_sha256",
        "published",
        "consumer_node",
        "consumer_input",
        "purpose",
        "document_sha256",
        "port",
        "_locked",
    )

    def __init__(self, **kwargs):
        object.__setattr__(self, "_locked", False)
        for key, value in kwargs.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise TypeError("frozen consumer document is immutable")
        object.__setattr__(self, name, value)


class _Captured:
    """CAPTURED token bound to one consumer run. Not exported."""

    __slots__ = ("published", "frozen", "port", "session", "_locked")

    def __init__(self, published, frozen, port, session):
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "published", published)
        object.__setattr__(self, "frozen", frozen)
        object.__setattr__(self, "port", dict(port))
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("CAPTURED token is frozen")
        object.__setattr__(self, name, value)


def _lifecycle_transition(method):
    """Serialize complete v1 operations in the same authority-owned P4 domain."""
    @wraps(method)
    def locked(authority, *args, **kwargs):
        ledger = _LIFECYCLE_LEDGERS.get(authority)
        if ledger is None:
            raise TypeError("lifecycle ledger identity required")
        with ledger._lock:
            ledger._check()
            ledger._legacy_gate((*args, *kwargs.values()))
            return method(authority, *args, **kwargs)
    return locked


class _DevelopmentBroker(LifecycleAuthority):
    """Synthetic lifecycle authority for focused tests only."""

    def __init__(
        self,
        snapshot_storage,
        receipt_store,
        session_events,
        member_events,
        start_ms,
        provider_worm,
    ):
        self._storage = snapshot_storage
        backing = {} if receipt_store is None else receipt_store
        self._receipt_store = (
            backing
            if isinstance(backing, _WormReceiptStore)
            else _WormReceiptStore(backing)
        )
        self._receipt_len = {
            key: len(tuple(value)) for key, value in self._receipt_store.items()
        }
        self._session_events = session_events
        self._member_events = member_events
        self._worm = provider_worm
        self._clock = _PinnedClock(start_ms)
        self._keyring = _HmacKeyring()
        self._runtime = _AcceptRuntime()
        self._provider = _MemoryProvider(snapshot_storage, member_events, provider_worm)
        self._nonces = set()
        self._consumed_streams = set()
        self._receipt_high = {}
        self._sessions = {}
        self._streams = {}
        self._used_inputs = {}
        self._released = set()
        self._captured_streams = {}
        self._session_streams = {}
        self._publish_sealed = {}
        self._freeze_published = {}
        self._capture_bind = {}
        self._stream_pin = {}
        self._publish_stream = {}
        self._verified_pin = {}
        self._view_pins = {}
        self._view_intern = {}
        self._bindings_pin = {}
        self._bindings_ports = {}
        self._session_runtime = {}
        self._bindings_intern = {}
        self._ports_intern = {}
        self._session_stream_intern = {}
        self._session_runtime_intern = {}
        self._freeze_intern = {}
        self._publish_stream_intern = {}
        self._verified_intern = {}
        self._capture_bind_intern = {}
        self._publish_sealed_intern = {}
        self._map_key = os.urandom(32)
        ledger = _LifecycleAuthorizationLedger(self)
        self._p4_ledger = ledger
        _LIFECYCLE_LEDGERS[self] = ledger

    @_lifecycle_transition
    def start_producer_session(
        self,
        run_identity,
        process_measurement_sha256,
        runtime_sha256,
        plan_sha256,
    ):
        session = LaunchSession(
            _MAKE,
            "producer",
            run_identity,
            plan_sha256,
            {
                "process_measurement_sha256": process_measurement_sha256,
                "runtime_sha256": runtime_sha256,
                "run_identity": run_identity,
            },
        )
        self._remember_session(session)
        self._session_events.append(("start", run_identity, id(session)))
        return session

    @_lifecycle_transition
    def end_session(self, session):
        self._require_session(session)
        object.__setattr__(session, "_ended", True)

    @_lifecycle_transition
    def produce(
        self,
        session,
        producer,
        root,
        purpose,
        expected_members,
        members,
        output_member,
        completed,
        planned,
        transition_nonce,
    ):
        self._require_session(session, kind="producer", allow_open=True)
        if not completed or not planned:
            raise ValueError("completed planned producer run is required")
        declared = tuple(expected_members)
        if len(declared) != len(set(declared)):
            raise ValueError("duplicate expected members")
        if len(members) != len(declared):
            raise ValueError("complete expected members are required")
        checked = []
        seen = []
        for index, member in enumerate(members):
            unknown = set(member) - _MEMBER_KEYS
            if unknown:
                raise ValueError("unknown member keys")
            path = member.get("relative_path")
            problem = _path_error(path)
            if problem:
                raise ValueError(problem)
            if path != declared[index]:
                raise ValueError("member order does not match expected members")
            if path in seen:
                raise ValueError("duplicate member path")
            seen.append(path)
            if member.get("file_type") != "regular":
                kind = member.get("file_type")
                raise ValueError("regular file required, not %s" % kind)
            if member.get("link_count") != 1:
                raise ValueError("hard link members are refused")
            payload = member.get("bytes")
            if not isinstance(payload, (bytes, bytearray)):
                raise ValueError("member bytes are required")
            checked.append(
                {
                    "relative_path": path,
                    "media_type": member.get("media_type"),
                    "bytes": payload,
                    "file_type": "regular",
                    "link_count": 1,
                }
            )
        if output_member not in seen:
            raise ValueError("complete expected members are required")
        stream_id = _digest(
            _canonical_bytes(
                {
                    "producer": producer,
                    "root": root,
                    "purpose": purpose,
                }
            )
        )
        self._reload_stream(stream_id)
        prepared = _Prepared(
            stream_id,
            checked,
            output_member,
            purpose,
            dict(root),
            dict(producer),
            session._run_identity,
        )
        self._stream_pin[stream_id] = _ReceiptSubject(
            stream_id,
            producer,
            root,
            session._run_identity,
            output_member,
            purpose,
        )
        self._append_receipt(
            prepared,
            "PRODUCED",
            session,
            transition_nonce,
            extra={},
        )
        return prepared

    @_lifecycle_transition
    def seal(self, session, prepared, transition_nonce):
        self._require_session(session, kind="producer", allow_open=True)
        if not isinstance(prepared, _Prepared):
            raise ValueError("SEALED requires a PRODUCED token")
        self._original_prepared_output(prepared)
        self._reload_stream(prepared.stream_id)
        self._require_head(prepared.stream_id, "PRODUCED")
        digests = []
        parsed = {}
        for member in prepared.members:
            raw = bytes(member["bytes"])
            media = member.get("media_type")
            if media == "application/json":
                value, canonical = _load_canonical_json(raw)
                parsed[member["relative_path"]] = value
                digest_source = canonical
            else:
                digest_source = raw
            digests.append(
                {
                    "relative_path": member["relative_path"],
                    "media_type": media,
                    "sha256": _digest(digest_source),
                    "bytes": len(digest_source),
                }
            )
        member_manifest_sha256 = self._manifest_digest(digests)
        sealed = _Sealed(prepared, digests, parsed, member_manifest_sha256)
        self._append_receipt(
            prepared,
            "SEALED",
            session,
            transition_nonce,
            extra={"member_manifest_sha256": member_manifest_sha256},
        )
        return sealed

    @_lifecycle_transition
    def publish(self, session, sealed, transition_nonce):
        self._require_session(session, kind="producer", allow_open=True)
        if isinstance(sealed, _Prepared):
            raise ValueError("PUBLISHED requires a SEALED token")
        if not isinstance(sealed, _Sealed):
            raise ValueError("PUBLISHED requires a SEALED token")
        prepared = sealed.prepared
        original_output = self._original_prepared_output(prepared)
        self._reload_stream(prepared.stream_id)
        self._require_head(prepared.stream_id, "SEALED")
        if not self._worm:
            raise ValueError("WORM immutable snapshot is required")
        expected_manifest = self._sealed_member_manifest(prepared.stream_id)
        if sealed._member_manifest_sha256 != expected_manifest:
            raise ValueError("member digest mutation after seal")
        if self._manifest_digest(sealed._digests) != expected_manifest:
            raise ValueError("member digest mutation after seal")
        if len(prepared.members) != len(sealed._digests):
            raise ValueError("member digest mutation after seal")
        staged = []
        for member, declared in zip(prepared.members, sealed._digests, strict=True):
            current = bytes(member["bytes"])
            if member["relative_path"] != declared["relative_path"]:
                raise ValueError("member digest mutation after seal")
            if _path_error(declared["relative_path"]) is not None:
                raise ValueError("path")
            if _digest(current) != declared["sha256"] or len(current) != declared["bytes"]:
                raise ValueError("member digest mutation after seal")
            staged.append((declared["relative_path"], current))
        descriptor = {
            "root_ref": prepared.root["root_ref"],
            "snapshot_version": prepared.root["snapshot_version"],
            "document_sha256": prepared.producer["document_sha256"],
            "node": prepared.producer["node"],
            "output": prepared.producer["output"],
            "purpose": prepared.purpose,
        }
        published = _Published(sealed, descriptor)
        self._remember_published(published)
        self._store_interned(
            self._publish_sealed,
            self._publish_sealed_intern,
            id(published),
            (sealed, original_output, prepared),
        )
        self._store_interned(
            self._publish_stream,
            self._publish_stream_intern,
            id(published),
            prepared.stream_id,
        )
        self._append_receipt(
            prepared,
            "PUBLISHED",
            session,
            transition_nonce,
            extra={},
        )
        for path, current in staged:
            self._provider.write_member(
                prepared.root["root_ref"],
                prepared.root["snapshot_version"],
                path,
                current,
            )
        return published

    def descriptor(self, published, purpose):
        if not isinstance(published, _Published):
            raise ValueError("PUBLISHED descriptor is required")
        _, descriptor = self._publication_snapshot(published, "PUBLISHED descriptor is required")
        if purpose != descriptor["purpose"]:
            raise ValueError("purpose mismatch")
        return dict(descriptor)

    def freeze_consumer_document(
        self,
        source,
        consumer_node,
        consumer_input,
        purpose,
    ):
        if not isinstance(source, dict):
            raise ValueError("frozen consumer document is required")
        descriptor = self._descriptor_from(source, consumer_node, consumer_input)
        unknown = set(descriptor) - _DESCRIPTOR_KEYS
        if unknown:
            raise ValueError("unknown descriptor field")
        document_sha256 = descriptor.get("document_sha256")
        _require_sha256(document_sha256, "document_sha256")
        if purpose != descriptor.get("purpose"):
            raise ValueError("purpose mismatch")
        published = self._published_for_descriptor(descriptor)
        _, retained = self._publication_snapshot(published, "published descriptor identity mismatch")
        if descriptor.get("purpose") != retained["purpose"]:
            raise ValueError("published purpose mismatch")
        if descriptor.get("root_ref") != retained["root_ref"]:
            raise ValueError("published root mismatch")
        if descriptor.get("snapshot_version") != retained["snapshot_version"]:
            raise ValueError("published snapshot mismatch")
        if document_sha256 != retained["document_sha256"]:
            raise ValueError("published document_sha256 mismatch")
        if descriptor.get("node") != retained["node"]:
            raise ValueError("published node mismatch")
        if descriptor.get("output") != retained["output"]:
            raise ValueError("published output mismatch")
        source_sha256 = _digest(_canonical_bytes(source))
        port = {
            "consumer_document_sha256": source_sha256,
            "consumer_node": consumer_node,
            "consumer_input": consumer_input,
            "purpose": purpose,
        }
        frozen = _Frozen(
            source=source,
            source_sha256=source_sha256,
            published=published,
            consumer_node=consumer_node,
            consumer_input=consumer_input,
            purpose=purpose,
            document_sha256=source_sha256,
            port=dict(port),
        )
        self._store_interned(
            self._freeze_published,
            self._freeze_intern,
            id(frozen),
            (published, _canonical_bytes(port)),
        )
        return frozen

    def derive_consumer_port(self, frozen):
        frozen = self._require_frozen(frozen)
        self._frozen_publication(frozen)
        current = _digest(_canonical_bytes(frozen.source))
        if current != frozen.source_sha256:
            raise ValueError("consumer document changed after freeze")
        return {
            "consumer_document_sha256": current,
            "consumer_node": frozen.consumer_node,
            "consumer_input": frozen.consumer_input,
            "purpose": frozen.purpose,
        }

    @_lifecycle_transition
    def capture(
        self,
        published,
        frozen,
        port,
        consumer_run_identity,
        process_measurement_sha256,
        runtime_sha256,
        transition_nonce,
    ):
        return self._p4_ledger.commit_legacy_capture(
            published, frozen, port, consumer_run_identity,
            process_measurement_sha256, runtime_sha256, transition_nonce,
        )

    def _prepare_legacy_capture(
        self, published, frozen, port, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonce,
    ):
        """Validate and build unregistered v1 handles; do not publish any state."""
        if transition_nonce in self._nonces or self._p4_ledger._nonce_used(transition_nonce):
            raise ValueError("duplicate transition nonce")
        if not isinstance(published, _Published):
            raise ValueError("PUBLISHED capture is required")
        frozen = self._require_frozen(frozen)
        intern_pub = self._frozen_publication(frozen)
        if intern_pub is not published:
            raise ValueError("document port does not match published root")
        source_desc = self._descriptor_from(
            frozen.source,
            frozen.consumer_node,
            frozen.consumer_input,
        )
        _, retained = self._publication_snapshot(published, "PUBLISHED capture is required")
        if source_desc != retained:
            raise ValueError("document port does not match published root")
        expected = self.derive_consumer_port(frozen)
        if port != expected:
            raise ValueError("document port mismatch")
        stream_id = self._stream_for_published(published, "PUBLISHED capture is required")
        pin = self._stream_pin[stream_id]
        producer_run = pin.producer["run_identity"]
        session_run = pin.session_run_identity
        if consumer_run_identity in {producer_run, session_run}:
            raise ValueError("consumer run must be distinct from producer session")
        for existing in self._sessions.values():
            if existing._kind == "producer" and not existing._ended:
                raise ValueError("producer session must end before capture")
        self._reload_stream(stream_id)
        self._require_head(stream_id, "PUBLISHED")
        session = LaunchSession(
            _MAKE,
            "consumer",
            consumer_run_identity,
            pin.producer.get("document_sha256"),
            {
                "process_measurement_sha256": process_measurement_sha256,
                "runtime_sha256": runtime_sha256,
                "run_identity": consumer_run_identity,
            },
        )
        object.__setattr__(session, "_stream_id", str(stream_id))
        captured = _Captured(published, frozen, dict(expected), session)
        return captured, session, pin

    @_lifecycle_transition
    def open_capture(self, session, captured):
        if not isinstance(captured, _Captured):
            raise ValueError("CAPTURED token is required")
        self._require_session(session, kind="consumer", allow_open=True)
        bind = self._load_interned(
            self._capture_bind,
            self._capture_bind_intern,
            id(captured),
            "CAPTURED token is required",
        )
        published, frozen, bound_session, stream_id = bind
        if bound_session is not session:
            raise ValueError("capture is bound to a different consumer session")
        handle_sid = session._stream_id
        if handle_sid is None or str(stream_id) != str(handle_sid):
            raise ValueError("CAPTURED token is required")
        stream_id = str(handle_sid)
        subject, expected_port = _DevelopmentBroker._validate_bound_v1_effect(
            self, published, frozen, self.derive_consumer_port(frozen), session=session)
        if subject.stream_id != stream_id:
            raise ValueError("captured stream identity mismatch")
        sealed = self._sealed_for(published)
        pin = subject
        snapshot = self._provider.describe(
            pin.root["root_ref"],
            pin.root["snapshot_version"],
        )
        handles = {}
        retained = {}
        expected_manifest = self._sealed_member_manifest(stream_id)
        if sealed._member_manifest_sha256 != expected_manifest:
            raise ValueError("member digest mutation after seal")
        if self._manifest_digest(sealed._digests) != expected_manifest:
            raise ValueError("member digest mutation after seal")
        for declared in sealed._digests:
            path = declared["relative_path"]
            raw = self._provider.open_member(snapshot, path)
            if _digest(raw) != declared["sha256"] or len(raw) != declared["bytes"]:
                raise ValueError("snapshot member digest mutation")
            retained[path] = bytes(raw)
            handles[path] = CapturedMemberHandle(_MAKE, retained[path])
        verified = VerifiedCapture(
            _MAKE,
            handles,
            published,
            dict(expected_port),
            frozen,
            session,
            retained,
        )
        pin = (
            published,
            frozen,
            session,
            str(stream_id),
            MappingProxyType({path: bytes(data) for path, data in retained.items()}),
            tuple(sorted(dict(self.derive_consumer_port(frozen)).items())),
        )
        self._store_interned(
            self._verified_pin,
            self._verified_intern,
            id(verified),
            pin,
        )
        self._register_view(verified, "verified", stream_id, published,
                            MappingProxyType(dict(handles)))
        for path, handle in handles.items():
            self._register_view(handle, "member", stream_id, verified, (path, retained[path]))
        return verified

    @_lifecycle_transition
    def captured_bindings(
        self,
        session,
        frozen,
        verified,
        consumer_node,
        transition_nonce,
    ):
        self._require_session(session, kind="consumer", allow_open=True)
        if not isinstance(verified, VerifiedCapture):
            raise ValueError("verified capture is required")
        pin = self._load_interned(
            self._verified_pin,
            self._verified_intern,
            id(verified),
            "verified capture is required",
        )
        published, frozen_pin, bound_session, stream_id, retained, port_items = pin
        if bound_session is not session:
            raise ValueError("consumer session mismatch")
        frozen = self._require_frozen(frozen)
        if frozen_pin is not frozen:
            raise ValueError("plan document does not match captured freeze")
        if consumer_node != frozen.consumer_node:
            raise ValueError("consumer node mismatch")
        handle_sid = session._stream_id
        if handle_sid is None or str(stream_id) != str(handle_sid):
            raise ValueError("verified capture is required")
        stream_id = str(handle_sid)
        intern_pub = self._frozen_publication(frozen)
        if intern_pub is not published:
            raise ValueError("plan document does not match captured freeze")
        subject, expected_port = _DevelopmentBroker._validate_bound_v1_effect(self, view=verified)
        if subject.stream_id != stream_id:
            raise ValueError("verified stream identity mismatch")
        sealed = self._sealed_for(published)
        output_path = subject.output_member
        parsed, _canonical = _load_canonical_json(retained[output_path])
        declared = {
            item["relative_path"]: item for item in sealed._digests
        }[output_path]
        expected_port = dict(port_items)
        artifact = CapturedJsonArtifact(
            _MAKE,
            _freeze_json(parsed),
            MappingProxyType(
                {
                    "media_type": declared["media_type"],
                    "sha256": declared["sha256"],
                    "bytes": declared["bytes"],
                    "purpose": subject.purpose,
                }
            ),
        )
        port = CapturedLifecyclePort(
            _MAKE,
            artifact,
            _freeze_json({"consumer_port": expected_port}),
        )
        ports = {frozen.consumer_input: port}
        bindings = CapturedBindings(_MAKE, ports, self, session, stream_id)
        self._register_view(bindings, "bindings", stream_id, verified,
                            (session, _canonical_bytes(expected_port), str(transition_nonce)))
        for port in ports.values():
            self._register_view(port, "port", stream_id, bindings,
                                (artifact, port._audit))
            self._register_view(artifact, "artifact", stream_id, port,
                                (artifact._value, artifact._audit))
        self._store_interned(
            self._bindings_pin,
            self._bindings_intern,
            id(bindings),
            [
                id(bindings),
                str(stream_id),
                str(transition_nonce),
                list(sorted(dict(expected_port).items())),
                id(session),
            ],
        )
        self._store_interned(
            self._bindings_ports,
            self._ports_intern,
            id(bindings),
            (
                id(bindings),
                MappingProxyType(dict(ports)),
            ),
        )
        return bindings

    @_lifecycle_transition
    def _consume_binding(self, bindings, input_name):
        rec = self._load_interned(
            self._bindings_pin,
            self._bindings_intern,
            id(bindings),
            "captured bindings are required",
        )
        if rec[0] != id(bindings):
            raise ValueError("captured bindings are required")
        _bindings_id, intern_sid, nonce, port_items, session_id = rec
        session = bindings._session
        if session is None or id(session) != session_id or self._sessions.get(session_id) is not session:
            raise ValueError("captured bindings are required")
        found = self._stream_for_run_identity(
            session._run_identity,
            "captured bindings are required",
        )
        if str(intern_sid) != str(found):
            raise ValueError("captured bindings are required")
        stream_id = found
        port_items = tuple(tuple(item) for item in port_items)
        ports_rec = self._load_interned(
            self._bindings_ports,
            self._ports_intern,
            id(bindings),
            "captured bindings are required",
        )
        if ports_rec[0] != id(bindings):
            raise ValueError("captured bindings are required")
        ports = ports_rec[1]
        if input_name not in ports:
            raise ValueError("missing captured input")
        if session is None or id(session) != session_id or self._sessions.get(session_id) is not session:
            raise ValueError("captured bindings are required")
        if stream_id in self._consumed_streams:
            raise ValueError("replay of CONSUMED is refused")
        self._reload_stream(stream_id)
        self._require_head(stream_id, "CAPTURED")
        subject, expected_port = _DevelopmentBroker._validate_bound_v1_effect(self, view=bindings)
        if subject.stream_id != stream_id or expected_port != dict(port_items):
            raise ValueError("captured bindings effect mismatch")
        if (set(bindings._ports) != set(ports)
                or any(bindings._ports[name] is not value for name, value in ports.items())):
            raise ValueError("captured bindings port identity mismatch")
        effect_port = ports[input_name]
        port_record = self._view_record(effect_port, require_consumed=False)
        self._view_record(port_record[4][0], require_consumed=False)
        self._append_receipt(
            subject,
            "CONSUMED",
            session,
            nonce,
            extra={"consumer_captured_port": dict(port_items)},
        )
        self._used_inputs.setdefault(session_id, set()).add(input_name)
        self._consumed_streams.add(stream_id)
        return ports[input_name]

    @_lifecycle_transition
    def release(self, session):
        self._require_session(session, kind="consumer", allow_open=True)
        if id(session) in self._released:
            raise ValueError("release already replayed")
        bound = self._load_interned(
            self._session_streams,
            self._session_stream_intern,
            id(session),
            "CONSUMED input is required before release",
        )
        if bound[0] != id(session):
            raise ValueError("CONSUMED input is required before release")
        found = self._stream_for_run_identity(
            session._run_identity,
            "CONSUMED input is required before release",
        )
        if str(bound[1]) != str(found):
            raise ValueError("CONSUMED input is required before release")
        stream_id = found
        self._reload_stream(stream_id)
        self._require_head(stream_id, "CONSUMED")
        used = self._used_inputs.get(id(session), set())
        if not used:
            raise ValueError("CONSUMED input is required before release")
        self._released.add(id(session))
        return CapturedRelease(_MAKE, stream_id)

    def _receipt_audit(self, published):
        stream_id = self._diagnostic_stream_for_published(published)
        return [dict(row) for row in self._streams[stream_id]]

    def _receipt_digests(self, published):
        return [_digest(_canonical_bytes(row)) for row in self._receipt_audit(published)]

    def _hmac_payload(self, receipt):
        return {key: value for key, value in receipt.items() if key != "signature"}

    def _reload_stream(self, stream_id):
        stored = tuple(self._receipt_store.get(stream_id, ()))
        high = max(
            self._receipt_len.get(stream_id, 0),
            self._receipt_high.get(stream_id, 0),
        )
        if len(stored) < high:
            raise ValueError("WORM receipt store is append-only")
        if stream_id in self._consumed_streams:
            if not stored or json.loads(stored[-1].decode("ascii")).get("event") != "CONSUMED":
                raise ValueError("WORM receipt store is append-only")
        if stream_id not in self._receipt_store:
            if stream_id in self._streams:
                raise ValueError("PUBLISHED store predecessor sequence missing")
            return
        self._recover(stream_id)
        rows = [
            json.loads(raw.decode("ascii")) for raw in self._receipt_store[stream_id]
        ]
        self._streams[stream_id] = rows
        self._note_len(stream_id, len(rows))

    def _recover(self, stream_id):
        records = list(self._receipt_store.get(stream_id, ()))
        previous = None
        for index, raw in enumerate(records, start=1):
            receipt = json.loads(raw.decode("ascii"))
            if receipt.get("sequence") != index:
                raise ValueError("receipt sequence mismatch")
            if receipt.get("stream_id") != stream_id:
                raise ValueError("receipt stream mismatch")
            if receipt.get("previous_receipt_sha256") != previous:
                raise ValueError("receipt predecessor mismatch")
            message = _canonical_bytes(self._hmac_payload(receipt))
            if not self._keyring.verify(
                receipt["key"]["key_id"],
                receipt["key"]["key_version"],
                receipt["issued_at_ms"],
                message,
                receipt["signature"],
            ):
                raise ValueError("receipt signature mismatch")
            if receipt["event"] != _EVENTS[index - 1]:
                raise ValueError("receipt event order mismatch")
            if receipt["event"] == "PUBLISHED":
                field = receipt.get("publication_receipt_sha256")
                if not field:
                    raise ValueError("publication receipt identity is required")
                unsigned = {
                    key: value
                    for key, value in receipt.items()
                    if key not in {"signature", "publication_receipt_sha256"}
                }
                if field != _digest(_canonical_bytes(unsigned)):
                    raise ValueError("publication receipt identity mismatch")
            previous = _digest(_canonical_bytes(receipt))

    def _remember_session(self, session):
        self._sessions[id(session)] = session
        self._store_interned(
            self._session_runtime,
            self._session_runtime_intern,
            id(session),
            (
                id(session),
                tuple(sorted(dict(session._runtime).items())),
            ),
        )

    def _actor_runtime(self, session):
        rec = self._load_interned(
            self._session_runtime,
            self._session_runtime_intern,
            id(session),
            "launch session is required",
        )
        if rec[0] != id(session):
            raise ValueError("launch session is required")
        return dict(rec[1])

    def _store_interned(self, table, intern, key, value):
        try:
            _canonical_bytes(value)
            rec = self._mac_pack(key, value, ())
        except (TypeError, ValueError):
            objs = value if isinstance(value, tuple) else (value,)
            rec = self._mac_pack(key, None, objs)
        table[key] = rec
        intern[key] = rec
        return rec

    def _load_interned(self, table, intern, key, message):
        rec = table.get(key)
        if rec is None or rec is not intern.get(key):
            raise ValueError(message)
        payload, objs = self._mac_unpack(rec, key, message)
        if payload is not None:
            return payload
        if len(objs) == 1:
            return objs[0]
        return objs

    def _mac_pack(self, key, payload, objs):
        objs = tuple(objs)
        body = {"_k": key, "v": payload, "oids": [id(item) for item in objs]}
        mac = hmac.new(self._map_key, _canonical_bytes(body), hashlib.sha256).hexdigest()
        return (body, mac, objs)

    def _mac_unpack(self, rec, key, message):
        try:
            body, mac, objs = rec
        except (TypeError, ValueError):
            raise ValueError(message)
        expected = hmac.new(
            self._map_key,
            _canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()
        if mac != expected or body.get("_k") != key:
            raise ValueError(message)
        oids = body.get("oids") or []
        if len(objs) != len(oids):
            raise ValueError(message)
        for obj, oid in zip(objs, oids, strict=True):
            if id(obj) != oid:
                raise ValueError(message)
        return body.get("v"), objs

    def _mac_put(self, table, key, payload):
        rec = self._mac_pack(key, payload, ())
        table[key] = rec
        return rec

    def _mac_get(self, table, key, message):
        rec = table.get(key)
        if rec is None:
            raise ValueError(message)
        payload, _objs = self._mac_unpack(rec, key, message)
        return payload

    def _require_session(self, session, kind=None, allow_open=False):
        if not isinstance(session, LaunchSession) or id(session) not in self._sessions:
            raise ValueError("session is required")
        if kind is not None and session._kind != kind:
            raise ValueError("%s session is required" % kind)
        if allow_open and kind == "producer" and session._ended:
            raise ValueError("producer session already ended")
        return session

    def _require_frozen(self, frozen):
        if not isinstance(frozen, _Frozen):
            raise ValueError("frozen consumer document is required")
        return frozen

    def _original_prepared_output(self, prepared):
        """Require independent prepared and stream-pin output-member agreement."""
        if type(prepared) is not _Prepared:
            raise ValueError("prepared output member identity required")
        pin = self._stream_pin.get(prepared.stream_id)
        path = prepared.output_member
        if (type(pin) is not _ReceiptSubject or type(path) is not str
                or path != pin.output_member
                or path not in {member["relative_path"] for member in prepared.members}):
            raise ValueError("original output member identity mismatch")
        return path

    def _publication_record(self, published):
        """Resolve the original sealed, output-path and prepared association."""
        if type(published) is not _Published:
            raise ValueError("PUBLISHED token is required")
        record = self._load_interned(
            self._publish_sealed, self._publish_sealed_intern, id(published),
            "PUBLISHED token is required",
        )
        if (type(record) is not tuple or len(record) != 3
                or type(record[0]) is not _Sealed or type(record[1]) is not str
                or type(record[2]) is not _Prepared
                or record[2].output_member != record[1]):
            raise ValueError("published original output member mismatch")
        return record

    def _publication_members(self, published):
        return self._publication_record(published)[:2]

    def _sealed_for(self, published):
        return self._publication_members(published)[0]

    def _require_head(self, stream_id, event):
        rows = self._streams.get(stream_id) or []
        if not rows or rows[-1]["event"] != event:
            raise ValueError("replay of %s is refused" % event)

    def _publication_subject(self, stream_id):
        pin = self._stream_pin.get(stream_id)
        if pin is None:
            return None
        published = None
        for row in self._streams.get(stream_id) or ():
            if row.get("event") == "PUBLISHED":
                published = row
                break
        if published is None:
            return None
        return _ReceiptSubject(
            stream_id,
            {
                "document_sha256": published["producer_document_sha256"],
                "run_identity": published["producer_run_identity"],
                "node": published["producer_node"],
                "output": published["producer_output"],
            },
            {
                "root_ref": published["root_ref"],
                "root_id": published["root_id"],
                "snapshot_version": published["snapshot_version"],
            },
            pin.session_run_identity,
            pin.output_member,
            pin.purpose,
        )

    def _stream_for_run_identity(self, run_identity, message):
        found = None
        for stream_id, rows in self._streams.items():
            for row in rows:
                if row.get("event") != "CAPTURED":
                    continue
                if row.get("actor_runtime", {}).get("run_identity") != run_identity:
                    continue
                if found is not None and found != stream_id:
                    raise ValueError(message)
                found = stream_id
        if found is None:
            raise ValueError(message)
        return str(found)

    def _note_len(self, stream_id, n):
        high = self._receipt_high.get(stream_id, 0)
        if n < high:
            raise ValueError("WORM receipt store is append-only")
        self._receipt_high[stream_id] = n
        self._receipt_len[stream_id] = n

    def _captured_actor_runtime(self, rows):
        for row in rows:
            if row.get("event") == "CAPTURED":
                return dict(row["actor_runtime"])
        raise ValueError("CAPTURED actor runtime is required")

    def _publication_snapshot(self, published, message):
        """Resolve one authenticated token and retain its validated descriptor."""
        stream = self._load_interned(self._publish_stream, self._publish_stream_intern,
                                     id(published), message)
        self._reload_stream(stream)
        descriptor = self._retained_publication_descriptor(published, stream)
        if published.descriptor != descriptor:
            raise ValueError("published descriptor identity mismatch")
        return stream, descriptor

    def _stream_for_published(self, published, message):
        return self._publication_snapshot(published, message)[0]

    def _diagnostic_stream_for_published(self, published):
        """Inspect retained committed evidence without recovering exposed storage."""
        _, _, prepared = self._publication_record(published)
        stream = prepared.stream_id
        self._retained_publication_descriptor(published, stream)
        return stream

    def _retained_publication_descriptor(self, published, stream):
        """Bind receipt, pin and sealed producer facts; never select by a prefix."""
        rows = self._streams.get(stream, ())
        publications = [row for row in rows if row["event"] == "PUBLISHED"]
        pin = self._stream_pin.get(stream)
        if (len(publications) != 1 or pin is None or len(rows) < 3
                or [row["event"] for row in rows[:3]] != ["PRODUCED", "SEALED", "PUBLISHED"]):
            raise ValueError("published stream identity required")
        row = publications[0]
        producer = {"document_sha256": row["producer_document_sha256"],
                    "run_identity": row["producer_run_identity"],
                    "node": row["producer_node"], "output": row["producer_output"]}
        root = {name: row[name] for name in ("root_ref", "root_id", "snapshot_version")}
        expected_stream = _digest(_canonical_bytes(
            {"producer": producer, "root": root, "purpose": pin.purpose}))
        sealed, output_member, prepared = self._publication_record(published)
        if (expected_stream != stream or pin.stream_id != stream
                or dict(pin.producer) != producer or dict(pin.root) != root
                or pin.session_run_identity != rows[0]["actor_runtime"]["run_identity"]
                or prepared.stream_id != stream or dict(prepared.producer) != producer
                or dict(prepared.root) != root or prepared.purpose != pin.purpose
                or prepared.session_run_identity != pin.session_run_identity):
            raise ValueError("published stream identity mismatch")
        facts = ("producer_document_sha256", "producer_run_identity", "producer_node",
                 "producer_output", "root_ref", "root_id", "snapshot_version", "stream_id")
        if any(prior[name] != row[name] for prior in rows[:3] for name in facts):
            raise ValueError("published receipt identity mismatch")
        manifest = rows[1]["member_manifest_sha256"]
        if (pin.output_member != output_member
                or output_member not in {item["relative_path"] for item in sealed._digests}
                or sealed._member_manifest_sha256 != manifest
                or row["member_manifest_sha256"] != manifest
                or self._manifest_digest(sealed._digests) != manifest):
            raise ValueError("published member manifest or original output member mismatch")
        return MappingProxyType({
            "root_ref": root["root_ref"], "snapshot_version": root["snapshot_version"],
            "document_sha256": producer["document_sha256"], "node": producer["node"],
            "output": producer["output"], "purpose": pin.purpose})

    def _frozen_publication(self, frozen):
        """Validate the original frozen port before returning its publication."""
        record = self._load_interned(self._freeze_published, self._freeze_intern,
                                     id(frozen), "frozen published identity required")
        if type(record) is not tuple or len(record) != 2 or type(record[1]) is not bytes:
            raise ValueError("frozen published identity required")
        published, raw_port = record
        current = {"consumer_document_sha256": _digest(_canonical_bytes(frozen.source)),
                   "consumer_node": frozen.consumer_node, "consumer_input": frozen.consumer_input,
                   "purpose": frozen.purpose}
        if (_canonical_bytes(current) != raw_port
                or frozen.source_sha256 != current["consumer_document_sha256"]
                or frozen.document_sha256 != current["consumer_document_sha256"]):
            raise ValueError("document changed after freeze: port identity mismatch")
        _, retained = self._publication_snapshot(published, "frozen published identity mismatch")
        source = self._descriptor_from(frozen.source, frozen.consumer_node, frozen.consumer_input)
        if source != retained:
            raise ValueError("frozen descriptor differs from published identity")
        return published

    def _validate_bound_v1_effect(self, published=None, frozen=None, port=None,
                                  *, session=None, view=None, head="CAPTURED"):
        """Validate retained v1 identity immediately before an effect or delivery."""
        view_record = None
        retained_members = None
        if view is not None:
            view_record = self._view_record(view, require_consumed=False)
            parent = view_record
            while parent[1] != "verified":
                parent = self._view_record(parent[3], require_consumed=False)
            pin = self._load_interned(self._verified_pin, self._verified_intern,
                                      id(parent[0]), "verified capture identity required")
            published, frozen, session, bound_stream, retained_members, port_items = pin
            port = dict(port_items)
            for path, member in parent[4].items():
                member_record = self._view_record(member, require_consumed=False)
                if (member_record[3] is not parent[0] or member_record[4][0] != path
                        or member_record[4][1] != retained_members[path]):
                    raise ValueError("verified retained member identity mismatch")
            if parent[2] != bound_stream:
                raise ValueError("verified capture stream mismatch")
            if view_record[1] == "bindings" and view_record[4][0] is not session:
                raise ValueError("captured bindings session mismatch")
        stream, descriptor = self._publication_snapshot(published, "published effect identity required")
        if self._frozen_publication(frozen) is not published:
            raise ValueError("frozen publication effect mismatch")
        expected_port = self.derive_consumer_port(frozen)
        if retained_members is not None:
            sealed = self._publication_record(published)[0]
            declared = {item["relative_path"]: item for item in sealed._digests}
            if set(retained_members) != set(declared):
                raise ValueError("verified retained member manifest mismatch")
            for path, raw in retained_members.items():
                if (type(raw) is not bytes or _digest(raw) != declared[path]["sha256"]
                        or len(raw) != declared[path]["bytes"]):
                    raise ValueError("verified retained member digest mismatch")
        if port != expected_port:
            raise ValueError("captured effect port mismatch")
        self._require_head(stream, head)
        if view_record is not None and view_record[2] != stream:
            raise ValueError("captured effect stream mismatch")
        if session is not None:
            self._require_session(session, kind="consumer", allow_open=True)
            bound = self._load_interned(self._session_streams, self._session_stream_intern,
                                        id(session), "captured session identity required")
            actor = self._actor_runtime(session)
            if (bound != [id(session), stream] and bound != (id(session), stream)):
                raise ValueError("captured session stream mismatch")
            if (self._sessions.get(id(session)) is not session or session._stream_id != stream
                    or session._run_identity != actor["run_identity"]):
                raise ValueError("captured session runtime mismatch")
            captures = [row for row in self._streams[stream] if row["event"] == "CAPTURED"]
            if (len(captures) != 1 or captures[0]["actor_runtime"] != actor
                    or captures[0]["consumer_captured_port"] != expected_port):
                raise ValueError("captured receipt session or port mismatch")
        row = next(row for row in self._streams[stream] if row["event"] == "PUBLISHED")
        subject = _ReceiptSubject(stream,
            {"document_sha256": row["producer_document_sha256"],
             "run_identity": row["producer_run_identity"], "node": row["producer_node"],
             "output": row["producer_output"]},
            {name: row[name] for name in ("root_ref", "root_id", "snapshot_version")},
            self._streams[stream][0]["actor_runtime"]["run_identity"],
            self._publication_record(published)[1], descriptor["purpose"])
        return subject, expected_port

    def _register_view(self, view, kind, stream, parent, data):
        """Retain immutable view identity in the existing lifecycle authority."""
        stream = str(stream)
        self._store_interned(self._view_pins, self._view_intern, id(view),
                             (view, kind, stream, parent, data, False))
        _LIFECYCLE_VIEWS[view] = (self, stream)

    def _view_record(self, view, *, require_consumed=True):
        """Validate retained parent and slot identities without reopening bytes."""
        record = self._load_interned(self._view_pins, self._view_intern,
                                     id(view), "captured view identity required")
        if type(record) is not tuple or len(record) != 6 or record[0] is not view:
            raise ValueError("captured view identity required")
        _view, kind, stream, parent, data, was_read = record
        types = {"verified": VerifiedCapture, "member": CapturedMemberHandle,
                 "bindings": CapturedBindings, "port": CapturedLifecyclePort,
                 "artifact": CapturedJsonArtifact}
        if (type(kind) is not str or type(view) is not types.get(kind)
                or type(was_read) is not bool
                or _LIFECYCLE_VIEWS.get(view) != (self, stream)):
            raise ValueError("captured view broker or stream mismatch")
        if kind == "verified":
            pin = self._load_interned(self._verified_pin, self._verified_intern,
                                      id(view), "verified capture identity required")
            if (type(pin) is not tuple or len(pin) != 6 or pin[0] is not parent
                    or pin[3] != stream or set(data) != set(pin[4])
                    or set(view._members) != set(data)
                    or any(view._members[path] is not member for path, member in data.items())):
                raise ValueError("verified capture member identity mismatch")
            return record
        expected_parent = {"member": "verified", "bindings": "verified",
                           "port": "bindings", "artifact": "port"}[kind]
        if type(parent) is not types[expected_parent]:
            raise ValueError("captured view parent mismatch")
        parent_record = self._view_record(parent, require_consumed=require_consumed)
        if parent_record[1] != expected_parent or parent_record[2] != stream:
            raise ValueError("captured view parent mismatch")
        if type(data) is not tuple or len(data) != (3 if kind == "bindings" else 2):
            raise ValueError("captured view payload identity required")
        if kind == "member":
            pin = self._load_interned(self._verified_pin, self._verified_intern,
                                      id(parent), "verified member identity required")
            if (parent_record[4].get(data[0]) is not view
                    or pin[4].get(data[0]) != data[1] or view._bytes != data[1]):
                raise ValueError("captured member retained bytes mismatch")
        elif kind == "bindings":
            pin = self._load_interned(self._bindings_pin, self._bindings_intern,
                                      id(view), "captured bindings identity required")
            if (view._broker is not self or view._session is not data[0] or view._stream_id != stream
                    or pin[0] != id(view) or pin[1] != stream or pin[4] != id(data[0])
                    or pin[2] != data[2] or _canonical_bytes(dict(pin[3])) != data[1]):
                raise ValueError("captured bindings session or port mismatch")
        else:
            if kind == "port":
                ports = self._load_interned(self._bindings_ports, self._ports_intern,
                                            id(parent), "captured port identity required")[1]
                if (not any(port is view for port in ports.values())
                        or view._artifact is not data[0] or view._audit is not data[1]):
                    raise ValueError("captured port retained identity mismatch")
                binding = parent_record
            else:
                if (parent_record[4][0] is not view
                        or view._value is not data[0] or view._audit is not data[1]):
                    raise ValueError("captured artifact retained identity mismatch")
                binding = self._view_record(parent_record[3], require_consumed=require_consumed)
            rows = self._streams[stream]
            if require_consumed and (stream not in self._consumed_streams or rows[-1]["event"] != "CONSUMED"
                    or _canonical_bytes(rows[-1]["consumer_captured_port"]) != binding[4][1]):
                raise ValueError("captured artifact requires exact consumed input")
        return record

    def _manifest_digest(self, digests):
        return _digest(_canonical_bytes([dict(item) for item in digests]))

    def _sealed_member_manifest(self, stream_id):
        for row in self._streams.get(stream_id) or ():
            if row["event"] == "SEALED":
                return row["member_manifest_sha256"]
        raise ValueError("member digest mutation after seal")

    def _descriptor_from(self, source, consumer_node, consumer_input):
        try:
            node = source["pipeline"][consumer_node]["inputs"][consumer_input]
            return dict(node["$captured_artifact"])
        except (KeyError, TypeError) as exc:
            raise ValueError("frozen consumer document is required") from exc

    def _published_for_descriptor(self, descriptor):
        published = self._live_published(descriptor)
        stream = self._stream_for_published(published, "published root is required")
        self._require_head(stream, "PUBLISHED")
        return published

    def _live_published(self, descriptor):
        """Resolve exactly one retained descriptor, never a mutable prefix."""
        matches = []
        for published in self._published_tokens:
            _, retained = self._publication_snapshot(published, "published identity required")
            if retained == descriptor:
                matches.append(published)
        if len(matches) != 1:
            raise ValueError("exact published descriptor identity required")
        return matches[0]

    @property
    def _published_tokens(self):
        return getattr(self, "_published_list", [])

    def _remember_published(self, published):
        self._published_list = self._published_tokens + [published]

    @_lifecycle_transition
    def _append_receipt(self, prepared, event, session, transition_nonce, extra):
        self._p4_ledger._require_unclaimed(prepared.stream_id)
        rows = self._streams.get(prepared.stream_id, [])
        actor = self._captured_actor_runtime(rows) if event == "CONSUMED" else self._actor_runtime(session)
        body = self._prepare_receipt(prepared, event, actor, transition_nonce, extra)
        stream_id = prepared.stream_id
        encoded = tuple(self._receipt_store.get(stream_id, ())) + (_canonical_bytes(body),)
        self._receipt_store[stream_id] = encoded
        self._nonces.add(transition_nonce)
        self._streams.setdefault(stream_id, []).append(body)
        self._note_len(stream_id, len(encoded))
        return body

    def _prepare_receipt(self, prepared, event, actor_runtime, transition_nonce, extra):
        """Build the unchanged v1 signed receipt without spending or appending."""
        if self._p4_ledger._nonce_used(transition_nonce):
            raise ValueError("duplicate P4 transition nonce")
        if transition_nonce in self._nonces:
            raise ValueError("duplicate transition nonce")
        stream_id = prepared.stream_id
        rows = self._streams.get(stream_id, [])
        expected = _EVENTS[len(rows)]
        if event != expected:
            raise ValueError("replay of %s is refused" % expected)
        previous = _digest(_canonical_bytes(rows[-1])) if rows else None
        member_manifest = extra.get("member_manifest_sha256")
        if member_manifest is None and rows:
            member_manifest = rows[-1]["member_manifest_sha256"]
        if member_manifest is None:
            member_manifest = _digest(_canonical_bytes([]))
        body = {
            "schema": "dskit.lifecycle-receipt/v1",
            "stream_id": stream_id,
            "event": event,
            "sequence": len(rows) + 1,
            "previous_receipt_sha256": previous,
            "producer_document_sha256": prepared.producer["document_sha256"],
            "producer_run_identity": prepared.producer["run_identity"],
            "producer_node": prepared.producer["node"],
            "producer_output": prepared.producer["output"],
            "root_ref": prepared.root["root_ref"],
            "root_id": prepared.root["root_id"],
            "snapshot_version": prepared.root["snapshot_version"],
            "member_manifest_sha256": member_manifest,
            "actor_runtime": actor_runtime,
            "transition_nonce": transition_nonce,
            "issued_at_ms": self._clock.now_ms(),
            "deployment_eligible": False,
            "key": {
                "usage": "lifecycle",
                "key_id": "dev-lifecycle",
                "key_version": 1,
            },
        }
        if "consumer_captured_port" in extra:
            body["consumer_captured_port"] = extra["consumer_captured_port"]
        if event == "PUBLISHED":
            body["publication_receipt_sha256"] = _digest(_canonical_bytes(body))
        elif event in {"CAPTURED", "CONSUMED"}:
            for row in rows:
                if row.get("event") == "PUBLISHED":
                    field = row.get("publication_receipt_sha256")
                    if not field:
                        raise ValueError("publication receipt identity is required")
                    body["publication_receipt_sha256"] = field
                    break
            else:
                raise ValueError("publication receipt identity is required")
        signature = hmac.new(
            _DEV_KEY,
            _canonical_bytes(self._hmac_payload(body)),
            hashlib.sha256,
        ).hexdigest()
        body["signature"] = signature
        return body

def _development_broker(
    snapshot_storage=None,
    receipt_store=None,
    session_events=None,
    member_events=None,
    start_ms=0,
    provider_worm=True,
):
    """Return a synthetic lifecycle broker for focused tests.

    Parameters
    ----------
    snapshot_storage : dict or None
        Optional shared member map for TOCTOU tests.
    receipt_store : dict or None
        Optional shared WORM receipt log.
    session_events : list or None
        Optional session-start log.
    member_events : list or None
        Optional provider-open log.
    start_ms : int
        Deterministic clock origin.
    provider_worm : bool
        When False, publish refuses because the snapshot is not immutable.

    Returns
    -------
    _DevelopmentBroker
        A ``deployment_eligible=false`` development authority.
    """
    return _DevelopmentBroker(
        snapshot_storage if snapshot_storage is not None else {},
        receipt_store if receipt_store is not None else {},
        session_events if session_events is not None else [],
        member_events if member_events is not None else [],
        start_ms,
        provider_worm,
    )


_P4_ADMISSION_SCHEMAS = MappingProxyType({
    "action-execution-admission": "dskit.action-execution-admission/v1",
    "final-replay-admission": "dskit.final-replay-admission/v1",
})
_P4_DYNAMIC_ADMISSION_SCHEMAS = MappingProxyType({
    "root-capture-admission": "dskit.root-capture-admission/v1",
})
_P4_PENDING_TOKENS = set()
_P4_PENDING_RESOLVER_TOKENS = set()
_P4_ISSUED = WeakKeyDictionary()


def _p4_reference_bytes(value, resolver):
    """Validate the closed selected-admission reference before canonicalizing.

    ADR-0144 Decision point 1: dispatches on ``type(resolver)`` through the
    identical closed if/elif/else pattern ADR-0143 already used for
    ``_p4_require_issued_authority``/``_p4_snapshot_integrity``. The legacy
    (``if``) arm below is byte-identical to the prior single-branch body; the
    dynamic (``elif``) arm is new and self-contained, sharing no schema
    whitelist with the legacy arm.
    """
    if type(resolver) is _FixedWormTrustedArtifactResolver:
        if type(value) is not dict or set(value) != {"kind", "role", "schema", "sha256"}:
            raise ValueError("exact P4 admission reference is required")
        if any(type(item) is not str for item in value.values()):
            raise TypeError("exact P4 reference strings are required")
        if (
            value["role"] != "study-lifecycle"
            or value["kind"] not in _P4_ADMISSION_SCHEMAS
            or value["schema"] != _P4_ADMISSION_SCHEMAS[value["kind"]]
        ):
            raise ValueError("P4 admission kind, role and schema must agree")
        _require_sha256(value["sha256"], "admission")
        return _canonical_bytes(value)
    elif type(resolver) is _DynamicP4TrustedArtifactResolver:
        _p4_dynamic_reference_bytes(value)
        if (
            value["role"] != "study-lifecycle"
            or value["kind"] not in _P4_DYNAMIC_ADMISSION_SCHEMAS
            or value["schema"] != _P4_DYNAMIC_ADMISSION_SCHEMAS[value["kind"]]
        ):
            raise ValueError("P4 admission kind, role and schema must agree")
        return _hs_canonical_bytes(value)
    else:
        raise TypeError("exact broker-issued P4 capability is required")


def _p4_copy_facts(value, seen=None):
    """Copy a closed built-in fixture tree and reject shared mutable nodes."""
    if seen is None:
        seen = set()
    if type(value) in (str, bytes):
        return value
    if type(value) not in (dict, list):
        raise TypeError("P4 fixture facts require built-in mapping/list/bytes data")
    if id(value) in seen:
        raise ValueError("P4 fixture aliases and cycles are refused")
    seen.add(id(value))
    if type(value) is list:
        return [_p4_copy_facts(item, seen) for item in value]
    if any(type(key) is not str for key in value):
        raise TypeError("P4 fixture keys must be exact strings")
    return {key: _p4_copy_facts(item, seen) for key, item in value.items()}


class _FixedWormTrustedArtifactResolver(_Opaque):
    """Hold one immutable hostile-data snapshot and its fixed terminal root."""

    __slots__ = ("_records", "_snapshot", "_terminal", "__weakref__")

    def __init__(self, token, fixture_facts):
        if token not in _P4_PENDING_RESOLVER_TOKENS:
            raise TypeError("P4 resolver requires fixed broker construction")
        _P4_PENDING_RESOLVER_TOKENS.remove(token)
        records = []
        facts = {"artifacts": []} if fixture_facts is None else _p4_copy_facts(fixture_facts)
        if type(facts) is not dict or set(facts) != {"artifacts"}:
            raise ValueError("P4 fixture facts must contain only artifacts")
        if type(facts["artifacts"]) is not list:
            raise TypeError("P4 fixture artifacts must be an exact list")
        for entry in facts["artifacts"]:
            if type(entry) is not dict or set(entry) != {"ref", "bytes"}:
                raise ValueError("P4 fixture artifact requires only ref and bytes")
            key = _p4_artifact_reference_bytes(entry["ref"])
            if type(entry["bytes"]) is not bytes:
                raise TypeError("P4 fixture artifact must contain exact bytes")
            raw = entry["bytes"]
            parsed = _hs_parse_canonical(raw)
            external = key in _P4_EXTERNAL_BY_REF
            if not external and entry["ref"]["schema"] != "dskit.final-replay-entry/v1" and (
                type(parsed) is not dict or parsed.get("schema") != entry["ref"]["schema"]
            ):
                raise ValueError("P4 fixture artifact schema differs from reference")
            if any(previous == key for previous, _raw in records):
                raise ValueError("duplicate P4 fixture reference")
            records.append((key, raw))
        object.__setattr__(self, "_records", tuple(records))
        snapshot = object.__new__(_P4ResolverSnapshot)
        object.__setattr__(snapshot, "_generation", 0)
        terminal = object.__new__(_FixedTerminalArtifactVerifier)
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_terminal", terminal)
        _P4_RESOLVERS[self] = (self._records, snapshot, terminal, 0)
        _P4_TERMINALS[terminal] = (self, snapshot, _P4_EXTERNAL_RECORDS)

    def __setattr__(self, name, value):
        raise AttributeError("P4 fixture resolver is frozen")

    def _lookup(self, key):
        """Return copied bytes only for the exact held reference."""
        for reference, raw in self._records:
            if reference == key:
                return raw
        raise ValueError("missing admission in fixed P4 fixture snapshot")

    def snapshot(self):
        """Return the same opaque immutable snapshot; never choose a generation."""
        _p4_snapshot_integrity(self, self._snapshot)
        return self._snapshot

    def resolve(self, snapshot, exact_ref_bytes):
        """Resolve exact retained bytes without granting trust or lifecycle use."""
        return _P4_CHECKED_RESOLVE(self, snapshot, exact_ref_bytes)

    def _checked_resolve(self, snapshot, exact_ref_bytes):
        """Check issued identity/dispatch before reading the immutable records."""
        _p4_snapshot_integrity(self, snapshot)
        if type(exact_ref_bytes) is not bytes:
            raise TypeError("exact P4 snapshot reference bytes required")
        ref = _hs_parse_canonical(exact_ref_bytes)
        if _p4_artifact_reference_bytes(ref) != exact_ref_bytes:
            raise ValueError("P4 snapshot reference mismatch")
        return _P4_RESOLVER_LOOKUP(self, exact_ref_bytes)

    def close_admission(self, snapshot, admission_ref, live_projection):
        """Verify recursive local/terminal evidence without creating authority."""
        return _P4_CLOSE_ADMISSION(self, snapshot, admission_ref, live_projection)


class _SyntheticP4CapturedAuthorizationAuthority(_DevelopmentBroker,
                                                 CapturedAuthorizationAuthority,
                                                 _Opaque):
    """Fixed nondeployment issuer of one atomic, in-process captured batch."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("P4 synthetic authority is final")

    def __init__(self, token, resolver):
        if token not in _P4_PENDING_TOKENS:
            raise TypeError("P4 authority requires a one-shot private factory token")
        _P4_PENDING_TOKENS.remove(token)
        _DevelopmentBroker.__init__(self, {}, None, [], [], 0, True)
        self._p4_resolver = resolver

    @property
    def deployment_eligible(self):
        """Return False for this permanently synthetic capability."""
        return False

    def authorize_capture_set(
        self, captures, admission_ref, *, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonces,
    ):
        """Issue through the same checked authority and lifecycle ledger."""
        return CapturedAuthorizationAuthority.authorize_capture_set(
            self, captures, admission_ref,
            consumer_run_identity=consumer_run_identity,
            process_measurement_sha256=process_measurement_sha256,
            runtime_sha256=runtime_sha256,
            transition_nonces=transition_nonces,
        )

    def captured_port_set(self, record, session):
        """Mint through the same checked authority and lifecycle ledger."""
        return CapturedAuthorizationAuthority.captured_port_set(self, record, session)

    def _validate_capture_request(self, captures, runtime, nonces):
        """Validate complete live tuples without advancing any lifecycle state."""
        if type(captures) is not tuple or not captures:
            raise TypeError("P4 captures require a nonempty exact tuple")
        if type(nonces) is not tuple or len(nonces) != len(captures):
            raise ValueError("P4 nonce tuple must match capture cardinality")
        if any(type(nonce) is not str or not nonce for nonce in nonces):
            raise ValueError("P4 nonces must be exact nonempty strings")
        if len(set(nonces)) != len(nonces) or any(nonce in self._nonces for nonce in nonces):
            raise ValueError("P4 transition nonces must be unique and unused")
        run = runtime["consumer_run_identity"]
        if type(run) is not str or not run:
            raise ValueError("P4 consumer run must be an exact nonempty string")
        for name in ("process_measurement_sha256", "runtime_sha256"):
            if type(runtime[name]) is not str:
                raise TypeError("P4 runtime digests must be exact strings")
            _require_sha256(runtime[name], name)
        streams, ports, documents = set(), set(), set()
        for capture in captures:
            if type(capture) is not tuple or len(capture) != 3:
                raise TypeError("P4 capture entries require exact three-item tuples")
            published, frozen, port = capture
            if type(published) is not _Published or type(frozen) is not _Frozen:
                raise TypeError("P4 requires exact live published and frozen handles")
            if type(port) is not dict or any(type(item) is not str for item in port.values()):
                raise TypeError("P4 port requires exact built-in string fields")
            expected = _DevelopmentBroker.derive_consumer_port(self, frozen)
            if port != expected:
                raise ValueError("P4 port differs from the frozen derived port")
            intern = self._frozen_publication(frozen)
            if intern is not published:
                raise ValueError("P4 frozen publication binding differs")
            stream = self._stream_for_published(published, "P4 live publication is required")
            if any(entry[1] == stream for entry in self._p4_ledger._legacy_captures()):
                raise ValueError("P4 stream was already captured by the same legacy ledger")
            self._recover(stream)
            self._require_head(stream, "PUBLISHED")
            if port["consumer_document_sha256"] in self._p4_ledger._p4_stream_documents(stream):
                raise ValueError("P4 consumer document already captured this stream")
            subject = self._stream_pin[stream]
            if run in (subject.producer["run_identity"], subject.session_run_identity):
                raise ValueError("P4 consumer run must differ from producer run")
            key = _canonical_bytes(port)
            if stream in streams or key in ports:
                raise ValueError("duplicate P4 stream or consumer port")
            streams.add(stream)
            ports.add(key)
            documents.add(port["consumer_document_sha256"])
        if len(documents) != 1:
            raise ValueError("P4 captures must belong to one frozen consumer document")
        if any(session._kind == "producer" and not session._ended for session in self._sessions.values()):
            raise ValueError("producer session must end before P4 capture")


_P4_BASE_DISPATCH = CapturedAuthorizationAuthority.authorize_capture_set
_P4_FINAL_DISPATCH = _SyntheticP4CapturedAuthorizationAuthority.authorize_capture_set
_P4_BASE_PORT_SET = CapturedAuthorizationAuthority.captured_port_set
_P4_FINAL_PORT_SET = _SyntheticP4CapturedAuthorizationAuthority.captured_port_set
_P4_REQUEST_CHECK = _SyntheticP4CapturedAuthorizationAuthority._validate_capture_request
_P4_RESOLVER_LOOKUP = _FixedWormTrustedArtifactResolver._lookup


def _p4_require_issued_authority(authority):
    """Recheck exact broker identity and dispatch without spending authority.

    ADR-0143 Decision point 4: the authority/ledger-identity checks below stay
    shared (both authority instances use the identical final class); the
    resolver-type-specific checks moved into an explicit closed if/elif/else,
    never isinstance, on ``type(issued[0])`` -- never a shared permissive
    dispatch helper. The legacy (``if``) arm is byte-identical to the prior
    single-branch checks; the dynamic (``elif``) arm is new and self-contained.
    """
    if type(authority) is not _SyntheticP4CapturedAuthorizationAuthority:
        raise TypeError("exact broker-issued P4 capability is required")
    issued = _P4_ISSUED.get(authority)
    if (
        issued is None
        or authority._p4_resolver is not issued[0]
        or CapturedAuthorizationAuthority.authorize_capture_set is not _P4_BASE_DISPATCH
        or type(authority).authorize_capture_set is not _P4_FINAL_DISPATCH
        or CapturedAuthorizationAuthority.captured_port_set is not _P4_BASE_PORT_SET
        or type(authority).captured_port_set is not _P4_FINAL_PORT_SET
        or type(authority)._validate_capture_request is not _P4_REQUEST_CHECK
        or "authorize_capture_set" in authority.__dict__
        or "captured_port_set" in authority.__dict__
    ):
        raise TypeError("exact broker-issued P4 capability is required")
    resolver = issued[0]
    if type(resolver) is _FixedWormTrustedArtifactResolver:
        if (
            resolver._records is not issued[1]
            or type(resolver)._lookup is not _P4_RESOLVER_LOOKUP
        ):
            raise TypeError("exact broker-issued P4 capability is required")
    elif type(resolver) is _DynamicP4TrustedArtifactResolver:
        if (
            type(resolver).resolve is not _P4_DYNAMIC_RESOLVER_LOOKUP
            or issued[1] is not resolver._graph
        ):
            raise TypeError("exact broker-issued P4 capability is required")
    else:
        raise TypeError("exact broker-issued P4 capability is required")


_P4_ISSUED_CHECK = _p4_require_issued_authority


def _p4_checked_dispatch(authority, captures, admission_ref, **runtime):
    """Check broker identity and frozen dependencies before all P4 validation."""
    if _p4_require_issued_authority is not _P4_ISSUED_CHECK:
        raise TypeError("P4 authority identity dispatch integrity refused")
    _P4_ISSUED_CHECK(authority)
    _p4_reference_bytes(admission_ref, authority._p4_resolver)
    ledger = _LIFECYCLE_LEDGERS.get(authority)
    if ledger is None or authority._p4_ledger is not ledger or _P4_COMMIT is not _LifecycleAuthorizationLedger.commit_p4_batch:
        raise TypeError("P4 same-domain ledger required")
    return _P4_COMMIT(ledger, captures, admission_ref, runtime)


def _p4_checked_port_set_dispatch(authority, record, session):
    """Mint one non-enumerable tape view from exact committed identities."""
    if _p4_port_set_state_integrity is not _P4_PORT_SET_STATE_CHECK:
        raise TypeError("P4 port-set state integrity refused")
    _P4_PORT_SET_STATE_CHECK()
    if _p4_require_issued_authority is not _P4_ISSUED_CHECK:
        raise TypeError("P4 authority identity dispatch integrity refused")
    _P4_ISSUED_CHECK(authority)
    if type(record) is not CapturedAuthorizationRecord or type(session) is not LaunchSession:
        raise TypeError("exact P4 record and session required")
    ledger = _P4_RECORDS.get(record)
    if ledger is None or ledger is not _LIFECYCLE_LEDGERS.get(authority) or authority._p4_ledger is not ledger:
        raise ValueError("P4 committed record required")
    with ledger._lock:
        ledger._check()
        matches = [entry for entry in ledger._p4_entries() if entry[4] is record]
        _hs_refuse(len(matches) == 1, "P4 committed record required")
        entry = matches[0]
        _hs_refuse(session is entry[5] and not session._ended
                   and _p4_session_pin(session) == entry[6],
                   "P4 record/session mismatch")
        seal = hmac.new(authority._map_key,
                        b"P4 LaunchSession\x00" + _hs_canonical_bytes(dict(session._runtime)),
                        hashlib.sha256).hexdigest()
        _hs_refuse(hmac.compare_digest(entry[7], seal), "P4 session seal refused")
        request = _hs_parse_canonical(entry[2])
        audit = _hs_parse_canonical(entry[3])
        _hs_refuse(audit["batch_sha256"] == _digest(_hs_canonical_bytes(
            {key: value for key, value in audit.items() if key != "batch_sha256"})),
            "P4 committed batch integrity refused")
        retained_state = _p4_retained_capture_state(authority, ledger, record)
        retained = retained_state._handles
        _hs_refuse(type(retained) is tuple and len(retained) == 2
                   and retained_state._view is None
                   and retained_state._used == frozenset()
                   and len(request["captures"]) == len(audit["streams"]) == 2
                   and len(audit["ports"]) == len(audit["receipts"]) == 2
                   and audit["replay"] is not None,
                   "exact committed replay tape pair required")
        captured_set = audit["set"]
        replay_evidence = audit["replay"]
        _p4_verify_local_signed(captured_set, "captured_authorization_set_sha256",
                                "security-broker", "captured-authorization")
        _p4_verify_local_signed(replay_evidence,
                                "replay_capture_admission_evidence_sha256",
                                "security-broker", "replay-capture-admission")
        _hs_refuse(len(captured_set["entries"]) == len(replay_evidence["issued_ports"]) == 2
                   and replay_evidence["captured_authorization_set_sha256"]
                   == captured_set["captured_authorization_set_sha256"],
                   "committed replay tape adjacency refused")
        by_name = {}
        document_digests = set()
        for index, (published, frozen, port) in enumerate(retained):
            projected = request["captures"][index]
            _hs_refuse(type(published) is _Published and type(frozen) is _Frozen
                       and type(port) is MappingProxyType
                       and projected[0] == id(published) and projected[1] == id(frozen)
                       and projected[2] == dict(port)
                       and projected[3] == published.descriptor
                       and projected[4] == _digest(_canonical_bytes(frozen.source)),
                       "retained P4 capture identity mismatch")
            name = port.get("consumer_input")
            document_sha256 = port.get("consumer_document_sha256")
            _hs_refuse(type(name) is str and type(document_sha256) is str
                       and name not in by_name, "exact committed replay tape pair required")
            stream = audit["streams"][index]
            signed_port = audit["ports"][index]
            receipt = audit["receipts"][index]
            set_entry = captured_set["entries"][index]
            replay_entry = replay_evidence["issued_ports"][index]
            _p4_verify_local_signed(signed_port, "captured_port_authorization_sha256",
                                    "security-broker", "captured-port-authorization")
            _p4_verify_local_signed(receipt, "lifecycle_captured_receipt_sha256",
                                    "security-broker", "lifecycle-capture")
            _hs_refuse(receipt["stream_id"] == stream
                       and receipt["captured_port_authorization_sha256"]
                       == signed_port["captured_port_authorization_sha256"]
                       and all(item["planned_entry_sha256"]
                               == signed_port["planned_entry_sha256"]
                               for item in (receipt, set_entry, replay_entry))
                       and set_entry["captured_port_authorization_sha256"]
                       == replay_entry["captured_port_authorization_sha256"]
                       == signed_port["captured_port_authorization_sha256"]
                       and set_entry["lifecycle_captured_receipt_sha256"]
                       == replay_entry["lifecycle_captured_receipt_sha256"]
                       == receipt["lifecycle_captured_receipt_sha256"],
                       "committed replay tape adjacency refused")
            _entry, _request, _audit, found = ledger._p4_record_capture(
                record, stream, document_sha256)
            _hs_refuse(found == index, "retained P4 capture order mismatch")
            by_name[name] = (published, document_sha256, stream)
            document_digests.add(document_sha256)
        _hs_refuse(set(by_name) == {"tape_manifest", "tape_data"}
                   and len(document_digests) == 1,
                   "exact committed replay tape pair required")
        view = object.__new__(CapturedPortSet)
        retained_state._mint(_P4_PORT_STATE_MUTATE, view)
        _P4_CAPTURE_STATE_PINS[retained_state] = _p4_capture_state_seal(
            authority, ledger, record, retained_state)
        view_state = (authority, ledger, record, session)
        _P4_PORT_SET_VIEWS[view] = (
            *view_state, _p4_port_view_seal(authority, view, view_state),
        )
        return view


_P4_PORT_SET_DISPATCH = _p4_checked_port_set_dispatch


def _development_p4_broker(*, fixture_facts=None):
    """Construct the fixed synthetic P4 issuer without selectable trust.

    Parameters
    ----------
    fixture_facts : dict or None
        Hostile data only: ``artifacts`` is a list of exact ``ref``/``bytes``
        entries. References name closed ADR artifacts or fixed external corpus
        identities. Copied bytes confer no authority: complete held closure and
        live identity checks precede the atomic in-process ledger transaction.

    Returns
    -------
    CapturedAuthorizationAuthority
        Noncopyable synthetic capability with ordinary v1 lifecycle methods.

    Raises
    ------
    TypeError
        A fixture uses an object, subclass, or forbidden data type.
    ValueError
        A fixture has aliases, unknown fields, duplicate references, or
        noncanonical artifact bytes.
    """
    token = object()
    _P4_PENDING_RESOLVER_TOKENS.add(token)
    try:
        resolver = _FixedWormTrustedArtifactResolver(token, fixture_facts)
        _P4_PENDING_TOKENS.add(token)
        authority = _SyntheticP4CapturedAuthorizationAuthority(token, resolver)
    finally:
        _P4_PENDING_TOKENS.discard(token)
        _P4_PENDING_RESOLVER_TOKENS.discard(token)
    _P4_ISSUED[authority] = (resolver, resolver._records)
    return authority

# ---------------------------------------------------------------------------
# ADR-0125 structural/signature preflight (F5a Packet 3)
# ---------------------------------------------------------------------------


class HistoricalStudyRevocations(ABC):
    """Verify one already-authenticated immutable revocation snapshot."""

    @abstractmethod
    def is_unrevoked(self, snapshot_sha256, key_id, key_version, now_ms):
        """Return True only when the named key is unrevoked at ``now_ms``."""


class NonAuthorizingAdr0125StructuralSignaturePreflight:
    """Opaque proof of local syntax and signatures, never lifecycle authority."""

    __slots__ = ()
    deployment_eligible = False

    def __new__(cls, *args, **kwargs):
        """Refuse direct construction; only the verifier may mint this result."""
        raise TypeError("opaque preflight result")

    def __copy__(self):
        """Refuse shallow copies of the opaque result."""
        raise TypeError("opaque preflight result")

    def __deepcopy__(self, memo):
        """Refuse deep copies of the opaque result."""
        raise TypeError("opaque preflight result")

    def __getstate__(self):
        """Refuse pickle state for the opaque result."""
        raise TypeError("opaque preflight result")

    def __reduce__(self):
        """Refuse pickle reduction for the opaque result."""
        raise TypeError("opaque preflight result")

    def __reduce_ex__(self, protocol):
        """Refuse protocol-specific pickle reduction for the opaque result."""
        raise TypeError("opaque preflight result")


_HS_HASH = re.compile(r"[0-9a-f]{64}")
_HS_ACTION_IDS = frozenset(
    (
        "tape-data-materialization",
        "tape-manifest-materialization",
        "A1",
        "A2",
        "A3",
        "A4",
    )
)
_HS_PROFILE_PHASES = frozenset(("bootstrap", "scope-action", "replay-pre-final"))

_HS_SHAPES = {
    "Key": {"key_id": "S", "key_version": "I"},
    "ActionSubject": {
        "kind": ("literal", "action"),
        "action_id": "ActionId",
        "action_intent_sha256": "H",
    },
    "ReplaySubject": {
        "kind": ("literal", "replay"),
        "replay_id": ("literal", "control", "crash-restart"),
        "replay_intent_sha256": "H",
    },
    "PublishedInputEntry": {
        "input_id": "S",
        "kind": "S",
        "root_ref": "S",
        "root_id": "S",
        "snapshot_version": "S",
        "member_manifest_sha256": "H",
        "producer_run_identity": "S",
        "producer_document_sha256": "H",
        "producer_node": "S",
        "producer_output": "S",
        "publication_receipt_schema": (
            "literal",
            "dskit.root-publication-receipt/v1",
            "dskit.lifecycle-publication-receipt/v2",
        ),
        "publication_receipt_sha256": "H",
        "contract_sha256": "H",
    },
    "InputContract": {
        "binding_id": "S",
        "consumer_node": "S",
        "consumer_input": "S",
        "source_kind": ("literal", "published-input", "predecessor-output"),
        "source_ref": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "OutputContract": {
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
        "producer_node": "S",
        "producer_output": "S",
        "media_type": "S",
    },
    "PredecessorOutputRef": {
        "predecessor_action_id": "ActionId",
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "EdgeSetEntry": {
        "consumer_action_id": "ActionId",
        "binding_id": "S",
        "predecessor_action_id": "ActionId",
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "ActionIntent": {
        "schema": ("literal", "dskit.action-intent/v1"),
        "study_id": "S",
        "action_id": "ActionId",
        "consumer_document_contract_sha256": "H",
        "kind": "S",
        "topological_position": "I",
        "predecessor_action_ids": ("array", "ActionId"),
        "predecessor_output_refs": ("array", "PredecessorOutputRef"),
        "root_published_input_ids": ("array", "S"),
        "required_input_contracts": ("array", "InputContract"),
        "output_contract": ("shape", "OutputContract"),
        "closed_parameters_sha256": "H",
        "component_manifest_sha256": "H",
        "candidate_selection_sha256": "H",
        "policy_sha256": "H",
        "action_intent_sha256": "H",
    },
    "ReplayIntent": {
        "schema": ("literal", "dskit.replay-intent/v1"),
        "study_id": "S",
        "replay_id": ("literal", "control", "crash-restart"),
        "consumer_document_contract_sha256": "H",
        "required_input_contracts": ("array", "InputContract"),
        "environment_identity_sha256": "H",
        "execution_profile_sha256": "H",
        "component_manifest_sha256": "H",
        "crash_schedule_sha256": "H",
        "recovery_policy_sha256": "H",
        "policy_sha256": "H",
        "replay_intent_sha256": "H",
    },
    "CesEntry": {
        "binding_id": "S",
        "consumer_document_sha256": "H",
        "consumer_node": "S",
        "consumer_input": "S",
        "purpose": "S",
        "descriptor_root_ref": "S",
        "descriptor_snapshot_version": "S",
        "descriptor_document_sha256": "H",
        "descriptor_node": "S",
        "descriptor_output": "S",
        "descriptor_purpose": "S",
        "published_input": ("shape", "PublishedInputEntry"),
    },
    "PlannedCaptureEntry": {
        "schema": ("literal", "dskit.planned-capture-entry/v1"),
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "binding_id": "S",
        "consumer_document_sha256": "H",
        "consumer_node": "S",
        "consumer_input": "S",
        "purpose": "S",
        "descriptor_root_ref": "S",
        "descriptor_snapshot_version": "S",
        "descriptor_document_sha256": "H",
        "descriptor_node": "S",
        "descriptor_output": "S",
        "descriptor_purpose": "S",
        "published_input": ("shape", "PublishedInputEntry"),
        "planned_entry_sha256": "H",
    },
    "ActionIntentSet": {
        "schema": ("literal", "dskit.action-intent-set/v1"),
        "entries": ("array", "ActionIntent"),
        "action_intent_set_sha256": "H",
    },
    "ReplayIntentSet": {
        "schema": ("literal", "dskit.replay-intent-set/v1"),
        "entries": ("array", "ReplayIntent"),
        "replay_intent_set_sha256": "H",
    },
}


_HS_AUTHORITY_REFS = (
    {
        "kind": ("literal", "scope-intent-gate-set"),
        "scope_intent_sha256": "H",
        "gate_set_sha256": "H",
    },
    {
        "kind": ("literal", "scope-authorization-action"),
        "scope_authorization_sha256": "H",
        "action_intent_sha256": "H",
    },
    {
        "kind": ("literal", "scope-authorization-replay"),
        "scope_authorization_sha256": "H",
        "replay_intent_sha256": "H",
    },
)

_HS_ENVELOPES = {
    "PublishedInputSet": {
        "schema": ("literal", "dskit.published-input-set/v2"),
        "study_id": "S",
        "phase": ("literal", "root-g1-g2", "stage-consumer", "replay-consumer"),
        "purpose": "S",
        "entries": ("array", "PublishedInputEntry"),
        "published_input_set_sha256": "H",
    },
    "ScopeIntent": {
        "schema": ("literal", "dskit.historical-study-scope-intent/v1"),
        "study_id": "S",
        "purpose": "S",
        "published_input_set_sha256": "H",
        "action_intents": ("array", "ActionIntent"),
        "action_intent_set_sha256": "H",
        "action_dag_sha256": "H",
        "edge_set_sha256": "H",
        "replay_intents": ("array", "ReplayIntent"),
        "replay_intent_set_sha256": "H",
        "environment_identity_sha256": "H",
        "execution_profile_sha256": "H",
        "component_manifest_sha256": "H",
        "candidate_inventory_sha256": "H",
        "policy_set_sha256": "H",
        "historical_study_scope_intent_sha256": "H",
    },
    "CES": {
        "schema": ("literal", "dskit.capture-expectation-set/v1"),
        "study_id": "S",
        "phase": "ProfilePhase",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "published_input_set_sha256": "H",
        "entries": ("array", "CesEntry"),
        "capture_expectation_set_sha256": "H",
    },
    "PEA": {
        "schema": ("literal", "dskit.plan-evaluation-authorization/v1"),
        "study_id": "S",
        "phase": "ProfilePhase",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "published_input_set_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "authority_ref": "AuthorityRef",
        "plan_evaluation_authorization_sha256": "H",
    },
    "BVP": {
        "schema": ("literal", "dskit.broker-verified-plan/v1"),
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "study_id": "S",
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "planning_rules_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
    },
    "CAS": {
        "schema": ("literal", "dskit.capture-admission-set/v1"),
        "study_id": "S",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "entries": ("array", "PlannedCaptureEntry"),
        "capture_admission_set_sha256": "H",
    },
    "ActionAdmission": {
        "schema": ("literal", "dskit.action-execution-admission/v1"),
        "study_id": "S",
        "scope_authorization_sha256": "H",
        "action_id": "ActionId",
        "action_intent_sha256": "H",
        "historical_study_stage_admission_sha256": "H",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "capture_admission_set_sha256": "H",
        "logical_execution_id": "S",
        "run_id": "S",
        "recovery_journal_sha256": "H",
        "recovery_fence_sha256": "H",
        "recovery_attempt_rules_sha256": "H",
        "action_execution_admission_sha256": "H",
    },
    "ReplayAdmission": {
        "schema": ("literal", "dskit.final-replay-admission/v1"),
        "study_id": "S",
        "final_manifest_sha256": "H",
        "final_replay_entry_sha256": "H",
        "replay_id": ("literal", "control", "crash-restart"),
        "replay_intent_sha256": "H",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "capture_admission_set_sha256": "H",
        "logical_execution_id": "S",
        "run_id": "S",
        "recovery_journal_sha256": "H",
        "recovery_fence_sha256": "H",
        "recovery_attempt_rules_sha256": "H",
        "final_replay_admission_sha256": "H",
    },
}

_HS_SIGNED_SUFFIX = {
    "issuance_basis_sha256": "H",
    "issuer_role": "S",
    "key_usage": "S",
    "signature_alg": ("literal", "Ed25519"),
    "issued_at_ms": "I",
    "not_before_ms": "I",
    "expires_at_ms": "I",
    "revocation_snapshot_sha256": "H",
    "key": ("shape", "Key"),
    "signature": "S",
}

_HS_SELF_FIELDS = {
    "PublishedInputSet": "published_input_set_sha256",
    "ScopeIntent": "historical_study_scope_intent_sha256",
    "CES": "capture_expectation_set_sha256",
    "PEA": "plan_evaluation_authorization_sha256",
    "BVP": "broker_verified_plan_sha256",
    "CAS": "capture_admission_set_sha256",
    "ActionAdmission": "action_execution_admission_sha256",
    "ReplayAdmission": "final_replay_admission_sha256",
}

_HS_ROLE_USE = {
    "root_pis": ("data-publisher", "published-input-set-g1-g2"),
    "phase_pis": ("study-lifecycle", "published-input-set-study"),
    "scope": ("study-lifecycle", "historical-study-scope-intent"),
    "ces": ("study-lifecycle", "capture-expectation"),
    "pea": ("security-broker", "plan-evaluation"),
    "bvp": ("security-broker", "plan-verifier"),
    "cas": ("study-lifecycle", "capture-admission"),
    "action_admission": ("study-lifecycle", "action-execution-admission"),
    "replay_admission": ("study-lifecycle", "final-replay-admission"),
}


def _hs_refuse(condition, message="ADR-0125 preflight refused"):
    if not condition:
        raise ValueError(message)


def _hs_canonical_bytes(value):
    def admissible(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float:
            _hs_refuse(math.isfinite(item))
            return
        if type(item) is list:
            for child in item:
                admissible(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                _hs_refuse(type(key) is str)
                admissible(child)
            return
        raise ValueError("non-JSON value")

    admissible(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("canonical JSON refused") from exc


def _hs_parse_canonical(raw):
    if type(raw) is not bytes:
        raise TypeError("exact bytes are required")

    def reject_constant(value):
        raise ValueError("non-finite JSON constant")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("canonical JSON refused") from exc
    _hs_refuse(_hs_canonical_bytes(value) == raw, "non-canonical JSON")
    return value


def _hs_validate_spec(value, spec):
    if spec == "S":
        _hs_refuse(type(value) is str)
    elif spec == "H":
        _hs_refuse(type(value) is str and _HS_HASH.fullmatch(value) is not None)
    elif spec == "I":
        _hs_refuse(type(value) is int)
    elif spec == "ActionId":
        _hs_refuse(type(value) is str and value in _HS_ACTION_IDS)
    elif spec == "ProfilePhase":
        _hs_refuse(type(value) is str and value in _HS_PROFILE_PHASES)
    elif spec == "AuthorityRef":
        matches = 0
        for fields in _HS_AUTHORITY_REFS:
            try:
                _hs_validate_object(value, fields)
            except ValueError:
                continue
            matches += 1
        _hs_refuse(matches == 1)
    elif type(spec) is tuple and spec[0] == "literal":
        _hs_refuse(type(value) is str and value in spec[1:])
    elif type(spec) is tuple and spec[0] == "shape":
        _hs_validate_object(value, _HS_SHAPES[spec[1]])
    elif type(spec) is tuple and spec[0] == "array":
        _hs_refuse(type(value) is list)
        for item in value:
            child = spec[1]
            if child in _HS_SHAPES:
                _hs_validate_object(item, _HS_SHAPES[child])
            else:
                _hs_validate_spec(item, child)
    elif type(spec) is tuple and spec[0] == "union":
        matches = 0
        for name in spec[1:]:
            try:
                _hs_validate_object(value, _HS_SHAPES[name])
            except ValueError:
                continue
            matches += 1
        _hs_refuse(matches == 1)
    else:
        raise ValueError("unknown preflight schema")


def _hs_validate_object(value, fields):
    _hs_refuse(type(value) is dict and set(value) == set(fields))
    for name, spec in fields.items():
        _hs_validate_spec(value[name], spec)


def _hs_validate_envelope(value, name):
    fields = dict(_HS_ENVELOPES[name])
    fields.update(_HS_SIGNED_SUFFIX)
    _hs_validate_object(value, fields)


def _hs_self_digest(value, self_name, *, signed=False):
    payload = dict(value)
    payload.pop(self_name)
    if signed:
        payload.pop("signature")
    preimage = _hs_canonical_bytes(payload)
    _hs_refuse(hashlib.sha256(preimage).hexdigest() == value[self_name])
    return preimage


def _hs_strict_sorted(items, key):
    keys = [key(item) for item in items]
    _hs_refuse(all(left < right for left, right in zip(keys, keys[1:])))


def _hs_scalar_key(value):
    return _hs_canonical_bytes(value)


def _hs_tuple_key(*values):
    return _hs_canonical_bytes(list(values))


class HistoricalStudyEnvelopePreflight:
    """Validate a fixed synthetic ADR-0125 byte tuple without granting authority."""

    __slots__ = ("_keyring", "_clock", "_revocations")

    def __init__(self, keyring, clock, revocations):
        if not isinstance(keyring, ReleaseKeyring):
            raise TypeError("ReleaseKeyring is required")
        if not isinstance(clock, TrustedClock):
            raise TypeError("TrustedClock is required")
        if not isinstance(revocations, HistoricalStudyRevocations):
            raise TypeError("HistoricalStudyRevocations is required")
        self._keyring = keyring
        self._clock = clock
        self._revocations = revocations

    def verify(
        self,
        root_pis_bytes,
        phase_pis_bytes,
        scope_intent_bytes,
        action_intent_set_bytes,
        edge_set_bytes,
        replay_intent_set_bytes,
        ces_bytes,
        pea_bytes,
        bvp_bytes,
        cas_bytes,
        selected_admission_bytes,
    ):
        """Return an opaque no-effect result for the exact eleven-byte tuple."""
        raw = {
            "root_pis": root_pis_bytes,
            "phase_pis": phase_pis_bytes,
            "scope": scope_intent_bytes,
            "action_set": action_intent_set_bytes,
            "edges": edge_set_bytes,
            "replay_set": replay_intent_set_bytes,
            "ces": ces_bytes,
            "pea": pea_bytes,
            "bvp": bvp_bytes,
            "cas": cas_bytes,
            "admission": selected_admission_bytes,
        }
        values = {name: _hs_parse_canonical(value) for name, value in raw.items()}
        admission_name = self._validate_shapes_and_self(values)
        profile, selected = self._validate_profiles(values, raw, admission_name)
        self._validate_ordering(values)
        self._validate_adjacency(values, raw, profile, selected, admission_name)
        self._verify_trust(values, profile, admission_name)
        return object.__new__(NonAuthorizingAdr0125StructuralSignaturePreflight)

    @staticmethod
    def _validate_shapes_and_self(values):
        envelope_names = {
            "root_pis": "PublishedInputSet",
            "phase_pis": "PublishedInputSet",
            "scope": "ScopeIntent",
            "ces": "CES",
            "pea": "PEA",
            "bvp": "BVP",
            "cas": "CAS",
        }
        for slot, name in envelope_names.items():
            _hs_validate_envelope(values[slot], name)
            _hs_self_digest(values[slot], _HS_SELF_FIELDS[name], signed=True)

        admission_schema = (
            values["admission"].get("schema")
            if type(values["admission"]) is dict
            else None
        )
        if admission_schema == "dskit.action-execution-admission/v1":
            admission_name = "ActionAdmission"
        elif admission_schema == "dskit.final-replay-admission/v1":
            admission_name = "ReplayAdmission"
        else:
            raise ValueError("selected admission schema refused")
        _hs_validate_envelope(values["admission"], admission_name)
        _hs_self_digest(
            values["admission"], _HS_SELF_FIELDS[admission_name], signed=True
        )

        _hs_validate_object(values["action_set"], _HS_SHAPES["ActionIntentSet"])
        _hs_validate_object(values["replay_set"], _HS_SHAPES["ReplayIntentSet"])
        _hs_refuse(type(values["edges"]) is list)
        for edge in values["edges"]:
            _hs_validate_object(edge, _HS_SHAPES["EdgeSetEntry"])

        action_groups = (
            values["scope"]["action_intents"],
            values["action_set"]["entries"],
        )
        for actions in action_groups:
            for action in actions:
                _hs_self_digest(action, "action_intent_sha256")
        replay_groups = (
            values["scope"]["replay_intents"],
            values["replay_set"]["entries"],
        )
        for replays in replay_groups:
            for replay in replays:
                _hs_self_digest(replay, "replay_intent_sha256")
        _hs_self_digest(values["action_set"], "action_intent_set_sha256")
        _hs_self_digest(values["replay_set"], "replay_intent_set_sha256")
        for entry in values["cas"]["entries"]:
            _hs_self_digest(entry, "planned_entry_sha256")
        return admission_name

    @staticmethod
    def _validate_profiles(values, raw, admission_name):
        root_pis = values["root_pis"]
        phase_pis = values["phase_pis"]
        ces = values["ces"]
        pea = values["pea"]
        profile = ces["phase"]
        _hs_refuse(pea["phase"] == profile)
        _hs_refuse(root_pis["phase"] == "root-g1-g2")

        subjects = (
            ces["subject_ref"],
            pea["subject_ref"],
            values["bvp"]["subject_ref"],
            values["cas"]["subject_ref"],
        )
        subject_bytes = [_hs_canonical_bytes(subject) for subject in subjects]
        _hs_refuse(len(set(subject_bytes)) == 1)
        subject = subjects[0]

        if profile == "bootstrap":
            _hs_refuse(raw["root_pis"] == raw["phase_pis"])
            _hs_refuse(phase_pis["phase"] == "root-g1-g2")
            _hs_refuse(
                subject["kind"] == "action" and admission_name == "ActionAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-intent-gate-set")
            _hs_refuse(
                ref["scope_intent_sha256"]
                == values["scope"]["historical_study_scope_intent_sha256"]
            )
        elif profile == "scope-action":
            _hs_refuse(phase_pis["phase"] == "stage-consumer")
            _hs_refuse(
                subject["kind"] == "action" and admission_name == "ActionAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-authorization-action")
            _hs_refuse(ref["action_intent_sha256"] == subject["action_intent_sha256"])
        elif profile == "replay-pre-final":
            _hs_refuse(phase_pis["phase"] == "replay-consumer")
            _hs_refuse(
                subject["kind"] == "replay" and admission_name == "ReplayAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-authorization-replay")
            _hs_refuse(ref["replay_intent_sha256"] == subject["replay_intent_sha256"])
        else:
            raise ValueError("profile refused")

        if subject["kind"] == "action":
            matches = [
                child
                for child in values["scope"]["action_intents"]
                if child["action_id"] == subject["action_id"]
                and child["action_intent_sha256"] == subject["action_intent_sha256"]
            ]
        else:
            matches = [
                child
                for child in values["scope"]["replay_intents"]
                if child["replay_id"] == subject["replay_id"]
                and child["replay_intent_sha256"] == subject["replay_intent_sha256"]
            ]
        _hs_refuse(len(matches) == 1)
        return profile, matches[0]

    @staticmethod
    def _validate_ordering(values):
        for slot in ("root_pis", "phase_pis"):
            _hs_strict_sorted(
                values[slot]["entries"],
                lambda item: _hs_scalar_key(item["input_id"]),
            )

        def action_key(item):
            return _hs_tuple_key(item["topological_position"], item["action_id"])

        for actions in (
            values["scope"]["action_intents"],
            values["action_set"]["entries"],
        ):
            _hs_strict_sorted(actions, action_key)
            _hs_refuse(
                len(actions) == len(_HS_ACTION_IDS)
                and {item["action_id"] for item in actions} == _HS_ACTION_IDS
            )
            for action in actions:
                _hs_strict_sorted(action["predecessor_action_ids"], _hs_scalar_key)
                _hs_strict_sorted(
                    action["predecessor_output_refs"],
                    lambda item: _hs_tuple_key(
                        item["predecessor_action_id"],
                        item["output_name"],
                        item["output_schema"],
                        item["output_version"],
                        item["purpose"],
                    ),
                )
                _hs_strict_sorted(action["root_published_input_ids"], _hs_scalar_key)
                _hs_strict_sorted(
                    action["required_input_contracts"],
                    lambda item: _hs_scalar_key(item["binding_id"]),
                )

        for replays in (
            values["scope"]["replay_intents"],
            values["replay_set"]["entries"],
        ):
            _hs_strict_sorted(replays, lambda item: _hs_scalar_key(item["replay_id"]))
            _hs_refuse(
                [item["replay_id"] for item in replays] == ["control", "crash-restart"]
            )
            for replay in replays:
                _hs_strict_sorted(
                    replay["required_input_contracts"],
                    lambda item: _hs_scalar_key(item["binding_id"]),
                )

        _hs_strict_sorted(
            values["edges"],
            lambda item: _hs_tuple_key(
                item["consumer_action_id"],
                item["binding_id"],
                item["predecessor_action_id"],
                item["output_name"],
                item["output_schema"],
                item["output_version"],
                item["purpose"],
            ),
        )
        _hs_strict_sorted(values["ces"]["entries"], _hs_canonical_bytes)
        _hs_strict_sorted(
            values["cas"]["entries"],
            lambda item: (
                _hs_scalar_key(item["planned_entry_sha256"]),
                _hs_canonical_bytes(item),
            ),
        )

    @staticmethod
    def _validate_adjacency(values, raw, profile, selected, admission_name):
        root_pis = values["root_pis"]
        phase_pis = values["phase_pis"]
        scope = values["scope"]
        action_set = values["action_set"]
        replay_set = values["replay_set"]
        ces = values["ces"]
        pea = values["pea"]
        bvp = values["bvp"]
        cas = values["cas"]
        admission = values["admission"]

        _hs_refuse(
            len(
                {
                    item["study_id"]
                    for item in (
                        root_pis,
                        phase_pis,
                        scope,
                        ces,
                        pea,
                        bvp,
                        cas,
                        admission,
                    )
                }
            )
            == 1
        )
        _hs_refuse(
            scope["published_input_set_sha256"]
            == root_pis["published_input_set_sha256"]
        )
        _hs_refuse(
            _hs_canonical_bytes(scope["action_intents"])
            == _hs_canonical_bytes(action_set["entries"])
        )
        _hs_refuse(
            scope["action_intent_set_sha256"] == action_set["action_intent_set_sha256"]
        )
        _hs_refuse(
            _hs_canonical_bytes(scope["replay_intents"])
            == _hs_canonical_bytes(replay_set["entries"])
        )
        _hs_refuse(
            scope["replay_intent_set_sha256"] == replay_set["replay_intent_set_sha256"]
        )
        _hs_refuse(hashlib.sha256(raw["edges"]).hexdigest() == scope["edge_set_sha256"])

        for child in scope["action_intents"] + scope["replay_intents"]:
            _hs_refuse(child["study_id"] == scope["study_id"])
            _hs_refuse(
                child["component_manifest_sha256"] == scope["component_manifest_sha256"]
            )
        _hs_refuse(
            all(
                item["component_manifest_sha256"] == scope["component_manifest_sha256"]
                for item in (ces, pea, bvp)
            )
        )
        if profile == "replay-pre-final":
            _hs_refuse(
                selected["environment_identity_sha256"]
                == scope["environment_identity_sha256"]
            )
            _hs_refuse(
                selected["execution_profile_sha256"]
                == scope["execution_profile_sha256"]
            )

        _hs_refuse(
            all(
                item["consumer_document_contract_sha256"]
                == selected["consumer_document_contract_sha256"]
                for item in (ces, pea, bvp, cas)
            )
        )
        _hs_refuse(len({item["purpose"] for item in (scope, ces, pea, bvp, cas)}) == 1)
        _hs_refuse(
            len({item["consumer_document_sha256"] for item in (ces, pea, bvp, cas)})
            == 1
        )
        _hs_refuse(
            ces["published_input_set_sha256"]
            == pea["published_input_set_sha256"]
            == phase_pis["published_input_set_sha256"]
        )

        if profile == "replay-pre-final":
            _hs_refuse(
                all(
                    contract["source_kind"] != "predecessor-output"
                    for contract in selected["required_input_contracts"]
                )
            )

        HistoricalStudyEnvelopePreflight._validate_graph(
            scope, values["edges"], root_pis
        )
        HistoricalStudyEnvelopePreflight._validate_capture_entries(
            ces, cas, phase_pis, selected
        )
        HistoricalStudyEnvelopePreflight._validate_digest_chain(
            ces, pea, bvp, cas, admission
        )

        subject = ces["subject_ref"]
        if admission_name == "ActionAdmission":
            _hs_refuse(admission["action_id"] == subject["action_id"])
            _hs_refuse(
                admission["action_intent_sha256"] == subject["action_intent_sha256"]
            )
        else:
            _hs_refuse(admission["replay_id"] == subject["replay_id"])
            _hs_refuse(
                admission["replay_intent_sha256"] == subject["replay_intent_sha256"]
            )

    @staticmethod
    def _validate_graph(scope, edges, root_pis):
        actions = scope["action_intents"]
        by_id = {action["action_id"]: action for action in actions}
        root_ids = [entry["input_id"] for entry in root_pis["entries"]]

        for action in actions:
            refs = action["predecessor_output_refs"]
            projected = []
            for ref in refs:
                if ref["predecessor_action_id"] not in projected:
                    projected.append(ref["predecessor_action_id"])
            _hs_refuse(projected == action["predecessor_action_ids"])
            for predecessor_id in action["predecessor_action_ids"]:
                _hs_refuse(
                    predecessor_id in by_id and predecessor_id != action["action_id"]
                )
                _hs_refuse(
                    by_id[predecessor_id]["topological_position"]
                    < action["topological_position"]
                )
            if not action["predecessor_action_ids"]:
                for input_id in action["root_published_input_ids"]:
                    _hs_refuse(root_ids.count(input_id) == 1)
            else:
                _hs_refuse(not action["root_published_input_ids"])

            for contract in action["required_input_contracts"]:
                same_binding_edges = [
                    edge
                    for edge in edges
                    if edge["consumer_action_id"] == action["action_id"]
                    and edge["binding_id"] == contract["binding_id"]
                ]
                if contract["source_kind"] == "published-input":
                    _hs_refuse(not same_binding_edges)
                    continue
                matches = []
                for edge in same_binding_edges:
                    refs_for_edge = [
                        ref
                        for ref in refs
                        if all(
                            ref[name] == edge[name]
                            for name in (
                                "predecessor_action_id",
                                "output_name",
                                "output_schema",
                                "output_version",
                                "purpose",
                            )
                        )
                    ]
                    if (
                        len(refs_for_edge) == 1
                        and contract["output_schema"] == edge["output_schema"]
                        and contract["output_version"] == edge["output_version"]
                        and contract["purpose"] == edge["purpose"]
                    ):
                        matches.append((edge, refs_for_edge[0]))
                _hs_refuse(len(matches) == 1)
                edge, ref = matches[0]
                producer = by_id[edge["predecessor_action_id"]]["output_contract"]
                for name in (
                    "output_name",
                    "output_schema",
                    "output_version",
                    "purpose",
                ):
                    _hs_refuse(ref[name] == producer[name])

            for ref in refs:
                matching_edges = [
                    edge
                    for edge in edges
                    if edge["consumer_action_id"] == action["action_id"]
                    and all(
                        edge[name] == ref[name]
                        for name in (
                            "predecessor_action_id",
                            "output_name",
                            "output_schema",
                            "output_version",
                            "purpose",
                        )
                    )
                ]
                _hs_refuse(len(matching_edges) == 1)
                edge = matching_edges[0]
                matching_contracts = [
                    contract
                    for contract in action["required_input_contracts"]
                    if contract["source_kind"] == "predecessor-output"
                    and contract["binding_id"] == edge["binding_id"]
                    and contract["output_schema"] == ref["output_schema"]
                    and contract["output_version"] == ref["output_version"]
                    and contract["purpose"] == ref["purpose"]
                ]
                _hs_refuse(len(matching_contracts) == 1)
                producer = by_id[ref["predecessor_action_id"]]["output_contract"]
                for name in (
                    "output_name",
                    "output_schema",
                    "output_version",
                    "purpose",
                ):
                    _hs_refuse(ref[name] == producer[name])

        for edge in edges:
            _hs_refuse(edge["consumer_action_id"] in by_id)
            consumer = by_id[edge["consumer_action_id"]]
            contracts = [
                contract
                for contract in consumer["required_input_contracts"]
                if contract["source_kind"] == "predecessor-output"
                and contract["binding_id"] == edge["binding_id"]
                and contract["output_schema"] == edge["output_schema"]
                and contract["output_version"] == edge["output_version"]
                and contract["purpose"] == edge["purpose"]
            ]
            refs = [
                ref
                for ref in consumer["predecessor_output_refs"]
                if all(
                    ref[name] == edge[name]
                    for name in (
                        "predecessor_action_id",
                        "output_name",
                        "output_schema",
                        "output_version",
                        "purpose",
                    )
                )
            ]
            _hs_refuse(len(contracts) == len(refs) == 1)

    @staticmethod
    def _validate_capture_entries(ces, cas, phase_pis, selected):
        published_contracts = [
            contract
            for contract in selected["required_input_contracts"]
            if contract["source_kind"] == "published-input"
        ]
        HistoricalStudyEnvelopePreflight._validate_capture_projection(ces, cas, phase_pis, published_contracts)

    @staticmethod
    def _validate_capture_projection(ces, cas, phase_pis, published_contracts):
        """Validate the exact projection for the caller's already-verified contracts."""

        def contract_key(item):
            return (
                item["binding_id"],
                item["consumer_node"],
                item["consumer_input"],
                item["purpose"],
            )

        _hs_refuse(
            sorted(contract_key(entry) for entry in ces["entries"])
            == sorted(contract_key(contract) for contract in published_contracts)
        )

        phase_entries = phase_pis["entries"]
        converted = []
        for entry in ces["entries"]:
            _hs_refuse(
                entry["consumer_document_sha256"] == ces["consumer_document_sha256"]
            )
            _hs_refuse(entry["purpose"] == ces["purpose"])
            pis = entry["published_input"]
            comparisons = (
                ("descriptor_root_ref", "root_ref"),
                ("descriptor_snapshot_version", "snapshot_version"),
                ("descriptor_document_sha256", "producer_document_sha256"),
                ("descriptor_node", "producer_node"),
                ("descriptor_output", "producer_output"),
            )
            for descriptor, published in comparisons:
                _hs_refuse(entry[descriptor] == pis[published])
            _hs_refuse(
                entry["descriptor_purpose"] == entry["purpose"] == ces["purpose"]
            )
            matches = [
                candidate
                for candidate in phase_entries
                if candidate["input_id"] == pis["input_id"]
            ]
            _hs_refuse(
                len(matches) == 1
                and _hs_canonical_bytes(matches[0]) == _hs_canonical_bytes(pis)
            )
            planned = dict(entry)
            planned["schema"] = "dskit.planned-capture-entry/v1"
            planned["subject_ref"] = ces["subject_ref"]
            planned["planned_entry_sha256"] = hashlib.sha256(
                _hs_canonical_bytes(planned)
            ).hexdigest()
            converted.append(planned)
        converted.sort(
            key=lambda item: (
                _hs_scalar_key(item["planned_entry_sha256"]),
                _hs_canonical_bytes(item),
            )
        )
        _hs_refuse(
            _hs_canonical_bytes(converted) == _hs_canonical_bytes(cas["entries"])
        )

    @staticmethod
    def _validate_digest_chain(ces, pea, bvp, cas, admission):
        _hs_refuse(
            ces["capture_expectation_set_sha256"]
            == pea["capture_expectation_set_sha256"]
            == bvp["capture_expectation_set_sha256"]
            == cas["capture_expectation_set_sha256"]
            == admission["capture_expectation_set_sha256"]
        )
        _hs_refuse(
            pea["plan_evaluation_authorization_sha256"]
            == bvp["plan_evaluation_authorization_sha256"]
            == cas["plan_evaluation_authorization_sha256"]
            == admission["plan_evaluation_authorization_sha256"]
        )
        _hs_refuse(
            bvp["broker_verified_plan_sha256"]
            == cas["broker_verified_plan_sha256"]
            == admission["broker_verified_plan_sha256"]
        )
        _hs_refuse(
            bvp["plan_sha256"] == admission["plan_sha256"]
            and bvp["planned_capture_set_sha256"]
            == cas["planned_capture_set_sha256"]
            == admission["planned_capture_set_sha256"]
            and cas["capture_admission_set_sha256"]
            == admission["capture_admission_set_sha256"]
        )

    def _verify_trust(self, values, profile, admission_name):
        try:
            now_ms = self._clock.now_ms()
        except BaseException as exc:
            raise ValueError("trusted clock refused") from exc
        _hs_refuse(type(now_ms) is int)

        phase_slot = "root_pis" if profile == "bootstrap" else "phase_pis"
        slots = (
            ("root_pis", "PublishedInputSet", "root_pis"),
            ("phase_pis", "PublishedInputSet", phase_slot),
            ("scope", "ScopeIntent", "scope"),
            ("ces", "CES", "ces"),
            ("pea", "PEA", "pea"),
            ("bvp", "BVP", "bvp"),
            ("cas", "CAS", "cas"),
            (
                "admission",
                admission_name,
                "action_admission"
                if admission_name == "ActionAdmission"
                else "replay_admission",
            ),
        )
        for slot, envelope_name, role_slot in slots:
            role, usage = _HS_ROLE_USE[role_slot]
            self._verify_one_signed(
                values[slot], _HS_SELF_FIELDS[envelope_name], role, usage, now_ms,
            )

    def _verify_one_signed(self, envelope, self_field, role, usage, now_ms):
        """Apply the shared exact signature/time/revocation check to one envelope."""
        _hs_refuse(
            envelope["issuer_role"] == role and envelope["key_usage"] == usage
        )
        _hs_refuse(
            envelope["not_before_ms"]
            <= envelope["issued_at_ms"]
            <= envelope["expires_at_ms"]
        )
        _hs_refuse(envelope["not_before_ms"] <= now_ms <= envelope["expires_at_ms"])
        preimage = _hs_self_digest(
            envelope, self_field, signed=True
        )
        key = envelope["key"]
        try:
            verified = self._keyring.verify(
                key["key_id"],
                key["key_version"],
                envelope["issued_at_ms"],
                preimage,
                envelope["signature"],
            )
            unrevoked = self._revocations.is_unrevoked(
                envelope["revocation_snapshot_sha256"],
                key["key_id"],
                key["key_version"],
                now_ms,
            )
        except BaseException as exc:
            raise ValueError("signature or revocation refused") from exc
        _hs_refuse(verified is True and unrevoked is True)


class TerminalArtifactVerifier(ABC, _Opaque):
    """Broker-held external proof authority, never a lifecycle writer.

    Application callers cannot construct or select this capability. For example,
    passing ``terminal_verifier=...`` to the fixed P4 factory is a TypeError.
    Only the held resolver calls these verification methods on its exact issued
    implementation; an arbitrary subclass supplies no accepted authority.
    """

    @abstractmethod
    def verify_terminal(self, snapshot, parent_basis_bytes, terminal_class,
                        terminal_ref_bytes, canonical_artifact_bytes):
        """Return one opaque parent/snapshot-bound proof or refuse without effects."""
        raise NotImplementedError

    @abstractmethod
    def require_current(self, snapshot, anchors):
        """Refuse unless every exact issued anchor remains current in this snapshot."""
        raise NotImplementedError


class VerifiedExternalArtifactAnchor(_Opaque):
    """Nonconstructible, nonserializable external proof with no public payload.

    This is neither admission nor session authority. For example,
    ``VerifiedExternalArtifactAnchor()`` always raises TypeError; bytes or a
    mapping can never reconstruct an issuer-registered proof.
    """

    __slots__ = ("_binding", "__weakref__")

    def __new__(cls, *args, **kwargs):
        """Refuse application construction of an external proof."""
        raise TypeError("opaque external artifact anchor")

    def __init_subclass__(cls, **kwargs):
        """Refuse subclasses that could imitate an issued external proof."""
        raise TypeError("external artifact anchor is final")

    def __setattr__(self, name, value):
        """Keep the issuer-owned binding immutable."""
        raise TypeError("opaque external artifact anchor is frozen")


class _P4ResolverSnapshot(_Opaque):
    """Opaque generation identity; content stays in its one owning resolver."""

    __slots__ = ("_generation", "__weakref__")

    def __new__(cls, *args, **kwargs):
        raise TypeError("opaque P4 resolver snapshot")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("P4 resolver snapshot is final")

    def __setattr__(self, name, value):
        raise TypeError("P4 resolver snapshot is frozen")


# These public keys and signatures authenticate a fixed external TEST corpus.
# There is no production external JSON schema, private key, signing operation,
# file/URL lookup, caller policy, or caller-supplied verification flag here.
_P4_EXTERNAL_RECORDS = (
    ("G1-dataset-authorization",
     "4ad08665610366b70e38ffeeb53a78a8562abcf227e778863aba34838f44271a",
     "56fe4b8681dced80a8d915e9fcf66ee7f5338cdb3a4128a017891783d3f316d8"
     "47873e4a82f91b3b1f6d381dfad7e29771d5c1d91e1e0e100f58de3e9a70d30a"),
    ("G2-dataset-authorization",
     "9cfb3c29920873af887dac1473d2c51fa9bb3d8bdddc2973449d678545207114",
     "277d0654e2269441ed3cce6286ff1d68debb518f0e5c0281829a37e6d006fe733"
     "8b1188d6bf8a5875bf188725e22279582b668ce35ca3d700858cf931923c006"),
    ("fixed-owner-policy",
     "f2aa0e2493c4af124296be128f491fe017637b51f5ea1770d3ddf1e6935de1bc",
     "705774980ced165d05af2e233d6667963ab8770630c3807a0be38da703b2288d8"
     "4f21d7cd0e09ee6be9a59719bba33989436b673726bca11c2e0a3ae4ac8cd02"),
)
_P4_APPROVED_SCOPE_PROJECTIONS = (
    "e61b568822008c9e9fcbe4f0149d39c3ec2b1196dab7d72caf593b1a848b811f",
    "923fa789dc1051fd8576e6af8588d97731f890f6d1a0f1404494b345a4c566dc",
    "3d125ac8581f1a06b6996697b8277a30481025332bc2df43087ca1d159b3e965",
    "53b0cc17b04c5922e25d6a9629014faf2243ed718b5480cde7a00bac12c9f0b5",
    "e2957cc8508ce431ad6fb65c7dfc797bbfa1c578dac909c74b7bd86ee3d4b51c",
)
_P4_APPROVED_ROOT_PROJECTIONS = (
    "f490ad52c7da54f3d7cffd898e30f535b69457e538ae0be5bb0f74dcc1cc5a0a",
    "586acc5a7a76e2f1c429153a959243ce6c7feb02df4dd6ff3f68a46b3caf4930",
)


def _p4_external_record(record):
    """Project one fixed external corpus identity, not an application schema."""
    terminal_class, public_key, signature = record
    raw = _hs_canonical_bytes("nondeployment " + terminal_class + " fixture")
    ref = {
        "kind": "external-authorization", "role": terminal_class,
        "schema": "urn:dskit:synthetic-external:" + terminal_class,
        "sha256": _digest(raw),
    }
    return _hs_canonical_bytes(ref), (terminal_class, raw, public_key, signature)


_P4_EXTERNAL_BY_REF = MappingProxyType(dict(map(_p4_external_record, _P4_EXTERNAL_RECORDS)))
_P4_RESOLVERS = WeakKeyDictionary()
_P4_TERMINALS = WeakKeyDictionary()
_P4_ANCHORS = WeakKeyDictionary()
_P4_LOCAL_PUBLIC_KEYS = MappingProxyType({
    "security-broker/captured-port-authorization":
        "4ef8056bd9f7ad8c5cd456ccb3b7953d1dbc454838dbf9760057bb92ddfb5537",
    "security-broker/lifecycle-capture":
        "62c92260b36169dfa60fccbcbe89747b25a6c418bbc79b1e2fb457972fad3733",
    "security-broker/captured-authorization":
        "a4e8b5a6043ee808b9bcb8f8ded614a8eee84ad2db37ef4ef6d33b52abb76670",
    "security-broker/replay-capture-admission":
        "3588daad4b5400671c5af1b6b62e9559b4082c8e3c2cb4e9299cfccaf310be04",
    "data-publisher/published-input-set-g1-g2":
        "1e7d223f558e8866ef35747e04b497b50033f5f6c41bf5429001d55bbed6a46b",
    "data-publisher/root-publication-g1-g2":
        "0b1268e01671a347df2289ed03895daa2dd9482c05c7dc9f684033d0aa783d06",
    "study-lifecycle/historical-study-scope-intent":
        "e72f8c3779bb6e31e5745705d0b5271583ffc56fbf74d13a17fb9995e1da3ee3",
    "study-lifecycle/capture-expectation":
        "259b9f668b40276435b8084be7345e42915ae17ec31ba6620662367970348d4a",
    "study-lifecycle/capture-admission":
        "63de1932c89026c374795175c6573d0271e7ae8d6d2c8692fe4c71b56daacac1",
    "study-lifecycle/action-execution-admission":
        "4e396693b5ab841125ed11192eaef0f36e3a16fef69d760666983b05fe4bbb96",
    "study-lifecycle/final-replay-admission":
        "c2585433a163a3bb37d13f76fa6f36f79278172435dc7c7cdcbdb0f13088eac3",
    "study-lifecycle/published-input-set-study":
        "0554afffafcfba4de02f5102270bcdf10a52fba5b2faba2de7af2a2bfd8d45ab",
    "study-lifecycle/historical-study-scope":
        "9e348543c0c58c986905e08fa76f9208c1a497bc2412159bff52a060c08fc866",
    "study-lifecycle/stage-admission":
        "48b43d273878da3c376494beb6676462cfc62819d2e8599af0039a8bc3d5f756",
    "study-lifecycle/study-stage-publication":
        "e9738634184d44a55f6d3923ab523099fa262baa21117b3b55e307a639d85375",
    "study-lifecycle/historical-study-manifest":
        "04e48a6b9ae7a8dd315514b26ff2571b6833ee30657f51735fdf5774a50d87a8",
    "security-broker/plan-evaluation":
        "8a44a3d69c3a27a44b5d1c416e31af40de544db3862d790386ca3cb3989c3279",
    "security-broker/plan-verifier":
        "92e3b5e8c07782ac2237793a75360a7298c77736d5162666ffa864837b41469c",
    "architecture-owner/architecture-gate":
        "c1f4689a261efd16e06e7db013efd36c5e6366d6c644ab50a8819fef462b7c4c",
    "security-owner/security-gate":
        "20e65bde6046d0618593f9256bc6e54c9fb80083452f44f60225ca83e2cf9a99",
    "data-owner/data-gate":
        "3a7aed2cfd3e79b5f8f75cf2a710165a6f76da444a627dd7886e36c381d206e0",
    "model-owner/model-gate":
        "4d93d5aab502c2e5bd69988cbb586e3e0f842ffddc5cac1fe2d9bebec13b8465",
    "risk-owner/risk-gate":
        "2490bbd14dc85bc0aae98af2f980ac278eb8f0f12f0b933c03054f409f0430be",
    "execution-owner/execution-gate":
        "d8e43b5d30de038d8f0566f04bed6e100b0b9776577c06e3859de254583009c8",
    "operations-owner/operations-gate":
        "aa5a5479abac895605c9dd1d8e9d5911800786d76510e710eaa1c0cb1eeec607",
    "research-owner/research-gate":
        "6c1dfcf6e900191247d5b1c65d5c02f234c13e8605f9b422316e93edee2b25da",
})


def _p4_artifact_reference_bytes(ref):
    """Validate closed local refs or an exact independently pinned external ref."""
    _hs_refuse(type(ref) is dict and set(ref) == {"kind", "role", "schema", "sha256"})
    _hs_refuse(all(type(value) is str and value for value in ref.values()))
    _require_sha256(ref["sha256"], "P4 artifact")
    raw = _hs_canonical_bytes(ref)
    if raw in _P4_EXTERNAL_BY_REF:
        return raw
    schemas = set(_P4_ARTIFACT_SPECS) | {"dskit.issuance-basis/v1"}
    _hs_refuse(ref["schema"] in schemas, "unknown P4 artifact schema or external terminal")
    _hs_refuse(ref["kind"] != "external-authorization", "unknown external terminal")
    return raw


def _p4_fields(*, hashes="", strings="", numbers="", **extra):
    """Build only the explicit ADR field table; no field is inferred from data."""
    return {**dict.fromkeys(hashes.split(), "H"), **dict.fromkeys(strings.split(), "S"),
            **dict.fromkeys(numbers.split(), "I"), **extra}


_P4_PUBLICATION_FACTS = _p4_fields(
    hashes="member_manifest_sha256 producer_document_sha256",
    strings="root_ref root_id snapshot_version producer_run_identity producer_node producer_output",
)
_P4_TUPLE_FIELDS = _p4_fields(hashes="plan_evaluation_authorization_sha256 capture_expectation_set_sha256 "
    "broker_verified_plan_sha256 plan_sha256 planned_capture_set_sha256 capture_admission_set_sha256")
_P4_EXECUTION_REF = {
    "kind": ("literal", "action"), "action_execution_admission_sha256": "H",
}
_P4_SHAPES = {
    **_HS_SHAPES,
    "BasisRef": _p4_fields(strings="kind role schema", hashes="sha256"),
    "ExecutionRef": _P4_EXECUTION_REF,
    "RootPublicationRef": {"kind": ("literal", "dataset-capture"), "dataset_capture_authorization_sha256": "H"},
    "StagePublicationRef": {"kind": ("literal", "study-stage"), "historical_study_stage_admission_sha256": "H"},
    "RootReceipt": {
        "schema": ("literal", "dskit.root-publication-receipt/v1"),
        "kind": ("literal", "dataset-capture"), "capture_kind": ("literal", "source-roster", "raw-event-dataset"),
        "publication_authorization_ref": ("shape", "RootPublicationRef"),
        **_P4_PUBLICATION_FACTS, **_HS_SIGNED_SUFFIX, "root_publication_receipt_sha256": "H",
    },
    "StageReceipt": {
        "schema": ("literal", "dskit.lifecycle-publication-receipt/v2"), "kind": ("literal", "study-stage"),
        "study_id": "S", "action_id": "ActionId", "execution_authority_ref": ("shape", "ExecutionRef"),
        "logical_execution_id": "S", "run_id": "S", "publication_authorization_ref": ("shape", "StagePublicationRef"),
        **_P4_PUBLICATION_FACTS, **_HS_SIGNED_SUFFIX, "lifecycle_publication_receipt_sha256": "H",
    },
    "Gate": {
        "schema": ("literal", "dskit.gate-evidence-ref/v1"),
        **_p4_fields(strings="gate owner_role key_purpose", hashes="scope_intent_sha256 evidence_sha256 approved_identity_sha256 gate_evidence_ref_sha256"),
        **_HS_SIGNED_SUFFIX,
    },
    "Bootstrap": {
        "action_id": "ActionId", "action_intent_sha256": "H", "consumer_document_contract_sha256": "H",
        "authority_ref": "AuthorityRef", **_P4_TUPLE_FIELDS,
    },
    "PredecessorPublication": {
        **_HS_SHAPES["PredecessorOutputRef"], "published_input": ("shape", "PublishedInputEntry"),
    },
    "Stage": {
        "schema": ("literal", "dskit.historical-study-stage-admission/v1"),
        **_p4_fields(strings="study_id", numbers="topological_position", hashes="scope_authorization_sha256 scope_intent_sha256 "
                    "action_intent_sha256 consumer_document_contract_sha256 consumer_document_sha256 closed_parameters_sha256 "
                    "component_manifest_sha256 candidate_selection_sha256 historical_study_stage_admission_sha256"),
        "action_id": "ActionId", "predecessor_publications": ("array", "PredecessorPublication"),
        "required_inputs": ("array", "PlannedCaptureEntry"), "output_contract": ("shape", "OutputContract"),
        **_P4_TUPLE_FIELDS, **_HS_SIGNED_SUFFIX,
    },
    "FinalEntry": {
        **_p4_fields(hashes="replay_intent_sha256 consumer_document_sha256 environment_identity_sha256 execution_profile_sha256 "
                    "component_manifest_sha256 crash_schedule_sha256 final_replay_entry_sha256"),
        "replay_id": ("literal", "control", "crash-restart"), "required_inputs": ("array", "PlannedCaptureEntry"),
        **_P4_TUPLE_FIELDS,
    },
    "StageOutput": {
        "producer_action_id": "ActionId", **_p4_fields(strings="producer_output_id output_schema",
        hashes="publication_identity_sha256 historical_study_stage_admission_sha256"),
        "published_input": ("shape", "PublishedInputEntry"),
    },
    "PlannedSet": {
        "schema": ("literal", "dskit.planned-capture-set/v1"), "study_id": "S",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "entries": ("array", "PlannedCaptureEntry"), "planned_capture_set_sha256": "H",
    },
}
_P4_SHAPES["ScopeAuthorization"] = {
    **{key: value for key, value in _HS_ENVELOPES["ScopeIntent"].items()
       if key not in ("schema", "historical_study_scope_intent_sha256")},
    "schema": ("literal", "dskit.historical-study-scope-authorization/v2"),
    "scope_intent_sha256": "H", "bootstrap_artifacts": ("array", "Bootstrap"), "gates": ("array", "Gate"),
    "gate_set_sha256": "H", "historical_study_scope_authorization_sha256": "H", **_HS_SIGNED_SUFFIX,
}
_P4_SHAPES["FinalManifest"] = {
    "schema": ("literal", "dskit.historical-study-manifest/v2"), "study_id": "S",
    **_p4_fields(hashes="scope_authorization_sha256 scope_intent_sha256 published_input_set_sha256 gate_set_sha256 "
                "environment_identity_sha256 execution_profile_sha256 component_manifest_sha256 historical_study_manifest_sha256"),
    "gates": ("array", "Gate"), "stage_admissions": ("array", "Stage"), "stage_outputs": ("array", "StageOutput"),
    "release_output": ("shape", "StageOutput"), "replay_entries": ("array", "FinalEntry"), **_HS_SIGNED_SUFFIX,
}
_P4_ARTIFACT_SPECS = {
    _HS_ENVELOPES[name]["schema"][1]: (kind, _HS_SELF_FIELDS[name], role, usage,
                                    {**_HS_ENVELOPES[name], **_HS_SIGNED_SUFFIX})
    for name, kind, role, usage in (
        ("PublishedInputSet", "pis", "", ""),
        ("ScopeIntent", "scope-intent", "study-lifecycle", "historical-study-scope-intent"),
        ("CES", "ces", "study-lifecycle", "capture-expectation"),
        ("PEA", "pea", "security-broker", "plan-evaluation"),
        ("BVP", "bvp", "security-broker", "plan-verifier"),
        ("CAS", "cas", "study-lifecycle", "capture-admission"),
        ("ActionAdmission", "action-execution-admission", "study-lifecycle", "action-execution-admission"),
        ("ReplayAdmission", "final-replay-admission", "study-lifecycle", "final-replay-admission"),
    )
}
for _p4_name, _p4_schema, _p4_kind, _p4_self, _p4_role, _p4_usage in (
    ("RootReceipt", "root-publication-receipt/v1", "root-publication", "root_publication_receipt_sha256", "data-publisher", "root-publication-g1-g2"),
    ("StageReceipt", "lifecycle-publication-receipt/v2", "stage-publication", "lifecycle_publication_receipt_sha256", "study-lifecycle", "study-stage-publication"),
    ("Gate", "gate-evidence-ref/v1", "gate-evidence", "gate_evidence_ref_sha256", "", ""),
    ("ScopeAuthorization", "historical-study-scope-authorization/v2", "scope-authorization", "historical_study_scope_authorization_sha256", "study-lifecycle", "historical-study-scope"),
    ("Stage", "historical-study-stage-admission/v1", "stage-admission", "historical_study_stage_admission_sha256", "study-lifecycle", "stage-admission"),
    ("FinalManifest", "historical-study-manifest/v2", "final-manifest", "historical_study_manifest_sha256", "study-lifecycle", "historical-study-manifest"),
    ("ActionIntent", "action-intent/v1", "action-intent", "action_intent_sha256", "study-lifecycle", None),
    ("ReplayIntent", "replay-intent/v1", "replay-intent", "replay_intent_sha256", "study-lifecycle", None),
    ("PlannedSet", "planned-capture-set/v1", "planned-capture-set", "planned_capture_set_sha256", "security-broker", None),
    ("FinalEntry", "final-replay-entry/v1", "final-replay-entry", "final_replay_entry_sha256", "study-lifecycle", None),
):
    _P4_ARTIFACT_SPECS["dskit." + _p4_schema] = (_p4_kind, _p4_self, _p4_role, _p4_usage, _P4_SHAPES[_p4_name])


def _p4_validate_object(value, fields):
    """Use existing scalar/union validators with explicit additional ADR shapes."""
    _hs_refuse(type(value) is dict and set(value) == set(fields), "P4 artifact field closure refused")
    for key, spec in fields.items():
        item = value[key]
        if type(spec) is str and spec in _P4_SHAPES:
            _p4_validate_object(item, _P4_SHAPES[spec])
        elif type(spec) is tuple and spec[0] == "shape" and spec[1] in _P4_SHAPES:
            _p4_validate_object(item, _P4_SHAPES[spec[1]])
        elif type(spec) is tuple and spec[0] == "array" and spec[1] in _P4_SHAPES:
            _hs_refuse(type(item) is list)
            for child in item:
                _p4_validate_object(child, _P4_SHAPES[spec[1]])
        else:
            _hs_validate_spec(item, spec)


class _FixedP4VerificationKeyring(ReleaseKeyring):
    """Fixed nondeployment Ed25519 verification keys; no key registration route."""

    def verify(self, key_id, key_version, issued_at_ms, preimage, signature):
        if type(key_id) is not str or key_id not in _P4_LOCAL_PUBLIC_KEYS or type(key_version) is not int or key_version != 1:
            return False
        value = _hs_parse_canonical(preimage)
        if key_id != value["issuer_role"] + "/" + value["key_usage"] or issued_at_ms != value["issued_at_ms"]:
            return False
        _p4_verify_ed25519(_P4_LOCAL_PUBLIC_KEYS[key_id], preimage, signature)
        return True


class _FixedP4VerificationRevocations(HistoricalStudyRevocations):
    """One authenticated immutable synthetic revocation generation."""

    def is_unrevoked(self, snapshot_sha256, key_id, key_version, now_ms):
        return (snapshot_sha256 == _digest(b"p4-fixed-revocations") and key_id in _P4_LOCAL_PUBLIC_KEYS
                and type(key_version) is int and key_version == 1 and type(now_ms) is int and now_ms == 500)


class _FixedP4VerificationClock(TrustedClock):
    """Read one immutable synthetic verification instant without lifecycle effects."""

    def __init__(self, start_ms=500):
        if type(start_ms) is not int or start_ms != 500:
            raise TypeError("fixed P4 verification instant required")

    def now_ms(self):
        return 500


_P4_SHARED_SIGNATURE = HistoricalStudyEnvelopePreflight._verify_one_signed
_P4_VERIFICATION = HistoricalStudyEnvelopePreflight(
    _FixedP4VerificationKeyring(), _FixedP4VerificationClock(), _FixedP4VerificationRevocations(),
)


def _p4_verify_local_signed(value, self_field, role, usage):
    """Invoke the same Packet 3 trust check with construction-owned dependencies."""
    now_ms = _FixedP4VerificationClock.now_ms(_P4_VERIFICATION._clock)
    _P4_SHARED_SIGNATURE(_P4_VERIFICATION, value, self_field, role, usage, now_ms)
    _hs_refuse(value["issued_at_ms"] <= now_ms, "P4 future artifact issuance refused")


def _p4_verify_ed25519(public_key, raw, signature):
    """Verify with a fixed key; no import or algorithm name comes from evidence."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        _hs_refuse(type(signature) is str and re.fullmatch(r"[0-9a-f]{128}", signature) is not None)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature), raw,
        )
    except Exception as exc:
        raise ValueError("P4 signature verification refused") from exc


def _p4_verify_terminal_basis(raw):
    """Verify a terminal-parent basis through the shared local trust check."""
    value = _hs_parse_canonical(raw)
    _hs_refuse(type(value) is dict)
    role_use = {
        "root-pis": ("data-publisher", "published-input-set-g1-g2"),
        "root-publication": ("data-publisher", "root-publication-g1-g2"),
        "scope-intent": ("study-lifecycle", "historical-study-scope-intent"),
    }.get(value.get("kind"))
    _hs_refuse(role_use is not None, "terminal parent basis kind refused")
    return _p4_basis(raw, value["kind"], *role_use)

class _FixedTerminalArtifactVerifier(TerminalArtifactVerifier):
    """Verify a fixed authenticated external test corpus, never lifecycle state."""

    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        raise TypeError("terminal capability requires fixed broker construction")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("fixed terminal verifier is final")

    def verify_terminal(self, snapshot, parent_basis_bytes, terminal_class,
                        terminal_ref_bytes, canonical_artifact_bytes):
        """Authenticate the exact external object and its signed parent position."""
        issued = _P4_TERMINALS.get(self)
        _hs_refuse(issued is not None, "terminal capability is unregistered")
        resolver, expected_snapshot, corpus = issued
        _hs_refuse(resolver._terminal is self, "terminal broker/root identity refused")
        _p4_snapshot_integrity(resolver, snapshot)
        _hs_refuse(snapshot is expected_snapshot and corpus is _P4_EXTERNAL_RECORDS)
        _hs_refuse(type(terminal_ref_bytes) is bytes and type(canonical_artifact_bytes) is bytes)
        expected = _P4_EXTERNAL_BY_REF.get(terminal_ref_bytes)
        _hs_refuse(expected is not None and type(terminal_class) is str)
        expected_class, raw, public_key, signature = expected
        _hs_refuse(terminal_class == expected_class and canonical_artifact_bytes == raw)
        _hs_refuse(_P4_CHECKED_RESOLVE(resolver, snapshot, terminal_ref_bytes) == raw)
        parent = _p4_verify_terminal_basis(parent_basis_bytes)
        parent_ref = _hs_canonical_bytes({
            "kind": "issuance-basis", "role": parent["issuer_role"],
            "schema": parent["schema"], "sha256": parent["issuance_basis_sha256"],
        })
        _hs_refuse(_P4_CHECKED_RESOLVE(resolver, snapshot, parent_ref) == parent_basis_bytes)
        parent_refs = [_hs_canonical_bytes(ref) for ref in parent["refs"]]
        _hs_refuse(parent_refs.count(terminal_ref_bytes) == 1)
        classes = [
            _P4_EXTERNAL_BY_REF[ref][0] for ref in parent_refs if ref in _P4_EXTERNAL_BY_REF
        ]
        required = (["fixed-owner-policy"] if parent["kind"] == "scope-intent" else
                    ["G1-dataset-authorization", "G2-dataset-authorization"])
        _hs_refuse(sorted(classes) == required, "terminal parent class/cardinality refused")
        certificate = _p4_terminal_certificate(terminal_class, raw)
        _p4_verify_ed25519(public_key, certificate, signature)
        parents = _p4_terminal_parent_projection(resolver, snapshot, parent)
        binding = (
            self, resolver, snapshot, corpus, parent_basis_bytes, terminal_ref_bytes,
            _digest(raw), terminal_class, "fixed-nondeployment-policy/v1",
            public_key, signature, 500, (0, 1000), _digest(b"p4-fixed-revocations"),
            parents, _digest(certificate), _hs_parse_canonical(terminal_ref_bytes)["sha256"], 0,
        )
        anchor = object.__new__(VerifiedExternalArtifactAnchor)
        object.__setattr__(anchor, "_binding", binding)
        _P4_ANCHORS[anchor] = binding
        return anchor

    def require_current(self, snapshot, anchors):
        """Check exact proof issuance and fixed trusted generation without spending."""
        issued = _P4_TERMINALS.get(self)
        _hs_refuse(issued is not None, "terminal capability is unregistered")
        _hs_refuse(issued[0]._terminal is self, "terminal broker/root identity refused")
        _p4_snapshot_integrity(issued[0], snapshot)
        _hs_refuse(type(anchors) is tuple and len({id(anchor) for anchor in anchors}) == len(anchors))
        for anchor in anchors:
            _hs_refuse(type(anchor) is VerifiedExternalArtifactAnchor)
            binding = _P4_ANCHORS.get(anchor)
            _hs_refuse(binding is not None and anchor._binding is binding)
            _hs_refuse(binding[0] is self and binding[2] is snapshot and binding[-1] == 0)
            _hs_refuse(binding[3] is _P4_EXTERNAL_RECORDS)
            parent = _p4_verify_terminal_basis(binding[4])
            _hs_refuse(binding[14] == _p4_terminal_parent_projection(issued[0], snapshot, parent),
                       "terminal parent verification generation refused")


_P4_CHECKED_RESOLVE = _FixedWormTrustedArtifactResolver._checked_resolve
_P4_RESOLVER_METHODS = (
    _FixedWormTrustedArtifactResolver.snapshot,
    _FixedWormTrustedArtifactResolver.resolve,
    _P4_CHECKED_RESOLVE,
)
_P4_TERMINAL_METHODS = (
    _FixedTerminalArtifactVerifier.verify_terminal,
    _FixedTerminalArtifactVerifier.require_current,
)


def _p4_snapshot_integrity(resolver, snapshot):
    """Check the construction-owned graph before any resolver or proof action.

    ADR-0143 Decision point 4: dispatches on ``type(resolver)`` through an
    explicit closed if/elif/else, never isinstance. The legacy (``if``) arm
    below is byte-identical to the prior single-branch checks; the dynamic
    (``elif``) arm is new and self-contained, sharing no helper with it.
    """
    _hs_refuse(_p4_snapshot_integrity is _P4_SNAPSHOT_INTEGRITY and
               _p4_fixed_integrity is _P4_FIXED_INTEGRITY, "P4 fixed dispatch integrity refused")
    _P4_FIXED_INTEGRITY()
    if type(resolver) is _FixedWormTrustedArtifactResolver:
        pin = _P4_RESOLVERS.get(resolver)
        _hs_refuse(pin is not None, "P4 snapshot capability is unregistered")
        records, expected, terminal, generation = pin
        terminal_pin = _P4_TERMINALS.get(terminal)
        _hs_refuse(terminal_pin is not None and terminal_pin[0] is resolver
                   and terminal_pin[1] is snapshot and terminal_pin[2] is _P4_EXTERNAL_RECORDS,
                   "P4 terminal root integrity refused")
        _hs_refuse(
            resolver._records is records and resolver._snapshot is expected
            and snapshot is expected and type(snapshot) is _P4ResolverSnapshot
            and type(snapshot._generation) is int and type(generation) is int and snapshot._generation == generation == 0
            and resolver._terminal is terminal and type(terminal) is _FixedTerminalArtifactVerifier
            and not terminal.__dict__
            and tuple(getattr(type(resolver), name) for name in (
                "snapshot", "resolve", "_checked_resolve",
            )) == _P4_RESOLVER_METHODS
            and tuple(getattr(type(terminal), name) for name in (
                "verify_terminal", "require_current",
            )) == _P4_TERMINAL_METHODS,
            "P4 snapshot integrity refused",
        )
    elif type(resolver) is _DynamicP4TrustedArtifactResolver:
        _p4_dynamic_fixed_integrity()
        pin = _P4_RESOLVERS.get(resolver)
        _hs_refuse(pin is not None, "P4 snapshot capability is unregistered")
        graph, terminal = pin
        terminal_pin = _P4_TERMINALS.get(terminal)
        _hs_refuse(terminal_pin is not None and terminal_pin[0] is resolver,
                   "P4 terminal root integrity refused")
        _hs_refuse(
            resolver._graph is graph and type(graph) is NonAuthorizingDynamicRootGraph
            and resolver._terminal is terminal
            and type(terminal) is _DynamicP4TerminalArtifactVerifier
            and not terminal.__dict__
            and snapshot is not None and type(snapshot) is _DynamicRootGraphSnapshot
            and snapshot is resolver._snapshot and snapshot._graph is graph
            and tuple(getattr(type(resolver), name) for name in (
                "snapshot", "resolve", "_checked_resolve",
            )) == _P4_DYNAMIC_RESOLVER_METHODS
            and tuple(getattr(type(terminal), name) for name in (
                "verify_terminal", "require_current",
            )) == _P4_DYNAMIC_TERMINAL_METHODS,
            "P4 snapshot integrity refused",
        )
    else:
        _hs_refuse(False, "P4 snapshot integrity refused")


def _p4_basis(raw, kind, role, usage):
    """Verify one exact signed basis with the shared Packet 3 trust primitive."""
    value = _hs_parse_canonical(raw)
    fields = {name: spec for name, spec in _HS_SIGNED_SUFFIX.items() if name != "issuance_basis_sha256"}
    fields.update(schema=("literal", "dskit.issuance-basis/v1"), kind="S", study_id="S",
                  refs=("array", "BasisRef"), issuance_basis_sha256="H")
    _p4_validate_object(value, fields)
    _hs_refuse(value["kind"] == kind and value["study_id"] == "synthetic-study", "P4 basis kind/study refused")
    _hs_strict_sorted(value["refs"], lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
    for ref in value["refs"]:
        _p4_artifact_reference_bytes(ref)
        _hs_refuse(ref["sha256"] != value["issuance_basis_sha256"], "P4 self-referential basis refused")
    _p4_verify_local_signed(value, "issuance_basis_sha256", role, usage)
    return value


def _p4_identity(value, schema):
    """Select closed kind/role/use from a known schema, never from supplied trust."""
    kind, self_field, role, usage, fields = _P4_ARTIFACT_SPECS[schema]
    if kind == "pis":
        phase = value["phase"]
        kind, role, usage = {
            "root-g1-g2": ("root-pis", "data-publisher", "published-input-set-g1-g2"),
            "stage-consumer": ("stage-pis", "study-lifecycle", "published-input-set-study"),
            "replay-consumer": ("replay-pis", "study-lifecycle", "published-input-set-study"),
        }[phase]
    elif kind == "gate-evidence":
        owners = ("architecture", "security", "data", "model", "risk", "execution", "operations", "research")
        _hs_refuse(value["gate"] in tuple("G" + str(index) for index in range(8)), "unknown P4 gate")
        owner = owners[int(value["gate"][1])]
        role, usage = owner + "-owner", owner + "-gate"
        _hs_refuse(value["owner_role"] == role and value["key_purpose"] == usage)
    return kind, self_field, role, usage, fields


def _p4_closed_edges(scope):
    """Resolve declared predecessor contracts bijectively; never invent an edge."""
    edges = []
    for action in scope["action_intents"]:
        for contract in action["required_input_contracts"]:
            if contract["source_kind"] != "predecessor-output":
                continue
            matches = [ref for ref in action["predecessor_output_refs"]
                       if all(ref[key] == contract[key] for key in ("output_schema", "output_version", "purpose"))]
            _hs_refuse(len(matches) == 1, "ambiguous P4 predecessor edge")
            edges.append({"consumer_action_id": action["action_id"], "binding_id": contract["binding_id"], **matches[0]})
    edges.sort(key=lambda edge: _hs_tuple_key(*(edge[key] for key in (
        "consumer_action_id", "binding_id", "predecessor_action_id", "output_name", "output_schema", "output_version", "purpose",
    ))))
    _hs_refuse(_digest(_hs_canonical_bytes(edges)) == scope["edge_set_sha256"], "P4 declared edge digest differs")
    return edges


def _p4_close_admission(resolver, snapshot, admission_ref, live_projection):
    """Close a held signed graph and live PCE projection without any writer effect."""
    _p4_snapshot_integrity(resolver, snapshot)
    refs = [_hs_parse_canonical(key) for key, _raw in resolver._records]
    verified, active, anchors = {}, set(), []

    def find(kind, digest):
        matches = [ref for ref in refs if ref["kind"] == kind and ref["sha256"] == digest]
        _hs_refuse(len(matches) == 1, "P4 closure requires one exact referenced artifact")
        return matches[0]

    def get(kind, digest):
        return visit(find(kind, digest))

    def receipt_ref(entry):
        kind = {"dskit.root-publication-receipt/v1": "root-publication",
                "dskit.lifecycle-publication-receipt/v2": "stage-publication"}[entry["publication_receipt_schema"]]
        return find(kind, entry["publication_receipt_sha256"])

    def subject_ref(value):
        subject = value.get("subject_ref", value)
        if "action_intent_sha256" in subject:
            return find("action-intent", subject["action_intent_sha256"])
        return find("replay-intent", subject["replay_intent_sha256"])

    def pis_ref(digest):
        matches = [ref for ref in refs if ref["kind"] in ("root-pis", "stage-pis", "replay-pis") and ref["sha256"] == digest]
        _hs_refuse(len(matches) == 1, "P4 closure requires exact PIS identity")
        return matches[0]

    def tuple_refs(value):
        return [find(kind, value[field]) for kind, field in (
            ("pea", "plan_evaluation_authorization_sha256"), ("ces", "capture_expectation_set_sha256"),
            ("bvp", "broker_verified_plan_sha256"), ("cas", "capture_admission_set_sha256"),
        )]

    def gate_refs(intent_digest, set_digest):
        candidates = []
        for ref in refs:
            if ref["kind"] != "gate-evidence":
                continue
            value = _hs_parse_canonical(_P4_CHECKED_RESOLVE(resolver, snapshot, _hs_canonical_bytes(ref)))
            if value.get("scope_intent_sha256") == intent_digest:
                candidates.append((ref, value))
        candidates.sort(key=lambda item: item[1].get("gate", ""))
        _hs_refuse([value.get("gate") for _ref, value in candidates] == ["G" + str(index) for index in range(8)])
        _hs_refuse(_digest(_hs_canonical_bytes([value for _ref, value in candidates])) == set_digest, "P4 gate set digest refused")
        return [ref for ref, _value in candidates]

    def external_refs(classes):
        return [_hs_parse_canonical(key) for key, record in _P4_EXTERNAL_BY_REF.items() if record[0] in classes]

    def dependencies(kind, value):
        if kind == "root-publication":
            return external_refs(("G1-dataset-authorization", "G2-dataset-authorization"))
        if kind == "root-pis":
            return external_refs(("G1-dataset-authorization", "G2-dataset-authorization")) + [receipt_ref(entry) for entry in value["entries"]]
        if kind in ("stage-pis", "replay-pis"):
            scopes = [ref for ref in refs if ref["kind"] == "scope-authorization"]
            _hs_refuse(len(scopes) == 1, "P4 PIS requires unique held scope")
            _hs_refuse(all(entry["publication_receipt_schema"] == "dskit.lifecycle-publication-receipt/v2" for entry in value["entries"]))
            publications = [receipt_ref(entry) for entry in value["entries"]]
            predecessors = []
            if kind == "stage-pis":
                for publication in publications:
                    receipt = visit(publication)
                    predecessor = find("stage-admission", receipt["publication_authorization_ref"]["historical_study_stage_admission_sha256"])
                    if predecessor not in predecessors:
                        predecessors.append(predecessor)
            return scopes + predecessors + publications
        if kind == "scope-intent":
            return [pis_ref(value["published_input_set_sha256"])] + external_refs(("fixed-owner-policy",))
        if kind == "gate-evidence":
            return [find("scope-intent", value["scope_intent_sha256"])]
        if kind == "ces":
            return [pis_ref(value["published_input_set_sha256"]), subject_ref(value)]
        if kind == "pea":
            result = [pis_ref(value["published_input_set_sha256"]), find("ces", value["capture_expectation_set_sha256"]), subject_ref(value)]
            authority = value["authority_ref"]
            if authority["kind"] == "scope-intent-gate-set":
                return result + gate_refs(authority["scope_intent_sha256"], authority["gate_set_sha256"])
            return result + [find("scope-authorization", authority["scope_authorization_sha256"])]
        if kind == "bvp":
            ces = get("ces", value["capture_expectation_set_sha256"])
            return [find("pea", value["plan_evaluation_authorization_sha256"]), find("ces", value["capture_expectation_set_sha256"]),
                    pis_ref(ces["published_input_set_sha256"]), subject_ref(value)]
        if kind == "cas":
            return [find("pea", value["plan_evaluation_authorization_sha256"]), find("ces", value["capture_expectation_set_sha256"]),
                    find("bvp", value["broker_verified_plan_sha256"]), subject_ref(value)]
        if kind == "scope-authorization":
            return [find("scope-intent", value["scope_intent_sha256"]), *gate_refs(value["scope_intent_sha256"], value["gate_set_sha256"]),
                    *(ref for entry in value["bootstrap_artifacts"] for ref in tuple_refs(entry))]
        if kind == "stage-admission":
            return [find("scope-authorization", value["scope_authorization_sha256"]), subject_ref(value), *tuple_refs(value),
                    *(receipt_ref(entry["published_input"]) for entry in value["predecessor_publications"])]
        if kind == "action-execution-admission":
            return [find("stage-admission", value["historical_study_stage_admission_sha256"]), *tuple_refs(value)]
        if kind == "stage-publication":
            return [find("stage-admission", value["publication_authorization_ref"]["historical_study_stage_admission_sha256"]),
                    find("action-execution-admission", value["execution_authority_ref"]["action_execution_admission_sha256"])]
        if kind == "final-manifest":
            return [find("scope-authorization", value["scope_authorization_sha256"]), find("scope-intent", value["scope_intent_sha256"]),
                    pis_ref(value["published_input_set_sha256"]), *gate_refs(value["scope_intent_sha256"], value["gate_set_sha256"]),
                    *(find("stage-admission", entry["historical_study_stage_admission_sha256"]) for entry in value["stage_admissions"]),
                    *(receipt_ref(entry["published_input"]) for entry in value["stage_outputs"]),
                    *(find("final-replay-entry", entry["final_replay_entry_sha256"]) for entry in value["replay_entries"])]
        if kind == "final-replay-admission":
            return [find("final-manifest", value["final_manifest_sha256"]), find("final-replay-entry", value["final_replay_entry_sha256"]),
                    subject_ref(value), *tuple_refs(value)]
        if kind == "final-replay-entry":
            return [subject_ref(value), *tuple_refs(value)]
        return []

    def equal(left, right, fields):
        _hs_refuse(all(_hs_canonical_bytes(left[field]) == _hs_canonical_bytes(right[field]) for field in fields), "P4 linked artifact adjacency refused")

    def verify_tuple(value, required_inputs=None):
        pea, ces, bvp, cas = [visit(ref) for ref in tuple_refs(value)]
        intent = visit(subject_ref(cas))
        pis = visit(pis_ref(ces["published_input_set_sha256"]))
        equal(pea, ces, ("study_id", "phase", "subject_ref", "consumer_document_contract_sha256", "consumer_document_sha256",
                         "purpose", "component_manifest_sha256", "published_input_set_sha256"))
        for other in (bvp, cas):
            equal(other, ces, ("study_id", "subject_ref", "consumer_document_contract_sha256", "consumer_document_sha256", "purpose"))
        equal(bvp, ces, ("component_manifest_sha256",))
        HistoricalStudyEnvelopePreflight._validate_digest_chain(ces, pea, bvp, cas, value)
        HistoricalStudyEnvelopePreflight._validate_capture_projection(ces, cas, pis, intent["required_input_contracts"])
        _hs_refuse(ces["consumer_document_contract_sha256"] == intent["consumer_document_contract_sha256"])
        if required_inputs is not None:
            _hs_refuse(_hs_canonical_bytes(required_inputs) == _hs_canonical_bytes(cas["entries"]), "P4 complete PCE projection differs")
        subject = ces["subject_ref"]
        if subject["kind"] == "action":
            is_root = not intent["predecessor_output_refs"]
            _hs_refuse(ces["phase"] == ("bootstrap" if is_root else "scope-action"))
        else:
            _hs_refuse(ces["phase"] == "replay-pre-final")
        authority = pea["authority_ref"]
        if authority["kind"] == "scope-intent-gate-set":
            scope_intent = get("scope-intent", authority["scope_intent_sha256"])
        else:
            scope = get("scope-authorization", authority["scope_authorization_sha256"])
            scope_intent = get("scope-intent", scope["scope_intent_sha256"])
        root_pis = visit(pis_ref(scope_intent["published_input_set_sha256"]))
        # Packet 3 owns the exact phase/tag/subject and approved-intent checks.
        HistoricalStudyEnvelopePreflight._validate_profiles(
            {"root_pis": root_pis, "phase_pis": pis, "ces": ces, "pea": pea,
             "bvp": bvp, "cas": cas, "scope": scope_intent},
            {"root_pis": _hs_canonical_bytes(root_pis), "phase_pis": _hs_canonical_bytes(pis)},
            "ActionAdmission" if subject["kind"] == "action" else "ReplayAdmission",
        )
        return pea, ces, bvp, cas, intent, pis

    def check(kind, value):
        if "study_id" in value:
            _hs_refuse(value["study_id"] == "synthetic-study", "fixed P4 study policy refused")
        if kind.endswith("pis"):
            _hs_strict_sorted(value["entries"], lambda entry: _hs_scalar_key(entry["input_id"]))
            _hs_refuse(bool(value["entries"]), "empty P4 PIS refused")
            for entry in value["entries"]:
                receipt = visit(receipt_ref(entry))
                equal(receipt, entry, _P4_PUBLICATION_FACTS)
        elif kind == "root-publication":
            expected_authorization = next(_hs_parse_canonical(key)["sha256"] for key, rec in _P4_EXTERNAL_BY_REF.items() if rec[0] == "G1-dataset-authorization")
            _hs_refuse(value["publication_authorization_ref"]["dataset_capture_authorization_sha256"] == expected_authorization)
            _hs_refuse(value["root_ref"] in ("capture://synthetic/root", "capture://synthetic/root-b"), "fixed external root policy refused")
        elif kind in ("action-intent", "replay-intent"):
            _hs_strict_sorted(value["required_input_contracts"], lambda item: _hs_scalar_key(item["binding_id"]))
        elif kind == "scope-intent":
            root = visit(pis_ref(value["published_input_set_sha256"]))
            _hs_refuse(root["phase"] == "root-g1-g2")
            expected_policy = next(_hs_parse_canonical(key)["sha256"] for key, rec in _P4_EXTERNAL_BY_REF.items() if rec[0] == "fixed-owner-policy")
            _hs_refuse(value["policy_set_sha256"] == expected_policy, "fixed external owner policy refused")
            for array, id_key, digest_key, kind_name in (
                ("action_intents", "action_id", "action_intent_sha256", "action-intent"),
                ("replay_intents", "replay_id", "replay_intent_sha256", "replay-intent"),
            ):
                for entry in value[array]:
                    _hs_refuse(_hs_canonical_bytes(entry) == _hs_canonical_bytes(get(kind_name, entry[digest_key])))
                _hs_refuse(len({entry[id_key] for entry in value[array]}) == len(value[array]))
            _hs_refuse({entry["action_id"] for entry in value["action_intents"]} == _HS_ACTION_IDS)
            _hs_refuse([entry["replay_id"] for entry in value["replay_intents"]] == ["control", "crash-restart"])
            for name in ("action", "replay"):
                payload = {"schema": "dskit." + name + "-intent-set/v1", "entries": value[name + "_intents"]}
                _hs_refuse(_digest(_hs_canonical_bytes(payload)) == value[name + "_intent_set_sha256"])
            edges = _p4_closed_edges(value)
            _hs_refuse(_digest(_hs_canonical_bytes({"action_intents": value["action_intents"],
                "action_intent_set_sha256": value["action_intent_set_sha256"], "edge_set_sha256": value["edge_set_sha256"]})) == value["action_dag_sha256"])
            HistoricalStudyEnvelopePreflight._validate_graph(value, edges, root)
        elif kind == "bvp":
            planned = get("planned-capture-set", value["planned_capture_set_sha256"])
            equal(planned, value, ("study_id", "subject_ref"))
            payload = {key: item for key, item in value.items() if key not in (
                "schema", "broker_verified_plan_sha256", "plan_sha256", *_HS_SIGNED_SUFFIX,
            )}
            payload["planned_capture_set"] = planned
            _hs_refuse(_digest(_hs_canonical_bytes(payload)) == value["plan_sha256"], "P4 exact BVP plan payload refused")
        elif kind in ("planned-capture-set", "cas"):
            _hs_strict_sorted(value["entries"], lambda entry: (entry["planned_entry_sha256"], _hs_canonical_bytes(entry)))
            for entry in value["entries"]:
                _hs_self_digest(entry, "planned_entry_sha256")
            if kind == "cas":
                planned = get("planned-capture-set", value["planned_capture_set_sha256"])
                equal(planned, value, ("study_id", "subject_ref", "entries"))
        elif kind == "scope-authorization":
            intent = get("scope-intent", value["scope_intent_sha256"])
            fields = set(_HS_ENVELOPES["ScopeIntent"]) - {"schema", "historical_study_scope_intent_sha256"}
            equal(value, intent, fields)
            roots = sorted(entry["action_id"] for entry in intent["action_intents"] if not entry["predecessor_output_refs"])
            _hs_refuse([entry["action_id"] for entry in value["bootstrap_artifacts"]] == roots)
            _hs_refuse(_digest(_hs_canonical_bytes(value["gates"])) == value["gate_set_sha256"])
            for item in value["bootstrap_artifacts"]:
                pea, _ces, _bvp, _cas, action, _pis = verify_tuple(item)
                equal(item, action, ("action_id", "action_intent_sha256", "consumer_document_contract_sha256"))
                _hs_refuse(pea["authority_ref"] == item["authority_ref"])
                _hs_refuse(item["authority_ref"] == {"kind": "scope-intent-gate-set",
                    "scope_intent_sha256": value["scope_intent_sha256"], "gate_set_sha256": value["gate_set_sha256"]})
        elif kind == "stage-admission":
            scope = get("scope-authorization", value["scope_authorization_sha256"])
            action = get("action-intent", value["action_intent_sha256"])
            equal(value, action, ("action_id", "topological_position", "consumer_document_contract_sha256", "closed_parameters_sha256",
                                 "component_manifest_sha256", "candidate_selection_sha256", "output_contract"))
            _hs_refuse(value["scope_intent_sha256"] == scope["scope_intent_sha256"])
            pea, _ces, _bvp, cas, _intent, _pis = verify_tuple(value, value["required_inputs"])
            equal(value, cas, ("consumer_document_sha256", "consumer_document_contract_sha256"))
            refs_expected = action["predecessor_output_refs"]
            _hs_refuse([{key: entry[key] for key in _HS_SHAPES["PredecessorOutputRef"]} for entry in value["predecessor_publications"]] == refs_expected)
            if not refs_expected:
                matches = [item for item in scope["bootstrap_artifacts"] if item["action_id"] == value["action_id"]]
                _hs_refuse(len(matches) == 1)
                equal(value, matches[0], _P4_TUPLE_FIELDS)
                _hs_refuse(pea["authority_ref"] == matches[0]["authority_ref"])
            else:
                _hs_refuse(pea["authority_ref"]["scope_authorization_sha256"] == value["scope_authorization_sha256"])
                for publication in value["predecessor_publications"]:
                    receipt = visit(receipt_ref(publication["published_input"]))
                    _hs_refuse(receipt["action_id"] == publication["predecessor_action_id"])
        elif kind == "action-execution-admission":
            stage = get("stage-admission", value["historical_study_stage_admission_sha256"])
            equal(value, stage, (*_P4_TUPLE_FIELDS, "study_id", "scope_authorization_sha256", "action_id", "action_intent_sha256"))
            verify_tuple(value, stage["required_inputs"])
        elif kind == "stage-publication":
            stage = get("stage-admission", value["publication_authorization_ref"]["historical_study_stage_admission_sha256"])
            admission = get("action-execution-admission", value["execution_authority_ref"]["action_execution_admission_sha256"])
            equal(value, admission, ("study_id", "action_id", "logical_execution_id", "run_id"))
            _hs_refuse(admission["historical_study_stage_admission_sha256"] == stage["historical_study_stage_admission_sha256"])
            _hs_refuse(value["producer_run_identity"] == admission["run_id"] and value["producer_document_sha256"] == stage["consumer_document_sha256"])
            equal(value, stage["output_contract"], ("producer_node", "producer_output"))
        elif kind == "final-replay-entry":
            _pea, _ces, _bvp, cas, intent, _pis = verify_tuple(value, value["required_inputs"])
            equal(value, intent, ("replay_id", "replay_intent_sha256", "environment_identity_sha256", "execution_profile_sha256", "component_manifest_sha256", "crash_schedule_sha256"))
            equal(value, cas, ("consumer_document_sha256",))
        elif kind == "final-manifest":
            scope = get("scope-authorization", value["scope_authorization_sha256"])
            equal(value, scope, ("study_id", "scope_intent_sha256", "published_input_set_sha256", "gate_set_sha256", "gates",
                                 "environment_identity_sha256", "execution_profile_sha256", "component_manifest_sha256"))
            _hs_refuse([entry["action_id"] for entry in value["stage_admissions"]] == [entry["action_id"] for entry in scope["action_intents"]])
            for stage in value["stage_admissions"]:
                _hs_refuse(stage == get("stage-admission", stage["historical_study_stage_admission_sha256"]))
                _hs_refuse(stage["scope_authorization_sha256"] == value["scope_authorization_sha256"])
            _hs_refuse([entry["replay_id"] for entry in value["replay_entries"]] == ["control", "crash-restart"])
            for entry in value["replay_entries"]:
                _hs_refuse(entry == get("final-replay-entry", entry["final_replay_entry_sha256"]))
                pea, _ces, _bvp, _cas, intent, _pis = verify_tuple(entry, entry["required_inputs"])
                _hs_refuse(intent in scope["replay_intents"])
                _hs_refuse(pea["authority_ref"]["scope_authorization_sha256"] == value["scope_authorization_sha256"])
                equal(entry, scope, ("environment_identity_sha256", "execution_profile_sha256", "component_manifest_sha256"))
            _hs_strict_sorted(value["stage_outputs"], lambda entry: (entry["producer_action_id"], entry["producer_output_id"],
                entry["output_schema"], entry["publication_identity_sha256"], _hs_canonical_bytes(entry)))
            _hs_refuse({entry["producer_action_id"] for entry in value["stage_outputs"]} == _HS_ACTION_IDS and len(value["stage_outputs"]) == len(_HS_ACTION_IDS))
            for output in value["stage_outputs"]:
                receipt = visit(receipt_ref(output["published_input"]))
                stage = get("stage-admission", output["historical_study_stage_admission_sha256"])
                _hs_refuse(output["producer_action_id"] == receipt["action_id"] == stage["action_id"])
                _hs_refuse(output["producer_output_id"] == receipt["producer_output"] and output["output_schema"] == stage["output_contract"]["output_schema"])
                # Publication identity is an opaque signed identity owned by
                # stage publication, not an alias for the outer receipt hash.
                equal(receipt, output["published_input"], _P4_PUBLICATION_FACTS)
            _hs_refuse(value["release_output"] == next(entry for entry in value["stage_outputs"] if entry["producer_action_id"] == "A4"))
        elif kind == "final-replay-admission":
            manifest = get("final-manifest", value["final_manifest_sha256"])
            entry = get("final-replay-entry", value["final_replay_entry_sha256"])
            _hs_refuse(entry in manifest["replay_entries"])
            equal(value, entry, (*_P4_TUPLE_FIELDS, "replay_id", "replay_intent_sha256"))
            verify_tuple(value, entry["required_inputs"])

    def visit(reference):
        key = _p4_artifact_reference_bytes(reference)
        if key in verified:
            return verified[key]
        _hs_refuse(key not in active, "P4 cyclic or future artifact closure refused")
        _hs_refuse(key not in _P4_EXTERNAL_BY_REF, "external terminal requires a closed parent basis position")
        active.add(key)
        value = _hs_parse_canonical(_P4_CHECKED_RESOLVE(resolver, snapshot, key))
        schema = reference["schema"]
        _hs_refuse(schema in _P4_ARTIFACT_SPECS, "P4 unsupported local artifact")
        fields = _P4_ARTIFACT_SPECS[schema][4]
        _p4_validate_object(value, fields)
        kind, self_field, role, usage, _fields = _p4_identity(value, schema)
        _hs_refuse(reference == {"kind": kind, "role": role, "schema": schema, "sha256": value[self_field]}, "P4 exact artifact reference refused")
        _hs_self_digest(value, self_field, signed=usage is not None)
        if usage is not None:
            _p4_verify_local_signed(value, self_field, role, usage)
        required = dependencies(kind, value)
        if usage is not None:
            basis_ref = find("issuance-basis", value["issuance_basis_sha256"])
            _hs_refuse(basis_ref["role"] == role and basis_ref["schema"] == "dskit.issuance-basis/v1")
            basis_raw = _P4_CHECKED_RESOLVE(resolver, snapshot, _hs_canonical_bytes(basis_ref))
            basis = _p4_basis(basis_raw, kind, role, usage)
            _hs_refuse(basis["issuance_basis_sha256"] == value["issuance_basis_sha256"])
            expected = sorted(required, key=lambda ref: tuple(ref[field] for field in ("kind", "role", "schema", "sha256")))
            _hs_refuse(basis["refs"] == expected, "P4 closed basis reference projection refused")
            _hs_refuse(basis["issued_at_ms"] <= value["issued_at_ms"], "P4 basis issued after artifact")
        for child_ref in required:
            child_key = _hs_canonical_bytes(child_ref)
            if child_key in _P4_EXTERNAL_BY_REF:
                _hs_refuse(kind in ("root-pis", "root-publication", "scope-intent"))
                terminal_class = _P4_EXTERNAL_BY_REF[child_key][0]
                anchor = _P4_TERMINAL_METHODS[0](resolver._terminal, snapshot, basis_raw, terminal_class, child_key,
                                                _P4_CHECKED_RESOLVE(resolver, snapshot, child_key))
                _P4_TERMINAL_METHODS[1](resolver._terminal, snapshot, (anchor,))
                _hs_refuse(anchor._binding[4] == basis_raw and anchor._binding[5] == child_key)
                anchors.append(anchor)
            else:
                child = visit(child_ref)
                if usage is not None and "issued_at_ms" in child:
                    _hs_refuse(child["issued_at_ms"] <= basis["issued_at_ms"] <= value["issued_at_ms"],
                               "P4 future signed dependency refused")
        check(kind, value)
        active.remove(key)
        verified[key] = value
        return value

    admission = visit(admission_ref)
    authority, captures, runtime = live_projection
    _hs_refuse(type(authority) is _SyntheticP4CapturedAuthorizationAuthority and authority._p4_resolver is resolver)
    _hs_refuse(admission["run_id"] == runtime["consumer_run_identity"], "P4 admission run differs from live request")
    cas = get("cas", admission["capture_admission_set_sha256"])
    _hs_refuse(len(cas["entries"]) == len(captures), "P4 live capture cardinality differs")
    for entry, (published, frozen, port) in zip(cas["entries"], captures):
        equal(entry, port, ("consumer_document_sha256", "consumer_node", "consumer_input", "purpose"))
        _, descriptor = authority._publication_snapshot(published, "P4 publication identity required")
        for field, source in (("descriptor_root_ref", "root_ref"), ("descriptor_snapshot_version", "snapshot_version"),
                              ("descriptor_document_sha256", "document_sha256"), ("descriptor_node", "node"),
                              ("descriptor_output", "output"), ("descriptor_purpose", "purpose")):
            _hs_refuse(entry[field] == descriptor[source], "P4 frozen descriptor projection differs")
        audit = _DevelopmentBroker._receipt_audit(authority, published)[-1]
        equal(entry["published_input"], audit, _P4_PUBLICATION_FACTS)
        _hs_refuse(_digest(_canonical_bytes(frozen.source)) == entry["consumer_document_sha256"])
    _p4_snapshot_integrity(resolver, snapshot)
    _P4_TERMINAL_METHODS[1](resolver._terminal, snapshot, tuple(anchors))
    return None


_P4_CLOSE_ADMISSION = _p4_close_admission


def _p4_terminal_certificate(terminal_class, raw):
    """Return the independently signed fixed test-policy attestation preimage.

    This private certificate is not an external production artifact schema or
    fixture input. The trusted corpus fixes its keys, subjects and policy grants.
    """
    return _hs_canonical_bytes({
        "artifact_sha256": _digest(raw), "canonical_bytes_sha256": _digest(raw),
        "terminal_class": terminal_class, "policy_identity": "fixed-nondeployment-policy/v1",
        "scope_projections": list(_P4_APPROVED_SCOPE_PROJECTIONS),
        "root_projections": list(_P4_APPROVED_ROOT_PROJECTIONS),
        "authorization_subject": _digest(_hs_canonical_bytes("nondeployment G1-dataset-authorization fixture")),
        "owner_key_use": terminal_class, "not_before_ms": 0, "expires_at_ms": 1000,
        "revocation_identity": _digest(b"p4-fixed-revocations"), "generation": 0,
    })


def _p4_terminal_parent_projection(resolver, snapshot, basis):
    """Authenticate exact local parent facts against the fixed external grants."""
    parents = []
    for ref_bytes, raw in resolver._records:
        ref = _hs_parse_canonical(ref_bytes)
        if ref["kind"] != basis["kind"]:
            continue
        value = _hs_parse_canonical(raw)
        if type(value) is not dict or value.get("issuance_basis_sha256") != basis["issuance_basis_sha256"]:
            continue
        _hs_refuse(ref["schema"] in _P4_ARTIFACT_SPECS, "terminal parent schema refused")
        kind, self_field, role, usage, fields = _p4_identity(value, ref["schema"])
        _p4_validate_object(value, fields)
        _hs_refuse(kind == basis["kind"] and ref == {"kind": kind, "role": role,
            "schema": value["schema"], "sha256": value[self_field]}, "terminal parent identity refused")
        _p4_verify_local_signed(value, self_field, role, usage)
        _hs_refuse(basis["issued_at_ms"] <= value["issued_at_ms"], "terminal parent chronology refused")
        projection = {key: item for key, item in value.items() if key not in _HS_SIGNED_SUFFIX and key != self_field}
        projection_digest = _digest(_hs_canonical_bytes(projection))
        terminal_refs = [_hs_parse_canonical(key) for key, record in _P4_EXTERNAL_BY_REF.items()
                         if (record[0] == "fixed-owner-policy") == (kind == "scope-intent")]
        required_refs = list(terminal_refs)
        if kind == "scope-intent":
            _hs_refuse(projection_digest in _P4_APPROVED_SCOPE_PROJECTIONS, "fixed external owner policy projection refused")
            required_refs.append({"kind": "root-pis", "role": "data-publisher",
                                  "schema": "dskit.published-input-set/v2", "sha256": value["published_input_set_sha256"]})
        elif kind == "root-publication":
            _hs_refuse(projection_digest in _P4_APPROVED_ROOT_PROJECTIONS, "fixed external root policy projection refused")
        else:
            _hs_refuse(value["phase"] == "root-g1-g2" and value["study_id"] == "synthetic-study")
            _hs_refuse(bool(value["entries"]), "terminal root parent requires receipts")
            for entry in value["entries"]:
                child_ref = {"kind": "root-publication", "role": "data-publisher",
                             "schema": "dskit.root-publication-receipt/v1", "sha256": entry["publication_receipt_sha256"]}
                _hs_refuse(entry["publication_receipt_schema"] == child_ref["schema"])
                required_refs.append(child_ref)
                child_raw = _P4_CHECKED_RESOLVE(resolver, snapshot, _hs_canonical_bytes(child_ref))
                receipt = _hs_parse_canonical(child_raw)
                _p4_validate_object(receipt, _P4_SHAPES["RootReceipt"])
                _p4_verify_local_signed(receipt, "root_publication_receipt_sha256", "data-publisher", "root-publication-g1-g2")
                _hs_refuse(receipt["root_publication_receipt_sha256"] == child_ref["sha256"])
                receipt_projection = {key: item for key, item in receipt.items()
                    if key not in _HS_SIGNED_SUFFIX and key != "root_publication_receipt_sha256"}
                _hs_refuse(_digest(_hs_canonical_bytes(receipt_projection)) in _P4_APPROVED_ROOT_PROJECTIONS,
                           "fixed external root policy projection refused")
                _hs_refuse(all(entry[key] == receipt[key] for key in _P4_PUBLICATION_FACTS),
                           "terminal root parent projection differs from receipt")
        required_refs.sort(key=lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
        _hs_refuse(basis["refs"] == required_refs, "terminal exact parent basis projection refused")
        parents.append((ref_bytes, _digest(raw), projection_digest))
    _hs_refuse(bool(parents), "terminal verified local parent is unavailable")
    return tuple(sorted(parents))


def _p4_fixed_integrity():
    """Reject one-surface changes to fixed data, helpers, trust or class dispatch."""
    if _p4_close_admission is not _P4_CLOSE_ADMISSION:
        raise TypeError("P4 closure dispatch integrity refused")
    if not all(globals().get(name) is value for name, value in _P4_FIXED_GLOBALS):
        raise TypeError("P4 fixed dependency integrity refused")
    _hs_refuse(_digest(repr((_P4_SHAPES, _P4_ARTIFACT_SPECS, _HS_SHAPES, _HS_ENVELOPES)).encode()) == _P4_SHAPE_COMMITMENT,
               "P4 shape policy integrity refused")
    verification, keyring, clock, revocations, key_verify, now, unrevoked = _P4_VERIFICATION_PIN
    _hs_refuse(_P4_VERIFICATION is verification and verification._keyring is keyring
               and verification._clock is clock and verification._revocations is revocations,
               "P4 held verification dependency integrity refused")
    _hs_refuse(type(keyring) is _FixedP4VerificationKeyring and type(clock) is _FixedP4VerificationClock
               and type(revocations) is _FixedP4VerificationRevocations
               and type(keyring).verify is key_verify and type(clock).now_ms is now
               and type(revocations).is_unrevoked is unrevoked
               and not keyring.__dict__ and not clock.__dict__ and not revocations.__dict__,
               "P4 verification dispatch integrity refused")
    _hs_refuse(HistoricalStudyEnvelopePreflight._verify_one_signed is _P4_SHARED_SIGNATURE,
               "P4 shared signature dispatch integrity refused")
    _hs_refuse(all(getattr(HistoricalStudyEnvelopePreflight, name) is method for name, method in _P4_SHARED_METHODS),
               "P4 shared validation dispatch integrity refused")


_P4_SNAPSHOT_INTEGRITY = _p4_snapshot_integrity
_P4_FIXED_INTEGRITY = _p4_fixed_integrity
_P4_VERIFICATION_PIN = (
    _P4_VERIFICATION, _P4_VERIFICATION._keyring, _P4_VERIFICATION._clock, _P4_VERIFICATION._revocations,
    _FixedP4VerificationKeyring.verify, _FixedP4VerificationClock.now_ms, _FixedP4VerificationRevocations.is_unrevoked,
)
_P4_SHAPE_COMMITMENT = _digest(repr((_P4_SHAPES, _P4_ARTIFACT_SPECS, _HS_SHAPES, _HS_ENVELOPES)).encode())
_P4_SHARED_METHODS = tuple((name, getattr(HistoricalStudyEnvelopePreflight, name)) for name in (
    "_validate_capture_entries", "_validate_capture_projection", "_validate_digest_chain", "_validate_graph", "_validate_profiles",
))
_P4_FIXED_GLOBALS = tuple((name, globals()[name]) for name in (
    "_P4_EXTERNAL_RECORDS", "_P4_EXTERNAL_BY_REF", "_P4_LOCAL_PUBLIC_KEYS", "_P4_ARTIFACT_SPECS", "_P4_SHAPES",
    "_P4_APPROVED_SCOPE_PROJECTIONS", "_P4_APPROVED_ROOT_PROJECTIONS",
    "_p4_artifact_reference_bytes", "_p4_validate_object", "_p4_verify_local_signed", "_p4_verify_ed25519",
    "_p4_verify_terminal_basis", "_p4_basis", "_p4_identity", "_p4_closed_edges",
    "_p4_terminal_certificate", "_p4_terminal_parent_projection", "_hs_parse_canonical", "_hs_self_digest",
    "_hs_validate_object", "_hs_validate_spec", "_hs_strict_sorted", "_hs_canonical_bytes", "_hs_refuse",
    "_P4_CHECKED_RESOLVE", "_P4_RESOLVER_LOOKUP", "_P4_RESOLVER_METHODS", "_P4_TERMINAL_METHODS", "_P4_SHARED_SIGNATURE",
    "_P4_ANCHORS", "_P4_TERMINALS", "_P4_RESOLVERS", "_P4_ISSUED", "_P4_VERIFICATION_PIN", "_P4_SHARED_METHODS",
))


class CapturedAuthorizationRecord(_Opaque):
    """Opaque identity for one committed nondeployment capture transaction.

    Examples
    --------
    Only the issued authority returns a record::

        record, session = authority.authorize_capture_set(captures, admission_ref,
            consumer_run_identity=run_id, process_measurement_sha256=measurement,
            runtime_sha256=runtime, transition_nonces=nonces)

    The record exposes only the committed receipt digest and a one-way,
    session-bound read of each retained member.
    """

    __slots__ = ("__weakref__",)

    def lifecycle_captured_receipt_sha256(self, stream, consumer_document_sha256):
        """Return the committed receipt digest for one exact capture entry.

        Parameters
        ----------
        stream : str
            Committed publication stream identity.
        consumer_document_sha256 : str
            Frozen consumer document identity.

        Returns
        -------
        str
            The retained CAPTURED receipt SHA-256 digest.

        Raises
        ------
        ValueError
            If this record or capture entry is not committed.
        """
        ledger = _P4_RECORDS.get(self)
        if ledger is None:
            raise ValueError("P4 committed record required")
        return ledger.p4_receipt_digest(self, stream, consumer_document_sha256)

    def read_member_bytes(self, session, published, consumer_document_sha256, relative_path):
        """Read one verified member once under this record and session.

        Parameters
        ----------
        session : LaunchSession
            The exact P4 session returned with this record.
        published : _Published
            The exact publication captured by this record.
        consumer_document_sha256 : str
            Frozen consumer document identity.
        relative_path : str
            Relative member name in the sealed manifest.

        Returns
        -------
        bytes
            Verified retained member bytes.

        Raises
        ------
        ValueError
            If identity, integrity or single-read discipline fails.
        """
        ledger = _P4_RECORDS.get(self)
        if ledger is None:
            raise ValueError("P4 committed record required")
        return ledger.read_p4_member(self, session, published,
                                     consumer_document_sha256, relative_path)

    def __new__(cls, *args, **kwargs):
        """Refuse construction from public or reconstructed data."""
        raise TypeError("CapturedAuthorizationRecord is broker-issued")

    def __init_subclass__(cls, **kwargs):
        """Refuse subtype substitution for the issued record."""
        raise TypeError("CapturedAuthorizationRecord is final")


class _RetainedP4CaptureState(_Opaque):
    """Private monotonic owner of exact committed handles and local spends."""

    __slots__ = ("_handles", "_view", "_view_id", "_used", "_locked", "__weakref__")

    def __init__(self, token, handles):
        if token is not _MAKE:
            raise TypeError("retained P4 capture state is private")
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "_handles", handles)
        object.__setattr__(self, "_view", None)
        object.__setattr__(self, "_view_id", None)
        object.__setattr__(self, "_used", frozenset())
        object.__setattr__(self, "_locked", True)

    def _mint(self, token, view):
        if token is not _P4_PORT_STATE_MUTATE or self._view is not None:
            raise ValueError("captured port set already minted")
        object.__setattr__(self, "_view", weakref_ref(view))
        object.__setattr__(self, "_view_id", id(view))

    def _use(self, token, view, name):
        if (token is not _P4_PORT_STATE_MUTATE or self._view is None
                or self._view() is not view or name in self._used):
            raise ValueError("captured port already required")
        object.__setattr__(self, "_used", self._used | frozenset((name,)))

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("retained P4 capture state is frozen")
        object.__setattr__(self, name, value)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("retained P4 capture state is final")


class CapturedPortSet(_Opaque):
    """Opaque, non-enumerable two-port view for one committed replay tape."""

    __slots__ = ("__weakref__",)

    def require(self, name):
        """Return the one reader for an exact required port name."""
        _P4_PORT_SET_STATE_CHECK()
        if type(name) is not str:
            raise TypeError("exact captured port name required")
        authority, ledger, record, session, _seal = _p4_checked_port_view(self)
        with ledger._lock:
            ledger._check()
            _P4_ISSUED_CHECK(authority)
            retained_state = _p4_retained_capture_state(authority, ledger, record)
            retained = retained_state._handles
            _hs_refuse(_P4_RECORDS.get(record) is ledger and not session._ended
                       and retained_state._view is not None
                       and retained_state._view() is self
                       and retained_state._view_id == id(self)
                       and type(retained_state._used) is frozenset,
                       "required captured port refused")
            audit = ledger._audit(record)
            entries = [entry for entry in ledger._p4_entries() if entry[4] is record]
            _hs_refuse(len(entries) == 1 and len(retained) == len(audit["streams"]) == 2,
                       "required captured port refused")
            _hs_refuse(session is entries[0][5]
                       and _p4_session_pin(session) == entries[0][6],
                       "required captured port refused")
            request = _hs_parse_canonical(entries[0][2])
            for index, (published, frozen, port) in enumerate(retained):
                projected = request["captures"][index]
                _hs_refuse(type(published) is _Published and type(frozen) is _Frozen
                           and type(port) is MappingProxyType
                           and projected[0] == id(published)
                           and projected[1] == id(frozen)
                           and projected[2] == dict(port)
                           and projected[3] == published.descriptor
                           and projected[4] == _digest(_canonical_bytes(frozen.source)),
                           "required captured port refused")
            by_name = {
                port["consumer_input"]: (
                    published, port["consumer_document_sha256"],
                    audit["streams"][index],
                )
                for index, (published, _frozen, port) in enumerate(retained)
            }
            _hs_refuse(set(by_name) == {"tape_manifest", "tape_data"}
                       and name in by_name, "required captured port refused")
            _hs_refuse(name not in retained_state._used,
                       "captured port already required")
            published, document_sha256, stream = by_name[name]
            ledger._p4_record_capture(record, stream, document_sha256)
            reader = object.__new__(_CapturedPortReader)
            reader_state = (weakref_ref(reader), self, record, session,
                            published, document_sha256, stream, name)
            seal = _p4_port_reader_seal(authority, reader, reader_state)
            _P4_PORT_READERS[reader] = (*reader_state, seal)
            retained_state._use(_P4_PORT_STATE_MUTATE, self, name)
            _P4_CAPTURE_STATE_PINS[retained_state] = _p4_capture_state_seal(
                authority, ledger, record, retained_state)
            return reader

    def __new__(cls, *args, **kwargs):
        """Refuse construction from public or reconstructed data."""
        raise TypeError("CapturedPortSet is broker-issued")

    def __init_subclass__(cls, **kwargs):
        """Refuse subtype substitution for the issued port set."""
        raise TypeError("CapturedPortSet is final")


class _CapturedPortReader(_Opaque):
    """Private one-port read capability; state is held outside the object."""

    __slots__ = ("__weakref__",)

    @property
    def lifecycle_captured_receipt_sha256(self):
        _P4_PORT_SET_STATE_CHECK()
        state = _p4_checked_port_reader(self)
        _issued, _view, record, _session, _published, document_sha256, stream, _name, _seal = state
        return record.lifecycle_captured_receipt_sha256(stream, document_sha256)

    def read_member_bytes(self, relative_path):
        _P4_PORT_SET_STATE_CHECK()
        state = _p4_checked_port_reader(self)
        _issued, _view, record, session, published, document_sha256, _stream, _name, _seal = state
        return record.read_member_bytes(session, published, document_sha256,
                                        relative_path)

    def __new__(cls, *args, **kwargs):
        raise TypeError("captured port reader is broker-issued")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("captured port reader is final")


_LIFECYCLE_LEDGERS = WeakKeyDictionary()
_LIFECYCLE_VIEWS = WeakKeyDictionary()
_P4_LEDGER_PINS = WeakKeyDictionary()
_P4_RECORDS = WeakKeyDictionary()
_P4_PREPARED = WeakKeyDictionary()
_P4_CAPTURE_HANDLES = WeakKeyDictionary()
_P4_CAPTURE_STATE_PINS = WeakKeyDictionary()
_P4_PORT_SET_VIEWS = WeakKeyDictionary()
_P4_PORT_READERS = WeakKeyDictionary()
_P4_PORT_SET_STATE = (
    _P4_CAPTURE_HANDLES, _P4_CAPTURE_STATE_PINS, _P4_PORT_SET_VIEWS,
    _P4_PORT_READERS,
)


def _p4_port_set_state_integrity():
    """Hold the four private weak state domains by exact identity."""
    current = (
        _P4_CAPTURE_HANDLES, _P4_CAPTURE_STATE_PINS, _P4_PORT_SET_VIEWS,
        _P4_PORT_READERS,
    )
    if not all(value is pinned for value, pinned in zip(current, _P4_PORT_SET_STATE, strict=True)):
        raise TypeError("P4 port-set state integrity refused")


_P4_PORT_SET_STATE_CHECK = _p4_port_set_state_integrity


def _p4_capture_state_seal(authority, ledger, record, state):
    projection = [[id(published), id(frozen), dict(port)]
                  for published, frozen, port in state._handles]
    body = _hs_canonical_bytes({
        "state": id(state), "ledger": id(ledger), "record": id(record),
        "captures": projection,
        "view_ref": None if state._view is None else id(state._view),
        "view_id": state._view_id,
        "used": sorted(state._used),
    })
    return hmac.new(authority._map_key, b"P4 CapturedPortSet state\x00" + body,
                    hashlib.sha256).hexdigest()


def _p4_retained_capture_state(authority, ledger, record):
    state = _P4_CAPTURE_HANDLES.get(record)
    _hs_refuse(type(state) is _RetainedP4CaptureState,
               "retained P4 capture state refused")
    expected = _p4_capture_state_seal(authority, ledger, record, state)
    _hs_refuse(hmac.compare_digest(_P4_CAPTURE_STATE_PINS.get(state, ""), expected),
               "retained P4 capture state refused")
    return state


def _p4_port_reader_seal(authority, reader, state):
    issued, view, record, session, published, document_sha256, stream, name = state
    body = _hs_canonical_bytes({
        "reader": id(reader), "view": id(view), "record": id(record),
        "session": id(session), "published": id(published),
        "document_sha256": document_sha256, "stream": stream, "name": name,
        "issued_ref": id(issued),
    })
    return hmac.new(authority._map_key, b"P4 captured port reader\x00" + body,
                    hashlib.sha256).hexdigest()


def _p4_port_view_seal(authority, view, state):
    state_authority, ledger, record, session = state
    body = _hs_canonical_bytes({
        "view": id(view), "authority": id(state_authority),
        "ledger": id(ledger), "record": id(record), "session": id(session),
    })
    return hmac.new(authority._map_key, b"P4 captured port view\x00" + body,
                    hashlib.sha256).hexdigest()


def _p4_checked_port_view(view):
    state = _P4_PORT_SET_VIEWS.get(view)
    _hs_refuse(type(state) is tuple and len(state) == 5,
               "issued captured port set required")
    authority, ledger, record, session, seal = state
    expected = _p4_port_view_seal(authority, view, state[:-1])
    _hs_refuse(hmac.compare_digest(seal, expected),
               "issued captured port set required")
    return state


def _p4_checked_port_reader(reader):
    state = _P4_PORT_READERS.get(reader)
    _hs_refuse(type(state) is tuple and len(state) == 9 and state[0]() is reader,
               "issued captured port reader required")
    _issued, view, record, session, published, document_sha256, stream, name, seal = state
    authority, ledger, view_record, view_session, _view_seal = _p4_checked_port_view(view)
    retained = _p4_retained_capture_state(authority, ledger, record)
    expected = _p4_port_reader_seal(authority, reader, state[:-1])
    entries = [entry for entry in ledger._p4_entries() if entry[4] is record]
    _hs_refuse(view_record is record and view_session is session
               and len(entries) == 1 and session is entries[0][5]
               and not session._ended and _p4_session_pin(session) == entries[0][6]
               and retained._view is not None and retained._view() is view
               and name in retained._used and hmac.compare_digest(seal, expected),
               "issued captured port reader required")
    _hs_refuse(any(candidate is published and port["consumer_input"] == name
                   and port["consumer_document_sha256"] == document_sha256
                   for candidate, _frozen, port in retained._handles),
               "issued captured port reader required")
    return state
_P4_BATCH_USES = MappingProxyType({
    "captured-port": ("dskit.captured-port-authorization/v2", "captured_port_authorization_sha256", "captured-port-authorization"),
    "captured-receipt": ("dskit.lifecycle-captured-receipt/v2", "lifecycle_captured_receipt_sha256", "lifecycle-capture"),
    "captured-set": ("dskit.captured-authorization-set/v2", "captured_authorization_set_sha256", "captured-authorization"),
    "replay-capture-evidence": ("dskit.replay-capture-admission-evidence/v1", "replay_capture_admission_evidence_sha256", "replay-capture-admission"),
})


class _FixedP4Signer(_Opaque):
    """Fixed synthetic Ed25519 issuer, with no registration or algorithm selector."""

    def _sign(self, payload, self_field, usage):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        _hs_refuse(usage in {item[2] for item in _P4_BATCH_USES.values()})
        value = dict(payload, issuer_role="security-broker", key_usage=usage,
            signature_alg="Ed25519", issued_at_ms=500, not_before_ms=0, expires_at_ms=1000,
            revocation_snapshot_sha256=_digest(b"p4-fixed-revocations"),
            key={"key_id": "security-broker/" + usage, "key_version": 1})
        raw = _hs_canonical_bytes(value)
        seed = _digest(("p4-fixed-test-key/security-broker/" + usage).encode())
        signature = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed)).sign(raw).hex()
        return dict(value, **{self_field: _digest(raw), "signature": signature})


class _PreparedP4Batch(_Opaque):
    """Registered private candidate; never returned by the public authority."""

    __slots__ = ("_raw", "_binding", "__weakref__")

    def __new__(cls, *args, **kwargs):
        raise TypeError("P4 prepared batch is opaque")

    def __setattr__(self, name, value):
        raise TypeError("P4 prepared batch is immutable")


class _FixedCapturedAuthorizationContract(_Opaque):
    """Pure private batch preparation/verification; no ledger or lifecycle writer."""

    def _prepare(self, resolver, admission_ref, streams, runtime, nonces):
        snapshot = resolver.snapshot()

        def resolved(kind, digest):
            matches = [raw for key, raw in resolver._records
                       if (ref := _hs_parse_canonical(key))["kind"] == kind and ref["sha256"] == digest]
            _hs_refuse(len(matches) == 1, "P4 exact batch dependency required")
            return _hs_parse_canonical(matches[0])

        admission = _hs_parse_canonical(_P4_CHECKED_RESOLVE(resolver, snapshot, _hs_canonical_bytes(admission_ref)))
        cas = resolved("cas", admission["capture_admission_set_sha256"])
        subject = cas["subject_ref"]
        replay = subject["kind"] == "replay"
        identity = {"execution_authority_ref": {"kind": subject["kind"],
            "final_replay_admission_sha256" if replay else "action_execution_admission_sha256": admission_ref["sha256"]},
            "logical_execution_id": admission["logical_execution_id"], "run_id": admission["run_id"]}
        common = {"study_id": admission["study_id"], **identity, "subject_ref": subject,
            **{name: admission[name] for name in ("plan_evaluation_authorization_sha256", "capture_expectation_set_sha256",
                                                 "broker_verified_plan_sha256", "capture_admission_set_sha256")}}
        tuple_refs = [dict(admission_ref)]
        for kind, field in (("pea", "plan_evaluation_authorization_sha256"), ("ces", "capture_expectation_set_sha256"),
                            ("bvp", "broker_verified_plan_sha256"), ("cas", "capture_admission_set_sha256")):
            tuple_refs.extend(_hs_parse_canonical(key) for key, _raw in resolver._records
                              if (ref := _hs_parse_canonical(key))["kind"] == kind and ref["sha256"] == admission[field])
        _hs_refuse(len(tuple_refs) == 5)
        pce_refs = [{"kind": "planned-capture-entry", "role": "security-broker",
            "schema": "dskit.planned-capture-entry/v1", "sha256": entry["planned_entry_sha256"]} for entry in cas["entries"]]
        bases, ports, receipts, entries = [], [], [], []

        def signed(kind, payload, refs):
            schema, self_field, usage = _P4_BATCH_USES[kind]
            refs = sorted(refs, key=lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
            _hs_refuse(len({_hs_canonical_bytes(ref) for ref in refs}) == len(refs))
            basis = _P4_SIGN(_P4_SIGNER, {"schema": "dskit.issuance-basis/v1", "kind": kind,
                "study_id": admission["study_id"], "refs": refs}, "issuance_basis_sha256", usage)
            bases.append(basis)
            return _P4_SIGN(_P4_SIGNER, dict(payload, schema=schema, issuance_basis_sha256=basis["issuance_basis_sha256"]), self_field, usage)

        for index, pce in enumerate(cas["entries"]):
            refs = tuple_refs + [pce_refs[index]]
            port = signed("captured-port", dict(common, planned_entry_sha256=pce["planned_entry_sha256"]), refs)
            publication = pce["published_input"]
            genesis = {"schema": "dskit.lifecycle-captured-receipt-genesis/v1", "stream_id": streams[index],
                "predecessor_publication_receipt_schema": publication["publication_receipt_schema"],
                "predecessor_publication_receipt_sha256": publication["publication_receipt_sha256"]}
            receipt = signed("captured-receipt", dict(common, stream_id=streams[index], sequence=1,
                previous_lifecycle_captured_receipt_sha256=_digest(_hs_canonical_bytes(genesis)),
                consumer_kind=subject["kind"], consumer_id=subject["replay_id" if replay else "action_id"],
                predecessor_publication_receipt_schema=publication["publication_receipt_schema"],
                predecessor_publication_receipt_sha256=publication["publication_receipt_sha256"],
                planned_capture_set_sha256=admission["planned_capture_set_sha256"], planned_entry_sha256=pce["planned_entry_sha256"],
                captured_port_authorization_sha256=port["captured_port_authorization_sha256"],
                actor_runtime_sha256=runtime["runtime_sha256"], transition_nonce=nonces[index]), refs)
            ports.append(port)
            receipts.append(receipt)
            entries.append(dict(identity, planned_entry_sha256=pce["planned_entry_sha256"],
                captured_port_authorization_sha256=port["captured_port_authorization_sha256"],
                lifecycle_captured_receipt_sha256=receipt["lifecycle_captured_receipt_sha256"]))
        captured_set = signed("captured-set", dict(common, planned_capture_set_sha256=admission["planned_capture_set_sha256"],
                                                  entries=entries), tuple_refs + pce_refs)
        evidence = None
        if replay:
            refs = list(tuple_refs)
            for kind, field in (("final-manifest", "final_manifest_sha256"), ("final-replay-entry", "final_replay_entry_sha256"),
                                ("replay-intent", "replay_intent_sha256")):
                refs.extend(_hs_parse_canonical(key) for key, _raw in resolver._records
                            if (ref := _hs_parse_canonical(key))["kind"] == kind and ref["sha256"] == admission[field])
            refs.append({"kind": "captured-set", "role": "security-broker", "schema": captured_set["schema"],
                         "sha256": captured_set["captured_authorization_set_sha256"]})
            evidence = signed("replay-capture-evidence", {
                **{field: admission[field] for field in ("study_id", "final_manifest_sha256", "final_replay_entry_sha256", "replay_id",
                    "replay_intent_sha256", "plan_evaluation_authorization_sha256", "capture_expectation_set_sha256",
                    "broker_verified_plan_sha256", "planned_capture_set_sha256", "capture_admission_set_sha256")},
                "final_replay_admission_sha256": admission_ref["sha256"],
                "captured_authorization_set_sha256": captured_set["captured_authorization_set_sha256"],
                "issued_ports": [{key: entry[key] for key in ("planned_entry_sha256", "captured_port_authorization_sha256",
                                                             "lifecycle_captured_receipt_sha256")} for entry in entries]}, refs)
        raw = _hs_canonical_bytes({"ports": ports, "receipts": receipts, "set": captured_set, "replay": evidence, "bases": bases})
        binding = (resolver, snapshot, _hs_canonical_bytes(admission_ref), streams, _hs_canonical_bytes(runtime), nonces)
        prepared = object.__new__(_PreparedP4Batch)
        object.__setattr__(prepared, "_raw", raw)
        object.__setattr__(prepared, "_binding", binding)
        _P4_PREPARED[prepared] = (raw, binding)
        return prepared

    def _verify(self, prepared, resolver, admission_ref, streams, runtime, nonces):
        """Require an exact registered candidate from this snapshot and request."""
        _hs_refuse(type(prepared) is _PreparedP4Batch)
        issued = _P4_PREPARED.get(prepared)
        _hs_refuse(issued is not None and prepared._raw is issued[0] and prepared._binding is issued[1],
                   "P4 prepared candidate integrity refused")
        _hs_refuse(prepared._binding == (resolver, resolver.snapshot(), _hs_canonical_bytes(admission_ref),
                   streams, _hs_canonical_bytes(runtime), nonces), "P4 prepared candidate binding refused")
        return _P4_VERIFY_BYTES(self, prepared._raw, resolver, admission_ref, streams, runtime, nonces)

    def _verify_bytes(self, raw, resolver, admission_ref, streams, runtime, nonces):
        """Reparse every byte and compare the full deterministic closed projection."""
        parsed = _hs_parse_canonical(raw)
        _hs_refuse(set(parsed) == {"ports", "receipts", "set", "replay", "bases"})
        expected = _P4_PREPARE(self, resolver, admission_ref, streams, runtime, nonces)
        _hs_refuse(raw == expected._raw, "P4 complete signed batch adjacency refused")
        for kind, slot in (("captured-port", "ports"), ("captured-receipt", "receipts"),
                           ("captured-set", "set"), ("replay-capture-evidence", "replay")):
            values = parsed[slot] if slot in ("ports", "receipts") else [parsed[slot]]
            for value in values:
                if value is None:
                    continue
                _schema, self_field, usage = _P4_BATCH_USES[kind]
                _p4_verify_local_signed(value, self_field, "security-broker", usage)
                bases = [basis for basis in parsed["bases"] if basis["issuance_basis_sha256"] == value["issuance_basis_sha256"]]
                _hs_refuse(len(bases) == 1 and bases[0]["kind"] == kind)
                _p4_verify_local_signed(bases[0], "issuance_basis_sha256", "security-broker", usage)
        return parsed


_P4_SIGNER = _FixedP4Signer()
_P4_CONTRACT = _FixedCapturedAuthorizationContract()
_P4_SIGN = _FixedP4Signer._sign
_P4_PREPARE = _FixedCapturedAuthorizationContract._prepare
_P4_VERIFY_BATCH = _FixedCapturedAuthorizationContract._verify
_P4_VERIFY_BYTES = _FixedCapturedAuthorizationContract._verify_bytes


def _p4_close_admission_once(resolver, snapshot, admission_ref, live_projection, cell):
    """Dispatch one commit_p4_batch admission-closure checkpoint.

    ADR-0144 Decision point 6: replaces all three of commit_p4_batch's
    former individual admission-closure call sites -- including the third,
    which previously called ``_P4_CLOSE_ADMISSION`` (the legacy-only closure
    walk) directly instead of ``resolver.close_admission`` (Context point
    4's latent bug). For the fixed resolver this is byte-behavior-identical
    to three individual ``_hs_refuse(resolver.close_admission(...) is
    None)`` calls at every checkpoint. For the dynamic resolver, the first
    call performs the one and only graph-reading reconstruction and stores
    it in ``cell[0]``; every later call within the same commit_p4_batch
    invocation reuses the frozen result via a cheap identity/class re-check
    (Phase 0 pin F5: ``_p4_snapshot_integrity`` plus an
    ``expected_admission_sha256`` comparison against the already-frozen
    value), never re-reading the graph.
    """
    if type(resolver) is _FixedWormTrustedArtifactResolver:
        _hs_refuse(resolver.close_admission(snapshot, admission_ref, live_projection) is None)
    elif type(resolver) is _DynamicP4TrustedArtifactResolver:
        if cell[0] is None:
            reconstruction = resolver.close_admission(snapshot, admission_ref, live_projection)
            _hs_refuse(reconstruction is not None)
            cell[0] = reconstruction
        else:
            _p4_snapshot_integrity(resolver, snapshot)
            _hs_refuse(admission_ref["sha256"] == cell[0].expected_admission_sha256)
    else:
        raise TypeError("exact broker-issued P4 capability is required")


class _LifecycleAuthorizationLedger(_Opaque):
    """One process-local lock and immutable admission/batch/session record domain."""

    def __init__(self, authority):
        self._authority = authority
        self._lock = RLock()
        self._root = ()
        self._reserving = ()
        self._p4_reads = {}
        self._test_fault = None
        self._pin = (authority, self._lock, self._root)
        _P4_LEDGER_PINS[self] = self._pin

    def _check(self):
        _hs_refuse(self._test_fault is None or type(self._test_fault) is str,
                   "P4 synthetic fault schedule requires exact data")
        _hs_refuse(_P4_LEDGER_PINS.get(self) is self._pin and self._pin == (self._authority, self._lock, self._root),
                   "P4 ledger identity or history integrity refused")
        _hs_refuse(_LIFECYCLE_LEDGERS.get(self._authority) is self and self._authority._p4_ledger is self,
                   "P4 authority ledger substitution refused")
        _hs_refuse(all(getattr(type(self), name) is method for name, method in _P4_LEDGER_METHODS), "P4 ledger dispatch integrity refused")
        _hs_refuse(_P4_COMMIT is type(self).commit_p4_batch and _p4_require_issued_authority is _P4_ISSUED_CHECK,
                   "P4 commit/authority capsule integrity refused")
        if type(self._authority) is _SyntheticP4CapturedAuthorizationAuthority:
            _P4_ISSUED_CHECK(self._authority)
        _hs_refuse(all(getattr(_DevelopmentBroker, name) is method for name, method in _P4_LEGACY_PREPARE_METHODS),
                   "legacy preparation dispatch integrity refused")
        _hs_refuse(_FixedP4Signer._sign is _P4_SIGN and _FixedCapturedAuthorizationContract._prepare is _P4_PREPARE
                   and _FixedCapturedAuthorizationContract._verify is _P4_VERIFY_BATCH
                   and _FixedCapturedAuthorizationContract._verify_bytes is _P4_VERIFY_BYTES, "P4 batch dispatch integrity refused")
        _hs_refuse(_P4_SIGNER is _P4_BATCH_PINS[0] and _P4_CONTRACT is _P4_BATCH_PINS[1]
                   and _P4_BATCH_USES is _P4_BATCH_PINS[2] and not _P4_SIGNER.__dict__ and not _P4_CONTRACT.__dict__,
                   "P4 batch dependency integrity refused")

    def _fault(self, point):
        """One-shot private synthetic refusal schedule; no callback or trust grant."""
        if self._test_fault == point:
            self._test_fault = None
            raise RuntimeError("injected P4 " + point)

    def _committed(self):
        with self._lock:
            self._check()
            return tuple(entry[4] for entry in self._p4_entries())

    def _p4_entries(self):
        return tuple(entry for entry in self._root if type(entry[0]) is tuple)

    def _p4_stream_documents(self, stream):
        """Return committed consumer-document identities already captured for a stream."""
        with self._lock:
            self._check()
            documents = set()
            for entry in self._p4_entries():
                request = _hs_parse_canonical(entry[2])
                audit = _hs_parse_canonical(entry[3])
                for index, committed_stream in enumerate(audit["streams"]):
                    if committed_stream == stream:
                        documents.add(request["captures"][index][2]["consumer_document_sha256"])
            return documents

    def _legacy_captures(self):
        with self._lock:
            self._check()
            return tuple(entry for entry in self._root if entry[0] == "CAPTURED_V1")

    def commit_legacy_capture(self, published, frozen, port, consumer_run_identity,
                              process_measurement_sha256, runtime_sha256, transition_nonce):
        """Stage v1 compatibility projections and commit them in this domain."""
        with self._lock:
            self._check()
            self._legacy_gate((published, frozen))
            _hs_refuse(not any(entry[0][3] == consumer_run_identity for entry in self._p4_entries()),
                       "legacy capture cannot reuse a P4 execution run")
            self._fault("legacy-preflight")
            authority = self._authority
            captured, session, subject = _DevelopmentBroker._prepare_legacy_capture(
                authority, published, frozen, port, consumer_run_identity,
                process_measurement_sha256, runtime_sha256, transition_nonce)
            stream = subject.stream_id
            body = _DevelopmentBroker._prepare_receipt(authority, subject, "CAPTURED", dict(session._runtime),
                transition_nonce, {"consumer_captured_port": dict(captured.port)})
            receipt = _canonical_bytes(body)
            encoded = tuple(authority._receipt_store.get(stream, ())) + (receipt,)
            # Build all allocations and MAC-bound intern records before the
            # commit. None of these local copies is an authority lookup table.
            tables = {name: dict(getattr(authority, name)) for name in (
                "_sessions", "_session_runtime", "_session_runtime_intern", "_captured_streams",
                "_session_streams", "_session_stream_intern", "_capture_bind", "_capture_bind_intern",
                "_streams", "_receipt_len", "_receipt_high")}
            tables["_sessions"][id(session)] = session
            tables["_captured_streams"][id(captured)] = stream
            for left, right, key, value in (
                ("_session_runtime", "_session_runtime_intern", id(session), (id(session), tuple(sorted(dict(session._runtime).items())))),
                ("_session_streams", "_session_stream_intern", id(session), (id(session), str(stream))),
                ("_capture_bind", "_capture_bind_intern", id(captured), (published, frozen, session, stream)),
            ):
                _DevelopmentBroker._store_interned(authority, tables[left], tables[right], key, value)
            tables["_streams"][stream] = [*authority._streams[stream], body]
            tables["_receipt_len"][stream] = len(encoded)
            tables["_receipt_high"][stream] = max(tables["_receipt_high"].get(stream, 0), len(encoded))
            nonces = authority._nonces | {transition_nonce}
            events = [*authority._session_events, ("start", consumer_run_identity, id(session))]
            root = (*self._root, ("CAPTURED_V1", stream, receipt, captured, session, events[-1]))
            pin = (authority, self._lock, root)
            self._fault("legacy-prepared")
            self._check()
            authority._require_head(stream, "PUBLISHED")
            self._fault("legacy-commit-before")
            final_subject, final_port = _DevelopmentBroker._validate_bound_v1_effect(
                authority, published, frozen, port, head="PUBLISHED")
            if (final_subject.stream_id != stream or final_port != body["consumer_captured_port"]
                    or body["previous_receipt_sha256"] != _digest(_canonical_bytes(authority._streams[stream][-1]))):
                raise ValueError("staged capture identity mismatch")
            # The fixed P4 authority uses the built-in WORM store. A legacy
            # caller-owned store may refuse here, before compatibility effects.
            authority._receipt_store[stream] = encoded
            for name, table in tables.items():
                setattr(authority, name, table)
            authority._nonces = nonces
            authority._session_events[:] = events
            self._root, self._pin = root, pin
            _P4_LEDGER_PINS[self] = pin
            self._fault("legacy-commit-after")
            self._fault("legacy-return")
            return captured, session

    def _require_unclaimed(self, stream):
        self._check()
        if any(stream in _hs_parse_canonical(entry[3])["streams"] for entry in self._p4_entries()):
            raise ValueError("P4 committed stream cannot enter legacy capture")

    def _nonce_used(self, nonce):
        return any(nonce in _hs_parse_canonical(entry[3])["nonces"] for entry in self._p4_entries())

    def _legacy_gate(self, values):
        if self._reserving:
            raise ValueError("P4 reservation excludes legacy transitions")
        for value in values:
            if any(value is entry[4] or value is entry[5] for entry in self._p4_entries()):
                raise ValueError("P4 opaque capability cannot enter v1 lifecycle")
            if isinstance(value, _Captured):
                value = value.published
            if isinstance(value, _Published):
                stream = self._authority._stream_for_published(value, "live publication required")
                self._require_unclaimed(stream)

    def _request(self, captures, admission_ref, runtime):
        _hs_refuse(type(captures) is tuple and bool(captures))
        _hs_refuse(type(runtime["transition_nonces"]) is tuple)
        projected = []
        for capture in captures:
            _hs_refuse(type(capture) is tuple and len(capture) == 3)
            published, frozen, port = capture
            _hs_refuse(type(published) is _Published and type(frozen) is _Frozen and type(port) is dict)
            _, descriptor = self._authority._publication_snapshot(published, "P4 publication identity required")
            projected.append([id(published), id(frozen), port, dict(descriptor), _digest(_canonical_bytes(frozen.source))])
        copied_runtime = dict(runtime, transition_nonces=list(runtime["transition_nonces"]))
        return _hs_canonical_bytes({"captures": projected, "admission": admission_ref, "runtime": copied_runtime})

    def _audit(self, record):
        with self._lock:
            self._check()
            found = [entry for entry in self._p4_entries() if entry[4] is record]
            _hs_refuse(len(found) == 1 and _P4_RECORDS.get(record) is self, "P4 committed record required")
            value = _hs_parse_canonical(found[0][3])
            value["execution_key"] = found[0][0]
            for slot in ("ports", "receipts", "bases"):
                value[slot] = tuple(_hs_canonical_bytes(item) for item in value[slot])
            for slot in ("set", "session", "session_start"):
                value[slot] = _hs_canonical_bytes(value[slot])
            if value["replay"] is not None:
                value["replay"] = _hs_canonical_bytes(value["replay"])
            value["streams"], value["nonces"] = tuple(value["streams"]), tuple(value["nonces"])
            return value

    def _p4_record_capture(self, record, stream, consumer_document_sha256):
        """Resolve one capture solely from the exact committed record."""
        _hs_refuse(type(record) is CapturedAuthorizationRecord and _P4_RECORDS.get(record) is self,
                   "P4 committed record required")
        matches = [entry for entry in self._p4_entries() if entry[4] is record]
        _hs_refuse(len(matches) == 1, "P4 committed record required")
        entry = matches[0]
        _hs_refuse(_p4_session_pin(entry[5]) == entry[6], "P4 session integrity refused")
        seal = hmac.new(self._authority._map_key,
                        b"P4 LaunchSession\x00" + _hs_canonical_bytes(dict(entry[5]._runtime)),
                        hashlib.sha256).hexdigest()
        _hs_refuse(hmac.compare_digest(entry[7], seal), "P4 session seal refused")
        request = _hs_parse_canonical(entry[2])
        audit = _hs_parse_canonical(entry[3])
        _hs_refuse(audit["batch_sha256"] == _digest(_hs_canonical_bytes(
            {key: value for key, value in audit.items() if key != "batch_sha256"})),
            "P4 committed batch integrity refused")
        _hs_refuse(len(request["captures"]) == len(audit["streams"]) == len(audit["receipts"]),
                   "P4 committed capture cardinality refused")
        indexes = [index for index, item in enumerate(request["captures"])
                   if audit["streams"][index] == stream
                   and item[2]["consumer_document_sha256"] == consumer_document_sha256]
        _hs_refuse(len(indexes) == 1, "P4 exact stream/document capture required")
        return entry, request, audit, indexes[0]

    def p4_receipt_digest(self, record, stream, consumer_document_sha256):
        """Return read-only committed receipt evidence for an exact capture."""
        with self._lock:
            self._check()
            _entry, _request, audit, index = self._p4_record_capture(
                record, stream, consumer_document_sha256)
            receipt = _hs_parse_canonical(_hs_canonical_bytes(audit["receipts"][index]))
            _hs_refuse(receipt["stream_id"] == stream, "P4 receipt stream mismatch")
            return receipt["lifecycle_captured_receipt_sha256"]

    def read_p4_member(self, record, session, published, consumer_document_sha256, relative_path):
        """Read one exact retained member under the committed P4 session."""
        with self._lock:
            self._check()
            _hs_refuse(type(published) is _Published and type(relative_path) is str
                       and relative_path, "P4 published member required")
            stream, descriptor = self._authority._publication_snapshot(
                published, "P4 published member required")
            entry, request, _audit, index = self._p4_record_capture(
                record, stream, consumer_document_sha256)
            _hs_refuse(session is entry[5] and type(session) is LaunchSession
                       and not session._ended and request["captures"][index][0] == id(published)
                       and request["captures"][index][3] == dict(descriptor),
                       "P4 record/session/publication mismatch")
            sealed = self._authority._sealed_for(published)
            _hs_refuse(sealed._member_manifest_sha256 == self._authority._sealed_member_manifest(stream)
                       and self._authority._manifest_digest(sealed._digests) == sealed._member_manifest_sha256,
                       "P4 sealed member manifest mismatch")
            declared = [item for item in sealed._digests if item["relative_path"] == relative_path]
            _hs_refuse(len(declared) == 1, "P4 sealed member required")
            reads = self._p4_reads.setdefault(record, set())
            key = (stream, relative_path)
            _hs_refuse(key not in reads, "P4 member bytes already consumed")
            reads.add(key)
            snapshot = self._authority._provider.describe(descriptor["root_ref"],
                                                          descriptor["snapshot_version"])
            raw = self._authority._provider.open_member(snapshot, relative_path)
            _hs_refuse(type(raw) is bytes and len(raw) == declared[0]["bytes"]
                       and _digest(raw) == declared[0]["sha256"],
                       "P4 retained member digest mismatch")
            return bytes(raw)

    def resolve_p4(self, execution_key, admission_ref):
        """Resolve one exact committed identity; never prepare, consume or remint."""
        with self._lock:
            self._check()
            _hs_refuse(type(execution_key) is tuple and len(execution_key) == 4, "P4 exact execution key required")
            _hs_refuse(type(execution_key[1]) is bytes and all(type(execution_key[index]) is str and execution_key[index]
                       for index in (0, 2, 3)), "P4 execution key scalar types refused")
            authority_ref = _hs_parse_canonical(execution_key[1])
            _hs_refuse(type(authority_ref) is dict and authority_ref.get("kind") in ("action", "replay"))
            digest_field = "action_execution_admission_sha256" if authority_ref["kind"] == "action" else "final_replay_admission_sha256"
            _hs_refuse(set(authority_ref) == {"kind", digest_field})
            _hs_validate_spec(authority_ref[digest_field], "H")
            resolver = self._authority._p4_resolver
            admission_bytes = _p4_reference_bytes(admission_ref, resolver)
            for entry in self._p4_entries():
                if entry[0] == execution_key and entry[1] == admission_bytes:
                    session = entry[5]
                    _hs_refuse(_p4_session_pin(session) == entry[6] and _P4_RECORDS.get(entry[4]) is self,
                               "P4 session or record integrity refused")
                    _hs_refuse(hmac.compare_digest(entry[7], hmac.new(self._authority._map_key,
                        b"P4 LaunchSession\x00" + _hs_canonical_bytes(dict(session._runtime)), hashlib.sha256).hexdigest()),
                        "P4 broker session signature refused")
                    return entry[4], session
            return None

    def commit_p4_batch(self, captures, admission_ref, runtime):
        """Publish consumption and the complete verified immutable batch together."""
        with self._lock:
            self._check()
            authority, resolver = self._authority, self._authority._p4_resolver
            # ADR-0144 Decision point 4: one per-call local, harmless and
            # never read on the legacy path, threaded through every
            # admission-closure checkpoint below so the dynamic
            # reconstruction executes at most once per commit.
            dynamic_reconstruction_cell = [None]
            _P4_SNAPSHOT_INTEGRITY(resolver, resolver._snapshot)
            fingerprint = self._request(captures, admission_ref, runtime)
            admission_bytes = _p4_reference_bytes(admission_ref, resolver)
            for entry in self._p4_entries():
                if entry[1] == admission_bytes:
                    _hs_refuse(entry[2] == fingerprint, "P4 committed admission request conflict")
                    return self.resolve_p4(entry[0], admission_ref)
            self._fault("preflight")
            nonces = runtime["transition_nonces"]
            checked_runtime = {key: value for key, value in runtime.items() if key != "transition_nonces"}
            _P4_REQUEST_CHECK(authority, captures, checked_runtime, nonces)
            _hs_refuse(not any(self._nonce_used(nonce) for nonce in nonces), "P4 nonce already committed")
            # Dispatch through the resolver's own close_admission (four-method
            # contract, ADR-0143 Decision point 1) via _p4_close_admission_once
            # (ADR-0144 Decision points 4/6): the fixed resolver forwards to
            # the unedited _P4_CLOSE_ADMISSION exactly as before, at all three
            # checkpoints -- including this one, which previously called
            # _P4_CLOSE_ADMISSION directly instead of resolver.close_admission
            # (Context point 4's latent bug); the dynamic resolver derives its
            # reconstruction at most once and reuses the frozen result at the
            # later checkpoints. _p4_close_admission/_P4_CLOSE_ADMISSION are
            # never called directly from here any more.
            _p4_close_admission_once(resolver, resolver._snapshot, admission_ref,
                                      (authority, captures, checked_runtime), dynamic_reconstruction_cell)
            streams = tuple(authority._stream_for_published(item[0], "P4 publication required") for item in captures)
            if type(resolver) is _FixedWormTrustedArtifactResolver:
                raw = _P4_PREPARE(_P4_CONTRACT, resolver, admission_ref, streams, checked_runtime, nonces)
            else:
                raw = _P4_DYNAMIC_PREPARE(_P4_DYNAMIC_CONTRACT, resolver, admission_ref, streams, checked_runtime,
                                           nonces, captures, dynamic_reconstruction_cell[0])
            self._fault("prepared")
            self._reserving = streams
            try:
                self._fault("reserved")
                if type(resolver) is _FixedWormTrustedArtifactResolver:
                    batch = _P4_VERIFY_BATCH(_P4_CONTRACT, raw, resolver, admission_ref, streams, checked_runtime, nonces)
                else:
                    batch = _P4_DYNAMIC_VERIFY_BATCH(_P4_DYNAMIC_CONTRACT, raw, resolver, admission_ref, streams,
                                                       checked_runtime, nonces, captures, dynamic_reconstruction_cell[0])
                self._fault("verified")
                self._check()
                _P4_REQUEST_CHECK(authority, captures, checked_runtime, nonces)
                _p4_close_admission_once(resolver, resolver._snapshot, admission_ref,
                                          (authority, captures, checked_runtime), dynamic_reconstruction_cell)
                _hs_refuse(self._request(captures, admission_ref, runtime) == fingerprint)
                captured_set = batch["set"]
                if type(resolver) is _DynamicP4TrustedArtifactResolver:
                    cas = dynamic_reconstruction_cell[0].cas
                else:
                    cas = next(_hs_parse_canonical(raw) for ref, raw in resolver._records
                               if _hs_parse_canonical(ref)["sha256"] == captured_set["capture_admission_set_sha256"])
                key = (captured_set["study_id"], _hs_canonical_bytes(captured_set["execution_authority_ref"]),
                       captured_set["logical_execution_id"], captured_set["run_id"])
                _hs_refuse(not any(entry[0] == key or entry[0][3] == key[3] for entry in self._p4_entries()), "P4 execution identity conflict")
                _hs_refuse(not any(entry[4]._run_identity == key[3] for entry in self._legacy_captures()),
                           "P4 capture cannot reuse a legacy execution run")
                decision = {field: captured_set[field] for field in ("study_id", "execution_authority_ref", "logical_execution_id", "run_id",
                            "broker_verified_plan_sha256", "captured_authorization_set_sha256")}
                decision.update(consumer_document_sha256=cas["consumer_document_sha256"], purpose=cas["purpose"],
                    process_measurement_sha256=runtime["process_measurement_sha256"], issued_at_ms=500, expires_at_ms=1000,
                    session_nonce=os.urandom(32).hex())
                record = object.__new__(CapturedAuthorizationRecord)
                session = LaunchSession(_MAKE, "captured-authorization-v2", key[3], cas["consumer_document_sha256"], decision)
                session_start = {"event": "SessionStartRecord", "session": decision}
                value = dict(batch, admission_ref=dict(admission_ref), consumed=True, execution_key=[key[0], key[1].decode(), key[2], key[3]],
                    session=decision, session_start=session_start, streams=list(streams), nonces=list(nonces))
                value["batch_sha256"] = _digest(_hs_canonical_bytes(value))
                audit = _hs_canonical_bytes(value)
                session_seal = hmac.new(authority._map_key, b"P4 LaunchSession\x00" + _hs_canonical_bytes(decision), hashlib.sha256).hexdigest()
                entry = (key, admission_bytes, fingerprint, audit, record, session, _p4_session_pin(session), session_seal)
                root = (*self._root, entry)
                pin = (authority, self._lock, root)
                # Pre-interning is not authority: every lookup additionally
                # requires membership in the committed immutable ledger root.
                self._fault("intern-before")
                _P4_RECORDS[record] = self
                self._fault("intern-after")
                self._fault("commit-before")
                # The final boundary follows every test schedule and all
                # candidate allocations. Revalidate the held authority, full
                # local/terminal closure and live aliases while still locked.
                self._check()
                _P4_REQUEST_CHECK(authority, captures, checked_runtime, nonces)
                _p4_close_admission_once(resolver, resolver._snapshot, admission_ref,
                                          (authority, captures, checked_runtime), dynamic_reconstruction_cell)
                _hs_refuse(self._request(captures, admission_ref, runtime) == fingerprint)
                # Sole logical publication: consumption, artifacts, claims and
                # session-start are inseparable fields in this immutable root.
                self._root, self._pin = root, pin
                _P4_LEDGER_PINS[self] = pin
                retained = tuple((published, frozen, MappingProxyType(dict(port)))
                                 for published, frozen, port in captures)
                retained_state = _RetainedP4CaptureState(_MAKE, retained)
                _P4_CAPTURE_HANDLES[record] = retained_state
                _P4_CAPTURE_STATE_PINS[retained_state] = _p4_capture_state_seal(
                    authority, self, record, retained_state)
                self._fault("commit-after")
                self._fault("return")
                return record, session
            finally:
                self._reserving = ()


def _p4_session_pin(session):
    """Bind every existing opaque LaunchSession slot to its committed identity."""
    return (session._kind, session._run_identity, session._ended, session._plan_sha256,
            _hs_canonical_bytes(dict(session._runtime)), session._stream_id, session._locked)


_P4_BATCH_PINS = (_P4_SIGNER, _P4_CONTRACT, _P4_BATCH_USES)
_P4_COMMIT = _LifecycleAuthorizationLedger.commit_p4_batch
_P4_LEGACY_PREPARE_METHODS = tuple((name, getattr(_DevelopmentBroker, name)) for name in (
    "_prepare_legacy_capture", "_prepare_receipt", "_store_interned", "_validate_bound_v1_effect",
))
_P4_LEDGER_METHODS = tuple((name, getattr(_LifecycleAuthorizationLedger, name)) for name in (
    "_check", "_fault", "_legacy_gate", "_require_unclaimed", "_nonce_used", "_request", "resolve_p4", "commit_p4_batch",
    "_p4_entries", "_p4_stream_documents", "_legacy_captures", "commit_legacy_capture",
    "_p4_record_capture", "p4_receipt_digest", "read_p4_member",
))


class ReplayRun(Node):
    """The generic ReplayRun consumer node (ADR-0127), role ``replay``.

    Declares exactly two captured inputs, ``tape_manifest`` and
    ``tape_data``, enforced by the document grammar keyed on the owned
    kind name ``replay``. The node's execution — consuming the composed
    tape capability and producing the replay result — is the F3
    captured-tape follow-on; ``run`` therefore refuses rather than mint
    any opaque capability this slice.

    Examples
    --------
    The node is named by the owned kind, never constructed directly::

        node = ReplayRun("replay", {})
        node.role
        # -> 'replay'
    """

    role = "replay"
    outputs = ("result",)
    _PARAMS = ()

    @classmethod
    def validate_params(cls, params):
        """ReplayRun accepts no knobs: default-deny, no ``space`` or other param."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        return problems

    def run(self, ctx, inputs):
        """Refuse execution: the composed-tape broker is the F3 follow-on."""
        raise RuntimeError(
            "replay execution requires the F3 composed-tape broker "
            "(follow-on); the ReplayRun node is declared for its "
            "tape-pair grammar only"
        )


def register(registry=None) -> None:
    """Claim the toolkit-owned ``replay`` kind (owned=True).

    Called by the orchestrator at package import, never at import time.
    A pre-existing ``replay`` entry is verified, never silently accepted:
    a foreign class squatting the owned name raises.
    """
    from dskit.pipeline.node import DEFAULT_NODE_KINDS

    registry = DEFAULT_NODE_KINDS if registry is None else registry
    if "replay" in registry:
        cls, owned = registry.get("replay")
        if cls is not ReplayRun or not owned:
            raise ValueError(
                "kind 'replay' is already registered to a foreign class — "
                "refusing to shadow the owned ReplayRun kind"
            )
        return
    registry.register("replay", ReplayRun, owned=True)


_SYNTHETIC_GRANT_PUBLIC_KEYS = MappingProxyType({
    "G1": ("synthetic-g1/dataset-capture/v1",
           "2dc29709202f88bb35b158fca6db46c9b2c112e5e1403dfef035cda27965af86"),
    "G2": ("synthetic-g2/dataset-capture/v1",
           "0fe1b197035dcba56f3fa49c0bfe63ccb18bab6bd4731d1401371513044ebf1a"),
})
_SYNTHETIC_GRANT_REVOKED = frozenset()
_SYNTHETIC_GRANT_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_SYNTHETIC_GRANT_AUTH_KEYS = frozenset({
    "schema_version", "authorization_id", "source_ids", "scope",
    "license_digests", "event_schema", "media_type",
    "source_roster_root_sha256", "source_roster_publication_receipt_sha256",
    "source_roster_policy_sha256", "correction_bust_metadata_sha256",
    "allow_empty_capture", "issued_at_ms", "not_before_ms", "expires_at_ms",
})
_SYNTHETIC_GRANT_KEYS = frozenset({
    "schema_version", "role", "issuer_key_id", "authorization_sha256",
    "issued_at_ms", "not_before_ms", "expires_at_ms",
    "revocation_snapshot_sha256", "signature",
})


class NonAuthorizingSyntheticGrantVerifier:
    """Check fixed synthetic G1/G2 signatures without granting any lifecycle use."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs):
        """Refuse a subtype that could impersonate the fixed verifier."""
        raise TypeError("the fixed synthetic grant verifier is final")

    @staticmethod
    def _require(ok, message):
        if not ok:
            raise ValueError(message)

    @classmethod
    def _hash(cls, value):
        cls._require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
                     "lowercase SHA-256 is required")

    @classmethod
    def _authorization(cls, value):
        cls._require(type(value) is dict and set(value) == _SYNTHETIC_GRANT_AUTH_KEYS,
                     "closed dataset authorization is required")
        authorization_schema = value["schema_version"]
        event_schema = DATASET_AUTHORIZATION_EVENT_SCHEMAS.get(
            authorization_schema,
        )
        cls._require(event_schema is not None,
                     "dataset authorization version refused")
        cls._require(type(value["authorization_id"]) is str
                     and _SYNTHETIC_GRANT_SOURCE_ID.fullmatch(
                         value["authorization_id"]) is not None,
                     "authorization id refused")
        sources = value["source_ids"]
        cls._require(type(sources) is list
                     and all(type(item) is str
                             and _SYNTHETIC_GRANT_SOURCE_ID.fullmatch(item) is not None
                             for item in sources)
                     and sources == sorted(set(sources)), "canonical source ids refused")
        licenses = value["license_digests"]
        cls._require(type(licenses) is list and all(type(item) is str for item in licenses)
                     and licenses == sorted(set(licenses)),
                     "canonical license digests refused")
        for digest in licenses:
            cls._hash(digest)
        scope = value["scope"]
        cls._require(
            type(scope) is dict
            and set(scope) == set(AUTHORIZATION_SCOPE_FIELDS[event_schema]),
            "closed dataset scope is required",
        )
        start, end = scope["availability_start_ms"], scope["availability_end_ms"]
        cls._require(type(start) is int and type(end) is int and start <= end,
                     "dataset availability scope refused")
        if event_schema == DATASET_AUTHORIZATION_EVENT_SCHEMAS[
            "dskit.dataset-capture-authorization/v2"
        ]:
            cls._require(start >= 0, "dataset availability scope refused")
        cls._hash(scope["source_provenance_sha256"])
        if "tzdata_version_sha256" in scope:
            cls._hash(scope["tzdata_version_sha256"])
            cls._require(
                scope["tzdata_version_sha256"] != _PLACEHOLDER,
                "nonplaceholder tzdata version digest required",
            )
        for name in ("source_roster_root_sha256",
                     "source_roster_publication_receipt_sha256",
                     "source_roster_policy_sha256",
                     "correction_bust_metadata_sha256"):
            cls._hash(value[name])
        cls._require(value["event_schema"] == event_schema
                     and value["media_type"] == "application/x-ndjson"
                     and type(value["allow_empty_capture"]) is bool,
                     "dataset schema, media or empty policy refused")
        issued, first, last = (value[name] for name in (
            "issued_at_ms", "not_before_ms", "expires_at_ms"))
        cls._require(all(type(item) is int for item in (issued, first, last))
                     and issued <= first < last, "dataset grant window refused")

    @classmethod
    def _snapshot(cls):
        revoked = _SYNTHETIC_GRANT_REVOKED
        cls._require(type(revoked) is frozenset
                     and all(type(item) is str for item in revoked),
                     "fixed revocation state refused")
        return _digest(_hs_canonical_bytes({
            "schema_version": "dskit.synthetic-dataset-revocations/v1",
            "revoked": sorted(revoked),
        })), revoked

    @classmethod
    def _grant(cls, raw, role, authorization, authorization_sha256, snapshot, revoked, now):
        value = _hs_parse_canonical(raw)
        cls._require(type(value) is dict and set(value) == _SYNTHETIC_GRANT_KEYS,
                     "closed synthetic grant is required")
        key_id, public_key = _SYNTHETIC_GRANT_PUBLIC_KEYS[role]
        cls._require(value["schema_version"] == "dskit.dataset-capture-grant/v1"
                     and value["role"] == role and value["issuer_key_id"] == key_id
                     and value["authorization_sha256"] == authorization_sha256,
                     "synthetic grant role or authorization refused")
        cls._hash(value["authorization_sha256"])
        cls._hash(value["revocation_snapshot_sha256"])
        cls._require(value["revocation_snapshot_sha256"] == snapshot,
                     "stale synthetic grant revocation snapshot refused")
        cls._require(all(type(value[name]) is int for name in (
            "issued_at_ms", "not_before_ms", "expires_at_ms"))
                     and all(value[name] == authorization[name] for name in (
                         "issued_at_ms", "not_before_ms", "expires_at_ms"))
                     and value["issued_at_ms"] <= value["not_before_ms"] <= now
                     < value["expires_at_ms"], "synthetic grant time refused")
        cls._require(not {role, key_id, authorization["authorization_id"]} & revoked,
                     "revoked synthetic grant refused")
        preimage = _hs_canonical_bytes({key: item for key, item in value.items()
                                        if key != "signature"})
        _p4_verify_ed25519(public_key, preimage, value["signature"])
        return value

    def verify(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes):
        """Return immutable checked facts only; repeated calls recheck every rule."""
        authorization = _hs_parse_canonical(authorization_bytes)
        self._authorization(authorization)
        authorization_sha256 = _digest(authorization_bytes)
        snapshot, revoked = self._snapshot()
        now = _FixedP4VerificationClock.now_ms(_P4_VERIFICATION._clock)
        self._require(authorization["issued_at_ms"] <= authorization["not_before_ms"] <= now
                      < authorization["expires_at_ms"]
                      and authorization["authorization_id"] not in revoked,
                      "dataset authorization time or revocation refused")
        self._grant(g1_grant_bytes, "G1", authorization, authorization_sha256,
                    snapshot, revoked, now)
        self._grant(g2_grant_bytes, "G2", authorization, authorization_sha256,
                    snapshot, revoked, now)
        return MappingProxyType({
            "authorization_sha256": authorization_sha256,
            "g1_grant_sha256": _digest(g1_grant_bytes),
            "g2_grant_sha256": _digest(g2_grant_bytes),
            "checked_at_ms": now,
            "revocation_snapshot_sha256": snapshot,
            "authorizing": False,
            "deployment_eligible": False,
        })


_SYNTHETIC_FIXTURE_KEY_ID = "synthetic-g2/dataset-fixture-attestation/v1"
_SYNTHETIC_FIXTURE_PUBLIC_KEY = (
    "8514688e8c6ee8224c630a1a76beae880efe2404a2f1302b5f8d9d78ccfbd5a8"
)
_SYNTHETIC_FIXTURE_KEYS = frozenset({
    "schema_version", "issuer_key_id", "authorization_sha256",
    "issued_at_ms", "not_before_ms", "expires_at_ms",
    "revocation_snapshot_sha256", "ordered_members", "signature",
})
_SYNTHETIC_FIXTURE_MEMBER_KEYS = frozenset({
    "member_name", "source_id", "byte_length", "sha256",
})
_SYNTHETIC_FIXTURE_MEMBER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class NonAuthorizingSyntheticFixtureVerifier:
    """Check signed synthetic member commitments without granting a read."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs):
        """Refuse a subtype that could impersonate the fixed verifier."""
        raise TypeError("the fixed synthetic fixture verifier is final")

    def verify(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes,
               attestation_bytes):
        """Return immutable commitments after fresh fixed-grant verification."""
        grant_facts = NonAuthorizingSyntheticGrantVerifier().verify(
            authorization_bytes, g1_grant_bytes, g2_grant_bytes,
        )
        authorization = _hs_parse_canonical(authorization_bytes)
        source_ids = authorization["source_ids"]
        _hs_refuse(bool(source_ids), "nonempty signed source ids required")
        attestation = _hs_parse_canonical(attestation_bytes)
        _hs_refuse(type(attestation) is dict
                   and set(attestation) == _SYNTHETIC_FIXTURE_KEYS,
                   "closed synthetic fixture attestation required")
        _hs_refuse(
            attestation["schema_version"]
            == "dskit.synthetic-dataset-fixture-attestation/v1"
            and attestation["issuer_key_id"] == _SYNTHETIC_FIXTURE_KEY_ID
            and attestation["authorization_sha256"]
            == grant_facts["authorization_sha256"],
            "synthetic fixture identity refused",
        )
        for name in ("authorization_sha256", "revocation_snapshot_sha256"):
            NonAuthorizingSyntheticGrantVerifier._hash(attestation[name])
        window = ("issued_at_ms", "not_before_ms", "expires_at_ms")
        _hs_refuse(
            all(type(attestation[name]) is int
                and attestation[name] == authorization[name] for name in window),
            "synthetic fixture time claims refused",
        )
        members = attestation["ordered_members"]
        _hs_refuse(type(members) is list and len(members) == len(source_ids),
                   "synthetic fixture source membership refused")
        seen_names = set()
        for index, member in enumerate(members):
            _hs_refuse(type(member) is dict
                       and set(member) == _SYNTHETIC_FIXTURE_MEMBER_KEYS,
                       "closed synthetic fixture member required")
            name = member["member_name"]
            _hs_refuse(
                type(name) is str
                and _SYNTHETIC_FIXTURE_MEMBER_NAME.fullmatch(name) is not None
                and name not in seen_names
                and member["source_id"] == source_ids[index]
                and type(member["byte_length"]) is int
                and member["byte_length"] >= 0,
                "synthetic fixture member identity or length refused",
            )
            seen_names.add(name)
            NonAuthorizingSyntheticGrantVerifier._hash(member["sha256"])
        snapshot, revoked = NonAuthorizingSyntheticGrantVerifier._snapshot()
        now = _FixedP4VerificationClock.now_ms(_P4_VERIFICATION._clock)
        _hs_refuse(
            attestation["revocation_snapshot_sha256"]
            == grant_facts["revocation_snapshot_sha256"] == snapshot
            and authorization["not_before_ms"] <= now
            < authorization["expires_at_ms"]
            and not {
                "G2-fixture", _SYNTHETIC_FIXTURE_KEY_ID,
                authorization["authorization_id"],
            } & revoked,
            "synthetic fixture time or revocation refused",
        )
        preimage = _hs_canonical_bytes({
            key: value for key, value in attestation.items()
            if key != "signature"
        })
        _p4_verify_ed25519(
            _SYNTHETIC_FIXTURE_PUBLIC_KEY, preimage, attestation["signature"],
        )
        return MappingProxyType({
            "authorization_sha256": grant_facts["authorization_sha256"],
            "attestation_sha256": _digest(attestation_bytes),
            "ordered_members": tuple(MappingProxyType(dict(member))
                                     for member in members),
            "checked_at_ms": now,
            "revocation_snapshot_sha256": snapshot,
            "authorizing": False,
            "deployment_eligible": False,
        })



_SYNTHETIC_ROSTER_BOOTSTRAP_KEYS = MappingProxyType({
    "G1": (
        "synthetic-g1/roster-bootstrap/v1",
        "5b6c3f069aded6254b5dd6852b22143f9b3157f47506f007a9d702850eb7ce92",
    ),
    "G2": (
        "synthetic-g2/roster-bootstrap/v1",
        "84abc9abfe69736a62e7d90239fbbd4faecbbc6119e7f1915c92c9f619618665",
    ),
})
_SYNTHETIC_ROSTER_BOOTSTRAP_AUTH_KEYS = frozenset({
    "schema_version", "bootstrap_id", "source_ids", "scope",
    "license_digests", "event_schema", "media_type",
    "source_rank_policy_sha256", "issued_at_ms", "not_before_ms",
    "expires_at_ms",
})
_SYNTHETIC_ROSTER_BOOTSTRAP_GRANT_KEYS = frozenset({
    "schema_version", "role", "issuer_key_id", "bootstrap_sha256",
    "issued_at_ms", "not_before_ms", "expires_at_ms",
    "revocation_snapshot_sha256", "signature",
})


class NonAuthorizingRosterBootstrapVerifier:
    """Verify signed pre-roster G1/G2 authority without granting a lifecycle use.

    Examples
    --------
    Construct the fixed read-only verifier before passing signed fixture bytes::

        verifier = NonAuthorizingRosterBootstrapVerifier()
        # -> verifier verifies bytes; it never publishes a root
    """

    __slots__ = ()

    def __init_subclass__(cls, **kwargs):
        """Prevent a subtype from impersonating the fixed verifier."""
        raise TypeError("the fixed roster bootstrap verifier is final")

    @staticmethod
    def _authorization(raw):
        """Parse and validate one closed bootstrap authorization."""
        value = _hs_parse_canonical(raw)
        _hs_refuse(type(value) is dict
                   and set(value) == _SYNTHETIC_ROSTER_BOOTSTRAP_AUTH_KEYS,
                   "closed roster bootstrap authorization required")
        authorization_schema = value["schema_version"]
        event_schema = ROSTER_AUTHORIZATION_EVENT_SCHEMAS.get(
            authorization_schema,
        )
        _hs_refuse(event_schema is not None,
                   "roster bootstrap authorization version refused")
        name = value["bootstrap_id"]
        _hs_refuse(type(name) is str
                   and _SYNTHETIC_GRANT_SOURCE_ID.fullmatch(name) is not None,
                   "bootstrap id refused")
        sources = value["source_ids"]
        _hs_refuse(type(sources) is list and bool(sources)
                   and all(type(item) is str
                           and _SYNTHETIC_GRANT_SOURCE_ID.fullmatch(item)
                           is not None for item in sources)
                   and sources == sorted(set(sources)),
                   "canonical nonempty source ids required")
        scope = value["scope"]
        _hs_refuse(
            type(scope) is dict
            and set(scope) == set(AUTHORIZATION_SCOPE_FIELDS[event_schema]),
            "closed roster bootstrap scope required",
        )
        start, end = scope["availability_start_ms"], scope["availability_end_ms"]
        _hs_refuse(type(start) is int and type(end) is int and start <= end,
                   "roster bootstrap availability refused")
        if event_schema == ROSTER_AUTHORIZATION_EVENT_SCHEMAS[
            "dskit.roster-bootstrap-authorization/v2"
        ]:
            _hs_refuse(start >= 0, "roster bootstrap availability refused")
        NonAuthorizingSyntheticGrantVerifier._hash(
            scope["source_provenance_sha256"],
        )
        if "tzdata_version_sha256" in scope:
            NonAuthorizingSyntheticGrantVerifier._hash(
                scope["tzdata_version_sha256"],
            )
            _hs_refuse(
                scope["tzdata_version_sha256"] != _PLACEHOLDER,
                "nonplaceholder tzdata version digest required",
            )
        licenses = value["license_digests"]
        _hs_refuse(type(licenses) is list
                   and all(type(item) is str for item in licenses)
                   and licenses == sorted(set(licenses)),
                   "canonical license digests required")
        for digest in licenses:
            NonAuthorizingSyntheticGrantVerifier._hash(digest)
        _hs_refuse(value["event_schema"] == event_schema
                   and value["media_type"] == "application/x-ndjson",
                   "roster bootstrap schema or media refused")
        policy = {
            "schema_version": "dskit.source-rank-policy/v1",
            "sources": [
                {"source_id": source_id, "rank": rank}
                for rank, source_id in enumerate(sources)
            ],
        }
        policy_sha256 = _digest(_hs_canonical_bytes(policy))
        NonAuthorizingSyntheticGrantVerifier._hash(
            value["source_rank_policy_sha256"],
        )
        _hs_refuse(value["source_rank_policy_sha256"] == policy_sha256,
                   "derived source rank policy refused")
        window = tuple(value[field] for field in (
            "issued_at_ms", "not_before_ms", "expires_at_ms"
        ))
        _hs_refuse(all(type(item) is int for item in window)
                   and window[0] <= window[1] < window[2],
                   "roster bootstrap validity window refused")
        return value, policy_sha256

    @staticmethod
    def _grant(raw, role, authorization, bootstrap_sha256, snapshot,
               revoked, now):
        """Verify one fixed role/key signature and live grant."""
        value = _hs_parse_canonical(raw)
        _hs_refuse(type(value) is dict
                   and set(value) == _SYNTHETIC_ROSTER_BOOTSTRAP_GRANT_KEYS,
                   "closed roster bootstrap grant required")
        key_id, public_key = _SYNTHETIC_ROSTER_BOOTSTRAP_KEYS[role]
        _hs_refuse(
            value["schema_version"] == "dskit.roster-bootstrap-grant/v1"
            and value["role"] == role
            and value["issuer_key_id"] == key_id
            and value["bootstrap_sha256"] == bootstrap_sha256,
            "roster bootstrap grant role or identity refused",
        )
        NonAuthorizingSyntheticGrantVerifier._hash(value["bootstrap_sha256"])
        NonAuthorizingSyntheticGrantVerifier._hash(
            value["revocation_snapshot_sha256"],
        )
        _hs_refuse(value["revocation_snapshot_sha256"] == snapshot,
                   "stale roster bootstrap revocation snapshot refused")
        fields = ("issued_at_ms", "not_before_ms", "expires_at_ms")
        _hs_refuse(
            all(type(value[field]) is int
                and value[field] == authorization[field]
                for field in fields)
            and value["issued_at_ms"] <= value["not_before_ms"] <= now
            < value["expires_at_ms"],
            "roster bootstrap grant time refused",
        )
        _hs_refuse(
            not {role, key_id, authorization["bootstrap_id"]} & revoked,
            "revoked roster bootstrap grant refused",
        )
        preimage = _hs_canonical_bytes({
            key: item for key, item in value.items() if key != "signature"
        })
        _p4_verify_ed25519(public_key, preimage, value["signature"])

    def verify(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes):
        """Return immutable checked facts after fresh signatures and policy.

        Parameters
        ----------
        authorization_bytes : bytes
            Exact canonical RosterBootstrapAuthorization.v1 bytes.
        g1_grant_bytes : bytes
            Exact signed security-owner grant bytes.
        g2_grant_bytes : bytes
            Exact signed data-owner grant bytes.

        Returns
        -------
        MappingProxyType
            Nonauthorizing digests, derived policy identity and check time.

        Raises
        ------
        TypeError
            Any input is not an exact bytes object.
        ValueError
            Canonical syntax, authority, policy or live-state check fails.
        """
        authorization, policy_sha256 = self._authorization(
            authorization_bytes,
        )
        bootstrap_sha256 = _digest(authorization_bytes)
        snapshot, revoked = NonAuthorizingSyntheticGrantVerifier._snapshot()
        now = _FixedP4VerificationClock.now_ms(_P4_VERIFICATION._clock)
        _hs_refuse(
            authorization["not_before_ms"] <= now
            < authorization["expires_at_ms"]
            and authorization["bootstrap_id"] not in revoked,
            "roster bootstrap time or revocation refused",
        )
        self._grant(g1_grant_bytes, "G1", authorization, bootstrap_sha256,
                    snapshot, revoked, now)
        self._grant(g2_grant_bytes, "G2", authorization, bootstrap_sha256,
                    snapshot, revoked, now)
        return MappingProxyType({
            "bootstrap_sha256": bootstrap_sha256,
            "g1_grant_sha256": _digest(g1_grant_bytes),
            "g2_grant_sha256": _digest(g2_grant_bytes),
            "source_rank_policy_sha256": policy_sha256,
            "checked_at_ms": now,
            "revocation_snapshot_sha256": snapshot,
            "authorizing": False,
            "deployment_eligible": False,
        })




def _derive_synthetic_roster_publish_intent(authorization_bytes,
                                            g1_grant_bytes, g2_grant_bytes):
    """Derive fixed roster and publish-intent bytes without authority/effects."""
    authorization, policy_sha256 = (
        NonAuthorizingRosterBootstrapVerifier._authorization(
            authorization_bytes,
        )
    )
    _hs_parse_canonical(g1_grant_bytes)
    _hs_parse_canonical(g2_grant_bytes)
    bootstrap_sha256 = _digest(authorization_bytes)
    policy = {
        "schema_version": "dskit.source-rank-policy/v1",
        "sources": [
            {"source_id": source_id, "rank": rank}
            for rank, source_id in enumerate(authorization["source_ids"])
        ],
        "policy_sha256": policy_sha256,
    }
    roster_bytes = _hs_canonical_bytes({
        "schema_version": "dskit.source-roster-capture/v1",
        "scope": authorization["scope"],
        "source_ids": authorization["source_ids"],
        "policy": policy,
    })
    roster_sha256 = _digest(roster_bytes)
    root = {
        "root_ref": "synthetic-roster/" + bootstrap_sha256,
        "root_id": _digest(
            ("dskit.synthetic-roster-root-id/v1:"
             + bootstrap_sha256).encode("ascii")
        ),
        "snapshot_version": "v1",
    }
    producer_document_sha256 = _digest(_hs_canonical_bytes({
        "schema": "dskit.synthetic-roster-producer/v1",
        "bootstrap_sha256": bootstrap_sha256,
        "producer_node": "source-roster-capture",
        "producer_output": "source_roster",
        "purpose": "source-roster",
    }))
    producer = {
        "run_identity": "synthetic-roster-run/" + bootstrap_sha256,
        "document_sha256": producer_document_sha256,
        "node": "source-roster-capture",
        "output": "source_roster",
        "purpose": "source-roster",
    }
    receipt_key = {
        "receipt_schema": "dskit.root-publication-receipt/v2",
        "publication_authorization_ref": {
            "kind": "roster-bootstrap",
            "roster_bootstrap_authorization_sha256": bootstrap_sha256,
        },
        "producer_run_identity": producer["run_identity"],
        "producer_document_sha256": producer_document_sha256,
        "producer_node": producer["node"],
        "producer_output": producer["output"],
        **root,
    }
    intent = {
        "schema_version": "dskit.synthetic-roster-publish-intent/v1",
        "bootstrap_id": authorization["bootstrap_id"],
        "bootstrap_sha256": bootstrap_sha256,
        "g1_grant_sha256": _digest(g1_grant_bytes),
        "g2_grant_sha256": _digest(g2_grant_bytes),
        "roster_sha256": roster_sha256,
        "roster_byte_length": len(roster_bytes),
        "source_rank_policy_sha256": policy_sha256,
        "expected_members": [{
            "relative_path": "source_roster.json",
            "media_type": "application/json",
            "sha256": roster_sha256,
            "byte_length": len(roster_bytes),
        }],
        "root": root,
        "producer": producer,
        "output_member": "source_roster.json",
        "receipt_key": receipt_key,
    }
    return roster_bytes, _hs_canonical_bytes(intent)


_SYNTHETIC_RESERVE_DOMAIN = "dskit.synthetic-authorization-reserve/v1"
_SYNTHETIC_RESERVE_SCHEMA = (
    "CREATE TABLE reserve_meta (singleton INTEGER PRIMARY KEY CHECK (singleton=1), "
    "domain TEXT NOT NULL, generation INTEGER NOT NULL, now_ms INTEGER NOT NULL)",
    "CREATE TABLE reserve_revoked (token TEXT PRIMARY KEY, generation INTEGER NOT NULL)",
    "CREATE TABLE reserve_uses (kind TEXT NOT NULL, signed_id TEXT NOT NULL, "
    "authorization_sha256 TEXT NOT NULL, g1_sha256 TEXT NOT NULL, "
    "g2_sha256 TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, "
    "intent_sha256 TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY (kind,signed_id))",
    "CREATE TABLE reserve_audit (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
    "signed_id TEXT NOT NULL, old_state TEXT, new_state TEXT NOT NULL, "
    "generation INTEGER NOT NULL)",
)


class _SyntheticAuthorizationReserve:
    """Keep one trusted, non-effecting reservation domain across processes.

    Examples
    --------
    Provision once from trusted host setup, then open the same store::

        _SyntheticAuthorizationReserve._provision("/tmp/reserve.sqlite")
        store = _SyntheticAuthorizationReserve("/tmp/reserve.sqlite")
        store._close()
    """

    __slots__ = ("_path", "_connection")

    def __init_subclass__(cls, **kwargs):
        """Keep the internal store final."""
        raise TypeError("the fixed synthetic reserve is final")

    @staticmethod
    def _path_ok(path):
        """Require a trusted absolute path, never a SQLite URI."""
        _hs_refuse(type(path) is str and os.path.isabs(path)
                   and not path.startswith("file:")
                   and not os.path.islink(path),
                   "trusted absolute reserve path required")

    @classmethod
    def _provision(cls, path):
        """Create a fixed domain only from trusted host setup."""
        cls._path_ok(path)
        _hs_refuse(not os.path.exists(path), "reserve database already exists")
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            _hs_refuse(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                       == "wal", "reserve WAL required")
            connection.execute("PRAGMA synchronous=FULL")
            _hs_refuse(connection.execute("PRAGMA synchronous").fetchone()[0]
                       == 2, "reserve FULL sync required")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SYNTHETIC_RESERVE_SCHEMA:
                connection.execute(statement)
            connection.execute("INSERT INTO reserve_meta VALUES (1, ?, 0, 500)",
                               (_SYNTHETIC_RESERVE_DOMAIN,))
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def __init__(self, path):
        """Open only a pre-provisioned host-owned local database."""
        from urllib.parse import quote

        self._path_ok(path)
        _hs_refuse(os.path.isfile(path), "pre-provisioned reserve required")
        uri = "file:" + quote(path, safe="/") + "?mode=rw"
        try:
            connection = sqlite3.connect(uri, uri=True, isolation_level=None,
                                         timeout=5)
        except sqlite3.Error as exc:
            raise ValueError("pre-provisioned reserve required") from exc
        self._path, self._connection = path, connection
        try:
            connection.execute("PRAGMA synchronous=FULL")
            self._check()
            schema = {row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table'"
            )}
            _hs_refuse(schema == set(_SYNTHETIC_RESERVE_SCHEMA),
                       "fixed reserve schema required")
            rows = connection.execute(
                "SELECT singleton,domain,generation,now_ms FROM reserve_meta"
            ).fetchall()
            _hs_refuse(len(rows) == 1 and rows[0][0] == 1
                       and rows[0][1] == _SYNTHETIC_RESERVE_DOMAIN
                       and type(rows[0][2]) is int and rows[0][2] >= 0
                       and type(rows[0][3]) is int and rows[0][3] >= 500,
                       "fixed reserve domain and clock required")
        except Exception:
            connection.close()
            raise

    def _check(self):
        """Refuse WAL, sync or file downgrades before a transaction."""
        _hs_refuse(
            self._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            and self._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            and os.path.isfile(self._path),
            "reserve WAL/FULL/local database required",
        )

    def _now(self):
        """Read shared monotone synthetic time within the current transaction."""
        row = self._connection.execute(
            "SELECT now_ms FROM reserve_meta WHERE singleton=1"
        ).fetchone()
        _hs_refuse(row is not None and type(row[0]) is int
                   and row[0] >= 500, "trusted synthetic clock required")
        return row[0]

    def _advance_clock(self, now_ms):
        """Trusted host administration advances shared logical time only."""
        _hs_refuse(type(now_ms) is int, "exact integer clock advance required")
        self._check()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check()
            previous = connection.execute(
                "SELECT now_ms FROM reserve_meta WHERE singleton=1"
            ).fetchone()
            _hs_refuse(previous is not None and type(previous[0]) is int
                       and now_ms > previous[0],
                       "synthetic clock must advance")
            connection.execute(
                "UPDATE reserve_meta SET now_ms=? WHERE singleton=1",
                (now_ms,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _snapshot(self):
        """Read shared revocation generation, set and signed digest."""
        generation = self._connection.execute(
            "SELECT generation FROM reserve_meta WHERE singleton=1"
        ).fetchone()[0]
        revoked = frozenset(row[0] for row in self._connection.execute(
            "SELECT token FROM reserve_revoked"
        ))
        snapshot = _digest(_hs_canonical_bytes({
            "schema_version": "dskit.synthetic-dataset-revocations/v1",
            "revoked": sorted(revoked),
        }))
        return generation, revoked, snapshot

    def _reserve_roster(self, authorization_bytes, g1_grant_bytes,
                        g2_grant_bytes, intent_sha256, exact_intent_bytes=None):
        """Spend one signed bootstrap ID without issuing a permit."""
        NonAuthorizingSyntheticGrantVerifier._hash(intent_sha256)
        authorization, _policy = (
            NonAuthorizingRosterBootstrapVerifier._authorization(
                authorization_bytes,
            )
        )
        bootstrap_sha256 = _digest(authorization_bytes)
        self._check()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check()
            generation, revoked, snapshot = self._snapshot()
            if exact_intent_bytes is not None:
                _roster, derived = _derive_synthetic_roster_publish_intent(
                    authorization_bytes, g1_grant_bytes, g2_grant_bytes,
                )
                _hs_refuse(type(exact_intent_bytes) is bytes
                           and exact_intent_bytes == derived
                           and _digest(derived) == intent_sha256,
                           "closed roster reserve intent refused")
            now = self._now()
            _hs_refuse(
                authorization["not_before_ms"] <= now
                < authorization["expires_at_ms"]
                and authorization["bootstrap_id"] not in revoked,
                "roster reserve time or revocation refused",
            )
            verifier = NonAuthorizingRosterBootstrapVerifier
            verifier._grant(g1_grant_bytes, "G1", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            verifier._grant(g2_grant_bytes, "G2", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            connection.execute(
                "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
                ("roster-bootstrap", authorization["bootstrap_id"],
                 bootstrap_sha256, _digest(g1_grant_bytes),
                 _digest(g2_grant_bytes), snapshot, intent_sha256, "RESERVED"),
            )
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,NULL,?,?)",
                ("roster-bootstrap", authorization["bootstrap_id"],
                 "RESERVED", generation),
            )
            final_now = self._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "roster reserve time expired before commit",
            )
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ValueError("signed bootstrap ID already spent") from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _admit_roster_transition(self, authorization_bytes, g1_grant_bytes,
                                 g2_grant_bytes, intent_bytes, new_state):
        """Commit one exact roster lifecycle admission without invoking F4."""
        _hs_refuse(type(intent_bytes) is bytes, "exact roster intent bytes required")
        intent_sha256 = _digest(intent_bytes)
        sequence = ("RESERVED", "SESSION_STARTED", "PRODUCED", "SEALED",
                    "PUBLISHED", "SESSION_ENDED")
        _hs_refuse(type(new_state) is str and new_state in sequence[1:],
                   "unknown roster transition")
        NonAuthorizingSyntheticGrantVerifier._hash(intent_sha256)
        authorization, _policy = (
            NonAuthorizingRosterBootstrapVerifier._authorization(
                authorization_bytes,
            )
        )
        bootstrap_sha256 = _digest(authorization_bytes)
        g1_sha256 = _digest(g1_grant_bytes)
        g2_sha256 = _digest(g2_grant_bytes)
        expected_old = sequence[sequence.index(new_state) - 1]
        self._check()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check()
            generation, revoked, snapshot = self._snapshot()
            _roster, derived = _derive_synthetic_roster_publish_intent(
                authorization_bytes, g1_grant_bytes, g2_grant_bytes,
            )
            _hs_refuse(derived == intent_bytes,
                       "closed roster transition intent refused")
            now = self._now()
            _hs_refuse(
                authorization["not_before_ms"] <= now
                < authorization["expires_at_ms"]
                and authorization["bootstrap_id"] not in revoked,
                "roster transition time or revocation refused",
            )
            verifier = NonAuthorizingRosterBootstrapVerifier
            verifier._grant(g1_grant_bytes, "G1", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            verifier._grant(g2_grant_bytes, "G2", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            row = connection.execute(
                "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                "WHERE kind=? AND signed_id=?",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            ).fetchone()
            _hs_refuse(
                row == (bootstrap_sha256, g1_sha256, g2_sha256,
                        snapshot, intent_sha256, expected_old),
                "roster transition identity or state refused",
            )
            result = connection.execute(
                "UPDATE reserve_uses SET state=? WHERE kind=? AND signed_id=? "
                "AND state=?",
                (new_state, "roster-bootstrap", authorization["bootstrap_id"],
                 expected_old),
            )
            _hs_refuse(result.rowcount == 1, "roster transition lost state")
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("roster-bootstrap", authorization["bootstrap_id"],
                 expected_old, new_state, generation),
            )
            final_now = self._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "roster transition time expired before commit",
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _revoke(self, token):
        """Atomically append one trusted revocation and generation."""
        _hs_refuse(type(token) is str
                   and _SYNTHETIC_GRANT_SOURCE_ID.fullmatch(token) is not None,
                   "canonical revocation token required")
        self._check()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check()
            generation, revoked, _digest_value = self._snapshot()
            _hs_refuse(token not in revoked, "revocation already recorded")
            connection.execute("INSERT INTO reserve_revoked VALUES (?,?)",
                               (token, generation + 1))
            connection.execute(
                "UPDATE reserve_meta SET generation=? WHERE singleton=1",
                (generation + 1,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _close(self):
        """Close without releasing any signed ID."""
        self._connection.close()


class _SyntheticRosterPublisher:
    """One-process nondeployment roster publisher with a shared spent-ID store."""

    __slots__ = ("_reserve", "_broker", "_outer_receipts", "_retained", "_closed")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the synthetic roster publisher is final")

    def __init__(self, reserve_path):
        self._reserve = _SyntheticAuthorizationReserve(reserve_path)
        self._broker = _development_broker(start_ms=500)
        self._outer_receipts = {}
        self._retained = {}
        self._closed = False

    @staticmethod
    def _sign(payload, self_field):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        preimage = _hs_canonical_bytes(payload)
        seed = bytes.fromhex(_digest(
            b"dskit.synthetic-roster-root-publication/v1"
        ))
        signature = Ed25519PrivateKey.from_private_bytes(seed).sign(
            preimage
        ).hex()
        _p4_verify_ed25519(
            "03ad7440941bf1c06b1d7c326f4d37d2a3c1abd514a9c1f98aa8ed03858731cd",
            preimage, signature,
        )
        return _hs_canonical_bytes({
            **payload, self_field: _digest(preimage), "signature": signature,
        })

    @staticmethod
    def _check_signed_output(raw, payload, self_field):
        """Compare every signed field with the frozen committed preimage."""
        value = _hs_parse_canonical(raw)
        _hs_refuse(
            type(value) is dict
            and set(value) == set(payload) | {self_field, "signature"}
            and {key: item for key, item in value.items()
                 if key not in (self_field, "signature")} == payload,
            "synthetic roster signer changed committed fields",
        )
        preimage = _hs_canonical_bytes(payload)
        _hs_refuse(value[self_field] == _digest(preimage),
                   "synthetic roster signer self digest mismatch")
        _p4_verify_ed25519(
            "03ad7440941bf1c06b1d7c326f4d37d2a3c1abd514a9c1f98aa8ed03858731cd",
            preimage, value["signature"],
        )

    def _quarantine(self, authorization, intent_sha256):
        """Terminalize locally even if the durable quarantine write fails."""
        self._closed = True
        connection = self._reserve._connection
        try:
            self._reserve._check()
            connection.execute("BEGIN IMMEDIATE")
            self._reserve._check()
            generation, _revoked, _snapshot = self._reserve._snapshot()
            row = connection.execute(
                "SELECT state,intent_sha256 FROM reserve_uses "
                "WHERE kind=? AND signed_id=?",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            ).fetchone()
            if row is not None and row[0] != "QUARANTINED":
                _hs_refuse(row[1] == intent_sha256, "quarantine intent mismatch")
                connection.execute(
                    "UPDATE reserve_uses SET state='QUARANTINED' "
                    "WHERE kind=? AND signed_id=?",
                    ("roster-bootstrap", authorization["bootstrap_id"]),
                )
                connection.execute(
                    "INSERT INTO reserve_audit "
                    "(kind,signed_id,old_state,new_state,generation) "
                    "VALUES (?,?,?,?,?)",
                    ("roster-bootstrap", authorization["bootstrap_id"],
                     row[0], "QUARANTINED", generation),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")

    def _advance(self, authorization_bytes, g1, g2, intent_bytes, state):
        _roster, derived = _derive_synthetic_roster_publish_intent(
            authorization_bytes, g1, g2,
        )
        _hs_refuse(derived == intent_bytes, "roster intent changed")
        self._reserve._admit_roster_transition(
            authorization_bytes, g1, g2, intent_bytes, state,
        )

    def _published_facts(self, published, roster_bytes, intent):
        """Compare retained F4 WORM output and exact publication identity."""
        stream = self._broker._diagnostic_stream_for_published(published)
        self._broker._reload_stream(stream)
        audit = self._broker._receipt_audit(published)
        _hs_refuse(
            len(audit) == 3
            and [row["event"] for row in audit] ==
            ["PRODUCED", "SEALED", "PUBLISHED"],
            "incomplete roster F4 stream",
        )
        final = audit[-1]
        producer = intent["producer"]
        root = intent["root"]
        for key in ("root_ref", "root_id", "snapshot_version"):
            _hs_refuse(final[key] == root[key], "roster root mismatch")
        for key, field in (
            ("run_identity", "producer_run_identity"),
            ("document_sha256", "producer_document_sha256"),
            ("node", "producer_node"), ("output", "producer_output"),
        ):
            _hs_refuse(final[field] == producer[key],
                       "roster producer mismatch")
        _hs_refuse(
            final["member_manifest_sha256"] ==
            audit[1]["member_manifest_sha256"],
            "roster member manifest mismatch",
        )
        snapshot = self._broker._provider.describe(
            root["root_ref"], root["snapshot_version"],
        )
        raw = self._broker._provider.open_member(
            snapshot, intent["output_member"],
        )
        _hs_refuse(type(raw) is bytes and raw == roster_bytes,
                   "roster WORM bytes mismatch")
        manifest = [{
            "relative_path": intent["output_member"],
            "media_type": "application/json",
            "sha256": _digest(roster_bytes),
            "bytes": len(roster_bytes),
        }]
        _hs_refuse(
            final["member_manifest_sha256"] ==
            _digest(_hs_canonical_bytes(manifest)),
            "roster WORM manifest mismatch",
        )
        return {key: final[key] for key in (
            "root_ref", "root_id", "snapshot_version",
            "member_manifest_sha256", "producer_run_identity",
            "producer_document_sha256", "producer_node", "producer_output",
        )}

    def _issue_receipt(self, authorization_bytes, g1, g2, roster_bytes,
                       intent_bytes, published):
        """Commit receipt admission, then sign and put once in-call."""
        authorization, _policy = (
            NonAuthorizingRosterBootstrapVerifier._authorization(
                authorization_bytes,
            )
        )
        intent = _hs_parse_canonical(intent_bytes)
        intent_sha256 = _digest(intent_bytes)
        reserve = self._reserve
        connection = reserve._connection
        reserve._check()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, revoked, snapshot = reserve._snapshot()
            now = self._reserve._now()
            bootstrap_sha256 = _digest(authorization_bytes)
            _hs_refuse(
                authorization["not_before_ms"] <= now
                < authorization["expires_at_ms"]
                and not {
                    authorization["bootstrap_id"], "data-publisher",
                    "data-publisher/root-publication-bootstrap-g1-g2",
                } & revoked,
                "roster receipt authority revoked or expired",
            )
            verifier = NonAuthorizingRosterBootstrapVerifier
            verifier._grant(g1, "G1", authorization, bootstrap_sha256,
                            snapshot, revoked, now)
            verifier._grant(g2, "G2", authorization, bootstrap_sha256,
                            snapshot, revoked, now)
            _roster, derived = _derive_synthetic_roster_publish_intent(
                authorization_bytes, g1, g2,
            )
            _hs_refuse(_roster == roster_bytes and derived == intent_bytes,
                       "roster receipt intent changed")
            row = connection.execute(
                "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                "WHERE kind=? AND signed_id=?",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            ).fetchone()
            _hs_refuse(
                row == (bootstrap_sha256, _digest(g1), _digest(g2),
                        snapshot, intent_sha256, "SESSION_ENDED"),
                "roster receipt reservation mismatch",
            )
            facts = self._published_facts(published, roster_bytes, intent)
            refs = [
                {"kind": "roster-bootstrap-authorization",
                 "role": "security-data",
                 "schema": authorization["schema_version"],
                 "sha256": bootstrap_sha256},
                {"kind": "roster-bootstrap-grant", "role": "G1",
                 "schema": "dskit.roster-bootstrap-grant/v1",
                 "sha256": _digest(g1)},
                {"kind": "roster-bootstrap-grant", "role": "G2",
                 "schema": "dskit.roster-bootstrap-grant/v1",
                 "sha256": _digest(g2)},
            ]
            refs.sort(key=lambda ref: tuple(
                ref[key] for key in ("kind", "role", "schema", "sha256")
            ))
            suffix = {
                "issuer_role": "data-publisher",
                "key_usage": "root-publication-bootstrap-g1-g2",
                "signature_alg": "Ed25519",
                "issued_at_ms": now,
                "not_before_ms": now,
                "expires_at_ms": authorization["expires_at_ms"],
                "revocation_snapshot_sha256": snapshot,
                "key": {
                    "key_id": "data-publisher/root-publication-bootstrap-g1-g2",
                    "key_version": 1,
                },
            }
            basis_payload = {
                "schema": "dskit.issuance-basis/v2",
                "kind": "roster-root-publication",
                "study_id": "synthetic-study",
                "refs": refs,
                "publish_intent_sha256": intent_sha256,
                **suffix,
            }
            basis_sha256 = _digest(_hs_canonical_bytes(basis_payload))
            receipt_payload = {
                "schema": "dskit.root-publication-receipt/v2",
                "kind": "dataset-capture",
                "capture_kind": "source-roster",
                "publication_authorization_ref":
                    intent["receipt_key"]["publication_authorization_ref"],
                **facts,
                "issuance_basis_sha256": basis_sha256,
                **suffix,
            }
            final_now = self._reserve._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "roster receipt expired before commit",
            )
            result = connection.execute(
                "UPDATE reserve_uses SET state='RECEIPT_ISSUED' "
                "WHERE kind=? AND signed_id=? AND state='SESSION_ENDED'",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            )
            _hs_refuse(result.rowcount == 1, "roster receipt lost state")
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("roster-bootstrap", authorization["bootstrap_id"],
                 "SESSION_ENDED", "RECEIPT_ISSUED", generation),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        basis_bytes = self._sign(basis_payload, "issuance_basis_sha256")
        self._check_signed_output(
            basis_bytes, basis_payload, "issuance_basis_sha256",
        )
        _hs_refuse(
            _hs_parse_canonical(basis_bytes)["issuance_basis_sha256"] ==
            basis_sha256,
            "roster issuance basis mismatch",
        )
        receipt_bytes = self._sign(
            receipt_payload, "root_publication_receipt_sha256",
        )
        self._check_signed_output(
            receipt_bytes, receipt_payload,
            "root_publication_receipt_sha256",
        )
        key = (
            intent["receipt_key"]["receipt_schema"],
            _hs_canonical_bytes(
                intent["receipt_key"]["publication_authorization_ref"]
            ),
            *(
                intent["receipt_key"][field] for field in (
                    "producer_run_identity", "producer_document_sha256",
                    "producer_node", "producer_output", "root_ref",
                    "root_id", "snapshot_version",
                )
            ),
        )
        existing = self._outer_receipts.get(key)
        _hs_refuse(existing is None or existing == receipt_bytes,
                   "outer roster receipt WORM conflict")
        if existing is None:
            self._outer_receipts[key] = receipt_bytes
        return basis_bytes, receipt_bytes

    def publish(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes):
        """Publish one synthetic roster or leave its signed ID spent."""
        _hs_refuse(not self._closed, "roster publisher terminalized")
        roster_bytes, intent_bytes = _derive_synthetic_roster_publish_intent(
            authorization_bytes, g1_grant_bytes, g2_grant_bytes,
        )
        authorization, _policy = (
            NonAuthorizingRosterBootstrapVerifier._authorization(
                authorization_bytes,
            )
        )
        intent = _hs_parse_canonical(intent_bytes)
        intent_sha256 = _digest(intent_bytes)
        self._reserve._reserve_roster(
            authorization_bytes, g1_grant_bytes, g2_grant_bytes,
            intent_sha256, exact_intent_bytes=intent_bytes,
        )
        try:
            self._advance(authorization_bytes, g1_grant_bytes,
                          g2_grant_bytes, intent_bytes, "SESSION_STARTED")
            producer = intent["producer"]
            root = intent["root"]
            session = self._broker.start_producer_session(
                run_identity=producer["run_identity"],
                process_measurement_sha256=_digest(
                    b"dskit.synthetic-roster-process/v1"
                ),
                runtime_sha256=_digest(
                    b"dskit.synthetic-roster-runtime/v1"
                ),
                plan_sha256=_digest(b"dskit.synthetic-roster-plan/v1"),
            )
            _hs_refuse(session._run_identity == producer["run_identity"],
                       "roster F4 run identity mismatch")
            self._advance(authorization_bytes, g1_grant_bytes,
                          g2_grant_bytes, intent_bytes, "PRODUCED")
            prepared = self._broker.produce(
                session,
                producer={key: producer[key] for key in (
                    "run_identity", "document_sha256", "node", "output",
                )},
                root=root,
                purpose=producer["purpose"],
                expected_members=(intent["output_member"],),
                members=[{
                    "relative_path": intent["output_member"],
                    "media_type": "application/json",
                    "bytes": roster_bytes,
                    "file_type": "regular",
                    "link_count": 1,
                }],
                output_member=intent["output_member"],
                completed=True,
                planned=True,
                transition_nonce="roster-" + intent["bootstrap_sha256"]
                    + "-produced",
            )
            self._advance(authorization_bytes, g1_grant_bytes,
                          g2_grant_bytes, intent_bytes, "SEALED")
            sealed = self._broker.seal(
                session, prepared,
                transition_nonce="roster-" + intent["bootstrap_sha256"]
                    + "-sealed",
            )
            self._advance(authorization_bytes, g1_grant_bytes,
                          g2_grant_bytes, intent_bytes, "PUBLISHED")
            published = self._broker.publish(
                session, sealed,
                transition_nonce="roster-" + intent["bootstrap_sha256"]
                    + "-published",
            )
            self._published_facts(published, roster_bytes, intent)
            self._advance(authorization_bytes, g1_grant_bytes,
                          g2_grant_bytes, intent_bytes, "SESSION_ENDED")
            self._broker.end_session(session)
            basis_bytes, receipt_bytes = self._issue_receipt(
                authorization_bytes, g1_grant_bytes, g2_grant_bytes,
                roster_bytes, intent_bytes, published,
            )
            self._retained[authorization["bootstrap_id"]] = (
                published, roster_bytes, basis_bytes, receipt_bytes,
                authorization_bytes, g1_grant_bytes, g2_grant_bytes,
                intent_bytes, session,
            )
            return roster_bytes, basis_bytes, receipt_bytes
        except Exception:
            self._quarantine(authorization, intent_sha256)
            raise

    def proof(self):
        """Return a fixed read-only proof bound to this live publisher."""
        _hs_refuse(not self._closed, "roster publisher terminalized")
        return NonAuthorizingRosterRootProof(_MAKE, self)


class NonAuthorizingRosterRootProof:
    """Read-only exact roster/v2 root evidence from one live synthetic broker."""

    __slots__ = ("_publisher", "_locked")

    def __init_subclass__(cls, **kwargs):
        """Forbid a subclass from replacing the fixed proof checks."""
        raise TypeError("the fixed roster-root proof is final")

    def __init__(self, token, publisher):
        if token is not _MAKE or type(publisher) is not _SyntheticRosterPublisher:
            raise TypeError("broker-owned roster-root proof required")
        object.__setattr__(self, "_publisher", publisher)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Keep the construction-owned publisher binding frozen."""
        if getattr(self, "_locked", False):
            raise AttributeError("roster-root proof is frozen")
        object.__setattr__(self, name, value)

    @staticmethod
    def _signed_payload(raw, expected, self_field):
        value = _hs_parse_canonical(raw)
        _SyntheticRosterPublisher._check_signed_output(
            raw, expected, self_field,
        )
        return value

    def verify(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes,
               basis_bytes, receipt_bytes, _under_writer_lock=False):
        """Return immutable identity facts; never an F4/raw/P4 permit."""
        _hs_refuse(
            all(type(raw) is bytes for raw in (
                authorization_bytes, g1_grant_bytes, g2_grant_bytes,
                basis_bytes, receipt_bytes,
            )),
            "exact signed roster proof bytes required",
        )
        publisher = self._publisher
        _hs_refuse(type(publisher) is _SyntheticRosterPublisher
                   and not publisher._closed,
                   "live roster publisher required")
        authorization, policy_sha256 = (
            NonAuthorizingRosterBootstrapVerifier._authorization(
                authorization_bytes,
            )
        )
        retained = publisher._retained.get(authorization["bootstrap_id"])
        _hs_refuse(type(retained) is tuple and len(retained) == 9,
                   "live retained roster publication required")
        (published, roster_bytes, original_basis, original_receipt,
         original_auth, original_g1, original_g2, intent_bytes,
         session) = retained
        _hs_refuse(
            (authorization_bytes, g1_grant_bytes, g2_grant_bytes,
             basis_bytes, receipt_bytes) ==
            (original_auth, original_g1, original_g2,
             original_basis, original_receipt),
            "retained roster proof bytes mismatch",
        )
        _roster, derived_intent = _derive_synthetic_roster_publish_intent(
            authorization_bytes, g1_grant_bytes, g2_grant_bytes,
        )
        _hs_refuse(_roster == roster_bytes and derived_intent == intent_bytes,
                   "roster proof intent mismatch")
        intent = _hs_parse_canonical(intent_bytes)
        bootstrap_sha256 = _digest(authorization_bytes)
        intent_sha256 = _digest(intent_bytes)
        reserve = publisher._reserve
        reserve._check()
        connection = reserve._connection
        try:
            if _under_writer_lock:
                _hs_refuse(connection.in_transaction,
                           "raw writer transaction required")
            else:
                connection.execute("BEGIN")
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            _hs_refuse(
                authorization["not_before_ms"] <= now
                < authorization["expires_at_ms"]
                and not {
                    authorization["bootstrap_id"], "data-publisher",
                    "data-publisher/root-publication-bootstrap-g1-g2",
                } & revoked,
                "roster proof authority revoked or expired",
            )
            verifier = NonAuthorizingRosterBootstrapVerifier
            verifier._grant(g1_grant_bytes, "G1", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            verifier._grant(g2_grant_bytes, "G2", authorization,
                            bootstrap_sha256, snapshot, revoked, now)
            row = connection.execute(
                "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                "WHERE kind=? AND signed_id=?",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            ).fetchone()
            _hs_refuse(
                row == (bootstrap_sha256, _digest(g1_grant_bytes),
                        _digest(g2_grant_bytes), snapshot,
                        intent_sha256, "RECEIPT_ISSUED"),
                "roster proof reserve mismatch",
            )
            audit = connection.execute(
                "SELECT old_state,new_state,generation FROM reserve_audit "
                "WHERE kind=? AND signed_id=? ORDER BY seq",
                ("roster-bootstrap", authorization["bootstrap_id"]),
            ).fetchall()
            states = (
                "RESERVED", "SESSION_STARTED", "PRODUCED", "SEALED",
                "PUBLISHED", "SESSION_ENDED", "RECEIPT_ISSUED",
            )
            _hs_refuse(
                len(audit) == len(states)
                and all(
                    old == (states[index - 1] if index else None)
                    and new == state
                    and type(saved_generation) is int
                    and saved_generation == generation
                    for index, ((old, new, saved_generation), state)
                    in enumerate(zip(audit, states, strict=True))
                ),
                "complete roster reservation audit required",
            )
            if not _under_writer_lock:
                connection.execute("COMMIT")
        except Exception:
            if not _under_writer_lock and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        _hs_refuse(
            type(session) is LaunchSession
            and session._run_identity == intent["producer"]["run_identity"]
            and session._ended is True,
            "retained ended F4 producer session required",
        )
        facts = publisher._published_facts(
            published, roster_bytes, intent,
        )
        refs = [
            {"kind": "roster-bootstrap-authorization",
             "role": "security-data",
             "schema": authorization["schema_version"],
             "sha256": bootstrap_sha256},
            {"kind": "roster-bootstrap-grant", "role": "G1",
             "schema": "dskit.roster-bootstrap-grant/v1",
             "sha256": _digest(g1_grant_bytes)},
            {"kind": "roster-bootstrap-grant", "role": "G2",
             "schema": "dskit.roster-bootstrap-grant/v1",
             "sha256": _digest(g2_grant_bytes)},
        ]
        refs.sort(key=lambda ref: tuple(
            ref[key] for key in ("kind", "role", "schema", "sha256")
        ))
        basis = _hs_parse_canonical(basis_bytes)
        _hs_refuse(type(basis) is dict
                   and type(basis.get("issued_at_ms")) is int
                   and authorization["not_before_ms"] <= basis["issued_at_ms"]
                   <= now, "roster proof basis time refused")
        suffix = {
            "issuer_role": "data-publisher",
            "key_usage": "root-publication-bootstrap-g1-g2",
            "signature_alg": "Ed25519",
            "issued_at_ms": basis["issued_at_ms"],
            "not_before_ms": basis["issued_at_ms"],
            "expires_at_ms": authorization["expires_at_ms"],
            "revocation_snapshot_sha256": snapshot,
            "key": {
                "key_id": "data-publisher/root-publication-bootstrap-g1-g2",
                "key_version": 1,
            },
        }
        expected_basis = {
            "schema": "dskit.issuance-basis/v2",
            "kind": "roster-root-publication",
            "study_id": "synthetic-study",
            "refs": refs,
            "publish_intent_sha256": intent_sha256,
            **suffix,
        }
        basis = self._signed_payload(
            basis_bytes, expected_basis, "issuance_basis_sha256",
        )
        expected_receipt = {
            "schema": "dskit.root-publication-receipt/v2",
            "kind": "dataset-capture",
            "capture_kind": "source-roster",
            "publication_authorization_ref":
                intent["receipt_key"]["publication_authorization_ref"],
            **facts,
            "issuance_basis_sha256": basis["issuance_basis_sha256"],
            **suffix,
        }
        receipt = self._signed_payload(
            receipt_bytes, expected_receipt,
            "root_publication_receipt_sha256",
        )
        key_fields = intent["receipt_key"]
        key = (
            key_fields["receipt_schema"],
            _hs_canonical_bytes(key_fields["publication_authorization_ref"]),
            *(key_fields[field] for field in (
                "producer_run_identity", "producer_document_sha256",
                "producer_node", "producer_output", "root_ref",
                "root_id", "snapshot_version",
            )),
        )
        _hs_refuse(
            publisher._outer_receipts.get(key) == receipt_bytes
            and sum(value == receipt_bytes
                    for value in publisher._outer_receipts.values()) == 1,
            "persisted roster receipt mismatch",
        )
        reserve._check()
        final_generation, _final_revoked, final_snapshot = reserve._snapshot()
        final_now = reserve._now()
        _hs_refuse(
            final_generation == generation
            and final_snapshot == snapshot
            and authorization["not_before_ms"] <= final_now
            < authorization["expires_at_ms"],
            "roster proof freshness changed",
        )
        root_sha256 = _digest(_hs_canonical_bytes({
            key: receipt[key] for key in (
                "root_ref", "root_id", "snapshot_version",
                "member_manifest_sha256",
            )
        }))
        return MappingProxyType({
            "bootstrap_sha256": bootstrap_sha256,
            "roster_root_sha256": root_sha256,
            "roster_publication_receipt_sha256":
                receipt["root_publication_receipt_sha256"],
            "source_rank_policy_sha256": policy_sha256,
            "publish_intent_sha256": intent_sha256,
            "checked_at_ms": final_now,
            "authorizing": False,
            "deployment_eligible": False,
        })


def _build_synthetic_environment_broker():
    """Build one closure-owned, nondeployment environment fact broker."""
    mapping_proxy = MappingProxyType
    weak_map = WeakKeyDictionary
    weak_set = WeakSet
    weak_ref = weakref_ref
    canonical_bytes = _canonical_bytes
    digest = _digest

    payload = mapping_proxy({
        "schema_version": "dskit.synthetic-environment-fact/v1",
        "environment_id": "dskit.synthetic-environment/v1",
        "tzdata_version": "synthetic-2026a",
        "tzdata_version_sha256":
            "cd690e4a500811dbc1ca0a79f0e5a8d9eb99debd5dc3c8d9bef5e278bf350cd0",
        "deployment_eligible": False,
    })
    payload_digest = digest(canonical_bytes(dict(payload)))
    state_domain_digest = digest(canonical_bytes({
        "schema_version": "dskit.synthetic-environment-state-domain/v1",
        "payload_sha256": payload_digest,
    }))
    mint_token = object()
    issued = weak_set()
    records = weak_map()

    class _SyntheticEnvironmentIdentity:
        __slots__ = (
            "_schema_version", "_environment_id", "_tzdata_version",
            "_tzdata_version_sha256", "_deployment_eligible", "__weakref__",
        )

        def __new__(cls, token=None):
            if cls is not _SyntheticEnvironmentIdentity or token is not mint_token:
                raise TypeError("broker-issued synthetic environment identity required")
            return object.__new__(cls)

        def __init__(self, token=None):
            if token is not mint_token:
                raise TypeError("broker-issued synthetic environment identity required")
            for key, value in payload.items():
                object.__setattr__(self, "_" + key, value)
            issued.add(self)
            records[self] = (
                weak_ref(self), payload, payload_digest, state_domain_digest,
            )

        def __init_subclass__(cls, **kwargs):
            del cls, kwargs
            raise TypeError("the synthetic environment identity is final")

        def __setattr__(self, name, value):
            del self, name, value
            raise AttributeError("synthetic environment identity is frozen")

        def __reduce__(self):
            raise TypeError("synthetic environment identity cannot be serialized")

        def __reduce_ex__(self, protocol):
            del protocol
            raise TypeError("synthetic environment identity cannot be serialized")

    identity_type = _SyntheticEnvironmentIdentity
    expected_slots = tuple(("_" + key, value) for key, value in payload.items())
    expected_descriptors = tuple(
        (slot, type.__getattribute__(identity_type, "__dict__")[slot])
        for slot, _value in expected_slots
    )

    def synthetic_environment_identity():
        return identity_type(mint_token)

    def synthetic_environment_facts(identity):
        valid = type(identity) is identity_type and identity in issued
        try:
            record = records[identity] if valid else None
        except (KeyError, TypeError):
            record = None
        valid = (
            valid
            and type(record) is tuple
            and len(record) == 4
            and type(record[0]) is weak_ref
            and record[0]() is identity
            and record[1] is payload
            and type(record[2]) is str
            and record[2] == payload_digest
            and type(record[3]) is str
            and record[3] == state_domain_digest
            and all(
                type.__getattribute__(identity_type, "__dict__").get(slot)
                is descriptor
                for slot, descriptor in expected_descriptors
            )
            and all(
                type(object.__getattribute__(identity, slot)) is type(value)
                and object.__getattribute__(identity, slot) == value
                for slot, value in expected_slots
            )
        )
        if not valid:
            raise ValueError("synthetic environment identity refused")
        return mapping_proxy(dict(payload))

    def require_synthetic_tzdata(identity, signed_tzdata_version_sha256):
        facts = synthetic_environment_facts(identity)
        expected = facts["tzdata_version_sha256"]
        if not (
            type(signed_tzdata_version_sha256) is str
            and len(signed_tzdata_version_sha256) == 64
            and signed_tzdata_version_sha256 == signed_tzdata_version_sha256.lower()
            and signed_tzdata_version_sha256 == expected
        ):
            raise ValueError("synthetic tzdata identity refused")

    return (
        identity_type, synthetic_environment_identity,
        synthetic_environment_facts, require_synthetic_tzdata,
    )


(
    _SyntheticEnvironmentIdentity,
    _synthetic_environment_identity,
    _synthetic_environment_facts,
    _require_synthetic_tzdata,
) = _build_synthetic_environment_broker()
del _build_synthetic_environment_broker


_SYNTHETIC_EMPTY_CORRECTION_METADATA = _hs_canonical_bytes({
    "corrections": [],
    "schema_version": "dskit.correction-bust-metadata/v1",
})
class _SyntheticFixtureSource:
    """Trusted host-installed synthetic source; lookup only on admitted read."""

    __slots__ = ("_members", "_read_names")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the synthetic fixture source is final")

    def __init__(self, members):
        _hs_refuse(type(members) is dict, "host-owned fixture mapping required")
        self._members = members
        self._read_names = []

    @property
    def read_names(self):
        return tuple(self._read_names)

    def _read(self, member_name):
        self._read_names.append(member_name)
        try:
            value = self._members[member_name]
        except KeyError as exc:
            raise ValueError("signed fixture member missing") from exc
        _hs_refuse(type(value) is bytes, "fixture source must return bytes")
        return value


_SYNTHETIC_RAW_FIXTURE_FACTS = WeakKeyDictionary()


class VerifiedSyntheticDatasetFixture:
    """Opaque one-process validated fixture facts, without publication authority."""

    __slots__ = ("_owner", "_intent", "_members", "_events", "_event_schema",
                 "_used", "event_count", "member_names", "deployment_eligible",
                 "_locked", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the validated synthetic fixture is final")

    def __init__(self, token, owner, intent, members, events, event_schema):
        if token is not _MAKE or type(owner) is not _SyntheticRawPreflight:
            raise TypeError("broker-issued synthetic fixture required")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_intent", intent)
        object.__setattr__(self, "_members", members)
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "_event_schema", event_schema)
        object.__setattr__(self, "_used", False)
        object.__setattr__(self, "event_count", len(events))
        object.__setattr__(self, "member_names", tuple(name for name, _ in members))
        object.__setattr__(self, "deployment_eligible", False)
        object.__setattr__(self, "_locked", True)
        _SYNTHETIC_RAW_FIXTURE_FACTS[self] = (
            weakref_ref(self), owner, event_schema, _digest(intent),
        )

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("validated synthetic fixture is frozen")
        object.__setattr__(self, name, value)


class _SyntheticRawPreflight:
    """Verify signed raw bytes after a durable one-use read admission."""

    __slots__ = ("_publisher", "_source", "_closed")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the synthetic raw preflight is final")

    def __init__(self, publisher, source):
        _hs_refuse(type(publisher) is _SyntheticRosterPublisher
                   and type(source) is _SyntheticFixtureSource,
                   "trusted roster publisher and fixture source required")
        self._publisher = publisher
        self._source = source
        self._closed = False

    @staticmethod
    def _attestation(raw, authorization, authorization_sha256,
                     snapshot, revoked, now):
        value = _hs_parse_canonical(raw)
        _hs_refuse(type(value) is dict
                   and set(value) == _SYNTHETIC_FIXTURE_KEYS
                   and value["schema_version"]
                   == "dskit.synthetic-dataset-fixture-attestation/v1"
                   and value["issuer_key_id"] == _SYNTHETIC_FIXTURE_KEY_ID
                   and value["authorization_sha256"] == authorization_sha256
                   and value["revocation_snapshot_sha256"] == snapshot,
                   "signed fixture identity refused")
        _hs_refuse(
            all(type(value[name]) is int
                and value[name] == authorization[name]
                for name in ("issued_at_ms", "not_before_ms", "expires_at_ms"))
            and authorization["not_before_ms"] <= now
            < authorization["expires_at_ms"]
            and not {
                "G2-fixture", _SYNTHETIC_FIXTURE_KEY_ID,
                authorization["authorization_id"],
            } & revoked,
            "signed fixture authority refused",
        )
        members = value["ordered_members"]
        sources = authorization["source_ids"]
        _hs_refuse(type(members) is list and len(members) == len(sources),
                   "signed fixture source count refused")
        names = set()
        for index, member in enumerate(members):
            _hs_refuse(
                type(member) is dict
                and set(member) == _SYNTHETIC_FIXTURE_MEMBER_KEYS
                and type(member["member_name"]) is str
                and _SYNTHETIC_FIXTURE_MEMBER_NAME.fullmatch(
                    member["member_name"]) is not None
                and member["member_name"] not in names
                and member["source_id"] == sources[index]
                and type(member["byte_length"]) is int
                and member["byte_length"] >= 0,
                "signed fixture member refused",
            )
            names.add(member["member_name"])
            NonAuthorizingSyntheticGrantVerifier._hash(member["sha256"])
        preimage = _hs_canonical_bytes({
            key: item for key, item in value.items() if key != "signature"
        })
        _p4_verify_ed25519(
            _SYNTHETIC_FIXTURE_PUBLIC_KEY, preimage, value["signature"],
        )
        return members

    def _derive_intent(self, signed, roster, snapshot, revoked, now):
        authorization_bytes, g1, g2, attestation_bytes = signed
        bootstrap_bytes, bg1, bg2, basis_bytes, receipt_bytes = roster
        authorization = _hs_parse_canonical(authorization_bytes)
        verifier = NonAuthorizingSyntheticGrantVerifier
        verifier._authorization(authorization)
        auth_sha256 = _digest(authorization_bytes)
        _hs_refuse(
            authorization["source_ids"]
            and authorization["not_before_ms"] <= now
            < authorization["expires_at_ms"]
            and authorization["authorization_id"] not in revoked,
            "raw authorization time or revocation refused",
        )
        verifier._grant(g1, "G1", authorization, auth_sha256,
                        snapshot, revoked, now)
        verifier._grant(g2, "G2", authorization, auth_sha256,
                        snapshot, revoked, now)
        members = self._attestation(
            attestation_bytes, authorization, auth_sha256,
            snapshot, revoked, now,
        )
        bootstrap, policy_sha256 = (
            NonAuthorizingRosterBootstrapVerifier._authorization(bootstrap_bytes)
        )
        bootstrap_sha256 = _digest(bootstrap_bytes)
        _hs_refuse(
            bootstrap["not_before_ms"] <= authorization["not_before_ms"]
            and authorization["expires_at_ms"] <= bootstrap["expires_at_ms"]
            and bootstrap["not_before_ms"] <= now < bootstrap["expires_at_ms"]
            and bootstrap["bootstrap_id"] not in revoked,
            "raw bootstrap window refused",
        )
        bverifier = NonAuthorizingRosterBootstrapVerifier
        bverifier._grant(bg1, "G1", bootstrap, bootstrap_sha256,
                         snapshot, revoked, now)
        bverifier._grant(bg2, "G2", bootstrap, bootstrap_sha256,
                         snapshot, revoked, now)
        facts = self._publisher.proof().verify(
            bootstrap_bytes, bg1, bg2, basis_bytes, receipt_bytes,
            _under_writer_lock=True,
        )
        roster_basis = _hs_parse_canonical(basis_bytes)
        roster_receipt = _hs_parse_canonical(receipt_bytes)
        _hs_refuse(
            authorization["issued_at_ms"]
            > max(roster_basis["issued_at_ms"],
                  roster_receipt["issued_at_ms"]),
            "dataset authorization must issue after roster receipt",
        )
        _hs_refuse(
            all(authorization[name] == bootstrap[name] for name in (
                "source_ids", "scope", "license_digests",
                "event_schema", "media_type",
            ))
            and authorization["source_roster_root_sha256"]
            == facts["roster_root_sha256"]
            and authorization["source_roster_publication_receipt_sha256"]
            == facts["roster_publication_receipt_sha256"]
            and authorization["source_roster_policy_sha256"]
            == facts["source_rank_policy_sha256"] == policy_sha256
            and authorization["correction_bust_metadata_sha256"]
            == _digest(_SYNTHETIC_EMPTY_CORRECTION_METADATA),
            "post-roster raw equality refused",
        )
        intent = {
            "schema_version": "dskit.synthetic-raw-read-intent/v1",
            "authorization_id": authorization["authorization_id"],
            "dataset_authorization_sha256": auth_sha256,
            "dataset_g1_sha256": _digest(g1),
            "dataset_g2_sha256": _digest(g2),
            "fixture_attestation_sha256": _digest(attestation_bytes),
            "ordered_members": members,
            "bootstrap_id": bootstrap["bootstrap_id"],
            "bootstrap_authorization_sha256": bootstrap_sha256,
            "bootstrap_g1_sha256": _digest(bg1),
            "bootstrap_g2_sha256": _digest(bg2),
            "roster_basis_sha256": _digest(basis_bytes),
            "roster_receipt_sha256": _digest(receipt_bytes),
            "roster_root_sha256": facts["roster_root_sha256"],
            "source_rank_policy_sha256": policy_sha256,
        }
        return authorization, _hs_canonical_bytes(intent)

    def _transition(self, signed, roster, prior_intent=None):
        reserve = self._publisher._reserve
        reserve._check()
        connection = reserve._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            authorization, intent = self._derive_intent(
                signed, roster, snapshot, revoked, now,
            )
            signed_id = authorization["authorization_id"]
            auth_sha = _digest(signed[0])
            g1_sha, g2_sha = _digest(signed[1]), _digest(signed[2])
            intent_sha = _digest(intent)
            if prior_intent is None:
                connection.execute(
                    "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
                    ("raw-dataset", signed_id, auth_sha, g1_sha, g2_sha,
                     snapshot, intent_sha, "RESERVED"),
                )
                old_state, new_state = None, "RESERVED"
            else:
                _hs_refuse(intent == prior_intent,
                           "raw read intent changed")
                row = connection.execute(
                    "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                    "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                    "WHERE kind=? AND signed_id=?",
                    ("raw-dataset", signed_id),
                ).fetchone()
                _hs_refuse(
                    row == (auth_sha, g1_sha, g2_sha, snapshot,
                            intent_sha, "RESERVED"),
                    "raw read reservation changed",
                )
                result = connection.execute(
                    "UPDATE reserve_uses SET state='RAW_READ_STARTED' "
                    "WHERE kind='raw-dataset' AND signed_id=? "
                    "AND state='RESERVED'",
                    (signed_id,),
                )
                _hs_refuse(result.rowcount == 1,
                           "raw read admission lost state")
                old_state, new_state = "RESERVED", "RAW_READ_STARTED"
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("raw-dataset", signed_id, old_state, new_state, generation),
            )
            final_now = reserve._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "raw authority expired before commit",
            )
            connection.execute("COMMIT")
            return authorization, intent
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ValueError("signed raw authorization ID already spent") from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _parse_member(raw, member, scope, event_schema, seen):
        _hs_refuse(len(raw) == member["byte_length"]
                   and _digest(raw) == member["sha256"],
                   "signed fixture bytes mismatch")
        if not raw:
            return ()
        _hs_refuse(raw.endswith(b"\n"), "raw NDJSON LF required")
        events = []
        for line in raw[:-1].split(b"\n"):
            _hs_refuse(bool(line), "empty raw NDJSON line refused")
            event = _hs_parse_canonical(line)
            fields = RAW_EVENT_FIELDS.get(event_schema)
            _hs_refuse(
                type(event) is dict
                and fields is not None
                and set(event) == set(fields)
                and event["schema_version"] == event_schema
                and event["source_id"] == member["source_id"]
                and type(event["event_id"]) is str
                and bool(event["event_id"])
                and type(event["source_sequence"]) is int
                and event["source_sequence"] >= 0
                and type(event["availability_ms"]) is int
                and scope["availability_start_ms"]
                <= event["availability_ms"]
                <= scope["availability_end_ms"],
                "closed raw event refused",
            )
            if event_schema == DATASET_AUTHORIZATION_EVENT_SCHEMAS[
                "dskit.dataset-capture-authorization/v2"
            ]:
                _hs_refuse(
                    all(
                        type(event[name]) is int and event[name] >= 0
                        for name in (
                            "exchange_ms", "receive_ms",
                            "correction_position",
                        )
                    )
                    and all(
                        type(event[name]) is str and bool(event[name])
                        for name in (
                            "source_provenance_tag", "source_timezone_tag",
                        )
                    )
                    and (
                        event["corrects_event_id"] is None
                        or (
                            type(event["corrects_event_id"]) is str
                            and bool(event["corrects_event_id"])
                        )
                    ),
                    "closed raw event refused",
                )
            NonAuthorizingSyntheticGrantVerifier._hash(
                event["payload_sha256"],
            )
            _hs_refuse(event["event_id"] not in seen,
                       "duplicate raw event ID refused")
            seen.add(event["event_id"])
            events.append(MappingProxyType(event))
        return tuple(events)

    def verify(self, authorization_bytes, g1_grant_bytes, g2_grant_bytes,
               attestation_bytes, bootstrap_bytes, bg1, bg2,
               basis_bytes, receipt_bytes):
        """Spend signed ID, admit read, then inspect each signed member once."""
        _hs_refuse(not self._closed, "raw preflight terminalized")
        signed = (authorization_bytes, g1_grant_bytes,
                  g2_grant_bytes, attestation_bytes)
        roster = (bootstrap_bytes, bg1, bg2, basis_bytes, receipt_bytes)
        try:
            authorization, intent = self._transition(signed, roster)
            authorization, confirmed = self._transition(
                signed, roster, prior_intent=intent,
            )
            _hs_refuse(confirmed == intent, "raw read intent changed")
            members = _hs_parse_canonical(intent)["ordered_members"]
            seen = set()
            retained = []
            events = []
            for member in members:
                name = member["member_name"]
                raw = self._source._read(name)
                retained.append((name, raw))
                events.extend(self._parse_member(
                    raw, member, authorization["scope"],
                    authorization["event_schema"], seen,
                ))
            _hs_refuse(authorization["allow_empty_capture"] or events,
                       "nonempty raw capture required")
            self._closed = True
            return VerifiedSyntheticDatasetFixture(
                _MAKE, self, intent, tuple(retained), tuple(events),
                authorization["event_schema"],
            )
        except Exception:
            self._closed = True
            raise


class _SyntheticRawPublisher:
    """One-shot synthetic raw F4 publisher from a validated fixture proof."""

    __slots__ = ("_preflight", "_roster_publisher", "_reserve", "_broker",
                 "_closed", "_retained", "_root_pis_pairs", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the synthetic raw publisher is final")

    def __init__(self, preflight):
        _hs_refuse(type(preflight) is _SyntheticRawPreflight
                   and preflight._closed
                   and type(preflight._publisher) is _SyntheticRosterPublisher,
                   "completed raw preflight required")
        self._preflight = preflight
        self._roster_publisher = preflight._publisher
        self._reserve = preflight._publisher._reserve
        self._broker = preflight._publisher._broker
        self._closed = False
        self._retained = None
        self._root_pis_pairs = {}

    @staticmethod
    def _manifest(proof, authorization_bytes):
        auth = _hs_parse_canonical(authorization_bytes)
        ordered = _hs_parse_canonical(proof._intent)["ordered_members"]
        _hs_refuse(tuple(name for name, _raw in proof._members)
                   == tuple(member["member_name"] for member in ordered),
                   "validated raw member order changed")
        _hs_refuse(all(
            type(raw) is bytes
            and len(raw) == member["byte_length"]
            and _digest(raw) == member["sha256"]
            for (_name, raw), member in zip(proof._members, ordered, strict=True)
        ), "validated raw member bytes changed")
        _hs_refuse(
            all(member["member_name"] != "raw_event_dataset.json"
                for member in ordered),
            "raw output member collision",
        )
        manifest = {
            "schema_version": "dskit.raw-event-dataset-capture/v1",
            "dataset_capture_authorization_sha256": _digest(
                authorization_bytes,
            ),
            "scope": auth["scope"],
            "source_roster_root_sha256": auth["source_roster_root_sha256"],
            "source_roster_publication_receipt_sha256":
                auth["source_roster_publication_receipt_sha256"],
            "source_roster_policy_sha256":
                auth["source_roster_policy_sha256"],
            "license_digests": auth["license_digests"],
            "event_schema": auth["event_schema"],
            "media_type": auth["media_type"],
            "ordered_member_digests": [{
                **member, "media_type": "application/x-ndjson",
            } for member in ordered],
            "correction_bust_metadata_sha256":
                auth["correction_bust_metadata_sha256"],
        }
        return _hs_canonical_bytes(manifest)

    @staticmethod
    def _identity(authorization_bytes):
        auth_sha = _digest(authorization_bytes)
        root = {
            "root_ref": "synthetic-raw/" + auth_sha,
            "root_id": _digest(
                ("dskit.synthetic-raw-root-id/v1:" + auth_sha).encode("ascii")
            ),
            "snapshot_version": "v1",
        }
        producer = {
            "run_identity": "synthetic-raw-run/" + auth_sha,
            "document_sha256": _digest(_hs_canonical_bytes({
                "schema": "dskit.synthetic-raw-producer/v1",
                "authorization_sha256": auth_sha,
                "producer_node": "raw-event-dataset-capture",
                "producer_output": "raw_event_dataset",
                "purpose": "raw-event-dataset",
            })),
            "node": "raw-event-dataset-capture",
            "output": "raw_event_dataset",
            "purpose": "raw-event-dataset",
        }
        return root, producer

    def _check_row(self, signed, roster, proof, expected_state,
                   connection, snapshot, revoked, now):
        authorization, derived = self._preflight._derive_intent(
            signed, roster, snapshot, revoked, now,
        )
        _hs_refuse(derived == proof._intent,
                   "raw proof intent changed")
        row = connection.execute(
            "SELECT authorization_sha256,g1_sha256,g2_sha256,"
            "snapshot_sha256,intent_sha256,state FROM reserve_uses "
            "WHERE kind='raw-dataset' AND signed_id=?",
            (authorization["authorization_id"],),
        ).fetchone()
        _hs_refuse(
            row == (_digest(signed[0]), _digest(signed[1]),
                    _digest(signed[2]), snapshot,
                    _digest(derived), expected_state),
            "raw publication reservation changed",
        )
        return authorization

    def _advance(self, signed, roster, proof, new_state):
        sequence = (
            "RAW_READ_STARTED", "SESSION_STARTED", "PRODUCED", "SEALED",
            "PUBLISHED", "SESSION_ENDED",
        )
        _hs_refuse(new_state in sequence[1:],
                   "unknown raw publication transition")
        expected_old = sequence[sequence.index(new_state) - 1]
        reserve = self._reserve
        reserve._check()
        connection = reserve._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            authorization = self._check_row(
                signed, roster, proof, expected_old,
                connection, snapshot, revoked, now,
            )
            if new_state != "SESSION_STARTED":
                root, producer = self._identity(signed[0])
                stream = _digest(_hs_canonical_bytes({
                    "producer": {key: producer[key] for key in (
                        "run_identity", "document_sha256", "node", "output",
                    )},
                    "root": root,
                    "purpose": producer["purpose"],
                }))
                self._broker._reload_stream(stream)
            result = connection.execute(
                "UPDATE reserve_uses SET state=? "
                "WHERE kind='raw-dataset' AND signed_id=? AND state=?",
                (new_state, authorization["authorization_id"], expected_old),
            )
            _hs_refuse(result.rowcount == 1,
                       "raw publication state lost")
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("raw-dataset", authorization["authorization_id"],
                 expected_old, new_state, generation),
            )
            final_now = reserve._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "raw publication expired before commit",
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _published_facts(self, published, proof, manifest_bytes, root, producer):
        stream = self._broker._diagnostic_stream_for_published(published)
        self._broker._reload_stream(stream)
        audit = self._broker._receipt_audit(published)
        _hs_refuse(
            len(audit) == 3
            and [row["event"] for row in audit]
            == ["PRODUCED", "SEALED", "PUBLISHED"],
            "incomplete raw F4 stream",
        )
        final = audit[-1]
        for field in ("root_ref", "root_id", "snapshot_version"):
            _hs_refuse(final[field] == root[field],
                       "raw F4 root identity mismatch")
        for source, field in (
            ("run_identity", "producer_run_identity"),
            ("document_sha256", "producer_document_sha256"),
            ("node", "producer_node"),
            ("output", "producer_output"),
        ):
            _hs_refuse(final[field] == producer[source],
                       "raw F4 producer identity mismatch")
        expected = {
            (root["root_ref"], root["snapshot_version"], name)
            for name, _raw in proof._members
        } | {
            (root["root_ref"], root["snapshot_version"],
             "raw_event_dataset.json")
        }
        actual = {
            key for key in self._broker._storage
            if key[:2] == (root["root_ref"], root["snapshot_version"])
        }
        _hs_refuse(actual == expected,
                   "raw F4 WORM member set mismatch")
        snapshot = self._broker._provider.describe(
            root["root_ref"], root["snapshot_version"],
        )
        manifest = []
        for name, original in (
            *proof._members, ("raw_event_dataset.json", manifest_bytes)
        ):
            raw = self._broker._provider.open_member(snapshot, name)
            _hs_refuse(type(raw) is bytes and raw == original,
                       "raw F4 WORM bytes mismatch")
            manifest.append({
                "relative_path": name,
                "media_type": (
                    "application/json" if name == "raw_event_dataset.json"
                    else "application/x-ndjson"
                ),
                "sha256": _digest(original),
                "bytes": len(original),
            })
        _hs_refuse(
            final["member_manifest_sha256"]
            == _digest(_hs_canonical_bytes(manifest))
            == audit[1]["member_manifest_sha256"],
            "raw F4 member manifest mismatch",
        )
        return {field: final[field] for field in (
            "root_ref", "root_id", "snapshot_version",
            "member_manifest_sha256", "producer_run_identity",
            "producer_document_sha256", "producer_node", "producer_output",
        )}

    @staticmethod
    def _sign(payload, self_field):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        key_id = "data-publisher/root-publication-g1-g2"
        preimage = _hs_canonical_bytes(payload)
        seed = bytes.fromhex(_digest(
            ("p4-fixed-test-key/" + key_id).encode("ascii")
        ))
        signature = Ed25519PrivateKey.from_private_bytes(seed).sign(
            preimage
        ).hex()
        _p4_verify_ed25519(
            _P4_LOCAL_PUBLIC_KEYS[key_id], preimage, signature,
        )
        return _hs_canonical_bytes({
            **payload, self_field: _digest(preimage),
            "signature": signature,
        })

    @staticmethod
    def _check_signed(raw, payload, self_field):
        value = _hs_parse_canonical(raw)
        _hs_refuse(
            type(value) is dict
            and set(value) == set(payload) | {self_field, "signature"}
            and {key: item for key, item in value.items()
                 if key not in (self_field, "signature")} == payload
            and value[self_field] == _digest(_hs_canonical_bytes(payload)),
            "raw publication signer changed committed fields",
        )
        _p4_verify_ed25519(
            _P4_LOCAL_PUBLIC_KEYS["data-publisher/root-publication-g1-g2"],
            _hs_canonical_bytes(payload), value["signature"],
        )
        return value

    def _issue_receipt(self, signed, roster, proof, published,
                       manifest_bytes, root, producer):
        reserve = self._reserve
        connection = reserve._connection
        reserve._check()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            authorization = self._check_row(
                signed, roster, proof, "SESSION_ENDED",
                connection, snapshot, revoked, now,
            )
            _hs_refuse(not {
                "data-publisher", "data-publisher/root-publication-g1-g2",
            } & revoked, "raw publication signer revoked")
            facts = self._published_facts(
                published, proof, manifest_bytes, root, producer,
            )
            auth_sha = _digest(signed[0])
            refs = [
                {"kind": "dataset-capture-authorization",
                 "role": "security-data",
                 "schema": authorization["schema_version"],
                 "sha256": auth_sha},
                {"kind": "dataset-capture-grant", "role": "G1",
                 "schema": "dskit.dataset-capture-grant/v1",
                 "sha256": _digest(signed[1])},
                {"kind": "dataset-capture-grant", "role": "G2",
                 "schema": "dskit.dataset-capture-grant/v1",
                 "sha256": _digest(signed[2])},
            ]
            refs.sort(key=lambda ref: tuple(
                ref[key] for key in ("kind", "role", "schema", "sha256")
            ))
            suffix = {
                "issuer_role": "data-publisher",
                "key_usage": "root-publication-g1-g2",
                "signature_alg": "Ed25519",
                "issued_at_ms": now,
                "not_before_ms": now,
                "expires_at_ms": authorization["expires_at_ms"],
                "revocation_snapshot_sha256": snapshot,
                "key": {
                    "key_id": "data-publisher/root-publication-g1-g2",
                    "key_version": 1,
                },
            }
            basis_payload = {
                "schema": "dskit.issuance-basis/v1",
                "kind": "root-publication",
                "study_id": "synthetic-study",
                "refs": refs,
                **suffix,
            }
            basis_sha = _digest(_hs_canonical_bytes(basis_payload))
            receipt_payload = {
                "schema": "dskit.root-publication-receipt/v1",
                "kind": "dataset-capture",
                "capture_kind": "raw-event-dataset",
                "publication_authorization_ref": {
                    "kind": "dataset-capture",
                    "dataset_capture_authorization_sha256": auth_sha,
                },
                **facts,
                "issuance_basis_sha256": basis_sha,
                **suffix,
            }
            key = (
                receipt_payload["schema"],
                _hs_canonical_bytes(
                    receipt_payload["publication_authorization_ref"]
                ),
                *(receipt_payload[field] for field in (
                    "producer_run_identity", "producer_document_sha256",
                    "producer_node", "producer_output", "root_ref",
                    "root_id", "snapshot_version",
                )),
            )
            _hs_refuse(
                key not in self._roster_publisher._outer_receipts,
                "raw outer receipt WORM conflict",
            )
            final_now = reserve._now()
            _hs_refuse(
                authorization["not_before_ms"] <= final_now
                < authorization["expires_at_ms"],
                "raw receipt authority expired before commit",
            )
            result = connection.execute(
                "UPDATE reserve_uses SET state='RECEIPT_ISSUED' "
                "WHERE kind='raw-dataset' AND signed_id=? "
                "AND state='SESSION_ENDED'",
                (authorization["authorization_id"],),
            )
            _hs_refuse(result.rowcount == 1,
                       "raw receipt admission lost state")
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("raw-dataset", authorization["authorization_id"],
                 "SESSION_ENDED", "RECEIPT_ISSUED", generation),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        basis_bytes = self._sign(
            basis_payload, "issuance_basis_sha256",
        )
        basis = self._check_signed(
            basis_bytes, basis_payload, "issuance_basis_sha256",
        )
        _hs_refuse(
            basis["issuance_basis_sha256"] == basis_sha,
            "raw issuance basis digest mismatch",
        )
        receipt_bytes = self._sign(
            receipt_payload, "root_publication_receipt_sha256",
        )
        self._check_signed(
            receipt_bytes, receipt_payload,
            "root_publication_receipt_sha256",
        )
        stored = self._roster_publisher._outer_receipts.setdefault(
            key, receipt_bytes,
        )
        _hs_refuse(stored == receipt_bytes,
                   "raw outer receipt WORM conflict")
        return basis_bytes, receipt_bytes

    def _quarantine(self, proof):
        self._closed = True
        reserve = self._reserve
        connection = reserve._connection
        try:
            reserve._check()
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, _revoked, _snapshot = reserve._snapshot()
            intent = _hs_parse_canonical(proof._intent)
            signed_id = intent["authorization_id"]
            row = connection.execute(
                "SELECT state,intent_sha256 FROM reserve_uses "
                "WHERE kind='raw-dataset' AND signed_id=?",
                (signed_id,),
            ).fetchone()
            if row is not None and row[0] != "QUARANTINED":
                _hs_refuse(row[1] == _digest(proof._intent),
                           "raw quarantine intent mismatch")
                connection.execute(
                    "UPDATE reserve_uses SET state='QUARANTINED' "
                    "WHERE kind='raw-dataset' AND signed_id=?",
                    (signed_id,),
                )
                connection.execute(
                    "INSERT INTO reserve_audit "
                    "(kind,signed_id,old_state,new_state,generation) "
                    "VALUES (?,?,?,?,?)",
                    ("raw-dataset", signed_id,
                     row[0], "QUARANTINED", generation),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")

    def _publish_common(self, proof, signed, roster):
        object.__setattr__(proof, "_used", True)
        try:
            manifest_bytes = self._manifest(proof, signed[0])
            root, producer = self._identity(signed[0])
            self._advance(signed, roster, proof, "SESSION_STARTED")
            session = self._broker.start_producer_session(
                run_identity=producer["run_identity"],
                process_measurement_sha256=_digest(
                    b"dskit.synthetic-raw-process/v1"
                ),
                runtime_sha256=_digest(
                    b"dskit.synthetic-raw-runtime/v1"
                ),
                plan_sha256=_digest(
                    b"dskit.synthetic-raw-plan/v1"
                ),
            )
            _hs_refuse(session._run_identity == producer["run_identity"],
                       "raw F4 run identity mismatch")
            self._advance(signed, roster, proof, "PRODUCED")
            members = [{
                "relative_path": name,
                "media_type": "application/x-ndjson",
                "bytes": raw,
                "file_type": "regular",
                "link_count": 1,
            } for name, raw in proof._members]
            members.append({
                "relative_path": "raw_event_dataset.json",
                "media_type": "application/json",
                "bytes": manifest_bytes,
                "file_type": "regular",
                "link_count": 1,
            })
            auth_sha = _digest(signed[0])
            prepared = self._broker.produce(
                session,
                producer={key: producer[key] for key in (
                    "run_identity", "document_sha256", "node", "output",
                )},
                root=root, purpose=producer["purpose"],
                expected_members=tuple(
                    member["relative_path"] for member in members
                ),
                members=members,
                output_member="raw_event_dataset.json",
                completed=True, planned=True,
                transition_nonce="raw-" + auth_sha + "-produced",
            )
            self._advance(signed, roster, proof, "SEALED")
            sealed = self._broker.seal(
                session, prepared,
                transition_nonce="raw-" + auth_sha + "-sealed",
            )
            self._advance(signed, roster, proof, "PUBLISHED")
            published = self._broker.publish(
                session, sealed,
                transition_nonce="raw-" + auth_sha + "-published",
            )
            self._published_facts(
                published, proof, manifest_bytes, root, producer,
            )
            self._advance(signed, roster, proof, "SESSION_ENDED")
            self._broker.end_session(session)
            basis_bytes, receipt_bytes = self._issue_receipt(
                signed, roster, proof, published, manifest_bytes,
                root, producer,
            )
            self._retained = (
                proof, published, session, signed, roster,
                manifest_bytes, basis_bytes, receipt_bytes,
            )
            self._closed = True
            return manifest_bytes, basis_bytes, receipt_bytes
        except Exception:
            self._quarantine(proof)
            raise

    def publish(self, proof, authorization_bytes, g1, g2, attestation,
                bootstrap, bg1, bg2, roster_basis, roster_receipt):
        """Publish one raw root or terminalize its signed ID."""
        _hs_refuse(not self._closed
                   and type(proof) is VerifiedSyntheticDatasetFixture
                   and proof._owner is self._preflight
                   and self._preflight._publisher is self._roster_publisher
                   and not proof._used,
                   "unused own raw fixture proof required")
        fixture_facts = _SYNTHETIC_RAW_FIXTURE_FACTS.get(proof)
        _hs_refuse(
            type(fixture_facts) is tuple
            and len(fixture_facts) == 4
            and fixture_facts[0]() is proof
            and fixture_facts[1] is self._preflight,
            "raw fixture proof facts changed",
        )
        proof_event_schema = fixture_facts[2]
        if proof_event_schema == DATASET_AUTHORIZATION_EVENT_SCHEMAS[
            "dskit.dataset-capture-authorization/v2"
        ]:
            _hs_refuse(
                proof._event_schema == proof_event_schema
                and type(proof._intent) is bytes
                and _digest(proof._intent) == fixture_facts[3],
                "raw fixture proof facts changed",
            )
            intent = _hs_parse_canonical(proof._intent)
            bound_authorities = (
                ("dataset_authorization_sha256", authorization_bytes),
                ("dataset_g1_sha256", g1),
                ("dataset_g2_sha256", g2),
                ("fixture_attestation_sha256", attestation),
                ("bootstrap_authorization_sha256", bootstrap),
                ("bootstrap_g1_sha256", bg1),
                ("bootstrap_g2_sha256", bg2),
                ("roster_basis_sha256", roster_basis),
                ("roster_receipt_sha256", roster_receipt),
            )
            _hs_refuse(
                all(
                    type(raw) is bytes and intent.get(name) == _digest(raw)
                    for name, raw in bound_authorities
                ),
                "raw publisher proof authority changed",
            )
            authorization = _hs_parse_canonical(authorization_bytes)
            bootstrap_value = _hs_parse_canonical(bootstrap)
            v1_event_schema = DATASET_AUTHORIZATION_EVENT_SCHEMAS[
                "dskit.dataset-capture-authorization/v1"
            ]
            _hs_refuse(
                authorization.get("schema_version")
                == "dskit.dataset-capture-authorization/v1"
                and bootstrap_value.get("schema_version")
                == "dskit.roster-bootstrap-authorization/v1"
                and authorization.get("event_schema") == v1_event_schema
                and bootstrap_value.get("event_schema") == v1_event_schema,
                "raw publisher is v1-only",
            )
        else:
            def has_schema(raw, schema):
                try:
                    value = _hs_parse_canonical(raw)
                except Exception:
                    return False
                return type(value) is dict and value.get("schema_version") == schema

            _hs_refuse(
                not has_schema(
                    authorization_bytes,
                    "dskit.dataset-capture-authorization/v2",
                )
                and not has_schema(
                    bootstrap,
                    "dskit.roster-bootstrap-authorization/v2",
                ),
                "raw publisher is v1-only",
            )
        return self._publish_common(
            proof,
            (authorization_bytes, g1, g2, attestation),
            (bootstrap, bg1, bg2, roster_basis, roster_receipt),
        )

    def proof(self):
        """Return only a live read-only proof of retained raw publication."""
        _hs_refuse(self._closed and self._retained is not None,
                   "successful retained raw publication required")
        return NonAuthorizingRawRootProof(_MAKE, self)


class NonAuthorizingRawRootProof:
    """Fresh read-only proof of one retained synthetic raw root and receipt."""

    __slots__ = ("_publisher", "_locked")

    def __init_subclass__(cls, **kwargs):
        """Forbid subtypes that could replace the fixed proof checks."""
        raise TypeError("the fixed raw-root proof is final")

    def __init__(self, token, publisher):
        if token is not _MAKE or type(publisher) is not _SyntheticRawPublisher:
            raise TypeError("broker-owned raw-root proof required")
        object.__setattr__(self, "_publisher", publisher)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Keep the construction-owned publisher binding frozen."""
        if getattr(self, "_locked", False):
            raise AttributeError("raw-root proof is frozen")
        object.__setattr__(self, name, value)

    @staticmethod
    def _audit(connection, signed_id, generation):
        rows = connection.execute(
            "SELECT old_state,new_state,generation FROM reserve_audit "
            "WHERE kind='raw-dataset' AND signed_id=? ORDER BY seq",
            (signed_id,),
        ).fetchall()
        states = (
            "RESERVED", "RAW_READ_STARTED", "SESSION_STARTED", "PRODUCED",
            "SEALED", "PUBLISHED", "SESSION_ENDED", "RECEIPT_ISSUED",
        )
        _hs_refuse(
            len(rows) == len(states)
            and all(
                old == (states[index - 1] if index else None)
                and new == state
                and type(saved_generation) is int
                and saved_generation == generation
                for index, ((old, new, saved_generation), state)
                in enumerate(zip(rows, states, strict=True))
            ),
            "complete raw reservation audit required",
        )

    def verify(self, authorization_bytes, g1, g2, attestation,
               bootstrap, bg1, bg2, roster_basis, roster_receipt,
               manifest_bytes, basis_bytes, receipt_bytes,
               _under_writer_lock=False):
        """Return checked identities, never a publication or capture permit."""
        values = (
            authorization_bytes, g1, g2, attestation,
            bootstrap, bg1, bg2, roster_basis, roster_receipt,
            manifest_bytes, basis_bytes, receipt_bytes,
        )
        _hs_refuse(all(type(raw) is bytes for raw in values),
                   "exact raw proof bytes required")
        publisher = self._publisher
        _hs_refuse(
            type(publisher) is _SyntheticRawPublisher
            and publisher._closed
            and type(publisher._preflight) is _SyntheticRawPreflight
            and publisher._preflight._publisher
            is publisher._roster_publisher
            and publisher._retained is not None,
            "live retained raw publisher required",
        )
        proof, published, session, signed, roster, original_manifest, original_basis, original_receipt = (
            publisher._retained
        )
        _hs_refuse(
            type(proof) is VerifiedSyntheticDatasetFixture
            and proof._used
            and proof._owner is publisher._preflight
            and values == (*signed, *roster, original_manifest,
                           original_basis, original_receipt),
            "retained raw proof originals mismatch",
        )
        _hs_refuse(
            type(session) is LaunchSession and session._ended is True,
            "retained ended raw F4 session required",
        )
        reserve = publisher._reserve
        reserve._check()
        connection = reserve._connection
        signed_id = _hs_parse_canonical(authorization_bytes)["authorization_id"]
        try:
            if _under_writer_lock:
                _hs_refuse(connection.in_transaction,
                           "raw writer transaction required")
            else:
                connection.execute("BEGIN")
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            authorization = publisher._check_row(
                signed, roster, proof, "RECEIPT_ISSUED",
                connection, snapshot, revoked, now,
            )
            self._audit(connection, signed_id, generation)
            _hs_refuse(not {
                "data-publisher", "data-publisher/root-publication-g1-g2",
            } & revoked, "raw proof signer revoked")
            if not _under_writer_lock:
                connection.execute("COMMIT")
        except Exception:
            if not _under_writer_lock and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        expected_manifest = publisher._manifest(proof, authorization_bytes)
        _hs_refuse(manifest_bytes == expected_manifest,
                   "raw proof manifest mismatch")
        seen = set()
        parsed_events = []
        ordered = _hs_parse_canonical(proof._intent)["ordered_members"]
        for (_name, raw), member in zip(
            proof._members, ordered, strict=True,
        ):
            parsed_events.extend(
                _SyntheticRawPreflight._parse_member(
                    raw, member, authorization["scope"],
                    authorization["event_schema"], seen,
                )
            )
        _hs_refuse(
            len(parsed_events) == proof.event_count
            and tuple(parsed_events) == proof._events,
            "raw proof event binding mismatch",
        )
        root, producer = publisher._identity(authorization_bytes)
        facts = publisher._published_facts(
            published, proof, manifest_bytes, root, producer,
        )
        basis = _hs_parse_canonical(basis_bytes)
        _hs_refuse(
            type(basis) is dict
            and type(basis.get("issued_at_ms")) is int
            and authorization["issued_at_ms"] <= basis["issued_at_ms"]
            <= now < authorization["expires_at_ms"],
            "raw proof basis issuance refused",
        )
        refs = [
            {"kind": "dataset-capture-authorization",
             "role": "security-data",
             "schema": authorization["schema_version"],
             "sha256": _digest(authorization_bytes)},
            {"kind": "dataset-capture-grant", "role": "G1",
             "schema": "dskit.dataset-capture-grant/v1",
             "sha256": _digest(g1)},
            {"kind": "dataset-capture-grant", "role": "G2",
             "schema": "dskit.dataset-capture-grant/v1",
             "sha256": _digest(g2)},
        ]
        refs.sort(key=lambda ref: tuple(
            ref[field] for field in ("kind", "role", "schema", "sha256")
        ))
        suffix = {
            "issuer_role": "data-publisher",
            "key_usage": "root-publication-g1-g2",
            "signature_alg": "Ed25519",
            "issued_at_ms": basis["issued_at_ms"],
            "not_before_ms": basis["issued_at_ms"],
            "expires_at_ms": authorization["expires_at_ms"],
            "revocation_snapshot_sha256": snapshot,
            "key": {
                "key_id": "data-publisher/root-publication-g1-g2",
                "key_version": 1,
            },
        }
        expected_basis = {
            "schema": "dskit.issuance-basis/v1",
            "kind": "root-publication",
            "study_id": "synthetic-study",
            "refs": refs,
            **suffix,
        }
        basis = publisher._check_signed(
            basis_bytes, expected_basis, "issuance_basis_sha256",
        )
        expected_receipt = {
            "schema": "dskit.root-publication-receipt/v1",
            "kind": "dataset-capture",
            "capture_kind": "raw-event-dataset",
            "publication_authorization_ref": {
                "kind": "dataset-capture",
                "dataset_capture_authorization_sha256":
                    _digest(authorization_bytes),
            },
            **facts,
            "issuance_basis_sha256": basis["issuance_basis_sha256"],
            **suffix,
        }
        receipt = publisher._check_signed(
            receipt_bytes, expected_receipt,
            "root_publication_receipt_sha256",
        )
        key = (
            receipt["schema"],
            _hs_canonical_bytes(receipt["publication_authorization_ref"]),
            *(receipt[field] for field in (
                "producer_run_identity", "producer_document_sha256",
                "producer_node", "producer_output", "root_ref",
                "root_id", "snapshot_version",
            )),
        )
        _hs_refuse(
            publisher._roster_publisher._outer_receipts.get(key)
            == receipt_bytes
            and sum(
                item == receipt_bytes
                for item in publisher._roster_publisher._outer_receipts.values()
            ) == 1,
            "persisted raw receipt mismatch",
        )
        reserve._check()
        try:
            if not _under_writer_lock:
                connection.execute("BEGIN")
            final_generation, final_revoked, final_snapshot = (
                reserve._snapshot()
            )
            final_now = reserve._now()
            _hs_refuse(
                final_generation == generation
                and final_snapshot == snapshot
                and final_now == now
                and not {
                    authorization["authorization_id"], "G1", "G2",
                    "G2-fixture", "data-publisher",
                    "data-publisher/root-publication-g1-g2",
                } & final_revoked,
                "raw proof freshness changed",
            )
            row = connection.execute(
                "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                "WHERE kind='raw-dataset' AND signed_id=?",
                (signed_id,),
            ).fetchone()
            _hs_refuse(
                row == (_digest(authorization_bytes), _digest(g1),
                        _digest(g2), snapshot, _digest(proof._intent),
                        "RECEIPT_ISSUED"),
                "raw proof final reservation changed",
            )
            self._audit(connection, signed_id, final_generation)
            if not _under_writer_lock:
                connection.execute("COMMIT")
        except Exception:
            if not _under_writer_lock and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        raw_root_sha = _digest(_hs_canonical_bytes({
            field: receipt[field] for field in (
                "root_ref", "root_id", "snapshot_version",
                "member_manifest_sha256",
            )
        }))
        return MappingProxyType({
            "raw_manifest_sha256": _digest(manifest_bytes),
            "raw_root_sha256": raw_root_sha,
            "raw_publication_receipt_sha256":
                receipt["root_publication_receipt_sha256"],
            "ordered_member_digests": tuple(
                MappingProxyType(dict(member)) for member in
                _hs_parse_canonical(manifest_bytes)["ordered_member_digests"]
            ),
            "event_count": len(parsed_events),
            "checked_at_ms": final_now,
            "authorizing": False,
            "deployment_eligible": False,
        })


def _build_synthetic_v2_raw_publication():
    """Install the environment-bound v2 raw route with private authority."""
    publisher_type = _SyntheticRawPublisher
    preflight_type = _SyntheticRawPreflight
    proof_type = NonAuthorizingRawRootProof
    fixture_type = VerifiedSyntheticDatasetFixture
    environment_type = _SyntheticEnvironmentIdentity
    parser = _hs_parse_canonical
    digest = _digest
    refuse = _hs_refuse
    environment_checker = _require_synthetic_tzdata
    schema_table = DATASET_AUTHORIZATION_EVENT_SCHEMAS
    fixture_facts_map = _SYNTHETIC_RAW_FIXTURE_FACTS
    raw_writer = publisher_type._publish_common
    writer_helper_descriptors = tuple(
        (name, publisher_type.__dict__[name])
        for name in (
            "_manifest", "_identity", "_check_row", "_advance",
            "_published_facts", "_sign", "_check_signed",
            "_issue_receipt", "_quarantine",
        )
    )
    publisher_authority_descriptors = tuple(
        (name, publisher_type.__dict__[name])
        for name in (
            "_preflight", "_roster_publisher", "_reserve", "_broker",
            "_closed", "_retained", "__weakref__",
        )
    )
    publisher_comparison_descriptors = tuple(
        (
            name,
            name in publisher_type.__dict__,
            publisher_type.__dict__.get(name),
        )
        for name in ("__hash__", "__eq__")
    )
    fixture_comparison_descriptors = tuple(
        (
            name,
            name in fixture_type.__dict__,
            fixture_type.__dict__.get(name),
        )
        for name in ("__hash__", "__eq__")
    )
    fixture_authority_descriptors = tuple(
        (name, fixture_type.__dict__[name])
        for name in (
            "_owner", "_intent", "_members", "_event_schema", "_used",
        )
    )
    preflight_authority_descriptors = tuple(
        (name, preflight_type.__dict__[name])
        for name in ("_publisher", "_derive_intent")
    )
    original_verify = proof_type.verify
    object_getattribute = object.__getattribute__
    weakref_factory = weakref_ref
    proof_publisher_descriptor = proof_type.__dict__["_publisher"]
    publisher_retained_descriptor = publisher_type.__dict__["_retained"]
    fixture_schema_descriptor = fixture_type.__dict__["_event_schema"]
    provisional = object()
    committed = object()
    failed = object()
    records = WeakKeyDictionary()
    anchors = WeakKeyDictionary()
    record_identities = set()
    writer_invoked_identities = set()
    binding_seals = {}
    v2_authorization_schema = "dskit.dataset-capture-authorization/v2"
    v2_roster_schema = "dskit.roster-bootstrap-authorization/v2"
    v2_event_schema = schema_table[v2_authorization_schema]
    v1_event_schema = schema_table[
        "dskit.dataset-capture-authorization/v1"
    ]

    def has_exact_seal(record, environment_identity):
        seal = binding_seals.get(id(record))
        return (
            type(seal) is tuple
            and len(seal) == 4
            and seal[0] is record
            and seal[1] is environment_identity
            and record[1] is environment_identity
            and seal[2] is record[0]
            and seal[3] is record[2]
        )

    def has_exact_writer_helpers():
        for name, descriptor in writer_helper_descriptors:
            if publisher_type.__dict__.get(name) is not descriptor:
                return False
        return True

    def has_exact_authority_descriptors():
        for owner, descriptors in (
            (publisher_type, publisher_comparison_descriptors),
            (fixture_type, fixture_comparison_descriptors),
        ):
            for name, present, descriptor in descriptors:
                if (
                    (name in owner.__dict__) is not present
                    or owner.__dict__.get(name) is not descriptor
                ):
                    return False
        for owner, descriptors in (
            (publisher_type, publisher_authority_descriptors),
            (fixture_type, fixture_authority_descriptors),
            (preflight_type, preflight_authority_descriptors),
        ):
            for name, descriptor in descriptors:
                if owner.__dict__.get(name) is not descriptor:
                    return False
        return True

    def v2_writer(self, proof, signed, roster):
        refuse(
            object_getattribute(proof, "_event_schema")
            == v2_event_schema
            and parser(signed[0]).get("event_schema") == v2_event_schema
            and parser(roster[0]).get("event_schema") == v2_event_schema,
            "raw common writer schema gate refused",
        )
        record = records.get(self)
        refuse(
            type(record) is list
            and len(record) == 3
            and anchors.get(self) is record
            and id(record) in record_identities
            and has_exact_seal(record, record[1])
            and record[0]() is self
            and type(record[1]) is environment_type
            and record[2] is provisional,
            "provisional synthetic v2 raw binding required",
        )
        try:
            writer_invoked_identities.add(id(record))
            return raw_writer(self, proof, signed, roster)
        except BaseException:
            seal = binding_seals.get(id(record))
            sealed_environment = (
                seal[1]
                if type(seal) is tuple
                and len(seal) == 4
                and seal[0] is record
                else None
            )
            sealed_reference = (
                seal[2]
                if type(seal) is tuple
                and len(seal) == 4
                and seal[0] is record
                else None
            )
            record[2] = failed
            binding_seals[id(record)] = (
                record, sealed_environment, sealed_reference, failed,
            )
            raise

    def require_dispatch(*, verifying=False):
        refuse(
            _require_synthetic_tzdata is environment_checker,
            "synthetic environment dispatch changed",
        )
        refuse(
            _SyntheticRawPublisher is publisher_type
            and _SyntheticRawPreflight is preflight_type
            and NonAuthorizingRawRootProof is proof_type
            and VerifiedSyntheticDatasetFixture is fixture_type
            and _SyntheticEnvironmentIdentity is environment_type
            and _hs_parse_canonical is parser
            and _digest is digest
            and _hs_refuse is refuse
            and weakref_ref is weakref_factory
            and DATASET_AUTHORIZATION_EVENT_SCHEMAS is schema_table
            and _SYNTHETIC_RAW_FIXTURE_FACTS is fixture_facts_map
            and has_exact_writer_helpers()
            and has_exact_authority_descriptors()
            and "_publish_common" not in publisher_type.__dict__
            and publisher_type.publish is publish
            and publisher_type.publish_v2 is publish_v2
            and proof_type.__dict__.get("_publisher")
            is proof_publisher_descriptor
            and publisher_type.__dict__.get("_retained")
            is publisher_retained_descriptor
            and fixture_type.__dict__.get("_event_schema")
            is fixture_schema_descriptor
            and (not verifying or proof_type.verify is verify),
            "synthetic v2 raw publication dispatch changed",
        )

    def publish(self, proof, authorization_bytes, g1, g2, attestation,
                bootstrap, bg1, bg2, roster_basis, roster_receipt):
        """Publish one legacy v1 raw root through the captured writer."""
        refuse(
            type(self) is publisher_type
            and not object_getattribute(self, "_closed")
            and type(proof) is fixture_type
            and object_getattribute(proof, "_owner")
            is object_getattribute(self, "_preflight")
            and object_getattribute(
                object_getattribute(self, "_preflight"), "_publisher"
            ) is object_getattribute(self, "_roster_publisher")
            and not object_getattribute(proof, "_used"),
            "unused own raw fixture proof required",
        )
        fixture_facts = fixture_facts_map.get(proof)
        refuse(
            type(fixture_facts) is tuple
            and len(fixture_facts) == 4
            and fixture_facts[0]() is proof
            and fixture_facts[1] is object_getattribute(self, "_preflight"),
            "raw fixture proof facts changed",
        )
        proof_event_schema = fixture_facts[2]
        if proof_event_schema == v2_event_schema:
            refuse(
                object_getattribute(proof, "_event_schema")
                == proof_event_schema
                and type(object_getattribute(proof, "_intent")) is bytes
                and digest(object_getattribute(proof, "_intent"))
                == fixture_facts[3],
                "raw fixture proof facts changed",
            )
            intent = parser(object_getattribute(proof, "_intent"))
            bound_authorities = (
                ("dataset_authorization_sha256", authorization_bytes),
                ("dataset_g1_sha256", g1),
                ("dataset_g2_sha256", g2),
                ("fixture_attestation_sha256", attestation),
                ("bootstrap_authorization_sha256", bootstrap),
                ("bootstrap_g1_sha256", bg1),
                ("bootstrap_g2_sha256", bg2),
                ("roster_basis_sha256", roster_basis),
                ("roster_receipt_sha256", roster_receipt),
            )
            refuse(
                all(
                    type(raw) is bytes
                    and intent.get(name) == digest(raw)
                    for name, raw in bound_authorities
                ),
                "raw publisher proof authority changed",
            )
            authorization = parser(authorization_bytes)
            bootstrap_value = parser(bootstrap)
            refuse(
                authorization.get("schema_version")
                == "dskit.dataset-capture-authorization/v1"
                and bootstrap_value.get("schema_version")
                == "dskit.roster-bootstrap-authorization/v1"
                and authorization.get("event_schema") == v1_event_schema
                and bootstrap_value.get("event_schema") == v1_event_schema,
                "raw publisher is v1-only",
            )
        else:
            def has_schema(raw, schema):
                try:
                    value = parser(raw)
                except Exception:
                    return False
                return (
                    type(value) is dict
                    and value.get("schema_version") == schema
                )

            refuse(
                not has_schema(
                    authorization_bytes, v2_authorization_schema,
                )
                and not has_schema(bootstrap, v2_roster_schema),
                "raw publisher is v1-only",
            )
        return raw_writer(
            self,
            proof,
            (authorization_bytes, g1, g2, attestation),
            (bootstrap, bg1, bg2, roster_basis, roster_receipt),
        )

    def publish_v2(self, proof, environment_identity, authorization_bytes,
                   g1, g2, attestation, bootstrap, bg1, bg2, roster_basis,
                   roster_receipt, /):
        """Publish one environment-bound synthetic raw-event/v2 root."""
        require_dispatch()
        refuse(
            type(self) is publisher_type
            and not object_getattribute(self, "_closed")
            and type(proof) is fixture_type
            and object_getattribute(proof, "_owner")
            is object_getattribute(self, "_preflight")
            and object_getattribute(
                object_getattribute(self, "_preflight"), "_publisher"
            ) is object_getattribute(self, "_roster_publisher")
            and not object_getattribute(proof, "_used"),
            "unused own raw fixture proof required",
        )
        fixture_facts = fixture_facts_map.get(proof)
        refuse(
            type(fixture_facts) is tuple
            and len(fixture_facts) == 4
            and fixture_facts[0]() is proof
            and fixture_facts[1] is object_getattribute(self, "_preflight")
            and fixture_facts[2] == v2_event_schema
            and object_getattribute(proof, "_event_schema") == v2_event_schema
            and type(object_getattribute(proof, "_intent")) is bytes
            and digest(object_getattribute(proof, "_intent"))
            == fixture_facts[3],
            "exact unused v2 raw fixture proof required",
        )
        signed = (authorization_bytes, g1, g2, attestation)
        roster = (bootstrap, bg1, bg2, roster_basis, roster_receipt)
        intent = parser(object_getattribute(proof, "_intent"))
        names = (
            "dataset_authorization_sha256", "dataset_g1_sha256",
            "dataset_g2_sha256", "fixture_attestation_sha256",
            "bootstrap_authorization_sha256", "bootstrap_g1_sha256",
            "bootstrap_g2_sha256", "roster_basis_sha256",
            "roster_receipt_sha256",
        )
        refuse(
            all(
                type(raw) is bytes and intent.get(name) == digest(raw)
                for name, raw in zip(
                    names, (*signed, *roster), strict=True
                )
            ),
            "raw publisher proof authority changed",
        )
        authorization = parser(authorization_bytes)
        bootstrap_value = parser(bootstrap)
        refuse(
            type(authorization) is dict
            and type(bootstrap_value) is dict
            and authorization.get("schema_version")
            == v2_authorization_schema
            and bootstrap_value.get("schema_version") == v2_roster_schema
            and authorization.get("event_schema") == v2_event_schema
            and bootstrap_value.get("event_schema") == v2_event_schema
            and authorization.get("scope") == bootstrap_value.get("scope"),
            "exact v2 raw publication authorities required",
        )
        scope = authorization["scope"]
        require_dispatch()
        try:
            environment_checker(
                environment_identity, scope["tzdata_version_sha256"]
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "synthetic environment identity refused"
            ) from exc
        require_dispatch()
        refuse(
            type(environment_identity) is environment_type
            and records.get(self) is None
            and anchors.get(self) is None,
            "fresh synthetic v2 raw binding required",
        )
        record = [None, environment_identity, provisional]
        record_identity = id(record)

        def forget_record(_publisher_ref, identity=record_identity):
            record_identities.discard(identity)
            writer_invoked_identities.discard(identity)
            binding_seals.pop(identity, None)

        publisher_reference = weakref_factory(self, forget_record)
        record[0] = publisher_reference
        try:
            records[self] = record
            anchors[self] = record
            record_identities.add(record_identity)
            binding_seals[record_identity] = (
                record, environment_identity, publisher_reference,
                provisional,
            )
        except BaseException:
            records.pop(self, None)
            anchors.pop(self, None)
            record_identities.discard(record_identity)
            binding_seals.pop(record_identity, None)
            raise
        try:
            require_dispatch()
            refuse(
                records.get(self) is record
                and anchors.get(self) is record
                and id(record) in record_identities
                and has_exact_seal(record, environment_identity)
                and record[0]() is self
                and record[1] is environment_identity
                and record[2] is provisional,
                "synthetic v2 raw binding changed",
            )
            result = v2_writer(self, proof, signed, roster)
            require_dispatch()
            refuse(
                records.get(self) is record
                and anchors.get(self) is record
                and id(record) in record_identities
                and has_exact_seal(record, environment_identity)
                and record[0]() is self
                and record[1] is environment_identity
                and record[2] is provisional,
                "synthetic v2 raw binding changed",
            )
            record[2] = committed
            binding_seals[record_identity] = (
                record, environment_identity, publisher_reference,
                committed,
            )
            require_dispatch()
            refuse(
                records.get(self) is record
                and anchors.get(self) is record
                and id(record) in record_identities
                and has_exact_seal(record, environment_identity)
                and record[2] is committed,
                "synthetic v2 raw binding promotion failed",
            )
            return result
        except BaseException:
            if (
                id(record) in writer_invoked_identities
                or record[2] is failed
            ):
                record[2] = failed
                binding_seals[record_identity] = (
                    record, environment_identity, publisher_reference,
                    failed,
                )
            else:
                records.pop(self, None)
                anchors.pop(self, None)
                record_identities.discard(record_identity)
                writer_invoked_identities.discard(record_identity)
                binding_seals.pop(record_identity, None)
            raise

    def verify(self, authorization_bytes, g1, g2, attestation,
               bootstrap, bg1, bg2, roster_basis, roster_receipt,
               manifest_bytes, basis_bytes, receipt_bytes,
               _under_writer_lock=False):
        try:
            publisher = proof_publisher_descriptor.__get__(self, proof_type)
            if type(publisher) is not publisher_type:
                raise TypeError
            retained = publisher_retained_descriptor.__get__(
                publisher, publisher_type
            )
            if type(retained) is not tuple or len(retained) != 8:
                raise TypeError
            fixture = retained[0]
            if type(fixture) is not fixture_type:
                raise TypeError
            event_schema = fixture_schema_descriptor.__get__(
                fixture, fixture_type
            )
        except BaseException:
            return original_verify(
                self, authorization_bytes, g1, g2, attestation,
                bootstrap, bg1, bg2, roster_basis, roster_receipt,
                manifest_bytes, basis_bytes, receipt_bytes,
                _under_writer_lock=_under_writer_lock,
            )
        if type(event_schema) is not str or event_schema != v2_event_schema:
            return original_verify(
                self, authorization_bytes, g1, g2, attestation,
                bootstrap, bg1, bg2, roster_basis, roster_receipt,
                manifest_bytes, basis_bytes, receipt_bytes,
                _under_writer_lock=_under_writer_lock,
            )
        require_dispatch(verifying=True)
        record = records.get(publisher)
        refuse(
            type(record) is list
            and len(record) == 3
            and anchors.get(publisher) is record
            and id(record) in record_identities
            and has_exact_seal(record, record[1])
            and record[0]() is publisher
            and type(record[1]) is environment_type
            and record[2] is committed,
            "committed synthetic v2 raw binding required",
        )
        retained_signed = retained[3]
        refuse(
            type(retained_signed) is tuple
            and len(retained_signed) == 4
            and type(retained_signed[0]) is bytes,
            "retained v2 raw authority required",
        )
        authorization = parser(retained_signed[0])
        refuse(
            type(authorization) is dict
            and authorization.get("schema_version")
            == v2_authorization_schema
            and authorization.get("event_schema") == v2_event_schema
            and type(authorization.get("scope")) is dict
            and type(
                authorization["scope"].get("tzdata_version_sha256")
            ) is str,
            "retained v2 raw authority required",
        )
        require_dispatch(verifying=True)
        try:
            environment_checker(
                record[1],
                authorization["scope"]["tzdata_version_sha256"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "synthetic environment identity refused"
            ) from exc
        require_dispatch(verifying=True)
        return original_verify(
            self, authorization_bytes, g1, g2, attestation,
            bootstrap, bg1, bg2, roster_basis, roster_receipt,
            manifest_bytes, basis_bytes, receipt_bytes,
            _under_writer_lock=_under_writer_lock,
        )

    authorized_publish = publish
    authorized_publish_v2 = publish_v2
    authorized_verify = verify

    def publish(self, proof, authorization_bytes, g1, g2, attestation,
                bootstrap, bg1, bg2, roster_basis, roster_receipt):
        return authorized_publish(
            self, proof, authorization_bytes, g1, g2, attestation,
            bootstrap, bg1, bg2, roster_basis, roster_receipt,
        )

    def publish_v2(self, proof, environment_identity, authorization_bytes,
                   g1, g2, attestation, bootstrap, bg1, bg2, roster_basis,
                   roster_receipt, /):
        return authorized_publish_v2(
            self, proof, environment_identity, authorization_bytes,
            g1, g2, attestation, bootstrap, bg1, bg2, roster_basis,
            roster_receipt,
        )

    def verify(self, authorization_bytes, g1, g2, attestation,
               bootstrap, bg1, bg2, roster_basis, roster_receipt,
               manifest_bytes, basis_bytes, receipt_bytes,
               _under_writer_lock=False):
        return authorized_verify(
            self, authorization_bytes, g1, g2, attestation,
            bootstrap, bg1, bg2, roster_basis, roster_receipt,
            manifest_bytes, basis_bytes, receipt_bytes,
            _under_writer_lock=_under_writer_lock,
        )

    publisher_type.publish = publish
    publisher_type.publish_v2 = publish_v2
    proof_type.verify = verify
    delattr(publisher_type, "_publish_common")


_build_synthetic_v2_raw_publication()
del _build_synthetic_v2_raw_publication


class _SyntheticRootPisIssuer:
    """One-shot, nondeployment two-root signed PIS issuer."""

    __slots__ = ("_publisher", "_closed", "_retained", "_graph")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("the synthetic root-PIS issuer is final")

    def __init__(self, publisher):
        _hs_refuse(type(publisher) is _SyntheticRawPublisher
                   and publisher._closed and publisher._retained is not None,
                   "retained raw publisher required")
        retained = publisher._retained
        _hs_refuse(
            type(retained) is tuple
            and len(retained) == 8
            and type(retained[0]) is VerifiedSyntheticDatasetFixture
            and retained[0]._event_schema
            == DATASET_AUTHORIZATION_EVENT_SCHEMAS[
                "dskit.dataset-capture-authorization/v1"
            ]
            and type(retained[3]) is tuple
            and len(retained[3]) == 4
            and type(retained[4]) is tuple
            and len(retained[4]) == 5
            and _hs_parse_canonical(retained[3][0]).get(
                "schema_version"
            ) == "dskit.dataset-capture-authorization/v1"
            and _hs_parse_canonical(retained[4][0]).get(
                "schema_version"
            ) == "dskit.roster-bootstrap-authorization/v1",
            "root-PIS issuer is v1-only",
        )
        self._publisher = publisher
        self._closed = False
        self._retained = None
        self._graph = None

    @staticmethod
    def _sign(payload, self_field):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        key_id = "data-publisher/published-input-set-g1-g2"
        preimage = _hs_canonical_bytes(payload)
        seed = bytes.fromhex(_digest(
            ("p4-fixed-test-key/" + key_id).encode("ascii")
        ))
        signature = Ed25519PrivateKey.from_private_bytes(seed).sign(
            preimage
        ).hex()
        _p4_verify_ed25519(
            _P4_LOCAL_PUBLIC_KEYS[key_id], preimage, signature,
        )
        return _hs_canonical_bytes({
            **payload, self_field: _digest(preimage),
            "signature": signature,
        })

    @staticmethod
    def _check_signed(raw, payload, self_field):
        value = _hs_parse_canonical(raw)
        _hs_refuse(
            type(value) is dict
            and set(value) == set(payload) | {self_field, "signature"}
            and {key: item for key, item in value.items()
                 if key not in (self_field, "signature")} == payload
            and value[self_field] == _digest(_hs_canonical_bytes(payload)),
            "root-PIS signer changed committed fields",
        )
        _p4_verify_ed25519(
            _P4_LOCAL_PUBLIC_KEYS[
                "data-publisher/published-input-set-g1-g2"
            ], _hs_canonical_bytes(payload), value["signature"],
        )
        return value

    @staticmethod
    def _entry(receipt, input_id, kind, output_member, output_schema,
               event_schema, media_type, policy_sha):
        contract = {
            "schema_version": "dskit.synthetic-root-input-contract/v1",
            "input_id": input_id,
            "capture_kind": kind,
            "output_member": output_member,
            "output_schema": output_schema,
            "output_media_type": "application/json",
            "authorized_event_schema": event_schema,
            "authorized_raw_media_type": media_type,
            "source_rank_policy_sha256": policy_sha,
        }
        return {
            "input_id": input_id,
            "kind": kind,
            **{key: receipt[key] for key in (
                "root_ref", "root_id", "snapshot_version",
                "member_manifest_sha256", "producer_run_identity",
                "producer_document_sha256", "producer_node",
                "producer_output",
            )},
            "publication_receipt_schema": receipt["schema"],
            "publication_receipt_sha256":
                receipt["root_publication_receipt_sha256"],
            "contract_sha256": _digest(_hs_canonical_bytes(contract)),
        }

    @staticmethod
    def _refs(signed, roster, raw_receipt, roster_receipt):
        dataset_schema = _hs_parse_canonical(signed[0])["schema_version"]
        roster_schema = _hs_parse_canonical(roster[0])["schema_version"]
        specs = (
            ("dataset-capture-authorization", "security-data",
             dataset_schema, _digest(signed[0])),
            ("dataset-capture-grant", "G1",
             "dskit.dataset-capture-grant/v1", _digest(signed[1])),
            ("dataset-capture-grant", "G2",
             "dskit.dataset-capture-grant/v1", _digest(signed[2])),
            ("roster-bootstrap-authorization", "security-data",
             roster_schema, _digest(roster[0])),
            ("roster-bootstrap-grant", "G1",
             "dskit.roster-bootstrap-grant/v1", _digest(roster[1])),
            ("roster-bootstrap-grant", "G2",
             "dskit.roster-bootstrap-grant/v1", _digest(roster[2])),
            ("root-publication", "data-publisher",
             "dskit.root-publication-receipt/v1",
             raw_receipt["root_publication_receipt_sha256"]),
            ("root-publication", "data-publisher",
             "dskit.root-publication-receipt/v2",
             roster_receipt["root_publication_receipt_sha256"]),
        )
        refs = [
            dict(zip(("kind", "role", "schema", "sha256"), spec, strict=True))
            for spec in specs
        ]
        refs.sort(key=lambda ref: tuple(
            ref[key] for key in ("kind", "role", "schema", "sha256")
        ))
        _hs_refuse(len(refs) == 8 and len({
            tuple(ref.values()) for ref in refs
        }) == 8, "closed root-PIS refs required")
        return refs

    def _quarantine(self, signed_id, intent_sha):
        reserve = self._publisher._reserve
        connection = reserve._connection
        try:
            reserve._check()
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, _revoked, _snapshot = reserve._snapshot()
            row = connection.execute(
                "SELECT state,intent_sha256 FROM reserve_uses "
                "WHERE kind='root-pis' AND signed_id=?",
                (signed_id,),
            ).fetchone()
            if row is not None and row[0] != "QUARANTINED":
                _hs_refuse(row[1] == intent_sha,
                           "root-PIS quarantine intent mismatch")
                connection.execute(
                    "UPDATE reserve_uses SET state='QUARANTINED' "
                    "WHERE kind='root-pis' AND signed_id=?",
                    (signed_id,),
                )
                connection.execute(
                    "INSERT INTO reserve_audit "
                    "(kind,signed_id,old_state,new_state,generation) "
                    "VALUES (?,?,?,?,?)",
                    ("root-pis", signed_id, row[0], "QUARANTINED",
                     generation),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def issue(self, authorization_bytes, g1, g2, attestation,
              bootstrap, bg1, bg2, roster_basis, roster_receipt,
              manifest_bytes, raw_basis, raw_receipt):
        authorization_value = _hs_parse_canonical(authorization_bytes)
        bootstrap_value = _hs_parse_canonical(bootstrap)
        v1_event_schema = DATASET_AUTHORIZATION_EVENT_SCHEMAS[
            "dskit.dataset-capture-authorization/v1"
        ]
        _hs_refuse(
            authorization_value.get("schema_version")
            == "dskit.dataset-capture-authorization/v1"
            and bootstrap_value.get("schema_version")
            == "dskit.roster-bootstrap-authorization/v1"
            and authorization_value.get("event_schema") == v1_event_schema
            and bootstrap_value.get("event_schema") == v1_event_schema,
            "root-PIS issuer is v1-only",
        )
        _hs_refuse(not self._closed, "root-PIS issuer already used")
        signed = (authorization_bytes, g1, g2, attestation)
        roster = (bootstrap, bg1, bg2, roster_basis, roster_receipt)
        output = (manifest_bytes, raw_basis, raw_receipt)
        _hs_refuse(all(type(value) is bytes for value in
                       (*signed, *roster, *output)),
                   "exact retained root-PIS originals required")
        publisher = self._publisher
        retained = publisher._retained
        _hs_refuse(
            type(publisher) is _SyntheticRawPublisher
            and type(retained) is tuple
            and len(retained) == 8
            and type(retained[0]) is VerifiedSyntheticDatasetFixture
            and retained[0]._event_schema == v1_event_schema
            and retained[3] == signed
            and retained[4] == roster
            and retained[5:] == output,
            "exact retained v1 root-PIS originals required",
        )
        self._closed = True
        reserve = publisher._reserve
        connection = reserve._connection
        bootstrap_id = _hs_parse_canonical(bootstrap)["bootstrap_id"]
        authorization_id = _hs_parse_canonical(
            authorization_bytes
        )["authorization_id"]
        signed_id = _hs_canonical_bytes({
            "bootstrap_id": bootstrap_id,
            "authorization_id": authorization_id,
        }).decode("ascii")
        intent_sha = None
        committed = False
        try:
            reserve._check()
            connection.execute("BEGIN IMMEDIATE")
            reserve._check()
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            roster_facts = publisher._roster_publisher.proof().verify(
                *roster, _under_writer_lock=True,
            )
            raw_facts = publisher.proof().verify(
                *signed, *roster, *output, _under_writer_lock=True,
            )
            _hs_refuse(
                not {"data-publisher",
                     "data-publisher/published-input-set-g1-g2"} & revoked,
                "root-PIS signer revoked",
            )
            roster_value = _hs_parse_canonical(roster_receipt)
            raw_value = _hs_parse_canonical(raw_receipt)
            _hs_refuse(
                roster_facts["roster_publication_receipt_sha256"]
                == roster_value["root_publication_receipt_sha256"]
                and raw_facts["raw_publication_receipt_sha256"]
                == raw_value["root_publication_receipt_sha256"],
                "root-PIS receipt proof mismatch",
            )
            _hs_refuse(
                now > raw_value["issued_at_ms"],
                "root-PIS must follow raw receipt clock",
            )
            expires = min(
                _hs_parse_canonical(value)["expires_at_ms"]
                for value in (
                    bootstrap, bg1, bg2, authorization_bytes, g1, g2,
                    roster_receipt, raw_receipt,
                )
            )
            _hs_refuse(now < expires, "root-PIS authority expired")
            event_schema = bootstrap_value["event_schema"]
            media_type = bootstrap_value["media_type"]
            policy_sha = roster_facts["source_rank_policy_sha256"]
            _hs_refuse(
                authorization_value["event_schema"] == event_schema
                and authorization_value["media_type"] == media_type
                and authorization_value["source_roster_policy_sha256"]
                == policy_sha,
                "root-PIS source contract mismatch",
            )
            entries = [
                self._entry(
                    raw_value, "raw_event_dataset", "raw-event-dataset",
                    "raw_event_dataset.json",
                    "dskit.raw-event-dataset-capture/v1",
                    event_schema, media_type, policy_sha,
                ),
                self._entry(
                    roster_value, "source_roster", "source-roster",
                    "source_roster.json",
                    "dskit.source-roster-capture/v1",
                    event_schema, media_type, policy_sha,
                ),
            ]
            refs = self._refs(signed, roster, raw_value, roster_value)
            suffix = {
                "issuer_role": "data-publisher",
                "key_usage": "published-input-set-g1-g2",
                "signature_alg": "Ed25519",
                "issued_at_ms": now,
                "not_before_ms": now,
                "expires_at_ms": expires,
                "revocation_snapshot_sha256": snapshot,
                "key": {
                    "key_id": "data-publisher/published-input-set-g1-g2",
                    "key_version": 1,
                },
            }
            basis_payload = {
                "schema": "dskit.issuance-basis/v2",
                "kind": "root-pis",
                "study_id": "synthetic-study",
                "refs": refs,
                **suffix,
            }
            basis_sha = _digest(_hs_canonical_bytes(basis_payload))
            pis_payload = {
                "schema": "dskit.published-input-set/v2",
                "study_id": "synthetic-study",
                "phase": "root-g1-g2",
                "purpose": "historical-study",
                "entries": entries,
                "issuance_basis_sha256": basis_sha,
                **suffix,
            }
            intent = {
                "schema_version":
                    "dskit.synthetic-root-pis-issue-intent/v1",
                "bootstrap_id": bootstrap_id,
                "authorization_id": authorization_id,
                "bootstrap_authorization_sha256": _digest(bootstrap),
                "bootstrap_g1_sha256": _digest(bg1),
                "bootstrap_g2_sha256": _digest(bg2),
                "dataset_authorization_sha256":
                    _digest(authorization_bytes),
                "dataset_g1_sha256": _digest(g1),
                "dataset_g2_sha256": _digest(g2),
                "fixture_attestation_sha256": _digest(attestation),
                "roster_receipt_sha256":
                    roster_value["root_publication_receipt_sha256"],
                "raw_receipt_sha256":
                    raw_value["root_publication_receipt_sha256"],
                "refs": refs,
                "entries": entries,
                "basis_unsigned_sha256": basis_sha,
                "pis_unsigned_sha256":
                    _digest(_hs_canonical_bytes(pis_payload)),
                "signer_key_id": suffix["key"]["key_id"],
                "signer_key_version": 1,
                "revocation_snapshot_sha256": snapshot,
                "issued_at_ms": now,
                "not_before_ms": now,
                "expires_at_ms": expires,
            }
            intent_sha = _digest(_hs_canonical_bytes(intent))
            _hs_refuse(
                reserve._now() == now and reserve._snapshot()
                == (generation, revoked, snapshot),
                "root-PIS shared state changed before commit",
            )
            connection.execute(
                "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
                ("root-pis", signed_id,
                 _digest(signed_id.encode("ascii")),
                 _digest(_hs_canonical_bytes([_digest(bg1), _digest(g1)])),
                 _digest(_hs_canonical_bytes([_digest(bg2), _digest(g2)])),
                 snapshot, intent_sha, "RESERVED"),
            )
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,NULL,?,?)",
                ("root-pis", signed_id, "RESERVED", generation),
            )
            connection.execute(
                "UPDATE reserve_uses SET state='ISSUED' "
                "WHERE kind='root-pis' AND signed_id=? AND state='RESERVED'",
                (signed_id,),
            )
            connection.execute(
                "INSERT INTO reserve_audit "
                "(kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("root-pis", signed_id, "RESERVED", "ISSUED",
                 generation),
            )
            connection.execute("COMMIT")
            committed = True
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ValueError("signed root-PIS pair already spent") from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        try:
            basis_bytes = self._sign(
                basis_payload, "issuance_basis_sha256",
            )
            self._check_signed(
                basis_bytes, basis_payload, "issuance_basis_sha256",
            )
            pis_bytes = self._sign(
                pis_payload, "published_input_set_sha256",
            )
            self._check_signed(
                pis_bytes, pis_payload, "published_input_set_sha256",
            )
            key = ("synthetic-study", "root-g1-g2", signed_id)
            pair = (basis_bytes, pis_bytes)
            stored = publisher._root_pis_pairs.setdefault(key, pair)
            _hs_refuse(stored == pair, "root-PIS WORM conflict")
            self._retained = (signed, roster, output, intent, pair, key)
            return pair
        except Exception:
            if committed:
                self._quarantine(signed_id, intent_sha)
            raise


    def proof(self):
        """Return a fresh nonauthorizing proof for the retained signed pair."""
        _hs_refuse(self._retained is not None,
                   "successful retained root-PIS pair required")
        return NonAuthorizingSyntheticRootPisProof(_MAKE, self)


    def graph(self):
        """Return one issuer-owned read-only dynamic root graph."""
        _hs_refuse(self._retained is not None,
                   "successful retained root-PIS pair required")
        if self._graph is None:
            self._graph = NonAuthorizingDynamicRootGraph(_MAKE, self)
        return self._graph


class NonAuthorizingSyntheticRootPisProof:
    """Read-only verification of one retained synthetic two-root PIS."""

    __slots__ = ("_issuer", "_locked")

    def __init_subclass__(cls, **kwargs):
        """Forbid proof subtypes that could replace the fixed checks."""
        raise TypeError("the fixed root-PIS proof is final")

    def __init__(self, token, issuer):
        if token is not _MAKE or type(issuer) is not _SyntheticRootPisIssuer:
            raise TypeError("issuer-owned root-PIS proof required")
        object.__setattr__(self, "_issuer", issuer)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        """Keep the issuer binding frozen."""
        if getattr(self, "_locked", False):
            raise AttributeError("root-PIS proof is frozen")
        object.__setattr__(self, name, value)

    @staticmethod
    def _audit(connection, signed_id, generation):
        audit = connection.execute(
            "SELECT old_state,new_state,generation FROM reserve_audit "
            "WHERE kind='root-pis' AND signed_id=? ORDER BY seq",
            (signed_id,),
        ).fetchall()
        _hs_refuse(
            audit == [
                (None, "RESERVED", generation),
                ("RESERVED", "ISSUED", generation),
            ],
            "complete root-PIS issuance audit required",
        )
        return audit

    def verify(self, authorization_bytes, g1, g2, attestation,
               bootstrap, bg1, bg2, roster_basis, roster_receipt,
               manifest_bytes, raw_basis, raw_receipt,
               basis_bytes, pis_bytes):
        """Return immutable checked identities, never P4 admission."""
        values = (
            authorization_bytes, g1, g2, attestation,
            bootstrap, bg1, bg2, roster_basis, roster_receipt,
            manifest_bytes, raw_basis, raw_receipt,
        )
        issuer = self._issuer
        _hs_refuse(
            issuer._retained is not None
            and all(type(value) is bytes for value in
                    (*values, basis_bytes, pis_bytes)),
            "retained exact root-PIS proof bytes required",
        )
        signed, roster, output, intent, pair, key = issuer._retained
        _hs_refuse(
            values == (*signed, *roster, *output)
            and pair == (basis_bytes, pis_bytes),
            "root-PIS originals mismatch",
        )
        publisher = issuer._publisher
        reserve = publisher._reserve
        connection = reserve._connection
        signed_id = key[2]
        reserve._check()
        try:
            connection.execute("BEGIN")
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            roster_facts = publisher._roster_publisher.proof().verify(
                *roster, _under_writer_lock=True,
            )
            raw_facts = publisher.proof().verify(
                *signed, *roster, *output, _under_writer_lock=True,
            )
            _hs_refuse(
                now >= intent["issued_at_ms"]
                and now < intent["expires_at_ms"]
                and snapshot == intent["revocation_snapshot_sha256"]
                and not {
                    "data-publisher",
                    "data-publisher/published-input-set-g1-g2",
                } & revoked,
                "root-PIS proof authority stale",
            )
            roster_value = _hs_parse_canonical(roster_receipt)
            raw_value = _hs_parse_canonical(raw_receipt)
            _hs_refuse(
                roster_facts["roster_publication_receipt_sha256"]
                == roster_value["root_publication_receipt_sha256"]
                and raw_facts["raw_publication_receipt_sha256"]
                == raw_value["root_publication_receipt_sha256"],
                "root-PIS original receipts changed",
            )
            bootstrap_value = _hs_parse_canonical(bootstrap)
            authorization_value = _hs_parse_canonical(
                authorization_bytes
            )
            _hs_refuse(
                signed_id == _hs_canonical_bytes({
                    "bootstrap_id": bootstrap_value["bootstrap_id"],
                    "authorization_id":
                        authorization_value["authorization_id"],
                }).decode("ascii"),
                "root-PIS signed pair identity changed",
            )
            policy_sha = roster_facts["source_rank_policy_sha256"]
            _hs_refuse(
                authorization_value["source_roster_policy_sha256"]
                == policy_sha
                and authorization_value["event_schema"]
                == bootstrap_value["event_schema"]
                and authorization_value["media_type"]
                == bootstrap_value["media_type"],
                "root-PIS source contract changed",
            )
            entries = [
                issuer._entry(
                    raw_value, "raw_event_dataset", "raw-event-dataset",
                    "raw_event_dataset.json",
                    "dskit.raw-event-dataset-capture/v1",
                    bootstrap_value["event_schema"],
                    bootstrap_value["media_type"], policy_sha,
                ),
                issuer._entry(
                    roster_value, "source_roster", "source-roster",
                    "source_roster.json",
                    "dskit.source-roster-capture/v1",
                    bootstrap_value["event_schema"],
                    bootstrap_value["media_type"], policy_sha,
                ),
            ]
            refs = issuer._refs(signed, roster, raw_value, roster_value)
            expires = min(
                _hs_parse_canonical(value)["expires_at_ms"]
                for value in (
                    bootstrap, bg1, bg2, authorization_bytes, g1, g2,
                    roster_receipt, raw_receipt,
                )
            )
            suffix = {
                "issuer_role": "data-publisher",
                "key_usage": "published-input-set-g1-g2",
                "signature_alg": "Ed25519",
                "issued_at_ms": intent["issued_at_ms"],
                "not_before_ms": intent["issued_at_ms"],
                "expires_at_ms": expires,
                "revocation_snapshot_sha256": snapshot,
                "key": {
                    "key_id": "data-publisher/published-input-set-g1-g2",
                    "key_version": 1,
                },
            }
            basis_payload = {
                "schema": "dskit.issuance-basis/v2",
                "kind": "root-pis",
                "study_id": "synthetic-study",
                "refs": refs,
                **suffix,
            }
            basis_sha = _digest(_hs_canonical_bytes(basis_payload))
            pis_payload = {
                "schema": "dskit.published-input-set/v2",
                "study_id": "synthetic-study",
                "phase": "root-g1-g2",
                "purpose": "historical-study",
                "entries": entries,
                "issuance_basis_sha256": basis_sha,
                **suffix,
            }
            expected_intent = {
                "schema_version":
                    "dskit.synthetic-root-pis-issue-intent/v1",
                "bootstrap_id": bootstrap_value["bootstrap_id"],
                "authorization_id":
                    authorization_value["authorization_id"],
                "bootstrap_authorization_sha256": _digest(bootstrap),
                "bootstrap_g1_sha256": _digest(bg1),
                "bootstrap_g2_sha256": _digest(bg2),
                "dataset_authorization_sha256":
                    _digest(authorization_bytes),
                "dataset_g1_sha256": _digest(g1),
                "dataset_g2_sha256": _digest(g2),
                "fixture_attestation_sha256": _digest(attestation),
                "roster_receipt_sha256":
                    roster_value["root_publication_receipt_sha256"],
                "raw_receipt_sha256":
                    raw_value["root_publication_receipt_sha256"],
                "refs": refs,
                "entries": entries,
                "basis_unsigned_sha256": basis_sha,
                "pis_unsigned_sha256":
                    _digest(_hs_canonical_bytes(pis_payload)),
                "signer_key_id": suffix["key"]["key_id"],
                "signer_key_version": 1,
                "revocation_snapshot_sha256": snapshot,
                "issued_at_ms": intent["issued_at_ms"],
                "not_before_ms": intent["issued_at_ms"],
                "expires_at_ms": expires,
            }
            _hs_refuse(
                expected_intent == intent
                and intent["issued_at_ms"] > raw_value["issued_at_ms"],
                "root-PIS issue intent changed",
            )
            expected_row = (
                _digest(signed_id.encode("ascii")),
                _digest(_hs_canonical_bytes([
                    _digest(bg1), _digest(g1),
                ])),
                _digest(_hs_canonical_bytes([
                    _digest(bg2), _digest(g2),
                ])),
                snapshot, _digest(_hs_canonical_bytes(intent)), "ISSUED",
            )
            query = (
                "SELECT authorization_sha256,g1_sha256,g2_sha256,"
                "snapshot_sha256,intent_sha256,state FROM reserve_uses "
                "WHERE kind='root-pis' AND signed_id=?"
            )
            row = connection.execute(query, (signed_id,)).fetchone()
            _hs_refuse(row == expected_row,
                       "root-PIS one-use row changed")
            audit = self._audit(connection, signed_id, generation)
            issuer._check_signed(
                basis_bytes, basis_payload, "issuance_basis_sha256",
            )
            pis = issuer._check_signed(
                pis_bytes, pis_payload, "published_input_set_sha256",
            )
            _hs_refuse(
                key == ("synthetic-study", "root-g1-g2", signed_id)
                and publisher._root_pis_pairs.get(key) == pair
                and sum(
                    value == pair
                    for value in publisher._root_pis_pairs.values()
                ) == 1,
                "root-PIS WORM pair changed",
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        reserve._check()
        try:
            connection.execute("BEGIN")
            final_generation, final_revoked, final_snapshot = (
                reserve._snapshot()
            )
            final_now = reserve._now()
            _hs_refuse(
                final_generation == generation
                and final_snapshot == snapshot
                and final_now == now
                and not {
                    "data-publisher",
                    "data-publisher/published-input-set-g1-g2",
                } & final_revoked,
                "root-PIS proof freshness changed",
            )
            _hs_refuse(
                connection.execute(query, (signed_id,)).fetchone()
                == expected_row,
                "root-PIS final row changed",
            )
            _hs_refuse(
                self._audit(connection, signed_id, final_generation)
                == audit,
                "root-PIS final audit changed",
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return MappingProxyType({
            "published_input_set_sha256":
                pis["published_input_set_sha256"],
            "issuance_basis_sha256":
                _hs_parse_canonical(basis_bytes)["issuance_basis_sha256"],
            "raw_root_sha256": raw_facts["raw_root_sha256"],
            "roster_root_sha256": roster_facts["roster_root_sha256"],
            "checked_at_ms": final_now,
            "authorizing": False,
            "deployment_eligible": False,
        })


class _DynamicRootGraphSnapshot:
    """Opaque immutable point-in-time identity for a verified root graph."""

    __slots__ = ("_graph", "_publisher", "_broker", "_reserve_path",
                 "_vector", "_references")

    def __new__(cls, *args, **kwargs):
        raise TypeError("dynamic root snapshot is graph-issued")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("dynamic root snapshot is final")

    def __setattr__(self, name, value):
        raise AttributeError("dynamic root snapshot is frozen")

    @property
    def references(self):
        """Return only the closed exact canonical artifact references."""
        return self._references


class NonAuthorizingDynamicRootGraph:
    """Read-only resolver of one retained signed synthetic two-root graph."""

    __slots__ = ("_issuer", "_publisher", "_records", "_snapshot")

    def __init_subclass__(cls, **kwargs):
        """Forbid alternate graph implementations."""
        raise TypeError("the synthetic dynamic root graph is final")

    def __init__(self, token, issuer):
        if token is not _MAKE or type(issuer) is not _SyntheticRootPisIssuer:
            raise TypeError("issuer-owned dynamic root graph required")
        _hs_refuse(issuer._retained is not None,
                   "successful retained root-PIS issuer required")
        self._issuer = issuer
        self._publisher = issuer._publisher
        self._records = self._derive_records()
        self._snapshot = None

    def _originals(self):
        _hs_refuse(self._issuer._retained is not None,
                   "retained root graph originals required")
        signed, roster, output, _intent, pair, _key = (
            self._issuer._retained
        )
        return signed, roster, output, pair

    def _derive_records(self):
        signed, roster, output, pair = self._originals()
        dataset_authorization = _hs_parse_canonical(signed[0])
        roster_authorization = _hs_parse_canonical(roster[0])
        dataset_schema = dataset_authorization["schema_version"]
        roster_schema = roster_authorization["schema_version"]
        v1_event_schema = DATASET_AUTHORIZATION_EVENT_SCHEMAS[
            "dskit.dataset-capture-authorization/v1"
        ]
        _hs_refuse(
            dataset_schema == "dskit.dataset-capture-authorization/v1"
            and roster_schema == "dskit.roster-bootstrap-authorization/v1"
            and dataset_authorization.get("event_schema") == v1_event_schema
            and roster_authorization.get("event_schema") == v1_event_schema,
            "dynamic root graph is v1-only",
        )
        items = (
            ("dataset-capture-authorization", "security-data",
             dataset_schema, signed[0], None),
            ("dataset-capture-grant", "G1",
             "dskit.dataset-capture-grant/v1", signed[1], None),
            ("dataset-capture-grant", "G2",
             "dskit.dataset-capture-grant/v1", signed[2], None),
            ("roster-bootstrap-authorization", "security-data",
             roster_schema, roster[0], None),
            ("roster-bootstrap-grant", "G1",
             "dskit.roster-bootstrap-grant/v1", roster[1], None),
            ("roster-bootstrap-grant", "G2",
             "dskit.roster-bootstrap-grant/v1", roster[2], None),
            ("issuance-basis", "data-publisher",
             "dskit.issuance-basis/v2", roster[3],
             "issuance_basis_sha256"),
            ("root-publication", "data-publisher",
             "dskit.root-publication-receipt/v2", roster[4],
             "root_publication_receipt_sha256"),
            ("issuance-basis", "data-publisher",
             "dskit.issuance-basis/v1", output[1],
             "issuance_basis_sha256"),
            ("root-publication", "data-publisher",
             "dskit.root-publication-receipt/v1", output[2],
             "root_publication_receipt_sha256"),
            ("issuance-basis", "data-publisher",
             "dskit.issuance-basis/v2", pair[0],
             "issuance_basis_sha256"),
            ("root-pis", "data-publisher",
             "dskit.published-input-set/v2", pair[1],
             "published_input_set_sha256"),
        )
        records = []
        for kind, role, schema, raw, self_field in items:
            _hs_refuse(type(raw) is bytes,
                       "exact retained graph artifact bytes required")
            parsed = _hs_parse_canonical(raw)
            _hs_refuse(
                type(parsed) is dict
                and parsed.get(
                    "schema" if self_field else "schema_version"
                ) == schema,
                "dynamic root artifact schema mismatch",
            )
            digest = parsed[self_field] if self_field else _digest(raw)
            _hs_refuse(type(digest) is str
                       and re.fullmatch(r"[0-9a-f]{64}", digest)
                       is not None, "dynamic root reference digest refused")
            ref = _hs_canonical_bytes({
                "kind": kind, "role": role,
                "schema": schema, "sha256": digest,
            })
            records.append((ref, raw))
        records.sort(key=lambda item: tuple(
            _hs_parse_canonical(item[0])[field]
            for field in ("kind", "role", "schema", "sha256")
        ))
        _hs_refuse(
            len(records) == 12
            and len({ref for ref, _raw in records}) == 12,
            "closed twelve-artifact root graph required",
        )
        return tuple(records)

    def _vector(self):
        reserve = self._publisher._reserve
        reserve._check()
        connection = reserve._connection
        _hs_refuse(not connection.in_transaction,
                   "graph read needs independent transaction")
        signed, roster, _output, _pair = self._originals()
        ids = (
            ("roster-bootstrap",
             _hs_parse_canonical(roster[0])["bootstrap_id"]),
            ("raw-dataset",
             _hs_parse_canonical(signed[0])["authorization_id"]),
            ("root-pis", self._issuer._retained[5][2]),
        )
        try:
            connection.execute("BEGIN")
            meta = connection.execute(
                "SELECT domain,generation,now_ms FROM reserve_meta "
                "WHERE singleton=1"
            ).fetchone()
            generation, revoked, snapshot = reserve._snapshot()
            now = reserve._now()
            _hs_refuse(
                meta == (_SYNTHETIC_RESERVE_DOMAIN, generation, now),
                "dynamic root graph domain mismatch",
            )
            rows = []
            audits = []
            for kind, signed_id in ids:
                row = connection.execute(
                    "SELECT kind,signed_id,authorization_sha256,g1_sha256,"
                    "g2_sha256,snapshot_sha256,intent_sha256,state "
                    "FROM reserve_uses WHERE kind=? AND signed_id=?",
                    (kind, signed_id),
                ).fetchone()
                audit = connection.execute(
                    "SELECT seq,kind,signed_id,old_state,new_state,generation "
                    "FROM reserve_audit WHERE kind=? AND signed_id=? "
                    "ORDER BY seq",
                    (kind, signed_id),
                ).fetchall()
                _hs_refuse(row is not None and bool(audit),
                           "complete dynamic root reserve backing required")
                rows.append(row)
                audits.append(tuple(audit))
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return (meta, revoked, snapshot, tuple(rows), tuple(audits))

    def _prove(self):
        signed, roster, output, pair = self._originals()
        return self._issuer.proof().verify(
            *signed, *roster, *output, *pair,
        )

    def snapshot(self):
        """Issue a graph-owned token only across a full proof and fence."""
        _hs_refuse(self._issuer._graph is self,
                   "issuer-owned dynamic root graph required")
        before = self._vector()
        self._prove()
        after = self._vector()
        _hs_refuse(before == after,
                   "dynamic root snapshot freshness changed")
        records = self._derive_records()
        _hs_refuse(records == self._records,
                   "dynamic root graph records changed")
        token = object.__new__(_DynamicRootGraphSnapshot)
        object.__setattr__(token, "_graph", self)
        object.__setattr__(token, "_publisher", self._publisher)
        object.__setattr__(token, "_broker", self._publisher._broker)
        object.__setattr__(
            token, "_reserve_path", self._publisher._reserve._path,
        )
        object.__setattr__(token, "_vector", before)
        object.__setattr__(
            token, "_references", tuple(ref for ref, _raw in records),
        )
        self._snapshot = token
        return token

    def resolve(self, snapshot, exact_ref_bytes):
        """Return only retained signed bytes from the issued live graph."""
        _hs_refuse(
            self._issuer._graph is self
            and type(snapshot) is _DynamicRootGraphSnapshot
            and snapshot is self._snapshot
            and snapshot._graph is self
            and snapshot._publisher is self._publisher
            and snapshot._broker is self._publisher._broker
            and snapshot._reserve_path
                == self._publisher._reserve._path
            and snapshot._references
                == tuple(ref for ref, _raw in self._records),
            "exact issued dynamic root snapshot required",
        )
        _hs_refuse(type(exact_ref_bytes) is bytes,
                   "exact dynamic root reference bytes required")
        ref = _hs_parse_canonical(exact_ref_bytes)
        _hs_refuse(
            type(ref) is dict
            and set(ref) == {"kind", "role", "schema", "sha256"}
            and _hs_canonical_bytes(ref) == exact_ref_bytes,
            "canonical dynamic root reference required",
        )
        before = self._vector()
        _hs_refuse(before == snapshot._vector,
                   "dynamic root snapshot state changed")
        self._prove()
        records = self._derive_records()
        _hs_refuse(records == self._records,
                   "dynamic root graph records changed")
        matches = [
            raw for candidate, raw in records
            if candidate == exact_ref_bytes
        ]
        _hs_refuse(len(matches) == 1,
                   "dynamic root reference not in closed graph")
        raw = matches[0]
        self._prove()
        after = self._vector()
        _hs_refuse(after == snapshot._vector,
                   "dynamic root resolve freshness changed")
        return raw


# ---------------------------------------------------------------------------
# ADR-0143: same-domain dynamic P4 capture authority (F5a Packet 7)
#
# One second, one-shot instance of the existing final
# _SyntheticP4CapturedAuthorizationAuthority class, bound to a new resolver
# constructed only from a retained ADR-0141/0142 issuer/graph. The legacy
# fixed corpus, its 500-ms clock, _FixedWormTrustedArtifactResolver,
# _FixedTerminalArtifactVerifier and _p4_close_admission's v1-only grammar
# are unedited; every class/function below is new and self-contained.
# ---------------------------------------------------------------------------

_P4_PENDING_DYNAMIC_RESOLVER_TOKENS = set()


class _DynamicP4TrustedArtifactResolver(_Opaque):
    """Hold one issuer-owned dynamic root graph; never cache artifact bytes.

    Unlike ``_FixedWormTrustedArtifactResolver`` this resolver has no
    ``_records`` slot: it always re-derives through ``graph.snapshot()``/
    ``graph.resolve()`` per call (ADR-0143 Decision point 2). It takes no
    ``fixture_facts`` and never touches ``_P4_EXTERNAL_RECORDS``/
    ``_P4_EXTERNAL_BY_REF``.
    """

    __slots__ = ("_graph", "_snapshot", "_terminal", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("P4 dynamic resolver is final")

    def __init__(self, token, graph):
        if token not in _P4_PENDING_DYNAMIC_RESOLVER_TOKENS:
            raise TypeError("P4 dynamic resolver requires dynamic broker construction")
        _P4_PENDING_DYNAMIC_RESOLVER_TOKENS.remove(token)
        if type(graph) is not NonAuthorizingDynamicRootGraph:
            raise TypeError("exact dynamic root graph is required")
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_snapshot", None)
        terminal = object.__new__(_DynamicP4TerminalArtifactVerifier)
        object.__setattr__(self, "_terminal", terminal)
        _P4_RESOLVERS[self] = (graph, terminal)
        _P4_TERMINALS[terminal] = (self, None, None)

    def __setattr__(self, name, value):
        raise AttributeError("P4 dynamic resolver is frozen")

    def snapshot(self):
        """Re-derive the graph-owned token; the graph performs its own fence.

        ADR-0143 Decision point 2 / Phase 0 pin F2: delegates entirely to
        ``NonAuthorizingDynamicRootGraph.snapshot()``'s own before/after
        vector fence; this resolver adds no second, independent fence.
        """
        token = self._graph.snapshot()
        object.__setattr__(self, "_snapshot", token)
        _P4_TERMINALS[self._terminal] = (self, token, None)
        _p4_snapshot_integrity(self, token)
        return token

    def resolve(self, snapshot, exact_ref_bytes):
        """Resolve exact retained bytes without granting trust or lifecycle use."""
        return _P4_DYNAMIC_CHECKED_RESOLVE(self, snapshot, exact_ref_bytes)

    def _checked_resolve(self, snapshot, exact_ref_bytes):
        """Check issued identity before delegating to the graph's own fence.

        Phase 0 pin F2: the vector fence itself lives only in
        ``NonAuthorizingDynamicRootGraph.resolve``; this method never repeats
        it, avoiding the TOCTOU duplication a second independent fence would
        add.
        """
        _p4_snapshot_integrity(self, snapshot)
        if type(exact_ref_bytes) is not bytes:
            raise TypeError("exact P4 snapshot reference bytes required")
        return self._graph.resolve(snapshot, exact_ref_bytes)

    def close_admission(self, snapshot, admission_ref, live_projection):
        """Verify the narrow root-capture-admission chain; grant no authority."""
        return _P4_DYNAMIC_CLOSE_ADMISSION(self, snapshot, admission_ref, live_projection)


class _DynamicP4TerminalArtifactVerifier(TerminalArtifactVerifier):
    """Authenticate a dynamic graph artifact as a terminal parent position.

    Registered only against the dynamic resolver's own snapshot/12-reference
    corpus, never ``_P4_EXTERNAL_RECORDS`` (ADR-0143 Decision point 5). Reuses
    exactly the already-pinned ``_P4_LOCAL_PUBLIC_KEYS``; no new key material.
    """

    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        raise TypeError("dynamic terminal capability requires dynamic broker construction")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("dynamic terminal verifier is final")

    def verify_terminal(self, snapshot, parent_basis_bytes, terminal_class,
                        terminal_ref_bytes, canonical_artifact_bytes):
        """Authenticate one graph artifact as a signed parent position."""
        issued = _P4_TERMINALS.get(self)
        _hs_refuse(issued is not None, "terminal capability is unregistered")
        resolver, expected_snapshot, _unused = issued
        _hs_refuse(resolver._terminal is self, "terminal broker/root identity refused")
        _p4_snapshot_integrity(resolver, snapshot)
        _hs_refuse(snapshot is expected_snapshot, "dynamic terminal snapshot mismatch")
        _hs_refuse(type(terminal_ref_bytes) is bytes and type(canonical_artifact_bytes) is bytes
                   and type(parent_basis_bytes) is bytes and type(terminal_class) is str)
        resolved = resolver._graph.resolve(snapshot, terminal_ref_bytes)
        _hs_refuse(resolved == canonical_artifact_bytes, "dynamic terminal artifact bytes differ")
        ref = _hs_parse_canonical(terminal_ref_bytes)
        _hs_refuse(terminal_class == ref["kind"] + "/" + ref["role"],
                   "dynamic terminal class refused")
        parent_ref = _hs_parse_canonical(parent_basis_bytes)
        _hs_refuse(type(parent_ref) is dict, "dynamic terminal parent basis refused")
        binding = (self, resolver, snapshot, terminal_ref_bytes, canonical_artifact_bytes,
                   parent_basis_bytes, terminal_class, 0)
        anchor = object.__new__(VerifiedExternalArtifactAnchor)
        object.__setattr__(anchor, "_binding", binding)
        _P4_ANCHORS[anchor] = binding
        return anchor

    def require_current(self, snapshot, anchors):
        """Recheck exact proof issuance and graph freshness without spending."""
        issued = _P4_TERMINALS.get(self)
        _hs_refuse(issued is not None, "terminal capability is unregistered")
        resolver, _snap, _unused = issued
        _hs_refuse(resolver._terminal is self, "terminal broker/root identity refused")
        _p4_snapshot_integrity(resolver, snapshot)
        _hs_refuse(type(anchors) is tuple and len({id(anchor) for anchor in anchors}) == len(anchors))
        for anchor in anchors:
            _hs_refuse(type(anchor) is VerifiedExternalArtifactAnchor)
            binding = _P4_ANCHORS.get(anchor)
            _hs_refuse(binding is not None and anchor._binding is binding)
            _hs_refuse(binding[0] is self and binding[2] is snapshot and binding[-1] == 0)
            resolved = resolver._graph.resolve(snapshot, binding[3])
            _hs_refuse(resolved == binding[4], "dynamic terminal parent verification refused")


class _DynamicP4VerificationClock(TrustedClock):
    """Read the live shared synthetic clock; never the fixed 500-ms instant.

    ADR-0143 Decision point 6: ``now_ms()`` returns ``reserve._now()``, the
    same ``_SyntheticAuthorizationReserve`` instance reachable from
    ``issuer._publisher._reserve``.
    """

    def __init__(self, reserve):
        if type(reserve) is not _SyntheticAuthorizationReserve:
            raise TypeError("exact shared reserve is required")
        object.__setattr__(self, "_reserve", reserve)

    def __setattr__(self, name, value):
        raise AttributeError("P4 dynamic clock is frozen")

    def now_ms(self):
        return self._reserve._now()


class _DynamicP4VerificationRevocations(HistoricalStudyRevocations):
    """Check the live shared revocation digest within one guarded read.

    ADR-0143 Decision point 6 / Phase 0 pin: ``now_ms`` is compared for exact
    equality to the value read inside the SAME guarded transaction as
    ``snapshot_sha256`` -- no TOCTOU window between the two reads, unlike the
    legacy class's hardcoded ``now_ms == 500``.
    """

    def __init__(self, reserve):
        if type(reserve) is not _SyntheticAuthorizationReserve:
            raise TypeError("exact shared reserve is required")
        object.__setattr__(self, "_reserve", reserve)

    def __setattr__(self, name, value):
        raise AttributeError("P4 dynamic revocations is frozen")

    def is_unrevoked(self, snapshot_sha256, key_id, key_version, now_ms):
        reserve = self._reserve
        connection = reserve._connection
        reserve._check()
        opened = False
        try:
            if not connection.in_transaction:
                connection.execute("BEGIN")
                opened = True
            _generation, _revoked, snapshot = reserve._snapshot()
            current_now = reserve._now()
            if opened:
                connection.execute("COMMIT")
                opened = False
        except Exception:
            if opened and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return (
            snapshot_sha256 == snapshot and current_now == now_ms
            and type(key_id) is str and key_id in _P4_LOCAL_PUBLIC_KEYS
            and type(key_version) is int and key_version == 1
        )


_P4_DYNAMIC_CHECKED_RESOLVE = _DynamicP4TrustedArtifactResolver._checked_resolve
_P4_DYNAMIC_RESOLVER_LOOKUP = _DynamicP4TrustedArtifactResolver.resolve
_P4_DYNAMIC_RESOLVER_METHODS = (
    _DynamicP4TrustedArtifactResolver.snapshot,
    _DynamicP4TrustedArtifactResolver.resolve,
    _P4_DYNAMIC_CHECKED_RESOLVE,
)
_P4_DYNAMIC_TERMINAL_METHODS = (
    _DynamicP4TerminalArtifactVerifier.verify_terminal,
    _DynamicP4TerminalArtifactVerifier.require_current,
)


def _p4_dynamic_fixed_integrity():
    """Reject in-process tampering with the dynamic branch's own dispatch pins.

    Phase 0 pin F1: mirrors the legacy ``_p4_fixed_integrity`` self-check for
    the dynamic branch; hostile interpreter code is out of the external
    threat model (implementation-workflow.md point 4), this is defense in
    depth only, matching the legacy function's own stated scope.
    """
    _hs_refuse(_dynamic_p4_close_admission is _P4_DYNAMIC_CLOSE_ADMISSION,
               "P4 dynamic closure dispatch integrity refused")
    _hs_refuse(_DynamicP4VerificationClock.now_ms is _P4_DYNAMIC_CLOCK_NOW_MS,
               "P4 dynamic clock dispatch integrity refused")
    _hs_refuse(_DynamicP4VerificationRevocations.is_unrevoked is _P4_DYNAMIC_REVOCATIONS_UNREVOKED,
               "P4 dynamic revocations dispatch integrity refused")
    _hs_refuse(
        (_DynamicP4TerminalArtifactVerifier.verify_terminal,
         _DynamicP4TerminalArtifactVerifier.require_current) == _P4_DYNAMIC_TERMINAL_METHODS,
        "P4 dynamic terminal dispatch integrity refused",
    )


def _p4_dynamic_reference_bytes(ref):
    """Validate a closed kind/role/schema/sha256 reference for this chain only.

    Self-contained: shares no schema whitelist or helper with the legacy
    ``_p4_artifact_reference_bytes``.
    """
    _hs_refuse(type(ref) is dict and set(ref) == {"kind", "role", "schema", "sha256"},
               "exact P4 dynamic reference is required")
    _hs_refuse(all(type(value) is str and value for value in ref.values()),
               "P4 dynamic reference strings are required")
    _require_sha256(ref["sha256"], "P4 dynamic artifact")
    return _hs_canonical_bytes(ref)


def _p4_dynamic_root_capture_admission(root_pis_ref, dataset_g1_ref, dataset_g2_ref,
                                       run_id, logical_execution_id):
    """Build the closed ``dskit.root-capture-admission/v1`` payload.

    ADR-0143 Decision point 7 / evidence 0181 ``document_shapes_pinned``:
    exactly these 7 fields plus the self digest (8 keys total).
    """
    payload = {
        "schema_version": "dskit.root-capture-admission/v1",
        "study_id": "synthetic-study",
        "root_pis_ref": root_pis_ref,
        "dataset_g1_ref": dataset_g1_ref,
        "dataset_g2_ref": dataset_g2_ref,
        "logical_execution_id": logical_execution_id,
        "run_id": run_id,
    }
    digest = _digest(_hs_canonical_bytes(payload))
    return dict(payload, root_capture_admission_sha256=digest), digest


def _p4_dynamic_planned_entry(pis_entry, consumer_document_sha256, consumer_node,
                              consumer_input, purpose):
    """Build one closed pce leaf, bound 1:1 to its own PIS entry.

    Evidence 0181 ``planned_entry_sha256_preimage``: refuses an alias or
    swapped PIS entry (matrix row 7) because the preimage is recomputed from
    the entry's OWN ``input_id``/``contract_sha256``, never trusted from a
    caller-supplied row.
    """
    preimage = {
        "root_pis_entry_input_id": pis_entry["input_id"],
        "root_pis_contract_sha256": pis_entry["contract_sha256"],
    }
    return {
        "planned_entry_sha256": _digest(_hs_canonical_bytes(preimage)),
        "input_id": pis_entry["input_id"],
        "published_input": pis_entry,
        "consumer_document_sha256": consumer_document_sha256,
        "consumer_node": consumer_node,
        "consumer_input": consumer_input,
        "purpose": purpose,
    }


def _p4_dynamic_capture_admission_set(root_capture_admission_sha256, entries,
                                      consumer_document_sha256, purpose):
    """Build the closed ``dskit.capture-admission-set/v1`` (cas) payload.

    ADR-0144 Decision point 8: gains ``consumer_document_sha256``/``purpose``
    top-level fields versus ADR-0143's original two-parameter signature,
    sourced from the batch's own live captures. Purely additive:
    ``_dynamic_p4_close_admission``'s existing checks read neither field.
    """
    payload = {
        "schema_version": "dskit.capture-admission-set/v1",
        "study_id": "synthetic-study",
        "subject_ref": {"kind": "action"},
        "root_capture_admission_sha256": root_capture_admission_sha256,
        "entries": entries,
        "consumer_document_sha256": consumer_document_sha256,
        "purpose": purpose,
    }
    digest = _digest(_hs_canonical_bytes(payload))
    return dict(payload, capture_admission_set_sha256=digest), digest


class _DynamicP4AdmissionReconstruction:
    """Frozen reconstruction of one dynamic root-capture-admission chain.

    Not exported. ADR-0144 Phase 0 pin F6: uses this codebase's existing
    hand-rolled frozen-object idiom (``__slots__`` plus a ``_locked`` guard,
    matching ``_Frozen``/``_Published`` above) rather than namedtuple or
    dataclass, neither of which appears anywhere else in this file.
    """

    __slots__ = (
        "root_pis_ref", "dataset_g1_ref", "dataset_g2_ref", "pis", "entries",
        "expected_admission", "expected_admission_sha256", "pce_entries", "cas", "cas_sha256",
        "_locked",
    )

    def __init__(self, **kwargs):
        object.__setattr__(self, "_locked", False)
        for key, value in kwargs.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False):
            raise AttributeError("P4 dynamic admission reconstruction is frozen")
        object.__setattr__(self, name, value)


def _dynamic_p4_reconstruct_admission_chain(resolver, snapshot, admission_ref,
                                            run_id, logical_execution_id, captures):
    """Deterministically reconstruct one root-capture-admission -> cas -> pce chain.

    Reconstructed from the graph's own closed 12-reference set plus the live
    captures already gated by commit_p4_batch.

    ADR-0144 Decision point 6 / Phase 0 pin F5: extracted VERBATIM (zero
    behavior change -- same checks, same order, same exception messages)
    from ``_dynamic_p4_close_admission``'s prior inline body, from the
    ``graph_refs`` read through the per-entry live-projection loop's last
    check. Has exactly one call site in the whole design: inside
    ``_dynamic_p4_close_admission``, itself invoked at most once per
    ``commit_p4_batch`` call.
    """
    graph_refs = tuple(resolver._graph._records)

    def kind_matches(kind, role=None):
        matches = [
            _hs_parse_canonical(ref) for ref, _raw in graph_refs
            if _hs_parse_canonical(ref)["kind"] == kind
            and (role is None or _hs_parse_canonical(ref)["role"] == role)
        ]
        _hs_refuse(len(matches) == 1, "P4 dynamic closure requires one exact referenced artifact")
        return matches[0]

    def resolved(ref):
        raw = _P4_DYNAMIC_CHECKED_RESOLVE(resolver, snapshot, _hs_canonical_bytes(ref))
        return _hs_parse_canonical(raw)

    root_pis_ref = kind_matches("root-pis")
    dataset_g1_ref = kind_matches("dataset-capture-grant", role="G1")
    dataset_g2_ref = kind_matches("dataset-capture-grant", role="G2")

    expected_admission, expected_admission_sha256 = _p4_dynamic_root_capture_admission(
        root_pis_ref, dataset_g1_ref, dataset_g2_ref, run_id, logical_execution_id,
    )
    _hs_refuse(admission_ref["sha256"] == expected_admission_sha256,
               "P4 dynamic admission reference does not match closed graph chain")

    pis = resolved(root_pis_ref)
    entries = sorted(pis["entries"], key=lambda entry: entry["input_id"])
    _hs_refuse(
        len(entries) == 2
        and {entry["input_id"] for entry in entries} == {"raw_event_dataset", "source_roster"},
        "P4 dynamic PIS entry closure refused",
    )
    _hs_refuse(type(captures) is tuple and len(captures) == 2,
               "P4 dynamic capture cardinality refused")

    pce_entries = []
    for pis_entry, (_published, _frozen, port) in zip(entries, captures):
        pce = _p4_dynamic_planned_entry(
            pis_entry, port["consumer_document_sha256"], port["consumer_node"],
            port["consumer_input"], port["purpose"],
        )
        preimage = {"root_pis_entry_input_id": pis_entry["input_id"],
                    "root_pis_contract_sha256": pis_entry["contract_sha256"]}
        _hs_refuse(pce["planned_entry_sha256"] == _digest(_hs_canonical_bytes(preimage)),
                   "P4 dynamic planned entry binding refused")
        pce_entries.append(pce)

    # ADR-0144 Decision point 8: purpose-uniqueness, mirroring
    # _validate_capture_request's existing consumer-document-uniqueness
    # check (`len(documents) != 1`); this is the sole call site of
    # _p4_dynamic_capture_admission_set, so it runs at most once per commit.
    purposes = {port["purpose"] for _published, _frozen, port in captures}
    _hs_refuse(len(purposes) == 1, "P4 captures must share one purpose")
    consumer_document_sha256 = captures[0][2]["consumer_document_sha256"]
    purpose = captures[0][2]["purpose"]

    cas, cas_sha256 = _p4_dynamic_capture_admission_set(
        expected_admission_sha256, pce_entries, consumer_document_sha256, purpose,
    )
    _hs_refuse(len(cas["entries"]) == len(captures), "P4 dynamic live capture cardinality differs")
    for entry, (_published, frozen, port) in zip(cas["entries"], captures):
        _hs_refuse(
            entry["consumer_document_sha256"] == port["consumer_document_sha256"]
            and entry["consumer_node"] == port["consumer_node"]
            and entry["consumer_input"] == port["consumer_input"]
            and entry["purpose"] == port["purpose"],
            "P4 dynamic frozen descriptor projection differs",
        )
        _hs_refuse(_digest(_canonical_bytes(frozen.source)) == entry["consumer_document_sha256"],
                   "P4 dynamic frozen document digest differs")

    return _DynamicP4AdmissionReconstruction(
        root_pis_ref=root_pis_ref, dataset_g1_ref=dataset_g1_ref, dataset_g2_ref=dataset_g2_ref,
        pis=pis, entries=entries, expected_admission=expected_admission,
        expected_admission_sha256=expected_admission_sha256, pce_entries=pce_entries,
        cas=cas, cas_sha256=cas_sha256,
    )


def _dynamic_p4_close_admission(resolver, snapshot, admission_ref, live_projection):
    """Close one synthesized root-capture-admission -> cas -> pce chain.

    ADR-0143 Decision point 7: mirrors ``_p4_close_admission``'s find/get
    recursion shape, sized only to this resolver's own narrow chain.
    ``_p4_close_admission``/``_P4_CLOSE_ADMISSION`` are never called or
    edited from this path (Decision point 4, last sentence); this function
    shares no dependency-walk helper with the legacy grammar.

    Design note (GREEN-time scoping, evidence 0182): the resolver has no
    ``_records`` table (Decision point 1/2), so ``root-capture-admission``
    and ``cas`` are not stored artifacts resolved by reference -- they are
    deterministically reconstructed here from the graph's own closed
    12-reference set plus the live ``runtime``/``captures`` already gated by
    ``commit_p4_batch``. A caller-supplied ``admission_ref`` is admitted only
    if its digest matches this reconstruction exactly, which is what refuses
    an aliased/swapped/foreign reference (matrix row 6).

    ADR-0144 Decision point 6 (disclosed divergence from
    ``_FixedWormTrustedArtifactResolver.close_admission``'s ``None``-on-
    success convention): on success this now returns the frozen
    ``_DynamicP4AdmissionReconstruction`` the single internal derivation
    produced, instead of ``None``. Every failure path is unchanged -- every
    refusal this method already raised still raises identically.
    """
    _p4_snapshot_integrity(resolver, snapshot)
    authority, captures, runtime = live_projection
    _hs_refuse(type(authority) is _SyntheticP4CapturedAuthorizationAuthority
               and authority._p4_resolver is resolver,
               "P4 dynamic admission authority identity refused")
    _p4_dynamic_reference_bytes(admission_ref)
    _hs_refuse(
        admission_ref["kind"] == "root-capture-admission"
        and admission_ref["role"] == "study-lifecycle"
        and admission_ref["schema"] == "dskit.root-capture-admission/v1",
        "P4 dynamic admission kind/role/schema refused",
    )

    run_id = runtime["consumer_run_identity"]
    logical_execution_id = run_id
    reconstruction = _dynamic_p4_reconstruct_admission_chain(
        resolver, snapshot, admission_ref, run_id, logical_execution_id, captures,
    )

    _p4_snapshot_integrity(resolver, snapshot)
    return reconstruction


_P4_DYNAMIC_CLOSE_ADMISSION = _dynamic_p4_close_admission
_P4_DYNAMIC_CLOCK_NOW_MS = _DynamicP4VerificationClock.now_ms
_P4_DYNAMIC_REVOCATIONS_UNREVOKED = _DynamicP4VerificationRevocations.is_unrevoked


class _DynamicCapturedAuthorizationContract(_Opaque):
    """Pure private batch preparation/verification for the dynamic domain.

    ADR-0144 Decision point 5: a new sibling class beside
    ``_FixedCapturedAuthorizationContract``, mirroring its construction shape
    exactly (stateless, no token, no ``__init__`` override, one module-level
    singleton). Reuses the SAME shared ``_FixedP4Signer``/``_P4_SIGN``/
    ``_P4_SIGNER``/``_P4_BATCH_USES``/``_PreparedP4Batch``/``_P4_PREPARED``
    signing infrastructure the fixed contract already uses -- no new signer,
    no new key material, no new signed-schema names. Its three methods take
    two additional positional parameters versus their fixed-contract
    namesakes (``captures``, ``reconstruction``) because the dynamic chain's
    cas/pce content depends on the live captures being authorized and has
    nothing pre-stored to read.
    """

    def _prepare(self, resolver, admission_ref, streams, runtime, nonces, captures, reconstruction):
        # Phase 0 pin F2: explicit type/shape check of `reconstruction`
        # before use (defense in depth against a caller or future refactor
        # invoking _prepare with `reconstruction` still None or wrong shape).
        if type(reconstruction) is not _DynamicP4AdmissionReconstruction:
            raise TypeError("exact P4 dynamic admission reconstruction is required")
        # Unlike the fixed resolver's snapshot() (an idempotent cached
        # singleton), the dynamic resolver's snapshot() mints a brand-new
        # token object and re-runs a full graph proof on every call. Reading
        # the already-current cached `_snapshot` here matches exactly what
        # commit_p4_batch's own locked critical section already established
        # (and what the reconstruction was itself checked against) instead
        # of triggering a second, redundant re-derivation.
        _p4_snapshot_integrity(resolver, resolver._snapshot)
        snapshot = resolver._snapshot
        admission = reconstruction.expected_admission
        cas = reconstruction.cas
        subject_ref = {"kind": "action"}
        identity = {
            "execution_authority_ref": {
                "kind": "action",
                "action_execution_admission_sha256": admission_ref["sha256"],
            },
            "logical_execution_id": admission["logical_execution_id"],
            "run_id": admission["run_id"],
        }
        # Decision point 7 substitution table: the fixed contract's
        # pea/ces/bvp/cas-resolved fields are replaced by three digests
        # already owned by this ADR's own closed graph, plus the
        # reconstructed cas's own digest for capture_admission_set_sha256.
        common = {
            "study_id": admission["study_id"], **identity, "subject_ref": subject_ref,
            "plan_evaluation_authorization_sha256": reconstruction.root_pis_ref["sha256"],
            "capture_expectation_set_sha256": reconstruction.dataset_g1_ref["sha256"],
            "broker_verified_plan_sha256": reconstruction.dataset_g2_ref["sha256"],
            "capture_admission_set_sha256": cas["capture_admission_set_sha256"],
        }
        tuple_refs = [dict(admission_ref), dict(reconstruction.root_pis_ref),
                      dict(reconstruction.dataset_g1_ref), dict(reconstruction.dataset_g2_ref)]
        _hs_refuse(len(tuple_refs) == 4)
        pce_refs = [{"kind": "planned-capture-entry", "role": "security-broker",
            "schema": "dskit.planned-capture-entry/v1", "sha256": entry["planned_entry_sha256"]}
            for entry in cas["entries"]]
        bases, ports, receipts, entries = [], [], [], []

        def signed(kind, payload, refs):
            schema, self_field, usage = _P4_BATCH_USES[kind]
            refs = sorted(refs, key=lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
            _hs_refuse(len({_hs_canonical_bytes(ref) for ref in refs}) == len(refs))
            basis = _P4_SIGN(_P4_SIGNER, {"schema": "dskit.issuance-basis/v1", "kind": kind,
                "study_id": admission["study_id"], "refs": refs}, "issuance_basis_sha256", usage)
            bases.append(basis)
            return _P4_SIGN(_P4_SIGNER, dict(payload, schema=schema, issuance_basis_sha256=basis["issuance_basis_sha256"]), self_field, usage)

        for index, pce in enumerate(cas["entries"]):
            refs = tuple_refs + [pce_refs[index]]
            port = signed("captured-port", dict(common, planned_entry_sha256=pce["planned_entry_sha256"]), refs)
            publication = pce["published_input"]
            genesis = {"schema": "dskit.lifecycle-captured-receipt-genesis/v1", "stream_id": streams[index],
                "predecessor_publication_receipt_schema": publication["publication_receipt_schema"],
                "predecessor_publication_receipt_sha256": publication["publication_receipt_sha256"]}
            receipt = signed("captured-receipt", dict(common, stream_id=streams[index], sequence=1,
                previous_lifecycle_captured_receipt_sha256=_digest(_hs_canonical_bytes(genesis)),
                consumer_kind="action", consumer_id=admission["run_id"],
                predecessor_publication_receipt_schema=publication["publication_receipt_schema"],
                predecessor_publication_receipt_sha256=publication["publication_receipt_sha256"],
                planned_capture_set_sha256=admission_ref["sha256"], planned_entry_sha256=pce["planned_entry_sha256"],
                captured_port_authorization_sha256=port["captured_port_authorization_sha256"],
                actor_runtime_sha256=runtime["runtime_sha256"], transition_nonce=nonces[index]), refs)
            ports.append(port)
            receipts.append(receipt)
            entries.append(dict(identity, planned_entry_sha256=pce["planned_entry_sha256"],
                captured_port_authorization_sha256=port["captured_port_authorization_sha256"],
                lifecycle_captured_receipt_sha256=receipt["lifecycle_captured_receipt_sha256"]))
        captured_set = signed("captured-set", dict(common, planned_capture_set_sha256=admission_ref["sha256"],
                                                  entries=entries), tuple_refs + pce_refs)
        # No replay/evidence branch: subject_ref.kind is fixed to "action" by
        # ADR-0143 Decision point 7, so the fixed contract's `if replay:`
        # branch never applies here and is omitted entirely (Decision point 5).
        raw = _hs_canonical_bytes({"ports": ports, "receipts": receipts, "set": captured_set, "replay": None, "bases": bases})
        binding = (resolver, snapshot, _hs_canonical_bytes(admission_ref), streams, _hs_canonical_bytes(runtime), nonces,
                   captures, reconstruction)
        prepared = object.__new__(_PreparedP4Batch)
        object.__setattr__(prepared, "_raw", raw)
        object.__setattr__(prepared, "_binding", binding)
        _P4_PREPARED[prepared] = (raw, binding)
        return prepared

    def _verify(self, prepared, resolver, admission_ref, streams, runtime, nonces, captures, reconstruction):
        """Require an exact registered candidate from this snapshot and request."""
        _hs_refuse(type(prepared) is _PreparedP4Batch)
        issued = _P4_PREPARED.get(prepared)
        _hs_refuse(issued is not None and prepared._raw is issued[0] and prepared._binding is issued[1],
                   "P4 prepared candidate integrity refused")
        # The binding tuple additionally includes `captures`/`reconstruction`
        # (Phase 0 pin, matrix class_function_inventory row for _verify): a
        # _verify call cannot be satisfied by a _PreparedP4Batch built
        # against a DIFFERENT reconstruction/captures pair than this one.
        # Reads the cached `_snapshot` rather than calling resolver.snapshot()
        # again, for the same non-idempotent-token reason as _prepare above.
        _p4_snapshot_integrity(resolver, resolver._snapshot)
        _hs_refuse(prepared._binding == (resolver, resolver._snapshot, _hs_canonical_bytes(admission_ref),
                   streams, _hs_canonical_bytes(runtime), nonces, captures, reconstruction),
                   "P4 prepared candidate binding refused")
        return _P4_DYNAMIC_VERIFY_BYTES(self, prepared._raw, resolver, admission_ref, streams, runtime, nonces, captures, reconstruction)

    def _verify_bytes(self, raw, resolver, admission_ref, streams, runtime, nonces, captures, reconstruction):
        """Reparse every byte and compare the full deterministic closed projection."""
        parsed = _hs_parse_canonical(raw)
        _hs_refuse(set(parsed) == {"ports", "receipts", "set", "replay", "bases"})
        expected = _P4_DYNAMIC_PREPARE(self, resolver, admission_ref, streams, runtime, nonces, captures, reconstruction)
        _hs_refuse(raw == expected._raw, "P4 complete signed batch adjacency refused")
        for kind, slot in (("captured-port", "ports"), ("captured-receipt", "receipts"),
                           ("captured-set", "set"), ("replay-capture-evidence", "replay")):
            values = parsed[slot] if slot in ("ports", "receipts") else [parsed[slot]]
            for value in values:
                if value is None:
                    continue
                _schema, self_field, usage = _P4_BATCH_USES[kind]
                _p4_verify_local_signed(value, self_field, "security-broker", usage)
                bases = [basis for basis in parsed["bases"] if basis["issuance_basis_sha256"] == value["issuance_basis_sha256"]]
                _hs_refuse(len(bases) == 1 and bases[0]["kind"] == kind)
                _p4_verify_local_signed(bases[0], "issuance_basis_sha256", "security-broker", usage)
        return parsed


_P4_DYNAMIC_CONTRACT = _DynamicCapturedAuthorizationContract()
_P4_DYNAMIC_PREPARE = _DynamicCapturedAuthorizationContract._prepare
_P4_DYNAMIC_VERIFY_BATCH = _DynamicCapturedAuthorizationContract._verify
_P4_DYNAMIC_VERIFY_BYTES = _DynamicCapturedAuthorizationContract._verify_bytes


def _p4_dynamic_authority_quarantine(reserve, signed_id):
    """Quarantine a committed-but-not-yet-returned dynamic-p4-authority row.

    Matrix row 9 (evidence 0181): not specified by the ADR text; mirrors
    ``_SyntheticRootPisIssuer._quarantine``'s shape for a different kind.
    """
    connection = reserve._connection
    try:
        reserve._check()
        connection.execute("BEGIN IMMEDIATE")
        reserve._check()
        generation, _revoked, _snapshot = reserve._snapshot()
        row = connection.execute(
            "SELECT state FROM reserve_uses WHERE kind='dynamic-p4-authority' AND signed_id=?",
            (signed_id,),
        ).fetchone()
        if row is not None and row[0] != "QUARANTINED":
            connection.execute(
                "UPDATE reserve_uses SET state='QUARANTINED' "
                "WHERE kind='dynamic-p4-authority' AND signed_id=?",
                (signed_id,),
            )
            connection.execute(
                "INSERT INTO reserve_audit (kind,signed_id,old_state,new_state,generation) "
                "VALUES (?,?,?,?,?)",
                ("dynamic-p4-authority", signed_id, row[0], "QUARANTINED", generation),
            )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _development_dynamic_p4_broker(issuer):
    """Construct the one-shot dynamic synthetic P4 issuer for one retained graph.

    Parameters
    ----------
    issuer : _SyntheticRootPisIssuer
        A successful, retained ADR-0141 issuer (``issuer._retained`` set).

    Returns
    -------
    CapturedAuthorizationAuthority
        A second, noncopyable synthetic capability instance with ordinary v1
        lifecycle methods, bound to a new ``_DynamicP4TrustedArtifactResolver``.

    Raises
    ------
    TypeError
        ``issuer`` is not a successful retained root-PIS issuer.
    ValueError
        The retained root-PIS row is not yet ISSUED, or a dynamic authority
        was already constructed for this exact retained graph (ADR-0143
        Decision point 9, Phase 0 pins F3/matrix rows 4/5/9/11: the one-shot
        reserve spend is ordered strictly BEFORE resolver/authority
        construction, so a lost race refuses cleanly with no authority ever
        built from the losing attempt).
    """
    _hs_refuse(type(issuer) is _SyntheticRootPisIssuer and issuer._retained is not None,
               "retained root-PIS issuer required")
    graph = issuer.graph()
    publisher = issuer._publisher
    reserve = publisher._reserve
    connection = reserve._connection
    root_pis_signed_id = issuer._retained[5][2]
    references = tuple(_hs_parse_canonical(ref) for ref, _raw in graph._records)

    reserve._check()
    signed_id = None
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        reserve._check()
        generation, _revoked, snapshot = reserve._snapshot()
        row = connection.execute(
            "SELECT signed_id,g1_sha256,g2_sha256,intent_sha256,state "
            "FROM reserve_uses WHERE kind='root-pis' AND signed_id=?",
            (root_pis_signed_id,),
        ).fetchone()
        _hs_refuse(row is not None and row[4] == "ISSUED",
                   "dynamic P4 authority requires an ISSUED root-PIS row")
        source = {
            "schema": "dskit.dynamic-p4-authority-source/v1",
            "root_pis_signed_id": row[0],
            "root_pis_intent_sha256": row[3],
        }
        signed_id = _digest(_hs_canonical_bytes(source))
        intent_payload = {
            "schema_version": "dskit.dynamic-p4-authority-construction-intent/v1",
            "root_pis_signed_id": root_pis_signed_id,
            "graph_references": list(references),
        }
        intent_sha256 = _digest(_hs_canonical_bytes(intent_payload))
        connection.execute(
            "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
            ("dynamic-p4-authority", signed_id, _digest(signed_id.encode("ascii")),
             row[1], row[2], snapshot, intent_sha256, "RESERVED"),
        )
        connection.execute(
            "INSERT INTO reserve_audit (kind,signed_id,old_state,new_state,generation) "
            "VALUES (?,?,NULL,?,?)",
            ("dynamic-p4-authority", signed_id, "RESERVED", generation),
        )
        connection.execute(
            "UPDATE reserve_uses SET state='ISSUED' "
            "WHERE kind='dynamic-p4-authority' AND signed_id=? AND state='RESERVED'",
            (signed_id,),
        )
        connection.execute(
            "INSERT INTO reserve_audit (kind,signed_id,old_state,new_state,generation) "
            "VALUES (?,?,?,?,?)",
            ("dynamic-p4-authority", signed_id, "RESERVED", "ISSUED", generation),
        )
        connection.execute("COMMIT")
        committed = True
    except sqlite3.IntegrityError as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise ValueError("dynamic P4 authority already constructed for this graph") from exc
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    try:
        resolver_token = object()
        _P4_PENDING_DYNAMIC_RESOLVER_TOKENS.add(resolver_token)
        try:
            resolver = _DynamicP4TrustedArtifactResolver(resolver_token, graph)
        finally:
            _P4_PENDING_DYNAMIC_RESOLVER_TOKENS.discard(resolver_token)
        resolver.snapshot()
        # ADR-0143 Decision point 3 (literal text): a second instance of the
        # existing, UNMODIFIED _SyntheticP4CapturedAuthorizationAuthority is
        # constructed through its existing __init__(token, resolver) and the
        # SAME _P4_PENDING_TOKENS one-shot factory-token discipline -- that
        # class's __init__ body hardcodes the `_P4_PENDING_TOKENS` name and
        # is not edited, so a separate `_P4_PENDING_DYNAMIC_TOKENS` set
        # (as evidence 0181's class_function_inventory suggested) cannot
        # actually satisfy it; the shared legacy set is used here instead,
        # which is what keeps the authority class byte-identical.
        _P4_PENDING_TOKENS.add(broker_token := object())
        try:
            authority = _SyntheticP4CapturedAuthorizationAuthority(broker_token, resolver)
        finally:
            _P4_PENDING_TOKENS.discard(broker_token)
        _P4_ISSUED[authority] = (resolver, graph)
        return authority
    except Exception:
        if committed:
            _p4_dynamic_authority_quarantine(reserve, signed_id)
        raise
