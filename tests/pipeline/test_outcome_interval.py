"""Dependence-aware calibration of realized-outcome uncertainty (ADR-0155)."""

from __future__ import annotations

import math
import random
import re
from pathlib import Path

import pytest

from dskit.pipeline.attempts import utc_day
from dskit.pipeline.driver import _canonical_hash
from dskit.pipeline.libs.pyomo import HARD_N_SCENARIOS_CEILING
from dskit.pipeline.node import class_ref
from dskit.pipeline.outcome_interval import (
    CALIBRATORS,
    DEFAULT_COVERAGE,
    DEFAULT_SEED,
    DEFAULT_WINDOW_BLOCKS,
    MAX_SCENARIOS,
    MIN_CALIBRATION_BLOCKS,
    MIN_SCENARIOS,
    WEIGHTS_SUM_TOLERANCE,
    BlockCalibrator,
    BlockConformalInterval,
    BlockResiduals,
    OutcomeCalibrator,
    OutcomeIntervalResult,
    ScenarioSet,
    TwoSidedBlockConformalInterval,
    calibrator,
    register_calibrator,
)

# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _residuals(n_blocks=12, per_block=5, names=("a", "b"), seed=7, sizes=None):
    """A time-ordered block-structured residual panel with a common shock."""
    rng = random.Random(seed)
    rows, blocks, stamps = [], [], []
    stamp = 0
    for b in range(n_blocks):
        shock = rng.gauss(0.0, 1.0)
        length = per_block if sizes is None else sizes[b]
        for _ in range(length):
            common = rng.gauss(0.0, 0.5)
            rows.append(tuple(shock + common + rng.gauss(0.0, 0.25) for _ in names))
            blocks.append(f"b{b:03d}")
            stamps.append(stamp)
            stamp += 1000
    return BlockResiduals(names=names, rows=rows, blocks=blocks, stamps=stamps)


def _flat(n_blocks=12, per_block=4, names=("a",)):
    """Deterministic, spread-out residuals — no RNG, easy to reason about."""
    rows, blocks = [], []
    for b in range(n_blocks):
        for j in range(per_block):
            rows.append(tuple(float(b - n_blocks / 2) + 0.1 * j for _ in names))
            blocks.append(f"b{b:03d}")
    return BlockResiduals(names=names, rows=rows, blocks=blocks)


# ---------------------------------------------------------------------------
# the evidence value
# ---------------------------------------------------------------------------


class TestBlockResiduals:
    def test_it_carries_the_panel_it_was_given(self):
        ev = _residuals(n_blocks=10, per_block=3)
        assert ev.n_blocks == 10
        assert ev.n_rows == 30
        assert ev.names == ("a", "b")
        assert len(ev.block_ids) == 10

    def test_the_block_statement_is_required_and_has_no_default(self):
        with pytest.raises(TypeError):
            BlockResiduals(names=("a",), rows=[(0.1,), (0.2,)])

    def test_an_empty_family_refuses(self):
        with pytest.raises(ValueError, match="names"):
            BlockResiduals(names=(), rows=[(), ()], blocks=["b0", "b1"])

    def test_duplicate_names_refuse_by_name(self):
        with pytest.raises(ValueError, match="duplicate"):
            BlockResiduals(names=("a", "a"), rows=[(0.1, 0.2)], blocks=["b0"])

    def test_no_rows_refuses(self):
        with pytest.raises(ValueError, match="at least one row"):
            BlockResiduals(names=("a",), rows=[], blocks=[])

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "x", None, True])
    def test_a_non_finite_residual_refuses_naming_its_position(self, bad):
        rows = [(0.1,), (0.2,), (bad,), (0.4,)]
        with pytest.raises(ValueError, match=r"row 2"):
            BlockResiduals(names=("a",), rows=rows, blocks=["b0", "b0", "b1", "b1"])

    def test_a_row_narrower_than_the_family_refuses(self):
        with pytest.raises(ValueError, match=r"row 1"):
            BlockResiduals(names=("a", "b"), rows=[(0.1, 0.2), (0.3,)], blocks=["b0", "b1"])

    @pytest.mark.parametrize("bad", ["", 5, None])
    def test_a_name_that_is_not_a_non_empty_string_refuses(self, bad):
        with pytest.raises(ValueError, match="non-empty strings"):
            BlockResiduals(
                names=("a", bad), rows=[(0.1, 0.2), (0.3, 0.4)], blocks=["b0", "b1"]
            )

    def test_blocks_must_align_with_rows(self):
        # Matched on the ALIGNMENT message specifically: a looser regex
        # let this pass on the minimum-block-count refusal instead.
        with pytest.raises(ValueError, match="the two must agree"):
            BlockResiduals(
                names=("a",), rows=[(0.1,), (0.2,), (0.3,)], blocks=["b0", "b1"]
            )

    @pytest.mark.parametrize("bad", ["", None, 1.5, True])
    def test_an_unusable_block_id_refuses(self, bad):
        with pytest.raises(ValueError, match="block id"):
            BlockResiduals(names=("a",), rows=[(0.1,), (0.2,)], blocks=["b0", bad])

    def test_a_block_that_reappears_after_another_refuses_as_out_of_order(self):
        with pytest.raises(ValueError, match="contiguous"):
            BlockResiduals(
                names=("a",),
                rows=[(0.1,), (0.2,), (0.3,)],
                blocks=["b0", "b1", "b0"],
            )

    def test_too_few_blocks_refuses_naming_the_floor(self):
        with pytest.raises(ValueError, match=str(MIN_CALIBRATION_BLOCKS)):
            BlockResiduals(names=("a",), rows=[(0.1,)], blocks=["b0"])

    def test_the_canonical_session_key_is_a_usable_block_id(self):
        # attempts.utc_day is the repo's session key and returns an INT;
        # a calibrator that refused it would be a second opinion on the
        # session-block doctrine rather than the same one.
        stamps = [1_700_000_000_000 + i * 3_600_000 for i in range(48)]
        ev = BlockResiduals(
            names=("a",),
            rows=[(0.01 * i,) for i in range(48)],
            blocks=[utc_day(s) for s in stamps],
            stamps=stamps,
        )
        assert ev.n_blocks >= 2

    def test_stamps_must_align_with_rows(self):
        with pytest.raises(ValueError, match="the two must agree"):
            BlockResiduals(
                names=("a",), rows=[(0.1,), (0.2,)], blocks=["b0", "b1"], stamps=[10]
            )

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "x", None])
    def test_a_non_finite_stamp_refuses(self, bad):
        with pytest.raises(ValueError, match="stamps at row"):
            BlockResiduals(
                names=("a",), rows=[(0.1,), (0.2,)], blocks=["b0", "b1"], stamps=[0, bad]
            )

    def test_stamps_must_be_ordered_when_given(self):
        with pytest.raises(ValueError, match="stamps"):
            BlockResiduals(
                names=("a",),
                rows=[(0.1,), (0.2,)],
                blocks=["b0", "b1"],
                stamps=[10, 5],
            )

    def test_block_weights_equalize_blocks_not_rows(self):
        ev = BlockResiduals(
            names=("a",),
            rows=[(0.1,), (0.2,), (0.3,), (0.4,)],
            blocks=["b0", "b0", "b0", "b1"],
        )
        w = ev.row_weights()
        assert math.isclose(sum(w), 1.0, abs_tol=1e-12)
        # b0 holds three rows and b1 one; each BLOCK still carries half.
        assert math.isclose(sum(w[:3]), 0.5, abs_tol=1e-12)
        assert math.isclose(w[3], 0.5, abs_tol=1e-12)

    def test_the_value_is_frozen(self):
        ev = _flat()
        with pytest.raises(Exception):
            ev.names = ("z",)

    def test_the_digest_is_stable_and_content_sensitive(self):
        a = _flat()
        b = _flat()
        assert a.digest() == b.digest()
        rows = [(v[0] + 1.0,) for v in a.rows]
        c = BlockResiduals(names=a.names, rows=rows, blocks=a.blocks)
        assert c.digest() != a.digest()


