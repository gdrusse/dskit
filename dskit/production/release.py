"""What gets armed: a content-and-runtime-bound release (plan §5.3.1, D24).

Arming binds a *release*, never a document. Every input that can change a
decision is a field of :class:`ReleaseManifest` — the document identity,
the run and serving hashes, every artifact digest and timestamp, every
resolved class with its code digest, the adapter, the derived ``FeedSpec``,
the source config, the expected ``ExecutionScope``, the approval and lease
verifier fingerprints, the readiness ``checklist_digest`` and the
interpreter/distribution inventory of :class:`RuntimeFingerprint` — and
``release_hash = canonical_hash(manifest)`` moves when any of them does.
The manifest is a frozen value object validated at construction, so a
``dataclasses.replace`` that breaks its shape refuses exactly as
``from_obj`` does.

A release is re-earned from bytes, never from mtimes. :func:`verify_release`
re-digests every artifact, refuses a missing or future-dated timestamp,
refuses one older than the document's ``max_artifact_age`` with the
:data:`ARTIFACT_EXPIRED` reason, recaptures the runtime fingerprint and
refuses drift, and (when the feed reports one) refuses a source-config
hash the release did not bind. :class:`ReleaseReader` is the only
capability a ``release_read`` node receives — ``get`` (digest-checked
bytes) and ``names``, nothing else: no path, no handle, no write verb.

The three layout names of the immutable release directory
(:data:`RELEASES_DIRNAME`, :data:`RELEASE_FILENAME`,
:data:`DOCUMENT_FILENAME`) and :func:`parse_iso_duration` (the reading of
``serving.max_artifact_age``, which guards' and monitors' period windows
reuse) live here because the release is what they describe.

Import cost: stdlib plus ``dskit.production.base``/``records``,
``dskit.pipeline.node`` (``class_ref``) and ``dskit.onboarding.base`` (the
durable write and the file digest).
"""

import copy
import hashlib
import inspect
import json
import platform
import re
import sys
import sysconfig
from dataclasses import dataclass, fields
from importlib import metadata
from pathlib import Path

from dskit.onboarding.base import durable_write_json, file_digest
from dskit.pipeline.node import check_int_param, class_ref
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_hash,
)
from dskit.production.records import ExecutionScope

__all__ = [
    "ARTIFACT_DRIFT",
    "ARTIFACT_EXPIRED",
    "ARTIFACT_FUTURE_DATED",
    "ARTIFACT_MISSING",
    "DOCUMENT_FILENAME",
    "Distribution",
    "FEED_SPEC_KEYS",
    "RELEASES_DIRNAME",
    "RELEASE_FILENAME",
    "RUNTIME_DRIFT",
    "ReleaseManifest",
    "ReleaseReader",
    "RuntimeFingerprint",
    "SOURCE_CONFIG_DRIFT",
    "artifact_digest",
    "fingerprint_class",
    "parse_iso_duration",
    "verify_release",
    "write_release",
]

#: The immutable release subdirectory of a serve-series root (§5.8):
#: ``<root>/releases/<release_hash>/{document.json, release.json}``.
RELEASES_DIRNAME = "releases"
RELEASE_FILENAME = "release.json"
DOCUMENT_FILENAME = "document.json"

#: §5.2's eight release-bound ``FeedSpec`` fields, in plan order. The
#: manifest carries them as a plain default-deny mapping so that this
#: module never imports ``feed.py`` (§10 builds the release first).
FEED_SPEC_KEYS = (
    "source_binding",
    "entity_key_fields",
    "event_time_field",
    "digest_recipe",
    "required_keys",
    "required_keys_digest",
    "source_config_hash",
    "source_config_version",
)

#: The refusal reasons :func:`verify_release` records, as the plan spells
#: them (``artifact_expired`` is the one §5.3.1 names literally).
ARTIFACT_MISSING = "artifact_missing"
ARTIFACT_DRIFT = "artifact_drift"
ARTIFACT_FUTURE_DATED = "artifact_future_dated"
ARTIFACT_EXPIRED = "artifact_expired"
RUNTIME_DRIFT = "runtime_drift"
SOURCE_CONFIG_DRIFT = "source_config_drift"

