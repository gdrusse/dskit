"""``intraday_equities-kelly-mio`` (``EquityKellyMIO``, ADR-0111): behaviour.

Does NOT run ``dskit.pipeline.conformance.conformance_suite``'s capital
probe: that probe's shape is ``("inputs", "gate_port", "outlay")``, built
against the doorway's ORIGINAL reference subclass (``BudgetedSelect`` /
``pmquant.KellyMIO``, both of which report a scalar ``outlay``).
``ScenarioUtilitySolve`` subclasses report a multi-name ``target`` +
``trades`` + ``cash_after`` contract instead — a real, deliberate output
shape, not a shortcut past the probe. The behavioural bar the probe checks
(planner gate, empty-gate zero-deploy, staying inside a declared cap) is
covered directly below instead.
"""

from __future__ import annotations

import pytest

from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext, node_class_errors
from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve

from intraday_equities.nodes_capital import (
    BUNDLE_FIELDS,
    NODE_KINDS,
    EquityKellyMIO,
    _bundle_problems,
)

KIND = "intraday_equities-kelly-mio"

PARAMS = {
    "risk_aversion_gamma": 2.0,
    "n_tangents": 12,
    "n_scenarios_max": 64,
    "cvar_alpha": 0.9,
    "cvar_limit": None,
    "cardinality": 3,
    "min_ticket": 200.0,
    "spread_bps": 2.2,
    "taf_per_share": 0.000195,
    "taf_cap": 9.79,
    "sec31_bps": 0.0206,
    "min_price": 5.0,
    "hfdr_q": 0.30,
    "band_bps": 10.0,
    "max_position_notional": 4000.0,
    "bundle_max_staleness_ms": 5000,
}

ASOF_MS = 1_700_000_000_000


def _weights(n=8):
    return [1.0 / n] * n


def _row(entity, price, pi_upper, scenarios, decision_ts=ASOF_MS - 1000, weights=None):
    return {
        "entity": entity,
        "decision_ts": decision_ts,
        "price": price,
        "pi_upper": pi_upper,
        "weights": weights or _weights(len(scenarios)),
        "scenarios": list(scenarios),
    }


def _bundle():
    w = _weights(8)
    return [
        _row("AAPL", 190.0, 0.20, [0.02, 0.01, -0.01, 0.015, -0.02, 0.0, 0.03, -0.01], weights=w),
        _row("MSFT", 410.0, 0.25, [0.01, 0.0, -0.005, 0.02, -0.01, 0.005, 0.01, -0.02], weights=w),
        _row("XOM", 110.0, 0.28, [0.005, -0.005, 0.0, 0.01, -0.01, 0.0, 0.005, -0.005], weights=w),
    ]


def _portfolio(**overrides):
    base = {
        "asof_ms": ASOF_MS,
        "cash": 20000.0,
        "buying_power": 20000.0,
        "positions": {},
        "cash_reserve": 0.0,
        "gross_limit": 12000.0,
        "sale_credit": 1.0,
    }
    base.update(overrides)
    return base


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "run"))


def _node(**params):
    return EquityKellyMIO("size", {**PARAMS, **params})


class TestRegistration:
    def test_it_is_registered_under_its_kind_name(self):
        assert NODE_KINDS[KIND] is EquityKellyMIO
        assert KIND in DEFAULT_NODE_KINDS.kinds()
        cls, owned = DEFAULT_NODE_KINDS.get(KIND)
        assert cls is EquityKellyMIO
        assert owned is False

    def test_it_subclasses_the_scenario_utility_doorway(self):
        assert issubclass(EquityKellyMIO, ScenarioUtilitySolve)

    def test_role_is_capital(self):
        assert EquityKellyMIO.role == "capital"

    def test_it_can_construct_and_is_concrete(self):
        assert not node_class_errors(EquityKellyMIO, KIND)


class TestParams:
    def test_the_reference_params_validate_clean(self):
        assert EquityKellyMIO.validate_params(PARAMS) == []

    @pytest.mark.parametrize(
        "name",
        [
            "spread_bps", "taf_per_share", "taf_cap", "sec31_bps", "min_price",
            "hfdr_q", "band_bps", "max_position_notional", "bundle_max_staleness_ms",
        ],
    )
    def test_the_cost_and_risk_knobs_have_no_default(self, name):
        params = {k: v for k, v in PARAMS.items() if k != name}
        problems = EquityKellyMIO.validate_params(params)
        assert any(name in p and "required" in p for p in problems), problems

    def test_unknown_knobs_are_refused_by_name(self):
        problems = EquityKellyMIO.validate_params({**PARAMS, "bogus": 1})
        assert any("bogus" in p for p in problems)

    def test_hfdr_q_out_of_range_is_refused(self):
        problems = EquityKellyMIO.validate_params({**PARAMS, "hfdr_q": 1.5})
        assert any("hfdr_q" in p for p in problems)