# ---------------------------------------------------------------------------
# the block-conformal correction — the load-bearing "never too narrow" rule
# ---------------------------------------------------------------------------


class TestTheBlockCorrectionCountsBlocks:
    def test_the_level_taken_is_inflated_over_the_target(self):
        member = BlockConformalInterval()
        level = member.achievable_level(0.9, 40)
        assert level == math.ceil(41 * 0.9) / 40
        assert level > 0.9

    def test_the_inflation_uses_the_block_count_not_the_row_count(self):
        member = BlockConformalInterval()
        ev = _residuals(n_blocks=40, per_block=20, names=("a",))
        result = member.calibrate(ev, coverage=0.9, window_blocks=10)
        # A row-counting correction at n = 800 would be ~0.90125; the
        # block-counting one at B = 40 is 0.925. They are not close.
        assert math.isclose(result.achieved_level, 37 / 40, abs_tol=1e-12)
        assert result.achieved_level > 0.92

    def test_a_coverage_the_block_count_cannot_support_refuses_by_name(self):
        member = BlockConformalInterval()
        with pytest.raises(ValueError) as exc:
            member.achievable_level(0.99, 8)
        assert "0.99" in str(exc.value) and "8" in str(exc.value)

    def test_the_refusal_reaches_the_caller_through_calibrate(self):
        ev = _residuals(n_blocks=8, per_block=10)
        with pytest.raises(ValueError, match="blocks"):
            BlockConformalInterval().calibrate(ev, coverage=0.99, window_blocks=4)

    def test_the_template_refuses_a_member_that_narrows_below_the_target(self):
        class TooNarrow(BlockConformalInterval):
            def achievable_level(self, coverage, n_blocks):
                return coverage / 2.0

        ev = _residuals(n_blocks=20, per_block=5)
        with pytest.raises(ValueError, match="TooNarrow"):
            TooNarrow().calibrate(ev, coverage=0.9, window_blocks=5)

    def test_the_template_refuses_a_member_whose_level_exceeds_one(self):
        class Impossible(BlockConformalInterval):
            def achievable_level(self, coverage, n_blocks):
                return 1.5

        ev = _residuals(n_blocks=20, per_block=5)
        with pytest.raises(ValueError, match="Impossible"):
            Impossible().calibrate(ev, coverage=0.9, window_blocks=5)


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


class TestCalibrate:
    def test_it_returns_an_offset_pair_per_component(self):
        ev = _residuals(n_blocks=30, per_block=8, names=("a", "b", "c"))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        assert isinstance(result, OutcomeIntervalResult)
        assert set(result.lower_offset) == {"a", "b", "c"}
        for name in ("a", "b", "c"):
            assert result.lower_offset[name] < result.upper_offset[name]

    def test_the_symmetric_member_is_symmetric_and_the_two_sided_one_need_not_be(self):
        ev = _residuals(n_blocks=30, per_block=8, names=("a",))
        sym = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        assert math.isclose(sym.lower_offset["a"], -sym.upper_offset["a"], abs_tol=1e-12)
        two = TwoSidedBlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        assert not math.isclose(two.lower_offset["a"], -two.upper_offset["a"], abs_tol=1e-9)

    def test_a_wider_coverage_never_narrows_the_interval(self):
        ev = _residuals(n_blocks=60, per_block=6, names=("a",))
        member = BlockConformalInterval()
        narrow = member.calibrate(ev, coverage=0.8, window_blocks=10)
        wide = member.calibrate(ev, coverage=0.95, window_blocks=10)
        assert wide.upper_offset["a"] >= narrow.upper_offset["a"]
        assert wide.lower_offset["a"] <= narrow.lower_offset["a"]

    def test_it_reports_realized_coverage_at_or_above_the_target(self):
        ev = _residuals(n_blocks=40, per_block=10, names=("a", "b"))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        for name in ("a", "b"):
            assert result.realized_coverage[name] >= 0.9

    def test_it_reports_the_worst_rolling_window_as_conditional_coverage(self):
        ev = _residuals(n_blocks=40, per_block=10, names=("a",))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        cond = result.conditional_coverage["a"]
        assert 0.0 <= cond <= 1.0
        # The worst window can never beat the pooled average.
        assert cond <= result.realized_coverage["a"] + 1e-12

    def test_tail_loss_is_the_pinball_loss_at_both_endpoints(self):
        from dskit.pipeline.metrics import pinball

        ev = _flat(n_blocks=20, per_block=4, names=("a",))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=5)
        level = result.achieved_level
        alpha = 1.0 - level
        lo_tau, hi_tau = alpha / 2.0, 1.0 - alpha / 2.0
        w = ev.row_weights()
        expected = sum(
            wi
            * (
                pinball(result.lower_offset["a"], row[0], lo_tau)
                + pinball(result.upper_offset["a"], row[0], hi_tau)
            )
            for wi, row in zip(w, ev.rows)
        )
        assert math.isclose(result.tail_loss["a"], expected, rel_tol=1e-12)

    def test_a_window_wider_than_the_calibration_set_refuses(self):
        ev = _residuals(n_blocks=12, per_block=4)
        with pytest.raises(ValueError, match="window_blocks must be between"):
            BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=99)

    @pytest.mark.parametrize("bad", [2.5, "x", True, None])
    def test_a_window_that_is_not_an_int_refuses(self, bad):
        ev = _residuals(n_blocks=20, per_block=4)
        with pytest.raises(ValueError, match="window_blocks must be an int"):
            BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=bad)

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.2, float("nan"), "x", None])
    def test_an_impossible_coverage_refuses(self, bad):
        ev = _residuals(n_blocks=30, per_block=4)
        with pytest.raises(ValueError, match="coverage"):
            BlockConformalInterval().calibrate(ev, coverage=bad, window_blocks=5)

    def test_it_refuses_anything_that_is_not_block_residuals(self):
        with pytest.raises(TypeError, match="BlockResiduals"):
            BlockConformalInterval().calibrate({"a": [0.1, 0.2]}, coverage=0.9)

    def test_the_result_records_its_provenance(self):
        ev = _residuals(n_blocks=30, per_block=6, names=("a",))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=10)
        prov = result.provenance
        # Exact match, not .endswith(...): a bare type(self).__name__ ends
        # with "BlockConformalInterval" too, so .endswith survived that
        # mutation (mutation-probe finding, re-review 2026-09-17).
        assert prov["block_rule"] == class_ref(BlockConformalInterval)
        assert prov["window_blocks"] == 10
        assert prov["first_block"] == ev.block_ids[0]
        assert prov["last_block"] == ev.block_ids[-1]
        assert prov["first_stamp"] == ev.stamps[0]
        assert prov["n_rows"] == ev.n_rows == 180
        assert result.calibration_hash == ev.digest()
        assert result.n_blocks == 30 and result.n_rows == 180

    def test_the_result_mappings_cannot_be_mutated(self):
        # All SIX frozen fields, not just two: removing realized_coverage,
        # conditional_coverage or tail_loss from the freeze loop survived
        # the prior suite entirely (mutation-probe finding, re-review
        # 2026-09-17).
        ev = _residuals(n_blocks=20, per_block=4, names=("a",))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=5)
        for field_name in (
            "lower_offset",
            "upper_offset",
            "realized_coverage",
            "conditional_coverage",
            "tail_loss",
        ):
            with pytest.raises(TypeError):
                getattr(result, field_name)["a"] = 0.0
        with pytest.raises(TypeError):
            result.provenance["block_rule"] = "x"

    def test_the_default_coverage_is_the_named_constant(self):
        ev = _residuals(n_blocks=60, per_block=4, names=("a",))
        member = BlockConformalInterval()
        assert member.calibrate(ev, window_blocks=10).coverage_target == DEFAULT_COVERAGE

    def test_the_default_window_is_the_named_constant_and_is_recorded(self):
        ev = _residuals(n_blocks=60, per_block=4, names=("a",))
        result = BlockConformalInterval().calibrate(ev)
        assert result.provenance["window_blocks"] == DEFAULT_WINDOW_BLOCKS


