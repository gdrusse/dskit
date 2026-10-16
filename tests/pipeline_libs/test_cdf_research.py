"""Synthetic contracts for market comparisons and bounded pocket research."""

import copy
import math

import numpy as np
import pytest

from dskit.pipeline.libs.cdf_experiment import IntegrityError
from dskit.pipeline.libs.cdf_research import (
    OptionPriceProxy,
    CDFExperimentGrid,
    ConditionalScorePockets,
    CDFReferenceScores,
)


PROXY = dict(
    identity=["symbol", "quote_date", "expiry"],
    date="quote_date",
    end="settlement_date",
    end_before="2021-01-01",
    spot="spot",
    scale="reference_scale",
    growth="growth",
    available="available_at",
    decision="decision_at",
    source_sha256="1" * 64,
    parameters=dict(
        probabilities=[0.1, 0.25, 0.5, 0.75, 0.9],
        min_forward_pairs=3,
        min_wing_nodes=3,
        max_inner_gap=0.1,
        max_outer_gap=0.2,
    ),
)


def quotes():
    from scipy.stats import norm

    rows = []
    for strike in np.arange(70.0, 131.0, 5.0):
        d1 = (np.log(100 / strike) + 0.02) / 0.2
        call = 100 * norm.cdf(d1) - strike * norm.cdf(d1 - 0.2)
        for right, mark in (("call", call), ("put", call - 100 + strike)):
            rows.append(
                dict(
                    symbol="A",
                    quote_date="2020-01-02",
                    expiry="2020-02-03",
                    strike=float(strike),
                    type=right,
                    bid=float(mark / 0.99),
                    ask=float(mark / 0.99),
                    available_at="2020-01-03",
                )
            )
    return rows


def request():
    return dict(
        symbol="A",
        quote_date="2020-01-02",
        expiry="2020-02-03",
        settlement_date="2020-02-03",
        spot=100.0,
        reference_scale=0.2,
        growth=0.99,
        decision_at="2020-01-03",
    )


def test_proxy_recovers_discounted_prices_without_iv_oi_or_mutation():
    rows = quotes()
    before = copy.deepcopy(rows)
    out = OptionPriceProxy("proxy", PROXY).run(
        None, {"quotes": rows, "requests": [request()]}
    )
    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["market_eligible"] == 1
    assert row["forward_ratio"] == pytest.approx(1.0)
    assert row["proxy_q_2"] == pytest.approx(-0.1, abs=0.02)
    assert rows == before


@pytest.mark.parametrize("fault", ["duplicate", "future_quote", "sealed", "bad_growth"])
def test_proxy_refuses_invalid_identity_clock_and_growth(fault):
    rows = quotes()
    req = request()
    if fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    if fault == "future_quote":
        rows[0]["available_at"] = "2020-01-04"
    if fault == "sealed":
        req["settlement_date"] = "2021-01-01"
    if fault == "bad_growth":
        req["growth"] = 0
    with pytest.raises(ValueError):
        OptionPriceProxy("proxy", PROXY).run(None, {"quotes": rows, "requests": [req]})


POCKET = dict(
    identity=["symbol", "quote_date"],
    date="quote_date",
    end="settlement_date",
    trained_through="trained_through",
    cutoff="2020-01-01",
    end_before="2021-01-01",
    features=["ratio", "tail_gap", "iv", "spread"],
    quantiles=[1 / 3, 2 / 3],
    max_order=2,
    losses={"model": "model_loss", "market": "market_loss"},
    model="model",
    min_rows=2,
    min_dates=2,
    eligible="market_eligible",
)


def pocket_rows(year):
    return [
        dict(
            symbol="A",
            quote_date=f"{year}-01-{i + 1:02d}",
            settlement_date=f"{year}-02-{i + 1:02d}",
            trained_through=f"{year - 1}-12-01",
            ratio=float(i),
            tail_gap=float(i),
            iv=float(i),
            spread=float(i),
            model_loss=1.0,
            market_loss=2.0,
            market_eligible=1,
        )
        for i in range(12)
    ]


