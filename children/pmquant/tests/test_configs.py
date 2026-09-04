"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it. Source configs are graded against the
dskit connector pack they name (default-deny against ``spec()``), the
asset model against the assets engine, and every run document against
the pipeline planner with the child's kinds registered.
"""

import json
import os

import pytest
from dskit.assets import load_model
from dskit.onboarding import check_config, resolve_connector
from dskit.pipeline.document import load_document

import pmquant  # noqa: F401 — import = registration of the child's kinds

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

#: Source config -> the registered connector kind it is written for. A
#: deliberate restatement: the config cannot name its own connector (the
#: CLI does), so this table is what pins the pairing.
SOURCE_CONNECTORS = {
    "source-pit-ladders.json": "localtables",
    "source-settled-markets.json": "localtables",
    "source-kalshi.json": "kalshi",
    "source-polymarket.json": "polymarket",
    "source-predexon.json": "predexon",
}

#: Reserved platform keys acquire strips before a connector sees config
#: (ADR-0036); ``check`` must see the config the connector will.
PLATFORM_KEYS = ("storage", "notes")


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


def _connector_view(config):
    return {k: v for k, v in config.items() if k not in PLATFORM_KEYS}


def test_every_shipped_source_config_is_listed():
    shipped = sorted(n for n in os.listdir(CONFIGS) if n.startswith("source-"))
    assert shipped == sorted(SOURCE_CONNECTORS), (
        "a source config was added or removed without updating the pairing table"
    )


@pytest.mark.parametrize("name", sorted(SOURCE_CONNECTORS))
def test_source_config_validates_against_its_connector_spec(name):
    config = _raw(name)
    assert isinstance(config.get("notes"), str) and config["notes"], (
        f"{name}: every config carries a why in `notes`"
    )
    connector = resolve_connector(SOURCE_CONNECTORS[name])()
    check_config(connector, config)  # default-deny against spec()


@pytest.mark.parametrize(
    "name", [n for n, kind in sorted(SOURCE_CONNECTORS.items()) if kind == "localtables"]
)
def test_on_disk_source_configs_pass_the_connector_check_offline(name):
    # localtables' check() reads only the config shape until a path exists;
    # the data root is machine-specific, so a missing directory is the one
    # refusal a fresh clone may legitimately see — and it must NAME the path.
    from dskit.onboarding import AssetError

    config = _connector_view(_raw(name))
    connector = resolve_connector("localtables")()
    try:
        connector.check(config)
    except AssetError as err:
        assert os.path.expanduser(config["path"]) in str(err) or "path" in str(err)


def test_secret_knobs_name_variables_never_values():
    config = _raw("source-predexon.json")
    assert config["api_key_env"] == "PREDEXON_API_KEY"
    assert "api_key" not in config, "a key VALUE in a config is a leak"


def test_suite_ladders_validates_and_names_its_rules():
    from dskit.onboarding import load_suite

    suite = load_suite(_path("suite-ladders.json"))
    targets = {r.target for r in suite.rules}
    assert targets == {"predexon_l2_pit", "markets"}, (
        "the suite must cover exactly the two imported streams"
    )
    # A deliberate restatement of the settlement vocabulary the reader
    # refuses on — sourced here, never from the code it validates.
    vocab = [r for r in suite.rules if r.id == "markets-result-vocabulary"][0]
    assert vocab.kwargs["values"] == ["yes", "no", ""]


def test_asset_model_validates_and_keeps_its_shape():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}
    governed = sorted(k for k, spec in model.kinds.items() if spec.states)
    assert governed == ["dataset"], (
        f"only 'dataset' is governed by design, got lifecycles on {governed}"
    )


#: The params a twin may legitimately differ on, per node: where the data
#: lives, the fee book, the eligibility bar, the training budget. Anything
#: else differing means the real run no longer proves what the test ran.
TWIN_FREEDOMS = {
    "ladder_records": {"source"},
    "settlements": {"source"},
    "eligible_family": {"min_events"},
    "banking_report": {"min_events"},
    "run_report": {"min_events"},
    "seed_0": {"epochs"},
    "seed_1": {"epochs"},
    "size": {"fee_rate_by_series"},
}


def test_the_real_twin_differs_from_the_proof_only_where_allowed():
    proof, twin = _raw("run-e2e.json"), _raw("run-kalshi-ladders.json")
    assert proof["splits"] == {k: v for k, v in twin["splits"].items()}, "splits must agree"
    assert set(proof["pipeline"]) == set(twin["pipeline"]), "same DAG keys"
    for key, spec in proof["pipeline"].items():
        other = twin["pipeline"][key]
        assert spec["uses"] == other["uses"], key
        assert spec.get("inputs", {}) == other.get("inputs", {}), key
        differing = {
            k for k in set(spec.get("params", {})) | set(other.get("params", {}))
            if spec.get("params", {}).get(k) != other.get("params", {}).get(k)
        }
        assert differing <= TWIN_FREEDOMS.get(key, set()), (key, differing)
    # The real fee book is per SERIES, dated, with provenance beside each entry.
    book = twin["pipeline"]["size"]["params"]["fee_rate_by_series"]
    assert len(book) >= 20 and all("cases" in v and "source" in v for v in book.values())
    # The production eligibility bar is the doctrine's, and never lower.
    assert twin["pipeline"]["eligible_family"]["params"]["min_events"] == 50


@pytest.mark.parametrize(
    "name", sorted(n for n in os.listdir(CONFIGS) if n.startswith("run-"))
)
def test_every_run_document_plans_with_the_child_registered(name):
    # load_document raises naming the path on any shape violation; the
    # planner grades roles, wiring and params with the child's kinds live.
    from dskit.pipeline.planner import plan

    document = load_document(_path(name))
    assert document.hash, "a valid document always has an identity hash"
    plan(document)
