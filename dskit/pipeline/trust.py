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
from types import MappingProxyType
from weakref import WeakKeyDictionary

__all__ = [
    "CapturedAuthorizationAuthority",
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
    "TrustedClock",
    "TrustedRuntimeVerifier",
    "VerifiedCapture",
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

    This foundation validates requests but always refuses authorization. Exact
    artifact verification and atomic admission/batch publication must exist
    before this doorway can issue any record or launch session. Ordinary v1
    lifecycle authorities retain their original abstract method set.

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
        """Validate a held-authority request without issuing a capture.

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
            Reserved for the future verified opaque record and launch session;
            this foundation returns no authorization result.

        Raises
        ------
        TypeError
            The receiver is not an exact broker-issued capability or an input
            has a forbidden type.
        ValueError
            Request identity is invalid, admission is missing, or complete
            artifact closure and atomic issuance are unavailable.
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

    __slots__ = ("_members", "_published", "_port", "_frozen", "_session", "_retained", "_locked")

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

    __slots__ = ("_bytes", "_read")

    def __init__(self, token, data):
        if token is not _MAKE:
            raise TypeError("CapturedMemberHandle is opaque")
        self._bytes = data
        self._read = False

    def read_bytes(self):
        """Return the retained bytes exactly once."""
        if self._read:
            raise ValueError("member bytes already consumed")
        self._read = True
        return self._bytes

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

    __slots__ = ("_ports", "_used", "_broker", "_session", "_stream_id", "_locked")

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

    def end_session(self, session):
        self._require_session(session)
        object.__setattr__(session, "_ended", True)

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
        self._remember_session(session)
        self._session_events.append(("start", consumer_run_identity, id(session)))
        captured = _Captured(published, frozen, dict(expected), session)
        self._append_receipt(
            pin,
            "CAPTURED",
            session,
            transition_nonce,
            extra={"consumer_captured_port": dict(expected)},
        )
        self._captured_streams[id(captured)] = stream_id
        self._store_interned(
            self._session_streams,
            self._session_stream_intern,
            id(session),
            (id(session), str(stream_id)),
        )
        self._store_interned(
            self._capture_bind,
            self._capture_bind_intern,
            id(captured),
            (published, frozen, session, stream_id),
        )
        return captured, session

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
        return verified

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

    def _append_receipt(self, prepared, event, session, transition_nonce, extra):
        if transition_nonce in self._nonces:
            raise ValueError("duplicate transition nonce")
        self._nonces.add(transition_nonce)
        stream_id = prepared.stream_id
        rows = self._streams.setdefault(stream_id, [])
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
            "actor_runtime": (
                self._captured_actor_runtime(rows)
                if event == "CONSUMED"
                else self._actor_runtime(session)
            ),
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
        rows.append(body)
        encoded = tuple(self._receipt_store.get(stream_id, ())) + (_canonical_bytes(body),)
        self._receipt_store[stream_id] = encoded
        self._note_len(stream_id, len(encoded))
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
    """Hold copied hostile admission bytes; presence confers no trust."""

    __slots__ = ("_records",)

    def __init__(self, fixture_facts):
        records = []
        facts = {"artifacts": []} if fixture_facts is None else _p4_copy_facts(fixture_facts)
        if type(facts) is not dict or set(facts) != {"artifacts"}:
            raise ValueError("P4 fixture facts must contain only artifacts")
        if type(facts["artifacts"]) is not list:
            raise TypeError("P4 fixture artifacts must be an exact list")
        for entry in facts["artifacts"]:
            if type(entry) is not dict or set(entry) != {"ref", "bytes"}:
                raise ValueError("P4 fixture artifact requires only ref and bytes")
            key = _p4_reference_bytes(entry["ref"])
            if type(entry["bytes"]) is not bytes:
                raise TypeError("P4 fixture artifact must contain exact bytes")
            parsed, raw = _load_canonical_json(entry["bytes"])
            if type(parsed) is not dict or parsed.get("schema") != entry["ref"]["schema"]:
                raise ValueError("P4 fixture artifact schema differs from reference")
            if any(previous == key for previous, _raw in records):
                raise ValueError("duplicate P4 fixture reference")
            records.append((key, raw))
        object.__setattr__(self, "_records", tuple(records))

    def __setattr__(self, name, value):
        raise AttributeError("P4 fixture resolver is frozen")

    def _lookup(self, key):
        """Return copied bytes only for the exact held reference."""
        for reference, raw in self._records:
            if reference == key:
                return raw
        raise ValueError("missing admission in fixed P4 fixture snapshot")


class _SyntheticP4CapturedAuthorizationAuthority(_DevelopmentBroker,
                                                 CapturedAuthorizationAuthority,
                                                 _Opaque):
    """Fixed nondeployment foundation; every P4 issuance remains refused."""

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
        """Run the checked base doorway; this foundation never issues."""
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


def _p4_checked_dispatch(authority, captures, admission_ref, **runtime):
    """Check broker identity and frozen dependencies before all P4 validation."""
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
    key = _p4_reference_bytes(admission_ref)
    nonces = runtime.pop("transition_nonces")
    _P4_REQUEST_CHECK(authority, captures, runtime, nonces)
    _P4_RESOLVER_LOOKUP(issued[0], key)
    # A held byte match is data only. No record, nonce, admission, receipt,
    # session, or member capability is created by this foundation increment.
    raise ValueError("P4 complete artifact closure and atomic issuance are unavailable")


def _development_p4_broker(*, fixture_facts=None):
    """Construct the fixed synthetic P4 foundation without selectable trust.

    Parameters
    ----------
    fixture_facts : dict or None
        Hostile data only: ``artifacts`` is a list of exact ``ref``/``bytes``
        entries. References currently name action or replay admissions. Copied
        canonical bytes confer no authority, and matching fixtures still refuse
        until complete recursive verification and atomic issuance are supplied.

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
    resolver = _FixedWormTrustedArtifactResolver(fixture_facts)
    token = object()
    _P4_PENDING_TOKENS.add(token)
    try:
        authority = _SyntheticP4CapturedAuthorizationAuthority(token, resolver)
    finally:
        _P4_PENDING_TOKENS.discard(token)
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
            envelope = values[slot]
            role, usage = _HS_ROLE_USE[role_slot]
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
                envelope, _HS_SELF_FIELDS[envelope_name], signed=True
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
