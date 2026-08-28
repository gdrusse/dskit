"""The config layer — the owner's design plus frozen-dataclass hardening.

Design rules (agreed 2026-08-13, from the owner's sketch plus review):

1. **Shape vs. environment, two layers.** ``__post_init__`` validates SHAPE
   only (types, domains, cross-field consistency) — checkable on ANY
   machine, so configs can be built, serialized, hashed, and tested where
   the data does not live. Environment checks (paths exist, stores
   readable) live in ``resolve()``, called once on the machine that runs.
2. **An invalid config can never exist.** Validation runs unconditionally
   in ``__post_init__`` on every config — there is no separate
   ``validate()`` someone must remember to call, and no ABC forcing one.
3. **Errors accumulate.** A config reports EVERY shape problem in one
   :class:`ConfigError`, not just the first.
4. **Canonical round-trip + content hash.** Every config serializes via
   ``to_obj()`` (JSON-ready, key-stable) and reconstructs via
   ``from_obj()`` (unknown keys REJECTED loudly). ``config_hash()`` over
   the canonical JSON is the experiment's identity: same hash = same run.
   ``notes`` fields are documentation, excluded from the hash.
5. **Frozen + slots everywhere.** Configs are values, not state.
6. **Strategy slots are tagged families** (owner ruling, second design
   pass). A section where genuine alternatives exist is not "the fields
   of the one strategy we built first": it declares a ``kind`` naming the
   strategy plus that kind's own validated fields. Splits are a family of
   typed variants OWNED BY THE TOOLKIT (dispatch in
   :func:`split_from_obj`); optimization is ``kind + params`` with the
   semantics OWNED BY WHOEVER REGISTERS THE KIND (adapters register
   validators via :func:`register_optimizer_kind` — the toolkit ships no
   kinds of its own). ``kind`` and every variant field are hash-material.

There is deliberately NO ``TrainingConfig``: training-procedure knobs
(seed ensembles, epoch budgets) are strategy-specific, so they live in
``ModelConfig.params`` under the model family that interprets them —
a universal section that only fits epoch-trained models would be exactly
the frozen-first-strategy mistake rule 6 exists to prevent.

Venue-agnostic by construction: nothing here knows about order-book ladders
or daily bars — ``venue`` is a tag, ``instruments`` a tuple of opaque ids,
and open ``params``/``space`` dicts carry backend-specific knobs (they are
hash-material).

Import cost: stdlib only.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType

from dskit.pipeline.split_policy import (
    DEFAULT_SPLIT_POLICY,
    SPLIT_POLICIES,
    EventBounds,
    event_bounds_from_records,
    merge_event_bounds,
    policy_instant,
    register_split_policy,
)

__all__ = [
    "DEFAULT_SPLIT_POLICY",
    "SPLIT_POLICIES",
    "ConfigError",
    "EventBounds",
    "event_bounds_from_records",
    "merge_event_bounds",
    "policy_instant",
    "register_split_policy",
    "DataConfig",
    "EnvConfig",
    "FeatureConfig",
    "FeatureStepConfig",
    "HPOConfig",
    "ModelConfig",
    "NON_IDENTITY_SECTIONS",
    "NULLED_IDENTITY_SECTIONS",
    "OPTIMIZER_KINDS",
    "OutputsConfig",
    "OptimizationConfig",
    "PipelineConfig",
    "RandomSplitConfig",
    "SINK_KINDS",
    "SPLIT_KINDS",
    "STAGES",
    "SinkConfig",
    "StatTestConfig",
    "TimeSplitConfig",
    "TRANSFORM_KINDS",
    "TrackingConfig",
    "ValidationConfig",
    "abstract_class_problem",
    "config_hash",
    "import_library_class",
    "import_ref",
    "is_class_ref",
    "is_ref",
    "parse_ref",
    "parse_stage_entry",
    "register_optimizer_kind",
    "register_sink_kind",
    "register_transform_kind",
    "resolve_refs",
    "split_from_obj",
]


class ConfigError(ValueError):
    """Every shape problem a config has, reported at once.

    ``errors`` is the raw list; the message joins them one per line, each
    prefixed with the config path (e.g. ``data.data_dir``) so a nested
    failure names exactly where it lives.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(
            "invalid config ({} problem{}):\n  ".format(
                len(self.errors), "s" if len(self.errors) != 1 else ""
            )
            + "\n  ".join(self.errors)
        )


# ---------------------------------------------------------------------------
# Shared validation helpers — each APPENDS to an error list, never raises,
# so a config can report every problem at once (rule 3).
# ---------------------------------------------------------------------------


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_str(errors, name, value, *, non_empty=True):
    if not isinstance(value, str) or (non_empty and not value):
        errors.append(f"{name} must be a non-empty string, got {value!r}")


def _check_int(errors, name, value, *, ge=None):
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{name} must be an int, got {value!r}")
    elif ge is not None and value < ge:
        errors.append(f"{name} must be >= {ge}, got {value!r}")


def _check_fraction(errors, name, value, *, lo=0.0, hi=1.0, lo_open=True, hi_open=True):
    if not _is_num(value):
        errors.append(f"{name} must be a number, got {value!r}")
        return
    lo_ok = value > lo if lo_open else value >= lo
    hi_ok = value < hi if hi_open else value <= hi
    if not (lo_ok and hi_ok):
        lo_b, hi_b = "(" if lo_open else "[", ")" if hi_open else "]"
        errors.append(f"{name} must lie in {lo_b}{lo}, {hi}{hi_b}, got {value!r}")


def _check_str_tuple(errors, name, value, *, allow_empty=True):
    if not isinstance(value, tuple) or any(
        not isinstance(v, str) or not v for v in value
    ):
        errors.append(f"{name} must be a tuple of non-empty strings, got {value!r}")
    elif not allow_empty and not value:
        errors.append(f"{name} must be non-empty")


def _check_open_dict(errors, name, value):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        errors.append(f"{name} must be a dict with string keys, got {value!r}")


def _check_child(errors, name, value, cls):
    """Type-check a nested config; its own shape was validated at its birth."""
    if value is not None and not isinstance(value, cls):
        cls_name = (
            " or ".join(c.__name__ for c in cls)
            if isinstance(cls, tuple)
            else cls.__name__
        )
        errors.append(
            f"{name} must be a {cls_name} (or None), got {type(value).__name__}"
        )


def _reject_unknown(obj, allowed, where):
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ConfigError(
            [f"{where}: unknown key(s) {unknown} — allowed: {sorted(allowed)}"]
        )


def _raise_if(errors):
    if errors:
        raise ConfigError(errors)


#: Top-level config sections excluded from the identity hash (with every
#: ``notes`` key): ``env`` (where credentials live), ``outputs`` (where
#: artifacts land) and ``tracking`` (where METRICS land) say nothing
#: about WHAT the experiment computes — the ``run_dir`` precedent,
#: generalized. A tracking sink is placement exactly as ``outputs`` is:
#: the identity hash grades what the run COMPUTES, and repointing
#: telemetry at another store changes none of it.
NON_IDENTITY_SECTIONS = ("env", "outputs", "tracking")

