"""configs/run-hold-vs-redeploy.json end to end on a synthetic positions snapshot.

The onboarding root is built the way the owner will build it — the CLI verbs,
from the child root — with ``localfiles`` standing in for ``schwab_positions``
(the observations kind reads the root, never the connector). The numbers are
worked by hand from the document's own rates.
"""

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.document import load_document

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-vs-redeploy.json")

ROWS = [
    {"account": "a" * 16, "symbol": "VTI", "asset_type": "EQUITY", "as_of": "2026-10-27",
     "long_quantity": 10.0, "short_quantity": 0.0, "average_price": 200.0,
     "cost_basis": 2000.0, "market_value": 2500.0},
    {"account": "a" * 16, "symbol": "AAPL", "asset_type": "EQUITY", "as_of": "2026-10-27",
     "long_quantity": 3.0, "short_quantity": 0.0, "average_price": 150.0,
     "cost_basis": 450.0, "market_value": 600.0},
]


# Twelve weekly net returns with mean 0.0055 and sd 0.0087; among five trials the
# expected max null Sharpe is 0.3978 (pinned in dskit's test_active_hurdle).
ACTIVE = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.01, 0.012, -0.002, 0.008, 0.003]
EMAX_5 = 0.39777384449675074


def _cli(cwd, *args):
    subprocess.run([sys.executable, "-m", "dskit.onboarding", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def positions_root(tmp_path):
    """An onboarding root under ``tmp_path/ob`` holding one positions snapshot."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "positions.jsonl", "w") as fh:
        for row in ROWS:
            fh.write(json.dumps(row) + "\n")
    active = tmp_path / "active"
    active.mkdir()
    with open(active / "returns.jsonl", "w") as fh:
        for index, value in enumerate(ACTIVE):
            fh.write(json.dumps({"period": f"2026-W{index + 1:02d}", "net_return": value,
                                 "day": f"2026-{(index // 4) + 1:02d}-{(index % 4) * 7 + 1:02d}"}) + "\n")
    (tmp_path / "decisions").mkdir()
    _cli(tmp_path, "init", "--root", "./ob")
    _cli(tmp_path, "register-source", "schwab", "--root", "./ob",
         "--catalog-source", "schwab-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(data), "effective_field": "as_of"}),
         "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "schwab",
         "--stream", "positions", "--mode", "backfill")
    _cli(tmp_path, "register-source", "active", "--root", "./ob",
         "--catalog-source", "active-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(active), "effective_field": "day"}),
         "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "active",
         "--stream", "returns", "--mode", "backfill")
    return tmp_path


def test_document_is_valid_and_wires_the_four_stages():
    document = load_document(RUN_DOC)
    assert list(document.pipeline) == [
        "positions", "tax", "market", "betas", "active_returns", "hurdle", "decide", "decisions"]
    assert document.hash


def test_hold_vs_redeploy_end_to_end(positions_root, monkeypatch):
    monkeypatch.chdir(positions_root)
    document = load_document(RUN_DOC)
    document = replace(document, outputs=OutputsConfig(run_root=str(positions_root / "runs")))
    result = run_document(document, asof="2026-10-27", journal=False)
    assert result.state == "ran", (result.state, result.error)
    rows = {row["symbol"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}
    vti = rows["VTI"]
    # default_holding long at 0.238: tax 500 x .238 = 119, net 2381
    assert vti["liq_holding"] == "long"
    assert vti["liq_tax"] == pytest.approx(119.0)
    assert vti["liq_net"] == pytest.approx(2381.0)
    # market: .013 + .02 + .025 + ((27/36)^(1/10) - 1) = 0.058 - 0.028366 = 0.029634
    market = 0.058 + (27.0 / 36.0) ** 0.1 - 1.0
    assert result.outputs["market"]["rate"] == pytest.approx(market)
    rates = result.outputs["betas"]["rates"]
    assert rates["VTI"] == pytest.approx(0.04 + 1.0 * (market - 0.04))
    assert rates["AAPL"] == pytest.approx(0.04 + 1.2 * (market - 0.04))
    # hurdle: (0.0055 - 0.3978 x 0.0087) x 52 = 0.1060, admissible (dsr 0.76 >= 0.5)
    mean = sum(ACTIVE) / len(ACTIVE)
    sd = (sum((x - mean) ** 2 for x in ACTIVE) / (len(ACTIVE) - 1)) ** 0.5
    hurdle = (mean - EMAX_5 * sd) * 52
    assert result.outputs["hurdle"]["rate"] == pytest.approx(hurdle)
    assert result.outputs["hurdle"]["admissible"] is True
    # hold 2500 x (1+VTI)^5 taxed on the gain over 2000; redeploy 2381 x (1+hurdle-.02)^5
    # taxed on the gain over 2381; both at .238
    hold_value = 2500.0 * (1.0 + rates["VTI"]) ** 5
    redeploy_value = 2381.0 * (1.0 + hurdle - 0.02) ** 5
    assert vti["hold_value"] == pytest.approx(hold_value)
    assert vti["hold_net"] == pytest.approx(hold_value - max(hold_value - 2000.0, 0.0) * 0.238)
    assert vti["redeploy_value"] == pytest.approx(redeploy_value)
    assert vti["redeploy_net"] == pytest.approx(redeploy_value - (redeploy_value - 2381.0) * 0.238)
    assert vti["decision"] == "exit"
    summary = result.outputs["decide"]["summary"]
    assert (summary["hold_return_source"], summary["redeploy_return_source"]) == ("wired", "wired")
    assert summary["decisions"] == {"hold": 0, "exit": 2}
    written = result.outputs["decisions"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(positions_root / "decisions")
    with open(written) as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["run_id"] == os.path.basename(result.run_dir) for line in lines)
