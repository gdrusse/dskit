"""ADR-0227: run one manifest as a chain of pipeline steps.

A project's study is several neutral pipeline documents run one after another,
each reading the previous one's outputs. This module is the single entry point
for that chain: the manifest names every input once (``args``), says where every
step writes (``layout``), which neutral template and command a step uses
(``registry``) and how steps feed each other (``steps``). The runner validates
the whole manifest before anything runs, expands each step's template into a
concrete config, runs the registry command, checks the declared outputs exist
and records hashes in a ledger so an unchanged step is skipped on a re-run.

Placeholders. A template is an ordinary pipeline or study document in which every
project-varying value is ``${name.path}`` (``name`` is a key of the step's ``in``
map, ``path`` descends dicts). A string that is exactly one placeholder keeps the
value's JSON type; inside a longer string the value is interpolated as text;
``$$`` escapes a dollar; the pipeline's own brace-free references are untouched;
``notes`` keys are copied verbatim.

Hooks and stop rules are named strategy objects held in registries, so a project
adds one by subclassing and registering it, never by editing a branch here.
Stdlib only and domain-neutral: the child supplies the manifest and templates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod

__all__ = [
    "WorkflowError",
    "load_json",
    "load_overlays",
    "Hook",
    "MissingInput",
    "NamesToIndices",
    "Workflow",
    "HOOKS",
    "register_hook",
    "StopRule",
    "UnchangedRule",
    "STOP_RULES",
    "register_stop_rule",
    "expand",
    "placeholder_names",
    "path_hash",
    "validate_manifest",
    "lane_flows",
    "run_workflow",
]

LEDGER_NAME = "workflow.json"
CONFIG_SUFFIX = ".json"
HASH_ALGORITHM = "sha256"
ENCODING = "utf-8"
PAIR = 2
REF_PARTS = 3
ARGS_ROOT = "args"
OUT_SEGMENT = "out"
ROOT_VAR = "W"
ROUND_VAR = "round"
LAYOUT_PREFIX = "L"
DIR_KEY = "dir"
NOTES_KEY = "notes"
HOOK_KEY = "hook"
RULE_KEY = "rule"
JOIN_TEXT = "_"
NULLABLE_SUFFIX = "?"
DEFERRED_TEXT = "?"
EXIT_OK = 0
EXIT_ERROR = 1
READ_CHUNK = 1 << 20
MIN_ROUNDS = 1
PIN_SEGMENT = "pin"
LANE_ROOT = "lane"
LANE_TOKEN = "$lane"
LANE_SEP = "@"
LAYOUT_ROOT = "L"
PIN_PATH_KEY = "path"
PIN_HASH_KEY = "sha256"

ENV_KEY = "env"
TOP_KEYS = {"args", "layout", "registry", "steps", "lanes", ENV_KEY, NOTES_KEY}
STEP_KEYS = {"registry", "in", "out", "loop", "stages", "collect", NOTES_KEY}
SELF_STEP = "self"
ROLE_VALUE = "value"
ROLE_STAGES = "stages"
ROLE_COLLECT = "collect"
BEFORE_KEY = "before"
AFTER_KEY = "after"
FILES_KEY = "files"
PHASE_MAIN = "main"
PHASE_KEYS = (BEFORE_KEY, AFTER_KEY)
ENTRY_KEYS = {
    "template", "command", "extends", "pins", FILES_KEY, *PHASE_KEYS, NOTES_KEY,
}
LANES_KEYS = {"key", "keyed", NOTES_KEY}
LANE_PATTERN_KEY = "*"
LANE_PATTERN_FIELDS = ("lane", "lane_lower")
PIN_KEYS = {"file", "sha256", "values", NOTES_KEY}
PIN_VALUE_KEYS = {"from", "path"}
PIN_RESERVED = {PIN_PATH_KEY, PIN_HASH_KEY}
LOOP_KEYS = {"until", "max_rounds", "group", NOTES_KEY}
COMMAND_FIXED = {"python", "config", "dir"}
HOOK_FIXED = {"python", "dir"}
FILE_SEGMENT = "file"
IN_PREFIX = "in."

_PLACEHOLDER = re.compile(r"\$\$|\$\{([A-Za-z_]\w*(?:\.\w+)*)\}")
_PATTERN_VAR = re.compile(r"\{([^{}]+)\}")
_UNFILLED = re.compile(r"^<[^<>]+>$")
_NAME = re.compile(r"^[A-Za-z_]\w*$")


class WorkflowError(Exception):
    """A manifest, template or run problem; carries every problem found.

    Parameters
    ----------
    problems : str or sequence of str
        One message or all the messages gathered.

    Examples
    --------
    Raise with two problems and read them back::

        err = WorkflowError(["no registry entry", "dangling ref"])
        err.problems
        # -> ['no registry entry', 'dangling ref']
    """

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


class _Deferred:
    """A value that exists only once an upstream file does (plan mode)."""


DEFERRED = _Deferred()


# -- placeholder expansion ----------------------------------------------------


def _text(value):
    """Render a value as text for interpolation or a path pattern."""
    if isinstance(value, _Deferred):
        return DEFERRED_TEXT
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return JOIN_TEXT.join(_text(item) for item in value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _descend(values, dotted):
    """Walk ``name.path`` through the values dict, raising WorkflowError."""
    name, *path = dotted.split(".")
    if name not in values:
        raise WorkflowError(f"placeholder ${{{dotted}}} has no value")
    node = values[name]
    for part in path:
        if isinstance(node, _Deferred):
            return node
        if not isinstance(node, dict) or part not in node:
            raise WorkflowError(f"placeholder ${{{dotted}}} has no value")
        node = node[part]
    return node


def _expand_string(text, values):
    whole = _PLACEHOLDER.fullmatch(text)
    if whole and whole.group(1):
        return copy.deepcopy(_descend(values, whole.group(1)))

    def replace(match):
        if not match.group(1):
            return "$"
        return _text(_descend(values, match.group(1)))

    return _PLACEHOLDER.sub(replace, text)


def expand(doc, values):
    """Substitute ``${name.path}`` placeholders in a template document.

    Parameters
    ----------
    doc : object
        A JSON-shaped template (dict, list, str, scalar).
    values : dict
        Placeholder roots to values.

    Returns
    -------
    object
        A new document. A string that is exactly one placeholder takes the
        value's JSON type; others interpolate text; ``$$`` becomes ``$``;
        ``notes`` keys are copied verbatim. A key ending in ``?`` is optional:
        it is written without the ``?`` and left out when its expanded value
        is None, so a null arg adds nothing (and moves no hash).

    Raises
    ------
    WorkflowError
        A placeholder names a root or path with no value.

    Examples
    --------
    Keep a list's type and interpolate a name::

        expand({"a": "${T}", "b": "x-${k}"}, {"T": [1, 2], "k": 7})
        # -> {'a': [1, 2], 'b': 'x-7'}

    Leave out an optional key whose value is null::

        expand({"a?": "${n}", "b?": "${k}"}, {"n": None, "k": 7})
        # -> {'b': 7}
    """
    if isinstance(doc, str):
        return _expand_string(doc, values)
    if isinstance(doc, list):
        return [expand(item, values) for item in doc]
    if isinstance(doc, dict):
        return _expand_dict(doc, values)
    return doc


def _expand_dict(doc, values):
    """Expand a mapping, dropping each optional key whose value comes out None."""
    out = {}
    for key, val in doc.items():
        if key == NOTES_KEY:
            out[key] = copy.deepcopy(val)
            continue
        nullable = key.endswith(NULLABLE_SUFFIX)
        name = key.removesuffix(NULLABLE_SUFFIX) if nullable else key
        if nullable and (not name or name in doc):
            raise WorkflowError(
                f"nullable key {key!r}: needs a name before the marker and no "
                f"plain twin {name!r} beside it (both would claim one key)"
            )
        found = expand(val, values)
        if not (nullable and found is None):
            out[name] = found
    return out


def placeholder_names(doc):
    """Return the placeholder roots a template uses, ignoring ``notes``.

    Parameters
    ----------
    doc : object
        A JSON-shaped template.

    Returns
    -------
    set of str
        The ``name`` part of every ``${name.path}``.
    """
    found = set()
    if isinstance(doc, str):
        for match in _PLACEHOLDER.finditer(doc):
            if match.group(1):
                found.add(match.group(1).split(".")[0])
    elif isinstance(doc, list):
        for item in doc:
            found |= placeholder_names(item)
    elif isinstance(doc, dict):
        for key, val in doc.items():
            if key != NOTES_KEY:
                found |= placeholder_names(val)
    return found


# -- hooks and stop rules -----------------------------------------------------


class Hook(ABC):
    """A named transform applied to resolved step inputs.

    Parameters
    ----------
    None
        Subclasses set ``params`` (the allowed input names) and implement
        :meth:`apply`.

    Examples
    --------
    Define and register a hook::

        class Twice(Hook):
            params = ("v",)

            def apply(self, inputs):
                return inputs["v"] * 2

        register_hook("twice", Twice())
    """

    params = ()
    #: Names the runner injects at apply time (``round``, and for the
    #: ``stages``/``collect`` roles ``config``, ``outs``, ``dir``); never user-set.
    runtime = ()
    #: Where a step may use it: an ``in`` value, its ``stages`` or its ``collect``.
    role = ROLE_VALUE

    @abstractmethod
    def apply(self, inputs):
        """Return the hook's value from its resolved inputs dict."""


