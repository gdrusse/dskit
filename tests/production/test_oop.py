"""§5.15's four pillars, enforced — the shape of the package rather than its behaviour.

Every other file in this directory proves what one module *does*. This one
proves the package is still built the way §5.15 rules it must be, because the
defects §5.15 exists to prevent are the ones no behavioural test can see: a
hook that raises `NotImplementedError` instead of being `@abstractmethod` lets
an incomplete venue construct and fail at the first live tick; a module that
instantiates `PaperExecutor` by name works perfectly until a child needs its
own; a subclass of `ActionPolicy` re-derives a permission the matrix owns.

What each assertion below can only pass for one reason:

* **Abstraction.** The twenty registry-resolved seam ABCs of §5.15 are pinned
  name by name against the twenty §4.3 registries, in BOTH directions — a
  registry added without a seam ABC fails, and an ABC whose registry moved
  fails. The seven structural ABCs are declared separately so the twenty is
  not mistaken for the whole abstract surface (§5.15 names them for exactly
  that reason), and asserted to be no registry's family.
* **`@abstractmethod`, not `NotImplementedError`.** Every seam ABC refuses
  instantiation, and the refusal must name abstractness — a `TypeError` from
  a missing constructor argument would pass a weaker test for the wrong
  reason.
* **Polymorphism.** No module instantiates a registry family member by name:
  the document names *what*, the registry answers *which class*. `compose.py`
  is exempt because resolving is its job.
* **Inheritance where it is refused.** `ServeLoop`, `GuardChain` and the two
  policies are never subclassed in shipped code, and `Tick.run` /
  `LegPipeline.run` refuse an override at class creation rather than at the
  first tick.
* **Liskov.** Every `SubmittingExecutor` subclass takes the base
  `submit(intent, permit, state)` — no subclass demands more — and answers a
  permission fact with an `Ack`, never a raise.
* **Encapsulation.** No class that subclasses a seam reaches a private name of
  another module.

The AST-driven assertions each carry a self-check that runs the same detector
over a synthetic violation, so a scan that silently stopped matching cannot
report a clean tree.
"""

import ast
import dataclasses
import importlib
import inspect
import pathlib
import pkgutil

import pytest

import dskit.production as production
from dskit.pipeline.policy import ExecutionPolicy
from dskit.production.accounting import Accounting
from dskit.production.alerts import AlertSink
from dskit.production.arming import ApprovalVerifier
from dskit.production.base import ProductionError, Registry
from dskit.production.cadence import Cadence
from dskit.production.clock import Clock, TestClock
from dskit.production.coordination import Lease
from dskit.production.decider import Proposer
from dskit.production.executor import (
    EXECUTOR_KINDS,
    Executor,
    Fee,
    LiveExecutor,
    SubmittingExecutor,
)
from dskit.production.feed import Feed
from dskit.production.guards import Guard, GuardChain, Measure
from dskit.production.health import HealthProbe, HeartbeatEmitter
from dskit.production.ids import IdSource
from dskit.production.ledger import Ledger
from dskit.production.leg import Authority, LegPipeline
from dskit.production.loop import ServeLoop, Tick
from dskit.production.monitors import Chunker, Monitor, Reference, Threshold
from dskit.production.outcomes import OutcomeSource
from dskit.production.policy import ActionPolicy, TransitionPolicy
from dskit.production.records import Ack
from dskit.production.resilience import Classifier, Transport
from dskit.production.sessions import Calendar
from tests.production.test_executor import intent, tick_state

# ---------------------------------------------------------------------------
# The package under inspection
# ---------------------------------------------------------------------------

#: The package's own source tree. `libs/` is phase 2 and has no modules yet;
#: every scan below walks the modules that exist.
PACKAGE_DIR = pathlib.Path(production.__file__).parent

#: `compose.py` is the one module whose JOB is to turn a document's `uses`
#: into objects, so it is the one module allowed to hold a family member by
#: name (§5.15, §8). Every other module must be handed its collaborators.
COMPOSER = "compose.py"

