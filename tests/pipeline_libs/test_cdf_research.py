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


# ADR-0290: outcome-blind freeze, bounded temporal selection, paired evaluation.
def residual_study_fixture(tmp_path):
    import pandas as pd
    import dskit.pipeline.libs.cdf_research as owner

    assert hasattr(owner, "ResidualCDFStudy"), "bounded residual study owner is absent"
    params = dict(
        identity=["symbol", "date", "expiry"], date="date", end="expiry",
        baseline_asof="asof", outcome="y", baseline=["b0", "b1", "b2"],
        features=["signal", "noise"], families={"signal": ["signal"], "noise": ["noise"]},
        alphas=[10., .01], z_grid=[-1., 0., 1.], score_weights=[1., 1., 1.],
        tail_width=2., end_before="2023-01-01", output=str(tmp_path / "study"),
        folds=[dict(name="2022", start="2022-01-01", stop="2023-01-01",
                    fit_start="2018-01-01", exclusions=[["2021-07-01", "2022-01-01"]],
                    inner=[["2020-10-01", "2021-01-01"],
                           ["2021-01-01", "2021-04-01"],
                           ["2021-04-01", "2021-07-01"]])],
        provenance={"source": "1" * 64, "code": "2" * 64},
    )
    rows = []
    for year, month in [(2019, 1), (2020, 10), (2021, 1), (2021, 4), (2021, 10), (2022, 1)]:
        for i in range(12):
            rows.append(dict(symbol="AB"[i % 2], date=f"{year}-{month:02d}-{i+1:02d}",
                             expiry=f"{year}-{month:02d}-{i+14:02d}", asof=f"{year-1}-12-31",
                             signal=float(i % 2), noise=float(i % 3), y=-.5 if i % 2 else .5,
                             b0=.1, b1=.5, b2=.9))
    all_rows = pd.DataFrame(rows)
    history = all_rows[all_rows.date < "2022-01-01"].copy()
    forecasts = all_rows[all_rows.date >= "2022-01-01"].drop(columns="y").copy()
    outcomes = all_rows[all_rows.date >= "2022-01-01"][["symbol", "date", "expiry", "y"]].copy()
    return owner.ResidualCDFStudy(params), history, forecasts, outcomes


def test_residual_study_inventory_ties_and_training_only_monitor_exclusion(tmp_path):
    study, history, forecast, _ = residual_study_fixture(tmp_path)
    assert len(study.specifications()) == 6  # F0, two singles, three family masks
    assert len(study.candidates()) == 13  # plus exact null
    assert study.choose([1., 1., 2.]) == 0
    assert study.choose([1., 1.-1e-14, 2.]) == 1  # strict minimum, no 1SE rule
    frozen = study.freeze(history, forecast)
    receipt = frozen["folds"]["2022"]
    assert receipt["training_rows"] == 48
    assert len(receipt["inner_losses"]) == 13
    assert len(receipt["models"]) == 7  # null and one inner-selected penalty per specification
    assert receipt["selected"] != "null"
    assert all(len(m["prediction_sha256"]) == 64 for m in receipt["models"].values())
    with pytest.raises((ValueError, IntegrityError)):
        study.freeze(history, forecast)


@pytest.mark.parametrize("fault", ["outcome", "duplicate", "future_base", "sealed_label"])
def test_residual_study_refuses_bad_forecast_inputs(tmp_path, fault):
    study, history, forecast, _ = residual_study_fixture(tmp_path)
    if fault == "outcome":
        forecast["y"] = 0.
    elif fault == "duplicate":
        forecast = __import__("pandas").concat([forecast, forecast.iloc[:1]])
    elif fault == "future_base":
        forecast.loc[forecast.index[0], "asof"] = "2022-01-01"
    else:
        forecast.loc[forecast.index[0], "expiry"] = "2023-01-01"
    with pytest.raises(ValueError):
        study.freeze(history, forecast)
    assert not (tmp_path / "study" / "freeze").exists()


