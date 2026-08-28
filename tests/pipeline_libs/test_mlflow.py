"""The mlflow tracking sink (tier-2 pack) — loud config, quiet logging.

The pack exists because of a gotcha, so most of this file is about the
gotcha: ``driver._Trackers`` SWALLOWS every per-sink exception (telemetry
must never kill a run), which means a misconfigured sink logs nothing and
SAYS nothing. The sink therefore has to be loud everywhere the swallow
does not reach — the document's validator and the constructor, both of
which run before any node does.

The e2e half needs a real mlflow (``pytest.importorskip`` inside those
tests); everything above it is stdlib-only and always runs, because the
loudness is exactly what must not silently regress.
"""

import http.server
import pathlib
import socket
import threading
import time
import tomllib

import pytest

import dskit
from dskit.pipeline.base import (
    SINK_KINDS,
    ConfigError,
    OutputsConfig,
    SinkConfig,
    TrackingConfig,
)
from dskit.pipeline.conformance import DEFAULT_BLOCKED_IMPORTS
from dskit.pipeline.document import DOC_NON_IDENTITY_SECTIONS
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.mlflow import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_EXPERIMENT,
    DEFAULT_TRACKING_URI,
    MAX_ENTITY_KEY_CHARS,
    MAX_PARAM_VALUE_CHARS,
    NODE_KINDS,
    SINK_KIND,
    TRACKING_URI_SCHEMES,
    MlflowTracker,
    register,
)
from dskit.pipeline.node import DEFAULT_NODE_KINDS
from tests.pipeline.dochelpers import banking_document, make_registry

ASOF = "2026-01-01"


@pytest.fixture(autouse=True)
def registered():
    """Every test speaks the registered kind; registration is idempotent."""
    register()
    return SINK_KIND


def closed_port():
    """A port nothing listens on — bound then released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def degraded_server():
    """A tracking server that ACCEPTS TCP and then answers 503 forever.

    The plan-time probe proves a destination reachable with a TCP
    connect, which this server passes — so it is the shape the probe
    cannot refuse and the run must survive: a correctly configured
    remote store having a bad day.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            self.send_error(503)

        do_POST = do_GET

        def log_message(self, *args):
            """Keep the test output clean."""

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def sink_config(**params):
    """Build the SinkConfig a document would carry (runs the validator)."""
    return SinkConfig(kind=SINK_KIND, params=params)


def store_uri(tmp_path, name="mlruns.db"):
    """A local, serverless sqlite tracking store inside ``tmp_path``."""
    return f"sqlite:///{tmp_path / name}"


def tracked_document(tmp_path, store, **params):
    """The demo banking document, tracked into ``store``."""
    return banking_document(
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
        tracking=TrackingConfig(sinks=(sink_config(tracking_uri=store, **params),)),
    )


# ---------------------------------------------------------------------------
# The registration seam — the one the test memory sink established
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_binds_the_kind_to_the_class_and_its_own_validator(self):
        assert SINK_KIND in SINK_KINDS
        entry = SINK_KINDS[SINK_KIND]
        assert entry["factory"] is MlflowTracker
        # ONE name for the rules: the registered validator IS the class's
        # own validate_params, which is also what _ref_param_errors runs
        # for the "dskit.pipeline.libs.mlflow:MlflowTracker" class-ref
        # spelling. Two validators would be two places to change.
        assert entry["validator"] == MlflowTracker.validate_params

    def test_register_is_idempotent(self):
        register()
        register()
        assert SINK_KIND in SINK_KINDS

    def test_the_pack_registers_no_node_kind(self):
        # A tracking destination is not a node: it may never be spelled
        # inside `pipeline`, the section identity is computed over.
        assert NODE_KINDS == ()


class TestPackagingAgreements:
    """The pack's library name lives in three places; pin them together."""

    def test_the_purity_gate_actually_blocks_this_packs_library(self):
        # The behavioural half of the tier-2 purity rule imports every pack
        # with DEFAULT_BLOCKED_IMPORTS gone from sys.modules. A library
        # missing from that tuple makes its pack's check vacuous.
        assert "mlflow" in DEFAULT_BLOCKED_IMPORTS

    def test_the_optional_extra_ships_and_is_folded_into_all(self):
        pyproject = pathlib.Path(dskit.__file__).parents[1] / "pyproject.toml"
        if not pyproject.is_file():
            pytest.skip("not a source checkout")
        with open(pyproject, "rb") as handle:
            extras = tomllib.load(handle)["project"]["optional-dependencies"]
        assert any(req.startswith("mlflow") for req in extras["mlflow"])
        assert set(extras["mlflow"]) <= set(extras["all"])


