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
from dskit.onboarding.libs import kalshi, kalshi_history
from dskit.onboarding.libs.httpblobs import RECORD_FIELDS, HttpBlobsConnector
from dskit.onboarding.libs.kalshi import KalshiConnector
from dskit.onboarding.libs.kalshi_history import KalshiHistoryConnector
from dskit.onboarding.libs.localtables import LocalTablesConnector
from dskit.onboarding.libs.restwindow import RestWindowConnector
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
    "suite-kalshi-history-markets.json": kalshi_history.MARKET_FIELDS,
    "suite-kalshi-history-candles.json": kalshi_history.CANDLE_FIELDS,
    "suite-kalshi-history-trades.json": kalshi_history.TRADE_FIELDS,
    # restwindow writes the declared row_fields plus the ISO effective instant it derives from the epoch field
    "suite-coinbase-candles.json": ("time", "low", "high", "open", "close", "volume", "time_iso"),
    "suite-deribit-dvol.json": ("ts", "open", "high", "low", "close", "ts_iso"),
}
#: source file -> (vendor URL template, the transform class: dskit's zip-to-parquet, the layout is in transform_params)
BINANCE_SOURCES = {
    "source-binance-btcusdt-1m.json": (
        "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{entity}.zip",
        "dskit.onboarding.libs.zipcsv:ZipCsvToParquet"),
    "source-binance-ethusdt-1m.json": (
        "https://data.binance.vision/data/spot/daily/klines/ETHUSDT/1m/ETHUSDT-1m-{entity}.zip",
        "dskit.onboarding.libs.zipcsv:ZipCsvToParquet"),
    "source-binance-btcbvol.json": (
        "https://data.binance.vision/data/option/daily/BVOLIndex/BTCBVOLUSDT/"
        "BTCBVOLUSDT-BVOLIndex-{entity}.zip",
        "dskit.onboarding.libs.zipcsv:ZipCsvToParquet"),
    "source-binance-ethbvol.json": (
        "https://data.binance.vision/data/option/daily/BVOLIndex/ETHBVOLUSDT/"
        "ETHBVOLUSDT-BVOLIndex-{entity}.zip",
        "dskit.onboarding.libs.zipcsv:ZipCsvToParquet"),
}
DATES_FILE = "binance_vision_dates.json"
#: registration configs for tables a run of this child derives and a later run reads (localtables)
DERIVED_SOURCES = ("source-features-15m.json", "source-features-hourly.json")
#: kalshi_history sources (ADR-0239): markets with the realised settlement value, hourly event-level candles, trades
HISTORY_SOURCES = (
    "source-kalshi-history-crypto.json",
    "source-kalshi-history-candles-hourly-btc.json",
    "source-kalshi-history-candles-hourly-eth.json",
    "source-kalshi-history-trades-15m.json",
    "source-kalshi-history-trades-hourly.json",
)
#: restwindow sources (ADR-0240): the live-safe spot candles and the Deribit implied-vol index
RESTWINDOW_SOURCES = (
    "source-coinbase-btcusd-1m.json",
    "source-coinbase-ethusd-1m.json",
    "source-deribit-btc-dvol.json",
    "source-deribit-eth-dvol.json",
)
#: suite file -> the one stream it targets (a snapshot holds one stream, so a
#: suite naming two would fail the absent one's row_count).
SUITES = {
    "suite-sample.json": "samples",
    "suite-kalshi-crypto-markets.json": "markets",
    "suite-kalshi-crypto-candles.json": "candles",
    "suite-kalshi-crypto-fees.json": "fee_schedules",
    "suite-kalshi-crypto-books.json": "orderbooks",
    "suite-binance-files.json": "files",
    "suite-kalshi-history-markets.json": "markets",
    "suite-kalshi-history-candles.json": "candles",
    "suite-kalshi-history-trades.json": "trades",
    "suite-coinbase-candles.json": "candles",
    "suite-deribit-dvol.json": "dvol",
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
    assert found == {"source-sample.json", *KALSHI_SOURCES, *BINANCE_SOURCES, *DERIVED_SOURCES, *HISTORY_SOURCES,
                     *RESTWINDOW_SOURCES}, (
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


HISTORY_SERIES = {
    "source-kalshi-history-crypto.json": KALSHI_SERIES,
    "source-kalshi-history-candles-hourly-btc.json": ("KXBTC", "KXBTCD"),
    "source-kalshi-history-candles-hourly-eth.json": ("KXETH", "KXETHD"),
    "source-kalshi-history-trades-15m.json": KALSHI_15M,
    "source-kalshi-history-trades-hourly.json": KALSHI_HOURLY,
}


@pytest.mark.parametrize("name", HISTORY_SOURCES)
def test_history_sources_pass_default_deny_and_resolve_their_knobs(name):
    config = _load(name)
    connector = KalshiHistoryConnector()
    check_config(connector, config)
    knobs = connector.resolve_knobs(_strip_platform(config))
    assert knobs["series"] == list(HISTORY_SERIES[name])
    if name == "source-kalshi-history-crypto.json":
        assert knobs["statuses"] == ["settled"], "the markets stream is the final rows of both archives"
    else:
        assert knobs["statuses"] == ["settled", "closed", "open"], (
            "the pack default, restated: 'closed' keeps the candle and trade cursors from skipping a market for good")
    if "candles" in name:
        assert knobs["period_interval"] == 1 and knobs["candle_grouping"] == "event"


def test_the_history_sources_split_the_universe_the_way_their_notes_say():
    series = {name: sorted(_load(name)["series"]) for name in HISTORY_SOURCES}
    assert series["source-kalshi-history-crypto.json"] == sorted(_load("source-kalshi-crypto.json")["series"]), (
        "the history markets are the six series the kalshi source pulls")
    candles = series["source-kalshi-history-candles-hourly-btc.json"] + series["source-kalshi-history-candles-hourly-eth.json"]
    assert sorted(candles) == sorted(KALSHI_HOURLY), "the two candle sources partition the four hourly series"
    trades = series["source-kalshi-history-trades-15m.json"] + series["source-kalshi-history-trades-hourly.json"]
    assert sorted(trades) == sorted(KALSHI_SERIES), "the two trade sources partition the six series"
    hourly_doc = _load("run-features-hourly.json")["pipeline"]["spot"]["params"]["assets"]
    assert series["source-kalshi-history-candles-hourly-btc.json"] == [s for s in hourly_doc["BTC"]["series"] if not s.endswith("15M")]
    assert series["source-kalshi-history-candles-hourly-eth.json"] == [s for s in hourly_doc["ETH"]["series"] if not s.endswith("15M")]


@pytest.mark.parametrize("name", HISTORY_SOURCES)
def test_history_sources_say_how_to_register_and_what_the_label_gate_is(name):
    notes = _load(name)["notes"]
    assert "--connector dskit.onboarding.libs.kalshi_history:KalshiHistoryConnector" in notes
    assert f"--config @configs/{name}" in notes and name[len("source-"):-len(".json")] in notes
    if name == "source-kalshi-history-crypto.json":
        for phrase in ("expiration_value", "settlement_ts", "KNOWN ONLY AT SETTLEMENT", "never on the row's effective date"):
            assert phrase in notes, f"{name}: the notes must say {phrase!r}"
    if "candles-hourly" in name or name.endswith("trades-hourly.json"):
        assert "COUNT the archived hourly markets" in notes, "the per-market archive cost is stated, not hidden"


@pytest.mark.parametrize("name", RESTWINDOW_SOURCES)
def test_restwindow_sources_pass_default_deny_and_are_registered_by_import_path(name):
    config = _load(name)
    check_config(RestWindowConnector(), config)
    assert "--connector dskit.onboarding.libs.restwindow:RestWindowConnector" in config["notes"]
    assert f"--config @configs/{name}" in config["notes"]
    (declaration,) = config["streams"].values()
    assert declaration["effective_field"] in ("time_iso", "ts_iso")
    assert declaration["pagination"]["lag"] == 60, "the minute still forming is never stored"


def test_the_restwindow_source_and_suite_fields_agree():
    for source, suite in (("source-coinbase-btcusd-1m.json", "suite-coinbase-candles.json"),
                          ("source-coinbase-ethusd-1m.json", "suite-coinbase-candles.json"),
                          ("source-deribit-btc-dvol.json", "suite-deribit-dvol.json"),
                          ("source-deribit-eth-dvol.json", "suite-deribit-dvol.json")):
        (declaration,) = _load(source)["streams"].values()
        assert (*declaration["row_fields"], declaration["effective_field"]) == tuple(SUITE_FIELDS[suite]), source
        assert declaration["primary_key"] == [declaration["row_fields"][0]]


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
                 "relpath_template", "raw_relpath_template", "transform"):
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


@pytest.mark.parametrize("name", ["suite-kalshi-crypto-markets.json", "suite-kalshi-crypto-fees.json",
                                  "suite-kalshi-history-markets.json"])
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
                                  "suite-kalshi-crypto-books.json", "suite-kalshi-history-markets.json"])
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
FEATURES_DIR = "~/data/crypto_trading/features-15m"  # the write node and localtables expand ~; the readers do not
RUNBOOK_TEXT = open(os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md"),
                    encoding="utf-8").read()