# ---------------------------------------------------------------------------
# the joint scenario set
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_every_scenario_is_a_real_simultaneous_row(self):
        # The invariant that makes this a JOINT set: a scenario is a
        # COPIED cross-component vector, so per-name independent
        # sampling is structurally impossible rather than discouraged.
        ev = _residuals(n_blocks=30, per_block=8, names=("a", "b", "c"))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=64, seed=3)
        historical = set(ev.rows)
        for omega in range(len(s.weights)):
            drawn = tuple(s.draws[name][omega] for name in ev.names)
            assert drawn in historical

    def test_the_cross_component_correlation_survives_the_draw(self):
        ev = _residuals(n_blocks=40, per_block=10, names=("a", "b"))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=200, seed=11)
        a, b = list(s.draws["a"]), list(s.draws["b"])
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
        va = sum((x - ma) ** 2 for x in a) / len(a)
        vb = sum((y - mb) ** 2 for y in b) / len(b)
        assert cov / math.sqrt(va * vb) > 0.8

    def test_identical_inputs_give_identical_scenarios(self):
        ev = _residuals(n_blocks=30, per_block=8)
        member = BlockConformalInterval()
        first = member.scenarios(ev, n_scenarios=32, seed=5)
        second = member.scenarios(ev, n_scenarios=32, seed=5)
        assert first.weights == second.weights
        assert first.draws["a"] == second.draws["a"]

    def test_a_different_seed_gives_a_different_draw(self):
        ev = _residuals(n_blocks=30, per_block=8)
        member = BlockConformalInterval()
        assert (
            member.scenarios(ev, n_scenarios=32, seed=5).draws["a"]
            != member.scenarios(ev, n_scenarios=32, seed=6).draws["a"]
        )

    @pytest.mark.parametrize("bad", [1.5, "x", None, True])
    def test_a_seed_that_is_not_an_int_refuses(self, bad):
        ev = _residuals(n_blocks=30, per_block=8)
        with pytest.raises(ValueError, match="seed must be an int"):
            BlockConformalInterval().scenarios(ev, n_scenarios=16, seed=bad)

    def test_the_draw_pattern_itself_changes_with_the_calibration_content(self):
        # Not just the recorded hash: the RNG must actually be keyed to
        # the evidence, so the sequence of BLOCKS drawn differs too.
        base = _flat(n_blocks=20, per_block=5, names=("a",))
        nudged = BlockResiduals(
            names=base.names,
            rows=[*base.rows[:-1], (base.rows[-1][0] + 1e-9,)],
            blocks=base.blocks,
        )
        member = BlockConformalInterval()
        one = member.scenarios(base, n_scenarios=100, seed=1)
        two = member.scenarios(nudged, n_scenarios=100, seed=1)
        # _flat gives block b the values (b - 10) + 0.1 * j, so a rounded
        # draw names the block it came from.
        assert [round(v) for v in one.draws["a"]] != [round(v) for v in two.draws["a"]]

    def test_the_draw_is_keyed_to_the_calibration_content(self):
        member = BlockConformalInterval()
        one = member.scenarios(_flat(n_blocks=20, per_block=6), n_scenarios=16, seed=1)
        base = _flat(n_blocks=20, per_block=6)
        shifted = BlockResiduals(
            names=base.names,
            rows=[(v[0] + 100.0,) for v in base.rows],
            blocks=base.blocks,
        )
        two = member.scenarios(shifted, n_scenarios=16, seed=1)
        assert one.calibration_hash != two.calibration_hash

    def test_whole_blocks_are_drawn_never_scattered_rows(self):
        ev = _flat(n_blocks=20, per_block=5, names=("a",))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=20, seed=2)
        # Within one drawn block the rows arrive in their stored order,
        # so consecutive draws step by the block's own +0.1 spacing.
        steps = [
            round(s.draws["a"][i + 1] - s.draws["a"][i], 6)
            for i in range(len(s.weights) - 1)
        ]
        assert steps.count(0.1) >= 12

    def test_the_weights_sum_to_one(self):
        ev = _residuals(n_blocks=30, per_block=8)
        s = BlockConformalInterval().scenarios(ev, n_scenarios=57, seed=4)
        assert abs(sum(s.weights) - 1.0) <= WEIGHTS_SUM_TOLERANCE

    def test_a_degenerate_scenario_set_refuses(self):
        rows = [(1.0,)] * 40
        ev = BlockResiduals(
            names=("a",),
            rows=rows,
            blocks=[f"b{i // 4:03d}" for i in range(40)],
        )
        with pytest.raises(ValueError, match="degenerate"):
            BlockConformalInterval().scenarios(ev, n_scenarios=16, seed=1)

    def test_the_smallest_legal_set_is_the_named_floor(self):
        ev = _residuals(n_blocks=30, per_block=8, names=("a",))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=MIN_SCENARIOS, seed=1)
        assert len(s.weights) == MIN_SCENARIOS

    @pytest.mark.parametrize("bad", [0, 1, -3, 2.5, None, True])
    def test_a_scenario_count_below_the_floor_refuses(self, bad):
        ev = _residuals(n_blocks=30, per_block=8)
        with pytest.raises(ValueError, match="n_scenarios"):
            BlockConformalInterval().scenarios(ev, n_scenarios=bad, seed=1)

    def test_a_scenario_count_above_the_consumer_ceiling_refuses(self):
        ev = _residuals(n_blocks=30, per_block=8)
        with pytest.raises(ValueError, match=str(MAX_SCENARIOS)):
            BlockConformalInterval().scenarios(ev, n_scenarios=MAX_SCENARIOS + 1, seed=1)

    def test_the_template_refuses_a_member_that_draws_an_unknown_block(self):
        class Liar(BlockConformalInterval):
            def draw_blocks(self, residuals, n_scenarios, rng):
                return ["not-a-block"]

        ev = _residuals(n_blocks=30, per_block=8)
        with pytest.raises(ValueError, match="not-a-block"):
            Liar().scenarios(ev, n_scenarios=16, seed=1)

    def test_the_template_refuses_a_member_that_draws_too_few_rows(self):
        class Short(BlockConformalInterval):
            def draw_blocks(self, residuals, n_scenarios, rng):
                return [residuals.block_ids[0]]

        ev = _residuals(n_blocks=30, per_block=2)
        with pytest.raises(ValueError, match="Short"):
            Short().scenarios(ev, n_scenarios=64, seed=1)

    def test_the_scenario_set_records_its_provenance(self):
        ev = _residuals(n_blocks=30, per_block=8, names=("a",))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=32, seed=9)
        assert s.seed == 9
        assert s.calibration_hash == ev.digest()
        # Exact match, not .endswith(...) — see the twin assertion in
        # TestCalibrate.test_the_result_records_its_provenance.
        assert s.provenance["block_rule"] == class_ref(BlockConformalInterval)
        assert s.provenance["n_blocks"] == 30
        assert s.provenance["n_rows"] == ev.n_rows
        assert s.provenance["first_block"] == ev.block_ids[0]

    def test_the_scenario_set_is_frozen_and_its_draws_unmutable(self):
        ev = _residuals(n_blocks=20, per_block=6, names=("a",))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=16, seed=1)
        with pytest.raises(Exception):
            s.weights = ()
        with pytest.raises(TypeError):
            s.draws["a"] = ()

    def test_the_default_seed_is_the_named_constant(self):
        ev = _residuals(n_blocks=20, per_block=6, names=("a",))
        member = BlockConformalInterval()
        assert member.scenarios(ev, n_scenarios=16).seed == DEFAULT_SEED


