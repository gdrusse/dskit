"""Shared fixtures for ``tests/production`` — one synthetic TRAINING RUN, served.

Everything the serving half of this package is tested against is built
here, once, from real dskit machinery and nothing else: a temp onboarding
root filled through the ``localfiles`` connector and ``run_acquisition``,
a small ``PipelineDocument`` whose ENTRY is an ``observations`` node over
that root, and a real ``run_document`` execution of it into a temp run
dir (so ``config.json``, ``artifacts/<key>/`` and ``nodes/NN-<key>.json``
are the driver's own, never hand-written imitations). No network, no wall
clock, no sleeping.

The pipeline is deliberately shaped so that every ADR-0091 serving effect
appears exactly once:

======================  ==================================  ==============
node                    kind                                serving effect
======================  ==================================  ==============
``bars``                ``observations`` (by class ref)     ``entry_read``
``weights``             :class:`SideTable` (by class ref)   ``pure``
``usable``              ``filter``                          ``pure``
``grid``                ``event-grid``                      ``pure``
``scaled``              ``standardize`` (``mode: train``)   ``release_read``
``scored``              ``derive``                          ``pure``
``picks``               ``join`` — the HEAD                 ``pure``
======================  ==================================  ==============

``weights`` is the one needed node that does NOT descend from the entry,
which is what gives the serving BASE PASS (``SubgraphRunner.run_keys``)
something to run; without it the base pass would be vacuously empty and
G12's base-pass tests would prove nothing. ``picks``'s rows carry
``instrument`` / ``side`` / ``qty`` / ``confidence`` / ``prediction``, so
they are proposal-shaped for the ``intent-rows`` proposer.

Fixtures
--------
``synthetic_root`` (session)
    An :class:`~dskit.onboarding.OnboardingRoot` holding ONE acquisition
    of the ``bars`` stream from an ACTIVE ``localfiles`` source named
    ``local``. Read-only: never acquire into it.
``synthetic_registry`` (session)
    That root's P2 registry — where the ACTIVE ``source_config`` alias
    lives, so a feed's D4 identity check has something real to resolve.
``source_config_hash`` (session)
    The ACTIVE ``source_config`` alias's ``version_id`` — what a
    ``FeedSpec`` pins and every pull re-checks.
``training_document`` (session)
    The :class:`~dskit.pipeline.document.PipelineDocument` above. Its
    entry declares ``"since_ms": null`` so the serving window override
    finds an EXISTING param (ADR-0091's existing-key-only rule).
``training_run`` (session)
    The :class:`~dskit.pipeline.driver.DocumentRunResult` of running it.
``run_dir`` (session)
    ``training_run.run_dir`` — the base run dir every serving derivation
    reads ``config.json``, ``artifacts/`` and ``nodes/`` from.
``entry_params`` (session)
    The entry node's declared params, as the document states them.
``serving_contract`` (session)
    ``ObservationRows.serving_contract(entry_params, {})``.
``release_manifest`` (session)
    A :class:`~dskit.production.release.ReleaseManifest` over that run
    that :func:`~dskit.production.release.verify_release` accepts, with
    the fitted sidecar as its one artifact.
``serve_document`` (session)
    A :class:`~dskit.production.document.ServeDocument` pointing at the
    run, with ``serving.entry``/``heads``/``required_universe`` wired to
    the names above.
``fresh_root``
    A function-scoped, MUTABLE onboarding root of the same shape — for
    tests that acquire again, grow the stream, or swap the source config.
``clock``
    A :class:`~dskit.production.clock.TestClock` started at :data:`NOW_MS`.
``recorder``
    A call recorder for monkeypatched seams ("assert the exact call").

Who uses what
-------------
* G12 (``test_feed``, ``test_decider``) uses all of them.
* G13 (verifier/executor) and G16 (leg) reuse ``clock``, ``recorder`` and
  ``release_manifest``; G17 (compose/loop) and G18 (``__main__``/e2e)
  reuse the whole synthetic run as their served release.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.acquire import find_active_source
from dskit.pipeline.base import OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.observations import ObservationRows
from dskit.pipeline.node import Node
from dskit.production.base import canonical_hash
from dskit.production.clock import TestClock
from dskit.production.document import ServeDocument
from dskit.production.records import ExecutionScope
from dskit.production.release import ReleaseManifest, RuntimeFingerprint, artifact_digest
from tests.production.test_document import minimal_document

# --------------------------------------------------------------------------
# the vocabulary of the synthetic run — imported by name, never re-spelled
# --------------------------------------------------------------------------

#: The onboarding source alias and the stream the entry reads.
SOURCE = "local"
STREAM = "bars"

#: The universe every tick must cover, sorted — the serve document's
#: ``serving.required_universe`` and the ``FeedSpec``'s ``required_keys``.
UNIVERSE = ("INS1", "INS2")

#: The serving entry's three knobs (``serving.entry`` in the document).
ENTRY_NODE = "bars"
ENTRY_PARAM = "since_ms"
WINDOW_MS = 2 * 86_400_000

#: The head whose rows are proposal-shaped, and the one needed node that
#: does not descend from the entry (the base pass's only work).
HEAD = "picks"
BASE_PASS_NODE = "weights"

#: The trainable flipped to ``mode: "load"`` when the run is served, and
#: the sidecar file its ``run_load`` asks its release reader for.
TRAINABLE_NODE = "scaled"
SIDECAR = "fitted.json"

DAY_MS = 86_400_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The four session days the source rows cover.
DAYS = ("2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05")

#: The run's ``asof``, and the instant the TestClock starts at — one day
#: after the last source row, so every watermark is in the past.
ASOF = "2026-01-06"


def iso_ms(day):
    """Exact epoch ms of an ISO date at UTC midnight (integer arithmetic)."""
    at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return (at - _EPOCH) // timedelta(milliseconds=1)


NOW_MS = iso_ms(ASOF)

#: The newest instant the acquired stream reaches.
LAST_ROW_MS = iso_ms(DAYS[-1])


def source_rows():
    """The rows the ``localfiles`` connector serves, one per (day, instrument).

    Fresh list each call, so a caller may mutate it. ``ts`` is both the
    connector's ``effective_field`` and the entry's ``ts_field``;
    ``side``/``qty``/``confidence`` ride along so the head's rows are
    proposal-shaped without a derive node per field.
    """
    rows = []
    for i, day in enumerate(DAYS):
        for j, key in enumerate(UNIVERSE):
            rows.append(
                {
                    "ts": day,
                    "instrument": key,
                    "value": 1.0 + i + j,
                    "side": "buy" if j == 0 else "sell",
                    "qty": 3 + j,
                    "confidence": 0.60 + j / 100.0,
                }
            )
    return rows


class SideTable(Node):
    """A literal lookup table — the one pure node that is not the entry's child.

    Role ``data`` (a source root with no inputs and fully literal params)
    and serving effect ``pure``: it reads its own declared table and
    nothing else, so a served tick may run it ONCE, in the base pass, and
    reuse the answer for the process lifetime. That is precisely the
    shape ``SubgraphRunner.run_keys`` exists for.
    """

    role = "data"
    outputs = ("table",)

    _PARAMS = ("table",)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``"pure"``: a literal table reads nothing but itself."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``: ``table`` must be a mapping."""
        problems = []
        unknown = sorted(set(params) - set(cls._PARAMS))
        if unknown:
            problems.append(f"unknown param(s) {unknown} — allowed: {list(cls._PARAMS)}")
        if not isinstance(params.get("table"), dict):
            problems.append(f"table must be a mapping, got {params.get('table')!r}")
        return problems

    def run(self, ctx, inputs):
        """Emit the declared table under the ``table`` port."""
        return {"table": dict(self.params["table"])}


