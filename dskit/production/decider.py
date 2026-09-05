"""The decider — one derived document, one deferred read, one head digest (plan §5.3, D3, §9.1).

A served decision is a re-execution of the run's own subgraph, never a
restatement of it. :func:`serving_document` DERIVES the document a tick
runs from the run's ``config.json`` and records: trainables flipped to a
pinned ``load`` of their own artifact, search winners applied through the
driver's own override rule and the search nodes dropped, ``gate`` /
``stat_test`` verdicts replayed from ``carry.json`` where the serve
document asks, the graph cut to the heads and their ancestors, and every
section serving cannot use (``splits``, ``foreach``, ``env``, ``tracking``,
``outputs``) dropped. It takes no knob that could disagree with the run.

:class:`Decider` then plans that document STRUCTURALLY — classes and edges,
nothing constructed — and asks :class:`ServingExecutionPolicy` for every
node's closed ``serving_effect`` before any node exists. Exactly one
``entry_read`` (the declared entry, a source root every head descends
from), the rest ``pure`` or manifest-backed ``release_read``, or the
process refuses. The immutable base pass then runs once, and every tick is
two calls: :meth:`Decider.read_entry` constructs and runs ONLY the entry
under the one window override and snapshots its exact outputs;
:meth:`Decider.evaluate` seeds that frozen batch and re-runs the entry's
descendants under a policy that DEFERS the entry, so a second mutable read
cannot occur. :class:`Proposer` turns head outputs into stable, unsized
:class:`~dskit.production.records.Candidate` values and, given the
account and the tick's frozen :class:`~dskit.production.records.Provenance`,
into proposals — never re-running a node.

Import cost: stdlib, ``dskit.pipeline`` (document, driver, node, planner,
policy, records) and the production base, document, feed, records,
redact, release and vocab modules.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass, replace
from decimal import Decimal, InvalidOperation

import dskit.pipeline.driver as driver
from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import (
    MODES,
    TRAINABLE_ROLES,
    NodeSpec,
    PipelineDocument,
    load_document,
)

# Two private driver names, imported rather than re-spelled (CLAUDE.md: a
# function is never repeated across modules): the collapsed-record shape
# a replayed verdict must refuse, and the run's recorded-winner spelling.
# Review debt for §9.1: export both publicly from driver.py.
from dskit.pipeline.driver import SubgraphRunner, _is_summary, _winner_names
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    SERVING_EFFECTS,
    Node,
    NodeContext,
    NodeKindRegistry,
    check_int_param,
    class_ref,
    reject_unknown_params,
)
from dskit.pipeline.planner import plan
from dskit.pipeline.policy import ExecutionPolicy, classify_plan
from dskit.pipeline.records import number_ok, price_ok
from dskit.production.base import ProductionError, Registry, _check_str, canonical_hash
from dskit.production.document import ServeDocument
from dskit.production.feed import FeedSpec, snapshot_entry
from dskit.production.records import (
    AccountState,
    Candidate,
    EntryBatch,
    Proposal,
    Provenance,
    Quote,
)
from dskit.production.redact import get_logger
from dskit.production.release import (
    ReleaseManifest,
    ReleaseReader,
    parse_iso_duration,
    verify_release,
)
from dskit.production.vocab import SIDES, TIFS

__all__ = [
    "ARTIFACTS_DIRNAME",
    "CARRY_FILENAME",
    "CONFIG_FILENAME",
    "DEFAULT_MAX_ARTIFACT_AGE",
    "DEFAULT_TIF",
    "NODES_DIRNAME",
    "PROPOSER_KINDS",
    "RECORDED",
    "RECORDED_GATE_KIND",
    "RECORDED_STAT_TEST_KIND",
    "UNMAPPED_MONEY",
    "UNMAPPED_RATIO",
    "Decider",
    "IntentRows",
    "Proposer",
    "RecordedOutputs",
    "RecordedStatTest",
    "ServingExecutionPolicy",
    "TargetPositions",
    "artifact_entries",
    "artifact_prefix",
    "serving_document",
    "serving_registry",
]

#: ``serving.max_artifact_age`` when the document names none.
DEFAULT_MAX_ARTIFACT_AGE = "P30D"

#: The time-in-force a proposal carries when neither the head row nor the
#: proposer's ``default_tif`` names one: immediate-or-cancel, so a served
#: decision never leaves a resting order behind by omission.
DEFAULT_TIF = "ioc"

#: What an unmapped money field and an unmapped dimensionless field of a
#: proposal carry: the proposer states only what its head emitted, and
#: the leg re-prices from the tick's quotes.
UNMAPPED_MONEY = Decimal(0)
UNMAPPED_RATIO = 0.0

#: The one ``serving.replay`` value: replay the run's recorded outputs.
RECORDED = "recorded"

#: The toolkit-owned kinds the serving registry registers for replayed
#: verdicts (R13); ``stat_test`` is doctrine, so its replacement must
#: resolve to an OWNED registration, never a class reference.
RECORDED_GATE_KIND = "recorded-gate"
RECORDED_STAT_TEST_KIND = "recorded-stat-test"

#: The run-directory names the derivation reads (the driver's layout).
CONFIG_FILENAME = "config.json"
CARRY_FILENAME = "carry.json"
NODES_DIRNAME = "nodes"
ARTIFACTS_DIRNAME = "artifacts"

#: The one params key every seam site may carry beside its knobs.
_NOTES = ("notes",)

#: The role whose nodes serving never runs: their winner is applied, then
#: they leave the graph.
_SEARCH_ROLE = "search"

#: The serving effects this module reasons about, pinned to the closed
#: pipeline-side vocabulary.
_ENTRY_READ = "entry_read"
_RUNNABLE = frozenset(("pure", "release_read"))
if not ({_ENTRY_READ} | _RUNNABLE) <= set(SERVING_EFFECTS):
    raise ProductionError(["decider: serving effect spellings drifted from SERVING_EFFECTS"])

#: The abstaining side, and how a side reads as a direction / a sign.
_ABSTAIN = "none"
_DIRECTIONS = {"buy": "long", "sell": "short", _ABSTAIN: "flat"}
_SIGN_SIDES = {1: "buy", -1: "sell", 0: _ABSTAIN}
if set(_DIRECTIONS) != set(SIDES) or set(_SIGN_SIDES.values()) != set(SIDES):
    raise ProductionError(["decider: side tables drifted from SIDES"])

_ABSENT = object()

_log = get_logger("decider")


# ---------------------------------------------------------------------------
# The run directory
# ---------------------------------------------------------------------------


def _read_json(path):
    """Load a JSON file, or refuse naming it."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ProductionError([f"cannot read {path}: {exc}"]) from exc


def _node_record(run_dir, key):
    """Return ``nodes/NN-<key>.json`` as a dict, refusing zero or several matches."""
    nodes_dir = os.path.join(run_dir, NODES_DIRNAME)
    pattern = re.compile(r"^\d+-" + re.escape(key) + r"\.json\Z")
    try:
        names = sorted(name for name in os.listdir(nodes_dir) if pattern.match(name))
    except OSError as exc:
        raise ProductionError([f"cannot list {nodes_dir}: {exc}"]) from exc
    if len(names) != 1:
        raise ProductionError(
            [f"pipeline.{key}: expected one node record under {nodes_dir}, found {names}"]
        )
    record = _read_json(os.path.join(nodes_dir, names[0]))
    if not isinstance(record, dict):
        raise ProductionError([f"pipeline.{key}: node record {names[0]} is not a JSON object"])
    return record