def test_residual_study_does_not_load_outcomes_until_all_predictions_verify(tmp_path):
    study, history, forecast, outcomes = residual_study_fixture(tmp_path)
    study.freeze(history, forecast)
    altered = forecast.copy()
    altered.loc[altered.index[0], "signal"] += 1
    calls = []
    def load():
        calls.append(1)
        return outcomes
    with pytest.raises((ValueError, IntegrityError)):
        study.evaluate(altered, load)
    assert not calls
    result = study.evaluate(forecast, load)
    assert calls == [1]
    assert result["rows"] == len(forecast)
    assert result["mean_losses"]["selected"] < result["mean_losses"]["null"]
    assert result["paired"]["selected_vs_null"]["mean_improvement"] > 0
    assert result["diagnostics"]["null"]["full_crps"] > 0
    # A completed evaluation cannot consume another outcome callback.
    with pytest.raises((ValueError, IntegrityError)):
        study.evaluate(forecast, load)
    assert calls == [1]


def test_residual_study_corrupt_freeze_refuses_before_label_read(tmp_path):
    study, history, forecast, _ = residual_study_fixture(tmp_path)
    study.freeze(history, forecast)
    path = tmp_path / "study" / "freeze" / "models.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(IntegrityError):
        study.evaluate(forecast, lambda: pytest.fail("outcomes read before integrity gate"))


def test_residual_study_two_way_variance_and_full_crps_match_hand_calculation(tmp_path):
    study, _, _, _ = residual_study_fixture(tmp_path)
    # 2x2 independent clusters: centered sums [2,-2] and [3,-3].
    delta = np.array([3., 0., 1., -2.])
    paired = study.paired(delta, ["a", "a", "b", "b"], ["x", "y", "x", "y"])
    centered = delta-delta.mean()
    expected = 2*(2**2+(-2)**2)/16 + 2*(3**2+(-3)**2)/16 - 4/3*(centered@centered)/16
    assert paired["variance"] == pytest.approx(expected)
    assert paired["clusters"] == [2, 2, 4]
    with pytest.raises(ValueError, match="clusters"):
        study.paired([1., 2.], ["a", "a"], ["x", "y"])
    # Grid + tails gives exactly Uniform(-3,3); CRPS at zero is width/12.
    score = study.distribution_scores(np.array([[1/3, .5, 2/3]]), np.array([0.]))
    assert score["full_crps"][0] == pytest.approx(.5, abs=1e-12)
    score = study.distribution_scores(np.array([[1/3, .5, 2/3]]), np.array([4.]))
    assert score["full_crps"][0] == pytest.approx(3., abs=1e-12)


def test_residual_study_baseline_ledger_uses_own_earlier_maps(tmp_path):
    import pandas as pd
    study, history, forecasts, _ = residual_study_fixture(tmp_path)
    assert hasattr(study, "issue_baselines"), "historical issue-time ledger is absent"
    early = history.iloc[:4].copy()
    early["pit"] = [.1, .2, .3, .4]
    early["asof"] = "2018-01-01"
    later = history[history.date >= "2021-01-01"].iloc[:4].copy()
    later["pit"] = [.6, .7, .8, .9]
    queries = pd.concat([history[history.date.str.startswith("2020")].drop(columns="y"), forecasts])
    blocks = [dict(name="early", start="2020-01-01", stop="2021-01-01", expected=None),
              dict(name="late", start="2022-01-01", stop="2023-01-01", expected=[0., 0., 1.])]
    issued, receipts = study.issue_baselines(queries, {"early": early, "late": later}, blocks,
                                             raw_columns=["b0", "b1", "b2"], pit_column="pit", knots=3)
    assert issued.loc[issued.date < "2021", "b1"].eq(1.).all()
    assert issued.loc[issued.date > "2021", "b1"].eq(0.).all()
    assert receipts["early"]["learning_rows"] == 4
    later["expiry"] = "2022-01-01"
    with pytest.raises(ValueError, match="availability"):
        study.issue_baselines(queries, {"early": early, "late": later}, blocks,
                             raw_columns=["b0", "b1", "b2"], pit_column="pit", knots=3)


