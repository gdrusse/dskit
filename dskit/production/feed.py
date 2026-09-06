"""The feed — one normalized binding, one snapshot, one freshness ladder (plan §5.2, D4, D6).

Live rows enter a served tick through the connector seam and nowhere else.
Three objects carry that rule. :class:`FeedSpec` is the release-bound half
of the entry class's pure :class:`~dskit.pipeline.node.ServingContract`
plus the serve document's required universe — the one place a contract
becomes release state, so acquisition and the entry read cannot drift onto
two locators. :class:`EntrySourceFeed` pulls through that binding, either
by calling ``run_acquisition`` in ``live`` mode or by reading a store a
separate ``watch`` process fills, and reports acquisition/link status
ONLY: it never hands rows to anyone. And :func:`snapshot_entry` turns the
entry node's exact outputs into the :class:`~dskit.production.records.EntryBatch`
descendants receive — rows, entity projection, event time and per-key
digests describing one frozen snapshot rather than a second read.

Two safety rules are enforced here and pinned by the suite. Every pull
re-resolves the source's ACTIVE alias BEFORE any fetch and refuses when it
no longer matches what the release pinned, so a swapped config can never
deliver a row (D4). And freshness is coverage-wide: the ladder reads the
OLDEST required key's watermark, a key the store does not carry is dead,
and a connector or link failure is dead immediately — zero new rows is
``live`` only while every key is fresh (D6).

``run_acquisition`` and ``scan_stream`` are called as MODULE attributes of
their onboarding modules, which is what lets a test seam them without a
network or a store.

Import cost: stdlib, ``dskit.onboarding`` (acquire, observations, the
root), ``dskit.pipeline.node``/``libs.observations`` (the contract type and
the entry class's spellings) and the production base, records, redact,
release and vocab modules.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields

import dskit.onboarding.acquire as acquire
import dskit.onboarding.observations as observations
from dskit.onboarding.base import AssetError
from dskit.onboarding.layout import OnboardingRoot
from dskit.pipeline.libs.observations import (
    DEFAULT_TS_UNIT,
    DIGEST_RECIPE_KIND,
    SOURCE_BINDING_KIND,
    TS_UNITS,
)
from dskit.pipeline.node import ServingContract, check_int_param
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_bytes,
    canonical_hash,
    reject_unknown_params,
)
from dskit.production.clock import ManualTime
from dskit.production.records import EntryBatch, FeedResult, InputWatermark
from dskit.production.redact import get_logger
from dskit.production.release import FEED_SPEC_KEYS
from dskit.production.vocab import PULL_MODES

__all__ = [
    "ACQUISITION_MODE",
    "DEFAULT_PULL_MODE",
    "FEED_KINDS",
    "SOURCE_CONFIG_KIND",
    "EntrySourceFeed",
    "Feed",
    "FeedSpec",
    "ReplayFeed",
    "active_source_identity",
    "snapshot_entry",
]

#: The pull mode a feed runs under when the document names none — the
#: connector pull (D4: "the feed is ``acquire --mode live``").
DEFAULT_PULL_MODE = "acquire"

#: The onboarding acquisition mode every live pull is stamped with
#: (ADR-0014: each mode keeps its own cursor; serving never backfills).
ACQUISITION_MODE = "live"

#: The onboarding registry kind whose ACTIVE alias is the source identity.
SOURCE_CONFIG_KIND = "source_config"

#: The one params key every seam site may carry beside its knobs.
_NOTES = ("notes",)

#: The binding kinds this feed can pull through — the entry class's own
#: spelling, imported so the two cannot drift.
_SERVED_BINDINGS = frozenset((SOURCE_BINDING_KIND,))

_log = get_logger("feed")


def _ms_ok(value):
    """Say whether ``value`` is an epoch-millisecond instant (an int, never a bool)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _universe_problems(problems, keys):
    """Append every problem with a required-key list; return the keys as a sorted tuple."""
    if isinstance(keys, (str, bytes)) or not isinstance(keys, (list, tuple, set, frozenset)):
        problems.append(f"required_keys must be a list of key strings, got {keys!r}")
        return ()
    keys = list(keys)
    if not keys:
        problems.append("required_keys must name at least one key — an empty universe serves nothing")
    for index, key in enumerate(keys):
        _check_str(problems, f"required_keys[{index}]", key)
    if len(set(keys)) != len(keys):
        problems.append(f"required_keys carries duplicates: {sorted(k for k in keys if keys.count(k) > 1)}")
    return tuple(sorted(k for k in keys if isinstance(k, str)))