#: The registry-resolved seam ABCs of §5.15, each with the §4.3 registry
#: that resolves it and the module both live in — twenty in phase 1, plus
#: `OutcomeSource`, which §4.3 names as one of the three families phase 2
#: adds. Restated here INDEPENDENTLY of the package: a list read from its
#: subject asserts nothing (CLAUDE.md, "deliberate independent
#: restatement"), and this table is one half of the two-way pin the section
#: asks for.
SEAM_ABCS = (
    ("Clock", "clock", "CLOCK_KINDS"),
    ("Calendar", "sessions", "CALENDAR_KINDS"),
    ("Cadence", "cadence", "CADENCE_KINDS"),
    ("Feed", "feed", "FEED_KINDS"),
    ("Proposer", "decider", "PROPOSER_KINDS"),
    ("Guard", "guards", "GUARD_KINDS"),
    ("Measure", "guards", "MEASURE_KINDS"),
    ("Executor", "executor", "EXECUTOR_KINDS"),
    ("Accounting", "accounting", "ACCOUNTING_KINDS"),
    ("Lease", "coordination", "LEASE_KINDS"),
    ("Monitor", "monitors", "MONITOR_KINDS"),
    ("Reference", "monitors", "REFERENCE_KINDS"),
    ("Chunker", "monitors", "CHUNKER_KINDS"),
    ("Threshold", "monitors", "THRESHOLD_KINDS"),
    ("AlertSink", "alerts", "ALERT_SINK_KINDS"),
    ("HealthProbe", "health", "PROBE_KINDS"),
    ("Transport", "resilience", "TRANSPORT_KINDS"),
    ("ApprovalVerifier", "arming", "APPROVAL_KINDS"),
    ("Fee", "executor", "FEE_KINDS"),
    ("HeartbeatEmitter", "health", "HEARTBEAT_KINDS"),
    ("OutcomeSource", "outcomes", "OUTCOME_SOURCE_KINDS"),
)

#: The seven ABCs §5.15 calls structural rather than registry-resolved, named
#: so the twenty above is not read as the whole abstract surface. Phase 1
#: ships one implementation of `Ledger` and `Classifier`, `SubmittingExecutor`
#: and the abstract `LiveExecutor` split the executor contract (§5.7),
#: `IdSource` and `Authority` are closed to core, and `ExecutionPolicy` is the
#: pipeline-side seam of §9.1.
STRUCTURAL_ABCS = (
    SubmittingExecutor,
    LiveExecutor,
    Ledger,
    Classifier,
    IdSource,
    Authority,
    ExecutionPolicy,
)

#: The seam ABCs by name, so the table above resolves without importing
#: through the module it is pinning.
ABC_BY_NAME = {
    cls.__name__: cls
    for cls in (
        Clock, Calendar, Cadence, Feed, Proposer, Guard, Measure, Executor,
        Accounting, Lease, Monitor, Reference, Chunker, Threshold, AlertSink,
        HealthProbe, Transport, ApprovalVerifier, Fee, HeartbeatEmitter,
        OutcomeSource,
    )
}

#: Composites and final walks a child may compose but never subclass
#: (§5.15, "Where inheritance is deliberately refused").
NEVER_SUBCLASSED = ("ServeLoop", "GuardChain", "ActionPolicy", "TransitionPolicy")

#: Where shipped code lives. `tests/` is deliberately outside it: the suites
#: subclass `ActionPolicy` and `LegPipeline` to spy on real rules, which is
#: what a test double is for — the rule is about what SHIPS.
SHIPPED_ROOTS = ("dskit", "children", "examples")

#: The one function in the package that builds a family member by name: the
#: conformance battery's own deterministic clock (§5.7). No document selects
#: it, and a caller supplying its own venue passes `build=`. Named as a
#: (module, function) pair so every other function in `executor.py` is
#: still scanned.
CONFORMANCE_CLOCK = ("executor.py", "executor_conformance_suite")

#: `dskit.assets.base`'s checkers, re-exported by `production.base` so the
#: three packages share ONE checker vocabulary (§8, and `base.py`'s own
#: docstring). They are private by name and public by contract, so the
#: encapsulation scan below reads them as the shared vocabulary they are.
SHARED_CHECKERS = ("_check_str", "_check_dict", "_check_unknown", "_raise_if")


