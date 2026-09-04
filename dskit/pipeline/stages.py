"""Journal-backed staged execution for long, resumable studies (ADR-0075)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from dskit.pipeline.base import ConfigError, import_ref, is_class_ref
from dskit.pipeline.document import load_document, parse_node_ref
from dskit.pipeline.runs import resolve_run_root

__all__ = [
    "DEFAULT_STAGE_KINDS",
    "Stage",
    "StageContext",
    "StageHalt",
    "StageKindRegistry",
    "StagedPlan",
    "StagedRunResult",
    "plan_stages",
    "register_stage_kind",
    "run_staged",
]

_KEY_OK = re.compile(r"^[a-z_][a-z0-9_]*$")
_KIND_OK = re.compile(r"^[a-z][a-z0-9_-]*$")
_DIGEST_OK = re.compile(r"^[0-9a-f]{64}$")


def reject_unknown_params(problems, params, allowed):
    """Append one default-deny problem for unknown stage parameters."""
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        problems.append(f"unknown param(s) {unknown} — allowed: {sorted(allowed)}")


@dataclass(frozen=True)
class StageContext:
    """The immutable execution frame handed to one study stage."""

    document: object
    source_path: str
    asof: str
    key: str
    run_dir: str
    artifact_dir: str


class Stage(ABC):
    """One resumable study step with strict params and named outputs."""

    outputs = ()

    def __init__(self, key, params=None):
        if not isinstance(key, str) or not _KEY_OK.match(key):
            raise ConfigError([f"stage key is invalid: {key!r}"])
        params = {} if params is None else params
        if not isinstance(params, dict) or any(
            not isinstance(name, str) for name in params
        ):
            raise ConfigError([f"{key}: params must be a string-keyed object"])
        problems = type(self).validate_params(params)
        if problems:
            raise ConfigError([f"{key}: {problem}" for problem in problems])
        self.key = key
        self.params = copy.deepcopy(params)

    @classmethod
    def validate_params(cls, params):
        """Default-deny all parameters; concrete stages name every knob."""
        problems = []
        reject_unknown_params(problems, params, ())
        return problems

    def validate_inputs(self, inputs):
        """Return input-contract problems; the default accepts named inputs."""
        if not isinstance(inputs, dict):
            return ["inputs must materialize as an object"]
        return []

    def validate_outputs(self, outputs):
        """Require a JSON object matching the declared output names exactly."""
        if not isinstance(outputs, dict) or any(
            not isinstance(name, str) or not name for name in outputs
        ):
            return ["run must return an object of named outputs"]
        wanted = set(type(self).outputs)
        got = set(outputs)
        if got == wanted:
            return []
        return [f"outputs must be exactly {sorted(wanted)}; got {sorted(got)}"]

    @abstractmethod
    def run(self, ctx, inputs):
        """Execute this stage and return its declared named outputs."""
        raise NotImplementedError


class StageHalt(Exception):
    """A deliberate terminal NO-GO carrying a valid stage result."""

    def __init__(self, reason, outputs):
        super().__init__(reason)
        self.reason = str(reason)
        self.outputs = outputs


class StageKindRegistry:
    """Map public stage kind names to Stage subclasses, refusing shadows."""

    def __init__(self):
        self._classes = {}

    def register(self, kind, cls):
        """Bind a kind name to one concrete stage class."""
        if not isinstance(kind, str) or not _KIND_OK.match(kind):
            raise ValueError(f"stage kind is invalid: {kind!r}")
        if kind in self._classes:
            raise ValueError(f"stage kind {kind!r} is already registered")
        problems = _stage_class_problems(cls)
        if problems:
            raise ValueError("; ".join(problems))
        self._classes[kind] = cls

    def resolve(self, uses):
        """Resolve a registered kind or import reference."""
        cls = import_ref(uses) if is_class_ref(uses) else self._classes.get(uses)
        if cls is None:
            raise ValueError(
                f"unknown stage kind {uses!r}; registered: {sorted(self._classes)}"
            )
        problems = _stage_class_problems(cls)
        if problems:
            raise ValueError(f"stage {uses!r}: {'; '.join(problems)}")
        return cls

    def kinds(self):
        """Return registered kind names in deterministic order."""
        return tuple(sorted(self._classes))


def _stage_class_problems(cls):
    problems = []
    if not isinstance(cls, type) or not issubclass(cls, Stage):
        return [f"must resolve to a Stage subclass, got {cls!r}"]
    outputs = getattr(cls, "outputs", None)
    if (
        not isinstance(outputs, tuple)
        or not outputs
        or any(not isinstance(name, str) or not name for name in outputs)
    ):
        problems.append("must declare a non-empty tuple of output names")
    if len(set(outputs or ())) != len(outputs or ()):
        problems.append("declares duplicate output names")
    if bool(getattr(cls, "__abstractmethods__", ())):
        problems.append("is abstract")
    return problems


DEFAULT_STAGE_KINDS = StageKindRegistry()


def register_stage_kind(kind, cls):
    """Register one public stage kind in the process-global registry."""
    DEFAULT_STAGE_KINDS.register(kind, cls)


@dataclass(frozen=True)
class StagedPlan:
    """A resolved, deterministic stage DAG."""

    document: object
    order: tuple
    classes: dict
    edges: tuple

    def to_obj(self):
        """Return the JSON-ready resolved stage plan."""
        return {
            "name": self.document.name,
            "document_hash": self.document.hash,
            "order": list(self.order),
            "stages": {
                key: {
                    "class": (
                        f"{self.classes[key].__module__}:"
                        f"{self.classes[key].__qualname__}"
                    ),
                    "inputs": dict(self.document.stages[key].inputs),
                }
                for key in self.order
            },
            "edges": [list(edge) for edge in self.edges],
        }


@dataclass(frozen=True)
class StagedRunResult:
    """The staged study's terminal state and recovered outputs."""

    state: str
    run_dir: str
    outputs: dict
    completed: tuple
    exit_code: int
    reason: str = ""


