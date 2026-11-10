"""ADR-0311 base stage 4 + Amendment 1 A4 + ADR-0313 X7: the index_options edge-calibration walk child.

SYNTHETIC DATA ONLY (``edge_calibration_world``): tiny chains, forecast grids and bars built in the test.
Leakage first, then the two parity pins against MIO's own rules (``_loss_row``, ``_settlements``), eligibility,
the hand-worked e and r, the fill sub-codes, the direction edge, the seal and pin refusals, one tiny end-to-end walk.
"""

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.edge_walk import EdgeReliabilityWalk
from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL
from dskit.pipeline.node import NodeContext
from dskit.pipeline.option_exposure import DirectionEdge, ParityForward

from edge_calibration_world import (BARS_FROM, STRIKES, WINDOW, World, close_of, inputs_of, sessions)
from index_options import edge_calibration as ec
from index_options.contracts import usable_quote
from index_options.edge_calibration import IndexOptionsEdgeWalk
from index_options.ledger_studies import WeeklyOptionPortfolioStudy

CHILD = Path(__file__).resolve().parents[1]
EDGES = {"dte_edges": [8, 22], "abs_z_edges": [1.0], "spread_edges": [0.15]}
POOL = {"min_clusters": 2, "min_dates": 2, "tau2_floor": 1e-6, "weight_cap": 1.0, "lcb_k": 1.0, "band": "two_block",
        "min_blocks": 2, "ci_levels": [0.90, 0.95]}
ADJUST = {"name_prior_clusters": 26, "name_min_clusters": 2}
REGIME = {"regime_lookback_weeks": 4, "regime_min_weeks": 2}
DIRECTION = {"rate": 0.0, "iv_source": "mid", "iv_disagree_tol": 0.5, "multiplier": 100, "min_years": 0.002,
             "vol_floor": 0.01, "parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5,
             "forward_se_floor_frac": 0.001}


class FxWalk(IndexOptionsEdgeWalk):
    """The child with its two reading seams answered from the synthetic world; ``reads`` logs them."""

    def __init__(self, key, params, world, **edit):
        super().__init__(key, params)
        self.world, self.edit, self.reads = world, edit, []

    def _read_market(self, domain, tail):
        self.reads.append(("market", tail))
        return inputs_of(self.world, **self.edit)[0]

    def _read_sessions(self, domain, tail):
        market = inputs_of(self.world, **self.edit)[0]
        return sorted(set.intersection(*({b["date"] for b in d["bars"]} for d in market.values())))

    def _read_forecasts(self, domain, start, end_before):
        self.reads.append(("forecasts", start, end_before))
        return inputs_of(self.world, **self.edit)[1]


def params(world, root, out="out", direction=False, **over):
    p = {**world.files(Path(root) / "in"), "window": dict(world.window), "report_from": "2019-10-07",
         "sealed_from": "2021-07-01", "out_dir": str(Path(root) / out), "buckets": dict(EDGES),
         "regime": dict(REGIME), "pooling": dict(POOL), "name_adjust": dict(ADJUST), "checkpoint_every": 1,
         "ram_gb_limit": 64, "side_rule": "best", "history_start": BARS_FROM, "protected_end_before": "2024-01-01", "study_scope": "window", "direction_regressor": direction}
    if direction:
        p["direction"] = dict(DIRECTION)
    p.update(over)
    return p


def run(node, root):
    return node.run(NodeContext(name="edge", asof="2019-12-01", run_dir=str(root)), {})["summary"]


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def maps(root, before=None):
    return {k: v for k, v in tree(root).items() if k.startswith("map-") and (before is None or k[4:14] < before)}


@pytest.fixture(scope="module")
def world():
    return World()


@pytest.fixture
def walk(world, tmp_path):
    node = FxWalk("edge", params(world, tmp_path, direction=True), world)
    node._open()
    return node


