"""``forecast_bundle`` (Gate 4, ADR-0114 Phase 4 / ADR-0117): behaviour.

Covers the three contracts ADR-0117 names: the §11 item 3 ruled inverse
label transformation (a vol-scaled SPY-residual prediction must never be
accepted as a gross return), the point-in-time bundle assembler
(:class:`intraday_equities.forecast_bundle.ForecastBundle`), and the
pinned confirmed-cap artifact validator
(:class:`intraday_equities.forecast_bundle.ConfirmedCaps`).

All rows here are synthetic, hand-calculated fixtures — never market
data, and nothing touches March-May evidence or
``configs/run-mean-confirmation.json`` (§11 item 4; plan §8).
"""

from __future__ import annotations

import math

import pytest

from intraday_equities.forecast_bundle import (
    BUNDLE_UNIT,
    LABEL_CONTRACT_FIELDS,
    ZERO_DRIFT,
    default_label_contract,
    gross_return,
)
from intraday_equities.nodes import (
    DEFAULT_BETA_WINDOW_MINUTES,
    DEFAULT_VOL_FLOOR,
    DEFAULT_VOL_WINDOW_MINUTES,
    LABEL_PARAMS,
)

ASOF_MS = 1_700_000_000_000
RELEASE = "release-sha256-0f" * 4  # any non-empty release identity
WEIGHTS = [0.5, 0.25, 0.25]
SCENARIOS = [-0.4, 0.0, 0.9]


def _input_row(entity, **overrides):
    """One hand-built point-in-time assembler input row."""
    row = {
        "entity": entity,
        "decision_ts": ASOF_MS - 1000,
        "lead": 3,
        "price": 190.0,
        "yhat": 0.5,
        "sigma_t": 0.0012,
        "beta_t": 1.1,
        "pi_upper": 0.20,
        "weights": list(WEIGHTS),
        "scenarios": list(SCENARIOS),
        "label": default_label_contract(),
        "known_at": {
            "sigma": ASOF_MS - 61_000,
            "beta": ASOF_MS - 61_000,
            "reference": ASOF_MS - 61_000,
            "price": ASOF_MS - 61_000,
            "yhat": ASOF_MS - 61_000,
            "pi_upper": ASOF_MS - 500_000,
            "scenarios": ASOF_MS - 61_000,
        },
    }
    row.update(overrides)
    return row


def _bundle(rows=None):
    from intraday_equities.forecast_bundle import ForecastBundle

    return ForecastBundle(
        RELEASE, [_input_row("AAPL"), _input_row("MSFT")] if rows is None else rows
    )


class TestGrossReturn:
    """The §11 item 3 ruled inverse: ``yhat * sigma_t * sqrt(lead)``."""

    def test_the_ruled_formula_is_exact(self):
        # yhat=1.5, sigma=0.0008, lead=4 bars -> 1.5 * 0.0008 * sqrt(4)
        assert gross_return(1.5, 0.0008, 4) == pytest.approx(0.0024)

    def test_lead_one_uses_the_sigma_unchanged(self):
        assert gross_return(-2.0, 0.001, 1) == pytest.approx(-0.002)

    def test_a_non_finite_yhat_is_refused(self):
        with pytest.raises(ValueError, match="yhat"):
            gross_return(float("nan"), 0.001, 1)

    def test_a_non_finite_sigma_is_refused(self):
        with pytest.raises(ValueError, match="sigma"):
            gross_return(1.0, float("inf"), 1)

    def test_a_zero_sigma_is_refused(self):
        with pytest.raises(ValueError, match="sigma"):
            gross_return(1.0, 0.0, 1)

    def test_a_negative_sigma_is_refused(self):
        with pytest.raises(ValueError, match="sigma"):
            gross_return(1.0, -0.001, 1)

    def test_a_zero_lead_is_refused(self):
        with pytest.raises(ValueError, match="lead"):
            gross_return(1.0, 0.001, 0)

    def test_a_negative_lead_is_refused(self):
        with pytest.raises(ValueError, match="lead"):
            gross_return(1.0, 0.001, -3)

    def test_a_fractional_lead_is_refused(self):
        with pytest.raises(ValueError, match="lead"):
            gross_return(1.0, 0.001, 2.5)

    def test_a_boolean_lead_is_refused(self):
        with pytest.raises(ValueError, match="lead"):
            gross_return(1.0, 0.001, True)

    def test_the_hand_example_from_the_ruling_scales_by_sqrt_h(self):
        # The label divided by sigma*sqrt(h); the inverse multiplies back,
        # so a round trip over one row returns the residual return itself.
        yhat, sigma, lead = 0.7, 0.0013, 9
        residual = yhat * sigma * math.sqrt(lead)
        assert gross_return(yhat, sigma, lead) == pytest.approx(residual)