def _toposort(keys, dependencies):
    remaining = {key: set(dependencies[key]) for key in keys}
    order = []
    while True:
        ready = [key for key in keys if key not in order and not remaining[key]]
        if not ready:
            break
        key = ready[0]
        order.append(key)
        for deps in remaining.values():
            deps.discard(key)
    leftover = [key for key in keys if key not in order]
    if leftover:
        raise ValueError(f"stages contain a cycle among {leftover}")
    return tuple(order)


def plan_stages(document, registry=DEFAULT_STAGE_KINDS):
    """Import, validate, and deterministically order a document's stages."""
    if document.stages is None:
        raise ValueError("document has no stages section")
    from dskit.pipeline.planner import plan

    # Stages orchestrate the declared node map; they never exempt it from
    # the ordinary import, parameter, role, and DAG checks.
    plan(document)
    keys = list(document.stages)
    classes = {}
    dependencies = {}
    edges = []
    problems = []
    for key, spec in document.stages.items():
        try:
            cls = registry.resolve(spec.uses)
            stage = cls(key, spec.params)
            classes[key] = cls
            del stage
        except (ImportError, ValueError) as exc:
            problems.append(f"stages.{key}: {exc}")
        dependencies[key] = set()
        for source, path in spec.refs():
            dependencies[key].add(source)
            edges.append((source, key))
    for key, spec in document.stages.items():
        for _port, ref in spec.inputs.items():
            source, path = parse_node_ref(ref)
            cls = classes.get(source)
            if cls is not None and path[0] not in cls.outputs:
                problems.append(
                    f"stages.{key}: {ref!r} references undeclared output "
                    f"{source}.{path[0]}"
                )

    if problems:
        raise ConfigError(problems)
    return StagedPlan(
        document=document,
        order=_toposort(keys, dependencies),
        classes=classes,
        edges=tuple(edges),
    )


