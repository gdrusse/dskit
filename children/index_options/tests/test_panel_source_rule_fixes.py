"""No-fit contracts for the ADR-0330 panel-source document.

``configs/panel-source-rule-fixes.json`` is ``strike-screen-panel-source.json`` with exactly two
``raw_chain`` keys added (``proxy_quote_fields`` and ``proxy_estimator``), ``data.min_dte`` added
(ADR-0330 Amendment 1) and its two ``notes`` rewritten.
These tests pin that duplication; nothing here opens a store, reads market data or fits.
"""
import copy
import json
from pathlib import Path

from index_options.cdf_study import RawChainFeatureBuilder

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
BASE, NEW = "strike-screen-panel-source.json", "panel-source-rule-fixes.json"
FIELDS = ("bid_size", "ask_size", "implied_volatility")
ESTIMATOR = "dskit.pipeline.libs.predictive_cdf:InnerBlockOptionPriceCDF"
OPTIONS = ("proxy_quote_fields", "proxy_estimator")


def _json(name):
    return json.loads((CONFIGS / name).read_text())


def _without(document, options=(), floor=False):
    """Return a copy of ``document`` without its two ``notes``, the named ``raw_chain`` keys and,
    when ``floor``, ``data.min_dte``."""
    document = copy.deepcopy(document)
    document.pop("notes")
    document["data"].pop("notes")
    for name in options:
        document["data"]["raw_chain"].pop(name)
    if floor:
        document["data"].pop("min_dte")
    return document


def test_the_document_is_the_base_plus_the_two_options_and_the_min_dte_floor():
    assert _without(_json(NEW), OPTIONS, floor=True) == _without(_json(BASE))


def test_the_min_dte_floor_is_the_owner_ruling_of_eight_days_and_the_base_has_none():
    assert _json(NEW)["data"]["min_dte"] == 8
    assert "min_dte" not in _json(BASE)["data"]
    assert "min_dte" in _json(NEW)["data"]["notes"] and "min_dte" in _json(NEW)["notes"]


def test_the_data_notes_extend_the_base_notes():
    assert _json(NEW)["data"]["notes"].startswith(_json(BASE)["data"]["notes"])


def test_the_raw_chain_block_builds_with_both_options_on():
    builder = RawChainFeatureBuilder(**_json(NEW)["data"]["raw_chain"])
    assert builder.proxy_quote_fields == FIELDS
    assert builder.proxy_estimator == ESTIMATOR
    assert builder.proxy_diagnostics is False


def test_the_base_document_keeps_the_class_defaults():
    builder = RawChainFeatureBuilder(**_json(BASE)["data"]["raw_chain"])
    assert builder.proxy_quote_fields == RawChainFeatureBuilder.QUOTE_FIELDS
    assert builder.proxy_estimator == RawChainFeatureBuilder.PROXY_ESTIMATOR
    assert builder.proxy_diagnostics is False


def test_the_option_names_live_only_under_raw_chain():
    document = _json(NEW)
    document["data"]["raw_chain"] = {key: value for key, value in document["data"]["raw_chain"].items()
                                     if key not in OPTIONS}
    text = json.dumps(_without(document))
    assert not any(name in text for name in OPTIONS)
