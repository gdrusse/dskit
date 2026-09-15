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
from abc import ABC, abstractmethod
from functools import wraps
from threading import RLock
from types import MappingProxyType
from weakref import WeakKeyDictionary

__all__ = [
    "CapturedAuthorizationAuthority",
    "CapturedAuthorizationRecord",
    "CapturedBindings",
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
    "ReleaseKeyring",
    "TerminalArtifactVerifier",
    "TrustedClock",
    "TrustedRuntimeVerifier",
    "VerifiedCapture",
    "VerifiedExternalArtifactAnchor",
]

_MAKE = object()
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
            return method(view, *args, **kwargs)
    return locked


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
        if relative_path not in self._members:
            raise ValueError("missing captured member")
        return self._members[relative_path]


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
        if self._read:
            raise ValueError("member bytes already consumed")
        self._read = True
        return self._bytes

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

    __slots__ = ("_value", "_audit")

    def __init__(self, token, value, audit):
        if token is not _MAKE:
            raise TypeError("CapturedJsonArtifact is opaque")
        self._value = value
        self._audit = audit

    @property
    def value(self):
        """Frozen JSON object decoded from retained bytes."""
        return self._value

    @property
    def audit(self):
        """Frozen audit mapping with no filesystem fields."""
        return self._audit


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

    __slots__ = ("_artifact", "_audit")

    def __init__(self, token, artifact, audit):
        if token is not _MAKE:
            raise TypeError("CapturedLifecyclePort is opaque")
        self._artifact = artifact
        self._audit = audit

    @property
    def artifact(self):
        """The captured JSON artifact for this port."""
        return self._artifact

    @property
    def audit(self):
        """Frozen port audit, including ``consumer_port``."""
        return self._audit


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
        self._used.add(input_name)
        return self._broker._consume_binding(self, input_name)


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
            sealed,
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
        if purpose != published.descriptor["purpose"]:
            raise ValueError("purpose mismatch")
        return dict(published.descriptor)

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
        if descriptor.get("root_ref") != published.descriptor["root_ref"]:
            raise ValueError("published root mismatch")
        if descriptor.get("snapshot_version") != published.descriptor["snapshot_version"]:
            raise ValueError("published snapshot mismatch")
        if document_sha256 != published.descriptor["document_sha256"]:
            raise ValueError("published document_sha256 mismatch")
        if descriptor.get("node") != published.descriptor["node"]:
            raise ValueError("published node mismatch")
        if descriptor.get("output") != published.descriptor["output"]:
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
            published,
        )
        return frozen

    def derive_consumer_port(self, frozen):
        frozen = self._require_frozen(frozen)
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
        intern_pub = self._load_interned(
            self._freeze_published,
            self._freeze_intern,
            id(frozen),
            "document port does not match published root",
        )
        if intern_pub is not published:
            raise ValueError("document port does not match published root")
        source_desc = self._descriptor_from(
            frozen.source,
            frozen.consumer_node,
            frozen.consumer_input,
        )
        if (
            source_desc.get("root_ref") != published.descriptor.get("root_ref")
            or source_desc.get("snapshot_version")
            != published.descriptor.get("snapshot_version")
            or source_desc.get("document_sha256")
            != published.descriptor.get("document_sha256")
        ):
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
        sealed = self._sealed_for(published)
        pin = self._stream_pin[stream_id]
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
            _LIFECYCLE_VIEWS[handles[path]] = (self, str(stream_id))
        verified = VerifiedCapture(
            _MAKE,
            handles,
            published,
            dict(captured.port),
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
        _LIFECYCLE_VIEWS[verified] = (self, str(stream_id))
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
        intern_pub = self._load_interned(
            self._freeze_published,
            self._freeze_intern,
            id(frozen),
            "plan document does not match captured freeze",
        )
        if intern_pub is not published:
            raise ValueError("plan document does not match captured freeze")
        sealed = self._sealed_for(published)
        subject = self._stream_pin[stream_id]
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
            MappingProxyType({"consumer_port": expected_port}),
        )
        ports = {frozen.consumer_input: port}
        bindings = CapturedBindings(_MAKE, ports, self, session, stream_id)
        _LIFECYCLE_VIEWS[bindings] = (self, str(stream_id))
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
        subject = self._publication_subject(stream_id)
        if subject is None:
            raise ValueError("captured bindings are required")
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
        stream_id = self._stream_for_published(
            published,
            "PUBLISHED token is required",
            require_intern_match=False,
        )
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

    def _sealed_for(self, published):
        if not isinstance(published, _Published):
            raise ValueError("PUBLISHED token is required")
        sealed = self._load_interned(
            self._publish_sealed,
            self._publish_sealed_intern,
            id(published),
            "PUBLISHED token is required",
        )
        return sealed

    def _require_head(self, stream_id, event):
        self._p4_ledger._require_unclaimed(stream_id)
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

    def _stream_for_published(self, published, message, require_intern_match=True):
        intern_sid = self._load_interned(
            self._publish_stream,
            self._publish_stream_intern,
            id(published),
            message,
        )
        descriptor = published.descriptor
        found = None
        for stream_id, rows in self._streams.items():
            for row in rows:
                if row.get("event") != "PUBLISHED":
                    continue
                if (
                    row["root_ref"] == descriptor.get("root_ref")
                    and row["snapshot_version"] == descriptor.get("snapshot_version")
                    and row["producer_document_sha256"]
                    == descriptor.get("document_sha256")
                ):
                    if found is not None and found != stream_id:
                        raise ValueError(message)
                    found = stream_id
        if found is None:
            raise ValueError(message)
        if str(intern_sid) != str(found) and require_intern_match:
            raise ValueError(message)
        return found

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
        for rows in self._streams.values():
            if not rows:
                continue
            head = rows[-1]
            if head["event"] != "PUBLISHED":
                continue
            if (
                head["root_ref"] == descriptor.get("root_ref")
                and head["snapshot_version"] == descriptor.get("snapshot_version")
                and head["producer_document_sha256"] == descriptor.get("document_sha256")
            ):
                # Recover the live published token from stream identity.
                break
        else:
            raise ValueError("published root is required")
        # Search live objects by matching stream receipts already recorded.
        # Freeze is called with the live published token's descriptor; look up
        # via producer identities on the last PUBLISHED receipt.
        return self._live_published(descriptor)

    def _live_published(self, descriptor):
        for published in getattr(self, "_live", ()):
            if published.descriptor == descriptor:
                return published
        # Fall back: the caller always freezes from descriptor() of a live token
        # in these tests, so remember published tokens as they are created.
        for published in self._published_tokens:
            if published.descriptor["root_ref"] == descriptor["root_ref"] and published.descriptor[
                "document_sha256"
            ] == descriptor["document_sha256"]:
                return published
        raise ValueError("published root is required")

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
_P4_PENDING_TOKENS = set()
_P4_PENDING_RESOLVER_TOKENS = set()
_P4_ISSUED = WeakKeyDictionary()


