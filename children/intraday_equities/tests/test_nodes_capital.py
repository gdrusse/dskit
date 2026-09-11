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

from intraday_equities.forecast_bundle import (
    BUNDLE_UNIT,
    ZERO_DRIFT,
    ConfirmedCaps,
    ForecastBundle,
    default_label_contract,
)
from intraday_equities.nodes_capital import (
    BUNDLE_FIELDS,
    NODE_KINDS,
    EquityKellyMIO,
    _bundle_problems,
)

KIND = "intraday_equities-kelly-mio"

RELEASE = "release-sha256-0f" * 4
CAP_PRODUCER_DOCUMENT_SHA256 = "a" * 64
CAP_EVIDENCE_SHA256 = "b" * 64
BUNDLE_PRODUCER_DOCUMENT_SHA256 = "c" * 64
MODEL_MANIFEST_SHA256 = "d" * 64
BUNDLE_PRODUCER = {
    "document_sha256": BUNDLE_PRODUCER_DOCUMENT_SHA256,
    "node": "forecast",
    "output": "bundle",
}

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
    "sec31_bps": 0.0206,
    "min_price": 5.0,
    "hfdr_q": 0.30,
    "band_bps": 10.0,
    "max_position_notional": 4000.0,
    "bundle_max_staleness_ms": 5000,
    "cap_max_staleness_ms": 5000,
}

ASOF_MS = 1_700_000_000_000


def _weights(n=8):
    return [1.0 / n] * n


def _row(entity, price, pi_upper, scenarios, decision_ts=ASOF_MS - 1000, weights=None):
    return {
        "entity": entity,
        "decision_ts": decision_ts,
        "lead": 3,
        "model_release_id": RELEASE,
        "unit": BUNDLE_UNIT,
        "price": price,
        "pi_hat": min(pi_upper, 0.10),
        "pi_upper": pi_upper,
        "weights": weights or _weights(len(scenarios)),
        "scenarios": list(scenarios),
        "reference_policy": ZERO_DRIFT,
        "label": default_label_contract(),
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "producer": dict(BUNDLE_PRODUCER),
        "known_at": {
            "sigma": decision_ts - 1,
            "beta": decision_ts - 1,
            "reference": decision_ts - 1,
            "price": decision_ts - 1,
            "yhat": decision_ts - 1,
            "pi_hat": decision_ts - 1,
            "pi_upper": decision_ts - 1,
            "scenarios": decision_ts - 1,
        },
    }


def _cap(**overrides):
    """One hash-pinnable synthetic cap artifact covering the demo names."""
    cap = {
        "schema_version": 2,
        "model_release_id": RELEASE,
        "deployment_eligible": False,
        "evidence_scope": "synthetic_mio_demo",
        "evidence_end_ms": ASOF_MS - 10_000_000,
        "generated_ms": ASOF_MS - 1000,
        "producer": {
            "document_sha256": CAP_PRODUCER_DOCUMENT_SHA256,
            "node": "confirm",
            "output": "cap",
        },
        "evidence": {
            "sha256": CAP_EVIDENCE_SHA256,
            "scope": "synthetic_mio_demo",
            "end_ms": ASOF_MS - 10_000_000,
        },
        "caps": [
            {"symbol": "AAPL", "capped_horizon": 10},
            {"symbol": "MSFT", "capped_horizon": 10},
            {"symbol": "XOM", "capped_horizon": 10},
        ],
    }
    cap.update(overrides)
    return cap


PARAMS.update(
    {
        "cap_artifact_sha256": ConfirmedCaps.digest(_cap()),
        "cap_producer_document_sha256": CAP_PRODUCER_DOCUMENT_SHA256,
        "cap_producer_node": "confirm",
        "cap_evidence_sha256": CAP_EVIDENCE_SHA256,
        "deployment_mode": False,
    }
)


def _bundle():
    w = _weights(8)
    return [
        _row("AAPL", 190.0, 0.20, [0.02, 0.01, -0.01, 0.015, -0.02, 0.0, 0.03, -0.01], weights=w),
        _row("MSFT", 410.0, 0.25, [0.01, 0.0, -0.005, 0.02, -0.01, 0.005, 0.01, -0.02], weights=w),
        _row("XOM", 110.0, 0.28, [0.005, -0.005, 0.0, 0.01, -0.01, 0.0, 0.005, -0.005], weights=w),
    ]