class NamesToIndices(Hook):
    """Map names to positions against one ordered list.

    Parameters
    ----------
    None
        Inputs at apply time: ``names`` (a list, or the path of a JSON file
        holding one) and ``order`` (the ordered list of all names).

    Examples
    --------
    Positions of two names in an ordered list::

        hook = NamesToIndices()
        hook.apply({"names": ["c", "a"], "order": ["a", "b", "c"]})
        # -> [2, 0]
    """

    params = ("names", "order")

    def apply(self, inputs):
        """Return the index in ``order`` of each name, in the given order."""
        names = self._names(inputs["names"])
        order = list(inputs["order"])
        missing = [name for name in names if name not in order]
        if missing:
            raise WorkflowError(f"names not in the ordered list: {missing}")
        return [order.index(name) for name in names]

    def invert(self, indices, order):
        """Return the names at ``indices`` in ``order`` (the round trip)."""
        return [order[index] for index in indices]

    def _names(self, names):
        """Accept a list, or load one from a JSON file path."""
        if isinstance(names, _Deferred):
            raise _MissingFile("names")
        if isinstance(names, str):
            if not os.path.isfile(names):
                raise _MissingFile(names)
            with open(names, encoding=ENCODING) as handle:
                names = json.load(handle)
        return list(names)


class _MissingFile(WorkflowError):
    """A hook input names a file that does not exist yet."""


MissingInput = _MissingFile


HOOKS = {}


def register_hook(name, hook):
    """Register a hook under ``name`` for use in step ``in`` maps.

    Parameters
    ----------
    name : str
        The ``hook`` value a step uses.
    hook : Hook
        The strategy instance.

    Raises
    ------
    TypeError
        ``hook`` is not a Hook.
    """
    if not isinstance(hook, Hook):
        raise TypeError("hook must be a Hook instance")
    HOOKS[name] = hook


class StopRule(ABC):
    """Decides, from the group output's hash per round, whether a loop stops.

    Parameters
    ----------
    None
        Subclasses implement :meth:`stop`.

    Examples
    --------
    A rule that never stops early (the round cap ends the loop)::

        class Never(StopRule):
            def stop(self, history):
                return False

        register_stop_rule("never", Never())
    """

    @abstractmethod
    def stop(self, history):
        """Return True when the loop should stop after the latest round."""

    def verdict(self, history, rounds):
        """Return True to stop, given the group hashes and each round's collected record.

        The default ignores ``rounds`` and defers to :meth:`stop`; a rule that
        judges what a round produced overrides this instead.
        """
        return self.stop(history)


class UnchangedRule(StopRule):
    """Stop when the group output equals the previous round's.

    Parameters
    ----------
    None

    Examples
    --------
    Stops once two consecutive hashes agree::

        UnchangedRule().stop(["a", "b", "b"])
        # -> True
    """

    def stop(self, history):
        """Return True when the last two history entries are equal."""
        return len(history) >= PAIR and history[-1] == history[-2]


STOP_RULES = {}


def register_stop_rule(name, rule):
    """Register a stop rule under ``name`` for use in a step's ``loop.until``.

    Parameters
    ----------
    name : str
        The ``rule`` value a loop uses.
    rule : StopRule
        The strategy instance.

    Raises
    ------
    TypeError
        ``rule`` is not a StopRule.
    """
    if not isinstance(rule, StopRule):
        raise TypeError("rule must be a StopRule instance")
    STOP_RULES[name] = rule


register_hook("names_to_indices", NamesToIndices())
register_stop_rule("unchanged", UnchangedRule())


# -- references ---------------------------------------------------------------


def _parse_ref(text):
    """Return ('args', path) or ('out', step, name) for a ref string, else None."""
    if not isinstance(text, str) or not text.startswith("$"):
        return None
    parts = text[1:].split(".")
    if text == LANE_TOKEN:
        return (LANE_ROOT,)
    if parts[0] == ARGS_ROOT and len(parts) > 1 and all(parts[1:]):
        return (ARGS_ROOT, parts[1:])
    if len(parts) == REF_PARTS and parts[1] == OUT_SEGMENT and all(parts):
        return (OUT_SEGMENT, parts[0], parts[2])
    if len(parts) == REF_PARTS and parts[1] == PIN_SEGMENT and all(parts):
        return (PIN_SEGMENT, parts[0], parts[2])
    return None


def _walk_args(args, path, lane=None):
    node = args
    for part in path:
        if part == LANE_TOKEN:
            if lane is None:
                raise WorkflowError(f"reference to {LANE_TOKEN} in a manifest with no lanes")
            part = lane
        if not isinstance(node, dict) or part not in node:
            raise WorkflowError(f"dangling reference to args {'.'.join(path)}")
        node = node[part]
    return node


def _out_refs(value, prefix, kinds=(OUT_SEGMENT,)):
    """Yield (label, step, name) for each ref of the given kinds inside an in-value."""
    ref = _parse_ref(value)
    if ref and ref[0] in kinds:
        if ref[1] != SELF_STEP:
            yield prefix, ref[1], ref[2]
    elif isinstance(value, dict):
        for key, sub in value.items():
            if key not in (HOOK_KEY, NOTES_KEY):
                yield from _out_refs(sub, f"{prefix}.{key}", kinds)


def _first_record(path):
    """Return the object a JSON file holds, or the first record of a JSON-lines file."""
    with open(path, encoding=ENCODING) as handle:
        text = handle.read()
    try:
        found = json.loads(text)
    except ValueError:
        lines = [line for line in text.splitlines() if line.strip()]
        found = json.loads(lines[0]) if lines else None
    if isinstance(found, list):
        found = found[0] if found else None
    return found


