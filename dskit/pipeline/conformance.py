"""The conformance suite — the invariants every Node pack must satisfy.

A package points this at its own registry and gets a pytest class back::

    from dskit.pipeline.conformance import NodeProbe, conformance_suite

    TestConformance = conformance_suite(
        registry=MY_NODE_KINDS,
        module="mypkg.nodes",
        probes=lambda tmp_path: {"my-source": NodeProbe(...)},
        expected_roles={"my-source": "data"},
    )

Why it exists: the 2026-08-14 build of the first adapter shipped **22
defects**, and every one of them violated a rule that could have been
stated once and checked everywhere ([[F-220]], docs/25 §1). Each test
below is one of those rules. They are not hypothetical — the docstring
of each names the class of bug it exists to catch. The suite itself then
went through the same adversarial review and lost 13 ways ([[F-222]]);
this is the hardened second cut.

Two kinds of check live here:

* **Structural** — read off the class alone (declared outputs, validator
  totality, default-deny, fingerprint declared). They run for every kind
  in the registry with no cooperation from the package.
* **Behavioural** — need real instances over real (fixture-built) data,
  so the package supplies a :class:`NodeProbe` per kind. A probe must be
  POPULATED for its kind's role, not merely present: an empty probe
  silently skips every behavioural check, which is exactly the
  opt-out-by-omission this suite refuses. The population rules are
  enforced by ``test_every_probe_is_populated_for_its_role``.

The probe contract that keeps the hard checks honest:

* ``move()`` MUTATES the dataset ``make()`` reads, in place — it does not
  build a second dataset somewhere else. Two datasets at two paths let a
  fingerprint "move" by echoing its own ``data_dir``, proving nothing
  about content sensitivity (F-222 FN-1). With one path and two contents,
  only the content can move the fingerprint.
* ``grow()`` appends new data to that same dataset — the live-recorder
  write landing between resolve and execute.

Placement (D-146): this is TIER 1 — stdlib only, no venue name anywhere.
``pytest`` is imported inside :func:`conformance_suite`, not at module
top, for exactly the reason node wrappers import their heavy libraries
inside ``run()``: importing this module must never drag a dependency in.

Import cost: stdlib only.
"""

from __future__ import annotations

import importlib.util
import json
import math
import secrets
import subprocess
import sys
from dataclasses import dataclass, field

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry, TrainableNode
from dskit.pipeline.planner import plan

__all__ = [
    "DEFAULT_BLOCKED_IMPORTS",
    "FINGERPRINTED_ROLES",
    "NodeProbe",
    "conformance_suite",
    "import_with_blocked",
]

#: Libraries a Node module must not need at IMPORT time. A document is
#: resolved (and therefore its classes imported) at PLAN, on machines that
#: may have none of these installed — so a top-level ``import pandas``
#: anywhere in a node module stops documents planning there.
#:
#: The purity gate's behavioural half blocks exactly this tuple while it
#: imports every tier-2 pack, so a pack whose library is MISSING here is
#: checked only statically — which is why ``mlflow`` is listed even though
#: it backs a tracking sink rather than a node.
DEFAULT_BLOCKED_IMPORTS = (
    "highspy",
    "matplotlib",
    "mlflow",
    "numpy",
    "optuna",
    "pandas",
    "pyarrow",
    "pyomo",
    "scipy",
    "sklearn",
    "torch",
    "transformers",
)

#: Roles whose identity the driver hashes into the run identity.
FINGERPRINTED_ROLES = ("data", "labels")

#: Roles that may carry ``mode``/``artifact`` (mirrors the planner's rule).
TRAINABLE_ROLES = ("train", "signal")

#: Keys no node has a knob named — SEVERAL, so a validator that
#: special-cases one probe literal still fails (F-222 FN-7).
_UNKNOWN_KNOBS = (
    "conformance_unknown_knob",
    "another_knob_no_node_declares",
)

#: JSON-shaped wrong-typed values for the validator-totality fuzz
#: (F-222 FN-13). Params arrive from a JSON document, so the panel is
#: JSON-shaped — the classic killer is the string where a number belongs.
_FUZZ_VALUES = (
    "1,000",
    "NaN",
    "",
    -1,
    0,
    1e308,
    float("nan"),
    True,
    False,
    None,
    [],
    [1],
    {},
    {"nested": 1},
)


@dataclass(frozen=True)
class NodeProbe:
    """What a package hands the suite so the behavioural checks can run.

    Supply what the kind's ROLE requires (see the population rules in the
    module docstring — an under-populated probe fails the suite naming
    the missing fields). The callables are built by the probe factory,
    closed over a ``tmp_path``-rooted fixture dataset — no check here
    ever wants a real data file.

    Parameters
    ----------
    params : dict
        A VALID param set for this kind. The suite asserts it validates
        clean, so every other probe-driven check runs against a node the
        package agrees is well-formed.
    required : tuple of str
        Params this kind cannot run without. The suite drops each one and
        demands ``validate_params`` name it — the plan-time refusal.
    inputs : dict or None
        Materialized inputs ``validate_inputs`` should accept — and,
        when ``runnable``, that ``run()`` works on.
    stream_ports : tuple of str, or None
        Ports of ``inputs`` carrying a RECORD STREAM — checked for the
        one-shot-consumption bug. ``None`` means UNDECLARED, which the
        population check refuses whenever ``inputs`` is given: pass
        ``()`` explicitly to state "no port here is a stream".
    make : callable or None
        ``() -> Node`` over the fixture dataset. Called repeatedly; each
        call must build an equivalent node, same params, same dataset.
    move : callable or None
        ``() -> None`` — mutate the CONTENT of the dataset ``make()``
        reads, in place, keeping counts and extents where possible (that
        is the hard case). It must NOT switch to a different path: same
        params + different content is the only shape that proves
        content sensitivity. Where the dataset is files, RESTORE their
        mtimes after rewriting (``os.utime``) — otherwise a fingerprint
        built from file metadata alone, which never reads a content
        byte, rides the mtime change through this check (F-222 round-2).
    grow : callable or None
        ``() -> None`` — append NEW data to that same dataset,
        simulating a live writer landing rows between resolve and
        execute.
    size : callable or None
        ``(outputs) -> int``, how much data a ``run`` return carries.
    digest : callable or None
        ``(outputs) -> comparable`` covering EVERY declared output — used
        to compare two ``run()`` returns. ``None`` falls back to ``==``
        on the output dicts, which requires the payloads to define
        equality by value.
    runnable : bool
        ``run(ctx, inputs)`` works as given — turns on the checks that
        need a real return value (output contract, budget, gate, load).
        Trainable and capital roles must either set this or write down a
        ``not_runnable_reason`` — leaving it at the default silently
        skipped the load, budget and gate checks (F-222 round-2 NEW-1),
        which is how F-220 #1 and #12 would ship again.
    not_runnable_reason : str
        Trainable/capital roles only — a WRITTEN reason ``run()`` cannot
        be exercised standalone (e.g. a search kind that needs the
        driver's ``ctx.rerun`` seam). Opting out silently is refused.
    budget : float or None
        Capital roles: the spend ceiling the probe's params declare.
    outlay : callable or None
        Capital roles: ``(outputs) -> float``, how much the run deployed.
    gate_port : str or None
        Capital roles: the input port carrying the stat_test survivors.
    no_budget_reason : str
        Capital roles only — a WRITTEN reason this kind has no single
        bounded outlay (e.g. a replay that spends through its own
        bankroll accounting). Opting out silently is refused.
    load_artifact : str
        Trainable roles: the artifact reference a ``mode="load"``
        construction should restore.
    verify_loaded : callable or None
        Trainable roles: ``(outputs) -> bool`` — proof the run RESTORED
        rather than silently refitted. A trainable kind whose ``run``
        accepts ``mode="load"`` without this proof fails.
    ctx : NodeContext or None
        Context for ``run``; the suite builds a bare one when omitted.
    """

    params: dict = field(default_factory=dict)
    required: tuple = ()
    inputs: object = None
    stream_ports: object = None
    make: object = None
    move: object = None
    grow: object = None
    size: object = None
    digest: object = None
    runnable: bool = False
    not_runnable_reason: str = ""
    budget: object = None
    outlay: object = None
    gate_port: object = None
    no_budget_reason: str = ""
    load_artifact: str = ""
    verify_loaded: object = None
    ctx: object = None