#: ``P[nD][T[nH][nM][nS]]`` — day and time components only. Years, months
#: and weeks are calendar units whose length depends on WHEN, so they can
#: not be a fixed number of milliseconds and are refused.
_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?\Z"
)
_UNIT_MS = {"d": 86_400_000, "h": 3_600_000, "m": 60_000, "s": 1_000}


def parse_iso_duration(text):
    """Read an ISO-8601 day/time duration as milliseconds.

    ``serving.max_artifact_age`` is written as ``"P30D"`` because a
    duration a human reviews should read as one; the code compares
    epoch-millisecond ints, so this is the single place the two meet.

    Parameters
    ----------
    text : str
        ``P[nD][T[nH][nM][nS]]`` with at least one component —
        ``"P30D"``, ``"PT90M"``, ``"P1DT2H3M4S"``, ``"PT0S"``.

    Returns
    -------
    int
        The duration in milliseconds.

    Raises
    ------
    ProductionError
        For a calendar unit (``P1M``, ``P1Y``), a missing ``P``, an empty
        or lowercase spelling, a dangling number, a sign, or no component.
    """
    match = _DURATION.match(text) if isinstance(text, str) else None
    parts = match.groupdict() if match is not None else {}
    if not any(parts.values()) or text.endswith("T"):
        raise ProductionError(
            [
                f"{text!r} is not an ISO-8601 day/time duration — expected "
                "P[nD][T[nH][nM][nS]] (calendar units are refused)"
            ]
        )
    return sum(int(value) * _UNIT_MS[unit] for unit, value in parts.items() if value)


# ---------------------------------------------------------------------------
# Content fingerprints — artifacts and classes
# ---------------------------------------------------------------------------


def artifact_digest(path):
    """Digest an artifact — a file's bytes, or a directory's names and bytes.

    Parameters
    ----------
    path : str or pathlib.Path
        A file (sha256 of its bytes) or a directory (the
        :func:`~dskit.production.base.canonical_hash` of ``{relative
        posix path: file sha256}`` over every file beneath it, so a
        rename moves the digest exactly as a rewrite does).

    Returns
    -------
    str
        64 lowercase hex characters.

    Raises
    ------
    ProductionError
        If ``path`` does not exist.
    """
    path = Path(path)
    if path.is_file():
        return file_digest(str(path))
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
        return canonical_hash(
            {p.relative_to(path).as_posix(): file_digest(str(p)) for p in files}
        )
    raise ProductionError([f"artifact {str(path)!r} is not there"])


def fingerprint_class(cls):
    """Fingerprint a class by its reference and its source text.

    Two classes with the same body but different references fingerprint
    differently, and a class whose body changed fingerprints differently
    under the same reference — which is what makes a ``classes`` entry of
    the manifest a binding to code rather than to a name.

    Parameters
    ----------
    cls : type
        A class whose source ``inspect`` can read.

    Returns
    -------
    str
        ``canonical_hash({"ref": class_ref(cls), "source": source})``.

    Raises
    ------
    ProductionError
        If ``cls`` is not a class, or its source cannot be read (a class
        built at runtime, or defined in an interactive session).
    """
    if not isinstance(cls, type):
        raise ProductionError([f"fingerprint_class expects a class, got {cls!r}"])
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError) as exc:
        raise ProductionError(
            [f"cannot read the source of class {cls.__qualname__!r}: {exc}"]
        ) from exc
    return canonical_hash({"ref": class_ref(cls), "source": source})


# ---------------------------------------------------------------------------
# The runtime inventory (D24)
# ---------------------------------------------------------------------------


def _optional_str(problems, name, value):
    """Accept None or a non-empty string."""
    if value is not None:
        _check_str(problems, name, value)