def _merge(base, overlay):
    merged = copy.deepcopy(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def _strip_notes(doc):
    if isinstance(doc, dict):
        return {k: _strip_notes(v) for k, v in doc.items() if k != NOTES_KEY}
    if isinstance(doc, list):
        return [_strip_notes(v) for v in doc]
    return doc


def _digest(data):
    return hashlib.new(HASH_ALGORITHM, data).hexdigest()


def _config_hash(config):
    canon = json.dumps(_strip_notes(config), sort_keys=True, separators=(",", ":"))
    return _digest(canon.encode(ENCODING))


def _file_hash(path):
    hasher = hashlib.new(HASH_ALGORITHM)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def path_hash(path):
    """Content hash of a file or directory tree; None when absent."""
    if os.path.isfile(path):
        return _file_hash(path)
    if not os.path.isdir(path):
        return None
    parts = []
    for base, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            parts.append(f"{os.path.relpath(full, path)}:{_file_hash(full)}")
    return _digest("\n".join(parts).encode(ENCODING))


# -- the workflow -------------------------------------------------------------


class Workflow:
    """A validated manifest: resolves inputs, layout paths and registry entries.

    Parameters
    ----------
    manifest : dict
        The manifest document.
    base_dir : str
        Directory that registry template paths are relative to.
    args : dict, optional
        An overlay merged over ``manifest["args"]``.
    lane : str, optional
        The lane this workflow runs (a value of the ``lanes.key`` arg); the
        ``lanes.keyed`` args are narrowed to it and ``$lane`` resolves to it.

    Examples
    --------
    Build and validate a one-step workflow::

        flow = Workflow(
            {"args": {"t": "a"}, "layout": {"s": {"dir": "{T}", "o": "{T}/o"}},
             "registry": {"r": {"template": "t.json", "command": "{python} {config}",
                                "extends": None}},
             "steps": {"s": {"registry": "r", "in": {"T": "$args.t"},
                             "out": ["o"]}}},
            base_dir=".")
        flow.check()
    """

    def __init__(self, manifest, base_dir, args=None, lane=None):
        self.manifest = manifest if isinstance(manifest, dict) else {}
        self.round = 0
        self.base_dir = base_dir
        merged = _merge(self.manifest.get("args") or {}, args or {})
        self.all_args = merged if isinstance(merged, dict) else {}
        self._expand_lane_patterns()
        self.lane = lane
        self.args = self._narrowed() if lane is not None else self.all_args
        self.layout = self._mapping("layout")
        self.registry = self._mapping("registry")
        self.steps = self._mapping("steps")
        self.templates = {}
        self.ledger = None

    def _mapping(self, key):
        value = self.manifest.get(key)
        return value if isinstance(value, dict) else {}

    # -- lanes --

    def lane_spec(self):
        """Return the manifest's ``lanes`` object, or None when it declares none."""
        spec = self.manifest.get("lanes")
        return spec if isinstance(spec, dict) else None

    def lane_values(self):
        """Return the lane values (the list at ``lanes.key``), or [] without lanes."""
        spec = self.lane_spec()
        values = self.all_args.get(spec.get("key")) if spec else None
        return list(values) if isinstance(values, list) else []

    def step_key(self, name):
        """Return the ledger key of a step: the name, or ``name@lane`` in a lane."""
        return name if self.lane is None else f"{name}{LANE_SEP}{self.lane}"

    def _expand_lane_patterns(self):
        """Fill each ``lanes.keyed`` object's ``*`` pattern into one entry per lane, then drop it.

        A string pattern is formatted with ``{lane}`` and ``{lane_lower}``; any other value is
        copied. An explicit entry for a lane wins over the pattern.
        """
        spec = self.lane_spec() or {}
        values = self.lane_values()
        for dotted in spec.get("keyed") or []:
            try:
                node = _walk_args(self.all_args, str(dotted).split("."))
            except WorkflowError:
                continue
            if not isinstance(node, dict) or LANE_PATTERN_KEY not in node:
                continue
            pattern = node.pop(LANE_PATTERN_KEY)
            folded = {str(lane).lower(): lane for lane in values}
            strays = sorted(str(k) for k in node
                            if k not in values and str(k).lower() in folded)
            if strays:
                raise WorkflowError(
                    f"lanes: keyed {dotted} has entries that miss a lane only by case "
                    f"beside its {LANE_PATTERN_KEY!r} pattern: {strays}")
            for lane in values:
                if lane not in node:
                    node[lane] = self._from_pattern(dotted, pattern, lane)

    @staticmethod
    def _from_pattern(dotted, pattern, lane):
        """Return the pattern's value for one lane, refusing an unknown placeholder."""
        if not isinstance(pattern, str):
            return copy.deepcopy(pattern)
        fields = {"lane": lane, "lane_lower": lane.lower()}
        try:
            return pattern.format(**fields)
        except (KeyError, IndexError, ValueError) as err:
            raise WorkflowError(
                f"lanes: keyed {dotted} pattern {pattern!r} may use only "
                f"{list(LANE_PATTERN_FIELDS)}: {err!r}") from err

    def _narrowed(self):
        """Return the args with every ``lanes.keyed`` object cut to this lane's key."""
        args = copy.deepcopy(self.all_args)
        spec = self.lane_spec() or {}
        for dotted in spec.get("keyed") or []:
            *parents, leaf = str(dotted).split(".")
            node = args
            for part in parents:
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, dict) and isinstance(node.get(leaf), dict):
                node[leaf] = {
                    k: v for k, v in node[leaf].items() if k == self.lane
                }
        return args

    def _lanes_problems(self):
        """Return problems with ``lanes``; args objects keyed by lane values must be in ``keyed``."""
        spec = self.manifest.get("lanes")
        if spec is None:
            return []
        if not isinstance(spec, dict):
            return ["lanes must be an object"]
        problems = [f"lanes: unknown param {k}" for k in spec if k not in LANES_KEYS]
        values = self.all_args.get(spec.get("key"))
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, str) and v for v in values)
            or len(set(values)) != len(values)
        ):
            return problems + [
                "lanes: key must name an args list of unique non-empty strings"
            ]
        keyed = spec.get("keyed", [])
        if not isinstance(keyed, list):
            return problems + ["lanes: keyed must be a list of args paths"]
        for dotted in keyed:
            problems += self._keyed_problems(dotted, values)
        return problems + self._unlisted_problems([str(d) for d in keyed], values)

    def _unlisted_problems(self, keyed, values):
        """Name every args object keyed by lane values that ``lanes.keyed`` does not list."""
        found = []

        def walk(node, path):
            for key, child in node.items():
                here = path + [key]
                dotted = ".".join(here)
                if dotted in keyed or not isinstance(child, dict):
                    continue
                if set(child) & set(values):
                    found.append(dotted)
                else:
                    walk(child, here)

        walk(self.all_args, [])
        return [
            f"lanes: args.{d} is keyed by lane values but is not listed in lanes.keyed"
            for d in found
        ]

    def _keyed_problems(self, dotted, values):
        try:
            node = _walk_args(self.all_args, str(dotted).split("."))
        except WorkflowError as err:
            return [f"lanes: keyed {dotted}: {err}"]
        if not isinstance(node, dict):
            return [f"lanes: keyed {dotted} is not an object"]
        return [
            f"lanes: keyed {dotted} has no entry for lane {v}"
            for v in values
            if v not in node
        ]

    # -- validation --

    def check(self):
        """Validate everything; raise WorkflowError carrying all problems."""
        problems = self.structure_problems()
        if not problems:
            problems = self.template_problems()
        if not problems:
            problems = self.expansion_problems()
        if problems:
            raise WorkflowError(problems)

    def structure_problems(self):
        """Return problems found without reading any template."""
        problems = [
            f"unknown top-level param {key}"
            for key in self.manifest
            if key not in TOP_KEYS
        ]
        for key in ("args", "layout", "registry", "steps"):
            if not isinstance(self.manifest.get(key), dict):
                problems.append(f"{key} must be an object")
        problems += self._lanes_problems()
        problems += self._env_problems()
        problems += self._registry_problems()
        for step in self.steps:
            problems += self._step_problems(step)
            problems += self._layout_problems(step)
        return problems

    def _env_problems(self):
        """Return problems with the top-level ``env`` reference and the object it names."""
        if ENV_KEY not in self.manifest:
            return []
        value = self.manifest[ENV_KEY]
        problems = self._ref_problems(ENV_KEY, value, None)
        ref = _parse_ref(value)
        if problems or ref is None or ref[0] != ARGS_ROOT:
            return problems or [f"{ENV_KEY}: expected an $args reference"]
        node = _walk_args(self.args, ref[1], self._probe_lane())
        if not isinstance(node, dict):
            return [f"{ENV_KEY}: must resolve to an object"]
        return [
            f"{ENV_KEY}: {name!r} must map a non-empty string to a string"
            for name, val in self._env_items(node)
            if not (isinstance(name, str) and name and isinstance(val, str))
        ]

    @staticmethod
    def _env_items(node):
        return [(k, v) for k, v in node.items() if k != NOTES_KEY]

    def env(self):
        """Return the variables the manifest's ``env`` adds to every command (name -> value)."""
        if ENV_KEY not in self.manifest:
            return {}
        ref = _parse_ref(self.manifest[ENV_KEY])
        node = _walk_args(self.args, ref[1], self.lane)
        return dict(self._env_items(node))

    def _registry_problems(self):
        problems = []
        for key, entry in self.registry.items():
            if not isinstance(entry, dict):
                problems.append(f"registry {key} must be an object")
                continue
            problems += [
                f"registry {key}: unknown param {name}"
                for name in entry
                if name not in ENTRY_KEYS
            ]
            try:
                resolved = self.entry(key)
            except WorkflowError as err:
                problems += err.problems
                continue
            for field in ("template", "command"):
                if not isinstance(resolved.get(field), str):
                    problems.append(f"registry {key}: no {field}")
            problems += self._phase_problems(key, resolved)
        return problems

    def _phase_problems(self, key, resolved):
        """Shape problems of a registry entry's ``before``/``after`` lists and ``files``."""
        problems = []
        for field in PHASE_KEYS:
            found = resolved.get(field, [])
            if not isinstance(found, list) or not all(
                isinstance(c, str) and c for c in found
            ):
                problems.append(f"registry {key}: {field} must be a list of command strings")
        files = resolved.get(FILES_KEY, {})
        if not isinstance(files, dict) or not all(
            _NAME.match(str(k)) and isinstance(v, str) and v for k, v in files.items()
        ):
            problems.append(
                f"registry {key}: {FILES_KEY} must map names to template paths"
            )
        return problems

    def entry(self, key, seen=()):
        """Return the registry entry with ``extends`` inheritance applied."""
        if key in seen:
            raise WorkflowError(f"registry extends cycle at {key}")
        entry = self.registry.get(key)
        if not isinstance(entry, dict):
            raise WorkflowError(f"no registry entry {key}")
        parent = entry.get("extends")
        base = self.entry(parent, seen + (key,)) if parent else {}
        merged = {k: v for k, v in base.items() if k != "extends"}
        merged.update({k: v for k, v in entry.items() if k != "extends"})
        return merged

    def _step_problems(self, name):
        step = self.steps[name]
        if not isinstance(step, dict):
            return [f"step {name} must be an object"]
        problems = [
            f"step {name}: unknown param {key}" for key in step if key not in STEP_KEYS
        ]
        if step.get("registry") not in self.registry:
            problems.append(f"step {name}: no registry entry {step.get('registry')}")
        outs = step.get("out")
        if not isinstance(outs, list) or not all(
            isinstance(o, str) and _NAME.match(o) for o in outs
        ):
            problems.append(f"step {name}: out must be a list of names")
            outs = []
        ins = step.get("in")
        if not isinstance(ins, dict):
            return problems + [f"step {name}: in must be an object"]
        for key, value in ins.items():
            problems += self._in_problems(name, key, value)
        if "loop" in step:
            problems += self._loop_problems(name, step["loop"], outs)
        for key, role in (("stages", ROLE_STAGES), ("collect", ROLE_COLLECT)):
            if key in step:
                problems += self._hook_problems(name, key, step[key], role)
        if SELF_STEP in self.steps:
            problems.append(f"step name {SELF_STEP} is reserved for own-output references")
        if LAYOUT_ROOT in ins:
            problems.append(f"step {name}: in key {LAYOUT_ROOT} is reserved for layout paths")
        return problems + self._pin_problems(name, step, outs)

    def pins(self, name):
        """Return the pin declarations a step's registry entry carries."""
        try:
            pins = self.entry(self.steps[name].get("registry")).get("pins")
        except (WorkflowError, AttributeError):
            return {}
        return pins if isinstance(pins, dict) else {}

    def _pin_problems(self, name, step, outs):
        try:
            declared = self.entry(step.get("registry")).get("pins", {})
        except WorkflowError:
            return []
        if not isinstance(declared, dict):
            return [f"step {name}: pins must be an object"]
        problems = []
        for pin, spec in declared.items():
            problems += self._one_pin_problems(f"step {name} pin {pin}", spec, outs)
        return problems

    def _one_pin_problems(self, where, spec, outs):
        if not isinstance(spec, dict):
            return [f"{where} must be an object"]
        problems = [f"{where}: unknown param {k}" for k in spec if k not in PIN_KEYS]
        if spec.get("file") not in outs:
            problems.append(f"{where}: file must name an output of the step")
        if not isinstance(spec.get(PIN_HASH_KEY, True), bool):
            problems.append(f"{where}: {PIN_HASH_KEY} must be true or false")
        values = spec.get("values", {})
        if not isinstance(values, dict):
            return problems + [f"{where}: values must be an object"]
        for key, found in values.items():
            problems += self._pin_value_problems(f"{where} value {key}", key, found, outs)
        return problems

    def _pin_value_problems(self, where, key, found, outs):
        if key in PIN_RESERVED or not _NAME.match(key):
            return [f"{where}: name is reserved or not a name"]
        if not isinstance(found, dict) or set(found) != PIN_VALUE_KEYS:
            return [f"{where}: needs exactly {sorted(PIN_VALUE_KEYS)}"]
        problems = []
        if found["from"] not in outs:
            problems.append(f"{where}: from must name an output of the step")
        if not isinstance(found["path"], str) or not all(found["path"].split(".")):
            problems.append(f"{where}: path must be a dotted path")
        return problems

    def _in_problems(self, step, key, value):
        where = f"step {step} in {key}"
        if isinstance(value, dict):
            return self._hook_problems(step, key, value)
        return self._ref_problems(where, value, step)

    def _ref_problems(self, where, value, step):
        if isinstance(value, dict) and HOOK_KEY in value:   # a hook may take a hook's value
            return self._hook_problems(step, where, value)
        ref = _parse_ref(value)
        if ref is None:
            return [f"{where}: literal value, expected a reference"]
        if ref[0] == LANE_ROOT:
            return [] if self.lane_spec() else [f"{where}: {value} but no lanes declared"]
        if ref[0] == ARGS_ROOT:
            try:
                _walk_args(self.args, ref[1], self._probe_lane())
            except WorkflowError as err:
                return [f"{where}: {err}"]
            return []
        kind, source, out = ref
        if source == SELF_STEP:
            own = self.steps[step].get("out") or [] if step in self.steps else []
            ok = kind == OUT_SEGMENT and out in own
            return [] if ok else [f"{where}: {value} is not an output of this step"]
        names = list(self.steps)
        earlier = names[: names.index(step)] if step in names else []
        if source not in earlier:
            return [f"{where}: {value} does not name an earlier step"]
        if kind == PIN_SEGMENT:
            if out not in self.pins(source):
                return [f"{where}: {value} is not a pin of {source}"]
            return []
        listed = self.steps[source].get("out") or []
        if out not in listed:
            return [f"{where}: {value} is not an output of {source}"]
        return []

    def _probe_lane(self):
        """Return the lane to check ``$lane`` path segments with (first value)."""
        if self.lane is not None:
            return self.lane
        values = self.lane_values()
        return values[0] if values else None

    def _hook_problems(self, step, key, value, role=ROLE_VALUE):
        where = f"step {step} in {key}"
        if not isinstance(value, dict):
            return [f"{where}: must be a hook object"]
        hook = HOOKS.get(value.get(HOOK_KEY))
        if hook is None:
            return [f"{where}: unknown hook {value.get(HOOK_KEY)}"]
        if hook.role != role:
            return [f"{where}: hook {value[HOOK_KEY]} cannot be used here ({hook.role})"]
        problems = []
        for sub, inner in value.items():
            if sub == HOOK_KEY or sub == NOTES_KEY:
                continue
            if sub not in hook.params:
                problems.append(f"{where}: unknown param {sub}")
            else:
                problems += self._ref_problems(f"{where}.{sub}", inner, step)
        return problems

    def _loop_problems(self, step, loop, outs):
        where = f"step {step} loop"
        if not isinstance(loop, dict):
            return [f"{where} must be an object"]
        problems = [f"{where}: unknown param {k}" for k in loop if k not in LOOP_KEYS]
        values = {}
        for key in ("until", "max_rounds", "group"):
            found = self._ref_problems(f"{where} {key}", loop.get(key), step)
            problems += found
            if not found:
                values[key] = _walk_args(self.args, _parse_ref(loop[key])[1])
        if problems:
            return problems
        return problems + self._loop_value_problems(where, values, outs)

    def _loop_value_problems(self, where, values, outs):
        problems = []
        until = values["until"]
        if not isinstance(until, dict) or until.get(RULE_KEY) not in STOP_RULES:
            problems.append(f"{where}: until names no registered stop rule")
        elif set(until) - {RULE_KEY, NOTES_KEY}:
            problems.append(f"{where}: until has unknown params")
        rounds = values["max_rounds"]
        if (
            isinstance(rounds, bool)
            or not isinstance(rounds, int)
            or (rounds < MIN_ROUNDS)
        ):
            problems.append(f"{where}: max_rounds must be a positive integer")
        if values["group"] not in outs:
            problems.append(f"{where}: group must name an output of the step")
        return problems

    def _layout_problems(self, step):
        entry = self.layout.get(step)
        if not isinstance(entry, dict) or DIR_KEY not in entry:
            return [f"step {step}: no layout dir"]
        outs = self.steps[step].get("out") if isinstance(self.steps[step], dict) else []
        keys = [DIR_KEY] + [o for o in (outs or []) if isinstance(o, str)]
        extras = [k for k in entry if k not in keys and k != NOTES_KEY]
        problems = []
        for key in keys:
            if key not in entry:
                problems.append(f"step {step}: out {key} has no layout pattern")
            else:
                problems += self._pattern_problems(step, key, entry[key])
        for key in extras:
            problems += self._pattern_problems(step, key, entry[key])
        return problems

    def _pattern_problems(self, step, key, pattern):
        where = f"layout {step}.{key}"
        if not isinstance(pattern, str) or not _PATTERN_VAR.search(pattern):
            return [f"{where}: literal path, expected a pattern with variables"]
        ins = self.steps[step].get("in") if isinstance(self.steps[step], dict) else {}
        problems = []
        for var in _PATTERN_VAR.findall(pattern):
            if var in (ins or {}) or (var == ROUND_VAR and "loop" in self.steps[step]):
                continue
            parts = var.split(".")
            known = (
                len(parts) == REF_PARTS
                and parts[0] == LAYOUT_PREFIX
                and isinstance(self.layout.get(parts[1]), dict)
                and parts[2] in self.layout[parts[1]]
            )
            if not known:
                problems.append(f"{where}: unknown variable {{{var}}}")
        return problems

    def template_problems(self):
        """Read each template and check placeholders against ``in`` and command."""
        problems = []
        for name, step in self.steps.items():
            entry = self.entry(step["registry"])
            doc = self.load_template(entry["template"])
            if isinstance(doc, WorkflowError):
                problems += doc.problems
                continue
            problems += self._usage_problems(name, step, entry, doc)
        return problems

    def load_template(self, relative):
        """Load (and cache) a template; return it or a WorkflowError."""
        path = os.path.join(self.base_dir, relative)
        if path not in self.templates:
            try:
                with open(path, encoding=ENCODING) as handle:
                    self.templates[path] = json.load(handle)
            except (OSError, ValueError) as err:
                self.templates[path] = WorkflowError(f"template {relative}: {err}")
        return self.templates[path]

    def _usage_problems(self, name, step, entry, doc):
        looped = "loop" in step
        roots = set(step["in"]) | {LAYOUT_ROOT} | ({ROUND_VAR} if looped else set())
        problems = []
        fields = []
        for phase, template in self.command_templates(name):
            for field in self.command_fields(template):
                problems += self._field_problems(name, step, field, looped, phase)
                fields.append(field)
        used = placeholder_names(doc)
        for key in self.file_templates(name):
            side = self.load_template(self.file_templates(name)[key])
            if isinstance(side, WorkflowError):
                problems += side.problems
            else:
                used |= placeholder_names(side)
        problems += [
            f"step {name}: placeholder {n} has no value" for n in sorted(used - roots)
        ]
        in_used = {f.split(".")[1] for f in fields if f.startswith("in.")}
        unused = set(step["in"]) - used - in_used - self.layout_vars(name)
        problems += [
            f"step {name}: in key {n} is used by no template, command or layout"
            for n in sorted(unused)
        ]
        return problems

    def command_templates(self, name):
        """Return (phase, command template) for a step: before, the main command, after."""
        entry = self.entry(self.steps[name]["registry"])
        return (
            [(BEFORE_KEY, c) for c in entry.get(BEFORE_KEY) or []]
            + [(PHASE_MAIN, entry["command"])]
            + [(AFTER_KEY, c) for c in entry.get(AFTER_KEY) or []]
        )

    def file_templates(self, name):
        """Return the step's registry ``files``: key -> template path."""
        files = self.entry(self.steps[name]["registry"]).get(FILES_KEY)
        return files if isinstance(files, dict) else {}

    def layout_vars(self, name):
        """Return the variables a step's layout patterns use."""
        entry = self.layout.get(name) or {}
        return {
            var
            for key, pattern in entry.items()
            if key != NOTES_KEY and isinstance(pattern, str)
            for var in _PATTERN_VAR.findall(pattern)
        }

    def command_fields(self, command):
        """Return the ``{field}`` names a command template uses."""
        return _PATTERN_VAR.findall(command)

    def _field_problems(self, name, step, field, looped, phase=PHASE_MAIN):
        kind, _, rest = field.partition(".")
        fixed = COMMAND_FIXED if phase == PHASE_MAIN else HOOK_FIXED
        ok = (
            field in fixed
            or (field == ROUND_VAR and looped and phase == PHASE_MAIN)
            or (kind == "in" and rest.split(".")[0] in step["in"])
            or (kind == OUT_SEGMENT and rest in step["out"])
            or (kind == FILE_SEGMENT and rest in self.file_templates(name))
        )
        return [] if ok else [f"step {name}: command field {{{field}}} is unknown"]

    def expansion_problems(self):
        """Dry-run every expansion (deferring hook inputs not yet on disk)."""
        problems = []
        for name in self.steps:
            try:
                self.expanded(name, strict=False, rnd=0)
                for key in self.file_templates(name):
                    self.expanded_file(name, key, strict=False, rnd=0)
                self._field_value_problems(name)
            except WorkflowError as err:
                problems += [f"step {name}: {p}" for p in err.problems]
        return problems

    def _field_value_problems(self, name):
        """Raise when a command's ``{in.key.path}`` descends where the value has no such path."""
        values = self.values(name, False)
        for _, template in self.command_templates(name):
            for field in self.command_fields(template):
                if field.startswith(IN_PREFIX):
                    _descend(values, field[len(IN_PREFIX):])

    # -- resolution --

    def values(self, name, strict, guard=(), only=None):
        """Resolve a step's ``in`` map (or just the keys in ``only``) to values."""
        out = {}
        for key, value in self.steps[name]["in"].items():
            if only is None or key in only:
                out[key] = self._resolve(value, name, strict, guard)
        return out

    def _resolve(self, value, step, strict, guard):
        ref = _parse_ref(value)
        if ref and ref[0] == LANE_ROOT:
            return self.lane
        if ref and ref[0] == ARGS_ROOT:
            return copy.deepcopy(_walk_args(self.args, ref[1], self.lane))
        if ref and ref[0] == PIN_SEGMENT:
            return self.pin(ref[1], ref[2], strict)
        if ref:
            return self.path(step if ref[1] == SELF_STEP else ref[1], ref[2], guard)
        try:
            return self.run_hook(value, step, strict, guard)
        except _MissingFile:
            if strict:
                raise
            return DEFERRED

    def run_hook(self, value, step, strict=True, guard=(), extra=None):
        """Resolve a hook object's inputs, add the runtime ones it declares, apply it."""
        hook = HOOKS[value[HOOK_KEY]]
        inputs = {
            sub: self._resolve(inner, step, strict, guard)
            for sub, inner in value.items()
            if sub not in (HOOK_KEY, NOTES_KEY)
        }
        known = {ROUND_VAR: self.round, **(extra or {})}
        inputs.update({name: known[name] for name in hook.runtime if name in known})
        return hook.apply(inputs)

    def pin(self, step, name, strict):
        """Return a step's pin object ``{path, sha256, <values>}`` from its output files.

        In plan mode a pin whose file does not exist yet is deferred; when
        running, it must exist and hash exactly as the ledger recorded.
        """
        spec = self.pins(step)[name]
        where = f"pin {step}.{name}"
        target = self.path(step, spec["file"])
        if not os.path.isfile(target):
            if strict:
                raise WorkflowError(f"{where}: {spec['file']} does not exist ({target})")
            return DEFERRED
        digest = _file_hash(target)
        if strict:
            self._check_recorded(where, step, spec["file"], digest)
        found = {PIN_PATH_KEY: target}
        if spec.get(PIN_HASH_KEY, True):
            found[PIN_HASH_KEY] = digest
        for key, source in (spec.get("values") or {}).items():
            found[key] = self._pin_value(where, step, source)
        return found

    def _check_recorded(self, where, step, out, digest):
        """Refuse a pin whose file is not exactly what the ledger recorded."""
        if self.ledger is None:
            return
        entry = self.ledger.get(self.step_key(step)) or {}
        recorded = (entry.get("outputs") or {}).get(out)
        if recorded is None:
            raise WorkflowError(f"{where}: the ledger holds no hash for {step}.{out}")
        if recorded != digest:
            raise WorkflowError(
                f"{where}: {out} hash {digest} differs from the ledger {recorded}"
            )

    def _pin_value(self, where, step, source):
        """Read one dotted path from the first record of a step output."""
        target = self.path(step, source["from"])
        try:
            node = _first_record(target)
        except (OSError, ValueError) as err:
            raise WorkflowError(f"{where}: {source['from']} is unreadable: {err}")
        for part in source["path"].split("."):
            if not isinstance(node, dict) or part not in node:
                raise WorkflowError(
                    f"{where}: {source['from']} has no {source['path']}"
                )
            node = node[part]
        return node

    def path(self, step, key, guard=()):
        """Return the layout path of a step's directory or output."""
        if (step, key) in guard:
            raise WorkflowError(f"layout reference cycle at {step}.{key}")
        guard = guard + ((step, key),)
        pattern = self.layout[step][key]
        values = self.values(step, False, guard, _PATTERN_VAR.findall(pattern))

        def fill(match):
            var = match.group(1)
            if var in values:
                return _text(values[var])
            if var == ROUND_VAR:
                return str(self.round)
            _, other, inner = var.split(".")
            return self.path(other, inner, guard)

        return _PATTERN_VAR.sub(fill, pattern)

    def expanded(self, name, strict, rnd):
        """Return the step's concrete config with its values."""
        entry = self.entry(self.steps[name]["registry"])
        return expand(self.load_template(entry["template"]), self._roots(name, strict, rnd))

    def expanded_file(self, name, key, strict, rnd):
        """Return one registry ``files`` template expanded with the step's values."""
        template = self.load_template(self.file_templates(name)[key])
        if isinstance(template, WorkflowError):
            raise template
        return expand(template, self._roots(name, strict, rnd))

    def _roots(self, name, strict, rnd):
        self.round = rnd
        values = self.values(name, strict)
        if "loop" in self.steps[name]:
            values[ROUND_VAR] = rnd
        values[LAYOUT_ROOT] = self.layout_paths()
        return values

    def config_stem(self, name):
        """Return the file stem of a step's written config: the name, plus ``_lane``."""
        return name if self.lane is None else name + JOIN_TEXT + self.lane

    def file_path(self, name, key):
        """Return where a registry ``files`` entry is written: beside the config."""
        stem = f"{self.config_stem(name)}.{key}{CONFIG_SUFFIX}"
        return os.path.join(self.path(name, DIR_KEY), stem)

    def layout_paths(self):
        """Return step -> key -> resolved path for every layout pattern (the ``L`` root)."""
        return {
            step: {
                key: self.path(step, key)
                for key, pattern in entry.items()
                if key != NOTES_KEY and isinstance(pattern, str)
            }
            for step, entry in self.layout.items()
            if step in self.steps and isinstance(entry, dict)
        }

    def work_root(self):
        """Directory holding the ledger: the first ``W`` value, else the manifest's."""
        for name, step in self.steps.items():
            if ROOT_VAR in step["in"]:
                return _text(self.values(name, False, (), (ROOT_VAR,))[ROOT_VAR])
        return self.base_dir

    def unfilled(self):
        """Return ``step.key`` labels whose resolved value is an unfilled marker."""
        found = []
        for name in self.steps:
            for key, value in self.values(name, False).items():
                if self._has_marker(value):
                    found.append(f"{name}.{key}")
        return found

    def _has_marker(self, value):
        if isinstance(value, str):
            return bool(_UNFILLED.match(value))
        if isinstance(value, dict):
            return any(self._has_marker(v) for v in value.values())
        if isinstance(value, list):
            return any(self._has_marker(v) for v in value)
        return False

    def loop_spec(self, name):
        """Return (rule, max_rounds, group) for a looped step."""
        loop = self.steps[name]["loop"]
        resolved = {
            key: _walk_args(self.args, _parse_ref(loop[key])[1])
            for key in ("until", "max_rounds", "group")
        }
        return (
            STOP_RULES[resolved["until"][RULE_KEY]],
            resolved["max_rounds"],
            resolved["group"],
        )

    def step_refs(self, name):
        """Yield (label, step, out) for every upstream output a step reads.

        A pin counts as the outputs it is computed from: its file and the
        outputs its values come from.
        """
        for key, value in self.steps[name]["in"].items():
            yield from _out_refs(value, key)
            for label, source, pin in _out_refs(value, key, (PIN_SEGMENT,)):
                spec = self.pins(source)[pin]
                yield label, source, spec["file"]
                for sub, found in (spec.get("values") or {}).items():
                    yield f"{label}.{sub}", source, found["from"]

    def input_hashes(self, name):
        """Return label -> content hash of each upstream output the step reads."""
        return {
            label: path_hash(self.path(source, out))
            for label, source, out in self.step_refs(name)
        }


