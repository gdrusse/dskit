"""No-fit contracts for the ADR-0270 market reference stage documents; nothing here opens a store or fits."""
import json
import re
from pathlib import Path

import pytest

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.planner import plan

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
REQUESTS = "run-strike-screen-market-requests.json"
QUOTES = "run-strike-screen-exact-expiry-quotes.json"
PILOT = Path("/home/russell/data/index_options/theta-strike-screen-20261018")
PILOT_COPIES = ("strike-screen-market-proxy", "strike-screen-market-reference-scores",
                "strike-screen-paired-forecasts", "strike-screen-score-pockets")
CHAIN = "dskit.pipeline.libs.chain_parquet:ParquetChainRows"
STREAM = "dskit.pipeline.libs.parquet:ParquetStreamRows"
HOLDOUT, SEALED, START = "2021-01-01", "2026-01-01", "2019-01-01"
ZERO = "0" * 64
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read(name):
    return json.loads((CONFIGS / name).read_text())


def _pilot_copies():
    paths = [PILOT / "documents" / "market" / f"{name}.json" for name in PILOT_COPIES]
    if not all(path.exists() for path in paths):
        pytest.skip("pilot copies live under the machine-local pilot root")
    return {path.stem: json.loads(path.read_text()) for path in paths}


def _m24():
    return _read(REQUESTS)["pipeline"]["panel"]["params"]["sources"][0]["where"][0]["value"]


