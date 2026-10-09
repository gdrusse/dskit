"""The Stage-0 source and suite configs validate against dskit's packs and agree on their series.

Every source here is a registration input for an EXISTING dskit connector pack (``kalshi``,
``kalshi_history``, ``cboe``): this child adds no connector. The four index series appear in
five sources and one suite, so the agreement is pinned here (CLAUDE.md: a value that must appear
twice is pinned). The suite restates the list on purpose; this test is what keeps it equal.
"""

import json
import os

import pytest

from dskit.onboarding import check_config, load_suite
from dskit.onboarding.libs.cboe import CboeConnector
from dskit.onboarding.libs.kalshi import KalshiConnector
from dskit.onboarding.libs.kalshi_history import KalshiHistoryConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

#: Independent restatement of the study's series (a test must not read its expectation from its subject).
INDEX_SERIES = ["KXINX", "KXINXU", "KXNASDAQ100", "KXNASDAQ100U"]

#: Each source config and the dskit pack it registers against.
SOURCES = {
    "source-kalshi-index.json": KalshiConnector,
    "source-kalshi-index-books.json": KalshiConnector,
    "source-kalshi-history-index.json": KalshiHistoryConnector,
    "source-kalshi-history-candles-index.json": KalshiHistoryConnector,
    "source-kalshi-history-trades-index.json": KalshiHistoryConnector,
    "source-cboe-0dte.json": CboeConnector,
}

SUITE = "suite-kalshi-history-index-markets.json"


def _load(name):
    """Parse one config file."""
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_each_source_validates_against_its_pack(name):
    # Default-deny against the pack's spec(), offline. connector.check() is not called: these packs
    # probe the live venue there, and a config test must not depend on the network.
    check_config(SOURCES[name](), _load(name))


@pytest.mark.parametrize("name", sorted(n for n, cls in SOURCES.items() if cls is not CboeConnector))
def test_every_kalshi_source_names_exactly_the_index_series(name):
    assert _load(name)["series"] == INDEX_SERIES, f"{name} drifted from the study's series"


def test_the_suite_validates_and_restates_the_same_series():
    suite = load_suite(os.path.join(CONFIGS, SUITE))
    rules = {r["id"]: r for r in _load(SUITE)["rules"]}
    assert [r.id for r in suite.rules] == list(rules), "the parsed suite lost or reordered a rule"
    assert rules["markets-series-universe"]["kwargs"]["values"] == INDEX_SERIES
    present = rules["markets-every-series-present"]["kwargs"]
    assert present["min"] == present["max"] == len(INDEX_SERIES)


def test_the_hedge_recorder_keeps_only_pm_settled_roots_within_a_day():
    config = _load("source-cboe-0dte.json")
    assert config["symbols"] == ["SPX", "NDX"]
    assert config["roots"] == ["SPXW", "NDXP"], "AM-settled SPX/NDX roots settle on a different print"
    assert config["max_dte"] == 1