def lane_flows(manifest, base_dir=".", args=None):
    """Validate a manifest once per lane and return one workflow per lane.

    Parameters
    ----------
    manifest : dict
        The manifest document.
    base_dir : str, default "."
        Directory registry template paths are relative to.
    args : dict, optional
        Overlay merged over the manifest's ``args``.

    Returns
    -------
    list of Workflow
        One per lane value, in order; a single lane-less workflow when the
        manifest declares no ``lanes``.

    Raises
    ------
    WorkflowError
        Any refusal, including lanes whose outputs would share a path.

    Examples
    --------
    Two lanes of one chain::

        flows = lane_flows(manifest, "study")
        [flow.lane for flow in flows]
        # -> ['first', 'second']
    """
    probe = Workflow(manifest, base_dir, args)
    problems = probe._lanes_problems()
    if problems or not probe.lane_spec():
        flow = Workflow(manifest, base_dir, args)
        flow.check()
        return [flow]
    flows = [Workflow(manifest, base_dir, args, lane) for lane in probe.lane_values()]
    for flow in flows:
        flow.check()
    clash = _shared_outputs(flows)
    if clash:
        raise WorkflowError(clash)
    return flows


def _shared_outputs(flows):
    """Return problems for outputs that two lanes would write to one path."""
    problems = []
    for name, step in flows[0].steps.items():
        for out in step["out"]:
            paths = [flow.path(name, out) for flow in flows]
            if len(set(paths)) != len(paths):
                problems.append(
                    f"lanes share the output path of {name}.{out}; "
                    "its layout pattern must vary by lane"
                )
    return problems