def import_with_blocked(module, blocked, *, timeout=120):
    """Import ``module`` in a FRESH interpreter with ``blocked`` unimportable.

    Returns ``(ok, detail)``. Setting ``sys.modules[name] = None`` makes
    ``import name`` raise, and a subprocess is the only honest way to run
    this: in-process, the libraries are already imported by the time any
    test executes, so an ``"x" not in sys.modules`` assertion is vacuous
    (the trap ``tests/ladder/test_protocols.py`` documents).

    Two hardenings from F-222: ``blocked`` must be a real sequence (a
    bare string would block single letters and nothing else, silently);
    and the subprocess inherits the PARENT's ``sys.path`` and must
    resolve ``module`` to the same file the parent would — otherwise a
    conftest path insert or an editable install can point the child at a
    different (clean) copy of a dirty module (FN-11).

    The copy-pin must not DISARM silently (F-222 round-2): when the
    parent cannot resolve a file for a module it demonstrably holds in
    ``sys.modules``, the check refuses rather than validating whatever
    copy the child happens to find.
    """
    if isinstance(blocked, (str, bytes)):
        raise TypeError(
            f"blocked must be a sequence of module names, got the string "
            f"{blocked!r} — a bare string would block its individual "
            "characters and nothing else"
        )
    try:
        spec = importlib.util.find_spec(module)
        expected = spec.origin if spec is not None else None
    except (ImportError, ValueError):
        expected = None
    if expected is None:
        # Fall back to the copy the parent actually holds, if any.
        held = sys.modules.get(module)
        expected = getattr(held, "__file__", None)
        if expected is None and held is not None:
            return False, (
                f"the calling process holds {module!r} in sys.modules but its "
                "file cannot be resolved — the check cannot prove the "
                "subprocess would validate the same copy, so it refuses "
                "rather than validating whichever copy the child finds"
            )
    script = (
        f"import sys\n"
        f"sys.path[:] = {list(sys.path)!r}\n"
        f"for _n in {tuple(blocked)!r}:\n"
        f"    sys.modules[_n] = None\n"
        f"import importlib\n"
        f"_m = importlib.import_module({module!r})\n"
        f"_expected = {expected!r}\n"
        f"_got = getattr(_m, '__file__', None)\n"
        f"if _expected is not None and _got != _expected:\n"
        f"    raise SystemExit(\n"
        f"        'resolved %s from %r, but the calling process resolves it '\n"
        f"        'from %r — the check validated the wrong copy' \n"
        f"        % ({module!r}, _got, _expected)\n"
        f"    )\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,  # a non-zero exit IS the answer, not an exception
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        return False, f"importing {module} timed out after {timeout}s"
    if done.returncode == 0:
        return True, ""
    return False, (done.stderr or done.stdout).strip()


def _normalize(registry):
    """``{name: (cls, owned)}`` from a registry, a mapping, or pairs.

    Accepted shapes, because packages legitimately hold their kinds in
    all three: a live :class:`~dskit.pipeline.node.NodeKindRegistry`;
    a sequence of ``(name, cls)`` or ``(name, cls, owned)`` pairs (the
    table adapters register FROM); or a mapping of ``name -> cls`` /
    ``name -> (cls, owned)`` (the shape ``NodeKindRegistry.get``
    returns, F-222 P3).
    """
    if isinstance(registry, NodeKindRegistry):
        return {name: registry.get(name) for name in registry.kinds()}
    out = {}
    if hasattr(registry, "items"):
        for name, value in registry.items():
            if isinstance(value, (tuple, list)) and len(value) == 2:
                out[name] = (value[0], bool(value[1]))
            else:
                out[name] = (value, False)
    else:
        for entry in registry:
            name, cls = entry[0], entry[1]
            owned = bool(entry[2]) if len(entry) > 2 else False
            out[name] = (cls, owned)
    if not out:
        raise ValueError("conformance_suite: the registry declares no kinds")
    for name, (cls, _owned) in out.items():
        if not isinstance(cls, type):
            raise TypeError(
                f"conformance_suite: kind {name!r} maps to {cls!r}, not a "
                "class — pass (name, cls) pairs, a name->cls mapping, or a "
                "NodeKindRegistry"
            )
    return out


def _evidence_bases(cls):
    """The bases whose code is EVIDENCE about ``cls`` — its own, never the
    toolkit's.

    One seam for the three MRO walks below, because the rule they share is
    not "walk the MRO" but "walk the child's OWN classes": the toolkit's
    bases are read by every kind, so anything they touch would vouch for
    every kind. That was free while :class:`Node` was the only toolkit
    ancestor — the walks simply stopped there — and stopped being free the
    moment :class:`TrainableNode` was inserted between a pack's class and
    ``Node`` (ADR-0038): its ``node_level_pin`` reads ``self.artifact``,
    which is a DECLARED knob of three pinned-inference kinds, so an
    unfiltered walk would certify a knob nobody read.

    Yields
    ------
    type
        Each base from ``cls.__mro__`` up to (excluding) :class:`Node`,
        skipping :class:`TrainableNode`.
    """
    for base in cls.__mro__:
        if base is Node or base is object:
            return
        if base is TrainableNode:
            continue
        yield base


def _referenced_names(cls):
    """Names ``cls``'s OWN code touches — its :func:`_evidence_bases`.

    Read from the compiled code objects, not the source text, for two
    reasons. It works wherever the class does (a notebook cell has no
    source file, and ``inspect.getsource`` raises there — a check that
    silently skips is a hole). And it cannot be satisfied by a MENTION: a
    knob named in a comment or a docstring is invisible to bytecode, while
    a real attribute access lands in ``co_names``.
    """
    names = set()
    stack = []
    for base in _evidence_bases(cls):
        for value in vars(base).values():
            fn = getattr(value, "__func__", value)  # unwrap class/staticmethod
            code = getattr(fn, "__code__", None)
            if code is not None:
                stack.append(code)
    while stack:
        code = stack.pop()
        names.update(code.co_names)
        stack.extend(c for c in code.co_consts if hasattr(c, "co_names"))
    return names


def _class_knobs(cls):
    """The knob names ``cls`` DECLARES for itself — the ``_PARAMS`` tuple
    and the ``_allowed()`` hook the packs use. Probe-supplied knobs are
    deliberately not here: this is the class's own allowed list."""
    knobs = set()
    declared = getattr(cls, "_PARAMS", ())
    if isinstance(declared, (tuple, list, set, frozenset)):
        knobs.update(k for k in declared if isinstance(k, str))
    allowed_hook = getattr(cls, "_allowed", None)
    if callable(allowed_hook):
        try:
            knobs.update(k for k in allowed_hook() if isinstance(k, str))
        # A broken discovery hook is not this helper's business — callers
        # still work from whatever the static table gave.
        except Exception:  # noqa: BLE001, S110
            pass
    return knobs


def _strings_in(value, depth=0):
    """Every string reachable inside a small container, keys included."""
    if isinstance(value, str):
        return {value}
    if depth > 2:
        return set()
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found |= _strings_in(key, depth + 1) | _strings_in(item, depth + 1)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            found |= _strings_in(item, depth + 1)
    return found


def _reachable_knob_names(cls):
    """Knob names ``cls``'s code could actually be reading (I-227 gap 1).

    :func:`_referenced_names` alone is not enough, and the reason is the
    honest half of why this check was deferred: a class that reads its
    knobs by looping over a MODULE-LEVEL table —
    ``for name in KELLY_MIO_SOLVER_KNOBS: self.params[name]`` — never
    carries the literals in its own bytecode. Scanning code only, every
    such knob looks unread, and the check would cry wolf on correct code.

    So a knob counts as reachable when its literal appears in the class's
    own code (name access or string constant) OR inside a module-level
    container the class's code NAMES. That is deliberately generous: this
    check exists to catch a knob NOBODY mentions anywhere — the typo on
    an allowed list, the knob left behind when its reader was deleted —
    not to prove a knob is used well.
    """
    names = _referenced_names(cls)
    reachable = set(names)
    stack, modules = [], []
    for base in _evidence_bases(cls):
        modules.append(sys.modules.get(base.__module__))
        for value in vars(base).values():
            fn = getattr(value, "__func__", value)
            code = getattr(fn, "__code__", None)
            if code is not None:
                stack.append(code)
    while stack:
        code = stack.pop()
        consts = list(code.co_consts)
        # Drop the leading docstring: a knob NAMED in prose is exactly the
        # mention _referenced_names refuses to accept as evidence, and
        # letting it in here would reopen that hole from the other side.
        if consts and isinstance(consts[0], str):
            consts = consts[1:]
        stack.extend(c for c in consts if hasattr(c, "co_names"))
        reachable |= _strings_in([c for c in consts if not hasattr(c, "co_names")])
    # Module-level tables the class's code names — `for k in TABLE:` reads
    # every knob in TABLE without carrying one literal of its own. NOT the
    # class's own attributes: `_PARAMS`/`_EXTRA_PARAMS` ARE the declaration,
    # so counting them would make every knob vouch for itself.
    for module in modules:
        if module is None:
            continue
        for name in names:
            if hasattr(module, name):
                reachable |= _strings_in(getattr(module, name))
    # Class-level tables the code NAMES read every knob they hold, exactly
    # like a module-level one — `for k in self._SHAPE_PARAMS: ...` carries no
    # literal of its own. The DECLARATION tables are excluded: a knob is not
    # READ merely by being DECLARED, or every knob would vouch for itself
    # (the reason the module-level pass already skips a class's own attrs).
    _DECLARATION_TABLES = {"_PARAMS", "_BASE_PARAMS", "_EXTRA_PARAMS"}
    for base in _evidence_bases(cls):
        members = vars(base)
        for name in names:
            if name in _DECLARATION_TABLES or name not in members:
                continue
            reachable |= _strings_in(members[name])
    return reachable


#: Probe fields each role MUST populate for the behavioural checks to
#: mean anything. Enforced by ``test_every_probe_is_populated_for_its_role``.
#: Trainable and capital roles additionally need ``runnable`` (or a written
#: ``not_runnable_reason``) — see :data:`_MUST_RUN_ROLES`.
_ROLE_POPULATION = {
    "data": ("make", "move", "grow", "size"),
    # ``grow`` on labels is I-229: a labels node straddles resolve and
    # execute exactly as a data node does — outcomes are hashed into the
    # run identity at resolve and read at execute, and a settlement store
    # SETTLES MORE in between. Without the hook the straddle check below
    # cannot run, and S4-F4 (the driver rebuilding labels at execute) was
    # invisible to this bar when it shipped.
    "labels": ("make", "move", "grow"),
    "capital": ("inputs", "gate_port", "outlay"),
    "train": ("inputs",),
    "signal": ("inputs",),
}

#: Roles whose teeth (budget, gate, load) only bite on a REAL run — the
#: F-222 round-2 hole: `runnable` defaulting to False silently skipped
#: exactly the checks that would have caught F-220 #1 and #12 again.
_MUST_RUN_ROLES = ("capital", *TRAINABLE_ROLES)


def conformance_suite(
    *,
    registry,
    module=None,
    probes=None,
    expected_roles=None,
    blocked_imports=DEFAULT_BLOCKED_IMPORTS,
    require_probes=True,
    max_fingerprint_bytes=65536,
    name="TestConformance",
):
    """Build the pytest class that holds a package's kinds to the bar.

    Parameters
    ----------
    registry : NodeKindRegistry | Mapping | sequence of pairs
        The package's kinds (see :func:`_normalize` for accepted shapes).
    module : str or None
        Dotted module the kinds live in. When given, it must import in a
        fresh interpreter with ``blocked_imports`` unimportable.
    probes : callable | Mapping | None
        ``(tmp_path) -> {kind: NodeProbe}``, or a plain mapping when the
        probes need no temporary directory.
    expected_roles : Mapping or None
        ``kind -> role`` as the PACKAGE declares them — an independent
        statement cross-checked against the classes, so a source
        mislabelled ``transform`` cannot silently exit the identity
        checks (F-222 FN-12). Omitting it skips the census with a named
        skip.
    require_probes : bool
        Fail when a kind's probe is missing or under-populated for its
        role (default). Turning this off runs structural checks only —
        a decision, so it must be written down.
    """
    import pytest

    kinds = _normalize(registry)
    names = tuple(sorted(kinds))

    def _of_role(*roles):
        """Parametrize argument for the kinds in ``roles`` — or a single
        ``None`` case, so an absent role reads as an explicit skip in the
        report rather than a silently empty parametrization."""
        picked = [k for k in names if kinds[k][0].role in roles]
        return picked or [None]

    def _probe(kind, tmp_path):
        table = probes(tmp_path) if callable(probes) else (probes or {})
        return table.get(kind)

    def _need(kind, tmp_path, *fields):
        probe = _probe(kind, tmp_path)
        if probe is None:
            pytest.skip(
                f"{kind}: no probe (reported by "
                "test_every_probe_is_populated_for_its_role)"
            )
        for f in fields:
            if getattr(probe, f) is None:
                pytest.skip(f"{kind}: probe supplies no {f}")
        return probe

    def _ctx(probe, tmp_path):
        if probe.ctx is not None:
            return probe.ctx
        return NodeContext(
            name="conformance", asof="2026-01-01", run_dir=str(tmp_path / "run")
        )

    def _node(kind, probe):
        return kinds[kind][0](kind.replace("-", "_"), dict(probe.params))

    def _not_runnable_note(kind, probe):
        """Why a not-runnable skip is happening — honest about whether the
        population check has this kind's back. It only demands runnable
        for the must-run roles; claiming otherwise asserted a guarantee
        the code does not give (F-222 round-2 DOG-5)."""
        if probe.not_runnable_reason:
            return probe.not_runnable_reason
        if kinds[kind][0].role in _MUST_RUN_ROLES:
            return "population check reports it"
        return "no reason given — role outside the must-run set"

    def _unknown_keys(probe):
        keys = list(_UNKNOWN_KNOBS)
        if probe is not None and probe.params:
            keys.append(next(iter(probe.params)) + "_typoed")
        # One key no validator can have seen: the fixed literals are
        # predictable, and a validator teaching to the test (denying
        # exactly the suite's known probes) must still fail (F-222
        # round-2 GAME class). Fresh per call; a conformant default-deny
        # treats every unknown key identically, so this cannot flake.
        keys.append("unk_" + secrets.token_hex(4))
        return keys

    def _declared_knobs(cls, probe):
        """Every knob name discoverable for ``cls`` — the probe's params
        and required, plus the ``_PARAMS`` tuple / ``_allowed()`` hook
        conventions the packs use. The fuzz must reach OPTIONAL knobs the
        probe never set: a validator that floats ``params['timeout']``
        only when present explodes at plan time on the one document that
        sets it (F-222 round-2 fuzz blind-spot)."""
        knobs = set(probe.params if probe is not None else ()) | set(
            probe.required if probe is not None else ()
        )
        # A broken discovery hook is not the fuzz's business — the fuzz
        # still runs over the probe-known knobs (see _class_knobs).
        return sorted(knobs | _class_knobs(cls))

    def _outputs_digest(probe, outputs):
        return probe.digest(outputs) if probe.digest is not None else outputs

    def _digests_equal(probe, a, b):
        """Value equality of two run() returns through the probe's lens.

        Hardened for payloads whose ``==`` is not a clean bool (numpy
        arrays raise on truth-testing; ``dataclass(eq=False)`` rows
        compare by identity): any comparison that raises or answers
        non-bool counts as NOT equal, and the caller's message points at
        ``probe.digest`` as the remedy (F-222 round-2 FP-A)."""
        try:
            outcome = _outputs_digest(probe, a) == _outputs_digest(probe, b)
        except Exception:  # noqa: BLE001 - ambiguous equality IS the answer
            return False
        return outcome is True

    def _contract_problems(cls, node, out):
        """The instance validator's problems PLUS a direct exact-match
        against the class contract — the validator alone can be widened
        by a mistaken override, and the driver would then ship the drift
        too (F-222 round-2)."""
        problems = list(node.validate_outputs(out))
        declared = cls.outputs
        if declared is not None and isinstance(out, dict):
            got, want = set(out), set(declared)
            if got != want:
                problems.append(
                    f"run() returned {sorted(got)} vs the class contract "
                    f"{sorted(want)} (checked directly — an override cannot "
                    "widen the declared outputs)"
                )
        return problems

    def _digest_covers_declared(probe, cls, out):
        """Problems when the probe's custom digest ignores a declared
        output — a records-only digest quietly reintroduces the partial
        pin (F-222 round-2). The default (dict equality) is total.

        Checked by MUTATION SENSITIVITY, not access recording: for each
        declared output, replacing its value with a sentinel must change
        the digest (a raise counts — the digest read it and choked).
        Access recording via a dict subclass was defeated by C-level
        copies (``dict(o)``, ``{**o}``) that never call the overridden
        methods, refusing honest full-value digests (round-2 DOG-4).
        """
        if probe.digest is None or not isinstance(out, dict):
            return []
        try:
            base = probe.digest(dict(out))
        except Exception as exc:  # noqa: BLE001 - a digest that cannot even
            return [f"the probe's digest fails on a real run() return: {exc!r}"]
        blind = []
        sentinel = object()
        for key in cls.outputs or ():
            mutated = {**out, key: sentinel}
            try:
                if probe.digest(mutated) == base:
                    blind.append(key)
            # Choking on the sentinel IS sensitivity — the digest read
            # the mutated output, which is all this check asks.
            except Exception:  # noqa: BLE001, S112
                continue
        if blind:
            return [
                (
                    f"the probe's digest cannot tell a change to declared "
                    f"output(s) {sorted(blind)} — a partial digest "
                    "reintroduces the partial-pin hole; cover every declared "
                    "output or drop digest to use full equality"
                )
            ]
        return []

    class Suite:
        """Conformance — one class per registry (see module docstring)."""

        #: Introspectable by the caller: what this suite was pointed at.
        conformance_kinds = names
        conformance_module = module

        # -- the pack imports on a dependency-less machine -----------------

        def test_module_imports_with_heavy_libraries_blocked(self):
            """Documents are PLANNED on machines with no heavy dependency
            installed, and planning imports the node classes. A top-level
            ``import pandas`` in a node module makes every document that
            names it unplannable there."""
            if module is None:
                pytest.skip("no module= given")
            ok, detail = import_with_blocked(module, blocked_imports)
            assert ok, (
                f"{module} does not import with {list(blocked_imports)} blocked "
                "— heavy imports belong INSIDE run(), never at module top:\n"
                f"{detail}"
            )

        # -- the package's own role declarations ---------------------------

        def test_roles_match_the_packages_declaration(self):
            """A source mislabelled ``transform`` exits every identity
            check and the report reads 'not applicable' (F-222 FN-12).
            The census is an INDEPENDENT declaration: the package states
            each kind's role in the test file, and the classes must
            agree."""
            if expected_roles is None:
                pytest.skip(
                    "no expected_roles= given — pass {kind: role} so a "
                    "mislabelled role cannot silently exit its checks"
                )
            declared = dict(expected_roles)
            assert set(declared) == set(names), (
                "expected_roles must cover exactly the registry's kinds — "
                f"missing {sorted(set(names) - set(declared))}, "
                f"extra {sorted(set(declared) - set(names))}"
            )
            mismatches = {
                kind: (declared[kind], kinds[kind][0].role)
                for kind in names
                if kinds[kind][0].role != declared[kind]
            }
            assert not mismatches, (
                "class roles contradict the package's declaration "
                f"(expected != class): {mismatches}"
            )

        # -- the class contract -------------------------------------------

        @pytest.mark.parametrize("kind", names)
        def test_declares_an_output_contract(self, kind):
            """An undeclared ``outputs`` means nothing can detect drift
            between what a node promises and what ``run`` returns — the
            driver's exact-match check has nothing to match against."""
            cls = kinds[kind][0]
            declared = cls.outputs
            assert declared is not None, (
                f"{kind} ({cls.__name__}) declares no outputs contract — "
                "declare `outputs = (...)` so run()'s return is checkable"
            )
            assert isinstance(declared, tuple) and declared, declared
            assert all(isinstance(o, str) and o for o in declared), declared

        @pytest.mark.parametrize("kind", names)
        def test_output_validation_refuses_undeclared_names(self, kind, tmp_path):
            """The base refuses a ``run`` return that misses a declared
            output or invents one. A subclass that overrode
            ``validate_outputs`` into something permissive would let
            contract drift through, so the check is behavioural.

            Only REFUSAL is asserted here: a subclass may lawfully be
            STRICTER than the base (type-checking payloads, F-222 P1), so
            acceptance of good outputs is checked against real ``run()``
            returns in the runnable checks, never against sentinels.
            """
            cls = kinds[kind][0]
            probe = _probe(kind, tmp_path)
            if probe is not None:
                node = _node(kind, probe)  # a REAL instance (F-222 P2)
            else:
                node = object.__new__(cls)
            declared = cls.outputs or ()
            # SEVERAL undeclared names, realistic ones included — an
            # override that reimplements exact-match but quietly allows
            # one plausible extra ("metrics") must still be refused
            # (F-222 round-2: the outputs analogue of the special-cased
            # unknown param key).
            extras = [
                name
                for name in ("conformance_undeclared", "metrics", "debug")
                if name not in declared
            ]
            bads = [{}, *(dict.fromkeys((*declared, e), 0) for e in extras)]
            for bad in bads:
                try:
                    problems = node.validate_outputs(bad)
                except AttributeError:
                    pytest.skip(
                        f"{kind}: validate_outputs reads instance state and "
                        "no probe params exist to construct with — supply a "
                        "probe"
                    )
                assert problems, (
                    f"{kind}: validate_outputs accepted {sorted(bad)!r} against "
                    f"the declared contract {sorted(declared)}"
                )

        @pytest.mark.parametrize("kind", names)
        def test_param_validation_is_total(self, kind, tmp_path):
            """``validate_params`` must RETURN problems, never raise, for
            any input. It runs at plan time over a document a human typed;
            a validator that explodes turns a typo into a stack trace and,
            worse, can leave the real check to execute (the I-001 fee
            guard fired only after the work — F-220).

            Fuzzed per knob with JSON-shaped wrong-typed values (F-222
            FN-13) — the classic killer is ``"1,000"`` where a number
            belongs.
            """
            cls = kinds[kind][0]
            probe = _probe(kind, tmp_path)
            base = dict(probe.params) if probe is not None else {}
            trials = [
                {},
                {_UNKNOWN_KNOBS[0]: 1},
                {"split": None},
                {"data_dir": 5, "instruments": "not-a-list"},
            ]
            for key in [*_declared_knobs(cls, probe), _UNKNOWN_KNOBS[0]]:
                trials.extend({**base, key: value} for value in _FUZZ_VALUES)
            for params in trials:
                try:
                    problems = cls.validate_params(dict(params))
                except Exception as exc:
                    raise AssertionError(
                        f"{kind}: validate_params raised "
                        f"{type(exc).__name__}: {exc} on {params!r} — a "
                        "validator must RETURN problems, never explode on a "
                        "typo"
                    ) from exc
                assert isinstance(problems, list), (
                    f"{kind}: validate_params({params!r}) returned "
                    f"{type(problems).__name__}, expected a list"
                )
                assert all(isinstance(p, str) for p in problems), problems

        @pytest.mark.parametrize("kind", names)
        def test_params_default_deny_unknown_keys(self, kind, tmp_path):
            """A knob the document sets and the node never reads is a lie
            the run tells about itself. Unknown keys must be REFUSED by
            name — SEVERAL of them, so a validator that special-cases the
            suite's literal probe key still fails (F-222 FN-7)."""
            cls = kinds[kind][0]
            probe = _probe(kind, tmp_path)
            base = dict(probe.params) if probe is not None else {}
            for unknown in _unknown_keys(probe):
                before = set(cls.validate_params(dict(base)))
                after = set(cls.validate_params({**base, unknown: 1}))
                new = after - before
                assert any(unknown in p for p in new), (
                    f"{kind} ({cls.__name__}) accepted the unknown param "
                    f"{unknown!r} — validate_params must default-deny unknown "
                    f"keys by name. Problems gained: {sorted(new)}"
                )

        # -- probe-driven: params ------------------------------------------

        @pytest.mark.parametrize("kind", names)
        def test_the_probes_params_validate_clean(self, kind, tmp_path):
            """Guards every other probe-driven check: they must exercise a
            node the package agrees is well-formed."""
            probe = _need(kind, tmp_path)
            assert kinds[kind][0].validate_params(dict(probe.params)) == []

        @pytest.mark.parametrize("kind", names)
        def test_required_params_refuse_at_plan_time(self, kind, tmp_path):
            """A param the node cannot run without must be refused by
            ``validate_params`` — which the planner calls BEFORE the first
            scan, fit or solve. Discovering it at execute is discovering
            it after the expensive part (I-001, F-220)."""
            probe = _need(kind, tmp_path)
            cls = kinds[kind][0]
            if not probe.required:
                pytest.skip(f"{kind}: probe declares no required params")
            for missing in probe.required:
                params = {k: v for k, v in probe.params.items() if k != missing}
                problems = cls.validate_params(params)
                assert any(missing in p for p in problems), (
                    f"{kind}: dropping the required param {missing!r} validated "
                    f"clean at plan time — problems: {problems}"
                )

        @pytest.mark.parametrize("kind", names)
        def test_every_declared_knob_is_reachable_from_the_class(self, kind):
            """A knob a kind ALLOWS and no code reads is the
            silently-ignored-knob lie one step removed (I-227 gap 1): the
            document sets it, validation passes, and the run behaves as
            though it were never there — which is exactly what
            default-deny was built to stop.

            The bar is reachability, not use: the literal must appear in
            the class's own code or in a module-level table that code
            names (see :func:`_reachable_knob_names` for why the second
            half is not optional). What fails is a knob mentioned NOWHERE
            — the typo on the allowed list, or the leftover of a reader
            somebody deleted.
            """
            cls = kinds[kind][0]
            declared = _class_knobs(cls)
            if not declared:
                pytest.skip(f"{kind}: declares no knob table to check")
            reachable = _reachable_knob_names(cls)
            orphans = sorted(declared - reachable)
            assert not orphans, (
                f"{kind} ({cls.__name__}) allows knob(s) {orphans} that appear "
                "nowhere in the class's code, nor in any module-level table it "
                "references — a document may set them and nothing will read "
                "them. Either read the knob, or drop it from the allowed list."
            )

        # -- probe-driven: identity ----------------------------------------

        @pytest.mark.parametrize("kind", _of_role(*FINGERPRINTED_ROLES))
        def test_fingerprint_is_implemented(self, kind):
            """The driver hashes ``data`` AND ``labels`` fingerprints into
            the run identity. A role that declines the hook contributes
            nothing, so two runs over different data — or the same ladder
            with every outcome FLIPPED — hash identically (F-220)."""
            if kind is None:
                pytest.skip(f"registry declares no {list(FINGERPRINTED_ROLES)} kinds")
            cls = kinds[kind][0]
            assert cls.fingerprint is not Node.fingerprint, (
                f"{kind} ({cls.__name__}) has role {cls.role!r} but does not "
                "override fingerprint() — its data never reaches the run identity"
            )

        @pytest.mark.parametrize("kind", _of_role(*FINGERPRINTED_ROLES))
        def test_fingerprint_is_json_small_and_deterministic(self, kind, tmp_path):
            """It is hashed into the run identity and written to
            ``resolved.json``: it must serialize, stay small enough to
            read, and be identical for two nodes over the same data."""
            if kind is None:
                pytest.skip(f"registry declares no {list(FINGERPRINTED_ROLES)} kinds")
            probe = _need(kind, tmp_path, "make")
            first, second = probe.make().fingerprint(), probe.make().fingerprint()
            assert first is not None, f"{kind}: fingerprint() answered None"
            text = json.dumps(first, sort_keys=True, allow_nan=False)
            assert first == second, (
                f"{kind}: two nodes over the same data fingerprinted "
                "differently — identity is not reproducible"
            )
            assert len(text) <= max_fingerprint_bytes, (
                f"{kind}: fingerprint is {len(text)} bytes, over the "
                f"{max_fingerprint_bytes} limit — it is a JSON-SMALL summary, "
                "not the data"
            )

        @pytest.mark.parametrize("kind", _of_role(*FINGERPRINTED_ROLES))
        def test_fingerprint_moves_when_the_data_moves(self, kind, tmp_path):
            """The one that matters. Counts and extents do not move when a
            price is rewritten IN PLACE, so a count-only fingerprint is
            content-blind: every downstream number changes while identity
            says nothing happened (F-220 #3).

            The probe contract carries the proof: ``move()`` mutates the
            dataset ``make()`` reads, so both fingerprints come from the
            SAME params over different content. A fingerprint that echoes
            its params cannot pass (F-222 FN-1).
            """
            if kind is None:
                pytest.skip(f"registry declares no {list(FINGERPRINTED_ROLES)} kinds")
            probe = _need(kind, tmp_path, "make", "move")
            ctx = _ctx(probe, tmp_path)
            base_size = None
            if probe.size is not None:
                node = probe.make()
                node.fingerprint()
                base_size = probe.size(node.run(ctx, {}))
            before = probe.make().fingerprint()
            probe.move()
            after = probe.make().fingerprint()
            if base_size is not None:
                # move() must change CONTENT, not shape: an append is
                # grow()'s job, and an append-shaped move lets a
                # counts-only fingerprint ride the count change straight
                # through this check (F-222 round-2 GAME-5).
                node = probe.make()
                node.fingerprint()
                moved_size = probe.size(node.run(ctx, {}))
                assert moved_size == base_size, (
                    f"{kind}: the probe's move() changed the dataset's SIZE "
                    f"({base_size} -> {moved_size}) — move() must rewrite "
                    "content in place (same shape); appending is grow()'s "
                    "job, and a size change lets a counts-only fingerprint "
                    "pass. Fix the probe."
                )
            assert before != after, (
                f"{kind}: the dataset's content changed IN PLACE and the "
                "fingerprint did not — either the fingerprint is content-blind "
                "(two datasets would share a run identity and a run dir), or "
                "the probe's move() changed nothing / the class caches its "
                "scan at class level. All three need fixing."
            )

        @pytest.mark.parametrize("kind", _of_role("data"))
        def test_run_consumes_exactly_what_was_fingerprinted(self, kind, tmp_path):
            """Identity is taken at RESOLVE, records are read at EXECUTE,
            and recorders write LIVE. A node that re-reads at execute
            consumes data its identity never saw (F-220 #7).

            The comparison covers EVERY declared output via ``digest``
            (or dict equality) — a node that pins ``records`` but
            re-reads its ``instruments`` universe fails (F-222 FN-6),
            and each return is held to the declared output contract on
            the way through (F-222 FN-3).
            """
            if kind is None:
                pytest.skip("registry declares no data kinds")
            probe = _need(kind, tmp_path, "make", "grow", "size")
            ctx = _ctx(probe, tmp_path)
            # The node the driver keeps: fingerprinted BEFORE the write,
            # run AFTER it — exactly the driver's resolve/execute straddle.
            pinned = probe.make()
            before = pinned.fingerprint()
            # What the pre-write data holds, measured independently.
            baseline = probe.make()
            baseline.fingerprint()
            base_out = baseline.run(ctx, {})
            problems = _contract_problems(kinds[kind][0], baseline, base_out)
            assert problems == [], (
                f"{kind}: run() broke its declared output contract: {problems}"
            )
            coverage = _digest_covers_declared(probe, kinds[kind][0], base_out)
            assert coverage == [], f"{kind}: {coverage}"

            # Between resolve and execute, the store changes BOTH ways a
            # real writer can change it: grow() appends (the live
            # recorder) and move() rewrites content in place (the
            # backfill). Append-only drift is not enough — a node that
            # re-reads the store and returns its first N rows is
            # indistinguishable from a pinned one under pure appends
            # (F-222 round-2 GAME-5), but not under a rewrite.
            if probe.move is not None:
                probe.move()
            probe.grow()

            fresh = probe.make()
            fresh_fp = fresh.fingerprint()
            assert fresh_fp != before, (
                f"{kind}: after grow(), a FRESH node's fingerprint is "
                "unchanged — either the probe's grow() added nothing, or the "
                "class caches its scan at CLASS level so no new instance can "
                "see new data. Investigate both; neither is conformant."
            )
            fresh_out = fresh.run(ctx, {})
            assert probe.size(fresh_out) > probe.size(base_out), (
                f"{kind}: grow() moved the fingerprint but a fresh node's "
                f"run() returned no more data ({probe.size(base_out)} -> "
                f"{probe.size(fresh_out)}) — either the probe's size() is not "
                "measuring the grown output, or the class memoises run() "
                "across instances so no fresh node can return new data. "
                "Investigate both; neither is conformant."
            )
            # A digest that cannot tell grown data from the baseline is
            # not measuring the outputs at all — the constant-digest
            # neutering (F-222 round-2 GAME-1) fails HERE, before it gets
            # to vouch for the pinned comparison below.
            assert not _digests_equal(probe, fresh_out, base_out), (
                f"{kind}: the probe's digest cannot distinguish the grown "
                "dataset from the baseline — it is not measuring the "
                "declared outputs (fix the probe's digest)"
            )
            pinned_out = pinned.run(ctx, {})
            pinned_problems = _contract_problems(kinds[kind][0], pinned, pinned_out)
            assert pinned_problems == [], (
                f"{kind}: run() broke its declared output contract: {pinned_problems}"
            )
            assert _digests_equal(probe, pinned_out, base_out), (
                f"{kind}: the node's execute-time return does not match the "
                "resolve-time snapshot — EITHER run() re-read the store and "
                "consumed data the run identity never covered, OR the output "
                "payloads do not define value equality (dataclass(eq=False), "
                "array types) and the default comparison cannot see through "
                "them: supply probe.digest covering every declared output"
            )
            assert pinned.fingerprint() == before, (
                f"{kind}: the fingerprint moved under the node between resolve "
                "and execute — identity must describe one snapshot"
            )

        @pytest.mark.parametrize("kind", _of_role("labels"))
        def test_labels_run_returns_exactly_what_was_fingerprinted(
            self, kind, tmp_path
        ):
            """The labels straddle (I-229). Outcomes are fingerprinted into
            the run identity at RESOLVE and read at EXECUTE, and a
            settlement store settles MORE in between — so the node the
            driver keeps must answer from the snapshot its identity
            covered, while a FRESH node sees the growth.

            This is the same shape as the ``data`` check above, and it is
            here because it was NOT: S4-F4 shipped (the driver rebuilt
            labels at execute, feeding a run outcomes its identity never
            hashed) and the bar had no way to notice. Fixing the driver
            fixed that instance; this check is what refuses the next one.
            """
            if kind is None:
                pytest.skip("registry declares no labels kinds")
            probe = _need(kind, tmp_path, "make", "grow")
            ctx = _ctx(probe, tmp_path)
            inputs = dict(probe.inputs or {})
            cls = kinds[kind][0]
            # Fingerprinted BEFORE the store grows, run AFTER it.
            pinned = probe.make()
            before = pinned.fingerprint()
            baseline = probe.make()
            baseline.fingerprint()
            base_out = baseline.run(ctx, dict(inputs))
            problems = _contract_problems(cls, baseline, base_out)
            assert problems == [], (
                f"{kind}: run() broke its declared output contract: {problems}"
            )

            probe.grow()

            fresh = probe.make()
            fresh_fp = fresh.fingerprint()
            assert fresh_fp != before, (
                f"{kind}: after grow(), a FRESH node's fingerprint is "
                "unchanged — either the probe's grow() settled no new "
                "outcome, or the class caches its scan at CLASS level so no "
                "new instance can see it. Investigate both; neither is "
                "conformant."
            )
            fresh_out = fresh.run(ctx, dict(inputs))
            assert not _digests_equal(probe, fresh_out, base_out), (
                f"{kind}: grow() moved the fingerprint but a fresh node's "
                "run() returned the same outcomes — either the probe's "
                "grow() is not visible through run(), or the class memoises "
                "its answer ACROSS instances (which would also freeze the "
                "next run of the series)."
            )
            pinned_out = pinned.run(ctx, dict(inputs))
            pinned_problems = _contract_problems(cls, pinned, pinned_out)
            assert pinned_problems == [], (
                f"{kind}: run() broke its declared output contract: {pinned_problems}"
            )
            assert _digests_equal(probe, pinned_out, base_out), (
                f"{kind}: the labels node's execute-time outcomes do not "
                "match the resolve-time snapshot — the run is being scored "
                "against outcomes its identity never hashed (I-229/S4-F4). "
                "One instance must be one view: memoise the scan on the "
                "INSTANCE at first read."
            )
            assert pinned.fingerprint() == before, (
                f"{kind}: the fingerprint moved under the node between "
                "resolve and execute — identity must describe one snapshot"
            )

        # -- probe-driven: inputs ------------------------------------------

        @pytest.mark.parametrize("kind", names)
        def test_the_probes_inputs_validate_clean(self, kind, tmp_path):
            """The companion guard to the params one: the one-shot check
            below only means something against inputs this kind accepts."""
            probe = _need(kind, tmp_path)
            if probe.inputs is None:
                pytest.skip(f"{kind}: probe supplies no inputs")
            node = _node(kind, probe)
            problems = node.validate_inputs(dict(probe.inputs))
            assert problems == [], f"{kind}: the probe's inputs are refused: {problems}"

        @pytest.mark.parametrize("kind", names)
        def test_validation_never_consumes_a_one_shot_input(self, kind, tmp_path):
            """``validate_inputs`` runs immediately before ``run`` on the
            SAME objects. A validator that walks a generator hands ``run``
            an exhausted stream: it sizes nothing, reports nothing and
            exits 0 (F-220 #6).

            Lawful answers, all accepted: REFUSE the one-shot iterable by
            name, RAISE on it (loud, not silent — F-222 P4), or leave it
            untouched. What is refused is consuming it silently.
            """
            probe = _need(kind, tmp_path)
            if probe.inputs is None or not probe.stream_ports:
                pytest.skip(f"{kind}: probe declares no stream ports")
            node = _node(kind, probe)
            for port in probe.stream_ports:
                items = list(probe.inputs[port])
                seen = []

                def _one_shot(items=items, seen=seen):
                    for item in items:
                        seen.append(item)
                        yield item

                inputs = dict(probe.inputs)
                inputs[port] = _one_shot()
                try:
                    problems = node.validate_inputs(inputs)
                # ANY raise is a lawful outcome here — loud, so run() never
                # sees the exhausted stream; only SILENT consumption fails.
                except Exception:  # noqa: BLE001, S112
                    continue
                assert problems or not seen, (
                    f"{kind}: validate_inputs consumed {len(seen)} item(s) of the "
                    f"one-shot input {port!r} without refusing it — run() would "
                    "receive an exhausted stream and silently do nothing"
                )

        # -- probe-driven: the run contract ---------------------------------

        @pytest.mark.parametrize("kind", names)
        def test_run_matches_the_declared_output_contract(self, kind, tmp_path):
            """Declared ``outputs`` match ``run()``'s return EXACTLY — the
            docs/25 §1 contract-drift row, checked against a real return,
            not against the validator alone (F-222 FN-3)."""
            probe = _need(kind, tmp_path)
            if not probe.runnable:
                pytest.skip(
                    f"{kind}: probe is not runnable — {_not_runnable_note(kind, probe)}"
                )
            node = _node(kind, probe)
            out = node.run(_ctx(probe, tmp_path), dict(probe.inputs or {}))
            problems = _contract_problems(kinds[kind][0], node, out)
            assert problems == [], (
                f"{kind}: run() returned "
                f"{sorted(out) if isinstance(out, dict) else type(out).__name__}, "
                f"breaking the declared contract "
                f"{sorted(kinds[kind][0].outputs or ())}: {problems}"
            )

        # -- roles: trainable ------------------------------------------------

        @pytest.mark.parametrize("kind", _of_role(*TRAINABLE_ROLES))
        def test_trainable_kinds_dispatch_through_the_base(self, kind):
            """The structural floor (ADR-0038): a trainable kind honours
            ``mode`` by INHERITING the dispatch, not by writing one.

            A class that carries no dispatch at all accepts ``mode="load"``,
            hashes it, and silently ignores it (F-220 #12). The bar used to
            sniff the compiled code for the name ``mode``, which only ever
            proved the class MENTIONED the field; now the type answers, and
            it answers about the two template methods as well — a kind that
            wraps either one has taken the dispatch back, and a wrapper is
            where a second opinion about ``mode`` quietly regrows.
            """
            if kind is None:
                pytest.skip(f"registry declares no {list(TRAINABLE_ROLES)} kinds")
            cls = kinds[kind][0]
            assert issubclass(cls, TrainableNode), (
                f"{kind} ({cls.__name__}) has trainable role {cls.role!r} but "
                "does not subclass TrainableNode — mode='load' would be "
                "accepted, hashed, and silently ignored. Subclass "
                "dskit.pipeline.node.TrainableNode and implement run_train / "
                "run_load."
            )
            assert cls.run is TrainableNode.run, (
                f"{kind} ({cls.__name__}) overrides run() — TrainableNode.run "
                "IS the mode dispatch, and a class that replaces it can drop "
                "or invert it unseen. Put the behaviour in run_train / "
                "run_load instead."
            )
            assert cls.validate_inputs is TrainableNode.validate_inputs, (
                f"{kind} ({cls.__name__}) overrides validate_inputs() — "
                "TrainableNode.validate_inputs dispatches BY mode, so an "
                "override re-imposes one set of demands on both modes (the "
                "load path then demands a wire it never reads). Override "
                "validate_common_inputs / validate_train_inputs / "
                "validate_load_inputs instead."
            )

        @pytest.mark.parametrize("kind", _of_role(*TRAINABLE_ROLES))
        def test_load_mode_loads_or_refuses(self, kind, tmp_path):
            """The behavioural half (F-222 FN-2): construct with
            ``mode="load"`` and RUN it. Two lawful outcomes — it raises,
            naming mode/load/artifact (the causal-beta shape: nothing
            persists, so restoring is impossible and saying so is
            honest), or it returns AND the probe's ``verify_loaded``
            proves a real restore. Returning without proof is the silent
            refit."""
            if kind is None:
                pytest.skip(f"registry declares no {list(TRAINABLE_ROLES)} kinds")
            probe = _need(kind, tmp_path)
            if probe.inputs is None or not probe.runnable:
                pytest.skip(
                    f"{kind}: probe is not runnable — {_not_runnable_note(kind, probe)}"
                )
            node = kinds[kind][0](
                kind.replace("-", "_"),
                dict(probe.params),
                mode="load",
                artifact=probe.load_artifact,
            )
            try:
                out = node.run(_ctx(probe, tmp_path), dict(probe.inputs))
            # ANY exception type may carry the refusal; what matters is
            # whether its message names mode/load/artifact.
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                assert any(w in message for w in ("load", "mode", "artifact")), (
                    f"{kind}: run() under mode='load' crashed with "
                    f"{type(exc).__name__}: {exc} — a refusal must NAME "
                    "mode/load/artifact; an unrelated crash is not a refusal"
                )
                return
            assert probe.verify_loaded is not None, (
                f"{kind}: run() accepted mode='load' and returned, but the "
                "probe supplies no verify_loaded — nothing distinguishes a "
                "real restore from the silent refit (F-220 #12). Supply "
                "verify_loaded, or make the node refuse."
            )
            assert probe.verify_loaded(out), (
                f"{kind}: mode='load' returned outputs that verify_loaded "
                "rejects — the node refitted instead of restoring the pinned "
                "artifact"
            )
            # The proof must DISCRIMINATE: a verify_loaded that also
            # blesses a fresh fit is a rubber stamp, and a rubber stamp
            # is the silent refit with paperwork (F-222 round-2 GAME-3).
            trainer = kinds[kind][0](
                kind.replace("-", "_"), dict(probe.params), mode="train"
            )
            try:
                fresh_fit = trainer.run(_ctx(probe, tmp_path), dict(probe.inputs))
            except Exception:  # noqa: BLE001 - no refit sample obtainable; loud is fine
                return
            assert not probe.verify_loaded(fresh_fit), (
                f"{kind}: verify_loaded accepts a FRESH mode='train' fit as "
                "readily as the restore — it proves nothing. Make the loaded "
                "output carry provenance (e.g. the artifact reference) and "
                "verify THAT."
            )

        # -- roles: capital ---------------------------------------------------

        @pytest.mark.parametrize("kind", _of_role("capital"))
        def test_capital_refuses_to_plan_without_a_stat_test_wire(self, kind, tmp_path):
            """Un-gated capital must not plan. This is the PLANNER's rule
            keyed off the class's declared role — what this proves for
            the class is that its role survives planning integration; the
            per-class teeth are the budget and empty-gate checks below."""
            if kind is None:
                pytest.skip("registry declares no capital kinds")
            probe = _probe(kind, tmp_path)
            params = dict(probe.params) if probe is not None else {}
            private = NodeKindRegistry()
            for other, (cls, owned) in kinds.items():
                private.register(other, cls, owned=owned)
            doc = PipelineDocument.from_obj(
                {
                    "name": "conformance",
                    "pipeline": {"cap": {"uses": kind, "params": params}},
                    "splits": {
                        "kind": "time",
                        "train_end_ms": 1,
                        "val_end_ms": 2,
                        "test_end_ms": 3,
                    },
                }
            )
            with pytest.raises(ConfigError) as excinfo:
                plan(doc, registry=private)
            assert any("stat_test" in p for p in excinfo.value.errors), (
                f"{kind}: planned with no stat_test wire — capital was not "
                f"gated. Problems raised: {excinfo.value.errors}"
            )

        @pytest.mark.parametrize("kind", _of_role("capital"))
        def test_capital_stays_inside_the_declared_budget(self, kind, tmp_path):
            """The docs/25 §1 row the first cut never checked (F-222
            FN-5): a REAL run's outlay must not exceed the declared
            budget. F-220 #1 shipped orders costing 19x the deployable —
            with 100% test coverage."""
            if kind is None:
                pytest.skip("registry declares no capital kinds")
            probe = _need(kind, tmp_path, "outlay")
            if probe.budget is None:
                if probe.no_budget_reason:
                    pytest.skip(
                        f"{kind}: probe declares no bounded outlay — "
                        f"{probe.no_budget_reason}"
                    )
                pytest.fail(
                    f"{kind}: capital probe carries neither a budget nor a "
                    "no_budget_reason — opting out of the budget bound must "
                    "be written down"
                )
            if probe.inputs is None or not probe.runnable:
                pytest.skip(
                    f"{kind}: probe is not runnable — {_not_runnable_note(kind, probe)}"
                )
            node = _node(kind, probe)
            out = node.run(_ctx(probe, tmp_path), dict(probe.inputs))
            spent = float(probe.outlay(out))
            assert math.isfinite(spent) and spent >= 0.0, spent
            assert spent <= probe.budget * (1 + 1e-9), (
                f"{kind}: run() deployed {spent} against a declared budget of "
                f"{probe.budget} — the budget did not bind (F-220 #1)"
            )

        @pytest.mark.parametrize("kind", _of_role("capital"))
        def test_capital_deploys_nothing_when_the_gate_clears_no_one(
            self, kind, tmp_path
        ):
            """The gate wire must be READ, not merely present: with the
            stat_test survivors emptied, a conformant capital node
            deploys zero (or refuses loudly). A node that sizes anyway is
            un-gated capital wearing a gated document (F-222 FN-5)."""
            if kind is None:
                pytest.skip("registry declares no capital kinds")
            probe = _need(kind, tmp_path, "outlay", "gate_port")
            if probe.inputs is None or not probe.runnable:
                pytest.skip(
                    f"{kind}: probe is not runnable — {_not_runnable_note(kind, probe)}"
                )
            node = _node(kind, probe)
            inputs = dict(probe.inputs)
            inputs[probe.gate_port] = []
            try:
                out = node.run(_ctx(probe, tmp_path), inputs)
            # A raise of any type refuses the empty gate LOUDLY — lawful;
            # only sizing anyway (silently) fails the check below.
            except Exception:  # noqa: BLE001
                return
            spent = float(probe.outlay(out))
            assert spent == 0.0, (
                f"{kind}: the stat_test gate cleared NO instruments and run() "
                f"still deployed {spent} — the survivors wire is decoration, "
                "not a gate"
            )

        # -- the bar cannot be dodged by omission ---------------------------

        def test_every_probe_is_populated_for_its_role(self, tmp_path):
            """A behavioural check with no probe input does not run, so an
            ABSENT OR EMPTY probe is a silent hole in the bar (F-222
            FN-4: an empty ``NodeProbe()`` satisfied the old existence
            check while every behavioural invariant skipped). Population
            is per-role; the failure names every missing field."""
            gaps = {}
            for kind in names:
                probe = _probe(kind, tmp_path)
                role = kinds[kind][0].role
                if probe is None:
                    gaps[kind] = ["no probe at all"]
                    continue
                missing = [
                    f
                    for f in _ROLE_POPULATION.get(role, ())
                    if getattr(probe, f) is None
                ]
                if (
                    role == "capital"
                    and probe.budget is None
                    and not probe.no_budget_reason
                ):
                    missing.append("budget (or a written no_budget_reason)")
                if (
                    role in _MUST_RUN_ROLES
                    and not probe.runnable
                    and not probe.not_runnable_reason
                ):
                    missing.append(
                        "runnable=True (or a written not_runnable_reason) — "
                        "the load/budget/gate teeth only bite on a real run"
                    )
                if probe.inputs is not None and probe.stream_ports is None:
                    missing.append(
                        "stream_ports (declare explicitly; () means 'no stream here')"
                    )
                if missing:
                    gaps[kind] = missing
            if not require_probes:
                pytest.skip(f"require_probes=False; probe gaps: {gaps}")
            assert not gaps, (
                "probes missing or under-populated for their roles — the "
                "behavioural invariants cannot run without these fields: "
                f"{gaps}. Populate them, or pass require_probes=False "
                "deliberately."
            )

    Suite.__name__ = Suite.__qualname__ = name
    return Suite
