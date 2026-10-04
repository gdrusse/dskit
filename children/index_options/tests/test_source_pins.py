"""Every shipped source config that reads a list from a file pins that file's hash."""

import glob
import hashlib
import json
import os


from dskit.onboarding.connector import check_config, resolve_connector

#: source config -> (connector kind, file knob, hash knob)
FILE_KNOBS = (("entities_file", "entities_sha256"), ("symbols_file", "symbols_sha256"))
CONNECTORS = {"entities_file": "httpblobs",
              "symbols_file": "dskit.onboarding.libs.alpaca:AlpacaOptionFetchConnector"}


def _pinned_configs(child_root):
    found = []
    for path in sorted(glob.glob(os.path.join(str(child_root), "configs", "source-*.json"))):
        doc = json.load(open(path, encoding="utf-8"))
        for file_knob, digest_knob in FILE_KNOBS:
            if file_knob in doc:
                found.append((os.path.basename(path), doc, file_knob, digest_knob))
    return found


def test_the_pinned_source_configs_are_found(child_root):
    names = {n for n, *_ in _pinned_configs(child_root)}
    assert {"source-stock-daily.json", "source-stock-daily-300.json",
            "source-benchmark-daily.json", "source-option-universe-300.json"} <= names


def test_every_file_list_is_pinned_and_passes_the_offline_check(child_root, monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_OPTIONS_ROOT", str(child_root))
    monkeypatch.setenv("STOCK_DAILY_RAW_ROOT", str(tmp_path))
    monkeypatch.setenv("STOCK_DAILY_300_RAW_ROOT", str(tmp_path))
    for name, doc, file_knob, digest_knob in _pinned_configs(child_root):
        path = os.path.expandvars(doc[file_knob])
        assert os.path.isabs(path), name
        with open(path, "rb") as handle:
            assert doc[digest_knob] == hashlib.sha256(handle.read()).hexdigest(), name
        connector = resolve_connector(CONNECTORS[file_knob])()
        if file_knob == "entities_file":
            check_config(connector, doc)
        else:
            assert connector.resolve_knobs(doc), name


def test_features_300_overlay_agrees_with_the_universe(child_root):
    overlay = json.load(open(os.path.join(str(child_root), "configs", "features-300.json")))
    tickers = json.load(open(os.path.join(str(child_root), "configs", "option_universe_300.json")))["tickers"]
    assert overlay["tickers"] == tickers
    assert overlay["bars"]["relpath"] == {t: f"{t.lower()}/underlying_prices.parquet" for t in tickers}


def test_features_300_trades_keep_list_is_the_universe(child_root):
    overlay = json.load(open(os.path.join(str(child_root), "configs", "features-300.json")))
    assert overlay["options"]["keep_values"] == {"symbol": overlay["tickers"]}
    assert overlay["targets"]["trades"] != overlay["targets"]["daily"]