def _dates(obj, path=""):
    """Yield (path, value) for every ISO date string in a document, notes excluded."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key != "notes":
                yield from _dates(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _dates(value, f"{path}[{index}]")
    elif isinstance(obj, str) and ISO.match(obj):
        yield path, obj


def test_both_documents_plan_without_opening_stores():
    assert set(plan(PipelineDocument.from_obj(_read(REQUESTS))).order) == {
        "panel", "panel_basis", "chain", "chain_rows", "chain_priced", "spot_days", "spot_table",
        "listed_days", "listed_table", "with_spot", "with_listing", "census", "eligible", "rates",
        "rate_by_date", "with_rate", "requests", "write"}
    assert list(plan(PipelineDocument.from_obj(_read(QUOTES))).order) == [
        "chain", "rows", "usable", "requested", "matched", "quotes", "write"]


def test_m24_is_the_registered_cohort_in_both_documents():
    m24 = _m24()
    assert len(m24) == len(set(m24)) == 24 and m24 == sorted(m24)
    assert _read(REQUESTS)["pipeline"]["chain"]["params"]["symbols"] == m24
    assert _read(QUOTES)["pipeline"]["chain"]["params"]["symbols"] == m24
    cohorts = PILOT / "registration" / "cohorts.json"
    if not cohorts.exists():
        pytest.skip("the registration lives under the machine-local pilot root")
    assert m24 == json.loads(cohorts.read_text())["m24"]


def test_readers_are_bounded_and_sealed():
    for doc in (_read(REQUESTS), _read(QUOTES)):
        for key, node in doc["pipeline"].items():
            params = node.get("params", {})
            if node["uses"] == CHAIN:
                assert (params["start"], params["end_before"], params["sealed_from"]) == (START, HOLDOUT, SEALED), key
            if node["uses"] == STREAM:
                for source in params["sources"]:
                    assert all(bound["end_before"] <= HOLDOUT for bound in source["read_bounds"].values()), key
    sources = {k: n["params"]["sources"][0] for k, n in _read(REQUESTS)["pipeline"].items() if n["uses"] == STREAM}
    assert sources["panel"]["read_bounds"] == {"quote_date": {"start": START, "end_before": HOLDOUT},
                                               "settlement_date": {"end_before": HOLDOUT}}
    assert sources["rates"]["read_bounds"] == {"observation_date": {"start": START, "end_before": HOLDOUT}}


def test_no_bound_reaches_the_holdout_except_the_sealed_marker():
    docs = {REQUESTS: _read(REQUESTS), QUOTES: _read(QUOTES)}
    if (PILOT / "documents" / "market").exists():
        docs.update(_pilot_copies())
    for name, doc in docs.items():
        for path, value in _dates(doc):
            if path.endswith(".sealed_from"):
                assert value == SEALED, (name, path)
            elif path.endswith(".end_before"):
                assert value <= HOLDOUT, (name, path, value)
            else:
                assert value < HOLDOUT, (name, path, value)


def test_requests_carry_the_proxy_request_contract():
    pipe = _read(REQUESTS)["pipeline"]
    for key in ("panel", "rates"):
        pin = pipe[key]["params"]["sources"][0]["snapshot"]
        assert len(pin) == 64 and pin != ZERO, key
    fields = pipe["requests"]["params"]["fields"]
    assert fields["decision_strike_z"] == {"const": 0.0}
    assert fields["available_at"] == fields["decision_at"] == {"field": "quote_date"}
    assert fields["clock"] == {"text": "after_date_close_indicative_not_executable"}
    rate, dte = fields["growth"]["args"][0]["args"]
    assert fields["growth"]["op"] == "exp"
    assert rate == {"op": "div", "args": [{"field": "dgs3mo"}, {"const": 100}]}
    assert dte == {"op": "div", "args": [{"field": "actual_calendar_dte"}, {"const": 365}]}
    assert pipe["with_rate"]["params"] == {"key": "quote_date", "how": "inner"}
    assert pipe["with_listing"]["params"]["identity"] == ["symbol", "quote_date", "settlement_date"]
    assert pipe["write"]["params"]["path"].startswith(str(PILOT) + "/")


def test_quotes_are_restricted_to_request_identities_in_the_proxy_schema():
    pipe = _read(QUOTES)["pipeline"]
    assert pipe["chain"]["params"]["fields"] == ["symbol", "date", "expiration", "strike", "right", "bid", "ask"]
    requested = pipe["requested"]["params"]
    assert requested["key"] == requested["tables"]["requests"]["key_fields"] == [
        "symbol", "quote_date", "settlement_date"]
    assert requested["tables"]["requests"]["source"] == "theta-market-requests"
    assert pipe["matched"]["params"]["where"] == [{"field": "expiry", "op": "!=", "value": None}]
    keep = pipe["quotes"]["params"]["keep"]
    assert {"symbol", "quote_date", "expiry", "strike", "type", "bid", "ask", "available_at"} <= set(keep)
    assert pipe["quotes"]["params"]["fields"] == {"decision_strike_z": {"const": 0.0}}
    assert pipe["write"]["params"]["path"].startswith(str(PILOT) + "/")


def test_pilot_copies_point_at_the_pilot_store_with_zero_pins():
    for name, doc in _pilot_copies().items():
        assert doc["outputs"]["run_root"].startswith(str(PILOT) + "/"), name
        for node in doc["pipeline"].values():
            for source in node.get("params", {}).get("sources", []):
                assert source["root"] == str(PILOT / "ob") and source["snapshot"] == ZERO, name
    copies = _pilot_copies()
    assert copies["strike-screen-market-proxy"]["pipeline"]["proxy"]["params"]["source_sha256"] == ZERO
    paired = copies["strike-screen-paired-forecasts"]["pipeline"]["paired"]["params"]
    assert (paired["components"], paired["decision_strike"]) == (3, "decision_strike_z")
    refs = copies["strike-screen-market-reference-scores"]["pipeline"]["refs"]["params"]
    assert (refs["cutoff"], refs["end_before"]) == ("2020-01-01", HOLDOUT)
    assert refs["claim"].startswith(str(PILOT) + "/")
    assert refs["transports"] == {"market_raw": 0, "market_calibrated": 21}


def _reference_rows(groups, date, end):
    row = {"expiry": end, "settlement_date": end, "terminal_return": 0.01, "decision_strike_z": 0.0,
           "market_eligible": 1, "actual_calendar_dte": 31, "reference_scale": 0.1, "quote_date": date}
    row.update({f"proxy_q_{i}": -1.0 + i * 0.25 for i in range(9)})
    return [dict(row, symbol=group) for group in groups]


@pytest.mark.parametrize("budget,refusal", [(119, ValueError), (120, FileExistsError), (121, ValueError)])
def test_reference_budget_is_exactly_the_owner_inventory(tmp_path, budget, refusal):
    """24 groups x (estimator + empirical_tail [+ pit_map when knots > 0]) over market_raw 0 / market_calibrated 21.

    The node checks the budget against its inventory BEFORE the exclusive claim; a pre-existing claim file makes
    the exact budget stop at the claim (FileExistsError), so no fit can start, while any other budget refuses on
    the inventory.
    """
    from dskit.pipeline.libs.cdf_research import CDFReferenceScores

    params = json.loads((CONFIGS / "run-theta-market-reference-scores.json").read_text())["pipeline"]["refs"]["params"]
    claim = tmp_path / "claim.json"
    claim.write_text("{}")
    params.update(authorized_fit_attempts=budget, claim=str(claim))
    transports = params["transports"]
    assert len(_m24()) * sum(2 + (knots > 0) for knots in transports.values()) == 120
    node = CDFReferenceScores("refs", params)
    inputs = {"fit": _reference_rows(_m24(), "2019-06-03", "2019-07-05"),
              "score": _reference_rows(_m24(), "2020-06-01", "2020-07-02")}
    with pytest.raises(refusal) as caught:
        node.run(None, inputs)
    if refusal is ValueError:
        assert "budget does not equal inventory" in str(caught.value)
    assert claim.read_text() == "{}"
