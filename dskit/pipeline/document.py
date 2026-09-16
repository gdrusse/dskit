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
* ``"$each"`` — the ``foreach`` fan-out token (ADR-0039), reserved and
  NOT a reference: inside a template's ``params`` a value that is exactly
  this string becomes the key; anywhere else it is the literal string.
* Any other string beginning with ``$`` is refused loudly — a mistyped
  wire must never ride through as a literal.

Import cost: stdlib only.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace

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
from dskit.pipeline.split_policy import SPLIT_NAMES as _SPLIT_NAMES

__all__ = [
    "CADENCES",
    "ClockConfig",
    "ALL_PRIOR",
    "DOC_NON_IDENTITY_SECTIONS",
    "DOC_SPLIT_KINDS",
    "EACH_TOKEN",
    "FOREACH_SEP",
    "ExecutionBacktestSpec",
    "ForeachSpec",
    "MODES",
    "NodeSpec",
    "StageSpec",
    "PipelineDocument",
    "RandomSplitSpec",
    "ROLES",
    "ScheduleConfig",
    "SEARCH_SPACE_PARAM",
    "SPLITS_SOURCE",
    "SPLIT_NAMES",
    "ScheduleConfig",
    "TRAINABLE_ROLES",
    "TrailingSplitSpec",
    "WalkForwardSpec",
    "doc_split_from_obj",
    "flatten_param_paths",
    "foreach_slug",
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
    "fitted_transform",
    "replay",
)

#: Roles that may carry ``mode``/``artifact``: the ones whose node either
#: FITS or RESTORES (:class:`~dskit.pipeline.node.TrainableNode`). ONE
#: tuple — the planner's rule, the conformance bar's census and the
#: family definitions all read it, because three copies of "which roles
#: are trainable" is three places for a new family to be forgotten.
TRAINABLE_ROLES = ("train", "signal", "fitted_transform")

#: The split names a document may address — RE-EXPORTED from
#: :mod:`~dskit.pipeline.split_policy`, which owns the tuple because the
#: modules below this one read it too (the stats and synthetic score
#: nodes, the sb3 pack, the straddle report). Read by every role rule
#: that asks a node WHICH split it reads or fits on.
SPLIT_NAMES = _SPLIT_NAMES

#: Node cadences under a clock (spec §3/§7). Omitted = ``once``; anything
#: other than ``once`` is only meaningful when the document has a clock.
CADENCES = ("once", "epoch", "day", "week")

#: What a node's ``mode`` may say (spec §3): FIT, or RESTORE a pinned
#: artifact. Omitted is legal and means the class's ``default_mode``
#: (:class:`~dskit.pipeline.node.TrainableNode`), which is checked
#: against this same tuple — the grammar and the dispatch read ONE
#: vocabulary, never two copies of it.
MODES = ("train", "load")

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

def _copy_mapping_or_raw(value):
    """Copy a JSON object, leaving invalid shapes for validation."""
    return dict(value) if isinstance(value, dict) else value


PREV_KEY = "$prev"

#: The captured-artifact descriptor key (ADR-0123). Legal only as the
#: complete value of one declared node input; the parser recognizes it and
#: the document-level sweep rejects every other position.
CAPTURED_KEY = "$captured_artifact"

#: The exact six keys of a ``$captured_artifact`` descriptor, in the same
#: closed set as ``dskit/pipeline/trust.py::_DESCRIPTOR_KEYS``.
CAPTURED_DESCRIPTOR_KEYS = (
    "root_ref",
    "snapshot_version",
    "document_sha256",
    "node",
    "output",
    "purpose",
)

#: The ``foreach`` fan-out token (ADR-0039 rule 3). A template ``params``
#: value that is EXACTLY this string becomes the key string; substring
#: interpolation is never performed, which is the line between fan-out
#: and templating. It is deliberately NOT a reference — :func:`is_node_ref`
#: excludes it — so outside a template it rides through as the literal it
#: is instead of being refused as a mistyped wire.
EACH_TOKEN = "$each"

#: How a fanned-out name is spelled: a template key plus one key's slug
#: (``qhat`` + ``aapl`` -> ``qhat__aapl``), and a shared node's opt-in
#: port plus that same slug (``records__each`` -> ``records__aapl``). ONE
#: separator, so the node-key spelling and the port spelling can never
#: drift apart.
FOREACH_SEP = "__"

#: The input-port suffix that OPTS IN to port fan-out (ADR-0039 rule 4),
#: derived from the separator and the token so the two spellings agree by
#: construction rather than by two authors remembering the same string.
_EACH_PORT = FOREACH_SEP + EACH_TOKEN[1:]

#: Every character a node key may not hold — replaced by ``_`` when a
#: foreach key is slugged into one. It restates the legal-character half
#: of :data:`_NODE_KEY_OK`, which is why every GENERATED instance key is
#: checked against that grammar in :meth:`PipelineDocument._expand`: the
#: two must agree, and a runtime refusal is what pins them.
_SLUG_BAD = r"[^a-z0-9_]"

#: The search node's param whose dict KEYS are override PATHS addressing
#: OTHER nodes' params (``'<node>.<param.path>'``) rather than values —
#: the one place a node key is spelled without a ``$``. A head naming a
#: foreach template is therefore re-aimed exactly as a reference is
#: (ADR-0039: "search spaces come for free"). Named ONCE here and
#: imported by the planner and the driver, which read the same map; each
#: search KIND still names the knob itself — in its ``_PARAMS``
#: default-deny vocabulary, or in its own ``validate_params`` where it
#: declares none — because a validator sourcing its vocabulary from the
#: thing it validates asserts nothing. EVERY shipped search kind is
#: pinned to this spelling by ``tests/pipeline/test_foreach.py``, which
#: DISCOVERS them from the tree rather than naming one.
SEARCH_SPACE_PARAM = "space"


# ---------------------------------------------------------------------------
# Reference grammar
# ---------------------------------------------------------------------------


def is_node_ref(value):
    """Report whether ``value`` is a node-output reference (``"$..."``).

    Grammar validity is :func:`parse_node_ref`'s business — this detects
    only the FORM, so a malformed ``$`` string is still routed into the
    parser and refused there, never passed through as a literal.

    Parameters
    ----------
    value : object
        Any inputs or params value, of any type.

    Returns
    -------
    bool
        True for a string beginning ``$`` OTHER than :data:`EACH_TOKEN`.
        The token is a reserved fan-out placeholder, not a wire, so it is
        not a reference and every walker composed of this predicate steps
        over it (ADR-0039 rule 3).
    """
    return isinstance(value, str) and value.startswith("$") and value != EACH_TOKEN


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