@dataclass(frozen=True)
class Distribution:
    """One installed distribution of the runtime inventory.

    Parameters
    ----------
    name, version : str
        The distribution's metadata ``Name`` and ``Version``.
    direct_url : str or None
        The PEP 610 ``direct_url.json`` content verbatim, when the
        distribution was installed from a direct URL (an editable or VCS
        install); None otherwise.
    record_digest : str or None
        sha256 of the ``RECORD`` file — the installed-file inventory with
        its own per-file hashes — when the installer wrote one.

    Examples
    --------
    An entry as ``RuntimeFingerprint.capture()`` records it::

        entry = Distribution(name="pytest", version="9.1.1", record_digest="ab" * 32)
        entry.to_obj()  # {'name': 'pytest', 'version': '9.1.1', 'record_digest': 'abab…'}
    """

    name: str
    version: str
    direct_url: str = None
    record_digest: str = None

    def __post_init__(self):
        """Refuse a nameless or versionless entry; the optional fields may be None."""
        problems = []
        _check_str(problems, "Distribution.name", self.name)
        _check_str(problems, "Distribution.version", self.version)
        _optional_str(problems, "Distribution.direct_url", self.direct_url)
        _optional_str(problems, "Distribution.record_digest", self.record_digest)
        if problems:
            raise ProductionError(problems)

    def to_obj(self):
        """Return the entry as a JSON-ready dict, omitting the fields it has no value for.

        Returns
        -------
        dict
            ``name`` and ``version`` always; ``direct_url`` and
            ``record_digest`` only when present.
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None}

    @classmethod
    def from_obj(cls, obj):
        """Rebuild an entry from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            ``name`` and ``version``, optionally ``direct_url`` and
            ``record_digest``.

        Returns
        -------
        Distribution

        Raises
        ------
        ProductionError
            On a non-dict, an unknown key, or a missing/malformed field.
        """
        return cls(**_fixed_fields(cls, obj, required=("name", "version")))


@dataclass(frozen=True)
class RuntimeFingerprint:
    """D24's runtime inventory: the interpreter, the platform and every installed distribution.

    Captured without network I/O from ``sys``, ``sysconfig``, ``platform``
    and ``importlib.metadata`` only, and deterministic across two calls in
    one interpreter, so ``verify_release`` can recapture it at startup, at
    every tick and before every submit and compare by value.

    Parameters
    ----------
    python_implementation, python_version, cache_tag : str
        ``platform.python_implementation()``, ``platform.python_version()``
        and ``sys.implementation.cache_tag``.
    abi : str or None
        ``sysconfig.get_config_var("SOABI")``; None where the build has none.
    platform : str
        ``platform.platform()``.
    libc : str or None
        ``platform.libc_ver()`` joined, e.g. ``"glibc 2.39"``; None when unknown.
    distributions : tuple of Distribution
        Every installed distribution, sorted by (name, version, direct_url,
        record_digest) — the COMPLETE inventory, duplicates included.
    project_digests : dict
        ``{path: sha256}`` for the project/lock files ``capture`` was given.
    image_digest : str or None
        A container image digest when the deployment supplies one.

    Examples
    --------
    Capture this interpreter, pinning a lock file into the fingerprint::

        fingerprint = RuntimeFingerprint.capture(project_files=["requirements.lock"])
        fingerprint == RuntimeFingerprint.capture(project_files=["requirements.lock"])  # True
        fingerprint.to_obj()["python_version"]  # e.g. '3.11.15'
    """

    python_implementation: str
    python_version: str
    cache_tag: str
    abi: str
    platform: str
    libc: str
    distributions: tuple
    project_digests: dict
    image_digest: str = None

    def __post_init__(self):
        """Check every field's type, coercing distribution dicts into records."""
        problems = []
        for name in ("python_implementation", "python_version", "cache_tag", "platform"):
            _check_str(problems, f"RuntimeFingerprint.{name}", getattr(self, name))
        for name in ("abi", "libc", "image_digest"):
            _optional_str(problems, f"RuntimeFingerprint.{name}", getattr(self, name))
        entries = []
        if not isinstance(self.distributions, (tuple, list)):
            problems.append(
                f"RuntimeFingerprint.distributions must be a list, got {self.distributions!r}"
            )
        else:
            for index, entry in enumerate(self.distributions):
                try:
                    entries.append(
                        entry if isinstance(entry, Distribution) else Distribution.from_obj(entry)
                    )
                except ProductionError as exc:
                    problems.extend(f"distributions[{index}]: {p}" for p in exc.problems)
        _check_dict(problems, "RuntimeFingerprint.project_digests", self.project_digests)
        if isinstance(self.project_digests, dict):
            for path, digest in self.project_digests.items():
                _check_str(problems, f"RuntimeFingerprint.project_digests[{path!r}]", digest)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "distributions", tuple(entries))
        object.__setattr__(self, "project_digests", dict(self.project_digests))

    @classmethod
    def capture(cls, project_files=(), image_digest=None):
        """Capture the running interpreter's fingerprint.

        Parameters
        ----------
        project_files : iterable of str or pathlib.Path
            Lock or requirement files to digest into ``project_digests``;
            every one must exist.
        image_digest : str or None
            A container image digest the deployment knows; None when it
            has none.

        Returns
        -------
        RuntimeFingerprint

        Raises
        ------
        ProductionError
            If a project file is missing, or an installed distribution
            carries no name or version (an inventory with a hole is not
            an inventory).
        """
        problems = []
        digests = {}
        for file in project_files:
            path = Path(file)
            if path.is_file():
                digests[str(path)] = file_digest(str(path))
            else:
                problems.append(f"project file {str(path)!r} is not there")
        entries = []
        for dist in metadata.distributions():
            name, version = dist.metadata["Name"], dist.metadata["Version"]
            if not name or not version:
                problems.append(
                    f"distribution at {dist.locate_file('')} has no Name/Version metadata"
                )
                continue
            record = dist.read_text("RECORD")
            direct_url = dist.read_text("direct_url.json")
            entries.append(
                Distribution(
                    name=name,
                    version=version,
                    direct_url=direct_url.strip() if direct_url else None,
                    record_digest=(
                        hashlib.sha256(record.encode("utf-8")).hexdigest() if record else None
                    ),
                )
            )
        if problems:
            raise ProductionError(problems)
        libc_name, libc_version = platform.libc_ver()
        return cls(
            python_implementation=platform.python_implementation(),
            python_version=platform.python_version(),
            cache_tag=sys.implementation.cache_tag,
            abi=sysconfig.get_config_var("SOABI"),
            platform=platform.platform(),
            libc=" ".join(part for part in (libc_name, libc_version) if part) or None,
            distributions=tuple(sorted(entries, key=_distribution_key)),
            project_digests=digests,
            image_digest=image_digest,
        )

    def to_obj(self):
        """Return the fingerprint as a JSON-ready dict.

        Returns
        -------
        dict
            Field order preserved; distributions as a list of entry dicts;
            ``abi``, ``libc`` and ``image_digest`` only when present.
        """
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            out[f.name] = _encode(value)
        return out

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a fingerprint from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            The declared fields; ``abi``, ``libc`` and ``image_digest``
            may be absent.

        Returns
        -------
        RuntimeFingerprint

        Raises
        ------
        ProductionError
            On a non-dict, an unknown key, a missing required field or a
            malformed entry.
        """
        required = tuple(
            f.name for f in fields(cls) if f.name not in ("abi", "libc", "image_digest")
        )
        return cls(**_fixed_fields(cls, obj, required=required))


def _distribution_key(entry):
    """Order entries totally, so the inventory is deterministic."""
    return (entry.name, entry.version, entry.direct_url or "", entry.record_digest or "")


def _encode(value):
    """Render a field JSON-ready: records via ``to_obj``, tuples as fresh lists, dicts copied."""
    if hasattr(value, "to_obj"):
        return value.to_obj()
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _fixed_fields(cls, obj, required):
    """Default-deny ``obj`` against ``cls``'s dataclass fields; return it for ``cls(**...)``."""
    if not isinstance(obj, dict):
        raise ProductionError([f"{cls.__name__}.from_obj expects a dict, got {obj!r}"])
    names = tuple(f.name for f in fields(cls))
    problems = []
    _check_unknown(problems, obj, names, where=cls.__name__)
    missing = [name for name in required if name not in obj]
    if missing:
        problems.append(f"{cls.__name__}: missing key(s) {missing}")
    if problems:
        raise ProductionError(problems)
    return dict(obj)


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