def test_residual_study_warmup_does_not_enter_pooled_diagnostics(tmp_path):
    study, history, forecast, outcomes = residual_study_fixture(tmp_path)
    import pandas as pd
    warm = history[history.date.str.startswith("2021-10")].copy()
    study.params["folds"].insert(0, dict(name="warmup", start="2021-10-01", stop="2022-01-01",
                                        fit_start="2018-01-01", exclusions=[], inner=[], ranked=False))
    all_forecasts = pd.concat([warm.drop(columns="y"), forecast], ignore_index=True)
    all_y = pd.concat([warm[["symbol", "date", "expiry", "y"]], outcomes], ignore_index=True)
    study.freeze(history, all_forecasts)
    result = study.evaluate(all_forecasts, lambda: all_y)
    assert result["rows"] == 12
    assert sum(result["diagnostics"]["null"]["pit_histogram"]) == 12
    assert set(result["warmup_unranked"]) == {"warmup"}



def test_residual_outer_forecasts_may_settle_next_year_before_sealed_cutoff(tmp_path):
    import copy
    import pandas as pd
    from dskit.pipeline.libs.cdf_research import ResidualCDFStudy
    study, history, forecast, _ = residual_study_fixture(tmp_path)
    params = copy.deepcopy(study.params)
    params["end_before"] = "2024-01-01"
    next_fold = copy.deepcopy(params["folds"][0])
    next_fold.update(name="2023", start="2023-01-01", stop="2024-01-01")
    params["folds"].append(next_fold)
    forecast["expiry"] = forecast.expiry.str.replace("2022", "2023")
    later = forecast.copy()
    later["date"] = later.date.str.replace("2022", "2023")
    later["expiry"] = later.expiry.str.replace("-01-", "-02-")
    all_forecasts = pd.concat([forecast, later], ignore_index=True)
    book = ResidualCDFStudy(params).freeze(history, all_forecasts)
    assert sum(f["forecast_rows"] for f in book["folds"].values()) == len(all_forecasts)
    assert all(f["max_training_end"] < name + "-01-01" for name, f in book["folds"].items())


def test_residual_family_semantics_survive_json_key_order_and_changed_order_refuses(tmp_path):
    import copy
    from dskit.pipeline.libs.cdf_research import ResidualCDFStudy
    study, history, forecast, outcomes = residual_study_fixture(tmp_path)
    assert study.params["family_order"] == ["signal", "noise"]
    study.freeze(history, forecast)
    reordered = copy.deepcopy(study.params)
    reordered["families"] = dict(reversed(list(reordered["families"].items())))
    same = ResidualCDFStudy(reordered)
    assert same.specifications() == study.specifications()
    changed = copy.deepcopy(reordered)
    changed["family_order"].reverse()
    calls = []
    def load():
        calls.append(1)
        return outcomes
    with pytest.raises(IntegrityError):
        ResidualCDFStudy(changed).evaluate(forecast, load)
    assert not calls
    report = same.evaluate(forecast, load)
    assert calls == [1]
    assert report["paired"]["ablation_signal"]["mean_improvement"] > 0


def test_residual_continuation_requires_every_predeclared_clause(tmp_path):
    from dskit.pipeline.libs.cdf_research import ResidualCDFStudy
    study, _, _, _ = residual_study_fixture(tmp_path)
    params = dict(study.params, continuation={"controls": ["null", "f0_only"], "folds": ["2022"]})
    study = ResidualCDFStudy(params)
    means = {"selected": 1., "null": 2., "f0_only": 1.5}
    annual = {"2020": {"selected": 3., "null": 2., "f0_only": 2.}, "2022": means}
    pairs = {"selected_vs_" + c: {"ci95": [.01, 1.]} for c in ("null", "f0_only")}
    assert study._continuation(means, annual, pairs)["passed"] is True
    pairs["selected_vs_null"]["ci95"] = None
    failed = study._continuation(means, annual, pairs)
    assert failed["passed"] is False
    assert failed["failed_clauses"] == ["pooled_ci95_vs_null"]
    pairs["selected_vs_null"]["ci95"] = [.01, 1.]
    annual["2022"] = dict(means, selected=1.5)
    assert study._continuation(means, annual, pairs)["failed_clauses"] == ["2022_vs_f0_only"]