def _contains_prev_ref(obj):
    """Whether a ``$prev`` carry hides anywhere in ``obj``."""
    if is_prev_ref(obj):
        return True
    if isinstance(obj, dict):
        return any(_contains_prev_ref(value) for value in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_prev_ref(value) for value in obj)
    return False


def _contains_captured_ref(obj):
    """Whether a ``$captured_artifact`` key hides anywhere in ``obj``.

    The single detection primitive for Packet 5: it recurses dict values
    and list/tuple items exactly like :func:`_contains_prev_ref`, so a
    descriptor nested under a list or map cannot evade a dict-only sweep.
    """
    if isinstance(obj, dict) and CAPTURED_KEY in obj:
        return True
    if isinstance(obj, dict):
        return any(_contains_captured_ref(value) for value in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_captured_ref(value) for value in obj)
    return False


def _captured_descriptor_shape_errors(inner, where):
    """Accumulate shape errors for one ``$captured_artifact`` descriptor.

    The exact six-key shape with scalar string values and an exact
    lowercase SHA-256 ``document_sha256`` (no placeholder, no ``self``).
    A ``$``-prefixed scalar is a wire, not a literal, and refuses.
    """
    errors = []
    if not isinstance(inner, dict):
        return [f"{where}: $captured_artifact must be an object, got {inner!r}"]
    unknown = sorted(
        (key for key in inner if key not in CAPTURED_DESCRIPTOR_KEYS), key=repr
    )
    if unknown:
        errors.append(
            f"{where}: $captured_artifact unknown key(s) {unknown} — allowed: "
            f"{list(CAPTURED_DESCRIPTOR_KEYS)}"
        )
    missing = sorted(set(CAPTURED_DESCRIPTOR_KEYS) - set(inner))
    if missing:
        errors.append(f"{where}: $captured_artifact missing key(s) {missing}")
    if unknown or missing:
        return errors
    for key in ("root_ref", "snapshot_version", "node", "output", "purpose"):
        value = inner[key]
        if not isinstance(value, str) or not value or value.startswith("$"):
            errors.append(
                f"{where}: $captured_artifact.{key} must be a non-empty "
                f"literal string, got {value!r}"
            )
    document_sha256 = inner["document_sha256"]
    if not isinstance(document_sha256, str) or not _SHA256_OK.fullmatch(document_sha256):
        errors.append(
            f"{where}: $captured_artifact.document_sha256 must be an exact "
            f"lowercase SHA-256, got {document_sha256!r}"
        )
    elif document_sha256 == "0" * 64:
        errors.append(f"{where}: $captured_artifact.document_sha256 placeholder is refused")
    return errors


