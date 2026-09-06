"""`bundles.py` — the seven collaborator bundles, and nothing else (§5.13, §5.16).

`ServeLoop`, `Tick` and `LegPipeline` take two values plus seven frozen
bundles rather than thirty positional arguments, and the bundles are
their own module because `LegPipeline` takes six of them while
`compose.py` builds all seven — putting them in either module makes the
build order cyclic (§8).

That cycle is the reason for the strongest test in this file: a bundle
validates **presence only**.  Checking that `Safety.breaker` is a
`Breaker` would mean importing `breaker.py`, and doing that for all
thirty-one members would import most of the package back into the module
that exists to break the cycle.  So `test_a_bundle_never_inspects_the_type_of_a_member`
constructs every bundle out of bare `object()`s and expects it to
succeed, and an AST test holds the import list to `base` and `vocab`.

What IS pinned:

- the member names and their ORDER, from §5.16's table — the order is
  the constructor contract `compose.bundles_for` and `LegPipeline`
  depend on;
- frozenness (§5.15: a bundle is a value, and folded state has no
  setter);
- a `None` member refuses with a `ProductionError` that NAMES it, and
  every missing member is reported in ONE raise (the package-wide
  accumulate-then-raise rule);
- `Invocation`, the frozen `{armed, env_release_hash, once, max_ticks}`
  `__main__` builds from `--armed`, `DSKIT_PRODUCTION_ARM`, `--once` and
  `--max-ticks` and `Safety` carries, so that `Arming.check_conjunction`
  can see all three of its inputs (§5.6).  It is a value object rather
  than a collaborator, so it DOES check its four knobs — and it can,
  since they are stdlib types.
"""

import ast
import dataclasses
import pathlib

import pytest

import dskit
from dskit.production import bundles as bundles_module
from dskit.production.base import ProductionError
from dskit.production.bundles import (
    Data,
    Decision,
    Execution,
    Invocation,
    Observability,
    Recording,
    Safety,
    Schedule,
)

MODULE_PATH = pathlib.Path(dskit.__file__).parent / "production" / "bundles.py"

#: §5.16's table, restated: the seven bundles and their members IN ORDER.
#: A shipped test never reads the proposal (§5.16), so this is the
#: deliberate independent restatement CLAUDE.md asks a validation suite
#: for.
BUNDLES = {
    Schedule: ("clock", "calendar", "cadence", "overrun"),
    Data: ("feed", "decider"),
    Decision: ("guards", "monitors"),
    Safety: (
        "breaker",
        "arming",
        "authorities",
        "readiness",
        "invocation",
        "action_policy",
        "transition_policy",
        "submission_verifier",
    ),
    Execution: ("executor", "accounting", "lease", "resilience"),
    Recording: (
        "ledger",
        "state",
        "inbox",
        "reconciler",
        "checkpoint",
        "journal_hook",
        "id_source",
    ),
    Observability: ("metrics", "alerts", "health", "heartbeat"),
}

#: The only production modules `bundles.py` may import — importing a type
#: to validate against is what would re-create the cycle (§8).
ALLOWED_PRODUCTION_IMPORTS = {"dskit.production.base", "dskit.production.vocab"}

PARAMS = list(BUNDLES.items())
IDS = [cls.__name__ for cls in BUNDLES]


def members(cls, **overrides):
    """Fresh sentinel objects for every member of ``cls``, overridable."""
    return {name: overrides.get(name, object()) for name in BUNDLES[cls]}


# ---------------------------------------------------------------------------
# Shape — the member names, their order, the public surface
# ---------------------------------------------------------------------------


def test_the_public_surface_is_the_seven_bundles_the_invocation_and_the_tape_seam():
    """`ReplayTape` joined them in phase 2 for the reason the bundles are
    here at all: `report.py` builds a tape and `compose.py` consumes one, so
    a declaration in either would make §10's build order cyclic (§5.13.3)."""
    assert set(bundles_module.__all__) == {cls.__name__ for cls in BUNDLES} | {
        "Invocation",
        "ReplayTape",
    }


def test_the_tape_seam_is_abstract_and_declares_the_three_answers_compose_reads():
    """A tape supplies DATA and never an object, which is what keeps "the
    rungs differ only by which objects were injected" (§5.15) a fact about
    `compose.py` rather than about whoever produced the tape."""
    assert bundles_module.ReplayTape.__abstractmethods__ == frozenset(
        {"start_ms", "feed_results", "id_allocations"}
    )
    with pytest.raises(TypeError, match="abstract"):
        bundles_module.ReplayTape()


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_carries_exactly_the_members_of_5_16_in_order(cls, expected):
    assert dataclasses.is_dataclass(cls)
    assert tuple(field.name for field in dataclasses.fields(cls)) == expected


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_binds_its_members_positionally_in_that_order(cls, expected):
    """The order IS the constructor contract: `compose.bundles_for`
    returns the seven and `LegPipeline` takes six of them, so a member
    that moved would silently swap two collaborators."""
    values = [object() for _ in expected]
    bundle = cls(*values)
    assert [getattr(bundle, name) for name in expected] == values


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_is_frozen(cls, expected):
    bundle = cls(**members(cls))
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(bundle, expected[0], object())


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_refuses_a_member_it_does_not_declare(cls, expected):
    with pytest.raises(TypeError):
        cls(**members(cls), extra=object())


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_refuses_a_missing_argument(cls, expected):
    with pytest.raises(TypeError):
        cls(*[object() for _ in expected[:-1]])