#: How the two node classes that are not registered kinds are referenced.
SIDE_TABLE_REF = "tests.production.conftest:SideTable"
OBSERVATIONS_REF = "dskit.pipeline.libs.observations:ObservationRows"


# --------------------------------------------------------------------------
# builders — module-level so a test may build a SECOND root/run of its own
# --------------------------------------------------------------------------


def write_source_files(data_dir, rows):
    """Write ``rows`` as the ``localfiles`` connector's ``bars.jsonl``."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{STREAM}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def data_dir_of(root):
    """Where :func:`build_onboarding_root` put the connector's data files."""
    return os.path.join(os.path.dirname(root.root), "data")


def register_source(root, data_dir, name=SOURCE, origin="conftest"):
    """Register an ACTIVE ``localfiles`` ``source_config`` over ``data_dir``."""
    registry = root.registry()
    vid = registry.register(
        "source_config",
        {
            "name": name,
            "catalog_source": f"{name}-src",
            "connector": "localfiles",
            "config": {"path": data_dir, "effective_field": "ts"},
        },
        origin=origin,
    )
    registry.transition(vid, "active", origin=origin)
    return vid


def build_onboarding_root(base_dir, rows=None):
    """Create an onboarding root, register the source, acquire the stream once.

    Returns the :class:`~dskit.onboarding.OnboardingRoot`; the acquisition
    is a real ``run_acquisition`` in ``backfill`` mode, so the on-disk
    shape is acquire's own.
    """
    data_dir = os.path.join(base_dir, "data")
    write_source_files(data_dir, source_rows() if rows is None else rows)
    root = OnboardingRoot.create(os.path.join(base_dir, "ob"))
    register_source(root, data_dir)
    run_acquisition(root, root.registry(), SOURCE, STREAM, "backfill")
    return root