def validate_manifest(manifest, base_dir=".", args=None):
    """Validate a manifest before anything runs.

    Parameters
    ----------
    manifest : dict
        The manifest document.
    base_dir : str, default "."
        Directory registry template paths are relative to.
    args : dict, optional
        Overlay merged over the manifest's ``args``.

    Returns
    -------
    Workflow
        The validated workflow (the first lane's, when lanes are declared;
        every lane is validated).

    Raises
    ------
    WorkflowError
        Any refusal; ``problems`` lists every one found.
    """
    return lane_flows(manifest, base_dir, args)[0]


# -- execution ----------------------------------------------------------------


class _Ledger:
    """The ``workflow.json`` record of what each step ran with and produced.

    Parameters
    ----------
    root : str
        Directory the ledger file lives in.

    Examples
    --------
    Read and update a ledger::

        ledger = _Ledger("work")
        ledger.record("s1", {"exit_code": 0})
    """

    def __init__(self, root):
        self.path = os.path.join(root, LEDGER_NAME)
        self.data = {"steps": {}}
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding=ENCODING) as handle:
                    self.data = json.load(handle)
            except ValueError as err:
                raise WorkflowError(f"ledger {self.path} unreadable: {err}")

    def get(self, step):
        """Return a step's recorded entry or None."""
        return self.data["steps"].get(step)

    def record(self, step, entry):
        """Store an entry and write the file atomically."""
        self.data["steps"][step] = entry
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding=ENCODING) as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
        os.replace(temp, self.path)


