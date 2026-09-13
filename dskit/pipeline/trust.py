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
import os
from abc import ABC, abstractmethod
from types import MappingProxyType

__all__ = [
    "CapturedBindings",
    "CapturedJsonArtifact",
    "CapturedLifecyclePort",
    "CapturedMemberHandle",
    "CapturedRelease",
    "ImmutableSnapshotProvider",
    "LaunchSession",
    "LifecycleAuthority",
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
        handle_sid = str(bindings._stream_id)
        if str(intern_sid) != handle_sid or session_id != id(session):
            raise ValueError("captured bindings are required")
        stream_id = handle_sid
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
        handle_sid = session._stream_id
        if handle_sid is None or str(bound[1]) != str(handle_sid):
            raise ValueError("CONSUMED input is required before release")
        stream_id = str(handle_sid)
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
        if len(stored) < self._receipt_len.get(stream_id, 0):
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
        self._receipt_len[stream_id] = max(self._receipt_len.get(stream_id, 0), len(rows))

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
        self._receipt_len[stream_id] = len(encoded)
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
