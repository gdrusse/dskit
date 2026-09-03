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

from dskit.pipeline.base import (
    ConfigError,
    abstract_class_problem,
    import_ref,
    is_class_ref,
)
from dskit.pipeline.document import MODES, ROLES

__all__ = [
    "DEFAULT_NODE_KINDS",
    "Node",
    "NodeContext",
    "NodeKindRegistry",
    "ResolvedUse",
    "TrainableNode",
    "check_int_param",
    "class_ref",
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
    without the check ``True`` would pass as 1. An integral float
    (``470.0``) is accepted: score metrics are stored as floats, and a
    ``$scan.metrics.lead`` wire is still a count.

    Parameters
    ----------
    problems : list of str
        The accumulator. Appended to in place.
    name : str
        The knob's name, used in the message.
    value : object
        The declared value; a non-integer accumulates a problem.
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
    if isinstance(value, bool):
        problems.append(f"{name} must be an int >= {ge}, got {value!r}")
        return
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < ge:
        problems.append(f"{name} must be an int >= {ge}, got {value!r}")


def class_ref(cls) -> str:
    """Spell a class the way a document and a sidecar reference one.

    ``module:QualName`` — the SAME grammar
    :func:`~dskit.pipeline.base.is_class_ref` recognizes in a ``uses``
    field. It lives here, beside :class:`Node`, because it is the
    ARTIFACT IDENTITY: every family that persists state records this
    string and load mode refuses a state whose recorded class is not
    the node's own. Three packs wrote the f-string out for themselves,
    and the failure that shape schedules is not a wrong answer but an
    orphaned artifact — the one thing this comparison exists to
    prevent.

    Parameters
    ----------
    cls : type
        The class to spell.

    Returns
    -------
    str
        ``"package.module:QualName"``.

    Examples
    --------
    What a sidecar records for a node class::

        class_ref(Node)     # 'dskit.pipeline.node:Node'
    """
    return f"{cls.__module__}:{cls.__qualname__}"


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
    ``fold_index`` is this run's 0-based ordinal within a walk-forward,
    ``None`` for a standalone run — a node that persists per-row evidence
    must be able to STAMP which fold produced a row, and the fold
    document carries only its cutoff (ADR-0064).
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
    fold_index: object = None


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

    # -- the pinned-artifact services (ADR-0038) ---------------------------
    #
    # They live HERE, not on TrainableNode, because a non-trainable caller
    # needs them: ``sb3-eval`` has role ``score`` — it may carry no
    # ``mode``/``artifact`` at all — yet it resolves the same artifact
    # reference from the same two places and wrote its own copies of both
    # checks. A service only trainables could reach would have left those
    # copies in place, which is the duplication this seam exists to end.

    def node_level_pin(self):
        """The artifact the DOCUMENT pinned on this node, or ``None``.

        The hook that keeps :meth:`pinned_artifact` free of a type test:
        the base answers "no node-level pin exists here", and
        :class:`TrainableNode` — the only place the field is meaningful —
        answers from ``mode``/``artifact``. An ``isinstance`` check inside
        :meth:`pinned_artifact` would be exactly the branch ADR-0038
        deletes, moved one class up.

        Returns
        -------
        str or None
            ``None`` when the document declared no node-level pin (always,
            for a plain Node). A subclass returns the declared pin, which
            may be the EMPTY string — "declared, but naming nothing" is a
            refusal, not an absence, and only a three-state answer can say
            so.
        """
        return None

    def pin_port_problems(self, inputs, port, *, hint):
        """Problems with a wired artifact-reference port, empty when none.

        An UNWIRED port is lawful — the reference may come from the
        document instead — so only a wired value is checked, and it must be
        a non-empty string. Written three times before ADR-0038 (two
        predict kinds and an eval), which is why it is a service rather
        than a snippet.

        Parameters
        ----------
        inputs : dict or None
            The materialized inputs handed to ``validate_inputs``.
        port : str
            The port name carrying the reference (``"artifact_path"``).
        hint : str
            Where a document should wire it from, quoted into the message.
            Required — a refusal that does not say what to do instead is
            half a refusal.

        Returns
        -------
        list of str
            One problem when the port is wired to something unusable;
            empty otherwise.
        """
        value = (inputs or {}).get(port)
        if value is not None and (not isinstance(value, str) or not value):
            return [f"{port} must be a non-empty string ({hint}), got {value!r}"]
        return []

    def pinned_artifact(self, declared=None, wired=None, *, missing):
        """The one artifact reference this node serves, or a refusal.

        The resolution order, stated once for every kind that loads a
        pinned artifact: the node-level pin (:meth:`node_level_pin`), then
        the declared param, then the wired port. A node-level pin that
        CONTRADICTS the declared param refuses — one pin, not two — while a
        restatement of the same path is lawful. A falsy ``declared`` or
        ``wired`` counts as absent, matching the ``x or y`` chains this
        replaces, so ``params: {"artifact": null}`` refuses by name instead
        of crashing downstream. A node-level pin that was DECLARED but
        empty refuses on the spot: that is the torch/sb3 rule, kept as the
        single one, and it is document-unreachable (the document already
        refuses ``mode="load"`` without an artifact).

        Parameters
        ----------
        declared : str or None
            The reference this node's own params declare, if any.
        wired : str or None
            The reference an input port supplied, if any.
        missing : str
            The refusal to raise when nothing pins an artifact — pack-local
            wording, because only the pack knows which param and which port
            a document should have used. Required.

        Returns
        -------
        str
            The resolved, non-empty artifact reference.

        Raises
        ------
        ValueError
            When the node-level pin is declared-but-empty, when it
            contradicts ``declared``, or when no source supplies one.
        """
        pin = self.node_level_pin()
        if pin is not None:
            if not pin:
                raise ValueError(
                    f"{self.key}: mode='load' was given an empty artifact "
                    "reference — a pin must name what it restores"
                )
            if declared and pin != declared:
                raise ValueError(
                    f"{self.key}: the node-level artifact {pin!r} and the "
                    f"declared pin {declared!r} disagree — one pin, not two: "
                    "one pinned artifact, one source of truth (mode='load' "
                    "may restate it, never replace it)"
                )
            return pin
        for candidate in (declared, wired):
            if candidate:
                return candidate
        raise ValueError(f"{self.key}: {missing}")


class TrainableNode(Node):
    """A node whose ``mode`` decides whether it FITS or RESTORES.

    The trainable roles — :data:`~dskit.pipeline.document.TRAINABLE_ROLES`,
    today ``train``/``signal``/``fitted_transform`` — are the only ones a
    document may give ``mode``/``artifact``, and every one of them used to
    hand-roll the same dispatch inside ``run``. Here it is a template
    method: ``run`` and ``validate_inputs`` are the base's, and a subclass
    supplies the per-mode hooks instead of branching (ADR-0038). Abstract
    by construction — both run hooks are ``@abstractmethod``, so an
    incomplete trainable refuses to CONSTRUCT rather than failing halfway
    through a fit — and never registered as a kind. A new family joins by
    adding its role to that tuple, never by widening this rule locally.

    A pinned-inference kind (one that always loads) sets
    ``default_mode = "load"`` and implements :meth:`run_train` as its
    refusal; a fit kind leaves the default and implements both.

    Attributes
    ----------
    default_mode : str
        Class-level, ``"train"`` by default. What an UNSET document
        ``mode`` means for this class. One of
        :data:`~dskit.pipeline.document.MODES` — the document grammar's
        vocabulary, not a second one — and :func:`node_class_errors`
        refuses a class declaring anything else, beside the same refusal
        for ``role``.

    Examples
    --------
    A fit kind and the pinned-inference kind that serves its artifact::

        class Fit(TrainableNode):
            role = "train"

            def run_train(self, ctx, inputs):
                return {"signal": fit(inputs["rows"])}

            def run_load(self, ctx, inputs):
                return {"signal": restore(self.artifact)}

        class Serve(Fit):
            role = "signal"
            default_mode = "load"

            def run_train(self, ctx, inputs):
                raise ValueError(f"{self.key}: this node never fits")

        node = Serve("serve", {}, mode="load", artifact="runs/x/model.pt")
        node.effective_mode   # 'load'
    """

    #: What an UNSET ``mode`` means for this class — ``"train"`` for a fit
    #: kind, ``"load"`` for a kind that only ever restores.
    default_mode = "train"

    @property
    def effective_mode(self):
        """The mode this node actually runs under: the document's ``mode``
        when it declared one, else :attr:`default_mode`. Read-only — the
        document decides, never the node."""
        return self.mode or type(self).default_mode

    # -- the template methods (do not override; override the hooks) --------

    def run(self, ctx, inputs):
        """Dispatch to :meth:`run_load` or :meth:`run_train` by
        :attr:`effective_mode`. See :meth:`Node.run` for the contract both
        hooks answer.

        The two branches ARE :data:`~dskit.pipeline.document.MODES`; a
        member this does not name would fall through to a refit, so the
        agreement is pinned by test
        (``test_every_mode_the_grammar_allows_reaches_its_OWN_hook``)
        rather than left to be noticed."""
        if self.effective_mode == "load":
            return self.run_load(ctx, inputs)
        return self.run_train(ctx, inputs)

    def validate_inputs(self, inputs):
        """Problems with ``inputs``: the common checks, then the ones for
        :attr:`effective_mode`. Additive — a mode's hook ADDS to the common
        list, so a check that holds either way is written once."""
        problems = list(self.validate_common_inputs(inputs))
        if self.effective_mode == "load":
            problems.extend(self.validate_load_inputs(inputs))
        else:
            problems.extend(self.validate_train_inputs(inputs))
        return problems

    # -- the hooks ---------------------------------------------------------

    @abstractmethod
    def run_train(self, ctx, inputs):
        """Fit, and return this node's named outputs. A kind that cannot
        fit implements this as its refusal, by name."""
        raise NotImplementedError

    @abstractmethod
    def run_load(self, ctx, inputs):
        """Restore the pinned artifact and return this node's named
        outputs. It must NEVER fit — that is the whole point of the pin."""
        raise NotImplementedError

    def validate_common_inputs(self, inputs):
        """Problems that hold in EITHER mode, empty when none. The base
        default accepts anything, like :meth:`Node.validate_inputs`."""
        return []

    def validate_train_inputs(self, inputs):
        """Problems with the inputs a FIT reads, empty when none."""
        return []

    def validate_load_inputs(self, inputs):
        """Problems with the inputs a RESTORE reads, empty when none —
        usually nothing: the artifact is the input."""
        return []

    def node_level_pin(self):
        """The node-level ``artifact``, or ``None`` when the document
        pinned none. The design's only raw ``mode`` read, and it is exact:
        the document refuses ``artifact`` without ``mode="load"`` and
        ``mode="load"`` without an ``artifact``, so a node-level pin exists
        IFF the document wrote ``mode="load"``.

        Returns
        -------
        str or None
            The declared pin under ``mode="load"`` (possibly empty, which
            :meth:`Node.pinned_artifact` refuses); ``None`` otherwise.
        """
        return self.artifact if self.mode == "load" else None


def node_class_errors(cls, where):
    """Why ``cls`` is not a usable Node subclass — empty when it is.

    Checked at kind registration and at import-path resolution, so a
    broken class is refused at the boundary it enters through, with the
    same words either way.

    The class-declared contract, in one place: it is a class, it is a
    Node, it is concrete, its ``role`` is in
    :data:`~dskit.pipeline.document.ROLES`, its ``default_mode`` (when it
    is a :class:`TrainableNode`) is in
    :data:`~dskit.pipeline.document.MODES`, and its ``outputs`` is a
    non-empty tuple of names or ``None``. None of the three is a param,
    so ``validate_params`` never sees them — this is their only doorway.

    Parameters
    ----------
    cls : object
        The candidate class. Not required to be a class at all — "not a
        class" is the first problem this reports.
    where : str
        What is being resolved (``"kind 'sklearn-fit'"``), prefixed onto
        every problem so a refusal names the reference that caused it.

    Returns
    -------
    list of str
        One problem per broken clause, in declaration order; empty when
        ``cls`` is usable.
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
    # Asked of core, not restated: every construction doorway words this
    # defect through ``base.abstract_class_problem``.
    abstract = abstract_class_problem(cls, where)
    if abstract:
        problems.append(abstract)
    if cls.role not in ROLES:
        problems.append(
            f"{where}: {cls.__name__} must declare a class-level role from "
            f"{list(ROLES)}, got {cls.role!r} — the role is the class's to "
            "declare, never the config's"
        )
    if issubclass(cls, TrainableNode) and cls.default_mode not in MODES:
        problems.append(
            f"{where}: {cls.__name__}.default_mode must be one of "
            f"{list(MODES)}, got {cls.default_mode!r} — it decides the run "
            "for every document that omits 'mode', and an unrecognized "
            "value would silently take the train path"
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
