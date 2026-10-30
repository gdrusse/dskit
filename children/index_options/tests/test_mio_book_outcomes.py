"""MioBookOutcomes: every saved MIO book with its realized or counterfactual settlement.

The pinned-payload owner (``PinnedPayloadSource``) is replaced by a fake that
serves files from a temporary directory and still enforces the declared
digest, so these tests exercise the node's own rules: book shaping, the
consistency refusals, the size match and the settlement rule.
"""

import copy
import hashlib
import json

import pandas as pd
import pytest

from index_options import mio_run_events
from index_options.mio_run_events import MioBookOutcomes

MULT, FEE = 100, 0.65


class _FakePinned:
    """Stand-in for PinnedPayloadSource over a temp dir; verifies digests."""

    base = None

    def __init__(self, pin):
        self.pin = pin

    def file(self, relative, *, verify=False):
        if relative not in self.pin["files"]:
            raise ValueError("undeclared source file")
        path = self.base / relative
        if verify and hashlib.sha256(path.read_bytes()).hexdigest() != self.pin["files"][relative]:
            raise ValueError("source payload drift")
        return str(path)


def _bars(tmp_path, closes):
    """Write one bars parquet per symbol; return the plan's bars pin block."""
    (tmp_path / "bars").mkdir(exist_ok=True)
    files, digests = {}, {}
    for symbol, rows in closes.items():
        rel = f"bars/{symbol}.parquet"
        pd.DataFrame({"symbol": symbol, "date": list(rows), "close": list(rows.values())}).to_parquet(
            tmp_path / rel, index=False)
        files[symbol] = rel
        digests[rel] = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    return {"files": files, "pin": {"root": "/r", "source": "s", "snapshot": "snap", "stream": "files",
                                    "manifest_sha256": "0" * 64, "files": digests}}


def _leg(right, sign, qty, strike, price, fill_price=None, selected=None):
    leg = {"right": right, "sign": sign, "quantity": qty, "strike": strike, "price": price}
    if fill_price is not None:
        leg["fill_price"] = fill_price
    if selected is not None:
        leg["selected_quantity"] = selected
    return leg


def _credit(legs, key="price", qty="quantity"):
    contracts = sum(leg[qty] for leg in legs)
    return sum(-leg["sign"] * leg[key] * leg[qty] * MULT for leg in legs) - contracts * FEE


def _table(tmp_path):
    """A two-method saved result over symbols X (factor 1) and Y (factor 10)."""
    bars = _bars(tmp_path, {
        "X": {"2021-01-04": 100.0, "2021-01-15": 90.0, "2021-02-19": 120.0, "2021-04-16": 1.0},
        "Y": {"2021-01-04": 50.0, "2021-01-15": 70.0},
    })
    # X filled at full size: short put 95 + long put 90 (bull put spread); X at 90 at expiry.
    legs1 = [_leg("put", -1, 1, 95.0, 3.0, 2.9), _leg("put", 1, 1, 90.0, 1.0, 1.1)]
    credit1 = _credit(legs1, "fill_price")
    pnl1 = credit1 + MULT * (-(95 - 90))
    trade1 = {"id": "2021-01-04:X:2021-01-15", "symbol": "X", "decision_date": "2021-01-04",
              "fill_date": "2021-01-05", "close_date": "2021-01-15", "expiry": "2021-01-15", "dte": 11,
              "contracts": 2, "credit": credit1, "decision_credit": _credit(legs1), "pnl": pnl1,
              "expected": 50.0, "factor": 1.0, "spot": 100.0, "method_fitted": True, "legs": legs1,
              "strategy": {"name": "bull_put_spread"}, "status": "filled"}
    # X filled at half size (resized 4 -> 2 per leg): long call 100, X at 120.
    legs2 = [_leg("call", 1, 2, 100.0, 5.0, 5.0, selected=4)]
    credit2 = _credit(legs2, "fill_price")
    trade2 = {"id": "2021-01-04:X:2021-02-19", "symbol": "X", "decision_date": "2021-01-04",
              "fill_date": "2021-01-05", "close_date": "2021-02-19", "expiry": "2021-02-19", "dte": 46,
              "contracts": 2, "credit": credit2,
              "decision_credit": _credit([_leg("call", 1, 4, 100.0, 5.0)]),
              "pnl": credit2 + MULT * 2 * 20.0, "expected": 400.0, "factor": 1.0, "spot": 100.0,
              "method_fitted": False, "legs": legs2, "strategy": {"name": "long_call"}, "status": "filled"}
    # Y refused: unadjusted spot 5.0 (bars 50 / factor 10); short call 6 at 0.4; Y at 7 at expiry.
    legs3 = [_leg("call", -1, 3, 6.0, 0.4)]
    refused1 = {"id": "2021-01-04:Y:2021-01-15", "symbol": "Y", "decision_date": "2021-01-04",
                "fill_date": "2021-01-05", "expiry": "2021-01-15", "dte": 11, "contracts": 3,
                "credit": _credit(legs3), "expected": 30.0, "factor": 10.0, "spot": 5.0,
                "method_fitted": False, "legs": legs3, "reason": "missing_fill_quote",
                "strategy": {"name": "short_call"}, "status": "unfilled"}
    # X refused after repricing: saved credit is at fill prices.
    legs4 = [_leg("put", 1, 1, 95.0, 2.0, 3.5)]
    refused2 = {"id": "2021-01-04:X:2021-01-15", "symbol": "X", "decision_date": "2021-01-04",
                "fill_date": "2021-01-05", "expiry": "2021-01-15", "dte": 11, "contracts": 1,
                "credit": _credit(legs4, "fill_price"), "expected": 80.0, "factor": 1.0, "spot": 100.0,
                "method_fitted": True, "legs": legs4, "reason": "repriced_shared_cap",
                "strategy": {"name": "long_put"}, "status": "unfilled"}
    return {
        "complete": True,
        "plan": {"source_pins": {"bars": bars},
                 "study": {"domain": {"params": {"bridge": {"solver": {
                     "multiplier": MULT, "fee_per_contract_usd": FEE}}}}}},
        "results": [
            {"id": "m1", "status": "completed",
             "result": {"trades": [trade1, trade2], "rejected": [refused1]}},
            {"id": "m2", "status": "completed", "result": {"trades": [], "rejected": [refused2]}},
        ],
    }


