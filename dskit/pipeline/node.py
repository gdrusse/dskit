"""The Node ABC — every pipeline component, one base class (docs/24 §1 ✔1).

D-145 ruling 1: configs may reference **only Node subclasses** — a
registered kind name or an import path, both resolving here. Raw
functions are not referenceable; existing machinery (your application or
any library) survives INSIDE wrappers — wrap, not fork.

The lifecycle the driver runs for every node (ruling 4)::

    validate_params -> validate_inputs -> run -> validate_outputs -> log

``run`` is abstract — a Node that does not run is nothing. The two
validators are concrete with permissive defaults, per the spec as
written: the lifecycle guarantee is delivered by the DRIVER calling the
hooks uniformly, not by forcing every subclass to hand-write
``return []`` (see the docs/24 §1 sketch; the strict-by-default
alternative is parked in I-222). ``validate_params`` is a *classmethod*
so the planner can check a document's params before anything is
instantiated; ``validate_inputs`` sees materialized upstream outputs and
can only run at execute time.

``role`` is declared BY the class (ruling 2) and must be one of
:data:`~dskit.pipeline.document.ROLES` — rules attach to roles, and a
config cannot mislabel a node because the label was never its to give.
A class may declare its output names in ``outputs``; when declared, the
planner checks wires against them and the driver refuses a ``run``
return that does not match exactly.

Import cost: stdlib only.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from dskit.pipeline.base import ConfigError, import_ref, is_class_ref
from dskit.pipeline.document import ROLES

__all__ = [
    "DEFAULT_NODE_KINDS",
    "Node",
    "NodeContext",
    "NodeKindRegistry",
    "ResolvedUse",
    "check_int_param",
    "node_class_errors",
    "register_node_kind",
    "reject_unknown_params",
    "resolve_uses",
]

_NODE_KEY_OK = r"^[a-z_][a-z0-9_]*$"
_KIND_OK = r"^[a-z][a-z0-9_-]*$"


# ---------------------------------------------------------------------------
# The validate_params helper family — PUBLIC, and public on purpose.
#
# These serve one protocol: `Node.validate_params`, which ACCUMULATES into
# a problems list and returns it (never raises). `base.py` has same-named
# private helpers serving the opposite protocol — raise-immediately, for
# dataclass `from_obj` parsing — which is why these cannot live there and
# why the names differ.
#
# They are exported rather than underscore-private so a tier-3 child
# IMPORTS them instead of copying them. Two children copied the old
# private version verbatim; that is the duplication class CLAUDE.md's
# "Duplication that diverges" section exists to stop.
# ---------------------------------------------------------------------------


def reject_unknown_params(problems, params, allowed) -> None:
    """Append a problem for any param name outside the allowed set.

    The default-deny half of a node's ``validate_params``. The base
    ``Node.validate_params`` accepts anything, so each kind closes the
    hole for itself — a typo'd knob that is silently ignored is a config
    lie.

    Keys only. A VALUE that is a ``$``-reference string is legal wiring
    (``hpo-grid``'s ``objective`` is one by design) and is never
    inspected here.

    Parameters
    ----------
    problems : list of str
        The accumulator. Appended to in place; never raised from.
    params : dict
        The node's declared params, straight from the document.
    allowed : iterable of str
        This kind's own knob names — conventionally its ``_PARAMS``.

    Returns
    -------
    None
        Problems are appended to ``problems``.

    Examples
    --------
    The default-deny opening of a node's validator::

        @classmethod
        def validate_params(cls, params):
            problems = []
            reject_unknown_params(problems, params, cls._PARAMS)
            check_int_param(problems, "lookback", params.get("lookback"), ge=2)
            return problems
    """
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        problems.append(f"unknown param(s) {unknown} — allowed: {sorted(allowed)}")


def check_int_param(problems, name, value, *, ge) -> None:
    """Append a problem unless ``value`` is an int at or above ``ge``.

    ``bool`` is refused explicitly: it is an ``int`` in Python, so
    without the check ``True`` would pass as 1.

    Parameters
    ----------
    problems : list of str
        The accumulator. Appended to in place.
    name : str
        The knob's name, used in the message.
    value : object
        The declared value; anything non-int accumulates a problem.
    ge : int
        Inclusive lower bound. Required — a bound the caller did not
        state is a bound nobody can review.

    Returns
    -------
    None
        Problems are appended to ``problems``.

    Examples
    --------
    Refuse a width nobody stated, and one below the floor::

        problems = []
        check_int_param(problems, "lookback", None, ge=2)
        check_int_param(problems, "lookback", 1, ge=2)
        len(problems)   # 2
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < ge:
        problems.append(f"{name} must be an int >= {ge}, got {value!r}")


@dataclass(frozen=True)
class NodeContext:
    """What the driver hands every node's ``run`` — the run's frame.

    ``splits`` is the materialized split object (with ``split_of``) or
    ``None``; ``splits_info`` is its JSON view, the namespace behind
    ``"$splits.<field>"`` references. ``prev`` is the previous run's
    carry (``{node: {output: value}}``) — empty on the first run of a
    series. ``secrets`` is the redacting façade (never serializable);
    ``tracker`` accepts ``log_metrics(node, mapping)``. ``rerun`` is the
    driver-injected subgraph re-execution seam (docs/24 §8), present ONLY
    for ``search``-role nodes: a callable ``rerun(overrides) -> float``
    that re-executes the objective's dirty subgraph under the given
    ``"node.param.path" -> value`` overrides; ``None`` everywhere else.
    """

    name: str
    asof: str
    run_dir: str
    splits: object = None
    splits_info: dict = field(default_factory=dict)
    secrets: object = None
    tracker: object = None
    prev: dict = field(default_factory=dict)
    rerun: object = None


class Node(ABC):
    """One pipeline component: params in, named outputs out.

    Subclasses declare ``role`` (class attribute, one of
    :data:`~dskit.pipeline.document.ROLES`), implement ``run``, and MAY
    override the validators and declare ``outputs``. Construction
    validates — an invalid node can never exist: the key must be a legal
    node key, params must be a dict, and ``validate_params`` must return
    no problems (the driver already checked at plan time; this is the
    defense at the object boundary).

    Params are defensively DEEP-COPIED at construction (a node must never
    mutate the document), so params are for knobs and small state — bulk
    data flows through ``inputs``, which is handed to ``run`` uncopied.
    """

    #: The rule-bearing category, declared by the class — never the config.
    role: str = ""
    #: Declared output names, in contract order; ``None`` = undeclared
    #: (any dict of named outputs is accepted).
    outputs = None
    #: Split kinds this node may LAWFULLY be used under; ``None`` = no
    #: opinion (the default — most nodes have none). The causal doctrine
    #: lives here: a venue whose records are a time series declares
    #: ``("time", "trailing")``, and the planner then REFUSES a document
    #: that cuts them randomly. docs/24 always asserted causal venues
    #: refuse ``random``; before this attribute the node-map grammar had
    #: nowhere to say it, and the stage list was the only place it was
    #: enforced — so a randomized cut over settled events planned clean
    #: and put the test set's future inside the calibrator's past.
    supported_split_kinds = None

    def __init__(self, key, params=None, *, mode=None, artifact=""):
        if not isinstance(key, str) or not re.match(_NODE_KEY_OK, key):
            raise ConfigError([f"node key must match {_NODE_KEY_OK}, got {key!r}"])
        if params is None:
            params = {}
        if not isinstance(params, dict) or any(not isinstance(k, str) for k in params):
            raise ConfigError(
                [f"{key}: params must be a dict with string keys, got {params!r}"]
            )
        problems = type(self).validate_params(params)
        if problems:
            raise ConfigError([f"{key}: {p}" for p in problems])
        self.key = key
        self.params = copy.deepcopy(params)
        #: ``train``/``load``/None and the pinned artifact — the document's
        #: node-level fields (spec §3), meaningful to trainable roles only.
        self.mode = mode
        self.artifact = artifact
        #: Namespaced logger, wired by the driver to the run dir + sinks.
        self.log = logging.getLogger(f"dskit.pipeline.{key}")

    # -- the subclass contract (docs/24 §1) --------------------------------

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none. Classmethod so the
        planner can check a document before instantiation. Override to
        enforce this class's knobs; the base default accepts anything."""
        return []

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none.
        Runs at execute time — upstream outputs exist only then."""
        return []

    @abstractmethod
    def run(self, ctx, inputs):
        """Do the work; return the node's named outputs as a dict.

        ``ctx`` is the :class:`NodeContext`; ``inputs`` maps this node's
        declared ports to the referenced upstream values. The returned
        dict IS the node's output namespace — what ``$key.<output>``
        wiring resolves against downstream.
        """
        raise NotImplementedError

    # -- base-provided services (ruling 1: "base provides ...") ------------

    def validate_outputs(self, outputs):
        """Problems with a ``run`` return value, empty when none: a dict
        with string keys, matching the declared ``outputs`` contract
        exactly when the class declares one."""
        problems = []
        if not isinstance(outputs, dict) or any(
            not isinstance(k, str) or not k for k in outputs
        ):
            return [
                f"run must return a dict of named outputs, got {type(outputs).__name__}"
            ]
        declared = type(self).outputs
        if declared is not None:
            got, want = set(outputs), set(declared)
            if got != want:
                missing, extra = sorted(want - got), sorted(got - want)
                problems.append(
                    f"outputs do not match the declared contract {sorted(want)}: "
                    + "; ".join(
                        p
                        for p in (
                            f"missing {missing}" if missing else "",
                            f"undeclared {extra}" if extra else "",
                        )
                        if p
                    )
                )
        return problems

    def fingerprint(self):
        """The node's data-snapshot identity — JSON-small, hashed into the
        run identity. Meaningful on ``data``-role nodes (it must move
        whenever the data a run would consume changes); ``None`` = no
        contribution. The base default contributes nothing."""
        return None

    def data_edge(self):
        """The newest instant (epoch ms) this node's data reaches, or
        ``None`` when the node has no edge to report.

        This is the seam a ``trailing`` split materializes against: its
        windows are counted BACKWARD from the data's edge, and only a
        source knows where its data ends. Asked of ``data``-role nodes
        during RESOLVE, alongside :meth:`fingerprint` and for the same
        reason — a source's params are fully literal, so it is complete
        before anything runs. Generic by construction: "where does your
        data end" is a question any venue's source can answer.

        The base default declines, which is why a document with no
        edge-supplying source refuses to materialize a trailing split
        rather than guessing one.
        """
        return None

    def event_bounds(self):
        """``cluster -> EventBounds`` for this node's data, or ``None``.

        The sibling of :meth:`data_edge`, asked at RESOLVE for the same
        reason and of the same ``data``-role nodes. Where ``data_edge``
        answers "where does your data END", this answers "where does each
        EVENT in your data start and end" — the map a ``splits.policy`` of
        ``event-close`` needs in order to put every record of an event in
        ONE split instead of letting a long event straddle a cut.

        Asked only when the document's policy actually needs it, so a source
        never pays for a scan a ``record``-policy run would throw away.
        Unlike an edge, several sources answering is NOT ambiguous — event
        tickers are disjoint across venues, so the driver takes the union
        (:func:`~dskit.pipeline.split_policy.merge_event_bounds`) rather
        than refusing to choose.

        The base default declines, which is why a document declaring an
        event policy over sources that cannot answer refuses loudly rather
        than silently reverting to per-record assignment — a silent revert
        would be the leak, restored.
        """
        return None

    def artifact_dir(self, ctx):
        """This node's artifact directory under the run dir, created on
        first use — where trained models and report files land."""
        path = os.path.join(ctx.run_dir, "artifacts", self.key)
        os.makedirs(path, exist_ok=True)
        return path

    def write_artifact(self, ctx, filename, payload):
        """Write a JSON artifact into :meth:`artifact_dir`; returns the
        path. Serialized fully before the file opens; NaN refused (an
        artifact some readers cannot parse is not an artifact)."""
        text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
        return self.write_artifact_text(ctx, filename, text + "\n")

    def write_artifact_text(self, ctx, filename, text):
        """Write a TEXT artifact into :meth:`artifact_dir`; returns the
        path. The rendered sibling of :meth:`write_artifact`: a ``report``
        role writes the machine record as JSON and the human read as
        markdown, and both must land in the same place under the same
        naming."""
        if not isinstance(text, str):
            raise TypeError(
                f"write_artifact_text needs already-rendered text, "
                f"got {type(text).__name__} (use write_artifact for JSON)"
            )
        path = os.path.join(self.artifact_dir(ctx), filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


def node_class_errors(cls, where):
    """Why ``cls`` is not a usable Node subclass — empty when it is.

    Checked at kind registration and at import-path resolution, so a
    broken class is refused at the boundary it enters through, with the
    same words either way.
    """
    if not isinstance(cls, type):
        return [f"{where}: must be a class, got {cls!r}"]
    if not issubclass(cls, Node):
        return [
            f"{where}: {cls.__name__} is not a Node subclass — configs may "
            "reference only Node subclasses (D-145 ruling 1); wrap existing "
            "machinery inside one, never reference a raw function"
        ]
    problems = []
    if getattr(cls, "__abstractmethods__", None):
        problems.append(
            f"{where}: {cls.__name__} is abstract "
            f"(missing {sorted(cls.__abstractmethods__)})"
        )
    if cls.role not in ROLES:
        problems.append(
            f"{where}: {cls.__name__} must declare a class-level role from "
            f"{list(ROLES)}, got {cls.role!r} — the role is the class's to "
            "declare, never the config's"
        )
    declared = cls.outputs
    if declared is not None and (
        not isinstance(declared, tuple)
        or not declared
        or any(not isinstance(o, str) or not o for o in declared)
    ):
        problems.append(
            f"{where}: {cls.__name__}.outputs must be a non-empty tuple of "
            f"output names (or None), got {declared!r}"
        )
    return problems


@dataclass(frozen=True)
class ResolvedUse:
    """What a ``uses`` reference resolved to: the class, and whether it
    came from a toolkit-OWNED registry entry (the ``stat_test`` doctrine
    check keys off this — an import path can never be owned)."""

    cls: type
    owned: bool
    ref: str


class NodeKindRegistry:
    """``kind name -> Node subclass``, fail-loud both ways.

    ``owned=True`` marks a toolkit-owned kind: a name whose semantics are
    doctrine (``stat_test``, ``validate``) and may not be replaced by a
    custom class. Ownership is a property of the REGISTRATION, so an
    import-path reference can never claim it.
    """

    def __init__(self):
        self._kinds = {}

    def register(self, name, cls, *, owned=False) -> None:
        """Bind ``name`` to ``cls``. A duplicate name raises — two classes
        silently fighting over one kind is exactly the parallel path this
        package exists to prevent."""
        if not isinstance(name, str) or not re.match(_KIND_OK, name):
            raise ValueError(f"kind name must match {_KIND_OK}, got {name!r}")
        problems = node_class_errors(cls, f"kind {name!r}")
        if problems:
            raise ValueError("; ".join(problems))
        if name in self._kinds:
            raise ValueError(
                f"kind {name!r} is already registered — refusing to shadow the "
                "existing class (unregister deliberately, never implicitly)"
            )
        self._kinds[name] = (cls, bool(owned))

    def __contains__(self, name) -> bool:
        return name in self._kinds

    def kinds(self) -> tuple:
        """Registered names, sorted."""
        return tuple(sorted(self._kinds))

    def get(self, name):
        """The ``(cls, owned)`` pair for ``name``; raises naming every
        registered kind — and the registration rule — on a miss."""
        entry = self._kinds.get(name)
        if entry is None:
            raise ValueError(
                f"no node kind registered as {name!r} — registered: "
                f"{sorted(self._kinds)}. Registration happens when the owning "
                "package is imported (toolkit kinds at toolkit import, adapter "
                "kinds at adapter import)."
            )
        return entry


#: The registry toolkit and adapter kinds register into on import. Tests
#: build private instances to stay isolated.
DEFAULT_NODE_KINDS = NodeKindRegistry()


def register_node_kind(name, cls, *, owned=False) -> None:
    """Register into :data:`DEFAULT_NODE_KINDS` (the import-time path)."""
    DEFAULT_NODE_KINDS.register(name, cls, owned=owned)


def resolve_uses(uses, registry=None) -> ResolvedUse:
    """Turn one ``uses`` reference into a Node subclass (IMPORT, §9 step 2).

    A registered kind name is looked up in ``registry`` (default
    :data:`DEFAULT_NODE_KINDS`); a ``pkg.module:ClassName`` reference is
    imported and checked against the same contract. Raises ``ValueError``
    naming the reference on any failure — an unresolvable ``uses`` is a
    plan-time error, never a runtime surprise.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    if is_class_ref(uses):
        cls = import_ref(uses)  # raises ValueError naming the ref
        problems = node_class_errors(cls, uses)
        if problems:
            raise ValueError("; ".join(problems))
        return ResolvedUse(cls=cls, owned=False, ref=uses)
    cls, owned = registry.get(uses)
    return ResolvedUse(cls=cls, owned=owned, ref=uses)