def artifact_prefix(key):
    """Name the manifest prefix under which one node's artifacts are recorded.

    Parameters
    ----------
    key : str
        The node key.

    Returns
    -------
    str
        ``"artifacts/<key>/"`` — posix, relative to the run dir; a
        release manifest names every file beneath it, and the node's
        :class:`~dskit.production.release.ReleaseReader` is scoped to it.
    """
    return f"{ARTIFACTS_DIRNAME}/{key}/"


def artifact_entries(run_dir, key):
    """List one node's artifact files as the release manifest names them.

    Parameters
    ----------
    run_dir : str
        The run directory.
    key : str
        The node key.

    Returns
    -------
    dict
        ``{manifest name: absolute path}`` for every file under
        ``<run_dir>/artifacts/<key>/``, names as :func:`artifact_prefix`
        spells them — what ``plan`` digests into ``ReleaseManifest.artifacts``
        so a scoped reader can serve each by its bare name.

    Raises
    ------
    ProductionError
        When the node has no artifact directory.
    """
    base = os.path.join(run_dir, ARTIFACTS_DIRNAME, key)
    if not os.path.isdir(base):
        raise ProductionError([f"pipeline.{key}: no artifact directory at {base}"])
    entries = {}
    for parent, _dirs, files in os.walk(base):
        for name in sorted(files):
            path = os.path.join(parent, name)
            relative = os.path.relpath(path, base).replace(os.sep, "/")
            entries[artifact_prefix(key) + relative] = path
    return entries


# ---------------------------------------------------------------------------
# Replayed verdicts (§5.3 note)
# ---------------------------------------------------------------------------