class TestScenarioSetValue:
    def test_weights_that_miss_summing_to_one_refuse(self):
        with pytest.raises(ValueError, match="sum to 1"):
            ScenarioSet(
                weights=(0.5, 0.2),
                draws={"a": (0.1, 0.2)},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_a_draw_length_mismatch_refuses(self):
        with pytest.raises(ValueError, match="length"):
            ScenarioSet(
                weights=(0.5, 0.5),
                draws={"a": (0.1, 0.2, 0.3)},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_a_negative_weight_refuses(self):
        with pytest.raises(ValueError, match="weights"):
            ScenarioSet(
                weights=(1.5, -0.5),
                draws={"a": (0.1, 0.2)},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_a_non_finite_draw_refuses(self):
        with pytest.raises(ValueError, match="finite"):
            ScenarioSet(
                weights=(0.5, 0.5),
                draws={"a": (0.1, float("nan"))},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_an_empty_family_refuses(self):
        with pytest.raises(ValueError, match="draws"):
            ScenarioSet(
                weights=(0.5, 0.5),
                draws={},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_an_empty_weights_vector_refuses(self):
        with pytest.raises(ValueError, match="non-empty"):
            ScenarioSet(
                weights=(),
                draws={"a": ()},
                calibration_hash="h",
                seed=0,
                provenance={},
            )

    def test_a_draw_shorter_than_the_weights_also_refuses(self):
        # The existing mismatch test above is one-directional (too LONG);
        # a length check loosened to only catch "too long" would pass it
        # while missing this direction entirely.
        with pytest.raises(ValueError, match="length"):
            ScenarioSet(
                weights=(0.5, 0.25, 0.25),
                draws={"a": (0.1, 0.2)},
                calibration_hash="h",
                seed=0,
                provenance={},
            )


# ---------------------------------------------------------------------------
# the declared consumer shapes (compatibility only — nothing is wired)
# ---------------------------------------------------------------------------


class TestConsumerShape:
    def test_the_ceiling_agrees_with_the_optimizer_that_enforces_it(self):
        # Two copies of one number: this module refuses a set the solver
        # would refuse, so the refusal names the scenario set rather than
        # arriving from inside a solve.
        assert MAX_SCENARIOS == HARD_N_SCENARIOS_CEILING

    def test_the_weight_tolerance_is_tighter_than_the_optimizers(self):
        # ScenarioUtilitySolve accepts |sum - 1| <= 1e-8; emitting inside
        # a looser band would produce sets it rejects.
        assert WEIGHTS_SUM_TOLERANCE <= 1e-8

    def test_weighted_draws_has_the_payoffs_pairing(self):
        ev = _residuals(n_blocks=30, per_block=8, names=("a", "b"))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=32, seed=1)
        weights, r = s.weighted_draws()
        assert isinstance(weights, list) and isinstance(r, dict)
        assert abs(sum(weights) - 1.0) <= 1e-8
        assert all(w >= 0.0 for w in weights)
        assert set(r) == {"a", "b"}
        for name in r:
            assert isinstance(r[name], list)
            assert len(r[name]) == len(weights)
            assert all(math.isfinite(v) for v in r[name])

    def test_one_shared_weights_vector_serves_every_component(self):
        # The bundle consumer validates that weights are SHARED across the
        # tick and paired by length with each component's scenarios.
        ev = _residuals(n_blocks=30, per_block=8, names=("a", "b", "c"))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=40, seed=1)
        weights, r = s.weighted_draws()
        assert len({len(v) for v in r.values()} | {len(weights)}) == 1


# ---------------------------------------------------------------------------
# the family seam
# ---------------------------------------------------------------------------


class TestTheTemplateIsEnforcedNotDocumented:
    @pytest.mark.parametrize("final", ["calibrate", "scenarios"])
    def test_a_subclass_that_replaces_a_template_refuses_at_definition(self, final):
        with pytest.raises(TypeError, match=final):
            type("Rogue", (BlockConformalInterval,), {final: lambda self, *a, **k: None})

    def test_the_hooks_are_abstract_so_an_incomplete_member_cannot_construct(self):
        class Partial(OutcomeCalibrator):
            pass

        with pytest.raises(TypeError):
            Partial()

    def test_a_member_supplying_only_offsets_works(self):
        class Widest(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (-10.0, 10.0) for n in residuals.names}

        ev = _residuals(n_blocks=30, per_block=6, names=("a",))
        result = Widest().calibrate(ev, coverage=0.9, window_blocks=10)
        assert result.realized_coverage["a"] == pytest.approx(1.0)

    def test_the_template_refuses_a_member_that_omits_a_component(self):
        class Forgetful(BlockCalibrator):
            def offsets(self, residuals, level):
                return {residuals.names[0]: (-1.0, 1.0)}

        ev = _residuals(n_blocks=30, per_block=6, names=("a", "b"))
        with pytest.raises(ValueError, match="Forgetful"):
            Forgetful().calibrate(ev, coverage=0.9, window_blocks=10)

    def test_the_template_refuses_an_inverted_offset_pair(self):
        class Inverted(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (1.0, -1.0) for n in residuals.names}

        ev = _residuals(n_blocks=30, per_block=6, names=("a",))
        with pytest.raises(ValueError, match="Inverted"):
            Inverted().calibrate(ev, coverage=0.9, window_blocks=10)

    def test_the_template_refuses_an_offset_that_is_not_a_pair(self):
        class Scalar(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: 1.0 for n in residuals.names}

        ev = _residuals(n_blocks=30, per_block=6, names=("a",))
        with pytest.raises(ValueError, match="pair"):
            Scalar().calibrate(ev, coverage=0.9, window_blocks=10)

    def test_the_template_refuses_a_non_finite_offset(self):
        class Infinite(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (-float("inf"), 1.0) for n in residuals.names}

        ev = _residuals(n_blocks=30, per_block=6, names=("a",))
        with pytest.raises(ValueError, match="Infinite"):
            Infinite().calibrate(ev, coverage=0.9, window_blocks=10)


class TestRegistry:
    def test_both_members_ship_registered(self):
        assert calibrator("block-conformal")["cls"] is BlockConformalInterval
        assert calibrator("block-conformal-two-sided")["cls"] is TwoSidedBlockConformalInterval

    def test_an_unknown_name_lists_what_is_known(self):
        with pytest.raises(ValueError, match="block-conformal"):
            calibrator("nope")

    def test_a_duplicate_registration_refuses(self):
        with pytest.raises(ValueError, match="already registered"):
            register_calibrator("block-conformal", BlockConformalInterval, doc="x")

    def test_a_project_can_bring_its_own(self):
        class Mine(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (-1.0, 1.0) for n in residuals.names}

        try:
            register_calibrator("mine-test", Mine, doc="test member")
            assert calibrator("mine-test")["cls"] is Mine
        finally:
            CALIBRATORS.pop("mine-test", None)

    def test_registering_a_non_calibrator_refuses(self):
        with pytest.raises(ValueError, match="OutcomeCalibrator"):
            register_calibrator("bad-test", dict, doc="x")


class TestCanonicalDigestParity:
    def test_the_digest_recipe_matches_the_drivers(self):
        # pipeline has no PUBLIC general-purpose object hasher, so this
        # module restates the pinned canonical-JSON recipe. The two
        # copies are pinned to agree here (ADR-0155 non-goals).
        from dskit.pipeline.outcome_interval import _canonical_digest

        payload = {"b": [1, 2, 3], "a": "x", "c": {"z": 1.5, "y": None}}
        assert _canonical_digest(payload) == _canonical_hash(payload)


# ---------------------------------------------------------------------------
# the empirical decision criterion (ADR-0155 Decision 5)
# ---------------------------------------------------------------------------


def _panel(n_blocks, seed, names=("a", "b"), lo=6, hi=26):
    """Dependent blocks of UNEQUAL length with a shared per-block shock."""
    rng = random.Random(seed)
    rows, blocks = [], []
    for b in range(n_blocks):
        shock = rng.gauss(0.0, 1.0)
        scale = 0.4 + abs(shock)  # a volatility regime that moves with the block
        for _ in range(rng.randint(lo, hi)):
            common = rng.gauss(0.0, scale)
            rows.append(tuple(shock + common + rng.gauss(0.0, 0.3 * scale) for _ in names))
            blocks.append(f"b{b:04d}")
    return rows, blocks


def _naive_symmetric_offset(rows, coverage, column):
    """The IID row-level split-conformal quantile — the thing to beat."""
    scores = sorted(abs(row[column]) for row in rows)
    n = len(scores)
    k = min(math.ceil((n + 1) * coverage), n)
    return scores[k - 1]


def _empirical_coverage(rows, column, lower, upper):
    hits = sum(1 for row in rows if lower <= row[column] <= upper)
    return hits / len(rows)


@pytest.mark.slow
class TestMeasuredCoverage:
    TARGET = 0.9
    CAL_BLOCKS = 40
    TEST_BLOCKS = 40
    REPS = 60

    def _run(self):
        block_cov, naive_cov = [], []
        for rep in range(self.REPS):
            cal_rows, cal_blocks = _panel(self.CAL_BLOCKS, seed=1000 + rep)
            test_rows, _ = _panel(self.TEST_BLOCKS, seed=5000 + rep)
            ev = BlockResiduals(names=("a", "b"), rows=cal_rows, blocks=cal_blocks)
            result = BlockConformalInterval().calibrate(
                ev, coverage=self.TARGET, window_blocks=10
            )
            block_cov.append(
                _empirical_coverage(test_rows, 0, result.lower_offset["a"], result.upper_offset["a"])
            )
            naive = _naive_symmetric_offset(cal_rows, self.TARGET, 0)
            naive_cov.append(_empirical_coverage(test_rows, 0, -naive, naive))
        return block_cov, naive_cov

    def test_out_of_block_coverage_holds_near_the_target(self):
        block_cov, naive_cov = self._run()
        mean_block = sum(block_cov) / len(block_cov)
        mean_naive = sum(naive_cov) / len(naive_cov)
        print(
            f"\nMEASURED out-of-block coverage at target {self.TARGET}: "
            f"block-corrected mean={mean_block:.4f} min={min(block_cov):.4f} "
            f"| naive-IID mean={mean_naive:.4f} min={min(naive_cov):.4f} "
            f"| reps={self.REPS}"
        )
        assert mean_block >= self.TARGET - 0.01
        # The block correction must EARN its place: it is never narrower
        # than the row-counting one, and covers at least as much.
        assert mean_block > mean_naive
        assert min(block_cov) > min(naive_cov)

    def test_the_block_corrected_interval_is_never_the_narrower_one(self):
        for rep in range(10):
            cal_rows, cal_blocks = _panel(self.CAL_BLOCKS, seed=2000 + rep)
            ev = BlockResiduals(names=("a", "b"), rows=cal_rows, blocks=cal_blocks)
            result = BlockConformalInterval().calibrate(
                ev, coverage=self.TARGET, window_blocks=10
            )
            naive = _naive_symmetric_offset(cal_rows, self.TARGET, 0)
            assert result.upper_offset["a"] >= naive

    def test_rolling_conditional_coverage_is_reported_and_worse_than_marginal(self):
        cal_rows, cal_blocks = _panel(120, seed=99)
        ev = BlockResiduals(names=("a", "b"), rows=cal_rows, blocks=cal_blocks)
        result = BlockConformalInterval().calibrate(ev, coverage=self.TARGET, window_blocks=10)
        print(
            f"\nMEASURED in-sample marginal={result.realized_coverage['a']:.4f} "
            f"worst-window conditional={result.conditional_coverage['a']:.4f}"
        )
        # The honest number: a volatility regime inside one window covers
        # worse than the pooled average, and the result says so.
        assert result.conditional_coverage["a"] < result.realized_coverage["a"]


class TestTwoSidedOffsetsUseHalfAlphaPerTail:
    """M-C (re-review 2026-09-17): TwoSidedBlockConformalInterval.offsets
    computes each tail at ``alpha / 2.0`` -- the two-sided split-conformal
    shape, each tail carrying half the miscoverage. Mutating away the
    ``/2`` (a full ``alpha`` per tail) still returns a VALID interval
    (finite, lower <= upper) but a narrower one, and TestMeasuredCoverage
    above never exercises this class at all -- only BlockConformalInterval.
    This pins the two tail LEVELS directly via _weighted_quantile, the
    same primitive offsets() calls, with the tail probabilities re-derived
    independently by the test rather than read off the result, and
    self-checks that the panel actually tells the two formulas apart."""

    def test_the_tail_offsets_are_the_half_alpha_quantiles_not_the_full_alpha_ones(self):
        from dskit.pipeline.outcome_interval import _weighted_quantile

        ev = _residuals(n_blocks=40, per_block=20, names=("a",), seed=42)
        member = TwoSidedBlockConformalInterval()
        level = member.achievable_level(0.9, ev.n_blocks)
        result = member.calibrate(ev, coverage=0.9, window_blocks=10)
        assert result.achieved_level == level

        weights = ev.row_weights()
        values = list(ev.column("a"))
        alpha = 1.0 - level
        expected_lower = _weighted_quantile(values, weights, alpha / 2.0)
        expected_upper = _weighted_quantile(values, weights, 1.0 - alpha / 2.0)

        # Self-check: the full-alpha (mutant) computation must actually
        # differ on this panel, or the assertions below would pass by
        # coincidence rather than by exercising the /2.
        mutant_lower = _weighted_quantile(values, weights, alpha)
        mutant_upper = _weighted_quantile(values, weights, 1.0 - alpha)
        assert (expected_lower, expected_upper) != (mutant_lower, mutant_upper)

        assert result.lower_offset["a"] == min(expected_lower, expected_upper)
        assert result.upper_offset["a"] == max(expected_lower, expected_upper)


@pytest.mark.slow
class TestMeasuredCoverageTwoSided:
    """M-C (re-review 2026-09-17): the empirical coverage experiment
    TestMeasuredCoverage runs for BlockConformalInterval, repeated here for
    TwoSidedBlockConformalInterval -- same design, same constants, held-out
    blocks. The finding's own reproduction: at target 0.90 the correct
    alpha/2-per-tail offsets measure ~0.93 held-out coverage; a full-alpha
    mutant measures ~0.85, under target."""

    TARGET = 0.9
    CAL_BLOCKS = 40
    TEST_BLOCKS = 40
    REPS = 60

    def _run(self):
        coverage = []
        for rep in range(self.REPS):
            cal_rows, cal_blocks = _panel(self.CAL_BLOCKS, seed=3000 + rep)
            test_rows, _ = _panel(self.TEST_BLOCKS, seed=7000 + rep)
            ev = BlockResiduals(names=("a", "b"), rows=cal_rows, blocks=cal_blocks)
            result = TwoSidedBlockConformalInterval().calibrate(
                ev, coverage=self.TARGET, window_blocks=10
            )
            coverage.append(
                _empirical_coverage(test_rows, 0, result.lower_offset["a"], result.upper_offset["a"])
            )
        return coverage

    def test_out_of_block_coverage_holds_near_the_target(self):
        coverage = self._run()
        mean_cov = sum(coverage) / len(coverage)
        print(
            f"\nMEASURED two-sided out-of-block coverage at target {self.TARGET}: "
            f"mean={mean_cov:.4f} min={min(coverage):.4f} | reps={self.REPS}"
        )
        # The finding's own numbers: correct ~0.93, a full-alpha mutant
        # ~0.85 -- well under this floor.
        assert mean_cov >= self.TARGET - 0.01


# ---------------------------------------------------------------------------
# skeptic-review correction pass (2026-09-17): Me-1, Me-2, and the recorded
# Minors. See docs/architecture/decision-log.md ADR-0155.
# ---------------------------------------------------------------------------


class TestAchievedLevelOfOneIsHandled:
    """Me-1: an achieved level of exactly 1.0 is a legitimate, maximally-
    conservative outcome -- BlockCalibrator.achievable_level only refuses
    ABOVE one, and OutcomeCalibrator._checked_level accepts up to and
    including one. _diagnostics must agree rather than crash through
    metrics.pinball's open-interval contract when the tail quantile levels
    land exactly on 0 or 1."""

    def test_the_class_docstring_worked_example_runs(self):
        # OutcomeIntervalResult's own Examples block: B=2, coverage=0.6
        # lands achieved_level exactly on 1.0 (ceil(3 * 0.6) / 2 == 1.0).
        # Verbatim repro from the skeptic finding -- must run, not raise.
        ev = BlockResiduals(
            names=("alpha",),
            rows=[(0.1,), (-0.2,), (0.3,), (0.0,)],
            blocks=["s1", "s1", "s2", "s2"],
        )
        result = BlockConformalInterval().calibrate(ev, coverage=0.6, window_blocks=2)
        assert result.achieved_level == 1.0
        assert result.upper_offset["alpha"] >= 0.0

    @pytest.mark.parametrize(
        ("member_cls", "n_blocks", "coverage"),
        [
            (BlockConformalInterval, 2, 0.5),
            (TwoSidedBlockConformalInterval, 2, 0.5),
            (BlockConformalInterval, 8, 0.85),
            (TwoSidedBlockConformalInterval, 8, 0.85),
            (BlockConformalInterval, 20, DEFAULT_COVERAGE),
            (TwoSidedBlockConformalInterval, 20, DEFAULT_COVERAGE),
            (BlockConformalInterval, 30, DEFAULT_COVERAGE),
            (TwoSidedBlockConformalInterval, 30, DEFAULT_COVERAGE),
        ],
    )
    def test_a_coverage_landing_exactly_on_the_ceiling_does_not_crash(
        self, member_cls, n_blocks, coverage
    ):
        ev = _residuals(n_blocks=n_blocks, per_block=5, names=("a",))
        result = member_cls().calibrate(ev, coverage=coverage, window_blocks=min(10, n_blocks))
        assert result.achieved_level == 1.0
        assert math.isfinite(result.tail_loss["a"])
        assert result.tail_loss["a"] >= 0.0
        assert 0.0 <= result.realized_coverage["a"] <= 1.0
        assert 0.0 <= result.conditional_coverage["a"] <= 1.0


class TestPublicHooksRefuseLoudly:
    """Me-2: achievable_level/offsets/draw_blocks are documented as directly
    callable (the class docstrings demonstrate exactly that), so a bad
    direct-call argument must refuse by name instead of leaking a raw
    stdlib exception (ZeroDivisionError, a bare TypeError, or a silently
    wrong answer)."""

    def test_achievable_level_refuses_zero_blocks_by_name(self):
        with pytest.raises(ValueError, match="n_blocks"):
            BlockConformalInterval().achievable_level(0.9, 0)

    def test_achievable_level_refuses_negative_blocks_by_name(self):
        with pytest.raises(ValueError, match="n_blocks"):
            BlockConformalInterval().achievable_level(0.9, -5)

    @pytest.mark.parametrize("bad", [2.5, "x", None, True])
    def test_achievable_level_refuses_a_non_int_block_count_by_name(self, bad):
        with pytest.raises(ValueError, match="n_blocks"):
            BlockConformalInterval().achievable_level(0.9, bad)

    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), -float("inf"), "x", None, 1.5, True]
    )
    def test_achievable_level_refuses_an_unusable_coverage_by_name(self, bad):
        with pytest.raises(ValueError, match="coverage"):
            BlockConformalInterval().achievable_level(bad, 40)

    def test_offsets_refuses_anything_that_is_not_block_residuals(self):
        with pytest.raises(TypeError, match="BlockResiduals"):
            BlockConformalInterval().offsets({"a": [0.1]}, 0.9)
        with pytest.raises(TypeError, match="BlockResiduals"):
            TwoSidedBlockConformalInterval().offsets({"a": [0.1]}, 0.9)

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.2, float("nan"), "x", None])
    def test_offsets_refuses_an_unusable_level_by_name(self, bad):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="level"):
            BlockConformalInterval().offsets(ev, bad)
        with pytest.raises(ValueError, match="level"):
            TwoSidedBlockConformalInterval().offsets(ev, bad)

    def test_draw_blocks_refuses_anything_that_is_not_block_residuals(self):
        with pytest.raises(TypeError, match="BlockResiduals"):
            BlockConformalInterval().draw_blocks({"a": [0.1]}, 5, random.Random(0))

    @pytest.mark.parametrize("bad", [2.5, "x", None, True])
    def test_draw_blocks_refuses_a_non_int_scenario_count_by_name(self, bad):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="n_scenarios"):
            BlockConformalInterval().draw_blocks(ev, bad, random.Random(0))

    def test_draw_blocks_refuses_a_negative_scenario_count_by_name(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="n_scenarios"):
            BlockConformalInterval().draw_blocks(ev, -3, random.Random(0))

    def test_draw_blocks_refuses_an_unusable_rng_by_name(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="rng"):
            BlockConformalInterval().draw_blocks(ev, 5, None)


