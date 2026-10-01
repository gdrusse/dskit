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
              "events": {"splits": {"1711123200": {"date": 1711123200}}}}
    path = tmp_path/"chart.json"
    path.write_text(json.dumps({"chart": {"result": [result]}}))
    config = {"files": {"prices": {"path": str(path),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
              "archive_observed_at": "2024-03-23T12:00:00Z",
              "session_timezone": "UTC", "complete_through": "2024-03-21"}
    reader = YahooChartArchiveConnector()
    rows = [m["data"] for m in reader.read(config, ["prices"], {}, "backfill")
            if m["type"] == "RECORD"]
    assert rows[0]["close"] == 100 and rows[0]["adjusted_close"] == 90
    assert rows[0]["post_session_split"] is True and rows[1]["post_session_split"] is False
    assert rows[0]["close_complete"] is True and rows[1]["close_complete"] is False
    path.write_text("{}")
    with pytest.raises(AssetError, match="sha256 mismatch"):
        list(reader.read(config, ["prices"], {}, "backfill"))
