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

from intraday_equities.final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from intraday_equities.forecast_bundle import (
    BUNDLE_UNIT,
    ZERO_DRIFT,
    ConfirmedCaps,
    ForecastBundle,
    default_label_contract,
)
from dskit.pipeline.false_signal import FalseSignalEstimate, GrenanderLocalFdr
from dskit.pipeline.mean_interval import ClusterBootstrapInterval, MeanEvidence
from dskit.pipeline.node import class_ref
from dskit.pipeline.outcome_interval import (
    BlockConformalInterval,
    BlockResiduals,
    TwoSidedBlockConformalInterval,
)
from dskit.pipeline.uncertainty_intake import (
    CLOSED_FAMILIES,
    REFUSAL_REASONS,
    AttestedFalseSignalRate,
    AttestedMeanConfidence,
    AttestedOutcomeBand,
    CoverageEvidence,
    DecisionDemand,
    ProbabilityUpperBound,
    UncertaintyAttestation,
)

from intraday_equities.nodes_capital import (
    BUNDLE_FIELDS,
    NODE_KINDS,
    EquityKellyMIO,
    SchwabCostModel,
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

#: The one instant this tick's bundle, cap and uncertainty artifacts must
#: all agree on — the audit's "a single decision timestamp".
DECISION_TS = ASOF_MS - 1000
FALSE_SIGNAL_ID = "fs-calibration-0001"
OUTCOME_ID = "outcome-calibration-0001"
UNCERTAINTY_IDS = {"false_signal": FALSE_SIGNAL_ID, "outcome": OUTCOME_ID}

#: Owner-declared intake policy. Neither number is derived from anything
#: measured here — they are the REQUIRED config a document must state,
#: exercised at a value that lets the attested fixtures pass.
UNCERTAINTY_PARAMS = {
    "uncertainty_max_calibration_age_ms": 600_000,
    "uncertainty_min_coverage": 0.90,
}
PARAMS.update(UNCERTAINTY_PARAMS)

#: Per-entity false-signal rates. ``pi_hat`` is DELIBERATELY not a function
#: of ``pi_widened``: round-1 review proved the old fixture clamped it to
#: ``min(pi_widened, 0.10)``, a name-independent constant, which made the
#: two candidate fields indistinguishable in exactly the test written to
#: distinguish them — swapping the HFDR row's field source left 127 tests
#: green. Independent values, plus the behavioural pin in
#: ``TestTheHfdrRowReadsTheWidenedField``, are what hold the field source
#: down now.
PI_WIDENED_BY_ENTITY = {"AAPL": 0.20, "MSFT": 0.25, "XOM": 0.28}
PI_HAT_BY_ENTITY = {"AAPL": 0.04, "MSFT": 0.11, "XOM": 0.19}


def _weights(n=8):
    return [1.0 / n] * n


def _ascending_weights(n=8):
    """Distinct, ascending scenario probabilities, so their ORDER matters."""
    total = n * (n + 1) / 2.0
    return [(i + 1) / total for i in range(n)]


def _row(
    entity,
    price,
    pi_widened,
    scenarios,
    decision_ts=DECISION_TS,
    weights=None,
    pi_hat=None,
):
    pi_hat = PI_HAT_BY_ENTITY[entity] if pi_hat is None else pi_hat
    return {
        "entity": entity,
        "decision_ts": decision_ts,
        "lead": 3,
        "model_release_id": RELEASE,
        "unit": BUNDLE_UNIT,
        "price": price,
        "pi_hat": pi_hat,
        "pi_widened": pi_widened,
        "weights": weights or _weights(len(scenarios)),
        "scenarios": list(scenarios),
        "reference_policy": ZERO_DRIFT,
        "label": default_label_contract(),
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "producer": dict(BUNDLE_PRODUCER),
        "uncertainty": dict(UNCERTAINTY_IDS),
        "known_at": {
            "sigma": decision_ts - 1,
            "beta": decision_ts - 1,
            "reference": decision_ts - 1,
            "price": decision_ts - 1,
            "yhat": decision_ts - 1,
            "pi_hat": decision_ts - 1,
            "pi_widened": decision_ts - 1,
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
        del bad[0]["pi_widened"]
        problems = _bundle_problems(bad)
        assert any("pi_widened" in p for p in problems)

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
            {"bundle": [], "portfolio": _portfolio(cash=500.0), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty([])},
        )
        assert out["target"] == {}
        assert out["metrics"]["objective"] == 0.0
        assert out["cash_after"] == 500.0
        assert out["evidence"]["n_bundle_rows"] == 0

    def test_an_unpinned_empty_bundle_refuses(self):
        problems = _node().validate_inputs(
            {"bundle": [], "portfolio": _portfolio(), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty([])}
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
                "cap": _cap(), "uncertainty": _uncertainty(empty_bundle),
            }
        )
        assert any("cannot authorize liquidation" in p for p in problems), problems


class TestRealSolve:
    def test_it_solves_within_every_declared_cap(self, tmp_path):
        node = _node()
        survivors = {"AAPL", "MSFT", "XOM"}
        out = node.run(
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
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
            _ctx(tmp_path), {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
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
                {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
            )

    def test_a_price_below_min_price_is_routed_out(self, tmp_path):
        bundle = _bundle()
        bundle[2] = dict(bundle[2], price=1.0)  # below min_price=5.0
        node = _node(bundle=bundle)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
        )
        assert "XOM" not in out["target"]
        assert "min_price" in out["evidence"]["routed_out"]["XOM"]

    def test_a_dropped_held_name_is_a_mandatory_full_exit(self, tmp_path):
        bundle = [row for row in _bundle() if row["entity"] != "XOM"]
        portfolio = _portfolio(positions={"XOM": 20}, mark_prices={"XOM": 108.0})
        node = _node(bundle=bundle)
        out = node.run(
            _ctx(tmp_path), {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)}
        )
        assert "XOM" not in out["target"]
        assert out["trades"]["XOM"] == {"buy": 0, "sell": 20}
        # The mandatory-exit name's HFDR coefficient is inert BECAUSE its
        # x_max is 0 — the coefficient sweep showed changing that
        # coefficient cannot move the answer. What must stay true, and is
        # asserted here rather than left implicit, is that a legacy
        # position being unwound never blocks the surviving names from
        # being funded.
        assert "AAPL" in out["target"]

    def test_the_hfdr_row_excludes_a_name_above_q(self, tmp_path):
        # Push XOM's pi_widened above hfdr_q with nothing to offset it — the
        # ADR-0088 row must refuse XOM exposure even though its own return
        # scenarios look attractive.
        bundle = _bundle()
        bundle[2] = dict(bundle[2], pi_widened=0.95)
        node = _node(bundle=bundle, hfdr_q=0.10)
        out = node.run(
            _ctx(tmp_path),
            {"bundle": bundle, "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
        )
        moved = out["trades"].get("AAPL", {"buy": 0, "sell": 0})
        total = moved["buy"] + moved["sell"]
        ticket = 190.0 * 100
        band_shares = int(-(-(1000.0 * 1e-4 * ticket) // 190.0))
        assert total == 0 or total >= band_shares

    def test_identical_inputs_give_identical_output_twice(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        inputs = {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)}
        )
        assert "XOM" in names
        assert rows["XOM"]["exit_cost_per_share"] == pytest.approx(rows["XOM"]["cost_sell"])
        assert rows["XOM"]["exit_cost_per_share"] > 0.0

    def test_zeroing_exit_cost_understates_the_reported_cvar(self, tmp_path):
        survivors = {"AAPL", "MSFT", "XOM"}
        portfolio = _portfolio(positions={"AAPL": 50}, cash=15000.0, buying_power=15000.0)
        inputs = {"bundle": _bundle(), "portfolio": portfolio, "survivors": survivors, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}

        real_out = _node(cvar_limit=100000.0).run(_ctx(tmp_path), inputs)

        class ZeroExitCost(EquityKellyMIO):
            def instruments(self, inp):
                names, rows, account = super().instruments(inp)
                for row in rows.values():
                    row["exit_cost_per_share"] = 0.0
                return names, rows, account

        zero_out = ZeroExitCost("size2", {**PARAMS, "cvar_limit": 100000.0}).run(_ctx(tmp_path), inputs)
        assert zero_out["metrics"]["cvar"] < real_out["metrics"]["cvar"]


class TestTheDoorwayHooksReadTheDeclaredFields:
    """Each value this node hands the MILP comes from the field it names.

    Round-1 review found the HFDR coefficient's field source unpinned; a
    sweep over every other field a constraint or objective coefficient
    reads found three more mutations the suite could not see. These are
    assertions on the doorway hooks' own declared outputs — the shape
    ``TestExitCostIsPriced`` already uses — not on internal state.
    """

    def test_cost_buy_is_the_declared_half_spread(self):
        # `"cost_buy": spread -> 0.0` survived the whole suite: the entry
        # half-spread also reaches cost_sell and exit_cost_per_share, so a
        # comparative solve at two spread_bps values still differs even
        # when the buy side is dead.
        node = _node()
        _names, rows, _account = node.instruments(_uinputs())
        costs = SchwabCostModel(
            {name: PARAMS[name] for name in SchwabCostModel._PARAMS}
        )
        for name, row in rows.items():
            assert row["cost_buy"] == costs.buy_per_share(row["price"])
        assert any(row["cost_buy"] > 0.0 for row in rows.values())

    def test_cost_buy_tracks_the_configured_rate(self):
        node = _node(spread_bps=50.0)
        _names, rows, _account = node.instruments(_uinputs())
        for name, row in rows.items():
            assert row["cost_buy"] == pytest.approx(row["price"] * 50.0 * 1e-4)

    def test_the_payoff_weights_are_the_bundles_declared_weights(self):
        # Uniform weights make their ORDER unobservable, so this case
        # declares ascending ones: reversing the vector in `instruments`
        # is then a different distribution, and is caught here.
        weights = _ascending_weights(8)
        bundle = [
            dict(row, weights=list(weights))
            for row in _bundle()
        ]
        node = _node(bundle=bundle)
        node.instruments(_uinputs(bundle=bundle))
        emitted, matrix = node.payoffs(_uinputs(bundle=bundle))
        assert emitted == weights
        assert emitted != list(reversed(weights))
        for row in bundle:
            assert matrix[row["entity"]] == [float(v) for v in row["scenarios"]]

    def test_the_payoff_matrix_is_the_bundles_scenarios(self):
        bundle = _bundle()
        node = _node(bundle=bundle)
        node.instruments(_uinputs(bundle=bundle))
        _weights_out, matrix = node.payoffs(_uinputs(bundle=bundle))
        for row in bundle:
            assert matrix[row["entity"]] == [float(v) for v in row["scenarios"]]


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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
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
                "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty([]),
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
            {"bundle": _bundle(), "portfolio": _portfolio(positions={"XOM": 19.9}), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("XOM" in p and "integer" in p for p in problems)

    def test_a_negative_position_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(positions={"XOM": -5}), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("XOM" in p for p in problems)

    def test_a_nan_mark_price_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {
                "bundle": _bundle(),
                "portfolio": _portfolio(positions={"XOM": 5}, mark_prices={"XOM": float("nan")}),
                "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle()),
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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
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
        row = _row("AAPL", price, pi_widened=0.10, scenarios=rng.normal(0.02, 0.01, 64), weights=weights)
        portfolio = _portfolio(
            positions={"AAPL": held}, cash=999999.0, buying_power=999999.0, gross_limit=gross_limit
        )
        node = _node(band_bps=band_bps, cardinality=1, max_position_notional=999999.0)
        inputs = {"bundle": [row], "portfolio": portfolio, "survivors": {"AAPL"}, "cap": _cap(), "uncertainty": _uncertainty([row])}
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
        row = _row(
            "XOM", price, pi_widened=0.10, scenarios=rng.normal(0.015, 0.02, 64),
            weights=weights, pi_hat=0.02,
        )
        portfolio = _portfolio(
            positions={"XOM": held}, cash=5000.0, buying_power=5000.0, gross_limit=None
        )
        # cvar_limit=3400: with band_bps=0 this fixture's unconstrained
        # CVaR-driven trim is 4 shares — comfortably below the floor of
        # 100, so the floor (not coincidence) is what forces the jump.
        node_params = dict(band_bps=band_bps, cardinality=1, cvar_alpha=0.9, cvar_limit=3400.0,
                            max_position_notional=999999.0)
        inputs = {"bundle": [row], "portfolio": portfolio, "survivors": {"XOM"}, "cap": _cap(), "uncertainty": _uncertainty([row])}
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
            {"bundle": _bundle(), "portfolio": _portfolio(cash_reserve=float("nan")), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("cash_reserve" in p for p in problems)

    def test_nan_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit=float("nan")), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("gross_limit" in p for p in problems)

    def test_a_negative_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit=-50.0), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("gross_limit" in p for p in problems)

    def test_a_non_numeric_gross_limit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(gross_limit="not-a-number"), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("gross_limit" in p for p in problems)

    def test_an_out_of_range_sale_credit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(sale_credit=1.5), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("sale_credit" in p for p in problems)

    def test_a_nan_sale_credit_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(sale_credit=float("nan")), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("sale_credit" in p for p in problems)

    def test_a_non_string_survivor_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {12345}, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("survivors entries must be strings" in p for p in problems)

    def test_a_non_string_position_key_is_refused(self, tmp_path):
        # Regression for a round-8 skeptic-review finding: dict VALUES were
        # validated but KEYS were not, so {42: 100} passed validate_inputs
        # cleanly and crashed run() with a raw TypeError from sorting a
        # mixed str/int set.
        node = _node()
        problems = node.validate_inputs(
            {"bundle": _bundle(), "portfolio": _portfolio(positions={42: 100}), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
        )
        assert any("portfolio.positions keys" in p for p in problems)

    def test_a_non_string_mark_price_key_is_refused(self, tmp_path):
        node = _node()
        problems = node.validate_inputs(
            {
                "bundle": _bundle(),
                "portfolio": _portfolio(mark_prices={42: 100.0}),
                "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty(_bundle()),
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(_bundle())},
        )
        assert node._pi_widened is None
        assert node._band_shares is None
        assert node._payoffs is None
        assert node._evidence is None

    def test_state_is_none_after_the_empty_gate_short_circuit(self, tmp_path):
        node = _node(bundle=[])
        node.run(
            _ctx(tmp_path),
            {"bundle": [], "portfolio": _portfolio(cash=500.0), "survivors": set(), "cap": _cap(), "uncertainty": _uncertainty([])},
        )
        assert node._pi_widened is None
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
                {"bundle": _bundle(), "portfolio": portfolio, "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(_bundle())},
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
            {"bundle": bundle, "portfolio": portfolio, "survivors": {"AAPL"}, "cap": _cap(), "uncertainty": _uncertainty(bundle)},
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": _cap(), "uncertainty": _uncertainty(_bundle())}
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": set(), "cap": {"schema_version": 1}, "uncertainty": _uncertainty(_bundle())}
        )
        assert any("cap" in p for p in problems), problems

    def test_a_deployable_cap_refuses_in_explicit_development_mode(self):
        cap = _cap(deployment_eligible=True)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("deployment_eligible" in p for p in problems), problems

    def test_deployment_mode_fails_closed_without_a_trusted_real_producer(self):
        cap = _cap(deployment_eligible=True)
        node = _node(cap=cap, deployment_mode=True)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("trusted real" in p for p in problems), problems

    def test_a_cap_that_differs_from_the_config_digest_pin_refuses(self):
        cap = _cap(caps=[{"symbol": "AAPL", "capped_horizon": 2}])
        problems = _node().validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("cap_artifact_sha256" in p for p in problems), problems

    def test_a_bundle_that_differs_from_the_config_digest_pin_refuses(self):
        bundle = _bundle()
        bundle[0] = dict(bundle[0], scenarios=[99.0] * 8)
        problems = _node().validate_inputs(
            {
                "bundle": bundle, "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": _cap(), "uncertainty": _uncertainty(bundle),
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
                "survivors": {"AAPL"}, "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("after bundle decision_ts" in p for p in problems), problems

    def test_fractional_future_cap_timestamp_refuses_before_truncation(self):
        cap = _cap(generated_ms=ASOF_MS + 0.5)
        problems = _node(cap=cap).validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(),
                "survivors": {"AAPL"}, "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("generated_ms" in p and "integer" in p for p in problems), problems

    def test_a_stale_cap_is_refused(self):
        cap = _cap(generated_ms=ASOF_MS - 999_999)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("stale" in p for p in problems), problems

    def test_a_future_dated_cap_is_refused(self):
        cap = _cap(generated_ms=ASOF_MS + 10_000)
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("cap" in p and ("future" in p or "stale" in p) for p in problems), problems

    def test_a_cap_for_the_wrong_model_release_is_refused(self):
        cap = _cap(model_release_id="a-different-release")
        node = _node(cap=cap)
        problems = node.validate_inputs(
            {
                "bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"},
                "cap": cap, "uncertainty": _uncertainty(_bundle()),
            }
        )
        assert any("model_release_id" in p for p in problems), problems

    def test_a_symbol_absent_from_the_cap_is_routed_out(self, tmp_path):
        cap = _cap(caps=[{"symbol": "AAPL", "capped_horizon": 10}])
        out = _node(cap=cap).run(
            _ctx(tmp_path),
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap, "uncertainty": _uncertainty(_bundle())},
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap, "uncertainty": _uncertainty(_bundle())},
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap, "uncertainty": _uncertainty(_bundle())},
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": {"AAPL", "MSFT", "XOM"}, "cap": cap, "uncertainty": _uncertainty(_bundle())},
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
                    "cap": cap, "uncertainty": _uncertainty(_bundle()),
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
            {"bundle": _bundle(), "portfolio": _portfolio(), "survivors": survivors, "cap": _cap(), "uncertainty": _uncertainty(_bundle())},
        )
        assert out["evidence"]["routed_out"]["XOM"] == "not a stat_test survivor"


# ---------------------------------------------------------------------------
# EQ-02: attested uncertainty at the capital boundary.
#
# The audit's acceptance test, verbatim: "Feed an apparently well-formed
# bundle whose uncertainty artifact is stale, wrong-unit, post-decision,
# uncalibrated or from a different model. Capital must refuse."  Each case
# below changes exactly ONE thing about the attestation and leaves the
# bundle, cap, portfolio and survivors identical to the passing case.
# ---------------------------------------------------------------------------

def _coverage(measured=0.94):
    """One producer's attested out-of-sample coverage measurement."""
    return CoverageEvidence(
        target=0.95,
        measured=measured,
        evidence_id="synthetic-coverage-probe",
        n_units=40,
    )


#: The registered producers the child's fixtures attest. dskit's intake
#: screen checks the attested producer against the estimand's registry AND
#: against the artifact's own self-report.
RATE_PRODUCER = class_ref(GrenanderLocalFdr)
BAND_PRODUCER = class_ref(BlockConformalInterval)
MEAN_PRODUCER = class_ref(ClusterBootstrapInterval)


def _attestation(artifact_id, producer=BAND_PRODUCER, **overrides):
    base = {
        "artifact_id": artifact_id,
        "model_identity": RELEASE,
        "calibration_end_ms": DECISION_TS - 60_000,
        "known_at_ms": DECISION_TS - 30_000,
        "producer": producer,
        "coverage": _coverage(),
    }
    base.update(overrides)
    return UncertaintyAttestation(**base)


def _false_signal_artifact(bundle=None):
    """The per-entity rates, read off the rows they will be checked against.

    Assembled by hand rather than fitted: these tests need rates that MATCH
    arbitrary fixture rows (a 0.95 widened rate for one name, say), which no
    real fit can be asked to produce on demand. It therefore travels on the
    limit dskit discloses and pins — an artifact that merely NAMES a
    registered producer is admitted. The child's job here is the wiring; the
    SHIPPED producer (``testing.SyntheticMioSource``) goes through the real
    registered estimator instead.
    """
    rows = _bundle() if not bundle else bundle
    return FalseSignalEstimate(
        pi_hat={row["entity"]: row["pi_hat"] for row in rows},
        pi_widened={row["entity"]: row["pi_widened"] for row in rows},
        evidence={"estimator": RATE_PRODUCER},
    )


def _outcome_artifact(bundle=None):
    """A block-conformal band over the entities the tick is sizing."""
    rows = _bundle() if not bundle else bundle
    names = tuple(sorted({row["entity"] for row in rows}))
    panel = [
        tuple(
            0.01 * (r + 1) * (c + 1) * (1 if (r + c) % 2 == 0 else -1)
            for c in range(len(names))
        )
        for r in range(8)
    ]
    residuals = BlockResiduals(
        names=names,
        rows=panel,
        blocks=["s1", "s1", "s2", "s2", "s3", "s3", "s4", "s4"],
    )
    return BlockConformalInterval().calibrate(residuals, coverage=0.6, window_blocks=2)


def _mean_artifact():
    """A real fitted mean interval — the wrong ESTIMAND for the outcome slot."""
    values = [0.004 + 0.01 * ((i % 7) - 3) for i in range(40)]
    units = [f"u{i // 2}" for i in range(40)]
    return ClusterBootstrapInterval(replicates=200, seed=3).interval(
        MeanEvidence(values=values, units=units)
    )


def _uncertainty(bundle=None, false_signal=None, outcome=None):
    """The `uncertainty` port: one attested envelope per estimand.

    Both artifacts are DERIVED from the bundle they will be checked
    against, which is what a sound producer does. Their agreeing with the
    rows is the baseline; what the tests below vary is the ATTESTATION.
    """
    if false_signal is None:
        false_signal = AttestedFalseSignalRate(
            _false_signal_artifact(bundle),
            _attestation(FALSE_SIGNAL_ID, producer=RATE_PRODUCER),
        )
    if outcome is None:
        outcome = AttestedOutcomeBand(
            _outcome_artifact(bundle), _attestation(OUTCOME_ID)
        )
    return {"false_signal": false_signal, "outcome": outcome}


def _uinputs(bundle=None, uncertainty=None, cap=None, portfolio=None):
    bundle = _bundle() if bundle is None else bundle
    return {
        "bundle": bundle,
        "portfolio": _portfolio() if portfolio is None else portfolio,
        "survivors": ["AAPL", "MSFT", "XOM"],
        "cap": _cap() if cap is None else cap,
        "uncertainty": _uncertainty(bundle) if uncertainty is None else uncertainty,
    }


class TestAttestedUncertaintyIsRequired:
    """The port exists, is required, and every row's ids must match it."""

    def test_a_bundle_with_no_uncertainty_port_refuses(self):
        node = _node()
        inputs = _uinputs()
        del inputs["uncertainty"]
        problems = node.validate_inputs(inputs)
        assert any("uncertainty" in p for p in problems), problems

    def test_the_two_intake_policy_knobs_have_no_default(self):
        for name in UNCERTAINTY_PARAMS:
            params = {**PARAMS, **UNCERTAINTY_PARAMS, **_bundle_pins(_bundle())}
            params.pop(name)
            problems = EquityKellyMIO.validate_params(params)
            assert any(name in p for p in problems), (name, problems)

    def test_a_row_naming_a_different_calibration_refuses(self):
        bundle = _bundle()
        bundle[1]["uncertainty"] = {
            "false_signal": "some-other-calibration",
            "outcome": OUTCOME_ID,
        }
        node = _node(bundle=bundle)
        problems = node.validate_inputs(_uinputs(bundle=bundle))
        assert any("some-other-calibration" in p for p in problems), problems


class TestCapitalRefusesAnInvalidUncertaintyArtifact:
    """Five refusals, one per reason, each on an otherwise well-formed bundle."""

    def test_a_stale_calibration_refuses(self):
        stale = AttestedOutcomeBand(
            _outcome_artifact(),
            _attestation(
                OUTCOME_ID,
                calibration_end_ms=DECISION_TS
                - UNCERTAINTY_PARAMS["uncertainty_max_calibration_age_ms"]
                - 1,
                known_at_ms=DECISION_TS - 30_000,
            ),
        )
        problems = _node().validate_inputs(_uinputs(uncertainty=_uncertainty(outcome=stale)))
        assert any(p.startswith("uncertainty.outcome: ") and "stale" in p for p in problems), problems

    def test_a_wrong_unit_artifact_refuses(self):
        mean_where_outcome_is_needed = AttestedMeanConfidence(
            _mean_artifact(), _attestation(OUTCOME_ID, producer=MEAN_PRODUCER)
        )
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=mean_where_outcome_is_needed))
        )
        assert any("wrong_unit" in p for p in problems), problems

    def test_a_post_decision_artifact_refuses(self):
        later = AttestedOutcomeBand(
            _outcome_artifact(),
            _attestation(OUTCOME_ID, known_at_ms=DECISION_TS + 1),
        )
        problems = _node().validate_inputs(_uinputs(uncertainty=_uncertainty(outcome=later)))
        assert any("post_decision" in p for p in problems), problems

    def test_an_uncalibrated_artifact_refuses(self):
        uncalibrated = AttestedOutcomeBand(
            _outcome_artifact(), _attestation(OUTCOME_ID, coverage=None)
        )
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=uncalibrated))
        )
        assert any("uncalibrated" in p for p in problems), problems

    def test_an_artifact_from_a_different_model_refuses(self):
        foreign = AttestedOutcomeBand(
            _outcome_artifact(),
            _attestation(OUTCOME_ID, model_identity="some-other-release"),
        )
        problems = _node().validate_inputs(_uinputs(uncertainty=_uncertainty(outcome=foreign)))
        assert any("foreign_model" in p for p in problems), problems

    def test_an_artifact_from_an_unregistered_producer_refuses(self):
        forged = AttestedOutcomeBand(
            _outcome_artifact(),
            _attestation(OUTCOME_ID, producer="attacker.module:TotallyFakeCalibrator"),
        )
        problems = _node().validate_inputs(_uinputs(uncertainty=_uncertainty(outcome=forged)))
        assert any("unknown_producer" in p for p in problems), problems

    def test_an_artifact_whose_self_report_contradicts_its_attestation_refuses(self):
        band = _outcome_artifact()
        mislabelled = AttestedOutcomeBand(
            band,
            _attestation(OUTCOME_ID, producer=class_ref(TwoSidedBlockConformalInterval)),
        )
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=mislabelled))
        )
        assert any("must agree on what made it" in p for p in problems), problems

    def test_the_declared_reason_set_is_exactly_six(self):
        # Shrink detection. The parametrized test below iterates
        # REFUSAL_REASONS, so a reason REMOVED upstream would silently
        # produce fewer cases instead of a failure; this pins the tuple.
        assert REFUSAL_REASONS == (
            "foreign_model",
            "post_decision",
            "stale",
            "uncalibrated",
            "unknown_producer",
            "wrong_unit",
        )

    @pytest.mark.parametrize("reason", REFUSAL_REASONS)
    def test_every_declared_reason_is_reachable_from_this_boundary(self, reason):
        """Each refusal code must be producible through the capital node.

        Parameterized over the module's own tuple, so a seventh reason
        added upstream fails here until the child exercises it too.
        """
        cases = {
            "stale": _attestation(
                OUTCOME_ID,
                calibration_end_ms=DECISION_TS
                - UNCERTAINTY_PARAMS["uncertainty_max_calibration_age_ms"]
                - 1,
            ),
            "post_decision": _attestation(OUTCOME_ID, known_at_ms=DECISION_TS + 1),
            "uncalibrated": _attestation(OUTCOME_ID, coverage=None),
            "foreign_model": _attestation(OUTCOME_ID, model_identity="other-release"),
            "unknown_producer": _attestation(OUTCOME_ID, producer="nobody:Nothing"),
        }
        if reason == "wrong_unit":
            envelope = AttestedMeanConfidence(
                _mean_artifact(), _attestation(OUTCOME_ID, producer=MEAN_PRODUCER)
            )
        else:
            envelope = AttestedOutcomeBand(_outcome_artifact(), cases[reason])
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=envelope))
        )
        assert any(reason in p for p in problems), (reason, problems)