PARAMS = {"cohort": "c", "methods": ["m1", "m2"], "end_before": "2021-04-01", "tolerance": 0.005}


@pytest.fixture
def table(tmp_path, monkeypatch):
    _FakePinned.base = tmp_path
    monkeypatch.setattr(mio_run_events, "PinnedPayloadSource", _FakePinned)
    return _table(tmp_path)


def _run(table, **over):
    return MioBookOutcomes("b", {**PARAMS, **over}).run(None, {"table": table})


def _by(out):
    return {(r["method"], r["id"]): r for r in out["books"]}


class TestBooks:
    def test_every_book_once_with_status_and_key(self, table):
        rows = _by(_run(table))
        assert set(rows) == {("m1", "2021-01-04:X:2021-01-15"), ("m1", "2021-01-04:X:2021-02-19"),
                             ("m1", "2021-01-04:Y:2021-01-15"), ("m2", "2021-01-04:X:2021-01-15")}
        assert rows[("m1", "2021-01-04:Y:2021-01-15")]["status"] == "refused"
        assert rows[("m1", "2021-01-04:X:2021-01-15")]["status"] == "filled"
        assert all(r["cohort"] == "c" for r in rows.values())
        assert rows[("m2", "2021-01-04:X:2021-01-15")]["book_key"] == "m2|2021-01-04:X:2021-01-15"

    def test_filled_outcome_is_the_saved_pnl(self, table):
        row = _by(_run(table))[("m1", "2021-01-04:X:2021-01-15")]
        trade = table["results"][0]["result"]["trades"][0]
        assert row["outcome"] == trade["pnl"] == row["realized_pnl"]
        assert row["expected_matched"] == 50.0 and row["fill_fraction"] == 1.0
        assert row["win"] == 0 and row["reason"] is None and row["fitted"] is True

    def test_a_resized_fill_scales_expected_to_the_filled_size(self, table):
        row = _by(_run(table))[("m1", "2021-01-04:X:2021-02-19")]
        assert row["fill_fraction"] == 0.5
        assert row["contracts_selected"] == 4 and row["contracts_filled"] == 2
        assert row["expected"] == 400.0 and row["expected_matched"] == 200.0
        # decision basis is the book AS SELECTED (4 calls) at decision prices
        assert row["decision_basis_pnl"] == pytest.approx(_credit([_leg("call", 1, 4, 100.0, 5.0)]) + 4 * 2000.0)

    def test_refused_counterfactual_settles_unadjusted(self, table):
        row = _by(_run(table))[("m1", "2021-01-04:Y:2021-01-15")]
        assert row["settle_spot"] == pytest.approx(7.0)
        want = _credit([_leg("call", -1, 3, 6.0, 0.4)]) - 3 * MULT * 1.0
        assert row["outcome"] == pytest.approx(want) == row["decision_basis_pnl"]
        assert row["realized_pnl"] is None and row["expected_matched"] == 30.0
        assert "snap" in row["source_of_settlement"]

    def test_repriced_refusal_uses_decision_prices(self, table):
        row = _by(_run(table))[("m2", "2021-01-04:X:2021-01-15")]
        want = (-2.0 * MULT - FEE) + MULT * 5.0
        assert row["outcome"] == pytest.approx(want)
        assert row["reason"] == "repriced_shared_cap"

    def test_audit_counts_and_pins(self, table):
        audit = _run(table)["audit"]
        assert audit["books"] == {"filled": 2, "refused": 2}
        assert audit["max_filled_recompute_error"] == pytest.approx(0.0, abs=1e-9)
        assert set(audit["bars"]) == {"X", "Y"}
        assert audit["bars_max_date_read"] < "2021-04-01"
        assert audit["multiplier"] == MULT and audit["fee_per_contract_usd"] == FEE