# ---------------------------------------------------------------------------
# Loud at plan time — default-deny params
# ---------------------------------------------------------------------------


class TestParamValidation:
    def test_params_tuple_pins_every_knob(self):
        # A pinning test that omits a knob is worse than none: add the
        # knob to this tuple in the same change that adds the knob.
        assert MlflowTracker._PARAMS == (
            "connect_timeout",
            "experiment",
            "notes",
            "run_name",
            "tags",
            "tracking_uri",
        )

    def test_unknown_param_is_refused_by_name(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            sink_config(tracking_uri=str(tmp_path), trackingURI="typo")
        assert "trackingURI" in str(exc.value)

    def test_notes_is_allowed_inside_params(self, tmp_path):
        sink_config(tracking_uri=str(tmp_path), notes="why this store")

    def test_defaults_are_accepted_with_no_params_at_all(self, tmp_path, monkeypatch):
        # A document that says only {"kind": "mlflow"} plans, with no
        # server and nothing else declared.
        monkeypatch.chdir(tmp_path)
        sink_config()
        assert DEFAULT_EXPERIMENT and DEFAULT_CONNECT_TIMEOUT > 0

    @pytest.mark.parametrize(
        "params",
        [
            {"experiment": ""},
            {"experiment": 7},
            {"run_name": 7},
            {"tags": ["a", "b"]},
            {"tags": {"a": 7}},
            {"connect_timeout": 0},
            {"connect_timeout": "soon"},
            {"tracking_uri": ""},
            {"tracking_uri": 7},
        ],
    )
    def test_bad_types_are_refused(self, tmp_path, params):
        with pytest.raises(ConfigError):
            SinkConfig(
                kind=SINK_KIND, params={"tracking_uri": str(tmp_path), **params}
            )

    def test_unknown_uri_scheme_is_refused_naming_the_vocabulary(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            sink_config(tracking_uri="postgresql://user@localhost/mlflow")
        assert "postgresql" in str(exc.value)
        assert TRACKING_URI_SCHEMES == ("", "file", "http", "https", "sqlite")

    def test_the_class_docstring_states_the_default_and_the_vocabulary(self):
        # CLAUDE.md routes a reader to the class's params tuple, so the
        # class docstring is the SECOND copy of both values. It drifted
        # from the constants in the very commit that introduced them
        # (it said "./mlruns" and omitted sqlite), so pin the agreement
        # rather than trusting prose: change the constant, change here.
        doc = MlflowTracker.__doc__
        assert f'``"{DEFAULT_TRACKING_URI}"``' in doc
        # EVERY default the prose restates, not just the one that drifted:
        # a pin that omits a knob is worse than none, and the docstring
        # says in so many words that this test holds all three.
        assert f'``"{DEFAULT_EXPERIMENT}"``' in doc
        assert f"default {DEFAULT_CONNECT_TIMEOUT} " in doc
        for scheme in TRACKING_URI_SCHEMES:
            token = '``""``' if scheme == "" else f"``{scheme}``"
            assert token in doc, f"scheme {scheme!r} missing from the docstring"
        # The Examples block must instantiate something that WORKS; the
        # first version documented a directory store, which current
        # mlflow refuses outright (see TestDirectoryStore below). The
        # block this asserts is EXECUTED, verbatim, by
        # test_the_default_store_is_resolved_in_one_place.
        assert f'MlflowTracker({{"tracking_uri": "{DEFAULT_TRACKING_URI}"}})' in doc

    def test_the_default_store_is_local_and_serverless(self, tmp_path, monkeypatch):
        # mlflow put the ./mlruns DIRECTORY store into maintenance mode and
        # refuses it unless MLFLOW_ALLOW_FILE_STORE is set; the pack sets no
        # env var for you, so the default is the local sqlite store instead.
        monkeypatch.chdir(tmp_path)
        assert DEFAULT_TRACKING_URI.startswith("sqlite:")
        assert MlflowTracker.validate_params({}) == []

    def test_a_sqlite_store_under_a_missing_parent_fails_the_plan(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            sink_config(tracking_uri=f"sqlite:///{tmp_path}/gone/m.db")
        assert "unreachable" in str(exc.value)


# ---------------------------------------------------------------------------
# Loud at plan time — an unreachable destination
# ---------------------------------------------------------------------------


class TestReachability:
    def test_a_local_store_under_a_missing_parent_fails_the_plan(self, tmp_path):
        missing = tmp_path / "no-such-dir" / "mlruns"
        with pytest.raises(ConfigError) as exc:
            sink_config(tracking_uri=str(missing))
        assert "unreachable" in str(exc.value)

    def test_a_local_store_that_is_a_file_fails_the_plan(self, tmp_path):
        occupied = tmp_path / "mlruns"
        occupied.write_text("not a directory\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            sink_config(tracking_uri=str(occupied))

    def test_a_file_uri_resolves_to_its_path(self, tmp_path):
        sink_config(tracking_uri=(tmp_path / "mlruns").as_uri())

    def test_an_unreachable_server_fails_the_plan_not_the_run(self):
        # The whole point of the pack's loudness: _Trackers would swallow
        # this at run time and the run would report success having logged
        # nothing at all.
        uri = f"http://127.0.0.1:{closed_port()}"
        with pytest.raises(ConfigError) as exc:
            sink_config(tracking_uri=uri, connect_timeout=0.25)
        assert "unreachable" in str(exc.value)

    def test_construction_revalidates_rather_than_trusting_the_caller(self, tmp_path):
        with pytest.raises(ConfigError):
            MlflowTracker({"tracking_uri": str(tmp_path / "gone" / "mlruns")})


class TestDirectoryStore:
    """The two DIRECTORY spellings, and what the README may claim of them.

    A bare path and ``file:`` name mlflow's plain-directory file store,
    which mlflow 3.x put into maintenance mode and REFUSES unless
    ``MLFLOW_ALLOW_FILE_STORE`` is set. The pack sets no environment
    variable on the reader's behalf, so the schemes stay in the
    vocabulary (correct on mlflow 2.x, and for anyone who opted in) and
    the refusal, when it comes, is the installed mlflow's — not the
    document's. What must NOT vary with the mlflow version is HOW that
    refusal arrives: never a raw ``MlflowException`` escaping the pack,
    always a ``ConfigError`` naming the URI, at construction, before a
    node runs. `README.md` states exactly that split; this pins it.
    """

    def test_a_directory_store_plans_clean_whatever_mlflow_thinks_of_it(
        self, tmp_path
    ):
        # Reachability is the document's business; whether the store
        # family is ALLOWED is the installed mlflow's, which the
        # stdlib-only plan-time probe cannot and must not decide.
        assert MlflowTracker.validate_params({"tracking_uri": str(tmp_path)}) == []

    def test_a_directory_store_either_works_or_refuses_as_a_ConfigError(
        self, tmp_path
    ):
        pytest.importorskip("mlflow")
        uri = str(tmp_path / "mlruns")
        try:
            sink = MlflowTracker({"tracking_uri": uri, "experiment": "dirstore"})
        except ConfigError as exc:
            # The mlflow 3.x path. The pack wrapped it: the reader is
            # told which sink, which experiment and which URI, instead
            # of an unattributed MlflowException from inside a tracker.
            assert SINK_KIND in str(exc) and uri in str(exc)
            # ...and it left NOTHING behind. The module docstring promises
            # construction "proves the store instead of writing to it";
            # an eager makedirs made that false, littering a store
            # directory for a family the same call then refused.
            assert not pathlib.Path(uri).exists()
        else:
            # The mlflow 2.x / MLFLOW_ALLOW_FILE_STORE path: it really
            # works, and mlflow's own file store created the directory.
            sink.close()
            assert pathlib.Path(uri).is_dir()


# ---------------------------------------------------------------------------
# Where tracking config belongs, relative to the identity hash
# ---------------------------------------------------------------------------


class TestHashPlacement:
    def test_nothing_this_pack_owns_can_be_spelled_inside_pipeline(self):
        # The claim is about the NODE registry, so pin it THERE. (The
        # first version asserted that no node of a document this test
        # itself built carried a 'tracking_uri' param — true of every
        # document ever written, and unfalsifiable by any change to this
        # pack.) register() must leave the node registry untouched:
        # empty NODE_KINDS covers every name, and the kind name this
        # pack does claim must resolve as a SINK and nothing else.
        assert NODE_KINDS == ()
        assert SINK_KIND in SINK_KINDS
        assert SINK_KIND not in DEFAULT_NODE_KINDS

    def test_identity_recipe_is_pinned_so_moving_tracking_trips_here(
        self, tmp_path
    ):
        """The exclusion list, pinned — a move in EITHER direction fails.

        `tracking` is NOT in `DOC_NON_IDENTITY_SECTIONS`, so the section
        IS hash-graded: two runs differing only in WHERE their metrics
        land carry different identities. Whether that is right is a
        design question (it reads like `outputs`, which IS excluded) —
        what is settled is that moving it is not this pack's to make.
        `PipelineDocument.to_obj` emits `"tracking": null` ALWAYS
        (`document.py`), and `config_hash` pops excluded top-level keys
        before hashing, so excluding `tracking` moves EVERY document's
        hash — not only those declaring a sink. Measured:
        `examples/pipeline/mpl-figure.json`, which declares no tracking
        at all, goes e9d5f60c… -> 314cea4d… . That orphans every run dir
        and every stored artifact in the repo, so it needs an ADR and a
        baseline re-cut, and it must trip a test first — this one.
        """
        assert DOC_NON_IDENTITY_SECTIONS == ("env", "outputs", "schedule")
        untracked = banking_document(
            outputs=OutputsConfig(run_root=str(tmp_path / "runs"))
        )
        tracked = tracked_document(tmp_path, store_uri(tmp_path))
        tracked_elsewhere = tracked_document(
            tmp_path, store_uri(tmp_path, "elsewhere.db")
        )
        assert untracked.hash != tracked.hash
        assert tracked.hash != tracked_elsewhere.hash


# ---------------------------------------------------------------------------
# End to end against a real local store
# ---------------------------------------------------------------------------


def mlflow_client(store):
    """An MlflowClient reading ``store`` (skips when mlflow is absent)."""
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    return MlflowClient(tracking_uri=store)


class TestEndToEnd:
    def test_a_run_lands_flattened_params_and_metrics_in_the_store(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        doc = tracked_document(tmp_path, store, experiment="e2e")
        result = run_document(doc, asof=ASOF, registry=make_registry())

        experiment = client.get_experiment_by_name("e2e")
        assert experiment is not None
        runs = client.search_runs([experiment.experiment_id])
        assert len(runs) == 1
        run = runs[0]

        # The five identity fields...
        assert run.data.params["name"] == doc.name
        assert run.data.params["asof"] == ASOF
        assert run.data.params["document_hash"] == doc.hash
        assert run.data.params["run_hash"] == result.run_hash
        assert run.data.params["nodes"].startswith("events,")
        # ...beside E1's flattened '<node>.<param.path>' keys.
        assert run.data.params["events.n_events"] == "432"
        assert run.data.params["clip.lo"] == "0.02"
        assert run.data.params["size.stake_frac"] == "0.1"
        # Metrics are namespaced by the stage that produced them, so two
        # nodes reporting 'metrics.loss' never collide.
        assert run.data.metrics["size.final_bankroll"] == pytest.approx(1020.0)
        assert "validate.metrics.loss" in run.data.metrics
        assert run.info.status == "FINISHED"

    def test_the_default_store_is_resolved_in_one_place(self, tmp_path, monkeypatch):
        # The classic defect is params.get(k, <literal>) in BOTH the
        # validator and the run: validation approves a store the run never
        # opens. Observe the path EACH half resolved, separately.
        monkeypatch.chdir(tmp_path)
        # The VALIDATOR's: park a directory where the default sqlite FILE
        # belongs. Only a validator that resolved exactly ./mlruns.db can
        # refuse this — one resolving anything else sees a clean parent
        # and approves.
        (tmp_path / "mlruns.db").mkdir()
        problems = MlflowTracker.validate_params({})
        assert problems and "mlruns.db" in problems[0]
        (tmp_path / "mlruns.db").rmdir()
        assert MlflowTracker.validate_params({}) == []
        # The RUN's: a sink that declares NO tracking_uri, so `_open` has
        # to fall back the same way the validator did. Declaring the URI
        # here instead would leave the run-side default pinned by nothing
        # — `_open` could read `params.get("tracking_uri", <other>)` and
        # every assertion below would still hold. Keep this the suite's
        # ONLY user of the relative default URI: mlflow caches one engine
        # per URI STRING, so a second test chdir-ing elsewhere would
        # silently share this one's store.
        pytest.importorskip("mlflow")
        defaulted = MlflowTracker({"experiment": "defaulted"})
        defaulted.log_params({"name": "defaulted"})
        defaulted.close()
        # Named, not merely present: a run-side default resolving to any
        # OTHER store would leave that store's file beside this one.
        assert [p.name for p in sorted(tmp_path.glob("*.db"))] == ["mlruns.db"]
        client = mlflow_client(DEFAULT_TRACKING_URI)
        assert client.get_run(defaulted.run_id).data.params["name"] == "defaulted"
        # And the class docstring's Examples block, VERBATIM, so the
        # standard's "copy it and have a working object" is executed
        # rather than asserted. (The first version documented
        # "/tmp/mlruns", a DIRECTORY store, refused by mlflow 3.x.)
        sink = MlflowTracker({"tracking_uri": DEFAULT_TRACKING_URI})
        sink.log_params({"name": "demo", "train.lr": 0.001})
        sink.log_metrics("validate", {"metrics.loss": 0.31})
        sink.close()
        assert (tmp_path / "mlruns.db").is_file()

    def test_close_terminates_the_run_and_is_idempotent(self, tmp_path):
        # The driver calls close() in a finally, on every path including a
        # crashed run, and _Trackers swallows what close() raises — so a
        # second close must be a no-op rather than a silently logged error.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "closes"})
        sink.log_params({"name": "closes"})
        sink.close()
        sink.close()
        assert client.get_run(sink.run_id).info.status == "FINISHED"

    def test_a_long_param_value_is_truncated_to_the_store_limit(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "truncation"})
        try:
            sink.log_params({"huge": "x" * (MAX_PARAM_VALUE_CHARS * 3)})
        finally:
            sink.close()
        run = client.get_run(sink.run_id)
        assert len(run.data.params["huge"]) == MAX_PARAM_VALUE_CHARS

    def test_tags_and_run_name_reach_the_store(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker(
            {
                "tracking_uri": store,
                "experiment": "tagged",
                "run_name": "named-run",
                "tags": {"owner": "pipeline"},
            }
        )
        sink.log_params({"name": "tagged"})
        sink.close()
        run = client.get_run(sink.run_id)
        assert run.data.tags["owner"] == "pipeline"
        assert run.info.run_name == "named-run"


# ---------------------------------------------------------------------------
# What the sink must NOT leave behind
# ---------------------------------------------------------------------------


class TestNoEmptyRuns:
    """A run appears in the store only once something was logged to it.

    The driver constructs sinks BEFORE resolve finishes and closes them
    on every pre-execution refusal (``driver.py``'s
    ``except BaseException`` around resolve — its comment even says "an
    mlflow-style tracker may hold a remote run from ``__init__``"). A
    sink that opened its mlflow run in ``__init__`` therefore wrote an
    EMPTY, FINISHED run for each refusal, indistinguishable when browsing
    from a successful one — poisoning the very cross-run comparison this
    pack exists to provide. Construction still proves the store usable;
    only the RUN is deferred to the first log.
    """

    def test_a_sink_that_never_logged_leaves_no_run(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "empty"})
        assert sink.run_id == ""
        sink.close()
        experiment = client.get_experiment_by_name("empty")
        # Construction is still LOUD: it opened the store and the
        # experiment, which is what proves the config honourable.
        assert experiment is not None
        assert len(client.search_runs([experiment.experiment_id])) == 0

    def test_a_refused_rerun_adds_no_run(self, tmp_path):
        # The documented normal case: same name+asof+identity refuses,
        # "reruns need a new asof or name". That refusal happens after
        # the sinks are open.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        run_document(
            tracked_document(tmp_path, store, experiment="reruns"),
            asof=ASOF,
            registry=make_registry(),
        )
        with pytest.raises(ValueError):
            run_document(
                tracked_document(tmp_path, store, experiment="reruns"),
                asof=ASOF,
                registry=make_registry(),
            )
        experiment = client.get_experiment_by_name("reruns")
        runs = client.search_runs([experiment.experiment_id])
        assert len(runs) == 1
        assert runs[0].data.params["asof"] == ASOF

    def test_a_lost_experiment_creation_race_does_not_abort_the_run(
        self, tmp_path, monkeypatch
    ):
        # Two run_document processes against one store both observe
        # get_experiment_by_name() -> None and both call
        # create_experiment(); the loser used to take an exception out of
        # __init__ as a ConfigError, i.e. TRACKING killed a correctly
        # configured run. A lost race is not a misconfiguration.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        expected = client.create_experiment("race")

        class LosesTheRace:
            """A client that reports the experiment missing exactly once."""

            def __init__(self, inner):
                self._inner = inner
                self._lied = False

            def get_experiment_by_name(self, name):
                if not self._lied:
                    self._lied = True
                    return None
                return self._inner.get_experiment_by_name(name)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(
            "mlflow.tracking.MlflowClient",
            lambda tracking_uri=None: LosesTheRace(client),
        )
        sink = MlflowTracker({"tracking_uri": store, "experiment": "race"})
        sink.log_params({"name": "raced"})
        sink.close()
        assert client.get_run(sink.run_id).info.experiment_id == expected


# ---------------------------------------------------------------------------
# The two ways an OPEN can fail, and why they must not be the same way
# ---------------------------------------------------------------------------


class TestExperimentState:
    """An experiment that exists but cannot be written to is a MISconfig.

    Deleting an experiment is a normal mlflow-UI operation and it is not
    reversible from a document, so the next run of that document must be
    told — at construction, where the driver is still outside
    ``_Trackers``' swallow. ``get_experiment_by_name`` keeps returning the
    deleted experiment (lifecycle ``"deleted"``), so a non-None answer
    proves nothing on its own.
    """

    def test_a_deleted_experiment_is_refused_at_construction(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        client.delete_experiment(client.create_experiment("gone"))
        with pytest.raises(ConfigError) as exc:
            MlflowTracker({"tracking_uri": store, "experiment": "gone"})
        assert "gone" in str(exc.value)

    def test_a_deleted_experiment_does_not_fail_silently_through_the_run(
        self, tmp_path
    ):
        # The failure this replaces: construction passed, the first
        # log_params raised inside _Trackers, the run reported success
        # and NOTHING was tracked.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        client.delete_experiment(client.create_experiment("gone-e2e"))
        with pytest.raises(ConfigError):
            run_document(
                tracked_document(tmp_path, store, experiment="gone-e2e"),
                asof=ASOF,
                registry=make_registry(),
            )


class TestDegradedServer:
    """A reachable server that then fails must not take the run with it.

    The plan-time probe proves a TCP connect; it cannot prove the server
    HEALTHY, and health is not a property of the document. ``_open_sinks``
    runs OUTSIDE ``run_document``'s try (``driver.py``), so a sink raising
    at construction aborts the run before a single node executes — which
    is exactly what ``_Trackers``' swallow exists to prevent. A store that
    was configured correctly and is merely having a bad day therefore
    DISABLES the sink and warns, and only a genuine misconfiguration
    raises.
    """

    def test_a_degraded_server_still_plans_clean(self, degraded_server):
        # Reachability is all the stdlib probe can prove, and it holds.
        assert (
            MlflowTracker.validate_params(
                {"tracking_uri": degraded_server, "connect_timeout": 1}
            )
            == []
        )

    def test_a_degraded_server_disables_the_sink_instead_of_raising(
        self, degraded_server, caplog
    ):
        pytest.importorskip("mlflow")
        sink = MlflowTracker({"tracking_uri": degraded_server, "connect_timeout": 1})
        # Disabled, not dead: the seam calls stay no-ops rather than
        # raising into the swallow, where nobody would see them.
        sink.log_params({"name": "degraded"})
        sink.log_metrics("train", {"metrics.loss": 1.0})
        sink.close()
        assert sink.run_id == ""
        assert any("mlflow" in rec.getMessage() for rec in caplog.records)

    def test_a_degraded_server_does_not_abort_the_run(self, tmp_path, degraded_server):
        pytest.importorskip("mlflow")
        result = run_document(
            tracked_document(tmp_path, degraded_server, connect_timeout=1),
            asof=ASOF,
            registry=make_registry(),
        )
        assert result.state == "ran"

    def test_connect_timeout_bounds_construction_not_only_the_probe(
        self, degraded_server
    ):
        # `connect_timeout` documents itself as the server budget. Before
        # it reached mlflow's own HTTP knobs it bounded ONLY the stdlib
        # TCP probe, and construction then ran under mlflow's default
        # retry/backoff policy: measured at over 200s against this very
        # server, ahead of every node, with no output at all.
        pytest.importorskip("mlflow")
        started = time.perf_counter()
        MlflowTracker({"tracking_uri": degraded_server, "connect_timeout": 1})
        assert time.perf_counter() - started < 60


# ---------------------------------------------------------------------------
# The store's OTHER length limit, and the ordering of restated metrics
# ---------------------------------------------------------------------------


class TestEntityKeys:
    """Param and metric KEYS are capped like values, and for the same reason.

    ``log_batch`` is all-or-nothing, so one over-long key used to cost the
    whole call — every param including the five identity fields — and
    ``_Trackers`` swallowed the exception. Keys are the driver's flattened
    ``"<node>.<param.path>"`` strings, whose length is bounded by nothing.
    """

    def test_an_overlong_param_key_does_not_cost_the_other_params(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "keys"})
        sink.log_params({"name": "keyed", "deep." * 80 + "lr": "0.01"})
        sink.close()
        params = client.get_run(sink.run_id).data.params
        assert params["name"] == "keyed"
        assert len(params) == 2
        assert max(len(key) for key in params) <= MAX_ENTITY_KEY_CHARS

    def test_two_overlong_keys_stay_two_params(self, tmp_path):
        # A cap that merely truncates collides: two flattened paths
        # sharing a long prefix would land as one param, and mlflow
        # refuses the conflicting second value — back into the swallow.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "collide"})
        stem = "deep." * 80
        sink.log_params({stem + "lr": "0.01", stem + "momentum": "0.9"})
        sink.close()
        assert len(client.get_run(sink.run_id).data.params) == 2

    def test_an_overlong_metric_key_does_not_cost_the_other_metrics(self, tmp_path):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "metrickeys"})
        sink.log_metrics("stage." * 80, {"loss": 1.0})
        sink.log_metrics("train", {"loss": 2.0})
        sink.close()
        metrics = client.get_run(sink.run_id).data.metrics
        assert metrics["train.loss"] == pytest.approx(2.0)
        assert len(metrics) == 2


class TestMetricOrdering:
    """The LAST write of a metric wins, deterministically.

    Restatement is a shipped pattern, not a hypothetical: the driver
    re-logs a re-executed node's metrics because "records and sinks must
    reflect the FINAL pass" (spec §8), and ``ctx.tracker.log_metrics`` is a
    node-facing seam a per-epoch loop may call. mlflow breaks a latest-
    metric tie by max(step), then max(timestamp), then max(VALUE) — so at
    a fixed step and a shared millisecond the LARGER value wins, whenever
    it was written.
    """

    def test_a_restated_metric_reports_the_last_value_not_the_largest(
        self, tmp_path, monkeypatch
    ):
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "restated"})
        monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
        sink.log_metrics("train", {"metrics.loss": 5.0})
        sink.log_metrics("train", {"metrics.loss": 1.0})
        sink.close()
        assert client.get_run(sink.run_id).data.metrics[
            "train.metrics.loss"
        ] == pytest.approx(1.0)

    def test_a_series_carries_an_increasing_step_per_key(self, tmp_path):
        # A node logging per-epoch values must get an x-axis, and each key
        # counts its OWN writes — a shared counter would leave holes.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "series"})
        for epoch in range(3):
            sink.log_metrics("train", {"loss": 1.0 / (epoch + 1), "seen": epoch})
        sink.close()
        history = client.get_metric_history(sink.run_id, "train.loss")
        assert [point.step for point in history] == [0, 1, 2]