def _bundle_pins(bundle=None):
    bundle = _bundle() if bundle is None else bundle
    return {
        "bundle_artifact_sha256": ForecastBundle.digest(bundle),
        "bundle_producer_document_sha256": BUNDLE_PRODUCER_DOCUMENT_SHA256,
        "bundle_producer_node": "forecast",
        "bundle_model_manifest_sha256": MODEL_MANIFEST_SHA256,
    }


PARAMS.update(_bundle_pins())


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


def _node(cap=None, bundle=None, **params):
    cap = _cap() if cap is None else cap
    pins = {
        "cap_artifact_sha256": ConfirmedCaps.digest(cap),
        "cap_producer_document_sha256": cap.get("producer", {}).get(
            "document_sha256", CAP_PRODUCER_DOCUMENT_SHA256
        ),
        "cap_producer_node": cap.get("producer", {}).get("node", "confirm"),
        "cap_evidence_sha256": cap.get("evidence", {}).get(
            "sha256", CAP_EVIDENCE_SHA256
        ),
    }
    return EquityKellyMIO(
        "size", {**PARAMS, **_bundle_pins(bundle), **pins, **params}
    )


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
            "spread_bps", "taf_per_share", "sec31_bps", "min_price",
            "hfdr_q", "band_bps", "max_position_notional", "bundle_max_staleness_ms",
            "cap_max_staleness_ms", "cap_artifact_sha256",
            "cap_producer_document_sha256", "cap_producer_node",
            "cap_evidence_sha256", "deployment_mode",
            "bundle_artifact_sha256", "bundle_producer_document_sha256",
            "bundle_producer_node", "bundle_model_manifest_sha256",
        ],
    )
    def test_the_cost_and_risk_knobs_have_no_default(self, name):
        params = {k: v for k, v in PARAMS.items() if k != name}
        problems = EquityKellyMIO.validate_params(params)
        assert any(name in p and "required" in p for p in problems), problems

    def test_unknown_knobs_are_refused_by_name(self):
        problems = EquityKellyMIO.validate_params({**PARAMS, "bogus": 1})
        assert any("bogus" in p for p in problems)

    def test_taf_cap_is_not_a_knob(self):
        # A round-2 skeptic review found taf_cap declared, required and
        # validated but never READ anywhere — dead after the round-1 fix
        # replaced the size-referenced TAF-cap trick with a flat uncapped
        # rate. Removed entirely rather than left as an unused knob.
        problems = EquityKellyMIO.validate_params({**PARAMS, "taf_cap": 9.79})
        assert any("taf_cap" in p for p in problems)

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
        node = _node(bundle=[])
        out = node.run(
            _ctx(tmp_path),
            {"bundle": [], "portfolio": _portfolio(cash=500.0), "survivors": set(), "cap": _cap()},
        )
        assert out["target"] == {}
        assert out["metrics"]["objective"] == 0.0
        assert out["cash_after"] == 500.0
        assert out["evidence"]["n_bundle_rows"] == 0

    def test_an_unpinned_empty_bundle_refuses(self):
        problems = _node().validate_inputs(
            {"bundle": [], "portfolio": _portfolio(), "survivors": set(), "cap": _cap()}
        )
        assert any("bundle_artifact_sha256" in p for p in problems), problems

    @pytest.mark.parametrize("empty_bundle", [[], ()])
    def test_an_empty_bundle_cannot_authorize_liquidation(self, empty_bundle):
        problems = _node(bundle=empty_bundle).validate_inputs(
            {
                "bundle": empty_bundle,
                "portfolio": _portfolio(
                    positions={"AAPL": 1}, mark_prices={"AAPL": 190.0}
                ),
                "survivors": set(),
                "cap": _cap(),
            }
        )
        assert any("cannot authorize liquidation" in p for p in problems), problems