def _explode(self, demand, expected):
    raise AssertionError("capital must not call the envelope's own method")


class _NothingClaimsThis:
    """An artifact type no registered intake claims, so it reaches use time."""


class TestCapitalAsksTheRegistryNotTheEnvelope:
    """Round-3 checkpoint, at the consumer boundary.

    ``EquityKellyMIO`` admits through ``admission_problems`` — the dskit
    module FUNCTION — precisely so that an envelope's own class never gets
    to answer "what am I". These are the reviewer's reproducers pointed at
    capital rather than at the seam.
    """

    def test_a_subclass_widening_its_hooks_cannot_reach_capital(self):
        class SneakyOutcome(AttestedOutcomeBand):
            @classmethod
            def artifact_type(cls):
                return object

            @classmethod
            def excluded_types(cls):
                return ()

        envelope = SneakyOutcome(
            _NothingClaimsThis(), _attestation(OUTCOME_ID, producer=BAND_PRODUCER)
        )
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=envelope))
        )
        assert any("not a registered intake" in p for p in problems), problems

    def test_an_artifact_swapped_after_construction_cannot_reach_capital(self):
        port = _uncertainty()
        assert _node().validate_inputs(_uinputs(uncertainty=port)) == []
        port["outcome"]._artifact = _mean_artifact()
        problems = _node().validate_inputs(_uinputs(uncertainty=port))
        assert any("swapped in after construction" in p for p in problems), problems

    def test_an_attestation_swapped_after_construction_cannot_reach_capital(self):
        port = _uncertainty()
        port["false_signal"]._attestation = _attestation(
            FALSE_SIGNAL_ID, producer=RATE_PRODUCER, model_identity="other-release"
        )
        problems = _node().validate_inputs(_uinputs(uncertainty=port))
        assert any("foreign_model" in p for p in problems), problems

    def test_capital_does_not_call_the_envelopes_own_method(self):
        # A hostile envelope whose problems() lies is still refused, because
        # capital never calls it. Recorded as the boundary: the METHOD is
        # defeatable, the module function is what capital uses.
        class Lying(AttestedOutcomeBand):
            @classmethod
            def artifact_type(cls):
                return object

            @classmethod
            def excluded_types(cls):
                return ()

        # Attached AFTER class creation, which is also how a hostile
        # metaclass gets past __init_subclass__. If capital called the
        # envelope's own method this test would ERROR rather than fail.
        Lying.problems = _explode

        envelope = Lying(
            _NothingClaimsThis(), _attestation(OUTCOME_ID, producer=BAND_PRODUCER)
        )
        problems = _node().validate_inputs(
            _uinputs(uncertainty=_uncertainty(outcome=envelope))
        )
        assert problems


