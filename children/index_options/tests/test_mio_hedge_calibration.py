"""ADR-0313 stage I7: the hedge and the calibration in one method, then all three blocks (SYNTHETIC DATA ONLY).

Parity first (a method with one block is unchanged), then B2's order, ``hedge_needs_direction_map``,
``block_params_disagree`` and the hand-worked value term on a tiny program.
"""

import copy

import pytest

from index_options import ledger_studies
from test_ledger_studies import _timeless_digest
from test_mio_calibration import Harness, programs_of, reweighted, untimed

#: The direction params that agree with ``HEDGE`` below (rate 0, mid iv, the overlay's own floors and tolerance).
DIRECTION = {"rate": 0.0, "iv_source": "mid", "iv_disagree_tol": 0.05, "multiplier": 100, "min_years": 0.001,
             "vol_floor": 0.01, "parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5,
             "forward_se_floor_frac": 0.001}
PARITY = {"parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5}
HEDGE = {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0, "delta_source": "black76_chain_forward",
         "iv_source": "mid", "rate": 0.0, "margin_fraction": 0.15, "round_units": False, "condition": None,
         "small_delta": 0.0, "hedge_cost_source": "placeholder", "htb_lookback": 6, "htb_vol_threshold": 2.5,
         "htb_jump_threshold": 0.75, "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0,
         "hedge_borrow_rate": 0.005}
LEG = {"rate": 0.0, "iv_source": "mid", "min_years": 0.001, "vol_floor": 0.01, "iv_disagree_tol": 0.05}
LIMITS = {"beta_delta_fraction": 50.0, "vega_fraction_per_pt": 50.0, "dte_edges": [8, 22], "beta_lookback": 2,
          "scope": "account"}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return Harness(tmp_path_factory.mktemp("walk2"), direction=DIRECTION)


def build(world, methods, *, maps=None, summary=None, **over):
    """A study over ``methods`` with the document keys their blocks need (hedge_parity, exposure_greeks)."""
    if any("hedge" in m for m in methods):
        over.setdefault("hedge_parity", dict(PARITY))
    if any("exposure_limits" in m for m in methods):
        over.setdefault("exposure_greeks", {"leg": dict(LEG), "parity": dict(PARITY)})
    return world.study(methods, maps=maps, summary=summary, **over)


def hedged(world, mid="hedge_arm", **block):
    return {**world.base, "id": mid, "hedge": {**HEDGE, **block}}


def limited(world, mid="limit_arm", **block):
    return {**world.base, "id": mid, "exposure_limits": {**LIMITS, **block}}


def digest(result):
    return _timeless_digest(untimed(result))


def two_maps(world, wd=0.5, ws=0.25):
    """Warm maps whose two-regressor origin line is ``(wd, ws)`` in every bucket."""
    maps = reweighted(world.maps, 0.5, warm=True)
    for body in maps.values():
        body["origin_two"]["warm"] = True
        for rec in body["origin_two"]["buckets"]:
            rec["w"] = [wd, ws]
    return maps


def two_maps(world, wd=0.5, ws=0.25):
    """Warm maps whose two-regressor origin line is ``(wd, ws)`` in every bucket."""
    maps = reweighted(world.maps, 0.5, warm=True)
    for body in maps.values():
        body["origin_two"]["warm"] = True
        for rec in body["origin_two"]["buckets"]:
            rec["w"] = [wd, ws]
    return maps


def single_block_methods(world):
    return {"cal1": world.method("cal1"), "cal2": world.method("cal2", regressors=2),
            "hedge": hedged(world), "limits": limited(world),
            "cal1_limits": {**world.method("cal1_limits"), "exposure_limits": dict(LIMITS)}}


#: Pinned at 747f3c5 (BEFORE the I7 wiring): a method with one block (or calibration with limits) runs as it did.
PARITY_DIGESTS = {
    "cal1": "badb0df05f311c6aae224c34d501b3d61f753f19e87a09a26c5de2a1607f978b",
    "cal2": "6d8ea4560f4213e313bcb4365800ee9a0a482626a1cca39eb68a59f571976aef",
    "hedge": "862ecf654d095cbcbe0e18cbe2545b8d530a8b9c1d87133de0f944867fd72055",
    "limits": "9e512a2549bdd897e2c208ac4bdc22bcb99ad146423f5c784610ba69b49f9d35",
    "cal1_limits": "77630d64cd85e1aa7d5f79939eea787d92c8a8e1c2aeb49f8af1656a7a521519"}


@pytest.mark.parametrize("name", sorted(PARITY_DIGESTS))
def test_a_method_with_one_block_behaves_exactly_as_before(world, name):
    method = single_block_methods(world)[name]
    result = build(world, [method], maps=two_maps(world))._method(method, None)
    assert result["trades"], "the pin is meaningful: the arm trades"
    assert digest(result) == PARITY_DIGESTS[name]


# --- the combination: refusals -------------------------------------------------------------------------------------

def combined(world, mid="cal_hedge", regressors=2, **block):
    return {**world.method(mid, regressors=regressors), "hedge": {**HEDGE, **block}}


def test_the_combination_runs_and_a_one_regressor_map_cannot_be_hedged(world):
    method = combined(world)
    result = build(world, [method], maps=two_maps(world))._method(method, None)
    assert result["hedge"]["mode"] == "program" and result["calibration"]["block"]["regressors"] == 2
    with pytest.raises(ValueError, match="hedge_needs_direction_map"):
        build(world, [combined(world, regressors=1)], maps=two_maps(world))


def test_block_params_disagree_between_the_hedge_and_the_walks_direction_params(world):
    for edit in ({"rate": 0.01}, {"iv_source": "vendor"}):
        method = combined(world, **edit)
        with pytest.raises(ValueError, match="block_params_disagree"):
            build(world, [method], maps=two_maps(world))._method(method, None)
    method = combined(world)
    with pytest.raises(ValueError, match="block_params_disagree"):
        build(world, [method], maps=two_maps(world), hedge_parity={**PARITY, "min_pairs": 1})._method(method, None)
    summary = world.passing()
    summary["direction_params"] = {**summary["direction_params"], "min_years": 0.002}
    with pytest.raises(ValueError, match="block_params_disagree"):
        build(world, [method], maps=two_maps(world), summary=summary)._method(method, None)


def test_block_params_disagree_between_the_exposure_greeks_and_the_direction_params(world):
    method = {**world.method("cal_limits", regressors=2), "exposure_limits": dict(LIMITS)}
    study = build(world, [method], maps=two_maps(world), exposure_greeks={"leg": {**LEG, "vol_floor": 0.02}, "parity": dict(PARITY)})
    with pytest.raises(ValueError, match="block_params_disagree"):
        study._method(method, None)
    build(world, [method], maps=two_maps(world))._method(method, None)
    # one regressor reads no direction params, so the exposure Greeks are free
    one = {**world.method("cal1_limits"), "exposure_limits": dict(LIMITS)}
    build(world, [one], maps=two_maps(world), exposure_greeks={"leg": {**LEG, "vol_floor": 0.02}, "parity": dict(PARITY)})._method(one, None)


# --- the value term, hand-worked --------------------------------------------------------------------------------

def trace(monkeypatch, study, method):
    """Run ``method``; return ``(result, [week])``, a week holding the program the solver was handed, the metadata and adjusts."""
    weeks, seen = [], []
    real_adjust = ledger_studies.CalibratedEdge.adjust
    real_run = ledger_studies.TransportPortfolioSolve.run if hasattr(ledger_studies, "TransportPortfolioSolve") else None
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    real_run = TransportPortfolioSolve.run

    def adjust(self, day, groups, metadata):
        out = real_adjust(self, day, groups, metadata)
        weeks.append({"day": day, "metas": copy.deepcopy(metadata), "adjusts": copy.deepcopy(out),
                      "rows": dict(self._rows(day)), "best": copy.deepcopy(self._best)})
        return out

    def run(self, ctx, inp):
        seen.append(copy.deepcopy(inp))
        return real_run(self, ctx, inp)

    monkeypatch.setattr(ledger_studies.CalibratedEdge, "adjust", adjust)
    monkeypatch.setattr(TransportPortfolioSolve, "run", run)
    result = study._method(method, None)
    monkeypatch.undo()
    for program in seen:
        ids = [g["id"] for g in program["groups"]]
        week = next(w for w in weeks if [m["id"] for m in w["metas"]] == ids)
        week["program"] = program
    return result, [w for w in weeks if "program" in w]


def coefficient(group, i):
    """The solver's per-contract value at radius 0 with the adjust: credit - masses . losses + value_adjust."""
    nominal = group["credit"][i] - sum(m * row[i] for m, row in zip(group["masses"], group["losses"]))
    return nominal + group["value_adjust"][i], nominal


@pytest.mark.parametrize("ratio", [0.0, 0.5, 1.0])
def test_the_value_term_is_the_hedged_nominal_plus_adj_for_h_0_half_1(world, monkeypatch, ratio):
    wd, ws = 0.5, 0.25
    method = combined(world, ratio=ratio)
    study = build(world, [method], maps=two_maps(world, wd, ws))
    result, weeks = trace(monkeypatch, study, method)
    assert weeks and result["trades"]
    checked = 0
    for week in weeks:
        for group, meta in zip(week["program"]["groups"], week["metas"]):
            hedge = meta.get("program_hedge")
            costs = hedge["costs"] if hedge else [0.0] * len(meta["items"])
            for i, item in enumerate(meta["items"]):
                best = week["best"][meta["id"]][item["right"], item["strike"]]
                row = week["rows"][meta["id"], item["right"], item["strike"], best]
                h = hedge["ratios"][i] if hedge else 0.0
                assert h == pytest.approx(ratio if hedge else 0.0, abs=1e-9)
                value, nominal = coefficient(group, i)
                e, d = row["e"], row["e_dir"]
                c_best = (1 - h) * wd * d + ws * (e - d)
                # the hedged nominal of the BEST side is the raw edge less the removed direction part, less the leg's cost
                if item["sign"] == best:
                    assert nominal == pytest.approx(e - h * d - costs[i], rel=1e-6, abs=1e-6)
                    assert value == pytest.approx(c_best - costs[i], rel=1e-6, abs=1e-6)
                    assert week["adjusts"][meta["id"]][i] == pytest.approx(c_best - (e - h * d))
                    checked += 1
    assert checked > 20


def test_a_books_expected_value_is_the_sum_of_its_items_coefficients_at_h_half(world, monkeypatch):
    method = combined(world, ratio=0.5)
    study = build(world, [method], maps=two_maps(world))
    result, weeks = trace(monkeypatch, study, method)
    by_day = {w["day"]: w for w in weeks}
    checked = 0
    for decision in result["decisions"]:
        for book in decision.get("books", []):
            week = by_day[decision["date"]]
            group = next(g for g, m in zip(week["program"]["groups"], week["metas"]) if m["id"] == book["id"])
            meta = next(m for m in week["metas"] if m["id"] == book["id"])
            total = 0.0
            for leg in book["legs"]:
                i = next(k for k, it in enumerate(meta["items"])
                         if (it["right"], it["strike"], it["sign"]) == (leg["right"], leg["strike"], leg["sign"]))
                total += leg["quantity"] * coefficient(group, i)[0]
            assert book["expected"] == pytest.approx(total, rel=1e-6, abs=1e-6)
            checked += 1
    assert checked >= 5


def test_with_a_zero_direction_weight_the_nominal_does_not_depend_on_h(world, monkeypatch):
    """b_dir = 0: the hedge no longer changes the objective's nominal (cost aside); it acts through risk only."""
    wide = {}
    for ratio in (0.0, 1.0):
        method = combined(world, ratio=ratio)
        study = build(world, [method], maps=two_maps(world, 0.0, 0.25))
        _, weeks = trace(monkeypatch, study, method)
        for week in weeks:
            for group, meta in zip(week["program"]["groups"], week["metas"]):
                costs = meta["program_hedge"]["costs"] if "program_hedge" in meta else [0.0] * len(meta["items"])
                for i, item in enumerate(meta["items"]):
                    best = week["best"][meta["id"]][item["right"], item["strike"]]
                    if item["sign"] == best:
                        wide.setdefault((week["day"], meta["id"], item["right"], item["strike"], item["sign"]), {})[ratio] = (
                            coefficient(group, i)[0] + costs[i])
    pairs = [v for v in wide.values() if len(v) == 2]
    assert len(pairs) > 20
    assert all(v[0.0] == pytest.approx(v[1.0], rel=1e-6, abs=1e-6) for v in pairs)


def test_ratio_zero_is_the_calibration_only_program_except_for_hedge_fields(world, monkeypatch):
    two = world.method("cal2", regressors=2)
    zero = combined(world, "cal_hedge0", ratio=0.0)
    maps = two_maps(world)
    a_result, a_programs = programs_of(monkeypatch, build(world, [two], maps=maps), two)
    b_result, b_programs = programs_of(monkeypatch, build(world, [zero], maps=maps), zero)
    assert a_programs and a_programs == b_programs
    strip = lambda v: {k: strip(x) for k, x in v.items() if k != "hedge"} if isinstance(v, dict) \
        else [strip(x) for x in v] if isinstance(v, list) else v                                  # noqa: E731
    for key in ("curve", "pnl", "final_equity"):
        assert a_result[key] == b_result[key]
    assert strip(untimed(a_result["trades"])) == strip(untimed(b_result["trades"]))


def test_a_small_delta_item_keeps_h_zero_and_stays_a_candidate(world, monkeypatch):
    method = combined(world, small_delta=0.30)
    study = build(world, [method], maps=two_maps(world))
    result, weeks = trace(monkeypatch, study, method)
    kept = unhedged = 0
    base = combined(world, small_delta=0.0)
    base_study = build(world, [base], maps=two_maps(world))
    for week in weeks:
        full = {m["id"]: m for m in base_study._prepare(week["day"])[1]}
        for group, meta in zip(week["program"]["groups"], week["metas"]):
            ratios = meta["program_hedge"]["ratios"] if "program_hedge" in meta else [0.0] * len(meta["items"])
            assert len(ratios) == len(group["credit"]) == len(meta["items"])
            for i, item in enumerate(meta["items"]):
                kept += 1
                if ratios[i] == 0.0:
                    unhedged += 1
                    best = week["best"][meta["id"]][item["right"], item["strike"]]
                    row = week["rows"][meta["id"], item["right"], item["strike"], best]
                    if item["sign"] == best:
                        # an unhedged item keeps its WHOLE calibrated direction part and pays no hedge cost
                        value, nominal = coefficient(group, i)
                        assert value == pytest.approx(0.5 * row["e_dir"] + 0.25 * (row["e"] - row["e_dir"]), abs=1e-6)
    assert unhedged > 0 and kept > unhedged, "the cut removed the small-delta items from the hedge but not from the program"
    assert result["hedge"]["counts"].get("hedge_small_delta", 0) > 0


def test_exclusions_are_the_union_and_precede_the_rewrite(world, monkeypatch):
    method = {**combined(world), "exposure_limits": dict(LIMITS)}
    study = build(world, [method], maps=two_maps(world))
    result, weeks = trace(monkeypatch, study, method)
    assert weeks
    for week in weeks:
        for group, meta in zip(week["program"]["groups"], week["metas"]):
            n = len(meta["items"])
            assert len(group["credit"]) == len(group["value_adjust"]) == len(group["capacity"]) == n
            assert len(meta["program_hedge"]["units"]) == len(meta["program_hedge"]["ratios"]) == n
            assert len(week["adjusts"][meta["id"]]) == n


def test_all_three_blocks_run_together_and_record_each_blocks_fields(world, monkeypatch):
    method = {**combined(world), "exposure_limits": dict(LIMITS)}
    study = build(world, [method], maps=two_maps(world))
    result, weeks = trace(monkeypatch, study, method)
    assert result["trades"] and result["hedge"]["mode"] == "program" and result["calibration"]["block"]["regressors"] == 2
    solved = [d for d in result["decisions"] if d["status"] in ("selected", "cash")]
    assert solved and all({"hedge", "calibration", "exposure"} <= set(d) for d in solved)
    assert all(d["exposure"]["status"] == "within" for d in solved)
    assert any(d["status"] == "calibration_warmup" for d in result["decisions"])
    for trade in result["trades"]:
        assert {"calibration_adjust", "expected_raw", "radius_premium", "hedge", "program_hedge"} <= set(trade)
        assert trade["expected"] == pytest.approx(trade["expected_raw"] + trade["calibration_adjust"])
    # the three blocks' rows reach the program together: value_adjust, the hedge's slope row, the exposure rows
    program = weeks[0]["program"]
    assert all("value_adjust" in g and g["linear_rows"] for g in program["groups"])
    assert {r["id"] for r in program["joint_rows"]} >= {"exposure:delta:+", "exposure:delta:-"}


def test_a_recorded_replay_of_the_combined_arm_reproduces_it(world):
    method = combined(world, ratio=0.5)
    study = build(world, [method], maps=two_maps(world))
    result = study._method(method, None)
    replay = study._method(method, None, recorded=result)
    assert digest(replay) == digest(result)