class TestRealSolve:
    def test_it_solves_within_every_declared_cap(self, tmp_path):
        node = _node()
        survivors = {"AAPL", "MSFT", "XOM"}
        out = node.run(
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap()}
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
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap()}
        )
        assert "XOM" not in out["target"]
        assert out["evidence"]["routed_out"]["XOM"] == "not a stat_test survivor"

    def test_a_stale_bundle_row_cannot_create_a_mixed_decision_batch(self, tmp_path):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], decision_ts=ASOF_MS - 999999)
        node = _node()
        with pytest.raises(ValueError, match="shared decision_ts"):
            node.run(
                _ctx(tmp_path),
                {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
            )

    def test_a_price_below_min_price_is_routed_out(self, tmp_path):
        bundle = _bundle()
        bundle[2] = dict(bundle[2], price=1.0)  # below min_price=5.0
        node = _node(bundle=bundle)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
        )
        assert "XOM" not in out["target"]
        assert "min_price" in out["evidence"]["routed_out"]["XOM"]

    def test_a_dropped_held_name_is_a_mandatory_full_exit(self, tmp_path):
        bundle = [row for row in _bundle() if row["entity"] != "XOM"]
        portfolio = _portfolio(positions={"XOM": 20}, mark_prices={"XOM": 108.0})
        node = _node(bundle=bundle)
        out = node.run(
            _ctx(tmp_path), {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()}
        )
        assert "XOM" not in out["target"]
        assert out["trades"]["XOM"] == {"buy": 0, "sell": 20}

    def test_the_hfdr_row_excludes_a_name_above_q(self, tmp_path):
        # Push XOM's pi_upper above hfdr_q with nothing to offset it — the
        # ADR-0088 row must refuse XOM exposure even though its own return
        # scenarios look attractive.
        bundle = _bundle()
        bundle[2] = dict(bundle[2], pi_upper=0.95)
        node = _node(bundle=bundle, hfdr_q=0.10)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
        )
        moved = out["trades"].get("AAPL", {"buy": 0, "sell": 0})
        total = moved["buy"] + moved["sell"]
        ticket = 190.0 * 100
        band_shares = int(-(-(1000.0 * 1e-4 * ticket) // 190.0))
        assert total == 0 or total >= band_shares

    def test_identical_inputs_give_identical_output_twice(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        inputs = {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap()}
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()}
        )
        assert names
        for name in names:
            assert rows[name]["exit_cost_per_share"] == pytest.approx(rows[name]["cost_sell"])
            assert rows[name]["exit_cost_per_share"] > 0.0

    def test_exit_cost_per_share_is_populated_on_the_mandatory_exit_branch_too(self, tmp_path):
        # A round-3 skeptic review flagged that the test above only
        # exercises the ordinary gated branch (price from the bundle row);
        # the mandatory-exit branch (price from portfolio.mark_prices) was
        # unpinned even though the code path is different.
        bundle = [row for row in _bundle() if row["entity"] != "XOM"]
        portfolio = _portfolio(positions={"XOM": 20}, mark_prices={"XOM": 108.0})
        node = _node()
        names, rows, _account = node.instruments(
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()}
        )
        assert "XOM" in names
        assert rows["XOM"]["exit_cost_per_share"] == pytest.approx(rows["XOM"]["cost_sell"])
        assert rows["XOM"]["exit_cost_per_share"] > 0.0

    def test_zeroing_exit_cost_understates_the_reported_cvar(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        portfolio = _portfolio(positions={"AAPL": 50}, cash=15000.0, buying_power=15000.0)
        inputs = {"bundle": _bundle(), "portfolio": portfolio, "survivors": survivors, "cap": _cap()}

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
        node = _node(bundle=bundle)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
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
                "survivors": set(), "cap": _cap(),
            }
        )
        assert names == ["AAPL"]
        assert rows["AAPL"]["cost_sell"] >= PARAMS["taf_per_share"] - 1e-12


class TestAdversarialInputsAreRefusedByName:
    """Regression for a skeptic-review round-2 finding: a fractional held
    position was silently truncated with no error at all (the one genuinely
    silent wrong-result path found), a negative position slipped past
    validation into an opaque solver crash, and NaN/Inf could sneak through
    ``decision_ts``/``weights``/``scenarios``/``mark_prices`` despite the
    module's own "fail-closed" claim. Every one of these must now be
    refused BY NAME in ``validate_inputs``, before ``run()`` ever sees it."""

    def test_a_fractional_position_is_refused_not_silently_truncated(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(positions={"XOM": 19.9}), "survivors": set(), "cap": _cap()}
        )
        assert any("XOM" in p and "integer" in p for p in problems)

    def test_a_negative_position_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(positions={"XOM": -5}), "survivors": set(), "cap": _cap()}
        )
        assert any("XOM" in p for p in problems)

    def test_a_nan_mark_price_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {
                "bundle": _bundle(),
                "portfolio": _portfolio(positions={"XOM": 5}, mark_prices={"XOM": float("nan")}),
                "survivors": set(), "cap": _cap(),
            }
        )
        assert any("mark_prices" in p and "XOM" in p for p in problems)

    def test_a_non_finite_decision_ts_is_refused(self, tmp_path):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], decision_ts=float("inf"))
        problems = _bundle_problems(bundle)
        assert any("decision_ts" in p for p in problems)

    def test_nan_scenario_values_are_refused(self, tmp_path):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], scenarios=[float("nan")] + list(bundle[0]["scenarios"])[1:])
        problems = _bundle_problems(bundle)
        assert any("scenarios" in p for p in problems)

    def test_non_finite_weights_are_refused(self, tmp_path):
        bundle = _bundle()
        bad_weights = [float("inf")] + list(bundle[0]["weights"])[1:]
        bundle[0] = dict(bundle[0], weights=bad_weights)
        problems = _bundle_problems(bundle)
        assert any("weights" in p for p in problems)