def entry_spec(root_dir):
    """The entry node's :class:`NodeSpec` — ``since_ms`` DECLARED as null."""
    return NodeSpec(
        uses=OBSERVATIONS_REF,
        params={
            "root": root_dir,
            "source": SOURCE,
            "stream": STREAM,
            # The entity field FIRST, the instant second: the contract
            # projects `ts_field` out of the dedupe key to get the entity.
            "key_fields": ["instrument", "ts"],
            "ts_field": "ts",
            # Declared, and null: a serving override may only address a
            # param that already exists (ADR-0091 / apply_param_override).
            ENTRY_PARAM: None,
        },
        notes="The tick's one mutable read; the serving window overrides since_ms.",
    )


def training_pipeline(root_dir):
    """The seven-node map above, fresh specs each call."""
    return {
        ENTRY_NODE: entry_spec(root_dir),
        BASE_PASS_NODE: NodeSpec(
            uses=SIDE_TABLE_REF,
            params={"table": {key: 0.5 for key in UNIVERSE}},
            notes="Pure, and no child of the entry — the base pass's only work.",
        ),
        "usable": NodeSpec(
            uses="filter",
            inputs={"records": f"${ENTRY_NODE}.records"},
            params={"where": [{"field": "value", "op": ">", "value": 0}]},
        ),
        "grid": NodeSpec(
            uses="event-grid",
            inputs={"records": "$usable.records"},
            params={"period_ms": DAY_MS, "offset_ms": 0},
        ),
        TRAINABLE_NODE: NodeSpec(
            uses="standardize",
            mode="train",
            inputs={"rows": "$grid.records"},
            params={"fit_split": "train", "features": ["value"]},
            notes="Flipped to mode 'load' and pinned to artifacts/scaled when served.",
        ),
        "scored": NodeSpec(
            uses="derive",
            inputs={"records": f"${TRAINABLE_NODE}.rows"},
            params={
                "field": "prediction",
                "cases": [
                    {"when": [{"field": "value", "op": ">=", "value": 0}], "value": 0.58},
                    {"when": [], "value": 0.42},
                ],
            },
        ),
        HEAD: NodeSpec(
            uses="join",
            inputs={"records": "$scored.records", "weight": f"${BASE_PASS_NODE}.table"},
            params={"key": "instrument", "how": "strict"},
            notes="The head: its rows carry instrument/side/qty/confidence/prediction.",
        ),
    }