class TestTheWidenedRateIsNotAnUpperBound:
    """`pi_upper` cannot enter, under that name or any other."""

    def test_a_row_carrying_pi_upper_is_refused_by_name(self):
        bundle = _bundle()
        bundle[0]["pi_upper"] = bundle[0].pop("pi_widened")
        problems = _bundle_problems(bundle)
        assert any(
            "pi_upper" in p and "WITHDRAWN" in p and "pi_widened" in p
            for p in problems
        ), problems

    def test_a_row_smuggling_pi_upper_alongside_pi_widened_still_refuses(self):
        # The alias/rename attack: a schema-complete row with the withdrawn
        # name added beside the valid one. A bundle can reach this node
        # without passing through ForecastBundle, so the screen lives here
        # too rather than only at the assembler.
        bundle = _bundle()
        bundle[0]["pi_upper"] = 0.99
        problems = _bundle_problems(bundle)
        assert any("WITHDRAWN" in p for p in problems), problems

    def test_a_known_at_stamp_named_pi_upper_is_refused_by_name(self):
        bundle = _bundle()
        stamps = bundle[0]["known_at"]
        stamps["pi_upper"] = stamps.pop("pi_widened")
        problems = _bundle_problems(bundle)
        assert any(
            "pi_upper" in p and "WITHDRAWN" in p for p in problems
        ), problems

    def test_the_bound_family_stays_closed_and_unjoinable(self):
        # The child's own pin on the seal dskit added in round 2: no
        # member exists, and none can be minted to slip a widened rate
        # into the HFDR row through a "bound" that was never earned.
        assert ProbabilityUpperBound in CLOSED_FAMILIES
        with pytest.raises(TypeError, match="CLOSED"):
            type("LocalBound", (ProbabilityUpperBound,), {})

    def test_the_widened_rate_cannot_be_admitted_as_a_probability_bound(self):
        env = AttestedFalseSignalRate(_false_signal_artifact(), _attestation(FALSE_SIGNAL_ID))
        demand = DecisionDemand(
            decision_ts_ms=DECISION_TS,
            model_identity=RELEASE,
            max_calibration_age_ms=UNCERTAINTY_PARAMS["uncertainty_max_calibration_age_ms"],
            min_measured_coverage=UNCERTAINTY_PARAMS["uncertainty_min_coverage"],
        )
        problems = env.problems(demand, ProbabilityUpperBound)
        assert any(p.startswith("wrong_unit") for p in problems), problems

    def test_the_hfdr_row_reads_the_widened_field_not_the_point_estimate(
        self, tmp_path
    ):
        """Swapping the HFDR coefficient's field source must FAIL here.

        Round-1 review: changing ``row[HFDR_COEFFICIENT_FIELD]`` to
        ``row["pi_hat"]`` in ``instruments`` left the whole capital suite
        green (127 passed). Every row below carries a ``pi_hat`` BELOW
        ``hfdr_q`` and a ``pi_widened`` far above it, so the two fields
        give opposite answers: reading the widened rate makes every
        coefficient positive and the row forces zero exposure, while
        reading the point estimate makes every coefficient negative and
        the row funds names whose attested widened rate says refuse.
        """
        pytest.importorskip("pyomo")
        bundle = [dict(row, pi_hat=0.05, pi_widened=0.95) for row in _bundle()]
        node = _node(bundle=bundle, hfdr_q=0.10)
        out = node.run(_ctx(tmp_path), _uinputs(bundle=bundle))
        assert out["target"] == {}
        assert out["trades"] == {} or all(
            trade == {"buy": 0, "sell": 0} for trade in out["trades"].values()
        )

    def test_the_two_rates_are_independent_in_the_fixtures(self):
        # The pin above is only as good as the fixture: if pi_hat were a
        # function of pi_widened again, the two fields would stop
        # disagreeing and the pin would go quiet.
        ratios = {
            entity: PI_HAT_BY_ENTITY[entity] / PI_WIDENED_BY_ENTITY[entity]
            for entity in PI_WIDENED_BY_ENTITY
        }
        assert len(set(round(r, 6) for r in ratios.values())) == len(ratios)
        assert len(set(PI_HAT_BY_ENTITY.values())) == len(PI_HAT_BY_ENTITY)
        for entity, widened in PI_WIDENED_BY_ENTITY.items():
            assert PI_HAT_BY_ENTITY[entity] <= widened

    def test_the_hfdr_row_records_that_it_is_not_a_chance_constraint(self, tmp_path):
        pytest.importorskip("pyomo")
        node = _node()
        out = node.run(_ctx(tmp_path), _uinputs())
        claim = out["evidence"]["uncertainty"]["hfdr_coefficient"]
        assert claim["field"] == "pi_widened"
        assert claim["chance_constraint"] is False


