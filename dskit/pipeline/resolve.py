"""Config -> ResolvedPipeline: the environment pass.

Design ruling (2026-08-13 design session, question 4) — a content-hash
identity principle, venue-generalized:

* :func:`resolve` is a PURE function of ``(config, asof, data snapshot)``:
  no clocks (``asof`` is an argument; ``None`` = today's UTC date, pin it
  to reproduce), no environment variables, no randomness. Same config +
  same asof + same files on disk -> byte-identical
  :class:`ResolvedPipeline`, same :func:`pipeline_hash`.
* Venue knowledge enters ONLY through the backend: its two resolve hooks
  (``discover_instruments`` when ``data.instruments`` is empty;
  ``fingerprint``, the per-instrument data-snapshot identity — HASHED, so
  a re-pulled settlement or a new bar changes the run's name instead of
  silently reusing it) and its two capability declarations
  (``supported_split_kinds`` / ``supported_optimizer_kinds``), which the
  config is checked against here — a causal venue REFUSING a randomized
  split at resolve is how the toolkit stays general without any venue's
  doctrine being weakened.
* Toolkit-owned names are checked here too (fail early, on the machine
  that runs): ``validation.metric`` against
  :data:`~dskit.pipeline.metrics.METRICS`, ``stat_test.correction``
  against :data:`~dskit.pipeline.stats.CORRECTIONS`.
* :func:`pipeline_hash` covers the resolved content (config with
  ``notes`` stripped at every level, per rule 4; instruments;
  fingerprint; roots) and EXCLUDES provenance (``asof``,
  ``resolver_version``) and ``run_dir`` (which embeds the hash itself).
* ``run_dir`` auto-derives as
  ``{data_root}/pipeline_runs/{name}-{asof}-{hash8}`` — a separate tree
  from any application's own artifacts, so the wrapper never shares an
  application's own run namespace.
* :func:`write_run_dir` persists ``config.json`` (as written) and
  ``resolved.json`` (with ``pipeline_hash`` stamped), refusing a
  non-empty existing dir — clobbering another run's artifacts under this
  run's identity is the exact failure the hash exists to prevent.

Import cost: stdlib only (adapters import their own heavy deps lazily).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from dskit.pipeline.base import (
    NON_IDENTITY_SECTIONS,
    OPTIMIZER_KINDS,
    SINK_KINDS,
    TRANSFORM_KINDS,
    PipelineConfig,
    _strip_notes,
    import_ref,
    is_class_ref,
    parse_stage_entry,
)
from dskit.pipeline.env import load_env
from dskit.pipeline.metrics import METRICS
from dskit.pipeline.registry import DEFAULT_REGISTRY
from dskit.pipeline.stats import CORRECTIONS

__all__ = [
    "RESOLVER_VERSION",
    "ResolvedPipeline",
    "pipeline_hash",
    "resolve",
    "write_run_dir",
]

#: Version of the resolution RULES (not the config schema). Recorded in
#: resolved.json OUTSIDE the hash; bump on any semantic change here.
RESOLVER_VERSION = "1.0.0"

#: Provenance keys excluded from the hash (recorded, never identity).
_PROVENANCE_KEYS = ("pipeline_hash", "asof", "resolver_version")


@dataclass(frozen=True, slots=True)
class ResolvedPipeline:
    """A pipeline config with its environment materialized — what runs.

    Parameters
    ----------
    config : PipelineConfig
        The as-written config (shape-valid by construction).
    asof : str
        ``YYYY-MM-DD`` resolution date. Provenance, not hashed: identity
        lives in the resolved content + fingerprint, so re-resolving
        unchanged data on a later day reproduces the same hash.
    data_root, model_root : str
        Absolute paths. ``data_root`` must exist at resolve time;
        ``model_root`` is an OUTPUT root the runner creates.
    instruments : tuple of str
        The concrete, sorted universe (explicit instruments canonicalized
        by sorting — a universe is a set; or the backend's auto-discovery).
    data_fingerprint : dict
        The backend's per-instrument snapshot identity. HASHED.
    run_dir : str
        Where artifacts land. Excluded from the hash (it embeds hash8).
    """

    config: PipelineConfig
    asof: str
    data_root: str
    model_root: str
    instruments: tuple
    data_fingerprint: dict = field(default_factory=dict)
    run_dir: str = ""

    def __post_init__(self):
        if not isinstance(self.config, PipelineConfig):
            raise ValueError(
                f"config must be a PipelineConfig, got {type(self.config).__name__}"
            )
        try:
            date.fromisoformat(self.asof)
        except (TypeError, ValueError):
            raise ValueError(
                f"asof must be a YYYY-MM-DD date, got {self.asof!r}"
            ) from None
        for name in ("data_root", "model_root", "run_dir"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v:
                raise ValueError(f"{name} must be a non-empty string, got {v!r}")
        if not isinstance(self.instruments, tuple) or not self.instruments:
            raise ValueError(
                f"instruments must be a non-empty tuple (resolution materializes "
                f"the universe), got {self.instruments!r}"
            )
        if list(self.instruments) != sorted(self.instruments):
            raise ValueError("instruments must be sorted (a universe is a set)")
        if not isinstance(self.data_fingerprint, dict):
            raise ValueError(
                f"data_fingerprint must be a dict, got "
                f"{type(self.data_fingerprint).__name__}"
            )

    def to_dict(self) -> dict:
        """JSON-ready form — the hash input (minus exclusions) and the
        resolved.json body."""
        return {
            "asof": self.asof,
            "resolver_version": RESOLVER_VERSION,
            "config": self.config.to_obj(),
            "data_root": self.data_root,
            "model_root": self.model_root,
            "instruments": list(self.instruments),
            "data_fingerprint": json.loads(json.dumps(self.data_fingerprint)),
            "run_dir": self.run_dir,
        }


def pipeline_hash(resolved) -> str:
    """sha256 hex of the resolved pipeline's canonical JSON.

    Accepts a :class:`ResolvedPipeline` or its ``to_dict()`` form (so a
    re-read resolved.json can be re-hashed byte-for-byte). Strips the
    provenance keys, ``run_dir``, and every ``notes`` key at every level
    (rule 4 — documentation never changes identity).
    """
    d = resolved.to_dict() if hasattr(resolved, "to_dict") else dict(resolved)
    d = _strip_notes({k: v for k, v in d.items() if k not in _PROVENANCE_KEYS})
    d.pop("run_dir", None)
    if isinstance(d.get("config"), dict):
        for section in NON_IDENTITY_SECTIONS:  # env + outputs: not identity
            d["config"].pop(section, None)
    try:
        canon = json.dumps(
            d, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
    except ValueError as exc:
        raise ValueError(
            f"resolved pipeline is not canonically serializable (NaN/Infinity "
            f"in an open dict or fingerprint?): {exc}"
        ) from exc
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def resolve(config, asof=None, backend=None, registry=DEFAULT_REGISTRY):
    """Materialize ``config`` against this machine's data snapshot.

    Parameters
    ----------
    config : PipelineConfig
    asof : str or datetime.date, optional
        Resolution date; ``None`` uses today's UTC date (pin it for
        anything you intend to reproduce).
    backend : Backend, optional
        Pre-built backend (tests inject fakes here); ``None`` builds one
        from ``registry`` by ``config.data.venue``.
    registry : BackendRegistry, optional

    Returns
    -------
    tuple of (ResolvedPipeline, Backend)
        The backend is returned alongside so the caller runs with exactly
        the instance whose hooks produced the resolution.

    Raises
    ------
    FileNotFoundError
        If ``data.data_dir`` does not exist here (the environment check
        deferred out of ``__post_init__`` by design rule 1).
    ValueError
        Unknown venue, unknown ``validation.metric`` or
        ``stat_test.correction``, a split/optimizer kind the backend does
        not support, an optimizer kind nobody registered, or an empty
        discovered universe.
    """
    if asof is None:
        asof_s = datetime.now(timezone.utc).date().isoformat()
    elif isinstance(asof, date):
        asof_s = asof.isoformat()
    else:
        try:
            asof_s = date.fromisoformat(asof).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"asof must be a YYYY-MM-DD date, got {asof!r}") from None

    if config.validation.metric not in METRICS:
        raise ValueError(
            f"validation.metric {config.validation.metric!r} is not registered — "
            f"known metrics: {sorted(METRICS)}"
        )
    if config.stat_test.correction not in CORRECTIONS:
        raise ValueError(
            f"stat_test.correction {config.stat_test.correction!r} is not "
            f"registered — known corrections: {sorted(CORRECTIONS)}"
        )
    if CORRECTIONS[config.stat_test.correction]["needs_weights"]:
        raise ValueError(
            f"correction {config.stat_test.correction!r} needs per-instrument "
            "weights, which the stage-list grammar cannot wire — use the "
            "node-map stat_test with a weights input"
        )

    backend = backend if backend is not None else registry.create(config)

    kind = config.splits.kind
    if kind not in backend.supported_split_kinds:
        raise ValueError(
            f"backend {backend.venue!r} does not support split kind {kind!r} "
            f"(supported: {list(backend.supported_split_kinds)}) — for causal "
            "market venues this is doctrine, not a limitation: a randomized "
            "split of time-series events puts the test set's future inside "
            "the training past"
        )
    if config.optimization is not None:
        okind = config.optimization.kind
        if is_class_ref(okind):
            # a custom sizing stage REPLACES backend.optimize: the imported
            # target must satisfy the stage contract (callable).
            if not callable(import_ref(okind)):
                raise ValueError(
                    f"optimization.kind {okind!r} must import to a callable "
                    "(ctx, signal, survivors, params) -> payload dict"
                )
        else:
            if okind not in backend.supported_optimizer_kinds:
                raise ValueError(
                    f"backend {backend.venue!r} does not support optimizer kind "
                    f"{okind!r} (supported: {list(backend.supported_optimizer_kinds)})"
                )
            if okind not in OPTIMIZER_KINDS:
                raise ValueError(
                    f"optimizer kind {okind!r} has no registered validator — the "
                    "adapter that owns it must register_optimizer_kind() on import, "
                    "so its params are checkable before anything runs"
                )

    if is_class_ref(config.model.name):
        family = import_ref(config.model.name)
        needed = ("train", "load") if config.model.mode == "load" else ("train",)
        missing = [m for m in needed if not callable(getattr(family, m, None))]
        if missing:
            raise ValueError(
                f"model family reference {config.model.name!r} must expose "
                f"callable {missing} — contract: train(ctx, params) -> "
                "SignalProvider; load(ctx, params, artifact) -> SignalProvider"
            )

    if config.features is not None:
        claimed = tuple(getattr(backend, "supported_transform_kinds", ()))
        for step in config.features.steps:
            if is_class_ref(step.kind):
                if not callable(import_ref(step.kind)):
                    raise ValueError(
                        f"features step reference {step.kind!r} must import to "
                        "a callable (records, params) -> iterable of records"
                    )
            elif step.kind not in TRANSFORM_KINDS and step.kind not in claimed:
                raise ValueError(
                    f"features step kind {step.kind!r} is neither a registered "
                    f"transform (known: {sorted(TRANSFORM_KINDS)}) nor claimed "
                    f"by backend {backend.venue!r} "
                    f"(supported_transform_kinds={list(claimed)})"
                )
    if config.tracking is not None:
        for sink in config.tracking.sinks:
            if is_class_ref(sink.kind):
                target = import_ref(sink.kind)
                missing = [
                    m
                    for m in ("log_params", "log_metrics", "close")
                    if not hasattr(target, m)
                ]
                if missing:
                    raise ValueError(
                        f"tracking sink reference {sink.kind!r} is missing the "
                        f"Tracker seam method(s) {missing}"
                    )
            elif sink.kind not in SINK_KINDS:
                raise ValueError(
                    f"tracking sink kind {sink.kind!r} is not registered "
                    f"(known: {sorted(SINK_KINDS)}) — the adapter that owns it "
                    "must register_sink_kind() on import"
                )
    for entry in config.stages:
        name, ref = parse_stage_entry(entry)
        if ref is not None and not callable(import_ref(ref)):
            raise ValueError(
                f"custom stage {name!r} reference {ref!r} must import to a "
                "callable (ctx) -> payload dict"
            )

    if config.env is not None:
        load_env(config.env)  # environment check only; values are discarded

    data_root = config.data.resolve()  # env check: must exist HERE
    model_root = os.path.abspath(os.path.expanduser(config.model.model_dir))

    if config.data.instruments:
        instruments = tuple(sorted(config.data.instruments))
    else:
        instruments = tuple(sorted(backend.discover_instruments(data_root)))
        if not instruments:
            raise ValueError(
                f"instrument auto-discovery found nothing under {data_root!r} "
                f"for venue {config.data.venue!r} — pin data.instruments or "
                "point data_dir at a populated store"
            )
    fingerprint = backend.fingerprint(data_root, instruments, asof_s)

    # run_dir embeds the hash, and the hash excludes run_dir — placeholder
    # first, a deliberate two-step.
    common = dict(
        config=config,
        asof=asof_s,
        data_root=data_root,
        model_root=model_root,
        instruments=instruments,
        data_fingerprint=fingerprint,
    )
    h8 = pipeline_hash(ResolvedPipeline(run_dir=".", **common))[:8]
    run_root = (
        os.path.abspath(os.path.expanduser(config.outputs.run_root))
        if config.outputs is not None and config.outputs.run_root
        else os.path.join(data_root, "pipeline_runs")
    )
    run_dir = os.path.join(run_root, f"{config.name}-{asof_s}-{h8}")
    return ResolvedPipeline(run_dir=run_dir, **common), backend


def write_run_dir(resolved) -> str:
    """Create the run dir; persist ``config.json`` + ``resolved.json``.

    Refuses a non-empty existing dir, naming both hashes (the incumbent's
    read from its resolved.json where possible) so a re-run is
    distinguishable from a genuine conflict. Both documents are serialized
    BEFORE anything touches disk — a NaN in an open dict can never leave a
    half-written run dir behind.
    """
    run_dir = resolved.run_dir
    new_hash = pipeline_hash(resolved)
    if os.path.isdir(run_dir) and os.listdir(run_dir):
        old_hash = "unknown"
        try:
            with open(os.path.join(run_dir, "resolved.json"), encoding="utf-8") as fh:
                old_hash = json.load(fh).get("pipeline_hash", "unknown")
        except (OSError, ValueError):
            pass
        raise ValueError(
            f"run_dir {run_dir!r} already exists and is non-empty (existing "
            f"pipeline_hash={old_hash}, new pipeline_hash={new_hash}); refusing "
            "to overwrite another run's artifacts"
        )
    config_text = json.dumps(
        resolved.config.to_obj(), indent=2, sort_keys=True, allow_nan=False
    )
    rd = resolved.to_dict()
    rd["pipeline_hash"] = new_hash
    resolved_text = json.dumps(rd, indent=2, sort_keys=True, allow_nan=False)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "config.json"), "w", encoding="utf-8") as fh:
        fh.write(config_text + "\n")
    with open(os.path.join(run_dir, "resolved.json"), "w", encoding="utf-8") as fh:
        fh.write(resolved_text + "\n")
    return run_dir
