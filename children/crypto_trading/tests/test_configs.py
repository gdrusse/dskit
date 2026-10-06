"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it.
"""

import datetime
import glob
import hashlib
import json
import os
import re

import pytest

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.onboarding.libs import kalshi
from dskit.onboarding.libs.httpblobs import RECORD_FIELDS, HttpBlobsConnector
from dskit.onboarding.libs.kalshi import KalshiConnector
from dskit.onboarding.libs.localtables import LocalTablesConnector
from dskit.pipeline.document import load_document

from crypto_trading.connectors import SampleConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")


def _path(name):
    return os.path.join(CONFIGS, name)


def test_run_sample_is_a_valid_pipeline_document():
    # load_document raises naming the path on any shape violation.
    document = load_document(_path("run-sample.json"))
    assert set(document.pipeline) == {"sample", "enrich"}, (
        "run-sample.json drifted from the documented sample -> enrich DAG"
    )
    assert document.hash, "a valid document always has an identity hash"


def test_asset_model_validates_and_keeps_its_shape():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}, (
        "asset-model.json drifted from the documented dataset/artifact pair"
    )
    governed = sorted(k for k, spec in model.kinds.items() if spec.states)
    assert governed == ["dataset"], (
        f"only 'dataset' is governed by design, got lifecycles on {governed}"
    )


def test_suite_sample_validates_and_names_its_rules():
    suite = load_suite(_path("suite-sample.json"))
    assert [r.id for r in suite.rules] == \
        ["rows-arrived", "value-present", "value-in-range"], (
        "suite-sample.json drifted from its documented rule set"
    )


def test_source_sample_validates_against_the_connectors_spec():
    with open(_path("source-sample.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    connector = SampleConnector()
    check_config(connector, config)  # default-deny against spec()
    # The reserved "storage" block (ADR-0036) is platform config —
    # acquire strips it before the connector sees config; mirror that.
    connector.check({k: v for k, v in config.items() if k != "storage"})


# -- stage A: the data-pull sources and their suites --------------------------
#
# The expectations below are RESTATED on purpose (the vendor's URL layout, the
# series universe, the suite vocabularies): an assertion that read its answer
# from the config it checks would assert nothing. Where two configs must agree
# the test pins that agreement.

KALSHI_SOURCES = (
    "source-kalshi-crypto.json",
    "source-kalshi-crypto-candles-btc.json",
    "source-kalshi-crypto-candles-eth.json",
    "source-kalshi-crypto-books-15m.json",
    "source-kalshi-crypto-books-hourly.json",
)
#: sources that pull settled history (the books sources read open markets only)
KALSHI_HISTORY = KALSHI_SOURCES[:3]
KALSHI_SERIES = ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")
KALSHI_15M = ("KXBTC15M", "KXETH15M")
KALSHI_HOURLY = ("KXBTC", "KXBTCD", "KXETH", "KXETHD")
#: suite file -> the field set the pack emits for its stream, so a rule naming a
#: field the pack does not emit fails here instead of passing vacuously.
SUITE_FIELDS = {
    "suite-kalshi-crypto-markets.json": kalshi.MARKET_FIELDS,
    "suite-kalshi-crypto-candles.json": kalshi.CANDLE_FIELDS,
    "suite-kalshi-crypto-fees.json": kalshi.FEE_FIELDS,
    "suite-kalshi-crypto-books.json": kalshi.ORDERBOOK_FIELDS,
    "suite-binance-files.json": RECORD_FIELDS,
}
#: source file -> (vendor URL template, the transform class)
BINANCE_SOURCES = {
    "source-binance-btcusdt-1m.json": (
        "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{entity}.zip",
        "crypto_trading.binance_vision:BinanceKlines"),
    "source-binance-ethusdt-1m.json": (
        "https://data.binance.vision/data/spot/daily/klines/ETHUSDT/1m/ETHUSDT-1m-{entity}.zip",
        "crypto_trading.binance_vision:BinanceKlines"),
    "source-binance-btcbvol.json": (
        "https://data.binance.vision/data/option/daily/BVOLIndex/BTCBVOLUSDT/"
        "BTCBVOLUSDT-BVOLIndex-{entity}.zip",
        "crypto_trading.binance_vision:BinanceBvol"),
    "source-binance-ethbvol.json": (
        "https://data.binance.vision/data/option/daily/BVOLIndex/ETHBVOLUSDT/"
        "ETHBVOLUSDT-BVOLIndex-{entity}.zip",
        "crypto_trading.binance_vision:BinanceBvol"),
}
DATES_FILE = "binance_vision_dates.json"
#: registration configs for tables a run of this child derives and a later run reads (localtables)
DERIVED_SOURCES = ("source-features-15m.json",)
#: suite file -> the one stream it targets (a snapshot holds one stream, so a
#: suite naming two would fail the absent one's row_count).
SUITES = {
    "suite-sample.json": "samples",
    "suite-kalshi-crypto-markets.json": "markets",
    "suite-kalshi-crypto-candles.json": "candles",
    "suite-kalshi-crypto-fees.json": "fee_schedules",
    "suite-kalshi-crypto-books.json": "orderbooks",
    "suite-binance-files.json": "files",
}


def _load(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


def _strip_platform(config):
    """The config as the connector sees it: ``storage`` is the platform's."""
    return {k: v for k, v in config.items() if k != "storage"}