def test_pockets_keep_all67_and_freeze_on_earlier_predictions():
    prior = pocket_rows(2019)
    later = pocket_rows(2020)
    node = ConditionalScorePockets("pockets", POCKET)
    out = node.run(None, {"fit": prior, "score": later})
    assert len(out["records"]) == 67
    assert out["records"][0]["improvement_pct"]["market"] == pytest.approx(50.0)
    assert sum(r["status"] == "insufficient_support" for r in out["records"]) > 0
    modified = copy.deepcopy(later)
    for row in modified:
        row["ratio"] += 1000
        row["model_loss"] = 50.0
    again = node.run(None, {"fit": prior, "score": modified})
    assert out["summary"]["thresholds"] == again["summary"]["thresholds"]
    assert again["records"][0]["improvement_pct"]["market"] < 0


@pytest.mark.parametrize("fault", ["unsettled", "in_sample", "duplicate", "nonfinite"])
def test_pockets_refuse_leaked_or_unpaired_records(fault):
    prior = pocket_rows(2019)
    later = pocket_rows(2020)
    if fault == "unsettled":
        prior[0]["settlement_date"] = "2020-01-01"
    if fault == "in_sample":
        prior[0]["trained_through"] = prior[0]["quote_date"]
    if fault == "duplicate":
        later.append(copy.deepcopy(later[0]))
    if fault == "nonfinite":
        later[0]["market_loss"] = float("nan")
    with pytest.raises(ValueError):
        ConditionalScorePockets("pockets", POCKET).run(
            None, {"fit": prior, "score": later}
        )


def test_grid_is_plan_only_counts_all_cells_and_cannot_overwrite(tmp_path):
    base = dict(
        training={
            "components": 3,
            "learning_rate": 0.0003,
            "weight_decay": 0.01,
            "seeds": [11],
        },
        arms={"price": {}, "options": {}},
        candidates=[{"name": "model"}],
        output="old",
    )
    params = dict(
        base_config=base,
        space={
            "components": [1, 3, 5],
            "learning_rate": [0.0001, 0.0003],
            "weight_decay": [0.01, 0.1],
        },
        seeds=[11, 29, 47],
        folds=[{"fold": 2, "groups": 277}, {"fold": 3, "groups": 287}],
        max_recipes=12,
        output=str(tmp_path / "plan"),
        end_before="2021-01-01",
    )
    out = CDFExperimentGrid("grid", params).run(None, {})
    assert out["summary"]["neural_fit_slots"] == 144
    assert out["summary"]["empirical_fit_slots"] == 40608
    assert out["summary"]["authorized_fit_attempts"] == 0
    assert all(len(doc["training"]["seeds"]) == 1 for doc in out["documents"].values())
    assert len(out["documents"]) == 36
    assert len(set(d["output"] for d in out["documents"].values())) == 36
    with pytest.raises((ValueError, FileExistsError, IntegrityError)):
        CDFExperimentGrid("grid", params).run(None, {})


def test_reference_fit_gate_refuses_before_touching_inputs():
    class Trap:
        def __iter__(self):
            raise AssertionError("read before gate")

    params = dict(
        identity=["symbol", "date"],
        group="symbol",
        date="date",
        end="end",
        cutoff="2020-01-01",
        end_before="2021-01-01",
        features=["q0", "q1", "ok", "h", "scale"],
        target="target",
        reference="scale",
        eligible="ok",
        decision_strike="strike",
        parameters=dict(
            proxy_indices=[0, 1],
            probabilities=[0.1, 0.9],
            eligible_index=2,
            condition_indices=[3],
            reference_index=4,
            knots=21,
            tail_width=4.0,
        ),
        transports={"market_raw": 0, "market_calibrated": 21},
        samples=50,
        tail_intervals=[[-3, -1], [1, 3]],
        tail_points=31,
        tail_weight=1.0,
        authorized_fit_attempts=0,
        claim=str(__import__("pathlib").Path("/unused/claim")),
    )
    with pytest.raises(ValueError, match="budget"):
        CDFReferenceScores("refs", params).run(None, {"fit": Trap(), "score": Trap()})