class RecordedOutputs(Node):
    """Emit a run's recorded outputs verbatim — a replayed ``gate`` verdict.

    Takes the replaced node's place in the served document (§5.3): role
    ``gate`` so the planner's rules for the wire it fed still hold,
    ``outputs`` undeclared at CLASS level so any recorded port name is
    accepted, and the INSTANCE declaring exactly the recorded names.
    Serving effect ``pure``: it reads its own params and nothing else. A
    summarised (spent) value refuses at validation — a training-time
    verdict is replayed or refused, never recomputed on live data.

    Parameters
    ----------
    params : dict
        ``outputs`` (dict, REQUIRED) — output name -> the recorded JSON
        value, as ``carry.json`` holds it.

    Examples
    --------
    Replay an eligibility verdict::

        node = RecordedOutputs("family", {"outputs": {"instruments": ["INS1"], "verdict": "GO"}})
        node.run(ctx, {})  # {'instruments': ['INS1'], 'verdict': 'GO'}
    """

    role = "gate"
    outputs = None

    _PARAMS = ("outputs",)

    def __init__(self, key, params=None, **frame):
        super().__init__(key, params, **frame)
        self.outputs = tuple(self.params["outputs"])

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``: an unknown knob, or outputs that cannot be replayed.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, non-mapping ``outputs``, blank
            output name, summarised value or value JSON cannot hold.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        recorded = params.get("outputs")
        if not isinstance(recorded, dict):
            problems.append(f"outputs must be a map of output name -> recorded value, got {recorded!r}")
            return problems
        for name, value in recorded.items():
            _check_str(problems, "outputs: output name", name)
            if _is_summary(value):
                problems.append(
                    f"outputs.{name}: {value!r} is a summarised (spent) record — a verdict "
                    "is replayed from its recorded outputs or refused, never recomputed"
                )
                continue
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as exc:
                problems.append(f"outputs.{name}: not a JSON value: {exc}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — it emits its own params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def run(self, ctx, inputs):
        """Emit the recorded outputs, verbatim.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            Unused — a replayed verdict takes no wire.

        Returns
        -------
        dict
            A fresh copy of the recorded outputs.
        """
        return copy.deepcopy(self.params["outputs"])


class RecordedStatTest(RecordedOutputs):
    """The ``stat_test`` sibling of :class:`RecordedOutputs`.

    Role ``stat_test`` is toolkit-owned doctrine, so this class reaches a
    served document only through the OWNED registration
    :data:`RECORDED_STAT_TEST_KIND` that :func:`serving_registry` makes.

    Examples
    --------
    ::

        node = RecordedStatTest("deploy", {"outputs": {"verdict": "GO", "survivors": ["INS1"]}})
        node.run(ctx, {})["verdict"]  # 'GO'
    """

    role = "stat_test"


#: Role -> the ``uses`` its replayed replacement is written as. A gate is
#: referenced by class; a stat_test must name the owned kind.
_RECORDED_USES = {"gate": class_ref(RecordedOutputs), "stat_test": RECORDED_STAT_TEST_KIND}

#: The owned kinds the serving registry adds (R13).
_RECORDED_NODES = {RECORDED_GATE_KIND: RecordedOutputs, RECORDED_STAT_TEST_KIND: RecordedStatTest}


def serving_registry(base=None):
    """Copy a node registry and add the owned replayed-verdict kinds (R13).

    Parameters
    ----------
    base : NodeKindRegistry, optional
        The registry to copy; ``None`` means the toolkit's
        ``DEFAULT_NODE_KINDS`` (the adapter's kinds are there once the
        adapter is imported).

    Returns
    -------
    NodeKindRegistry
        A fresh registry holding every kind of ``base`` with its
        ownership, plus :data:`RECORDED_GATE_KIND` and
        :data:`RECORDED_STAT_TEST_KIND` registered ``owned=True`` —
        ``role 'stat_test' is toolkit-owned`` in the planner, and
        production is the toolkit.
    """
    source = DEFAULT_NODE_KINDS if base is None else base
    registry = NodeKindRegistry()
    for name in source.kinds():
        cls, owned = source.get(name)
        registry.register(name, cls, owned=owned)
    for name, cls in _RECORDED_NODES.items():
        if name not in registry:
            registry.register(name, cls, owned=True)
    return registry


# ---------------------------------------------------------------------------
# serving_document — the derivation
# ---------------------------------------------------------------------------


class _Carry:
    """``carry.json``, read once on first use — the run's JSON-small outputs (ADR-0048)."""

    def __init__(self, run_dir):
        self._path = os.path.join(run_dir, CARRY_FILENAME)
        self._data = None

    def outputs(self, key):
        """Return the recorded outputs carried for ``key``, or None when the run carried none."""
        if self._data is None:
            data = _read_json(self._path)
            if not isinstance(data, dict):
                raise ProductionError([f"{self._path} is not a JSON object"])
            self._data = data
        return self._data.get(key)


def _recorded(key, role, carry):
    """Replace one ``gate``/``stat_test`` node by a node emitting its recorded outputs."""
    recorded = carry.outputs(key)
    if not isinstance(recorded, dict) or not recorded:
        raise ProductionError(
            [
                f"pipeline.{key}: {CARRY_FILENAME} carries no replayable outputs for this "
                f"{role} node — a spent (summarised) or absent record cannot be replayed, "
                "and a training-time verdict is never recomputed on live data"
            ]
        )
    return NodeSpec(
        uses=_RECORDED_USES[role],
        params={"outputs": copy.deepcopy(recorded)},
        notes=f"{role} verdict replayed from the run's recorded outputs",
    )


#: ``serving.replay`` value -> how a node of a replayed role is replaced.
_REPLAYERS = {RECORDED: _recorded}


def _pinned_load(key, spec, run_dir, problems):
    """Serve a TRAINED trainable as a load pinned to its own artifact directory."""
    artifact = os.path.join(run_dir, ARTIFACTS_DIRNAME, key)
    if not os.path.isdir(artifact):
        problems.append(
            f"pipeline.{key}: no artifact directory at {artifact} — a trained node "
            "cannot be served without what it trained"
        )
    return replace(spec, mode="load", artifact=artifact)


def _kept(key, spec, run_dir, problems):
    """Keep a trainable that already loads exactly as it was declared."""
    return spec


#: Effective mode -> how a trainable is served; pinned to the grammar's own vocabulary.
_SERVED_MODES = {"train": _pinned_load, "load": _kept}
if set(_SERVED_MODES) != set(MODES):
    raise ProductionError([f"decider: served modes {sorted(_SERVED_MODES)} do not cover MODES {list(MODES)}"])


def _checked_heads(heads):
    """Return ``heads`` as a tuple of unique, non-empty node keys, or refuse."""
    problems = []
    if isinstance(heads, (str, bytes)) or not isinstance(heads, (list, tuple)):
        raise ProductionError([f"heads must be a list of node keys, got {heads!r}"])
    if not heads:
        problems.append("heads must name at least one node — a served document with no head decides nothing")
    for index, head in enumerate(heads):
        _check_str(problems, f"heads[{index}]", head)
    if len(set(heads)) != len(heads):
        problems.append(f"heads carries duplicates: {list(heads)!r}")
    if problems:
        raise ProductionError(problems)
    return tuple(heads)


def _checked_replay(replay):
    """Return ``serving.replay`` as ``{role: replacer}``, refusing an unknown role or value."""
    replay = {} if replay is None else replay
    if not isinstance(replay, dict) and not hasattr(replay, "items"):
        raise ProductionError([f"replay must be a map of role -> {RECORDED!r}, got {replay!r}"])
    problems, replacers = [], {}
    for role, how in replay.items():
        if role not in _RECORDED_USES:
            problems.append(f"replay.{role}: only roles {sorted(_RECORDED_USES)} can be replayed")
            continue
        replacer = _REPLAYERS.get(how) if isinstance(how, str) else None
        if replacer is None:
            problems.append(f"replay.{role}: {how!r} is not one of {sorted(_REPLAYERS)}")
            continue
        replacers[role] = replacer
    if problems:
        raise ProductionError(problems)
    return replacers


def _planned(document, registry=None):
    """Plan a document, refusing as a ProductionError."""
    try:
        return plan(document, registry)
    except (ConfigError, ValueError) as exc:
        raise ProductionError([f"the document does not plan: {exc}"]) from exc


def _apply_winners(the_plan, run_dir, params):
    """Apply every search node's recorded winner into ``params`` through the driver's own rule."""
    _produced, recorded = _winner_names()
    for key in the_plan.order:
        if the_plan.role_of(key) != _SEARCH_ROLE:
            continue
        record = _node_record(run_dir, key)
        if recorded not in record:
            raise ProductionError(
                [
                    f"pipeline.{key}: the run recorded no winner (its node record carries no "
                    f"{recorded!r}) — a search node is served only through its recorded winner"
                ]
            )
        winner = record[recorded]
        if not isinstance(winner, dict):
            raise ProductionError(
                [f"pipeline.{key}: the recorded winner must map 'node.param.path' -> value, got {winner!r}"]
            )
        for target, value in winner.items():
            node, _sep, path = target.partition(".") if isinstance(target, str) else ("", "", "")
            if node not in params or not path:
                raise ProductionError(
                    [f"pipeline.{key}: winner target {target!r} must be '<node>.<param.path>' on a node of the run"]
                )
            try:
                driver.apply_param_override(params[node], node, tuple(path.split(".")), value)
            except ValueError as exc:
                raise ProductionError([f"pipeline.{key}: winner {target!r}: {exc}"]) from exc


def _reachable(specs, heads):
    """Collect the heads and every node they transitively reference."""
    needed, stack = set(), list(heads)
    while stack:
        key = stack.pop()
        if key in needed:
            continue
        needed.add(key)
        node_refs, _prev = specs[key].refs()
        stack.extend(source for source, _path in node_refs if source in specs)
    return needed


def serving_document(run_document, run_dir, heads, replay):
    """Derive the document a served tick runs from the run's own document and records.

    A pure derivation over ``run_document.expanded`` (instances, never
    templates): search winners applied and search nodes dropped, replayed
    ``gate``/``stat_test`` nodes replaced by their recorded outputs, the
    graph cut to ``ancestors(heads) ∪ heads``, every needed trainable
    flipped to ``mode: "load"`` pinned at ``<run_dir>/artifacts/<key>``,
    and ``splits``/``foreach``/``env``/``tracking``/``outputs`` dropped.
    Nothing here restates a node value.

    Parameters
    ----------
    run_document : PipelineDocument
        The run's document (``config.json``), planned against the
        toolkit registry — import the adapter first.
    run_dir : str
        The run directory: ``nodes/`` (winners), ``carry.json``
        (replayed outputs) and ``artifacts/`` (pins) are read from it.
    heads : list of str
        The node keys whose outputs form the proposals.
    replay : dict
        ``serving.replay`` — role -> ``"recorded"``; ``None`` or ``{}``
        replays nothing.

    Returns
    -------
    PipelineDocument
        The served document; its ``hash`` is the serving hash.

    Raises
    ------
    ProductionError
        No heads or an unknown head; a run document that does not plan;
        a search node without a recorded winner or a winner addressing a
        param that does not exist; a replayed node with absent or
        summarised outputs; an unknown replay role or value; a needed
        trainable without an artifact directory; a needed ``$prev``
        reference or a needed search node.
    """
    if not isinstance(run_document, PipelineDocument):
        raise ProductionError([f"run_document must be a PipelineDocument, got {run_document!r}"])
    if not isinstance(run_dir, str) or not run_dir:
        raise ProductionError([f"run_dir must be a non-empty path, got {run_dir!r}"])
    heads = _checked_heads(heads)
    replacers = _checked_replay(replay)
    the_plan = _planned(run_document)
    specs = run_document.expanded
    unknown = [head for head in heads if head not in specs]
    if unknown:
        raise ProductionError([f"heads {unknown} are not nodes of the run document ({sorted(specs)})"])
    params = {key: copy.deepcopy(spec.params) for key, spec in specs.items()}
    _apply_winners(the_plan, run_dir, params)
    carry = _Carry(run_dir)
    served = {}
    for key, spec in specs.items():
        role = the_plan.role_of(key)
        replacer = replacers.get(role)
        served[key] = (
            replace(spec, params=params[key]) if replacer is None else replacer(key, role, carry)
        )
    needed = _reachable(served, heads)
    problems, cut = [], {}
    for key in specs:
        if key not in needed:
            continue
        spec, role, cls = served[key], the_plan.role_of(key), the_plan.resolved[key].cls
        if role in TRAINABLE_ROLES:
            spec = _SERVED_MODES[spec.mode or cls.default_mode](key, spec, run_dir, problems)
        _node_refs, prev_refs = spec.refs()
        if prev_refs:
            problems.append(
                f"pipeline.{key}: carries a $prev reference — a served tick has no previous run to bind"
            )
        if role == _SEARCH_ROLE:
            problems.append(
                f"pipeline.{key}: a search node is in the served subgraph — serving applies a "
                "recorded winner, it never searches"
            )
        cut[key] = spec
    if problems:
        raise ProductionError(problems)
    try:
        return PipelineDocument(name=run_document.name, pipeline=cut, notes=run_document.notes)
    except ConfigError as exc:
        raise ProductionError([f"the served document is invalid: {e}" for e in exc.errors]) from exc


# ---------------------------------------------------------------------------
# ServingExecutionPolicy
# ---------------------------------------------------------------------------


class ServingExecutionPolicy(ExecutionPolicy):
    """The serving policy (§5.3, §9.1): each class's own closed effect, one deferred entry.

    ``classify`` returns exactly ``cls.serving_effect(params, evidence)``
    and remembers it; ``defer`` is true for the entry alone; ``reader``
    hands a ``release_read`` node a
    :class:`~dskit.production.release.ReleaseReader` scoped to its own
    ``artifacts/<key>/`` manifest entries and every other node nothing.
    Entry dominance and the "sole ``entry_read``" rule are
    :meth:`Decider.prepare`'s.

    Parameters
    ----------
    entry : str
        The declared entry node key.
    release : ReleaseManifest
        The release whose artifacts a ``release_read`` node may read.
    root : str
        The directory the manifest's artifact names are relative to
        (the base run dir).

    Examples
    --------
    ::

        policy = ServingExecutionPolicy("bars", release=release, root=run_dir)
        policy.classify("bars", ObservationRows, params, {})  # 'entry_read'
        policy.defer("bars")                                  # True
        policy.reader("bars") is None                          # True
    """

    def __init__(self, entry, *, release, root):
        problems = []
        _check_str(problems, "entry", entry)
        if not isinstance(release, ReleaseManifest):
            problems.append(f"release must be a ReleaseManifest, got {release!r}")
        if not isinstance(root, (str, os.PathLike)) or not str(root):
            problems.append(f"root must be a path, got {root!r}")
        if problems:
            raise ProductionError(problems)
        self._entry = entry
        self._release = release
        self._root = root
        self._effects = {}

    def classify(self, key, cls, params, evidence):
        """Answer the class's own closed serving effect, and remember it for :meth:`reader`.

        Parameters
        ----------
        key : str
            The node key.
        cls : type
            The resolved node class — asked, never instantiated.
        params : dict
            The node's declared params.
        evidence : dict
            The release's evidence for the node.

        Returns
        -------
        str
            ``cls.serving_effect(params, evidence)``.
        """
        effect = cls.serving_effect(params, evidence)
        self._effects[key] = effect
        return effect

    def defer(self, key):
        """Say whether ``key`` is the entry — executed by ``read_entry``, only seeded here.

        Parameters
        ----------
        key : str
            The node key.

        Returns
        -------
        bool
            True for the entry alone.
        """
        return key == self._entry

    def reader(self, key):
        """Hand a ``release_read`` node its scoped reader; every other node gets none.

        Parameters
        ----------
        key : str
            The node key.

        Returns
        -------
        ReleaseReader or None
            A reader over the manifest's ``artifacts/<key>/`` entries
            when :meth:`classify` answered ``release_read`` for ``key``.
        """
        make = _READERS.get(self._effects.get(key))
        return None if make is None else make(self, key)

    def _release_reader(self, key):
        """Make a reader scoped to this node's own manifest prefix."""
        prefix = artifact_prefix(key)
        allowed = [name for name in self._release.artifacts if name.startswith(prefix)]
        return ReleaseReader(self._release, allowed, self._root, prefix=prefix)


#: Serving effect -> how a node's reader is made; effects absent here get none.
_READERS = {"release_read": ServingExecutionPolicy._release_reader}


# ---------------------------------------------------------------------------
# Decider
# ---------------------------------------------------------------------------


class Decider:
    """Plan the served subgraph once, then read the entry and evaluate it per tick (§5.3).

    Parameters
    ----------
    document : ServeDocument
        Its ``serving`` section names the entry node/param/window, the
        heads and the replay map.
    release : ReleaseManifest
        The release being served — re-verified at :meth:`prepare`.
    registry : NodeKindRegistry or None
        The node registry the served document is planned against
        (copied by :func:`serving_registry`); ``None`` means the
        toolkit's.
    adapter : str or None
        The adapter package to import before planning, so the run's
        kinds resolve; ``None`` imports nothing.
    proposer : Proposer
        The tick's proposer, kept as :attr:`proposer` for the loop.
    clock : Clock
        Answers ``now_ms()`` for release verification.

    Attributes
    ----------
    serving_hash : str or None
        The derived document's hash after :meth:`prepare`.
    proposer : Proposer
        As given.

    Examples
    --------
    Prepare once, then one tick::

        decider = Decider(document, release, registry=None, adapter="yourproject",
                          proposer=IntentRows({"output": "picks", "fields": {...}}), clock=clock)
        effects = decider.prepare("2026-01-06", "pipeline_runs/train-2026-01-01-abcd1234")
        # -> {'bars': 'entry_read', 'weights': 'pure', ..., 'picks': 'pure'}
        batch = decider.read_entry(tick_at_ms)
        head_outputs, head_digest = decider.evaluate(batch)
    """

    def __init__(self, document, release, *, registry, adapter, proposer, clock):
        problems = []
        if not isinstance(document, ServeDocument):
            problems.append(f"document must be a ServeDocument, got {document!r}")
        if not isinstance(release, ReleaseManifest):
            problems.append(f"release must be a ReleaseManifest, got {release!r}")
        if registry is not None and not isinstance(registry, NodeKindRegistry):
            problems.append(f"registry must be a NodeKindRegistry or None, got {registry!r}")
        if adapter is not None:
            _check_str(problems, "adapter", adapter)
        if not isinstance(proposer, Proposer):
            problems.append(f"proposer must be a Proposer, got {proposer!r}")
        if not callable(getattr(clock, "now_ms", None)):
            problems.append(f"clock must answer now_ms(), got {clock!r}")
        if problems:
            raise ProductionError(problems)
        self._document = document
        self._release = release
        self._registry = registry
        self._adapter = adapter
        self.proposer = proposer
        self._clock = clock
        entry = document.serving.entry
        self._entry, self._param, self._window_ms = entry.node, entry.param, entry.window_ms
        self._heads = tuple(document.serving.heads)
        self._target = f"{self._entry}.{self._param}"
        self.serving_hash = None
        self._plan = self._policy = self._contract = self._spec = self._ctx = None
        self._base_outputs = {}

    @property
    def entry(self):
        """The declared entry node key (str)."""
        return self._entry

    @property
    def contract(self):
        """The entry's ``ServingContract`` after :meth:`prepare`, else None."""
        return self._contract

    @property
    def feed_spec(self):
        """The release's ``FeedSpec`` after :meth:`prepare`, else None."""
        return self._spec

    # -- prepare -----------------------------------------------------------

    def prepare(self, asof, base_run_dir):
        """Verify the release, derive and classify the served document, run the base pass.

        Nothing is constructed before classification: the release is
        re-verified, the run document loaded and derived, the served
        document planned structurally, every node classified through
        :class:`ServingExecutionPolicy`, and the structure judged —
        exactly one ``entry_read`` and it is the declared entry, a
        source root every head descends from, with a window param that
        already exists; every other node ``pure`` or manifest-backed
        ``release_read``; a ``ServingContract`` that is what the release
        bound. Every problem is accumulated and raised once. Only then
        does the base pass construct and run the needed nodes that are
        neither the entry nor its descendants, once for the process.

        Parameters
        ----------
        asof : str
            The ``YYYY-MM-DD`` handed to every node's context.
        base_run_dir : str
            The run directory the release's artifact names are relative
            to — ``config.json``, ``artifacts/``, ``nodes/`` and
            ``carry.json`` are read from it.

        Returns
        -------
        dict
            Node key -> serving effect, in plan order (the
            ``classify_plan`` answer).

        Raises
        ------
        ProductionError
            Every refusal §5.3 lists, accumulated.
        """
        problems = []
        _check_str(problems, "asof", asof)
        _check_str(problems, "base_run_dir", base_run_dir)
        if problems:
            raise ProductionError(problems)
        problems.extend(self._release_problems(base_run_dir))
        self._import_adapter(problems)
        served = self._served(base_run_dir, problems)
        the_plan = self._planned(served, problems)
        policy = ServingExecutionPolicy(self._entry, release=self._release, root=base_run_dir)
        evidence = {key: self._evidence(key, the_plan) for key in the_plan.order}
        try:
            effects = classify_plan(the_plan, policy, evidence)
        except ConfigError as exc:
            raise ProductionError(problems + list(exc.errors)) from exc
        problems.extend(self._structure_problems(the_plan, effects))
        contract, spec = self._binding(the_plan, evidence, problems)
        if problems:
            raise ProductionError(problems)
        ctx = NodeContext(name=served.name, asof=asof, run_dir=base_run_dir)
        self._base_outputs = self._base_pass(the_plan, policy, ctx)
        self._plan, self._policy, self._ctx = the_plan, policy, ctx
        self._contract, self._spec = contract, spec
        self.serving_hash = served.hash
        _log.info(
            "prepared serving document %s: %d node(s), base pass ran %s",
            served.hash[:12], len(the_plan.order), sorted(self._base_outputs),
        )
        return dict(effects)

    def _release_problems(self, base_run_dir):
        """Re-verify the release against the run dir; return its problems rather than raise."""
        age = self._document.serving.max_artifact_age or DEFAULT_MAX_ARTIFACT_AGE
        try:
            verify_release(self._release, base_run_dir, self._clock.now_ms(), parse_iso_duration(age))
        except ProductionError as exc:
            return list(exc.problems)
        return []

    def _import_adapter(self, problems):
        """Import the adapter so the run's kinds resolve; a failure is raised with what came before."""
        if self._adapter is None:
            return
        try:
            importlib.import_module(self._adapter)
        except ImportError as exc:
            raise ProductionError(problems + [f"cannot import adapter {self._adapter!r}: {exc}"]) from exc

    def _served(self, base_run_dir, problems):
        """Load ``config.json`` and derive the served document, raising with what came before."""
        path = os.path.join(base_run_dir, CONFIG_FILENAME)
        try:
            run_document = load_document(path)
        except (OSError, ValueError) as exc:
            raise ProductionError(problems + [f"cannot load the run document {path}: {exc}"]) from exc
        replay = self._document.serving.replay
        try:
            return serving_document(
                run_document, base_run_dir, list(self._heads), None if replay is None else dict(replay)
            )
        except ProductionError as exc:
            raise ProductionError(problems + exc.problems) from exc

    def _planned(self, served, problems):
        """Plan the served document against the serving registry, raising with what came before."""
        try:
            return _planned(served, serving_registry(self._registry))
        except ProductionError as exc:
            raise ProductionError(problems + exc.problems) from exc

    def _evidence(self, key, the_plan):
        """Collect what the release verified about one node: its mode, pin and whether the pin is bound."""
        spec = the_plan.document.expanded[key]
        prefix = artifact_prefix(key)
        return {
            "mode": spec.mode,
            "artifact": spec.artifact,
            "artifact_pinned": any(name.startswith(prefix) for name in self._release.artifacts),
            "role": the_plan.role_of(key),
        }

    def _structure_problems(self, the_plan, effects):
        """Judge the classified plan against §5.3's structural rules; every problem listed."""
        problems, specs, entry = [], the_plan.document.expanded, self._entry
        for key, effect in effects.items():
            if key != entry and effect == _ENTRY_READ:
                problems.append(
                    f"pipeline.{key}: a second mutable read (entry_read) beside the entry "
                    f"{entry!r} — a served tick has exactly one"
                )
            if key != entry and effect not in _RUNNABLE:
                problems.append(
                    f"pipeline.{key}: serving effect {effect!r} — only 'pure' and 'release_read' "
                    "nodes may run beside the entry"
                )
        if entry not in specs:
            problems.append(
                f"serving.entry.node {entry!r} is not a node of the served document "
                f"(nodes: {list(the_plan.order)})"
            )
        else:
            if effects[entry] != _ENTRY_READ:
                problems.append(
                    f"serving.entry.node {entry!r} is classified {effects[entry]!r}, not "
                    "'entry_read' — the entry must be the tick's one mutable read"
                )
            spec = specs[entry]
            node_refs, prev_refs = spec.refs()
            if spec.inputs or node_refs or prev_refs:
                problems.append(
                    f"pipeline.{entry}: the entry must be a source root — no inputs, no $ references"
                )
            problems.extend(self._window_problems(spec, the_plan.resolved[entry].cls))
        for head in self._heads:
            if head != entry and (entry not in specs or entry not in the_plan.ancestors(head)):
                problems.append(
                    f"serving.heads: {head!r} does not descend from the entry {entry!r} — "
                    "every head must be dominated by the mutable read"
                )
        return problems

    def _window_problems(self, spec, cls):
        """Check the window param is a knob of the entry class AND already exists in the run document."""
        problems, param = [], self._param
        declared = getattr(cls, "_PARAMS", None)
        if declared is not None and param.split(".")[0] not in declared:
            problems.append(
                f"serving.entry.param {param!r} is not a knob of {cls.__name__} (allowed: {list(declared)})"
            )
        cursor = spec.params
        for segment in param.split("."):
            if not isinstance(cursor, dict) or segment not in cursor:
                problems.append(
                    f"serving.entry.param {param!r}: the path is absent from the run document's "
                    f"{self._entry!r} params (declared: {sorted(spec.params)}) — an override may "
                    f"only address an existing param; declare \"{param}\": null in the training document"
                )
                break
            cursor = cursor[segment]
        return problems

    def _binding(self, the_plan, evidence, problems):
        """Return the entry's contract and the release's feed spec, which must bind that same contract."""
        try:
            spec = FeedSpec.from_obj(self._release.feed_spec)
        except ProductionError as exc:
            problems.extend(exc.problems)
            spec = None
        if self._entry not in the_plan.order:
            return None, spec
        cls = the_plan.resolved[self._entry].cls
        params = the_plan.document.expanded[self._entry].params
        try:
            contract = cls.serving_contract(params, evidence[self._entry])
        except ValueError as exc:
            problems.append(f"pipeline.{self._entry}: {exc}")
            return None, spec
        if contract is None:
            problems.append(
                f"pipeline.{self._entry}: {cls.__name__} offers no ServingContract — it cannot serve as the entry"
            )
            return None, spec
        if spec is not None:
            try:
                bound = FeedSpec.from_contract(
                    contract, spec.required_keys, spec.source_config_hash, spec.source_config_version
                )
            except ProductionError as exc:
                problems.extend(exc.problems)
                return contract, spec
            if bound != spec:
                problems.append(
                    "the release's feed_spec is not this entry's contract bound to its universe — "
                    "source binding, entity projection, event time or digest recipe differ"
                )
        return contract, spec

    def _base_pass(self, the_plan, policy, ctx):
        """Run, once, every needed node that is neither the entry nor a descendant of it."""
        descendants = the_plan.descendants(self._entry)
        keys = [key for key in the_plan.order if key != self._entry and key not in descendants]
        runner = SubgraphRunner(the_plan, set(the_plan.order), {}, {}, {}, policy)
        outputs = {}
        try:
            runner.run_keys(keys, outputs, ctx, {})
        except (ConfigError, ValueError) as exc:
            raise ProductionError([f"base pass: {exc}"]) from exc
        return outputs

    # -- the tick ------------------------------------------------------------

    def _require_prepared(self):
        """Refuse a tick verb before :meth:`prepare`."""
        if self._plan is None:
            raise ProductionError(["the decider is not prepared — call prepare(asof, base_run_dir) first"])

    def read_entry(self, tick_at_ms):
        """Construct and run ONLY the entry under the tick's window; snapshot its exact outputs.

        The one override ``"<entry>.<param>" -> tick_at_ms - window_ms``
        goes through the driver's existing-param-only rule inside a
        runner whose ``needed`` is the entry alone, so nothing else can
        run; the entry's outputs are then described by
        :func:`~dskit.production.feed.snapshot_entry`.

        Parameters
        ----------
        tick_at_ms : int
            The tick's instant, epoch ms.

        Returns
        -------
        EntryBatch
            The frozen batch :meth:`evaluate` takes.

        Raises
        ------
        ProductionError
            Before :meth:`prepare`; a non-int tick; an entry that refuses
            to construct or run; a snapshot that fails coverage.
        """
        self._require_prepared()
        problems = []
        check_int_param(problems, "tick_at_ms", tick_at_ms, ge=0)
        if problems:
            raise ProductionError(problems)
        runner = SubgraphRunner(self._plan, {self._entry}, self._base_outputs, {}, {})
        outputs = {}
        try:
            runner.rerun({self._target: tick_at_ms - self._window_ms}, outputs, self._ctx, {})
        except (ConfigError, ValueError) as exc:
            raise ProductionError([f"entry read: {exc}"]) from exc
        return snapshot_entry(
            self._contract, self._spec, outputs[self._entry], self._release.source_config["hash"]
        )

    def evaluate(self, batch):
        """Re-run the entry's descendants from a frozen batch; return the heads and their digest.

        The batch's outputs are seeded as the entry's and the serving
        runner re-runs the dirty subgraph under the policy, which DEFERS
        the entry — the window override is passed only so the entry's
        descendants are dirty; its value is never applied. Nodes outside
        the entry's descent come from the base pass.

        Parameters
        ----------
        batch : EntryBatch
            What :meth:`read_entry` returned, after the loop validated
            its identity, coverage and freshness.

        Returns
        -------
        tuple
            ``(head_outputs, head_digest)`` — ``{head: outputs}`` and
            ``canonical_hash`` of it.

        Raises
        ------
        ProductionError
            Before :meth:`prepare`; a non-batch; a descendant that
            refuses to construct or run.
        """
        self._require_prepared()
        if not isinstance(batch, EntryBatch):
            raise ProductionError([f"evaluate expects an EntryBatch, got {batch!r}"])
        outputs = {self._entry: copy.deepcopy(batch.outputs)}
        runner = SubgraphRunner(self._plan, set(self._plan.order), self._base_outputs, {}, {}, self._policy)
        try:
            runner.rerun({self._target: None}, outputs, self._ctx, {})
        except (ConfigError, ValueError) as exc:
            raise ProductionError([f"evaluate: {exc}"]) from exc
        head_outputs = {head: outputs[head] for head in self._heads}
        return head_outputs, canonical_hash(head_outputs)


# ---------------------------------------------------------------------------
# Proposer — candidates, quotes, proposals
# ---------------------------------------------------------------------------


def _field(row, name):
    """Read one field of a mapping or attribute-shaped row; :data:`_ABSENT` when it has none."""
    if isinstance(row, dict):
        return row.get(name, _ABSENT)
    return getattr(row, name, _ABSENT)


def _row_obj(row):
    """Render a row as JSON-shaped data: a mapping as is, a dataclass by its fields."""
    if isinstance(row, dict):
        return dict(row)
    if is_dataclass(row) and not isinstance(row, type):
        return asdict(row)
    raise ProductionError([f"a head row must be a mapping or a record, got {row!r}"])


def _head_items(head_outputs):
    """``(head, outputs)`` pairs, refusing anything but a non-empty map of maps."""
    if not isinstance(head_outputs, dict) or not head_outputs:
        raise ProductionError([f"head_outputs must be a non-empty map of head -> outputs, got {head_outputs!r}"])
    items = []
    for head, outputs in head_outputs.items():
        if not isinstance(head, str) or not isinstance(outputs, dict):
            raise ProductionError([f"head {head!r}: outputs must be a map of ports, got {outputs!r}"])
        items.append((head, outputs))
    return items


def _money(value, name):
    """Read a money field as a Decimal; float via its shortest repr; bool and non-numbers refuse."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is _ABSENT:
        raise ProductionError([f"{name} must be a number, got {value!r}"])
    if isinstance(value, int) or (isinstance(value, float) and number_ok(value)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ProductionError([f"{name} must be a decimal string, got {value!r}"]) from exc
    raise ProductionError([f"{name} must be a number, got {value!r}"])


def _money_or_none(value, name):
    """Read a nullable money field."""
    return None if value is None else _money(value, name)


def _ratio(value, name):
    """Read a dimensionless field as a float; anything but a finite number refuses."""
    if not number_ok(value):
        raise ProductionError([f"{name} must be a finite number, got {value!r}"])
    return float(value)


def _instant(value, name):
    """Read an epoch-ms field as an int."""
    problems = []
    check_int_param(problems, name, value, ge=0)
    if problems:
        raise ProductionError(problems)
    return int(value)


def _text(value, name):
    """Read a non-empty string field."""
    problems = []
    _check_str(problems, name, value)
    if problems:
        raise ProductionError(problems)
    return value


def _quote_of(row):
    """Build a Quote from a MarketRecord-shaped row, or None when the row carries no full market."""
    instrument, asof = _field(row, "instrument"), _field(row, "asof_ms")
    prices = [_field(row, name) for name in ("bid", "ask", "mid")]
    if not isinstance(instrument, str) or not instrument or not all(price_ok(p) for p in prices):
        return None
    if isinstance(asof, bool) or not isinstance(asof, int):
        return None
    bid, ask, mid = (_money(price, name) for price, name in zip(prices, ("bid", "ask", "mid")))
    return Quote(instrument=instrument, bid=bid, ask=ask, mid=mid, asof_ms=asof)


class Proposer(ABC):
    """The proposer seam (§5.3, D8): head outputs -> candidates -> quotes -> proposals.

    ``cls(params)`` construction, default-deny over the subclass's
    ``_PARAMS`` plus ``notes``. Two abstract hooks — :meth:`candidates`
    (stable, unsized, state-blind) and :meth:`proposals` (sized against
    the account, binding the frozen provenance) — and one concrete
    default, :meth:`quotes`, which is pure and reads
    ``MarketRecord``-shaped rows. None of them may re-run a node.

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; ``None`` means ``{}``.

    Examples
    --------
    A proposer that never proposes::

        class Silent(Proposer):
            def candidates(self, head_outputs):
                return []

            def proposals(self, head_outputs, candidates, state, provenance):
                return []

        Silent().candidates({"picks": {"records": []}})  # []
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
    def candidates(self, head_outputs):
        """Derive the stable, unsized candidates the tick may propose on.

        Parameters
        ----------
        head_outputs : dict
            ``{head: outputs}`` as :meth:`Decider.evaluate` returned.

        Returns
        -------
        list of Candidate
            Release-bound and state-independent: the same outputs give
            the same candidates whatever the account holds.
        """
        raise NotImplementedError

    @abstractmethod
    def proposals(self, head_outputs, candidates, state, provenance):
        """Size the candidates into an ordered proposal list.

        Parameters
        ----------
        head_outputs : dict
            The same immutable head outputs.
        candidates : list of Candidate
            What :meth:`candidates` returned; every proposal preserves
            its candidate's id, instrument and scope keys.
        state : AccountState
            The account as accounting snapshotted it.
        provenance : Provenance
            The tick's frozen bindings, copied onto every proposal.

        Returns
        -------
        list of Proposal
        """
        raise NotImplementedError

    def quotes(self, head_outputs):
        """Extract quotes from the head outputs — pure, state-independent.

        Every list-valued port of every head is read; a row shaped like
        :class:`~dskit.pipeline.records.MarketRecord` (an instrument, an
        ``asof_ms`` and three positive prices, read through that
        envelope's own ``price_ok``) becomes a
        :class:`~dskit.production.records.Quote`; any other row is
        skipped.

        Parameters
        ----------
        head_outputs : dict
            ``{head: outputs}``.

        Returns
        -------
        list of Quote
            In head, port and row order.
        """
        quotes = []
        for _head, outputs in _head_items(head_outputs):
            for rows in outputs.values():
                if not isinstance(rows, list):
                    continue
                quotes.extend(quote for quote in map(_quote_of, rows) if quote is not None)
        return quotes


class _RowProposer(Proposer):
    """One row of one head port per candidate; sizing is the subclass's :meth:`_size` hook."""

    _PARAMS = ("output", "fields")
    #: The Proposal fields ``fields`` may map to a head row field.
    _MAPPABLE = (
        "instrument",
        "side",
        "qty",
        "notional",
        "limit",
        "tif",
        "expires_ms",
        "reference_price",
        "exposure",
        "direction",
        "confidence",
        "prediction",
        "baseline",
        "expected_value",
        "scope_keys",
    )
    #: The mapped fields the sizing hook reads; the rest are carried as mapped.
    _SIZED = ("instrument", "side", "qty", "scope_keys")
    #: What the map must name.
    _REQUIRED_MAP = ("instrument",)

    @classmethod
    def validate_params(cls, params):
        """Return every problem: the port name, and a field map over Proposal fields only.

        Parameters
        ----------
        params : dict
            ``output`` (str) and ``fields`` (Proposal field -> row field).

        Returns
        -------
        list of str
        """
        problems = super().validate_params(params)
        _check_str(problems, "output", params.get("output"))
        mapping = params.get("fields")
        if not isinstance(mapping, dict):
            problems.append(f"fields must map Proposal field -> head row field, got {mapping!r}")
            return problems
        unknown = sorted(set(mapping) - set(cls._MAPPABLE))
        if unknown:
            problems.append(f"fields: {unknown} are not mappable Proposal fields (allowed: {list(cls._MAPPABLE)})")
        for name, source in mapping.items():
            _check_str(problems, f"fields.{name}", source)
        missing = [name for name in cls._REQUIRED_MAP if name not in mapping]
        if missing:
            problems.append(f"fields must map {missing}")
        return problems

    def _configure(self, params):
        """Keep the port and the field map."""
        self._output = params["output"]
        self._map = dict(params["fields"])

    def _tif(self):
        """Answer the time-in-force an unmapped proposal carries."""
        return DEFAULT_TIF

    @abstractmethod
    def _size(self, row, state, instrument):
        """Answer ``(side, qty)`` for one row — the subclass's whole decision."""
        raise NotImplementedError

    def _value(self, row, name):
        """Read the row field ``name`` maps to; an unmapped name or an absent field refuses."""
        source = self._map.get(name)
        if source is None:
            raise ProductionError([f"fields does not map {name!r}"])
        value = _field(row, source)
        if value is _ABSENT:
            raise ProductionError([f"head row {_row_obj(row)!r} carries no field {source!r} (mapped to {name!r})"])
        return value

    def _scope_keys(self, row, instrument):
        """Derive the candidate's scope keys: mapped, else the instrument alone."""
        if "scope_keys" not in self._map:
            return (instrument,)
        keys = self._value(row, "scope_keys")
        if not isinstance(keys, (list, tuple)) or not keys or any(not isinstance(k, str) or not k for k in keys):
            raise ProductionError([f"scope_keys must be a non-empty list of strings, got {keys!r}"])
        return tuple(keys)

    def _rows(self, head_outputs):
        """``(head, row)`` pairs from every head's declared port; a head without it refuses."""
        pairs = []
        for head, outputs in _head_items(head_outputs):
            rows = outputs.get(self._output)
            if not isinstance(rows, list):
                raise ProductionError(
                    [f"head {head!r} carries no list output {self._output!r} (ports: {sorted(outputs)})"]
                )
            pairs.extend((head, row) for row in rows)
        return pairs

    def _backed(self, head_outputs):
        """``(Candidate, row)`` pairs; the id covers the WHOLE row, and a duplicate refuses."""
        pairs, seen = [], set()
        for head, row in self._rows(head_outputs):
            row_obj = _row_obj(row)
            ident = canonical_hash({"head": head, "row": row_obj})
            if ident in seen:
                raise ProductionError([f"duplicate candidate: head {head!r} emitted the row {row_obj!r} twice"])
            seen.add(ident)
            instrument = _text(self._value(row, "instrument"), "instrument")
            candidate = Candidate(id=ident, instrument=instrument, scope_keys=self._scope_keys(row, instrument))
            pairs.append((candidate, row))
        return pairs

    def candidates(self, head_outputs):
        """One candidate per row of the declared port, id over the whole row.

        Parameters
        ----------
        head_outputs : dict
            ``{head: outputs}``.

        Returns
        -------
        list of Candidate

        Raises
        ------
        ProductionError
            A head without the port, a row without the mapped instrument,
            or two identical rows.
        """
        return [candidate for candidate, _row in self._backed(head_outputs)]

    def proposals(self, head_outputs, candidates, state, provenance):
        """Size every given candidate from its backing row and the account.

        Parameters
        ----------
        head_outputs : dict
            The same head outputs the candidates came from.
        candidates : list of Candidate
        state : AccountState
        provenance : Provenance

        Returns
        -------
        list of Proposal
            In the candidates' order, each preserving its candidate's id,
            instrument and scope keys and binding ``provenance``.

        Raises
        ------
        ProductionError
            A candidate no row backs, one whose instrument or scope keys
            changed, a duplicate, or a row that cannot be sized.
        """
        if not isinstance(state, AccountState):
            raise ProductionError([f"state must be an AccountState, got {state!r}"])
        if not isinstance(provenance, Provenance):
            raise ProductionError([f"provenance must be a Provenance, got {provenance!r}"])
        backing = {candidate.id: (candidate, row) for candidate, row in self._backed(head_outputs)}
        out, seen = [], set()
        for given in candidates:
            if not isinstance(given, Candidate):
                raise ProductionError([f"candidates must be Candidate records, got {given!r}"])
            if given.id in seen:
                raise ProductionError([f"candidate {given.id!r} is given twice"])
            seen.add(given.id)
            backed = backing.get(given.id)
            if backed is None:
                raise ProductionError([f"candidate {given.id!r} ({given.instrument!r}) is backed by no head row"])
            if backed[0] != given:
                raise ProductionError(
                    [f"candidate {given.id!r} changed since it was derived: {given.to_obj()} vs {backed[0].to_obj()}"]
                )
            out.append(self._proposal(given, backed[1], state, provenance))
        return out

    def _mapped(self, row):
        """Read the carried (non-sizing) mapped fields from the row."""
        return {name: self._value(row, name) for name in self._map if name not in self._SIZED}

    def _proposal(self, candidate, row, state, provenance):
        """Build one Proposal: the hook's size, the mapped fields, named defaults, the provenance."""
        side, qty = self._size(row, state, candidate.instrument)
        mapped = self._mapped(row)
        return Proposal(
            id=candidate.id,
            instrument=candidate.instrument,
            side=side,
            qty=qty,
            notional=_money_or_none(mapped.get("notional"), "notional"),
            limit=_money_or_none(mapped.get("limit"), "limit"),
            tif=_text(mapped.get("tif", self._tif()), "tif"),
            expires_ms=_instant(mapped.get("expires_ms", provenance.inputs_asof_ms), "expires_ms"),
            reference_price=_money(mapped.get("reference_price", UNMAPPED_MONEY), "reference_price"),
            exposure=_money(mapped.get("exposure", UNMAPPED_MONEY), "exposure"),
            direction=_text(mapped.get("direction", _DIRECTIONS[side]), "direction"),
            confidence=_ratio(mapped.get("confidence", UNMAPPED_RATIO), "confidence"),
            prediction=_ratio(mapped.get("prediction", UNMAPPED_RATIO), "prediction"),
            baseline=_ratio(mapped.get("baseline", UNMAPPED_RATIO), "baseline"),
            expected_value=_ratio(mapped.get("expected_value", UNMAPPED_RATIO), "expected_value"),
            inputs_asof_ms=provenance.inputs_asof_ms,
            inputs_digest=provenance.inputs_digest,
            coverage_digest=provenance.coverage_digest,
            quote_asof_ms=provenance.quote_asof_ms,
            quote_digest=provenance.quote_digest,
            extra={},
        )


class IntentRows(_RowProposer):
    """Each head row IS an intent: its mapped ``side`` and ``qty`` are the proposal's (§5.3).

    Parameters
    ----------
    params : dict
        ``output`` (str, REQUIRED) — the head port holding the rows;
        ``fields`` (dict, REQUIRED) — Proposal field -> row field, at
        least ``instrument``, ``side`` and ``qty``; ``default_tif`` (one
        of ``TIFS``, default :data:`DEFAULT_TIF`) — the time-in-force of
        a row that maps none.

    Examples
    --------
    ::

        proposer = IntentRows({
            "output": "records",
            "fields": {"instrument": "instrument", "side": "side", "qty": "qty",
                       "confidence": "confidence", "prediction": "prediction"},
        })
        candidates = proposer.candidates(head_outputs)
        proposals = proposer.proposals(head_outputs, candidates, account, provenance)
    """

    _PARAMS = ("output", "fields", "default_tif")
    _REQUIRED_MAP = ("instrument", "side", "qty")

    @classmethod
    def validate_params(cls, params):
        """Return every problem: the row proposer's, plus an off-vocabulary ``default_tif``.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = super().validate_params(params)
        tif = params.get("default_tif", DEFAULT_TIF)
        if tif not in TIFS:
            problems.append(f"default_tif must be one of {list(TIFS)}, got {tif!r}")
        return problems

    def _configure(self, params):
        """Keep the port, the map and the default time-in-force."""
        super()._configure(params)
        self._default_tif = params.get("default_tif", DEFAULT_TIF)

    def _tif(self):
        """Answer the configured default time-in-force."""
        return self._default_tif

    def _size(self, row, state, instrument):
        """Take the row's own side and qty; a sided row without a qty refuses (R10)."""
        side = _text(self._value(row, "side"), "side")
        if side not in SIDES:
            raise ProductionError([f"side must be one of {list(SIDES)}, got {side!r}"])
        qty = _money_or_none(self._value(row, "qty"), "qty")
        if side != _ABSTAIN and qty is None:
            raise ProductionError([f"{instrument}: side {side!r} needs a qty — only an abstaining row may omit it"])
        return side, qty


class TargetPositions(_RowProposer):
    """Each head row is a TARGET position; the proposal is the diff against the account (§5.3).

    Parameters
    ----------
    params : dict
        ``output`` (str, REQUIRED) — the head port holding the rows;
        ``fields`` (dict, REQUIRED) — Proposal field -> row field, at
        least ``instrument`` and ``qty`` (the target); ``side`` is never
        mapped, the diff decides it.

    Examples
    --------
    ::

        proposer = TargetPositions({"output": "records", "fields": {"instrument": "instrument", "qty": "qty"}})
        proposal = proposer.proposals(head_outputs, proposer.candidates(head_outputs), account, provenance)[0]
        proposal.side, proposal.qty  # ('buy', Decimal('6')) when the target is 10 and 4 are held
    """

    _MAPPABLE = tuple(name for name in _RowProposer._MAPPABLE if name != "side")
    _REQUIRED_MAP = ("instrument", "qty")

    def _size(self, row, state, instrument):
        """Buy or sell the difference between the target and the held position; abstain at zero."""
        target = _money(self._value(row, "qty"), "qty")
        held = sum((p.qty for p in state.positions if p.instrument == instrument), UNMAPPED_MONEY)
        diff = target - held
        side = _SIGN_SIDES[(diff > 0) - (diff < 0)]
        return side, (None if side == _ABSTAIN else abs(diff))


#: The proposer family's open doorway (§4.3).
PROPOSER_KINDS = Registry("proposer", Proposer)
PROPOSER_KINDS.register("intent-rows", IntentRows)
PROPOSER_KINDS.register("target-positions", TargetPositions)