def _captured_position_errors(obj):
    """Return errors for every ``$captured_artifact`` outside a legal position.

    One full-document walk (Packet 5 changed approach): the only legal
    position is the complete (sole-key) value of a
    ``pipeline.<node>.inputs.<port>`` entry in an execution_backtest
    document. Every other occurrence — in params, tracking sinks, stages,
    foreach templates, outputs, env, schedule, clock, splits, walkforward,
    a ``$prev`` default, artifact, nested lists/maps, or any depth — refuses.
    """
    errors = []
    has_execution = obj.get("execution_backtest") is not None
    pipeline = obj.get("pipeline")
    if isinstance(pipeline, dict):
        for node_key, node_obj in pipeline.items():
            if not isinstance(node_obj, dict):
                continue
            inputs = node_obj.get("inputs")
            if isinstance(inputs, dict):
                for port, value in inputs.items():
                    where = f"pipeline.{node_key}.inputs.{port}"
                    if isinstance(value, dict) and CAPTURED_KEY in value:
                        if set(value) != {CAPTURED_KEY}:
                            errors.append(
                                f"{where}: $captured_artifact must be the "
                                "complete input value"
                            )
                        if not has_execution:
                            errors.append(
                                f"{where}: $captured_artifact requires an "
                                "execution_backtest document"
                            )
                    elif _contains_captured_ref(value):
                        errors.append(
                            f"{where}: $captured_artifact is only legal as the "
                            "complete value of a declared node input"
                        )
            for field, child in node_obj.items():
                if field == "inputs":
                    continue
                if _contains_captured_ref(child):
                    errors.append(
                        f"pipeline.{node_key}.{field}: $captured_artifact is "
                        "only legal as the complete value of a declared node input"
                    )
    for key, child in obj.items():
        if key in ("pipeline", "execution_backtest"):
            continue
        if _contains_captured_ref(child):
            errors.append(
                f"{key}: $captured_artifact is only legal as the complete "
                "value of a declared node input"
            )
    return errors


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
                elif isinstance(ref, dict) and CAPTURED_KEY in ref:
                    if set(ref) != {CAPTURED_KEY}:
                        errors.append(
                            f"inputs.{port}: $captured_artifact must be the "
                            "complete input value"
                        )
                    else:
                        errors.extend(
                            _captured_descriptor_shape_errors(
                                ref[CAPTURED_KEY], f"inputs.{port}"
                            )
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
        if self.mode is not None and self.mode not in MODES:
            errors.append(
                f"mode must be one of {list(MODES)} (or absent), got {self.mode!r}"
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
            inputs=_copy_mapping_or_raw(obj.get("inputs", {})),
            params=_copy_mapping_or_raw(obj.get("params", {})),
            every=obj.get("every", "once"),
            mode=obj.get("mode", None),
            artifact=obj.get("artifact", ""),
            role=obj.get("role", None),
            notes=obj.get("notes", ""),
        )


_EXECUTION_BACKTEST_SCHEMA = "dskit.execution-backtest/v1"
_EXECUTION_BACKTEST_PURPOSES = ("synthetic", "historical-simulator")
_EXECUTION_BACKTEST_FORBIDDEN_SECTIONS = ("stages", "foreach", "walkforward")
_EVENT_ENVELOPE_SCHEMA = "dskit.event-envelope/v2"
_SHA256_OK = re.compile(r"^[0-9a-f]{64}$")

#: The toolkit-owned ReplayRun consumer kind (ADR-0127). A node that uses
#: this kind declares exactly two captured inputs, ``tape_manifest`` and
#: ``tape_data``, and is legal only in an ``execution_backtest`` document.
REPLAY_RUN_KIND = "replay"
_REPLAY_TAPE_INPUTS = ("tape_manifest", "tape_data")


def _execution_backtest_section_errors(sections):
    return [
        f"execution_backtest documents forbid user-authored {section}; "
        "capture barriers are derived from verified captured ports"
        for section in sections
    ]


def _execution_backtest_node_errors(node_maps):
    """Return forbidden ordinary-pipeline semantics in execution node maps."""
    errors = []
    for where, specs in node_maps:
        if not isinstance(specs, dict):
            continue
        for key, spec in specs.items():
            if not isinstance(spec, NodeSpec):
                continue
            node_where = f"{where}.{key}"
            if _contains_prev_ref(spec.inputs) or _contains_prev_ref(spec.params):
                errors.append(
                    f"{node_where}: execution_backtest documents forbid $prev "
                    "carries"
                )
            if spec.mode == "load":
                errors.append(
                    f"{node_where}: execution_backtest documents forbid "
                    "mode 'load'"
                )
            if spec.artifact:
                errors.append(
                    f"{node_where}: execution_backtest documents forbid "
                    "artifact pins"
                )
    return errors


def _replay_node_errors(node_maps, has_execution):
    """Return the ReplayRun consumer tape-pair grammar refusals.

    A node whose ``uses`` is the owned ``replay`` kind must live in an
    ``execution_backtest`` document and declare exactly the two captured
    inputs ``tape_manifest`` and ``tape_data``. The pair is keyed on the
    owned kind name (ADR-0127), never on a uses-string resemblance.
    """
    errors = []
    for where, specs in node_maps:
        if not isinstance(specs, dict):
            continue
        for key, spec in specs.items():
            if not isinstance(spec, NodeSpec) or spec.uses != REPLAY_RUN_KIND:
                continue
            node_where = f"{where}.{key}"
            if not has_execution:
                errors.append(
                    f"{node_where}: the 'replay' kind is legal only in an "
                    "execution_backtest document"
                )
                continue
            ports = set(spec.inputs)
            if ports != set(_REPLAY_TAPE_INPUTS):
                errors.append(
                    f"{node_where}: the 'replay' node must declare exactly the "
                    "inputs tape_manifest and tape_data, got "
                    f"{sorted(ports) or 'none'}"
                )
                continue
            for port in _REPLAY_TAPE_INPUTS:
                value = spec.inputs[port]
                if not (isinstance(value, dict) and set(value) == {CAPTURED_KEY}):
                    errors.append(
                        f"{node_where}.inputs.{port}: the 'replay' input must be "
                        "a complete $captured_artifact descriptor, not a wired "
                        "or non-descriptor value"
                    )
    return errors


@dataclass(frozen=True, slots=True)
class ExecutionBacktestSpec:
    """Closed, versioned identity for an execution-pipeline document."""

    schema_version: str
    purpose: str
    event_envelope_schema: str
    source_rank_policy_sha256: str
    execution_profile_sha256: str
    environment_identity_sha256: str
    notes: str = ""

    def __post_init__(self):
        """Validate every required v1 member without supplying defaults."""
        errors = []
        _check_str(errors, "execution_backtest.schema_version", self.schema_version)
        if (
            isinstance(self.schema_version, str)
            and self.schema_version
            and self.schema_version != _EXECUTION_BACKTEST_SCHEMA
        ):
            errors.append(
                "execution_backtest.schema_version must be "
                f"{_EXECUTION_BACKTEST_SCHEMA!r}, got {self.schema_version!r}"
            )
        if self.purpose not in _EXECUTION_BACKTEST_PURPOSES:
            errors.append(
                "execution_backtest.purpose must be one of "
                f"{list(_EXECUTION_BACKTEST_PURPOSES)}, got {self.purpose!r}"
            )
        _check_str(
            errors,
            "execution_backtest.event_envelope_schema",
            self.event_envelope_schema,
        )
        if (
            isinstance(self.event_envelope_schema, str)
            and self.event_envelope_schema
            and self.event_envelope_schema != _EVENT_ENVELOPE_SCHEMA
        ):
            errors.append(
                "execution_backtest.event_envelope_schema must be "
                f"{_EVENT_ENVELOPE_SCHEMA!r}, got {self.event_envelope_schema!r}"
            )
        for name in (
            "source_rank_policy_sha256",
            "execution_profile_sha256",
            "environment_identity_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_OK.fullmatch(value):
                errors.append(
                    f"execution_backtest.{name} must be an exact lowercase "
                    f"SHA-256 digest, got {value!r}"
                )
        _check_str(errors, "execution_backtest.notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self):
        """Return all identity members plus standard hash-excluded notes."""
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj):
        """Parse the exact v1 shape; no semantic member has a default."""
        if not isinstance(obj, dict):
            raise ConfigError(
                [f"execution_backtest must be an object, got {obj!r}"]
            )
        required = (
            "schema_version",
            "purpose",
            "event_envelope_schema",
            "source_rank_policy_sha256",
            "execution_profile_sha256",
            "environment_identity_sha256",
        )
        allowed = (*required, "notes")
        errors = []
        unknown = sorted(set(obj) - set(allowed))
        if unknown:
            errors.append(
                "execution_backtest: unknown key(s) "
                f"{unknown} — allowed: {sorted(allowed)}"
            )
        missing = sorted(set(required) - set(obj))
        if missing:
            errors.append(
                "execution_backtest: missing required key(s) " f"{missing}"
            )
        _raise_if(errors)
        return cls(
            **{name: obj[name] for name in required},
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One resumable study stage (ADR-0081).

    Parameters
    ----------
    uses : str
        Registered stage kind or an import reference.
    inputs : dict, optional
        Port names to ``$stage.output`` references.
    params : dict, optional
        JSON configuration passed to the stage class.
    notes : str, optional
        Explanatory text, excluded from document identity.

    Examples
    --------
    A stage consuming a prior stage's artifact::

        spec = StageSpec(
            uses="example-stage",
            inputs={"gate": "$gate1.result"},
            params={"alpha": 0.05},
        )
    """

    uses: str
    inputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        """Validate the stage declaration."""
        errors = []
        _check_str(errors, "uses", self.uses)
        if (
            isinstance(self.uses, str)
            and self.uses
            and not (re.match(_KIND_OK, self.uses) or is_class_ref(self.uses))
        ):
            errors.append(
                "uses must be a registered stage kind or an import "
                f"reference, got {self.uses!r}"
            )
        if not isinstance(self.inputs, dict):
            errors.append(f"inputs must be a dict, got {self.inputs!r}")
        else:
            for port, ref in self.inputs.items():
                if not isinstance(port, str) or not re.match(_NODE_KEY_OK, port):
                    errors.append(f"inputs: bad port name {port!r}")
                if not is_node_ref(ref):
                    errors.append(
                        f"inputs.{port} must be a '$stage.output' reference, "
                        f"got {ref!r}"
                    )
        _check_open_dict(errors, "params", self.params)
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    def refs(self):
        """Return stage input references as ``(source, path)`` pairs."""
        return tuple(parse_node_ref(ref) for ref in self.inputs.values())

    def to_obj(self):
        """Return the canonical JSON object."""
        return _dataclass_to_obj(self)

    @classmethod
    def from_obj(cls, obj):
        """Build one stage declaration from a JSON object."""
        _reject_unknown(obj, ("uses", "inputs", "params", "notes"), "stage")
        return cls(
            uses=obj.get("uses", ""),
            inputs=_copy_mapping_or_raw(obj.get("inputs", {})),
            params=_copy_mapping_or_raw(obj.get("params", {})),
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
        paying for the data edge. Bounded ``train_days`` materializes
        (ADR-0050); this stays empty unless a future knob cannot.
        """
        return ""

    def materialize(self, newest_ms) -> TimeSplitConfig:
        """The pure cut arithmetic: pinned causal cuts counted backward
        from ``newest_ms`` (the newest settled event's instant). The
        returned :class:`TimeSplitConfig` carries ``test_end_ms ==
        newest_ms`` and re-validates strict ordering by construction.

        Only ``train_days == "all-prior"`` leaves train unbounded on the
        left. An integer ``train_days`` stamps ``train_start_ms`` so train
        holds exactly that many daily stamps (ADR-0050)."""
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
        train_start = None
        if self.train_days != ALL_PRIOR:
            train_start = train_end - self.train_days * _DAY_MS + 1
            if train_start < 1:
                raise ValueError(
                    f"trailing splits need newest_ms deep enough for the "
                    f"windows: newest_ms={newest_ms} leaves "
                    f"train_start_ms={train_start}"
                )
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
            train_start_ms=train_start,
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
    outputs — what each fold reports upward. Train windows default to
    expanding (``"all-prior"``); an integer ``train_days`` slides a
    bounded window (ADR-0050). Optional ``weight_halflife_folds`` applies
    recency weights to the aggregate (ADR-0053), omitted when unset.

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
    train_days: object = ALL_PRIOR
    weight_halflife_folds: int = 0

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
                or any(not isinstance(f, str) or _date_problem(f) for f in self.folds)
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
        if self.train_days != ALL_PRIOR:
            _check_int(errors, "walkforward.train_days", self.train_days, ge=1)
        _check_int(
            errors,
            "walkforward.weight_halflife_folds",
            self.weight_halflife_folds,
            ge=0,
        )
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
        if self.train_days == ALL_PRIOR:
            del obj["train_days"]
        if not self.weight_halflife_folds:
            del obj["weight_halflife_folds"]
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
                "train_days",
                "weight_halflife_folds",
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
            train_days=obj.get("train_days", ALL_PRIOR),
            weight_halflife_folds=obj.get("weight_halflife_folds", 0),
        )


# ---------------------------------------------------------------------------
# Foreach (ADR-0039) — declared fan-out over a key list. The document
# STORES what was written (this section, which is identity) and DERIVES
# what runs (``PipelineDocument.expanded``, which is not).
# ---------------------------------------------------------------------------


def foreach_slug(key):
    """Slug one ``foreach`` key into the name half of an instance key.

    Parameters
    ----------
    key : str
        A ``foreach.keys`` entry, exactly as the document wrote it.

    Returns
    -------
    str
        The key lowercased, with every character no node key may hold
        replaced by ``_`` (``"BTC-USD"`` -> ``"btc_usd"``). The RAW key
        is what :data:`EACH_TOKEN` substitutes into params; the slug is
        only ever a NAME. Two keys may slug alike — the expansion
        refuses that collision by name rather than merging them.
    """
    return re.sub(_SLUG_BAD, "_", key.lower())


def _instance_key(name, slug):
    """One template key (or port base) plus one key's slug."""
    return f"{name}{FOREACH_SEP}{slug}"


def _each_key_errors(where, obj):
    """Every place :data:`EACH_TOKEN` is used as a dict KEY inside ``obj``."""
    errors = []
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == EACH_TOKEN:
                errors.append(
                    f"{where}: {EACH_TOKEN!r} is not legal as a params KEY — "
                    "key substitution is not built, and letting the token ride "
                    "unchanged onto every instance is exactly the ride-through "
                    "this grammar exists to refuse"
                )
            errors.extend(_each_key_errors(f"{where}.{name}", value))
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            errors.extend(_each_key_errors(f"{where}[{i}]", value))
    return errors


def _template_node_errors(where, spec):
    """Collect the refusals a template node earns before any key is applied."""
    errors = []
    for port in spec.inputs:
        if port.endswith(_EACH_PORT):
            errors.append(
                f"{where}: input port {port!r} asks for foreach port fan-out, "
                "which is a SHARED node's opt-in — inside a template every "
                "reference to a template key already rewrites to this "
                "instance"
            )
    if spec.artifact:
        errors.append(
            f"{where}: a template may not pin a node-level artifact "
            f"({spec.artifact!r}) — the pin names ONE stored model, so every "
            "instance would restore that same one while the TRAIN half of the "
            "same fan-out writes an artifact dir per instance, and nothing "
            f"would say so. Rule 3 cannot rescue it: {EACH_TOKEN!r} is a "
            "whole-value params substitution, so here it is a literal and not "
            "a path, and this grammar builds no interpolation. Pin the "
            "artifact through a param or a wired port on a kind whose "
            "default_mode is 'load', or write the loading nodes longhand"
        )
    errors.extend(_each_key_errors(f"{where}.params", spec.params))
    return errors


@dataclass(frozen=True, slots=True)
class ForeachSpec:
    """Declared fan-out over a key list (ADR-0039).

    ONE template subgraph, instantiated once per key at document
    construction.

    "One model per symbol" written longhand is N byte-identical node pairs
    whose only difference is a name and one param — and N unpinned copies
    of every search-space key. This section collapses that to one
    declaration. It is deliberately NOT a template language: no
    expressions, no conditionals, no nesting. The whole vocabulary is the
    key list, the suffixing rule, reference rewrite inside the template
    (including a search space's override PATHS, which name nodes without
    a ``$``), the :data:`EACH_TOKEN` whole-value substitution, and a
    shared node's opt-in ``<base>__each`` port.

    ``keys`` is SORTED and pinned as a tuple: keys are a set, so sorting
    beats refusing-unless-sorted. A key beginning ``$`` refuses, because
    the RAW key is substituted as a params value and ``"$window.records"``
    would expand into a live reference.

    Parameters
    ----------
    keys : list of str
        The fan-out keys: non-empty, unique, non-empty strings, none
        starting ``$``. Sorted at construction.
    pipeline : dict
        The template subgraph — ``template key`` -> :class:`NodeSpec`,
        non-empty, normalized exactly as a node is (so ``{"uses": "x"}``
        and its fully-spelled twin hash identically).
    notes : str, optional
        Why this fan-out exists; never hash material.

    Raises
    ------
    ConfigError
        Listing every shape problem at once — an empty or duplicated key
        list, a ``$``-prefixed key, an empty template map, a template
        using :data:`EACH_TOKEN` as a params key, a template declaring a
        ``<base>__each`` port, or a template pinning a node-level
        ``artifact``. That last one is refused because the pin names ONE
        stored model and the grammar has no interpolation: N instances
        would silently restore one artifact while their TRAIN half wrote
        one dir each.

    Examples
    --------
    One filter per symbol, keyed by the symbol itself::

        spec = ForeachSpec(
            keys=["AAPL", "MSFT"],
            pipeline={
                "rows": NodeSpec(
                    uses="filter",
                    inputs={"records": "$bars.records"},
                    params={
                        "where": [
                            {"field": "symbol", "op": "==", "value": "$each"}
                        ]
                    },
                )
            },
        )
        spec.keys  # ('AAPL', 'MSFT')
    """

    keys: tuple
    pipeline: dict
    notes: str = ""

    def __post_init__(self):
        """Validate the section and pin its key list, or raise ConfigError."""
        errors = []
        self._check_keys(errors)
        self._check_templates(errors)
        _check_str(errors, "foreach.notes", self.notes, non_empty=False)
        _raise_if(errors)
        # Pin the validated key list as a SORTED tuple: the frozen spec
        # must not share a mutable list with whoever built it, and a
        # canonical order makes emission order a property of the grammar
        # rather than of JSON key order.
        object.__setattr__(self, "keys", tuple(sorted(self.keys)))

    def _check_keys(self, errors):
        """Accumulate the key-list problems."""
        if not isinstance(self.keys, (list, tuple)) or not self.keys:
            errors.append(
                "foreach.keys must be a non-empty list of unique, non-empty "
                f"strings — a fan-out with no keys fans over nothing, got "
                f"{self.keys!r}"
            )
            return
        seen = set()
        for key in self.keys:
            if not isinstance(key, str) or not key:
                errors.append(
                    f"foreach.keys: every key must be a non-empty string, got {key!r}"
                )
                continue
            if key.startswith("$"):
                errors.append(
                    f"foreach.keys: key {key!r} may not begin with '$' — the "
                    f"RAW key is substituted for {EACH_TOKEN!r} in a template's "
                    "params, and a $-key would expand into a live reference"
                )
            if key in seen:
                errors.append(
                    f"foreach.keys: key {key!r} is declared twice — keys are a "
                    "set, and a repeat would build the same instance twice"
                )
            seen.add(key)

    def _check_templates(self, errors):
        """Accumulate the template-subgraph problems."""
        if not isinstance(self.pipeline, dict) or not self.pipeline:
            errors.append(
                "foreach.pipeline must be a non-empty map of template key -> "
                "node object — a foreach declaring no template fans out "
                f"nothing, got {self.pipeline!r}"
            )
            return
        for key, spec in self.pipeline.items():
            if not isinstance(key, str) or not re.match(_NODE_KEY_OK, key):
                errors.append(
                    f"foreach.pipeline: template keys must match "
                    f"{_NODE_KEY_OK}, got {key!r}"
                )
            elif key == SPLITS_SOURCE:
                errors.append(
                    "foreach.pipeline: 'splits' is a reserved reference source "
                    "and cannot be a template key"
                )
            _check_child(errors, f"foreach.pipeline.{key}", spec, NodeSpec)
            if isinstance(spec, NodeSpec):
                errors.extend(_template_node_errors(f"foreach.pipeline.{key}", spec))

    def to_obj(self):
        """Serialize this section — the identity payload, all of it.

        Returns
        -------
        dict
            ``keys`` (a sorted list), ``pipeline`` (each template
            serialized as a node) and ``notes``. Nothing DERIVED appears
            here: the expansion belongs to the document, and what
            ``to_obj`` cannot say can never be hash material.
        """
        return {
            "keys": list(self.keys),
            "pipeline": {k: v.to_obj() for k, v in self.pipeline.items()},
            "notes": self.notes,
        }

    @classmethod
    def from_obj(cls, obj):
        """Rebuild one ``foreach`` section from its serialized form.

        Parameters
        ----------
        obj : dict
            The section's ``keys`` / ``pipeline`` / ``notes``; any other
            key is refused by name.

        Returns
        -------
        ForeachSpec
            The validated section.

        Raises
        ------
        ConfigError
            On an unknown key, a template that is not an object, or any
            shape problem the constructor accumulates.
        """
        _reject_unknown(obj, ("keys", "pipeline", "notes"), "foreach")
        errors = []
        templates = {}
        raw = obj.get("pipeline", {})
        if isinstance(raw, dict):
            for key, node_obj in raw.items():
                if not isinstance(node_obj, dict):
                    errors.append(
                        f"foreach.pipeline.{key}: a node must be an object, "
                        f"got {node_obj!r}"
                    )
                    continue
                try:
                    templates[key] = NodeSpec.from_obj(node_obj)
                except ConfigError as exc:
                    errors.extend(f"foreach.pipeline.{key}: {e}" for e in exc.errors)
        else:
            templates = raw
        _raise_if(errors)
        return cls(
            keys=obj.get("keys", ()),
            pipeline=templates,
            notes=obj.get("notes", ""),
        )


def _rewrite_refs(obj, template_keys, slug):
    """Aim every reference naming a template key at that template's instance.

    ADR-0039 rule 2, for one key. Shared-node and ``$splits`` references
    pass untouched.
    """
    if is_prev_ref(obj):
        node, path, default = parse_prev_ref(obj)
        head = _instance_key(node, slug) if node in template_keys else node
        return {PREV_KEY: ".".join((head, *path)), "default": default}
    if is_node_ref(obj):
        source, path = parse_node_ref(obj)
        if source not in template_keys:
            return obj
        return "$" + ".".join((_instance_key(source, slug), *path))
    if isinstance(obj, dict):
        return {k: _rewrite_refs(v, template_keys, slug) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_rewrite_refs(v, template_keys, slug) for v in obj)
    return obj


def _substitute_each(obj, key):
    """Replace every value that is EXACTLY :data:`EACH_TOKEN` with ``key``.

    ADR-0039 rule 3, at any depth. Whole values only — a string that
    merely CONTAINS the token is left alone.
    """
    if isinstance(obj, str):
        return key if obj == EACH_TOKEN else obj
    if isinstance(obj, dict):
        return {k: _substitute_each(v, key) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_substitute_each(v, key) for v in obj)
    return obj


def _space_instances(target, targets):
    """Name the key(s) one space key becomes — itself, or one per instance."""
    head = target.split(".", 1)[0] if isinstance(target, str) else None
    names = targets.get(head)
    if not names:
        return (target,)
    return tuple(name + target[len(head) :] for name in names)


def _fanned_space(where, params, targets):
    """Re-aim ANY node's top-level ``space`` paths at the instances.

    A ``space`` key is the one place the grammar spells a node key
    WITHOUT a ``$`` — it is an override path, not a wire — so rule 2
    would step straight over it and leave the key addressing a template
    that is not a node. ADR-0039 says a space key naming a template param
    expands per instance instead, which is what stops N instances from
    needing N unpinned copies of the same declaration.

    This fans the top-level ``space`` key of EVERY node it is handed, not
    of search-role nodes only, and it cannot do better: a role lives on
    the node CLASS and is resolved at plan, while this is the shape
    layer, which deliberately resolves no classes — teaching it to would
    buy a cosmetic narrowing with a real architectural break. Within the
    dict only keys whose HEAD names a foreach template are re-aimed;
    every other key is carried across as written. So a non-search kind
    declaring a top-level ``space`` does come back rewritten, and that is
    harmless because such a document never reaches the engine: ``space``
    is not among that kind's declared params, so default-deny refuses it
    at plan. Pinned, rather than assumed, by
    ``test_a_non_search_kind_carrying_a_space_param_is_refused_at_plan``
    and — across the whole shipped kind vocabulary —
    ``test_no_shipped_kind_but_the_search_ones_accepts_a_space_param``.
    The harmlessness is exactly as wide as default-deny is: a class that
    enumerates no allowed set (``synthetic_nodes``, the demo double)
    refuses no unknown param at all, which is a property of that class
    and not of ``space``.

    Unlike port fan-out this needs no opt-in marker: a ``Node`` declares
    no port set, so a port's fannability is unknowable, while a space
    key's head either names a template or does not, and a head naming one
    could not have been a legal document before. Only the TOP-LEVEL
    params key is read, exactly where the planner and the driver read it
    — a nested dict that happens to be called ``space`` is data.

    Parameters
    ----------
    where : str
        The node's error prefix, e.g. ``"pipeline.tune"``.
    params : dict
        The node's params, already reference-rewritten.
    targets : dict
        Template key -> the instance names one of its space keys becomes:
        the single instance inside a template, and every instance for a
        shared node.

    Returns
    -------
    tuple
        ``(params, errors)``: the params — the ORIGINAL object when no
        key moved, so a node nothing touched keeps its spec — and the
        list of problems, one per name two space keys both address.
    """
    space = params.get(SEARCH_SPACE_PARAM)
    if not isinstance(space, dict):
        return params, []
    out, errors, sources = {}, [], {}
    for target, grid in space.items():
        for name in _space_instances(target, targets):
            if name in out:
                errors.append(
                    f"{where}: search.{SEARCH_SPACE_PARAM} addresses {name!r} "
                    f"twice — once as {sources[name]!r} and once as "
                    f"{target!r}. A key naming a foreach template expands to "
                    "EVERY instance, so naming an instance as well leaves two "
                    "grids for one param and one of them silently loses"
                )
            sources[name] = target
            out[name] = grid
    if out == space:
        return params, errors
    return {**params, SEARCH_SPACE_PARAM: out}, errors


def _instantiate(template, template_keys, key, slug, where):
    """One template node as its instance for one foreach key (rules 2-3).

    Returns ``(spec, errors)``; a top-level ``space`` inside a template
    has its keys re-aimed at THIS instance, the same way its references
    are — on any node, for the reason :func:`_fanned_space` gives.
    """
    rewritten = _rewrite_refs(template.params, template_keys, slug)
    params, errors = _fanned_space(
        where,
        _substitute_each(rewritten, key),
        {t: (_instance_key(t, slug),) for t in template_keys},
    )
    return (
        replace(
            template,
            inputs=_rewrite_refs(template.inputs, template_keys, slug),
            params=params,
        ),
        errors,
    )


def _instance_name_errors(groups):
    """Refuse a generated name the node-key grammar itself would not accept."""
    errors = []
    for tkey, instances in groups.items():
        for name in instances:
            if not re.match(_NODE_KEY_OK, name):
                errors.append(
                    f"foreach: instance key {name!r} (template {tkey!r}) does "
                    f"not match the node-key grammar {_NODE_KEY_OK} — the slug "
                    f"rule ({_SLUG_BAD}) and that grammar have drifted apart, "
                    "and a generated key no declared key could spell would "
                    "reach the plan, the run dir and the $prev namespace"
                )
    return errors


def _collision_errors(shared, groups):
    """Refuse every name two provenances could both claim (rule 1)."""
    errors = []
    owners = {key: "a declared node" for key in shared}
    for tkey in groups:
        if tkey in owners:
            errors.append(
                f"foreach.pipeline.{tkey}: template key {tkey!r} is also "
                f"{owners[tkey]} — shared, template and instance names must be "
                "pairwise distinct so every name has ONE provenance"
            )
        else:
            owners[tkey] = f"the template {tkey!r}"
    for tkey, instances in groups.items():
        for name in instances:
            if name in owners:
                errors.append(
                    f"foreach: instance key {name!r} (template {tkey!r}) "
                    f"collides with {owners[name]} — shared, template and "
                    "instance names must be pairwise distinct"
                )
            else:
                owners[name] = f"an instance of the template {tkey!r}"
    return errors


def _reach_message(key, where, source, groups):
    """Word the one refusal a shared node naming a template key earns."""
    return (
        f"pipeline.{key}: {where} references the foreach template {source!r}, "
        f"which is not a node — the template exists once per key "
        f"({list(groups[source])}). Fan a port out with '<port>{_EACH_PORT}', "
        "or name one instance"
    )


def _reach_errors(key, spec, groups):
    """Refuse a shared node reaching into a template other than by fan-out."""
    errors = []
    for port, ref in spec.inputs.items():
        if port.endswith(_EACH_PORT):
            continue  # rule 4's opt-in — checked where it fans out
        source, _path = parse_node_ref(ref)
        if source in groups:
            errors.append(_reach_message(key, f"inputs.{port}", source, groups))
    node_refs, prev_refs = [], []
    _collect_refs(spec.params, node_refs, prev_refs)
    sources = [s for s, _p in node_refs] + [n for n, _p, _d in prev_refs]
    for source in sources:
        if source in groups:
            errors.append(_reach_message(key, "params", source, groups))
    return errors


def _fanned_inputs(key, spec, groups, keys, slugs):
    """Fan a shared node's opt-in ``<base>__each`` ports out (rule 4).

    Returns ``(inputs, errors)``. ``inputs`` is None when the node
    declares no ``<base>__each`` port (the caller then reuses the node
    object unchanged) or when the fan-out itself refused.
    """
    if not any(port.endswith(_EACH_PORT) for port in spec.inputs):
        return None, []
    errors = []
    out = {}
    for port, ref in spec.inputs.items():
        if not port.endswith(_EACH_PORT):
            out[port] = ref
            continue
        base = port[: -len(_EACH_PORT)]
        source, path = parse_node_ref(ref)
        if not base:
            errors.append(
                f"pipeline.{key}: input port {port!r} needs a base name before "
                f"{_EACH_PORT!r} — the fanned-out ports are named "
                f"'<base>{FOREACH_SEP}<key>'"
            )
        elif source not in groups:
            errors.append(
                f"pipeline.{key}: input port {port!r} asks for foreach fan-out "
                f"but {ref!r} names {source!r}, which is not a foreach template "
                f"(templates: {sorted(groups)})"
            )
        else:
            for each_key in keys:
                fanned = _instance_key(base, slugs[each_key])
                if fanned in out or fanned in spec.inputs:
                    errors.append(
                        f"pipeline.{key}: fanned-out port {fanned!r} collides "
                        f"with a port the node already declares"
                    )
                out[fanned] = "$" + ".".join(
                    (_instance_key(source, slugs[each_key]), *path)
                )
    return (None if errors else out), errors


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

    A ``foreach`` section (ADR-0039) is expanded HERE, at construction:
    the document stores what was WRITTEN (``pipeline`` + ``foreach``,
    both identity) and derives what RUNS (``expanded`` and
    ``foreach_groups``, neither ever emitted by :meth:`to_obj`, which is
    what makes them provably not hash material). With no ``foreach``,
    ``expanded`` IS ``pipeline`` — the same object — so every engine site
    that reads it is byte-identical to reading the declared map.
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
    foreach: object = None
    stages: object = None
    execution_backtest: object = None
    #: DERIVED, never declared and never emitted: the node map that
    #: actually runs, and ``template key -> instance keys``. ``init=False``
    #: so no caller can inject one, ``compare=False`` because they are a
    #: pure function of the fields that ARE compared.
    expanded: dict = field(default=None, init=False, compare=False, repr=False)
    foreach_groups: dict = field(default=None, init=False, compare=False, repr=False)

    def __post_init__(self):
        """Validate every section, derive the expansion, or raise ConfigError."""
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
        if not isinstance(self.pipeline, dict) or (
            not self.pipeline and self.foreach is None
        ):
            errors.append(
                "pipeline must be a non-empty map of node key -> node object "
                "(it may be empty ONLY when a foreach section declares the "
                "templates that fan out)"
            )
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
        _check_child(errors, "foreach", self.foreach, ForeachSpec)
        _check_child(
            errors,
            "execution_backtest",
            self.execution_backtest,
            ExecutionBacktestSpec,
        )
        if self.stages is not None:
            if not isinstance(self.stages, dict) or not self.stages:
                errors.append("stages must be a non-empty map when declared")
            else:
                for key, spec in self.stages.items():
                    if not isinstance(key, str) or not re.match(_NODE_KEY_OK, key):
                        errors.append(
                            f"stages: keys must match {_NODE_KEY_OK}, got {key!r}"
                        )
                    _check_child(errors, f"stages.{key}", spec, StageSpec)
        node_maps = [("pipeline", self.pipeline)]
        if isinstance(self.foreach, ForeachSpec):
            node_maps.append(("foreach.pipeline", self.foreach.pipeline))
        if self.execution_backtest is not None:
            errors.extend(
                _execution_backtest_section_errors(
                    section
                    for section in _EXECUTION_BACKTEST_FORBIDDEN_SECTIONS
                    if getattr(self, section) is not None
                )
            )
            errors.extend(_execution_backtest_node_errors(node_maps))
        errors.extend(
            _replay_node_errors(node_maps, self.execution_backtest is not None)
        )
        _check_str(errors, "notes", self.notes, non_empty=False)
        # The derived pair defaults to the declared map — with no foreach
        # that IS the answer, and the expansion overwrites it otherwise.
        object.__setattr__(self, "expanded", self.pipeline)
        object.__setattr__(self, "foreach_groups", {})
        if not errors:
            errors.extend(self._expand())
        if not errors:
            errors.extend(self._cross_field_errors())
        _raise_if(errors)

    def _expand(self):
        """Install the derived map; return the expansion's refusals.

        Emission order is FIXED because the toposort breaks ties on it:
        shared nodes in declaration order, then template-major and
        key-minor. With no ``foreach`` nothing is installed and the
        derived map stays the declared one — the same object.

        A shared node is REBUILT only where the fan-out touched it — an
        opt-in port, or a top-level ``space`` naming a template — so a
        document whose shared nodes are all untouched keeps their spec
        objects. ``$each`` is NOT touched here: rule 3 substitutes inside
        a template only, so the token rides through a shared node as the
        literal string.
        """
        if self.foreach is None:
            return []
        keys = self.foreach.keys
        slugs = {k: foreach_slug(k) for k in keys}
        groups = {
            t: tuple(_instance_key(t, slugs[k]) for k in keys)
            for t in self.foreach.pipeline
        }
        errors = _instance_name_errors(groups) + _collision_errors(
            self.pipeline, groups
        )
        if errors:
            return errors
        expanded = {}
        for key, spec in self.pipeline.items():
            inputs, problems = _fanned_inputs(key, spec, groups, keys, slugs)
            errors.extend(problems)
            errors.extend(_reach_errors(key, spec, groups))
            params, problems = _fanned_space(f"pipeline.{key}", spec.params, groups)
            errors.extend(problems)
            changes = {}
            if inputs is not None:
                changes["inputs"] = inputs
            if params is not spec.params:
                changes["params"] = params
            expanded[key] = replace(spec, **changes) if changes else spec
        templates = set(self.foreach.pipeline)
        for tkey, template in self.foreach.pipeline.items():
            # `groups[tkey]` was built from `keys` in that order two
            # statements above, so the zip pairs each instance NAME with
            # the key it was named for — one source, never two lists.
            for name, key in zip(groups[tkey], keys):
                expanded[name], problems = _instantiate(
                    template, templates, key, slugs[key], f"pipeline.{name}"
                )
                errors.extend(problems)
        if errors:
            return errors
        object.__setattr__(self, "expanded", expanded)
        object.__setattr__(self, "foreach_groups", groups)
        return []

    def _cross_field_errors(self):
        errors = []
        keys = set(self.expanded)
        wants_split = []
        for key, spec in self.expanded.items():
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
        if self.stages is not None:
            stage_keys = set(self.stages)
            for key, spec in self.stages.items():
                for source, _path in spec.refs():
                    if source not in stage_keys:
                        errors.append(
                            f"stages.{key}: dangling stage reference "
                            f"${source!s}; declared: {sorted(stage_keys)}"
                        )
                    elif source == key:
                        errors.append(
                            f"stages.{key}: a stage cannot consume its own output"
                        )
        return errors

    @property
    def hash(self) -> str:
        """The experiment's identity (spec §2): canonical-JSON sha256 with
        notes stripped and env/outputs/schedule/tracking excluded."""
        return config_hash(self, exclude=DOC_NON_IDENTITY_SECTIONS)

    def to_obj(self):
        """Serialize everything this document STORES, nothing it derives.

        Returns
        -------
        dict
            ``name`` and ``pipeline`` always; the six always-emitted
            sections as objects or ``None``; ``walkforward`` and
            ``foreach`` ONLY when present; then ``notes``. The derived
            pair (``expanded``, ``foreach_groups``) NEVER appears — the
            identity hash reads this method alone, so what it cannot say
            can never be hash material (ADR-0039).
        """
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
        if self.foreach is not None:
            # Emitted only when present, for the reason ``walkforward``
            # is: an always-present null would move every existing
            # document's identity. The DERIVED pair (``expanded``,
            # ``foreach_groups``) is emitted NEVER — the hash reads
            # to_obj alone, so what to_obj cannot say cannot be identity.
            obj["foreach"] = self.foreach.to_obj()
        if self.stages is not None:
            obj["stages"] = {key: spec.to_obj() for key, spec in self.stages.items()}
        if self.execution_backtest is not None:
            obj["execution_backtest"] = self.execution_backtest.to_obj()
        obj["notes"] = self.notes
        return obj

    @classmethod
    def from_obj(cls, obj):
        """Rebuild one document from its serialized form.

        Parameters
        ----------
        obj : dict
            The document's sections; any key outside the known set is
            refused by name. ``pipeline`` may be absent or empty when a
            ``foreach`` section declares the templates that fan out.

        Returns
        -------
        PipelineDocument
            The validated document, its expansion already derived.

        Raises
        ------
        ConfigError
            Listing every problem at once: an unknown key, a node that is
            not an object, a section that refuses, and every shape,
            expansion and cross-field rule the constructor accumulates.
        """
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
                "foreach",
                "notes",
                "stages",
                "execution_backtest",
            ),
            "document",
        )
        if "execution_backtest" in obj:
            _raise_if(
                _execution_backtest_section_errors(
                    section
                    for section in _EXECUTION_BACKTEST_FORBIDDEN_SECTIONS
                    if section in obj
                )
            )
        errors = []
        errors.extend(_captured_position_errors(obj))
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
            if not isinstance(raw, dict):
                errors.append(f"{name}: must be an object, got {raw!r}")
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
        foreach = _section("foreach", ForeachSpec.from_obj)
        execution_backtest = None
        if "execution_backtest" in obj:
            raw_execution_backtest = obj["execution_backtest"]
            try:
                execution_backtest = ExecutionBacktestSpec.from_obj(
                    raw_execution_backtest
                )
            except ConfigError as exc:
                errors.extend(exc.errors)
        stages = None
        raw_stages = obj.get("stages")
        if raw_stages is not None:
            if not isinstance(raw_stages, dict):
                errors.append("stages: must be an object")
            else:
                stages = {}
                for key, stage_obj in raw_stages.items():
                    if not isinstance(stage_obj, dict):
                        errors.append(
                            f"stages.{key}: a stage must be an object, got {stage_obj!r}"
                        )
                        continue
                    try:
                        stages[key] = StageSpec.from_obj(stage_obj)
                    except ConfigError as exc:
                        errors.extend(f"stages.{key}: {e}" for e in exc.errors)
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
            foreach=foreach,
            stages=stages,
            execution_backtest=execution_backtest,
        )


# ---------------------------------------------------------------------------
# File I/O — the io.py discipline: every parse/shape error names the path
# ---------------------------------------------------------------------------


def _load_strict_json(path):
    """Read JSON once, refusing non-finite constants with its path."""

    def _refuse_nonfinite_json_constant(constant):
        raise ValueError(
            f"{path}: non-finite JSON constant {constant} is not valid JSON"
        )

    def _refuse_nonfinite_json_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError(
                f"{path}: non-finite JSON number {text} is not valid JSON"
            )
        return value

    def _refuse_duplicate_json_key(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(
                    f"{path}: duplicate JSON object key {key!r} is not valid JSON"
                )
            obj[key] = value
        return obj

    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(
                fh,
                parse_constant=_refuse_nonfinite_json_constant,
                parse_float=_refuse_nonfinite_json_float,
                object_pairs_hook=_refuse_duplicate_json_key,
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc


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
    doc = _load_strict_json(path)
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