def _check_str_list(problems, name, value):
    """Require a list of non-empty strings."""
    if not isinstance(value, list):
        problems.append(f"{name} must be a list of strings, got {value!r}")
        return
    for index, item in enumerate(value):
        _check_str(problems, f"{name}[{index}]", item)


def _check_epoch_ms(problems, name, value):
    """Require an epoch-millisecond instant: an int at or after the epoch."""
    check_int_param(problems, name, value, ge=0)


#: The closed shape of each sub-mapping: key -> checker(problems, name, value).
_ARTIFACT_ENTRY = {"digest": _check_str, "timestamp_ms": _check_epoch_ms}
_CLASS_ENTRY = {"ref": _check_str, "code_digest": _check_str}
_ADAPTER = {"name": _check_str, "digest": _check_str}
_SOURCE_CONFIG = {"hash": _check_str, "version": _check_str}
_FEED_SPEC = {
    "source_binding": _check_dict,
    "entity_key_fields": _check_str_list,
    "event_time_field": _check_str,
    "digest_recipe": _check_str,
    "required_keys": _check_str_list,
    "required_keys_digest": _check_str,
    "source_config_hash": _check_str,
    "source_config_version": _check_str,
}


def _check_fixed(problems, where, value, spec):
    """Default-deny mapping check: every key of ``spec`` required, nothing else allowed."""
    if not isinstance(value, dict):
        problems.append(f"{where} must be a dict with keys {sorted(spec)}, got {value!r}")
        return
    _check_unknown(problems, value, spec, where=where)
    for key, check in spec.items():
        if key not in value:
            problems.append(f"{where}.{key} is required")
        else:
            check(problems, f"{where}.{key}", value[key])