def build_training_document(root_dir, run_root, **overrides):
    """The training document over ``root_dir``, writing runs under ``run_root``."""
    base = {
        "name": "synth-serve-train",
        "pipeline": training_pipeline(root_dir),
        "splits": TimeSplitConfig(
            train_end_ms=iso_ms(DAYS[1]),
            val_end_ms=iso_ms(DAYS[2]),
            test_end_ms=iso_ms(DAYS[3]),
        ),
        "outputs": OutputsConfig(run_root=run_root),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def build_release_manifest(run_dir, source_config_hash, feed_spec, **overrides):
    """A :class:`ReleaseManifest` over ``run_dir`` that ``verify_release`` accepts.

    Its one artifact is the trainable's fitted sidecar, named RELATIVE to
    the run dir and digested from the bytes on disk.
    """
    sidecar = os.path.join("artifacts", TRAINABLE_NODE, SIDECAR)
    stamp = NOW_MS - 1000
    fields = {
        "series_id": SERIES_ID,
        "doc_hash": "c1" * 32,
        "run_hash": "d2" * 32,
        "serving_hash": "e3" * 32,
        "artifacts": {
            sidecar: {
                "digest": artifact_digest(os.path.join(run_dir, sidecar)),
                "timestamp_ms": stamp,
            }
        },
        "classes": {TRAINABLE_NODE: {"ref": "dskit.pipeline.fitted:Standardize",
                                     "code_digest": "f4" * 32}},
        "adapter": {"name": "tests.production.conftest", "digest": "b6" * 32},
        "feed_spec": feed_spec,
        "source_config": {"hash": source_config_hash, "version": "1"},
        "execution_scope": ExecutionScope(venue="paper", account="strategy-a"),
        "approval_fingerprint": "c7" * 32,
        "lease_fingerprint": "d8" * 32,
        "checklist_digest": "e9" * 32,
        "runtime_fingerprint": RuntimeFingerprint.capture(),
        "created_ms": stamp,
    }
    fields.update(overrides)
    return ReleaseManifest(**fields)


#: The series the serve document and its release belong to.
SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"


class Recorder:
    """Records every call made through it — for monkeypatched seams.

    Attributes
    ----------
    calls : list of tuple
        ``(name, args, kwargs)`` in call order.
    """

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def __call__(self, *args, **kwargs):
        """Record a bare call and return the configured answer."""
        self.calls.append(("__call__", args, kwargs))
        return self.answer(*args, **kwargs) if callable(self.answer) else self.answer

    def hook(self, name, answer=None):
        """Return a callable that records under ``name`` and answers ``answer``."""

        def recorded(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return answer(*args, **kwargs) if callable(answer) else answer

        return recorded

    def named(self, name):
        """Every recorded call under ``name``, as ``(args, kwargs)`` pairs."""
        return [(a, k) for n, a, k in self.calls if n == name]


def boom(*args, **kwargs):
    """Stand in for a seam that must NOT be reached: being called is the defect."""
    raise AssertionError(f"forbidden call: {args!r} {kwargs!r}")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def synthetic_root(tmp_path_factory):
    """A read-only onboarding root with one acquisition of ``bars``."""
    return build_onboarding_root(str(tmp_path_factory.mktemp("onboarding")))


@pytest.fixture(scope="session")
def synthetic_registry(synthetic_root):
    """The P2 registry of :func:`synthetic_root`."""
    return synthetic_root.registry()


@pytest.fixture(scope="session")
def source_config_hash(synthetic_registry):
    """The ACTIVE ``source_config`` alias's ``version_id``."""
    return find_active_source(synthetic_registry, SOURCE)


@pytest.fixture(scope="session")
def training_document(synthetic_root, tmp_path_factory):
    """The training :class:`PipelineDocument` over :func:`synthetic_root`."""
    run_root = str(tmp_path_factory.mktemp("runs"))
    return build_training_document(synthetic_root.root, run_root)


@pytest.fixture(scope="session")
def training_run(training_document):
    """One real ``run_document`` execution of :func:`training_document`."""
    result = run_document(training_document, asof=ASOF)
    assert result.state == "ran", result.state
    return result


@pytest.fixture(scope="session")
def run_dir(training_run):
    """The run directory ``training_run`` wrote."""
    return training_run.run_dir


@pytest.fixture(scope="session")
def entry_params(training_document):
    """The entry node's declared params, as the document states them."""
    return dict(training_document.expanded[ENTRY_NODE].params)


@pytest.fixture(scope="session")
def serving_contract(entry_params):
    """The entry class's pure serving contract for those params."""
    return ObservationRows.serving_contract(entry_params, {})


@pytest.fixture(scope="session")
def feed_spec_obj(serving_contract, source_config_hash):
    """The eight-key ``FeedSpec`` mapping the release binds, built without feed.py.

    ``release.py`` carries the spec as a plain mapping so it need not
    import ``feed.py``; this fixture is that mapping, so a release can be
    built before ``feed.FeedSpec`` exists.
    """
    return {
        "source_binding": dict(serving_contract.source_binding),
        "entity_key_fields": list(serving_contract.entity_key_fields),
        "event_time_field": serving_contract.event_time_field,
        "digest_recipe": dict(serving_contract.digest_recipe),
        "required_keys": list(UNIVERSE),
        "required_keys_digest": canonical_hash(list(UNIVERSE)),
        "source_config_hash": source_config_hash,
        "source_config_version": "1",
    }


@pytest.fixture(scope="session")
def release_manifest(run_dir, source_config_hash, feed_spec_obj):
    """A release over the synthetic run that ``verify_release`` accepts."""
    return build_release_manifest(run_dir, source_config_hash, feed_spec_obj)


@pytest.fixture(scope="session")
def serve_document(run_dir):
    """A ``ServeDocument`` naming the synthetic run, its entry and its head."""
    obj = minimal_document(
        series_id=SERIES_ID,
        serving={
            "run_dir": run_dir,
            "adapter": "tests.production.conftest",
            "entry": {"node": ENTRY_NODE, "param": ENTRY_PARAM, "window_ms": WINDOW_MS},
            "heads": [HEAD],
            "required_universe": list(UNIVERSE),
            "proposer": {
                "uses": "intent-rows",
                "params": {
                    "output": "records",
                    "fields": {
                        "instrument": "instrument",
                        "side": "side",
                        "qty": "qty",
                        "confidence": "confidence",
                        "prediction": "prediction",
                    },
                },
            },
            "max_artifact_age": "P30D",
        },
    )
    return ServeDocument.from_obj(obj)


@pytest.fixture
def fresh_root(tmp_path):
    """A MUTABLE onboarding root of the same shape — safe to grow or re-point."""
    return build_onboarding_root(str(tmp_path / "fresh"))


@pytest.fixture
def clock():
    """A ``TestClock`` started at :data:`NOW_MS` — nothing reads wall time."""
    return TestClock(start_ms=NOW_MS)


@pytest.fixture
def recorder():
    """A fresh :class:`Recorder` for monkeypatched seams."""
    return Recorder()