class TestNoTradeBandNeverStrandsAPosition:
    """Regression for a skeptic-review round-5 MAJOR: a legacy position
    smaller than the band's floor (band_shares > held) could be held or
    bought into, but never sold — a full exit needs sell >= band_shares,
    which is impossible once sell is capped at held. The solve stayed
    feasible and optimal; it just silently never executed an exit the
    objective clearly wanted. The sell-side floor must never exceed held."""

    def test_a_catastrophic_legacy_position_below_the_band_can_still_fully_exit(self, tmp_path):
        w = _weights(8)
        bundle = _bundle()
        # AAPL: uniformly catastrophic scenario returns — any reasonable
        # risk aversion should want it gone.
        bundle[0] = dict(bundle[0], scenarios=[-0.30] * 8, weights=w)
        portfolio = _portfolio(positions={"AAPL": 1})  # $190 notional, one lonely share
        # band_bps=10000 (100%) on a ticket floored at min_ticket=$200
        # gives band_shares=2 > held=1 — the exact "roach motel" shape.
        node = _node(bundle=bundle, band_bps=10000.0)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
        )
        assert "AAPL" not in out["target"]
        assert out["trades"]["AAPL"] == {"buy": 0, "sell": 1}


class TestNoTradeBandFloorsAreLoadBearing:
    """Regression for a skeptic-review round-6 finding: round 5's own
    regression test above only checks "0 or >= floor" in a scenario where
    the UNCONSTRAINED optimum already happens to land on one of those two
    values — proved by mutation that neither band_buy_floor nor
    band_sell_floor is actually exercised by the shipped suite (removing
    either leaves all other tests green). These two tests isolate each
    floor: each fixture's UNCONSTRAINED answer (band_bps=0) is a small
    "dust" trade strictly between 0 and the floor, so the floor's presence
    is the only thing that can move the answer to 0 or to the floor
    itself — verified against a deliberately mutated build before being
    committed here, per the review."""

    def test_the_buy_floor_blocks_a_dust_top_up(self, tmp_path):
        import numpy as np

        price, held = 190.0, 100
        band_bps = 1000.0  # -> band_shares = ceil(1000*1e-4*19000/190) = 10
        gross_limit = price * held + 5 * price  # room for exactly 5 more shares — < the floor
        rng = np.random.default_rng(7)
        weights = _weights(64)
        row = _row("AAPL", price, pi_upper=0.10, scenarios=rng.normal(0.02, 0.01, 64), weights=weights)
        portfolio = _portfolio(
            positions={"AAPL": held}, cash=999999.0, buying_power=999999.0, gross_limit=gross_limit
        )
        node = _node(band_bps=band_bps, cardinality=1, max_position_notional=999999.0)
        inputs = {"bundle": [row], "portfolio": portfolio, "survivors": {"AAPL"}, "cap": _cap()}
        # instruments() alone, not post-run state — run() clears its
        # transient bookkeeping in a finally (a round-10 skeptic-review fix).
        _names, _rows, _account = node.instruments(inputs)
        assert node._band_shares["AAPL"] == 10
        out = _node(bundle=[row], band_bps=band_bps, cardinality=1, max_position_notional=999999.0).run(
            _ctx(tmp_path), inputs
        )
        assert out["trades"] == {}  # blocked: only 5 shares of room, floor needs 10

    def test_the_sell_floor_forces_a_real_trim_not_a_dust_sell(self, tmp_path):
        import numpy as np

        price, held = 190.0, 1000
        band_bps = 1000.0  # -> band_shares = ceil(1000*1e-4*190000/190) = 100
        rng = np.random.default_rng(1)
        weights = _weights(64)
        row = _row("XOM", price, pi_upper=0.10, scenarios=rng.normal(0.015, 0.02, 64), weights=weights)
        portfolio = _portfolio(
            positions={"XOM": held}, cash=5000.0, buying_power=5000.0, gross_limit=None
        )
        # cvar_limit=3400: with band_bps=0 this fixture's unconstrained
        # CVaR-driven trim is 4 shares — comfortably below the floor of
        # 100, so the floor (not coincidence) is what forces the jump.
        node_params = dict(band_bps=band_bps, cardinality=1, cvar_alpha=0.9, cvar_limit=3400.0,
                            max_position_notional=999999.0)
        inputs = {"bundle": [row], "portfolio": portfolio, "survivors": {"XOM"}, "cap": _cap()}
        # instruments() alone, not post-run state — run() clears its
        # transient bookkeeping in a finally (a round-10 skeptic-review fix).
        node = _node(**node_params)
        node.instruments(inputs)
        assert node._band_shares["XOM"] == 100
        out = _node(bundle=[row], **node_params).run(_ctx(tmp_path), inputs)
        sold = out["trades"].get("XOM", {"sell": 0})["sell"]
        assert sold == 0 or sold >= 100
        assert sold != 4  # pin the exact "would-be dust trim" this fixture proves the floor blocks


