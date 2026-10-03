"""ADR-0229: the strategy objects that make a model-selection study runnable from a manifest.

A study step needs four things the workflow runner (ADR-0227) cannot know: the
candidate blocks its template asks for, the ordered command lines to run, a
reading of the study's output files into the step's declared outputs, and a
rule saying when a forward loop is done. Each is a subclassable strategy object
registered by name, never a branch in the runner:

* :class:`CandidateGenerator` (an ``in`` hook): forward, zoo and grid generators.
* :class:`StageSequence` (a step's ``stages``): one command tail per declared stage.
* :class:`Collector` (a step's ``collect``): study output files to step outputs.
* :class:`NoGainRule` (a loop's ``until``): stop when no addition improved.

Every word that names a project value (families, stage and partition words,
file and field names, flag spellings, metric direction) is a hook input read from
the manifest's args; nothing here knows a ticker, a family or a metric.
Stdlib only.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
from abc import abstractmethod

from .workflow import (
    Hook,
    MissingInput,
    NOTES_KEY,
    NamesToIndices,
    ROLE_COLLECT,
    ROLE_STAGES,
    StopRule,
    WorkflowError,
    register_hook,
    register_stop_rule,
)

__all__ = [
    "RoundResult",
    "CoreState",
    "SpecBuilder",
    "CandidateGenerator",
    "ForwardCandidates",
    "ZooCandidates",
    "GridCandidates",
    "StageSequence",
    "DeclaredSequence",
    "Collector",
    "ForwardCollector",
    "PickCollector",
    "ZooCollector",
    "GridCollector",
    "NoGainRule",
    "KeyedFamilies",
    "FamiliesSpec",
    "FamilyContracts",
    "ReaderOptions",
    "KeyedPool",
    "KeyedCap",
    "FeatureOrder",
    "HorizonLimits",
    "MarketSeries",
    "ReferenceIndices",
    "AgreeingBlock",
]

ENCODING = "utf-8"
SLOT_KEYS = ("candidates", "groups", "partitions", "max_candidates")
DEFAULT_SLOTS = {key: key for key in SLOT_KEYS}
PATH_KEYS = (
    "features",
    "encoder",
    "sequence_names",
    "sequence_indices",
    "context_indices",
)
DIRECTIONS = ("max", "min")
DROP_KEY = "drop"
#: Where step 1b's cohort rows come from: the prepared panel without a reader, else the reader's
#: own rows (a price-calendar lane has no prepared option panel). Both are port references.
COHORT_PREPARED = "$source.records"
COHORT_READER = "$panel.cohort"
POOL_WHERE = "the selection pool"
CORE_WHERE = "core.families"
MODEL_KEY = "model"
COMPARISON_KEY = "comparison"


# -- small shared helpers ------------------------------------------------------


def _dig(doc, dotted, what="document"):
    """Return the value at a path inside nested dicts, or raise.

    ``dotted`` is a dotted string, or a list of segments when a segment is data
    that may itself contain a dot (a candidate name such as ``enc_lr0.001``).
    """
    parts = list(dotted) if isinstance(dotted, (list, tuple)) else dotted.split(".")
    node = doc
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise WorkflowError(f"{what} has no {'.'.join(parts)}")
        node = node[part]
    return node


def _put(doc, dotted, value):
    """Set a dotted path inside nested dicts, creating the dicts on the way."""
    *head, last = dotted.split(".")
    node = doc
    for part in head:
        node = node.setdefault(part, {})
    node[last] = value


def _read_text(path):
    """Return a file's text, or raise MissingInput when it does not exist yet."""
    if not isinstance(path, str) or not os.path.isfile(path):
        raise MissingInput(f"{path} does not exist yet")
    with open(path, encoding=ENCODING) as handle:
        return handle.read()


def _read_json(path):
    """Return the JSON a file holds (the first record of a JSON-lines file)."""
    text = _read_text(path)
    try:
        return json.loads(text)
    except ValueError:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise WorkflowError(f"{path} is empty")
        return json.loads(lines[0])