class TestBundleValidation:
    def test_the_reference_bundle_validates_clean(self):
        assert _bundle_problems(_bundle()) == []

    def test_a_non_list_bundle_is_refused_by_name_not_walked(self):
        problems = _bundle_problems(iter(_bundle()))
        assert any("materialized list" in p for p in problems)

    def test_a_missing_field_is_refused_by_name(self):
        bad = [dict(_bundle()[0])]
        del bad[0]["pi_upper"]
        problems = _bundle_problems(bad)
        assert any("pi_upper" in p for p in problems)

    def test_mismatched_weights_across_rows_are_refused(self):
        bad = _bundle()
        bad[1] = dict(bad[1])
        bad[1]["weights"] = _weights(4)
        bad[1]["scenarios"] = bad[1]["scenarios"][:4]
        problems = _bundle_problems(bad)
        assert any("differ from the batch" in p for p in problems)

    def test_duplicate_entities_are_refused(self):
        bad = _bundle() + [_bundle()[0]]
        problems = _bundle_problems(bad)
        assert any("duplicate entity" in p for p in problems)

    def test_every_bundle_field_is_covered_by_the_tuple(self):
        # A pinning test: BUNDLE_FIELDS is what _bundle_problems actually
        # checks for presence — this keeps the tuple and the fixture
        # honest about each other (CLAUDE.md: a pin that omits a knob
        # claims coverage it lacks).
        assert set(BUNDLE_FIELDS) <= set(_bundle()[0])


class TestEmptyGate:
    def test_empty_bundle_and_no_position_deploys_zero_without_solving(self, tmp_path):
        node = _node()
        out = node.run(
            _ctx(tmp_path),
            {"bundle": [], "portfolio": _portfolio(cash=500.0), "survivors": set()},
        )
        assert out["target"] == {}
        assert out["metrics"]["objective"] == 0.0
        assert out["cash_after"] == 500.0
        assert out["evidence"]["n_bundle_rows"] == 0