def _entity_key(row, fields_):
    """Render one row's entity key: the field's value, or the canonical list of several."""
    missing = [f for f in fields_ if f not in row]
    if missing:
        raise ProductionError([f"row {row!r} is missing entity key field(s) {missing}"])
    values = [row[f] for f in fields_]
    if len(values) == 1:
        if not isinstance(values[0], str) or not values[0]:
            raise ProductionError(
                [f"entity key {fields_[0]!r} must be a non-empty string, got {values[0]!r}"]
            )
        return values[0]
    return canonical_bytes(values).decode("ascii")


# ---------------------------------------------------------------------------
# FeedSpec — the release-bound binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedSpec:
    """The release-bound feed binding: the entry's contract plus the required universe (§5.2).

    Eight fields in :data:`~dskit.production.release.FEED_SPEC_KEYS` order —
    the four the entry class declares, the sorted required key set with its
    digest, and the source-config identity every pull re-checks. Frozen and
    validated at construction; the two mappings are deep copies, so a
    contract's later mutation cannot reach a spec.

    Parameters
    ----------
    source_binding : dict
        Where rows come from, as the entry class spells it.
    entity_key_fields : tuple of str
        The dedupe key with the time field projected out.
    event_time_field : str
        The epoch-ms field every emitted row carries.
    digest_recipe : dict
        How a per-key snapshot is digested.
    required_keys : tuple of str
        The universe every tick must cover exactly — sorted, unique.
    required_keys_digest : str
        ``canonical_hash(list(required_keys))``.
    source_config_hash, source_config_version : str
        The ACTIVE source alias's identity at ``plan``
        (:func:`active_source_identity`).

    Examples
    --------
    Bind a contract to a two-instrument universe::

        spec = FeedSpec.from_contract(contract, ["INS2", "INS1"], digest, version)
        spec.required_keys  # ('INS1', 'INS2')
        FeedSpec.from_obj(spec.to_obj()) == spec  # True
    """

    source_binding: dict
    entity_key_fields: tuple
    event_time_field: str
    digest_recipe: dict
    required_keys: tuple
    required_keys_digest: str
    source_config_hash: str
    source_config_version: str

    def __post_init__(self):
        """Validate every field, accumulating, then freeze copies of the mappings."""
        problems = []
        for name in ("source_binding", "digest_recipe"):
            _check_dict(problems, name, getattr(self, name))
        entity = self.entity_key_fields
        if isinstance(entity, list):
            entity = tuple(entity)
        if not isinstance(entity, tuple) or not entity:
            problems.append(f"entity_key_fields must be a non-empty list of field names, got {entity!r}")
        else:
            for index, name in enumerate(entity):
                _check_str(problems, f"entity_key_fields[{index}]", name)
        keys = _universe_problems(problems, self.required_keys)
        if keys and keys != tuple(self.required_keys):
            problems.append(f"required_keys must be sorted and unique, got {list(self.required_keys)!r}")
        for name in (
            "event_time_field",
            "required_keys_digest",
            "source_config_hash",
            "source_config_version",
        ):
            _check_str(problems, name, getattr(self, name))
        if keys and self.required_keys_digest != canonical_hash(list(keys)):
            problems.append("required_keys_digest is not the canonical hash of required_keys")
        if problems:
            raise ProductionError([f"FeedSpec: {p}" for p in problems])
        object.__setattr__(self, "entity_key_fields", entity)
        object.__setattr__(self, "required_keys", keys)
        for name in ("source_binding", "digest_recipe"):
            object.__setattr__(self, name, copy.deepcopy(getattr(self, name)))

    @classmethod
    def from_contract(cls, contract, required_keys, source_config_hash, source_config_version):
        """Bind an entry class's contract to the document's universe and the source identity.

        Parameters
        ----------
        contract : ServingContract
            The entry class's pure ``serving_contract`` answer.
        required_keys : list of str
            ``document.serving.required_universe``, in any order; sorted
            and digested here.
        source_config_hash, source_config_version : str
            What :func:`active_source_identity` answered at ``plan``.

        Returns
        -------
        FeedSpec

        Raises
        ------
        ProductionError
            A non-contract, a malformed universe (empty, duplicate, blank,
            non-string or a bare string), or a non-string identity.
        """
        if not isinstance(contract, ServingContract):
            raise ProductionError([f"FeedSpec.from_contract expects a ServingContract, got {contract!r}"])
        problems = []
        keys = _universe_problems(problems, required_keys)
        if problems:
            raise ProductionError([f"FeedSpec: {p}" for p in problems])
        return cls(
            source_binding=copy.deepcopy(contract.source_binding),
            entity_key_fields=tuple(contract.entity_key_fields),
            event_time_field=contract.event_time_field,
            digest_recipe=copy.deepcopy(contract.digest_recipe),
            required_keys=keys,
            required_keys_digest=canonical_hash(list(keys)),
            source_config_hash=source_config_hash,
            source_config_version=source_config_version,
        )

    def to_obj(self):
        """Return the spec as the manifest carries it: a JSON-ready dict in field order.

        Returns
        -------
        dict
            Exactly the eight keys; tuples as lists, mappings deep-copied.
        """
        return {
            f.name: copy.deepcopy(list(value) if isinstance(value, tuple) else value)
            for f in fields(self)
            for value in (getattr(self, f.name),)
        }

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a spec from :meth:`to_obj`'s rendering, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the eight keys.

        Returns
        -------
        FeedSpec
            Equal to the spec that produced ``obj``.

        Raises
        ------
        ProductionError
            A non-dict, an unknown or missing key, or a malformed field.
        """
        if not isinstance(obj, dict):
            raise ProductionError([f"FeedSpec.from_obj expects a dict, got {obj!r}"])
        problems = []
        _check_unknown(problems, obj, FEED_SPEC_KEYS, where="FeedSpec")
        missing = [name for name in FEED_SPEC_KEYS if name not in obj]
        if missing:
            problems.append(f"FeedSpec: missing key(s) {missing}")
        if problems:
            raise ProductionError(problems)
        values = dict(obj)
        for name in ("entity_key_fields", "required_keys"):
            if isinstance(values[name], list):
                values[name] = tuple(values[name])
        return cls(**values)


# ---------------------------------------------------------------------------
# Source identity (D4)
# ---------------------------------------------------------------------------


def active_source_identity(registry, source):
    """Answer what the source alias resolves to NOW — the one owner for ``plan`` and every pull.

    Parameters
    ----------
    registry : Registry
        The onboarding root's P2 registry.
    source : str
        The ``source_config`` alias.

    Returns
    -------
    tuple
        ``(source_config_hash, source_config_version)`` — the ACTIVE
        alias's ``version_id`` and, as a monotone label, the count of
        registered versions of the alias as a string (R14).

    Raises
    ------
    ProductionError
        When no alias, or more than one, is active, or the registry
        refuses the lookup.
    """
    try:
        active = acquire.find_active_source(registry, source)
        versions = registry.find(SOURCE_CONFIG_KIND, source)
    except AssetError as exc:
        raise ProductionError([f"source {source!r}: {p}" for p in exc.errors]) from exc
    return active, str(len(versions))


# ---------------------------------------------------------------------------
# The snapshot over the entry's exact outputs (D6)
# ---------------------------------------------------------------------------


def _stream_digest(rows):
    """Digest a key's rows by the frozen whole-dump recipe the entry's fingerprint uses."""
    try:
        return observations.stream_digest(rows)
    except AssetError as exc:
        raise ProductionError(list(exc.errors)) from exc


#: Digest recipe kind -> the digest over one key's rows. A recipe the
#: table does not name refuses: a snapshot is only comparable under the
#: recipe its contract declared.
_DIGESTS = {DIGEST_RECIPE_KIND: _stream_digest}


def _row_stream(entry_outputs):
    """Return the entry's single list-valued port, refusing zero or several."""
    if not isinstance(entry_outputs, dict):
        raise ProductionError([f"entry outputs must be a dict of ports, got {entry_outputs!r}"])
    ports = [name for name, value in entry_outputs.items() if isinstance(value, list)]
    if len(ports) != 1:
        raise ProductionError(
            [
                "the entry must emit exactly one row stream (a list-valued port), "
                f"got {ports} among {sorted(entry_outputs)}"
            ]
        )
    return entry_outputs[ports[0]]


