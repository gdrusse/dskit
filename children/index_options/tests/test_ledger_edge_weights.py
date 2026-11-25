"""ADR-0318: accuracy weighting, a days-to-expiry tilt and a name-spread multiplier on the FAST hedged Kelly search."""

import copy
import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from index_options import ledger_studies
from index_options.ledger_studies import EdgeWeights
from test_ledger_multistart import HEDGED_FORMULATIONS, _hedged_world, _result
from test_ledger_studies import FORMULATIONS, _PARITY

BASE = "kelly075_ls_fast_hedged_static_lp"
#: The three declared values (ADR-0318) and the four new ids: the base document plus kelly keys, nothing else.
ACCURACY = {"shrink_rows": 60}
DTE_TILT = .5
NAME_SPREAD = 2.
KNOBS = {
    "kelly075_ls_fast_hedged_static_acc_lp": {"accuracy": ACCURACY},
    "kelly075_ls_fast_hedged_static_acc_dte_lp": {"accuracy": ACCURACY, "dte_tilt": DTE_TILT},
    "kelly075_ls_fast_hedged_static_acc_names_lp": {"accuracy": ACCURACY, "name_spread": NAME_SPREAD},
    "kelly075_ls_fast_hedged_static_acc_dte_names_lp": {"accuracy": ACCURACY, "dte_tilt": DTE_TILT,
                                                        "name_spread": NAME_SPREAD},
}
EDGE_FORMULATIONS = {name: {**copy.deepcopy(HEDGED_FORMULATIONS[BASE]),
                            "kelly": {**copy.deepcopy(HEDGED_FORMULATIONS[BASE]["kelly"]), **copy.deepcopy(knobs)}}
                     for name, knobs in KNOBS.items()}


def _arm(name, kelly=None):
    method = EDGE_FORMULATIONS.get(name) or HEDGED_FORMULATIONS[name]
    arm = {"id": name, **copy.deepcopy(method)}
    if kelly is not None:
        arm["kelly"] = {**arm["kelly"], **kelly}
    return arm


