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

import pathlib
import socket
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
    MAX_PARAM_VALUE_CHARS,
    NODE_KINDS,
    SINK_KIND,
    TRACKING_URI_SCHEMES,
    MlflowTracker,
    register,
)
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

    def test_an_unreachable_server_fails_the_plan_not_the_run(self, tmp_path):
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


# ---------------------------------------------------------------------------
# Where tracking config belongs, relative to the identity hash
# ---------------------------------------------------------------------------


class TestHashPlacement:
    def test_sink_config_never_reaches_the_pipeline_section(self, tmp_path):
        # The pack ships no node kind and no node params, so nothing it
        # owns can be spelled inside `pipeline`.
        doc = tracked_document(tmp_path, store_uri(tmp_path))
        for spec in doc.pipeline.values():
            assert "tracking_uri" not in spec.params

    def test_identity_recipe_is_pinned_so_moving_tracking_trips_here(
        self, tmp_path
    ):
        """The exclusion list, pinned — a move in EITHER direction fails.

        `tracking` is NOT in `DOC_NON_IDENTITY_SECTIONS` today, so the
        section IS hash-graded: two runs differing only in WHERE their
        metrics land carry different identities. Whether that is right is
        a design question (it reads like `outputs`, which IS excluded);
        what is not in question is that changing it renames every
        document that declares a sink and orphans their run dirs. So it
        must trip a test first — this one.
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
        # validator and the run: validation approves a value the run never
        # uses. Prove they agree by letting the default do the work end to
        # end — the store the run actually opened must be the one the
        # validator probed.
        monkeypatch.chdir(tmp_path)
        pytest.importorskip("mlflow")
        sink = MlflowTracker({"experiment": "defaulted"})
        sink.close()
        assert (tmp_path / "mlruns.db").is_file()

    def test_close_terminates_the_run_and_is_idempotent(self, tmp_path):
        # The driver calls close() in a finally, on every path including a
        # crashed run, and _Trackers swallows what close() raises — so a
        # second close must be a no-op rather than a silently logged error.
        store = store_uri(tmp_path)
        client = mlflow_client(store)
        sink = MlflowTracker({"tracking_uri": store, "experiment": "closes"})
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
        sink.close()
        run = client.get_run(sink.run_id)
        assert run.data.tags["owner"] == "pipeline"
        assert run.info.run_name == "named-run"