class TestDefaultLabelContract:
    """The pinned contract reads nodes.py's own names — never a restated copy."""

    def test_it_carries_the_training_label_knobs(self):
        contract = default_label_contract()
        assert contract["label_scale"] == "vol"
        assert contract["label_residual"] == "SPY"
        assert contract["vol_window_minutes"] == DEFAULT_VOL_WINDOW_MINUTES
        assert contract["beta_window_minutes"] == DEFAULT_BETA_WINDOW_MINUTES
        assert contract["vol_floor"] == DEFAULT_VOL_FLOOR

    def test_every_contract_field_is_a_real_label_knob(self):
        # A pinning test: the contract vocabulary cannot drift from the
        # label's own declared knob list (CLAUDE.md: pin the agreement).
        assert set(LABEL_CONTRACT_FIELDS) <= set(LABEL_PARAMS)
        assert set(default_label_contract()) == set(LABEL_CONTRACT_FIELDS)


class TestForecastBundleAssembly:
    """The ruled conversion, applied point-in-time to a whole decision tick."""

    def test_the_reference_bundle_assembles(self):
        bundle = _bundle()
        assert [row["entity"] for row in bundle.rows] == ["AAPL", "MSFT"]

    def test_mu_gross_is_the_ruled_conversion_of_yhat(self):
        bundle = _bundle()
        for out in bundle.rows:
            assert out["mu_gross"] == pytest.approx(gross_return(0.5, 0.0012, 3))

    def test_every_scenario_residual_converts_with_the_same_factor(self):
        bundle = _bundle()
        for out in bundle.rows:
            assert out["scenarios"] == pytest.approx(
                [gross_return(v, 0.0012, 3) for v in SCENARIOS]
            )

    def test_output_rows_carry_the_shared_tick_identity(self):
        bundle = _bundle()
        for out in bundle.rows:
            assert out["decision_ts"] == ASOF_MS - 1000
            assert out["lead"] == 3
            assert out["model_release_id"] == RELEASE
            assert out["unit"] == BUNDLE_UNIT
            assert out["weights"] == WEIGHTS

    def test_output_rows_record_the_ruled_reference_policy(self):
        assert all(row["reference_policy"] == ZERO_DRIFT for row in _bundle().rows)

    def test_the_point_in_time_audit_trail_is_carried(self):
        bundle = _bundle()
        assert bundle.rows[0]["known_at"]["pi_upper"] == ASOF_MS - 500_000

    def test_price_pi_upper_and_entity_pass_through(self):
        out = _bundle().rows[0]
        assert out["price"] == 190.0
        assert out["pi_upper"] == 0.20
        assert out["entity"] == "AAPL"

    def test_the_label_unit_inputs_are_consumed_not_passed_through(self):
        # yhat/sigma/beta are consumed by the conversion; a gross-unit
        # bundle row never re-offers them as values to size off.
        out = _bundle().rows[0]
        assert "yhat" not in out
        assert "sigma_t" not in out
        assert "beta_t" not in out

    def test_an_empty_row_list_is_the_empty_gate(self):
        bundle = _bundle(rows=[])
        assert bundle.rows == []

    def test_a_non_list_rows_argument_is_refused_by_name(self):
        from intraday_equities.forecast_bundle import ForecastBundle

        with pytest.raises(ValueError, match="materialized list"):
            ForecastBundle(RELEASE, iter([_input_row("AAPL")]))


class TestAVolScaledResidualIsNeverAGrossReturn:
    """Plan §6 Phase 4 item 1: label-unit predictions have no path through."""

    def test_a_row_trying_to_smuggle_a_precomputed_gross_mean_refuses(self):
        bad = _input_row("AAPL")
        bad["mu_gross"] = 0.002
        with pytest.raises(ValueError, match="mu_gross"):
            _bundle([bad])

    def test_a_row_declaring_its_own_unit_refuses(self):
        bad = _input_row("AAPL")
        bad["unit"] = "vol_scaled_residual"
        with pytest.raises(ValueError, match="unit"):
            _bundle([bad])


class TestPointInTimeAvailability:
    """Plan §6 Phase 4 item 2: every dependency is known at or before the tick."""

    @pytest.mark.parametrize(
        "key",
        ["sigma", "beta", "reference", "price", "yhat", "pi_upper", "scenarios"],
    )
    def test_a_dependency_known_after_the_decision_refuses(self, key):
        bad_known = dict(_input_row("AAPL")["known_at"], **{key: ASOF_MS + 1})
        bad = _input_row("AAPL", known_at=bad_known)
        with pytest.raises(ValueError, match=key):
            _bundle([bad])

    @pytest.mark.parametrize(
        "key",
        ["sigma", "beta", "reference", "price", "yhat", "pi_upper", "scenarios"],
    )
    def test_a_missing_availability_stamp_refuses(self, key):
        bad_known = {
            k: v for k, v in _input_row("AAPL")["known_at"].items() if k != key
        }
        bad = _input_row("AAPL", known_at=bad_known)
        with pytest.raises(ValueError, match=key):
            _bundle([bad])

    def test_a_non_finite_availability_stamp_refuses(self):
        bad_known = dict(_input_row("AAPL")["known_at"], sigma=float("nan"))
        bad = _input_row("AAPL", known_at=bad_known)
        with pytest.raises(ValueError, match="sigma"):
            _bundle([bad])

    def test_an_unknown_availability_key_refuses(self):
        # Default-deny: the closed vocabulary IS the dependency list.
        bad_known = dict(_input_row("AAPL")["known_at"], volatility=ASOF_MS)
        bad = _input_row("AAPL", known_at=bad_known)
        with pytest.raises(ValueError, match="volatility"):
            _bundle([bad])

    def test_future_outcome_knowledge_has_no_path_into_the_bundle(self):
        # "outcomes" is deliberately NOT in the vocabulary: a realized
        # outcome of the decision being assembled is future information,
        # and under the zero-drift ruling nothing in this bundle may read
        # it. Supplying one refuses by name.
        bad_known = dict(_input_row("AAPL")["known_at"], outcome=ASOF_MS - 10)
        bad = _input_row("AAPL", known_at=bad_known)
        with pytest.raises(ValueError, match="outcome"):
            _bundle([bad])