def _contract_problems(problems, contract, spec):
    """Append each of the four contract fields the spec disagrees with."""
    for name in ("source_binding", "entity_key_fields", "event_time_field", "digest_recipe"):
        mine, theirs = getattr(contract, name), getattr(spec, name)
        if isinstance(mine, tuple) or isinstance(theirs, tuple):
            mine, theirs = tuple(mine), tuple(theirs)
        if mine != theirs:
            problems.append(f"the feed spec's {name} {theirs!r} is not the contract's {mine!r}")


def _rows_by_key(rows, contract):
    """Group rows by entity key, refusing a non-mapping row or a malformed event time."""
    by_key = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ProductionError([f"row {index} is not a mapping: {row!r}"])
        stamp = row.get(contract.event_time_field)
        if not _ms_ok(stamp):
            raise ProductionError(
                [
                    f"row {index}: {contract.event_time_field!r} must be an epoch-ms int, "
                    f"got {stamp!r}"
                ]
            )
        by_key.setdefault(_entity_key(row, contract.entity_key_fields), []).append(row)
    return by_key


def snapshot_entry(contract, spec, entry_outputs, source_config_hash):
    """Describe the entry's exact outputs as the frozen batch descendants receive.

    Parameters
    ----------
    contract : ServingContract
        The entry class's contract — entity projection, event time, recipe.
    spec : FeedSpec
        The release-bound binding: the universe the rows must cover.
    entry_outputs : dict
        The entry node's outputs, exactly as ``run`` returned them; the
        one list-valued port is the row stream.
    source_config_hash : str
        The source identity the pull verified.

    Returns
    -------
    EntryBatch
        ``outputs`` (the same outputs), one
        :class:`~dskit.production.records.InputWatermark` per required
        key (its latest event time and the digest of its rows under the
        contract's recipe), ``data_asof_ms`` = the OLDEST watermark,
        ``coverage_digest`` over the watermarks, ``inputs_digest`` =
        ``canonical_hash(entry_outputs)``.

    Raises
    ------
    ProductionError
        A spec that disagrees with the contract; outputs without exactly
        one row stream; a non-mapping row, a row missing an entity key
        field or carrying a malformed event time; a required key with no
        row (named) or a row outside the universe (named); an unknown
        digest recipe; outputs canonical JSON cannot hold.
    """
    problems = []
    if not isinstance(contract, ServingContract):
        problems.append(f"contract must be a ServingContract, got {contract!r}")
    if not isinstance(spec, FeedSpec):
        problems.append(f"spec must be a FeedSpec, got {spec!r}")
    _check_str(problems, "source_config_hash", source_config_hash)
    if problems:
        raise ProductionError(problems)
    _contract_problems(problems, contract, spec)
    if problems:
        raise ProductionError(problems)
    digest = _DIGESTS.get(spec.digest_recipe.get("kind"))
    if digest is None:
        raise ProductionError(
            [f"unknown digest recipe {spec.digest_recipe!r} — known: {sorted(_DIGESTS)}"]
        )
    by_key = _rows_by_key(_row_stream(entry_outputs), contract)
    required = spec.required_keys
    missing = [key for key in required if key not in by_key]
    extra = sorted(key for key in by_key if key not in required)
    if missing:
        problems.append(f"required key(s) with no row this tick: {missing}")
    if extra:
        problems.append(f"row(s) outside the required universe: {extra}")
    if problems:
        raise ProductionError(problems)
    watermarks = {
        key: InputWatermark(
            key=key,
            latest_asof_ms=max(row[contract.event_time_field] for row in by_key[key]),
            source_digest=digest(by_key[key]),
        )
        for key in required
    }
    return EntryBatch(
        outputs=entry_outputs,
        watermarks_by_key=watermarks,
        required_keys_digest=spec.required_keys_digest,
        coverage_digest=canonical_hash({key: watermarks[key].to_obj() for key in required}),
        data_asof_ms=min(w.latest_asof_ms for w in watermarks.values()),
        inputs_digest=canonical_hash(entry_outputs),
        source_config_hash=source_config_hash,
    )


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class Feed(ABC):
    """The feed seam (§5.2): fetch through the release-bound binding, report status only.

    ``cls(params)`` construction — default-deny over the subclass's
    ``_PARAMS`` plus ``notes`` — and one abstract hook, :meth:`pull`. A
    feed never returns rows: the deferred entry reads them once, and
    :func:`snapshot_entry` describes that read.

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; ``None`` means ``{}``.

    Examples
    --------
    A feed that is always live::

        class AlwaysLive(Feed):
            def pull(self, tick_at_ms):
                return FeedResult(status="live", acq_id=None, records_added=0,
                                  source_config_hash=None, at_ms=tick_at_ms)

        AlwaysLive().pull(1_767_000_000_000).status  # 'live'
    """

    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            One problem per unknown key; subclasses extend the list.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + _NOTES)
        return problems

    def _configure(self, params):
        """Read validated params; the base has none to read."""

    @abstractmethod
    def pull(self, tick_at_ms):
        """Fetch for the tick at ``tick_at_ms`` and report what came of it.

        Parameters
        ----------
        tick_at_ms : int
            The tick's instant, epoch ms — what the freshness ladder is
            measured against.

        Returns
        -------
        FeedResult
            Acquisition/link status and counts, never rows.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# EntrySourceFeed — the connector pull, in either mode
# ---------------------------------------------------------------------------


class _AcquirePull:
    """``pull: acquire`` — one ``run_acquisition`` in live mode through the binding."""

    @staticmethod
    def fetch(root, registry, binding):
        """Pull once; return ``(acq_id, records_added)`` from the summary."""
        summary = acquire.run_acquisition(
            root, registry, binding["source"], binding["stream"], ACQUISITION_MODE
        )
        return summary.get("acq_id"), int(summary.get("records") or 0)


class _StorePull:
    """``pull: store`` — a separate ``watch`` process fills the store; nothing is fetched."""

    @staticmethod
    def fetch(root, registry, binding):
        """Fetch nothing; the store is read as it stands."""
        return None, 0


#: Pull mode -> its strategy; pinned to the closed vocabulary below.
_PULLS = {"acquire": _AcquirePull, "store": _StorePull}
if set(_PULLS) != set(PULL_MODES):
    raise ProductionError([f"feed: pull strategies {sorted(_PULLS)} do not cover PULL_MODES {list(PULL_MODES)}"])


class _IsoStamps:
    """``ts_unit: iso`` — the scan derives the epoch-ms field and bounds at intake."""

    @staticmethod
    def scan_args(recipe, ts_out, since_ms):
        """Build the scan keywords: derive ``ts_out`` from ``ts_field``, drop rows before ``since_ms``."""
        return {"ts_field": recipe.get("ts_field"), "ts_out": ts_out, "since_ms": since_ms}

    @staticmethod
    def stamp(record, recipe, ts_out):
        """Read the row's instant from the derived field."""
        return record.get(ts_out)