#: The excluded sections whose KEY stays in the hash material, as
#: ``null``, instead of being removed with the section. ``tracking``
#: joined the exclusion above LATE — every ``to_obj`` has always emitted
#: a ``"tracking"`` key, so every hash ever written counted one, and
#: REMOVING it would move them all (orphaning every run directory and
#: stored artifact keyed to one). Rendering the section as UNDECLARED
#: excludes it just as completely — present/absent and one store versus
#: another all hash alike — and leaves the canonical JSON byte-identical.
#: Same reasoning as ``walkforward``, which is emitted only when present.
NULLED_IDENTITY_SECTIONS = ("tracking",)


def _strip_non_identity(
    obj, exclude=NON_IDENTITY_SECTIONS, nulled=NULLED_IDENTITY_SECTIONS
):
    """Drop the non-identity top-level sections from a config mapping."""
    if not isinstance(obj, dict):
        return obj
    for section in exclude:
        if section in nulled:
            if section in obj:
                obj[section] = None
        else:
            obj.pop(section, None)
    return obj


def config_hash(cfg, exclude=NON_IDENTITY_SECTIONS) -> str:
    """sha256 of a config's canonical JSON — the experiment's identity.

    Canonical form: sorted keys, no whitespace, ASCII escapes, NaN/Infinity
    refused (they are not JSON, and a hash that some writers can produce
    and others cannot is not an identity). ``notes`` keys are stripped at
    every nesting level before hashing — documentation must never change
    what an experiment IS — and the top-level ``exclude`` sections
    (default :data:`NON_IDENTITY_SECTIONS`) are stripped with them,
    except those :data:`NULLED_IDENTITY_SECTIONS` renders as undeclared
    instead of removing. The node-map document grammar passes its own
    exclusion list (env/outputs/tracking plus the provenance-only
    ``schedule``).
    """
    obj = _strip_non_identity(_strip_notes(cfg.to_obj()), exclude)
    try:
        canon = json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except ValueError as exc:
        raise ConfigError(
            [
                f"config is not canonically serializable (NaN/Infinity in an open dict?): {exc}"
            ]
        ) from exc
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def _strip_notes(obj):
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if k != "notes"}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