def _validated_asof(asof):
    if asof is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        datetime.strptime(asof, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError(f"asof must be YYYY-MM-DD, got {asof!r}") from None
    return asof


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_artifact(path):
    with open(path, encoding="utf-8") as handle:
        obj = json.load(handle)
    if not isinstance(obj, dict):
        raise ValueError(f"stage artifact is not an object: {path}")
    return obj


def _write_artifact(path, payload):
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stage-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _journal_rows(source_path):
    from dskit.journal import find_journal
    from dskit.journal.store import read_actions

    root = find_journal(start=os.path.dirname(source_path))
    if root is None:
        raise ValueError("staged execution requires a child journal")
    return root, read_actions(root)


def _latest_stage_row(rows, token):
    needle = f"stage_token={token}"
    for row in reversed(rows):
        if row.category == "execute" and needle in row.notes.split("; "):
            return row
    return None


def _note_value(notes, key):
    prefix = f"{key}="
    for part in notes.split("; "):
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _resume(path, token, rows):
    row = _latest_stage_row(rows, token)
    exists = os.path.isfile(path)
    resumable = row is not None and _note_value(row.notes, "state") in {
        "ran",
        "halted",
    }
    if not resumable:
        if exists:
            raise ValueError(
                f"stage artifact exists without a matching successful journal "
                f"row: {path}"
            )
        return None
    if not exists:
        raise ValueError(
            f"journal says stage completed but its artifact is missing: {path}"
        )
    expected = _note_value(row.notes, "sha256")
    if not expected or not _DIGEST_OK.match(expected) or _sha256(path) != expected:
        raise ValueError(f"stage artifact digest does not match the journal: {path}")
    obj = _read_artifact(path)
    if obj.get("stage_token") != token:
        raise ValueError(f"stage artifact token does not match the journal: {path}")
    return obj


def _materialize_inputs(spec, outputs):
    materialized = {}
    for port, ref in spec.inputs.items():
        source, path = parse_node_ref(ref)
        value = outputs[source]
        try:
            for segment in path:
                value = value[segment]
        except (KeyError, TypeError):
            raise ValueError(f"cannot resolve staged input {ref!r}") from None
        materialized[port] = value
    return materialized


def _record(source_path, key, token, state, artifact, digest, reason=""):
    from dskit.journal import append_action

    notes = f"stage_token={token}; state={state}; sha256={digest}; reason={reason}"
    return append_action(
        "execute",
        f"staged {key}",
        inputs=source_path,
        outputs=artifact,
        notes=notes,
        start=os.path.dirname(source_path),
    )


def run_staged(path, asof=None, registry=DEFAULT_STAGE_KINDS):
    """Run or resume every stage, trusting only journal-plus-digest evidence."""
    source_path = os.path.abspath(path)
    document = load_document(source_path)
    plan = plan_stages(document, registry=registry)
    asof = _validated_asof(asof)
    declared_root = document.outputs.run_root if document.outputs else ""
    run_root = resolve_run_root(declared_root)
    run_dir = os.path.join(
        run_root, f"{document.name}-staged-{asof}-{document.hash[:8]}"
    )
    stage_dir = os.path.join(run_dir, "stages")
    os.makedirs(stage_dir, exist_ok=True)
    _root, rows = _journal_rows(source_path)
    outputs = {}
    completed = []
    for key in plan.order:
        spec = document.stages[key]
        token = f"{document.hash}:{key}"
        artifact = os.path.join(stage_dir, f"{key}.json")
        prior = _resume(artifact, token, rows)
        if prior is not None:
            outputs[key] = prior["outputs"]
            completed.append(key)
            if prior["state"] == "halted":
                return StagedRunResult(
                    "halted",
                    run_dir,
                    outputs,
                    tuple(completed),
                    3,
                    prior.get("reason", ""),
                )
            continue
        cls = plan.classes[key]
        stage = cls(key, spec.params)
        inputs = _materialize_inputs(spec, outputs)
        problems = stage.validate_inputs(inputs)
        if problems:
            raise ConfigError([f"{key}: {problem}" for problem in problems])
        ctx = StageContext(
            document=document,
            source_path=source_path,
            asof=asof,
            key=key,
            run_dir=run_dir,
            artifact_dir=stage_dir,
        )
        state = "ran"
        reason = ""
        try:
            stage_outputs = stage.run(ctx, inputs)
        except StageHalt as exc:
            state = "halted"
            reason = exc.reason
            stage_outputs = exc.outputs
        except BaseException as exc:
            from dskit.journal import append_action

            append_action(
                "execute",
                f"staged {key}",
                inputs=source_path,
                notes=(
                    f"stage_token={token}; state=error; "
                    f"reason={type(exc).__name__}: {exc}"
                ),
                start=os.path.dirname(source_path),
            )
            raise
        problems = stage.validate_outputs(stage_outputs)
        if problems:
            raise ConfigError([f"{key}: {problem}" for problem in problems])
        payload = {
            "stage_token": token,
            "stage": key,
            "state": state,
            "reason": reason,
            "outputs": stage_outputs,
        }
        _write_artifact(artifact, payload)
        digest = _sha256(artifact)
        _record(source_path, key, token, state, artifact, digest, reason)
        rows = _journal_rows(source_path)[1]
        outputs[key] = stage_outputs
        completed.append(key)
        if state == "halted":
            return StagedRunResult(state, run_dir, outputs, tuple(completed), 3, reason)
    return StagedRunResult("ran", run_dir, outputs, tuple(completed), 0)