class TestAccountFieldsAreValidated:
    """Regression for a skeptic-review round-7 finding: validate_inputs
    checked portfolio.cash/buying_power/positions/mark_prices but not its
    siblings cash_reserve/gross_limit/sale_credit, which instruments()
    reads unguarded — a NaN/Inf/negative/non-numeric value in any of them
    reached raw pyomo internals or an opaque solver infeasibility instead
    of a named refusal, contradicting this module's own fail-closed claim."""

    def test_nan_cash_reserve_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(cash_reserve=float("nan")), "survivors": set(), "cap": _cap()}
        )
        assert any("cash_reserve" in p for p in problems)

    def test_nan_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit=float("nan")), "survivors": set(), "cap": _cap()}
        )
        assert any("gross_limit" in p for p in problems)

    def test_a_negative_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit=-50.0), "survivors": set(), "cap": _cap()}
        )
        assert any("gross_limit" in p for p in problems)

    def test_a_non_numeric_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit="not-a-number"), "survivors": set(), "cap": _cap()}
        )
        assert any("gross_limit" in p for p in problems)

    def test_an_out_of_range_sale_credit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(sale_credit=1.5), "survivors": set(), "cap": _cap()}
        )
        assert any("sale_credit" in p for p in problems)

    def test_a_nan_sale_credit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(sale_credit=float("nan")), "survivors": set(), "cap": _cap()}
        )
        assert any("sale_credit" in p for p in problems)

    def test_a_non_string_survivor_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {12345}, "cap": _cap()}
        )
        assert any("survivors entries must be strings" in p for p in problems)

    def test_a_non_string_position_key_is_refused(self, tmp_path):
        # Regression for a round-8 skeptic-review finding: dict VALUES were
        # validated but KEYS were not, so {42: 100} passed validate_inputs
        # cleanly and crashed run() with a raw TypeError from sorting a
        # mixed str/int set.
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(positions={42: 100}), "survivors": set(), "cap": _cap()}
        )
        assert any("portfolio.positions keys" in p for p in problems)

    def test_a_non_string_mark_price_key_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {
                "bundle": _bundle(),
                "portfolio": _portfolio(mark_prices={42: 100.0}),
                "survivors": set(), "cap": _cap(),
            }
        )
        assert any("portfolio.mark_prices keys" in p for p in problems)

    def test_bundle_weights_not_summing_to_one_are_refused(self, tmp_path):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], weights=[0.1] * 8)  # sums to 0.8
        problems = _bundle_problems(bundle)
        assert any("must sum to 1" in p for p in problems)