class _MsStamps:
    """``ts_unit: ms`` — rows already carry the epoch-ms count under ``ts_field``."""

    @staticmethod
    def scan_args(recipe, ts_out, since_ms):
        """Build the scan keywords: nothing derived."""
        return {}

    @staticmethod
    def stamp(record, recipe, ts_out):
        """Read the row's instant from the declared field, as it is."""
        return record.get(recipe.get("ts_field"))


#: Time unit -> how a row's instant is read back from the store; pinned to
#: the entry class's own vocabulary.
_STAMPS = {"iso": _IsoStamps, "ms": _MsStamps}
if set(_STAMPS) != set(TS_UNITS):
    raise ProductionError([f"feed: stamp readers {sorted(_STAMPS)} do not cover TS_UNITS {list(TS_UNITS)}"])


class EntrySourceFeed(Feed):
    """Pull through the entry's normalized binding; status from the oldest watermark (§5.2, D4, D6).

    One class, parameterised by ``pull``: ``acquire`` calls
    ``run_acquisition(root, registry, source, stream, "live")``; ``store``
    fetches nothing and reads what a separate ``watch`` process landed.
    Both then derive every required key's latest event time through
    ``scan_stream`` and grade the OLDEST against ``tick_at_ms``:
    ``age <= max_staleness_ms`` is ``live``, ``age <= dead_after_ms`` is
    ``stale``, older is ``dead``; a required key the store does not carry
    is ``dead``; a connector or link failure (``OSError``, or the
    connector's own ``ValueError``-shaped refusal) is ``dead`` immediately
    and never raises. Before ANY of that, the source's ACTIVE alias is
    re-resolved and a hash or version other than the release's refuses —
    a swapped config never delivers a row.

    Parameters
    ----------
    params : dict, optional
        ``pull`` (one of ``PULL_MODES``, default
        :data:`DEFAULT_PULL_MODE`); ``notes``.
    root : OnboardingRoot
        The onboarding root — must be the one the binding names.
    registry : Registry
        That root's P2 registry.
    contract : ServingContract
        The entry class's contract.
    spec : FeedSpec
        The release-bound binding; must agree with ``contract``.
    clock : Clock
        Stamps ``FeedResult.at_ms``.
    max_staleness_ms, dead_after_ms : int
        The ladder's two inclusive thresholds
        (``schedule.max_staleness_ms`` / ``schedule.dead_after_ms``).

    Examples
    --------
    A store-fed feed over the root a run was trained from::

        feed = EntrySourceFeed(
            {"pull": "store"}, root=root, registry=root.registry(),
            contract=contract, spec=spec, clock=clock,
            max_staleness_ms=120_000, dead_after_ms=600_000,
        )
        feed.pull(1_767_000_000_000).status  # 'live' | 'stale' | 'dead'
    """

    _PARAMS = ("pull",)

    def __init__(
        self,
        params=None,
        *,
        root,
        registry,
        contract,
        spec,
        clock,
        max_staleness_ms,
        dead_after_ms,
    ):
        super().__init__(params)
        problems = []
        if not isinstance(contract, ServingContract):
            problems.append(f"contract must be a ServingContract, got {contract!r}")
        if not isinstance(spec, FeedSpec):
            problems.append(f"spec must be a FeedSpec, got {spec!r}")
        if not isinstance(root, OnboardingRoot):
            problems.append(f"root must be an OnboardingRoot, got {root!r}")
        for name, value in (("max_staleness_ms", max_staleness_ms), ("dead_after_ms", dead_after_ms)):
            check_int_param(problems, name, value, ge=0)
        if problems:
            raise ProductionError(problems)
        binding = contract.source_binding
        if binding.get("kind") not in _SERVED_BINDINGS:
            problems.append(
                f"binding kind {binding.get('kind')!r} is not one this feed pulls through "
                f"({sorted(_SERVED_BINDINGS)})"
            )
        if binding.get("root") != root.root:
            problems.append(
                f"the onboarding root {root.root!r} is not the entry's binding root "
                f"{binding.get('root')!r} — acquisition and the entry read ONE locator"
            )
        _contract_problems(problems, contract, spec)
        if dead_after_ms < max_staleness_ms:
            problems.append(
                f"dead_after_ms {dead_after_ms} is below max_staleness_ms {max_staleness_ms} — "
                "the ladder must be monotone"
            )
        if problems:
            raise ProductionError(problems)
        self._root = root
        self._registry = registry
        self._contract = contract
        self._spec = spec
        self._clock = clock
        self._max_staleness_ms = max_staleness_ms
        self._dead_after_ms = dead_after_ms

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``: an unknown key or an off-vocabulary ``pull``.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems.
        """
        problems = super().validate_params(params)
        pull = params.get("pull", DEFAULT_PULL_MODE)
        if pull not in PULL_MODES:
            problems.append(f"pull must be one of {list(PULL_MODES)}, got {pull!r}")
        return problems

    def _configure(self, params):
        """Bind the pull strategy the validated params name."""
        self._puller = _PULLS[params.get("pull", DEFAULT_PULL_MODE)]

    def pull(self, tick_at_ms):
        """Verify the source identity, fetch, and grade every required key's freshness.

        Parameters
        ----------
        tick_at_ms : int
            The tick's instant, epoch ms; the ladder is measured against
            it, never against the clock.

        Returns
        -------
        FeedResult
            ``status`` from the ladder (``dead`` on any failure), the
            acquisition id and count (``None``/``0`` under ``store`` or
            on failure), the release's source-config hash, and
            ``at_ms`` from the injected clock.

        Raises
        ------
        ProductionError
            A non-int tick, or — BEFORE any fetch — a source alias whose
            hash or version is not the one the release pinned.
        """
        if not _ms_ok(tick_at_ms):
            raise ProductionError([f"tick_at_ms must be an epoch-ms int, got {tick_at_ms!r}"])
        self._check_identity()
        try:
            acq_id, added = self._puller.fetch(self._root, self._registry, self._contract.source_binding)
            latest = self._latest_by_key(tick_at_ms)
        except (OSError, ValueError) as exc:
            _log.warning("pull failed, feed is dead: %s: %s", type(exc).__name__, exc)
            return self._result("dead", None, 0)
        return self._result(self._status(latest, tick_at_ms), acq_id, added)

    def _check_identity(self):
        """Refuse when the ACTIVE alias is not what the release pinned (D4)."""
        digest, version = active_source_identity(self._registry, self._contract.source_binding["source"])
        problems = []
        if digest != self._spec.source_config_hash:
            problems.append(
                f"source config drift: the release pinned {self._spec.source_config_hash!r}, "
                f"the ACTIVE alias resolves to {digest!r}"
            )
        if version != self._spec.source_config_version:
            problems.append(
                f"source config version drift: the release pinned {self._spec.source_config_version!r}, "
                f"the alias is at {version!r}"
            )
        if problems:
            raise ProductionError(problems)

    def _latest_by_key(self, tick_at_ms):
        """Read every entity's latest event time from the store; keys the store lacks are absent."""
        binding, recipe = self._contract.source_binding, self._spec.digest_recipe
        ts_out, entity = self._contract.event_time_field, self._contract.entity_key_fields
        unit = recipe.get("ts_unit", DEFAULT_TS_UNIT)
        reader = _STAMPS.get(unit)
        if reader is None:
            raise ProductionError([f"digest recipe names ts_unit {unit!r}, not one of {list(TS_UNITS)}"])
        kwargs = reader.scan_args(recipe, ts_out, tick_at_ms - self._dead_after_ms)
        if len(entity) == 1:
            kwargs["keep_values"] = {entity[0]: list(self._spec.required_keys)}
        records = observations.scan_stream(
            binding["root"],
            binding["source"],
            binding["stream"],
            key_fields=list(recipe.get("key_fields") or entity),
            **kwargs,
        )
        latest = {}
        for record in records:
            stamp = reader.stamp(record, recipe, ts_out)
            if _ms_ok(stamp):
                key = _entity_key(record, entity)
                latest[key] = max(latest.get(key, stamp), stamp)
        return latest

    def _status(self, latest, tick_at_ms):
        """Grade the OLDEST required key against the tick; a missing key is dead."""
        missing = [key for key in self._spec.required_keys if key not in latest]
        if missing:
            _log.warning("required key(s) absent from the store: %s", missing)
            return "dead"
        age = tick_at_ms - min(latest[key] for key in self._spec.required_keys)
        for bound, status in ((self._max_staleness_ms, "live"), (self._dead_after_ms, "stale")):
            if age <= bound:
                return status
        return "dead"

    def _result(self, status, acq_id, added):
        """Stamp one result with the release's source hash and the clock's reading."""
        return FeedResult(
            status=status,
            acq_id=acq_id,
            records_added=added,
            source_config_hash=self._spec.source_config_hash,
            at_ms=self._clock.now_ms(),
        )