class _Runner:
    """Executes validated steps in order, skipping unchanged ones."""

    def __init__(self, flow, ledger, out):
        self.flow = flow
        self.ledger = ledger
        self.out = out
        flow.ledger = ledger
        self.records = []
        self.commands = []

    def run_step(self, name):
        """Run or skip one step; return its exit code."""
        flow = self.flow
        config = flow.expanded(name, strict=True, rnd=0)
        entry = {
            "registry": flow.steps[name]["registry"],
            "config_hash": _config_hash(config),
            "inputs": flow.input_hashes(name),
        }
        if flow.env():
            entry["env"] = flow.env()
        files = self._file_docs(name)
        if files:
            entry["files"] = {key: _config_hash(doc) for key, doc in files.items()}
        if flow.lane is not None:
            entry["lane"] = flow.lane
        key = flow.step_key(name)
        if self._unchanged(name, entry):
            self.out(f"skip {key} (unchanged)")
            return EXIT_OK
        self.out(f"run {key}")
        code = self._execute(name, entry, files)
        self.ledger.record(key, entry)
        return code

    def _file_docs(self, name):
        flow = self.flow
        return {
            key: flow.expanded_file(name, key, strict=True, rnd=0)
            for key in flow.file_templates(name)
        }

    def _unchanged(self, name, entry):
        prior = self.ledger.get(self.flow.step_key(name))
        if not prior or prior.get("exit_code") != EXIT_OK:
            return False
        same = all(prior.get(k) == entry.get(k) for k in ("config_hash", "inputs", "files", ENV_KEY))
        return same and prior.get("outputs") == self._output_hashes(name)

    def _output_hashes(self, name):
        flow = self.flow
        hashes = {o: path_hash(flow.path(name, o)) for o in flow.steps[name]["out"]}
        return {k: v for k, v in hashes.items() if v is not None}

    def _execute(self, name, entry, files):
        flow = self.flow
        os.makedirs(flow.path(name, DIR_KEY), exist_ok=True)
        for out in flow.steps[name]["out"]:
            parent = os.path.dirname(flow.path(name, out))
            os.makedirs(parent or ".", exist_ok=True)
        self._write_files(name, files)
        self.commands = []
        code = self._phase(name, BEFORE_KEY)
        if code == EXIT_OK:
            code = self._loop(name, entry) if "loop" in flow.steps[name] else self._once(name, 0)
        if code == EXIT_OK:
            code = self._phase(name, AFTER_KEY)
        if self.commands:
            entry["commands"] = self.commands
        entry["exit_code"] = code
        entry["outputs"] = self._output_hashes(name) if code == EXIT_OK else {}
        if code == EXIT_OK and not self._outputs_exist(name):
            entry["exit_code"] = EXIT_ERROR
            return EXIT_ERROR
        return code

    def _write_files(self, name, files):
        """Write each expanded registry ``files`` document beside the step's config."""
        for key, doc in files.items():
            with open(self.flow.file_path(name, key), "w", encoding=ENCODING) as handle:
                json.dump(doc, handle, indent=2)

    def _phase(self, name, phase):
        """Run a step's ``before`` or ``after`` commands in order; stop at the first failure."""
        templates = [t for p, t in self.flow.command_templates(name) if p == phase]
        for template in templates:
            argv = self.command(name, None, 0, template)
            code = self._spawn(phase, argv)
            if code != EXIT_OK:
                return code
        return EXIT_OK

    def _spawn(self, phase, argv):
        """Run one command, record it for the ledger, return its exit code."""
        added = self.flow.env()
        env = {**os.environ, **added} if added else None
        code = subprocess.run(argv, check=False, env=env).returncode
        record = {"phase": phase, "argv": argv, "exit_code": code}
        if added:
            record[ENV_KEY] = added
        self.commands.append(record)
        return code

    def _outputs_exist(self, name):
        flow = self.flow
        missing = [
            o for o in flow.steps[name]["out"] if not os.path.exists(flow.path(name, o))
        ]
        for out in missing:
            self.out(f"step {name}: declared output {out} was not produced")
        return not missing

    def _once(self, name, rnd):
        flow = self.flow
        config = flow.expanded(name, strict=True, rnd=rnd)
        stem = flow.config_stem(name)
        target = os.path.join(flow.path(name, DIR_KEY), stem + CONFIG_SUFFIX)
        with open(target, "w", encoding=ENCODING) as handle:
            json.dump(config, handle, indent=2)
        base = self.command(name, target, rnd)
        for argv in self._invocations(name, base, config):
            code = self._spawn(PHASE_MAIN, argv)
            if code != EXIT_OK:
                return code
        return self._collect(name, config)

    def _invocations(self, name, base, config):
        """Return the argv lists one round runs: the base command, or one per stage."""
        spec = self.flow.steps[name].get("stages")
        if spec is None:
            return [base]
        extra = {"config": config}
        return [base + list(tail) for tail in self.flow.run_hook(spec, name, extra=extra)]

    def _collect(self, name, config):
        """Run the step's collector after a successful round; keep its record."""
        spec = self.flow.steps[name].get("collect")
        if spec is None:
            return EXIT_OK
        flow = self.flow
        extra = {
            "config": config,
            "dir": flow.path(name, DIR_KEY),
            "outs": {o: flow.path(name, o) for o in flow.steps[name]["out"]},
        }
        try:
            self.records.append(flow.run_hook(spec, name, extra=extra))
        except WorkflowError as err:
            self.out(f"step {name}: collect failed: {err}")
            return EXIT_ERROR
        return EXIT_OK

    def command(self, name, config_path, rnd, template=None):
        """Return the argv for a step's registry command (or one ``before``/``after`` command)."""
        flow = self.flow
        step = flow.steps[name]
        template = template or flow.entry(step["registry"])["command"]
        values = flow.values(name, True)
        fields = {
            "python": sys.executable,
            "dir": flow.path(name, DIR_KEY),
            ROUND_VAR: str(rnd),
        }
        if config_path is not None:
            fields["config"] = config_path
        fields.update({f"{OUT_SEGMENT}.{o}": flow.path(name, o) for o in step["out"]})
        fields.update(
            {f"{FILE_SEGMENT}.{k}": flow.file_path(name, k) for k in flow.file_templates(name)}
        )

        def fill(match):
            field = match.group(1)
            if field.startswith(IN_PREFIX):
                return _text(_descend(values, field[len(IN_PREFIX):]))
            return fields[field]

        return [_PATTERN_VAR.sub(fill, token) for token in shlex.split(template)]

    def _loop(self, name, entry):
        rule, cap, group = self.flow.loop_spec(name)
        history = []
        self.records = []
        entry["stopped"] = "max_rounds"
        for rnd in range(cap):
            code = self._once(name, rnd)
            entry["rounds"] = rnd + 1
            if code != EXIT_OK:
                return code
            history.append(path_hash(self.flow.path(name, group)))
            if rule.verdict(history, self.records):
                entry["stopped"] = "rule"
                break
        return EXIT_OK


