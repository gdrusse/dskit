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


def _hand(rows):
    ab = aa = 0.
    for cdf, y in rows:
        a = [f-g for f, g in zip(cdf, G)]
        b = [(1. if y <= z else 0.)-g for z, g in zip(Z, G)]
        ab += sum(x*v for x, v in zip(a, b))
        aa += sum(x*x for x in a)
    return ab, aa


def test_a_sharp_right_forecast_weighs_one_a_wrong_one_zero_and_the_reference_itself_one():
    right = [("A", "2019-01-02", "2019-01-04", [0., 1., 1.], -.5)]   # the outcome's own step
    wrong = [("A", "2019-01-02", "2019-01-04", [1., 1., 1.], .5)]    # all mass below an outcome above zero
    same = [("A", "2019-01-02", "2019-01-04", G, .5)]
    for rows, expected in ((right, 1.), (wrong, 0.), (same, 1.)):
        w, record = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 1}}).accuracy("2019-01-07", ["A"])
        assert w == {"A": expected} and record["scored_rows"] == 1 and not record["no_history"]


def test_only_rows_settled_and_quoted_strictly_before_the_day_count():
    rows = [("A", "2019-01-02", "2019-01-04", [.2, .7, .9], .3),
            ("A", "2019-01-03", "2019-01-07", [.9, .95, 1.], .8),    # settles ON the day: unread
            ("A", "2019-01-07", "2019-01-11", [.0, .0, .0], -2.)]    # quoted on the day, settles after: unread
    weights = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 5}})
    w, record = weights.accuracy("2019-01-07", ["A"])
    ab, aa = _hand([([.2, .7, .9], .3)])
    assert record["scored_rows"] == 1 and w["A"] == round(min(1., max(0., (ab+5*ab)/(aa+5*aa))), 9)
    # a later day reads the second row too, and nothing on the first day moved when the future rows changed
    later, _ = weights.accuracy("2019-01-08", ["A"])
    ab2, aa2 = _hand([([.2, .7, .9], .3), ([.9, .95, 1.], .8)])
    assert later["A"] == round(min(1., max(0., (ab2+5*ab2/2)/(aa2+5*aa2/2))), 9)
    changed = [rows[0], ("A", "2019-01-03", "2019-01-07", [0., 0., 1.], -3.), rows[2]]
    assert EdgeWeights(_fake(changed), {"accuracy": {"shrink_rows": 5}}).accuracy("2019-01-07", ["A"])[0] == w


def test_a_name_is_shrunk_toward_the_pool_and_a_name_without_rows_takes_the_pooled_value():
    rows = [("A", "2019-01-02", "2019-01-04", [.1, .6, .95], .2), ("A", "2019-01-03", "2019-01-04", [.3, .5, .8], .2),
            ("B", "2019-01-02", "2019-01-04", [.6, .9, 1.], .4)]
    w, record = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-07", ["A", "B", "C"])
    a_ab, a_aa = _hand([([.1, .6, .95], .2), ([.3, .5, .8], .2)])
    b_ab, b_aa = _hand([([.6, .9, 1.], .4)])
    mean_ab, mean_aa = (a_ab+b_ab)/3, (a_aa+b_aa)/3
    bound = lambda ab, aa: round(min(1., max(0., ab/aa)), 9)
    assert w["A"] == pytest.approx(bound(a_ab+2*mean_ab, a_aa+2*mean_aa), abs=1e-9)
    assert w["B"] == pytest.approx(bound(b_ab+2*mean_ab, b_aa+2*mean_aa), abs=1e-9)
    assert w["C"] == record["pooled"] == pytest.approx(bound(mean_ab, mean_aa), abs=1e-9)
    assert record["fallback_pooled"] == 1 and record["names"] == 3 and record["min"] <= record["median"] <= record["max"]
    assert all(0. <= v <= 1. for v in w.values())


def test_a_week_with_no_scored_row_takes_the_forecast_at_face_value_and_says_so():
    rows = [("A", "2019-01-02", "2019-01-04", [.1, .6, .95], .2)]
    w, record = EdgeWeights(_fake(rows), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-04", ["A"])
    assert w == {"A": 1.} and record["no_history"] and record["scored_rows"] == 0 and record["pooled"] is None
    w, _ = EdgeWeights(_fake(rows, pairs=False), {"accuracy": {"shrink_rows": 2}}).accuracy("2019-01-07", ["A"])
    assert w == {"A": 1.}, "a row the method could not trade is not scored"


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