class TestOverflowInputsRefuseAsValueError:
    """M-A (re-review 2026-09-17): the four hooks' validation calls
    ``records.number_ok``, which routes through ``math.isfinite`` --
    and ``math.isfinite`` raises ``OverflowError``, not ``ValueError``,
    on an int too large to convert to a float (``10**400``). Every
    docstring here promises ``ValueError``, so an ``except ValueError``
    at a caller would NOT catch this. Reproduced verbatim from the
    finding."""

    HUGE = 10**400

    def test_achievable_level_refuses_a_huge_coverage_as_value_error(self):
        with pytest.raises(ValueError, match="coverage"):
            BlockConformalInterval().achievable_level(self.HUGE, 40)

    def test_achievable_level_refuses_a_huge_n_blocks_as_value_error(self):
        with pytest.raises(ValueError, match="n_blocks"):
            BlockConformalInterval().achievable_level(0.9, self.HUGE)

    @pytest.mark.parametrize("member_cls", [BlockConformalInterval, TwoSidedBlockConformalInterval])
    def test_offsets_refuses_a_huge_level_as_value_error(self, member_cls):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="level"):
            member_cls().offsets(ev, self.HUGE)

    def test_the_template_screens_also_absorb_a_huge_member_returned_level(self):
        # _checked_level (the template's OWN screen of a member's answer)
        # calls number_ok too -- a member that returns something monstrous
        # must still surface as calibrate()'s documented ValueError.
        class Monstrous(BlockConformalInterval):
            def achievable_level(self, coverage, n_blocks):
                return 10**400

        ev = _residuals(n_blocks=20, per_block=5, names=("a",))
        with pytest.raises(ValueError, match="Monstrous"):
            Monstrous().calibrate(ev, coverage=0.9, window_blocks=5)

    def test_the_template_screens_also_absorb_a_huge_member_returned_offset(self):
        class Monstrous(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (-10**400, 1.0) for n in residuals.names}

        ev = _residuals(n_blocks=20, per_block=5, names=("a",))
        with pytest.raises(ValueError, match="Monstrous"):
            Monstrous().calibrate(ev, coverage=0.9, window_blocks=5)

    def test_a_calibrator_lookup_by_an_unhashable_name_refuses_as_value_error(self):
        with pytest.raises(ValueError, match="unknown calibrator"):
            calibrator([])
        with pytest.raises(ValueError, match="unknown calibrator"):
            calibrator({})

    def test_registering_an_unhashable_name_refuses_as_value_error(self):
        with pytest.raises(ValueError, match="hashable"):
            register_calibrator([], BlockConformalInterval)

    @pytest.mark.parametrize("bad_kwargs", [{"names": None}, {"rows": None}, {"blocks": None}])
    def test_block_residuals_refuses_none_containers_as_value_error(self, bad_kwargs):
        kwargs = {"names": ("a",), "rows": [(0.1,), (0.2,)], "blocks": ["b0", "b1"]}
        kwargs.update(bad_kwargs)
        with pytest.raises(ValueError):
            BlockResiduals(**kwargs)

    def test_block_residuals_refuses_non_nested_rows_as_value_error(self):
        with pytest.raises(ValueError, match="rows"):
            BlockResiduals(names=("a",), rows=[1, 2, 3], blocks=["b0", "b0", "b1"])

    def test_block_residuals_refuses_a_huge_residual_as_value_error(self):
        with pytest.raises(ValueError, match="row 0"):
            BlockResiduals(names=("a",), rows=[(self.HUGE,), (0.2,)], blocks=["b0", "b1"])

    def test_block_residuals_refuses_a_huge_stamp_as_value_error(self):
        with pytest.raises(ValueError, match="stamps"):
            BlockResiduals(
                names=("a",), rows=[(0.1,), (0.2,)], blocks=["b0", "b1"], stamps=[0, self.HUGE]
            )

    def test_scenario_set_refuses_none_weights_as_value_error(self):
        with pytest.raises(ValueError, match="weights"):
            ScenarioSet(weights=None, draws={"a": (0.1, 0.2)}, calibration_hash="h", seed=0, provenance={})

    def test_scenario_set_refuses_a_none_weight_as_value_error(self):
        with pytest.raises(ValueError, match="weights"):
            ScenarioSet(
                weights=(0.5, None), draws={"a": (0.1, 0.2)}, calibration_hash="h", seed=0, provenance={}
            )

    def test_scenario_set_refuses_a_none_draw_value_as_value_error(self):
        with pytest.raises(ValueError, match="draws"):
            ScenarioSet(
                weights=(0.5, 0.5), draws={"a": (0.1, None)}, calibration_hash="h", seed=0, provenance={}
            )

    def test_scenario_set_refuses_non_dict_draws_as_value_error(self):
        with pytest.raises(ValueError, match="draws"):
            ScenarioSet(
                weights=(0.5, 0.5), draws=[0.1, 0.2], calibration_hash="h", seed=0, provenance={}
            )

    def test_scenario_set_refuses_a_huge_weight_as_value_error(self):
        with pytest.raises(ValueError, match="weights"):
            ScenarioSet(
                weights=(self.HUGE, 0.5), draws={"a": (0.1, 0.2)}, calibration_hash="h", seed=0,
                provenance={},
            )