class TestSigmaComesFromTheSamePipeline:
    """The ruling: the SAME causal sigma, never recomputed differently."""

    def test_a_sigma_at_or_below_the_labels_vol_floor_refuses(self):
        # The label itself refuses sigma <= vol_floor (a stale tape, not a
        # quiet market); the inverse must refuse the same rows.
        bad = _input_row("AAPL", sigma_t=DEFAULT_VOL_FLOOR)
        with pytest.raises(ValueError, match="sigma_t"):
            _bundle([bad])

    def test_a_row_recomputed_with_a_different_label_contract_refuses(self):
        drifted = dict(default_label_contract(), vol_window_minutes=60)
        bad = _input_row("AAPL", label=drifted)
        with pytest.raises(ValueError, match="label"):
            _bundle([bad])

    def test_a_row_with_no_label_contract_refuses(self):
        bad = _input_row("AAPL")
        del bad["label"]
        with pytest.raises(ValueError, match="label"):
            _bundle([bad])

    def test_a_non_finite_beta_refuses(self):
        bad = _input_row("AAPL", beta_t=float("nan"))
        with pytest.raises(ValueError, match="beta_t"):
            _bundle([bad])


class TestOneJointScenarioSetPerTick:
    """Plan §6 Phase 4 item 3: shared timestamp, horizon, weights, unit, release."""

    def test_mixed_leads_in_one_bundle_refuse(self):
        with pytest.raises(ValueError, match="lead"):
            _bundle([_input_row("AAPL"), _input_row("MSFT", lead=4)])

    def test_mixed_decision_timestamps_refuse(self):
        with pytest.raises(ValueError, match="decision_ts"):
            _bundle(
                [
                    _input_row("AAPL"),
                    _input_row("MSFT", decision_ts=ASOF_MS - 2000),
                ]
            )

    def test_mismatched_weights_refuse(self):
        bad = _input_row("MSFT", weights=[1.0, 0.0, 0.0])
        with pytest.raises(ValueError, match="weights"):
            _bundle([_input_row("AAPL"), bad])

    def test_weights_that_do_not_sum_to_one_refuse(self):
        bad = _input_row("AAPL", weights=[0.5, 0.2, 0.1])
        with pytest.raises(ValueError, match="sum to 1"):
            _bundle([bad])

    def test_a_scenario_row_length_mismatch_refuses(self):
        bad = _input_row("AAPL", scenarios=[-0.4, 0.0])
        with pytest.raises(ValueError, match="scenarios"):
            _bundle([bad])

    def test_non_finite_scenario_residuals_refuse(self):
        bad = _input_row("AAPL", scenarios=[float("nan"), 0.0, 0.9])
        with pytest.raises(ValueError, match="scenarios"):
            _bundle([bad])

    def test_duplicate_entities_refuse(self):
        with pytest.raises(ValueError, match="duplicate"):
            _bundle([_input_row("AAPL"), _input_row("AAPL")])

    def test_a_missing_required_field_refuses_by_name(self):
        bad = _input_row("AAPL")
        del bad["pi_upper"]
        with pytest.raises(ValueError, match="pi_upper"):
            _bundle([bad])

    def test_a_lead_outside_the_ten_head_vocabulary_refuses(self):
        with pytest.raises(ValueError, match="lead"):
            _bundle([_input_row("AAPL", lead=11)])

    def test_a_non_integer_lead_refuses(self):
        with pytest.raises(ValueError, match="lead"):
            _bundle([_input_row("AAPL", lead=3.5)])

    def test_a_price_at_or_below_zero_refuses(self):
        with pytest.raises(ValueError, match="price"):
            _bundle([_input_row("AAPL", price=0.0)])

    def test_a_pi_upper_outside_unit_interval_refuses(self):
        with pytest.raises(ValueError, match="pi_upper"):
            _bundle([_input_row("AAPL", pi_upper=1.5)])


class TestTheCapitalNodeContractIsPinned:
    """The produced rows must satisfy the consumer's own bundle validator."""

    def test_produced_rows_pass_the_capital_nodes_bundle_contract(self):
        from intraday_equities.nodes_capital import _bundle_problems

        assert _bundle_problems(_bundle().rows) == []