def _dataclass_to_obj(cfg) -> dict:
    """Default ``to_obj``: field order preserved, nested configs recursed,
    tuples -> lists, open dicts deep-copied (never shared with the caller)."""
    out = {}
    for f in fields(cfg):
        v = getattr(cfg, f.name)
        if hasattr(v, "to_obj"):
            v = v.to_obj()
        elif isinstance(v, tuple):
            v = list(v)
        elif isinstance(v, dict):
            v = copy.deepcopy(v)
        out[f.name] = v
    return out


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataConfig:
    """Where the data lives and what universe the run covers.

    ``venue`` is an opaque backend tag (``"markets"``, ``"equities"``, ...)
    that selects which :mod:`~dskit.pipeline.protocols`
    implementations the runner binds — an adapter package must have
    REGISTERED the tag before resolution. ``instruments`` are venue-native
    ids (series tickers, symbols); empty = the backend's
    auto-universe rule, materialized at resolve time.
    """

    venue: str
    data_dir: str
    instruments: tuple = ()
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "data.venue", self.venue)
        _check_str(errors, "data.data_dir", self.data_dir)
        _check_str_tuple(errors, "data.instruments", self.instruments)
        _check_str(errors, "data.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def resolve(self) -> str:
        """Environment check: the data root must exist HERE. Returns the
        absolute path. Shape stays machine-independent (rule 1)."""
        root = os.path.abspath(os.path.expanduser(self.data_dir))
        if not os.path.isdir(root):
            raise FileNotFoundError(
                f"data.data_dir does not exist on this machine: {root}"
            )
        return root

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "DataConfig":
        _reject_unknown(obj, ("venue", "data_dir", "instruments", "notes"), "data")
        return cls(
            venue=obj.get("venue", ""),
            data_dir=obj.get("data_dir", ""),
            instruments=tuple(obj.get("instruments", ())),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Splits — a TYPED FAMILY owned by the toolkit (rule 6). Every variant is a
# frozen config with its own shape rules, plus one behavior: ``split_of``,
# the pure assignment function every stage keys off. Variants dispatch
# through ``split_from_obj`` on the hash-material ``kind`` tag.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeSplitConfig:
    """Causal temporal cuts: train < validation < test, as epoch-ms instants.

    The default for market venues — and the ONLY split kind a causal
    backend should declare support for (a random split of time-series
    events puts the test set's future inside the training past; backends
    refuse unsupported kinds at resolve, so the doctrine is enforced where
    the venue is known, not by narrowing the toolkit).
    Strictly ascending by construction — a leaky cut cannot exist.

    WHERE the cuts are is this config; WHICH INSTANT each record is cut on
    is the separate, declared ``policy`` (:mod:`dskit.pipeline.split_policy`).

    ``val_start_ms`` (optional, ADR-0027) opens an EMBARGO band: records
    in ``(train_end_ms, val_start_ms)`` belong to NO split — the guard
    for labels that have not resolved by the cut (a record whose outcome
    is still in flight at ``train_end`` must neither train nor validate).
    Absent, validation starts right after ``train_end_ms`` — the
    original semantics, byte for byte (the field is omitted from
    ``to_obj`` when unset, so existing identities do not move). The band
    is applied to the instant the ``policy`` selects, so an event-cut
    record is embargoed on its event's instant, not its own.

    ``cal_start_ms`` (optional, ADR-0034) declares a CALIBRATION band as
    the tail of the val window: ``split_of`` returns a fourth name,
    ``"cal"``, for ``[cal_start_ms, val_end_ms]``, and val shrinks to
    ``[val_start_ms, cal_start_ms)``. The ordering is leakage-correct by
    construction — ``train < embargo < val < cal < test`` — so a
    calibrator fits strictly after training data, on rows disjoint from
    the selection (val) set, and strictly before everything it is
    applied to. Same omission discipline as ``val_start_ms``, same
    policy interaction (the band cuts the policy-selected instant).
    """

    train_end_ms: int
    val_end_ms: int
    test_end_ms: int
    kind: str = "time"
    #: WHICH INSTANT the cuts are applied to. ``"record"`` (the default, and
    #: the behaviour this class always had) cuts each record on its own
    #: ``asof_ms``, so a long-lived event contributes records to two splits
    #: — the event is the independence unit everywhere else in the toolkit,
    #: so that is a leak. ``"event-close"`` cuts every record of an event on
    #: the event's last observed instant, landing the event wholly in one
    #: split. Hash-material WHEN DECLARED (see :meth:`to_obj`).
    policy: str = DEFAULT_SPLIT_POLICY
    notes: str = ""
    #: Resolved ``cluster -> EventBounds``, bound by the driver from the data
    #: nodes' ``event_bounds()`` — DERIVED, never declared, never identity
    #: (it is a function of data the run's fingerprint already covers).
    #: ``None`` until bound; an event policy refuses rather than guessing.
    event_bounds: object = None
    val_start_ms: object = None
    cal_start_ms: object = None

    def __post_init__(self):
        errors = []
        if self.kind != "time":
            errors.append(f"splits.kind must be 'time', got {self.kind!r}")
        for name in ("train_end_ms", "val_end_ms", "test_end_ms"):
            _check_int(errors, f"splits.{name}", getattr(self, name), ge=1)
        if self.policy not in SPLIT_POLICIES:
            errors.append(
                f"splits.policy: unknown policy {self.policy!r} — known "
                f"policies: {sorted(SPLIT_POLICIES)}"
            )
        if self.event_bounds is not None and not isinstance(self.event_bounds, Mapping):
            errors.append(
                "splits.event_bounds must be a mapping of cluster -> "
                f"EventBounds, got {self.event_bounds!r}"
            )
        if not errors and not (self.train_end_ms < self.val_end_ms < self.test_end_ms):
            errors.append(
                "splits must be strictly ascending: train_end_ms < val_end_ms "
                f"< test_end_ms, got ({self.train_end_ms}, {self.val_end_ms}, "
                f"{self.test_end_ms})"
            )
        if self.val_start_ms is not None:
            _check_int(errors, "splits.val_start_ms", self.val_start_ms, ge=1)
            if not errors and not (
                self.train_end_ms < self.val_start_ms <= self.val_end_ms
            ):
                errors.append(
                    "splits.val_start_ms must open an embargo band inside the "
                    "val window: train_end_ms < val_start_ms <= val_end_ms, "
                    f"got ({self.train_end_ms}, {self.val_start_ms}, "
                    f"{self.val_end_ms})"
                )
        if self.cal_start_ms is not None:
            _check_int(errors, "splits.cal_start_ms", self.cal_start_ms, ge=1)
            if not errors:
                # Strict on the left: an empty val band would mean models
                # are selected on nothing — a cal-but-no-val design is a
                # different experiment and refuses rather than passing
                # silently.
                floor = (
                    self.val_start_ms
                    if self.val_start_ms is not None
                    else self.train_end_ms
                )
                if not floor < self.cal_start_ms <= self.val_end_ms:
                    errors.append(
                        "splits.cal_start_ms must carve the cal band out of "
                        "the tail of the val window: "
                        "(val_start_ms or train_end_ms) < cal_start_ms <= "
                        f"val_end_ms, got ({floor}, {self.cal_start_ms}, "
                        f"{self.val_end_ms})"
                    )
        _check_str(errors, "splits.notes", self.notes, non_empty=False)
        _raise_if(errors)

    @property
    def needs_event_bounds(self) -> bool:
        """Whether this split's policy reads the event-bounds map — the
        question the driver asks BEFORE paying to build one."""
        return SPLIT_POLICIES[self.policy]["needs_bounds"]

    def with_event_bounds(self, bounds) -> "TimeSplitConfig":
        """This split with ``bounds`` bound — a NEW frozen config, never a
        mutation, and the mapping is proxied so the caller's dict cannot
        change an assignment after the fact."""
        return replace(
            self,
            event_bounds=None if bounds is None else MappingProxyType(dict(bounds)),
        )

    def split_of(self, record):
        """Assign by the instant this split's ``policy`` selects —
        ``"train"``/``"val"``/``"cal"``/``"test"``, or ``None`` beyond the
        horizon, or ``None`` inside the embargo band when ``val_start_ms``
        is set (an embargoed record belongs to NO split, ADR-0027). The
        ``"cal"`` name only exists when ``cal_start_ms`` is declared
        (ADR-0034).

        Under the default ``"record"`` policy that instant IS
        ``record.asof_ms``, so behaviour is unchanged."""
        t = policy_instant(self.policy, record, self.event_bounds)
        if t is None:
            return None
        if t <= self.train_end_ms:
            return "train"
        if self.val_start_ms is not None and t < self.val_start_ms:
            return None
        if t <= self.val_end_ms:
            if self.cal_start_ms is not None and t >= self.cal_start_ms:
                return "cal"
            return "val"
        if t <= self.test_end_ms:
            return "test"
        return None

    def to_obj(self) -> dict:
        """Serialized form. ``event_bounds`` is dropped (derived, and far too
        large to be config); ``policy`` is dropped WHEN IT IS THE DEFAULT, and
        ``val_start_ms``/``cal_start_ms`` are dropped WHEN UNSET, so that
        adding these knobs does not silently change the identity hash of
        every run that ever ran. A document that DECLARES a policy, an
        embargo, or a cal band carries it into the hash, which is correct:
        each is a different experiment from the plain three-way cut."""
        obj = _dataclass_to_obj(self)
        obj.pop("event_bounds", None)
        if obj.get("policy") == DEFAULT_SPLIT_POLICY:
            obj.pop("policy", None)
        if self.val_start_ms is None:
            del obj["val_start_ms"]
        if self.cal_start_ms is None:
            del obj["cal_start_ms"]
        return obj

    @classmethod
    def from_obj(cls, obj) -> "TimeSplitConfig":
        _reject_unknown(
            obj,
            (
                "kind",
                "train_end_ms",
                "val_start_ms",
                "cal_start_ms",
                "val_end_ms",
                "test_end_ms",
                "policy",
                "notes",
            ),
            "splits",
        )
        return cls(
            train_end_ms=obj.get("train_end_ms", 0),
            val_end_ms=obj.get("val_end_ms", 0),
            test_end_ms=obj.get("test_end_ms", 0),
            kind=obj.get("kind", "time"),
            policy=obj.get("policy", DEFAULT_SPLIT_POLICY),
            notes=obj.get("notes", ""),
            val_start_ms=obj.get("val_start_ms"),
            cal_start_ms=obj.get("cal_start_ms"),
        )


@dataclass(frozen=True, slots=True)
class RandomSplitConfig:
    """Randomized assignment for venues/experiments where exchangeability
    holds (cross-sectional studies, deliberately non-causal baselines).

    Randomization is ALWAYS at CLUSTER level — the record's ``cluster``
    (its dependence group: an event, a trading day), never the
    individual record: records within a cluster are correlated, so a
    record-level shuffle leaks by construction in ANY venue. Assignment is
    a pure hash of ``(seed, cluster)`` — deterministic across machines and
    runs, no RNG state — and ``seed`` is hash-material, so re-seeding is a
    different experiment. The test fraction is the remainder
    ``1 - train_frac - val_frac`` and must be positive.

    Carries no ``policy``: hashing the cluster makes this kind EVENT-ATOMIC
    by construction, so the straddle :mod:`dskit.pipeline.split_policy`
    exists to close cannot happen here. The policy is a TIME-family knob
    because only a time cut can land in the middle of an event.
    """

    train_frac: float
    val_frac: float
    seed: int = 0
    kind: str = "random"
    notes: str = ""

    def __post_init__(self):
        errors = []
        if self.kind != "random":
            errors.append(f"splits.kind must be 'random', got {self.kind!r}")
        _check_fraction(errors, "splits.train_frac", self.train_frac)
        _check_fraction(errors, "splits.val_frac", self.val_frac)
        if not errors and not self.train_frac + self.val_frac < 1.0:
            errors.append(
                "splits must leave a positive test remainder: train_frac + "
                f"val_frac must be < 1, got {self.train_frac} + {self.val_frac}"
            )
        _check_int(errors, "splits.seed", self.seed, ge=0)
        _check_str(errors, "splits.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def split_of(self, record):
        """Assign by deterministic hash of the record's ``cluster``."""
        digest = hashlib.sha256(f"{self.seed}:{record.cluster}".encode()).digest()
        u = int.from_bytes(digest[:8], "big") / 2**64
        if u < self.train_frac:
            return "train"
        if u < self.train_frac + self.val_frac:
            return "val"
        return "test"

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "RandomSplitConfig":
        _reject_unknown(
            obj, ("kind", "train_frac", "val_frac", "seed", "notes"), "splits"
        )
        return cls(
            train_frac=obj.get("train_frac", 0.0),
            val_frac=obj.get("val_frac", 0.0),
            seed=obj.get("seed", 0),
            kind=obj.get("kind", "random"),
            notes=obj.get("notes", ""),
        )


#: The split family: ``kind`` tag -> variant class. Toolkit-owned; a new
#: variant (walk-forward, purged K-fold) is a new entry here, and every
#: consumer keeps working through ``split_of``.
SPLIT_KINDS = {"time": TimeSplitConfig, "random": RandomSplitConfig}


def split_from_obj(obj):
    """Reconstruct a split variant from its serialized form, dispatching on
    the ``"kind"`` tag. Unknown or missing kinds fail loudly, naming the
    family."""
    if not isinstance(obj, dict) or "kind" not in obj:
        raise ConfigError(
            [f"splits: a serialized split must carry a 'kind' key, got {obj!r}"]
        )
    cls = SPLIT_KINDS.get(obj["kind"])
    if cls is None:
        raise ConfigError(
            [
                f"splits: unknown kind {obj['kind']!r} — known kinds: "
                f"{sorted(SPLIT_KINDS)}"
            ]
        )
    return cls.from_obj(obj)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """What model to build and where its artifacts land.

    ``name`` tags the model family; ``params`` is the open, hash-material
    knob dict that family interprets — architecture, features, AND
    training-procedure knobs (seed ensembles, epoch budgets): there is no
    separate TrainingConfig, because training procedure is strategy-
    specific (rule 6), not a universal section.
    """

    name: str
    model_dir: str
    params: dict = field(default_factory=dict)
    seed: int = 0
    #: "train" fits a fresh model; "load" runs inference from a pinned
    #: ``artifact`` (path, absolute or under ``model_dir``) — WHICH
    #: artifact you run is identity, so both fields are hash-material.
    mode: str = "train"
    artifact: str = ""
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "model.name", self.name)
        _check_str(errors, "model.model_dir", self.model_dir)
        _check_open_dict(errors, "model.params", self.params)
        _check_int(errors, "model.seed", self.seed, ge=0)
        if self.mode not in ("train", "load"):
            errors.append(f"model.mode must be 'train' or 'load', got {self.mode!r}")
        elif self.mode == "load" and not self.artifact:
            errors.append(
                "model.artifact is required when mode='load' — pin the exact "
                "artifact inference runs from"
            )
        elif self.mode == "train" and self.artifact:
            errors.append(
                "model.artifact must be empty when mode='train' (a stray pin "
                "that training ignores is a config lie)"
            )
        if not isinstance(self.artifact, str):
            errors.append(f"model.artifact must be a string, got {self.artifact!r}")
        _check_str(errors, "model.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def resolve(self, *, create: bool = False) -> str:
        """Environment check: the model dir exists (or is created when the
        caller says this machine OWNS it). Returns the absolute path."""
        root = os.path.abspath(os.path.expanduser(self.model_dir))
        if not os.path.isdir(root):
            if not create:
                raise FileNotFoundError(
                    f"model.model_dir does not exist on this machine: {root} "
                    "(pass create=True on the training machine)"
                )
            os.makedirs(root, exist_ok=True)
        return root

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "ModelConfig":
        _reject_unknown(
            obj,
            ("name", "model_dir", "params", "seed", "mode", "artifact", "notes"),
            "model",
        )
        return cls(
            name=obj.get("name", ""),
            model_dir=obj.get("model_dir", ""),
            params=dict(obj.get("params", {})),
            seed=obj.get("seed", 0),
            mode=obj.get("mode", "train"),
            artifact=obj.get("artifact", ""),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Validation / HPO / stat test — wrapper-owned semantics, typed fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """The out-of-sample bar a model must clear before anything downstream.

    ``baseline`` names the null the model must beat (market venues: the
    market-implied price; an equities venue: e.g. buy-and-hold or flat) — resolved by
    the backend. ``min_events`` is the testability floor per instrument —
    below it, no verdict, no deployment.
    """

    baseline: str = "market"
    metric: str = "logloss"
    min_events: int = 50
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "validation.baseline", self.baseline)
        _check_str(errors, "validation.metric", self.metric)
        _check_int(errors, "validation.min_events", self.min_events, ge=2)
        _check_str(errors, "validation.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "ValidationConfig":
        _reject_unknown(
            obj, ("baseline", "metric", "min_events", "notes"), "validation"
        )
        return cls(
            baseline=obj.get("baseline", "market"),
            metric=obj.get("metric", "logloss"),
            min_events=obj.get("min_events", 50),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class HPOConfig:
    """Hyperparameter search: budget, seeds, and the open search space.

    Selection discipline travels with the config: HPO selects on the
    VALIDATION split only — the test split is never consulted (a strict
    house rule, and just as binding for any other venue).
    """

    n_trials: int = 1
    seeds: tuple = (0,)
    space: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_int(errors, "hpo.n_trials", self.n_trials, ge=1)
        if (
            not isinstance(self.seeds, tuple)
            or not self.seeds
            or any(isinstance(s, bool) or not isinstance(s, int) for s in self.seeds)
        ):
            errors.append(
                f"hpo.seeds must be a non-empty tuple of ints, got {self.seeds!r}"
            )
        _check_open_dict(errors, "hpo.space", self.space)
        _check_str(errors, "hpo.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "HPOConfig":
        _reject_unknown(obj, ("n_trials", "seeds", "space", "notes"), "hpo")
        return cls(
            n_trials=obj.get("n_trials", 1),
            seeds=tuple(obj.get("seeds", (0,))),
            space=dict(obj.get("space", {})),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class StatTestConfig:
    """The edge test: is the signal real, before any capital?

    Pre-registered by hash: alpha, the multiplicity correction across the
    instrument family, and the bootstrap budget/seed all change the
    experiment's identity. Per-instrument hypotheses stay per-instrument —
    pooling for power is not an option in ANY venue this wraps.
    """

    alpha: float = 0.05
    correction: str = "bh"
    n_boot: int = 10_000
    seed: int = 0
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_fraction(errors, "stat_test.alpha", self.alpha)
        _check_str(errors, "stat_test.correction", self.correction)
        _check_int(errors, "stat_test.n_boot", self.n_boot, ge=1)
        _check_int(errors, "stat_test.seed", self.seed, ge=0)
        _check_str(errors, "stat_test.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "StatTestConfig":
        _reject_unknown(
            obj, ("alpha", "correction", "n_boot", "seed", "notes"), "stat_test"
        )
        return cls(
            alpha=obj.get("alpha", 0.05),
            correction=obj.get("correction", "bh"),
            n_boot=obj.get("n_boot", 10_000),
            seed=obj.get("seed", 0),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Optimization — ``kind`` + open params; semantics owned by the registrant
# ---------------------------------------------------------------------------

#: ``kind -> validator(params) -> iterable of error strings``. The toolkit
#: ships NO kinds: adapters register theirs (e.g. an adapter
#: registers "fractional-kelly-mio" and its knob schema) so validation is
#: as strict as the machine's imports allow — the same machine-independence
#: argument as rule 1. Messages should name fields as
#: ``optimization.params.<key>``.
OPTIMIZER_KINDS = {}


def register_optimizer_kind(kind, validator) -> None:
    """Bind an optimizer ``kind`` to its params validator. Duplicate kinds
    raise — two validators silently fighting over one kind is a parallel
    path, not an extension."""
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"optimizer kind must be a non-empty string, got {kind!r}")
    if not callable(validator):
        raise ValueError(f"validator for {kind!r} must be callable, got {validator!r}")
    if kind in OPTIMIZER_KINDS:
        raise ValueError(f"optimizer kind {kind!r} is already registered")
    OPTIMIZER_KINDS[kind] = validator


@dataclass(frozen=True, slots=True)
class OptimizationConfig:
    """The allocation step, as a tagged strategy (rule 6).

    ``kind`` names the optimizer family ("fractional-kelly-mio",
    "mean-variance", ...); ``params`` carries that family's knobs — nothing
    is universal enough to be a fixed field here (bankroll is a Kelly/cash
    notion; a risk-budget optimizer has no such knob). When ``kind`` is
    registered (:data:`OPTIMIZER_KINDS`), its validator runs at
    construction with accumulated errors; an unregistered kind passes
    shape-only here (its adapter may not be importable on this machine)
    and MUST be claimed by the bound backend at resolve time, or
    resolution fails loudly. Both ``kind`` and ``params`` are
    hash-material.
    """

    kind: str
    params: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "optimization.kind", self.kind)
        _check_open_dict(errors, "optimization.params", self.params)
        _check_refs(errors, "optimization.params", self.params)
        if not errors and is_class_ref(self.kind):
            errors.extend(
                _ref_param_errors(self.kind, self.params, "optimization.params")
            )
        elif not errors and self.kind in OPTIMIZER_KINDS:
            errors.extend(OPTIMIZER_KINDS[self.kind](self.params))
        _check_str(errors, "optimization.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "OptimizationConfig":
        _reject_unknown(obj, ("kind", "params", "notes"), "optimization")
        return cls(
            kind=obj.get("kind", ""),
            params=dict(obj.get("params", {})),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Imported-component references (owner ruling, fifth design pass): anywhere
# a ``kind`` names a registered component, ``"pkg.module:Attr"`` may name
# YOUR OWN one instead — the colon distinguishes an import path from a
# registered name. The reference string + its params are hash-material
# (they declare the component); the imported object must satisfy the
# slot's seam contract, checked structurally at resolve. Trust posture is
# UNRESTRICTED by owner decision: configs are repo-resident, owner-
# authored documents — a config file is code-adjacent, so importing what
# it names is no more dangerous than running the repo. Construction-time
# strictness mirrors registered kinds: when the module imports HERE and
# the target exposes ``validate_params(params) -> errors``, it runs with
# accumulated errors; otherwise shape-only (the machine-independence
# argument of rule 1), settled at resolve.
# ---------------------------------------------------------------------------

_CLASS_REF_OK = r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$"


def is_class_ref(kind) -> bool:
    """True iff ``kind`` is an import reference (``pkg.module:Attr``)."""
    return isinstance(kind, str) and bool(re.match(_CLASS_REF_OK, kind))


def import_ref(ref):
    """Import and return the referenced object; failures name the ref."""
    module_name, attr = ref.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import component reference {ref!r}: {exc}") from exc
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ValueError(
            f"component reference {ref!r}: module {module_name!r} has no "
            f"attribute {attr!r}"
        ) from None


def library_path_problems(name, value, *, example):
    """Problems with a DECLARED LIBRARY CLASS path — shape only, at plan.

    The document-facing grammar for "name me a class from some library"
    (an estimator, an ``nn.Module``, an HF config). Two spellings are
    accepted so a document author never has to remember which doorway
    wants which: the dotted ``pkg.module.ClassName``
    :func:`import_library_class` splits on the last dot, and the colon
    form ``pkg.module:ClassName`` that :func:`import_ref` already uses for
    component references. Whether it IMPORTS is deliberately not checked
    here — the library may legitimately be absent on the planning
    machine, which is why this is shape-only and the resolution happens at
    execute.
    """
    if not isinstance(value, str) or not value:
        return [f"{name} must be a class path string like {example!r}, got {value!r}"]
    head, sep, attr = value.rpartition(":") if ":" in value else value.rpartition(".")
    if not sep or not attr.isidentifier():
        return [
            f"{name} must name a class as module.ClassName or module:ClassName "
            f"(like {example!r}), got {value!r}"
        ]
    if not head or not all(p.isidentifier() for p in head.split(".")):
        return [
            f"{name} must name an importable module before the class "
            f"(like {example!r}), got {value!r}"
        ]
    return []


def abstract_class_problem(cls, where, subject=None):
    """Why ``cls`` cannot be constructed, when hooks are still abstract.

    The ONE place this repo words that defect. Three doorways ask it
    today, and that list is exhaustive:
    :func:`~dskit.pipeline.node.node_class_errors` (which serves BOTH
    kind registration and ``uses`` import-path resolution, so a Node
    reference is refused at whichever boundary it enters through), and
    the torch pack's adapter doorway twice — at run
    (``_DeclaredParams.build_adapter``) and at plan
    (``_DeclaredParams.validate_params``), saying the same sentence in
    both. Asking every such door to say core's sentence is what keeps it
    from drifting between sites and a pack from re-deriving it.

    Not yet universal, and deliberately not claimed to be:
    ``_DeclaredModule.build_module`` constructs the document's declared
    ``nn.Module`` WITHOUT asking, so an abstract module class is still
    reported there as its constructor's ``TypeError``, rewrapped as a
    "rejected module_params" refusal — the same mis-diagnosis the adapter
    doorway was fixed for. Queued for the ``TrainableNode`` /
    long-method decomposition work (TODO 3c/3d), which rebuilds that
    doorway.

    The one resolver that deliberately does NOT ask is
    :func:`import_library_class`: its ``ValueError`` already means "the
    library may rightly be missing on this machine" — a meaning
    plan-time callers swallow, which would swallow this refusal with it.
    A doorway that wants the refusal asks AFTER resolving, on a channel
    of its own: its own raise, or a problems list.

    Parameters
    ----------
    cls : type
        The class to check. Anything without ``__abstractmethods__`` (a
        non-ABC, a plain object) is fine by definition.
    where : str
        The site being reported, prefixed to the message so a refusal
        says which door it came from.
    subject : str or None
        How to NAME the class in the message; defaults to ``cls.__name__``.
        A doorway resolving a declared path passes ``repr(path)`` instead,
        because the path is what the document actually wrote.

    Returns
    -------
    str or None
        The problem, naming every unimplemented hook in sorted order, or
        ``None`` when the class is complete.
    """
    missing = getattr(cls, "__abstractmethods__", None)
    if not missing:
        return None
    name = subject if subject is not None else getattr(cls, "__name__", cls)
    return f"{where}: {name} is abstract (missing {sorted(missing)})"


def import_library_class(path, where, *, requires=()):
    """The class behind a declared library path, or a refusal naming it.

    Accepts both spellings :func:`library_path_problems` allows. Import
    failure is reported as the honest "library not installed, or the path
    is a typo" answer AT EXECUTE, where the library is due — never at
    plan, where the library may rightly be missing.

    ``requires`` names methods the class must expose (``("fit",)`` for an
    estimator); a class lacking one is refused BY NAME rather than
    failing later inside a training loop, where the cause would be much
    harder to read.
    """
    module_name, _, cls_name = (
        path.rpartition(":") if ":" in path else path.rpartition(".")
    )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            f"{where}: cannot import {path!r} ({exc}) — is the library "
            "installed on this machine, and the path spelled as "
            "module.ClassName?"
        ) from exc
    cls = getattr(module, cls_name, None)
    if cls is None:
        raise ValueError(
            f"{where}: module {module_name!r} has no attribute {cls_name!r} — "
            f"{path!r} does not exist"
        )
    for method in requires:
        if not callable(getattr(cls, method, None)):
            raise ValueError(
                f"{where}: {path!r} has no {method}() method — not usable here"
            )
    return cls


def _ref_param_errors(ref, params, where):
    """Construction-time validation for a class reference: run the target's
    ``validate_params`` when it is importable here; silently defer when it
    is not (resolve settles it on the machine that runs)."""
    try:
        target = import_ref(ref)
    except ValueError:
        return []
    validator = getattr(target, "validate_params", None)
    if validator is None:
        return []
    return [f"{where}: {m}" for m in validator(params)]


# ---------------------------------------------------------------------------
# Cross-stage references — "some params are hardcoded, some are outputs
# from earlier in the pipeline" (owner ruling, third design pass). A param
# value may be ``{"$from": "<stage>.<path...>"}``; the REFERENCE is
# hash-material (it declares the wiring), the runtime value is not. The
# Runner resolves references against the producing stage's outputs and
# fails loudly on anything unknown.
# ---------------------------------------------------------------------------

#: The reference sentinel key.
REF_KEY = "$from"


def is_ref(value) -> bool:
    """True iff ``value`` is a cross-stage reference ``{"$from": "..."}``."""
    return isinstance(value, dict) and set(value) == {REF_KEY}


def parse_ref(value):
    """Split a reference into ``(stage, path)``; raises on bad grammar.

    Grammar only — whether the stage is actually DECLARED (canonical or a
    named custom stage) is a whole-config fact, cross-checked by
    :class:`PipelineConfig` where the stage list is visible."""
    target = value[REF_KEY]
    parts = target.split(".") if isinstance(target, str) else []
    if len(parts) < 2 or not all(parts):
        raise ConfigError(
            [
                f"$from must be '<stage>.<output-path>', got {target!r} "
                "(e.g. 'stat_test.survivors')"
            ]
        )
    return parts[0], parts[1:]


def _collect_ref_stages(obj, acc):
    """Gather every $from target stage nested in ``obj`` into ``acc``."""
    if is_ref(obj):
        acc.add(obj[REF_KEY].split(".", 1)[0])
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_ref_stages(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_ref_stages(v, acc)


def _check_refs(errors, name, obj):
    """Accumulate grammar errors for every reference nested in ``obj``."""
    if is_ref(obj):
        try:
            parse_ref(obj)
        except ConfigError as exc:
            errors.extend(f"{name}: {e}" for e in exc.errors)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_refs(errors, f"{name}.{k}", v)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_refs(errors, f"{name}[{i}]", v)


def resolve_refs(params, outputs):
    """Deep-copy ``params`` with every ``$from`` replaced by the referenced
    stage output. Raises ``ValueError`` naming what exists when a stage or
    path is missing — a dangling wire is a config bug, never a default."""

    def walk(node, name):
        if is_ref(node):
            stage, path = parse_ref(node)
            if stage not in outputs:
                raise ValueError(
                    f"{name}: $from {node[REF_KEY]!r} — stage {stage!r} has not "
                    f"produced outputs (available: {sorted(outputs)})"
                )
            value = outputs[stage]
            for key in path:
                if not isinstance(value, dict) or key not in value:
                    raise ValueError(
                        f"{name}: $from {node[REF_KEY]!r} — no output {key!r} "
                        f"under {stage!r} (available: "
                        f"{sorted(value) if isinstance(value, dict) else type(value).__name__})"
                    )
                value = value[key]
            return value
        if isinstance(node, dict):
            return {k: walk(v, f"{name}.{k}") for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{name}[{i}]") for i, v in enumerate(node)]
        return copy.deepcopy(node)

    return walk(params, "params")


# ---------------------------------------------------------------------------
# Features — the declared tensor-building recipe (rule 6, two-layer per the
# owner ruling): an ORDERED list of tagged steps. Stream-level kinds
# (filter, regroup, ...) are toolkit-owned — registered with an ``apply``
# in :mod:`dskit.pipeline.features` and run venue-neutrally on the
# record stream before ANY consumer sees it. Tensor-building kinds are
# registered WITHOUT an apply by the model family/adapter that owns them,
# must be claimed via ``backend.supported_transform_kinds`` at resolve,
# and are interpreted inside the backend's train/inference stages.
# ---------------------------------------------------------------------------

#: ``kind -> {"validator": fn, "apply": fn | None}``. ``apply(records,
#: params)`` yields transformed records (stream kinds); ``None`` marks a
#: backend-owned tensor step.
TRANSFORM_KINDS = {}


def register_transform_kind(kind, validator, apply=None) -> None:
    """Bind a transform ``kind`` to its params validator (and, for stream
    kinds, its apply). Duplicates raise."""
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"transform kind must be a non-empty string, got {kind!r}")
    if not callable(validator):
        raise ValueError(f"validator for {kind!r} must be callable, got {validator!r}")
    if apply is not None and not callable(apply):
        raise ValueError(f"apply for {kind!r} must be callable, got {apply!r}")
    if kind in TRANSFORM_KINDS:
        raise ValueError(f"transform kind {kind!r} is already registered")
    TRANSFORM_KINDS[kind] = {"validator": validator, "apply": apply}


@dataclass(frozen=True, slots=True)
class FeatureStepConfig:
    """One step of the feature recipe: ``kind`` + that kind's params."""

    kind: str
    params: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "features.step.kind", self.kind)
        _check_open_dict(errors, f"features.{self.kind}.params", self.params)
        _check_refs(errors, f"features.{self.kind}.params", self.params)
        if not errors and is_class_ref(self.kind):
            errors.extend(
                _ref_param_errors(self.kind, self.params, f"features.{self.kind}")
            )
        elif not errors and self.kind in TRANSFORM_KINDS:
            errors.extend(
                f"features.{self.kind}: {m}"
                for m in TRANSFORM_KINDS[self.kind]["validator"](self.params)
            )
        _check_str(errors, "features.step.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "FeatureStepConfig":
        _reject_unknown(obj, ("kind", "params", "notes"), "features.step")
        return cls(
            kind=obj.get("kind", ""),
            params=dict(obj.get("params", {})),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """The ordered feature/transform recipe (see section comment above)."""

    steps: tuple
    notes: str = ""

    def __post_init__(self):
        errors = []
        if not isinstance(self.steps, tuple) or not self.steps:
            errors.append(
                "features.steps must be a non-empty tuple of steps — omit the "
                "features section entirely for a raw stream"
            )
        else:
            for i, step in enumerate(self.steps):
                _check_child(errors, f"features.steps[{i}]", step, FeatureStepConfig)
        _check_str(errors, "features.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return {"steps": [s.to_obj() for s in self.steps], "notes": self.notes}

    @classmethod
    def from_obj(cls, obj) -> "FeatureConfig":
        _reject_unknown(obj, ("steps", "notes"), "features")
        return cls(
            steps=tuple(FeatureStepConfig.from_obj(s) for s in obj.get("steps", ())),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Tracking — WHAT gets tracked is the stages' payloads (numeric leaves);
# WHERE is declared here as tagged sinks. The toolkit ships the seam and a
# "memory" sink for tests; real sinks (mlflow) register application-side.
# ---------------------------------------------------------------------------

#: ``kind -> {"validator": fn, "factory": fn}``; ``factory(params)``
#: returns a :class:`~dskit.pipeline.protocols.Tracker`.
SINK_KINDS = {}


def register_sink_kind(kind, validator, factory) -> None:
    """Bind a tracking-sink ``kind``. Duplicates raise."""
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"sink kind must be a non-empty string, got {kind!r}")
    if not callable(validator) or not callable(factory):
        raise ValueError(f"validator and factory for {kind!r} must be callable")
    if kind in SINK_KINDS:
        raise ValueError(f"sink kind {kind!r} is already registered")
    SINK_KINDS[kind] = {"validator": validator, "factory": factory}


@dataclass(frozen=True, slots=True)
class SinkConfig:
    """One tracking destination: ``kind`` + that sink's params."""

    kind: str
    params: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "tracking.sink.kind", self.kind)
        _check_open_dict(errors, f"tracking.{self.kind}.params", self.params)
        if not errors and is_class_ref(self.kind):
            errors.extend(
                _ref_param_errors(self.kind, self.params, f"tracking.{self.kind}")
            )
        elif not errors and self.kind in SINK_KINDS:
            errors.extend(
                f"tracking.{self.kind}: {m}"
                for m in SINK_KINDS[self.kind]["validator"](self.params)
            )
        _check_str(errors, "tracking.sink.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "SinkConfig":
        _reject_unknown(obj, ("kind", "params", "notes"), "tracking.sink")
        return cls(
            kind=obj.get("kind", ""),
            params=dict(obj.get("params", {})),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class TrackingConfig:
    """Where run metrics land (run-dir artifacts are always written; sinks
    are ADDITIONAL destinations)."""

    sinks: tuple
    notes: str = ""

    def __post_init__(self):
        errors = []
        if not isinstance(self.sinks, tuple) or not self.sinks:
            errors.append(
                "tracking.sinks must be a non-empty tuple — omit the tracking "
                "section entirely for run-dir artifacts only"
            )
        else:
            for i, sink in enumerate(self.sinks):
                _check_child(errors, f"tracking.sinks[{i}]", sink, SinkConfig)
        _check_str(errors, "tracking.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return {"sinks": [s.to_obj() for s in self.sinks], "notes": self.notes}

    @classmethod
    def from_obj(cls, obj) -> "TrackingConfig":
        _reject_unknown(obj, ("sinks", "notes"), "tracking")
        return cls(
            sinks=tuple(SinkConfig.from_obj(s) for s in obj.get("sinks", ())),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Env + outputs — the two NON-IDENTITY sections (both excluded from the
# hash, the run_dir precedent): where credentials live and where
# artifacts land say nothing about WHAT the experiment computes, so
# changing them must never rename a run. Credential VALUES never appear
# anywhere in a config — the env section is a reference, materialized at
# use time by :func:`dskit.pipeline.env.load_env` into a redacting
# ``Secrets`` façade.
# ---------------------------------------------------------------------------

_ENV_NAME_OK = r"^[A-Za-z_][A-Za-z0-9_]*$"


@dataclass(frozen=True, slots=True)
class EnvConfig:
    """The security/credentials REFERENCE: an env file plus required names.

    ``env_file`` is loaded when present (process environment wins over
    it); every name in ``require`` must exist at resolve time or
    resolution fails listing all missing names. Values are never stored,
    hashed, or written to artifacts.
    """

    env_file: str = ".env"
    require: tuple = ()
    notes: str = ""

    def __post_init__(self):
        import re

        errors = []
        _check_str(errors, "env.env_file", self.env_file)
        _check_str_tuple(errors, "env.require", self.require)
        if not errors:
            bad = [n for n in self.require if not re.match(_ENV_NAME_OK, n)]
            if bad:
                errors.append(
                    f"env.require: not valid environment variable name(s): {bad}"
                )
        _check_str(errors, "env.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "EnvConfig":
        _reject_unknown(obj, ("env_file", "require", "notes"), "env")
        return cls(
            env_file=obj.get("env_file", ".env"),
            require=tuple(obj.get("require", ())),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class OutputsConfig:
    """Artifact placement. ``run_root`` overrides the default
    ``{data_root}/pipeline_runs`` tree; empty = derive. Placement is not
    identity (hash-excluded)."""

    run_root: str = ""
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "outputs.run_root", self.run_root, non_empty=False)
        _check_str(errors, "outputs.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "OutputsConfig":
        _reject_unknown(obj, ("run_root", "notes"), "outputs")
        return cls(run_root=obj.get("run_root", ""), notes=obj.get("notes", ""))


# ---------------------------------------------------------------------------
# Stages — the pipeline's stage list, EXPLICIT in the config (owner ruling,
# third design pass): the config names exactly what runs, in order. The
# toolkit still owns SOUNDNESS: prerequisites must appear earlier, so no
# ordering can put capital ahead of the stat test or scoring ahead of a
# signal — declarativeness never weakens the gates.
# ---------------------------------------------------------------------------

#: Every stage the Runner knows, in canonical order.
STAGES = ("train", "validate", "stat_test", "optimize", "backtest")

#: Hard prerequisites: each stage requires these EARLIER in the list.
#: optimize/backtest sit behind stat_test — capital never precedes the
#: edge test, whatever the config says.
_STAGE_PREREQS = {
    "validate": ("train",),
    "stat_test": ("validate",),
    "optimize": ("stat_test",),
    "backtest": ("stat_test",),
}


_STAGE_NAME_OK = r"^[a-z_][a-z0-9_]*$"


def parse_stage_entry(entry):
    """Split one stages-list entry into ``(name, ref_or_None)``.

    A canonical entry is a bare stage name (``"train"``); a CUSTOM stage
    is ``"name=pkg.module:Attr"`` — the name is how the stage appears in
    artifacts, outputs, and ``$from`` references; the reference is the
    imported callable that runs it.
    """
    if not isinstance(entry, str):
        return entry, None
    if "=" not in entry:
        return entry, None
    name, ref = entry.split("=", 1)
    return name, ref


def _check_stages(errors, stages, optimization):
    if not isinstance(stages, tuple) or not stages:
        errors.append(f"stages must be a non-empty tuple, got {stages!r}")
        return
    names = []
    for entry in stages:
        name, ref = parse_stage_entry(entry)
        if ref is None:
            if name not in STAGES:
                errors.append(
                    f"stages: unknown stage {name!r} — canonical: {list(STAGES)}; "
                    "a CUSTOM stage is declared 'name=pkg.module:Attr'"
                )
                return
        else:
            if not isinstance(name, str) or not re.match(_STAGE_NAME_OK, name):
                errors.append(
                    f"stages: custom stage name {name!r} must match {_STAGE_NAME_OK}"
                )
            if name in STAGES:
                errors.append(
                    f"stages: custom stage name {name!r} shadows a canonical stage"
                )
            if not is_class_ref(ref):
                errors.append(
                    f"stages: custom stage {name!r} reference {ref!r} is not a "
                    "valid 'pkg.module:Attr' import path"
                )
        names.append(name)
    if len(set(names)) != len(names):
        errors.append(f"stages must not repeat, got {stages!r}")
    for i, name in enumerate(names):
        missing = [p for p in _STAGE_PREREQS.get(name, ()) if p not in names[:i]]
        if missing:
            errors.append(
                f"stages: {name!r} requires {missing} earlier in the list "
                f"(got {list(stages)}) — the deploy gates are ordering, not "
                "convention"
            )
    if optimization is None and any(n in names for n in ("optimize", "backtest")):
        errors.append(
            "stages include a capital stage but optimization is null — drop "
            "'optimize'/'backtest' from stages for a predict-only run"
        )


# ---------------------------------------------------------------------------
# The composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """The whole run, composed: one object, one hash, one identity.

    Children are validated at THEIR construction (rule 2), so this level
    only type-checks the composition — a child that exists is already
    shape-valid. ``hpo`` and ``optimization`` are optional (``None`` = no
    search / a predict-only run with no allocation step — prediction
    stands alone). ``hash`` is the pre-registration identity: freeze it
    before results are seen; a different hash is a different experiment.
    """

    name: str
    data: DataConfig
    splits: object
    model: ModelConfig
    features: object = None
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    hpo: object = None
    stat_test: StatTestConfig = field(default_factory=StatTestConfig)
    optimization: object = None
    tracking: object = None
    env: object = None
    outputs: object = None
    #: None derives the stage list (full pipeline with optimization,
    #: predict-only without); the DERIVED list is what serializes and
    #: hashes, so the document always shows exactly what runs.
    stages: object = None
    notes: str = ""

    def __post_init__(self):
        if self.stages is None:
            derived = (
                STAGES
                if self.optimization is not None
                else ("train", "validate", "stat_test")
            )
            object.__setattr__(self, "stages", derived)
        errors = []
        _check_str(errors, "name", self.name)
        _check_child(errors, "data", self.data, DataConfig)
        _check_child(errors, "splits", self.splits, tuple(SPLIT_KINDS.values()))
        _check_child(errors, "model", self.model, ModelConfig)
        _check_child(errors, "features", self.features, FeatureConfig)
        _check_child(errors, "validation", self.validation, ValidationConfig)
        _check_child(errors, "hpo", self.hpo, HPOConfig)
        _check_child(errors, "stat_test", self.stat_test, StatTestConfig)
        _check_child(errors, "optimization", self.optimization, OptimizationConfig)
        _check_child(errors, "tracking", self.tracking, TrackingConfig)
        _check_child(errors, "env", self.env, EnvConfig)
        _check_child(errors, "outputs", self.outputs, OutputsConfig)
        _check_stages(errors, self.stages, self.optimization)
        # $from targets must be DECLARED stages (canonical or custom) —
        # a wire into a stage that never runs is a config bug, caught here
        # where the whole document is visible.
        if isinstance(self.stages, tuple):
            declared = {parse_stage_entry(e)[0] for e in self.stages}
            referenced = set()
            if self.optimization is not None:
                _collect_ref_stages(self.optimization.params, referenced)
            if self.features is not None:
                for step in getattr(self.features, "steps", ()):
                    _collect_ref_stages(getattr(step, "params", {}), referenced)
            undeclared = sorted(referenced - declared)
            if undeclared:
                errors.append(
                    f"$from references target undeclared stage(s) {undeclared} "
                    f"— declared: {sorted(declared)}"
                )
        for req in ("data", "splits", "model"):
            if getattr(self, req) is None:
                errors.append(f"{req} is required")
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    @property
    def hash(self) -> str:
        """The run's identity (sha256 hex; ``notes`` excluded, rule 4)."""
        return config_hash(self)

    def to_obj(self) -> dict:
        return {
            "name": self.name,
            "data": self.data.to_obj(),
            "splits": self.splits.to_obj(),
            "model": self.model.to_obj(),
            "features": None if self.features is None else self.features.to_obj(),
            "validation": self.validation.to_obj(),
            "hpo": None if self.hpo is None else self.hpo.to_obj(),
            "stat_test": self.stat_test.to_obj(),
            "optimization": (
                None if self.optimization is None else self.optimization.to_obj()
            ),
            "tracking": None if self.tracking is None else self.tracking.to_obj(),
            "env": None if self.env is None else self.env.to_obj(),
            "outputs": None if self.outputs is None else self.outputs.to_obj(),
            "stages": list(self.stages),
            "notes": self.notes,
        }

    @classmethod
    def from_obj(cls, obj) -> "PipelineConfig":
        allowed = (
            "name",
            "data",
            "splits",
            "model",
            "features",
            "validation",
            "hpo",
            "stat_test",
            "optimization",
            "tracking",
            "env",
            "outputs",
            "stages",
            "notes",
        )
        _reject_unknown(obj, allowed, "pipeline")
        missing = [k for k in ("name", "data", "splits", "model") if k not in obj]
        if missing:
            raise ConfigError([f"pipeline: required key(s) missing: {missing}"])
        return cls(
            name=obj["name"],
            data=DataConfig.from_obj(obj["data"]),
            splits=split_from_obj(obj["splits"]),
            model=ModelConfig.from_obj(obj["model"]),
            features=(
                FeatureConfig.from_obj(obj["features"])
                if obj.get("features") is not None
                else None
            ),
            validation=ValidationConfig.from_obj(obj.get("validation", {})),
            hpo=HPOConfig.from_obj(obj["hpo"]) if obj.get("hpo") is not None else None,
            stat_test=StatTestConfig.from_obj(obj.get("stat_test", {})),
            optimization=(
                OptimizationConfig.from_obj(obj["optimization"])
                if obj.get("optimization") is not None
                else None
            ),
            tracking=(
                TrackingConfig.from_obj(obj["tracking"])
                if obj.get("tracking") is not None
                else None
            ),
            env=EnvConfig.from_obj(obj["env"]) if obj.get("env") is not None else None,
            outputs=(
                OutputsConfig.from_obj(obj["outputs"])
                if obj.get("outputs") is not None
                else None
            ),
            stages=(tuple(obj["stages"]) if obj.get("stages") is not None else None),
            notes=obj.get("notes", ""),
        )