def _check_named(problems, where, value, spec):
    """Check a mapping of user-chosen names to ``spec``-shaped entries."""
    _check_dict(problems, where, value)
    if isinstance(value, dict):
        for name, entry in value.items():
            _check_fixed(problems, f"{where}.{name}", entry, spec)


@dataclass(frozen=True)
class ReleaseManifest:
    """Every release input, bound once, hashed once (§5.3.1, D24).

    Frozen and validated at construction — a ``dataclasses.replace`` that
    breaks the shape refuses like ``from_obj`` does — and rendered by
    ``to_obj`` as canonical-ready JSON whose ``canonical_hash`` is the
    release identity. Mutable paths are never a field: the release binds
    what a run COMPUTES with, not where it lives.

    Parameters
    ----------
    series_id : str
        The document's series UUID.
    doc_hash, run_hash, serving_hash : str
        The document identity (``ServeDocument.doc_hash``), the served
        run's config hash and its serving-plan hash.
    artifacts : dict
        ``{name: {"digest": str, "timestamp_ms": int}}`` — every artifact
        the release reads, digested by :func:`artifact_digest`.
    classes : dict
        ``{key: {"ref": "pkg.module:Class", "code_digest": str}}`` — every
        resolved class, digested by :func:`fingerprint_class`.
    adapter : dict
        ``{"name": str, "digest": str}``.
    feed_spec : dict
        §5.2's eight fields, :data:`FEED_SPEC_KEYS`.
    source_config : dict
        ``{"hash": str, "version": str}`` of the onboarding source.
    execution_scope : ExecutionScope
        The graded expected scope, ``document.coordination.scope``.
    approval_fingerprint, lease_fingerprint : str
        The approval-verifier and lease class/code/params fingerprints.
    checklist_digest : str
        The canonical digest of the readiness checklist file.
    runtime_fingerprint : RuntimeFingerprint
    created_ms : int
        When ``plan`` minted the release, epoch ms.

    Examples
    --------
    A manifest over one artifact (every hash below stands for a real one)::

        manifest = ReleaseManifest(
            series_id="018f0f4e-7b21-7d3a-9c31-6d8f36d806a1",
            doc_hash="c1" * 32, run_hash="d2" * 32, serving_hash="e3" * 32,
            artifacts={"model": {"digest": artifact_digest("run/model"), "timestamp_ms": 1_767_000_000_000}},
            classes={"bars": {"ref": "yourproject.nodes:Bars", "code_digest": "f4" * 32}},
            adapter={"name": "yourproject", "digest": "b6" * 32},
            feed_spec={
                "source_binding": {"source": "bars-1m", "connector": "yourproject:Bars"},
                "entity_key_fields": ["symbol"], "event_time_field": "ts_ms",
                "digest_recipe": "sha256/canonical-rows", "required_keys": ["AAPL"],
                "required_keys_digest": "7a" * 32, "source_config_hash": "5b" * 32,
                "source_config_version": "3",
            },
            source_config={"hash": "5b" * 32, "version": "3"},
            execution_scope=ExecutionScope(venue="paper", account="strategy-a"),
            approval_fingerprint="c7" * 32, lease_fingerprint="d8" * 32,
            checklist_digest="e9" * 32,
            runtime_fingerprint=RuntimeFingerprint.capture(),
            created_ms=1_767_000_001_000,
        )
        len(manifest.release_hash)  # 64
        ReleaseManifest.from_obj(manifest.to_obj()) == manifest  # True
    """

    series_id: str
    doc_hash: str
    run_hash: str
    serving_hash: str
    artifacts: dict
    classes: dict
    adapter: dict
    feed_spec: dict
    source_config: dict
    execution_scope: ExecutionScope
    approval_fingerprint: str
    lease_fingerprint: str
    checklist_digest: str
    runtime_fingerprint: RuntimeFingerprint
    created_ms: int

    def __post_init__(self):
        """Validate every field's shape, accumulating, then freeze copies of the mappings."""
        problems = []
        for name in (
            "series_id",
            "doc_hash",
            "run_hash",
            "serving_hash",
            "approval_fingerprint",
            "lease_fingerprint",
            "checklist_digest",
        ):
            _check_str(problems, name, getattr(self, name))
        _check_named(problems, "artifacts", self.artifacts, _ARTIFACT_ENTRY)
        _check_named(problems, "classes", self.classes, _CLASS_ENTRY)
        _check_fixed(problems, "adapter", self.adapter, _ADAPTER)
        _check_fixed(problems, "feed_spec", self.feed_spec, _FEED_SPEC)
        _check_fixed(problems, "source_config", self.source_config, _SOURCE_CONFIG)
        if not isinstance(self.execution_scope, ExecutionScope):
            problems.append(
                f"execution_scope must be an ExecutionScope, got {self.execution_scope!r}"
            )
        if not isinstance(self.runtime_fingerprint, RuntimeFingerprint):
            problems.append(
                f"runtime_fingerprint must be a RuntimeFingerprint, got {self.runtime_fingerprint!r}"
            )
        _check_epoch_ms(problems, "created_ms", self.created_ms)
        if problems:
            raise ProductionError(problems)
        for name in ("artifacts", "classes", "adapter", "feed_spec", "source_config"):
            object.__setattr__(self, name, copy.deepcopy(getattr(self, name)))

    @property
    def release_hash(self):
        """The release identity: ``canonical_hash(self.to_obj())``, 64 hex chars."""
        return canonical_hash(self.to_obj())

    def to_obj(self):
        """Return the manifest as a JSON-ready dict the caller may mutate freely.

        Returns
        -------
        dict
            Exactly the fifteen fields in declaration order; nested
            records rendered with their own ``to_obj``; every mapping and
            list a fresh copy.
        """
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a manifest from ``release.json``, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the fifteen fields; ``execution_scope`` and
            ``runtime_fingerprint`` as their ``to_obj`` dicts.

        Returns
        -------
        ReleaseManifest
            Equal to the manifest that produced ``obj``.

        Raises
        ------
        ProductionError
            On a non-dict, an unknown or missing key, or any malformed field.
        """
        values = _fixed_fields(cls, obj, required=tuple(f.name for f in fields(cls)))
        values["execution_scope"] = ExecutionScope.from_obj(values["execution_scope"])
        values["runtime_fingerprint"] = RuntimeFingerprint.from_obj(values["runtime_fingerprint"])
        return cls(**values)


# ---------------------------------------------------------------------------
# The reader — the one capability a release_read node receives
# ---------------------------------------------------------------------------


class ReleaseReader:
    """Digest-checked read access to the artifacts one node is permitted to see.

    Constructed per node by the structural planner and handed over as
    ``NodeContext.release_reader`` (§5.3.1, §9.1). Its public surface is
    exactly ``get`` and ``names``: no path, no handle, no write verb, so a
    release read can reach neither the filesystem nor a mutable store.
    Holds no open file.

    Parameters
    ----------
    manifest : ReleaseManifest
        The release whose artifacts are readable.
    allowed_names : iterable of str
        The manifest artifact names THIS node may read; a name the
        manifest does not carry refuses at construction.
    root : str or pathlib.Path
        The directory the manifest's artifact names are relative to.

    Raises
    ------
    ProductionError
        If an allowed name is not an artifact of the manifest.

    Examples
    --------
    A node permitted to read one artifact of a release::

        reader = ReleaseReader(manifest, ("model",), "pipeline_runs/train-2026-01-01-abcd1234")
        reader.names()  # ('model',)
        weights = reader.get("model")  # the bytes, after their digest was re-verified
    """

    def __init__(self, manifest, allowed_names, root):
        allowed = tuple(sorted(allowed_names))
        self._names = frozenset(manifest.artifacts)
        ghosts = [name for name in allowed if name not in self._names]
        if ghosts:
            raise ProductionError(
                [f"allowed names {ghosts} are not artifacts of release {manifest.release_hash}"]
            )
        self._digests = {name: manifest.artifacts[name]["digest"] for name in allowed}
        self._root = Path(root)

    def names(self):
        """List what this node may read.

        Returns
        -------
        tuple of str
            The allowed artifact names, sorted.
        """
        return tuple(sorted(self._digests))

    def get(self, name):
        """Return an artifact's bytes after re-verifying its recorded digest.

        Parameters
        ----------
        name : str
            A manifest artifact name this node is allowed to read.

        Returns
        -------
        bytes
            The artifact's content, exactly the bytes that were digested.

        Raises
        ------
        ProductionError
            If ``name`` is not in the manifest, is not readable by this
            node, is not a file at the root, or no longer matches its
            recorded digest.
        """
        if name not in self._digests:
            why = "is not an artifact of this release" if name not in self._names else "is not readable by this node"
            raise ProductionError([f"release artifact {name!r} {why}"])
        path = self._root / name
        if not path.is_file():
            raise ProductionError([f"release artifact {name!r} is not a file at {path}"])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != self._digests[name]:
            raise ProductionError(
                [f"release artifact {name!r} at {path} no longer matches its recorded digest"]
            )
        return data


# ---------------------------------------------------------------------------
# Re-earning the release
# ---------------------------------------------------------------------------


def _check_artifacts(problems, manifest, root, now_ms, max_age_ms):
    """Re-digest every artifact and judge its manifest timestamp against ``now_ms``."""
    for name, entry in manifest.artifacts.items():
        path = Path(root) / name
        if not path.exists():
            problems.append(f"{ARTIFACT_MISSING}: artifact {name!r} is not at {path}")
        elif artifact_digest(path) != entry["digest"]:
            problems.append(f"{ARTIFACT_DRIFT}: artifact {name!r} at {path} changed since the release")
        stamp = entry["timestamp_ms"]
        if stamp > now_ms:
            problems.append(
                f"{ARTIFACT_FUTURE_DATED}: artifact {name!r} is stamped {stamp} ms, after now ({now_ms} ms)"
            )
        elif now_ms - stamp > max_age_ms:
            problems.append(
                f"{ARTIFACT_EXPIRED}: artifact {name!r} is {now_ms - stamp} ms old, "
                f"over the {max_age_ms} ms limit"
            )


def _check_runtime(problems, expected):
    """Recapture the runtime fingerprint with the release's own project files and compare."""
    try:
        actual = RuntimeFingerprint.capture(
            project_files=tuple(expected.project_digests), image_digest=expected.image_digest
        )
    except ProductionError as exc:
        problems.extend(f"{RUNTIME_DRIFT}: {p}" for p in exc.problems)
        return
    drift = [
        f.name
        for f in fields(RuntimeFingerprint)
        if getattr(actual, f.name) != getattr(expected, f.name)
    ]
    if drift:
        problems.append(f"{RUNTIME_DRIFT}: runtime fingerprint differs in {drift}")