class TestAFullyAttestedTickAgreesOnOneDecisionTimestamp:
    """The positive case: everything admitted, every identity on one stamp."""

    def test_a_fully_attested_bundle_solves_and_records_its_provenance(self, tmp_path):
        pytest.importorskip("pyomo")
        node = _node()
        inputs = _uinputs()
        assert node.validate_inputs(inputs) == []
        out = node.run(_ctx(tmp_path), inputs)
        record = out["evidence"]["uncertainty"]
        assert record["decision_ts"] == DECISION_TS
        assert record["model_identity"] == RELEASE
        assert record["admitted"]["false_signal"]["artifact_id"] == FALSE_SIGNAL_ID
        assert record["admitted"]["outcome"]["artifact_id"] == OUTCOME_ID
        assert {row["decision_ts"] for row in inputs["bundle"]} == {DECISION_TS}
        assert inputs["cap"]["generated_ms"] <= DECISION_TS
        assert set(out["target"]) <= set(inputs["survivors"])


class TestTheUncertaintyPortIsRefusedByNameNotWalked:
    """Adversarial port shapes name the problem instead of raising a bare error."""

    @pytest.mark.parametrize(
        "port",
        [
            {},
            {"false_signal": "fs-calibration-0001"},
            "not-a-mapping",
            {"false_signal": 0.2, "outcome": 0.3},
            {"false_signal": object(), "outcome": object()},
        ],
    )
    def test_a_malformed_port_is_refused(self, port):
        problems = _node().validate_inputs(_uinputs(uncertainty=port))
        assert any("uncertainty" in p for p in problems), problems

    def test_a_null_port_is_refused(self):
        inputs = _uinputs()
        inputs["uncertainty"] = None
        problems = _node().validate_inputs(inputs)
        assert any("uncertainty" in p for p in problems), problems

    def test_a_non_string_port_key_is_refused_rather_than_sorted(self):
        # A mixed-type key set would raise a bare TypeError out of sorted();
        # the screen compares sets so it refuses by name instead.
        problems = _node().validate_inputs(
            _uinputs(uncertainty={"false_signal": object(), 42: object()})
        )
        assert any("uncertainty" in p for p in problems), problems

    def test_a_row_naming_a_non_string_calibration_identity_is_refused(self):
        bundle = _bundle()
        bundle[0]["uncertainty"] = {"false_signal": 7, "outcome": OUTCOME_ID}
        problems = _bundle_problems(bundle)
        assert any("uncertainty" in p for p in problems), problems

    def test_a_row_whose_numbers_differ_from_the_admitted_artifact_is_refused(self):
        bundle = _bundle()
        uncertainty = _uncertainty(bundle)
        bundle = [dict(row) for row in bundle]
        bundle[0]["pi_widened"] = 0.99
        problems = _node(bundle=bundle).validate_inputs(
            _uinputs(bundle=bundle, uncertainty=uncertainty)
        )
        assert any("does not match the admitted" in p for p in problems), problems

    def test_an_entity_the_artifacts_do_not_cover_is_refused(self):
        covered = _bundle()
        uncertainty = _uncertainty(covered)
        extra = dict(covered[0], entity="NVDA")
        bundle = covered + [extra]
        problems = _node(bundle=bundle).validate_inputs(
            _uinputs(bundle=bundle, uncertainty=uncertainty)
        )
        assert any("no entry in the admitted false-signal" in p for p in problems), problems
        assert any("no calibrated outcome band" in p for p in problems), problems