class TestRealSolve:
    def test_it_solves_within_every_declared_cap(self, tmp_path):
        node = _node()
        survivors = {"AAPL", "MSFT", "XOM"}
        out = node.run(
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors}
        )
        assert len(out["target"]) <= PARAMS["cardinality"]
        assert out["metrics"]["gross_exposure"] <= 12000.0 + 1e-6
        for name, shares in out["target"].items():
            price = {"AAPL": 190.0, "MSFT": 410.0, "XOM": 110.0}[name]
            assert shares * price <= PARAMS["max_position_notional"] + 1e-6
            assert shares * price >= PARAMS["min_ticket"] - 1e-6

    def test_a_stat_test_miss_routes_the_name_out(self, tmp_path):
        node = _node()
        survivors = {"AAPL", "MSFT"}  # XOM not a survivor
        out = node.run(
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors}
        )
        assert "XOM" not in out["target"]
        assert out["evidence"]["routed_out"]["XOM"] == "not a stat_test survivor"

    def test_a_stale_bundle_row_is_routed_out(self, tmp_path):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], decision_ts=ASOF_MS - 999999)
        node = _node()
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}},
        )
        assert "AAPL" not in out["target"]
        assert "stale" in out["evidence"]["routed_out"]["AAPL"]

    def test_a_price_below_min_price_is_routed_out(self, tmp_path):
        bundle = _bundle()
        bundle[2] = dict(bundle[2], price=1.0)  # below min_price=5.0
        node = _node()
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}},
        )
        assert "XOM" not in out["target"]
        assert "min_price" in out["evidence"]["routed_out"]["XOM"]

    def test_a_dropped_held_name_is_a_mandatory_full_exit(self, tmp_path):
        bundle = [row for row in _bundle() if row["entity"] != "XOM"]
        portfolio = _portfolio(positions={"XOM": 20}, mark_prices={"XOM": 108.0})
        node = _node()
        out = node.run(
            _ctx(tmp_path), {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}}
        )
        assert "XOM" not in out["target"]
        assert out["trades"]["XOM"] == {"buy": 0, "sell": 20}

    def test_the_hfdr_row_excludes_a_name_above_q(self, tmp_path):
        # Push XOM's pi_upper above hfdr_q with nothing to offset it — the
        # ADR-0088 row must refuse XOM exposure even though its own return
        # scenarios look attractive.
        bundle = _bundle()
        bundle[2] = dict(bundle[2], pi_upper=0.95)
        node = _node(hfdr_q=0.10)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}},
        )
        assert "XOM" not in out["target"]

    def test_the_no_trade_band_forbids_a_dust_sized_trade(self, tmp_path):
        # A name already held near its target should not see a trade
        # smaller than the declared band — band_bps=1000 (10%) on a
        # $19,000 ticket is a ~$1,900 (10-share) threshold, well above a
        # one-share ($190) AAPL nudge, so band-driven inaction must show
        # up as EITHER no trade, or a trade at least band_shares_i in
        # size — asserted UNCONDITIONALLY (a skeptic review flagged the
        # earlier version of this test for skipping the assertion
        # entirely whenever no AAPL trade happened).
        bundle = _bundle()
        portfolio = _portfolio(positions={"AAPL": 100}, cash=1000.0, buying_power=1000.0)
        node = _node(band_bps=1000.0)  # a deliberately huge band
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}},
        )
        moved = out["trades"].get("AAPL", {"buy": 0, "sell": 0})
        total = moved["buy"] + moved["sell"]
        ticket = 190.0 * 100
        band_shares = int(-(-(1000.0 * 1e-4 * ticket) // 190.0))
        assert total == 0 or total >= band_shares

    def test_identical_inputs_give_identical_output_twice(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        inputs = {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors}
        out_a = _node().run(_ctx(tmp_path), inputs)
        out_b = _node().run(_ctx(tmp_path), inputs)
        assert out_a["target"] == out_b["target"]
        assert out_a["trades"] == out_b["trades"]


class TestExitCostIsPriced:
    """Regression for a skeptic-review BLOCKER: ``exit_cost_per_share`` was
    never populated by ``instruments()``, so it silently defaulted to the
    doorway's zero — pricing every liquidation as free and understating
    reported CVaR by ~23x in the reviewer's worked example."""

    def test_exit_cost_per_share_is_populated_and_matches_the_sell_cost(self, tmp_path):
        node = _node()
        names, rows, _account = node.instruments(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}}
        )
        assert names
        for name in names:
            assert rows[name]["exit_cost_per_share"] == pytest.approx(rows[name]["cost_sell"])
            assert rows[name]["exit_cost_per_share"] > 0.0

    def test_zeroing_exit_cost_understates_the_reported_cvar(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        portfolio = _portfolio(positions={"AAPL": 50}, cash=15000.0, buying_power=15000.0)
        inputs = {"bundle": _bundle(), "portfolio": portfolio, "survivors": survivors}

        real_out = _node(cvar_limit=100000.0).run(_ctx(tmp_path), inputs)

        class ZeroExitCost(EquityKellyMIO):
            def instruments(self, inp):
                names, rows, account = super().instruments(inp)
                for row in rows.values():
                    row["exit_cost_per_share"] = 0.0
                return names, rows, account

        zero_out = ZeroExitCost("size2", {**PARAMS, "cvar_limit": 100000.0}).run(_ctx(tmp_path), inputs)
        assert zero_out["metrics"]["cvar"] < real_out["metrics"]["cvar"]


class TestPositionBookkeeping:
    """Regression for a skeptic-review MAJOR: a zero-share entry in
    ``portfolio.positions`` (a closed-out symbol left at 0 rather than
    removed) was wrongly treated as held, demanding a mark price for a
    position that does not exist."""

    def test_a_zero_share_position_entry_is_not_treated_as_held(self, tmp_path):
        bundle = [row for row in _bundle() if row["entity"] != "XOM"]
        portfolio = _portfolio(positions={"XOM": 0})  # no mark_prices supplied on purpose
        node = _node()
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}},
        )
        assert "XOM" not in out["target"]
        assert "XOM" not in out["trades"]


class TestTafNeverUndercharges:
    """Regression for a skeptic-review MAJOR: sizing the per-share TAF rate
    against currently-held shares (``taf_cap / held``) undercharged a small
    sell against a large held position by ~20x in the reviewer's worked
    example. The fixed rate must never fall below the flat, uncapped
    ``taf_per_share``."""

    def test_a_small_sell_against_a_large_position_is_not_undercharged(self, tmp_path):
        node = _node()
        names, rows, _account = node.instruments(
            {
                "bundle": [],
                "portfolio": _portfolio(positions={"AAPL": 1000}, mark_prices={"AAPL": 190.0}),
                "survivors": set(),
            }
        )
        assert names == ["AAPL"]
        assert rows["AAPL"]["cost_sell"] >= PARAMS["taf_per_share"] - 1e-12
