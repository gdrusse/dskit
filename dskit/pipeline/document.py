"""The node-map document — docs/24 §2–§4 as config classes.

This is the grammar the spec froze (docs/24-pipeline-config-spec.md): one
JSON document declares the entire process as a keyed **node map**, and the
universal execution file (``python -m dskit.pipeline run <config>``)
runs any such document. The stage-list grammar in :mod:`.base` is the
predecessor this grammar replaces; its *organs* are reused here verbatim
(``ConfigError`` + the accumulate-then-raise-once helpers, the canonical
hash, the env/outputs/tracking sections), its stage grammar is not.

Layers, unchanged from the built toolkit:

* **Shape** validates in ``__post_init__`` — checkable on any machine, so
  documents can be built, hashed, and tested where the data does not
  live. An invalid document can never exist; errors accumulate.
* **Plan** (:mod:`.planner`) imports ``uses`` references, cross-checks
  roles, sorts the DAG, and applies the role rules.
* **Resolve/Execute** (:mod:`.driver`) touches the environment.

Identity (spec §2): sha256 over canonical JSON with ``notes`` stripped at
every level and ``env``/``outputs``/``schedule`` excluded — placement,
credentials, and cadence documentation say nothing about WHAT the
experiment computes. ``$prev`` references hash as written (reference +
default are identity; the runtime value is not).

Reference grammar (spec §4):

* ``"$node.path.to.output"`` — another node's output this run. The
  source must be a declared node key or the reserved word ``splits``
  (the materialized splits section, e.g. ``"$splits.t1"``). Legal as
  any ``inputs`` value and anywhere inside ``params``.
* ``{"$prev": "node.output", "default": X}`` — the previous completed
  run in this series. ``default`` is REQUIRED (it defines the first
  run) and must be JSON-legal. Legal inside ``params`` only: ``inputs``
  wire THIS run's DAG.
* Any other string beginning with ``$`` is refused loudly — a mistyped
  wire must never ride through as a literal.

Import cost: stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from dskit.pipeline.base import (
    DEFAULT_SPLIT_POLICY,
    SPLIT_POLICIES,
    ConfigError,
    EnvConfig,
    OutputsConfig,
    TimeSplitConfig,
    TrackingConfig,
    _check_child,
    _check_fraction,
    _check_int,
    _check_open_dict,
    _check_str,
    _dataclass_to_obj,
    _raise_if,
    _reject_unknown,
    config_hash,
    is_class_ref,
)

__all__ = [
    "CADENCES",
    "ClockConfig",
    "DOC_NON_IDENTITY_SECTIONS",
    "DOC_SPLIT_KINDS",
    "NodeSpec",
    "PipelineDocument",
    "RandomSplitSpec",
    "ROLES",
    "ScheduleConfig",
    "SPLITS_SOURCE",
    "TrailingSplitSpec",
    "WalkForwardSpec",
    "doc_split_from_obj",
    "flatten_param_paths",
    "is_node_ref",
    "is_prev_ref",
    "load_document",
    "parse_node_ref",
    "parse_prev_ref",
    "save_document",
]

#: Rule-bearing step categories (spec §5). ``role`` is declared BY the
#: Node class; a document's optional ``role`` key is readable redundancy,
#: cross-checked at plan time and refused on mismatch.
ROLES = (
    "data",
    "labels",
    "transform",
    "tensor",
    "accrual",
    "gate",
    "search",
    "signal",
    "train",
    "score",
    "stat_test",
    "capital",
    "report",
)

#: Node cadences under a clock (spec §3/§7). Omitted = ``once``; anything
#: other than ``once`` is only meaningful when the document has a clock.
CADENCES = ("once", "epoch", "day", "week")

#: Top-level document sections excluded from the identity hash: env,
#: outputs and tracking (the stage-list precedent,
#: :data:`~dskit.pipeline.base.NON_IDENTITY_SECTIONS`) plus ``schedule``
#: — the run-series cadence is provenance, documentation of when the same
#: command is re-invoked, never part of what one run computes (spec §2,
#: I-222 lean). ``tracking`` is here for the reason ``outputs`` is: WHERE
#: a run's metrics are logged is placement, and the identity hash grades
#: what the run COMPUTES. Excluding it moved no hash — ``to_obj`` emits
#: a ``"tracking"`` key for every document, so the section is rendered
#: UNDECLARED rather than removed (base's ``NULLED_IDENTITY_SECTIONS``).
DOC_NON_IDENTITY_SECTIONS = ("env", "outputs", "schedule", "tracking")

#: The reserved reference source naming the materialized splits section
#: (``"$splits.t1"``). A node may not take this name.
SPLITS_SOURCE = "splits"

_NODE_KEY_OK = r"^[a-z_][a-z0-9_]*$"
_KIND_OK = r"^[a-z][a-z0-9_-]*$"
_NAME_OK = r"^[a-z0-9][a-z0-9._-]*$"
_SEGMENT_OK = r"^[A-Za-z_][A-Za-z0-9_]*$"

PREV_KEY = "$prev"


# ---------------------------------------------------------------------------
# Reference grammar
# ---------------------------------------------------------------------------


def is_node_ref(value) -> bool:
    """True iff ``value`` is a node-output reference string (``"$..."``).

    Grammar validity is :func:`parse_node_ref`'s business — this only
    detects the FORM, so a malformed ``$`` string is still routed into
    the parser and refused there, never passed through as a literal.
    """
    return isinstance(value, str) and value.startswith("$")


def parse_node_ref(value):
    """Split ``"$source.path.to.output"`` into ``(source, path_tuple)``.

    ``source`` is a node key (or the reserved ``splits``); the path has at
    least one segment. Raises :class:`ConfigError` on bad grammar.
    """
    body = value[1:] if isinstance(value, str) and value.startswith("$") else None
    parts = body.split(".") if body else []
    if (
        len(parts) < 2
        or not re.match(_NODE_KEY_OK, parts[0])
        or not all(re.match(_SEGMENT_OK, p) for p in parts[1:])
    ):
        raise ConfigError(
            [
                f"a $-reference must be '$<node>.<output-path>', got {value!r} "
                "(e.g. '$edge_test.survivors')"
            ]
        )
    return parts[0], tuple(parts[1:])


def is_prev_ref(value) -> bool:
    """True iff ``value`` is shaped like a run-over-run carry — a dict
    containing the ``$prev`` key. Exact-shape validation (both keys, no
    more) is :func:`parse_prev_ref`'s business, so a half-formed carry is
    refused, not treated as an ordinary params dict."""
    return isinstance(value, dict) and PREV_KEY in value


def _contains_refs(obj) -> bool:
    """Whether any ``$``-shaped reference hides anywhere in ``obj``."""
    if is_node_ref(obj) or is_prev_ref(obj):
        return True
    if isinstance(obj, dict):
        return any(_contains_refs(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_refs(v) for v in obj)
    return False


def parse_prev_ref(value):
    """Split ``{"$prev": "node.output", "default": X}`` into
    ``(node, path_tuple, default)``. Raises :class:`ConfigError` unless
    the dict has exactly those two keys, the target parses, and the
    default is fully literal — on the first run of a series the default
    IS the value, so a ``$``-reference inside it would ride through as a
    literal string."""
    errors = []
    if set(value) != {PREV_KEY, "default"}:
        errors.append(
            f"a $prev carry must have exactly the keys ['$prev', 'default'], "
            f"got {sorted(value)} — 'default' is required (it defines the "
            "first run of the series)"
        )
        _raise_if(errors)
    target = value[PREV_KEY]
    parts = target.split(".") if isinstance(target, str) else []
    if (
        len(parts) < 2
        or not re.match(_NODE_KEY_OK, parts[0])
        or not all(re.match(_SEGMENT_OK, p) for p in parts[1:])
    ):
        raise ConfigError(
            [
                f"$prev must target '<node>.<output-path>', got {target!r} "
                "(e.g. 'replay.final_bankroll')"
            ]
        )
    if _contains_refs(value["default"]):
        raise ConfigError(
            [
                f"$prev default must be a literal value, got "
                f"{value['default']!r} — on the first run the default IS the "
                "value, and references are never resolved inside it"
            ]
        )
    return parts[0], tuple(parts[1:]), value["default"]


def _flatten_into(out, prefix, value):
    """Emit ``prefix`` -> leaf for one params subtree, key by key.

    A reference is one leaf, as declared — descent never enters it (both
    reference predicates, as every sibling walker composes them, though
    only the ``$prev`` carry is dict-shaped). A block that holds a key no
    path segment can name is emitted WHOLE as well as descended, so the
    walk never drops a value.
    """
    if is_node_ref(value) or is_prev_ref(value):
        out[prefix] = value
        return
    inside = _spellable_keys(value)
    if not inside or len(inside) < len(value):
        out[prefix] = value
    for name in inside:
        _flatten_into(out, f"{prefix}.{name}", value[name])


def _spellable_keys(value):
    """Return the keys of ``value`` a path segment can name — () for a non-dict."""
    if not isinstance(value, dict):
        return ()
    return tuple(k for k in value if isinstance(k, str) and re.match(_SEGMENT_OK, k))


def flatten_param_paths(node_key, params):
    """One node's params as ``"<node>.<param.path>"`` override targets.

    This is the override grammar read FORWARDS —
    :func:`dskit.pipeline.driver._apply_param_override` resolves such a
    path, ``hpo-grid`` tunes one, and the driver logs one per knob to the
    tracking sinks. The walk descends a dict exactly where an override
    could descend, KEY BY KEY: a key that is no legal path segment
    (``1d``, a dotted space target) is skipped, since nothing could ever
    spell it, while its siblings ride along — dropping a whole dict for
    one unspellable key would hide knobs that ARE tunable (``n.opt.lr``
    beside ``n.opt.1st_moment``).

    Descent never LOSES a value, though: a block holding an unspellable
    key is ALSO emitted whole under its own path, so two runs differing
    only in ``opt.1st_moment`` still log differently. Emission therefore
    never depends on whether a value happens to have a spellable sibling
    — the one asymmetry left is the node's own params dict, which has no
    path of its own (``"size"`` is no override target): an unspellable
    TOP-level param name is unrecoverable, and a params block that IS a
    reference emits nothing at all.

    A value with no spellable key below it is the LEAF and is emitted
    whole: a scalar, a list, an empty dict, and a block like ``hpo-grid``'s
    ``space`` (whose keys are all dotted targets) — that block is itself
    addressable as ``search.space``, and dropping it would hide the
    searched grid from every sink.

    A REFERENCE is wiring, not a dict of knobs, and is one leaf logged
    exactly as declared: a ``$node.port``/``$splits...`` string rides
    through as written, and a ``$prev`` carry is emitted whole — never
    entered (its ``default`` is reference plumbing, so it gets no key of
    its own). A params block that IS a reference at the ROOT emits no
    keys at all: the node declares no addressable knob, and the block
    has no path of its own to be emitted under. All of that keeps the
    emitted key set equal to the declared tree — stable across a run
    series, where a carry's RESOLUTION changes shape run over run — and
    keeps every key an address the override/space grammar can spell.
    What a reference bound to lives where it happened: outputs,
    ``carry.json``, ``resolved.json``.

    Values otherwise pass through unchanged — rendering belongs to the
    sink, so a numeric knob stays comparable as a number.

    Parameters
    ----------
    node_key : str
        The node's key in the document's ``pipeline`` map.
    params : dict
        That node's params AS DECLARED (post-override) in the document —
        the driver passes the document's own text at run start, never a
        materialized copy, so every reference is still spelled as
        written.

    Returns
    -------
    dict
        ``"<node_key>.<param.path>"`` -> the declared value at that path
        (a leaf, or a block that also holds something unspellable);
        empty when the node declares no addressable params — none at
        all, or a whole-block reference. Every key contains at least one
        dot, so these never collide with the undotted run-identity
        fields a tracker logs beside them.
    """
    if is_node_ref(params) or is_prev_ref(params):
        return {}
    out = {}
    for name, value in params.items():
        if isinstance(name, str) and re.match(_SEGMENT_OK, name):
            _flatten_into(out, f"{node_key}.{name}", value)
    return out


def _check_ref_tree(errors, where, obj, *, allow_prev):
    """Accumulate grammar errors for every reference nested in ``obj``.

    Walks dicts and lists; validates ``$``-strings via
    :func:`parse_node_ref` and ``$prev`` dicts via :func:`parse_prev_ref`
    (only where ``allow_prev``). Literal values pass untouched.
    """
    if is_prev_ref(obj):
        if not allow_prev:
            errors.append(
                f"{where}: $prev carries are only legal inside params — "
                "inputs wire THIS run's DAG"
            )
            return
        try:
            parse_prev_ref(obj)
        except ConfigError as exc:
            errors.extend(f"{where}: {e}" for e in exc.errors)
        return
    if is_node_ref(obj):
        try:
            parse_node_ref(obj)
        except ConfigError as exc:
            errors.extend(f"{where}: {e}" for e in exc.errors)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_ref_tree(errors, f"{where}.{k}", v, allow_prev=allow_prev)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_ref_tree(errors, f"{where}[{i}]", v, allow_prev=allow_prev)


def _collect_refs(obj, node_refs, prev_refs):
    """Gather every parseable reference nested in ``obj``: node refs into
    ``node_refs`` as ``(source, path)``, carries into ``prev_refs`` as
    ``(node, path, default)``. Malformed refs are skipped here — shape
    validation already reported them."""
    if is_prev_ref(obj):
        try:
            prev_refs.append(parse_prev_ref(obj))
        except ConfigError:
            pass
        return
    if is_node_ref(obj):
        try:
            node_refs.append(parse_node_ref(obj))
        except ConfigError:
            pass
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_refs(v, node_refs, prev_refs)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_refs(v, node_refs, prev_refs)


# ---------------------------------------------------------------------------
# The node (spec §3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """One node of the map: WHICH code (``uses``), its wiring, its knobs.

    Everything except ``notes`` is hash-material. Error messages name the
    field only — :class:`PipelineDocument` prefixes the node's key, so a
    problem reads ``pipeline.qhat: uses must ...``.
    """

    uses: str
    inputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    every: str = "once"
    mode: object = None
    artifact: str = ""
    role: object = None
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "uses", self.uses)
        if (
            isinstance(self.uses, str)
            and self.uses
            and not (re.match(_KIND_OK, self.uses) or is_class_ref(self.uses))
        ):
            errors.append(
                f"uses must be a registered kind name (like 'stat_test') or an "
                f"import reference 'pkg.module:ClassName', got {self.uses!r}"
            )
        if not isinstance(self.inputs, dict):
            errors.append(
                f"inputs must be a dict of port -> $-reference, got {self.inputs!r}"
            )
        else:
            for port, ref in self.inputs.items():
                if not isinstance(port, str) or not re.match(_NODE_KEY_OK, port):
                    errors.append(
                        f"inputs: port names must match {_NODE_KEY_OK}, got {port!r}"
                    )
                if is_prev_ref(ref):
                    errors.append(
                        f"inputs.{port}: $prev carries are only legal inside "
                        "params — inputs wire THIS run's DAG"
                    )
                elif not is_node_ref(ref):
                    errors.append(
                        f"inputs.{port}: every input wires another node's output "
                        f"and must be a '$node.output' reference, got {ref!r}"
                    )
                else:
                    _check_ref_tree(errors, f"inputs.{port}", ref, allow_prev=False)
        _check_open_dict(errors, "params", self.params)
        if isinstance(self.params, dict):
            _check_ref_tree(errors, "params", self.params, allow_prev=True)
        if self.every not in CADENCES:
            errors.append(f"every must be one of {list(CADENCES)}, got {self.every!r}")
        if self.mode not in (None, "train", "load"):
            errors.append(
                f"mode must be 'train' or 'load' (or absent), got {self.mode!r}"
            )
        _check_str(errors, "artifact", self.artifact, non_empty=False)
        if self.mode == "load" and not self.artifact:
            errors.append("mode 'load' requires a pinned 'artifact' (hash-material)")
        if self.mode == "train" and self.artifact:
            errors.append(
                "mode 'train' with a pinned 'artifact' is contradictory — "
                "training produces the artifact; pin one only with mode 'load'"
            )
        if self.mode is None and self.artifact:
            errors.append("'artifact' without mode 'load' has no meaning")
        if self.role is not None and self.role not in ROLES:
            errors.append(
                f"role must be one of {list(ROLES)} (or absent), got {self.role!r}"
            )
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    def refs(self):
        """Every reference this node makes: ``(node_refs, prev_refs)`` —
        node refs as ``(source, path)`` from inputs AND params, carries as
        ``(node, path, default)`` from params."""
        node_refs, prev_refs = [], []
        for ref in self.inputs.values():
            _collect_refs(ref, node_refs, prev_refs)
        _collect_refs(self.params, node_refs, prev_refs)
        return node_refs, prev_refs

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "NodeSpec":
        _reject_unknown(
            obj,
            ("uses", "inputs", "params", "every", "mode", "artifact", "role", "notes"),
            "node",
        )
        return cls(
            uses=obj.get("uses", ""),
            inputs=dict(obj.get("inputs", {})),
            params=dict(obj.get("params", {})),
            every=obj.get("every", "once"),
            mode=obj.get("mode", None),
            artifact=obj.get("artifact", ""),
            role=obj.get("role", None),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Splits (spec §6) — a family, one kind per document. ``time`` is reused
# from the stage-list grammar verbatim (identical rules); ``random`` is
# re-cut for the spec change (train+val == 1.0 legal: the plain
# train/validation case); ``trailing`` is the banking variant (proposed).
# ---------------------------------------------------------------------------

#: Float slack for the fraction sum: 0.7 + 0.3 lands a hair below 1.0 in
#: IEEE arithmetic and must still mean "no test split", never a
#: microscopic accidental one.
_FRAC_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class RandomSplitSpec:
    """Cluster-hashed random assignment; a test split exists only if the
    fractions leave room for it (spec §6 change vs the stage grammar).

    Assignment is a pure hash of ``(seed, cluster)`` — deterministic
    across machines, no RNG state — identical to the stage grammar's rule.
    ``train_frac + val_frac == 1.0`` is legal: plain train/validation,
    nothing pre-registered. Causal venues still refuse the kind (a
    backend/doctrine declaration, not this class's business).
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
        if not errors and self.train_frac + self.val_frac > 1.0 + _FRAC_TOL:
            errors.append(
                "splits: train_frac + val_frac must not exceed 1.0, got "
                f"{self.train_frac} + {self.val_frac}"
            )
        _check_int(errors, "splits.seed", self.seed, ge=0)
        _check_str(errors, "splits.notes", self.notes, non_empty=False)
        _raise_if(errors)

    @property
    def has_test(self) -> bool:
        """Whether the fractions leave a test remainder."""
        return self.train_frac + self.val_frac < 1.0 - _FRAC_TOL

    def split_of(self, record):
        """Assign by deterministic hash of the record's ``cluster``:
        ``"train"``/``"val"``, and ``"test"`` only when it exists."""
        import hashlib

        digest = hashlib.sha256(f"{self.seed}:{record.cluster}".encode()).digest()
        u = int.from_bytes(digest[:8], "big") / 2**64
        if u < self.train_frac:
            return "train"
        if u < self.train_frac + self.val_frac or not self.has_test:
            return "val"
        return "test"

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "RandomSplitSpec":
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


_DAY_MS = 24 * 60 * 60 * 1000

#: ``train_days`` sentinel: train reaches back to the beginning of data.
ALL_PRIOR = "all-prior"


@dataclass(frozen=True, slots=True)
class TrailingSplitSpec:
    """★BANKING: cuts counted BACKWARD from the newest settled event at
    this run's asof — test = the newest ``test_days``, val = the
    ``val_days`` before that, train = everything earlier (bounded by
    ``train_days`` unless ``"all-prior"``). Windows roll forward as data
    banks; the concrete cuts stamp into ``resolved.json`` per run.

    Materialization needs the data's edge (the newest settled instant),
    which only a data node knows — :meth:`materialize` is the pure cut
    arithmetic, called by the driver once that instant is in hand.

    ``policy`` rides through materialization unchanged: WHERE the cuts land
    is what rolls forward, WHICH INSTANT each record is cut on is a separate
    declared choice (:mod:`dskit.pipeline.split_policy`) and must not
    depend on when the run happened.
    """

    test_days: int
    val_days: int
    train_days: object = ALL_PRIOR
    kind: str = "trailing"
    #: Handed verbatim to the materialized :class:`TimeSplitConfig`.
    policy: str = DEFAULT_SPLIT_POLICY
    notes: str = ""
    embargo_days: int = 0
    #: ADR-0034: a trailing calibration band, carved out BETWEEN test and
    #: val — cal = the ``cal_days`` newest days of the val window, val
    #: counts backward from the cal band's start. Zero (the default)
    #: materializes no band, byte for byte.
    cal_days: int = 0

    def __post_init__(self):
        errors = []
        if self.kind != "trailing":
            errors.append(f"splits.kind must be 'trailing', got {self.kind!r}")
        _check_int(errors, "splits.test_days", self.test_days, ge=1)
        _check_int(errors, "splits.val_days", self.val_days, ge=1)
        if self.policy not in SPLIT_POLICIES:
            errors.append(
                f"splits.policy: unknown policy {self.policy!r} — known "
                f"policies: {sorted(SPLIT_POLICIES)}"
            )
        _check_int(errors, "splits.embargo_days", self.embargo_days, ge=0)
        _check_int(errors, "splits.cal_days", self.cal_days, ge=0)
        if self.train_days != ALL_PRIOR:
            _check_int(errors, "splits.train_days", self.train_days, ge=1)
        _check_str(errors, "splits.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def unmaterializable_reason(self) -> str:
        """Why this spec can NEVER materialize, or ``""`` when it can.

        Separate from :meth:`materialize` so a caller can refuse before
        paying for the data edge — reading every ledger to compute an
        anchor, only to reject the spec on its own declared fields, is an
        expensive way to say no. :meth:`materialize` re-checks, so this
        stays an optimization and never the only guard.
        """
        if self.train_days != ALL_PRIOR:
            return (
                f"train_days={self.train_days!r} cannot materialize yet: a "
                "bounded train window needs a train-start cut (lands with the "
                "adapter kinds, I-223) — use 'all-prior' for now"
            )
        return ""

    def materialize(self, newest_ms) -> TimeSplitConfig:
        """The pure cut arithmetic: pinned causal cuts counted backward
        from ``newest_ms`` (the newest settled event's instant). The
        returned :class:`TimeSplitConfig` carries ``test_end_ms ==
        newest_ms`` and re-validates strict ordering by construction.

        Only ``train_days == "all-prior"`` materializes today: a bounded
        train window needs a train-START cut that
        :class:`~dskit.pipeline.base.TimeSplitConfig` cannot express —
        refusing beats silently behaving as all-prior (I-223)."""
        blocked = self.unmaterializable_reason()
        if blocked:
            raise ValueError(blocked)
        if isinstance(newest_ms, bool) or not isinstance(newest_ms, int):
            raise ValueError(f"newest_ms must be an int (epoch ms), got {newest_ms!r}")
        test_end = newest_ms
        val_end = test_end - self.test_days * _DAY_MS
        # The cal band (ADR-0034) is the tail of the val window: cal =
        # [cal_start, val_end], and val counts backward from the band's
        # start — declaring cal_days never shrinks val, it pushes the
        # earlier cuts deeper into history. The +1 mirrors _fold_splits'
        # boundary discipline: the cal band is INCLUSIVE-left, so without
        # it the midnight stamp exactly cal_days before val_end would move
        # from val into cal — cal_days+1 daily stamps in cal, one stolen
        # from val. With it, cal holds exactly cal_days daily stamps.
        cal_start = val_end - self.cal_days * _DAY_MS + 1
        val_start = (cal_start if self.cal_days else val_end) - self.val_days * _DAY_MS
        # The embargo (ADR-0027) is carved out of TRAIN's tail, never out
        # of the val window: train ends embargo_days before val starts,
        # and the band between belongs to no split.
        train_end = val_start - self.embargo_days * _DAY_MS
        if train_end < 1:
            raise ValueError(
                f"trailing splits need newest_ms deep enough for the windows: "
                f"newest_ms={newest_ms} leaves train_end_ms={train_end}"
            )
        return TimeSplitConfig(
            train_end_ms=train_end,
            val_end_ms=val_end,
            test_end_ms=test_end,
            policy=self.policy,
            val_start_ms=val_start if self.embargo_days else None,
            cal_start_ms=cal_start if self.cal_days else None,
        )

    def to_obj(self) -> dict:
        """``policy`` is dropped when it is the default, and ``embargo_days``
        / ``cal_days`` when zero, for the same reason
        :meth:`TimeSplitConfig.to_obj` drops them: a knob nobody declared
        must not change the identity hash of runs that already happened."""
        obj = _dataclass_to_obj(self)
        if obj.get("policy") == DEFAULT_SPLIT_POLICY:
            obj.pop("policy", None)
        if not self.embargo_days:
            del obj["embargo_days"]
        if not self.cal_days:
            del obj["cal_days"]
        return obj

    @classmethod
    def from_obj(cls, obj) -> "TrailingSplitSpec":
        _reject_unknown(
            obj,
            (
                "kind",
                "test_days",
                "val_days",
                "train_days",
                "policy",
                "embargo_days",
                "cal_days",
                "notes",
            ),
            "splits",
        )
        return cls(
            test_days=obj.get("test_days", 0),
            val_days=obj.get("val_days", 0),
            train_days=obj.get("train_days", ALL_PRIOR),
            kind=obj.get("kind", "trailing"),
            policy=obj.get("policy", DEFAULT_SPLIT_POLICY),
            notes=obj.get("notes", ""),
            embargo_days=obj.get("embargo_days", 0),
            cal_days=obj.get("cal_days", 0),
        )


#: The document grammar's split family. ``time`` IS the stage grammar's
#: class — identical rules, reused, not forked.
DOC_SPLIT_KINDS = {
    "time": TimeSplitConfig,
    "random": RandomSplitSpec,
    "trailing": TrailingSplitSpec,
}


# ---------------------------------------------------------------------------
# Walk-forward (ADR-0027) — the rolling-origin evaluation a document declares
# ---------------------------------------------------------------------------

_DATE_OK = r"^\d{4}-\d{2}-\d{2}$"


def _date_problem(value) -> bool:
    """True when ``value`` is not a REAL calendar date. The regex alone
    let a 2026-02-30 through `validate` to crash mid-plan at run (the
    skeptic pass) — fail-loudly-at-validate is the document doctrine."""
    if not re.match(_DATE_OK, value):
        return True
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError:
        return True
    return False


@dataclass(frozen=True, slots=True)
class WalkForwardSpec:
    """Rolling-origin evaluation, declared (ADR-0027): K fold CUTOFFS
    (each a validation-start date), a val window, an optional embargo
    carved out of train's tail, and the objective to collect per fold.

    Folds come ONE of two ways — an explicit ``folds`` list of
    ``YYYY-MM-DD`` cutoffs (strictly ascending), or a generated schedule
    ``first`` + ``step_days`` + ``count``. Declaring both (or neither) is
    refused: two sources of the same truth is how they drift.

    ``objective`` is a ``$node.path`` reference into a score node's
    outputs — what each fold reports upward. Train windows are expanding
    ("all-prior") in v1; a bounded train window joins the existing I-223
    restriction when the cut grammar can express it.

    This section IS identity (unlike ``schedule``): the fold plan defines
    the experiment.
    """

    objective: str
    val_days: int
    folds: object = None
    first: str = ""
    step_days: int = 0
    count: int = 0
    embargo_days: int = 0
    select: str = "min"
    notes: str = ""

    def __post_init__(self):
        errors = []
        if not isinstance(self.objective, str) or not self.objective:
            errors.append(
                "walkforward.objective is required — a '$node.path' reference "
                "to the score output each fold reports"
            )
        else:
            try:
                parse_node_ref(self.objective)
            except ConfigError:
                errors.append(
                    "walkforward.objective must be a '$node.path' reference "
                    f"(e.g. '$validate.metrics.loss'), got {self.objective!r}"
                )
        _check_int(errors, "walkforward.val_days", self.val_days, ge=1)
        _check_int(errors, "walkforward.embargo_days", self.embargo_days, ge=0)
        if self.select not in ("min", "max"):
            errors.append(
                f"walkforward.select must be 'min' or 'max', got {self.select!r}"
            )
        explicit = self.folds is not None
        generated = bool(self.first) or self.step_days or self.count
        if explicit and generated:
            errors.append(
                "walkforward: declare folds EITHER as an explicit list OR as "
                "first/step_days/count — both is two sources of one truth"
            )
        elif explicit:
            if (
                not isinstance(self.folds, (list, tuple))
                or not self.folds
                or any(
                    not isinstance(f, str) or _date_problem(f) for f in self.folds
                )
            ):
                errors.append(
                    "walkforward.folds must be a non-empty list of REAL "
                    f"'YYYY-MM-DD' cutoffs (a 2026-02-30 must refuse at "
                    f"validate, never crash a fold), got {self.folds!r}"
                )
            elif list(self.folds) != sorted(set(self.folds)):
                errors.append(
                    "walkforward.folds must be strictly ascending with no "
                    f"duplicates, got {list(self.folds)!r}"
                )
        elif generated:
            if not isinstance(self.first, str) or _date_problem(self.first or ""):
                errors.append(
                    "walkforward.first must be a REAL 'YYYY-MM-DD' cutoff, got "
                    f"{self.first!r}"
                )
            _check_int(errors, "walkforward.step_days", self.step_days, ge=1)
            _check_int(errors, "walkforward.count", self.count, ge=1)
        else:
            errors.append(
                "walkforward: no folds declared — give an explicit folds list "
                "or a first/step_days/count schedule"
            )
        _check_str(errors, "walkforward.notes", self.notes, non_empty=False)
        _raise_if(errors)
        if self.folds is not None:
            # Pin the validated list as a tuple: the frozen spec must not
            # share a mutable list with whoever built it (or with to_obj's
            # callers) — object.__setattr__ is the frozen-dataclass door.
            object.__setattr__(self, "folds", tuple(self.folds))

    def fold_cutoffs(self) -> tuple:
        """The fold cutoff dates, ascending, as ``YYYY-MM-DD`` strings."""
        if self.folds is not None:
            return tuple(self.folds)
        from datetime import date, timedelta

        first = date.fromisoformat(self.first)
        return tuple(
            (first + timedelta(days=i * self.step_days)).isoformat()
            for i in range(self.count)
        )

    def to_obj(self) -> dict:
        obj = _dataclass_to_obj(self)
        # Only the ACTIVE fold declaration is emitted — the unset half's
        # defaults are absence, not data (and never hash material).
        if self.folds is not None:
            for name in ("first", "step_days", "count"):
                del obj[name]
        else:
            del obj["folds"]
        return obj

    @classmethod
    def from_obj(cls, obj) -> "WalkForwardSpec":
        _reject_unknown(
            obj,
            (
                "objective",
                "val_days",
                "folds",
                "first",
                "step_days",
                "count",
                "embargo_days",
                "select",
                "notes",
            ),
            "walkforward",
        )
        return cls(
            objective=obj.get("objective", ""),
            val_days=obj.get("val_days", 0),
            folds=obj.get("folds"),
            first=obj.get("first", ""),
            step_days=obj.get("step_days", 0),
            count=obj.get("count", 0),
            embargo_days=obj.get("embargo_days", 0),
            select=obj.get("select", "min"),
            notes=obj.get("notes", ""),
        )


def doc_split_from_obj(obj):
    """Reconstruct a split variant, dispatching on the ``kind`` tag."""
    if not isinstance(obj, dict) or "kind" not in obj:
        raise ConfigError(
            [f"splits: a serialized split must carry a 'kind' key, got {obj!r}"]
        )
    cls = DOC_SPLIT_KINDS.get(obj["kind"])
    if cls is None:
        raise ConfigError(
            [
                f"splits: unknown kind {obj['kind']!r} — known kinds: "
                f"{sorted(DOC_SPLIT_KINDS)}"
            ]
        )
    return cls.from_obj(obj)


# ---------------------------------------------------------------------------
# Clock + schedule (spec §7 / §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClockConfig:
    """The smallest-increment driver. ABSENT = single-pass DAG — the plain
    pipeline and the default mental model. Present, the runner drives
    ticks from ``start`` to ``until`` and executes due nodes per tick.
    (Clocked EXECUTION is pending the I-222 A/B ruling; the grammar is
    frozen here so documents parse and plan either way.)"""

    increment: str
    start: object = "auto"
    until: object = "asof"
    notes: str = ""

    def __post_init__(self):
        errors = []
        if self.increment not in ("epoch", "day", "week"):
            errors.append(
                f"clock.increment must be one of ['epoch', 'day', 'week'], "
                f"got {self.increment!r}"
            )
        for name in ("start", "until"):
            v = getattr(self, name)
            sentinel = "auto" if name == "start" else "asof"
            if v != sentinel and (
                isinstance(v, bool) or not isinstance(v, int) or v < 0
            ):
                errors.append(
                    f"clock.{name} must be {sentinel!r} or epoch-ms >= 0, got {v!r}"
                )
        _check_str(errors, "clock.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "ClockConfig":
        _reject_unknown(obj, ("increment", "start", "until", "notes"), "clock")
        return cls(
            increment=obj.get("increment", ""),
            start=obj.get("start", "auto"),
            until=obj.get("until", "asof"),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    """Re-run cadence DOCUMENTATION — the run *series* ledger (spec §2).

    Never executed by the runner; the scheduler just re-invokes the same
    command. Provenance, not identity: hash-excluded, so tightening the
    cadence never renames the experiment."""

    cadence: str
    day: str = ""
    asof: str = ""
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "schedule.cadence", self.cadence)
        _check_str(errors, "schedule.day", self.day, non_empty=False)
        _check_str(errors, "schedule.asof", self.asof, non_empty=False)
        _check_str(errors, "schedule.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj) -> "ScheduleConfig":
        _reject_unknown(obj, ("cadence", "day", "asof", "notes"), "schedule")
        return cls(
            cadence=obj.get("cadence", ""),
            day=obj.get("day", ""),
            asof=obj.get("asof", ""),
            notes=obj.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# The document (spec §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineDocument:
    """One JSON document declaring everything a process does.

    ``pipeline`` maps node keys to :class:`NodeSpec`; declaration order is
    preserved (it breaks ties in the planner's deterministic ordering).
    Cross-field shape rules enforced here, on any machine:

    * every ``$``-reference's source is a declared node or ``splits``
      (dangling wires cannot exist, spec §4),
    * a node cannot wire ITSELF as an input this run (``$prev`` self-
      reference is legal — that is last run's value),
    * any node declaring a ``split`` param requires the ``splits``
      section; any ``every`` other than ``once`` requires the ``clock``,
    * ``splits`` is a reserved word — no node may take the name.

    Everything needing imports (role cross-check, output contracts, DAG
    order, capital⇐stat_test) is the planner's business.
    """

    name: str
    pipeline: dict
    splits: object = None
    clock: object = None
    schedule: object = None
    env: object = None
    outputs: object = None
    tracking: object = None
    notes: str = ""
    walkforward: object = None

    def __post_init__(self):
        errors = []
        _check_str(errors, "name", self.name)
        if (
            isinstance(self.name, str)
            and self.name
            and not re.match(_NAME_OK, self.name)
        ):
            errors.append(
                f"name must be a run-dir-safe series name matching {_NAME_OK}, "
                f"got {self.name!r}"
            )
        if not isinstance(self.pipeline, dict) or not self.pipeline:
            errors.append("pipeline must be a non-empty map of node key -> node object")
        else:
            for key, spec in self.pipeline.items():
                if not isinstance(key, str) or not re.match(_NODE_KEY_OK, key):
                    errors.append(
                        f"pipeline: node keys must match {_NODE_KEY_OK}, got {key!r}"
                    )
                elif key == SPLITS_SOURCE:
                    errors.append(
                        "pipeline: 'splits' is a reserved reference source and "
                        "cannot be a node key"
                    )
                _check_child(errors, f"pipeline.{key}", spec, NodeSpec)
        _check_child(
            errors,
            "splits",
            self.splits,
            (TimeSplitConfig, RandomSplitSpec, TrailingSplitSpec),
        )
        _check_child(errors, "clock", self.clock, ClockConfig)
        _check_child(errors, "schedule", self.schedule, ScheduleConfig)
        _check_child(errors, "env", self.env, EnvConfig)
        _check_child(errors, "outputs", self.outputs, OutputsConfig)
        _check_child(errors, "tracking", self.tracking, TrackingConfig)
        _check_child(errors, "walkforward", self.walkforward, WalkForwardSpec)
        _check_str(errors, "notes", self.notes, non_empty=False)
        if not errors:
            errors.extend(self._cross_field_errors())
        _raise_if(errors)

    def _cross_field_errors(self):
        errors = []
        keys = set(self.pipeline)
        wants_split = []
        for key, spec in self.pipeline.items():
            node_refs, prev_refs = spec.refs()
            for source, _path in node_refs:
                if source == SPLITS_SOURCE:
                    if self.splits is None and self.walkforward is None:
                        errors.append(
                            f"pipeline.{key}: references '$splits...' but the "
                            "document has no splits section (a walkforward "
                            "section would materialize one per fold)"
                        )
                elif source not in keys:
                    errors.append(
                        f"pipeline.{key}: dangling wire — references "
                        f"{'$' + source!s} but no node {source!r} is declared "
                        f"(declared: {sorted(keys)})"
                    )
                elif source == key:
                    errors.append(
                        f"pipeline.{key}: a node cannot wire its own output as "
                        "an input this run — use a $prev carry for last run's "
                        "value"
                    )
            for node, _path, _default in prev_refs:
                if node not in keys:
                    errors.append(
                        f"pipeline.{key}: $prev targets {node!r} but no such "
                        f"node is declared (declared: {sorted(keys)})"
                    )
            if "split" in spec.params:
                wants_split.append(key)
            if spec.every != "once" and self.clock is None:
                errors.append(
                    f"pipeline.{key}: every={spec.every!r} is only meaningful "
                    "under a clock — add the clock section or drop 'every'"
                )
        if wants_split and self.splits is None and self.walkforward is None:
            errors.append(
                f"splits section required: node(s) {sorted(wants_split)} "
                "declare a 'split' param (a walkforward section counts — it "
                "materializes the splits per fold)"
            )
        if self.walkforward is not None:
            source, _path = parse_node_ref(self.walkforward.objective)
            if source == SPLITS_SOURCE or source not in keys:
                errors.append(
                    "walkforward.objective must reference a DECLARED node's "
                    f"output, got {self.walkforward.objective!r} "
                    f"(declared: {sorted(keys)})"
                )
        return errors

    @property
    def hash(self) -> str:
        """The experiment's identity (spec §2): canonical-JSON sha256 with
        notes stripped and env/outputs/schedule/tracking excluded."""
        return config_hash(self, exclude=DOC_NON_IDENTITY_SECTIONS)

    def to_obj(self) -> dict:
        obj = {
            "name": self.name,
            "pipeline": {k: v.to_obj() for k, v in self.pipeline.items()},
        }
        for section in ("splits", "clock", "schedule", "env", "outputs", "tracking"):
            v = getattr(self, section)
            obj[section] = v.to_obj() if v is not None else None
        if self.walkforward is not None:
            # Emitted only when present (unlike the always-emitted nulls
            # above): the section postdates the hash recipe, and an
            # always-present null would move every existing document's
            # identity (ADR-0027).
            obj["walkforward"] = self.walkforward.to_obj()
        obj["notes"] = self.notes
        return obj

    @classmethod
    def from_obj(cls, obj) -> "PipelineDocument":
        _reject_unknown(
            obj,
            (
                "name",
                "pipeline",
                "splits",
                "clock",
                "schedule",
                "env",
                "outputs",
                "tracking",
                "walkforward",
                "notes",
            ),
            "document",
        )
        errors = []
        nodes = {}
        raw_pipeline = obj.get("pipeline", {})
        if isinstance(raw_pipeline, dict):
            for key, node_obj in raw_pipeline.items():
                if not isinstance(node_obj, dict):
                    errors.append(
                        f"pipeline.{key}: a node must be an object, got {node_obj!r}"
                    )
                    continue
                try:
                    nodes[key] = NodeSpec.from_obj(node_obj)
                except ConfigError as exc:
                    errors.extend(f"pipeline.{key}: {e}" for e in exc.errors)
        else:
            nodes = raw_pipeline

        def _section(name, builder):
            raw = obj.get(name)
            if raw is None:
                return None
            try:
                return builder(raw)
            except ConfigError as exc:
                errors.extend(exc.errors)
                return None

        splits = _section("splits", doc_split_from_obj)
        clock = _section("clock", ClockConfig.from_obj)
        schedule = _section("schedule", ScheduleConfig.from_obj)
        env = _section("env", EnvConfig.from_obj)
        outputs = _section("outputs", OutputsConfig.from_obj)
        tracking = _section("tracking", TrackingConfig.from_obj)
        walkforward = _section("walkforward", WalkForwardSpec.from_obj)
        _raise_if(errors)
        return cls(
            name=obj.get("name", ""),
            pipeline=nodes,
            splits=splits,
            clock=clock,
            schedule=schedule,
            env=env,
            outputs=outputs,
            tracking=tracking,
            notes=obj.get("notes", ""),
            walkforward=walkforward,
        )


# ---------------------------------------------------------------------------
# File I/O — the io.py discipline: every parse/shape error names the path
# ---------------------------------------------------------------------------


def load_document(path) -> PipelineDocument:
    """Read and validate one node-map document (LOAD, spec §9 step 1).

    Raises
    ------
    ValueError
        Not valid JSON, or valid JSON violating the grammar — the message
        always names the path (:class:`ConfigError` is a ``ValueError``).
    OSError
        If the file cannot be read.
    """
    with open(path, encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigError([f"{path}: a document must be a JSON object, got {doc!r}"])
    try:
        return PipelineDocument.from_obj(doc)
    except ConfigError as exc:
        raise ConfigError([f"{path}: {e}" for e in exc.errors]) from exc


def save_document(document, path) -> None:
    """Write a document as canonical, human-diffable JSON (sorted keys,
    2-space indent, NaN refused). Serialized fully before the file opens."""
    text = json.dumps(document.to_obj(), indent=2, sort_keys=True, allow_nan=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