class TestTransientStateIsClearedAfterRun:
    """Regression for a round-10 skeptic-review finding: the class
    docstring claimed _pi_upper/_band_shares/_payoffs/_evidence are "set
    by instruments and cleared by run", but run() never actually cleared
    them — a false claim and a missing defensive guard (the doorway's own
    _scn IS cleared in a finally). Not an observed wrong-output bug (a
    subsequent run's instruments() call always rebuilds all four before
    anything reads them), but a node instance should not carry a prior
    run's bookkeeping around once run() has returned."""

    def test_state_is_none_after_a_successful_run(self, tmp_path):
        node = _node()
        node.run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
        )
        assert node._pi_upper is None
        assert node._band_shares is None
        assert node._payoffs is None
        assert node._evidence is None

    def test_state_is_none_after_the_empty_gate_short_circuit(self, tmp_path):
        node = _node(bundle=[])
        node.run(
            _ctx(tmp_path),
            {"bundle": [], "portfolio": _portfolio(cash=500.0), "survivors": set(), "cap": _cap()},
        )
        assert node._pi_upper is None
        assert node._band_shares is None
        assert node._payoffs is None
        assert node._evidence is None


class TestNegativeNetWorthGivesTheClearMessage:
    """Regression for a round-11 skeptic-review finding: a DEEPLY negative
    net worth (cash far below the mark value of held positions) could
    surface the generic "wealth_lo must be < wealth_hi" refusal instead of
    the dedicated net-worth message, because EquityKellyMIO's own envelope
    derivation (wealth_lo = max(1.0, w0_mark - span)) can accidentally
    keep wealth_lo < wealth_hi even when net worth itself is deeply
    negative. Both refusals are loud and named — nothing was silent — but
    the net-worth message is the one that actually points an operator at
    cash/positions, the thing to fix, so it must win the race."""

    def test_a_deep_margin_debit_gets_the_net_worth_message(self, tmp_path):
        node = _node()
        portfolio = _portfolio(
            positions={"AAPL": 30}, cash=-6000.0, buying_power=1000.0
        )  # net worth = -6000 + 30*190 = -300
        with pytest.raises(ValueError, match="net worth"):
            node.run(
                _ctx(tmp_path),
                {"bundle": _bundle(), "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()},
            )


class TestSmallPositiveNetWorthSolvesInsteadOfRefusing:
    """Regression for a round-12 skeptic-review finding: the wealth-
    envelope's ``wealth_lo`` used a hardcoded ``max(1.0, ...)`` absolute
    dollar floor, which could exceed ``wealth_hi`` for a small POSITIVE net
    worth (net worth in roughly $0-$1) — a perfectly legitimate account
    state (e.g. a near-zero account holding one residual sub-dollar
    position the bundle no longer covers) spuriously refused with the
    generic "wealth_lo must be < wealth_hi" message. The floor is now
    relative to net worth itself, never an absolute dollar figure."""

    def test_a_near_zero_account_can_still_exit_its_one_residual_position(self, tmp_path):
        # The authenticated bundle carries AAPL but routes it below the
        # declared price floor, so liquidation authority remains explicit.
        bundle = [_row("AAPL", 0.005, 0.10, [-0.30] * 8)]
        node = _node(
            bundle=bundle,
            n_tangents=16,
            n_scenarios_max=128,
            min_ticket=0.1,
            min_price=0.01,
            hfdr_q=0.90,
            max_position_notional=999999.0,
        )
        portfolio = {
            "asof_ms": ASOF_MS, "cash": 0.0, "buying_power": 0.0, "positions": {"AAPL": 1},
            "mark_prices": {"AAPL": 0.50}, "cash_reserve": 0.0, "gross_limit": None,
            "sale_credit": 1.0,
        }
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL"}, "cap": _cap()},
        )
        assert "AAPL" not in out["target"]
        assert out["trades"]["AAPL"] == {"buy": 0, "sell": 1}