class TestDrawBlocksIsBounded:
    """M-B (re-review 2026-09-17): draw_blocks validated n_scenarios as a
    non-negative int but imposed no UPPER bound, unlike scenarios() (its
    own caller) which bounds to [MIN_SCENARIOS, MAX_SCENARIOS]. Directly
    callable and undocumented-as-bounded, so a huge n_scenarios spins the
    while loop rather than refusing -- observed at 10**9 taking ~110s;
    10**400 is effectively unbounded. Now bounded identically to
    scenarios(), and pinned to return instantly rather than merely
    'eventually'."""

    def test_a_scenario_count_above_the_ceiling_refuses_instead_of_hanging(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match=str(MAX_SCENARIOS)):
            BlockConformalInterval().draw_blocks(ev, 10**9, random.Random(0))

    def test_an_astronomically_large_scenario_count_refuses_instead_of_hanging(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="n_scenarios"):
            BlockConformalInterval().draw_blocks(ev, 10**400, random.Random(0))

    def test_the_ceiling_itself_is_still_accepted(self):
        ev = _residuals(n_blocks=30, per_block=10, names=("a",))
        labels = BlockConformalInterval().draw_blocks(ev, MAX_SCENARIOS, random.Random(0))
        assert sum(ev.block_counts()[label] for label in labels) >= MAX_SCENARIOS

    def test_one_above_the_ceiling_refuses(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match=str(MAX_SCENARIOS)):
            BlockConformalInterval().draw_blocks(ev, MAX_SCENARIOS + 1, random.Random(0))