def test_reference_scores_pair_active_and_fallback_rows_and_consume_claim(tmp_path):
    params = dict(
        identity=["symbol", "date"],
        group="symbol",
        date="date",
        end="end",
        cutoff="2020-01-01",
        end_before="2021-01-01",
        features=["q0", "q1", "ok", "h", "scale"],
        target="target",
        reference="scale",
        eligible="ok",
        decision_strike="strike",
        parameters=dict(
            proxy_indices=[0, 1],
            probabilities=[0.1, 0.9],
            eligible_index=2,
            condition_indices=[3],
            reference_index=4,
            knots=21,
            tail_width=4.0,
        ),
        transports={"market_raw": 0, "market_calibrated": 21},
        samples=50,
        tail_intervals=[[-3, -1], [1, 3]],
        tail_points=31,
        tail_weight=1.0,
        authorized_fit_attempts=5,
        claim=str(tmp_path / "claim.json"),
    )

    def rows(year):
        return [
            dict(
                symbol="A",
                date=f"{year}-01-{i + 1:02d}",
                end=f"{year}-02-{i + 1:02d}",
                q0=-1.0,
                q1=1.0,
                ok=int(i != 0),
                h=31.0,
                scale=0.2,
                target=(i - 6) * 0.01,
                strike=-0.5,
            )
            for i in range(12)
        ]

    node = CDFReferenceScores("refs", params)
    out = node.run(None, {"fit": rows(2019), "score": rows(2020)})
    assert len(out["records"]) == 12
    assert out["summary"]["market_rows"] == 11
    assert out["summary"]["fallback_rows"] == 1
    assert out["summary"]["fit_attempts"] == 5
    for row in out["records"]:
        assert math.isfinite(row["market_raw_weighted_crps"])
        assert math.isfinite(row["market_calibrated_weighted_crps"])
    with pytest.raises(FileExistsError):
        node.run(None, {"fit": rows(2019), "score": rows(2020)})


def test_pocket_ties_retain_empty_bins_without_recutting():
    prior = pocket_rows(2019)
    later = pocket_rows(2020)
    for row in prior:
        row["iv"] = 1.0
    out = ConditionalScorePockets("pockets", POCKET).run(
        None, {"fit": prior, "score": later}
    )
    assert len(out["records"]) == 67
    assert out["summary"]["thresholds"]["iv"] == [1.0, 1.0]
    assert any(
        row["pocket"] == {"iv": 1} and row["rows"] == 0 for row in out["records"]
    )


@pytest.mark.parametrize("source", ["model", "control", "market", "context"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("scale", 0.4),
        ("scale", 0.0),
        ("scale", float("nan")),
        ("strike", 0.2),
        ("strike", float("inf")),
        ("trained_through", "2020-01-01"),
        ("trained_through", "2020-12-31"),
        ("trained_through", "2019-1-1"),
    ],
)
def test_paired_forecasts_align_before_computing_gap_and_refuse_missing(
    source, field, value
):
    from dskit.pipeline.libs.cdf_research import PairedForecastScores

    params = dict(
        identity=["symbol", "date"],
        date="date",
        end="end",
        end_before="2021-01-01",
        components=1,
        market="market_raw",
        decision_strike="strike",
        iv="iv",
        spread="spread",
        trained_through="trained_through",
        reference="scale",
    )
    common = dict(
        symbol="A",
        date="2020-01-01",
        end="2020-02-01",
        scale=0.2,
        strike=0.0,
        trained_through="2019-01-01",
    )
    model = {
        **common,
        "weights0": 1.0,
        "means0": 0.0,
        "scales0": 2.0,
        "model_weighted_crps": 1.0,
        "baseline_weighted_crps": 3.0,
    }
    market = {
        **common,
        "market_eligible": 1,
        "market_raw_sd_z": 1.0,
        "market_raw_lower_probability": 0.5,
        "market_raw_weighted_crps": 2.0,
        "market_calibrated_weighted_crps": 1.5,
        "strike": 0.0,
        "scale": 0.2,
    }
    context = {
        **common,
        "strike": 0.0,
        "scale": 0.2,
        "iv": 0.3,
        "spread": 0.01,
        "trained_through": "2019-01-01",
    }
    node = PairedForecastScores("paired", params)
    out = node.run(
        None,
        {
            "model": [model],
            "control": [model],
            "market": [market],
            "context": [context],
        },
    )
    assert out["records"][0]["ratio"] == pytest.approx(2.0)
    assert out["records"][0]["tail_gap"] == pytest.approx(0.0)
    assert out["records"][0]["price_only_weighted_crps"] == 1.0
    with pytest.raises(ValueError):
        node.run(
            None,
            {"model": [model], "control": [], "market": [market], "context": [context]},
        )
    inputs = {
        "model": [dict(model)],
        "control": [dict(model)],
        "market": [dict(market)],
        "context": [dict(context)],
    }
    inputs[source][0][field] = value
    with pytest.raises(ValueError):
        node.run(None, inputs)
    del inputs[source][0][field]
    with pytest.raises(ValueError):
        node.run(None, inputs)
    inputs[source][0][field] = common[field]
    inputs["control"][0]["trained_through"] = "2019-12-01"
    out = node.run(None, inputs)
    assert out["records"][0]["trained_through"] == "2019-12-01"
    for row in inputs.values():
        row[0]["end"] = row[0]["date"]
    with pytest.raises(ValueError):
        node.run(None, inputs)