def key(row):
    return (row["name"], row["expiry"], row["right"], row["strike"], row["sign"])


# ---- parity pins against MIO's own rules (copied, not edited) ---------------------------------------

def test_leg_payoff_equals_mio_loss_row(walk):
    """The realized intrinsic the walk books (r_dec - credit) is minus MIO's ``_loss_row`` at the settlement level."""
    study = walk._study_for("2019-10-07")
    mult = study.params["bridge"]["solver"]["multiplier"]
    t = "2019-10-07"
    rows = {key(r): r for r in walk._decision_rows(t)}
    out = Path(walk.params["out_dir"])
    run(walk, out.parent)
    seen = 0
    for line in (out / "rows" / "rows-2019-10-18.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["t"] != t:
            continue
        item = {"right": r["right"], "strike": r["strike"], "sign": r["sign"]}
        level = close_of("AAA", sessions().index("2019-10-18"), "2019-10-18", None)
        row = rows[("AAA", "2019-10-18", r["right"], r["strike"], r["sign"])]
        intrinsic = r["r_dec"] - row["credit"]
        assert intrinsic == pytest.approx(-WeeklyOptionPortfolioStudy._loss_row([item], level, mult)[0], abs=1e-9)
        seen += 1
    assert seen > 0


@pytest.mark.parametrize("split", [False, True])
def test_settlement_level_equals_mio_settlements(split):
    """``settlement_level`` is MIO's ``_settlements`` rule; a split between decision and settlement is refused by both."""
    spec = {"symbol": "AAA", "from": "2019-10-10", "factor": 2.0} if split else None
    world_ = World(split=spec)
    node = FxWalk("edge", params(world_, "/tmp/unused"), world_)
    node._open()
    study = node._study_for("2019-10-07")
    book = {"expiry": "2019-10-18", "symbol": "AAA", "factor": 1.0, "credit": 0.,
            "legs": [{"right": "call", "sign": 1, "quantity": 1, "strike": 99.0}]}

    class Account:
        open = {"b": book}

    level = ec.settlement_level(study._bars["AAA"], study._factors["AAA"], "2019-10-18", 1.0)
    if split:
        assert level == "basis_changed"
        with pytest.raises(ValueError, match="unsettleable"):
            study._settlements(Account, "2019-10-18")
    else:
        got = study._settlements(Account, "2019-10-18")["b"]
        assert got == pytest.approx(100 * max(level - 99.0, 0.))
    assert ec.settlement_level(study._bars["AAA"], study._factors["AAA"], "2019-10-19", 1.0) == "no_settlement_bar"


def test_fill_credit_equals_mio_fill_on_a_one_leg_book(walk):
    study = walk._study_for("2019-10-07")
    t, fill_day = "2019-10-07", "2019-10-08"
    for sign, right, strike in ((1, "call", 100.0), (-1, "put", 95.0), (1, "put", 105.0)):
        book = {"symbol": "AAA", "expiry": "2019-10-18", "factor": 1.0, "credit": 0., "contracts": 1,
                "legs": [{"right": right, "strike": strike, "sign": sign, "quantity": 1}]}
        filled, why = study._fill(book, fill_day)
        view = {"group": "g", "name": "AAA", "expiry": "2019-10-18", "settle_date": "2019-10-18",
                "fill_date": fill_day, "factor": 1.0, "right": right, "strike": strike, "sign": sign,
                "multiplier": 100.0, "decision_date": t}
        got = walk._fill_credits([view])[0]
        credit = got[0] if isinstance(got, tuple) else got
        assert why is None and credit == pytest.approx(filled["credit"])


# ---- eligibility equals MIO's ------------------------------------------------------------------------

def test_rows_are_exactly_the_items_of_mio_prepare_including_itm_strikes(walk):
    t = "2019-10-14"
    study = walk._study_for(t)
    groups, meta, _ = study._prepare(t)
    want = {("AAA", m["expiry"], i["right"], i["strike"], i["sign"]) for m in meta for i in m["items"]}
    got = {key(r) for r in walk._decision_rows(t)}
    assert got == want
    rights = {(r, k) for _, _, r, k, _s in got}
    assert ("call", 90.0) in rights and ("put", 110.0) in rights  # in the money on both sides of spot 100


def test_a_quote_mio_refuses_gives_no_row(world, tmp_path):
    t = "2019-10-14"
    exp = "2019-10-25"
    bad = {(t, exp, "call", 90.0): {"bid_size": 0}, (t, exp, "put", 95.0): {"bid": 3.0, "ask": 2.0},
           (t, exp, "call", 110.0): {"bid": 0.01, "ask": 4.0}, (t, exp, "put", 100.0): {"bid": 3.0, "ask": 3.0}}
    node = FxWalk("edge", params(world, tmp_path), world, quote=bad)
    node._open()
    study = node._study_for(t)
    present = {key(r) for r in node._decision_rows(t) if r["expiry"] == exp}
    for (_, _, right, strike), _ in bad.items():
        assert not any(k[2] == right and k[3] == strike for k in present)
    others = {(r, k) for _, _, r, k, _s in present}
    assert ("call", 100.0) in others
    row = study._quotes["AAA"].get((t, exp, "call", 90.0))
    assert usable_quote(row, 1.0, 1, 0.5, strict=True) is None


# ---- hand-worked e and r --------------------------------------------------------------------------------

def test_e_and_r_by_hand_for_a_bought_call_and_a_sold_put(world, tmp_path):
    t, fill_day, exp = "2019-10-07", "2019-10-08", "2019-10-18"
    probe = FxWalk("edge", params(world, tmp_path, out="probe"), world)
    probe._open()
    best = {}
    for r in probe._decision_rows(t):
        k = (r["expiry"], r["right"], r["strike"])
        if k not in best or r["e"] > best[k]["e"]:
            best[k] = r
    call = next(r for k, r in best.items() if k[0] == exp and k[1] == "call" and r["sign"] == 1)
    put = next(r for k, r in best.items() if k[0] == exp and k[1] == "put" and r["sign"] == -1)
    study = probe._study_for(t)
    groups, meta, _ = study._prepare(t)
    g = next(x for x in groups if x["id"].endswith(exp))
    for r in (call, put):
        pay = [max(x - r["strike"], 0.) if r["right"] == "call" else max(r["strike"] - x, 0.) for x in g["grid"]]
        ev = sum(m * p for m, p in zip(g["masses"], pay))
        mid, spread = r["mid"], r["spread"]
        price = mid * (1 + r["sign"] * spread / 2)
        want = -r["sign"] * price * 100 - .65 + r["sign"] * 100 * ev
        assert r["e"] == pytest.approx(want, rel=1e-9, abs=1e-9)
        assert r["credit"] == pytest.approx(-r["sign"] * price * 100 - .65)
    edits = {(fill_day, exp, "call", call["strike"]): {"bid": 2.0, "ask": 2.2},
             (fill_day, exp, "put", put["strike"]): {"bid": 1.5, "ask": 1.7}}
    node = FxWalk("edge", params(world, tmp_path), world, quote=edits, bar={exp: 103.0})
    run(node, tmp_path)
    lines = [json.loads(x) for x in (Path(node.params["out_dir"]) / "rows" / f"rows-{exp}.jsonl").read_text().splitlines()]
    got = {(x["right"], x["strike"], x["sign"]): x for x in lines if x["t"] == t}
    c = got[("call", call["strike"], 1)]
    p = got[("put", put["strike"], -1)]
    call_pay = max(103.0 - call["strike"], 0.) * 100
    put_pay = max(put["strike"] - 103.0, 0.) * 100
    assert c["r"] == pytest.approx(call_pay - 2.2 * 100 - .65)
    assert p["r"] == pytest.approx(-put_pay + 1.5 * 100 - .65)
    assert c["r_dec"] == pytest.approx(call_pay + call["credit"])


# ---- fill sub-codes ---------------------------------------------------------------------------------------

def view(**kw):
    base = {"group": "g", "name": "AAA", "expiry": "2019-10-18", "settle_date": "2019-10-18",
            "fill_date": "2019-10-08", "factor": 1.0, "right": "call", "strike": 100.0, "sign": 1,
            "multiplier": 100.0, "decision_date": "2019-10-07"}
    base.update(kw)
    return base


def test_no_fill_quote_subcodes(world, tmp_path):
    f, e = "2019-10-08", "2019-10-18"
    edits = {(f, e, "call", 100.0): {"bid_size": 0}, (f, e, "call", 95.0): {"bid": 3.0, "ask": 2.0},
             (f, e, "call", 105.0): {"ask": 9.0, "bid": 0.2}}
    node = FxWalk("edge", params(world, tmp_path), world, quote=edits)
    node._open()
    out = node._fill_credits([view(), view(strike=95.0), view(strike=105.0),
                              view(strike=100.0, fill_date=e), view(factor=2.0), view(strike=100.0, sign=-1)])
    assert out[0] == "size"        # thin depth only
    assert out[1] == "quote"       # crossed
    assert out[2] == "quote"       # wide
    assert out[3] == "session"
    assert out[4] == "basis"
    assert out[5] == "size"


# ---- direction edge -----------------------------------------------------------------------------------------

def test_e_dir_sign_and_restatement(walk):
    t = "2019-10-14"
    study = walk._study_for(t)
    rows = [r for r in walk._decision_rows(t) if r["expiry"] == "2019-10-25"]
    groups, meta, _ = study._prepare(t)
    g = next(x for x in groups if x["id"].endswith("2019-10-25"))
    mf = DirectionEdge.forecast_mean(g["masses"], g["grid"])
    q = study._quotes["AAA"]
    usable = ec.usable_parity_rows(
        [{"right": x["type"], "strike": x["strike_raw"], **x} for x in
         (q.get((t, "2019-10-25", rt, k)) for rt in ("call", "put") for k in STRIKES) if x],
        1.0, 1, 0.5, rescale=True)
    spot = next(m for m in meta if m["expiry"] == "2019-10-25")["spot"]
    forward, source = ParityForward({"parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5}).from_chain(
        usable, spot)
    assert source == "parity"
    by = {key(r): r for r in rows}
    for r in rows:
        assert r["e_dir"] is not None
        assert r["e_dir"] * by[(r["name"], r["expiry"], r["right"], r["strike"], -r["sign"])]["e_dir"] <= 0.
        assert r["e_dir"] == pytest.approx(-by[(r["name"], r["expiry"], r["right"], r["strike"], -r["sign"])]["e_dir"])
        delta_sign = 1 if r["right"] == "call" else -1
        if abs(mf - forward) > 1e-9:
            assert (r["e_dir"] > 0) == ((mf - forward) * delta_sign * r["sign"] > 0)


def test_rows_carry_the_notional_scale_the_fee_and_the_mid(walk):
    t = "2019-10-07"
    spot = walk._study_for(t)._prepare(t)[1][0]["spot"]
    for r in walk._decision_rows(t):
        assert r["scale"] == pytest.approx(spot * 100) and r["multiplier"] == 100 and r["fee"] == 0.65
        assert r["cdf_repaired"] is False and r["mid"] > 0 and r["sigma2"] is None or r["sigma2"] >= 0


# ---- the seam and the hooks ------------------------------------------------------------------------------------

def test_the_item_feature_seam_equals_decision_rows_row_for_row(walk):
    t = "2019-10-21"
    study = walk._study_for(t)
    seam = ec.EdgeItemFeatures(walk._features.direction, walk._features.edge_scale)
    assert walk._decision_rows(t) == seam.rows(study, t)


def test_calendar_is_the_studys_and_next_session_is_its_next(walk, world):
    study = walk._study_for("2019-10-01")
    assert walk._decision_calendar() == study.weeks == world.decisions
    cal = study.calendar
    assert walk._next_session("2019-10-04") == cal[cal.index("2019-10-04") + 1]


def test_walk_hooks_are_the_only_abstract_surface():
    assert not inspect.isabstract(IndexOptionsEdgeWalk)
    assert issubclass(IndexOptionsEdgeWalk, EdgeReliabilityWalk)
    assert set(IndexOptionsEdgeWalk._EXTRA_PARAMS) == {"mio_document", "mio_documents", "forecast_pins", "side_rule", "study_scope", "direction", "history_start",
                                                          "protected_end_before"}


# ---- leakage end to end ------------------------------------------------------------------------------------------

T = "2019-11-04"


def test_poisoned_future_changes_nothing_at_t(world, tmp_path):
    base = FxWalk("edge", params(world, tmp_path, out="base"), world)
    run(base, tmp_path)
    bad = FxWalk("edge", params(world, tmp_path, out="bad"), world, poison_from=T)
    run(bad, tmp_path)
    a, b = maps(tmp_path / "base", T), maps(tmp_path / "bad", T)
    assert a and a == b
    at = lambda root: {k: v for k, v in json.loads((root / f"map-{T}.json").read_text()).items() if k != "regimes"}
    assert at(tmp_path / "base") == at(tmp_path / "bad")


def test_deleted_future_gives_byte_identical_maps(world, tmp_path):
    full = FxWalk("edge", params(world, tmp_path, out="full"), world)
    run(full, tmp_path)
    cut = World(window={"start": WINDOW["start"], "end": "2019-10-31"})
    short = FxWalk("edge", params(cut, tmp_path, out="short"), cut, delete_from=T)
    run(short, tmp_path)
    a, b = maps(tmp_path / "full", T), maps(tmp_path / "short", T)
    assert a and a == b


def test_a_changed_decision_week_forecast_moves_only_its_own_map_regimes(world, tmp_path):
    # positive control: poisoning BEFORE t does move the maps at t
    base = FxWalk("edge", params(world, tmp_path, out="base2"), world)
    run(base, tmp_path)
    bad = FxWalk("edge", params(world, tmp_path, out="bad2"), world, poison_from="2019-10-14")
    run(bad, tmp_path)
    assert maps(tmp_path / "base2", T) != maps(tmp_path / "bad2", T)


# ---- seal, pins and refusals --------------------------------------------------------------------------------------

def refusal(node, root, code):
    with pytest.raises(ValueError) as err:
        run(node, root)
    assert getattr(err.value, "code", None) == code, err.value
    return err.value


def test_sealed_from_above_the_development_seal_refuses(world, tmp_path):
    node = FxWalk("edge", params(world, tmp_path, sealed_from="2021-07-02"), world)
    refusal(node, tmp_path, "development_seal_exceeded")
    assert node.reads == []


def test_sealed_from_missing_refuses(world, tmp_path):
    p = params(world, tmp_path)
    del p["sealed_from"]
    refusal(FxWalk("edge", p, world), tmp_path, "sealed_from_missing")


@pytest.mark.parametrize("window_end,sealed", [("2019-11-24", "2019-11-10"), ("2024-02-01", "2021-07-01")])
def test_window_breaching_the_seal_or_2024_refuses_before_any_reader_opens(world, tmp_path, window_end, sealed):
    p = params(world, tmp_path, sealed_from=sealed, window={"start": "2019-10-01", "end": window_end})
    node = FxWalk("edge", p, world)
    refusal(node, tmp_path, "window_outside_data_rights")
    assert node.reads == []


def test_a_reader_row_dated_at_or_after_sealed_from_refuses(world, tmp_path):
    p = params(world, tmp_path, sealed_from="2019-12-02", window={"start": "2019-10-01", "end": "2019-11-24"})
    refusal(FxWalk("edge", p, world), tmp_path, "window_outside_data_rights")  # the fixture's bars run to 2019-12-13


def test_forecast_pin_mismatch_and_missing_quarter_refuse(world, tmp_path):
    p = params(world, tmp_path)
    p["forecast_pins"]["2019Q4"]["forecasts"] = "0" * 64
    node = FxWalk("edge", p, world)
    refusal(node, tmp_path, "forecast_not_pinned")
    assert node.reads == []
    q = params(world, tmp_path)
    q["forecast_pins"] = {"2019Q3": q["forecast_pins"]["2019Q4"]}
    refusal(FxWalk("edge", q, world), tmp_path, "forecast_not_pinned")


def test_mio_document_drift_refuses(world, tmp_path):
    p = params(world, tmp_path)
    Path(p["mio_document"]["path"]).write_bytes(b"{}\n")
    node = FxWalk("edge", p, world)
    refusal(node, tmp_path, "mio_document_not_pinned")
    assert node.reads == []


def test_out_dir_inside_inputs_refuses(world, tmp_path):
    p = params(world, tmp_path)
    p["out_dir"] = str(Path(p["mio_document"]["path"]).parent / "results")
    refusal(FxWalk("edge", p, world), tmp_path, "out_dir_inside_inputs")


def test_direction_config_invalid_both_ways(world, tmp_path):
    p = params(world, tmp_path, direction=True)
    del p["direction"]
    with pytest.raises(ConfigError, match="direction_config_invalid"):
        FxWalk("edge", p, world)
    q = params(world, tmp_path)
    q["direction"] = dict(DIRECTION)
    with pytest.raises(ConfigError, match="direction_config_invalid"):
        FxWalk("edge", q, world)
    r = params(world, tmp_path, direction=True)
    r["direction"]["rate"] = 0.01
    with pytest.raises(ConfigError, match="direction_config_invalid"):
        FxWalk("edge", r, world)


def test_multiplier_mismatch_and_other_child_knobs(world, tmp_path):
    p = params(world, tmp_path, direction=True)
    p["direction"]["multiplier"] = 50
    refusal(FxWalk("edge", p, world), tmp_path, "multiplier_mismatch")
    with pytest.raises(ConfigError, match="side_rule_invalid"):
        FxWalk("edge", params(world, tmp_path, side_rule="worst"), world)
    with pytest.raises(ConfigError, match="study_scope_invalid"):
        FxWalk("edge", params(world, tmp_path, study_scope="year"), world)
    for key, code in (("side_rule", "side_rule_invalid"), ("study_scope", "study_scope_invalid")):
        node = FxWalk("edge", params(world, tmp_path), world)
        node.params[key] = "bogus"          # a caller that skipped validation still refuses at run
        refusal(node, tmp_path, code)


def test_a_window_not_after_history_start_refuses(world, tmp_path):
    p = params(world, tmp_path, window={"start": "2019-06-01", "end": "2019-11-24"})
    refusal(FxWalk("edge", p, world), tmp_path, "history_start_invalid")


# ---- a full tiny walk -------------------------------------------------------------------------------------------------

def test_tiny_end_to_end_walk_writes_the_x1_x6_x7_keys(world, tmp_path):
    node = FxWalk("edge", params(world, tmp_path, direction=True), world)
    summary = run(node, tmp_path)
    out = Path(node.params["out_dir"])
    assert summary["walk_decision_dates"] == world.decisions
    assert "regimes" in json.loads((out / f"map-{world.decisions[2]}.json").read_text())      # X1
    assert set(summary["integration_reads"]) >= {"read2w", "read2b", "affine_side"}               # X6
    assert summary["forecast_pins"] == {"2019Q4": world.forecast_pins["2019Q4"]}                  # X7
    assert summary["mio_quote_values"] == {"max_relative_spread": .5, "min_quote_size": 1, "max_abs_z": 2.5,
                                           "fee_per_contract_usd": .65, "multiplier": 100}
    assert summary["symbol_list_sha256"] == hashlib.sha256(json.dumps(["AAA"]).encode()).hexdigest()
    assert set(summary["pins"]) >= {"mio_document", "forecast:2019Q4"}
    assert summary["counts"]["rows_decided"] > 0 and summary["counts"]["rows_admitted"] > 0
    assert (out / "forward-reliability.csv").exists() and any((out / "rows").iterdir())
    assert not list(out.rglob("*.tmp*"))


def test_study_scope_quarter_gives_the_same_maps_and_rows_as_window(world, tmp_path):
    a = FxWalk("edge", params(world, tmp_path, out="w", direction=True), world)
    run(a, tmp_path)
    b = FxWalk("edge", params(world, tmp_path, out="q", direction=True, study_scope="quarter"), world)
    run(b, tmp_path)
    ta, tb = tree(tmp_path / "w"), tree(tmp_path / "q")
    keep = lambda t: {k: v for k, v in t.items() if k.startswith(("map-", "rows/")) or k.endswith(".csv")}
    assert keep(ta) and keep(ta) == keep(tb)


def test_a_cdf_noise_row_is_flagged_cdf_repaired(world, tmp_path):
    t = "2019-10-14"
    market, forecasts = inputs_of(world)
    for r in forecasts["AAA"]["forecasts"]:
        if r["quote_date"] == t:
            r["method_cdf_7"] = r["method_cdf_6"] - 5e-10
            break
    node = FxWalk("edge", params(world, tmp_path), world)
    node._read_forecasts = lambda d, s, e: forecasts
    node._open()
    assert any(r["cdf_repaired"] for r in node._decision_rows(t))


# ---- template -------------------------------------------------------------------------------------------------------

def test_every_edge_calibration_config_is_under_the_development_seal():
    configs = sorted((CHILD / "configs").glob("edge-calibration-*.json"))
    assert configs
    for path in configs:
        doc = json.loads(path.read_text())
        assert doc.get("notes")
        node = doc["pipeline"]["edge"]
        p = node["params"]
        assert node["uses"] == "index_options.edge_calibration:IndexOptionsEdgeWalk" and node.get("notes")
        assert p["sealed_from"] <= DEVELOPMENT_SEAL and p["window"]["end"] < p["sealed_from"]
        assert p["window"]["end"] < "2024-01-01" and p["report_from"] > p["window"]["start"]
        assert IndexOptionsEdgeWalk.validate_params(p) == []


def test_the_template_has_the_prescribed_window_and_marks_its_placeholders():
    p = json.loads((CHILD / "configs/edge-calibration-template.json").read_text())["pipeline"]["edge"]["params"]
    assert p["sealed_from"] == "2021-07-01" and p["window"] == {"start": "2019-10-01", "end": "2021-06-30"}
    assert p["report_from"] == "2020-01-01"
    for k in ("out_dir",):
        assert p[k].startswith("PLACEHOLDER")
    assert "mio_document" not in p and set(p["mio_documents"]) == set(p["forecast_pins"])
    assert all(v["path"].startswith("PLACEHOLDER") and v["sha256"].startswith("PLACEHOLDER")
               for v in p["mio_documents"].values())
    notes = json.loads((CHILD / "configs/edge-calibration-template.json").read_text())["pipeline"]["edge"]["notes"]
    assert "dskit.pipeline run <cfg>.json --asof" in notes and "quarterly_forecasts pin" in notes
    assert all(v["forecasts"].startswith("PLACEHOLDER") for v in p["forecast_pins"].values())
    assert set(p["forecast_pins"]) == {"2019Q4", "2020Q1", "2020Q2", "2020Q3", "2020Q4", "2021Q1", "2021Q2"}