@pytest.mark.parametrize("side", ["forecast", "learning"])
@pytest.mark.parametrize("fault", ["date_alias", "expiry_alias", "blank_symbol"])
def test_residual_baseline_identity_normalization_precedes_calibration(tmp_path, monkeypatch, side, fault):
    import pandas as pd
    from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod
    study, history, forecasts, _ = residual_study_fixture(tmp_path)
    learning = history.iloc[:2].copy().assign(pit=[.2, .8])
    target = forecasts if side == "forecast" else learning
    if fault == "blank_symbol":
        target.loc[target.index[0], "symbol"] = "  "
    else:
        alias = target.iloc[:1].copy()
        column = "date" if fault == "date_alias" else "expiry"
        alias[column] = alias[column] + "T12:00:00"
        target = pd.concat([target, alias], ignore_index=True)
        # A uniformly parseable timestamp representation isolates identity validation.
        target[column] = pd.to_datetime(target[column], format="mixed")
    if side == "forecast":
        forecasts = target
    else:
        learning = target
    calls = []
    original = GridCDFMethod.calibration_map
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(GridCDFMethod, "calibration_map", staticmethod(tracked))
    with pytest.raises((ValueError, IntegrityError), match="identit|duplicate|pair"):
        study.issue_baselines(forecasts, {"annual": learning},
                              [dict(name="annual", start="2022-01-01", stop="2023-01-01", expected=None)],
                              raw_columns=["b0", "b1", "b2"], pit_column="pit", knots=3)
    assert not calls
    assert not (tmp_path / "study").exists()


@pytest.mark.parametrize("side", ["history", "forecast"])
def test_residual_blank_identity_refuses_before_freeze_claim(tmp_path, side):
    study, history, forecast, _ = residual_study_fixture(tmp_path)
    target = history if side == "history" else forecast
    target.loc[target.index[0], "symbol"] = " \t"
    with pytest.raises((ValueError, IntegrityError), match="identit|pair"):
        study.freeze(history, forecast)
    assert not (tmp_path / "study").exists()


def memory_fixture():
    import pandas as pd
    import dskit.pipeline.libs.cdf_research as owner

    assert hasattr(owner, "SettledErrorFeatures"), "settled-error transform is absent"
    params = dict(identity=["symbol", "date", "end"], date="date", end="end",
                  outcome="y", baseline=["b0", "b1", "b2"], z_grid=[-1., 0., 1.],
                  score_weights=[1., 1., 1.], end_before="2021-01-01", windows=[2, 10],
                  groups={"symbol": ["symbol"]}, group_window=10,
                  probability_edges=[.5], probability_window=10, prior_rows=2., exclusions=[])
    history = pd.DataFrame([
        dict(symbol="A", date="2020-01-01", end="2020-01-03", y=0., b0=.2, b1=.5, b2=.8),
        dict(symbol="B", date="2020-01-02", end="2020-01-04", y=2., b0=.6, b1=.7, b2=.9),
        dict(symbol="A", date="2020-01-03", end="2020-01-05", y=-2., b0=.2, b1=.5, b2=.8),
    ])
    queries = pd.DataFrame([
        dict(symbol="A", date=f"2020-01-{day:02d}", end="2020-01-20", b0=.2, b1=.5, b2=.8)
        for day in [3, 4, 5, 6]
    ])
    return owner.SettledErrorFeatures(params), history, queries


def test_settled_error_strict_delay_windows_groups_and_bins():
    import numpy as np

    transform, history, queries = memory_fixture()
    result = transform.transform(history, queries)
    pool = result[[f"pool_10_{i}" for i in range(3)]].to_numpy()
    np.testing.assert_array_equal(pool[0], [0., 0., 0.])
    np.testing.assert_allclose(pool[1], [-.2, .5, .2])
    np.testing.assert_allclose(pool[2], [-.4, -.1, -.35])
    np.testing.assert_allclose(result.loc[3, [f"pool_2_{i}" for i in range(3)]].to_numpy(float), [.1, -.1, -.35])
    np.testing.assert_allclose(result.loc[2, [f"group_symbol_10_{i}" for i in range(3)]].to_numpy(float), [-1/3, .1, -1/6])
    assert result.loc[2, "bin_10_0"] == pytest.approx(-1/3)
    assert result.loc[2, "bin_10_1"] == pytest.approx(-.1)
    assert result[queries.columns.intersection(["symbol", "date", "end"])].equals(queries[["symbol", "date", "end"]])