def verify_release(manifest, root, now_ms, max_age_ms, source_config_hash=None):
    """Re-earn a release from bytes and the runtime — at startup, every tick, before submit.

    Parameters
    ----------
    manifest : ReleaseManifest
        The release being served.
    root : str or pathlib.Path
        The directory the manifest's artifact names are relative to.
    now_ms : int
        The injected clock's reading; never the wall clock read here.
    max_age_ms : int
        ``parse_iso_duration(document.serving.max_artifact_age)``; an
        artifact exactly this old still verifies, one millisecond older
        does not.
    source_config_hash : str or None
        The source-config hash the feed reports on a pull; None when the
        caller has none to compare.

    Returns
    -------
    None
        When nothing drifted.

    Raises
    ------
    ProductionError
        Every problem at once, each prefixed with its reason —
        :data:`ARTIFACT_MISSING`, :data:`ARTIFACT_DRIFT`,
        :data:`ARTIFACT_FUTURE_DATED`, :data:`ARTIFACT_EXPIRED`,
        :data:`RUNTIME_DRIFT` or :data:`SOURCE_CONFIG_DRIFT`.
        Filesystem mtimes are never consulted.
    """
    problems = []
    _check_artifacts(problems, manifest, root, now_ms, max_age_ms)
    _check_runtime(problems, manifest.runtime_fingerprint)
    bound = manifest.source_config["hash"]
    if source_config_hash is not None and source_config_hash != bound:
        problems.append(
            f"{SOURCE_CONFIG_DRIFT}: the release binds source config {bound!r}, "
            f"the feed reports {source_config_hash!r}"
        )
    if problems:
        raise ProductionError(problems)
    return None