# ---------------------------------------------------------------------------
# ADR-0182 Revision 2: the declared developmental switch
# ``cap_evidence_look_ahead``. It skips exactly the two cap-timing refusals
# ("cap is from the future", "cap.generated_ms ... after bundle
# decision_ts"), is legal only in development mode, and is disclosed in the
# evidence output. Every other cap check is unchanged.
# ---------------------------------------------------------------------------

LOOK_AHEAD_DISCLOSURE = "caps use post-selection evidence (look-ahead disclosed)"


def _future_cap(**overrides):
    """A cap with TRUE stamps a day after the tick (evidence end, then generation)."""
    later = ASOF_MS + 86_400_000
    fields = {
        "evidence_end_ms": later,
        "generated_ms": later + 1000,
        "evidence": {"sha256": CAP_EVIDENCE_SHA256, "scope": "synthetic_mio_demo", "end_ms": later},
    }
    fields.update(overrides)
    return _cap(**fields)


class TestCapEvidenceLookAhead:
    def test_cap_look_ahead_default_refuses_future_cap(self):
        cap = _future_cap()
        problems = _node(cap=cap).validate_inputs(_uinputs(cap=cap))
        assert any("cap is from the future" in p for p in problems), problems
        assert any("after bundle decision_ts" in p for p in problems), problems
        explicit = _node(cap=cap, cap_evidence_look_ahead=False).validate_inputs(_uinputs(cap=cap))
        assert explicit == problems

    def test_cap_look_ahead_true_admits_future_cap_and_records_disclosure(self, tmp_path):
        pytest.importorskip("pyomo")
        cap = _future_cap()
        node = _node(cap=cap, cap_evidence_look_ahead=True)
        assert node.validate_inputs(_uinputs(cap=cap)) == []
        out = node.run(_ctx(tmp_path), _uinputs(cap=cap))
        assert out["evidence"]["cap_evidence_look_ahead"] == LOOK_AHEAD_DISCLOSURE
        default = _node().run(_ctx(tmp_path), _uinputs())
        assert "cap_evidence_look_ahead" not in default["evidence"]
        assert out["trades"] == default["trades"]

    def test_cap_look_ahead_true_requires_development_mode(self):
        deploy = {**PARAMS, "deployment_mode": True}
        problems = EquityKellyMIO.validate_params({**deploy, "cap_evidence_look_ahead": True})
        assert any(
            "cap_evidence_look_ahead" in p and "deployment_mode" in p for p in problems
        ), problems
        for params in (deploy, {**deploy, "cap_evidence_look_ahead": False}):
            assert not any("cap_evidence_look_ahead" in p for p in EquityKellyMIO.validate_params(params))
        assert EquityKellyMIO.validate_params({**PARAMS, "cap_evidence_look_ahead": True}) == []

    @pytest.mark.parametrize("value", [1, 0, 1.0, "true", None, [True]])
    def test_cap_look_ahead_must_be_json_bool(self, value):
        problems = EquityKellyMIO.validate_params({**PARAMS, "cap_evidence_look_ahead": value})
        assert any("cap_evidence_look_ahead must be a JSON boolean" in p for p in problems), problems

    @pytest.mark.parametrize(
        "case",
        [
            "stale", "digest", "producer_document", "producer_node", "evidence",
            "deployment_eligible", "release", "scope",
        ],
    )
    def test_cap_look_ahead_keeps_every_other_cap_check(self, case):
        pinned = given = _future_cap()
        params = {}
        if case == "stale":
            pinned = given = _cap(generated_ms=ASOF_MS - 999_999)
            needle = "cap is stale"
        elif case == "digest":
            given = _future_cap(caps=[{"symbol": "AAPL", "capped_horizon": 2}])
            needle = "cap_artifact_sha256"
        elif case == "producer_document":
            params, needle = {"cap_producer_document_sha256": "f" * 64}, "cap producer document"
        elif case == "producer_node":
            params, needle = {"cap_producer_node": "elsewhere"}, "cap producer node"
        elif case == "evidence":
            params, needle = {"cap_evidence_sha256": "f" * 64}, "cap evidence does not match"
        elif case == "deployment_eligible":
            pinned = given = _future_cap(deployment_eligible=True)
            needle = "deployment_eligible must be false"
        elif case == "release":
            pinned = given = _future_cap(model_release_id="another-release")
            needle = "model_release_id"
        else:
            later = ASOF_MS + 86_400_000
            pinned = given = _future_cap(
                evidence_scope=DEVELOPMENT_EVIDENCE_SCOPE,
                evidence={"sha256": CAP_EVIDENCE_SHA256, "scope": DEVELOPMENT_EVIDENCE_SCOPE, "end_ms": later},
            )
            needle = "P16 development scope"
        node = _node(cap=pinned, cap_evidence_look_ahead=True, **params)
        problems = node.validate_inputs(_uinputs(cap=given))
        assert any(needle in p for p in problems), problems
        assert not any("from the future" in p or "after bundle decision_ts" in p for p in problems)