#: the store root the runbook asks the owner to set; the stage B document carries the same absolute value
STORE_ROOT = re.search(r"^export OB=(\S+)", RUNBOOK_TEXT, re.M).group(1)


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


def test_every_store_root_in_the_document_is_the_runbooks_absolute_placeholder():
    """ObservationRows and payload_files do not expand `~`, so a `~` root fails on a fresh clone."""
    roots = [spec["params"]["root"] for spec in _doc()["pipeline"].values() if "root" in spec.get("params", {})]
    assert set(roots) == {STORE_ROOT}, "one store, named once per node: they must agree"
    assert os.path.isabs(STORE_ROOT) and not STORE_ROOT.startswith("~")
    text = open(_path(FEATURES_DOC), encoding="utf-8").read()
    assert text.count(STORE_ROOT) == len(roots), "the placeholder appears only as a root, so one sed catches all"


def test_the_runbook_rewrites_every_root_with_one_command(tmp_path):
    import shutil
    import subprocess

    (line,) = re.findall(r'^(sed -i "s#[^#\n]+#\$OB#g" configs/run-features-15m\.json)$', RUNBOOK_TEXT, re.M)
    assert f's#{STORE_ROOT}#' in line, "the command rewrites the placeholder the document carries"
    (tmp_path / "configs").mkdir()
    shutil.copy(_path(FEATURES_DOC), tmp_path / "configs" / FEATURES_DOC)
    done = subprocess.run(["bash", "-c", line], cwd=tmp_path, env={**os.environ, "OB": "/data/elsewhere/ob"},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    rewritten = json.loads((tmp_path / "configs" / FEATURES_DOC).read_text(encoding="utf-8"))
    roots = {s["params"]["root"] for s in rewritten["pipeline"].values() if "root" in s.get("params", {})}
    assert roots == {"/data/elsewhere/ob"}, "every node moved together, none was left on the placeholder"


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
    from dskit.pipeline.binary_pricing import PAYOFFS

    rules = {r.id: r for r in load_suite(_path("suite-kalshi-crypto-markets.json")).rules}
    params = _doc()["pipeline"]["markets"]["params"]
    geometries = set(rules["markets-strike-type-set"].kwargs["values"])
    assert set(params["payoff_by_strike_type"]) == geometries, "every geometry the suite lets through has a payoff"
    assert set(params["payoff_by_strike_type"].values()) <= set(PAYOFFS)
    assert set(params["result_labels"]) == set(rules["markets-result-vocabulary"].kwargs["values"])
    assert set(params["result_labels"].values()) == {0, 1}
    assert "finalized" in params["settled_statuses"], "the venue spells a settled payload 'finalized'"


def test_the_fee_types_the_node_prices_cover_what_the_fee_suite_accepts_and_each_names_a_real_mechanic():
    from dskit.pipeline.fee_mechanics import FEE_MECHANICS, ROUNDING_POLICIES, fee_spec_problems

    (rule,) = [r for r in load_suite(_path("suite-kalshi-crypto-fees.json")).rules
               if r.kwargs.get("field") == "fee_type"]
    types = _doc()["pipeline"]["fees"]["params"]["fee_types"]
    assert set(rule.kwargs["values"]) <= set(types), "every fee type the suite lets through has a mapping"
    for fee_type, entry in types.items():
        assert entry["model"]["mechanic"] in FEE_MECHANICS and entry["model"]["rounding"]["policy"] in ROUNDING_POLICIES
        assert fee_spec_problems(entry["model"]) == [], fee_type


def _written_columns(source_file):
    """The parquet columns a source's zip layout writes (dropped columns omitted), read from its transform_params."""
    return {c["output"] for c in _load(source_file)["transform_params"]["columns"] if c["output"]}


def test_the_bar_and_column_vocabulary_matches_the_binance_files_stage_a_writes():
    KLINE_COLUMNS = _written_columns("source-binance-btcusdt-1m.json")
    BVOL_COLUMNS = _written_columns("source-binance-btcbvol.json")
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
    from crypto_trading.spot_features import tape_name

    pipeline = _doc()["pipeline"]
    streams = pipeline["streams"]["params"]["streams"]
    expected = {}
    for asset, spec in pipeline["spot"]["params"]["assets"].items():
        expected[tape_name(asset, "klines")] = spec["klines"]
        expected[tape_name(asset, "bvol")] = spec["bvol"]
    assert streams == expected


def test_every_fair_value_column_reads_a_volatility_column_that_exists():
    from dskit.pipeline.libs.vol_estimators import build_estimators

    spot = _doc()["pipeline"]["spot"]["params"]
    columns = {spot["vol_column_prefix"] + e.label() for e in build_estimators(spot["estimators"])} | {"bvol_per_sqrt_s"}
    fairs = _nodes(":BinaryFairValue")
    assert fairs, "the document prices at least one fair value"
    for key, spec in fairs.items():
        assert spec["params"]["vol_field"] in columns, f"{key} reads a column no node writes"
        assert spec["params"]["averaging_window_s"] == 60, "the BRTI averages the last 60 seconds"
    kill = _doc()["pipeline"]["kill_test"]["params"]
    assert kill["model_fields"] == [s["params"]["fair_field"] for s in fairs.values()]


def test_the_leads_never_start_inside_the_averaging_window():
    pipeline = _doc()["pipeline"]
    window_min = pipeline["fair_rms"]["params"]["averaging_window_s"] / 60.0
    assert min(pipeline["decisions"]["params"]["leads_minutes"]) >= window_min


CUT_MS = int(datetime.datetime(2026, 9, 15, tzinfo=datetime.timezone.utc).timestamp()) * 1000


def test_the_kill_test_holds_a_segment_out_and_the_two_never_overlap():
    segments = _doc()["pipeline"]["kill_test"]["params"]["segments"]
    assert set(segments) == {"development", "heldout"}
    assert segments["development"]["end_ms"] == segments["heldout"]["start_ms"], "one cut: no row in both, none in neither"
    assert set(segments["development"]) == {"end_ms"} and set(segments["heldout"]) == {"start_ms"}
    assert segments["heldout"]["start_ms"] == CUT_MS, "the cut is 2026-09-15T00:00:00Z, restated here in epoch ms"


def test_the_published_table_registration_matches_what_the_document_writes():
    config = _load("source-features-15m.json")
    check_config(LocalTablesConnector(), config)
    node = _doc()["pipeline"]["write"]
    write = node["params"]
    assert node["uses"] == "dskit.pipeline.kinds_run_write:RecordsWriteRun"
    assert os.path.dirname(write["path"]) == config["path"] == FEATURES_DIR
    assert os.path.basename(write["path"]) == "decision_features-{run}.jsonl", "one file, one stream, per run"
    assert write["path"].count("{run}") == 1
    assert "streams" not in config, "a fixed stream list would hide every run's table but the first"
    assert config["formats"] == ["jsonl"]
    assert config["effective_field"] == "decision_ms" and config["effective_unit"] == "ms"
    assert config["layout"] == "file" and config["notes"].strip()


def test_the_registration_and_the_runbook_never_publish_under_one_fixed_stream_name():
    """A fixed stream name serves the OLD rows after a change (localtables' cursor); name the hazard, never the habit."""
    config = _load("source-features-15m.json")
    for text in (config["notes"], RUNBOOK_TEXT):
        assert "--stream decision_features " not in text and "--stream decision_features\n" not in text
    assert "OLD rows" in config["notes"]


def test_the_runbook_registers_and_publishes_the_feature_table():
    text = open(os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md"),
                encoding="utf-8").read()
    for needle in (FEATURES_DOC, "source-features-15m.json", "--connector localtables"):
        assert needle in text, f"the runbook never mentions {needle}"


# -- stage B review round: the basis anchors, the segments, the fee base, the runbook ------

KALSHI_FEE_BASE = {"quadratic": 0.07}  # Kalshi's published taker coefficient, restated on purpose
MARKET_MINUTES = 15  # a 15-minute up/down market is open for 15 minutes


def test_the_fee_base_rate_is_pinned_at_the_config_level_not_only_in_a_unit_test():
    types = _doc()["pipeline"]["fees"]["params"]["fee_types"]
    assert {t: e["base_rate"] for t, e in types.items()} == KALSHI_FEE_BASE
    assert "0.07" in _doc()["pipeline"]["fees"]["notes"]


def test_kalshis_quadratic_fee_is_priced_by_dskits_probability_quadratic_mechanic_rounded_up_to_the_cent():
    """The Kalshi mapping lives in the document, restated here: which mechanic, which grid."""
    model = _doc()["pipeline"]["fees"]["params"]["fee_types"]["quadratic"]["model"]
    assert model == {"mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}


def test_the_fee_suite_warns_when_the_schedule_is_not_the_one_the_base_rate_assumes():
    rules = {r.id: r for r in load_suite(_path("suite-kalshi-crypto-fees.json")).rules}
    assert rules["fees-type-is-quadratic"].severity == "warn"
    rule = rules["fees-multiplier-is-one"]
    assert (rule.rule, rule.severity, rule.kwargs["field"]) == ("in_range", "warn", "fee_multiplier")
    assert rule.kwargs["min"] == rule.kwargs["max"] == 1, "the 0.07 base is Kalshi's standard rate: multiplier 1"


def test_the_strike_lag_covers_exactly_the_series_whose_strike_is_the_previous_window_average():
    pipeline = _doc()["pipeline"]
    lags = pipeline["markets"]["params"]["strike_known_lag_s"]
    assert sorted(lags) == sorted(KALSHI_15M)
    assert all(v >= 0 for v in lags.values()), "a publication lag is a wait, never negative"
    assert sorted(pipeline["anchors"]["params"]["anchor_series"]) == sorted(KALSHI_15M), (
        "the anchors are the up/down series' strikes; a fixed-strike series has none")


def test_the_leads_leave_the_strike_known_before_every_decision():
    pipeline = _doc()["pipeline"]
    lag_minutes = max(pipeline["markets"]["params"]["strike_known_lag_s"].values()) / 60.0
    assert max(pipeline["decisions"]["params"]["leads_minutes"]) < MARKET_MINUTES - lag_minutes


def test_the_basis_window_is_the_bar_the_settlement_average_covers():
    pipeline = _doc()["pipeline"]
    spot = pipeline["spot"]["params"]
    window_s = {n["params"]["averaging_window_s"] for n in _nodes(":BinaryFairValue").values()}
    assert window_s == {60} and spot["bar_ms"] == 60 * 1000, (
        "the anchor is a 60-second average and is compared with ONE 1-minute bar")
    assert spot["max_basis_age_ms"] >= (MARKET_MINUTES * 60 + max(
        pipeline["markets"]["params"]["strike_known_lag_s"].values())) * 1000, (
        "an anchor must stay usable until the next 15-minute market's strike replaces it")


def test_the_unit_ranges_admit_what_real_days_showed_and_refuse_a_strike_in_other_units():
    """A-R1-01: the basis ran 0.9989 to 1.0008 and |ln(K/spot_brti)| up to 0.555 on real days; 1e-4 times the index is a slip."""
    for name, doc in (("15m", _doc()), ("hourly", _hourly())):
        spot = doc["pipeline"]["spot"]["params"]
        low, high = spot["basis_range"]
        assert 0.5 < low < 0.9989 and 1.0008 < high < 1.5, name
        assert not low <= 1e-4 <= high, "the five April 13 strikes (the index over 10,000) are refused"
        assert 0.56 < spot["max_abs_log_moneyness"] <= 2.0, name
        notes = doc["pipeline"]["spot"]["notes"]
        assert "basis_range" in notes and "max_abs_log_moneyness" in notes and "7.243729" in notes, name


def test_every_fair_value_prices_the_index_unit_spot_not_the_raw_binance_price():
    for key, spec in _nodes(":BinaryFairValue").items():
        assert spec["params"]["spot_field"] == "spot_brti", key
    assert _doc()["pipeline"]["spot"]["inputs"]["anchors"] == "$anchors.records"


def test_the_shipped_document_reports_development_only_and_cuts_on_the_close():
    kill = _doc()["pipeline"]["kill_test"]["params"]
    assert kill["report_segments"] == ["development"], "the held-out read is a deliberate, recorded edit"
    assert set(kill["report_segments"]) <= set(kill["segments"])


def test_the_stream_pair_check_has_one_home_and_it_is_dskits():
    from dskit.pipeline.libs import parquet_series

    from crypto_trading import spot_features

    assert spot_features.stream_problems is parquet_series.stream_problems
    assert parquet_series.stream_problems("streams['x']", {"source": "s"}), "a pair needs both keys"
    assert parquet_series.stream_problems("streams['x']", {"source": "s", "stream": "files"}) == []


def test_the_instant_parser_is_dskits_and_refuses_a_naive_stamp():
    from dskit.production.base import parse_utc_ms

    from crypto_trading.clock import instant_ms

    assert instant_ms("2026-09-02T00:15:00Z") == parse_utc_ms("2026-09-02T00:15:00Z")
    assert instant_ms("2026-09-02T00:15:00") is None, "a stamp with no zone is a guess"
    assert instant_ms("") is None and instant_ms(None) is None


RUNBOOK = os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md")


def _pull_function():
    text = open(RUNBOOK, encoding="utf-8").read()
    start = text.index("pull() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run_pull(tmp_path, acquire_answer):
    """Run the runbook's pull() under bash with a stubbed `python -m dskit.onboarding`; return the call log."""
    import subprocess

    calls = tmp_path / "calls.log"
    script = f"""
python() {{
  if [ "$1" = "-m" ]; then
    echo "$*" >> {calls}
    [ "$3" = acquire ] && echo '{acquire_answer}'
    return 0
  fi
  command python "$@"
}}
OB=/ob
{_pull_function()}
pull kalshi-crypto fee_schedules suite-kalshi-crypto-fees.json
"""
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout, calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_the_runbook_pull_helper_validates_a_new_snapshot(tmp_path):
    out, calls = _run_pull(tmp_path, '{"snapshot": "20261006T000000Z-backfill-abc"}')
    assert "validate" in calls and "--snapshot 20261006T000000Z-backfill-abc" in calls


def test_the_runbook_pull_helper_skips_validate_after_a_no_op_pull(tmp_path):
    out, calls = _run_pull(tmp_path, '{"snapshot": null}')
    assert "acquire" in calls and "validate" not in calls, "a null snapshot has nothing to validate"
    assert "nothing to validate" in out


# -- lens-2 review: values the mutation sweep showed nothing pinned -------------------------


def test_the_execution_lag_is_positive_and_leaves_the_averaging_window_after_it():
    pipeline = _doc()["pipeline"]
    lag = pipeline["decisions"]["params"]["exec_lag_s"]
    assert lag > 0, "a fill cannot precede the information it acts on; zero lag would re-open the stale-quote bias"
    window = pipeline["fair_rms"]["params"]["averaging_window_s"]
    assert min(pipeline["decisions"]["params"]["leads_minutes"]) * 60 - lag >= window


def test_the_block_clusters_are_whole_days_because_volatility_regimes_persist():
    kill = _doc()["pipeline"]["kill_test"]["params"]
    assert kill["cluster_block_s"] == 86_400, "per-market clustering understates the error about twofold"
    assert kill["cluster_field"] == "event_ticker", "the per-event error is kept for comparison"


def test_bvol_is_published_in_percent_so_the_scale_is_one_hundredth():
    # confirmed on a real day in the 2026-10-06 review: BTC BVOL reads near 50
    assert _doc()["pipeline"]["spot"]["params"]["bvol_scale"] == 0.01


def test_bvol_is_annualised_on_a_365_day_year():
    # the convention is UNVERIFIED (runbook B2); pinned so changing it is a decision, not a drift
    assert _doc()["pipeline"]["spot"]["params"]["seconds_per_year"] == 365 * 24 * 3600


def test_each_fair_value_reads_the_volatility_its_name_promises():
    nodes = _nodes(":BinaryFairValue")
    assert {k: (v["params"]["fair_field"], v["params"]["vol_field"]) for k, v in nodes.items()} == {
        "fair_rms": ("fair_rms", "rv_rms_60"), "fair_ewma": ("fair_ewma", "rv_ewma_30"),
        "fair_bvol": ("fair_bvol", "bvol_per_sqrt_s")}


def test_the_margin_is_the_one_decided_before_the_held_out_read():
    assert _doc()["pipeline"]["kill_test"]["params"]["margin"] == 0.02, (
        "a margin edit is a tuning decision: it must be deliberate and recorded, so it is pinned")


def test_the_strike_lag_is_at_least_twice_the_measured_publication_delay():
    # the review measured the strike appearing about 7 s after the open
    assert min(_doc()["pipeline"]["markets"]["params"]["strike_known_lag_s"].values()) >= 14


def test_the_age_caps_stop_stale_readings_from_being_carried_forward():
    pipeline = _doc()["pipeline"]
    spot, state = pipeline["spot"]["params"], pipeline["state"]["params"]
    assert 0 < spot["max_spot_age_ms"] <= 2 * spot["bar_ms"], "a spot older than two bars is stale"
    assert 0 < spot["max_bvol_age_ms"] <= 60_000, "BVOL ticks about once a second"
    assert 0 < state["max_candle_age_ms"] <= 2 * 60_000, "a candle older than two minutes is stale"
    assert spot["max_basis_age_ms"] <= 30 * 60_000, "a basis older than two strikes' spacing is a different regime"


def test_the_settled_statuses_are_the_venues_spelling():
    assert _doc()["pipeline"]["markets"]["params"]["settled_statuses"] == ["finalized", "settled"]


# -- the dskit nodes the document names: every column name they take is a name this child writes ------


def test_every_fair_value_node_reads_the_columns_the_child_writes_and_the_lag_the_decisions_use():
    """BinaryFairValue takes every column as a param and prices the horizon from the fill itself: pin the agreement."""
    from crypto_trading import fields as f

    pipeline = _doc()["pipeline"]
    lag = pipeline["decisions"]["params"]["exec_lag_s"]
    for key, spec in _nodes(":BinaryFairValue").items():
        p = spec["params"]
        assert (p["payoff_field"], p["lower_field"], p["upper_field"]) == (f.PAYOFF, f.FLOOR, f.CAP), key
        assert (p["decision_field"], p["settle_field"]) == (f.DECISION_MS, f.CLOSE_MS), key
        assert p["exec_lag_s"] == lag, f"{key}: the horizon runs from the fill, so the lag is the decisions' lag"


def test_the_scorer_reads_the_columns_the_child_writes():
    from crypto_trading import fields as f

    kill = _doc()["pipeline"]["kill_test"]["params"]
    assert (kill["market_field"], kill["label_field"], kill["settle_field"]) == (f.MID, f.LABEL, f.CLOSE_MS)
    assert (kill["bid_field"], kill["ask_field"]) == (f.YES_BID, f.YES_ASK)
    assert (kill["fee_yes_field"], kill["fee_no_field"]) == (f.FEE_BUY_YES, f.FEE_BUY_NO)
    assert kill["eligible_field"] == f.TWO_SIDED, "the scorer's gate is the quote's own two-sided flag"
    assert kill["cluster_field"] == f.EVENT
    assert kill["bucket_edges"][0] == 0.0 and kill["bucket_edges"][-1] == 1.0, "mids are probabilities"


# -- stage B, the hourly document: built from the same nodes, so every shared knob is pinned equal ------------------

HOURLY_DOC = "run-features-hourly.json"
HOURLY_DIR = "~/data/crypto_trading/features-hourly"


def _hourly():
    return _load(HOURLY_DOC)


def test_the_hourly_document_loads_and_every_node_says_why():
    document = load_document(_path(HOURLY_DOC))
    assert document.hash and document.hash != load_document(_path(FEATURES_DOC)).hash
    raw = _hourly()
    assert raw["notes"].strip()
    for key, spec in raw["pipeline"].items():
        assert spec.get("notes", "").strip(), f"node {key!r} has no notes: say why it is wired this way"


def test_every_store_root_of_the_hourly_document_is_the_runbooks_placeholder_and_one_sed_moves_them_all(tmp_path):
    import shutil
    import subprocess

    roots = [spec["params"]["root"] for spec in _hourly()["pipeline"].values() if "root" in spec.get("params", {})]
    assert set(roots) == {STORE_ROOT} and len(roots) == 7, "one store, named once per reading node"
    text = open(_path(HOURLY_DOC), encoding="utf-8").read()
    assert text.count(STORE_ROOT) == len(roots), "the placeholder appears only as a root, so one sed catches all"
    (line,) = re.findall(r'^(sed -i "s#[^#\n]+#\$OB#g" configs/run-features-hourly\.json)$', RUNBOOK_TEXT, re.M)
    assert f's#{STORE_ROOT}#' in line
    (tmp_path / "configs").mkdir()
    shutil.copy(_path(HOURLY_DOC), tmp_path / "configs" / HOURLY_DOC)
    done = subprocess.run(["bash", "-c", line], cwd=tmp_path, env={**os.environ, "OB": "/data/elsewhere/ob"},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    rewritten = json.loads((tmp_path / "configs" / HOURLY_DOC).read_text(encoding="utf-8"))
    assert {s["params"]["root"] for s in rewritten["pipeline"].values() if "root" in s.get("params", {})} == {"/data/elsewhere/ob"}


def test_the_hourly_readers_name_sources_that_exist_and_the_series_each_pulls():
    sources = {os.path.basename(p)[len("source-"):-len(".json")] for p in glob.glob(_path("source-*.json"))}
    pipeline = _hourly()["pipeline"]
    assert pipeline["markets"]["params"]["source"] == pipeline["anchor_markets"]["params"]["source"] == "kalshi-history-crypto"
    assert pipeline["fee_schedules"]["params"]["source"] == "kalshi-crypto", "fee schedules are series-level: the kalshi source's stream"
    assert pipeline["candles_btc"]["params"]["source"] == "kalshi-history-candles-hourly-btc"
    assert pipeline["candles_eth"]["params"]["source"] == "kalshi-history-candles-hourly-eth"
    for key in ("markets", "anchor_markets", "fee_schedules", "candles_btc", "candles_eth"):
        assert pipeline[key]["params"]["source"] in sources, key
    for tape in pipeline["streams"]["params"]["streams"].values():
        assert tape["source"] in sources and tape["stream"] == "files"
    assert sorted(pipeline["markets"]["params"]["series"]) == sorted(KALSHI_HOURLY)
    assert sorted(pipeline["anchor_markets"]["params"]["series"]) == sorted(KALSHI_15M)
    assert sorted(pipeline["anchors"]["params"]["anchor_series"]) == sorted(KALSHI_15M)
    assert pipeline["anchors"]["inputs"] == {"records": "$anchor_markets.records"}
    assert pipeline["decisions"]["inputs"] == {"records": "$markets.records"}, "only the hourly markets are decisions"


def test_the_hourly_assets_claim_their_hourly_and_their_anchor_series_and_nothing_else():
    assets = _hourly()["pipeline"]["spot"]["params"]["assets"]
    assert {a: sorted(v["series"]) for a, v in assets.items()} == {
        "BTC": ["KXBTC", "KXBTC15M", "KXBTCD"], "ETH": ["KXETH", "KXETH15M", "KXETHD"]}
    assert sorted(s for a in assets.values() for s in a["series"]) == sorted(KALSHI_SERIES)
    for asset, spec in assets.items():
        shipped15 = _doc()["pipeline"]["spot"]["params"]["assets"][asset]
        assert (spec["klines"], spec["bvol"]) == (shipped15["klines"], shipped15["bvol"]), "the same Binance tapes"


def test_the_hourly_document_shares_every_modelling_knob_with_the_15_minute_one():
    fifteen, hourly = _doc()["pipeline"], _hourly()["pipeline"]
    for key in ("streams", "state", "fair_rms", "fair_ewma", "fair_bvol", "fees", "kill_test", "fee_schedules"):
        a, b = fifteen[key], hourly[key]
        pa, pb = dict(a["params"]), dict(b["params"])
        if key == "kill_test":  # the one difference by design: the hourly rows carry the settlement instant (see the next test)
            assert (pa.pop("settle_field"), pb.pop("settle_field")) == ("close_ms", "settlement_ms")
        assert a["uses"] == b["uses"] and pa == pb and a.get("inputs") == b.get("inputs"), key
    spot15, spot_h = fifteen["spot"]["params"], hourly["spot"]["params"]
    assert {k: v for k, v in spot15.items() if k != "assets"} == {k: v for k, v in spot_h.items() if k != "assets"}
    assert fifteen["decisions"]["params"]["exec_lag_s"] == hourly["decisions"]["params"]["exec_lag_s"]
    for key in ("payoff_by_strike_type", "result_labels", "settled_statuses"):
        assert fifteen["markets"]["params"][key] == hourly["markets"][ "params"][key] == hourly["anchor_markets"]["params"][key], key
    assert hourly["anchor_markets"]["params"]["strike_known_lag_s"] == fifteen["markets"]["params"]["strike_known_lag_s"]
    assert hourly["markets"]["params"]["strike_known_lag_s"] == {}, "an hourly strike is fixed at creation: known at the open"


def test_each_document_cuts_on_the_settlement_instant_its_rows_carry_and_the_cut_is_on_the_contract_grids():
    """The hourly rows carry the settlement instant and the scorer cuts on it; the 15-minute rows cannot, so they cut on the close."""
    from crypto_trading import fields as f

    assert _hourly()["pipeline"]["kill_test"]["params"]["settle_field"] == f.SETTLEMENT_MS
    assert _doc()["pipeline"]["kill_test"]["params"]["settle_field"] == f.CLOSE_MS
    for params in (_doc()["pipeline"]["kill_test"]["params"], _hourly()["pipeline"]["kill_test"]["params"]):
        (cut,) = {params["segments"]["development"]["end_ms"], params["segments"]["heldout"]["start_ms"]}
        for grid_s in (900, 3600):  # 15-minute closes, and the hourly series' (the 25 h and weekly ladders close on the hour too)
            assert cut % (grid_s * 1000) == 0, f"the cut {cut} is not on the {grid_s} s grid: a market could straddle it"


def test_the_hourly_leads_fit_the_hour_and_leave_the_averaging_window_after_the_fill():
    pipeline = _hourly()["pipeline"]
    leads, lag = pipeline["decisions"]["params"]["leads_minutes"], pipeline["decisions"]["params"]["exec_lag_s"]
    assert max(leads) < 60, "an hourly market is open for the hour before its close: a longer lead is before the open"
    assert min(leads) * 60 - lag >= pipeline["fair_rms"]["params"]["averaging_window_s"]
    assert leads != _doc()["pipeline"]["decisions"]["params"]["leads_minutes"], "the hourly leads are their own research choice"


def test_one_held_out_cut_serves_both_documents_and_the_hourly_one_reports_development_only():
    kills = [d["pipeline"]["kill_test"]["params"] for d in (_doc(), _hourly())]
    assert kills[0]["segments"] == kills[1]["segments"], "one cut across both: neither read can inform the other's"
    assert kills[1]["report_segments"] == ["development"]


def test_the_hourly_table_registration_matches_what_the_document_writes():
    config = _load("source-features-hourly.json")
    check_config(LocalTablesConnector(), config)
    node = _hourly()["pipeline"]["write"]
    write = node["params"]
    assert node["uses"] == "dskit.pipeline.kinds_run_write:RecordsWriteRun"
    assert os.path.dirname(write["path"]) == config["path"] == HOURLY_DIR != FEATURES_DIR
    assert os.path.basename(write["path"]) == "decision_features-{run}.jsonl" and write["path"].count("{run}") == 1
    assert "streams" not in config and config["formats"] == ["jsonl"]
    assert config["effective_field"] == "decision_ms" and config["effective_unit"] == "ms" and config["layout"] == "file"
    assert "features-hourly" in config["notes"] and "run-features-hourly.json" in config["notes"]


def test_no_note_still_says_the_label_is_yes_no_only_or_waits_on_a_proposed_adr():
    """The history pack landed: the realised value is pulled, and the hourly candles and the Coinbase/Deribit sources exist."""
    stale = ("waits on ADR-0239", "wait on PROPOSED ADR-0239", "PROPOSED kalshi /historical", "LABEL GAP",
             "the realised settlement value waits", "need ADR-0240", "PROPOSED ADR-0239", "PROPOSED in ADR-0239")
    texts = {name: open(_path(name), encoding="utf-8").read() for name in os.listdir(CONFIGS) if name.endswith(".json")}
    for name, text in texts.items():
        for phrase in stale:
            assert phrase not in text, f"{name} still says {phrase!r}"