def _p4_reference_bytes(value):
    """Validate the closed selected-admission reference before canonicalizing."""
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
            intern = self._load_interned(
                self._freeze_published, self._freeze_intern, id(frozen),
                "P4 frozen publication binding is required",
            )
            if intern is not published:
                raise ValueError("P4 frozen publication binding differs")
            stream = self._stream_for_published(published, "P4 live publication is required")
            if any(entry[1] == stream for entry in self._p4_ledger._legacy_captures()):
                raise ValueError("P4 stream was already captured by the same legacy ledger")
            self._recover(stream)
            self._require_head(stream, "PUBLISHED")
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
_P4_REQUEST_CHECK = _SyntheticP4CapturedAuthorizationAuthority._validate_capture_request
_P4_RESOLVER_LOOKUP = _FixedWormTrustedArtifactResolver._lookup


def _p4_require_issued_authority(authority):
    """Recheck exact broker identity and dispatch without spending authority."""
    if type(authority) is not _SyntheticP4CapturedAuthorizationAuthority:
        raise TypeError("exact broker-issued P4 capability is required")
    issued = _P4_ISSUED.get(authority)
    if (
        issued is None
        or authority._p4_resolver is not issued[0]
        or type(issued[0]) is not _FixedWormTrustedArtifactResolver
        or issued[0]._records is not issued[1]
        or CapturedAuthorizationAuthority.authorize_capture_set is not _P4_BASE_DISPATCH
        or type(authority).authorize_capture_set is not _P4_FINAL_DISPATCH
        or type(authority)._validate_capture_request is not _P4_REQUEST_CHECK
        or type(issued[0])._lookup is not _P4_RESOLVER_LOOKUP
        or "authorize_capture_set" in authority.__dict__
    ):
        raise TypeError("exact broker-issued P4 capability is required")