def _read_rows(path):
    """Return the records of a JSON-lines file."""
    rows = []
    for line in _read_text(path).splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write(path, text):
    """Write a file whole (temp file, then rename), creating its directory."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = path + ".part"
    with open(temp, "w", encoding=ENCODING) as handle:
        handle.write(text)
    os.replace(temp, path)


def _lines(rows):
    """Render records as JSON lines."""
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _ordered(names, order):
    """Return the distinct names sorted by position in the ordered list."""
    NamesToIndices().apply({"names": list(names), "order": order})
    return sorted(set(names), key=order.index)


def _need(inputs, *keys):
    """Return the named inputs as a tuple, refusing any that are absent."""
    missing = [key for key in keys if key not in inputs]
    if missing:
        raise WorkflowError(f"missing inputs {missing}")
    return tuple(inputs[key] for key in keys)


class RoundResult:
    """What a collector reports about one round, for a stop rule to judge.

    Parameters
    ----------
    improved : bool
        The round changed the answer (a family was added).
    exhausted : bool
        Nothing is left to try after this round.

    Examples
    --------
    A round that added a family and left nothing more to try::

        result = RoundResult(improved=True, exhausted=True)
    """

    def __init__(self, improved, exhausted=False):
        self.improved = bool(improved)
        self.exhausted = bool(exhausted)


class CoreState:
    """The incumbent feature set: its names and the families it holds.

    Parameters
    ----------
    names : list of str
        Feature names in the ordered list's order.
    families : list of str
        Families already in the set.

    Examples
    --------
    Read the initial set from a declaration (``labels`` names its fields)::

        state = CoreState.initial(
            {"names": ["a"], "families": ["f"]}, {"f": {"fields": ["b"]}},
            ["a", "b", "c"], {"names": "names", "families": "families", "fields": "fields"})
        state.names
        # -> ['a', 'b']
    """

    def __init__(self, names, families):
        self.names = list(names)
        self.families = list(families)

    @classmethod
    def initial(cls, core, contracts, order, labels):
        """Build the starting set from a declaration and the family contracts."""
        families = list(core[labels["families"]])
        names = list(core[labels["names"]])
        for family in families:
            names += _dig(contracts, f"{family}.{labels['fields']}", "contracts")
        return cls(_ordered(names, order), families)

    @classmethod
    def read(cls, path, labels):
        """Build the set from a core file a collector wrote."""
        found = _read_json(path)
        return cls(found[labels["names"]], found[labels["families"]])

    @classmethod
    def at_round(cls, inputs):
        """Return the set a round starts from: the declaration, then the last core file."""
        labels = inputs["labels"]
        if inputs["round"] == 0:
            return cls.initial(inputs["start"], inputs["contracts"], inputs["order"], labels)
        return cls.read(inputs["current"], labels)

    def by_family(self, contracts, labels):
        """Return family -> count of its fields in the set; the rest under ``other``."""
        counts, seen = {}, set()
        for family in self.families:
            fields = set(_dig(contracts, f"{family}.{labels['fields']}", "contracts"))
            held = fields & set(self.names)
            counts[family] = len(held - seen)
            seen |= held
        counts[labels["other"]] = len(set(self.names) - seen)
        return counts


class SpecBuilder:
    """Assemble candidate model specs from one base spec and declared paths.

    Parameters
    ----------
    base : dict
        The model spec every candidate starts from.
    paths : dict
        Dotted locations inside a spec: ``features`` (subset indices),
        ``encoder`` (the encoder block), ``sequence_names`` (key in a zoo
        encoder holding rows of feature names), ``sequence_indices`` and
        ``context_indices`` (keys written into the encoder).
    order : list of str
        The ordered list of all feature names; indices are positions in it.

    Examples
    --------
    Put a feature subset into a copy of the base spec::

        builder = SpecBuilder(
            {"params": {}}, {"features": "params.indices", "encoder": "params.enc",
                             "sequence_names": "seq", "sequence_indices": "si",
                             "context_indices": "ci"}, ["a", "b", "c"])
        builder.subset(["c", "a"])
        # -> {'params': {'indices': [0, 2]}}
    """

    def __init__(self, base, paths, order):
        missing = [key for key in PATH_KEYS if key not in paths]
        if missing:
            raise WorkflowError(f"spec paths lack {missing}")
        self.base = copy.deepcopy(base)
        self.paths = paths
        self.order = list(order)

    def indices(self, names):
        """Return the ordered-list positions of ``names``, in feature order."""
        return NamesToIndices().apply({"names": _ordered(names, self.order), "order": self.order})

    def subset(self, names):
        """Return the base spec restricted to ``names``."""
        spec = copy.deepcopy(self.base)
        _put(spec, self.paths["features"], self.indices(names))
        return spec

    def with_encoder(self, names, encoder):
        """Return the subset spec with ``encoder``; name rows become subset positions."""
        spec = self.subset(names)
        block = copy.deepcopy(encoder)
        rows = block.pop(self.paths["sequence_names"], None)
        if rows is not None:
            position = {name: i for i, name in enumerate(_ordered(names, self.order))}
            lookup = [[self._position(position, name) for name in row] for row in rows]
            used = {i for row in lookup for i in row}
            block[self.paths["sequence_indices"]] = lookup
            block[self.paths["context_indices"]] = [
                i for i in range(len(position)) if i not in used
            ]
        _put(spec, self.paths["encoder"], block)
        return spec

    @staticmethod
    def _position(position, name):
        if name not in position:
            raise WorkflowError(f"sequence feature {name} is not in the feature set")
        return position[name]


# -- candidate generators --------------------------------------------------------


class CandidateGenerator(Hook):
    """Produce the placeholder values a study template asks for (an ``in`` hook).

    The hook's value is the ``base`` dict with the generated slots (candidates,
    groups, partitions and, where a subclass sets it, the candidate cap) laid
    over it, so a template keeps reading ``${name.candidates}`` beside the
    knobs it already read from the same args block.

    Parameters
    ----------
    None
        Subclasses set ``params`` and implement :meth:`candidates`,
        :meth:`groups`.

    Examples
    --------
    Subclass, register and use in a step's ``in`` map::

        class One(CandidateGenerator):
            params = ("base", "slots", "spec")

            def candidates(self, inputs):
                return {"only": inputs["spec"]}

            def groups(self, inputs, candidates):
                return {"all": list(candidates)}

        register_hook("one", One())
    """

    def apply(self, inputs):
        """Return the base dict with the generated slots laid over it."""
        slots = inputs.get("slots") or DEFAULT_SLOTS
        found = self.candidates(inputs)
        groups = self.groups(inputs, found)
        value = copy.deepcopy(inputs["base"])
        value[slots["candidates"]] = found
        value[slots["groups"]] = copy.deepcopy(groups)
        value[slots["partitions"]] = copy.deepcopy(groups)
        value.update(self.extra(inputs, found, slots))
        return value

    def extra(self, inputs, found, slots):
        """Return more slots to lay over the base; none by default."""
        return {}

    @abstractmethod
    def candidates(self, inputs):
        """Return the ordered candidates: name -> model spec."""

    @abstractmethod
    def groups(self, inputs, candidates):
        """Return group name -> candidate names (the search partitions)."""


class ForwardCandidates(CandidateGenerator):
    """Forward selection: the incumbent set alone, and it plus each remaining family.

    Parameters
    ----------
    None
        Inputs: ``base``, ``spec`` and ``paths`` (see :class:`SpecBuilder`),
        ``order``, ``contracts`` (family -> block holding a field list),
        ``labels`` (file and field names), ``start`` (the starting set),
        ``current`` (path of the core file the collector keeps), ``pool``
        (families to try, in order), ``cap`` (most candidates), ``admission``
        (path of the admission record) with ``admit`` (``flag`` pattern and
        ``value``), ``naming`` (``incumbent`` name, ``added`` and ``group``
        patterns), ``group_name`` and optional ``slots``.  ``round`` is injected.

    Examples
    --------
    Round 0 over two admitted families (``inputs`` carries the manifest's values)::

        value = ForwardCandidates().apply(inputs)
        sorted(value["candidates"])
        # -> ['core', 'core+fam_a', 'core+fam_b']
    """

    params = (
        "base", "spec", "paths", "order", "contracts", "labels", "start", "current",
        "pool", "cap", "admission", "admit", "naming", "group_name", "slots",
    )
    runtime = ("round",)

    def remaining(self, inputs, state):
        """Return the admitted pool families not yet in the set, in pool order."""
        record = _read_json(inputs["admission"])
        flag, wanted = inputs["admit"]["flag"], inputs["admit"]["value"]
        found = []
        for family in inputs["pool"]:
            key = flag.format(family=family)
            if key not in record:
                raise WorkflowError(f"admission record has no {key}")
            if family not in state.families and record[key] == wanted:
                found.append(family)
        return found

    def candidates(self, inputs):
        """Return the incumbent alone, then it plus each remaining family."""
        state = CoreState.at_round(inputs)
        builder = SpecBuilder(inputs["spec"], inputs["paths"], inputs["order"])
        naming, labels = inputs["naming"], inputs["labels"]
        found = {naming["incumbent"]: builder.subset(state.names)}
        for family in self.remaining(inputs, state):
            added = _dig(inputs["contracts"], f"{family}.{labels['fields']}", "contracts")
            name = naming["added"].format(family=family)
            found[name] = builder.subset(state.names + list(added))
        if len(found) > inputs["cap"]:
            raise WorkflowError(f"{len(found)} candidates exceed the cap {inputs['cap']}")
        return found

    def groups(self, inputs, candidates):
        """Return one group holding every candidate, named from the round."""
        group = inputs["naming"]["group"].format(
            group_name=inputs["group_name"], round=inputs["round"]
        )
        return {group: list(candidates)}

    def extra(self, inputs, found, slots):
        """Set the candidate cap to this round's count."""
        return {slots["max_candidates"]: len(found)}


class _EncoderCandidates(CandidateGenerator):
    """Shared reading of the final set and the encoder overlays (zoo and grid)."""

    def final_names(self, inputs):
        """Return the feature names of the final set the step-4 collector wrote."""
        return CoreState.read(inputs["final"], inputs["labels"]).names

    def builder(self, inputs):
        """Return the spec builder over the step's base spec."""
        return SpecBuilder(inputs["spec"], inputs["paths"], inputs["order"])


class ZooCandidates(_EncoderCandidates):
    """One candidate per declared encoder over the final feature set.

    Parameters
    ----------
    None
        Inputs: ``base``, ``encoders`` (name -> encoder block, optionally with
        rows of feature names under the ``sequence_names`` path key), ``spec``,
        ``paths``, ``order``, ``labels``, ``final`` (path of the final core file),
        optional ``slots``.

    Examples
    --------
    Two encoders, each in its own group::

        value = ZooCandidates().apply(inputs)
        value["groups"]
        # -> {'plain': ['plain'], 'recurrent': ['recurrent']}
    """

    params = ("base", "encoders", "spec", "paths", "order", "labels", "final", "slots")

    def candidates(self, inputs):
        """Return one spec per encoder."""
        names = self.final_names(inputs)
        builder = self.builder(inputs)
        return {n: builder.with_encoder(names, enc) for n, enc in inputs["encoders"].items()}

    def groups(self, inputs, candidates):
        """Return each candidate in a group of its own."""
        return {name: [name] for name in candidates}


class GridCandidates(_EncoderCandidates):
    """The cartesian product of declared axes over the chosen encoder.

    Parameters
    ----------
    None
        Inputs: ``base``, ``encoders``, ``axes`` (axis -> list of values),
        ``axis_paths`` (axis -> dotted spec path), ``naming`` (``pattern`` with
        ``{axis}`` fields and the ``winner_var`` field, ``winner_var``,
        ``list_join``), ``winner`` (path of the file holding the chosen encoder),
        ``spec``, ``paths``, ``order``, ``labels``, ``final``, optional ``slots``.

    Examples
    --------
    Nine candidates from two axes, grouped under the chosen encoder::

        value = GridCandidates().apply(inputs)
        len(value["candidates"])
        # -> 9
    """

    params = (
        "base", "encoders", "axes", "axis_paths", "naming", "winner", "spec", "paths",
        "order", "labels", "final", "slots",
    )

    def chosen(self, inputs):
        """Return the chosen encoder's name from the winner file."""
        found = _read_json(inputs["winner"])
        return found[inputs["labels"]["winner"]]

    def candidates(self, inputs):
        """Return one spec per point of the axes product."""
        winner = self.chosen(inputs)
        encoders = inputs["encoders"]
        if winner not in encoders:
            raise WorkflowError(f"chosen encoder {winner} is not declared")
        base = self.builder(inputs).with_encoder(self.final_names(inputs), encoders[winner])
        axes = inputs["axes"]
        found = {}
        for point in itertools.product(*axes.values()):
            spec = copy.deepcopy(base)
            for axis, value in zip(axes, point):
                _put(spec, inputs["axis_paths"][axis], copy.deepcopy(value))
            found[self.name(inputs["naming"], winner, dict(zip(axes, point)))] = spec
        return found

    def name(self, naming, winner, point):
        """Return a candidate's name from the pattern, joining list values."""
        join = naming["list_join"]
        text = {
            axis: join.join(str(v) for v in value) if isinstance(value, list) else str(value)
            for axis, value in point.items()
        }
        return naming["pattern"].format(**{naming["winner_var"]: winner}, **text)

    def groups(self, inputs, candidates):
        """Return one group, named by the chosen encoder, holding every candidate."""
        return {self.chosen(inputs): list(candidates)}


# -- stage sequence -------------------------------------------------------------


class StageSequence(Hook):
    """Turn a declared stage list into the command tails a step runs in order.

    Parameters
    ----------
    None
        Subclasses implement :meth:`apply`; ``config`` (the expanded study
        document) is injected at run time.

    Examples
    --------
    A sequence that runs the base command once, unchanged::

        class Once(StageSequence):
            params = ()

            def apply(self, inputs):
                return [[]]

        register_hook("once", Once())
    """

    role = ROLE_STAGES
    runtime = ("config",)


class DeclaredSequence(StageSequence):
    """Run the declared records in order, expanding a wildcard into every partition.

    Parameters
    ----------
    None
        Inputs: ``records`` (list of dicts), ``flags`` (record field -> command
        flag, in command order), ``expand`` (the field that may be a wildcard),
        ``wildcard`` (its token) and ``partitions_at`` (dotted path in the
        study document of a dict whose keys are the partitions).

    Examples
    --------
    A wildcard record becomes one tail per partition::

        DeclaredSequence().apply({
            "records": [{"stage": "search", "partition": "*"}, {"stage": "select"}],
            "flags": {"stage": "--stage", "partition": "--partition"},
            "expand": "partition", "wildcard": "*",
            "partitions_at": "parts", "config": {"parts": {"a": [], "b": []}}})
        # -> [['--stage', 'search', '--partition', 'a'],
        #     ['--stage', 'search', '--partition', 'b'], ['--stage', 'select']]
    """

    params = ("records", "flags", "expand", "wildcard", "partitions_at")

    def apply(self, inputs):
        """Return one list of command tokens per stage invocation."""
        flags = inputs["flags"]
        tails = []
        for record in inputs["records"]:
            unknown = sorted(set(record) - set(flags))
            if unknown:
                raise WorkflowError(f"stage record has fields with no flag: {unknown}")
            tails += [self._tail(flags, one) for one in self._expanded(inputs, record)]
        return tails

    def _expanded(self, inputs, record):
        """Return the record, or one copy per partition when it holds the wildcard."""
        field = inputs["expand"]
        if record.get(field) != inputs["wildcard"]:
            return [record]
        partitions = _dig(inputs["config"], inputs["partitions_at"], "study document")
        return [{**record, field: name} for name in partitions]

    @staticmethod
    def _tail(flags, record):
        tail = []
        for field, flag in flags.items():
            if field in record:
                tail += [flag, str(record[field])]
        return tail


# -- collectors -------------------------------------------------------------------


class Collector(Hook):
    """Read a study's selection output into the step's declared outputs.

    The study's own file and field names arrive as the ``select`` input, so
    nothing here names a file the study writes.  Subclasses implement
    :meth:`write`.

    Parameters
    ----------
    None
        Shared inputs: ``select`` (``dir``, ``file`` and the row fields
        ``candidate``, ``partition``, ``score``, ``winner``, optional
        ``variant``), ``direction`` (``max`` or ``min``), ``output_at`` and
        ``candidates_at`` (dotted paths in the study document), ``labels``,
        ``rounds`` (path of the rounds output). ``config`` and ``round`` are
        injected.

    Examples
    --------
    Subclass and register::

        class Quiet(Collector):
            params = Collector.params

            def write(self, inputs, rows, best):
                return RoundResult(improved=False)

        register_hook("quiet", Quiet())
    """

    role = ROLE_COLLECT
    runtime = ("config", "round")
    params = ("select", "direction", "output_at", "candidates_at", "labels", "rounds")

    def apply(self, inputs):
        """Read the study's rows, pick the best partition winner, write the outputs."""
        rows = self.rows(inputs)
        return self.write(inputs, rows, self.best(inputs, rows))

    def rows(self, inputs):
        """Return the study's per-candidate rows for this round."""
        select = inputs["select"]
        output = _dig(inputs["config"], inputs["output_at"], "study document")
        path = os.path.join(output, select["dir"], select["file"])
        if not os.path.isfile(path):
            raise WorkflowError(f"the study wrote no {path}")
        return _read_rows(path)

    def best(self, inputs, rows):
        """Return the best of the partition winners; the first one wins a tie."""
        select = inputs["select"]
        if inputs["direction"] not in DIRECTIONS:
            raise WorkflowError(f"direction must be one of {DIRECTIONS}")
        winners = [row for row in rows if row[select["winner"]]]
        if not winners:
            raise WorkflowError("the study marked no winner")
        best = winners[0]
        for row in winners[1:]:
            if self.better(inputs["direction"], row[select["score"]], best[select["score"]]):
                best = row
        return best

    @staticmethod
    def better(direction, score, other):
        """Return True when ``score`` is strictly better than ``other``."""
        return score > other if direction == "max" else score < other

    def spec(self, inputs, name):
        """Return the named candidate's spec from the expanded study document."""
        return _dig(
            inputs["config"], [*inputs["candidates_at"].split("."), name], "study document")

    @abstractmethod
    def write(self, inputs, rows, best):
        """Write the step's outputs; return a :class:`RoundResult`."""


class ForwardCollector(Collector):
    """Forward selection: the new core, and one rounds row per round.

    Parameters
    ----------
    None
        Shared inputs plus ``result`` (path of the core file), ``order``,
        ``contracts``, ``start``, ``current``, ``pool``, ``paths`` (``features``
        locates the subset indices in a spec), ``naming`` (``incumbent`` and
        ``added`` patterns) and ``margin`` (the least score gain that counts).

    Examples
    --------
    One round: the winner adds a family, so the core file is rewritten::

        result = ForwardCollector().apply(inputs)
        result.improved
        # -> True
    """

    params = Collector.params + (
        "result", "order", "contracts", "start", "current", "pool", "paths", "naming", "margin",
    )

    def write(self, inputs, rows, best):
        """Move the core to the winner when it beat the incumbent by the margin."""
        name = best[inputs["select"]["candidate"]]
        state = CoreState.at_round(inputs)
        incumbent = inputs["naming"]["incumbent"]
        gain = self.gain(inputs, rows, best)
        improved = name != incumbent and gain > inputs["margin"]
        family = self.family(inputs, name) if improved else None
        if improved:
            indices = _dig(self.spec(inputs, name), inputs["paths"]["features"], "spec")
            state = CoreState([inputs["order"][i] for i in indices], state.families + [family])
        _write(inputs["result"], json.dumps(self.core_record(inputs, state), indent=2) + "\n")
        self.append_round(inputs, rows, best, family)
        cands = len(self.rows_for(inputs, rows))
        return RoundResult(improved, exhausted=(cands - 1 - int(improved)) == 0)

    def rows_for(self, inputs, rows):
        """Return the distinct candidate names of the round."""
        key = inputs["select"]["candidate"]
        return list(dict.fromkeys(row[key] for row in rows))

    def gain(self, inputs, rows, best):
        """Return the winner's improvement over the incumbent, in the metric's direction."""
        select = inputs["select"]
        key = select["candidate"]
        variant = select.get("variant")
        same = [
            row for row in rows
            if row[key] == inputs["naming"]["incumbent"]
            and (variant is None or row[variant] == best[variant])
        ]
        if not same:
            raise WorkflowError("the study scored no incumbent candidate")
        base, new = same[0][select["score"]], best[select["score"]]
        return new - base if inputs["direction"] == "max" else base - new

    def family(self, inputs, name):
        """Return the family a candidate name added (inverse of the naming pattern)."""
        for family in inputs["pool"]:
            if inputs["naming"]["added"].format(family=family) == name:
                return family
        raise WorkflowError(f"candidate {name} matches no pool family")

    def core_record(self, inputs, state):
        """Return the core file's content."""
        labels = inputs["labels"]
        return {
            labels["names"]: state.names,
            labels["families"]: state.families,
            labels["by_family"]: state.by_family(inputs["contracts"], labels),
        }

    def append_round(self, inputs, rows, best, family):
        """Add this round's row to the rounds file (a new file at round 0)."""
        select, labels = inputs["select"], inputs["labels"]
        variant = select.get("variant")
        scored = {
            row[select["candidate"]]: row[select["score"]]
            for row in rows
            if variant is None or row[variant] == best[variant]
        }
        row = {
            labels["round"]: inputs["round"],
            labels["candidates"]: self.rows_for(inputs, rows),
            labels["scores"]: scored,
            labels["winner"]: best[select["candidate"]],
            labels["added"]: family,
        }
        path = inputs["rounds"]
        kept = _read_rows(path) if inputs["round"] > 0 and os.path.isfile(path) else []
        _write(path, _lines(kept + [row]))


class PickCollector(Collector):
    """Choose the best candidate once: write the choice and copy the study's rows.

    Parameters
    ----------
    None
        Shared inputs plus ``result`` (path of the choice file). Subclasses
        implement :meth:`payload`.

    Examples
    --------
    See :class:`ZooCollector`.
    """

    params = Collector.params + ("result",)

    def write(self, inputs, rows, best):
        """Write the choice file and the rounds file; the round always counts."""
        name = best[inputs["select"]["candidate"]]
        record = self.payload(inputs, name)
        _write(inputs["result"], json.dumps(record, indent=2) + "\n")
        _write(inputs["rounds"], _lines(rows))
        return RoundResult(improved=True, exhausted=True)

    @abstractmethod
    def payload(self, inputs, name):
        """Return the choice file's content for the chosen candidate."""


class ZooCollector(PickCollector):
    """The chosen encoder: a file holding its name under the ``winner`` label.

    Parameters
    ----------
    None

    Examples
    --------
    The winner file for a chosen encoder named ``plain``::

        ZooCollector().payload({"labels": {"winner": "winner"}}, "plain")
        # -> {'winner': 'plain'}
    """

    def payload(self, inputs, name):
        """Return the encoder's name."""
        return {inputs["labels"]["winner"]: name}


class GridCollector(PickCollector):
    """The chosen grid candidate: its name and full spec.

    Parameters
    ----------
    None

    Examples
    --------
    The best file holds the name and the spec::

        GridCollector().payload(inputs, "plain_h16")
        # -> {'name': 'plain_h16', 'spec': {...}}
    """

    def payload(self, inputs, name):
        """Return the candidate's name and its spec."""
        labels = inputs["labels"]
        return {labels["name"]: name, labels["spec"]: self.spec(inputs, name)}


# -- keyed families: a new family is data, not a graph edit --------------------------


class KeyedFamilies:
    """The ``families.keyed`` declaration, parsed once and the lists it implies.

    A keyed family is a table of columns keyed by an entity and a date that lives in an
    onboarded store. Declaring it under ``spec["keyed"]`` is the whole change: this
    object derives what the step templates read for it (availability contract, the
    panel's schema fields, the attach entry, the date flag, the reader's table block)
    so no list is typed twice. ``spec["keyed_key"]`` names the entity and date fields,
    ``spec["keyed_naming"]`` the ``age``/``missing`` companion suffixes of an as-of table.

    Parameters
    ----------
    spec : dict
        The ``families`` block: ``keyed`` (family -> ``sources`` [``root``, ``source``,
        ``stream``, ``key_fields``, ``columns`` {stream column: output field}], ``fields``,
        optional ``require``, ``max_age_days``, ``strict_prior``, ``withheld_fields``,
        and ``lookback``/``clock_note`` text), ``keyed_key``, ``keyed_naming``, and the
        built-in ``availability`` and ``schema_fields`` the new names must not collide with.
        Optional ``drop`` (list of distinct names: a built-in or keyed family, or a carried entry): families
        removed after everything folds, so an args overlay can take one out (an overlay
        cannot delete a key). A name no family declares is refused.

    Examples
    --------
    One as-of family over a single table::

        keyed = KeyedFamilies({
            "availability": {}, "schema_fields": [], "keyed_key": ["sym", "day"],
            "keyed_naming": {"age": "_age", "missing": "_gone"},
            "keyed": {"fam": {
                "sources": [{"root": "./ob", "source": "s", "stream": "t",
                             "key_fields": ["sym", "day"], "columns": {"x": "fam_x"}}],
                "fields": ["fam_x"], "max_age_days": 3,
                "lookback": "last value", "clock_note": "prior close"}}})
        keyed.outputs("fam")
        # -> ['fam_x', 'fam_x_age', 'fam_x_gone']
    """

    _FAMILY_KEYS = ("sources", "fields", "require", "max_age_days", "strict_prior",
                    "withheld_fields", "lookback", "clock_note", "notes")
    _SOURCE_KEYS = ("root", "source", "stream", "key_fields", "columns", "notes")

    def __init__(self, spec):
        self.spec = spec
        self.keyed = spec.get("keyed") or {}
        if not isinstance(self.keyed, dict):
            raise WorkflowError("families.keyed must be an object")
        self.drop = self._drop_names()
        self.keyed = {n: fam for n, fam in self.keyed.items() if n not in self.drop}
        if self.keyed:
            self._check_globals()
            for name, fam in self.keyed.items():
                self._check_family(name, fam)

    def _drop_names(self):
        """Return the ``drop`` list, refusing a non-list, a repeat or an undeclared name."""
        names = self.spec.get(DROP_KEY)
        if names is None:
            return []
        known = (set(self.spec.get("availability") or ()) | set(self.keyed)
                 | set(self.spec.get("carried") or ()))
        if (not isinstance(names, list) or not all(isinstance(n, str) for n in names)
                or len(set(names)) != len(names)):
            raise WorkflowError("families.drop must be a list of distinct family names")
        unknown = [n for n in names if n not in known]
        if unknown:
            raise WorkflowError(f"families.drop names {unknown}, which no family declares")
        return list(names)

    def names(self):
        """Return the keyed family names in declaration order, minus the dropped."""
        return list(self.keyed)

    def availability(self):
        """Return the spec's own ``availability`` block without the dropped families."""
        return {n: c for n, c in (self.spec.get("availability") or {}).items()
                if n not in self.drop}

    def _check_globals(self):
        key, naming = self.spec.get("keyed_key"), self.spec.get("keyed_naming")
        if not (isinstance(key, list) and len(key) >= 2 and all(isinstance(k, str) and k for k in key)):
            raise WorkflowError("families.keyed_key must list the entity and date fields")
        if not (isinstance(naming, dict) and set(naming) == {"age", "missing"}
                and all(isinstance(v, str) and v for v in naming.values())):
            raise WorkflowError("families.keyed_naming must be {age, missing} suffix strings")

    def _check_family(self, name, fam):
        where = f"families.keyed.{name}"
        if not isinstance(fam, dict):
            raise WorkflowError(f"{where} must be an object")
        extra = sorted(set(fam) - set(self._FAMILY_KEYS))
        if extra:
            raise WorkflowError(f"{where}: unknown key(s) {extra}")
        if name in (self.spec.get("availability") or {}):
            raise WorkflowError(f"{where}: {name!r} is already a built-in family")
        for need in ("sources", "fields", "lookback", "clock_note"):
            if not fam.get(need):
                raise WorkflowError(f"{where}.{need} is required")
        for number, source in enumerate(fam["sources"]):
            self._check_source(f"{where}.sources[{number}]", source)
        outputs = self.outputs(name)
        if len(set(outputs)) != len(outputs):
            raise WorkflowError(f"{where}: an output field is declared twice")
        absent = [f for f in fam["fields"] if f not in outputs]
        if absent:
            raise WorkflowError(f"{where}.fields {absent} are not among the sources' columns")
        clash = sorted(set(outputs) & set(self.spec.get("schema_fields") or ()))
        if clash:
            raise WorkflowError(f"{where}: {clash} already exist in schema_fields")
        age = fam.get("max_age_days")
        if age is not None and (isinstance(age, bool) or not isinstance(age, int) or age < 0):
            raise WorkflowError(f"{where}.max_age_days must be an int >= 0")

    def _check_source(self, where, source):
        if not isinstance(source, dict):
            raise WorkflowError(f"{where} must be an object")
        extra = sorted(set(source) - set(self._SOURCE_KEYS))
        if extra:
            raise WorkflowError(f"{where}: unknown key(s) {extra}")
        for need in ("root", "source", "stream"):
            if not isinstance(source.get(need), str) or not source[need]:
                raise WorkflowError(f"{where}.{need} is required")
        if len(source.get("key_fields") or ()) != len(self.spec["keyed_key"]):
            raise WorkflowError(f"{where}.key_fields needs one stream field per keyed_key entry")
        columns = source.get("columns")
        if not isinstance(columns, dict) or not columns:
            raise WorkflowError(f"{where}.columns must map stream columns to output fields")

    def _own(self, name):
        """Return the family's own output fields (no companions), in source order."""
        return [out for src in self.keyed[name]["sources"] for out in src["columns"].values()]

    def companions(self, name):
        """Return the age and missing fields of an as-of family, per column; else none."""
        if self.keyed[name].get("max_age_days") is None:
            return []
        naming = self.spec["keyed_naming"]
        own = self._own(name)
        return [f + naming["age"] for f in own] + [f + naming["missing"] for f in own]

    def outputs(self, name):
        """Return every field the family adds to the panel, companions last."""
        return self._own(name) + self.companions(name)

    def features(self, name):
        """Return the model features of the family: its fields and their companions."""
        naming = self.spec.get("keyed_naming") or {}
        fields = list(self.keyed[name]["fields"])
        if self.keyed[name].get("max_age_days") is None:
            return fields
        return fields + [f + naming["age"] for f in fields] + [f + naming["missing"] for f in fields]

    def contract(self, name):
        """Return the availability contract: fields, sources and the as-of conditions."""
        fam, naming = self.keyed[name], self.spec.get("keyed_naming")
        require = [dict(r) for r in fam.get("require") or ()]
        if fam.get("max_age_days") is not None:
            for field in fam["fields"]:
                require += [
                    {"field": field + naming["missing"], "op": "==", "value": 0},
                    {"field": field + naming["age"], "op": ">=", "value": 0},
                    {"field": field + naming["age"], "op": "<=", "value": fam["max_age_days"]},
                ]
        sources = [{"source": s["source"], "stream": s["stream"]} for s in fam["sources"]]
        return {"fields": list(fam["fields"]), "require": require, "sources": sources}

    def tables(self):
        """Return the reader's ``tables`` block: one table per source."""
        found = {}
        for name, fam in self.keyed.items():
            for number, src in enumerate(fam["sources"]):
                table = {k: copy.deepcopy(src[k]) for k in ("root", "source", "stream", "key_fields", "columns")}
                for knob in ("max_age_days", "strict_prior"):
                    if knob in fam:
                        table[knob] = fam[knob]
                found[name if len(fam["sources"]) == 1 else f"{name}_{number}"] = table
        return found

    def merged(self):
        """Return the families block with every derived list extended by the keyed families."""
        from .kinds_availability import FLAG_PREFIX

        out = {k: copy.deepcopy(v) for k, v in self.spec.items()}
        for part in ("availability", "documentation", "engineered", "flags"):
            out.setdefault(part, {})
        schema, dates = list(out.get("schema_fields") or []), list(out.get("dates_fields") or [])
        for name, fam in self.keyed.items():
            out["availability"][name] = self.contract(name)
            out["documentation"][name] = {"lookback": fam["lookback"], "clock": fam["clock_note"]}
            out["engineered"][name] = {"fields": self.outputs(name),
                                       "withheld_fields": dict(fam.get("withheld_fields") or {}),
                                       "clock_note": fam["clock_note"]}
            out["flags"][name] = {"flag": FLAG_PREFIX + name}
            schema += [f for f in self.outputs(name) if f not in schema]
            dates.append(FLAG_PREFIX + name)
        out["schema_fields"], out["dates_fields"] = schema, dates
        out["keyed_tables"] = self.tables()
        return self._without_dropped(out)

    def _without_dropped(self, out):
        """Remove the dropped families, and the fields and flags only they used, from ``out``."""
        if not self.drop:
            return out
        gone = {f for n in self.drop for f in self._used(out, n)}
        flags = {out["flags"][n]["flag"] for n in self.drop if n in out["flags"]}
        for part in ("availability", "documentation", "engineered", "flags", "keyed"):
            out[part] = {n: v for n, v in (out.get(part) or {}).items() if n not in self.drop}
        gone -= {f for n in out["availability"] for f in self._used(out, n)}
        if "carried" in out:
            named = {n: v for n, v in out["carried"].items() if n not in self.drop}
            out["carried"] = self._carried(named, gone)
        for part in ("schema_fields", "columns"):
            if part in out:
                out[part] = [f for f in out[part] if f not in gone]
        out["dates_fields"] = [f for f in out["dates_fields"] if f not in flags]
        return out

    @staticmethod
    def _carried(carried, gone):
        """Return the ``carried`` entries minus those wholly gone; refuse a partly gone one."""
        kept = {}
        for name, entry in (carried or {}).items():
            fields = list(entry.get("fields") or ())
            hit = [f for f in fields if f in gone]
            if hit and len(hit) < len(fields):
                raise WorkflowError(
                    f"families.drop removes {hit} but carried.{name} also holds "
                    f"{[f for f in fields if f not in gone]}: split or drop it")
            if not hit:
                kept[name] = entry
        return kept

    def refuse_dropped(self, names, where):
        """Raise when ``names`` (a pool or core family list) names a dropped family."""
        named = [n for n in names or () if n in self.drop]
        if named:
            raise WorkflowError(f"{where} names dropped families {named}: remove them there too")

    @staticmethod
    def _used(out, name):
        """Return every field a family's contract and engineered block name."""
        contract = (out.get("availability") or {}).get(name) or {}
        engineered = (out.get("engineered") or {}).get(name) or {}
        return (list(contract.get("fields") or ())
                + [r["field"] for r in contract.get("require") or ()]
                + list(engineered.get("fields") or ()))


class FamiliesSpec(Hook):
    """The ``families`` block with the keyed families folded into every derived list.

    Parameters
    ----------
    None
        Input ``spec``: the manifest's families block (see :class:`KeyedFamilies`). With no
        keyed family the block comes back with an empty ``keyed_tables``.

    Examples
    --------
    Used as a step's ``in`` value::

        {"families": {"hook": "families_spec", "spec": "$args.families"}}
    """

    params = ("spec",)

    def apply(self, inputs):
        """Return the merged families block."""
        return KeyedFamilies(inputs["spec"]).merged()


class ReaderOptions(Hook):
    """The optional panel-reader knobs, with everything that repeats another arg derived from it.

    ``exact_dte`` is the horizon (set only when a ``reader`` is selected); ``keyed_tables`` is
    the families block's keyed tables with their key and companion naming (set only when a keyed
    family is declared); with a reader, ``price_source`` is the step-1 price source's own
    ``source``/``stream``/lane ``relpath`` plus the declared ``columns`` map (without one it is
    the feature source's value, untouched); ``corporate_actions`` gets this lane's ``windows``.
    None of those is declared twice, and declaring a derived one is refused.

    The declared names are the reader vocabulary of the panel readers this toolkit's child
    packages ship (``reader``, ``corporate_actions``, ``columns``); a project with other options
    adds a hook, it does not reuse this one. ``tests/test_panel_options.py`` pins ``DECLARED``
    to the manifest's ``panel_options`` keys.

    Parameters
    ----------
    None
        Inputs ``options`` (dict of the ``DECLARED`` keys; null means unset), ``horizon`` (int),
        ``spec`` (the families block), ``lane`` (str), ``windows`` (``{lane: [[from, to], ...]}``),
        ``prices`` (the step-1 price source: ``root``, ``source``, ``stream``, ``relpath``),
        ``feature_price`` (the feature sources' ``price_source`` value) and ``root`` (the feature
        sources' store root, which must equal ``prices.root`` when a reader is set).

    Raises
    ------
    WorkflowError
        ``options`` declares a derived or unknown knob, ``corporate_actions`` carries its own
        ``windows``, a reader has no ``columns``, or the two store roots differ.

    Examples
    --------
    No reader selected, no keyed family::

        ReaderOptions().apply({"options": {}, "horizon": 7, "spec": {"keyed": {}, "availability": {}},
                               "lane": "A", "windows": {}, "prices": {}, "feature_price": "src",
                               "root": "r"})
        # -> {'reader': None, 'corporate_actions': None, 'exact_dte': None, 'keyed_tables': None, 'price_source': 'src'}
    """

    params = ("options", "horizon", "spec", "lane", "windows", "prices", "feature_price", "root")
    DECLARED = ("reader", "corporate_actions", "columns")

    def apply(self, inputs):
        """Return the five knobs (unset ones None) and the cohort port reference."""
        options = inputs["options"] or {}
        stray = sorted(set(options) - set(self.DECLARED) - {NOTES_KEY})
        if stray:
            raise WorkflowError(f"panel options {stray} are derived or unknown; declare only "
                                f"{list(self.DECLARED)}")
        reader = options.get("reader")
        return {"reader": reader, "corporate_actions": self._actions(options, inputs),
                "exact_dte": None if reader is None else inputs["horizon"],
                "keyed_tables": self._keyed(inputs["spec"]),
                "price_source": self._price_source(options, inputs),
                "cohort": COHORT_PREPARED if reader is None else COHORT_READER,
                "cohort_columns": self._cohort_columns(inputs["spec"], reader)}

    @staticmethod
    def _cohort_columns(spec, reader):
        """Return the identity plus agreed fields (what a cohort row holds), or None w/o a reader."""
        if reader is None:
            return None
        attach = KeyedFamilies(spec).merged()["attach"]
        return list(attach["identity"]) + list(attach["agree_fields"])

    @staticmethod
    def _keyed(spec):
        """Return the keyed-tables block of the families spec, or None when none is declared."""
        merged = KeyedFamilies(spec).merged()
        if not merged["keyed_tables"]:
            return None
        return {"key": merged["keyed_key"], "tables": merged["keyed_tables"],
                "age_suffix": merged["keyed_naming"]["age"],
                "missing_suffix": merged["keyed_naming"]["missing"]}

    @staticmethod
    def _actions(options, inputs):
        """Return the corporate-action spec with this lane's windows, or None when unset."""
        spec = options.get("corporate_actions")
        if spec is None:
            return None
        if "windows" in spec:
            raise WorkflowError("corporate_actions.windows is the lane-keyed arg corporate_windows, "
                                "not part of the declaration")
        return {**spec, "windows": {inputs["lane"]: (inputs["windows"] or {}).get(inputs["lane"], [])}}

    @staticmethod
    def _price_source(options, inputs):
        """Return the feature price source: the step-1 file reference plus ``columns`` under a reader."""
        if options.get("reader") is None:
            return inputs["feature_price"]
        prices, columns = inputs["prices"] or {}, options.get("columns")
        if not columns:
            raise WorkflowError("a reader needs panel_options.columns {file column: field}")
        if prices.get("root") != inputs["root"]:
            raise WorkflowError(f"price_source.root {prices.get('root')!r} differs from "
                                f"feature_sources.root {inputs['root']!r}")
        return {"source": prices["source"], "stream": prices["stream"],
                "relpath": prices["relpath"], "columns": columns}


class FamilyContracts(FamiliesSpec):
    """Only the merged ``availability`` block: family -> its fields and conditions.

    Parameters
    ----------
    None
        Input ``spec`` as for :class:`FamiliesSpec`.

    Examples
    --------
    Used where a hook wants the contracts::

        {"contracts": {"hook": "family_contracts", "spec": "$args.families"}}
    """

    def apply(self, inputs):
        """Return the merged availability block."""
        return super().apply(inputs)["availability"]


class KeyedPool(Hook):
    """The forward candidate pool plus every keyed family not already in it.

    Parameters
    ----------
    None
        Inputs ``base`` (the typed pool) and ``spec`` (the families block).

    Examples
    --------
    A keyed family joins the pool::

        KeyedPool().apply({"base": ["a"], "spec": spec_with_keyed_fam})
        # -> ['a', 'fam']
    """

    params = ("base", "spec")

    def apply(self, inputs):
        """Return the pool, base order first."""
        keyed, pool = KeyedFamilies(inputs["spec"]), list(inputs["base"])
        keyed.refuse_dropped(pool, POOL_WHERE)
        return pool + [n for n in keyed.names() if n not in pool]


class KeyedCap(Hook):
    """The candidate cap grown by the keyed families the base pool did not count.

    Parameters
    ----------
    None
        Inputs ``base`` (the typed cap), ``pool`` (the typed pool) and ``spec``.

    Examples
    --------
    Two pool families plus the incumbent, one more keyed family::

        KeyedCap().apply({"base": 3, "pool": ["a", "b"], "spec": spec_with_keyed_fam})
        # -> 4
    """

    params = ("base", "pool", "spec")

    def apply(self, inputs):
        """Return the cap plus the number of keyed families outside the typed pool."""
        keyed, pool = KeyedFamilies(inputs["spec"]), set(inputs["pool"])
        keyed.refuse_dropped(pool, POOL_WHERE)
        return inputs["base"] + sum(n not in pool for n in keyed.names())


class FeatureOrder(Hook):
    """One lane's ordered feature list: a shared order, the lane's task feature, the declared rest.

    Positions are what a model's ``feature_indices`` mean, so an ``order`` with ``names`` is
    kept as written, with its ``slot`` entry replaced by the lane's task feature. Every
    declared name the list lacks (core names, the task feature, availability fields, keyed
    families' features) is appended, never inserted. With no ``names`` the order is the core
    names, the task feature, then every family's fields in declaration order.

    Parameters
    ----------
    None
        Inputs ``order`` (optional ``{"names": [...], "slot": str}``), ``task`` (lane ->
        task feature), ``lane``, ``spec`` (the families block), ``start`` (the starting
        set) and ``labels`` (its field names).

    Raises
    ------
    WorkflowError
        ``task`` has no entry for the lane.

    Examples
    --------
    A shared list with a slot for the task feature::

        FeatureOrder().apply({"order": {"slot": "@t", "names": ["b", "@t"]}, "task": {"T": "is_T"},
                              "lane": "T", "start": {"names": ["c"]},
                              "labels": {"names": "names"}, "spec": spec})
        # -> ['b', 'is_T', 'c', ...every availability field, then the keyed ones]
    """

    params = ("order", "task", "lane", "spec", "start", "labels")

    def apply(self, inputs):
        """Return the lane's feature order."""
        keyed = KeyedFamilies(inputs["spec"])
        keyed.refuse_dropped(inputs["start"].get(inputs["labels"].get("families")), CORE_WHERE)
        order = inputs.get("order") or {}
        task = self._task(inputs)
        slot = order.get("slot")
        names = [task if name == slot else name for name in order.get("names") or ()]
        wanted = list(inputs["start"][inputs["labels"]["names"]])
        wanted += [task] if task else []
        for contract in keyed.availability().values():
            wanted += contract["fields"]
        wanted += [f for n in keyed.names() for f in keyed.features(n)]
        return names + [f for f in dict.fromkeys(wanted) if f not in names]

    @staticmethod
    def _task(inputs):
        """Return the lane's task feature (None when no ``task`` mapping is given)."""
        task = inputs.get("task")
        if not task:
            return None
        if inputs["lane"] not in task:
            raise WorkflowError(f"feature order: no task feature for lane {inputs['lane']!r}")
        return task[inputs["lane"]]


class HorizonLimits(Hook):
    """The limits block with its cell table derived from the horizon and ``bound`` checked.

    The expected (role, lane) cells hold exactly the horizon, so they are derived rather than
    typed beside it. A table the limits declare anyway must equal the derived one.

    Parameters
    ----------
    None
        Inputs ``limits`` (dict), ``horizon`` (int), ``lane`` (str) and ``contract``: its
        ``roles`` (list of the role keys), ``cells`` (the limits key holding the table) and
        ``bound`` (the limits key whose value must be at least the horizon).

    Raises
    ------
    WorkflowError
        The bound is missing, not an integer or below the horizon, or a declared table
        differs from the derived one.

    Examples
    --------
    Cells for a horizon of 7::

        HorizonLimits().apply({"limits": {"max_dte": 45}, "horizon": 7, "lane": "T",
                               "contract": {"roles": ["dev"], "cells": "cells", "bound": "max_dte"}})
        # -> {'max_dte': 45, 'cells': {'dev': {'T': [7]}}}
    """

    params = ("limits", "horizon", "lane", "contract")

    def apply(self, inputs):
        """Return a copy of the limits with the derived cell table."""
        limits, horizon = copy.deepcopy(inputs["limits"]), inputs["horizon"]
        roles, cells, key = (inputs["contract"][k] for k in ("roles", "cells", "bound"))
        bound = limits.get(key)
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < horizon:
            raise WorkflowError(f"{key} {bound!r} must be an integer of at least the horizon {horizon}")
        derived = {role: {inputs["lane"]: [horizon]} for role in roles}
        declared = limits.get(cells)
        if declared is not None and declared != derived:
            raise WorkflowError(f"{cells} {declared!r} differs from the horizon {horizon} cells {derived!r}")
        limits[cells] = derived
        return limits


class MarketSeries(Hook):
    """The market series without the one that repeats the lane's own volatility index.

    A series whose symbol equals the lane's own index is the same column twice. It is dropped
    only when ``drop`` is true, and refused when a family field, a family condition or the
    feature order still names it (the series' own column or one of its ``_`` companions).

    Parameters
    ----------
    None
        Inputs ``series`` (name -> {symbol, ...}), ``own`` (lane -> index symbol), ``lane``,
        ``drop`` (bool), ``spec`` (the families block) and ``order`` (the lane's feature order).

    Raises
    ------
    WorkflowError
        A series to drop is still required.

    Examples
    --------
    Drop the duplicate of the lane's own index::

        MarketSeries().apply({"series": {"m_x": {"symbol": "XV"}}, "own": {"T": "XV"},
                              "lane": "T", "drop": True, "spec": {"availability": {}},
                              "order": []})
        # -> {}
    """

    params = ("series", "own", "lane", "drop", "spec", "order")

    def apply(self, inputs):
        """Return the series, minus the lane's own-index duplicates when ``drop``."""
        series = copy.deepcopy(inputs["series"])
        if not inputs.get("drop"):
            return series
        own = (inputs.get("own") or {}).get(inputs["lane"])
        for name in [n for n, item in series.items() if item["symbol"] == own]:
            self._refuse_if_required(name, inputs)
            del series[name]
        return series

    @staticmethod
    def _refuse_if_required(name, inputs):
        """Raise when a family or the feature order still names the series."""
        def of_series(field):
            return field == name or str(field).startswith(name + "_")

        for family, contract in KeyedFamilies(inputs["spec"]).availability().items():
            used = list(contract["fields"]) + [q["field"] for q in contract.get("require") or ()]
            if any(of_series(f) for f in used):
                raise WorkflowError(f"market series {name} duplicates the lane's own index but family {family} requires it")
        if any(of_series(f) for f in inputs["order"]):
            raise WorkflowError(f"market series {name} duplicates the lane's own index but the feature order names it")


class ReferenceIndices(Hook):
    """The reference-model block with feature names resolved to positions in the lane's order.

    The block's ``resolve`` object maps a name param (a feature name, or a list of them) to
    the index param the model class takes; the hook swaps each, in place, using the
    :class:`NamesToIndices` strategy, and drops ``resolve`` from the result. Only the
    models ``model`` and ``comparison`` name are resolved and kept (declaration order);
    every model not named is REMOVED from the result, so one a lane does not use cannot
    refuse it. Naming an undeclared model is refused.

    Parameters
    ----------
    None
        Inputs ``references`` (the block, with ``resolve`` and ``models``) and ``order`` (the
        lane's ordered feature list).

    Raises
    ------
    WorkflowError
        A name is not in the order, or a model gives both the name and the index param.

    Examples
    --------
    One name and one list::

        ReferenceIndices().apply({"order": ["a", "b", "c"],
            "references": {"resolve": {"at": "at_index", "set": "set_indices"},
                           "models": {"m": {"params": {"at": "b", "set": ["c", "a"]}}}}})
        # -> {'models': {'m': {'params': {'at_index': 1, 'set_indices': [2, 0]}}}}
    """

    params = ("references", "order")

    def apply(self, inputs):
        """Return the block with every declared name param replaced by its position(s)."""
        refs = copy.deepcopy(inputs["references"])
        resolve = {k: v for k, v in (refs.pop("resolve", None) or {}).items() if k != NOTES_KEY}
        models = refs.get("models") or {}
        named = self._named(refs, models)
        refs["models"] = {m: spec for m, spec in models.items() if m in named}
        for model, spec in refs["models"].items():
            spec["params"] = self._resolved(model, spec.get("params") or {}, resolve, inputs["order"])
        return refs

    @staticmethod
    def _named(refs, models):
        """Return the set of model names ``model`` and ``comparison`` use, each declared."""
        comparison = refs.get(COMPARISON_KEY) or []
        if not isinstance(comparison, list) or not all(isinstance(n, str) for n in comparison):
            raise WorkflowError("references comparison must be a list of model names")
        wanted = [refs.get(MODEL_KEY)] + comparison
        wanted = [name for name in wanted if name is not None]
        missing = [name for name in wanted if name not in models]
        if missing:
            raise WorkflowError(f"reference models {missing} are named but not declared in models")
        return set(wanted)

    @staticmethod
    def _resolved(model, params, resolve, order):
        """Return one model's params with the name params swapped for index params."""
        out = {}
        for key, value in params.items():
            if key not in resolve:
                out[key] = value
                continue
            if resolve[key] in params:
                raise WorkflowError(f"reference model {model} gives both {key} and {resolve[key]}")
            names = [value] if isinstance(value, str) else value
            found = NamesToIndices().apply({"names": names, "order": order})
            out[resolve[key]] = found[0] if isinstance(value, str) else found
        return out


class AgreeingBlock(Hook):
    """A study block with its notes removed, checked against the source it shares keys with.

    A template can pass a whole args block to a study document that refuses unknown keys, so
    prose must not travel with it. Every key the block shares with ``with`` must hold the same
    value there (one fact typed twice, refused when an overlay lets the copies drift). A
    None block stays None, so a nullable template key can leave it out.

    Parameters
    ----------
    None
        Inputs ``block`` (an object or None) and ``with`` (an object holding the source's values).

    Raises
    ------
    WorkflowError
        ``block`` is not an object or None, or a shared key differs.

    Examples
    --------
    Strip notes and check one shared key::

        AgreeingBlock().apply({"block": {"root": 1, "notes": "x"}, "with": {"root": 1}})
        # -> {'root': 1}
    """

    params = ("block", "with")

    def apply(self, inputs):
        """Return the notes-free copy of ``block``, or None."""
        block, other = inputs["block"], inputs["with"]
        if block is None:
            return None
        if not isinstance(block, dict) or not isinstance(other, dict):
            raise WorkflowError("agreeing block: block and with must be objects")
        for key in sorted(set(block) & set(other) - {NOTES_KEY}):
            if block[key] != other[key]:
                raise WorkflowError(
                    f"agreeing block: {key!r} is {block[key]!r} here but {other[key]!r} in its source")
        return {k: copy.deepcopy(v) for k, v in block.items() if k != NOTES_KEY}


# -- stop rule --------------------------------------------------------------------


class NoGainRule(StopRule):
    """Stop a forward loop when the latest round changed nothing, or nothing is left.

    Parameters
    ----------
    None

    Examples
    --------
    Stops after a round that did not improve::

        NoGainRule().verdict([], [RoundResult(True), RoundResult(False)])
        # -> True
    """

    def stop(self, history):
        """Never stops on group hashes alone; the round records decide."""
        return False

    def verdict(self, history, rounds):
        """Return True when the latest round was not an improvement, or exhausted."""
        if not rounds:
            return False
        last = rounds[-1]
        return not last.improved or last.exhausted


register_hook("forward_candidates", ForwardCandidates())
register_hook("zoo_candidates", ZooCandidates())
register_hook("grid_candidates", GridCandidates())
register_hook("declared_sequence", DeclaredSequence())
register_hook("forward_collector", ForwardCollector())
register_hook("zoo_collector", ZooCollector())
register_hook("grid_collector", GridCollector())
register_hook("families_spec", FamiliesSpec())
register_hook("family_contracts", FamilyContracts())
register_hook("reader_options", ReaderOptions())
register_hook("keyed_pool", KeyedPool())
register_hook("keyed_cap", KeyedCap())
register_hook("feature_order", FeatureOrder())
register_hook("horizon_limits", HorizonLimits())
register_hook("market_series", MarketSeries())
register_hook("reference_indices", ReferenceIndices())
register_hook("agreeing_block", AgreeingBlock())
register_stop_rule("no_gain", NoGainRule())