@pytest.mark.parametrize("failure", ["fit", "file_sync", "directory_sync"])
def test_reference_failure_leaves_consumed_claim_and_terminal_receipt(
    tmp_path, monkeypatch, failure
):
    from dskit.pipeline.libs.predictive_cdf import OptionImpliedTransportCDF
    import json

    params = dict(
        identity=["symbol", "date"],
        group="symbol",
        date="date",
        end="end",
        cutoff="2020-01-01",
        end_before="2021-01-01",
        features=["q0", "q1", "ok", "h", "scale"],
        target="target",
        reference="scale",
        eligible="ok",
        decision_strike="strike",
        parameters=dict(
            proxy_indices=[0, 1],
            probabilities=[0.1, 0.9],
            eligible_index=2,
            condition_indices=[3],
            reference_index=4,
            knots=21,
            tail_width=4.0,
        ),
        transports={"raw": 0},
        samples=50,
        tail_intervals=[[-3, -1], [1, 3]],
        tail_points=31,
        tail_weight=1.0,
        authorized_fit_attempts=2,
        claim=str(tmp_path / "claim.json"),
    )

    calls = []

    def fail(*args, **kwargs):
        calls.append("fit")
        raise RuntimeError("injected fit interruption")

    monkeypatch.setattr(OptionImpliedTransportCDF, "fit", fail)
    if failure != "fit":
        import os

        original_sync = os.fsync
        sync_calls = []

        def sync(descriptor):
            sync_calls.append(descriptor)
            if len(sync_calls) == (1 if failure == "file_sync" else 2):
                raise OSError("injected sync interruption")
            return original_sync(descriptor)

        monkeypatch.setattr(os, "fsync", sync)

    def row(year):
        return dict(
            symbol="A",
            date=f"{year}-01-01",
            end=f"{year}-02-01",
            q0=-1.0,
            q1=1.0,
            ok=1,
            h=31.0,
            scale=0.2,
            target=0.1,
            strike=-0.5,
        )

    node = CDFReferenceScores("refs", params)
    with pytest.raises((RuntimeError, OSError), match="injected"):
        node.run(None, {"fit": [row(2019)], "score": [row(2020)]})
    assert (tmp_path / "claim.json").exists()
    terminal = json.loads((tmp_path / "claim.json.terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert terminal["charged_fit_attempts"] == 2
    assert calls == (["fit"] if failure == "fit" else [])
    with pytest.raises(FileExistsError):
        node.run(None, {"fit": [row(2019)], "score": [row(2020)]})


def test_pockets_never_count_empirical_fallback_as_market_evidence():
    fit = pocket_rows(2019)
    score = pocket_rows(2020)
    score[0]["market_eligible"] = 0
    score[0]["model_loss"] = 1000000.0
    out = ConditionalScorePockets("pockets", POCKET).run(
        None, {"fit": fit, "score": score}
    )
    assert out["records"][0]["rows"] == 11
    assert out["records"][0]["improvement_pct"]["market"] == 50.0
    assert out["summary"]["excluded_nonmarket"]["score"] == 1