class TestExtendedBundleContract:
    """Gate 4 (ADR-0121): a lead, a release identity, and a gross unit on
    every bundle row — a vol-scaled SPY-residual prediction is never
    accepted as a gross return (plan §6 Phase 4 item 1)."""

    def test_a_missing_lead_is_refused_by_name(self):
        bad = [dict(_bundle()[0])]
        del bad[0]["lead"]
        problems = _bundle_problems(bad)
        assert any("lead" in p for p in problems), problems

    def test_a_missing_model_release_id_is_refused_by_name(self):
        bad = [dict(_bundle()[0])]
        del bad[0]["model_release_id"]
        problems = _bundle_problems(bad)
        assert any("model_release_id" in p for p in problems), problems

    def test_a_missing_unit_is_refused_by_name(self):
        bad = [dict(_bundle()[0])]
        del bad[0]["unit"]
        problems = _bundle_problems(bad)
        assert any("unit" in p for p in problems), problems

    def test_a_label_unit_row_is_never_accepted_as_a_gross_return(self):
        bad = _bundle()
        bad[0] = dict(bad[0], unit="vol_scaled_residual")
        problems = _bundle_problems(bad)
        assert any("gross_fractional_return" in p for p in problems), problems

    def test_a_lead_outside_the_ten_heads_is_refused(self):
        bad = _bundle()
        bad[0] = dict(bad[0], lead=11)
        problems = _bundle_problems(bad)
        assert any("lead" in p for p in problems), problems

    def test_a_non_integer_lead_is_refused(self):
        bad = _bundle()
        bad[0] = dict(bad[0], lead=2.5)
        problems = _bundle_problems(bad)
        assert any("lead" in p for p in problems), problems

    def test_mixed_leads_in_one_bundle_refuse(self):
        bad = _bundle()
        bad[1] = dict(bad[1], lead=4)
        problems = _bundle_problems(bad)
        assert any("lead" in p and "shared" in p for p in problems), problems

    def test_mixed_releases_in_one_bundle_refuse(self):
        bad = _bundle()
        bad[1] = dict(bad[1], model_release_id="another-release")
        problems = _bundle_problems(bad)
        assert any("model_release_id" in p and "shared" in p for p in problems), problems

    def test_mixed_decision_timestamps_in_one_bundle_refuse(self):
        bad = _bundle()
        bad[1] = dict(bad[1], decision_ts=ASOF_MS - 2000)
        problems = _bundle_problems(bad)
        assert any("decision_ts" in p and "shared" in p for p in problems), problems

    def test_fractional_future_decision_timestamp_refuses_before_truncation(self):
        bad = _bundle()
        bad[0] = dict(bad[0], decision_ts=ASOF_MS + 0.5)
        problems = _bundle_problems(bad)
        assert any("decision_ts" in p and "integer" in p for p in problems), problems

    def test_missing_label_and_point_in_time_audit_refuse(self):
        bad = _bundle()
        del bad[0]["label"]
        del bad[0]["known_at"]
        problems = _bundle_problems(bad)
        assert any("label" in p for p in problems), problems
        assert any("known_at" in p for p in problems), problems