class TestQuantileEpsilonIsLoadBearing:
    """Minor: _QUANTILE_EPS was an undisclosed live mutation survivor. Pin
    it directly rather than remove it -- it is genuinely load-bearing."""

    def test_it_absorbs_representation_noise_at_an_exact_weight_tie(self):
        from dskit.pipeline.outcome_interval import _weighted_quantile

        # Seven equal 1/7 weights summed left-to-right land ~1.1e-16 short
        # of the exact tie at level 5/7 -- pure float representation
        # noise. Without _QUANTILE_EPS the tie is missed and the quantile
        # reads one row high (5.0 instead of the correct 4.0).
        values = list(range(7))
        weights = [1.0 / 7.0] * 7
        level = 5.0 / 7.0
        assert _weighted_quantile(values, weights, level) == 4.0

    def test_the_epsilon_shifted_boundary_is_inclusive(self):
        from dskit.pipeline.outcome_interval import _weighted_quantile

        # cumulative after the first item is exactly level - _QUANTILE_EPS
        # (0.3 == 0.3 + 1e-12 - 1e-12 in float). The boundary comparison
        # must stay ">=": the tied value is correct, the next one up is
        # not. A ">" mutation here is otherwise invisible to every other
        # test in the suite.
        level = 0.3 + 1e-12
        assert _weighted_quantile([10.0, 20.0], [0.3, 0.7], level) == 10.0


class TestClosedPinballBoundary:
    """_closed_pinball's two boundary formulas are both always >= 0 by
    construction, so TestAchievedLevelOfOneIsHandled's finiteness/
    non-negativity checks cannot tell a correct formula from a swapped
    one. Pin the exact values directly."""

    def test_the_boundary_formulas_are_not_interchangeable(self):
        from dskit.pipeline.outcome_interval import _closed_pinball

        assert _closed_pinball(1.0, 3.0, 0.0) == 0.0
        assert _closed_pinball(3.0, 1.0, 0.0) == 2.0
        assert _closed_pinball(1.0, 3.0, 1.0) == 2.0
        assert _closed_pinball(3.0, 1.0, 1.0) == 0.0

    def test_interior_tau_still_routes_through_pinball(self):
        from dskit.pipeline.outcome_interval import _closed_pinball
        from dskit.pipeline.metrics import pinball

        assert _closed_pinball(1.0, 3.0, 0.5) == pinball(1.0, 3.0, 0.5)


class TestProvenanceFreezesDeep:
    """Minor: provenance was shallow-frozen -- only the top-level dict was a
    MappingProxyType, so the nested ``components`` list could be mutated
    in place through a live reference."""

    def test_a_results_provenance_list_cannot_be_mutated_in_place(self):
        ev = _residuals(n_blocks=20, per_block=4, names=("a", "b"))
        result = BlockConformalInterval().calibrate(ev, coverage=0.9, window_blocks=5)
        assert isinstance(result.provenance["components"], tuple)
        with pytest.raises(AttributeError):
            result.provenance["components"].append("HACKED")

    def test_a_scenario_sets_provenance_list_cannot_be_mutated_in_place(self):
        ev = _residuals(n_blocks=20, per_block=4, names=("a",))
        s = BlockConformalInterval().scenarios(ev, n_scenarios=16, seed=1)
        assert isinstance(s.provenance["components"], tuple)
        with pytest.raises(AttributeError):
            s.provenance["components"].append("HACKED")