def test_settled_error_future_and_monitor_labels_cannot_change_features():
    import pandas as pd

    transform, history, queries = memory_fixture()
    expected = transform.transform(history, queries.iloc[:3])
    changed = history.copy()
    changed.loc[changed.end >= "2020-01-05", "y"] = 999.
    pd.testing.assert_frame_equal(expected, transform.transform(changed, queries.iloc[:3]))
    transform.params["exclusions"] = [["2020-01-04", "2020-01-06"]]
    expected = transform.transform(history, queries)
    changed.loc[changed.end >= "2020-01-04", "y"] = -999.
    pd.testing.assert_frame_equal(expected, transform.transform(changed, queries))
    assert transform.audit["excluded_rows"] == 2


@pytest.mark.parametrize("fault", ["alias_duplicate", "blank", "future", "equal_end", "nonfinite", "outcome_query", "missing_group"])
def test_settled_error_rejects_invalid_frames(fault):
    import pandas as pd

    transform, history, queries = memory_fixture()
    if fault == "alias_duplicate":
        extra = history.iloc[[0]].copy()
        extra["date"] = "2020-01-01T00:00:00"
        history = pd.concat([history, extra], ignore_index=True)
    elif fault == "blank":
        history.loc[0, "symbol"] = " "
    elif fault == "future":
        history.loc[0, "end"] = "2021-01-01"
    elif fault == "equal_end":
        history.loc[0, "end"] = history.loc[0, "date"]
    elif fault == "nonfinite":
        history.loc[0, "y"] = float("nan")
    elif fault == "outcome_query":
        queries["y"] = 0.
    elif fault == "missing_group":
        transform.params["groups"] = {"missing": ["absent"]}
    with pytest.raises((ValueError, IntegrityError)):
        transform.transform(history, queries)


def test_explicit_residual_specs_freeze_evaluate_and_preserve_legacy_identity(tmp_path):
    import copy
    from dskit.pipeline.libs.cdf_research import ResidualCDFStudy

    legacy, history, forecast, outcomes = residual_study_fixture(tmp_path)
    original = copy.deepcopy(legacy.params)
    assert "feature_specs" not in original
    assert ResidualCDFStudy(original).params == original
    params = {**original, "feature_specs": [dict(name="z_signal", features=["signal"]),
                                           dict(name="a_joint", features=["signal", "noise"])]}
    study = ResidualCDFStudy(params)
    assert list(study.specifications()) == ["f0_only", "z_signal", "a_joint"]
    assert len(study.candidates()) == 7
    study.freeze(history, forecast)
    result = study.evaluate(forecast, lambda: outcomes)
    assert result["rows"] == len(forecast)
    assert not any(k.startswith("ablation_") for k in result["paired"])
    assert "a_joint_vs_null" in result["paired"]


@pytest.mark.parametrize("fault", ["unknown_feature", "reserved_name", "duplicate_name", "duplicate_feature", "empty", "unranked", "extra_key"])
def test_explicit_residual_specs_default_deny(tmp_path, fault):
    from dskit.pipeline.libs.cdf_research import ResidualCDFStudy

    study, _, _, _ = residual_study_fixture(tmp_path)
    params = dict(study.params, feature_specs=[dict(name="chosen", features=["signal"])])
    if fault == "unknown_feature":
        params["feature_specs"][0]["features"] = ["not_a_feature"]
    elif fault == "reserved_name":
        params["feature_specs"][0]["name"] = "selected"
    elif fault == "duplicate_name":
        params["feature_specs"] *= 2
    elif fault == "duplicate_feature":
        params["feature_specs"][0]["features"] *= 2
    elif fault == "empty":
        params["feature_specs"] = []
    elif fault == "unranked":
        params["folds"][0]["ranked"] = False
    elif fault == "extra_key":
        params["feature_specs"][0]["typo"] = 1
    with pytest.raises(ValueError):
        ResidualCDFStudy(params)