class TestConfirmedCapEnforcement:
    """Gate 4 (ADR-0121): the pinned confirmed (symbol, lead) cap is a
    required input and is enforced before optimization — absent, zero and
    over-cap rows route out; stale, provenance-mismatched, mode-ineligible,
    and wrong-release caps refuse outright. The stat_test survivor wire is
    unchanged."""

    def test_the_reference_cap_input_validates_clean(self):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap()}
        )
        assert problems == [], problems

    def test_a_missing_cap_input_is_refused(self):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": set()}
        )
        assert any("cap is required" in p for p in problems), problems

    def test_a_malformed_cap_artifact_is_refused_by_name(self):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": set(), "cap": {"schema_version": 1}}
        )
        assert any("cap" in p for p in problems), problems

    def test_a_deployable_cap_refuses_in_explicit_development_mode(self):
        cap = _cap(deployment_eligible=True)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap,
            }
        )
        assert any("deployment_eligible" in p for p in problems), problems

    def test_deployment_mode_fails_closed_without_a_trusted_real_producer(self):
        cap = _cap(deployment_eligible=True)
        node = _node(cap=cap, deployment_mode=True)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap,
            }
        )
        assert any("trusted real" in p for p in problems), problems

    def test_a_cap_that_differs_from_the_config_digest_pin_refuses(self):
        cap = _cap(caps=[{"symbol": "AAPL", "capped_horizon": 2}])
        problems = _node().validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap,
            }
        )
        assert any("cap_artifact_sha256" in p for p in problems), problems

    def test_a_bundle_that_differs_from_the_config_digest_pin_refuses(self):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], scenarios=[99.0] * 8)
        problems = _node().validate_inputs(
            {
                "bundle": bundle, "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": _cap(),
            }
        )
        assert any("bundle_artifact_sha256" in p for p in problems), problems

    def test_a_cap_generated_after_the_bundle_decision_refuses(self):
        decision_ts = _bundle()[0]["decision_ts"]
        cap = _cap(
            evidence_end_ms=decision_ts,
            generated_ms=decision_ts + 1,
            evidence={
                "sha256": CAP_EVIDENCE_SHA256,
                "scope": "synthetic_mio_demo",
                "end_ms": decision_ts,
            },
        )
        problems = _node(cap=cap).validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap,
            }
        )
        assert any("after bundle decision_ts" in p for p in problems), problems

    def test_fractional_future_cap_timestamp_refuses_before_truncation(self):
        cap = _cap(generated_ms=ASOF_MS + 0.5)
        problems = _node(cap=cap).validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap,
            }
        )
        assert any("generated_ms" in p and "integer" in p for p in problems), problems

    def test_a_stale_cap_is_refused(self):
        cap = _cap(generated_ms=ASOF_MS - 999_999)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap,
            }
        )
        assert any("stale" in p for p in problems), problems

    def test_a_future_dated_cap_is_refused(self):
        cap = _cap(generated_ms=ASOF_MS + 10_000)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap,
            }
        )
        assert any("cap" in p and ("future" in p or "stale" in p) for p in problems), problems

    def test_a_cap_for_the_wrong_model_release_is_refused(self):
        cap = _cap(model_release_id="a-different-release")
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap,
            }
        )
        assert any("model_release_id" in p for p in problems), problems

    def test_a_symbol_absent_from_the_cap_is_routed_out(self, tmp_path):
        cap = _cap(caps=[{"symbol": "AAPL", "capped_horizon": 10}])
        out = _node(cap=cap).run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap},
        )
        assert "MSFT" not in out["target"]
        assert "XOM" not in out["target"]
        assert "no confirmed cap" in out["evidence"]["routed_out"]["MSFT"]

    def test_a_zero_cap_routes_the_symbol_out(self, tmp_path):
        cap = _cap(caps=[
            {"symbol": "AAPL", "capped_horizon": 10},
            {"symbol": "MSFT", "capped_horizon": 10},
            {"symbol": "XOM", "capped_horizon": 0},
        ])
        out = _node(cap=cap).run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap},
        )
        assert "XOM" not in out["target"]
        assert "zero" in out["evidence"]["routed_out"]["XOM"]

    def test_a_lead_above_the_confirmed_cap_is_routed_out(self, tmp_path):
        cap = _cap(caps=[
            {"symbol": "AAPL", "capped_horizon": 2},  # bundle rows sit at lead 3
            {"symbol": "MSFT", "capped_horizon": 10},
            {"symbol": "XOM", "capped_horizon": 10},
        ])
        out = _node(cap=cap).run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap},
        )
        assert "AAPL" not in out["target"]
        assert "above" in out["evidence"]["routed_out"]["AAPL"]

    def test_a_within_cap_lead_still_sizes(self, tmp_path):
        # lead 3 inside a capped_horizon of 3 is the boundary's inclusive
        # edge — the row must survive the cap gate and size normally.
        cap = _cap(caps=[
            {"symbol": "AAPL", "capped_horizon": 3},
            {"symbol": "MSFT", "capped_horizon": 10},
            {"symbol": "XOM", "capped_horizon": 10},
        ])
        out = _node(cap=cap).run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap},
        )
        assert "AAPL" not in out["evidence"]["routed_out"]

    def test_a_bad_cap_refuses_at_run_time_too_not_just_validation(self, tmp_path):
        # run() is callable directly (the tests above do); a development
        # node must reject a deployment-marked cap, never silently size.
        cap = _cap(deployment_eligible=True)
        node = _node(cap=cap)
        with pytest.raises(ValueError, match="deployment_eligible"):
            node.run(
                _ctx(tmp_path),
                {
                    "bundle": _bundle(), "portfolio": _portfolio(),
                    "survivors": {"AAPL", "MSFT", "XOM"},
                    "cap": cap,
                },
            )

    def test_a_missing_cap_input_refuses_at_run_time_too(self, tmp_path):
        node = _node()
        with pytest.raises(ValueError, match="cap"):
            node.run(
                _ctx(tmp_path),
                {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": set()},
            )

    def test_the_stat_test_wire_still_gates_before_the_cap(self, tmp_path):
        # A name that is BOTH a non-survivor and cap-covered is routed for
        # the stat_test reason — the survivor wire keeps its own message.
        survivors = {"AAPL", "MSFT"}  # XOM covered by cap but not a survivor
        out = _node().run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap()},
        )
        assert out["evidence"]["routed_out"]["XOM"] == "not a stat_test survivor"