def modules():
    """Import and return every module of `dskit.production`, keyed by short name."""
    found = {}
    for info in pkgutil.iter_modules([str(PACKAGE_DIR)]):
        if info.ispkg:
            continue
        found[info.name] = importlib.import_module(f"dskit.production.{info.name}")
    return found


MODULES = modules()


def sources():
    """Return `(path, parsed tree)` for every module of the package."""
    return [(path, ast.parse(path.read_text())) for path in sorted(PACKAGE_DIR.glob("*.py"))]


def registries():
    """Return every distinct `Registry` the package holds, keyed by its variable name."""
    found = {}
    for module in MODULES.values():
        for name, value in vars(module).items():
            if isinstance(value, Registry):
                found.setdefault(name, value)
    return found


# ---------------------------------------------------------------------------
# Abstraction — every seam ABC is abstract, and refuses to be built
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [row[0] for row in SEAM_ABCS])
def test_every_seam_abc_declares_at_least_one_abstract_hook(name):
    """§5.15: "Each seam ABC declares its hooks `@abstractmethod` so an
    incomplete subclass fails at construction, not at the first live tick."
    A hook that only raised `NotImplementedError` would let the incomplete
    subclass construct."""
    cls = ABC_BY_NAME[name]
    assert inspect.isabstract(cls)
    assert cls.__abstractmethods__


@pytest.mark.parametrize("cls", STRUCTURAL_ABCS, ids=lambda cls: cls.__name__)
def test_every_structural_abc_declares_at_least_one_abstract_hook(cls):
    """The same rule for the seven ABCs §5.15 calls structural: they carry no
    `uses` site, but an incomplete `Ledger` or `Authority` is exactly as
    dangerous as an incomplete venue."""
    assert inspect.isabstract(cls)
    assert cls.__abstractmethods__


@pytest.mark.parametrize(
    "cls",
    [ABC_BY_NAME[row[0]] for row in SEAM_ABCS] + list(STRUCTURAL_ABCS),
    ids=lambda cls: cls.__name__,
)
def test_a_seam_abc_refuses_instantiation_for_being_abstract(cls):
    """The refusal must be about abstractness. A `TypeError` for a missing
    constructor argument would satisfy a weaker assertion while proving
    nothing about the hooks."""
    with pytest.raises(TypeError, match="abstract"):
        cls()


def test_the_structural_abcs_are_no_registrys_family():
    """§5.15 names them "structural rather than registry-resolved" — the split
    is what makes "twenty" a count of the document's selectable families
    rather than of the package's abstract classes."""
    families = {registry.abc for registry in registries().values()}
    assert families.isdisjoint(set(STRUCTURAL_ABCS))


# ---------------------------------------------------------------------------
# Abstraction — the twenty ABCs and the twenty registries pin each other
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,module,registry", SEAM_ABCS)
def test_each_seam_abc_is_its_registrys_family_in_its_own_module(name, module, registry):
    """§4.3: "each family has its own registry", and it "lives with its seam".
    A registry whose `abc` moved would resolve a `uses` against the wrong
    family — the one check that a `pkg.module:Class` reference is refused for
    naming the wrong kind of class."""
    home = MODULES[module]
    cls = ABC_BY_NAME[name]
    assert cls.__module__ == f"dskit.production.{module}"
    found = vars(home)[registry]
    assert isinstance(found, Registry)
    assert found.abc is cls


def test_the_package_holds_exactly_the_declared_registries():
    """The other half of the pin: §4.3 lists the families and §5.15 lists
    the ABCs "and a test pins the two lists against each other". A registry
    added without a seam ABC — or without §4.3 — fails here."""
    assert set(registries()) == {row[2] for row in SEAM_ABCS}


def test_every_registered_kind_is_a_subclass_of_its_family():
    """`Registry.register` refuses a foreign class; this is the standing proof
    that every core kind actually shipped honours it."""
    for name, registry in registries().items():
        for kind in registry.kinds():
            assert issubclass(registry.resolve(kind), registry.abc), (name, kind)


# ---------------------------------------------------------------------------
# Polymorphism — a family member is resolved, never named
# ---------------------------------------------------------------------------