_P4_ISSUED_CHECK = _p4_require_issued_authority


def _p4_checked_dispatch(authority, captures, admission_ref, **runtime):
    """Check broker identity and frozen dependencies before all P4 validation."""
    if _p4_require_issued_authority is not _P4_ISSUED_CHECK:
        raise TypeError("P4 authority identity dispatch integrity refused")
    _P4_ISSUED_CHECK(authority)
    _p4_reference_bytes(admission_ref)
    ledger = _LIFECYCLE_LEDGERS.get(authority)
    if ledger is None or authority._p4_ledger is not ledger or _P4_COMMIT is not _LifecycleAuthorizationLedger.commit_p4_batch:
        raise TypeError("P4 same-domain ledger required")
    return _P4_COMMIT(ledger, captures, admission_ref, runtime)


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
     "03e4a325a9adde7fec26ee1835c6cdef9f6b84eea226e7fedfebb351a21a72b4",
     "48afcac1de36bb1eb45475a66ec4e61563cf66fe4cc10f0e7c8f2d10ddca7a2389"
     "5fe5238f19b7a008353dcc610d2956c0decea8bb1ad1c6cae5c8c2393b6b01"),
    ("G2-dataset-authorization",
     "871ce2c3f83d488b08b3df46b5dac40c7693239904d4e076c08c3d857283d645",
     "bd6a813d14a6f461ab54d894bd62cc962a4759184b4f812cb46a567d4a7627376c"
     "c8c27a37fcd5d4caf8c21b48438a91ad11bbd314d1634da4199affd6766100"),
    ("fixed-owner-policy",
     "456884347696ca39296e2442b21268838ec5b9e728a0c3ca28bf0396a661753e",
     "5939a9f5b4709563c0be0695083e5abbce3f41f482d2d2b27a984203b733855be"
     "34792e3d82b9f26ecc1cacf0fc330eb980aaa455434a1846c14fc94c427c904"),
)
_P4_APPROVED_SCOPE_PROJECTIONS = (
    "e61b568822008c9e9fcbe4f0149d39c3ec2b1196dab7d72caf593b1a848b811f",
    "923fa789dc1051fd8576e6af8588d97731f890f6d1a0f1404494b345a4c566dc",
    "3d125ac8581f1a06b6996697b8277a30481025332bc2df43087ca1d159b3e965",
    "53b0cc17b04c5922e25d6a9629014faf2243ed718b5480cde7a00bac12c9f0b5",
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
    """Check the construction-owned graph before any resolver or proof action."""
    _hs_refuse(_p4_snapshot_integrity is _P4_SNAPSHOT_INTEGRITY and
               _p4_fixed_integrity is _P4_FIXED_INTEGRITY, "P4 fixed dispatch integrity refused")
    _P4_FIXED_INTEGRITY()
    _hs_refuse(type(resolver) is _FixedWormTrustedArtifactResolver, "P4 snapshot integrity refused")
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
        descriptor = published.descriptor
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

    The record has no bytes, mapping, member, lookup or reconstruction API.
    """

    __slots__ = ("__weakref__",)

    def __new__(cls, *args, **kwargs):
        """Refuse construction from public or reconstructed data."""
        raise TypeError("CapturedAuthorizationRecord is broker-issued")

    def __init_subclass__(cls, **kwargs):
        """Refuse subtype substitution for the issued record."""
        raise TypeError("CapturedAuthorizationRecord is final")


_LIFECYCLE_LEDGERS = WeakKeyDictionary()
_LIFECYCLE_VIEWS = WeakKeyDictionary()
_P4_LEDGER_PINS = WeakKeyDictionary()
_P4_RECORDS = WeakKeyDictionary()
_P4_PREPARED = WeakKeyDictionary()
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


class _LifecycleAuthorizationLedger(_Opaque):
    """One process-local lock and immutable admission/batch/session record domain."""

    def __init__(self, authority):
        self._authority = authority
        self._lock = RLock()
        self._root = ()
        self._reserving = ()
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
            projected.append([id(published), id(frozen), port, published.descriptor, _digest(_canonical_bytes(frozen.source))])
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
            admission_bytes = _p4_reference_bytes(admission_ref)
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
            _P4_SNAPSHOT_INTEGRITY(resolver, resolver._snapshot)
            fingerprint = self._request(captures, admission_ref, runtime)
            admission_bytes = _p4_reference_bytes(admission_ref)
            for entry in self._p4_entries():
                if entry[1] == admission_bytes:
                    _hs_refuse(entry[2] == fingerprint, "P4 committed admission request conflict")
                    return self.resolve_p4(entry[0], admission_ref)
            self._fault("preflight")
            nonces = runtime["transition_nonces"]
            checked_runtime = {key: value for key, value in runtime.items() if key != "transition_nonces"}
            _P4_REQUEST_CHECK(authority, captures, checked_runtime, nonces)
            _hs_refuse(not any(self._nonce_used(nonce) for nonce in nonces), "P4 nonce already committed")
            _hs_refuse(_P4_CLOSE_ADMISSION(resolver, resolver._snapshot, admission_ref, (authority, captures, checked_runtime)) is None)
            streams = tuple(authority._stream_for_published(item[0], "P4 publication required") for item in captures)
            raw = _P4_PREPARE(_P4_CONTRACT, resolver, admission_ref, streams, checked_runtime, nonces)
            self._fault("prepared")
            self._reserving = streams
            try:
                self._fault("reserved")
                batch = _P4_VERIFY_BATCH(_P4_CONTRACT, raw, resolver, admission_ref, streams, checked_runtime, nonces)
                self._fault("verified")
                self._check()
                _P4_REQUEST_CHECK(authority, captures, checked_runtime, nonces)
                _hs_refuse(_P4_CLOSE_ADMISSION(resolver, resolver._snapshot, admission_ref, (authority, captures, checked_runtime)) is None)
                _hs_refuse(self._request(captures, admission_ref, runtime) == fingerprint)
                captured_set = batch["set"]
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
                _hs_refuse(_P4_CLOSE_ADMISSION(resolver, resolver._snapshot, admission_ref,
                           (authority, captures, checked_runtime)) is None)
                _hs_refuse(self._request(captures, admission_ref, runtime) == fingerprint)
                # Sole logical publication: consumption, artifacts, claims and
                # session-start are inseparable fields in this immutable root.
                self._root, self._pin = root, pin
                _P4_LEDGER_PINS[self] = pin
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
    "_prepare_legacy_capture", "_prepare_receipt", "_store_interned",
))
_P4_LEDGER_METHODS = tuple((name, getattr(_LifecycleAuthorizationLedger, name)) for name in (
    "_check", "_fault", "_legacy_gate", "_require_unclaimed", "_nonce_used", "_request", "resolve_p4", "commit_p4_batch",
    "_p4_entries", "_legacy_captures", "commit_legacy_capture",
))