def _study(arm, cheap_call=True):
    params, inputs = _hedged_world(cheap_call)
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    return params, inputs, ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def _spy(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve
    scored, dollar = [], []
    real_score, real_run = TransportActionScore.__init__, TransportPortfolioSolve.run

    def score(scorer, program, *a, **k):
        scored.append(copy.deepcopy(program))
        real_score(scorer, program, *a, **k)

    def run(solver, inputs, program, *a, **k):
        dollar.append(copy.deepcopy(program))
        return real_run(solver, inputs, program, *a, **k)
    monkeypatch.setattr(TransportActionScore, "__init__", score)
    monkeypatch.setattr(TransportPortfolioSolve, "run", run)
    return scored, dollar


def _expected(group):
    return [math.fsum([float(c), *(-float(p)*float(row[j]) for p, row in zip(group["masses"], group["losses"]))])
            for j, c in enumerate(group["credit"])]


# --- declaration -------------------------------------------------------------------------------------------------

def test_the_four_ids_are_the_base_document_plus_their_kelly_keys_and_validate():
    base = HEDGED_FORMULATIONS[BASE]
    for name, method in EDGE_FORMULATIONS.items():
        assert {k: v for k, v in method.items() if k != "kelly"} == {k: v for k, v in base.items() if k != "kelly"}
        assert {k: v for k, v in method["kelly"].items() if k not in EdgeWeights.KEYS} == base["kelly"]
        assert method["kelly"]["accuracy"] == {"shrink_rows": 60}
        params, _ = _hedged_world()
        params.update(methods=[{"id": name, **method}], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert [("dte_tilt" in m["kelly"], "name_spread" in m["kelly"]) for m in EDGE_FORMULATIONS.values()] == [
        (False, False), (True, False), (False, True), (True, True)]


@pytest.mark.parametrize("kelly", [
    {"accuracy": {"shrink_rows": 0}}, {"accuracy": {"shrink_rows": 60, "extra": 1}}, {"accuracy": 60},
    {"dte_tilt": 0.}, {"dte_tilt": 1.}, {"dte_tilt": True}, {"name_spread": .5}, {"name_spread": float("nan")}])
def test_a_malformed_knob_refuses(kelly):
    params, _ = _hedged_world()
    params.update(methods=[_arm(BASE, kelly)], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_knob_on_a_multistart_method_refuses():
    params, _ = _hedged_world()
    params.update(methods=[_arm("kelly075_ls_multistart3_hedged_static_lp", {"dte_tilt": .5})],
                  hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


# --- no knob: every existing method is byte for byte what origin/main fb6a66e5 produced ------------------------------

#: sha256 of ``json.dumps(obj, sort_keys=True, default=str)`` of the result, the scorer programs and the method, recorded
#: on the untouched origin/main tree (fb6a66e5) by the same code as :func:`_digests` before this change was written.
#: ``result`` is the result without its clock-read fields (:func:`_stable`: a stage-one certificate's ``seconds``),
#: equal on two origin/main runs.
ORIGIN_MAIN = {
    "kelly075_ls_fast_hedged_static_lp:cheap=True": {
        "method": "bae312e0ab58afc82f16bd442d5864c42e85fa9a31257fc882ad5e8fea7e3467",
        "programs": "9414ab52c6650f7173e1501a84dc68d6fa5c76ddddb1cbd7706c400e7354572e",
        "result": "4bcb60a076112295e0ae498169a5d4474cde2c0f6f527aad3adbb57be192753d"},
    "kelly075_ls_fast_hedged_static_lp:cheap=False": {
        "method": "bae312e0ab58afc82f16bd442d5864c42e85fa9a31257fc882ad5e8fea7e3467",
        "programs": "d761bd26f73f20349f9597a78a3336d72afc8c1120e103bb409587d373f5b38c",
        "result": "dd1df57c44af2c3156905d32a44cad561a802c2fb348abf51f79b74c05c282ec"},
    "kelly075_ls_fast_hedged_dynamic_lp:cheap=True": {
        "method": "c2ce175f6eca217d51061ece317e85d051764f8718c70feec32a1bf1ed6e2de7",
        "programs": "c72be1e120a8e3421e9c8fc48fdf93e22f1c92363d7a0dc0b2c369e09262331f",
        "result": "865674e4bda13ba396b299602d676ae6c0247abe9888e3308dd95ae7649a7186"},
    "kelly075_ls_multistart3_hedged_static_lp:cheap=True": {
        "method": "5d99b91f41c8937d65b4f9d016dc3bbeb496a644a13613684cb1865674e8fc01",
        "programs": "d1d140f65e7b3d86a6165418f0e601472795437158e59e78774640750729f19a",
        "result": "2532688d904c970fc17e42adf951601b416003da22f019e851295d9f7c82800b"},
}


def _stable(obj):
    if isinstance(obj, dict):
        return {k: _stable(v) for k, v in obj.items() if not (k == "seconds" or str(k).endswith("_seconds"))}
    if isinstance(obj, (list, tuple)):
        return [_stable(v) for v in obj]
    return obj

def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@pytest.mark.parametrize("key", sorted(ORIGIN_MAIN))
def test_without_a_knob_the_scored_programs_and_method_are_origin_mains_byte_for_byte(key, monkeypatch):
    name, cheap = key.split(":cheap=")
    from dskit.pipeline.libs.pyomo import TransportActionScore
    seen = []
    real = TransportActionScore.__init__

    def spy(scorer, program, *a, **k):
        seen.append(program)
        real(scorer, program, *a, **k)
    monkeypatch.setattr(TransportActionScore, "__init__", spy)
    arm = _arm(name)
    _params, _inputs, study = _study(arm, cheap == "True")
    result = study._method(arm, None)
    assert _digest(arm) == ORIGIN_MAIN[key]["method"]
    assert _digest(seen) == ORIGIN_MAIN[key]["programs"]
    assert _digest(_stable(result)) == ORIGIN_MAIN[key]["result"]
    assert "edge_weights" not in (result["decisions"][0].get("kelly") or {})
    assert all("value_adjust" not in g for p in seen for g in p["groups"])
    assert all("charge_scale" not in p.get("utility", {}) for p in seen)


def test_without_a_knob_a_multistart_result_is_origin_mains_byte_for_byte():
    *_, method, _study_, result = _result()
    assert _digest(method) == "224c8ee3847a80fbdce46a8bef63551a50edb58feb05831b7587fca00e5dc247"
    assert _digest(result) == "fdbae4498bd85324e8ac347a47527c5e305761f2cdb47b02042e4d20b7f38786"


# --- the accuracy weight: causal, bounded, shrunk ---------------------------------------------------------------

Z = [-1., 0., 1.]
LABELS = ["zm1", "zp0", "zp1"]


def _fake(rows, pairs=True):
    """A study stand-in: ``rows`` are (symbol, quote, expiry, cdf, realised_z) with scale 1 and a close of 100 at quote."""
    forecasts, contexts, bars = {}, {}, {}
    for symbol, quote, expiry, cdf, y in rows:
        forecasts.setdefault(symbol, {})[quote, expiry] = {
            "fitted": True, **{f"method_cdf_{label}": v for label, v in zip(LABELS, cdf)}}
        contexts.setdefault(symbol, {})[quote, expiry] = {"settlement_date": expiry, "reference_scale": 1.}
        closes = bars.setdefault(symbol, {})
        start = closes.setdefault(quote, 100.)
        closes[expiry] = start*math.exp(y)
    symbols = sorted(forecasts)
    return SimpleNamespace(
        params={"symbols": symbols}, _forecasts=forecasts, _contexts=contexts, _bars=bars,
        _factors={s: {} for s in symbols}, _pair=lambda s, q, e: {"pair": s} if pairs else None,
        _nodes={s: SimpleNamespace(params={"z_grid": Z, "grid_labels": LABELS}) for s in symbols})


G = [.5*(1+math.erf(z/math.sqrt(2))) for z in Z]


def _hand(rows, z_grid=Z):
    g_ref = [.5*(1+math.erf(z/math.sqrt(2))) for z in z_grid]
    ab = aa = 0.
    for cdf, y in rows:
        a = [f-g for f, g in zip(cdf, g_ref)]
        b = [(1. if y <= z else 0.)-g for z, g in zip(z_grid, g_ref)]
        ab += sum(x*v for x, v in zip(a, b))
        aa += sum(x*x for x in a)
    return ab, aa


def _bound(ab, aa):
    return 1. if not aa > 0 else round(min(1., max(0., ab/aa)), 9)


def _shrunk(by_name, k, names):
    """The ADR-0318 weights by hand: ``by_name`` maps a name to its ``(ab, aa, rows)``; returns (weights, pooled)."""
    n = sum(r for _ab, _aa, r in by_name.values())
    s_ab, s_aa = sum(ab for ab, _aa, _r in by_name.values()), sum(aa for _ab, aa, _r in by_name.values())
    bar = s_aa/n
    pooled = _bound(s_ab+k*bar, s_aa+k*bar)
    own = {s: by_name.get(s, (0., 0., 0)) for s in names}
    return {s: _bound(ab+k*pooled*bar, aa+k*bar) for s, (ab, aa, _r) in own.items()}, pooled


#: Small enough that the pseudo-rows vanish at 9 places: the name's own least-squares weight.
UNSHRUNK = {"accuracy": {"shrink_rows": 1e-12}}


def test_a_sharp_right_forecast_weighs_one_a_wrong_one_zero_and_the_reference_itself_one():
    right = [("A", "2019-01-02", "2019-01-04", [0., 1., 1.], -.5)]   # the outcome's own step
    wrong = [("A", "2019-01-02", "2019-01-04", [1., 1., 1.], .5)]    # all mass below an outcome above zero
    same = [("A", "2019-01-02", "2019-01-04", G, .5)]
    for rows, expected in ((right, 1.), (wrong, 0.), (same, 1.)):
        w, record = EdgeWeights(_fake(rows), UNSHRUNK).accuracy("2019-01-07", ["A"])
        assert w == {"A": expected} and record["scored_rows"] == 1 and not record["no_history"]


def test_only_rows_settled_and_quoted_strictly_before_the_day_count():
    rows = [("A", "2019-01-02", "2019-01-04", [.2, .7, .9], .3),
            ("A", "2019-01-03", "2019-01-07", [.9, .95, 1.], .8),    # settles ON the day: unread
            ("A", "2019-01-07", "2019-01-11", [.0, .0, .0], -2.)]    # quoted on the day, settles after: unread
    weights = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 5}})
    w, record = weights.accuracy("2019-01-07", ["A"])
    ab, aa = _hand([([.2, .7, .9], .3)])
    assert record["scored_rows"] == 1 and w == _shrunk({"A": (ab, aa, 1)}, 5, ["A"])[0]
    assert record["unscored"] == {}, "rows not yet settled are not counted as unscored"
    # a later day reads the second row too, and nothing on the first day moved when the future rows changed
    later, _ = weights.accuracy("2019-01-08", ["A"])
    ab2, aa2 = _hand([([.2, .7, .9], .3), ([.9, .95, 1.], .8)])
    assert later == _shrunk({"A": (ab2, aa2, 2)}, 5, ["A"])[0]
    changed = [rows[0], ("A", "2019-01-03", "2019-01-07", [0., 0., 1.], -3.), rows[2]]
    assert EdgeWeights(_fake(changed), {"accuracy": {"shrink_rows": 5}}).accuracy("2019-01-07", ["A"])[0] == w


def test_nothing_settling_on_or_after_the_development_seal_is_scored():
    from index_options.ledger_studies import DEVELOPMENT_SEAL
    assert DEVELOPMENT_SEAL == "2021-07-01"
    rows = [("A", "2021-06-21", "2021-06-25", [.2, .7, .9], .3),
            ("A", "2021-06-24", "2021-07-01", [1., 1., 1.], 2.),     # settles on the seal: never read
            ("A", "2021-06-25", "2021-07-02", [0., 0., 0.], -2.)]    # settles after it: never read
    weights = EdgeWeights(_fake(rows), UNSHRUNK)
    for day, sealed in (("2021-07-02", 1), ("2021-07-09", 2)):
        w, record = weights.accuracy(day, ["A"])
        assert record["scored_rows"] == 1 and record["unscored"] == {"after_seal": sealed}
        assert w == {"A": _bound(*_hand([([.2, .7, .9], .3)]))}
    # the holdout rows' values cannot move the weight
    other = [rows[0], ("A", "2021-06-24", "2021-07-01", [0., 0., 1.], -3.), ("A", "2021-06-25", "2021-07-02", G, 0.)]
    assert EdgeWeights(_fake(other), UNSHRUNK).accuracy("2021-07-09", ["A"])[0] == w


def test_a_name_is_shrunk_toward_the_pool_and_a_name_without_rows_takes_the_pooled_value():
    rows = [("A", "2019-01-02", "2019-01-04", [0., .9, 1.], -.5), ("A", "2019-01-03", "2019-01-04", [.05, .6, .95], -.5),
            ("B", "2019-01-02", "2019-01-04", [.2, .6, .9], .4)]
    w, record = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-07", ["A", "B", "C"])
    a_ab, a_aa = _hand([([0., .9, 1.], -.5), ([.05, .6, .95], -.5)])
    b_ab, b_aa = _hand([([.2, .6, .9], .4)])
    assert 0. < _shrunk({"A": (a_ab, a_aa, 2), "B": (b_ab, b_aa, 1)}, 2, ["B"])[0]["B"] < 1., "B is interior"
    want, pooled = _shrunk({"A": (a_ab, a_aa, 2), "B": (b_ab, b_aa, 1)}, 2, ["A", "B", "C"])
    assert w == pytest.approx(want, abs=1e-9) and record["pooled"] == pytest.approx(pooled, abs=1e-9)
    assert w["C"] == record["pooled"] and w["A"] != w["B"]
    assert record["fallback_pooled"] == 1 and record["names"] == 3 and record["min"] <= record["median"] <= record["max"]
    assert all(0. <= v <= 1. for v in w.values())


def test_the_pooled_weight_is_shrunk_toward_face_value_so_a_few_early_rows_cannot_zero_every_name():
    wrong = [(s, "2019-01-02", "2019-01-04", [1., 1., 1.], .5) for s in ("A", "B", "C")]
    w, record = EdgeWeights(_fake(wrong), {"accuracy": {"shrink_rows": 60}}).accuracy("2019-01-07", ["A", "B", "C", "D"])
    assert _bound(*_hand([([1., 1., 1.], .5)]*3)) == 0., "unshrunk, three wrong rows would zero the pool"
    assert record["pooled"] > .9 and min(w.values()) > .9 and w["D"] == record["pooled"]
    # with a long record the data speak: 600 wrong rows bring the pool well under a half
    many = [(f"N{i:03d}", "2019-01-02", "2019-01-04", [1., 1., 1.], .5) for i in range(600)]
    _, record = EdgeWeights(_fake(many), {"accuracy": {"shrink_rows": 60}}).accuracy("2019-01-07", ["N000"])
    assert record["pooled"] < .5


def test_a_week_with_no_scored_row_takes_the_forecast_at_face_value_and_says_so():
    rows = [("A", "2019-01-02", "2019-01-04", [.1, .6, .95], .2)]
    w, record = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-04", ["A"])
    assert w == {"A": 1.} and record["no_history"] and record["scored_rows"] == 0 and record["pooled"] is None
    w, record = EdgeWeights(_fake(rows, pairs=False), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-07", ["A"])
    assert w == {"A": 1.} and record["unscored"] == {"no_pair": 1}, "a row the method could not trade is not scored"


def test_an_empty_history_past_the_grace_days_refuses_loudly():
    rows = [("A", "2019-01-02", "2019-01-04", [.1, .6, .95], .2)]
    weights = EdgeWeights(_fake(rows, pairs=False), {"accuracy": {"shrink_rows": 2}})
    assert EdgeWeights.NO_HISTORY_GRACE_DAYS == 21
    assert weights.accuracy("2019-01-23", ["A"])[1]["no_history"]           # 21 days after the first forecast
    with pytest.raises(ValueError, match="accuracy_no_history.*no_pair"):
        weights.accuracy("2019-01-24", ["A"])


# --- the accuracy weight on the REAL study class (its own forecasts, contexts, closes, factors, pairs, node grid) ---

def _real_study(closes, fitted=(True, False), require_fitted=False):
    """The hedged world's study with a sharp forecast (a normal shifted up by one z) on both of its rows.

    Rows: quoted 2019-01-02 settling 2019-02-01 and quoted 2019-01-07 settling 2019-02-08 (pair X30, 28-32 days).
    ``closes`` sets the close on those four days; ``fitted`` the two rows' flags.
    """
    params, inputs = _hedged_world()
    params["bridge"]["require_fitted"] = require_fitted
    z_grid = [float(z) for z in params["bridge"]["z_grid"]]
    sharp = [.5*(1+math.erf((z-1.)/math.sqrt(2))) for z in z_grid]
    for row, flag in zip(sorted(inputs["X"]["forecasts"], key=lambda r: r["quote_date"]), fitted, strict=True):
        row.update({f"method_cdf_{label}": v for label, v in zip(params["bridge"]["grid_labels"], sharp)}, fitted=flag)
    for bar in inputs["X"]["bars"]:
        if bar["date"] in closes:
            bar["close"] = closes[bar["date"]]
    arm = _arm("kelly075_ls_fast_hedged_static_acc_lp")
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    rows = sorted((r["quote_date"], r["settlement_date"], r["reference_scale"]) for r in inputs["X"]["contexts"])
    return study, arm, z_grid, sharp, rows


def test_the_real_study_scores_its_own_settled_rows_and_the_weight_follows_the_outcomes():
    up = {"2019-01-02": 100., "2019-02-01": 102.5, "2019-01-07": 100., "2019-02-08": 102.}    # the sharp view was right
    down = {"2019-01-02": 100., "2019-02-01": 97.5, "2019-01-07": 100., "2019-02-08": 98.}  # it was wrong
    weights = {}
    for label, closes in (("up", up), ("down", down)):
        study, arm, z_grid, sharp, rows = _real_study(closes)
        edge = EdgeWeights(study, arm["kelly"])
        assert [s for s in study.params["symbols"]] == ["X"]
        _w, first = edge.accuracy("2019-01-02", ["X"])
        assert first["no_history"] and first["scored_rows"] == 0
        w, mid = edge.accuracy("2019-02-04", ["X"])      # the first row has settled, the second has not
        assert mid["scored_rows"] == 1 and mid["unscored"] == {} and not mid["no_history"]
        w, late = edge.accuracy("2019-02-11", ["X"])     # both settled; the unfitted one counts (require_fitted off)
        assert late["scored_rows"] == 2 and late["unscored"] == {} and late["fallback_pooled"] == 0
        hand = [(sharp, math.log(closes[s]/closes[q])/scale) for q, s, scale in rows]
        ab, aa = _hand(hand, z_grid)
        assert w == _shrunk({"X": (ab, aa, 2)}, 60, ["X"])[0] and late["pooled"] == _shrunk({"X": (ab, aa, 2)}, 60, ["X"])[1]
        weights[label] = w["X"]
    assert weights["down"] < weights["up"] <= 1. and weights["down"] < 1.


def test_the_real_study_follows_require_fitted_and_drops_a_basis_change():
    closes = {"2019-01-02": 100., "2019-02-01": 102.5, "2019-01-07": 100., "2019-02-08": 102.}
    study, arm, *_ = _real_study(closes, require_fitted=True)
    _w, record = EdgeWeights(study, arm["kelly"]).accuracy("2019-02-11", ["X"])
    assert record["scored_rows"] == 1 and record["unscored"] == {"not_fitted": 1}
    study, arm, *_ = _real_study(closes)
    study._factors["X"].update({"2019-01-02": 1., "2019-02-01": 2.})
    _w, record = EdgeWeights(study, arm["kelly"]).accuracy("2019-02-11", ["X"])
    assert record["scored_rows"] == 1 and record["unscored"] == {"basis_change": 1}


def test_the_dte_tilt_is_one_at_the_long_end_and_falls_linearly_to_the_short_end():
    tilt = EdgeWeights(None, {"dte_tilt": .5}).tilt
    assert tilt(60) == 1. and tilt(90) == 1. and tilt(30) == pytest.approx(.75)
    assert tilt(7) == pytest.approx(1-.5*53/60) and tilt(1) == pytest.approx(1-.5*59/60)
    assert EdgeWeights(None, {"accuracy": ACCURACY}).tilt(1) == 1.


# --- the knobs in the week: objective only, stage one too, replays reproduce ---------------------------------------

def _weighted(monkeypatch, name, weight):
    def accuracy(self, day, symbols):
        return {s: weight for s in symbols}, {"stub": weight}
    monkeypatch.setattr(EdgeWeights, "accuracy", accuracy)
    scored, dollar = _spy(monkeypatch)
    arm = _arm(name)
    params, inputs, study = _study(arm)
    return arm, study, study._method(arm, None), scored, dollar


@pytest.mark.parametrize("name", sorted(KNOBS))
def test_the_weighted_edge_is_the_value_adjust_of_both_programs_and_nothing_else_moves(monkeypatch, name):
    arm, study, result, scored, dollar = _weighted(monkeypatch, name, .4)
    base_arm = _arm(BASE)
    _, _, base_study = _study(base_arm)
    monkeypatch.undo()
    b_scored, b_dollar = _spy(monkeypatch)
    base_study._method(base_arm, None)
    week = result["decisions"][0]
    dte = study._prepare("2019-01-02")[1][0]["dte"]
    m = round(.4*(EdgeWeights(None, arm["kelly"]).tilt(dte)), 9)
    program = next(p for p in reversed(scored) if "utility" in p)
    base_program = next(p for p in reversed(b_scored) if "utility" in p)
    assert dollar and b_dollar and "utility" not in dollar[0]
    for weighted, plain in ((program, base_program), (dollar[0], b_dollar[0])):
        for group, base_group in zip(weighted["groups"], plain["groups"], strict=True):
            assert _digest({k: v for k, v in group.items() if k != "value_adjust"}) == _digest(base_group)
            assert list(group["value_adjust"]) == pytest.approx([(m-1.)*e for e in _expected(base_group)], abs=1e-12)
    utility = {k: v for k, v in program["utility"].items() if k != "charge_scale"}
    assert _digest(utility) == _digest(base_program["utility"])
    assert program["utility"].get("charge_scale") == arm["kelly"].get("name_spread")
    assert _digest({k: v for k, v in program.items() if k not in ("groups", "utility")}) == \
        _digest({k: v for k, v in base_program.items() if k not in ("groups", "utility")})
    record = week["kelly"]["edge_weights"]
    assert record["accuracy"] == {"stub": .4} and record["multiplier"]["min"] == record["multiplier"]["max"] == m
    for book in week["books"]:
        assert book["edge_multiplier"] == m and book["expected_raw"] != book["expected"]


def test_the_scorers_expected_profit_is_the_multiplier_times_the_forecasts(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    _, _, _, scored, _ = _weighted(monkeypatch, "kelly075_ls_fast_hedged_static_acc_lp", .25)
    program = scored[-1]
    plain = {**program, "groups": [{k: v for k, v in g.items() if k != "value_adjust"} for g in program["groups"]]}
    weighted, raw = TransportActionScore(program, {}).expected, TransportActionScore(plain, {}).expected
    for w_row, r_row in zip(weighted, raw):
        assert list(w_row) == pytest.approx([.25*e for e in r_row], abs=1e-9)
        assert all((w > 0) == (e > 0) for w, e in zip(w_row, r_row) if abs(e) > 1e-9), "a sign never changes"


def test_a_zero_weight_trades_nothing_and_a_unit_weight_is_the_base_selection(monkeypatch):
    zero = _weighted(monkeypatch, "kelly075_ls_fast_hedged_static_acc_lp", 0.)[2]
    assert zero["decisions"][0]["status"] == "cash" and not zero["trades"]
    monkeypatch.undo()
    one = _weighted(monkeypatch, "kelly075_ls_fast_hedged_static_acc_lp", 1.)[2]
    base_arm = _arm(BASE)
    base = _study(base_arm)[2]._method(base_arm, None)
    assert one["decisions"][0]["selection"]["quantities"] == base["decisions"][0]["selection"]["quantities"]
    drop = ("edge_multiplier", "expected_raw")
    assert ([{k: v for k, v in t.items() if k not in drop} for t in one["trades"]]
            == [{k: v for k, v in t.items() if k not in drop} for t in base["trades"]])
    assert all(b["expected_raw"] == b["expected"] for b in one["decisions"][0]["books"])


def test_the_name_spread_multiplies_every_names_charge_and_nothing_else():
    from dskit.pipeline.libs.pyomo import TransportActionScore
    scored = []
    real = TransportActionScore.__init__

    def spy(scorer, program, *a, **k):
        scored.append(copy.deepcopy(program))
        real(scorer, program, *a, **k)
    TransportActionScore.__init__ = spy
    try:
        arm = _arm("kelly075_ls_fast_hedged_static_acc_names_lp")
        result = _study(arm)[2]._method(arm, None)
    finally:
        TransportActionScore.__init__ = real
    program = scored[-1]
    q = result["decisions"][0]["selection"]["quantities"]
    plain = {**program, "utility": {k: v for k, v in program["utility"].items() if k != "charge_scale"}}
    with_scale, without = TransportActionScore(program, {}).value(q), TransportActionScore(plain, {}).value(q)
    assert any(c > 0 for c in without["utility"]["exact_charges"])
    assert with_scale["utility"]["exact_charges"] == pytest.approx([2.*c for c in without["utility"]["exact_charges"]])
    assert with_scale["utility"]["wealth"] == without["utility"]["wealth"]
    assert with_scale["utility"]["envelope"] == without["utility"]["envelope"]
    for bad in (.5, float("inf"), "2"):
        with pytest.raises(ValueError, match="invalid utility block"):
            TransportActionScore({**program, "utility": {**program["utility"], "charge_scale": bad}}, {})


@pytest.mark.parametrize("name", sorted(KNOBS))
def test_a_knob_week_is_deterministic_and_replays_from_record_and_worker_and_by_direct_scoring(name):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    from index_options.mio_worker import MIOMethodWorker

    arm = _arm(name)
    _, _, study = _study(arm)
    result = study._method(arm, None)
    again = _study(arm)[2]._method(arm, None)
    assert again["trades"] == result["trades"]
    assert [w.get("selection") for w in again["decisions"]] == [w.get("selection") for w in result["decisions"]]
    assert result["decisions"][0]["kelly"]["edge_weights"]["accuracy"]["no_history"] is True
    assert study._method(arm, None, recorded=result) == result
    claim = MIOMethodWorker.actions(result, study.weeks)
    replay = study._method(arm, None, derived=claim)
    assert replay["trades"] == result["trades"] and replay["decisions"][0]["books"] == result["decisions"][0]["books"]
    # replay_keep.py's fast route: the program the search scored values the recorded picks at the recorded objective
    seen = []
    real = TransportActionScore.__init__

    def spy(scorer, program, *a, **k):
        seen.append(copy.deepcopy(program))
        real(scorer, program, *a, **k)
    TransportActionScore.__init__ = spy
    try:
        _study(arm)[2]._method(arm, None)
    finally:
        TransportActionScore.__init__ = real
    picks = claim["decisions"][study.weeks[0]]["actions"]
    value = TransportActionScore(seen[-1], {"parity_tolerance": 1e-6}).evaluate(picks)["utility"]["exact"]
    assert math.isclose(value, claim["decisions"][study.weeks[0]]["certificates"][0]["objective"],
                        rel_tol=1e-6, abs_tol=1e-6)
    wrong = copy.deepcopy(claim)
    actions = wrong["decisions"][study.weeks[0]]["actions"]
    if any(actions[0]):
        actions[0][next(i for i, q in enumerate(actions[0]) if q)] += 1
        with pytest.raises(Exception, match="differ"):
            study._method(arm, None, derived=wrong)


# --- the two-scale worst-case edge (robust_scale): the lower of the forecast's and a jump-robust scale's value -----

ROBUST = {"reference": {"kind": "rms", "window": 22}, "robust": {"kind": "rms_trimmed", "window": 22, "drop": 1}}
DECISION = "2019-01-02"


def _robust_arm(**extra):
    return _arm(BASE, {"robust_scale": copy.deepcopy(ROBUST), **extra})


def _shape_closes(inputs, returns_before):
    """Rewrite the closes so the returns into the sessions up to the decision day are ``returns_before`` (oldest
    first, the last one ending on the decision day); the decision close and everything after it are unchanged."""
    bars = sorted(inputs["X"]["bars"], key=lambda r: r["date"])
    at = next(i for i, r in enumerate(bars) if r["date"] == DECISION)
    level = bars[at]["close"]
    for k, r in enumerate(reversed(returns_before)):
        bars[at-k-1]["close"] = level = level/math.exp(r)


EQUAL = [.01*(-1)**i for i in range(40)]                     # no dominant day: every return the same magnitude
JUMP = [.004*(-1)**i for i in range(39)] + [.08]              # one day carries most of the 22-day variance


def _robust_study(returns, arm=None):
    arm = arm or _robust_arm()
    params, inputs = _hedged_world()
    _shape_closes(inputs, returns)
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    return arm, inputs, ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def test_the_module_under_test_is_this_checkout():
    import pathlib
    import dskit.pipeline.libs.vol_estimators as ve
    here = pathlib.Path(__file__).resolve().parents[3]
    assert pathlib.Path(ledger_studies.__file__).resolve().is_relative_to(here)
    assert pathlib.Path(ve.__file__).resolve().is_relative_to(here)


def test_a_robust_scale_method_validates_and_a_malformed_one_refuses():
    params, _ = _hedged_world()
    params.update(methods=[_robust_arm()], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for bad in ({"reference": ROBUST["reference"]}, {**ROBUST, "extra": 1}, "rms",
                {**ROBUST, "robust": {"kind": "rms_trimmed", "window": 21, "drop": 1}},   # not the same window
                {**ROBUST, "robust": {"kind": "high_low", "window": 22}},                 # reads no close
                {**ROBUST, "robust": {"kind": "rms_trimmed", "window": 22}},
                {**ROBUST, "robust": dict(ROBUST["reference"])}):                         # the same label
        params.update(methods=[_arm(BASE, {"robust_scale": bad})])
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_scale_ratio_is_one_without_a_dominant_day_and_below_one_with_one():
    _, _, flat = _robust_study(EQUAL)
    _, _, jump = _robust_study(JUMP)
    ratio, _ = EdgeWeights(flat, flat.params["methods"][0]["kelly"]).scale_ratio("X", DECISION)
    assert ratio == pytest.approx(1., abs=1e-9)
    ratio, _ = EdgeWeights(jump, jump.params["methods"][0]["kelly"]).scale_ratio("X", DECISION)
    window = JUMP[-22:]
    full, rest = sum(r*r for r in window)/22, (sum(r*r for r in window)-max(r*r for r in window))/21
    assert ratio == pytest.approx(math.sqrt(rest/full), rel=1e-8) and ratio < .5


def test_the_scale_ratio_reads_no_bar_after_the_decision_day():
    _, _, study = _robust_study(JUMP)
    before = EdgeWeights(study, study.params["methods"][0]["kelly"]).scale_ratio("X", DECISION)
    for day, close in study._bars["X"].items():
        if day > DECISION:
            study._bars["X"][day] = close*(3. if day.endswith("3") else .2)
    assert EdgeWeights(study, study.params["methods"][0]["kelly"]).scale_ratio("X", DECISION) == before


def test_a_short_history_is_unestimated():
    _, _, study = _robust_study(JUMP)
    first = study.history_calendar[10]
    ratio, reason = EdgeWeights(study, study.params["methods"][0]["kelly"]).scale_ratio("X", first)
    assert ratio is None and reason == "short_history"


def _week_groups(study, arm):
    groups, metadata, _ = study._prepare(DECISION)
    forwards = ledger_studies.ParityForwards(study, study.params["hedge_parity"])
    return [dict(g) for g in groups], metadata, EdgeWeights(study, arm["kelly"], forwards)


def test_the_robust_adjustment_is_never_positive_and_cuts_a_long_out_of_the_money_call():
    arm, _, study = _robust_study(JUMP)
    configured, metadata, edge = _week_groups(study, arm)
    out, record, multipliers = edge.apply(DECISION, configured, metadata)
    long_calls = []
    for group, meta in zip(out, metadata, strict=True):
        assert all(a <= 0. for a in group["value_adjust"]), "it can remove edge, never create it"
        for item, a in zip(meta["items"], group["value_adjust"]):
            otm_call = item["right"] == "call" and item["strike"] > meta["spot"]*1.02 and not item.get("hedged")
            if otm_call and item["sign"] == 1:
                long_calls.append(a)
            if otm_call and item["sign"] == -1:
                assert a == 0., "a sold one is worth more on the narrower scale: its forecast value stands"
    assert long_calls and min(long_calls) < 0, "a long out-of-the-money call is worth less on the narrower scale"
    robust = record["robust_scale"]
    assert robust["groups"] == len(out) and robust["scaled"] == len(out) and robust["unestimated"] == {}
    assert robust["items_cut"] > 0 and robust["cut_total"] < 0 and robust["ratio"]["X"] < .5
    assert robust["reference"] == "rms_22" and robust["robust"] == "rms_trim1_22"
    assert set(multipliers.values()) == {1.}


def test_with_no_dominant_day_the_adjustment_is_zero():
    arm, _, study = _robust_study(EQUAL)
    configured, metadata, edge = _week_groups(study, arm)
    out, record, _ = edge.apply(DECISION, configured, metadata)
    assert all(abs(a) < 1e-9 for g in out for a in g["value_adjust"])
    assert record["robust_scale"]["scaled"] == 0 and record["robust_scale"]["items_cut"] == 0


def test_the_cut_is_the_robust_projections_value_less_the_forecasts_times_the_multiplier():
    arm, _, study = _robust_study(JUMP, _robust_arm(dte_tilt=.5))
    configured, metadata, edge = _week_groups(study, arm)
    out, _, multipliers = edge.apply(DECISION, configured, metadata)
    ratio, _ = edge.scale_ratio("X", DECISION)
    node = study._nodes["X"]
    for group, meta, plain in zip(out, metadata, configured, strict=True):
        forecast = study._forecasts["X"][DECISION, meta["expiry"]]
        scale = float(study._contexts["X"][DECISION, meta["expiry"]]["reference_scale"])*ratio
        masses = node._project(node._method_cdf(forecast, node.CDF_NOISE)[0], meta["spot"], scale,
                               plain["grid"]).masses
        robust = _expected({**plain, "masses": list(masses)})
        m = multipliers[group["id"]]
        assert m < 1.
        want = [(m-1.)*e+m*min(0., r-e) for e, r in zip(_expected(plain), robust)]
        assert group["value_adjust"] == pytest.approx(want, abs=1e-9)


def test_a_robust_week_runs_records_its_cut_and_replays_from_its_record():
    arm, _, study = _robust_study(JUMP)
    result = study._method(arm, None)
    week = result["decisions"][0]
    assert week["kelly"]["edge_weights"]["robust_scale"]["items_cut"] > 0 and week["books"]
    assert any(book["robust_cut"] < 0 for book in week["books"])
    for book in week["books"]:
        assert book["robust_cut"] <= 0. and book["edge_multiplier"] == 1.
        assert book["expected_raw"] == pytest.approx(book["expected"]-book["robust_cut"], abs=1e-9)
    assert study._method(arm, None, recorded=result) == result


def test_a_tilted_books_robust_cut_carries_the_multiplier():
    arm, _, study = _robust_study(JUMP, _robust_arm(dte_tilt=.5))
    configured, metadata, edge = _week_groups(study, arm)
    out, _, multipliers = edge.apply(DECISION, configured, metadata)
    raw = EdgeWeights(study, _robust_arm()["kelly"]).apply(DECISION, [dict(g) for g in configured], metadata)[0]
    q = [[1]*len(g["credit"]) for g in out]
    books = [{"id": g["id"]} for g in out]
    edge.stamp(books, metadata, q)
    for book, g, plain in zip(books, out, raw, strict=True):
        m = multipliers[g["id"]]
        assert m < 1. and book["robust_cut"] == pytest.approx(m*sum(plain["value_adjust"]), abs=1e-9)


def test_a_refused_robust_projection_cuts_every_positive_edge_to_zero(monkeypatch):
    arm, _, study = _robust_study(JUMP)
    configured, metadata, edge = _week_groups(study, arm)

    def refuse(*a, **k):
        raise ValueError("payoff tolerance")
    monkeypatch.setattr(type(study._nodes["X"]), "_project", refuse)
    out, record, _ = edge.apply(DECISION, configured, metadata)
    for group, plain in zip(out, configured, strict=True):
        assert group["value_adjust"] == [-max(0., e) for e in _expected(plain)]
    assert record["robust_scale"]["unprojected"] == len(out)


def test_with_the_scales_agreeing_the_robust_method_selects_what_the_base_selects():
    arm, _, study = _robust_study(EQUAL)
    base_arm = _arm(BASE)
    _, _, base_study = _robust_study(EQUAL, base_arm)
    robust, base = study._method(arm, None), base_study._method(base_arm, None)
    assert robust["decisions"][0]["selection"]["quantities"] == base["decisions"][0]["selection"]["quantities"]
    drop = ("edge_multiplier", "expected_raw", "robust_cut")
    assert ([{k: v for k, v in t.items() if k not in drop} for t in robust["trades"]]
            == [{k: v for k, v in t.items() if k not in drop} for t in base["trades"]])


#: sha256 (:func:`_digest`) of the scored programs and the stable result of the robust-only week, recorded on 12597d1c
#: (before ``forward_centre`` existed) by this test's own body: a robust-only method is unchanged.
ROBUST_ONLY = {
    "plain": ("bf7f6b2702fc8b2b7d2e7701af84d9836583322460961be349ca38978d43b668",
              "cea1a862188f69512158d669aac682d26dcbaf17f4e4546512c52522ed94794e"),
    "tilted": ("f41af62f7b52034730ff3c3d6cecdbc8442116935c2a05f6b3893a75b73add2c",
               "5093ea59c0894518c4cf6b34a169644ecaa77e3348ef0e7207ee704241e5ca5a")}


@pytest.mark.parametrize("key", sorted(ROBUST_ONLY))
def test_a_robust_only_method_is_byte_for_byte_what_it_was(key, monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    seen, real = [], TransportActionScore.__init__

    def spy(scorer, program, *a, **k):
        seen.append(program)
        real(scorer, program, *a, **k)
    monkeypatch.setattr(TransportActionScore, "__init__", spy)
    arm, _, study = _robust_study(JUMP, _robust_arm(**({"dte_tilt": .5} if key == "tilted" else {})))
    result = study._method(arm, None)
    assert (_digest(seen), _digest(_stable(result))) == ROBUST_ONLY[key]
    assert all("forward_cut" not in book for week in result["decisions"] for book in week.get("books", []))


# --- the forward-centred worst-case edge (forward_centre): the lower of the value centred on spot and on the forward --

SPOT = 100.


def _forward(monkeypatch, ratio, source="parity"):
    """Make every parity forward ``ratio`` times its spot (``source`` ``"spot"`` at ratio 1 is the no-pair case)."""
    def forward(self, symbol, day, expiry, factor, exclude=()):
        spot = self.spot(symbol, day, factor)
        return (None, "no_spot") if spot is None else (spot*ratio, source)
    monkeypatch.setattr(ledger_studies.ParityForwards, "forward", forward)


def _centre_arm(**extra):
    return _arm(BASE, {"forward_centre": True, **extra})


def _centre_week(returns=EQUAL, arm=None):
    arm = arm or _centre_arm()
    _, _, study = _robust_study(returns, arm)
    configured, metadata, edge = _week_groups(study, arm)
    return arm, study, configured, metadata, edge


def _masses(study, meta, plain, centre, scale_ratio=1.):
    node = study._nodes["X"]
    forecast = study._forecasts["X"][DECISION, meta["expiry"]]
    scale = float(study._contexts["X"][DECISION, meta["expiry"]]["reference_scale"])*scale_ratio
    return list(node._project(node._method_cdf(forecast, node.CDF_NOISE)[0], centre, scale, plain["grid"]).masses)


def test_a_forward_centre_method_validates_and_a_malformed_or_unhedged_one_refuses():
    params, _ = _hedged_world()
    params.update(methods=[_centre_arm()], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    params.update(methods=[_centre_arm(robust_scale=copy.deepcopy(ROBUST))])
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for bad in (False, 1, "yes", {}, None):
        params.update(methods=[_arm(BASE, {"forward_centre": bad})])
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    unhedged = {k: v for k, v in _centre_arm().items() if k != "hedge"}
    replay = {**_centre_arm(), "hedge": {**_centre_arm()["hedge"], "mode": "replay"}}
    assert ledger_studies.WeeklyOptionPortfolioStudy._kelly_valid({**_centre_arm()})
    assert not ledger_studies.WeeklyOptionPortfolioStudy._kelly_valid(unhedged), "the forward is the hedge's"
    assert not ledger_studies.WeeklyOptionPortfolioStudy._kelly_valid(replay), "only a program hedge prices the items"


def test_with_the_forward_at_spot_the_adjustment_is_zero(monkeypatch):
    for source in ("spot", "parity"):
        _forward(monkeypatch, 1., source)
        _, _, configured, metadata, edge = _centre_week()
        out, record, _ = edge.apply(DECISION, configured, metadata)
        assert all(a == 0. for g in out for a in g["value_adjust"])
        centre = record["forward_centre"]
        assert centre["agree"] == len(out) and centre["shifted"] == 0 and centre["items_cut"] == 0
        assert centre["cut_total"] == 0.


@pytest.mark.parametrize("ratio", [.9, 1.1])
def test_the_adjustment_is_never_positive_and_cuts_the_bet_on_spot_against_the_forward(monkeypatch, ratio):
    """Forward below spot: a sold in-the-money put (a synthetic long) and a bought call lose edge, a bought put gains
    none; forward above spot, the mirror."""
    _forward(monkeypatch, ratio)
    _, study, configured, metadata, edge = _centre_week()
    out, record, _ = edge.apply(DECISION, configured, metadata)
    long_bet, short_bet, kept = [], [], []
    for group, meta, plain in zip(out, metadata, configured, strict=True):
        assert all(a <= 0. for a in group["value_adjust"]), "it can remove edge, never create it"
        for item, a in zip(meta["items"], group["value_adjust"]):
            below = ratio < 1
            itm_short = item["sign"] == -1 and (item["right"] == "put" if below else item["right"] == "call") and (
                item["strike"] > SPOT*1.02 if below else item["strike"] < SPOT*.98)
            # a strike at the grid's outer point pays nothing on the grid whatever the centre
            inside = plain["grid"][0] < item["strike"] < plain["grid"][-1]
            bought_with = item["sign"] == 1 and item["right"] == ("call" if below else "put") and inside
            bought_against = item["sign"] == 1 and item["right"] == ("put" if below else "call")
            (long_bet if itm_short else short_bet if bought_with else kept if bought_against else []).append(a)
    assert long_bet and max(long_bet) < 0, "the sold in-the-money option bets on spot against the forward"
    assert short_bet and max(short_bet) < 0, "the bought option on the forward's wrong side loses edge"
    assert kept and all(a == 0. for a in kept), "the option the forward favours gains nothing"
    centre = record["forward_centre"]
    assert centre["shifted"] == len(out) and centre["items_cut"] > 0 and centre["cut_total"] < 0
    assert centre["ratio_min"] == centre["ratio_max"] == pytest.approx(ratio)


def test_the_cut_is_the_forward_centred_projection_less_the_forecast_times_the_multiplier(monkeypatch):
    _forward(monkeypatch, .9)
    _, study, configured, metadata, edge = _centre_week(arm=_centre_arm(dte_tilt=.5))
    out, _, multipliers = edge.apply(DECISION, configured, metadata)
    for group, meta, plain in zip(out, metadata, configured, strict=True):
        shifted = _expected({**plain, "masses": _masses(study, meta, plain, meta["spot"]*.9)})
        m = multipliers[group["id"]]
        assert m < 1.
        want = [(m-1.)*e+m*min(0., f-e) for e, f in zip(_expected(plain), shifted)]
        assert group["value_adjust"] == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("ratio", [.9, 1.1])
def test_with_both_keys_the_value_is_the_least_of_the_four_valuations(monkeypatch, ratio):
    _forward(monkeypatch, ratio)
    arm = _centre_arm(robust_scale=copy.deepcopy(ROBUST))
    _, study, configured, metadata, edge = _centre_week(JUMP, arm)
    out, record, _ = edge.apply(DECISION, configured, metadata)
    r, _ = edge.scale_ratio("X", DECISION)
    assert r < .5
    q = [[1]*len(g["credit"]) for g in out]
    books = [{"id": g["id"]} for g in out]
    edge.stamp(books, metadata, q)
    for group, meta, plain, book in zip(out, metadata, configured, books, strict=True):
        spot = meta["spot"]
        values = [_expected({**plain, "masses": _masses(study, meta, plain, c, s)})
                  for c, s in ((spot, r), (spot*ratio, 1.), (spot*ratio, r))]
        e = _expected(plain)
        want = [min(0., min(v[j] for v in values)-e[j]) for j in range(len(e))]
        assert group["value_adjust"] == pytest.approx(want, abs=1e-9)
        robust_only = [min(0., values[0][j]-e[j]) for j in range(len(e))]
        assert book["robust_cut"] == pytest.approx(sum(robust_only), abs=1e-9)
        assert book["forward_cut"] == pytest.approx(sum(want)-sum(robust_only), abs=1e-9)
        assert book["forward_cut"] <= 0.
    assert record["robust_scale"]["items_cut"] > 0 and record["forward_centre"]["items_cut"] > 0


def test_the_forward_reads_nothing_after_the_decision_day(monkeypatch):
    """The real parity forward of the decision-day chain: rows and closes after the day do not move the cut."""
    def run(mutate):
        params, inputs = _hedged_world(False)
        _shape_closes(inputs, EQUAL)
        if mutate:
            for row in inputs["X"]["chain"]:
                if row["date"] > DECISION:
                    row["bid"], row["ask"] = row["bid"]*3+1, row["ask"]*3+1
            for row in inputs["X"]["bars"]:
                if row["date"] > DECISION:
                    row["close"] *= .5
        arm = _centre_arm()
        params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        configured, metadata, edge = _week_groups(study, arm)
        return edge.apply(DECISION, configured, metadata), study, configured, metadata, edge
    (before, study, configured, metadata, edge), (after, *_rest) = run(False), run(True)
    assert before[1]["forward_centre"]["shifted"] > 0, "the world's decision chain has a parity forward off spot"
    # the real owner's forward: the decision chain's parity forward 100.1 on a close of 100 (strike basis 1)
    assert edge.forward_ratio(metadata[0], DECISION) == (pytest.approx(1.001, abs=1e-9), None)
    for group, meta, plain in zip(before[0], metadata, configured, strict=True):
        shifted = _expected({**plain, "masses": _masses(study, meta, plain, meta["spot"]*1.001)})
        assert group["value_adjust"] == pytest.approx([min(0., f-e) for e, f in zip(_expected(plain), shifted)],
                                                      abs=1e-9)
    assert [g["value_adjust"] for g in before[0]] == [g["value_adjust"] for g in after[0]]
    assert before[1] == after[1]


def test_without_a_forward_the_week_trades_at_forecast_value_and_says_why(monkeypatch):
    monkeypatch.setattr(ledger_studies.ParityForwards, "forward",
                        lambda self, symbol, day, expiry, factor, exclude=(): (None, "duplicate_quote"))
    _, _, configured, metadata, edge = _centre_week()
    out, record, _ = edge.apply(DECISION, configured, metadata)
    assert all(a == 0. for g in out for a in g["value_adjust"])
    assert record["forward_centre"]["unestimated"] == {"duplicate_quote": len(out)}


def test_a_refused_forward_projection_cuts_every_positive_edge_to_zero(monkeypatch):
    _forward(monkeypatch, .9)
    _, study, configured, metadata, edge = _centre_week()

    def refuse(*a, **k):
        raise ValueError("payoff tolerance")
    monkeypatch.setattr(type(study._nodes["X"]), "_project", refuse)
    out, record, _ = edge.apply(DECISION, configured, metadata)
    for group, plain in zip(out, configured, strict=True):
        assert group["value_adjust"] == [-max(0., e) for e in _expected(plain)]
    assert record["forward_centre"]["unprojected"] == len(out)


def test_a_forward_centre_week_runs_records_its_cut_and_replays_from_its_record(monkeypatch):
    _forward(monkeypatch, .9)
    arm, _, study = _robust_study(EQUAL, _centre_arm())
    result = study._method(arm, None)
    week = result["decisions"][0]
    assert week["kelly"]["edge_weights"]["forward_centre"]["items_cut"] > 0
    assert "robust_scale" not in week["kelly"]["edge_weights"]
    for book in week["books"]:
        assert book["forward_cut"] <= 0. and "robust_cut" not in book
        assert book["expected_raw"] == pytest.approx(book["expected"]-book["forward_cut"], abs=1e-9)
    assert week["books"] and any(book["forward_cut"] < 0 for book in week["books"])
    assert study._method(arm, None, recorded=result) == result


@pytest.mark.parametrize("tilt", [None, .5])
def test_a_both_keys_week_stamps_both_cuts_and_replays_from_its_record(monkeypatch, tilt):
    _forward(monkeypatch, .9)
    extra = {"robust_scale": copy.deepcopy(ROBUST), **({} if tilt is None else {"dte_tilt": tilt})}
    arm, _, study = _robust_study(JUMP, _centre_arm(**extra))
    result = study._method(arm, None)
    week = result["decisions"][0]
    assert week["kelly"]["edge_weights"]["forward_centre"]["shifted"] > 0
    assert week["kelly"]["edge_weights"]["robust_scale"]["scaled"] > 0 and week["books"]
    for book in week["books"]:
        assert book["robust_cut"] <= 0. and book["forward_cut"] <= 0.
        if tilt is None:
            assert book["expected_raw"] == pytest.approx(book["expected"]-book["robust_cut"]-book["forward_cut"],
                                                         abs=1e-9)
    assert study._method(arm, None, recorded=result) == result


def test_with_the_forward_at_spot_the_forward_centre_method_selects_what_the_base_selects(monkeypatch):
    _forward(monkeypatch, 1., "spot")
    robust = {"robust_scale": copy.deepcopy(ROBUST)}
    for extra, base_kelly in (({}, None), (robust, robust)):
        arm, _, study = _robust_study(JUMP, _centre_arm(**extra))
        base_arm = _arm(BASE, base_kelly)
        _, _, base_study = _robust_study(JUMP, base_arm)
        centred, base = study._method(arm, None), base_study._method(base_arm, None)
        assert centred["decisions"][0]["selection"]["quantities"] == base["decisions"][0]["selection"]["quantities"]
        drop = ("edge_multiplier", "expected_raw", "forward_cut")
        assert ([{k: v for k, v in t.items() if k not in drop} for t in centred["trades"]]
                == [{k: v for k, v in t.items() if k not in drop} for t in base["trades"]])


# --- a hard per-name reserve cap on a kelly method (name_fraction below 1) --------------------------------------------

NAME_CAP = .35


def _capped_arm(fraction=NAME_CAP):
    return {**_arm(BASE), "name_fraction": fraction}


def test_a_kelly_method_may_declare_a_name_fraction_below_one():
    params, _ = _hedged_world()
    for fraction in (NAME_CAP, 1.):
        params.update(methods=[_capped_arm(fraction)], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for fraction in (0., 1.5, float("nan")):
        params.update(methods=[_capped_arm(fraction)])
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_kelly_limits_add_one_row_per_name_only_when_the_cap_is_declared():
    account = SimpleNamespace(funding=1000., open={"a": {"symbol": "X", "reserve": 100.}},
                              remaining=lambda members, f: 1000.*f-(100. if "X" in members else 0.))
    study = SimpleNamespace(params={"symbols": ["X", "Y"]}, _kelly=ledger_studies.WeeklyOptionPortfolioStudy._kelly,
                            _sizer=ledger_studies.WeeklyOptionPortfolioStudy._sizer)
    limits = ledger_studies.WeeklyOptionPortfolioStudy._limits
    plain = _arm(BASE)
    assert limits(study, account, plain, None) == [{"members": ["X", "Y"], "upper": 900.}]
    assert limits(study, account, plain, None, solve=True) == [{"members": ["X", "Y"], "upper": 850.}]
    for solve, total in ((False, 900.), (True, 850.)):
        assert limits(study, account, _capped_arm(), None, solve=solve) == [
            {"members": ["X", "Y"], "upper": total}, {"members": ["X"], "upper": pytest.approx(250.)},
            {"members": ["Y"], "upper": pytest.approx(350.)}]


def test_a_capped_kelly_week_never_holds_more_than_the_cap_in_one_name(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    seen = []
    real = TransportActionScore.__init__

    def spy(scorer, program, *a, **k):
        seen.append(copy.deepcopy(program))
        real(scorer, program, *a, **k)
    monkeypatch.setattr(TransportActionScore, "__init__", spy)
    base_arm, arm = _arm(BASE), _capped_arm()
    base = _study(base_arm)[2]._method(base_arm, None)
    funding = base["decisions"][0]["funding"]
    assert base["trades"][0]["reserve"] > NAME_CAP*funding, "uncapped, the one name takes most of the funding"
    seen.clear()
    result = _study(arm)[2]._method(arm, None)
    week = result["decisions"][0]
    scored = [p for p in seen if "utility" in p]
    assert scored and all(any(c["upper"] == pytest.approx(NAME_CAP*funding) for c in p["caps"]) for p in scored)
    assert week["status"] == "selected" and result["trades"]
    assert sum(b["reserve"] for b in week["books"]) <= NAME_CAP*funding+1e-6
    assert sum(t["reserve"] for t in result["trades"] if t["symbol"] == "X") <= NAME_CAP*funding+1e-6


def test_the_fill_refuses_a_book_over_the_name_cap():
    arm = _capped_arm()
    _, _, study = _study(arm)
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    p = study.params
    account = FundedOptionAccount(p["initial"], p["deposit"], p["deposit_every_days"], study.calendar[0])
    day = study.calendar[1]
    account.advance(day, {})
    book = {"id": "b", "symbol": "X", "expiry": "2019-02-01", "reserve": NAME_CAP*account.funding+1., "credit": -1.}
    assert not account.admit(day, [book], study._limits(account, arm, {"groups": []}))
    assert account.admit(day, [{**book, "reserve": NAME_CAP*account.funding-1.}],
                         study._limits(account, arm, {"groups": []}))


def test_a_stock_margin_of_one_half_validates_and_scales_the_hedged_margin_floor(monkeypatch):
    """Configuration only: ``hedge.margin_fraction`` .5 in place of .15 raises each hedged item's margin floor row."""
    floors = {}
    for margin in (.15, .5):
        arm = _arm(BASE)
        arm["hedge"]["margin_fraction"] = margin
        scored, _ = _spy(monkeypatch)
        params, _, study = _study(arm)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
        study._method(arm, None)
        group = next(p for p in scored if "utility" in p)["groups"][0]
        floors[margin] = group["reserve_rows"][-2]
        monkeypatch.undo()
    assert any(floors[.15])
    assert floors[.5] == pytest.approx([v*.5/.15 for v in floors[.15]])


# --- ADR-0323 and ADR-0324 beside the other keys: no sold in-the-money option and premium cover -----------------------

def test_both_new_keys_run_beside_the_exercise_robust_tilt_settleable_and_name_cap_keys_and_replay(monkeypatch):
    """Every key on one hedged arm: the week records both exercise and moneyness cuts, each program group carries the
    cover row after the hedge's slope row, no book sells in the money, and the week replays from its record."""
    scored, _ = _spy(monkeypatch)
    arm = {**_robust_arm(dte_tilt=.5, short_call_above_exercise=True, short_not_in_the_money=True, premium_cover=1.,
                         settleable_only=True), "name_fraction": NAME_CAP}
    _, _, study = _robust_study(JUMP, arm)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(study.params)
    result = study._method(arm, None)
    week = result["decisions"][0]
    assert {"short_call_above_exercise", "short_not_in_the_money"} <= set(week)
    assert week["short_not_in_the_money"] > 0 and week["books"], "the world's in-the-money items are cut, a book trades"
    groups =[g for p in scored if "utility" in p for g in p["groups"]]
    assert groups and all(len(g["linear_rows"]) == 2 for g in groups), "the hedge's slope row, then the cover row"
    for book in week.get("books", []):
        spot = book["spot"]
        assert not any(leg["sign"] < 0 and ((leg["right"] == "put" and leg["strike"] > spot)
                                            or (leg["right"] == "call" and leg["strike"] < spot))
                       for leg in book["legs"])
    assert study._method(arm, None, recorded=result) == result


# --- ADR-0325: the credit floor on the hedged program ---------------------------------------------------------------

FLOOR = .05


def _ratio(trade):
    return trade["decision_credit"] / trade["max_loss"]


def test_a_thin_naked_put_the_plain_search_picks_is_redeployed_to_a_book_over_the_floor():
    """The fair world's best unconstrained book is a naked put collecting under 5% of its worst-case loss."""
    _, _, plain_study = _study(_arm(BASE), cheap_call=False)
    plain = plain_study._method(_arm(BASE), None)
    assert plain["trades"] and all(_ratio(t) < FLOOR for t in plain["trades"]), "premise: the plain book is thin"
    arm = _arm(BASE, {"credit_floor": FLOOR})
    _, _, study = _study(arm, cheap_call=False)
    result = study._method(arm, None)
    assert result["trades"], "the floor redeploys, it does not empty the week"
    assert all(_ratio(t) >= FLOOR for t in result["trades"])
    assert [b["legs"] for b in result["decisions"][0]["books"]] != [b["legs"] for b in plain["decisions"][0]["books"]]
    assert study._method(arm, None, recorded=result) == result


def test_on_a_hedged_book_the_rows_max_loss_is_the_programs_hedged_loss_without_the_hedge_costs(monkeypatch):
    """The cheap-call world trades hedged twins: the floor rows at the chosen quantities read the hedged max loss."""
    scored, _ = _spy(monkeypatch)
    rho = 1e-3
    arm = _arm(BASE, {"credit_floor": rho})
    _, _, study = _study(arm)
    result = study._method(arm, None)
    (book,) = result["decisions"][0]["books"]
    assert any(leg.get("hedged") for leg in book["legs"]) and book["program_hedge"]["cost_usd"] > 0
    (group,) = [g for g in scored[-1]["groups"] if g["id"] == book["id"]]
    (quantities,) = [q for q, gid in zip(result["decisions"][0]["selection"]["quantities"],
                                         [g["id"] for g in scored[-1]["groups"]]) if gid == book["id"]]
    strikes = {leg["strike"] for leg in book["legs"]}
    assert len(group["linear_rows"]) >= 2 + len(strikes), "the slope row, then one floor row per kink"
    activity = max(sum(c * q for c, q in zip(row["coefficients"], quantities, strict=True))
                   for row in group["linear_rows"][1:])
    loss = book["max_loss"] - book["program_hedge"]["cost_usd"]      # the decision book's loss carries the costs
    assert activity == pytest.approx(rho * loss - book["credit"], rel=1e-9)


def test_the_floor_runs_beside_every_other_kelly_key_and_replays(monkeypatch):
    """Cover, moneyness, exercise, robust scale, name cap, settleable, dte tilt, a 0.9 wealth floor, longer moves."""
    scored, _ = _spy(monkeypatch)
    moves = [-.3, -.25, -.2, *_arm(BASE)["kelly"]["moves"], .2, .25, .3]
    arm = {**_robust_arm(dte_tilt=.5, short_call_above_exercise=True, short_not_in_the_money=True, premium_cover=1.,
                         settleable_only=True, credit_floor=FLOOR, wealth_floor=.9, moves=moves),
           "name_fraction": NAME_CAP}
    _, _, study = _robust_study(JUMP, arm)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(study.params)
    result = study._method(arm, None)
    week = result["decisions"][0]
    groups = [g for p in scored if "utility" in p for g in p["groups"]]
    assert groups and all(len(g["linear_rows"]) > 2 for g in groups), "slope, cover, then the floor rows"
    assert week["books"] and result["trades"]
    # The world's hedged synthetic cannot lose (a negative max loss), which any floor admits.
    assert all(t["decision_credit"] >= FLOOR * t["max_loss"] and t["decision_credit"] > 0 for t in result["trades"])
    assert study._method(arm, None, recorded=result) == result