# -- command line -------------------------------------------------------------


def _plan_lines(flow):
    lines = [] if flow.lane is None else [f"lane {flow.lane}"]
    for name, step in flow.steps.items():
        deps = sorted({src for _, src, _ in flow.step_refs(name)})
        lines.append(
            f"{name}  registry={step['registry']}  after={','.join(deps) or '-'}"
        )
        lines += [f"    out {o} -> {flow.path(name, o)}" for o in step["out"]]
        lines += [
            f"    {phase} {template}"
            for phase, template in flow.command_templates(name)
            if phase != PHASE_MAIN
        ]
        if "loop" in step:
            rule, cap, group = flow.loop_spec(name)
            lines.append(f"    loop group={group} max_rounds={cap}")
    return lines


def _split_filter(label):
    """Split ``step@lane`` into (step, lane); either part may be empty -> None."""
    step, sep, lane = label.partition(LANE_SEP)
    if sep and not lane:
        raise WorkflowError(f"empty lane in {label}")
    return step or None, lane or None


def _select(flows, from_step, only):
    """Return the (flow, step) pairs to run, lane by lane, after the filters."""
    if only and from_step:
        raise WorkflowError("--only and --from cannot be combined")
    names = list(flows[0].steps)
    lanes = [flow.lane for flow in flows]
    step, lane = _split_filter(only or from_step or "")
    if step and step not in names:
        raise WorkflowError(f"no such step {step}")
    if lane and lane not in lanes:
        raise WorkflowError(f"no such lane {lane}")
    start = names.index(step) if step else 0
    stop = start + 1 if step and only else len(names)
    return [
        (flow, name)
        for flow in flows
        if not lane or flow.lane == lane
        for name in names[start:stop]
    ]


