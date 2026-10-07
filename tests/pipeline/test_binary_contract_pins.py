"""Boundary pins for the binary-contract pricer and scorer (ADR-0243): the exact-equality and tiny-horizon cases.

A mutation pass over ``binary_pricing.py`` and ``binary_scoring.py`` found single-token changes that every test still passed:
the take rule at exact equality, a trade that earns exactly nothing, a horizon under one second, a range with one usable bound
and a settlement before 1970. Each case here is an input only the shipped rule answers correctly. Values are chosen exactly
representable in binary (halves, eighths) so equality is equality, not a rounding accident.
"""

import pytest

from dskit.pipeline.binary_pricing import AveragedLognormal

from .test_binary_pricing import SIGMA, decision_for, row as price_row, run as run_pricing
from .test_binary_scoring import HELD
from .test_binary_scoring import pick, row as score_row, score


# -- the pricer: the horizon and the bounds --------------------------------------------------------------


def test_a_horizon_under_one_second_prices_when_there_is_no_averaging_window():
    model = AveragedLognormal(SIGMA, 0.5, 0.0)
    assert model.variance == pytest.approx(SIGMA**2 * 0.5)
    out = run_pricing([price_row(decision_ms=decision_for(0.5))], averaging_window_s=0)[0]
    assert out["fair_status"] == "ok" and out["fair_tau"] == pytest.approx(0.5)
    assert out["fair"] == pytest.approx(model.survival(100.0, 100.0))


@pytest.mark.parametrize("tau", [0.0, -0.5, -1.0])
def test_a_horizon_that_is_not_after_the_fill_is_never_priced_even_with_no_window(tau):
    out = run_pricing([price_row(decision_ms=decision_for(tau))], averaging_window_s=0)[0]
    assert out["fair_status"] == "exec_not_before_settle" and out["fair"] is None


@pytest.mark.parametrize("tau", [0.0, 0.5, 1.0])
def test_a_model_refuses_a_horizon_that_is_not_positive_only_at_or_below_zero(tau):
    if tau == 0.0:
        with pytest.raises(ValueError, match="positive"):
            AveragedLognormal(SIGMA, tau, 0.0)
    else:
        assert AveragedLognormal(SIGMA, tau, 0.0).variance == pytest.approx(SIGMA**2 * tau)


@pytest.mark.parametrize("over", [
    pytest.param({"payoff": "between", "lo": -5.0, "hi": 100.0}, id="between-negative-lower"),
    pytest.param({"payoff": "between", "lo": 0.0, "hi": 100.0}, id="between-zero-lower"),
    pytest.param({"payoff": "between", "lo": 99.0, "hi": 99.0}, id="between-empty-but-usable"),
])
def test_a_range_needs_every_bound_positive_not_just_one(over):
    out = run_pricing([price_row(**over)])[0]
    if over["lo"] <= 0.0:
        assert out["fair_status"] == "bad_bounds" and out["fair"] is None
    else:
        assert out["fair_status"] == "ok" and out["fair"] == pytest.approx(0.0), "lower == upper pays on no value"


# -- the scorer: the take rule, a break-even trade and the clock before 1970 -----------------------------------------


def trades_of(tmp_path, rows, **over):
    return pick(score(tmp_path, rows=rows, **over), segment="heldout")


def test_an_edge_exactly_equal_to_the_fee_plus_margin_is_not_a_trade(tmp_path):
    over = {"margin": 0.125}
    yes = score_row("Y", 1, 0.5, 0.75, 0.5, 0.4, "e1", settle=HELD, fee_yes=0.125, fee_no=0.125)  # edge 0.25 == 0.125 + 0.125
    no = score_row("N", 0, 0.5, 0.25, 0.6, 0.5, "e2", settle=HELD, fee_yes=0.125, fee_no=0.125)   # -edge 0.25 == 0.125 + 0.125
    assert trades_of(tmp_path, [yes, no], **over)["n_trades"] == 0, "the rule is strictly greater"
    yes_over = score_row("Y", 1, 0.5, 0.751, 0.5, 0.4, "e1", settle=HELD, fee_yes=0.125, fee_no=0.125)
    no_over = score_row("N", 0, 0.5, 0.249, 0.6, 0.5, "e2", settle=HELD, fee_yes=0.125, fee_no=0.125)
    assert trades_of(tmp_path, [yes_over], **over)["n_trades"] == 1, "just over the YES threshold trades"
    assert trades_of(tmp_path, [no_over], **over)["n_trades"] == 1, "just over the NO threshold trades"


def test_a_trade_that_earns_exactly_nothing_is_a_trade_but_not_a_hit(tmp_path):
    flat = score_row("F", 1, 0.5, 0.9, 0.875, 0.4, "e1", settle=HELD, fee_yes=0.125)  # 1 - 0.875 - 0.125 == 0.0
    win = score_row("W", 1, 0.5, 0.9, 0.5, 0.4, "e2", settle=HELD, fee_yes=0.125)     # 1 - 0.5 - 0.125 == 0.375
    one = trades_of(tmp_path, [flat], margin=0.0)
    assert (one["n_trades"], one["pnl_total"], one["hit_rate"]) == (1, 0.0, 0.0)
    both = trades_of(tmp_path, [flat, win], margin=0.0)
    assert (both["n_trades"], both["pnl_total"], both["hit_rate"]) == (2, 0.375, 0.5)


def test_settlements_either_side_of_1970_fall_in_different_cluster_blocks(tmp_path):
    """Blocks are floor-divided, so the last second before the epoch is day -1, not day 0 (truncation would merge them)."""
    rows = [score_row("A", 1, 0.4, 0.7, 0.42, 0.38, "e1", settle=-1000), score_row("B", 0, 0.4, 0.3, 0.42, 0.38, "e1", settle=1000)]
    cell = pick(score(tmp_path, rows=rows), segment="development")
    assert cell["n"] == 2 and cell["n_clusters"] == 2 and cell["n_event_clusters"] == 1