class TestRefusals:
    def _mutate(self, table, fn):
        t = copy.deepcopy(table)
        fn(t)
        return t

    def test_a_saved_pnl_the_bars_do_not_reproduce(self, table):
        t = self._mutate(table, lambda t: t["results"][0]["result"]["trades"][0].update(pnl=1.0))
        with pytest.raises(ValueError, match="pnl"):
            _run(t)

    def test_a_refused_credit_that_does_not_add_up(self, table):
        t = self._mutate(table, lambda t: t["results"][0]["result"]["rejected"][0].update(credit=1.0))
        with pytest.raises(ValueError, match="credit"):
            _run(t)

    def test_a_spot_off_the_basis(self, table):
        t = self._mutate(table, lambda t: t["results"][0]["result"]["rejected"][0].update(spot=50.0))
        with pytest.raises(ValueError, match="spot"):
            _run(t)

    def test_two_factors_for_one_symbol(self, table):
        t = self._mutate(table, lambda t: t["results"][1]["result"]["rejected"][0].update(factor=2.0))
        with pytest.raises(ValueError, match="factor"):
            _run(t)

    def test_a_book_reaching_past_end_before(self, table):
        def late(t):
            t["results"][0]["result"]["rejected"][0].update(expiry="2021-04-16")
        with pytest.raises(ValueError, match="end_before"):
            _run(self._mutate(table, late))

    def test_method_set_must_match(self, table):
        with pytest.raises(ValueError, match="methods"):
            _run(table, methods=["m1"])

    def test_an_incomplete_result(self, table):
        with pytest.raises(ValueError, match="complete"):
            _run(self._mutate(table, lambda t: t.update(complete=False)))

    def test_byte_drift_in_the_bars(self, table, tmp_path):
        (tmp_path / "bars" / "X.parquet").write_bytes(b"x")
        with pytest.raises(ValueError, match="drift"):
            _run(table)

    def test_uneven_resize(self, table):
        def uneven(t):
            t["results"][0]["result"]["trades"][1]["legs"].append(_leg("call", -1, 1, 130.0, 0.0, 0.0, selected=4))
        with pytest.raises(ValueError):
            _run(self._mutate(table, uneven))

    def test_a_missing_expiry_close_is_unsettled_not_invented(self, table, tmp_path):
        t = copy.deepcopy(table)
        bars = _bars(tmp_path, {"X": {"2021-01-04": 100.0, "2021-01-15": 90.0, "2021-02-19": 120.0},
                                "Y": {"2021-01-04": 50.0}})
        t["plan"]["source_pins"]["bars"] = bars
        rows = _by(_run(t))
        row = rows[("m1", "2021-01-04:Y:2021-01-15")]
        assert row["outcome"] is None and row["settle_spot"] is None and row["win"] is None
        assert "no close" in row["source_of_settlement"]


class TestParams:
    @pytest.mark.parametrize("bad", [
        {"cohort": ""}, {"methods": []}, {"methods": ["a", "a"]}, {"end_before": "2021-13-01"},
        {"tolerance": -1}, {"tolerance": float("nan")}, {"typo": 1},
    ])
    def test_refuses(self, bad):
        assert MioBookOutcomes.validate_params({**PARAMS, **bad})

    def test_accepts(self):
        assert MioBookOutcomes.validate_params(dict(PARAMS)) == []

    def test_rows_are_json_clean(self, table):
        json.dumps(_run(table), allow_nan=False)