def family_members():
    """Return the class name of every kind registered in any family."""
    return {
        registry.resolve(kind).__name__
        for registry in registries().values()
        for kind in registry.kinds()
    }


def named_instantiations(tree, members, exempt=()):
    """Return `(line, class name)` for every call of a family member by name."""
    hits = []

    def visit(node, allowed, inside_exempt):
        """Recurse, narrowing what may be named inside a class and inside an exempt function."""
        if isinstance(node, ast.ClassDef):
            # A class deriving a new instance of ITSELF (`Limit.with_overlay`)
            # is not a caller choosing a family member; it is a copy
            # constructor, and the choice was made where it was built.
            allowed = allowed - {node.name}
        if isinstance(node, ast.FunctionDef) and node.name in exempt:
            inside_exempt = True
        if (
            not inside_exempt
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in allowed
        ):
            hits.append((node.lineno, node.func.id))
        for child in ast.iter_child_nodes(node):
            visit(child, allowed, inside_exempt)

    visit(tree, set(members), False)
    return sorted(hits)


def scan_named_instantiations():
    """Return every `module:line: Class(...)` a family member is built by name at."""
    members = family_members()
    found = []
    for path, tree in sources():
        if path.name == COMPOSER:
            continue
        exempt = (CONFORMANCE_CLOCK[1],) if path.name == CONFORMANCE_CLOCK[0] else ()
        found += [
            f"{path.name}:{line}: {name}(...)"
            for line, name in named_instantiations(tree, members, exempt)
        ]
    return sorted(found)


def test_no_registry_family_member_is_instantiated_by_name():
    """§5.15: "The serve document names *what* … no caller may instantiate a
    concrete class by name." A module holding `PaperExecutor(...)` works until
    a child needs its own venue, and then the seam is not a seam."""
    assert scan_named_instantiations() == []


def test_the_instantiation_scan_catches_a_module_that_names_a_family_member():
    """The detector is the assertion above, so it is tested on a violation:
    a scan that stopped matching would report a clean tree forever."""
    tree = ast.parse("def build():\n    return PaperExecutor({}, clock=None)\n")
    assert named_instantiations(tree, {"PaperExecutor"}) == [(2, "PaperExecutor")]


def test_the_scan_exempts_only_the_composer_a_copy_constructor_and_the_battery():
    """Three exemptions, each narrow by construction: `compose.py` resolves
    for a living; a class deriving a new instance of itself made no choice;
    and the conformance battery's own `TestClock` is a test fixture no
    document selects, exempted by (module, function) so every other function
    in `executor.py` is still scanned."""
    assert (PACKAGE_DIR / COMPOSER).exists()
    tree = ast.parse("class Limit:\n    def with_overlay(self):\n        return Limit({})\n")
    assert named_instantiations(tree, {"Limit"}) == []
    battery = ast.parse("def executor_conformance_suite():\n    return TestClock(0)\n")
    assert named_instantiations(battery, {"TestClock"}) == [(2, "TestClock")]
    assert named_instantiations(battery, {"TestClock"}, (CONFORMANCE_CLOCK[1],)) == []
    module, function = CONFORMANCE_CLOCK
    assert function in (MODULES[module.removesuffix(".py")].__dict__)


# ---------------------------------------------------------------------------
# Inheritance — where it is deliberately refused
# ---------------------------------------------------------------------------


def subclass_sites(names, roots=SHIPPED_ROOTS):
    """Return every `path:line: Sub(Base)` where shipped code subclasses one of `names`."""
    found = []
    for root in roots:
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for base in node.bases:
                    base_name = base.attr if isinstance(base, ast.Attribute) else getattr(
                        base, "id", None
                    )
                    if base_name in names:
                        found.append(f"{path}:{node.lineno}: {node.name}({base_name})")
    return sorted(found)


def test_the_loop_the_chain_and_the_policies_are_never_subclassed():
    """§5.15: "Children never subclass `ServeLoop`, `GuardChain` or any
    policy — the extension points are the ABCs above plus `Proposer` and
    `Measure`." A subclassed policy is a second copy of the matrix."""
    # The restated names are pinned to the classes first: a renamed policy
    # would otherwise leave the scan below looking clean forever.
    assert {cls.__name__ for cls in (ServeLoop, GuardChain, ActionPolicy, TransitionPolicy)} == set(
        NEVER_SUBCLASSED
    )
    assert subclass_sites(NEVER_SUBCLASSED) == []