def _check_upstream(selected):
    """Require steps skipped by --from or --only to have produced their outputs."""
    chosen = {(flow.lane, name) for flow, name in selected}
    first = list(selected[0][0].steps)[0]
    for flow, name in selected:
        for _, source, out in flow.step_refs(name):
            if (flow.lane, source) not in chosen and not os.path.exists(
                flow.path(source, out)
            ):
                raise WorkflowError(
                    f"step {flow.step_key(name)} needs {source}.{out}, which "
                    f"does not exist; run {first} first"
                )


def load_overlays(paths):
    """Return the args overlays merged in order (a later file wins key by key).

    Parameters
    ----------
    paths : str, list of str or None
        One overlay path, several, or none.

    Returns
    -------
    dict
        The deep-merged overlay; ``{}`` for none.

    Raises
    ------
    WorkflowError
        A file is unreadable or not JSON.

    Examples
    --------
    Layer a feature overlay over a base overlay::

        load_overlays(["stocks-long.json", "stocks-long-features.json"])
    """
    if not paths:
        return {}
    merged = {}
    for path in [paths] if isinstance(paths, str) else paths:
        merged = _merge(merged, load_json(path, "args"))
    return merged


def load_json(path, what):
    """Return the JSON a file holds; raise WorkflowError naming ``what`` when unreadable."""
    try:
        with open(path, encoding=ENCODING) as handle:
            return json.load(handle)
    except (OSError, ValueError) as err:
        raise WorkflowError(f"{what} {path}: {err}")


def _execute_all(selected, out):
    ledger = _Ledger(selected[0][0].work_root())
    runners = {}
    for flow, name in selected:
        runner = runners.setdefault(id(flow), _Runner(flow, ledger, out))
        code = runner.run_step(name)
        if code != EXIT_OK:
            out(f"halted at {flow.step_key(name)} (exit {code})")
            return code
    return EXIT_OK


def run_workflow(
    manifest_path, args_path=None, from_step=None, only=None, plan=False, out=print
):
    """Validate a manifest and run (or, with ``plan``, only print) its steps.

    Parameters
    ----------
    manifest_path : str
        The manifest JSON.
    args_path : str or list of str, optional
        JSON object file(s) overlaid on the manifest's ``args``, merged in order.
    from_step : str, optional
        Run this step and every later one. ``step@lane`` limits it to one
        lane; ``@lane`` is that lane's whole chain.
    only : str, optional
        Run just this step. ``step@lane`` runs it in one lane; ``@lane`` runs
        one lane's whole chain; a bare step runs it in every lane.
    plan : bool, default False
        Validate and print the resolved DAG; run nothing, write nothing.
    out : callable, default print
        Receives each output line.

    Returns
    -------
    int
        0 ran, 1 error or refusal, otherwise the halting step's exit code
        (3 halted, 5 refused).

    Examples
    --------
    Print the resolved DAG of a manifest::

        run_workflow("study/manifest.json", plan=True)
        # -> 0
    """
    try:
        manifest = load_json(manifest_path, "manifest")
        overlay = load_overlays(args_path) or None
        base = os.path.dirname(os.path.abspath(manifest_path))
        flows = lane_flows(manifest, base, overlay)
        selected = _select(flows, from_step, only)
        if plan:
            for flow in flows:
                for line in _plan_lines(flow) + [
                    f"unfilled arg {label}" for label in flow.unfilled()
                ]:
                    out(line)
            return EXIT_OK
        unfilled = [x for flow in flows for x in flow.unfilled()]
        if unfilled:
            raise WorkflowError([f"unfilled arg at {x}" for x in unfilled])
        _check_upstream(selected)
        return _execute_all(selected, out)
    except WorkflowError as err:
        for problem in err.problems:
            out(f"refused: {problem}")
        return EXIT_ERROR


from . import workflow_hooks  # noqa: E402,F401  (registers the study strategies)