# ---------------------------------------------------------------------------
# The immutable release directory
# ---------------------------------------------------------------------------


def _read_json(path):
    """Load a JSON file, or raise naming it."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProductionError([f"cannot read {path}: {exc}"]) from exc


def write_release(root, manifest, document):
    """Materialise ``releases/<release_hash>/`` under ``root`` — once.

    Idempotent for the same manifest and document; a directory that
    already holds a DIFFERENT ``release.json`` or ``document.json`` under
    the same hash refuses, because a release directory is immutable.
    Each file lands durably (staged, fsynced, renamed).

    Parameters
    ----------
    root : str or pathlib.Path
        The serve-series root the ``releases`` directory hangs from.
    manifest : ReleaseManifest
    document : ServeDocument
        The serve document the manifest was planned from; written verbatim.

    Returns
    -------
    pathlib.Path
        ``root / "releases" / manifest.release_hash``.

    Raises
    ------
    ProductionError
        If an existing file under that hash differs from what would be
        written, or cannot be read back.
    """
    target = Path(root) / RELEASES_DIRNAME / manifest.release_hash
    target.mkdir(parents=True, exist_ok=True)
    contents = ((RELEASE_FILENAME, manifest.to_obj()), (DOCUMENT_FILENAME, document.to_obj()))
    problems = [
        f"{target / filename} differs from the {filename} of release "
        f"{manifest.release_hash}; a release directory is immutable"
        for filename, obj in contents
        if (target / filename).exists() and _read_json(target / filename) != obj
    ]
    if problems:
        raise ProductionError(problems)
    for filename, obj in contents:
        if not (target / filename).exists():
            durable_write_json(str(target / filename), obj)
    return target