# ---------------------------------------------------------------------------
# Validation — presence, and only presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_an_absent_member_refuses_and_the_problem_names_it(cls, expected):
    for name in expected:
        with pytest.raises(ProductionError) as excinfo:
            cls(**members(cls, **{name: None}))
        assert name in str(excinfo.value), (cls.__name__, name)


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_every_absent_member_is_reported_in_one_raise(cls, expected):
    """The package-wide rule: validation accumulates every problem and
    raises once, so composing a bundle wrong is one legible failure
    rather than a game of whack-a-mole."""
    with pytest.raises(ProductionError) as excinfo:
        cls(**{name: None for name in expected})
    reported = str(excinfo.value)
    assert all(name in reported for name in expected), reported
    assert len(excinfo.value.problems) == len(expected)


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_bundle_never_inspects_the_type_of_a_member(cls, expected):
    """Presence only (§8's `test_bundles.py` line): type validation would
    import fourteen later modules and re-create the very cycle this
    module exists to break."""
    values = members(cls)
    bundle = cls(**values)
    for name, value in values.items():
        assert getattr(bundle, name) is value


@pytest.mark.parametrize(("cls", "expected"), PARAMS, ids=IDS)
def test_a_falsy_member_is_present(cls, expected):
    """`if not member` is the defect this catches: a collaborator that
    defines `__bool__` or `__len__` — an empty `GuardChain`, a monitor
    tuple with no monitors — is present, not absent."""
    for falsy in (False, 0, (), ""):
        bundle = cls(**members(cls, **{expected[0]: falsy}))
        assert getattr(bundle, expected[0]) == falsy


# ---------------------------------------------------------------------------
# The import rule — the cycle this module exists to break
# ---------------------------------------------------------------------------


def test_bundles_imports_no_production_module_but_base_and_vocab():
    """§8: `bundles.py` sits ahead of `leg.py` and `compose.py` in the
    build order precisely because it depends on neither.  An import of a
    seam type — to validate a member against it — is what would put the
    cycle back."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.add(f"dskit.production.{node.module or ''}".rstrip("."))
            elif node.module:
                imported.add(node.module)
    offenders = {
        name
        for name in imported
        if name.startswith("dskit.production")
        and name not in ALLOWED_PRODUCTION_IMPORTS
    }
    assert not offenders, sorted(offenders)


# ---------------------------------------------------------------------------
# Invocation — the three inputs `Arming.check_conjunction` needs (§5.6)
# ---------------------------------------------------------------------------


def test_invocation_carries_the_four_knobs_main_builds_it_from():
    assert tuple(field.name for field in dataclasses.fields(Invocation)) == (
        "armed",
        "env_release_hash",
        "once",
        "max_ticks",
    )


def test_invocation_is_frozen():
    invocation = Invocation(armed=True, env_release_hash="a" * 64, once=False, max_ticks=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        invocation.armed = False


def test_invocation_accepts_the_absent_forms():
    """No `--armed`, no `DSKIT_PRODUCTION_ARM`, no `--max-ticks`: an
    unbounded serve that has not been armed is the normal shadow case."""
    invocation = Invocation(
        armed=False, env_release_hash=None, once=False, max_ticks=None
    )
    assert invocation.env_release_hash is None
    assert invocation.max_ticks is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"armed": "yes"},
        {"armed": 1},
        {"once": "no"},
        {"once": 0},
        {"env_release_hash": 64},
        {"env_release_hash": b"a" * 64},
        {"max_ticks": 0},
        {"max_ticks": -1},
        {"max_ticks": 1.0},
        {"max_ticks": "3"},
    ],
)
def test_invocation_refuses_a_knob_it_cannot_act_on(kwargs):
    """`armed` and `once` are booleans, `env_release_hash` a hash or
    absent, `max_ticks` a positive count or absent.  `Invocation` is a
    value object, not a collaborator bundle, so it validates its own
    stdlib-typed fields — `--max-ticks 0` must refuse rather than serve
    forever or stop immediately, depending on how the loop reads it."""
    fields = {
        "armed": True,
        "env_release_hash": "a" * 64,
        "once": False,
        "max_ticks": 5,
    }
    fields.update(kwargs)
    with pytest.raises(ProductionError):
        Invocation(**fields)


def test_invocation_reports_every_bad_knob_in_one_raise():
    with pytest.raises(ProductionError) as excinfo:
        Invocation(armed="yes", env_release_hash=64, once="no", max_ticks=0)
    assert len(excinfo.value.problems) == 4