@pytest.fixture
def child_root_env(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRADING_ROOT", CHILD_ROOT)


def test_every_source_and_suite_file_is_covered_here():
    found = {os.path.basename(p) for p in glob.glob(_path("source-*.json"))}
    assert found == {"source-sample.json", *KALSHI_SOURCES, *BINANCE_SOURCES, *DERIVED_SOURCES}, (
        "a new source config must be added to this file's tables, not just to configs/")
    suites = {os.path.basename(p) for p in glob.glob(_path("suite-*.json"))}
    assert suites == set(SUITES)


@pytest.mark.parametrize("name", KALSHI_SOURCES)
def test_kalshi_sources_pass_default_deny_and_resolve_their_knobs(name):
    config = _load(name)
    connector = KalshiConnector()
    check_config(connector, config)
    knobs = connector.resolve_knobs(_strip_platform(config))
    if name in KALSHI_HISTORY:
        assert knobs["statuses"] == ["settled"], "history is settled markets, every row final"
    else:
        assert "statuses" not in config, "the orderbooks stream ignores statuses; do not imply it matters"


@pytest.mark.parametrize("name", KALSHI_HISTORY[1:])
def test_the_candle_sources_ask_for_one_minute_candles(name):
    knobs = KalshiConnector().resolve_knobs(_load(name))
    assert knobs["period_interval"] == 1, "1-minute candles are the modelling resolution"


def test_kalshi_series_universe_and_how_each_source_slices_it():
    series = {name: sorted(_load(name)["series"]) for name in KALSHI_SOURCES}
    assert series["source-kalshi-crypto.json"] == sorted(KALSHI_SERIES)
    assert series["source-kalshi-crypto-candles-btc.json"] == ["KXBTC15M"]
    assert series["source-kalshi-crypto-candles-eth.json"] == ["KXETH15M"]
    assert series["source-kalshi-crypto-books-15m.json"] == sorted(KALSHI_15M), (
        "the 15-minute books are a few requests a pass: a short cadence, their own source")
    assert series["source-kalshi-crypto-books-hourly.json"] == sorted(KALSHI_HOURLY)
    assert sorted(KALSHI_15M + KALSHI_HOURLY) == sorted(KALSHI_SERIES), (
        "the two books sources partition the six series: none missed, none read twice")


@pytest.mark.parametrize("name", sorted(BINANCE_SOURCES))
def test_binance_sources_pass_default_deny_and_resolve_their_knobs(name, child_root_env):
    config = _load(name)
    url, transform = BINANCE_SOURCES[name]
    connector = HttpBlobsConnector()
    check_config(connector, config)
    connector.check(config)  # imports the transform by reference; moves no data
    knobs = connector.resolve_knobs(config)
    assert knobs["url_template"] == url
    assert knobs["transform"] == transform
    dates = _load(DATES_FILE)["dates"]
    assert knobs["entities"] == dates
    assert knobs["stream"] == "files"
    assert knobs["paths"][dates[0]] == {
        "relpath_template": f"{dates[0]}.parquet", "raw_relpath_template": f"_raw/{dates[0]}.zip"}


def test_binance_sources_agree_where_they_must(child_root_env):
    configs = [_load(name) for name in BINANCE_SOURCES]
    for knob in ("entities_file", "entities_key", "entities_sha256", "as_of", "headers",
                 "throttle_s", "timeout", "max_retries", "max_bytes", "stream",
                 "relpath_template", "raw_relpath_template"):
        assert len({json.dumps(c[knob], sort_keys=True) for c in configs}) == 1, knob
    with open(_path(DATES_FILE), "rb") as handle:
        assert configs[0]["entities_sha256"] == hashlib.sha256(handle.read()).hexdigest()


#: HEAD sweep of every URL on 2026-10-06 (data.binance.vision answered 404 NoSuchKey): missing days
#: per source and the range endpoints the notes must name. Restated, not read from the configs.
KNOWN_MISSING = {
    "source-binance-btcusdt-1m.json": (0, ()),
    "source-binance-ethusdt-1m.json": (0, ()),
    "source-binance-btcbvol.json": (26, ("2023-09-25", "2023-10-24", "2024-06-11", "2024-06-12",
                                         "2024-06-30", "2025-12-15", "2026-01-04")),
    "source-binance-ethbvol.json": (26, ("2023-09-25", "2023-10-24", "2024-06-11", "2024-06-12",
                                         "2024-06-30", "2025-12-15", "2026-01-04")),
}


@pytest.mark.parametrize("name, known", sorted(KNOWN_MISSING.items()))
def test_max_refused_covers_the_measured_missing_days_with_headroom_and_the_notes_say_so(name, known):
    count, endpoints = known
    config = _load(name)
    assert count < config["max_refused"] <= count + 10, (
        "a pull that tolerates fewer refusals than the vendor lacks aborts after an hour of work")
    assert "HEAD sweep 2026-10-06" in config["notes"] and f"{count} days returned 404" in config["notes"]
    for day in endpoints:
        assert day in config["notes"], f"{name}: the notes must list the missing range ending {day}"


def test_binance_dates_are_whole_consecutive_utc_days_ending_before_as_of():
    dates = [datetime.date.fromisoformat(d) for d in _load(DATES_FILE)["dates"]]
    assert dates[0] == datetime.date(2023, 6, 20), "the BVOL archive starts here; klines go further back"
    assert all(b - a == datetime.timedelta(days=1) for a, b in zip(dates, dates[1:]))
    as_of = datetime.datetime.fromisoformat(_load("source-binance-btcusdt-1m.json")["as_of"])
    assert dates[-1] < as_of.date(), "a day's file is published after the day ends"


@pytest.mark.parametrize("name", sorted(BINANCE_SOURCES))
def test_binance_configs_state_the_licence(name):
    notes = _load(name)["notes"]
    for phrase in ("CC BY-NC-SA", "research use only", "not for live trading"):
        assert phrase in notes, f"{name}: the notes must say {phrase!r}"


def test_configs_carry_no_machine_path_and_one_env_variable():
    for path in glob.glob(_path("source-*.json")) + glob.glob(_path("suite-*.json")):
        text = open(path, encoding="utf-8").read()
        for needle in ("/home/", "/Users/", "/tmp", "C:\\"):
            assert needle not in text, f"{os.path.basename(path)} names a machine path: {needle}"
        variables = set(re.findall(r"\$([A-Z_]+)", text))
        assert variables <= {"CRYPTO_TRADING_ROOT"}, os.path.basename(path)


@pytest.mark.parametrize("name, fields", sorted(SUITE_FIELDS.items()))
def test_every_field_a_suite_rule_names_is_one_the_pack_emits(name, fields):
    for rule in load_suite(_path(name)).rules:
        named = [rule.kwargs[k] for k in ("field", "group_by") if k in rule.kwargs]
        for field in [f for n in named for f in (n if isinstance(n, list) else [n])]:
            assert field in fields, f"{name}: rule {rule.id!r} names {field!r}, not in {tuple(fields)}"


@pytest.mark.parametrize("name", ["suite-kalshi-crypto-markets.json", "suite-kalshi-crypto-fees.json"])
def test_the_distinct_series_count_equals_the_source_series_list(name):
    expected = len(_load("source-kalshi-crypto.json")["series"])
    (rule,) = [r for r in load_suite(_path(name)).rules if r.rule == "distinct_count"]
    assert rule.kwargs["min"] == rule.kwargs["max"] == expected


def test_agents_md_mirrors_claude_md_but_for_its_title():
    claude = open(os.path.join(CHILD_ROOT, "CLAUDE.md"), encoding="utf-8").read().splitlines()
    agents = open(os.path.join(CHILD_ROOT, "AGENTS.md"), encoding="utf-8").read().splitlines()
    assert [a for a, c in zip(agents, claude) if a != c] == ["# AGENTS.md — crypto_trading (a dskit child)"]
    assert len(agents) == len(claude)


@pytest.mark.parametrize("name, stream", sorted(SUITES.items()))
def test_every_suite_validates_and_targets_one_stream(name, stream):
    suite = load_suite(_path(name))
    assert {rule.target for rule in suite.rules} == {stream}
    assert len({rule.id for rule in suite.rules}) == len(suite.rules)
    assert any(rule.rule == "row_count" and rule.severity == "error" for rule in suite.rules), (
        "an empty pull is a broken pull: every suite gates on rows arriving")


@pytest.mark.parametrize("name", ["suite-kalshi-crypto-markets.json", "suite-kalshi-crypto-fees.json",
                                  "suite-kalshi-crypto-books.json"])
def test_kalshi_suites_restate_the_series_universe_and_agree_with_the_source(name):
    suite = load_suite(_path(name))
    (rule,) = [r for r in suite.rules if r.kwargs.get("field") == "series_ticker"
               and r.rule == "accepted_values"]
    assert sorted(rule.kwargs["values"]) == sorted(KALSHI_SERIES)
    assert sorted(rule.kwargs["values"]) == sorted(_load("source-kalshi-crypto.json")["series"])


# -- stage B: the feature document and the derived-table registration -------------------
#
# Restated, not read from the document: the vocabularies below are what the stage A suites
# and sources say, so a document that drifted from them fails here.

FEATURES_DOC = "run-features-15m.json"
STORE_ROOT = "~/data/crypto_trading/ob"
FEATURES_DIR = "~/data/crypto_trading/features-15m"


def _doc():
    return _load(FEATURES_DOC)


def _nodes(uses_suffix):
    return {key: spec for key, spec in _doc()["pipeline"].items() if spec["uses"].endswith(uses_suffix)}


def test_the_features_document_loads_and_every_node_says_why():
    document = load_document(_path(FEATURES_DOC))
    assert document.hash
    raw = _doc()
    assert raw["notes"].strip()
    for key, spec in raw["pipeline"].items():
        assert spec.get("notes", "").strip(), f"node {key!r} has no notes: say why it is wired this way"


def test_every_store_root_in_the_document_is_one_value():
    roots = {spec["params"]["root"] for spec in _doc()["pipeline"].values() if "root" in spec.get("params", {})}
    assert roots == {STORE_ROOT}, "one store, named once per node: they must agree"
    for spec in _doc()["pipeline"].values():
        assert "/home/" not in json.dumps(spec) and "/tmp" not in json.dumps(spec)


def test_the_readers_name_the_sources_the_runbook_registers():
    sources = {os.path.basename(p)[len("source-"):-len(".json")] for p in glob.glob(_path("source-*.json"))}
    pipeline = _doc()["pipeline"]
    assert pipeline["markets"]["params"]["source"] == "kalshi-crypto"
    assert pipeline["fee_schedules"]["params"]["source"] == "kalshi-crypto"
    assert pipeline["candles_btc"]["params"]["source"] == "kalshi-crypto-candles-btc"
    assert pipeline["candles_eth"]["params"]["source"] == "kalshi-crypto-candles-eth"
    named = {pipeline[k]["params"]["source"] for k in ("markets", "fee_schedules", "candles_btc", "candles_eth")}
    assert named <= sources
    for tape in pipeline["streams"]["params"]["streams"].values():
        assert tape["source"] in sources and tape["stream"] == "files"


def test_the_document_reads_the_15_minute_series_the_candle_sources_pull():
    pipeline = _doc()["pipeline"]
    assert sorted(pipeline["markets"]["params"]["series"]) == sorted(KALSHI_15M)
    assets = pipeline["spot"]["params"]["assets"]
    assert sorted(s for a in assets.values() for s in a["series"]) == sorted(KALSHI_15M)
    assert _load("source-kalshi-crypto-candles-btc.json")["series"] == assets["BTC"]["series"]
    assert _load("source-kalshi-crypto-candles-eth.json")["series"] == assets["ETH"]["series"]


def test_the_market_vocabulary_is_the_one_the_stage_a_suite_gates():
    from crypto_trading.payoffs import PAYOFFS

    rules = {r.id: r for r in load_suite(_path("suite-kalshi-crypto-markets.json")).rules}
    params = _doc()["pipeline"]["markets"]["params"]
    geometries = set(rules["markets-strike-type-set"].kwargs["values"])
    assert set(params["payoff_by_strike_type"]) == geometries, "every geometry the suite lets through has a payoff"
    assert set(params["payoff_by_strike_type"].values()) <= set(PAYOFFS)
    assert set(params["result_labels"]) == set(rules["markets-result-vocabulary"].kwargs["values"])
    assert set(params["result_labels"].values()) == {0, 1}
    assert "finalized" in params["settled_statuses"], "the venue spells a settled payload 'finalized'"


def test_the_fee_types_the_node_prices_cover_what_the_fee_suite_accepts():
    from crypto_trading.fees import FEE_MODELS

    (rule,) = [r for r in load_suite(_path("suite-kalshi-crypto-fees.json")).rules
               if r.kwargs.get("field") == "fee_type"]
    params = _doc()["pipeline"]["fees"]["params"]
    assert set(rule.kwargs["values"]) <= set(params["base_rate_by_type"]) <= set(FEE_MODELS)


def test_the_bar_and_column_vocabulary_matches_the_binance_files_stage_a_writes():
    from crypto_trading.binance_vision import BVOL_COLUMNS, KLINE_COLUMNS

    spot = _doc()["pipeline"]["spot"]["params"]
    assert spot["bar_ms"] == 60_000
    for name in BINANCE_SOURCES:
        if "usdt-1m" in name:
            assert "/1m/" in BINANCE_SOURCES[name][0], "bar_ms is the interval of the klines the source pulls"
    assert set(spot["columns"]["klines"].values()) <= set(KLINE_COLUMNS)
    assert set(spot["columns"]["bvol"].values()) <= set(BVOL_COLUMNS)
    template = _load("source-binance-btcusdt-1m.json")["relpath_template"]
    assert spot["day_relpath_template"] == template.replace("{entity}", "{day}")


def test_the_tape_names_the_manifest_node_declares_are_the_ones_the_spot_node_looks_up():
    from crypto_trading.day_series import tape_name

    pipeline = _doc()["pipeline"]
    streams = pipeline["streams"]["params"]["streams"]
    expected = {}
    for asset, spec in pipeline["spot"]["params"]["assets"].items():
        expected[tape_name(asset, "klines")] = spec["klines"]
        expected[tape_name(asset, "bvol")] = spec["bvol"]
    assert streams == expected


def test_every_fair_value_column_reads_a_volatility_column_that_exists():
    from crypto_trading.vol_estimators import build_estimators

    spot = _doc()["pipeline"]["spot"]["params"]
    columns = {e.column() for e in build_estimators(spot["estimators"])} | {"bvol_per_sqrt_s"}
    fairs = _nodes(":FairValue")
    assert fairs, "the document prices at least one fair value"
    for key, spec in fairs.items():
        assert spec["params"]["vol_field"] in columns, f"{key} reads a column no node writes"
        assert spec["params"]["averaging_window_s"] == 60, "the BRTI averages the last 60 seconds"
    kill = _doc()["pipeline"]["kill_test"]["params"]
    assert kill["fair_fields"] == [s["params"]["fair_field"] for s in fairs.values()]


def test_the_leads_never_start_inside_the_averaging_window():
    pipeline = _doc()["pipeline"]
    window_min = pipeline["fair_rms"]["params"]["averaging_window_s"] / 60.0
    assert min(pipeline["decisions"]["params"]["leads_minutes"]) >= window_min


def test_the_kill_test_holds_a_segment_out_and_the_two_never_overlap():
    segments = _doc()["pipeline"]["kill_test"]["params"]["segments"]
    assert set(segments) == {"development", "heldout"}
    assert segments["development"]["end"] == segments["heldout"]["start"], "one cut: no row in both, none in neither"


def test_the_published_table_registration_matches_what_the_document_writes():
    config = _load("source-features-15m.json")
    check_config(LocalTablesConnector(), config)
    write = _doc()["pipeline"]["write"]["params"]
    assert os.path.dirname(write["path"]) == config["path"] == FEATURES_DIR
    assert os.path.basename(write["path"]) == "decision_features.jsonl"
    assert config["streams"] == ["decision_features"] and config["formats"] == ["jsonl"]
    assert config["effective_field"] == "decision_ms" and config["effective_unit"] == "ms"
    assert config["layout"] == "file" and config["notes"].strip()


def test_the_runbook_registers_and_publishes_the_feature_table():
    text = open(os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md"),
                encoding="utf-8").read()
    for needle in (FEATURES_DOC, "source-features-15m.json", "--connector localtables"):
        assert needle in text, f"the runbook never mentions {needle}"
