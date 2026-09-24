"""The statistics table vs hand values, and three-valued criteria verdicts."""

import pytest

from dskit.evaluation.book import EvaluationBook
from dskit.evaluation.criteria import Criterion, Scorecard, Verdict
from dskit.evaluation.events import EventLog
from dskit.evaluation.statistics import DEFAULT_MIN_N, STAT_NAMES, Statistic, StatisticsTable
from dskit.pipeline.base import ConfigError
from dskit.pipeline.stats import across_fold_t, lower_tail_mean
from tests.evaluation.conftest import build_scenario


@pytest.fixture
def table(scenario):
    return StatisticsTable(EvaluationBook(EventLog(scenario)))


def test_the_table_builds_exactly_the_declared_names(table):
    assert table.names == STAT_NAMES


def test_pnl_rows_match_the_hand_values(table):
    assert table.get("net_pnl").value == pytest.approx(16.9)
    assert table.get("gross_pnl").value == pytest.approx(19.4)
    assert table.get("fees").value == pytest.approx(2.5)
    assert table.get("cost_share").value == pytest.approx(2.5 / 19.4)


def test_trade_rows_reuse_pipeline_stats(table):
    pnls = [2.9, 10.0, 4.0]
    assert table.get("trades").value == 3
    assert table.get("hit_rate").value == 1.0
    assert table.get("trade_mean").value == pytest.approx(sum(pnls) / 3)
    assert table.get("trade_t").value == pytest.approx(across_fold_t(pnls)["t"])
    assert table.get("trade_es95").value == pytest.approx(lower_tail_mean(pnls, 0.95))
    assert table.get("profit_factor").value is None  # no losing trip
    assert table.get("avg_loss").value is None


def test_short_samples_are_labelled_insufficient(table):
    assert table.min_n == DEFAULT_MIN_N
    assert not table.get("trade_mean").sufficient
    assert not table.get("daily_sharpe").sufficient
    assert table.get("net_pnl").sufficient  # an identity has no sample floor
    assert StatisticsTable(table.book, min_n=2).get("daily_sharpe").sufficient


def test_activity_and_holding_rows(table):
    assert (table.get("decisions").value, table.get("orders").value) == (8, 5)
    assert table.get("fill_ratio").value == 1.0
    assert table.get("refusal_rate").value == pytest.approx(1 / 8)
    assert table.get("holding_median_minutes").value == 1.0
    assert table.get("days").value == 2


def test_dsr_reads_the_trials_from_run_start():
    one = StatisticsTable(EvaluationBook(EventLog(build_scenario(trials=1))))
    many = StatisticsTable(EvaluationBook(EventLog(build_scenario(trials=50))))
    assert one.get("dsr").value == pytest.approx(one.get("psr").value)
    assert many.get("dsr").value < one.get("dsr").value


def _stat(value, n):
    return Statistic("x", value, n, 30, "")


@pytest.mark.parametrize("op, value, observed, n, status", [
    (">", 0.0, 1.0, 50, "PASS"),
    (">", 0.0, -1.0, 50, "FAIL"),
    ("<=", 1.0, 1.0, 50, "PASS"),
    ("<", 1.0, 1.0, 50, "FAIL"),
    (">=", 0.0, 1.0, 10, "INCONCLUSIVE"),
    (">=", 0.0, None, 50, "INCONCLUSIVE"),
    (">=", 0.0, 1.0, None, "PASS"),
])
def test_criterion_verdicts(op, value, observed, n, status):
    verdict = Criterion("c", "x", op, value, 30).evaluate(_stat(observed, n))
    assert isinstance(verdict, Verdict) and verdict.status == status


def test_the_scorecard_takes_the_worst_status(table):
    passing = Criterion("p", "net_pnl", ">", 0.0, 0)
    failing = Criterion("f", "fees", ">", 100.0, 0)
    short = Criterion("s", "daily_sharpe", ">", 0.0, 30)
    assert Scorecard([passing], table).status == "PASS"
    assert Scorecard([passing, short], table).status == "INCONCLUSIVE"
    assert Scorecard([passing, short, failing], table).status == "FAIL"
    assert Scorecard([], table).status == "UNJUDGED"
    body = Scorecard([short], table).to_obj()
    assert body["verdicts"][0]["reason"] == "n=2 < min_n=30"


def test_a_criterion_on_an_unknown_statistic_is_refused(table):
    with pytest.raises(ConfigError, match="unknown statistic"):
        Scorecard([Criterion("typo", "sharp", ">", 0.0, 0)], table)


def test_criterion_objects_are_default_deny():
    with pytest.raises(ConfigError) as caught:
        Criterion.from_obj({"name": "", "stat": "x", "op": "==", "value": "1", "min_n": -1,
                            "extra": True})
    text = str(caught.value)
    for needle in ("unknown field", "name must be", "op must be", "value must be", "min_n"):
        assert needle in text
    with pytest.raises(ConfigError):
        Criterion("c", "x", ">", float("inf"), 0)
    obj = {"name": "c", "stat": "x", "op": ">", "value": 1.0, "min_n": 3}
    assert Criterion.from_obj(obj).to_obj() == obj
