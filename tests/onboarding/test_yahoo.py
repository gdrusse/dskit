"""Saved Yahoo history units, completion and pin checks."""
import json
import hashlib

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding.libs.yahoo import YahooChartArchiveConnector


def test_chart_keeps_adjusted_close_separate_and_marks_split_units(tmp_path):
    result = {"meta": {"symbol": "XYZ"}, "timestamp": [1711036800, 1711123200],
              "indicators": {"quote": [{"open": [99, 109], "high": [101, 111],
                                        "low": [98, 108], "close": [100, 110],
                                        "volume": [10, 20]}],
                             "adjclose": [{"adjclose": [90, 99]}]},
              "events": {"splits": {"1711123200": {"date": 1711123200, "numerator": 2, "denominator": 1}}}}
    path = tmp_path/"chart.json"
    path.write_text(json.dumps({"chart": {"result": [result]}}))
    config = {"files": {"prices": {"path": str(path),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
              "archive_observed_at": "2024-03-23T12:00:00Z",
              "session_timezone": "UTC", "complete_through": "2024-03-21",
              "corporate_actions_complete": True}
    reader = YahooChartArchiveConnector()
    rows = [m["data"] for m in reader.read(config, ["prices"], {}, "backfill")
            if m["type"] == "RECORD"]
    assert rows[0]["close"] == 100 and rows[0]["adjusted_close"] == 90
    assert rows[0]["post_session_split"] is True and rows[1]["post_session_split"] is False
    assert rows[0]["close_complete"] is True and rows[1]["close_complete"] is False
    path.write_text("{}")
    with pytest.raises(AssetError, match="sha256 mismatch"):
        list(reader.read(config, ["prices"], {}, "backfill"))


def test_chart_unknown_corporate_actions_never_certifies_units(tmp_path):
    result = {"meta": {"symbol": "XYZ"}, "timestamp": [1711036800],
              "indicators": {"quote": [{"open": [99], "high": [101], "low": [98],
                                        "close": [100], "volume": [10]}],
                             "adjclose": [{"adjclose": [90]}]}}
    path = tmp_path/"chart.json"
    path.write_text(json.dumps({"chart": {"result": [result]}}))
    config = {"files": {"prices": {"path": str(path),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
              "archive_observed_at": "2024-03-23T12:00:00Z",
              "session_timezone": "UTC", "complete_through": "2024-03-21",
              "corporate_actions_complete": True}
    rows = [m["data"] for m in YahooChartArchiveConnector().read(config, ["prices"], {}, "backfill")
            if m["type"] == "RECORD"]
    assert rows[0]["post_session_split"] is None
    assert rows[0]["unit_history_verified"] is False

@pytest.mark.parametrize("events,present", [(None, False), ({}, False),
    ({"splits": None}, False), ({"splits": {}}, True)])
def test_inventory_unknown_and_explicit_empty_are_distinct(events, present):
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    inventory = YahooSplitInventory({"events": events}, "UTC")
    assert inventory.present is present
    assert inventory.ratios == {}
    if present:
        inventory.require_bar_dates([])
    else:
        with pytest.raises(AssetError, match="unknown"):
            inventory.require_bar_dates([])


@pytest.mark.parametrize("numerator,denominator", [
    (None, 1), (0, 1), (-1, 1), (True, 1), ("2", 1), (float("nan"), 1),
    (float("inf"), 1), (1, 0), (1, -1), (1, False), (1, float("inf")),
    (1e308, 1e-308), (1e-308, 1e308),
])
def test_inventory_invalid_factor_family_refuses(numerator, denominator):
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    event = {"date": 1711123200, "numerator": numerator, "denominator": denominator}
    with pytest.raises(AssetError, match="split inventory"):
        YahooSplitInventory({"events": {"splits": {"one": event}}}, "UTC")


@pytest.mark.parametrize("stamp", [None, True, "1711123200", 1711123200.5, 10**100])
def test_inventory_invalid_effective_timestamp_refuses(stamp):
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    with pytest.raises(AssetError, match="split inventory"):
        YahooSplitInventory({"events": {"splits": {"one": {
            "date": stamp, "numerator": 2, "denominator": 1}}}}, "UTC")


@pytest.mark.parametrize("events", [[], 7, {"splits": []}, {"splits": 3},
                                      {"splits": {"bad": None}}])
def test_inventory_malformed_containers_refuse(events):
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    with pytest.raises(AssetError, match="split inventory"):
        YahooSplitInventory({"events": events}, "UTC")


def test_inventory_distinct_events_on_same_session_refuse():
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    # UTC dates differ; both are March 21 in America/New_York.
    events = {"a": {"date": 1711069200, "numerator": 2, "denominator": 1},
              "b": {"date": 1711072800, "numerator": 1, "denominator": 2}}
    with pytest.raises(AssetError, match="duplicate effective date"):
        YahooSplitInventory({"events": {"splits": events}}, "America/New_York")


def test_inventory_preserves_repeated_reverse_and_irregular_factors():
    from dskit.onboarding.libs.yahoo import YahooSplitInventory
    factors = [(2, 1), (1, 4), (1.319, 1), (2, 1)]
    events = {str(i): {"date": 1711036800 + i*86400, "numerator": n, "denominator": d}
              for i, (n, d) in enumerate(factors)}
    inventory = YahooSplitInventory({"events": {"splits": events}}, "UTC")
    assert list(inventory.ratios.values()) == [2, .25, 1.319, 2]
    inventory.require_bar_dates(list(inventory.ratios))
    with pytest.raises(AssetError, match="exactly one"):
        inventory.require_bar_dates(list(inventory.ratios)[:-1])
    with pytest.raises(AssetError, match="exactly one"):
        inventory.require_bar_dates([*inventory.ratios, next(iter(inventory.ratios))])


@pytest.mark.parametrize("declared", [False, True])
def test_archive_invalid_inventory_refuses_before_yield_even_if_not_verified(declared):
    reader = YahooChartArchiveConnector()
    reader._archive_config = {"complete_through": "2024-12-31",
        "session_timezone": "UTC", "corporate_actions_complete": declared}
    doc = {"meta": {"symbol": "XYZ"}, "timestamp": [1711036800],
        "indicators": {"quote": [{"open": [1], "high": [1], "low": [1],
            "close": [1], "volume": [1]}], "adjclose": [{"adjclose": [1]}]},
        "events": {"splits": {"event": {"date": 1711036800}}}}
    rows = reader.normalize("prices", json.dumps({"chart": {"result": [doc]}}).encode())
    with pytest.raises(AssetError, match="split inventory"):
        next(rows)