# ---------------------------------------------------------------------------
# ReplayFeed — the recorded pulls, in order
# ---------------------------------------------------------------------------


class ReplayFeed(Feed):
    """Replay recorded pulls, in order, advancing the instant they were taken at.

    D20's feed: it touches neither store nor connector, and it is what
    makes ``ReplayClock`` work. That clock never advances itself — "the
    feed will" — because a replayed tick must be evaluated at the instant
    the recording evaluated it, and the feed is the first phase of a tick
    that knows which instant that was. Without the advance every replayed
    tick would evaluate at the process's start instant and every
    ``evidence_asof_ms`` would diverge for a reason that has nothing to do
    with the decision.

    The tape carries results ONLY. An ``EntryBatch`` is not on it because
    the rows are not the feed's to hold: §5.13 gives ``read_entry`` to the
    decider, which re-executes the entry against the same immutable
    onboarding root, and the recorded ``inputs_digest`` is what PROVES the
    re-read matched — a stronger claim than replaying a blob, which can
    only prove that the blob was replayed.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` only.
    tape : iterable of FeedResult
        The recorded pulls in tick order; a malformed entry refuses at
        construction.
    time : ManualTime or None, keyword-only
        The instant a :class:`~dskit.production.clock.ReplayClock` shares.
        Each pull sets it to that result's ``at_ms``. ``None`` replays the
        results without moving time, which is what a unit test wants.

    Examples
    --------
    ::

        feed = ReplayFeed({}, tape=[result], time=clock.time)
        feed.pull(result.at_ms) is result   # True
        clock.now_ms() == result.at_ms      # True
    """

    def __init__(self, params=None, *, tape, time=None):
        super().__init__(params)
        try:
            entries = list(tape)
        except TypeError as exc:
            raise ProductionError([f"tape must be an iterable of FeedResults: {exc}"]) from exc
        problems = [
            f"tape[{index}] must be a FeedResult, got {entry!r}"
            for index, entry in enumerate(entries)
            if not isinstance(entry, FeedResult)
        ]
        if time is not None and not isinstance(time, ManualTime):
            problems.append(f"time must be a ManualTime, got {time!r}")
        if problems:
            raise ProductionError(problems)
        self._tape = tuple(entries)
        self._time = time
        self._cursor = 0

    def pull(self, tick_at_ms):
        """Return the next recorded result, moving the shared instant to its own.

        Parameters
        ----------
        tick_at_ms : int
            Unused — replay runs on its recorded clock.

        Returns
        -------
        FeedResult
            The next entry.

        Raises
        ------
        ProductionError
            When the tape is exhausted — the replay asked for a tick the
            recording never took, which is a divergence, not an end.
        """
        if self._cursor >= len(self._tape):
            raise ProductionError([f"replay tape exhausted after {len(self._tape)} pull(s)"])
        result = self._tape[self._cursor]
        self._cursor += 1
        if self._time is not None:
            self._time.set(result.at_ms)
        return result


#: The feed family's open doorway (§4.3): a registered name or a
#: ``pkg.module:Class`` reference, both subclasses of :class:`Feed`.
FEED_KINDS = Registry("feed", Feed)
FEED_KINDS.register("entry-source", EntrySourceFeed)
FEED_KINDS.register("replay", ReplayFeed)