class TestFrozenTreeDisclosedGaps:
    """Minor: _frozen_tree's docstring now discloses two edge behaviors
    rather than silently having them -- pin both so the disclosure stays
    true. Both are safe in practice because the only internal producer,
    OutcomeCalibrator._provenance, ever builds provenance from flat
    scalars plus one list of strings; neither shape below is reachable
    through calibrate()/scenarios(), only through direct construction."""

    def test_a_custom_mutable_leaf_object_survives_unfrozen(self):
        from dskit.pipeline.outcome_interval import _frozen_tree

        class MutableThing:
            def __init__(self):
                self.value = 1

        leaf = MutableThing()
        frozen = _frozen_tree({"a": [leaf]})
        # The leaf itself is returned BY REFERENCE, not copied or frozen.
        assert frozen["a"][0] is leaf
        leaf.value = 2
        assert frozen["a"][0].value == 2

    def test_a_self_referential_structure_raises_recursion_error_not_silently(self):
        from dskit.pipeline.outcome_interval import _frozen_tree

        cyclic = {}
        cyclic["self"] = cyclic
        with pytest.raises(RecursionError):
            _frozen_tree(cyclic)

    def test_realistic_deep_nesting_is_unaffected(self):
        # The disclosed gap is CYCLES, not depth: ordinary nesting to 50+
        # levels -- deeper than any real provenance ever goes -- freezes
        # cleanly.
        from dskit.pipeline.outcome_interval import _frozen_tree

        deep = "leaf"
        for _ in range(60):
            deep = [deep]
        frozen = _frozen_tree(deep)
        for _ in range(60):
            frozen = frozen[0]
        assert frozen == "leaf"


class TestTailLossIsZeroAtTheConservativeBoundary:
    """Minor: tail_loss is unconditionally, deterministically 0.0 whenever
    achieved_level == 1.0, for BOTH calibrators -- proved algebraically
    (the level-1.0 quantile is the sample extreme, so both offsets bracket
    every calibration value and both closed-form pinball terms vanish) and
    pinned here rather than only documented."""

    @pytest.mark.parametrize("member_cls", [BlockConformalInterval, TwoSidedBlockConformalInterval])
    def test_tail_loss_is_exactly_zero_when_the_level_is_one(self, member_cls):
        ev = _residuals(n_blocks=2, per_block=5, names=("a",))
        result = member_cls().calibrate(ev, coverage=0.5, window_blocks=2)
        assert result.achieved_level == 1.0
        assert result.tail_loss["a"] == 0.0


class TestMutationProbePrecisionPins:
    """Boundary-precision regressions surfaced by re-running the mutation
    probe on the correction pass. Each existing test that came close to
    these boundaries used a margin wide enough that a one-sided or
    off-by-one mutation slipped through; these pin the exact edge."""

    def test_the_template_accepts_a_level_exactly_at_the_epsilon_floor(self):
        from dskit.pipeline.outcome_interval import _QUANTILE_EPS

        class RightAtFloor(BlockConformalInterval):
            def achievable_level(self, coverage, n_blocks):
                return coverage - _QUANTILE_EPS

        ev = _residuals(n_blocks=20, per_block=5, names=("a",))
        result = RightAtFloor().calibrate(ev, coverage=0.9, window_blocks=5)
        assert result.achieved_level == pytest.approx(0.9 - _QUANTILE_EPS)

    def test_the_template_accepts_equal_lower_and_upper_bounds(self):
        # _checked_offsets's own docstring says "lower at or below upper"
        # -- equal is legitimate, not inverted.
        class Pointwise(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (0.0, 0.0) for n in residuals.names}

        ev = _residuals(n_blocks=20, per_block=5, names=("a",))
        result = Pointwise().calibrate(ev, coverage=0.9, window_blocks=5)
        assert result.lower_offset["a"] == result.upper_offset["a"] == 0.0

    def test_the_template_refuses_a_member_short_by_exactly_one_row(self):
        class OffByOne(BlockConformalInterval):
            def draw_blocks(self, residuals, n_scenarios, rng):
                return residuals.block_ids[:-1]

        ev = _residuals(n_blocks=21, per_block=1, names=("a",))
        with pytest.raises(ValueError, match="OffByOne"):
            OffByOne().scenarios(ev, n_scenarios=21, seed=1)

    def test_a_level_barely_above_one_is_still_refused(self):
        # The natural formula's smallest possible excess over 1.0 is
        # 1 / n_blocks, so a very large (but O(1) to evaluate) n_blocks
        # lands just inside a hypothetical hard-coded tolerance window
        # without needing to build a panel of that size.
        n = 20_000_000
        coverage = 1.0 - 0.5 / (n + 1)
        with pytest.raises(ValueError, match="not achievable"):
            BlockConformalInterval().achievable_level(coverage, n)

    def test_draw_blocks_refuses_a_scenario_count_of_exactly_negative_one(self):
        ev = _residuals(n_blocks=10, per_block=4, names=("a",))
        with pytest.raises(ValueError, match="n_scenarios"):
            BlockConformalInterval().draw_blocks(ev, -1, random.Random(0))


# The one pattern TestWeightsToleranceSiblingAgreement scans the toolkit
# with, and the one its own unit tests exercise directly -- HOISTED to
# module level (mirroring the sibling uncertainty_set.py's own fix) so a
# second, slightly different copy inline in a test method can never drift
# from what the scan actually uses. `[ \t]*` after `^` is deliberate: a
# bare `^` only matches a definition starting in column zero, missing an
# indented class- or function-body redefinition entirely -- the exact
# blind spot a mutation probe found here (re-review 2026-09-17), already
# fixed on the sibling lane; this is the mirror image of that fix.
_WEIGHTS_SUM_TOLERANCE_ASSIGNMENT = re.compile(
    r"^[ \t]*WEIGHTS_SUM_TOLERANCE\s*=\s*(.+?)\s*(?:#.*)?$", re.MULTILINE
)


class TestWeightsToleranceSiblingAgreement:
    """Minor: WEIGHTS_SUM_TOLERANCE is duplicated with the unmerged sibling
    uncertainty_set.py (ADR-0156). No cross-branch import is possible, so
    this is a plain TEXT scan across dskit/ for the constant's definition,
    refusing on divergence -- the mirror image of the pin the sibling's own
    suite carries, so whichever module lands first protects the other."""

    def test_every_definition_of_the_constant_across_dskit_agrees(self):
        root = Path(__file__).resolve().parents[2] / "dskit"
        found = []
        for path in sorted(root.rglob("*.py")):
            for value in _WEIGHTS_SUM_TOLERANCE_ASSIGNMENT.findall(
                path.read_text(encoding="utf-8")
            ):
                found.append((str(path.relative_to(root)), float(value)))
        # The scan itself must not be vacuous: this module's own constant
        # must be among what it found, or a broken pattern would pass by
        # finding nothing at all.
        assert found, "the scan found no WEIGHTS_SUM_TOLERANCE at all — it has stopped pinning"
        assert any(name.endswith("outcome_interval.py") for name, _value in found)
        assert len({value for _name, value in found}) == 1, (
            f"WEIGHTS_SUM_TOLERANCE diverged: {found}"
        )

    def test_the_scan_pattern_catches_an_indented_redefinition(self):
        # A bare `^WEIGHTS_SUM_TOLERANCE` used to require column zero, so a
        # class- or function-body copy (indented) sailed through unseen.
        sample = "class Foo:\n    WEIGHTS_SUM_TOLERANCE = 1e-3\n"
        assert _WEIGHTS_SUM_TOLERANCE_ASSIGNMENT.findall(sample) == ["1e-3"]

    def test_the_scan_pattern_ignores_a_fully_commented_out_definition(self):
        # Leading whitespace must not widen the pattern into matching a
        # line that never assigns anything at module or class/function
        # scope.
        sample = "# WEIGHTS_SUM_TOLERANCE = 1e-3\n"
        assert _WEIGHTS_SUM_TOLERANCE_ASSIGNMENT.findall(sample) == []

