"""Paper-only live wrapper reads the shipped documents."""

import os

import pytest

from dskit.onboarding import AssetError
from dskit.pipeline.document import load_document

from intraday_equities.auth import authorize
from intraday_equities.live import intents, paper_intent
from intraday_equities.testing import StubSchwabBars

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DOC = os.path.join(CHILD_ROOT, "configs", "run-train.json")
SOURCE = os.path.join(CHILD_ROOT, "configs", "source-schwab-live.json")


def test_paper_intent_is_a_limit_order():
    intent = paper_intent({"symbol": "AAPL", "asof_ms": 1}, 2)
    assert intent["venue"] == "paper"
    assert intent["type"] == "limit"
    assert intent["qty"] == 2


def test_real_money_is_refused():
    with pytest.raises(AssetError, match="real-money"):
        paper_intent({"symbol": "AAPL", "asof_ms": 1}, 1, paper=False)
    with pytest.raises(AssetError, match="real-money"):
        intents(RUN_DOC, [], source_config=SOURCE, paper=False)


def test_intents_read_the_train_document_tradable():
    document = load_document(RUN_DOC)
    tradable = document.pipeline["select"].params["tradable"]
    records = [
        {"symbol": symbol, "asof_ms": 1_700_000_000_000 + i * 60_000,
         "close": 100.0 + i}
        for i in range(40)
        for symbol in tradable
    ]
    rows = intents(RUN_DOC, records, source_config=SOURCE, quantity=1)
    assert rows
    assert rows[0]["symbol"] in tradable
    assert rows[0]["venue"] == "paper"


def test_auth_prints_a_url_through_the_stub(monkeypatch):
    from intraday_equities import auth as auth_mod

    monkeypatch.setattr(auth_mod, "SchwabBars", StubSchwabBars)
    url = authorize(SOURCE)
    assert url.startswith("https://")
