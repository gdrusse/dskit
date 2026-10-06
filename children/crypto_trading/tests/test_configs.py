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
from dskit.onboarding.libs.httpblobs import HttpBlobsConnector
from dskit.onboarding.libs.kalshi import KalshiConnector
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

KALSHI_SOURCES = ("source-kalshi-crypto.json", "source-kalshi-crypto-candles.json")
KALSHI_SERIES = ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")
KALSHI_15M = ("KXBTC15M", "KXETH15M")
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
    assert found == {"source-sample.json", *KALSHI_SOURCES, *BINANCE_SOURCES}, (
        "a new source config must be added to this file's tables, not just to configs/")
    suites = {os.path.basename(p) for p in glob.glob(_path("suite-*.json"))}
    assert suites == set(SUITES)


@pytest.mark.parametrize("name", KALSHI_SOURCES)
def test_kalshi_sources_pass_default_deny_and_resolve_their_knobs(name):
    config = _load(name)
    connector = KalshiConnector()
    check_config(connector, config)
    knobs = connector.resolve_knobs(_strip_platform(config))
    assert knobs["statuses"] == ["settled"], (
        "history is settled markets; the live recorder reads open books by itself")


def test_the_candle_source_asks_for_one_minute_candles():
    knobs = KalshiConnector().resolve_knobs(_load("source-kalshi-crypto-candles.json"))
    assert knobs["period_interval"] == 1, "1-minute candles are the modelling resolution"


def test_kalshi_series_universe_and_the_candle_subset():
    markets = _load("source-kalshi-crypto.json")["series"]
    candles = _load("source-kalshi-crypto-candles.json")["series"]
    assert sorted(markets) == sorted(KALSHI_SERIES)
    assert sorted(candles) == sorted(KALSHI_15M), (
        "hourly ladders carry ~190 strikes an event: per-market candles would be ~1.2M "
        "requests (see the runbook and the PROPOSED kalshi ADR); they await the trades stream")


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
                 "throttle_s", "timeout", "max_retries", "max_refused", "max_bytes", "stream",
                 "relpath_template", "raw_relpath_template"):
        assert len({json.dumps(c[knob], sort_keys=True) for c in configs}) == 1, knob
    with open(_path(DATES_FILE), "rb") as handle:
        assert configs[0]["entities_sha256"] == hashlib.sha256(handle.read()).hexdigest()


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