def test_the_subclass_scan_would_see_one():
    """The detector, on a tree that does subclass a policy."""
    assert ActionPolicy.__name__ in NEVER_SUBCLASSED
    tree = ast.parse("class Mine(ActionPolicy):\n    pass\n")
    node = [n for n in tree.body if isinstance(n, ast.ClassDef)][0]
    assert node.bases[0].id in NEVER_SUBCLASSED


@pytest.mark.parametrize("cls", (Tick, LegPipeline), ids=lambda cls: cls.__name__)
def test_the_walk_is_final_and_refuses_an_override_at_class_creation(cls):
    """§5.15: `Tick` and `LegPipeline` "are concrete classes with final `run`
    methods" — the invariant each carries is the ORDER `run` walks, so a
    subclass that could reorder or skip a barrier must be impossible rather
    than discouraged. The steps stay overridable; that is the seam."""
    with pytest.raises(ProductionError, match="run"):
        type("Reordered", (cls,), {"run": lambda self: None})


@pytest.mark.parametrize("cls", (Tick, LegPipeline), ids=lambda cls: cls.__name__)
def test_a_subclass_that_replaces_a_step_is_still_allowed(cls):
    """The other half: making `run` final would be useless if it also froze
    the steps, which is what replay and the test doubles override."""
    step = "guard" if cls is LegPipeline else "fetch"
    assert type("Stepped", (cls,), {step: lambda self, *args: None})


def test_serve_loop_composes_its_collaborators_rather_than_inheriting_them():
    """§5.15: "`ServeLoop` **contains** its clock, feed, executor and policies
    rather than subclassing anything, which is why D2 can forbid a mode
    branch"."""
    assert ServeLoop.__bases__ == (object,)
    assert GuardChain.__bases__ == (object,)


# ---------------------------------------------------------------------------
# Liskov — the base `submit` contract every venue honours
# ---------------------------------------------------------------------------


def submitting_executors():
    """Return every `SubmittingExecutor` subclass the package defines."""
    found = {}
    for name, module in MODULES.items():
        for value in vars(module).values():
            if (
                inspect.isclass(value)
                and issubclass(value, SubmittingExecutor)
                and value.__module__ == f"dskit.production.{name}"
            ):
                found[value] = value.__name__
    return sorted(found, key=lambda cls: cls.__name__)


#: How to build each concrete venue: the collaborators its constructor
#: requires, and nothing else. A new core venue must appear here, which is the
#: completeness half of the behavioural check below.
VENUE_KEYWORDS = {"ShadowExecutor": {}, "PaperExecutor": {}, "RecordedExecutor": {"tape": ()}}


@pytest.mark.parametrize("cls", submitting_executors(), ids=lambda cls: cls.__name__)
def test_every_submitting_executor_takes_the_base_submit_signature(cls):
    """§5.15's Liskov ruling: "`submit` is **not** `submit(intent,
    permit=None)` with `LiveExecutor` demanding more than its base". A
    subclass that added a required argument would break every caller holding
    an `Executor`."""
    assert tuple(inspect.signature(cls.submit).parameters) == (
        "self",
        "intent",
        "permit",
        "state",
    )


def test_every_concrete_submitting_executor_is_built_by_this_test():
    """A venue with no recipe here would silently skip the refusal check
    below, so the recipe table is pinned equal to the concrete subclasses."""
    concrete = {cls.__name__ for cls in submitting_executors() if not inspect.isabstract(cls)}
    assert concrete == set(VENUE_KEYWORDS)


@pytest.mark.parametrize(
    "cls",
    [cls for cls in submitting_executors() if not inspect.isabstract(cls)],
    ids=lambda cls: cls.__name__,
)
def test_a_permission_fact_comes_back_as_an_ack_never_as_a_raise(cls):
    """§5.15: "The base contract is therefore total: `submit` returns an `Ack`
    describing what happened, including refusal … no subclass raises where its
    base returns a value." A raise here would leave the leg unable to
    terminalise the intent it had already recorded."""
    venue = cls({}, clock=TestClock(start_ms=0), **VENUE_KEYWORDS[cls.__name__])
    ack = venue.submit(intent(), "raw-ordinary-authority", tick_state())
    assert isinstance(ack, Ack)
    assert (ack.status, ack.reason) == ("not_sent", "permit_type")


def test_the_read_and_cancel_half_of_the_contract_needs_no_permit():
    """§5.15 splits the hierarchy so an `Executor` is "always constructible,
    never armed": a caller holding one can recover and cancel without knowing
    the rung, which is why `submit` is not on the base at all."""
    assert "submit" not in vars(Executor)
    assert issubclass(EXECUTOR_KINDS.abc, Executor)


# ---------------------------------------------------------------------------
# Encapsulation — a seam subclass reaches no other module's private name
# ---------------------------------------------------------------------------


def seam_names():
    """Return the name of every ABC a class may subclass to become a seam."""
    return {row[0] for row in SEAM_ABCS} | {cls.__name__ for cls in STRUCTURAL_ABCS}


def private_imports(tree):
    """Return the private names a module imports from another module."""
    borrowed = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.asname or alias.name
            if (
                alias.name.startswith("_")
                and not alias.name.startswith("__")
                and alias.name not in SHARED_CHECKERS
            ):
                borrowed[name] = f"{node.module}.{alias.name}"
    return borrowed


def private_reaches(tree, seams):
    """Return `(class, private name)` for every seam subclass reaching a borrowed private name."""
    borrowed = private_imports(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {
            base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
            for base in node.bases
        }
        if not bases & seams:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id in borrowed:
                hits.append((node.name, borrowed[inner.id]))
    return sorted(set(hits))


def test_no_seam_subclass_reaches_a_private_name_of_another_module():
    """§5.15's encapsulation ruling: "`__all__` plus the `_` prefix is the API
    contract". A seam implementation that reached another module's private
    helper would make that helper part of the seam's contract without anyone
    deciding it should be."""
    found = []
    for path, tree in sources():
        found += [f"{path.name}: {cls} -> {name}" for cls, name in private_reaches(tree, seam_names())]
    assert found == []


def test_the_private_reach_scan_catches_one():
    """The detector, on a seam subclass that borrows a private helper."""
    source = (
        "from dskit.production.state import _money\n"
        "class MyClock(Clock):\n"
        "    def now_ms(self):\n"
        "        return _money(1)\n"
    )
    assert private_reaches(ast.parse(source), {"Clock"}) == [
        ("MyClock", "dskit.production.state._money")
    ]


def test_the_shared_checker_vocabulary_is_the_only_private_name_the_package_passes_around():
    """`base.py` re-exports `dskit.assets.base`'s checkers so three packages
    share ONE checker vocabulary (§8) — private by name, public by contract.
    Naming them here keeps the exemption a decision rather than a hole."""
    borrowed = set()
    for _path, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "production" in node.module:
                borrowed |= {
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("_") and not alias.name.startswith("__")
                }
    assert borrowed == set(SHARED_CHECKERS) - {"_raise_if"}


# ---------------------------------------------------------------------------
# One concept, one class
# ---------------------------------------------------------------------------


def test_a_measure_and_a_monitor_are_two_registries_not_one():
    """§5.15's "Two concepts that look like one": a `Measure` answers about
    one candidate at decision time, a `Monitor` about a window of recorded
    decisions. Merging them would force every implementer to stub the half it
    cannot answer."""
    assert registries()["MEASURE_KINDS"] is not registries()["MONITOR_KINDS"]
    assert not issubclass(Measure, Monitor)
    assert not issubclass(Monitor, Measure)
    assert set(registries()["MEASURE_KINDS"].kinds()).isdisjoint(
        registries()["MONITOR_KINDS"].kinds()
    )


def test_the_bundles_are_frozen_values_the_loop_contains():
    """§5.16's seven bundles are composition made explicit: `ServeLoop` holds
    them, so swapping a rung swaps an object rather than taking a branch."""
    from dskit.production import bundles

    for name in ("Schedule", "Data", "Decision", "Safety", "Execution", "Recording",
                 "Observability"):
        cls = getattr(bundles, name)
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen
